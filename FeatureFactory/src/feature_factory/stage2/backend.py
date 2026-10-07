from __future__ import annotations

import json
import math
import os
import re
import signal
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Literal, Protocol

from feature_factory.config import Settings
from feature_factory.models import GitHubRepository
from feature_factory.openhands_llm import (
    DEPLOYMENT_LLM_MODEL_ENV,
    preserve_deployment_llm_model_env,
    set_openhands_llm_ssl_verify_env,
)
from feature_factory.stage2.assets import asset_root
from feature_factory.stage2.base_images import ensure_base_image_built, list_base_images
from feature_factory.stage2.bridge_events import (
    WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage2.image_assets import (
    PLANNER_AGENT_SERVER_BASE_IMAGE,
    build_host_sdk_runtime_env,
    detect_platform,
    ensure_agent_server_image_built,
)
from feature_factory.stage2.planner import PlannerDecision
from feature_factory.stage2.validation_feedback import validation_failure_message

BackendEventCallback = Callable[["Stage2BackendEvent"], None]
OPENHANDS_BRIDGE_PYTHON_VERSION = "3.13"
BRIDGE_LOG_TAIL_LIMIT = 12
BRIDGE_LOG_EMIT_INTERVAL_SECONDS = 2.0
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True, slots=True)
class Stage2BackendEvent:
    actor: str
    phase: str | None
    title: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorkerExecutionResult:
    title: str
    description: str
    strategy_payload: dict[str, Any]
    dockerfile_text: str
    run_script_text: str
    model: str
    token_usage: int
    token_usage_details: dict[str, Any]
    validation_attempts: list[dict[str, Any]] = field(default_factory=list)
    final_validation: dict[str, Any] = field(default_factory=dict)
    validation_attempts_persisted_live: bool = False
    post_agent_full_validation_required: bool = False


@dataclass(slots=True)
class _BridgeOutputTailState:
    stdout_offset: int = 0
    stderr_offset: int = 0
    pending_lines: list[str] = field(default_factory=list)
    last_emit_monotonic: float = 0.0


@dataclass(slots=True)
class _BridgeTimeoutBudget:
    timeout_seconds: float
    clock: Callable[[], float] = time.monotonic
    started_at: float = field(init=False)
    exempt_credited_seconds: float = 0.0
    active_exempt_starts: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    def observe_event(self, event: Stage2BackendEvent) -> None:
        payload = dict(event.payload or {})
        if payload.get("operation") != WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION:
            return
        request_key = _timeout_exempt_request_key(payload)
        status = str(payload.get("status") or "").strip()
        now = self.clock()
        if status == WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED:
            self.active_exempt_starts.setdefault(request_key, now)
            return
        if status != WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED:
            return
        started_at = self.active_exempt_starts.pop(request_key, None)
        duration_seconds = _positive_float(payload.get("duration_seconds"))
        if duration_seconds is None and started_at is not None:
            duration_seconds = max(0.0, now - started_at)
        if duration_seconds is not None:
            self.exempt_credited_seconds += duration_seconds

    def expired(self) -> bool:
        return self.clock() >= self.deadline()

    def deadline(self) -> float:
        return self.started_at + self.timeout_seconds + self.exempt_seconds()

    def exempt_seconds(self) -> float:
        now = self.clock()
        active_seconds = sum(
            max(0.0, now - started_at)
            for started_at in self.active_exempt_starts.values()
        )
        return self.exempt_credited_seconds + active_seconds


@dataclass(slots=True)
class _BuildOutputTailState:
    pending_lines: list[str] = field(default_factory=list)
    last_emit_monotonic: float = 0.0


class Stage2Backend(Protocol):
    backend_name: str
    selection_detail: str

    def plan_repository(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        emit_event: BackendEventCallback | None = None,
    ) -> PlannerDecision: ...

    def max_worker_attempts(self) -> int: ...

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint: dict[str, Any] | None = None,
        last_smoke_report: dict[str, Any] | None = None,
        emit_event: BackendEventCallback | None = None,
    ) -> WorkerExecutionResult: ...


