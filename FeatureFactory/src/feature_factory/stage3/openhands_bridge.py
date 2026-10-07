from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from contextlib import ExitStack, contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import LLMCompletionLogEvent, ObservationEvent
from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_condenser, get_default_tools
from openhands.tools.preset.gpt5 import get_gpt5_condenser, get_gpt5_tools

from feature_factory.config import get_settings
from feature_factory.db import build_engine, build_session_factory
from feature_factory.openhands_llm import (
    build_openhands_llm_config,
    openhands_llm_session_id,
    release_openhands_llm_resources,
)
from feature_factory.stage2.openhands_bridge import (
    WORKER_SANDBOX_WORKSPACE,
    _agent_server_container_labels,
    _append_event,
    _append_llm_completion_archive_event,
    _archive_llm_completion_event,
    _compact_openhands_event_payload,
    _conversation_failure_context,
    _conversation_failure_message,
    _conversation_metrics_payload,
    _conversation_execution_status,
    _docker_workspace,
    _event_summary,
    _has_token_usage,
    _llm_completion_event_payload,
    _make_container_readable_file,
    _make_container_traversable,
    _make_container_writable,
    _openhands_server_runtime_env,
    _resolve_worker_resume_server_image,
    _token_usage_from_event_payload,
    _token_usage_payload,
    _tracked_token_usage,
)
from feature_factory.stage2.raw_archive import (
    llm_completion_archive_dir,
    summarize_llm_completion_archive,
)
from feature_factory.stage2.worker_validation_queue import process_pending_validation_requests
from feature_factory.stage3.assets import read_text_asset
from feature_factory.stage3.bridge_events import (
    STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
    STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage3.openhands_save_tool import (
    SAVE_REQUEST_DIR_ENV,
    SAVE_RESULT_DIR_ENV,
    SaveTool,
)
from feature_factory.stage3.resume_paths import rewrite_stage3_json_tree_path_references
from feature_factory.stage3.runtime_images import ensure_stage3_breaker_runtime_image_built
from feature_factory.stage3.service import (
    Stage3SavepointRejectedError,
    Stage3Service,
    extract_stage3_runtime_snapshot,
    stage3_gold_patch_diff_pathspec_args,
)
from feature_factory.stage3.backend import stage3_settings_with_runtime_snapshot

FEATURE_FACTORY_STAGE3_TOOL_ROOT = ".feature_factory_openhands_tools"
FEATURE_FACTORY_STAGE3_RUNTIME_ROOT = ".stage3_runtime"
FEATURE_FACTORY_STAGE3_SAVE_DIR = "save_tool"
FEATURE_FACTORY_STAGE3_SAVE_REQUESTS_DIRNAME = "requests"
FEATURE_FACTORY_STAGE3_SAVE_RESULTS_DIRNAME = "results"
FEATURE_FACTORY_STAGE3_HOST_WORKSPACE_MOUNT = "/feature_factory_workspace"
FEATURE_FACTORY_STAGE3_HOST_REPO_DIR = f"{FEATURE_FACTORY_STAGE3_HOST_WORKSPACE_MOUNT}/repo"
FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR = f"{WORKER_SANDBOX_WORKSPACE}/repo"
FEATURE_FACTORY_STAGE3_CONTAINER_RUN_SCRIPT_PATH = f"{WORKER_SANDBOX_WORKSPACE}/run_script.sh"
FEATURE_FACTORY_STAGE3_BREAKER_STATUS_POLL_INTERVAL_SECONDS = 0.2
FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS = 60.0
STAGE3_OPENHANDS_BRIDGE_BREAKER_OPERATION = "openhands_bridge_breaker"
STAGE3_OPENHANDS_LLM_RETRY_OPERATION = "openhands_llm_retry_breaker"


def main() -> int:
    args = _parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    events_path = Path(args.events)
    result_path = Path(args.result)
    events_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        payload = _run_request(request=request, events_path=events_path)
        _write_json(result_path, payload)
        return 0
    except Exception as exc:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "breaker_agent",
                "title": "OpenHands bridge failed",
                "message": _truncate_text(str(exc), 1000),
                "payload": {
                    "operation": STAGE3_OPENHANDS_BRIDGE_BREAKER_OPERATION,
                    "error_type": type(exc).__name__,
                },
            },
        )
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FeatureFactory Stage3 OpenHands bridge")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--events", required=True)
    return parser.parse_args()


def _stage3_progress_log_callback(
    *,
    events_path: Path,
    title: str,
    operation: str,
    phase: str = "breaker_agent",
    parent_operation: str | None = None,
    batch_size: int = 8,
    flush_interval_seconds: float = 1.0,
) -> tuple[Callable[[str], None], Callable[[], None]]:
    pending_lines: list[str] = []
    last_flush_monotonic = time.monotonic()

    def flush(*, force: bool = False) -> None:
        nonlocal last_flush_monotonic
        if not pending_lines:
            return
        now = time.monotonic()
        if (
            not force
            and len(pending_lines) < batch_size
            and (now - last_flush_monotonic) < flush_interval_seconds
        ):
            return
        lines = pending_lines[:]
        pending_lines.clear()
        last_flush_monotonic = now
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": phase,
                "title": title,
                "message": lines[-1],
                "payload": {
                    "operation": operation,
                    "parent_operation": parent_operation or operation,
                    "tail_lines": lines,
                },
            },
        )

    def callback(line: str) -> None:
        normalized = _truncate_text(str(line or "").rstrip("\n"), 4000).strip()
        if not normalized:
            return
        pending_lines.append(normalized)
        flush(force=False)

    return callback, lambda: flush(force=True)


@contextmanager
def _bridge_interrupt_cancel_scope() -> Callable[[], bool]:
    cancel_event = threading.Event()
    installed_handlers: dict[int, Any] = {}

    def handler(signum, frame) -> None:  # noqa: ARG001
        cancel_event.set()

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            installed_handlers[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, handler)
        except ValueError:
            installed_handlers.clear()
            break

    try:
        yield cancel_event.is_set
    finally:
        for sig, previous_handler in installed_handlers.items():
            try:
                signal.signal(sig, previous_handler)
            except ValueError:
                continue


def _run_request(*, request: dict[str, Any], events_path: Path) -> dict[str, Any]:
    mode = str(request.get("mode") or "")
    if mode != "breaker":
        raise RuntimeError(f"unsupported stage3 bridge mode: {mode}")

    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "breaker_agent",
            "title": "OpenHands bridge initialized",
            "message": "Bridge process is preparing the breaker agent runtime",
            "payload": {
                "operation": "openhands_bridge_breaker",
                "mode": mode,
                "workspace_dir": str(request.get("workspace_dir") or ""),
            },
        },
    )
    llm = _build_llm(events_path=events_path)
    try:
        completion_archive_dir = Path(str(llm.log_completions_folder))
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "breaker_agent",
                "title": "OpenHands LLM configured",
                "message": f"LLM configured for breaker agent: {llm.model}",
                "payload": {
                    "operation": "openhands_bridge_breaker",
                    "model": llm.model,
                    "base_url": str(getattr(llm, "base_url", "") or ""),
                    "llm_completion_archive_dir": str(completion_archive_dir),
                    "llm_session_id": openhands_llm_session_id(llm) or "",
                },
            },
        )
        agent = _build_agent(llm=llm, preset=str(request.get("preset") or "gpt5"))
        max_iterations = int(request.get("max_iterations") or 150)
        workspace_dir = Path(str(request.get("workspace_dir") or "")).resolve()
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "breaker_agent",
                "title": "OpenHands agent constructed",
                "message": f"Breaker preset {request.get('preset') or 'gpt5'} is ready with {max_iterations} max iterations",
                "payload": {
                    "operation": "openhands_bridge_breaker",
                    "preset": str(request.get("preset") or "gpt5"),
                    "max_iterations": max_iterations,
                },
            },
        )
        return _run_breaker(
            request=request,
            workspace_dir=workspace_dir,
            agent=agent,
            llm=llm,
            max_iterations=max_iterations,
            events_path=events_path,
        )
    finally:
        _release_stage3_llm_session(llm=llm, events_path=events_path)


