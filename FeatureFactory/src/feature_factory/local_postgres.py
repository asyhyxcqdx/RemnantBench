from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import psycopg
from sqlalchemy.engine.url import make_url

from feature_factory.config import Settings
from feature_factory.docker_mirrors import mirror_docker_image_ref, should_use_china_docker_mirrors

LOCAL_POSTGRES_HOSTS = {"127.0.0.1", "localhost"}
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LocalPostgresSpec:
    database_url: str
    container_name: str
    image: str
    data_dir: Path
    host: str
    port: int
    username: str
    password: str
    database: str
    startup_timeout_seconds: float

    @property
    def publish_host(self) -> str:
        return "127.0.0.1" if self.host in LOCAL_POSTGRES_HOSTS else self.host


def resolve_local_postgres_spec(settings: Settings) -> LocalPostgresSpec | None:
    url = make_url(settings.database_url)
    if url.get_backend_name() != "postgresql":
        return None
    host = url.host or "127.0.0.1"
    if host not in LOCAL_POSTGRES_HOSTS:
        return None
    if not url.username or not url.password or not url.database:
        raise RuntimeError(
            "Local PostgreSQL auto-management requires username, password, and database name in FEATURE_FACTORY_DATABASE_URL."
        )
    data_dir = Path(settings.local_postgres_data_dir).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir
    return LocalPostgresSpec(
        database_url=settings.database_url,
        container_name=settings.local_postgres_container_name,
        image=settings.local_postgres_image,
        data_dir=data_dir,
        host=host,
        port=int(url.port or 5432),
        username=url.username,
        password=str(url.password),
        database=url.database,
        startup_timeout_seconds=settings.local_postgres_startup_timeout_seconds,
    )


def _run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        stderr = completed.stderr.strip() or completed.stdout.strip() or "command failed"
        raise RuntimeError(f"{' '.join(args)} failed: {stderr}")
    return completed


def _inspect_container(name: str) -> dict | None:
    completed = subprocess.run(
        ["docker", "container", "inspect", name],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        if "No such container" in stderr or "No such object" in stderr:
            return None
        raise RuntimeError(f"docker container inspect {name} failed: {stderr or completed.stdout.strip()}")
    payload = json.loads(completed.stdout)
    return payload[0] if payload else None


def _to_psycopg_connection_string(database_url: str) -> str:
    url = make_url(database_url)
    if url.get_backend_name() == "postgresql":
        return url.set(drivername="postgresql").render_as_string(hide_password=False)
    return database_url


def _database_is_ready(database_url: str) -> bool:
    connection_string = _to_psycopg_connection_string(database_url)
    try:
        with psycopg.connect(connection_string, connect_timeout=1):
            return True
    except psycopg.Error:
        return False


def _wait_until_database_ready(database_url: str, timeout_seconds: float) -> None:
    connection_string = _to_psycopg_connection_string(database_url)
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(connection_string, connect_timeout=1):
                return
        except psycopg.Error as exc:
            last_error = str(exc)
            time.sleep(0.5)
    message = f"managed PostgreSQL did not become ready within {timeout_seconds:.1f}s"
    if last_error:
        message = f"{message}: {last_error}"
    raise RuntimeError(message)


def _stop_container(name: str, *, check: bool = True) -> None:
    _run_command(["docker", "stop", name], check=check)


def _remove_container(name: str, *, check: bool = True) -> None:
    _run_command(["docker", "rm", "-f", name], check=check)


def _run_container(spec: LocalPostgresSpec, *, log: Callable[[str], None] | None = None) -> None:
    spec.data_dir.mkdir(parents=True, exist_ok=True)
    image = mirror_docker_image_ref(spec.image, enabled=should_use_china_docker_mirrors())
    if log is not None:
        log(
            f"Starting managed PostgreSQL container {spec.container_name} "
            f"on {spec.publish_host}:{spec.port}"
        )
    try:
        _run_command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                spec.container_name,
                "--publish",
                f"{spec.publish_host}:{spec.port}:5432",
                "--env",
                f"POSTGRES_DB={spec.database}",
                "--env",
                f"POSTGRES_USER={spec.username}",
                "--env",
                f"POSTGRES_PASSWORD={spec.password}",
                "--volume",
                f"{spec.data_dir.resolve()}:/var/lib/postgresql/data",
                image,
            ]
        )
    except RuntimeError as exc:
        message = str(exc)
        if "address already in use" in message:
            raise RuntimeError(
                f"Managed PostgreSQL could not bind {spec.publish_host}:{spec.port} because the port is already in use. "
                "Stop the conflicting service, change FEATURE_FACTORY_DATABASE_URL to another local port, or run serve with --no-auto-db."
            ) from exc
        raise


@contextmanager
def managed_local_postgres(
    settings: Settings,
    *,
    enabled: bool = True,
    log: Callable[[str], None] | None = None,
) -> Iterator[LocalPostgresSpec | None]:
    spec = resolve_local_postgres_spec(settings)
    if not enabled or spec is None:
        yield None
        return

    if _database_is_ready(spec.database_url):
        yield spec
        return

    if shutil.which("docker") is None:
        raise RuntimeError(
            "Local PostgreSQL auto-management requires docker on PATH. "
            "Install docker, start PostgreSQL yourself, or run serve with --no-auto-db."
        )

    container = _inspect_container(spec.container_name)
    started_by_this_process = False
    stopped = False

    if container is not None:
        if log is not None:
            state = container.get("State", {})
            if bool(state.get("Running")):
                log(f"Restarting managed PostgreSQL container {spec.container_name}")
            else:
                log(f"Removing stale PostgreSQL container {spec.container_name} before restart")
        _remove_container(spec.container_name, check=False)
        _run_container(spec, log=log)
        started_by_this_process = True
    else:
        _run_container(spec, log=log)
        started_by_this_process = True

    try:
        _wait_until_database_ready(spec.database_url, spec.startup_timeout_seconds)
        yield spec
    except Exception:
        if started_by_this_process:
            _stop_container(spec.container_name, check=False)
            stopped = True
        raise
    finally:
        if started_by_this_process and not stopped:
            if log is not None:
                log(f"Stopping managed PostgreSQL container {spec.container_name}")
            _stop_container(spec.container_name, check=False)