class OpenHandsStage2Backend:
    backend_name = "openhands"

    def __init__(
        self,
        settings: Settings,
        *,
        selection_detail: str,
        ready: bool,
        readiness_message: str,
        app_instance_id: str = "",
    ) -> None:
        self.settings = settings
        self.selection_detail = selection_detail
        self._ready = ready
        self._readiness_message = readiness_message
        self._project_root = Path(__file__).resolve().parents[3]
        self._src_root = self._project_root / "src"
        self._sdk_root = Path(settings.stage2_openhands_sdk_root).expanduser().resolve()
        self._bridge_module = "feature_factory.stage2.openhands_bridge"
        self._app_instance_id = str(app_instance_id or "").strip()
        self._bridge_process_lock = Lock()
        self._bridge_processes: dict[str, subprocess.Popen[str]] = {}
        self._bridge_cancel_requests: set[str] = set()

    def cancel_run(self, run_id: str) -> bool:
        with self._bridge_process_lock:
            self._bridge_cancel_requests.add(run_id)
            process = self._bridge_processes.get(run_id)
        if process is None or process.poll() is not None:
            return True
        try:
            self._send_bridge_signal(process, signal.SIGINT)
        except ProcessLookupError:
            return True
        return True

    def plan_repository(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        emit_event: BackendEventCallback | None = None,
    ) -> PlannerDecision:
        self._ensure_ready()
        planner_preset = self.settings.stage2_agent_openhands_preset("planner")
        planner_max_iterations = self.settings.stage2_agent_openhands_max_iterations("planner")
        planner_timeout_seconds = self.settings.stage2_agent_timeout("planner")
        planner_model = self.settings.stage2_agent_llm_model("planner") or ""
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase="host_planner",
                    title="Planner sandbox starting",
                    message=f"Launching OpenHands planner sandbox for {repository.full_name}",
                    payload={
                        "operation": "openhands_bridge_planner",
                        "backend": self.backend_name,
                        "workspace_dir": str(workspace_dir),
                        "repo_path": str(repo_path),
                        "runtime_dir": str(self._runtime_dir_for(workspace_dir)),
                        "model": planner_model,
                        "preset": planner_preset,
                        "max_iterations": planner_max_iterations,
                        "timeout_seconds": planner_timeout_seconds,
                    },
                )
            )
        base_image_catalog = [image.to_payload() for image in list_base_images()]
        platform_name = detect_platform()
        planner_agent_server_image_ref = self._prepare_agent_server_image(
            run_id=workspace_dir.name,
            base_image=PLANNER_AGENT_SERVER_BASE_IMAGE,
            platform_name=platform_name,
            phase="host_planner",
            operation="planner_agent_server_image_prepare",
            title_prefix="Planner agent-server image",
            emit_event=emit_event,
        )
        request = {
            "mode": "planner",
            "workspace_dir": str(workspace_dir),
            "repo_path": str(repo_path),
            "repository": _repository_payload(repository, repo_path=repo_path),
            "base_image_catalog": base_image_catalog,
            "planner_agent_server_image_ref": planner_agent_server_image_ref,
            "agent_timeout_seconds": planner_timeout_seconds,
            "build_timeout_seconds": self.settings.stage2_build_timeout_seconds,
            "preset": planner_preset,
            "max_iterations": planner_max_iterations,
            "app_instance_id": self._app_instance_id,
        }
        response = self._run_bridge_request(
            request=request,
            workspace_dir=workspace_dir,
            label="planner",
            emit_event=emit_event,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("OpenHands planner did not return a structured result payload")

        model = str(response.get("model") or self.settings.stage2_agent_llm_model("planner") or "openhands-agent")
        token_usage = _total_token_usage(response.get("token_usage"))
        status = str(result.get("status") or "").strip()
        worker_model = self.settings.stage2_agent_llm_model("worker") or model

        if status == "ready":
            selected_base_image = _select_base_image(
                base_image_catalog,
                image_id=str(result.get("selected_base_image_id") or ""),
            )
            if selected_base_image is None:
                raise RuntimeError("OpenHands planner selected a base image that is not in the catalog")
            return PlannerDecision(
                status="ready",
                base_image=str(selected_base_image["image_id"]),
                base_image_ref=str(selected_base_image["image_ref"]),
                planner_model=model,
                planner_token_usage=token_usage,
                worker_model=worker_model,
                worker_token_usage=0,
                guidance=str(result.get("guidance") or "").strip(),
                base_image_catalog=base_image_catalog,
            )

        if status == "abandoned":
            return PlannerDecision(
                status="abandoned",
                base_image=None,
                base_image_ref=None,
                planner_model=model,
                planner_token_usage=token_usage,
                worker_model=worker_model,
                worker_token_usage=0,
                reason=str(result.get("reason") or "").strip(),
                base_image_catalog=base_image_catalog,
            )

        if status == "defect":
            return PlannerDecision(
                status="defect",
                base_image=None,
                base_image_ref=None,
                planner_model=model,
                planner_token_usage=token_usage,
                worker_model=worker_model,
                worker_token_usage=0,
                reason=str(result.get("reason") or "").strip(),
                upd_dockerfile=str(result.get("upd_dockerfile") or "").strip(),
                base_image_catalog=base_image_catalog,
            )

        raise RuntimeError(f"OpenHands planner returned unsupported status: {status or '<empty>'}")

    def max_worker_attempts(self) -> int:
        return self.settings.stage2_max_worker_attempts

    def _prepare_worker_base_image(
        self,
        *,
        run_id: str,
        selected_base_image: dict[str, Any],
        platform_name: str,
        emit_event: BackendEventCallback | None,
    ) -> str:
        timeout_seconds = self.settings.stage2_build_timeout_seconds
        image_ref = str(selected_base_image.get("image_ref") or "").strip()
        asset_path = str(selected_base_image.get("asset_path") or "").strip()
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase="container_worker",
                    title="Worker base image preparing",
                    message=(
                        f"Ensuring selected base image {image_ref} "
                        f"from {asset_path}"
                    ),
                    payload={
                        "operation": "worker_base_image_prepare",
                        "image_id": selected_base_image.get("image_id"),
                        "image_ref": image_ref,
                        "asset_path": asset_path,
                        "platform": platform_name,
                        "timeout_seconds": timeout_seconds,
                        "counts_toward_agent_timeout": False,
                    },
                )
            )
        log_callback, flush_logs = self._build_output_logger(
            emit_event=emit_event,
            phase="container_worker",
            operation="worker_base_image_prepare",
            title="Worker base image build output",
        )
        started_at = time.monotonic()
        try:
            resolved_ref = ensure_base_image_built(
                source_root=asset_root(),
                base_image_payload=selected_base_image,
                platform_name=platform_name,
                timeout_seconds=timeout_seconds,
                log_callback=log_callback,
                cancel_requested=lambda: self._bridge_cancel_requested(run_id),
            )
        except subprocess.TimeoutExpired as exc:
            flush_logs()
            message = (
                "Worker base image build exceeded build timeout "
                f"of {timeout_seconds:.0f}s"
            )
            self._emit_build_failed_event(
                emit_event=emit_event,
                phase="container_worker",
                operation="worker_base_image_prepare",
                title="Worker base image build timed out",
                message=message,
                image_ref=image_ref,
                platform_name=platform_name,
            )
            raise RuntimeError(message) from exc
        except InterruptedError as exc:
            flush_logs()
            message = "Worker base image build interrupted by user"
            self._emit_build_failed_event(
                emit_event=emit_event,
                phase="container_worker",
                operation="worker_base_image_prepare",
                title="Worker base image build interrupted",
                message=message,
                image_ref=image_ref,
                platform_name=platform_name,
            )
            raise RuntimeError(message) from exc
        finally:
            flush_logs()
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase="container_worker",
                    title="Worker base image ready",
                    message=f"Selected base image {resolved_ref} is ready",
                    payload={
                        "operation": "worker_base_image_prepare",
                        "image_id": selected_base_image.get("image_id"),
                        "image_ref": resolved_ref,
                        "asset_path": asset_path,
                        "platform": platform_name,
                        "timeout_seconds": timeout_seconds,
                        "duration_seconds": max(0.0, time.monotonic() - started_at),
                        "counts_toward_agent_timeout": False,
                    },
                )
            )
        return resolved_ref

    def _prepare_agent_server_image(
        self,
        *,
        run_id: str,
        base_image: str,
        platform_name: str,
        phase: str,
        operation: str,
        title_prefix: str,
        emit_event: BackendEventCallback | None,
    ) -> str:
        timeout_seconds = self.settings.stage2_build_timeout_seconds
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase=phase,
                    title=f"{title_prefix} preparing",
                    message=f"Ensuring OpenHands agent-server image for {base_image}",
                    payload={
                        "operation": operation,
                        "base_image_ref": base_image,
                        "platform": platform_name,
                        "timeout_seconds": timeout_seconds,
                        "counts_toward_agent_timeout": False,
                    },
                )
            )
        log_callback, flush_logs = self._build_output_logger(
            emit_event=emit_event,
            phase=phase,
            operation=operation,
            title=f"{title_prefix} build output",
        )
        started_at = time.monotonic()
        try:
            image_ref = ensure_agent_server_image_built(
                base_image=base_image,
                platform_name=platform_name,
                timeout_seconds=timeout_seconds,
                log_callback=log_callback,
                cancel_requested=lambda: self._bridge_cancel_requested(run_id),
            )
        except subprocess.TimeoutExpired as exc:
            flush_logs()
            message = (
                f"{title_prefix} build exceeded build timeout "
                f"of {timeout_seconds:.0f}s"
            )
            self._emit_build_failed_event(
                emit_event=emit_event,
                phase=phase,
                operation=operation,
                title=f"{title_prefix} build timed out",
                message=message,
                image_ref=base_image,
                platform_name=platform_name,
            )
            raise RuntimeError(message) from exc
        except InterruptedError as exc:
            flush_logs()
            message = f"{title_prefix} build interrupted by user"
            self._emit_build_failed_event(
                emit_event=emit_event,
                phase=phase,
                operation=operation,
                title=f"{title_prefix} build interrupted",
                message=message,
                image_ref=base_image,
                platform_name=platform_name,
            )
            raise RuntimeError(message) from exc
        finally:
            flush_logs()
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase=phase,
                    title=f"{title_prefix} ready",
                    message=f"OpenHands agent-server image {image_ref} is ready",
                    payload={
                        "operation": operation,
                        "base_image_ref": base_image,
                        "agent_server_image_ref": image_ref,
                        "platform": platform_name,
                        "timeout_seconds": timeout_seconds,
                        "duration_seconds": max(0.0, time.monotonic() - started_at),
                        "counts_toward_agent_timeout": False,
                    },
                )
            )
        return image_ref

    def _build_output_logger(
        self,
        *,
        emit_event: BackendEventCallback | None,
        phase: str,
        operation: str,
        title: str,
    ) -> tuple[Callable[[str], None] | None, Callable[[], None]]:
        if emit_event is None:
            return None, lambda: None
        state = _BuildOutputTailState()

        def flush(*, force: bool = True) -> None:
            if not state.pending_lines:
                return
            now = time.monotonic()
            if (
                not force
                and state.last_emit_monotonic
                and now - state.last_emit_monotonic < BRIDGE_LOG_EMIT_INTERVAL_SECONDS
            ):
                return
            tail_lines = state.pending_lines[-BRIDGE_LOG_TAIL_LIMIT:]
            state.pending_lines.clear()
            state.last_emit_monotonic = now
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase=phase,
                    title=title,
                    message=tail_lines[-1] if tail_lines else "Docker build emitted output",
                    payload={
                        "operation": operation,
                        "tail_lines": tail_lines,
                        "counts_toward_agent_timeout": False,
                    },
                )
            )

        def append(line: str) -> None:
            cleaned = _clean_bridge_log_line(line)
            if not cleaned:
                return
            state.pending_lines.append(cleaned)
            flush(force=False)

        return append, flush

    def _emit_build_failed_event(
        self,
        *,
        emit_event: BackendEventCallback | None,
        phase: str,
        operation: str,
        title: str,
        message: str,
        image_ref: str,
        platform_name: str,
    ) -> None:
        if emit_event is None:
            return
        emit_event(
            Stage2BackendEvent(
                actor="system",
                phase=phase,
                title=title,
                message=message,
                payload={
                    "operation": operation,
                    "image_ref": image_ref,
                    "platform": platform_name,
                    "timeout_seconds": self.settings.stage2_build_timeout_seconds,
                    "counts_toward_agent_timeout": False,
                },
            )
        )

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint: dict[str, Any] | None = None,
        last_smoke_report: dict[str, Any] | None = None,
        emit_event: BackendEventCallback | None = None,
    ) -> WorkerExecutionResult:
        self._ensure_ready()
        if decision.status != "ready":
            raise RuntimeError(f"worker cannot run when planner status is {decision.status}")
        if not decision.base_image or not decision.base_image_ref or not decision.guidance:
            raise RuntimeError("worker cannot run without a ready planner decision")
        worker_preset = self.settings.stage2_agent_openhands_preset("worker")
        worker_max_iterations = self.settings.stage2_agent_openhands_max_iterations("worker")
        worker_timeout_seconds = self.settings.stage2_agent_timeout("worker")
        worker_model = self.settings.stage2_agent_llm_model("worker") or ""
        if emit_event is not None:
            emit_event(
                Stage2BackendEvent(
                    actor="system",
                    phase="container_worker",
                    title="Worker sandbox starting",
                    message=(
                        "Launching OpenHands worker sandbox on base image "
                        f"{decision.base_image_ref}"
                    ),
                    payload={
                        "operation": "openhands_bridge_worker",
                        "backend": self.backend_name,
                        "workspace_dir": str(workspace_dir),
                        "repo_path": str(repo_path),
                        "runtime_dir": str(self._runtime_dir_for(workspace_dir)),
                        "base_image_ref": decision.base_image_ref,
                        "attempt_index": attempt_index,
                        "model": worker_model,
                        "preset": worker_preset,
                        "max_iterations": worker_max_iterations,
                        "timeout_seconds": worker_timeout_seconds,
                    },
                )
            )
        base_image_catalog = list(
            decision.base_image_catalog
            or [image.to_payload() for image in list_base_images()]
        )
        selected_base_image = _select_base_image(
            base_image_catalog,
            image_id=decision.base_image,
        )
        if selected_base_image is None:
            raise RuntimeError("worker cannot run because selected base image is not in the catalog")
        platform_name = detect_platform()
        worker_base_image_ref = self._prepare_worker_base_image(
            run_id=workspace_dir.name,
            selected_base_image=selected_base_image,
            platform_name=platform_name,
            emit_event=emit_event,
        )
        resume_checkpoint_payload = dict(resume_checkpoint or {})
        worker_agent_server_image_ref = ""
        if not resume_checkpoint_payload:
            worker_agent_server_image_ref = self._prepare_agent_server_image(
                run_id=workspace_dir.name,
                base_image=worker_base_image_ref,
                platform_name=platform_name,
                phase="container_worker",
                operation="worker_agent_server_image_prepare",
                title_prefix="Worker agent-server image",
                emit_event=emit_event,
            )
        request = {
            "mode": "worker",
            "workspace_dir": str(workspace_dir),
            "repo_path": str(repo_path),
            "repository": _repository_payload(repository, repo_path=repo_path),
            "planner_decision": {
                "base_image": decision.base_image,
                "base_image_ref": decision.base_image_ref,
                "guidance": decision.guidance,
                "base_image_catalog": base_image_catalog,
            },
            "worker_base_image_ref": worker_base_image_ref,
            "worker_agent_server_image_ref": worker_agent_server_image_ref,
            "agent_timeout_seconds": worker_timeout_seconds,
            "max_validate_calls": self.settings.stage2_max_worker_attempts,
            "validation": {
                "quickcheck_sample_size": self.settings.stage2_quickcheck_sample_size,
                "entry_file_test_count_min": self.settings.stage2_entry_file_test_count_min,
                "p2p_file_count_limit": self.settings.stage2_p2p_file_count_limit,
                "p2p_sample_seed": self.settings.stage2_p2p_sample_seed,
                "collect_timeout_seconds": self.settings.stage2_collect_timeout_seconds,
                "run_test_timeout_seconds": self.settings.stage2_run_test_timeout_seconds,
                "build_timeout_seconds": self.settings.stage2_build_timeout_seconds,
                "full_validation_timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                "docker_image_prefix": self.settings.stage2_docker_image_prefix,
            },
            "resume_checkpoint": resume_checkpoint_payload,
            "preset": worker_preset,
            "max_iterations": worker_max_iterations,
            "app_instance_id": self._app_instance_id,
        }
        response = self._run_bridge_request(
            request=request,
            workspace_dir=workspace_dir,
            label=f"worker-attempt-{attempt_index}",
            emit_event=emit_event,
        )
        result = response.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("OpenHands worker did not return a structured result payload")

        validation_attempts = [
            dict(item)
            for item in list(response.get("validation_attempts") or [])
            if isinstance(item, dict)
        ]
        final_validation = dict(response.get("final_validation") or {})
        post_agent_full_validation_required = bool(response.get("post_agent_full_validation_required"))

        dockerfile_path = workspace_dir / "Dockerfile"
        run_script_path = workspace_dir / "run_script.sh"
        if not dockerfile_path.exists() or not run_script_path.exists():
            raise RuntimeError(
                validation_failure_message(
                    final_validation=final_validation,
                    description="OpenHands worker did not produce Dockerfile and run_script.sh",
                    unvalidated_description="OpenHands worker did not produce Dockerfile and run_script.sh",
                )
            )

        dockerfile_text = dockerfile_path.read_text()
        run_script_text = run_script_path.read_text()
        strategy_payload = {
            "backend": self.backend_name,
            "validate_call_limit": self.settings.stage2_max_worker_attempts,
            "base_image": decision.base_image,
            "base_image_ref": decision.base_image_ref,
            "worker_result": result,
            "validation_attempt_count": len(validation_attempts),
            "final_validation": final_validation,
            "post_agent_full_validation_required": post_agent_full_validation_required,
        }
        model = str(
            response.get("model") or decision.worker_model or self.settings.stage2_agent_llm_model("worker")
            or "openhands-agent"
        )
        return WorkerExecutionResult(
            title=f"OpenHands worker attempt {attempt_index}",
            description=str(result.get("summary") or "OpenHands worker generated stage2 artifacts"),
            strategy_payload=strategy_payload,
            dockerfile_text=dockerfile_text,
            run_script_text=run_script_text,
            model=model,
            token_usage=_total_token_usage(response.get("token_usage")),
            token_usage_details=dict(response.get("token_usage") or {}),
            validation_attempts=validation_attempts,
            final_validation=final_validation,
            validation_attempts_persisted_live=bool(
                response.get("validation_attempts_persisted_live", True)
            ),
            post_agent_full_validation_required=post_agent_full_validation_required,
        )

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        raise RuntimeError(self._readiness_message)

    def _run_bridge_request(
        self,
        *,
        request: dict[str, Any],
        workspace_dir: Path,
        label: str,
        emit_event: BackendEventCallback | None,
    ) -> dict[str, Any]:
        bridge_dir = self._runtime_dir_for(workspace_dir)
        bridge_dir.mkdir(parents=True, exist_ok=True)
        request_path = bridge_dir / f"{label}-request.json"
        result_path = bridge_dir / f"{label}-result.json"
        events_path = bridge_dir / f"{label}-events.jsonl"
        stdout_path = bridge_dir / f"{label}-stdout.log"
        stderr_path = bridge_dir / f"{label}-stderr.log"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")

        agent_role: Literal["planner", "worker"] = "worker" if request.get("mode") == "worker" else "planner"
        command = self._build_bridge_command(
            request_path=request_path,
            result_path=result_path,
            events_path=events_path,
        )
        env = self._build_agent_bridge_env(agent_role)
        timeout_seconds = self.settings.stage2_agent_timeout(agent_role)
        output_operation = f"openhands_bridge_{agent_role}"
        output_state = _BridgeOutputTailState()
        run_id = workspace_dir.name

        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr_handle:
            process = subprocess.Popen(
                command,
                cwd=str(self._project_root),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
                start_new_session=True,
            )
            with self._bridge_process_lock:
                self._bridge_processes[run_id] = process
            offset = 0
            timeout_budget = _BridgeTimeoutBudget(timeout_seconds=timeout_seconds)
            timeout_event_observer = (
                timeout_budget.observe_event
                if agent_role == "worker"
                else None
            )
            try:
                while True:
                    offset = self._drain_event_log(
                        events_path,
                        offset,
                        emit_event,
                        on_event=timeout_event_observer,
                    )
                    self._drain_bridge_output_logs(
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        state=output_state,
                        emit_event=emit_event,
                        operation=output_operation,
                        phase="container_worker" if agent_role == "worker" else "host_planner",
                        force=False,
                    )
                    returncode = process.poll()
                    if returncode is not None:
                        break
                    if self._bridge_cancel_requested(run_id):
                        self._interrupt_bridge_process(process)
                        offset = self._drain_event_log(
                            events_path,
                            offset,
                            emit_event,
                            on_event=timeout_event_observer,
                        )
                        self._drain_bridge_output_logs(
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            state=output_state,
                            emit_event=emit_event,
                            operation=output_operation,
                            phase="container_worker" if agent_role == "worker" else "host_planner",
                            force=True,
                        )
                        raise RuntimeError(f"OpenHands {label} interrupted by user")
                    if timeout_budget.expired():
                        self._send_bridge_signal(process, signal.SIGKILL)
                        process.wait(timeout=5)
                        offset = self._drain_event_log(
                            events_path,
                            offset,
                            emit_event,
                            on_event=timeout_event_observer,
                        )
                        self._drain_bridge_output_logs(
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            state=output_state,
                            emit_event=emit_event,
                            operation=output_operation,
                            phase="container_worker" if agent_role == "worker" else "host_planner",
                            force=True,
                        )
                        message = f"OpenHands {label} exceeded timeout of {timeout_seconds:.0f}s"
                        if agent_role == "worker":
                            message += " excluding validate tool host time"
                        raise RuntimeError(message)
                    time.sleep(0.25)
            finally:
                if process.poll() is None:
                    self._send_bridge_signal(process, signal.SIGKILL)
                    process.wait(timeout=5)
                with self._bridge_process_lock:
                    if self._bridge_processes.get(run_id) is process:
                        self._bridge_processes.pop(run_id, None)
                    self._bridge_cancel_requests.discard(run_id)

        self._drain_event_log(events_path, offset, emit_event)
        self._drain_bridge_output_logs(
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            state=output_state,
            emit_event=emit_event,
            operation=output_operation,
            phase="container_worker" if agent_role == "worker" else "host_planner",
            force=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                self._format_bridge_failure(label=label, stdout_path=stdout_path, stderr_path=stderr_path)
            )
        if not result_path.exists():
            raise RuntimeError(f"OpenHands {label} completed without writing a result payload")
        payload = json.loads(result_path.read_text())
        if not isinstance(payload, dict):
            raise RuntimeError(f"OpenHands {label} returned a non-object result payload")
        return payload

    def _bridge_cancel_requested(self, run_id: str) -> bool:
        with self._bridge_process_lock:
            return run_id in self._bridge_cancel_requests

    def _interrupt_bridge_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            self._send_bridge_signal(process, signal.SIGINT)
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._send_bridge_signal(process, signal.SIGKILL)
            process.wait(timeout=5)
        except ProcessLookupError:
            return

    def _send_bridge_signal(self, process: subprocess.Popen[str], sig: int) -> None:
        if hasattr(os, "killpg"):
            os.killpg(process.pid, sig)
            return
        process.send_signal(sig)

    def _build_bridge_command(
        self,
        *,
        request_path: Path,
        result_path: Path,
        events_path: Path,
    ) -> list[str]:
        return [
            "uv",
            "run",
            "--project",
            str(self._sdk_root),
            "--with-editable",
            str(self._project_root),
            "--frozen",
            "--python",
            OPENHANDS_BRIDGE_PYTHON_VERSION,
            "python",
            "-m",
            self._bridge_module,
            "--request",
            str(request_path),
            "--result",
            str(result_path),
            "--events",
            str(events_path),
        ]

    def _build_bridge_env(self) -> dict[str, str]:
        env = build_host_sdk_runtime_env(
            sdk_root=self._sdk_root,
            src_root=self._src_root,
            base_env=os.environ.copy(),
        )
        preserve_deployment_llm_model_env(
            env,
            deployment_model=self.settings.huawei_llm_model,
        )
        encrypted_reasoning_enabled = "1" if self.settings.openhands_enable_encrypted_reasoning else "0"
        env["OPENHANDS_ENABLE_ENCRYPTED_REASONING"] = encrypted_reasoning_enabled
        env["FEATURE_FACTORY_OPENHANDS_ENABLE_ENCRYPTED_REASONING"] = encrypted_reasoning_enabled
        env.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
        return env

    def _build_agent_bridge_env(self, agent_role: Literal["planner", "worker"]) -> dict[str, str]:
        env = self._build_bridge_env()
        set_openhands_llm_ssl_verify_env(env, verify=self.settings.llm_ssl_verify)
        llm_model = self.settings.stage2_agent_llm_model(agent_role)
        if llm_model:
            env["LLM_MODEL"] = llm_model
        llm_base_url = self.settings.stage2_agent_llm_base_url(agent_role)
        if llm_base_url:
            env["LLM_BASE_URL"] = llm_base_url
            if llm_model and not env.get(DEPLOYMENT_LLM_MODEL_ENV):
                preserve_deployment_llm_model_env(env, deployment_model=llm_model)
        api_key_secret = self.settings.stage2_agent_llm_api_key(agent_role)
        api_key = api_key_secret.get_secret_value() if api_key_secret else None
        if api_key:
            env["LLM_API_KEY"] = api_key
            env.setdefault("OPENAI_API_KEY", api_key)
        return env

    def _runtime_dir_for(self, workspace_dir: Path) -> Path:
        workspace_root = Path(self.settings.stage2_workspace_dir).expanduser().resolve()
        return workspace_root / "runtime" / workspace_dir.name

    def _drain_event_log(
        self,
        events_path: Path,
        offset: int,
        emit_event: BackendEventCallback | None,
        *,
        on_event: Callable[[Stage2BackendEvent], None] | None = None,
    ) -> int:
        if not events_path.exists():
            return offset
        if emit_event is None and on_event is None:
            return events_path.stat().st_size if events_path.exists() else offset
        with events_path.open("r", encoding="utf-8") as handle:
            handle.seek(offset)
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                event = _decode_backend_event(payload)
                if event is not None:
                    if on_event is not None:
                        on_event(event)
                    if emit_event is not None:
                        emit_event(event)
            return handle.tell()

    def _drain_bridge_output_logs(
        self,
        *,
        stdout_path: Path,
        stderr_path: Path,
        state: _BridgeOutputTailState,
        emit_event: BackendEventCallback | None,
        operation: str,
        phase: str,
        force: bool,
    ) -> None:
        if emit_event is None:
            return
        state.stdout_offset, stdout_lines = _read_new_bridge_log_lines(stdout_path, state.stdout_offset)
        state.stderr_offset, stderr_lines = _read_new_bridge_log_lines(stderr_path, state.stderr_offset)
        for line in stdout_lines:
            state.pending_lines.append(f"stdout: {line}")
        for line in stderr_lines:
            state.pending_lines.append(f"stderr: {line}")
        if not state.pending_lines:
            return

        now = time.monotonic()
        if not force and state.last_emit_monotonic and now - state.last_emit_monotonic < BRIDGE_LOG_EMIT_INTERVAL_SECONDS:
            return
        tail_lines = state.pending_lines[-BRIDGE_LOG_TAIL_LIMIT:]
        state.pending_lines.clear()
        state.last_emit_monotonic = now
        emit_event(
            Stage2BackendEvent(
                actor="system",
                phase=phase,
                title="OpenHands bridge output",
                message=tail_lines[-1] if tail_lines else "OpenHands bridge emitted process output",
                payload={
                    "operation": operation,
                    "tail_lines": tail_lines,
                    "stdout_log": str(stdout_path),
                    "stderr_log": str(stderr_path),
                },
            )
        )

    def _format_bridge_failure(self, *, label: str, stdout_path: Path, stderr_path: Path) -> str:
        stderr = stderr_path.read_text().strip() if stderr_path.exists() else ""
        stdout = stdout_path.read_text().strip() if stdout_path.exists() else ""
        details = stderr or stdout or "bridge process failed without logs"
        return f"OpenHands {label} failed: {details}"


