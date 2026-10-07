from __future__ import annotations

import inspect
import os
import re
import select as select_module
import shutil
import stat
import subprocess
import tarfile
import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Callable, TypeVar

from pydantic import SecretStr
from sqlalchemy.orm import Session, sessionmaker

from feature_factory.config import Settings
from feature_factory.docker_mirrors import (
    github_proxy_retry_attempts,
    refresh_github_proxy_urls,
    rotate_failed_github_proxy_urls,
    temporary_git_remote_url as _shared_temporary_git_remote_url,
)
from feature_factory.host_repository_materializer import HostRepositoryMaterializer
from feature_factory.models import GitHubRepository, Stage2Run, Stage2RunResult
from feature_factory.stage2.base_images import list_base_images, should_use_china_mirrors
from feature_factory.stage2.backend import OpenHandsStage2Backend, Stage2Backend, build_stage2_backend
from feature_factory.stage2.planner import PlannerDecision, UnsupportedStage2RepositoryError
from feature_factory.stage2.service import (
    Stage2Service,
    extract_stage2_runtime_snapshot,
    merge_stage2_runtime_snapshot,
    resolve_repo_head_commit,
)
from feature_factory.stage2.validation_feedback import validation_failure_message
from feature_factory.stage2.validator import Stage2ValidationInterrupted, Stage2Validator

T = TypeVar("T")

_GIT_PROGRESS_RE = re.compile(r"^(?P<stage>[A-Za-z][A-Za-z ]+):\s*(?P<percent>\d{1,3})%")
_GIT_OBJECT_COUNT_RE = re.compile(r"\((?P<current>\d+)\s*/\s*(?P<total>\d+)\)")
_GIT_PROGRESS_PERCENT_STEP = 10
_GIT_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
_GIT_REMOTE_MAX_ATTEMPTS = 3
_GIT_REMOTE_RETRY_DELAY_SECONDS = 3.0
_GIT_RETRYABLE_ERROR_MARKERS = (
    "SSL_ERROR_SYSCALL",
    "RPC failed",
    "Transferred a partial file",
    "early EOF",
    "unexpected disconnect",
    "invalid index-pack output",
    "Failed to connect",
    "Connection reset",
    "Connection refused",
    "Operation timed out",
    "command timed out",
    "timed out after",
    "Could not resolve host",
    "GnuTLS recv error",
    "TLS connection was non-properly terminated",
    "Empty reply from server",
    "requested URL returned error: 429",
    "requested URL returned error: 500",
    "requested URL returned error: 502",
    "requested URL returned error: 503",
    "requested URL returned error: 504",
    "requested URL returned error: 520",
    "requested URL returned error: 522",
    "requested URL returned error: 524",
    "curl 18",
    "curl 28",
    "curl 35",
    "curl 56",
    "HTTP/2 stream",
    "The remote end hung up unexpectedly",
    "smudge filter lfs failed",
    "external filter 'git-lfs filter-process' failed",
    "LFS: Repository or object not found",
)


def _safe_extract_tar(archive: tarfile.TarFile, target_dir: Path) -> None:
    target_dir = target_dir.resolve()
    for member in archive.getmembers():
        member_path = (target_dir / member.name).resolve()
        member_path.relative_to(target_dir)
    archive.extractall(target_dir)


def _make_container_writable(path: Path, *, recursive: bool = False) -> None:
    if not path.exists():
        return
    _chmod_container_writable(path)
    if not recursive:
        return
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        _chmod_container_writable(child)


def _chmod_container_writable(path: Path) -> None:
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            desired_mode = current_mode | stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
        else:
            desired_mode = (
                current_mode
                | stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IRGRP
                | stat.S_IWGRP
                | stat.S_IROTH
                | stat.S_IWOTH
            )
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


class Stage2RunInterruptedError(RuntimeError):
    pass


def _parse_git_progress_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("remote:"):
        line = line.removeprefix("remote:").strip()

    match = _GIT_PROGRESS_RE.search(line)
    if match is None:
        return None

    percent = max(0, min(100, int(match.group("percent"))))
    progress: dict[str, Any] = {
        "git_stage": match.group("stage").strip(),
        "percent": percent,
        "raw_line": line,
    }
    count_match = _GIT_OBJECT_COUNT_RE.search(line)
    if count_match is not None:
        progress["current"] = int(count_match.group("current"))
        progress["total"] = int(count_match.group("total"))
    return progress


def _is_retryable_git_remote_error(message: str) -> bool:
    return any(marker.lower() in message.lower() for marker in _GIT_RETRYABLE_ERROR_MARKERS)


def _temporary_git_remote_url(url: str) -> str:
    return _shared_temporary_git_remote_url(url, enabled=should_use_china_mirrors())


