import subprocess

import pytest

import feature_factory.local_postgres as local_postgres
from feature_factory.config import Settings


def test_managed_local_postgres_is_noop_for_sqlite(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'feature_factory.db'}")

    with local_postgres.managed_local_postgres(settings) as spec:
        assert spec is None


def test_database_ready_probe_converts_sqlalchemy_postgresql_url(monkeypatch) -> None:
    observed: list[str] = []

    class DummyConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_connect(conninfo: str, connect_timeout: int):
        observed.append(conninfo)
        return DummyConnection()

    monkeypatch.setattr(local_postgres.psycopg, "connect", fake_connect)

    assert (
        local_postgres._database_is_ready(
            "postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:55432/feature_factory"
        )
        is True
    )
    assert observed == ["postgresql://feature_factory:feature_factory@127.0.0.1:55432/feature_factory"]


def test_local_postgres_relative_data_dir_is_project_relative(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory",
        local_postgres_data_dir="data/postgres",
    )

    spec = local_postgres.resolve_local_postgres_spec(settings)

    assert spec is not None
    assert spec.data_dir == local_postgres.PROJECT_ROOT / "data" / "postgres"


def test_managed_local_postgres_starts_and_stops_new_container(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory",
        local_postgres_data_dir=tmp_path / "postgres-data",
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: False)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(local_postgres, "_inspect_container", lambda _: None)
    monkeypatch.setattr(local_postgres, "_wait_until_database_ready", lambda *_args, **_kwargs: None)

    def fake_run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(local_postgres, "_run_command", fake_run_command)

    with local_postgres.managed_local_postgres(settings):
        assert (tmp_path / "postgres-data").exists()

    assert commands[0][0:3] == ["docker", "run", "--detach"]
    assert commands[-1] == ["docker", "stop", settings.local_postgres_container_name]


def test_managed_local_postgres_uses_cn_docker_registry_mirror(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory",
        local_postgres_data_dir=tmp_path / "postgres-data",
    )
    commands: list[list[str]] = []

    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: False)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(local_postgres, "_inspect_container", lambda _: None)
    monkeypatch.setattr(local_postgres, "_wait_until_database_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        local_postgres,
        "_run_command",
        lambda args, *, check=True: commands.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    with local_postgres.managed_local_postgres(settings):
        pass

    assert commands[0][-1] == "docker.1ms.run/library/postgres:16-alpine"


def test_managed_local_postgres_uses_external_ready_database_without_docker(monkeypatch) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory"
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: True)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(local_postgres, "_inspect_container", lambda _: None)
    monkeypatch.setattr(local_postgres, "_wait_until_database_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        local_postgres,
        "_run_command",
        lambda args, *, check=True: commands.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    with local_postgres.managed_local_postgres(settings) as spec:
        assert spec is not None

    assert commands == []


def test_managed_local_postgres_reuses_running_ready_container(monkeypatch) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory"
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: True)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(local_postgres, "_inspect_container", lambda _: {"State": {"Running": True}})
    monkeypatch.setattr(local_postgres, "_wait_until_database_ready", lambda *_args, **_kwargs: None)

    def fake_run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(local_postgres, "_run_command", fake_run_command)

    with local_postgres.managed_local_postgres(settings):
        pass

    assert commands == []


def test_managed_local_postgres_recreates_stopped_container(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory",
        local_postgres_data_dir=tmp_path / "postgres-data",
    )
    commands: list[list[str]] = []

    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: False)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(local_postgres, "_inspect_container", lambda _: {"State": {"Running": False}})
    monkeypatch.setattr(local_postgres, "_wait_until_database_ready", lambda *_args, **_kwargs: None)

    def fake_run_command(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(local_postgres, "_run_command", fake_run_command)

    with local_postgres.managed_local_postgres(settings):
        pass

    assert commands[0] == ["docker", "rm", "-f", settings.local_postgres_container_name]
    assert commands[1][0:3] == ["docker", "run", "--detach"]
    assert commands[-1] == ["docker", "stop", settings.local_postgres_container_name]


def test_managed_local_postgres_requires_docker_when_database_is_not_ready(monkeypatch) -> None:
    settings = Settings(
        database_url="postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:5432/feature_factory"
    )

    monkeypatch.setattr(local_postgres, "_database_is_ready", lambda _: False)
    monkeypatch.setattr(local_postgres.shutil, "which", lambda _: None)

    with pytest.raises(RuntimeError, match="docker"):
        with local_postgres.managed_local_postgres(settings):
            pass