def build_stage2_backend(settings: Settings, *, app_instance_id: str = "") -> Stage2Backend:
    backend_settings = settings.model_copy(deep=True)
    readiness = evaluate_openhands_readiness(backend_settings)
    return OpenHandsStage2Backend(
        backend_settings,
        selection_detail=(
            "Using OpenHands stage2 planner backend."
            if readiness["ready"]
            else f"OpenHands stage2 planner backend is not ready: {readiness['message']}"
        ),
        ready=bool(readiness["ready"]),
        readiness_message=str(readiness["message"]),
        app_instance_id=app_instance_id,
    )


def evaluate_openhands_readiness(settings: Settings) -> dict[str, Any]:
    sdk_root = Path(settings.stage2_openhands_sdk_root).expanduser().resolve()
    if not sdk_root.exists():
        return {
            "ready": False,
            "message": (
                f"software-agent-sdk root not found at {sdk_root}; "
                "clone with --recurse-submodules or run "
                "'git submodule update --init --recursive'"
            ),
        }
    if shutil.which("uv") is None:
        return {
            "ready": False,
            "message": "uv executable is not available on PATH",
        }
    if not settings.stage2_agent_llm_model("planner"):
        return {
            "ready": False,
            "message": (
                "stage2 planner LLM model is not configured; set FEATURE_FACTORY_STAGE2_PLANNER_LLM_MODEL, "
                "FEATURE_FACTORY_STAGE2_LLM_MODEL, or LLM_MODEL"
            ),
        }
    return {
        "ready": True,
        "message": "OpenHands backend is ready",
    }


