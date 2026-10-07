from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Callable, TypeVar

from sqlalchemy.orm import Session, sessionmaker

from feature_factory.config import Settings
from feature_factory.models import GitHubRepository, Stage3Run, Stage3RunResult
from feature_factory.stage3.backend import (
    OpenHandsStage3Backend,
    Stage3Backend,
    build_stage3_backend,
    evaluate_openhands_readiness,
    stage3_settings_with_runtime_snapshot,
)
from feature_factory.stage3.service import (
    Stage3RunConflictError,
    Stage3Service,
    extract_stage3_runtime_snapshot,
)

T = TypeVar("T")

TOKEN_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


class Stage3RunInterruptedError(RuntimeError):
    pass


def _event_token_usage_total(payload: dict[str, Any]) -> int:
    usage = payload.get("token_usage") or payload.get("metrics")
    if not isinstance(usage, dict):
        return 0
    explicit_total = usage.get("total_tokens")
    if explicit_total is not None:
        return int(explicit_total or 0)
    return sum(int(usage.get(key, 0) or 0) for key in TOKEN_USAGE_KEYS)


def _event_token_usage_model_name(value: Any) -> str:
    model_name = str(value or "").strip()
    if model_name.lower() in {"", "unknown", "unknown_model"}:
        return ""
    return model_name


def _event_token_usage_model(payload: dict[str, Any]) -> str:
    completion = payload.get("llm_completion")
    if isinstance(completion, dict):
        model_name = _event_token_usage_model_name(completion.get("model_name"))
        if model_name:
            return model_name
    agent = payload.get("agent")
    if isinstance(agent, dict):
        model_name = _event_token_usage_model_name(agent.get("model"))
        if model_name:
            return model_name
    return ""


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
        if normalized_root is not None and (resolved == normalized_root or normalized_root not in resolved.parents):
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