def _run_breaker(
    *,
    request: dict[str, Any],
    workspace_dir: Path,
    agent,
    llm: LLM,
    max_iterations: int,
    events_path: Path,
) -> dict[str, Any]:
    stage3_payload = dict(request.get("stage3") or {})
    runtime_snapshot = extract_stage3_runtime_snapshot(dict(stage3_payload.get("runtime") or {}))
    resolved_settings = stage3_settings_with_runtime_snapshot(
        get_settings().model_copy(deep=True),
        runtime_snapshot,
    )
    entry_pass_rate_ceiling = float(
        getattr(resolved_settings, "stage3_entry_pass_rate_ceiling", 0.5)
    )
    min_removed_code_lines = int(
        getattr(resolved_settings, "stage3_min_removed_code_lines", 10)
    )
    support = _prepare_breaker_support_files(workspace_dir=workspace_dir)
    save_service = _Stage3SaveService(
        run_id=workspace_dir.name,
        workspace_dir=workspace_dir,
        runtime_dir=events_path.parent,
        events_path=events_path,
        request_dir=support["save_request_dir_host"],
        result_dir=support["save_result_dir_host"],
        checkpoint_enabled=bool(resolved_settings.stage3_enable_checkpoints),
    )
    resume_checkpoint = dict(request.get("resume_checkpoint") or {})
    resume_server_image_ref = _resolve_worker_resume_server_image(
        checkpoint=resume_checkpoint,
        events_path=events_path,
    )
    platform_name = _detect_platform()
    runtime_base_image_ref = ""
    if not resume_server_image_ref:
        runtime_image_started_at = time.monotonic()
        runtime_image_log_callback, flush_runtime_image_logs = _stage3_progress_log_callback(
            events_path=events_path,
            title="Stage3 runtime image build output",
            operation="stage3_runtime_image",
        )
        runtime_image_prepared = False
        _append_stage3_host_setup_timeout_event(
            events_path=events_path,
            title="Stage3 runtime image preparation started",
            message="Preparing the Stage3 runtime image outside breaker agent time accounting",
            request_id="stage3-runtime-image",
            step="stage3_runtime_image",
            status=STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
        )
        try:
            with _bridge_interrupt_cancel_scope() as runtime_image_cancel_requested:
                runtime_base_image_ref = ensure_stage3_breaker_runtime_image_built(
                    workspace_dir=workspace_dir,
                    snapshot_id=str(stage3_payload.get("snapshot_id") or ""),
                    source_stage2_run_id=str(stage3_payload.get("source_stage2_run_id") or ""),
                    source_commit_sha=str(stage3_payload.get("source_commit_sha") or ""),
                    base_image_id=str(stage3_payload.get("base_image") or ""),
                    platform_name=platform_name,
                    timeout_seconds=float(resolved_settings.stage3_build_timeout_seconds),
                    log_callback=runtime_image_log_callback,
                    cancel_requested=runtime_image_cancel_requested,
                    emit_event=lambda title, message, payload: _append_event(
                        events_path,
                        {
                            "actor": "system",
                            "phase": "breaker_agent",
                            "title": title,
                            "message": message,
                            "payload": {
                                "operation": "stage3_runtime_image",
                                **dict(payload or {}),
                            },
                        },
                    ),
                )
            runtime_image_prepared = True
        finally:
            flush_runtime_image_logs()
            if runtime_image_prepared:
                _append_stage3_host_setup_timeout_event(
                    events_path=events_path,
                    title="Stage3 runtime image preparation completed",
                    message="Stage3 runtime image preparation finished",
                    request_id="stage3-runtime-image",
                    step="stage3_runtime_image",
                    status=STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    duration_seconds=max(0.0, time.monotonic() - runtime_image_started_at),
                )
    prompt = _build_breaker_prompt(
        request=request,
        stage3_payload={
            **stage3_payload,
            "entry_pass_rate_ceiling": entry_pass_rate_ceiling,
            "min_removed_code_lines": min_removed_code_lines,
        },
    )
    conversation_ref: dict[str, Any] = {
        "llm_completion_archive_dir": str(llm.log_completions_folder),
    }

    def callback(event) -> None:
        archived_file = _archive_llm_completion_event(
            event,
            archive_dir=Path(str(llm.log_completions_folder)),
        )
        save_service.request_checkpoint_for_save_observation(event=event)
        _append_event(
            events_path,
            _event_payload(
                event=event,
                conversation_ref=conversation_ref,
                archived_completion_file=archived_file,
            ),
        )

    openhands_runtime_env = _openhands_server_runtime_env(runtime_dir=events_path.parent, role="breaker")
    conversation_id = _prepare_breaker_resume_conversation_state(
        checkpoint=resume_checkpoint,
        runtime_env=openhands_runtime_env,
        events_path=events_path,
        stage3_root=workspace_dir.parents[1],
    )
    agent_server_started_at = time.monotonic()
    agent_server_log_callback, flush_agent_server_logs = _stage3_progress_log_callback(
        events_path=events_path,
        title="Breaker agent-server image build output",
        operation=STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
    )
    _append_stage3_host_setup_timeout_event(
        events_path=events_path,
        title="Breaker agent-server image preparation started",
        message="Preparing the OpenHands agent-server image outside breaker agent time accounting",
        request_id="breaker-agent-server-image",
        step="agent_server_image",
        status=STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
    )
    try:
        with ExitStack() as stack:
            with _bridge_interrupt_cancel_scope() as agent_server_cancel_requested:
                workspace = stack.enter_context(
                    _docker_workspace(
                        workspace_dir,
                        base_image=runtime_base_image_ref or resume_server_image_ref or "feature-factory/stage3-runtime:missing",
                        server_image_override=resume_server_image_ref,
                        mount_target=FEATURE_FACTORY_STAGE3_HOST_WORKSPACE_MOUNT,
                        working_dir=FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR,
                        runtime_dir=events_path.parent,
                        openhands_role="breaker",
                        agent_server_build_timeout_seconds=float(resolved_settings.stage3_build_timeout_seconds),
                        extra_volumes=_breaker_extra_volumes(support=support),
                        labels=_agent_server_container_labels(
                            request=request,
                            runtime_dir=events_path.parent,
                            role="breaker",
                            stage="stage3",
                        ),
                        agent_server_log_callback=agent_server_log_callback,
                        agent_server_cancel_requested=agent_server_cancel_requested,
                        forward_env_names=[
                            "OH_CONVERSATIONS_PATH",
                            "OH_BASH_EVENTS_DIR",
                            "PYTHONPATH",
                            SAVE_REQUEST_DIR_ENV,
                            SAVE_RESULT_DIR_ENV,
                        ],
                        forward_env_updates={
                            **openhands_runtime_env,
                            "PYTHONPATH": str(support["tool_python_root_container"]),
                            SAVE_REQUEST_DIR_ENV: str(support["save_request_dir_container"]),
                            SAVE_RESULT_DIR_ENV: str(support["save_result_dir_container"]),
                        },
                    )
                )
            _append_stage3_host_setup_timeout_event(
                events_path=events_path,
                title="Breaker agent-server image preparation completed",
                message="OpenHands agent-server image preparation finished",
                request_id="breaker-agent-server-image",
                step="agent_server_image",
                status=STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                duration_seconds=max(0.0, time.monotonic() - agent_server_started_at),
            )
            baseline_commit_sha = str(
                stage3_payload.get("baseline_commit_sha")
                or stage3_payload.get("source_commit_sha")
                or ""
            ).strip()
            git_baseline_payload = _ensure_breaker_repo_git_baseline(
                workspace=workspace,
                baseline_commit_sha=baseline_commit_sha,
            )
            git_baseline_repaired = git_baseline_payload.get("repair_status") == "repaired"
            _append_event(
                events_path,
                {
                    "actor": "system",
                    "phase": "breaker_agent",
                    "title": "Breaker repository Git baseline ready",
                    "message": (
                        "Attached the live breaker repository to the frozen baseline Git objects"
                        if git_baseline_repaired
                        else "Verified the live breaker repository Git baseline"
                    ),
                    "payload": {
                        "operation": "stage3_breaker_git_baseline",
                        **git_baseline_payload,
                    },
                },
            )
            conversation = Conversation(
                agent=agent,
                workspace=workspace,
                conversation_id=conversation_id,
                callbacks=[callback],
                max_iteration_per_run=max_iterations,
                visualizer=None,
            )
            conversation_ref["conversation"] = conversation
            conversation_ref["workspace"] = workspace
            save_service.bind_conversation_ref(conversation_ref)
            save_service.start()
            _append_event(
                events_path,
                {
                    "actor": "breaker_agent",
                    "phase": "breaker_agent",
                    "title": "Breaker conversation started",
                    "message": (
                        f"OpenHands breaker is editing {request['repository']['full_name']} "
                        f"on runtime image {resume_server_image_ref or runtime_base_image_ref}"
                    ),
                    "payload": {
                        "workspace_dir": str(workspace_dir),
                        "repo_path": str(request["repo_path"]),
                        "base_image": str(stage3_payload.get("base_image") or ""),
                        "runtime_base_image_ref": runtime_base_image_ref,
                        "server_image_ref": resume_server_image_ref or "",
                        "resumed_from_checkpoint": bool(resume_checkpoint),
                        "resume_depth": int(resume_checkpoint.get("depth") or 0),
                    },
                },
            )
            try:
                if not resume_checkpoint:
                    conversation.send_message(prompt)
                conversation.run(blocking=False)
                _run_breaker_conversation_until_terminal(
                    conversation=conversation,
                    save_service=save_service,
                    conversation_ref=conversation_ref,
                    events_path=events_path,
                )
                _record_breaker_entry_pass_rate_ceiling_result(
                    savepoint_history=save_service.history_payload(),
                    entry_pass_rate_ceiling=entry_pass_rate_ceiling,
                    events_path=events_path,
                )
            except Exception as exc:
                context = _append_breaker_conversation_failure_event(
                    events_path=events_path,
                    conversation_ref=conversation_ref,
                    exc=exc,
                )
                raise RuntimeError(
                    _conversation_failure_message(
                        "OpenHands breaker conversation failed",
                        context,
                        exc,
                    )
                ) from exc
            finally:
                save_service.stop()
                _append_llm_completion_archive_event(
                    events_path,
                    mode="breaker",
                    archive_dir=Path(str(llm.log_completions_folder)),
                )
    finally:
        flush_agent_server_logs()

    return {
        "status": "completed",
        "mode": "breaker",
        "model": llm.model,
        "token_usage": _token_usage_payload(conversation),
        "conversation_id": str(conversation.state.id),
        "execution_status": str(conversation.state.execution_status),
        "llm_completion_archive": summarize_llm_completion_archive(events_path.parent, "breaker"),
        "result": {
            "summary": "OpenHands breaker completed the current Stage3 run.",
            "artifacts_present": True,
        },
        "savepoints": save_service.history_payload(),
    }


def _build_llm(*, events_path: Path) -> LLM:
    llm_config = build_openhands_llm_config(usage_id="stage3-breaker")
    completions_dir = llm_completion_archive_dir(events_path.parent, "breaker").resolve()
    completions_dir.mkdir(parents=True, exist_ok=True)
    return LLM(
        usage_id="stage3-breaker",
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        enable_encrypted_reasoning=_openhands_enable_encrypted_reasoning(),
        log_completions=True,
        log_completions_folder=str(completions_dir),
        retry_listener=_build_stage3_llm_retry_listener(
            model=llm_config.model,
            events_path=events_path,
        ),
        **llm_config.optional_kwargs,
    )


