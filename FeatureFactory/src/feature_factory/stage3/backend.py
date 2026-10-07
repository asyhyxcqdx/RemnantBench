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
from typing import Any, Callable, Protocol

from pydantic import SecretStr

from feature_factory.config import Settings
from feature_factory.models import GitHubRepository
from feature_factory.openhands_llm import (
    DEPLOYMENT_LLM_MODEL_ENV,
    preserve_deployment_llm_model_env,
    set_openhands_llm_ssl_verify_env,
)
from feature_factory.stage3.bridge_events import (
    STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
    STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage2.backend import OPENHANDS_BRIDGE_PYTHON_VERSION
from feature_factory.stage2.image_assets import build_host_sdk_runtime_env

BackendEventCallback = Callable[["Stage3BackendEvent"], None]
BRIDGE_LOG_TAIL_LIMIT = 12
BRIDGE_LOG_EMIT_INTERVAL_SECONDS = 2.0
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True, slots=True)
class Stage3BackendEvent:
    actor: str
    phase: str | None
    title: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class BreakerExecutionResult:
    summary: str
    model: str
    token_usage: int
    token_usage_details: dict[str, Any]
    llm_completion_archive: dict[str, Any]
    execution_status: str
    savepoints: list[dict[str, Any]] = field(default_factory=list)


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

    def observe_event(self, event: Stage3BackendEvent) -> None:
        payload = dict(event.payload or {})
        if payload.get("operation") not in {
            STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
            STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
        }:
            return
        request_key = _timeout_exempt_request_key(payload)
        status = str(payload.get("status") or "").strip()
        now = self.clock()
        if status == STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED:
            self.active_exempt_starts.setdefault(request_key, now)
            return
        if status != STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED:
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
        active_seconds = sum(max(0.0, now - started_at) for started_at in self.active_exempt_starts.values())
        return self.exempt_credited_seconds + active_seconds


class Stage3Backend(Protocol):
    backend_name: str
    selection_detail: str

    def cancel_run(self, run_id: str) -> bool: ...

    def run_breaker(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        run_context: dict[str, Any],
        emit_event: BackendEventCallback | None = None,
    ) -> BreakerExecutionResult: ...