def _repository_payload(repository: GitHubRepository, *, repo_path: Path | None = None) -> dict[str, Any]:
    return {
        "full_name": repository.full_name,
        "html_url": repository.html_url,
        "default_branch": repository.default_branch,
        "target_commit_sha": _repo_head_commit(repo_path),
    }


def _repo_head_commit(repo_path: Path | None) -> str:
    if repo_path is None:
        return ""
    completed = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "HEAD"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _decode_backend_event(payload: dict[str, Any]) -> Stage2BackendEvent | None:
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not title and not message:
        return None
    return Stage2BackendEvent(
        actor=str(payload.get("actor") or "agent"),
        phase=str(payload.get("phase") or "") or None,
        title=title or "OpenHands event",
        message=message or title,
        payload=dict(payload.get("payload") or {}),
    )


def _timeout_exempt_request_key(payload: dict[str, Any]) -> str:
    request_id = str(payload.get("request_id") or "").strip()
    if request_id:
        return request_id
    attempt_index = str(payload.get("attempt_index") or "").strip()
    return f"attempt:{attempt_index or 'unknown'}"


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _select_base_image(base_image_catalog: list[dict[str, Any]], *, image_id: str) -> dict[str, Any] | None:
    for item in base_image_catalog:
        if str(item.get("image_id") or "") == image_id:
            return item
    return None


def _total_token_usage(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    return sum(
        int(payload.get(key, 0) or 0)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "reasoning_tokens",
        )
    )


def _read_new_bridge_log_lines(path: Path, offset: int) -> tuple[int, list[str]]:
    if not path.exists():
        return offset, []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(offset)
        chunk = handle.read()
        next_offset = handle.tell()
    if not chunk:
        return next_offset, []
    lines = []
    for raw_line in chunk.replace("\r", "\n").splitlines():
        line = _clean_bridge_log_line(raw_line)
        if line:
            lines.append(line)
    return next_offset, lines


def _clean_bridge_log_line(value: str) -> str:
    line = _ANSI_ESCAPE_RE.sub("", value).strip()
    if not line:
        return ""
    return _truncate_text(line, limit=500)


def _truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."