def _release_stage3_llm_session(*, llm: LLM, events_path: Path) -> None:
    session_id = openhands_llm_session_id(llm)
    if not session_id:
        return

    def on_error(error: str) -> None:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "breaker_agent",
                "title": "OpenHands LLM session release failed",
                "message": (
                    f"Could not release LLM session {session_id}: "
                    f"{_truncate_text(error, 600)}"
                ),
                "payload": {
                    "operation": "openhands_bridge_breaker",
                    "llm_session_id": session_id,
                    "error": _truncate_text(error, 1000),
                },
            },
        )

    release_openhands_llm_resources(llm=llm, on_error=on_error)


def _openhands_enable_encrypted_reasoning() -> bool:
    raw_value = os.getenv("OPENHANDS_ENABLE_ENCRYPTED_REASONING")
    if raw_value is None:
        raw_value = os.getenv("FEATURE_FACTORY_OPENHANDS_ENABLE_ENCRYPTED_REASONING")
    return _parse_bool_env(raw_value, default=True)


def _parse_bool_env(raw_value: str | None, *, default: bool) -> bool:
    if raw_value is None:
        return default
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _build_stage3_llm_retry_listener(*, model: str, events_path: Path):
    def listener(attempt_number: int, max_attempts: int, exc: BaseException | None) -> None:
        error_type = type(exc).__name__ if exc is not None else "UnknownError"
        error_message = _truncate_text(str(exc or ""), 600)
        next_attempt = int(attempt_number) + 1
        delay_seconds = _estimated_stage3_llm_retry_delay_seconds(attempt_number)
        message = (
            f"{model} LLM request failed with {error_type}; "
            f"retrying attempt {next_attempt}/{max_attempts}"
        )
        if delay_seconds is not None:
            message = f"{message} after about {delay_seconds:g}s"
        try:
            _append_event(
                events_path,
                {
                    "actor": "system",
                    "phase": "breaker_agent",
                    "title": "OpenHands LLM retrying",
                    "message": message,
                    "payload": {
                        "operation": STAGE3_OPENHANDS_LLM_RETRY_OPERATION,
                        "parent_operation": STAGE3_OPENHANDS_LLM_RETRY_OPERATION,
                        "model": model,
                        "failed_attempt": int(attempt_number),
                        "next_attempt": next_attempt,
                        "max_attempts": int(max_attempts),
                        "retry_delay_seconds": delay_seconds,
                        "error_type": error_type,
                        "error_message": error_message,
                    },
                },
            )
        except Exception:
            return

    return listener


def _estimated_stage3_llm_retry_delay_seconds(attempt_number: int) -> float | None:
    try:
        failed_attempt = max(int(attempt_number), 1)
        retry_multiplier = 8.0
        retry_min_wait = 8.0
        retry_max_wait = 64.0
        delay = retry_multiplier * (2 ** (failed_attempt - 1))
        delay = max(delay, retry_min_wait)
        delay = min(delay, retry_max_wait)
        return float(delay)
    except Exception:
        return None


def _append_breaker_conversation_failure_event(
    *,
    events_path: Path,
    conversation_ref: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    context = _conversation_failure_context(conversation_ref=conversation_ref, exc=exc)
    _append_event(
        events_path,
        {
            "actor": "breaker_agent",
            "phase": "breaker_agent",
            "title": "Breaker conversation failed",
            "message": _conversation_failure_message(
                "OpenHands breaker conversation failed",
                context,
                exc,
            ),
            "payload": {
                "operation": STAGE3_OPENHANDS_BRIDGE_BREAKER_OPERATION,
                **context,
            },
        },
    )
    return context


def _build_agent(*, llm: LLM, preset: str):
    import feature_factory.stage3.openhands_save_tool  # noqa: F401

    extra_tools = [Tool(name=SaveTool.name)]
    if preset == "default":
        tools = get_default_tools(enable_browser=False)
        tools.extend(extra_tools)
        return Agent(
            llm=llm,
            tools=tools,
            system_prompt_kwargs={"cli_mode": True},
            condenser=get_default_condenser(
                llm=llm.model_copy(update={"usage_id": "condenser"})
            ),
        )
    tools = get_gpt5_tools(enable_browser=False)
    tools.extend(extra_tools)
    return Agent(
        llm=llm,
        tools=tools,
        system_prompt_kwargs={"cli_mode": True},
        condenser=get_gpt5_condenser(
            llm=llm.model_copy(update={"usage_id": "condenser"})
        ),
    )


def _build_breaker_prompt(*, request: dict[str, Any], stage3_payload: dict[str, Any]) -> str:
    repository_payload = {
        "full_name": str((request.get("repository") or {}).get("full_name") or ""),
        "workspace_repo_path": FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR,
    }
    entry_file_payload = {
        "test_file_path": str(stage3_payload.get("entry_file_path") or ""),
    }
    original_p2p_file_paths = _original_p2p_file_paths(stage3_payload.get("original_p2p_files"))
    prompt_template = read_text_asset("breaker_prompt.md")
    entry_pass_rate_ceiling_text = _stage3_breaker_entry_pass_rate_ceiling_text(
        stage3_payload.get("entry_pass_rate_ceiling")
    )
    min_removed_code_lines_text = _stage3_breaker_min_removed_code_lines_text(
        stage3_payload.get("min_removed_code_lines")
    )
    return (
        prompt_template.replace(
            "{{REPOSITORY_JSON}}",
            json.dumps(repository_payload, ensure_ascii=False, indent=2),
        )
        .replace(
            "{{ENTRY_FILE_JSON}}",
            json.dumps(entry_file_payload, ensure_ascii=False, indent=2),
        )
        .replace(
            "{{ORIGINAL_P2P_FILES_JSON}}",
            json.dumps(original_p2p_file_paths, ensure_ascii=False, indent=2),
        )
        .replace("{{ENTRY_PASS_RATE_CEILING_TEXT}}", entry_pass_rate_ceiling_text)
        .replace("{{MIN_REMOVED_CODE_LINES_TEXT}}", min_removed_code_lines_text)
        .replace("{{SAVE_TOOL_NAME}}", SaveTool.name)
    )


def _original_p2p_file_paths(raw_files: Any) -> list[str]:
    paths: list[str] = []
    for item in list(raw_files or []):
        if isinstance(item, dict):
            path = str(item.get("path") or "").strip()
        else:
            path = str(item or "").strip()
        if path:
            paths.append(path)
    return paths


def _stage3_breaker_entry_pass_rate_ceiling_text(raw_value: Any) -> str:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = 0.5
    if not value == value or value < 0.0 or value > 1.0:
        value = 0.5
    return f"{value:.3f} ({value:.1%})"


def _stage3_breaker_min_removed_code_lines_text(raw_value: Any) -> str:
    try:
        value = int(raw_value)
    except (TypeError, ValueError, OverflowError):
        value = 10
    if value < 0:
        value = 10
    return f"{value} line{'s' if value != 1 else ''}"


def _prepare_breaker_support_files(*, workspace_dir: Path) -> dict[str, Any]:
    workspace_dir = workspace_dir.resolve()
    repo_dir = workspace_dir / "repo"
    if repo_dir.exists():
        _make_container_writable(repo_dir, recursive=True)
    run_script_host = workspace_dir / "assets" / "run_script.sh"
    if not run_script_host.is_file():
        raise RuntimeError(f"stage3 run_script.sh asset is missing: {run_script_host}")
    run_script_host.chmod(0o755)
    tool_python_root_host = workspace_dir / FEATURE_FACTORY_STAGE3_TOOL_ROOT
    tool_module_dir = tool_python_root_host / "feature_factory" / "stage3"
    tool_module_dir.mkdir(parents=True, exist_ok=True)
    for package_init in (
        tool_python_root_host / "feature_factory" / "__init__.py",
        tool_module_dir / "__init__.py",
    ):
        package_init.parent.mkdir(parents=True, exist_ok=True)
        package_init.write_text("", encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parent / "openhands_save_tool.py",
        tool_module_dir / "openhands_save_tool.py",
    )
    stage3_runtime_root = workspace_dir / FEATURE_FACTORY_STAGE3_RUNTIME_ROOT
    stage3_runtime_root.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(stage3_runtime_root)
    save_root = stage3_runtime_root / FEATURE_FACTORY_STAGE3_SAVE_DIR
    save_root.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(save_root)
    request_dir_host = save_root / FEATURE_FACTORY_STAGE3_SAVE_REQUESTS_DIRNAME
    result_dir_host = save_root / FEATURE_FACTORY_STAGE3_SAVE_RESULTS_DIRNAME
    for directory in (request_dir_host, result_dir_host):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        _make_container_writable(directory)
    _make_container_writable(tool_python_root_host, recursive=True)
    return {
        "tool_python_root_host": tool_python_root_host,
        "tool_python_root_container": _host_mount_path_for_host_path(tool_python_root_host, workspace_dir=workspace_dir),
        "save_request_dir_host": request_dir_host,
        "save_request_dir_container": _host_mount_path_for_host_path(request_dir_host, workspace_dir=workspace_dir),
        "save_result_dir_host": result_dir_host,
        "save_result_dir_container": _host_mount_path_for_host_path(result_dir_host, workspace_dir=workspace_dir),
        "run_script_host": run_script_host,
        "run_script_container": FEATURE_FACTORY_STAGE3_CONTAINER_RUN_SCRIPT_PATH,
    }


def _breaker_extra_volumes(*, support: dict[str, Any]) -> list[str]:
    return [
        f"{Path(str(support['run_script_host'])).resolve()}:{support['run_script_container']}:ro",
    ]


def _host_mount_path_for_host_path(path: Path, *, workspace_dir: Path) -> str:
    relative = path.resolve().relative_to(workspace_dir.resolve())
    return str(Path(FEATURE_FACTORY_STAGE3_HOST_WORKSPACE_MOUNT) / relative)


def _run_breaker_conversation_until_terminal(
    *,
    conversation,
    save_service: "_Stage3SaveService",
    conversation_ref: dict[str, Any],
    events_path: Path,
) -> None:
    checkpoint_pause_key = ""
    checkpoint_pause_requested_at = 0.0
    paused_without_pending_since = 0.0
    while True:
        checkpoint_error = save_service.checkpoint_control_error()
        if checkpoint_error:
            raise RuntimeError(checkpoint_error)

        status = _conversation_execution_status(conversation, refresh=True)
        pending_checkpoint = save_service.pending_checkpoint_payload()
        pending_checkpoint_key = str(pending_checkpoint.get("checkpoint_key") or "")
        if not pending_checkpoint_key and _recover_pending_stage3_save_checkpoint_from_conversation_events(
            conversation=conversation,
            save_service=save_service,
        ):
            pending_checkpoint = save_service.pending_checkpoint_payload()
            pending_checkpoint_key = str(pending_checkpoint.get("checkpoint_key") or "")

        if pending_checkpoint_key:
            pending_label = str(pending_checkpoint.get("label") or pending_checkpoint_key)
            if checkpoint_pause_key != pending_checkpoint_key:
                checkpoint_pause_key = pending_checkpoint_key
                checkpoint_pause_requested_at = time.monotonic()
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "breaker_agent",
                        "title": "Breaker checkpoint pause requested",
                        "message": f"Save tool result {pending_label} returned; waiting for the breaker to pause",
                        "payload": dict(pending_checkpoint),
                    },
                )
            if status == ConversationExecutionStatus.PAUSED:
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "breaker_agent",
                        "title": "Breaker checkpoint pause confirmed",
                        "message": f"Breaker paused after save tool result {pending_label}",
                        "payload": dict(pending_checkpoint),
                    },
                )
                saved_checkpoint = save_service.checkpoint_pending_save_boundary(conversation_ref=conversation_ref)
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "breaker_agent",
                        "title": "Breaker checkpoint resume requested",
                        "message": (
                            "Resuming breaker conversation after checkpointing "
                            f"{saved_checkpoint.get('label') or saved_checkpoint.get('checkpoint_key')}"
                        ),
                        "payload": dict(saved_checkpoint),
                    },
                )
                conversation.run(blocking=False)
                checkpoint_pause_key = ""
                checkpoint_pause_requested_at = 0.0
                paused_without_pending_since = 0.0
                time.sleep(0.05)
                continue
            if status in (
                ConversationExecutionStatus.FINISHED,
                ConversationExecutionStatus.ERROR,
                ConversationExecutionStatus.STUCK,
            ):
                raise RuntimeError(
                    "breaker conversation reached a terminal state before the "
                    f"checkpoint pause completed for {pending_label}"
                )
            if (
                checkpoint_pause_requested_at > 0.0
                and (time.monotonic() - checkpoint_pause_requested_at)
                > FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS
            ):
                raise RuntimeError(
                    "breaker checkpoint pause timed out after "
                    f"{FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS:.0f}s "
                    f"for {pending_label}"
                )
        else:
            checkpoint_pause_key = ""
            checkpoint_pause_requested_at = 0.0

            if status == ConversationExecutionStatus.FINISHED:
                return
            if status == ConversationExecutionStatus.ERROR:
                raise RuntimeError("breaker conversation ended with error")
            if status == ConversationExecutionStatus.STUCK:
                raise RuntimeError("breaker conversation got stuck")
            if status == ConversationExecutionStatus.PAUSED:
                if paused_without_pending_since <= 0.0:
                    paused_without_pending_since = time.monotonic()
                elif (
                    time.monotonic() - paused_without_pending_since
                    > FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS
                ):
                    raise RuntimeError(
                        "breaker conversation remained paused without a pending checkpoint request"
                    )
            else:
                paused_without_pending_since = 0.0
        time.sleep(FEATURE_FACTORY_STAGE3_BREAKER_STATUS_POLL_INTERVAL_SECONDS)


