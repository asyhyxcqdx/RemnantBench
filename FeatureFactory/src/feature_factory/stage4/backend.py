from __future__ import annotations

import json
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
from feature_factory.openhands_llm import (
    DEPLOYMENT_LLM_MODEL_ENV,
    preserve_deployment_llm_model_env,
    set_openhands_llm_ssl_verify_env,
)
from feature_factory.stage2.backend import OPENHANDS_BRIDGE_PYTHON_VERSION
from feature_factory.stage2.image_assets import build_host_sdk_runtime_env
from feature_factory.stage4.issue_styles import (
    IssueStyleConfigError,
    local_style_submission,
    runtime_issue_style_specs,
)
from feature_factory.stage4.service import (
    _leakage_check,
    _private_leakage_terms_for_source_context,
)
from feature_factory.stage4.style_outputs import validate_and_render_style_output

BackendEventCallback = Callable[["Stage4BackendEvent"], None]
BRIDGE_LOG_TAIL_LIMIT = 12
BRIDGE_LOG_EMIT_INTERVAL_SECONDS = 2.0
STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION = "openhands_bridge_issuer"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


@dataclass(frozen=True, slots=True)
class Stage4BackendEvent:
    actor: str
    phase: str | None
    title: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IssueGenerationResult:
    summary: str
    model: str
    token_usage: int
    token_usage_details: dict[str, Any]
    llm_completion_archive: dict[str, Any]
    bundle: dict[str, Any]


class Stage4Backend(Protocol):
    backend_name: str
    selection_detail: str

    def cancel_run(self, run_id: str) -> bool: ...

    def generate_issues(
        self,
        source_context: dict[str, Any],
        *,
        emit_event: BackendEventCallback | None = None,
    ) -> IssueGenerationResult: ...


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
    started_at: float | None = None

    def observe_event(self, event: Stage4BackendEvent) -> None:
        if self.started_at is not None:
            return
        if not str(event.title or "").strip().lower().endswith("conversation started"):
            return
        self.started_at = self.clock()

    def expired(self) -> bool:
        if self.started_at is None:
            return False
        return self.clock() >= self.started_at + self.timeout_seconds