def _remove_docker_images(image_refs: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    failed: list[str] = []
    for image_ref in image_refs:
        reference = str(image_ref or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        try:
            completed = subprocess.run(
                ["docker", "image", "rm", "-f", reference],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except Exception:
            failed.append(reference)
            continue
        stderr = str(completed.stderr or "").strip().lower()
        stdout = str(completed.stdout or "").strip().lower()
        if completed.returncode != 0 and "no such image" not in stderr and "no such image" not in stdout:
            failed.append(reference)
    return failed


def _remove_paths(paths: list[str] | tuple[str, ...], *, root_dir: Path | None = None) -> list[str]:
    seen: set[str] = set()
    failed: list[str] = []
    normalized_root = root_dir.expanduser().resolve() if root_dir is not None else None
    for candidate in paths:
        path = Path(str(candidate or "")).expanduser()
        if not str(path):
            continue
        try:
            resolved = path.resolve()
        except Exception:
            continue
        resolved_key = str(resolved)
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        if normalized_root is not None:
            if resolved == normalized_root or normalized_root not in resolved.parents:
                continue
        if not resolved.exists():
            continue
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        except Exception:
            failed.append(str(resolved))
            continue
    return failed


def _event_token_usage_total(payload: dict[str, Any]) -> int:
    usage = payload.get("token_usage") or payload.get("metrics")
    if not isinstance(usage, dict):
        return 0
    explicit_total = usage.get("total_tokens")
    if explicit_total is not None:
        return int(explicit_total or 0)
    return sum(
        int(usage.get(key, 0) or 0)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
    )


def _stage2_runtime_snapshot(settings: Settings) -> dict[str, Any]:
    return {
        "concurrency": {
            "max_concurrent_runs": settings.stage2_default_task_max_concurrent_runs
            or settings.stage2_max_concurrent_runs,
        },
        "planner": {
            "model": settings.stage2_agent_llm_model("planner") or "",
            "base_url": settings.stage2_agent_llm_base_url("planner") or "",
            "api_key": _secret_value(settings.stage2_agent_llm_api_key("planner")),
            "api_key_preview": _secret_preview(settings.stage2_agent_llm_api_key("planner")),
            "preset": settings.stage2_agent_openhands_preset("planner"),
            "max_iterations": settings.stage2_agent_openhands_max_iterations("planner"),
            "timeout_seconds": settings.stage2_agent_timeout("planner"),
        },
        "worker": {
            "model": settings.stage2_agent_llm_model("worker") or "",
            "base_url": settings.stage2_agent_llm_base_url("worker") or "",
            "api_key": _secret_value(settings.stage2_agent_llm_api_key("worker")),
            "api_key_preview": _secret_preview(settings.stage2_agent_llm_api_key("worker")),
            "preset": settings.stage2_agent_openhands_preset("worker"),
            "max_iterations": settings.stage2_agent_openhands_max_iterations("worker"),
            "timeout_seconds": settings.stage2_agent_timeout("worker"),
        },
        "hyperparameters": {
            "max_worker_attempts": settings.stage2_max_worker_attempts,
            "quickcheck_sample_size": settings.stage2_quickcheck_sample_size,
            "entry_file_test_count_min": settings.stage2_entry_file_test_count_min,
            "p2p_file_count_limit": settings.stage2_p2p_file_count_limit,
            "p2p_sample_seed": settings.stage2_p2p_sample_seed,
            "collect_timeout_seconds": settings.stage2_collect_timeout_seconds,
            "run_test_timeout_seconds": settings.stage2_run_test_timeout_seconds,
            "build_timeout_seconds": settings.stage2_build_timeout_seconds,
            "full_validation_timeout_seconds": settings.stage2_full_validation_timeout_seconds,
        },
    }


def _secret_value(secret: SecretStr | None) -> str:
    if secret is None:
        return ""
    return secret.get_secret_value() or ""


def _secret_preview(secret: SecretStr | None) -> str:
    if secret is None:
        return ""
    value = secret.get_secret_value()
    if not value:
        return ""
    return f"{value[:6]}..."


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _int_at_least(value: Any, minimum: int) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= minimum else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _selected_base_image_payload(decision: PlannerDecision) -> dict[str, Any]:
    selected_id = str(decision.base_image or "").strip()
    selected_ref = str(decision.base_image_ref or "").strip()
    for item in decision.base_image_catalog:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("image_id") or "").strip()
        item_ref = str(item.get("image_ref") or "").strip()
        if selected_ref and item_ref == selected_ref:
            return dict(item)
        if selected_id and item_id == selected_id:
            return dict(item)
    return {}


def _settings_with_runtime_snapshot(settings: Settings, runtime_snapshot: dict[str, Any]) -> Settings:
    resolved = settings.model_copy(deep=True)
    planner = dict(runtime_snapshot.get("planner") or {})
    worker = dict(runtime_snapshot.get("worker") or {})
    hyperparameters = dict(runtime_snapshot.get("hyperparameters") or {})

    planner_model = str(planner.get("model") or "").strip()
    worker_model = str(worker.get("model") or "").strip()
    planner_base_url = str(planner.get("base_url") or "").strip()
    worker_base_url = str(worker.get("base_url") or "").strip()
    planner_api_key = str(planner.get("api_key") or "")
    worker_api_key = str(worker.get("api_key") or "")
    planner_has_api_key = "api_key" in planner
    worker_has_api_key = "api_key" in worker
    planner_preset = str(planner.get("preset") or "").strip()
    worker_preset = str(worker.get("preset") or "").strip()
    planner_iterations = _positive_int(planner.get("max_iterations"))
    worker_iterations = _positive_int(worker.get("max_iterations"))
    planner_timeout = _positive_float(planner.get("timeout_seconds"))
    worker_timeout = _positive_float(worker.get("timeout_seconds"))
    max_worker_attempts = _positive_int(hyperparameters.get("max_worker_attempts"))
    quickcheck_sample_size = _positive_int(hyperparameters.get("quickcheck_sample_size"))
    entry_file_test_count_min = _int_at_least(
        hyperparameters.get("entry_file_test_count_min"),
        -1,
    )
    p2p_file_count_limit = _positive_int(hyperparameters.get("p2p_file_count_limit"))
    p2p_sample_seed = str(hyperparameters.get("p2p_sample_seed") or "").strip()
    legacy_command_timeout_seconds = _positive_float(hyperparameters.get("command_timeout_seconds"))
    collect_timeout_seconds = _positive_float(hyperparameters.get("collect_timeout_seconds"))
    run_test_timeout_seconds = _positive_float(hyperparameters.get("run_test_timeout_seconds"))
    build_timeout_seconds = _positive_float(hyperparameters.get("build_timeout_seconds"))
    full_validation_timeout_seconds = _positive_float(
        hyperparameters.get("full_validation_timeout_seconds")
    )

    resolved.stage2_planner_llm_model = planner_model or None
    resolved.stage2_worker_llm_model = worker_model or None
    resolved.stage2_planner_llm_base_url = planner_base_url or None
    resolved.stage2_worker_llm_base_url = worker_base_url or None
    if planner_has_api_key or worker_has_api_key:
        resolved.stage2_llm_api_key = None
    if planner_has_api_key:
        resolved.stage2_planner_llm_api_key = SecretStr(planner_api_key) if planner_api_key else None
    if worker_has_api_key:
        resolved.stage2_worker_llm_api_key = SecretStr(worker_api_key) if worker_api_key else None
    if planner_preset in {"default", "gpt5"}:
        resolved.stage2_planner_openhands_preset = planner_preset
    if worker_preset in {"default", "gpt5"}:
        resolved.stage2_worker_openhands_preset = worker_preset
    if planner_iterations is not None:
        resolved.stage2_planner_openhands_max_iterations = planner_iterations
    if worker_iterations is not None:
        resolved.stage2_worker_openhands_max_iterations = worker_iterations
    if planner_timeout is not None:
        resolved.stage2_planner_agent_timeout_seconds = planner_timeout
    if worker_timeout is not None:
        resolved.stage2_worker_agent_timeout_seconds = worker_timeout
    if max_worker_attempts is not None:
        resolved.stage2_max_worker_attempts = max_worker_attempts
    if quickcheck_sample_size is not None:
        resolved.stage2_quickcheck_sample_size = quickcheck_sample_size
    if entry_file_test_count_min is not None:
        resolved.stage2_entry_file_test_count_min = entry_file_test_count_min
    if "p2p_file_count_limit" in hyperparameters:
        resolved.stage2_p2p_file_count_limit = p2p_file_count_limit
    if "p2p_sample_seed" in hyperparameters:
        resolved.stage2_p2p_sample_seed = p2p_sample_seed or None
    if collect_timeout_seconds is not None:
        resolved.stage2_collect_timeout_seconds = collect_timeout_seconds
    elif legacy_command_timeout_seconds is not None:
        resolved.stage2_collect_timeout_seconds = legacy_command_timeout_seconds
    if run_test_timeout_seconds is not None:
        resolved.stage2_run_test_timeout_seconds = run_test_timeout_seconds
    elif legacy_command_timeout_seconds is not None:
        resolved.stage2_run_test_timeout_seconds = legacy_command_timeout_seconds
    if build_timeout_seconds is not None:
        resolved.stage2_build_timeout_seconds = build_timeout_seconds
    if full_validation_timeout_seconds is not None:
        resolved.stage2_full_validation_timeout_seconds = full_validation_timeout_seconds
    return resolved


class Stage2RunRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        max_workers: int | None = None,
        max_limit: int | None = None,
        app_instance_id: str = "",
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.app_instance_id = str(app_instance_id or "").strip()
        self.max_limit = max_limit or settings.stage2_max_concurrent_runs_cap
        self._max_workers = max_workers or settings.stage2_max_concurrent_runs
        self.executor = ThreadPoolExecutor(max_workers=self.max_limit, thread_name_prefix="ff-stage2")
        self.backend = build_stage2_backend(settings, app_instance_id=self.app_instance_id)
        self._lock = Lock()
        self._capacity = Condition(Lock())
        self._active_runs = 0
        self._futures: dict[str, Future[None]] = {}
        self._running_run_ids: set[str] = set()
        self._running_repository_ids: set[int] = set()
        self._cancel_requested_run_ids: set[str] = set()
        self._repo_cache_locks: dict[str, Lock] = {}
        self._run_backends: dict[str, Stage2Backend] = {}

    def _repository_materializer(self) -> HostRepositoryMaterializer:
        def emit_or_append_run_event(
            run: str,
            *,
            actor: str,
            phase: str | None,
            title: str,
            message: str,
            payload: dict[str, Any] | None = None,
            emit_event: Callable[..., None] | None = None,
        ) -> None:
            if emit_event is not None:
                emit_event(
                    actor=actor,
                    phase=phase,
                    title=title,
                    message=message,
                    payload=dict(payload or {}),
                )
                return
            self._record_event(
                run,
                actor=actor,
                phase=phase,
                title=title,
                message=message,
                payload=payload,
            )

        def run_checked_command(
            args: list[str],
            *,
            timeout_seconds: float = 0.0,
            error_message: str,
        ) -> subprocess.CompletedProcess[str]:
            return self._run_command(args, error_message=error_message)

        def run_command_with_git_progress(
            args: list[str],
            *,
            error_message: str,
            run: str,
            phase: str | None,
            progress_title: str,
            progress_payload: dict[str, Any],
            emit_event: Callable[..., None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
            cwd: Path | None = None,
            timeout_seconds: float = 600.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._run_command_with_git_progress(
                args,
                error_message=error_message,
                run_id=run,
                phase=phase,
                progress_title=progress_title,
                progress_payload=progress_payload,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )

        def run_git_remote_command_with_progress(
            args: list[str],
            *,
            error_message: str,
            run: str,
            phase: str | None,
            progress_title: str,
            retry_title: str,
            progress_payload: dict[str, Any],
            emit_event: Callable[..., None] | None = None,
            cancel_requested: Callable[[], bool] | None = None,
            cleanup_after_failed_attempt: Callable[[], None] | None = None,
            cwd: Path | None = None,
            timeout_seconds: float = 600.0,
        ) -> subprocess.CompletedProcess[str]:
            return self._run_git_remote_command_with_progress(
                args,
                error_message=error_message,
                run_id=run,
                phase=phase,
                progress_title=progress_title,
                retry_title=retry_title,
                progress_payload=progress_payload,
                cleanup_after_failed_attempt=cleanup_after_failed_attempt,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )

        def raise_if_cancel_requested(cancel_requested: Callable[[], bool] | None) -> None:
            if cancel_requested is not None and cancel_requested():
                raise Stage2RunInterruptedError("stage2 run interrupted by user")

        return HostRepositoryMaterializer(
            self.settings,
            emit_or_append_run_event=emit_or_append_run_event,
            run_checked_command=run_checked_command,
            run_command_with_git_progress=run_command_with_git_progress,
            run_git_remote_command_with_progress=run_git_remote_command_with_progress,
            raise_if_cancel_requested=raise_if_cancel_requested,
            temporary_git_remote_url_fn=_temporary_git_remote_url,
            prefer_repository_html_url=True,
        )

    def schedule_run(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
            if future is not None and not future.done():
                return False
            future = self.executor.submit(self._run_future, run_id)
            self._futures[run_id] = future
            future.add_done_callback(lambda completed_future, target=run_id: self._cleanup_future(target, completed_future))
            return True

    def is_run_running(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._running_run_ids

    def is_repository_running(self, repository_id: int) -> bool:
        with self._lock:
            return repository_id in self._running_repository_ids

    def interrupt_run(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
            if future is None or future.done():
                return False
            self._cancel_requested_run_ids.add(run_id)
            cancelled_before_start = future.cancel()
            backend = self._run_backends.get(run_id, self.backend)
        with self._capacity:
            self._capacity.notify_all()
        cancel_backend = getattr(backend, "cancel_run", None)
        if callable(cancel_backend):
            cancel_backend(run_id)
        self._record_event(
            run_id,
            actor="system",
            phase="interrupt",
            title="Stage2 interrupt accepted",
            message="Runner accepted the interrupt request and is stopping this run",
            payload={"run_id": run_id, "cancelled_before_start": cancelled_before_start},
        )
        if cancelled_before_start:
            self._complete_interrupted_run(run_id, repo_checkout_dir=None)
            with self._lock:
                self._cancel_requested_run_ids.discard(run_id)
        return True

    def list_running_repository_ids(self) -> set[int]:
        with self._lock:
            return set(self._running_repository_ids)

    def get_max_workers(self) -> int:
        with self._capacity:
            return self._max_workers

    def set_max_workers(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if max_workers > self.max_limit:
            raise ValueError(f"max_concurrent_runs cannot exceed {self.max_limit}")
        with self._capacity:
            self._max_workers = max_workers
            self.settings.stage2_max_concurrent_runs = max_workers
            self._capacity.notify_all()

    def reload_backend(self) -> None:
        next_backend = build_stage2_backend(self.settings, app_instance_id=self.app_instance_id)
        with self._lock:
            self.backend = next_backend

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        with self._lock:
            active_futures = {
                run_id: future
                for run_id, future in self._futures.items()
                if not future.done()
            }
            backends = {
                run_id: self._run_backends.get(run_id, self.backend)
                for run_id in active_futures
            }
            self._cancel_requested_run_ids.update(active_futures)
        with self._capacity:
            self._capacity.notify_all()
        for run_id, backend in backends.items():
            cancel_backend = getattr(backend, "cancel_run", None)
            if callable(cancel_backend):
                try:
                    cancel_backend(run_id)
                except Exception:
                    continue
        if timeout_seconds is None:
            self.executor.shutdown(wait=True, cancel_futures=True)
            return []
        self.executor.shutdown(wait=False, cancel_futures=True)
        if not active_futures:
            return []
        _, not_done = wait(tuple(active_futures.values()), timeout=max(timeout_seconds, 0.0))
        return [
            run_id
            for run_id, future in active_futures.items()
            if future in not_done and not future.done()
        ]

    def _cleanup_future(self, run_id: str, future: Future[None]) -> None:
        with self._lock:
            active = self._futures.get(run_id)
            if active is future and future.done():
                self._futures.pop(run_id, None)

    def _run_future(self, run_id: str) -> None:
        repository_id: int | None = None
        slot_acquired = False
        try:
            self._raise_if_cancel_requested(run_id)
            run = self._with_service(lambda service: service.get_run(run_id, include_detail=False))
            if not self._acquire_slot(run_id, max_workers=self._run_concurrency_limit(run)):
                self._complete_interrupted_run(run_id, repo_checkout_dir=None)
                with self._lock:
                    self._cancel_requested_run_ids.discard(run_id)
                return
            slot_acquired = True
            run = self._with_service(lambda service: service.mark_run_started(run_id, phase="host_planner"))
            repository_id = run.repository_id
            repository = self._with_service(lambda service: service.get_repository(repository_id))
            with self._lock:
                self._running_run_ids.add(run_id)
                self._running_repository_ids.add(repository.id)
            self._raise_if_cancel_requested(run_id)
            self._execute_run(repository, run)
        except Stage2RunInterruptedError:
            self._complete_interrupted_run(run_id, repo_checkout_dir=None)
        except Exception as exc:  # noqa: BLE001
            try:
                self._with_service(
                    lambda service: service.mark_run_schedule_failed(
                        run_id,
                        error_message=str(exc),
                    )
                )
            except Exception as mark_exc:  # noqa: BLE001
                print(
                    "[feature_factory] stage2 runner failed before the run could be updated "
                    f"for {run_id}: primary={exc}; secondary={mark_exc}",
                    flush=True,
                )
        finally:
            with self._lock:
                self._run_backends.pop(run_id, None)
                self._running_run_ids.discard(run_id)
                self._cancel_requested_run_ids.discard(run_id)
                if repository_id is not None:
                    self._running_repository_ids.discard(repository_id)
            if slot_acquired:
                self._release_slot()

    def _execute_run(self, repository: GitHubRepository, run: Stage2Run) -> None:
        run_id = run.id
        backend = self._backend_for_run(run)
        workspace_dir = self._prepare_workspace(run_id)
        repo_checkout_dir = workspace_dir / "repo"
        requested_commit_sha = self._load_run_target_commit_sha(run_id)
        try:
            self._with_service(
                lambda service: service.update_run_plan(
                    run_id,
                    phase="host_planner",
                    workspace_path=str(workspace_dir),
                )
            )
            self._record_event(
                run_id,
                actor="system",
                phase="host_planner",
                title="Workspace prepared",
                message=f"Prepared stage2 workspace at {workspace_dir}",
                payload={"workspace_path": str(workspace_dir)},
            )
            target_commit_sha, cache_dir = self._prepare_repository_checkout(
                repository,
                run_id=run_id,
                checkout_dir=repo_checkout_dir,
                requested_commit_sha=requested_commit_sha,
            )
            self._raise_if_cancel_requested(run_id)
            self._record_event(
                run_id,
                actor="planner",
                phase="host_planner",
                title="Repository checkout prepared",
                message=f"Prepared {repository.full_name} at {target_commit_sha or 'unknown commit'} from shared cache",
                payload={
                    "repository_full_name": repository.full_name,
                    "target_commit_sha": target_commit_sha,
                    "requested_commit_sha": requested_commit_sha,
                    "cache_path": str(cache_dir),
                    "checkout_path": str(repo_checkout_dir),
                },
            )
            resume_checkpoint = self._resume_checkpoint_payload(run)
            resume_kind = self._resume_kind(run)
            if resume_checkpoint:
                self._restore_worker_checkpoint(
                    run_id,
                    workspace_dir=workspace_dir,
                    checkpoint=resume_checkpoint,
                )
            if resume_kind == "full_validation":
                runtime_snapshot = dict(run.runtime_snapshot_json or {})
                self._with_service(
                    lambda service: service.update_run_plan(
                        run_id,
                        phase="validator_full",
                        target_commit_sha=target_commit_sha,
                        base_image=run.base_image,
                        planner_model=run.planner_model,
                        planner_token_usage=run.planner_token_usage,
                        worker_model=run.worker_model,
                        planner_guidance=run.planner_guidance,
                        workspace_path=str(workspace_dir),
                    )
                )
                self._record_event(
                    run_id,
                    actor="system",
                    phase="validator_full",
                    title="Full validation resume started",
                    message=(
                        "Restored the smoke-passed workspace checkpoint and resumed host "
                        "full validation without reopening the worker agent."
                    ),
                    payload={
                        "resume_source_run_id": runtime_snapshot.get("resume_source_run_id"),
                        "resume_source_attempt_index": runtime_snapshot.get("resume_source_attempt_index"),
                        "trigger_kind": run.trigger_kind,
                    },
                )
                final_validation = self._run_host_full_validation(
                    run_id,
                    workspace_dir=workspace_dir,
                    backend=backend,
                    collect_report=dict(run.collect_report_json or {}),
                    smoke_report=dict(run.smoke_report_json or {}),
                    start_message=(
                        "Resuming full validation from the saved smoke-passed checkpoint "
                        "without reopening the worker agent."
                    ),
                )
                final_status = str(final_validation.get("status") or "unvalidated")
                if final_status == "passed":
                    self._record_event(
                        run_id,
                        actor="validator",
                        phase="validator_full",
                        title="Resumed full validation passed",
                        message="Artifacts passed full validation after resuming from the saved smoke checkpoint",
                        payload={"status": final_status},
                    )
                    self._complete_run(
                        run_id,
                        repo_checkout_dir=repo_checkout_dir,
                        result=Stage2RunResult.passed.value,
                    )
                    return

                failure_message = validation_failure_message(
                    final_validation=final_validation,
                    description="resumed full validation failed",
                )
                self._record_event(
                    run_id,
                    actor="validator",
                    phase="validator_full",
                    title="Resumed full validation failed",
                    message=failure_message,
                    payload={"status": final_status},
                )
                self._complete_run(
                    run_id,
                    repo_checkout_dir=repo_checkout_dir,
                    result=Stage2RunResult.failed.value,
                    error_message=failure_message,
                )
                return
            reused_planner = False
            decision = self._reuse_planner_decision(run)
            if decision is None:
                decision = backend.plan_repository(
                    repo_checkout_dir,
                    repository,
                    workspace_dir=workspace_dir,
                    emit_event=lambda event: self._record_backend_event(run_id, event),
                )
            else:
                reused_planner = True
            self._raise_if_cancel_requested(run_id)
            self._with_service(
                lambda service: service.update_run_plan(
                    run_id,
                    phase="host_planner",
                    target_commit_sha=target_commit_sha,
                    base_image=decision.base_image,
                    base_image_ref=decision.base_image_ref,
                    selected_base_image=_selected_base_image_payload(decision),
                    planner_model=decision.planner_model,
                    planner_token_usage=decision.planner_token_usage,
                    planner_guidance=decision.guidance,
                    workspace_path=str(workspace_dir),
                )
            )
            if decision.status == "ready":
                self._with_service(
                    lambda service: service.update_run_plan(
                        run_id,
                        phase="container_worker",
                        target_commit_sha=target_commit_sha,
                        base_image=decision.base_image,
                        base_image_ref=decision.base_image_ref,
                        selected_base_image=_selected_base_image_payload(decision),
                        planner_model=decision.planner_model,
                        planner_token_usage=decision.planner_token_usage,
                        worker_model=decision.worker_model,
                        planner_guidance=decision.guidance,
                        workspace_path=str(workspace_dir),
                    )
                )
                self._record_event(
                    run_id,
                    actor="planner",
                    phase="host_planner",
                    title="Planner guidance reused" if reused_planner else "Planner guidance generated",
                    message=decision.guidance or "",
                    payload={
                        "status": decision.status,
                        "base_image": decision.base_image,
                        "base_image_ref": decision.base_image_ref,
                        "base_image_catalog": decision.base_image_catalog,
                        "planner_model": decision.planner_model,
                        "planner_token_usage": decision.planner_token_usage,
                        "reused_planner": reused_planner,
                        "trigger_kind": run.trigger_kind,
                    },
                )
                worker_result = backend.run_worker_attempt(
                    repo_checkout_dir,
                    repository,
                    workspace_dir=workspace_dir,
                    decision=decision,
                    attempt_index=1,
                    resume_checkpoint=resume_checkpoint,
                    emit_event=lambda event: self._record_backend_event(run_id, event),
                )
                self._with_service(
                    lambda service: service.update_run_plan(
                        run_id,
                        phase="container_worker",
                        worker_model=worker_result.model,
                        worker_token_usage=worker_result.token_usage,
                    )
                )
                self._record_event(
                    run_id,
                    actor="worker",
                    phase="container_worker",
                    title="Worker conversation completed",
                    message=worker_result.description,
                    payload={
                        "validation_attempt_count": len(worker_result.validation_attempts),
                        "final_validation": worker_result.final_validation,
                        "worker_model": worker_result.model,
                        "worker_token_usage": worker_result.token_usage,
                    },
                )
                if worker_result.validation_attempts and not worker_result.validation_attempts_persisted_live:
                    for attempt in worker_result.validation_attempts:
                        attempt_index = int(attempt.get("attempt_index") or 0)
                        dockerfile_text = str(attempt.get("dockerfile_text") or "")
                        run_script_text = str(attempt.get("run_script_text") or "")
                        if dockerfile_text or run_script_text:
                            self._with_service(
                                lambda service, dockerfile_text=dockerfile_text, run_script_text=run_script_text, attempt_index=attempt_index: service.store_artifacts(
                                    run_id,
                                    dockerfile_text=dockerfile_text,
                                    run_script_text=run_script_text,
                                    attempt_index=attempt_index or None,
                                )
                            )
                        collect_report = dict(attempt.get("collect_report") or {})
                        if collect_report:
                            self._with_service(
                                lambda service, collect_report=collect_report, attempt_index=attempt_index: service.store_collect_report(
                                    run_id,
                                    report=collect_report,
                                    attempt_index=attempt_index or None,
                                )
                            )
                        smoke_report = dict(attempt.get("smoke_report") or {})
                        if smoke_report:
                            self._with_service(
                                lambda service, smoke_report=smoke_report, attempt_index=attempt_index: service.store_smoke_report(
                                    run_id,
                                    report=smoke_report,
                                    attempt_index=attempt_index or None,
                                )
                            )
                        full_report = dict(attempt.get("full_report") or {})
                        if full_report:
                            self._with_service(
                                lambda service, full_report=full_report, attempt_index=attempt_index: service.store_full_report(
                                    run_id,
                                    report=full_report,
                                    attempt_index=attempt_index or None,
                                )
                            )
                        checkpoint = dict(attempt.get("checkpoint") or {})
                        if checkpoint:
                            self._with_service(
                                lambda service, attempt_index=attempt_index, checkpoint=checkpoint: service.store_validation_checkpoint(
                                    run_id,
                                    attempt_index=attempt_index,
                                    checkpoint=checkpoint,
                                )
                            )
                elif not worker_result.validation_attempts:
                    self._with_service(
                        lambda service: service.store_artifacts(
                            run_id,
                            dockerfile_text=worker_result.dockerfile_text,
                            run_script_text=worker_result.run_script_text,
                        )
                    )

                final_validation = dict(worker_result.final_validation or {})
                if worker_result.post_agent_full_validation_required:
                    final_validation = self._run_host_full_validation(
                        run_id,
                        workspace_dir=workspace_dir,
                        backend=backend,
                        collect_report=dict((worker_result.final_validation or {}).get("collect_report") or {}),
                        smoke_report=dict((worker_result.final_validation or {}).get("smoke_report") or {}),
                        start_message=(
                            "Smoke validation passed. The worker agent run ended, and the host "
                            "is continuing full validation outside the agent time budget."
                        ),
                    )

                final_full_report = dict((worker_result.final_validation or {}).get("full_report") or {})
                if (
                    final_full_report
                    and not worker_result.validation_attempts_persisted_live
                    and not worker_result.post_agent_full_validation_required
                ):
                    self._with_service(
                        lambda service: service.store_full_report(
                            run_id,
                            report=final_full_report,
                        )
                    )

                final_status = str(final_validation.get("status") or "unvalidated")
                if final_status == "passed":
                    self._record_event(
                        run_id,
                        actor="validator",
                        phase="validator_full" if worker_result.post_agent_full_validation_required else "container_worker",
                        title="Worker validation passed",
                        message="Worker artifacts passed full validation",
                        payload={"status": final_status},
                    )
                    self._complete_run(
                        run_id,
                        repo_checkout_dir=repo_checkout_dir,
                        result=Stage2RunResult.passed.value,
                    )
                    return

                failure_message = validation_failure_message(
                    final_validation=final_validation,
                    description=worker_result.description,
                )
                self._record_event(
                    run_id,
                    actor="validator",
                    phase="validator_full" if worker_result.post_agent_full_validation_required else "container_worker",
                    title="Worker validation failed",
                    message=failure_message,
                    payload={"status": final_status},
                )
                self._complete_run(
                    run_id,
                    repo_checkout_dir=repo_checkout_dir,
                    result=Stage2RunResult.failed.value,
                    error_message=failure_message,
                )
                return

            if decision.status == "abandoned":
                self._record_event(
                    run_id,
                    actor="planner",
                    phase="host_planner",
                    title="Planner abandoned repository",
                    message=decision.reason or "Planner marked this repository as unsuitable for stage2",
                    payload={
                        "status": decision.status,
                        "target_commit_sha": target_commit_sha,
                        "planner_model": decision.planner_model,
                        "planner_token_usage": decision.planner_token_usage,
                    },
                )
                self._complete_run(
                    run_id,
                    repo_checkout_dir=repo_checkout_dir,
                    result=Stage2RunResult.abandoned.value,
                    error_message=decision.reason or "planner abandoned repository",
                )
                return

            if decision.status == "defect":
                self._record_event(
                    run_id,
                    actor="planner",
                    phase="host_planner",
                    title="Planner reported base image defect",
                    message=decision.reason or "Planner reported the base image catalog is inadequate for this repository",
                    payload={
                        "status": decision.status,
                        "target_commit_sha": target_commit_sha,
                        "planner_model": decision.planner_model,
                        "planner_token_usage": decision.planner_token_usage,
                        "upd_dockerfile": decision.upd_dockerfile,
                    },
                )
                self._complete_run(
                    run_id,
                    repo_checkout_dir=repo_checkout_dir,
                    result=Stage2RunResult.defect.value,
                    error_message=decision.reason or "planner reported base image catalog defect",
                )
                return

            raise RuntimeError(f"unsupported planner status: {decision.status}")
        except UnsupportedStage2RepositoryError as exc:
            self._record_event(
                run_id,
                actor="planner",
                phase="host_planner",
                title="Unsupported repository",
                message=str(exc),
                payload={"repository": repository.full_name},
            )
            self._complete_run(
                run_id,
                repo_checkout_dir=repo_checkout_dir,
                passed=False,
                error_message=str(exc),
            )
        except (RuntimeError, subprocess.SubprocessError, Exception) as exc:
            if self._is_cancel_requested(run_id):
                self._complete_interrupted_run(run_id, repo_checkout_dir=repo_checkout_dir)
                return
            self._record_event(
                run_id,
                actor="system",
                phase="failed",
                title="Stage2 run failed",
                message=str(exc),
                payload={"repository": repository.full_name},
            )
            self._complete_run(
                run_id,
                repo_checkout_dir=repo_checkout_dir,
                passed=False,
                error_message=str(exc),
            )

    def _run_host_full_validation(
        self,
        run_id: str,
        *,
        workspace_dir: Path,
        backend: Stage2Backend,
        collect_report: dict[str, Any],
        smoke_report: dict[str, Any],
        start_message: str,
    ) -> dict[str, Any]:
        self._raise_if_cancel_requested(run_id)
        self._with_service(
            lambda service: service.update_run_plan(
                run_id,
                phase="validator_full",
            )
        )
        self._record_event(
            run_id,
            actor="validator",
            phase="validator_full",
            title="Host full validation started",
            message=start_message,
            payload={},
        )
        validator_settings = getattr(backend, "settings", self.settings)
        validator = Stage2Validator(validator_settings)
        run_full_kwargs: dict[str, Any] = {
            "run_id": workspace_dir.name,
            "workspace_dir": workspace_dir,
            "cancel_requested": lambda: self._is_cancel_requested(run_id),
        }
        if "emit_event" in inspect.signature(validator.run_full).parameters:
            run_full_kwargs["emit_event"] = lambda title, message, payload: self._record_event(
                run_id,
                actor="validator",
                phase="validator_full",
                title=title,
                message=message,
                payload=payload,
            )
        try:
            outcome = validator.run_full(**run_full_kwargs)
        except Stage2ValidationInterrupted as exc:
            raise Stage2RunInterruptedError(str(exc)) from exc
        self._raise_if_cancel_requested(run_id)
        self._with_service(
            lambda service: service.store_full_report(
                run_id,
                report=dict(outcome.report or {}),
            )
        )
        return {
            "status": "passed" if outcome.passed else "failed",
            "phase": str((outcome.report or {}).get("phase") or "full"),
            "passed": bool(outcome.passed),
            "collect_report": dict(collect_report or {}),
            "smoke_report": dict(smoke_report or {}),
            "full_report": dict(outcome.report or {}),
        }

    def _current_backend(self) -> Stage2Backend:
        with self._lock:
            return self.backend

    def _backend_for_run(self, run: Stage2Run) -> Stage2Backend:
        with self._lock:
            shared_backend = self.backend
        settings_snapshot = self.settings.model_copy(deep=True)
        existing_runtime_snapshot = extract_stage2_runtime_snapshot(dict(run.runtime_snapshot_json or {}))
        runtime_snapshot_seed = _stage2_runtime_snapshot(settings_snapshot)
        inherits_runtime_snapshot = bool(str((run.runtime_snapshot_json or {}).get("resume_source_run_id") or "").strip())
        if inherits_runtime_snapshot and isinstance(existing_runtime_snapshot.get("hyperparameters"), dict):
            runtime_snapshot_seed.pop("hyperparameters", None)
        for agent_key in ("planner", "worker"):
            existing_agent_snapshot = existing_runtime_snapshot.get(agent_key)
            if isinstance(existing_agent_snapshot, dict):
                if "api_key" not in existing_agent_snapshot:
                    runtime_snapshot_seed.get(agent_key, {}).pop("api_key", None)
                if "api_key_preview" not in existing_agent_snapshot:
                    runtime_snapshot_seed.get(agent_key, {}).pop("api_key_preview", None)
        runtime_snapshot = merge_stage2_runtime_snapshot(
            runtime_snapshot_seed,
            existing_runtime_snapshot,
        )
        merged_runtime_snapshot = merge_stage2_runtime_snapshot(
            dict(run.runtime_snapshot_json or {}),
            runtime_snapshot,
        )
        run.runtime_snapshot_json = merged_runtime_snapshot
        self._with_service(
            lambda service: service.update_run_runtime_snapshot(
                run.id,
                runtime_snapshot=runtime_snapshot,
            )
        )
        settings_snapshot = _settings_with_runtime_snapshot(settings_snapshot, runtime_snapshot)
        backend = (
            build_stage2_backend(settings_snapshot, app_instance_id=self.app_instance_id)
            if isinstance(shared_backend, OpenHandsStage2Backend)
            else shared_backend
        )
        with self._lock:
            self._run_backends[run.id] = backend
        return backend

    def _with_service(self, callback: Callable[[Stage2Service], T]) -> T:
        session = self.session_factory()
        try:
            service = Stage2Service(session)
            result = callback(service)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _record_event(
        self,
        run_id: str,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._with_service(
            lambda service: service.record_event(
                run_id,
                actor=actor,
                phase=phase,
                title=title,
                message=message,
                payload=payload,
            )
        )

    def _record_backend_event(self, run_id: str, event) -> None:
        payload = dict(event.payload or {})
        self._record_event(
            run_id,
            actor=event.actor,
            phase=event.phase,
            title=event.title,
            message=event.message,
            payload=payload,
        )
        token_usage = _event_token_usage_total(payload)
        if token_usage <= 0:
            return
        phase = event.phase or ("container_worker" if "worker" in str(event.actor) else "host_planner")
        if "worker" in str(event.actor) or phase == "container_worker":
            self._with_service(
                lambda service: service.update_run_plan(
                    run_id,
                    phase=phase,
                    worker_token_usage=token_usage,
                )
            )
            return
        self._with_service(
            lambda service: service.update_run_plan(
                run_id,
                phase=phase,
                planner_token_usage=token_usage,
            )
        )

    def _complete_run(
        self,
        run_id: str,
        *,
        repo_checkout_dir: Path,
        passed: bool | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._cleanup_workspace_repo(run_id, repo_checkout_dir=repo_checkout_dir)
        self._with_service(
            lambda service: service.mark_run_completed(
                run_id,
                passed=passed,
                result=result,
                error_message=error_message,
            )
        )
        completion_result = (
            result
            if result is not None
            else (
                Stage2RunResult.passed.value
                if passed is True
                else (Stage2RunResult.failed.value if passed is False else None)
            )
        )
        if completion_result == Stage2RunResult.passed.value:
            self._cleanup_successful_run_worker_checkpoints(run_id)
        self._cleanup_terminal_run_validator_images(run_id)

    def _complete_interrupted_run(self, run_id: str, *, repo_checkout_dir: Path | None) -> None:
        if repo_checkout_dir is not None:
            self._cleanup_workspace_repo(run_id, repo_checkout_dir=repo_checkout_dir)
        self._with_service(lambda service: service.mark_run_interrupted(run_id))
        self._cleanup_terminal_run_validator_images(run_id)

    def _cleanup_workspace_repo(self, run_id: str, *, repo_checkout_dir: Path) -> None:
        if not repo_checkout_dir.exists():
            return
        self._record_event(
            run_id,
            actor="system",
            phase="cleanup",
            title="Workspace repo cleanup started",
            message=f"Removing checkout at {repo_checkout_dir}",
            payload={"checkout_path": str(repo_checkout_dir)},
        )
        try:
            shutil.rmtree(repo_checkout_dir)
        except Exception as exc:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Workspace repo cleanup failed",
                message=str(exc),
                payload={"checkout_path": str(repo_checkout_dir)},
            )
            return
        self._record_event(
            run_id,
            actor="system",
            phase="cleanup",
            title="Workspace repo cleaned",
            message=f"Removed checkout at {repo_checkout_dir}",
            payload={"checkout_path": str(repo_checkout_dir)},
        )

    def _cleanup_terminal_run_validator_images(self, run_id: str) -> None:
        image_refs = self._with_service(lambda service: service.validator_image_refs(run_id))
        failed_image_refs = list(_remove_docker_images(image_refs) or [])
        if not failed_image_refs:
            return
        try:
            self._with_service(
                lambda service: service.record_terminal_validator_image_cleanup_failure(
                    run_id,
                    image_refs=list(image_refs),
                    failed_image_refs=failed_image_refs,
                )
            )
        except Exception as exc:
            print(
                "[feature_factory] failed to persist terminal validator image cleanup "
                f"failure for {run_id}: {exc}",
                flush=True,
            )

    def _cleanup_successful_run_worker_checkpoints(self, run_id: str) -> None:
        try:
            cleanup = self._with_service(lambda service: service.worker_checkpoint_cleanup_targets(run_id))
        except Exception as exc:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Worker checkpoint cleanup failed",
                message=str(exc),
                payload={},
            )
            return

        checkpoint_paths = list(cleanup.get("checkpoint_paths") or [])
        checkpoint_image_refs = list(cleanup.get("checkpoint_docker_image_refs") or [])
        failed_paths = list(
            _remove_paths(
                checkpoint_paths,
                root_dir=Path(self.settings.stage2_workspace_dir).expanduser().resolve(),
            )
            or []
        )
        failed_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
        if failed_paths or failed_image_refs:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Worker checkpoint cleanup incomplete",
                message="Some worker checkpoint assets could not be removed after successful completion",
                payload={
                    "checkpoint_paths": checkpoint_paths,
                    "checkpoint_docker_image_refs": checkpoint_image_refs,
                    "failed_paths": failed_paths,
                    "failed_checkpoint_docker_image_refs": failed_image_refs,
                },
            )
            return

        self._with_service(lambda service: service.clear_worker_checkpoints(run_id))
        if checkpoint_paths or checkpoint_image_refs:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Worker checkpoint cleaned",
                message="Released worker checkpoint files and docker images after successful completion",
                payload={
                    "checkpoint_paths": checkpoint_paths,
                    "checkpoint_docker_image_refs": checkpoint_image_refs,
                },
            )

    def _load_run_target_commit_sha(self, run_id: str) -> str | None:
        return self._with_service(lambda service: service.get_run(run_id).target_commit_sha)

    def _reuse_planner_decision(self, run: Stage2Run) -> PlannerDecision | None:
        if run.trigger_kind not in {"rerun_worker", "resume_worker"}:
            return None
        base_image = str(run.base_image or "").strip()
        guidance = str(run.planner_guidance or "").strip()
        if not base_image or not guidance:
            return None

        base_image_catalog = [image.to_payload() for image in list_base_images()]
        runtime_snapshot = extract_stage2_runtime_snapshot(dict(run.runtime_snapshot_json or {}))
        worker_runtime = dict(runtime_snapshot.get("worker") or {})
        selection_runtime = dict(runtime_snapshot.get("selection") or {})
        worker_model = str(
            worker_runtime.get("model")
            or self.settings.stage2_agent_llm_model("worker")
            or run.worker_model
            or ""
        )
        stored_selected_base_image = dict(selection_runtime.get("selected_base_image") or {})
        selected_base_image = (
            stored_selected_base_image
            if stored_selected_base_image
            else next(
                (
                    payload
                    for payload in base_image_catalog
                    if (
                        str(payload.get("image_ref") or "") == str(selection_runtime.get("base_image_ref") or "")
                        or str(payload.get("image_id") or "") == base_image
                    )
                ),
                None,
            )
        )
        base_image_ref = str(
            (selected_base_image or {}).get("image_ref")
            or selection_runtime.get("base_image_ref")
            or base_image
        )
        reused_catalog = [dict(selected_base_image)] if isinstance(selected_base_image, dict) and selected_base_image else base_image_catalog
        return PlannerDecision(
            status="ready",
            base_image=base_image,
            base_image_ref=base_image_ref,
            planner_model=str(run.planner_model or "reused-planner"),
            planner_token_usage=0,
            worker_model=worker_model,
            worker_token_usage=0,
            guidance=guidance,
            base_image_catalog=reused_catalog,
        )

    def _resume_checkpoint_payload(self, run: Stage2Run) -> dict[str, Any]:
        snapshot = dict(run.runtime_snapshot_json or {})
        checkpoint = dict(snapshot.get("resume_checkpoint") or {})
        return checkpoint

    def _resume_kind(self, run: Stage2Run) -> str:
        snapshot = dict(run.runtime_snapshot_json or {})
        resume_kind = str(snapshot.get("resume_kind") or "").strip()
        if resume_kind:
            return resume_kind
        if run.trigger_kind == "resume_full_validation":
            return "full_validation"
        if run.trigger_kind == "resume_worker":
            return "worker"
        return ""

    def _restore_worker_checkpoint(
        self,
        run_id: str,
        *,
        workspace_dir: Path,
        checkpoint: dict[str, Any],
    ) -> None:
        snapshot_path = Path(str(checkpoint.get("workspace_snapshot_path") or "")).expanduser()
        if not snapshot_path.exists():
            raise RuntimeError(f"resume checkpoint workspace snapshot is missing: {snapshot_path}")
        self._record_event(
            run_id,
            actor="system",
            phase="container_worker",
            title="Worker checkpoint restore started",
            message=f"Restoring worker workspace snapshot from {snapshot_path}",
            payload={
                "workspace_snapshot_path": str(snapshot_path),
                "attempt_index": int(checkpoint.get("attempt_index") or 0),
            },
        )
        with tarfile.open(snapshot_path, "r:gz") as archive:
            _safe_extract_tar(archive, workspace_dir)
        _make_container_writable(workspace_dir, recursive=True)
        self._record_event(
            run_id,
            actor="system",
            phase="container_worker",
            title="Worker checkpoint restored",
            message="Restored the saved worker workspace snapshot before resuming the worker agent",
            payload={
                "workspace_snapshot_path": str(snapshot_path),
                "attempt_index": int(checkpoint.get("attempt_index") or 0),
            },
        )

    def _prepare_workspace(self, run_id: str) -> Path:
        self._raise_if_cancel_requested(run_id)
        workspace_dir = Path(self.settings.stage2_workspace_dir).expanduser() / "runs" / run_id
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        return workspace_dir

    def _prepare_repository_checkout(
        self,
        repository: GitHubRepository,
        *,
        run_id: str,
        checkout_dir: Path,
        requested_commit_sha: str | None,
    ) -> tuple[str | None, Path]:
        self._raise_if_cancel_requested(run_id)
        cache_dir = self._repository_cache_dir(repository)
        cache_lock = self._repo_cache_lock(cache_dir)
        with cache_lock:
            self._sync_repository_cache(repository, cache_dir, run_id=run_id)
            self._raise_if_cancel_requested(run_id)
            target_commit_sha = self._resolve_cached_target_commit(
                repository,
                cache_dir=cache_dir,
                requested_commit_sha=requested_commit_sha,
                run_id=run_id,
            )
            self._raise_if_cancel_requested(run_id)
            self._materialize_checkout_from_cache(
                repository,
                cache_dir=cache_dir,
                checkout_dir=checkout_dir,
                target_commit_sha=target_commit_sha,
                run_id=run_id,
            )
        _make_container_writable(checkout_dir, recursive=True)
        return target_commit_sha, cache_dir

    def _repository_cache_dir(self, repository: GitHubRepository) -> Path:
        return self._repository_materializer().repository_cache_dir(repository)

    def _repo_cache_lock(self, cache_dir: Path) -> Lock:
        return self._repository_materializer().repo_cache_lock(cache_dir)

    def _sync_repository_cache(self, repository: GitHubRepository, cache_dir: Path, *, run_id: str) -> None:
        self._raise_if_cancel_requested(run_id)
        self._repository_materializer().sync_repository_cache(
            run_id,
            repository=repository,
            cache_dir=cache_dir,
            phase="host_planner",
            cancel_requested=lambda: self._is_cancel_requested(run_id),
        )

    def _repository_cache_branch(self, repository: GitHubRepository) -> str | None:
        return self._repository_materializer().repository_cache_branch(repository)

    def _repository_cache_rebuild_reason(self, cache_dir: Path, *, default_branch: str | None) -> str | None:
        return self._repository_materializer().repository_cache_rebuild_reason(
            cache_dir,
            default_branch=default_branch,
        )

    def _create_repository_cache(
        self,
        repository: GitHubRepository,
        cache_dir: Path,
        *,
        default_branch: str | None,
        run_id: str,
    ) -> None:
        self._repository_materializer().create_repository_cache(
            run_id,
            repository=repository,
            cache_dir=cache_dir,
            default_branch=default_branch,
            phase="host_planner",
            cancel_requested=lambda: self._is_cancel_requested(run_id),
        )

    def _refresh_repository_cache(
        self,
        repository: GitHubRepository,
        cache_dir: Path,
        *,
        default_branch: str | None,
        run_id: str,
    ) -> None:
        self._repository_materializer().refresh_repository_cache(
            run_id,
            repository=repository,
            cache_dir=cache_dir,
            default_branch=default_branch,
            phase="host_planner",
            cancel_requested=lambda: self._is_cancel_requested(run_id),
        )

    def _git_config_value(self, cache_dir: Path, key: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={cache_dir}", "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return value or None

    def _try_symbolic_ref(self, cache_dir: Path, revision: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={cache_dir}", "symbolic-ref", "-q", revision],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        resolved = completed.stdout.strip()
        return resolved or None

    def _resolve_cached_target_commit(
        self,
        repository: GitHubRepository,
        *,
        cache_dir: Path,
        requested_commit_sha: str | None,
        run_id: str,
    ) -> str | None:
        return self._repository_materializer().resolve_cached_target_commit(
            run_id,
            repository=repository,
            cache_dir=cache_dir,
            requested_commit_sha=requested_commit_sha,
            phase="host_planner",
            cancel_requested=lambda: self._is_cancel_requested(run_id),
            resolve_head_commit=resolve_repo_head_commit,
            try_rev_parse_fn=self._try_rev_parse,
            error_factory=RuntimeError,
        )

    def _materialize_checkout_from_cache(
        self,
        repository: GitHubRepository,
        *,
        cache_dir: Path,
        checkout_dir: Path,
        target_commit_sha: str | None,
        run_id: str,
    ) -> None:
        self._repository_materializer().materialize_checkout_from_cache(
            run_id,
            repository=repository,
            cache_dir=cache_dir,
            checkout_dir=checkout_dir,
            target_commit_sha=str(target_commit_sha or ""),
            phase="host_planner",
            cancel_requested=lambda: self._is_cancel_requested(run_id),
        )

    def _try_rev_parse(self, cache_dir: Path, revision: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={cache_dir}", "rev-parse", "--verify", revision],
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        resolved = completed.stdout.strip()
        return resolved or None

    def _run_command(self, args: list[str], *, error_message: str) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(args, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            details = completed.stderr.strip() or completed.stdout.strip() or "command failed"
            raise RuntimeError(f"{error_message}: {details}")
        return completed

    def _run_git_remote_command_with_progress(
        self,
        args: list[str],
        *,
        error_message: str,
        run_id: str,
        phase: str | None,
        progress_title: str,
        retry_title: str,
        progress_payload: dict[str, Any],
        cleanup_after_failed_attempt: Callable[[], None] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        max_attempts = github_proxy_retry_attempts(args, minimum=_GIT_REMOTE_MAX_ATTEMPTS)
        for attempt in range(1, max_attempts + 1):
            self._raise_if_cancel_requested(run_id)
            refresh_github_proxy_urls(args)
            payload = dict(progress_payload)
            payload.update(
                {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }
            )
            try:
                return self._run_command_with_git_progress(
                    args,
                    error_message=error_message,
                    run_id=run_id,
                    phase=phase,
                    progress_title=progress_title,
                    progress_payload=payload,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                )
            except RuntimeError as exc:
                if cleanup_after_failed_attempt is not None:
                    cleanup_after_failed_attempt()
                retryable = _is_retryable_git_remote_error(str(exc))
                failed_proxy, next_proxy = (
                    rotate_failed_github_proxy_urls(args) if retryable else (None, None)
                )
                if attempt >= max_attempts or not retryable:
                    raise
                self._record_event(
                    run_id,
                    actor="system",
                    phase=phase,
                    title=retry_title,
                    message=(
                        "Git remote operation failed with a retryable network error; "
                        f"retrying attempt {attempt + 1}/{max_attempts}"
                    ),
                    payload={
                        **progress_payload,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "retry_delay_seconds": _GIT_REMOTE_RETRY_DELAY_SECONDS,
                        "failed_github_proxy_prefix": failed_proxy,
                        "next_github_proxy_prefix": next_proxy,
                        "error": str(exc)[-4000:],
                    },
                )
                for _ in range(int(_GIT_REMOTE_RETRY_DELAY_SECONDS * 10)):
                    self._raise_if_cancel_requested(run_id)
                    time.sleep(0.1)
        raise RuntimeError(error_message)

    def _run_command_with_git_progress(
        self,
        args: list[str],
        *,
        error_message: str,
        run_id: str,
        phase: str | None,
        progress_title: str,
        progress_payload: dict[str, Any],
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        if process.stdout is None:
            raise RuntimeError(f"{error_message}: failed to capture git output")

        output_lines: list[str] = []
        line_buffer: list[str] = []
        last_emit_at = 0.0
        last_percent_by_stage: dict[str, int] = {}
        started_at = time.monotonic()

        def flush_line() -> None:
            nonlocal last_emit_at
            line = "".join(line_buffer).strip()
            line_buffer.clear()
            if not line:
                return
            output_lines.append(line)
            progress = _parse_git_progress_line(line)
            if progress is None:
                return
            if not self._should_emit_git_progress(progress, last_percent_by_stage, last_emit_at):
                return
            last_emit_at = time.monotonic()
            last_percent_by_stage[progress["git_stage"]] = int(progress["percent"])
            payload = dict(progress_payload)
            payload.update(progress)
            self._record_event(
                run_id,
                actor="system",
                phase=phase,
                title=progress_title,
                message=progress["raw_line"],
                payload=payload,
            )

        while True:
            if self._is_cancel_requested(run_id):
                process.kill()
                process.wait(timeout=5)
                raise Stage2RunInterruptedError("stage2 run interrupted by user")
            if timeout_seconds > 0 and (time.monotonic() - started_at) > timeout_seconds:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError(
                    f"{error_message}: command timed out after {timeout_seconds:.0f}s"
                )
            ready, _, _ = select_module.select([process.stdout], [], [], 0.2)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            chunk = process.stdout.read(1)
            if chunk == "":
                if process.poll() is not None:
                    break
                continue
            if chunk in {"\r", "\n"}:
                flush_line()
            else:
                line_buffer.append(chunk)
        flush_line()

        return_code = process.wait()
        output = "\n".join(output_lines)
        if return_code != 0:
            details = "\n".join(output_lines[-20:]).strip() or "command failed"
            raise RuntimeError(f"{error_message}: {details}")
        return subprocess.CompletedProcess(args, return_code, stdout=output, stderr="")

    def _should_emit_git_progress(
        self,
        progress: dict[str, Any],
        last_percent_by_stage: dict[str, int],
        last_emit_at: float,
    ) -> bool:
        stage = str(progress["git_stage"])
        percent = int(progress["percent"])
        last_percent = last_percent_by_stage.get(stage)
        if last_percent is None:
            return True
        if percent >= 100 and last_percent < 100:
            return True
        if percent - last_percent >= _GIT_PROGRESS_PERCENT_STEP:
            return True
        return time.monotonic() - last_emit_at >= _GIT_PROGRESS_MIN_INTERVAL_SECONDS and percent != last_percent

    def _is_cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancel_requested_run_ids

    def _raise_if_cancel_requested(self, run_id: str) -> None:
        if self._is_cancel_requested(run_id):
            raise Stage2RunInterruptedError("stage2 run interrupted by user")

    def _run_concurrency_limit(self, run: Stage2Run) -> int:
        runtime_snapshot = extract_stage2_runtime_snapshot(dict(run.runtime_snapshot_json or {}))
        concurrency = dict(runtime_snapshot.get("concurrency") or {})
        raw_value = concurrency.get("max_concurrent_runs")
        if raw_value is None:
            raw_value = self.settings.stage2_default_task_max_concurrent_runs
        try:
            requested = int(raw_value)
        except (TypeError, ValueError):
            requested = 0
        if requested < 1:
            requested = self.settings.stage2_max_concurrent_runs
        with self._capacity:
            system_limit = self._max_workers
        return max(1, min(int(requested), int(system_limit)))

    def _acquire_slot(self, run_id: str, *, max_workers: int | None = None) -> bool:
        with self._capacity:
            effective_max_workers = max(1, int(max_workers or self._max_workers))
            while self._active_runs >= effective_max_workers:
                if self._is_cancel_requested(run_id):
                    return False
                self._capacity.wait(timeout=0.2)
            if self._is_cancel_requested(run_id):
                return False
            self._active_runs += 1
            return True

    def _release_slot(self) -> None:
        with self._capacity:
            self._active_runs = max(self._active_runs - 1, 0)
            self._capacity.notify_all()