def _openhands_observation_field(observation: Any, field_name: str, default: Any = None) -> Any:
    if isinstance(observation, dict):
        return observation.get(field_name, default)
    return getattr(observation, field_name, default)


def _latest_accepted_stage3_savepoint(
    savepoint_history: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for item in reversed(list(savepoint_history or [])):
        if isinstance(item, dict) and bool(item.get("accepted")):
            return dict(item)
    return None


def _record_breaker_entry_pass_rate_ceiling_result(
    *,
    savepoint_history: list[dict[str, Any]] | None,
    entry_pass_rate_ceiling: float,
    events_path: Path,
) -> bool:
    ceiling = float(entry_pass_rate_ceiling)
    latest = _latest_accepted_stage3_savepoint(savepoint_history)
    if latest is None:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "breaker_agent",
                "title": "Breaker finish threshold check",
                "message": (
                    "Breaker finished without any accepted savepoint; "
                    "the entry-file pass-rate ceiling was not met."
                ),
                "payload": {
                    "entry_pass_rate_ceiling": ceiling,
                    "savepoint_count": 0,
                    "threshold_met": False,
                },
            },
        )
        return False
    latest_entry_pass_rate = float(latest.get("entry_pass_rate") or 0.0)
    depth = int(latest.get("depth") or 0)
    met = latest_entry_pass_rate <= ceiling
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "breaker_agent",
            "title": "Breaker finish threshold check",
            "message": (
                "Breaker finished after meeting the entry-file pass-rate ceiling."
                if met
                else (
                    "Breaker finished before reaching the entry-file pass-rate ceiling; "
                    "the run will still complete with the latest archived savepoints."
                )
            ),
            "payload": {
                "entry_pass_rate_ceiling": ceiling,
                "latest_entry_pass_rate": latest_entry_pass_rate,
                "depth": depth,
                "threshold_met": met,
            },
        },
    )
    return met


def _recover_pending_stage3_save_checkpoint_from_conversation_events(
    *,
    conversation,
    save_service: "_Stage3SaveService",
) -> bool:
    state = getattr(conversation, "state", None)
    if state is None:
        return False
    events = getattr(state, "events", None)
    if events is None:
        return False
    reconcile = getattr(events, "reconcile", None)
    if callable(reconcile):
        try:
            reconcile()
        except Exception:
            pass
    try:
        snapshot = list(events)
    except Exception:
        return False
    for event in snapshot[-20:]:
        save_service.request_checkpoint_for_save_observation(event=event)
    return bool(save_service.pending_checkpoint_payload())