class OpenHandsStage4Backend:
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
        self._sdk_root = Path(settings.stage4_openhands_sdk_root).expanduser().resolve()
        self._bridge_module = "feature_factory.stage4.openhands_bridge"
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

    def generate_issues(
        self,
        source_context: dict[str, Any],
        *,
        emit_event: BackendEventCallback | None = None,
    ) -> IssueGenerationResult:
        self._ensure_ready()
        stage4 = dict(source_context.get("stage4") or {})
        workspace = dict(stage4.get("workspace") or {})
        workspace_dir_raw = str(workspace.get("workspace_path") or "").strip()
        repo_path_raw = str(workspace.get("repo_dir") or "").strip()
        if not workspace_dir_raw:
            raise RuntimeError("stage4 source context is missing workspace_path")
        if not repo_path_raw:
            raise RuntimeError("stage4 source context is missing repo_dir")
        workspace_dir = Path(workspace_dir_raw).expanduser().resolve()
        repo_path = Path(repo_path_raw).expanduser().resolve()
        run_id = workspace_dir.name
        try:
            return self._generate_issues_for_run(
                source_context,
                stage4=stage4,
                workspace_dir=workspace_dir,
                repo_path=repo_path,
                emit_event=emit_event,
            )
        finally:
            self._clear_bridge_cancel_request(run_id)

    def _generate_issues_for_run(
        self,
        source_context: dict[str, Any],
        *,
        stage4: dict[str, Any],
        workspace_dir: Path,
        repo_path: Path,
        emit_event: BackendEventCallback | None,
    ) -> IssueGenerationResult:
        run_id = workspace_dir.name
        self._raise_if_bridge_cancel_requested(run_id, label="generation")
        repository = _repository_payload_from_context(source_context, repo_path=repo_path)
        issuer_preset = self.settings.stage4_agent_openhands_preset()
        issuer_max_iterations = self.settings.stage4_agent_openhands_max_iterations()
        issuer_timeout_seconds = self.settings.stage4_agent_timeout()
        issuer_model = self.settings.stage4_agent_llm_model() or ""
        generation_specs = runtime_issue_style_specs(stage4)
        base_request = {
            "mode": "issuer",
            "workspace_dir": str(workspace_dir),
            "repo_path": str(repo_path),
            "repository": repository,
            "stage4": dict(source_context or {}),
            "agent_timeout_seconds": issuer_timeout_seconds,
            "preset": issuer_preset,
            "max_iterations": issuer_max_iterations,
            "app_instance_id": self._app_instance_id,
        }
        combined_bundle: dict[str, Any] = {
            "issue_variants": [],
            "style_summaries": [],
        }
        combined_usage: dict[str, int] = {}
        archives: dict[str, Any] = {}
        model = issuer_model or "openhands-agent"
        for spec in generation_specs:
            label = f"{spec.id}-attempt-1"
            self._raise_if_bridge_cancel_requested(run_id, label=label)
            if emit_event is not None:
                emit_event(
                    Stage4BackendEvent(
                        actor="system",
                        phase=f"{spec.id}_agent",
                        title=f"{spec.display_name} agent starting",
                        message=(
                            f"Launching independent Stage4 {spec.id} conversation for "
                            f"{repository.get('full_name') or 'repository'}"
                        ),
                        payload={
                            "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                            "generation_style_id": spec.id,
                            "backend": self.backend_name,
                            "workspace_dir": str(workspace_dir),
                            "repo_path": str(repo_path),
                            "runtime_dir": str(self._runtime_dir_for(workspace_dir)),
                            "model": issuer_model,
                            "preset": issuer_preset,
                            "max_iterations": issuer_max_iterations,
                            "timeout_seconds": issuer_timeout_seconds,
                        },
                    )
                )
            try:
                response = self._run_bridge_request(
                    request={**base_request, "generation_style_id": spec.id},
                    workspace_dir=workspace_dir,
                    label=label,
                    emit_event=emit_event,
                )
            finally:
                self._cleanup_isolated_style_repo(
                    workspace_dir=workspace_dir,
                    style_id=spec.id,
                )
            self._raise_if_bridge_cancel_requested(run_id, label=label)
            result = dict(response.get("result") or {})
            bundle = dict(result.get("bundle") or {})
            if not bundle:
                raise RuntimeError(f"OpenHands {spec.id} agent completed without returning a bundle")
            combined_bundle["issue_variants"].extend(list(bundle.get("issue_variants") or []))
            combined_bundle["style_summaries"].append(
                {
                    "style": spec.id,
                    "summary": str(result.get("summary") or ""),
                }
            )
            usage = dict(response.get("token_usage") or {})
            for key, value in usage.items():
                if isinstance(value, (int, float)):
                    combined_usage[key] = combined_usage.get(key, 0) + int(value)
            archives[spec.id] = dict(response.get("llm_completion_archive") or {})
            response_model = str(response.get("model") or "").strip()
            if response_model:
                model = response_model
        self._raise_if_bridge_cancel_requested(run_id, label="generation")
        combined_bundle["source_summary"] = "Independent Stage4 agents generated the configured public assets."
        combined_bundle["self_check_notes"] = (
            "Each enabled style was generated in a separate OpenHands conversation and accepted by "
            "the host-side schema and leakage checks."
        )
        return IssueGenerationResult(
            summary="Independent OpenHands agents generated the configured Stage4 assets.",
            model=model,
            token_usage=_total_token_usage(combined_usage),
            token_usage_details=combined_usage,
            llm_completion_archive=archives,
            bundle=combined_bundle,
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

        timeout_seconds = self.settings.stage4_agent_timeout()
        output_state = _BridgeOutputTailState()
        run_id = workspace_dir.name
        timeout_budget = _BridgeTimeoutBudget(timeout_seconds=timeout_seconds)
        self._raise_if_bridge_cancel_requested(run_id, label=label)

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
                self._raise_if_bridge_cancel_requested(run_id, label=label)
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
                            force=True,
                        )
                        raise RuntimeError(f"OpenHands {label} interrupted by user")
                    if timeout_budget.expired():
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
                            force=True,
                        )
                        raise RuntimeError(
                            "OpenHands "
                            f"{label} exceeded timeout of {timeout_seconds:.0f}s excluding runtime "
                            "image and agent-server preparation time"
                        )
                    time.sleep(0.25)
            finally:
                if process.poll() is None:
                    self._interrupt_bridge_process(process)
                with self._bridge_process_lock:
                    if self._bridge_processes.get(run_id) is process:
                        self._bridge_processes.pop(run_id, None)

        self._drain_event_log(events_path, offset, emit_event, on_event=timeout_budget.observe_event)
        self._drain_bridge_output_logs(
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            state=output_state,
            emit_event=emit_event,
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

    def _raise_if_bridge_cancel_requested(self, run_id: str, *, label: str) -> None:
        if self._bridge_cancel_requested(run_id):
            raise RuntimeError(f"OpenHands {label} interrupted by user")

    def _clear_bridge_cancel_request(self, run_id: str) -> None:
        with self._bridge_process_lock:
            self._bridge_cancel_requests.discard(run_id)

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

    def _cleanup_isolated_style_repo(self, *, workspace_dir: Path, style_id: str) -> None:
        isolated_repo_dir = (
            self._runtime_dir_for(workspace_dir)
            / f"{style_id}_tool_workspace"
            / "repo"
        )
        if isolated_repo_dir.exists():
            shutil.rmtree(isolated_repo_dir)

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
        env["FEATURE_FACTORY_STAGE4_OPENHANDS_SDK_ROOT"] = str(self._sdk_root)
        env.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
        return env

    def _build_agent_bridge_env(self) -> dict[str, str]:
        env = self._build_bridge_env()
        set_openhands_llm_ssl_verify_env(env, verify=self.settings.llm_ssl_verify)
        llm_model = self.settings.stage4_agent_llm_model()
        if llm_model:
            env["LLM_MODEL"] = llm_model
        llm_base_url = self.settings.stage4_agent_llm_base_url()
        if llm_base_url:
            env["LLM_BASE_URL"] = llm_base_url
            if llm_model and not env.get(DEPLOYMENT_LLM_MODEL_ENV):
                preserve_deployment_llm_model_env(env, deployment_model=llm_model)
        api_key_secret = self.settings.stage4_agent_llm_api_key()
        api_key = api_key_secret.get_secret_value() if api_key_secret else None
        if api_key:
            env["LLM_API_KEY"] = api_key
            env.setdefault("OPENAI_API_KEY", api_key)
        return env

    def _runtime_dir_for(self, workspace_dir: Path) -> Path:
        workspace_root = Path(self.settings.stage4_workspace_dir).expanduser().resolve()
        return workspace_root / "runtime" / workspace_dir.name

    def _drain_event_log(
        self,
        events_path: Path,
        offset: int,
        emit_event: BackendEventCallback | None,
        on_event: Callable[[Stage4BackendEvent], None] | None = None,
    ) -> int:
        if not events_path.exists():
            return offset
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
            Stage4BackendEvent(
                actor="system",
                phase="issuer_agent",
                title="OpenHands bridge output",
                message=tail_lines[-1] if tail_lines else "OpenHands bridge emitted process output",
                payload={
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
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


class LocalStage4Backend:
    backend_name = "local"
    selection_detail = "Using deterministic Stage4 issuer backend."

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def cancel_run(self, run_id: str) -> bool:
        del run_id
        return True

    def generate_issues(
        self,
        source_context: dict[str, Any],
        *,
        emit_event: BackendEventCallback | None = None,
    ) -> IssueGenerationResult:
        if emit_event is not None:
            emit_event(
                Stage4BackendEvent(
                    actor="issuer_agent",
                    phase="issuer_agent",
                    title="Issuer generation started",
                    message="Generating configured issue styles from the saved repository context",
                    payload={
                        "backend": self.backend_name,
                        "source_savepoint_id": (source_context.get("savepoint") or {}).get("id"),
                    },
                )
            )
        bundle = _build_deterministic_bundle(source_context)
        if emit_event is not None:
            emit_event(
                Stage4BackendEvent(
                    actor="issuer_agent",
                    phase="issuer_agent",
                    title="Issuer generation finished",
                    message=f"Generated {len(bundle['issue_variants'])} issue variant(s)",
                    payload={
                        "backend": self.backend_name,
                        "issue_variant_count": len(bundle["issue_variants"]),
                    },
                )
            )
        return IssueGenerationResult(
            summary="Configured issue style variants generated.",
            model="stage4-local-issuer",
            token_usage=0,
            token_usage_details={},
            llm_completion_archive={},
            bundle=bundle,
        )


def build_stage4_backend(settings: Settings, *, app_instance_id: str = "") -> Stage4Backend:
    backend_settings = settings.model_copy(deep=True)
    readiness = evaluate_stage4_backend_readiness(backend_settings)
    return OpenHandsStage4Backend(
        backend_settings,
        selection_detail=(
            "Using OpenHands stage4 issuer backend."
            if readiness["ready"]
            else f"OpenHands stage4 issuer backend is not ready: {readiness['message']}"
        ),
        ready=bool(readiness["ready"]),
        readiness_message=str(readiness["message"]),
        app_instance_id=app_instance_id,
    )


def stage4_settings_with_runtime_snapshot(settings: Settings, runtime_snapshot: dict[str, Any]) -> Settings:
    resolved = settings.model_copy(deep=True)
    issuer = dict(runtime_snapshot.get("issuer") or {})
    hyperparameters = dict(runtime_snapshot.get("hyperparameters") or {})

    issuer_model = str(issuer.get("model") or "").strip()
    issuer_base_url = str(issuer.get("base_url") or "").strip()
    issuer_api_key = str(issuer.get("api_key") or "")
    issuer_has_api_key = "api_key" in issuer
    issuer_preset = str(issuer.get("preset") or "").strip()
    issuer_iterations = _positive_int(issuer.get("max_iterations"))
    issuer_timeout = _positive_float(issuer.get("timeout_seconds"))
    build_timeout = _positive_float(hyperparameters.get("build_timeout_seconds"))

    resolved.stage4_llm_model = issuer_model or None
    resolved.stage4_llm_base_url = issuer_base_url or None
    if issuer_has_api_key:
        resolved.stage4_llm_api_key = SecretStr(issuer_api_key) if issuer_api_key else None
    if issuer_preset in {"default", "gpt5"}:
        resolved.stage4_openhands_preset = issuer_preset
    if issuer_iterations is not None:
        resolved.stage4_openhands_max_iterations = issuer_iterations
    if issuer_timeout is not None:
        resolved.stage4_agent_timeout_seconds = issuer_timeout
    if build_timeout is not None:
        resolved.stage4_build_timeout_seconds = build_timeout
    return resolved


def evaluate_stage4_backend_readiness(settings: Settings) -> dict[str, Any]:
    sdk_root = Path(settings.stage4_openhands_sdk_root).expanduser().resolve()
    if not sdk_root.exists():
        return {
            "ready": False,
            "message": (
                f"software-agent-sdk root not found at {sdk_root}; "
                "clone with --recurse-submodules or run "
                "'git submodule update --init --recursive'"
            ),
            "backend": "openhands",
        }
    if shutil.which("uv") is None:
        return {
            "ready": False,
            "message": "uv executable is not available on PATH",
            "backend": "openhands",
        }
    if not settings.stage4_agent_llm_model():
        return {
            "ready": False,
            "message": (
                "stage4 issuer LLM model is not configured; set FEATURE_FACTORY_STAGE4_LLM_MODEL or LLM_MODEL"
            ),
            "backend": "openhands",
        }
    return {
        "ready": True,
        "message": "OpenHands stage4 issuer backend is ready",
        "backend": "openhands",
    }


def _repository_payload_from_context(source_context: dict[str, Any], *, repo_path: Path) -> dict[str, Any]:
    repository = dict(source_context.get("repository") or {})
    return {
        "full_name": str(repository.get("full_name") or ""),
        "html_url": str(repository.get("html_url") or ""),
        "default_branch": str(repository.get("default_branch") or ""),
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


def _decode_backend_event(payload: dict[str, Any]) -> Stage4BackendEvent | None:
    title = str(payload.get("title") or "").strip()
    message = str(payload.get("message") or "").strip()
    if not title and not message:
        return None
    return Stage4BackendEvent(
        actor=str(payload.get("actor") or "agent"),
        phase=str(payload.get("phase") or "") or None,
        title=title or "OpenHands event",
        message=message or title,
        payload=dict(payload.get("payload") or {}),
    )


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


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0.0 else None


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
    return value[: max(limit - 1, 0)] + "..."


def _build_deterministic_bundle(source_context: dict[str, Any]) -> dict[str, Any]:
    stage4 = dict(source_context.get("stage4") or {})
    generation_specs = runtime_issue_style_specs(stage4)

    repository = dict(source_context.get("repository") or {})
    savepoint = dict(source_context.get("savepoint") or {})
    summary = dict(savepoint.get("summary") or {})
    feature_summary = _feature_summary(source_context)
    repo_name = str(repository.get("full_name") or "repository")

    private = dict(source_context.get("private") or {})
    private_terms = _private_leakage_terms_for_source_context(source_context)
    issue_variants = []
    for index, spec in enumerate(generation_specs, start=1):
        submission = local_style_submission(spec)
        title = str(submission["title"])
        fields = dict(submission["fields"])
        style_result = validate_and_render_style_output(
            spec=spec,
            title=title,
            fields=fields,
            repository_name=repo_name,
        )
        if not style_result.accepted:
            raise IssueStyleConfigError(
                f"local example for style {spec.id} failed its own contract: {style_result.errors}"
            )
        leakage = _leakage_check(
            "\n".join(
                (
                    title,
                    json.dumps(fields, ensure_ascii=False, sort_keys=True),
                    style_result.rendered_markdown,
                )
            ),
            gold_patch_text=str(private.get("gold_patch_text") or ""),
            private_terms=private_terms,
        )
        if not leakage["passed"]:
            raise IssueStyleConfigError(
                f"local example for style {spec.id} leaks private context: {leakage['reasons']}"
            )
        issue_variants.append(
            {
                "style": spec.id,
                "title": title,
                "issue_markdown": style_result.rendered_markdown,
                "issue_json": {
                    "variant_index": index,
                    "style": spec.id,
                    "repository": repo_name,
                    "title": title,
                    "content": style_result.rendered_markdown,
                    "fields": fields,
                    "submission_mode": "style_local_example",
                },
                "quality_json": {
                    "submission_mode": "style_local_example",
                    "style_validation": style_result.validation,
                },
                "leakage_check_json": leakage,
            }
        )

    return {
        "issue_variants": issue_variants,
        "source_summary": (
            f"{repo_name} savepoint depth {savepoint.get('depth')} breaks a public feature behavior: "
            f"{feature_summary}."
        ),
        "self_check_notes": (
            "Generated task input from private context without patch hunks, test paths, "
            "runner commands, or concrete code edits."
        ),
        "private_context_used": {
            "gold_patch_available": bool(str((source_context.get("private") or {}).get("gold_patch_text") or "").strip()),
            "milestone_summary": summary.get("milestone_summary"),
            "rationale": summary.get("rationale"),
        },
    }


def _feature_summary(source_context: dict[str, Any]) -> str:
    savepoint = dict(source_context.get("savepoint") or {})
    summary = dict(savepoint.get("summary") or {})
    private = dict(source_context.get("private") or {})
    private_terms = _private_leakage_terms_for_source_context(source_context)
    candidates = [
        summary.get("milestone_summary"),
        summary.get("rationale"),
    ]
    for candidate in candidates:
        text = re.sub(r"\s+", " ", str(candidate or "")).strip()
        if text and _leakage_check(
            text,
            gold_patch_text=str(private.get("gold_patch_text") or ""),
            private_terms=private_terms,
        )["passed"]:
            return text.rstrip(".")
    return "the affected public feature behavior"


def _format_percent(value: Any) -> str:
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "-"