class Stage3RunRunner:
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
        self.max_limit = max_limit or settings.stage3_max_concurrent_runs_cap
        self._max_workers = max_workers or settings.stage3_max_concurrent_runs
        self.executor = ThreadPoolExecutor(max_workers=self.max_limit, thread_name_prefix="ff-stage3")
        self.backend = build_stage3_backend(settings, app_instance_id=self.app_instance_id)
        self._lock = Lock()
        self._capacity = Condition(Lock())
        self._active_runs = 0
        self._futures: dict[str, Future[None]] = {}
        self._running_run_ids: set[str] = set()
        self._running_repository_ids: set[int] = set()
        self._cancel_requested_run_ids: set[str] = set()
        self._run_backends: dict[str, Stage3Backend] = {}

    def is_backend_ready(self) -> bool:
        return bool(evaluate_openhands_readiness(self.settings).get("ready"))

    def backend_readiness(self) -> dict[str, Any]:
        return dict(evaluate_openhands_readiness(self.settings))

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
            title="Stage3 interrupt accepted",
            message="Runner accepted the interrupt request and is stopping this Stage3 run",
            payload={
                "run_id": run_id,
                "cancelled_before_start": bool(cancelled_before_start),
            },
        )
        if cancelled_before_start:
            self._complete_interrupted_run(run_id)
            with self._lock:
                self._cancel_requested_run_ids.discard(run_id)
        return True

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
            self.settings.stage3_max_concurrent_runs = max_workers
            self._capacity.notify_all()

    def reload_backend(self) -> None:
        next_backend = build_stage3_backend(self.settings, app_instance_id=self.app_instance_id)
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
            repository_id = run.entry_file.snapshot.repository_id
            if not self._acquire_slot(run_id, max_workers=self._run_concurrency_limit(run)):
                self._complete_interrupted_run(run_id)
                with self._lock:
                    self._cancel_requested_run_ids.discard(run_id)
                return
            slot_acquired = True
            repository = self._with_service(lambda service: service.get_repository(repository_id))
            self._raise_if_cancel_requested(run_id)
            run = self._with_service(
                lambda service: service.start_run(
                    repository_id,
                    run_id,
                    prepare_workspace=False,
                )
            )
            with self._lock:
                self._running_run_ids.add(run_id)
                self._running_repository_ids.add(repository_id)
            self._raise_if_cancel_requested(run_id)
            try:
                run = self._with_service(
                    lambda service: service.prepare_run_for_breaker(
                        repository_id,
                        run_id,
                        emit_event=lambda **kwargs: self._record_event(run_id, **kwargs),
                        cancel_requested=lambda: self._is_cancel_requested(run_id),
                    )
                )
            except Stage3RunConflictError as exc:
                if self._is_cancel_requested(run_id):
                    raise Stage3RunInterruptedError(str(exc)) from exc
                raise
            self._raise_if_cancel_requested(run_id)
            self._execute_run(repository, run)
        except Stage3RunInterruptedError:
            self._complete_interrupted_run(run_id)
        except Exception as exc:  # noqa: BLE001
            self._record_event(
                run_id,
                actor="system",
                phase="failed",
                title="Stage3 run failed before breaker execution",
                message=str(exc),
                payload={"error_type": type(exc).__name__},
            )
            self._with_service(
                lambda service: service.mark_run_failed(
                    run_id,
                    error_message=str(exc),
                    summary_updates={"runner_status": "failed"},
                )
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

    def _execute_run(self, repository: GitHubRepository, run: Stage3Run) -> None:
        run_id = run.id
        backend = self._backend_for_run(run)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        workspace_dir = Path(str(run.workspace_path or "")).expanduser()
        repo_dir = self._repo_dir_for_run(run)
        if not workspace_dir.exists() or not repo_dir.exists():
            raise RuntimeError("stage3 workspace is not prepared; start the run before scheduling the breaker")

        self._record_event(
            run_id,
            actor="system",
            phase="breaker_agent",
            title="Breaker agent running",
            message=f"Launching breaker agent for {repository.full_name}",
            payload={"workspace_path": str(workspace_dir), "repo_dir": str(repo_dir)},
        )
        try:
            result = backend.run_breaker(
                repo_dir,
                repository,
                workspace_dir=workspace_dir,
                run_context=runtime_stage3,
                emit_event=lambda event: self._record_backend_event(run_id, event),
            )
            self._raise_if_cancel_requested(run_id)
        except Exception as exc:
            if self._is_cancel_requested(run_id):
                raise Stage3RunInterruptedError(str(exc)) from exc
            error_message = str(exc)
            self._record_event(
                run_id,
                actor="system",
                phase="failed",
                title="Breaker agent failed",
                message=error_message,
                payload={"error_type": type(exc).__name__},
            )
            self._with_service(
                lambda service: service.mark_run_failed(
                    run_id,
                    error_message=error_message,
                    summary_updates={"runner_status": "failed"},
                )
            )
            return

        self._record_event(
            run_id,
            actor="breaker_agent",
            phase="breaker_agent",
            title="Breaker conversation completed",
            message=result.summary,
            payload={
                "breaker_model": result.model,
                "breaker_token_usage": result.token_usage,
                "execution_status": result.execution_status,
                "savepoint_count": len(result.savepoints),
            },
        )
        completed_run = self._with_service(
            lambda service: service.mark_run_completed(
                run_id,
                summary_updates={
                    "runner_status": "completed",
                    "breaker_model": result.model,
                    "breaker_token_usage": result.token_usage,
                    "latest_depth": service._effective_latest_depth(service.get_run(run_id)),  # noqa: SLF001
                    "savepoint_count": service._effective_savepoint_count(service.get_run(run_id)),  # noqa: SLF001
                },
            )
        )
        if str(completed_run.result or "") == Stage3RunResult.archived.value:
            self._cleanup_successful_run_breaker_checkpoints_best_effort(run_id)

    def _repo_dir_for_run(self, run: Stage3Run) -> Path:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        workspace_manifest = dict(runtime_stage3.get("workspace") or {})
        repo_dir = str(workspace_manifest.get("repo_dir") or "").strip()
        if repo_dir:
            return Path(repo_dir).expanduser()
        return Path(str(run.workspace_path or "")).expanduser() / "repo"

    def _backend_for_run(self, run: Stage3Run) -> Stage3Backend:
        with self._lock:
            shared_backend = self.backend
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
        settings_snapshot = stage3_settings_with_runtime_snapshot(self.settings.model_copy(deep=True), runtime_snapshot)
        backend = (
            build_stage3_backend(settings_snapshot, app_instance_id=self.app_instance_id)
            if isinstance(shared_backend, OpenHandsStage3Backend)
            else shared_backend
        )
        with self._lock:
            self._run_backends[run.id] = backend
        return backend

    def _record_backend_event(self, run_id: str, event) -> None:
        payload = dict(event.payload or {})
        self._record_event(
            run_id,
            actor=str(event.actor or "agent"),
            phase=event.phase,
            title=event.title,
            message=event.message,
            payload=payload,
        )
        token_usage = _event_token_usage_total(payload)
        if token_usage <= 0:
            return
        summary_updates: dict[str, Any] = {"breaker_token_usage": token_usage}
        model_name = _event_token_usage_model(payload)
        if model_name:
            summary_updates["breaker_model"] = model_name
        self._with_service(
            lambda service: service.update_run_progress(
                run_id,
                phase=event.phase,
                summary_updates=summary_updates,
            )
        )

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
                payload=payload or {},
            )
        )

    def _cleanup_successful_run_breaker_checkpoints_best_effort(self, run_id: str) -> None:
        try:
            self._cleanup_successful_run_breaker_checkpoints(run_id)
        except Exception as exc:  # noqa: BLE001
            try:
                self._record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Breaker checkpoint cleanup failed",
                    message=str(exc),
                    payload={"error_type": type(exc).__name__},
                )
            except Exception:
                return

    def _cleanup_successful_run_breaker_checkpoints(self, run_id: str) -> None:
        try:
            cleanup = self._with_service(lambda service: service.breaker_checkpoint_cleanup_targets(run_id))
        except Exception as exc:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Breaker checkpoint cleanup failed",
                message=str(exc),
                payload={},
            )
            return

        checkpoint_paths = list(cleanup.get("checkpoint_paths") or [])
        checkpoint_image_refs = list(cleanup.get("checkpoint_docker_image_refs") or [])
        failed_paths = list(
            _remove_paths(
                checkpoint_paths,
                root_dir=Path(self.settings.stage3_workspace_dir).expanduser().resolve(),
            )
            or []
        )
        failed_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
        if failed_paths or failed_image_refs:
            self._with_service(
                lambda service: service.upsert_breaker_checkpoint_cleanup_tombstone(
                    run_id,
                    checkpoint_paths=checkpoint_paths,
                    checkpoint_docker_image_refs=checkpoint_image_refs,
                    reason="successful_breaker_checkpoint_cleanup",
                )
            )
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Breaker checkpoint cleanup incomplete",
                message="Some breaker checkpoint assets could not be removed after successful completion",
                payload={
                    "checkpoint_paths": checkpoint_paths,
                    "checkpoint_docker_image_refs": checkpoint_image_refs,
                    "failed_paths": failed_paths,
                    "failed_checkpoint_docker_image_refs": failed_image_refs,
                },
            )
            return

        self._with_service(lambda service: service.clear_breaker_checkpoints(run_id))
        if checkpoint_paths or checkpoint_image_refs:
            self._record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Breaker checkpoint cleaned",
                message="Released breaker checkpoint files and docker images after successful completion",
                payload={
                    "checkpoint_paths": checkpoint_paths,
                    "checkpoint_docker_image_refs": checkpoint_image_refs,
                },
            )

    def _complete_interrupted_run(self, run_id: str) -> None:
        self._record_event(
            run_id,
            actor="system",
            phase="interrupt",
            title="Stage3 run interrupted",
            message="Interrupted the active stage3 breaker run",
            payload={"run_id": run_id},
        )
        self._with_service(
            lambda service: service.mark_run_failed(
                run_id,
                error_message="stage3 run interrupted by user",
                result=Stage3RunResult.interrupted.value,
                phase="interrupted",
                summary_updates={"runner_status": "interrupted"},
            )
        )

    def _is_cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancel_requested_run_ids

    def _raise_if_cancel_requested(self, run_id: str) -> None:
        if self._is_cancel_requested(run_id):
            raise Stage3RunInterruptedError("stage3 run interrupted")

    def _run_concurrency_limit(self, run: Stage3Run) -> int:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
        concurrency = dict(runtime_snapshot.get("concurrency") or {})
        raw_value = concurrency.get("max_concurrent_runs")
        if raw_value is None:
            raw_value = self.settings.stage3_default_task_max_concurrent_runs
        try:
            requested = int(raw_value)
        except (TypeError, ValueError):
            requested = 0
        if requested < 1:
            requested = self.settings.stage3_max_concurrent_runs
        with self._capacity:
            system_limit = self._max_workers
        return max(1, min(int(requested), int(system_limit)))

    def _acquire_slot(self, run_id: str, *, max_workers: int | None = None) -> bool:
        with self._capacity:
            effective_max_workers = max(1, int(max_workers or self._max_workers))
            while self._active_runs >= effective_max_workers:
                if self._is_cancel_requested(run_id):
                    return False
                self._capacity.wait(timeout=0.5)
            if self._is_cancel_requested(run_id):
                return False
            self._active_runs += 1
            return True

    def _release_slot(self) -> None:
        with self._capacity:
            self._active_runs = max(self._active_runs - 1, 0)
            self._capacity.notify_all()

    def _with_service(self, callback: Callable[[Stage3Service], T]) -> T:
        session = self.session_factory()
        try:
            service = Stage3Service(session, workspace_root=self.settings.stage3_workspace_dir, settings=self.settings)
            result = callback(service)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