class _Stage3SaveService:
    def __init__(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        runtime_dir: Path,
        events_path: Path,
        request_dir: Path,
        result_dir: Path,
        checkpoint_enabled: bool = True,
    ) -> None:
        self.run_id = run_id
        self.workspace_dir = workspace_dir.resolve()
        self.runtime_dir = runtime_dir.resolve()
        self.events_path = events_path.resolve()
        self.request_dir = request_dir.resolve()
        self.result_dir = result_dir.resolve()
        self.checkpoint_enabled = bool(checkpoint_enabled)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._history: list[dict[str, Any]] = []
        self._processed_request_ids: set[str] = set()
        self._lock = threading.RLock()
        self._pending_checkpoint: dict[str, Any] | None = None
        self._checkpoint_control_error: str | None = None
        self._checkpointed_keys: set[str] = set()
        self._checkpoint_sequence = 0
        self._conversation_ref: dict[str, Any] = {}

    def bind_conversation_ref(self, conversation_ref: dict[str, Any]) -> None:
        with self._lock:
            self._conversation_ref = conversation_ref

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="feature-factory-stage3-save-tool",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def history_payload(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._history]

    def request_checkpoint_for_save_observation(self, *, event) -> int:
        if not self.checkpoint_enabled:
            return 0
        if not isinstance(event, ObservationEvent):
            return 0
        if str(getattr(event, "tool_name", "") or "") != SaveTool.name:
            return 0
        observation = getattr(event, "observation", None)
        try:
            pause_for_checkpoint = bool(
                _openhands_observation_field(observation, "pause_for_checkpoint", False)
            )
            accepted = bool(_openhands_observation_field(observation, "accepted", False))
            depth = int(_openhands_observation_field(observation, "depth", 0) or 0)
            request_id = str(
                _openhands_observation_field(observation, "request_id", "") or ""
            ).strip()
        except (TypeError, ValueError):
            return 0
        if not pause_for_checkpoint:
            return 0
        if accepted and depth <= 0:
            return 0
        if not accepted and not request_id:
            return 0
        checkpoint_key = f"depth-{depth:03d}" if accepted else f"request-{request_id}"
        with self._lock:
            pending_key = str((self._pending_checkpoint or {}).get("checkpoint_key") or "")
            if checkpoint_key in self._checkpointed_keys or pending_key == checkpoint_key:
                return 0
            self._checkpoint_sequence += 1
            checkpoint_index = self._checkpoint_sequence
            self._pending_checkpoint = {
                "checkpoint_index": checkpoint_index,
                "checkpoint_key": checkpoint_key,
                "request_id": request_id,
                "accepted": accepted,
                "depth": depth,
                "label": f"accepted savepoint depth {depth}" if accepted else f"rejected save request {request_id}",
            }
        return checkpoint_index

    def pending_checkpoint_payload(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._pending_checkpoint or {})

    def checkpoint_control_error(self) -> str:
        with self._lock:
            return str(self._checkpoint_control_error or "")

    def record_checkpoint_control_error(self, message: str) -> None:
        with self._lock:
            self._checkpoint_control_error = str(message or "")
        self._emit_event(
            title="Breaker checkpoint control failed",
            message=str(message or ""),
            payload={},
        )

    def checkpoint_pending_save_boundary(self, *, conversation_ref: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            pending_checkpoint = dict(self._pending_checkpoint or {})
            if not pending_checkpoint:
                return {}
            depth = int(pending_checkpoint.get("depth") or 0)
            accepted = bool(pending_checkpoint.get("accepted"))
            checkpoint_key = str(pending_checkpoint.get("checkpoint_key") or "")
            checkpoint_index = int(pending_checkpoint.get("checkpoint_index") or 0)
            label = str(pending_checkpoint.get("label") or checkpoint_key)
        self._emit_event(
            title="Breaker checkpoint started",
            message=f"Saving breaker checkpoint after save tool result {label}",
            payload=dict(pending_checkpoint),
        )
        checkpoint = self._create_checkpoint(
            checkpoint_request=pending_checkpoint,
            conversation_ref=conversation_ref,
        )
        if accepted and depth > 0:
            _with_stage3_service(
                lambda service: service.store_savepoint_checkpoint(
                    self.run_id,
                    depth=depth,
                    checkpoint=checkpoint,
                ),
                runtime_run_id=self.run_id,
            )
        else:
            _with_stage3_service(
                lambda service: service.store_runtime_checkpoint(
                    self.run_id,
                    checkpoint=checkpoint,
                ),
                runtime_run_id=self.run_id,
            )
        with self._lock:
            self._checkpointed_keys.add(checkpoint_key)
            self._pending_checkpoint = None
            for item in self._history:
                if str(item.get("request_id") or "") == str(pending_checkpoint.get("request_id") or ""):
                    item["checkpoint"] = dict(checkpoint)
        self._emit_event(
            title="Breaker checkpoint saved",
            message=f"Saved breaker checkpoint for save tool result {label}",
            payload={
                "depth": depth,
                "checkpoint_index": checkpoint_index,
                "checkpoint_key": checkpoint_key,
                "accepted": accepted,
                "checkpoint_dir": checkpoint.get("checkpoint_dir"),
                "workspace_snapshot_path": checkpoint.get("workspace_snapshot_path"),
                "docker_image_ref": checkpoint.get("docker_image_ref"),
                "docker_commit_status": checkpoint.get("docker_commit_status"),
                "conversation_id": checkpoint.get("conversation_id"),
            },
        )
        return dict(pending_checkpoint)

    def _run_loop(self) -> None:
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        _make_container_writable(self.request_dir)
        _make_container_writable(self.result_dir)
        while not self._stop_event.is_set():
            handled = process_pending_validation_requests(
                request_dir=self.request_dir,
                processed_request_ids=self._processed_request_ids,
                handle_request=self._handle_request,
                handle_error=self._handle_request_failure,
                handle_error_failure=self._handle_request_failure_error,
            )
            if not handled:
                self._stop_event.wait(0.2)

    def _handle_request(self, *, request_id: str, request_path: Path) -> None:
        request = _load_json(request_path)
        validate_timeout_exempt_started_at = time.monotonic()
        self._emit_event(
            title="Save tool call received",
            message="Breaker requested a Stage3 savepoint evaluation",
            payload={
                "request_id": request_id,
                "milestone_summary": str(request.get("milestone_summary") or ""),
                "rationale": str(request.get("rationale") or ""),
                "operation": STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
                "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
            },
        )
        try:
            repo_sync_payload = self._prepare_host_repo_for_evaluation()
            run, feedback = _with_stage3_service(
                lambda service: service.create_savepoint(
                    repository_id=service.get_run(self.run_id, include_detail=False).entry_file.snapshot.repository_id,
                    run_id=self.run_id,
                    milestone_summary=str(request.get("milestone_summary") or ""),
                    rationale=str(request.get("rationale") or ""),
                ),
                runtime_run_id=self.run_id,
            )
            savepoint_payload = next(
                (
                    item
                    for item in list(run.savepoints or [])
                    if int(item.depth or 0) == int(feedback.get("depth") or 0)
                ),
                None,
            )
            collateral_summary = dict((savepoint_payload.collateral_json if savepoint_payload is not None else {}) or {})
            feedback_for_agent = dict(feedback or {})
            live_git_payload = self._commit_live_breaker_savepoint(
                depth=int(feedback_for_agent.get("depth") or 0),
                baseline_commit_sha=str(repo_sync_payload.get("baseline_commit_sha") or ""),
            )
            live_git_commit_sha = str(live_git_payload.get("git_commit_sha") or "").strip()
            if live_git_commit_sha:
                feedback_for_agent["git_commit_sha"] = live_git_commit_sha
            else:
                feedback_for_agent.pop("git_commit_sha", None)
                if live_git_payload:
                    feedback_for_agent["git_restore_error"] = str(live_git_payload.get("error") or "unknown error")
            result_payload = {
                "request_id": request_id,
                "accepted": True,
                "depth": int(feedback_for_agent.get("depth") or 0),
                "message": str(feedback_for_agent.get("message") or ""),
                "entry_file_path": str(feedback_for_agent.get("entry_file_path") or ""),
                "entry_pass_rate": float(feedback_for_agent.get("entry_pass_rate") or 0.0),
                "previous_entry_pass_rate": float(feedback_for_agent.get("previous_entry_pass_rate") or 0.0),
                "p2p_count": int(feedback_for_agent.get("p2p_count") or 0),
                "f2p_count": int(feedback_for_agent.get("f2p_count") or 0),
                "feedback": feedback_for_agent,
                "collateral_summary": collateral_summary,
                "pause_for_checkpoint": self.checkpoint_enabled,
            }
            with self._lock:
                self._history.append({**result_payload, "checkpoint": {}})
            self._emit_event(
                title="Savepoint accepted",
                message=result_payload["message"],
                payload={
                    "operation": STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
                    "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    "request_id": request_id,
                    "depth": result_payload["depth"],
                    "duration_seconds": max(0.0, time.monotonic() - validate_timeout_exempt_started_at),
                    "accepted": True,
                    "repo_sync": repo_sync_payload,
                    "live_git": live_git_payload,
                },
            )
            self._write_result_payload(request_id, result_payload)
            return
        except Stage3SavepointRejectedError as exc:
            feedback = dict(exc.feedback or {})
            collateral_summary = dict(exc.collateral or {})
            result_payload = {
                "request_id": request_id,
                "accepted": False,
                "depth": 0,
                "message": str(exc),
                "entry_file_path": str(feedback.get("entry_file_path") or ""),
                "entry_pass_rate": float(feedback.get("entry_pass_rate") or 0.0),
                "previous_entry_pass_rate": float(feedback.get("previous_entry_pass_rate") or 0.0),
                "p2p_count": int(feedback.get("p2p_count") or 0),
                "f2p_count": int(feedback.get("f2p_count") or 0),
                "feedback": feedback,
                "collateral_summary": collateral_summary,
                "pause_for_checkpoint": self.checkpoint_enabled,
            }
            with self._lock:
                self._history.append({**result_payload, "checkpoint": {}})
            self._emit_event(
                title="Savepoint rejected",
                message=result_payload["message"],
                payload={
                    "operation": STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
                    "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    "request_id": request_id,
                    "duration_seconds": max(0.0, time.monotonic() - validate_timeout_exempt_started_at),
                    "accepted": False,
                    "feedback": feedback,
                    "collateral_summary": collateral_summary,
                    "repo_sync": repo_sync_payload,
                },
            )
            self._write_result_payload(request_id, result_payload)
            return
        except Exception as exc:
            self._emit_event(
                title="Savepoint evaluation failed",
                message=str(exc),
                payload={
                    "operation": STAGE3_SAVE_TIMEOUT_EXEMPT_OPERATION,
                    "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    "request_id": request_id,
                    "duration_seconds": max(0.0, time.monotonic() - validate_timeout_exempt_started_at),
                    "error_type": type(exc).__name__,
                },
            )
            raise

    def _handle_request_failure(
        self,
        request_id: str,
        request_path: Path,
        exc: Exception,
    ) -> None:
        message = f"save tool host failed while handling {request_path.name}: {exc}"
        result_payload = {
            "request_id": request_id,
            "accepted": False,
            "depth": 0,
            "message": message,
            "entry_file_path": "",
            "entry_pass_rate": 0.0,
            "previous_entry_pass_rate": 0.0,
            "p2p_count": 0,
            "f2p_count": 0,
            "feedback": {
                "accepted": False,
                "code": "SAVE_TOOL_HOST_ERROR",
                "message": str(exc),
                "retryable": False,
            },
            "collateral_summary": {},
            "pause_for_checkpoint": False,
        }
        self._write_result_payload(request_id, result_payload, allow_request_sidecar_fallback=True)
        self._emit_event(
            title="Save tool host failure",
            message=message,
            payload={
                "request_id": request_id,
                "request_path": str(request_path),
                "error_type": type(exc).__name__,
            },
        )

    def _handle_request_failure_error(
        self,
        request_id: str,
        request_path: Path,
        request_exc: Exception,
        error_handler_exc: Exception,
    ) -> bool:
        del request_path, request_exc
        try:
            self._write_result_payload(
                request_id,
                {
                    "request_id": request_id,
                    "accepted": False,
                    "depth": 0,
                    "message": str(error_handler_exc),
                    "entry_file_path": "",
                    "entry_pass_rate": 0.0,
                    "previous_entry_pass_rate": 0.0,
                    "p2p_count": 0,
                    "f2p_count": 0,
                    "feedback": {
                        "accepted": False,
                        "code": "SAVE_TOOL_HOST_FAILURE_HANDLER_ERROR",
                        "message": str(error_handler_exc),
                        "retryable": False,
                    },
                    "collateral_summary": {},
                    "pause_for_checkpoint": False,
                },
                allow_request_sidecar_fallback=True,
            )
            return True
        except Exception:
            return False

    def _write_result_payload(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        allow_request_sidecar_fallback: bool = False,
    ) -> Path:
        primary_path = self.result_dir / f"{request_id}.json"
        fallback_path = self.request_dir / f"{request_id}.result"
        try:
            _write_json(primary_path, payload)
            _make_container_readable_file(primary_path)
            return primary_path
        except Exception:
            if not allow_request_sidecar_fallback:
                raise
        _write_json(fallback_path, payload)
        _make_container_readable_file(fallback_path)
        return fallback_path

    def _prepare_host_repo_for_evaluation(self) -> dict[str, Any]:
        with self._lock:
            conversation_ref = dict(self._conversation_ref)
        workspace = conversation_ref.get("workspace")
        if workspace is None:
            raise RuntimeError("breaker workspace is unavailable for savepoint evaluation")
        baseline_commit_sha = _stage3_run_baseline_commit_sha(run_id=self.run_id)
        if not baseline_commit_sha:
            raise RuntimeError("stage3 run is missing baseline_commit_sha for savepoint evaluation")
        host_repo_dir = self.workspace_dir / "repo"
        patch_text = _extract_breaker_repo_patch(
            workspace=workspace,
            baseline_commit_sha=baseline_commit_sha,
        )
        _apply_repo_patch_to_host_workspace(
            host_repo_dir=host_repo_dir,
            baseline_commit_sha=baseline_commit_sha,
            patch_text=patch_text,
        )
        _make_container_writable(host_repo_dir, recursive=True)
        patch_bytes = len(patch_text.encode("utf-8"))
        self._emit_event(
            title="Breaker repo patch materialized",
            message=(
                "Prepared host-side evaluation repository from the live breaker repo diff "
                f"against baseline {baseline_commit_sha[:12]}"
            ),
            payload={
                "baseline_commit_sha": baseline_commit_sha,
                "repo_dir": str(host_repo_dir),
                "patch_bytes": patch_bytes,
            },
        )
        return {
            "baseline_commit_sha": baseline_commit_sha,
            "repo_dir": str(host_repo_dir),
            "patch_bytes": patch_bytes,
        }

    def _commit_live_breaker_savepoint(
        self,
        *,
        depth: int,
        baseline_commit_sha: str,
    ) -> dict[str, Any]:
        if depth <= 0:
            return {}
        with self._lock:
            conversation_ref = dict(self._conversation_ref)
        workspace = conversation_ref.get("workspace")
        return _commit_live_breaker_repo_savepoint(
            workspace=workspace,
            run_id=self.run_id,
            depth=depth,
            baseline_commit_sha=baseline_commit_sha,
        )

    def _create_checkpoint(
        self,
        *,
        checkpoint_request: dict[str, Any],
        conversation_ref: dict[str, Any],
    ) -> dict[str, Any]:
        depth = int(checkpoint_request.get("depth") or 0)
        checkpoint_index = int(checkpoint_request.get("checkpoint_index") or 0)
        checkpoint_key = str(checkpoint_request.get("checkpoint_key") or "").strip()
        checkpoint_dir_name = checkpoint_key or f"save-{checkpoint_index:03d}"
        safe_checkpoint_dir_name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", checkpoint_dir_name).strip("-")
        if not safe_checkpoint_dir_name:
            safe_checkpoint_dir_name = f"save-{checkpoint_index:03d}"
        conversation = conversation_ref.get("conversation")
        conversation_state = getattr(conversation, "state", None)
        conversation_id = str(getattr(conversation_state, "id", "") or "")
        workspace = conversation_ref.get("workspace")
        checkpoint_root = self.runtime_dir / "breaker-checkpoints" / safe_checkpoint_dir_name
        temp_dir = checkpoint_root.parent / f".tmp-{safe_checkpoint_dir_name}-{uuid.uuid4().hex}"
        new_image_ref = ""
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)
            workspace_snapshot_path = temp_dir / "workspace_snapshot.tar.gz"
            snapshot_payload = _create_workspace_snapshot(
                workspace_dir=self.workspace_dir,
                output_path=workspace_snapshot_path,
            )
            if str(snapshot_payload.get("status") or "") != "saved":
                raise RuntimeError(
                    "failed to create breaker workspace snapshot: "
                    f"{snapshot_payload.get('error') or 'unknown error'}"
                )
            docker_payload = _commit_breaker_container_checkpoint(
                run_id=self.run_id,
                checkpoint_key=safe_checkpoint_dir_name,
                workspace=workspace,
            )
            new_image_ref = str(docker_payload.get("docker_image_ref") or "").strip()
            baseline_commit_sha = _stage3_run_baseline_commit_sha(run_id=self.run_id)
            openhands_payload = {
                "host": str(getattr(workspace, "host", "") or ""),
                "host_port": getattr(workspace, "host_port", None),
                "working_dir": str(getattr(workspace, "working_dir", "") or ""),
                "container_id": str(getattr(workspace, "_container_id", "") or ""),
                "conversations_path": "",
                "bash_events_dir": "",
            }
            source_conversations_path_raw = str(os.getenv("OH_CONVERSATIONS_PATH", "") or "").strip()
            if source_conversations_path_raw and conversation_id:
                source_conversation_dir = Path(source_conversations_path_raw).expanduser() / uuid.UUID(conversation_id).hex
                if source_conversation_dir.exists():
                    destination_conversations_path = temp_dir / "openhands" / "conversations"
                    destination_conversation_dir = destination_conversations_path / uuid.UUID(conversation_id).hex
                    shutil.copytree(source_conversation_dir, destination_conversation_dir)
                    openhands_payload["conversations_path"] = str(checkpoint_root / "openhands" / "conversations")
            source_bash_events_dir_raw = str(os.getenv("OH_BASH_EVENTS_DIR", "") or "").strip()
            if source_bash_events_dir_raw:
                source_bash_events_path = Path(source_bash_events_dir_raw).expanduser()
                if source_bash_events_path.exists():
                    destination_bash_events_dir = temp_dir / "openhands" / "bash_events"
                    shutil.copytree(source_bash_events_path, destination_bash_events_dir)
                    openhands_payload["bash_events_dir"] = str(checkpoint_root / "openhands" / "bash_events")
            checkpoint = {
                "schema_version": 1,
                "checkpoint_type": "stage3_breaker_cold_restore",
                "run_id": self.run_id,
                "depth": depth,
                "checkpoint_index": checkpoint_index,
                "checkpoint_key": safe_checkpoint_dir_name,
                "save_request_id": str(checkpoint_request.get("request_id") or ""),
                "save_accepted": bool(checkpoint_request.get("accepted")),
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "checkpoint_dir": str(checkpoint_root),
                "workspace_snapshot_path": str(checkpoint_root / "workspace_snapshot.tar.gz"),
                "workspace_snapshot": {
                    **snapshot_payload,
                    "path": str(checkpoint_root / "workspace_snapshot.tar.gz"),
                },
                "conversation_id": conversation_id,
                "conversation_status": str(getattr(conversation_state, "execution_status", "") or ""),
                "workspace_dir": str(self.workspace_dir),
                "baseline_commit_sha": baseline_commit_sha,
                "openhands": openhands_payload,
                "docker_image_ref": docker_payload.get("docker_image_ref"),
                "docker_commit_status": docker_payload.get("docker_commit_status"),
                "docker_commit": docker_payload,
                "restore_notes": [
                    "This is a cold restore checkpoint for a Stage3 breaker conversation.",
                    "The full Stage3 workspace is included except checkpoint/savepoint runtime internals.",
                ],
            }
            _write_json(temp_dir / "checkpoint.json", checkpoint)
            if checkpoint_root.exists():
                shutil.rmtree(checkpoint_root)
            temp_dir.replace(checkpoint_root)
            return checkpoint
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if new_image_ref:
                _remove_breaker_checkpoint_image(new_image_ref)
            raise

    def _emit_event(self, *, title: str, message: str, payload: dict[str, Any]) -> None:
        _append_event(
            self.events_path,
            {
                "actor": "save_tool",
                "phase": "breaker_agent",
                "title": title,
                "message": message,
                "payload": payload,
            },
        )


