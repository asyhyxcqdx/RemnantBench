from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from threading import Condition, Lock
from typing import Any, Callable, TypeVar

from sqlalchemy.orm import Session, sessionmaker

from feature_factory.config import Settings
from feature_factory.data_pool import DataPoolService
from feature_factory.models import Stage4Run, Stage4RunResult, Stage4RunStatus
from feature_factory.stage4.backend import (
    LocalStage4Backend,
    OpenHandsStage4Backend,
    Stage4Backend,
    build_stage4_backend,
    evaluate_stage4_backend_readiness,
    stage4_settings_with_runtime_snapshot,
)
from feature_factory.stage4.service import Stage4Service

T = TypeVar("T")

TOKEN_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


class Stage4RunInterruptedError(RuntimeError):
    pass


def _event_token_usage_total(payload: dict[str, Any]) -> int:
    explicit_payload_total = payload.get("token_usage_total")
    if explicit_payload_total is not None:
        return int(explicit_payload_total or 0)
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
    model_name = _event_token_usage_model_name(payload.get("model"))
    if model_name:
        return model_name
    return ""


class Stage4RunRunner:
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
        self.max_limit = max_limit or settings.stage4_max_concurrent_runs_cap
        self._max_workers = max_workers or settings.stage4_max_concurrent_runs
        self.executor = ThreadPoolExecutor(max_workers=self.max_limit, thread_name_prefix="ff-stage4")
        self.backend = build_stage4_backend(settings, app_instance_id=self.app_instance_id)
        self._lock = Lock()
        self._capacity = Condition(Lock())
        self._active_runs = 0
        self._futures: dict[str, Future[None]] = {}
        self._running_run_ids: set[str] = set()
        self._cancel_requested_run_ids: set[str] = set()
        self._completion_committed_run_ids: set[str] = set()
        self._run_backends: dict[str, Stage4Backend] = {}

    def is_backend_ready(self) -> bool:
        return bool(evaluate_stage4_backend_readiness(self.settings).get("ready"))

    def backend_readiness(self) -> dict[str, Any]:
        return dict(evaluate_stage4_backend_readiness(self.settings))

    def schedule_run(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
            if future is not None and not future.done():
                return False
            self._completion_committed_run_ids.discard(run_id)
            future = self.executor.submit(self._run_future, run_id)
            self._futures[run_id] = future
            future.add_done_callback(
                lambda completed_future, target=run_id: self._cleanup_future(
                    target,
                    completed_future,
                )
            )
            return True

    def interrupt_run(self, run_id: str) -> bool:
        with self._lock:
            future = self._futures.get(run_id)
            if (
                future is None
                or future.done()
                or run_id in self._completion_committed_run_ids
            ):
                return False
            self._cancel_requested_run_ids.add(run_id)
            backend = self._run_backends.get(run_id, self.backend)
        # Future.cancel() invokes done callbacks synchronously on some paths. It
        # must run outside self._lock because _cleanup_future() takes the same lock.
        cancelled_before_start = future.cancel()
        with self._capacity:
            self._capacity.notify_all()
        cancel_backend = getattr(backend, "cancel_run", None)
        if callable(cancel_backend):
            cancel_backend(run_id)
        self._record_event(
            run_id,
            actor="system",
            phase="interrupt",
            title="Stage4 interrupt accepted",
            message="Runner accepted the interrupt request and is stopping this Stage4 run",
            payload={"run_id": run_id, "cancelled_before_start": bool(cancelled_before_start)},
        )
        if cancelled_before_start:
            self._complete_interrupted_run(run_id)
            with self._lock:
                self._cancel_requested_run_ids.discard(run_id)
        return True

    def is_run_running(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._running_run_ids

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
            self.settings.stage4_max_concurrent_runs = max_workers
            self._capacity.notify_all()

    def reload_backend(self) -> None:
        with self._lock:
            self.backend = build_stage4_backend(self.settings, app_instance_id=self.app_instance_id)

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        with self._lock:
            active_futures = {
                run_id: future
                for run_id, future in self._futures.items()
                if not future.done() and run_id not in self._completion_committed_run_ids
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
                self._completion_committed_run_ids.discard(run_id)

    def _run_future(self, run_id: str) -> None:
        slot_acquired = False
        try:
            self._raise_if_cancel_requested(run_id)
            run = self._with_service(lambda service: service.get_run(run_id, include_detail=False))
            if not self._acquire_slot(run_id, max_workers=self._run_concurrency_limit(run)):
                self._complete_interrupted_run(run_id)
                with self._lock:
                    self._cancel_requested_run_ids.discard(run_id)
                return
            slot_acquired = True
            run = self._with_service(lambda service: service.start_run(run_id))
            with self._lock:
                self._running_run_ids.add(run_id)
            self._raise_if_cancel_requested(run_id)
            source_context = self._with_service(
                lambda service: service.prepare_run_workspace(
                    run_id,
                    emit_event=lambda **kwargs: self._record_event(run_id, **kwargs),
                    cancel_requested=lambda: self._is_cancel_requested(run_id),
                )
            )
            self._raise_if_cancel_requested(run_id)
            self._execute_run(run, source_context)
            self._materialize_default_data_pool_if_generated(run_id)
        except Stage4RunInterruptedError:
            self._complete_interrupted_run(run_id)
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            self._record_event(
                run_id,
                actor="system",
                phase="failed",
                title="Stage4 run failed before issuer execution",
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
        finally:
            with self._lock:
                self._run_backends.pop(run_id, None)
                self._running_run_ids.discard(run_id)
                self._cancel_requested_run_ids.discard(run_id)
            if slot_acquired:
                self._release_slot()

    def _materialize_default_data_pool_if_generated(self, run_id: str) -> None:
        session = self.session_factory()
        try:
            run = session.get(Stage4Run, run_id)
            if run is None:
                return
            if str(run.trigger_kind or "") in {"batch", "data_pool_backfill"}:
                return
            if run.status != Stage4RunStatus.completed.value or run.result != Stage4RunResult.generated.value:
                return
            service = DataPoolService(session)
            pool = service.ensure_default_pool(
                name=self.settings.default_data_pool_name,
                root_path=self.settings.default_data_pool_root,
            )
            service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=run_id)
            session.commit()
            self._record_event(
                run_id,
                actor="system",
                phase="data_pool",
                title="Default data pool asset materialized",
                message=f"Stage4 generated asset was written to default data pool {pool.root_path}",
                payload={"data_pool_id": pool.id, "data_pool_root": pool.root_path},
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            self._record_event(
                run_id,
                actor="system",
                phase="data_pool",
                title="Default data pool materialization failed",
                message=str(exc),
                payload={"error_type": type(exc).__name__},
            )
        finally:
            session.close()

    def _execute_run(self, run: Stage4Run, source_context: dict[str, Any]) -> None:
        run_id = run.id
        backend = self._backend_for_run(run)
        self._raise_if_cancel_requested(run_id)
        self._record_event(
            run_id,
            actor="system",
            phase="issuer_agent",
            title="Issuer agent running",
            message="Launching Stage4 issuer generation",
            payload={"workspace_path": str(run.workspace_path or "")},
        )
        try:
            result = backend.generate_issues(
                source_context,
                emit_event=lambda event: self._record_backend_event(run_id, event),
            )
            self._raise_if_cancel_requested(run_id)
        except Exception as exc:
            if self._is_cancel_requested(run_id):
                raise Stage4RunInterruptedError(str(exc)) from exc
            error_message = str(exc)
            self._record_event(
                run_id,
                actor="system",
                phase="failed",
                title="Issuer agent failed",
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
        try:
            self._commit_generated_result(run_id, result)
        except Exception as exc:
            if self._is_cancel_requested(run_id):
                raise Stage4RunInterruptedError(str(exc)) from exc
            error_message = str(exc)
            self._record_event(
                run_id,
                actor="system",
                phase="validation",
                title="Stage4 issuer output validation failed",
                message=error_message,
                payload={"error_type": type(exc).__name__},
            )
            self._with_service(
                lambda service: service.mark_run_failed(
                    run_id,
                    error_message=error_message,
                    summary_updates={
                        "runner_status": "failed",
                        "validation_status": "failed",
                    },
                )
            )

    def _commit_generated_result(self, run_id: str, result: Any) -> None:
        # This lock is the linearization boundary between an accepted interrupt
        # and committing a generated result. Whichever acquires it first wins.
        with self._lock:
            if run_id in self._cancel_requested_run_ids:
                raise Stage4RunInterruptedError("stage4 run interrupted by user")
            self._with_service(
                lambda service: service.complete_run_with_bundle(
                    run_id,
                    bundle=result.bundle,
                    model=result.model,
                    token_usage=result.token_usage,
                    token_usage_details=result.token_usage_details,
                )
            )
            self._completion_committed_run_ids.add(run_id)

    def _backend_for_run(self, run: Stage4Run) -> Stage4Backend:
        with self._lock:
            backend = self.backend
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        runtime_snapshot = dict(runtime_stage4.get("runtime") or {})
        settings_snapshot = stage4_settings_with_runtime_snapshot(
            self.settings.model_copy(deep=True),
            runtime_snapshot,
        )
        if isinstance(backend, OpenHandsStage4Backend):
            backend = build_stage4_backend(settings_snapshot, app_instance_id=self.app_instance_id)
        elif isinstance(backend, LocalStage4Backend):
            backend = LocalStage4Backend(settings_snapshot)
        with self._lock:
            self._run_backends[run.id] = backend
        return backend

    def _record_backend_event(self, run_id: str, event) -> None:
        payload = dict(event.payload or {})
        self._record_event(
            run_id,
            actor=str(event.actor or "issuer_agent"),
            phase=event.phase,
            title=event.title,
            message=event.message,
            payload=payload,
        )
        token_usage = _event_token_usage_total(payload)
        if token_usage <= 0:
            return
        summary_updates: dict[str, Any] = {"issuer_token_usage": token_usage}
        model_name = _event_token_usage_model(payload)
        if model_name:
            summary_updates["issuer_model"] = model_name
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

    def _complete_interrupted_run(self, run_id: str) -> None:
        try:
            self._with_service(lambda service: service.interrupt_run(run_id))
        except Exception:
            return

    def _run_concurrency_limit(self, run: Stage4Run) -> int:
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        runtime_snapshot = dict(runtime_stage4.get("runtime") or {})
        concurrency = dict(runtime_snapshot.get("concurrency") or {})
        raw_value = concurrency.get("max_concurrent_runs")
        if raw_value is None:
            raw_value = self.settings.stage4_default_task_max_concurrent_runs
        try:
            requested = int(raw_value)
        except (TypeError, ValueError):
            requested = 0
        if requested < 1:
            requested = self.settings.stage4_max_concurrent_runs
        with self._capacity:
            system_limit = self._max_workers
        return max(1, min(int(requested), int(system_limit)))

    def _acquire_slot(self, run_id: str, *, max_workers: int | None = None) -> bool:
        with self._capacity:
            effective_max_workers = max(1, int(max_workers or self._max_workers))
            while self._active_runs >= effective_max_workers:
                if self._is_cancel_requested(run_id):
                    return False
                self._capacity.wait(timeout=0.25)
            if self._is_cancel_requested(run_id):
                return False
            self._active_runs += 1
            return True

    def _release_slot(self) -> None:
        with self._capacity:
            self._active_runs = max(0, self._active_runs - 1)
            self._capacity.notify_all()

    def _is_cancel_requested(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cancel_requested_run_ids

    def _raise_if_cancel_requested(self, run_id: str) -> None:
        if self._is_cancel_requested(run_id):
            raise Stage4RunInterruptedError("stage4 run interrupted by user")

    def _with_service(self, callback: Callable[[Stage4Service], T]) -> T:
        session = self.session_factory()
        try:
            service = Stage4Service(session, workspace_root=Path(self.settings.stage4_workspace_dir), settings=self.settings)
            result = callback(service)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()