class OpenHandsStage3Backend:
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
        self._sdk_root = Path(settings.stage3_openhands_sdk_root).expanduser().resolve()
        self._bridge_module = "feature_factory.stage3.openhands_bridge"
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

    def run_breaker(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        run_context: dict[str, Any],
        emit_event: BackendEventCallback | None = None,
    ) -> BreakerExecutionResult:
        self._ensure_ready()
        breaker_preset = self.settings.stage3_agent_openhands_preset()
        breaker_max_iterations = self.settings.stage3_agent_openhands_max_iterations()
        breaker_timeout_seconds = self.settings.stage3_agent_timeout()
        breaker_model = self.settings.stage3_agent_llm_model() or ""
        if emit_event is not None:
            emit_event(
                Stage3BackendEvent(
                    actor="system",
                    phase="breaker_agent",
                    title="Breaker sandbox starting",
                    message=f"Launching OpenHands breaker sandbox for {repository.full_name}",
                    payload={
                        "operation": "openhands_bridge_breaker",
                        "backend": self.backend_name,
                        "workspace_dir": str(workspace_dir),
                        "repo_path": str(repo_path),
                        "runtime_dir": str(self._runtime_dir_for(workspace_dir)),
                        "model": breaker_model,
                        "preset": breaker_preset,
                        "max_iterations": breaker_max_iterations,
                        "timeout_seconds": breaker_timeout_seconds,
                    },
                )
            )
        request = {
            "mode": "breaker",
            "workspace_dir": str(workspace_dir),
            "repo_path": str(repo_path),
            "repository": _repository_payload(repository, repo_path=repo_path),
            "stage3": dict(run_context or {}),
            "resume_checkpoint": dict((run_context or {}).get("resume_checkpoint") or {}),
            "agent_timeout_seconds": breaker_timeout_seconds,
            "preset": breaker_preset,
            "max_iterations": breaker_max_iterations,
            "app_instance_id": self._app_instance_id,
        }
        response = self._run_bridge_request(
            request=request,
            workspace_dir=workspace_dir,
            label="breaker-attempt-1",
            emit_event=emit_event,
        )
        result = dict(response.get("result") or {})
        return BreakerExecutionResult(
            summary=str(result.get("summary") or "OpenHands breaker completed."),
            model=str(response.get("model") or breaker_model or "openhands-agent"),
            token_usage=_total_token_usage(response.get("token_usage")),
            token_usage_details=dict(response.get("token_usage") or {}),
            llm_completion_archive=dict(response.get("llm_completion_archive") or {}),
            execution_status=str(response.get("execution_status") or ""),
            savepoints=[
                dict(item)
                for item in list(response.get("savepoints") or [])
                if isinstance(item, dict)
            ],
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
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        command = self._build_bridge_command(
            request_path=request_path,
            result_path=result_path,
            events_path=events_path,
        )
        env = self._build_agent_bridge_env()
        timeout_seconds = self.settings.stage3_agent_timeout()
        output_state = _BridgeOutputTailState()
        run_id = workspace_dir.name
        timeout_budget = _BridgeTimeoutBudget(timeout_seconds=timeout_seconds)

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
            try:
                while True:
                    offset = self._drain_event_log(
                        events_path,
                        offset,
                        emit_event,
                        on_event=timeout_budget.observe_event,
                    )
                    self._drain_bridge_output_logs(
                        stdout_path=stdout_path,
                        stderr_path=stderr_path,
                        state=output_state,
                        emit_event=emit_event,
                        operation="openhands_bridge_breaker",
                        phase="breaker_agent",
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
                            on_event=timeout_budget.observe_event,
                        )
                        self._drain_bridge_output_logs(
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            state=output_state,
                            emit_event=emit_event,
                            operation="openhands_bridge_breaker",
                            phase="breaker_agent",
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
                            on_event=timeout_budget.observe_event,
                        )
                        self._drain_bridge_output_logs(
                            stdout_path=stdout_path,
                            stderr_path=stderr_path,
                            state=output_state,
                            emit_event=emit_event,
                            operation="openhands_bridge_breaker",
                            phase="breaker_agent",
                            force=True,
                        )
                        raise RuntimeError(
                            f"OpenHands {label} exceeded timeout of {timeout_seconds:.0f}s excluding save tool host time"
                        )
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
            operation="openhands_bridge_breaker",
            phase="breaker_agent",
            force=True,
        )
        if process.returncode != 0:
            raise RuntimeError(
                self._format_bridge_failure(label=label, stdout_path=stdout_path, stderr_path=stderr_path)
            )
        if not result_path.exists():
            raise RuntimeError(f"OpenHands {label} completed without writing a result payload")
        payload = json.loads(result_path.read_text(encoding="utf-8"))
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
        env["FEATURE_FACTORY_STAGE3_OPENHANDS_SDK_ROOT"] = str(self._sdk_root)
        env.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
        return env

    def _build_agent_bridge_env(self) -> dict[str, str]:
        env = self._build_bridge_env()
        set_openhands_llm_ssl_verify_env(env, verify=self.settings.llm_ssl_verify)
        llm_model = self.settings.stage3_agent_llm_model()
        if llm_model:
            env["LLM_MODEL"] = llm_model
        llm_base_url = self.settings.stage3_agent_llm_base_url()
        if llm_base_url:
            env["LLM_BASE_URL"] = llm_base_url
            if llm_model and not env.get(DEPLOYMENT_LLM_MODEL_ENV):
                preserve_deployment_llm_model_env(env, deployment_model=llm_model)
        api_key_secret = self.settings.stage3_agent_llm_api_key()
        api_key = api_key_secret.get_secret_value() if api_key_secret else None
        if api_key:
            env["LLM_API_KEY"] = api_key
            env.setdefault("OPENAI_API_KEY", api_key)
        return env

    def _runtime_dir_for(self, workspace_dir: Path) -> Path:
        workspace_root = Path(self.settings.stage3_workspace_dir).expanduser().resolve()
        return workspace_root / "runtime" / workspace_dir.name

    def _drain_event_log(
        self,
        events_path: Path,
        offset: int,
        emit_event: BackendEventCallback | None,
        *,
        on_event: Callable[[Stage3BackendEvent], None] | None = None,
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
            Stage3BackendEvent(
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
        stderr = stderr_path.read_text(encoding="utf-8").strip() if stderr_path.exists() else ""
        stdout = stdout_path.read_text(encoding="utf-8").strip() if stdout_path.exists() else ""
        details = stderr or stdout or "bridge process failed without logs"
        return f"OpenHands {label} failed: {details}"


def build_stage3_backend(settings: Settings, *, app_instance_id: str = "") -> Stage3Backend:
    backend_settings = settings.model_copy(deep=True)
    readiness = evaluate_openhands_readiness(backend_settings)
    return OpenHandsStage3Backend(
        backend_settings,
        selection_detail=(
            "Using OpenHands stage3 breaker backend."
            if readiness["ready"]
            else f"OpenHands stage3 breaker backend is not ready: {readiness['message']}"
        ),
        ready=bool(readiness["ready"]),
        readiness_message=str(readiness["message"]),
        app_instance_id=app_instance_id,
    )


def evaluate_openhands_readiness(settings: Settings) -> dict[str, Any]:
    sdk_root = Path(settings.stage3_openhands_sdk_root).expanduser().resolve()
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
    if not settings.stage3_agent_llm_model():
        return {
            "ready": False,
            "message": (
                "stage3 breaker LLM model is not configured; set FEATURE_FACTORY_STAGE3_LLM_MODEL or LLM_MODEL"
            ),
        }
    return {
        "ready": True,
        "message": "OpenHands backend is ready",
    }


def stage3_settings_with_runtime_snapshot(settings: Settings, runtime_snapshot: dict[str, Any]) -> Settings:
    resolved = settings.model_copy(deep=True)
    breaker = dict(runtime_snapshot.get("breaker") or {})
    hyperparameters = dict(runtime_snapshot.get("hyperparameters") or {})

    breaker_model = str(breaker.get("model") or "").strip()
    breaker_base_url = str(breaker.get("base_url") or "").strip()
    breaker_api_key = str(breaker.get("api_key") or "")
    breaker_has_api_key = "api_key" in breaker
    breaker_preset = str(breaker.get("preset") or "").strip()
    breaker_iterations = _positive_int(breaker.get("max_iterations"))
    breaker_timeout = _positive_float(breaker.get("timeout_seconds"))
    build_timeout_seconds = _positive_float(hyperparameters.get("build_timeout_seconds"))
    run_test_timeout_seconds = _positive_float(hyperparameters.get("run_test_timeout_seconds"))
    full_validation_timeout_seconds = _positive_float(hyperparameters.get("full_validation_timeout_seconds"))
    entry_pass_rate_ceiling = _pass_rate_ceiling(hyperparameters.get("entry_pass_rate_ceiling"))
    min_removed_code_lines = _nonnegative_int(hyperparameters.get("min_removed_code_lines"))

    resolved.stage3_llm_model = breaker_model or None
    resolved.stage3_llm_base_url = breaker_base_url or None
    if breaker_has_api_key:
        resolved.stage3_llm_api_key = SecretStr(breaker_api_key) if breaker_api_key else None
    if breaker_preset in {"default", "gpt5"}:
        resolved.stage3_openhands_preset = breaker_preset
    if breaker_iterations is not None:
        resolved.stage3_openhands_max_iterations = breaker_iterations
    if breaker_timeout is not None:
        resolved.stage3_agent_timeout_seconds = breaker_timeout
    if build_timeout_seconds is not None:
        resolved.stage3_build_timeout_seconds = build_timeout_seconds
    if run_test_timeout_seconds is not None:
        resolved.stage3_run_test_timeout_seconds = run_test_timeout_seconds
    if full_validation_timeout_seconds is not None:
        resolved.stage3_full_validation_timeout_seconds = full_validation_timeout_seconds
    if entry_pass_rate_ceiling is not None:
        resolved.stage3_entry_pass_rate_ceiling = entry_pass_rate_ceiling
    if min_removed_code_lines is not None:
        resolved.stage3_min_removed_code_lines = min_removed_code_lines
    return resolved


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


def _decode_backend_event(payload: dict[str, Any]) -> Stage3BackendEvent | None:
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not title and not message:
        return None
    return Stage3BackendEvent(
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
    depth = str(payload.get("depth") or "").strip()
    return f"depth:{depth or 'unknown'}"


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _nonnegative_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed >= 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        return None
    return parsed


def _pass_rate_ceiling(value: Any) -> float | None:
    parsed = _positive_float(value)
    if parsed is None:
        if value in {0, 0.0, "0", "0.0"}:
            return 0.0
        return None
    if parsed > 1.0:
        return None
    return parsed


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
    return value[: max(limit - 1, 0)] + "…"