def _create_workspace_snapshot(*, workspace_dir: Path, output_path: Path) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = str(member.name or "").lstrip("./")
        if name.startswith(".stage3/checkpoints") or name.startswith(".stage3/savepoints"):
            return None
        if name.startswith(".stage3_runtime/save_tool/requests") or name.startswith(".stage3_runtime/save_tool/results"):
            return None
        return member

    included_paths: list[str] = []
    try:
        if output_path.exists():
            output_path.unlink()
        with tarfile.open(output_path, "w:gz") as archive:
            for child in sorted(workspace_dir.iterdir(), key=lambda item: item.name):
                archive.add(child, arcname=child.name, filter=_filter)
                included_paths.append(child.name)
        stat = output_path.stat()
        return {
            "status": "saved",
            "path": str(output_path),
            "size_bytes": stat.st_size,
            "included_roots": included_paths,
            "excluded_patterns": [
                ".stage3/checkpoints",
                ".stage3/savepoints",
                ".stage3_runtime/save_tool/requests",
                ".stage3_runtime/save_tool/results",
            ],
        }
    except Exception as exc:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "status": "failed",
            "path": str(output_path),
            "included_roots": included_paths,
            "error": str(exc),
        }


def _commit_breaker_container_checkpoint(*, run_id: str, checkpoint_key: str, workspace) -> dict[str, Any]:
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        return {"docker_commit_status": "skipped", "reason": "container_id unavailable"}
    safe_run_id = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-")
    safe_checkpoint_key = re.sub(r"[^a-z0-9_.-]+", "-", str(checkpoint_key or "").lower()).strip("-")
    image_ref = f"feature-factory/stage3-breaker-checkpoint:{safe_run_id}-{safe_checkpoint_key}"
    command = ["docker", "commit", "--pause=true", container_id, image_ref]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
    except Exception as exc:
        return {
            "docker_commit_status": "failed",
            "container_id": container_id,
            "docker_image_ref": image_ref,
            "error": str(exc),
        }
    duration_seconds = max(time.monotonic() - started, 0.0)
    payload = {
        "docker_commit_status": "saved" if completed.returncode == 0 else "failed",
        "container_id": container_id,
        "docker_image_ref": image_ref,
        "returncode": completed.returncode,
        "duration_seconds": duration_seconds,
        "stdout": _truncate_text(str(completed.stdout or "").strip(), 2000),
        "stderr": _truncate_text(str(completed.stderr or "").strip(), 2000),
    }
    if completed.returncode != 0:
        payload["error"] = str(completed.stderr or completed.stdout or "").strip()
    return payload


def _remove_breaker_checkpoint_image(image_ref: str) -> None:
    reference = str(image_ref or "").strip()
    if not reference:
        return
    try:
        subprocess.run(
            ["docker", "image", "rm", "-f", reference],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except Exception:
        return


def _breaker_repo_git_baseline_script(
    *,
    repo_dir: str = FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR,
    baseline_repo_dir: str = FEATURE_FACTORY_STAGE3_HOST_REPO_DIR,
) -> str:
    """Attach a missing live-repo Git database to the host baseline without copying objects."""
    repo_assignment = shlex.quote(str(repo_dir))
    baseline_repo_assignment = shlex.quote(str(baseline_repo_dir))
    return (
        "set -eu\n"
        f"repo={repo_assignment}\n"
        f"baseline_repo={baseline_repo_assignment}\n"
        'host_git_objects="$baseline_repo/.git/objects"\n'
        'if [ ! -d "$host_git_objects" ]; then\n'
        '  echo "host baseline Git object directory is missing: $host_git_objects" >&2\n'
        "  exit 1\n"
        "fi\n"
        'probe_git="$(mktemp -d)"\n'
        'trap \'rm -rf "$probe_git"\' EXIT\n'
        'git init --bare -q "$probe_git"\n'
        'if ! GIT_DIR="$probe_git" GIT_ALTERNATE_OBJECT_DIRECTORIES="$host_git_objects" '
        'git cat-file -e "${BASELINE_COMMIT}^{commit}"; then\n'
        '  echo "host baseline Git objects do not contain commit $BASELINE_COMMIT" >&2\n'
        "  exit 1\n"
        "fi\n"
        'repo_git() { git -c "safe.directory=$repo" -C "$repo" "$@"; }\n'
        'if repo_git rev-parse --is-inside-work-tree >/dev/null 2>&1 '
        '&& repo_git cat-file -e "${BASELINE_COMMIT}^{commit}" >/dev/null 2>&1 '
        '&& repo_git rev-parse --verify HEAD >/dev/null 2>&1; then\n'
        "  printf '__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\\t%s\\t%s\\n' "
        "'existing' \"$BASELINE_COMMIT\"\n"
        "  exit 0\n"
        "fi\n"
        'rm -rf "$repo/.git"\n'
        'git -C "$repo" init -q\n'
        'mkdir -p "$repo/.git/objects/info"\n'
        'printf \'%s\\n\' "$host_git_objects" > "$repo/.git/objects/info/alternates"\n'
        'repo_git config user.name "FeatureFactory Stage3"\n'
        'repo_git config user.email "stage3@featurefactory.local"\n'
        'repo_git update-ref refs/heads/feature-factory-stage3 "$BASELINE_COMMIT"\n'
        'repo_git symbolic-ref HEAD refs/heads/feature-factory-stage3\n'
        'repo_git reset --mixed --quiet "$BASELINE_COMMIT"\n'
        "printf '__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\\t%s\\t%s\\n' "
        "'repaired' \"$BASELINE_COMMIT\"\n"
    )


def _ensure_breaker_repo_git_baseline(
    *,
    workspace,
    baseline_commit_sha: str,
) -> dict[str, Any]:
    """Keep the breaker worktree usable as Git while leaving its source files untouched."""
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        raise RuntimeError("breaker workspace container_id is unavailable")
    normalized_baseline_commit_sha = str(baseline_commit_sha or "").strip()
    if not normalized_baseline_commit_sha:
        raise RuntimeError("stage3 run is missing baseline_commit_sha")
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"BASELINE_COMMIT={normalized_baseline_commit_sha}",
            container_id,
            "sh",
            "-lc",
            _breaker_repo_git_baseline_script(),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip() or "docker exec failed"
        raise RuntimeError(f"failed to prepare breaker repository Git baseline: {error}")
    marker = "__FEATURE_FACTORY_STAGE3_GIT_BASELINE__"
    marker_payload = next(
        (
            line.removeprefix(marker).strip()
            for line in str(completed.stdout or "").splitlines()
            if line.startswith(marker)
        ),
        "",
    )
    marker_parts = marker_payload.split("\t") if marker_payload else []
    repair_status = marker_parts[0] if marker_parts else ""
    if repair_status not in {"existing", "repaired"}:
        raise RuntimeError("failed to prepare breaker repository Git baseline: status marker is missing")
    return {
        "status": "ready",
        "repair_status": repair_status,
        "baseline_commit_sha": (
            marker_parts[1] if len(marker_parts) > 1 else normalized_baseline_commit_sha
        ),
        "container_id": container_id,
    }


def _commit_live_breaker_repo_savepoint(
    *,
    workspace,
    run_id: str,
    depth: int,
    baseline_commit_sha: str,
) -> dict[str, Any]:
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        return {"git_commit_status": "skipped", "reason": "container_id unavailable"}
    try:
        git_baseline_payload = _ensure_breaker_repo_git_baseline(
            workspace=workspace,
            baseline_commit_sha=baseline_commit_sha,
        )
    except Exception as exc:
        return {
            "git_commit_status": "failed",
            "container_id": container_id,
            "error": str(exc),
        }
    script = (
        "set -eu\n"
        f"repo={FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR}\n"
        'repo_git() { git -c "safe.directory=$repo" -C "$repo" "$@"; }\n'
        'repo_git config user.name "FeatureFactory Stage3"\n'
        'repo_git config user.email "stage3@featurefactory.local"\n'
        'repo_git add -A\n'
        'commit_status="existing"\n'
        'if ! repo_git diff --cached --quiet; then\n'
        '  repo_git commit --no-verify -m "$SAVEPOINT_COMMIT_MESSAGE"\n'
        '  commit_status="created"\n'
        "fi\n"
        'git_commit_sha="$(repo_git rev-parse HEAD)"\n'
        'printf "__FEATURE_FACTORY_STAGE3_GIT_COMMIT__%s\\t%s\\n" "$git_commit_sha" "$commit_status"\n'
    )
    safe_run_id = re.sub(r"[^a-zA-Z0-9_.-]+", "-", str(run_id or "")).strip("-") or "unknown"
    commit_message = f"stage3 savepoint depth {int(depth):03d} for run {safe_run_id}"
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"SAVEPOINT_COMMIT_MESSAGE={commit_message}",
            container_id,
            "sh",
            "-lc",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    payload = {
        "git_commit_status": "saved" if completed.returncode == 0 else "failed",
        "container_id": container_id,
        "git_baseline": git_baseline_payload,
        "returncode": completed.returncode,
        "stdout": _truncate_text(str(completed.stdout or "").strip(), 2000),
        "stderr": _truncate_text(str(completed.stderr or "").strip(), 2000),
    }
    if completed.returncode != 0:
        payload["error"] = str(completed.stderr or completed.stdout or "").strip()
        return payload
    marker = "__FEATURE_FACTORY_STAGE3_GIT_COMMIT__"
    line = next(
        (
            item.removeprefix(marker)
            for item in str(completed.stdout or "").splitlines()
            if item.startswith(marker)
        ),
        "",
    )
    parts = line.strip().split("\t")
    if parts:
        payload["git_commit_sha"] = parts[0]
    if len(parts) > 1:
        payload["commit_status"] = parts[1]
    return payload


def _breaker_repo_patch_script(
    *,
    repo_dir: str = FEATURE_FACTORY_STAGE3_CONTAINER_REPO_DIR,
    baseline_repo_dir: str = FEATURE_FACTORY_STAGE3_HOST_REPO_DIR,
) -> str:
    """Diff the live worktree against baseline without trusting a broken live `.git`."""
    diff_pathspec_args = " ".join(
        shlex.quote(arg) for arg in stage3_gold_patch_diff_pathspec_args()
    )
    repo_assignment = shlex.quote(str(repo_dir))
    baseline_repo_assignment = shlex.quote(str(baseline_repo_dir))
    return (
        "set -eu\n"
        f"repo={repo_assignment}\n"
        f"baseline_repo={baseline_repo_assignment}\n"
        'tmp_root="$(mktemp -d)"\n'
        'trap \'rm -rf "$tmp_root"\' EXIT\n'
        'tmp_index="$tmp_root/index"\n'
        'if git -C "$repo" cat-file -e "${BASELINE_COMMIT}^{commit}" >/dev/null 2>&1; then\n'
        '  GIT_INDEX_FILE="$tmp_index" git -C "$repo" read-tree "$BASELINE_COMMIT"\n'
        '  GIT_INDEX_FILE="$tmp_index" git -C "$repo" add -A\n'
        '  GIT_INDEX_FILE="$tmp_index" git -C "$repo" diff --binary --cached '
        f'"$BASELINE_COMMIT" {diff_pathspec_args}\n'
        "  exit 0\n"
        "fi\n"
        'host_git_objects="$baseline_repo/.git/objects"\n'
        'if [ ! -d "$host_git_objects" ]; then\n'
        '  echo "host baseline Git object directory is missing: $host_git_objects" >&2\n'
        "  exit 1\n"
        "fi\n"
        'git init --bare -q "$tmp_root/git"\n'
        'export GIT_DIR="$tmp_root/git"\n'
        'export GIT_WORK_TREE="$repo"\n'
        'export GIT_INDEX_FILE="$tmp_index"\n'
        'export GIT_ALTERNATE_OBJECT_DIRECTORIES="$host_git_objects"\n'
        'git cat-file -e "${BASELINE_COMMIT}^{commit}"\n'
        'git read-tree "$BASELINE_COMMIT"\n'
        "git add -A\n"
        'git diff --binary --cached '
        f'"$BASELINE_COMMIT" {diff_pathspec_args}\n'
    )


def _extract_breaker_repo_patch(*, workspace, baseline_commit_sha: str) -> str:
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        raise RuntimeError("breaker workspace container_id is unavailable")
    script = _breaker_repo_patch_script()
    completed = subprocess.run(
        [
            "docker",
            "exec",
            "-e",
            f"BASELINE_COMMIT={baseline_commit_sha}",
            container_id,
            "sh",
            "-lc",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip() or "docker exec git diff failed"
        raise RuntimeError(f"failed to extract breaker repo patch: {error}")
    return str(completed.stdout or "")


def _stage3_run_baseline_commit_sha(*, run_id: str) -> str:
    return _with_stage3_service(
        lambda service: _baseline_commit_sha_from_run_payload(
            service.get_run(run_id, include_detail=False).runtime_snapshot_json or {}
        ),
        runtime_run_id=run_id,
    )


def _baseline_commit_sha_from_run_payload(payload: dict[str, Any]) -> str:
    stage3_payload = dict((payload or {}).get("stage3") or {})
    return str(
        stage3_payload.get("baseline_commit_sha")
        or stage3_payload.get("source_commit_sha")
        or ""
    ).strip()


def _apply_repo_patch_to_host_workspace(
    *,
    host_repo_dir: Path,
    baseline_commit_sha: str,
    patch_text: str,
) -> None:
    host_repo_dir = host_repo_dir.expanduser().resolve()
    host_repo_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_host_git(
        host_repo_dir,
        ["reset", "--hard", baseline_commit_sha],
        error_message="failed to reset host evaluation repo to baseline commit",
    )
    _run_host_git(
        host_repo_dir,
        ["clean", "-fd"],
        error_message="failed to clean host evaluation repo before applying breaker patch",
    )
    if not patch_text.strip():
        return
    patch_path = host_repo_dir.parent / f".stage3-breaker-{uuid.uuid4().hex}.patch"
    try:
        patch_path.write_text(patch_text, encoding="utf-8")
        completed = subprocess.run(
            ["git", "-C", str(host_repo_dir), "apply", "--binary", str(patch_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
    finally:
        patch_path.unlink(missing_ok=True)
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip() or "git apply failed"
        raise RuntimeError(f"failed to apply breaker repo patch on host: {error}")


def _run_host_git(repo_dir: Path, args: list[str], *, error_message: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    if completed.returncode != 0:
        error = str(completed.stderr or completed.stdout or "").strip() or error_message
        raise RuntimeError(f"{error_message}: {error}")


def _prepare_breaker_resume_conversation_state(
    *,
    checkpoint: dict[str, Any],
    runtime_env: dict[str, str],
    events_path: Path,
    stage3_root: Path,
) -> uuid.UUID | None:
    if not checkpoint:
        return None
    conversation_id_raw = str(checkpoint.get("conversation_id") or "").strip()
    if not conversation_id_raw:
        raise RuntimeError("resume checkpoint is missing conversation_id")
    try:
        conversation_id = uuid.UUID(conversation_id_raw)
    except ValueError as exc:
        raise RuntimeError(f"resume checkpoint has invalid conversation_id: {conversation_id_raw}") from exc

    source_conversations_path_raw = str(((checkpoint.get("openhands") or {}).get("conversations_path")) or "").strip()
    if not source_conversations_path_raw:
        raise RuntimeError("resume checkpoint is missing OpenHands conversations_path")
    source_conversations_path = Path(source_conversations_path_raw).expanduser()
    if not source_conversations_path.exists():
        raise RuntimeError(
            f"resume checkpoint OpenHands conversation path is missing: {source_conversations_path}"
        )
    target_conversations_path_raw = str(runtime_env.get("OH_CONVERSATIONS_PATH") or "").strip()
    if not target_conversations_path_raw:
        raise RuntimeError("resume runtime is missing OH_CONVERSATIONS_PATH")
    target_conversations_path = Path(target_conversations_path_raw).expanduser().resolve()
    target_conversations_path.mkdir(parents=True, exist_ok=True)
    source_conversation_dir = source_conversations_path / conversation_id.hex
    if not source_conversation_dir.exists():
        raise RuntimeError(f"resume checkpoint conversation directory is missing: {source_conversation_dir}")
    target_conversation_dir = target_conversations_path / conversation_id.hex
    if target_conversation_dir.exists():
        shutil.rmtree(target_conversation_dir)
    shutil.copytree(source_conversation_dir, target_conversation_dir)
    _make_container_writable(target_conversation_dir, recursive=True)
    rewrite_stage3_json_tree_path_references(
        target_conversation_dir,
        stage3_root=stage3_root,
        destination_run_id=target_conversations_path.parents[2].name,
    )
    source_bash_events_path_raw = str(((checkpoint.get("openhands") or {}).get("bash_events_dir")) or "").strip()
    source_bash_events_path = Path(source_bash_events_path_raw).expanduser() if source_bash_events_path_raw else None
    target_bash_events_path_raw = str(runtime_env.get("OH_BASH_EVENTS_DIR") or "").strip()
    target_bash_events_path = Path(target_bash_events_path_raw).expanduser().resolve() if target_bash_events_path_raw else None
    bash_events_restored = False
    if (
        source_bash_events_path is not None
        and target_bash_events_path is not None
        and source_bash_events_path.exists()
    ):
        target_bash_events_path.parent.mkdir(parents=True, exist_ok=True)
        if target_bash_events_path.exists():
            shutil.rmtree(target_bash_events_path)
        shutil.copytree(source_bash_events_path, target_bash_events_path)
        _make_container_writable(target_bash_events_path, recursive=True)
        rewrite_stage3_json_tree_path_references(
            target_bash_events_path,
            stage3_root=stage3_root,
            destination_run_id=target_bash_events_path.parents[2].name,
        )
        bash_events_restored = True
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "breaker_agent",
            "title": "Breaker resume state prepared",
            "message": f"Copied OpenHands conversation state for resume depth {int(checkpoint.get('depth') or 0)}",
            "payload": {
                "conversation_id": str(conversation_id),
                "source_conversation_dir": str(source_conversation_dir),
                "target_conversation_dir": str(target_conversation_dir),
                "bash_events_restored": bash_events_restored,
                "depth": int(checkpoint.get("depth") or 0),
            },
        },
    )
    return conversation_id


def _event_payload(*, event, conversation_ref: dict[str, Any], archived_completion_file: Path | None = None) -> dict[str, Any]:
    actor = "breaker_agent"
    phase = "breaker_agent"
    if isinstance(event, LLMCompletionLogEvent):
        return _llm_completion_event_payload(
            event=event,
            actor=actor,
            phase=phase,
            conversation_ref=conversation_ref,
            archived_completion_file=archived_completion_file,
        )
    payload = _compact_openhands_event_payload(event)
    token_usage = (
        _token_usage_from_event_payload(payload)
        or _conversation_metrics_payload(conversation_ref.get("conversation"))
        or _tracked_token_usage(conversation_ref)
    )
    if _has_token_usage(token_usage):
        conversation_ref["token_usage"] = dict(token_usage)
        payload["token_usage"] = token_usage
        payload["token_usage_total"] = int(token_usage.get("total_tokens") or 0)
    title, message = _event_summary(event)
    return {
        "actor": actor,
        "phase": phase,
        "title": title,
        "message": message,
        "payload": payload,
    }


def _detect_platform() -> str:
    machine = os.uname().machine.lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


@lru_cache(maxsize=1)
def _stage3_service_session_factory():
    engine = build_engine(get_settings())
    return build_session_factory(engine)


def _with_stage3_service(callback, *, runtime_run_id: str | None = None):
    session_factory = _stage3_service_session_factory()
    session = session_factory()
    try:
        settings = get_settings()
        if runtime_run_id:
            base_service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            run = base_service.get_run(runtime_run_id, include_detail=False)
            runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
            runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
            settings = stage3_settings_with_runtime_snapshot(settings.model_copy(deep=True), runtime_snapshot)
        service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
        result = callback(service)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _append_stage3_host_setup_timeout_event(
    *,
    events_path: Path,
    title: str,
    message: str,
    request_id: str,
    step: str,
    status: str,
    duration_seconds: float | None = None,
) -> None:
    payload: dict[str, Any] = {
        "operation": STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
        "status": status,
        "request_id": request_id,
        "step": step,
    }
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "breaker_agent",
            "title": title,
            "message": message,
            "payload": payload,
        },
    )


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid json payload: {path}")
    return payload


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"


if __name__ == "__main__":
    raise SystemExit(main())