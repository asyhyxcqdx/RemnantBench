from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Callable

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import LLMCompletionLogEvent
from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_condenser, get_default_tools
from openhands.tools.preset.gpt5 import get_gpt5_condenser, get_gpt5_tools

from feature_factory.config import get_settings
from feature_factory.diff_stats import filter_noise_files_from_patch
from feature_factory.openhands_llm import (
    build_openhands_llm_config,
    openhands_llm_session_id,
    release_openhands_llm_resources,
)
from feature_factory.stage2.openhands_bridge import (
    WORKER_SANDBOX_WORKSPACE,
    _agent_server_container_labels,
    _append_event,
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
    _token_usage_from_event_payload,
    _token_usage_payload,
    _tracked_token_usage,
)
from feature_factory.stage2.raw_archive import (
    llm_completion_archive_dir,
    summarize_llm_completion_archive,
)
from feature_factory.stage2.worker_validation_queue import process_pending_validation_requests
from feature_factory.stage3.runtime_images import (
    detect_stage3_platform,
    ensure_stage3_breaker_runtime_image_built,
)
from feature_factory.stage4.openhands_receive_tool import (
    RECEIVE_REQUEST_DIR_ENV,
    RECEIVE_RESULT_DIR_ENV,
    ReceiveTool,
)
from feature_factory.stage4.issue_styles import (
    runtime_generation_spec,
    runtime_issuer_prompt,
)
from feature_factory.stage4.service import _leakage_check, _private_leakage_terms_for_source_context
from feature_factory.stage4.style_outputs import (
    style_fields_schema,
    validate_and_render_style_output,
)

FEATURE_FACTORY_STAGE4_HOST_WORKSPACE_MOUNT = "/feature_factory_workspace"
FEATURE_FACTORY_STAGE4_CONTAINER_REPO_DIR = f"{WORKER_SANDBOX_WORKSPACE}/repo"
FEATURE_FACTORY_STAGE4_CONTAINER_RUN_SCRIPT_PATH = f"{WORKER_SANDBOX_WORKSPACE}/run_script.sh"
FEATURE_FACTORY_STAGE4_TOOL_ROOT = ".feature_factory_openhands_tools"
FEATURE_FACTORY_STAGE4_RUNTIME_ROOT = ".stage4_runtime"
FEATURE_FACTORY_STAGE4_RECEIVE_DIR = "receive_tool"
FEATURE_FACTORY_STAGE4_RECEIVE_REQUESTS_DIRNAME = "requests"
FEATURE_FACTORY_STAGE4_RECEIVE_RESULTS_DIRNAME = "results"
FEATURE_FACTORY_STAGE4_STATUS_POLL_INTERVAL_SECONDS = 0.2
STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION = "openhands_bridge_issuer"
STAGE4_OPENHANDS_LLM_RETRY_OPERATION = "openhands_llm_retry_issuer"
STAGE4_REFERENCE_PATCH_MAX_CHARS = 50_000
STAGE4_REFERENCE_PATCH_TRUNCATION_MARKER = (
    "\n[TRUNCATION: middle of reference patch omitted]\n"
)


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
                "phase": "issuer_agent",
                "title": "OpenHands bridge failed",
                "message": _truncate_text(str(exc), 1000),
                "payload": {
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                    "error_type": type(exc).__name__,
                },
            },
        )
        raise


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="FeatureFactory Stage4 OpenHands bridge")
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--events", required=True)
    return parser.parse_args()


def _run_request(*, request: dict[str, Any], events_path: Path) -> dict[str, Any]:
    mode = str(request.get("mode") or "")
    if mode != "issuer":
        raise RuntimeError(f"unsupported stage4 bridge mode: {mode}")

    generation_style_id = str(request.get("generation_style_id") or "").strip()
    source_context = dict(request.get("stage4") or {})
    generation_spec = runtime_generation_spec(
        dict(source_context.get("stage4") or {}),
        generation_style_id,
    )
    event_phase = f"{generation_style_id}_agent"
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": event_phase,
            "title": "OpenHands bridge initialized",
            "message": f"Bridge process is preparing the independent {generation_style_id} agent runtime",
            "payload": {
                "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                "mode": mode,
                "generation_style_id": generation_style_id,
                "workspace_dir": str(request.get("workspace_dir") or ""),
            },
        },
    )
    llm = _build_llm(events_path=events_path, role=generation_style_id)
    try:
        completion_archive_dir = Path(str(llm.log_completions_folder))
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": event_phase,
                "title": "OpenHands LLM configured",
                "message": f"LLM configured for {generation_style_id} agent: {llm.model}",
                "payload": {
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                    "model": llm.model,
                    "base_url": str(getattr(llm, "base_url", "") or ""),
                    "llm_completion_archive_dir": str(completion_archive_dir),
                    "llm_session_id": openhands_llm_session_id(llm) or "",
                },
            },
        )
        agent = _build_agent(
            llm=llm,
            preset=str(request.get("preset") or "gpt5"),
            fields_schema=style_fields_schema(generation_spec),
        )
        max_iterations = int(request.get("max_iterations") or 80)
        workspace_dir = Path(str(request.get("workspace_dir") or "")).resolve()
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": event_phase,
                "title": "OpenHands agent constructed",
                "message": (
                    f"{generation_style_id} preset {request.get('preset') or 'gpt5'} is ready "
                    f"with {max_iterations} max iterations"
                ),
                "payload": {
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                    "preset": str(request.get("preset") or "gpt5"),
                    "max_iterations": max_iterations,
                },
            },
        )
        return _run_issuer(
            request=request,
            workspace_dir=workspace_dir,
            agent=agent,
            llm=llm,
            max_iterations=max_iterations,
            events_path=events_path,
        )
    finally:
        _release_stage4_llm_session(llm=llm, events_path=events_path)


def _run_issuer(
    *,
    request: dict[str, Any],
    workspace_dir: Path,
    agent,
    llm: LLM,
    max_iterations: int,
    events_path: Path,
) -> dict[str, Any]:
    generation_style_id = str(request.get("generation_style_id") or "").strip()
    with _issuer_support_files(
        workspace_dir=workspace_dir,
        runtime_dir=events_path.parent,
        role=generation_style_id,
    ) as support:
        return _run_issuer_with_support(
            request=request,
            workspace_dir=workspace_dir,
            agent=agent,
            llm=llm,
            max_iterations=max_iterations,
            events_path=events_path,
            support=support,
        )


def _run_issuer_with_support(
    *,
    request: dict[str, Any],
    workspace_dir: Path,
    agent,
    llm: LLM,
    max_iterations: int,
    events_path: Path,
    support: dict[str, Any],
) -> dict[str, Any]:
    source_context = dict(request.get("stage4") or {})
    generation_style_id = str(request.get("generation_style_id") or "").strip()
    generation_spec = runtime_generation_spec(
        dict(source_context.get("stage4") or {}),
        generation_style_id,
    )
    snapshot = dict(source_context.get("snapshot") or {})
    receive_service = _Stage4ReceiveService(
        source_context=source_context,
        generation_style_id=generation_style_id,
        events_path=events_path,
        request_dir=Path(str(support["receive_request_dir_host"])),
        result_dir=Path(str(support["receive_result_dir_host"])),
    )
    settings = get_settings().model_copy(deep=True)
    platform_name = detect_stage3_platform()
    runtime_image_ref = ""
    runtime_image_started_at = time.monotonic()
    runtime_image_log_callback, flush_runtime_image_logs = _stage4_progress_log_callback(
        events_path=events_path,
        title="Stage4 runtime image build output",
        operation="stage4_runtime_image",
    )
    try:
        with _bridge_interrupt_cancel_scope() as runtime_image_cancel_requested:
            runtime_image_ref = ensure_stage3_breaker_runtime_image_built(
                workspace_dir=workspace_dir,
                snapshot_id=str(snapshot.get("id") or ""),
                source_stage2_run_id=str(snapshot.get("source_stage2_run_id") or ""),
                source_commit_sha=str(snapshot.get("source_commit_sha") or ""),
                base_image_id=str(snapshot.get("base_image") or ""),
                platform_name=platform_name,
                timeout_seconds=float(settings.stage4_build_timeout_seconds),
                log_callback=runtime_image_log_callback,
                cancel_requested=runtime_image_cancel_requested,
                emit_event=lambda title, message, payload: _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "issuer_agent",
                        "title": title.replace("Stage3", "Stage4"),
                        "message": message.replace("Stage3", "Stage4"),
                        "payload": {
                            "operation": "stage4_runtime_image",
                            **dict(payload or {}),
                        },
                    },
                ),
            )
    finally:
        flush_runtime_image_logs()
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "issuer_agent",
            "title": "Stage4 runtime image preparation completed",
            "message": "Stage4 runtime image preparation finished",
            "payload": {
                "operation": "stage4_runtime_image",
                "image_ref": runtime_image_ref,
                "duration_seconds": max(0.0, time.monotonic() - runtime_image_started_at),
            },
        },
    )

    prompt = _build_issuer_prompt(
        source_context=source_context,
        generation_style_id=generation_style_id,
    )
    conversation_ref: dict[str, Any] = {
        "llm_completion_archive_dir": str(llm.log_completions_folder),
        "generation_style_id": generation_style_id,
    }

    def callback(event) -> None:
        archived_file = _archive_llm_completion_event(
            event,
            archive_dir=Path(str(llm.log_completions_folder)),
        )
        _append_event(
            events_path,
            _event_payload(
                event=event,
                conversation_ref=conversation_ref,
                archived_completion_file=archived_file,
            ),
        )

    openhands_runtime_env = _openhands_server_runtime_env(
        runtime_dir=events_path.parent,
        role=generation_style_id,
    )
    agent_server_started_at = time.monotonic()
    agent_server_log_callback, flush_agent_server_logs = _stage4_progress_log_callback(
        events_path=events_path,
        title=f"{generation_spec.display_name} agent-server image build output",
        operation="stage4_agent_server_image",
    )
    try:
        with ExitStack() as stack:
            with _bridge_interrupt_cancel_scope() as agent_server_cancel_requested:
                workspace = stack.enter_context(
                    _docker_workspace(
                        Path(str(support["tool_workspace_host"])),
                        base_image=runtime_image_ref or "feature-factory/stage4-runtime:missing",
                        mount_target=FEATURE_FACTORY_STAGE4_HOST_WORKSPACE_MOUNT,
                        working_dir=FEATURE_FACTORY_STAGE4_CONTAINER_REPO_DIR,
                        runtime_dir=events_path.parent,
                        openhands_role=generation_style_id,
                        agent_server_build_timeout_seconds=float(settings.stage4_build_timeout_seconds),
                        extra_volumes=_issuer_extra_volumes(support=support),
                        labels=_agent_server_container_labels(
                            request=request,
                            runtime_dir=events_path.parent,
                            role=generation_style_id,
                            stage="stage4",
                        ),
                        agent_server_log_callback=agent_server_log_callback,
                        agent_server_cancel_requested=agent_server_cancel_requested,
                        forward_env_names=[
                            "OH_CONVERSATIONS_PATH",
                            "OH_BASH_EVENTS_DIR",
                            "PYTHONPATH",
                            RECEIVE_REQUEST_DIR_ENV,
                            RECEIVE_RESULT_DIR_ENV,
                        ],
                        forward_env_updates={
                            **openhands_runtime_env,
                            "PYTHONPATH": str(support["tool_python_root_container"]),
                            RECEIVE_REQUEST_DIR_ENV: str(support["receive_request_dir_container"]),
                            RECEIVE_RESULT_DIR_ENV: str(support["receive_result_dir_container"]),
                        },
                    )
                )
            _append_event(
                events_path,
                {
                    "actor": "system",
                    "phase": f"{generation_style_id}_agent",
                    "title": f"{generation_spec.display_name} agent-server image preparation completed",
                    "message": "OpenHands agent-server image preparation finished",
                    "payload": {
                        "operation": "stage4_agent_server_image",
                        "duration_seconds": max(0.0, time.monotonic() - agent_server_started_at),
                    },
                },
            )
            conversation = Conversation(
                agent=agent,
                workspace=workspace,
                callbacks=[callback],
                max_iteration_per_run=max_iterations,
                visualizer=None,
            )
            conversation_ref["conversation"] = conversation
            conversation_ref["workspace"] = workspace
            _append_event(
                events_path,
                {
                    "actor": f"{generation_style_id}_agent",
                    "phase": f"{generation_style_id}_agent",
                    "title": f"{generation_spec.display_name} conversation started",
                    "message": (
                        f"OpenHands {generation_style_id} writer is drafting its configured issue for "
                        f"{request.get('repository', {}).get('full_name') or 'repository'}"
                    ),
                    "payload": {
                        "workspace_dir": str(workspace_dir),
                        "repo_path": str(request.get("repo_path") or ""),
                        "runtime_base_image_ref": runtime_image_ref,
                        "generation_style_id": generation_style_id,
                    },
                },
            )
            try:
                conversation.send_message(prompt)
                conversation.run(blocking=False)
                _run_issuer_conversation_until_terminal(
                    conversation=conversation,
                    events_path=events_path,
                    conversation_ref=conversation_ref,
                    receive_service=receive_service,
                )
            except Exception as exc:
                context = _append_issuer_conversation_failure_event(
                    events_path=events_path,
                    conversation_ref=conversation_ref,
                    exc=exc,
                )
                raise RuntimeError(
                    _conversation_failure_message(
                        "OpenHands issuer conversation failed",
                        context,
                        exc,
                    )
                ) from exc
            finally:
                _append_issuer_llm_completion_archive_event(
                    events_path,
                    archive_dir=Path(str(llm.log_completions_folder)),
                    role=generation_style_id,
                )
    finally:
        flush_agent_server_logs()

    bundle = receive_service.completed_bundle()
    if not bundle:
        raise RuntimeError(f"{generation_style_id} conversation ended without a completed receive-tool bundle")
    return {
        "status": "completed",
        "mode": "issuer",
        "model": llm.model,
        "token_usage": _token_usage_payload(conversation),
        "conversation_id": str(conversation.state.id),
        "execution_status": str(conversation.state.execution_status),
        "generation_style_id": generation_style_id,
        "llm_completion_archive": summarize_llm_completion_archive(
            events_path.parent,
            generation_style_id,
        ),
        "result": {
            "summary": str(
                bundle.get("source_summary")
                or "OpenHands task author generated the configured Stage4 task input."
            ),
            "bundle": bundle,
        },
    }


def _build_llm(*, events_path: Path, role: str) -> LLM:
    usage_id = f"stage4-{role}"
    llm_config = build_openhands_llm_config(usage_id=usage_id)
    completions_dir = llm_completion_archive_dir(events_path.parent, role).resolve()
    completions_dir.mkdir(parents=True, exist_ok=True)
    return LLM(
        usage_id=usage_id,
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        enable_encrypted_reasoning=_openhands_enable_encrypted_reasoning(),
        log_completions=True,
        log_completions_folder=str(completions_dir),
        retry_listener=_build_stage4_llm_retry_listener(
            model=llm_config.model,
            events_path=events_path,
        ),
        **llm_config.optional_kwargs,
    )


def _release_stage4_llm_session(*, llm: LLM, events_path: Path) -> None:
    session_id = openhands_llm_session_id(llm)
    if not session_id:
        return

    def on_error(error: str) -> None:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "issuer_agent",
                "title": "OpenHands LLM session release failed",
                "message": (
                    f"Could not release LLM session {session_id}: "
                    f"{_truncate_text(error, 600)}"
                ),
                "payload": {
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                    "llm_session_id": session_id,
                    "error": _truncate_text(error, 1000),
                },
            },
        )

    release_openhands_llm_resources(llm=llm, on_error=on_error)


def _build_agent(*, llm: LLM, preset: str, fields_schema: dict[str, Any]):
    import feature_factory.stage4.openhands_receive_tool  # noqa: F401

    extra_tools = [
        Tool(
            name=ReceiveTool.name,
            params={"fields_schema": fields_schema},
        )
    ]
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


def _build_issuer_prompt(
    *,
    source_context: dict[str, Any],
    generation_style_id: str,
) -> str:
    stage4 = dict((source_context or {}).get("stage4") or {})
    spec = runtime_generation_spec(stage4, generation_style_id)
    context_payload = _issuer_prompt_context(
        source_context=source_context,
        generation_style_id=generation_style_id,
    )
    one_shot_section = ""
    if spec.one_shots:
        examples = "\n\n".join(asset.text.strip() for asset in spec.one_shots)
        one_shot_section = f"Example task input in the requested structure and level of detail:\n\n{examples}"
    prompt_template = runtime_issuer_prompt(stage4).text
    return (
        prompt_template.replace("{{RECEIVE_TOOL_NAME}}", ReceiveTool.name)
        .replace("{{OUTPUT_INSTRUCTIONS}}", spec.prompt.text.strip())
        .replace("{{ONE_SHOT_SECTION}}", one_shot_section)
        .replace("{{PRIVATE_CONTEXT_JSON}}", json.dumps(context_payload, ensure_ascii=False, indent=2))
    )


def _issuer_prompt_context(
    *,
    source_context: dict[str, Any],
    generation_style_id: str,
) -> dict[str, Any]:
    repository = dict((source_context or {}).get("repository") or {})
    entry_file = dict((source_context or {}).get("entry_file") or {})
    savepoint = dict((source_context or {}).get("savepoint") or {})
    summary = dict(savepoint.get("summary") or {})
    stage4 = dict((source_context or {}).get("stage4") or {})
    private = dict((source_context or {}).get("private") or {})
    runtime_generation_spec(stage4, generation_style_id)

    target_path = str(entry_file.get("test_file_path") or "")
    target_selector = str(entry_file.get("target_selector") or target_path)
    file_results = [
        dict(item)
        for item in list(savepoint.get("file_results") or [])
        if isinstance(item, dict)
    ]
    entry_result = next(
        (
            item
            for item in file_results
            if bool(item.get("is_entry_file"))
            or (target_path and str(item.get("test_file_path") or "") == target_path)
        ),
        {},
    )
    return {
        "repository": _drop_empty_values(
            {
                "name": repository.get("full_name"),
                "language": repository.get("primary_language"),
            }
        ),
        "broken_behavior": _drop_empty_values(
            {
                "summary": summary.get("milestone_summary"),
                "rationale": summary.get("rationale"),
                "entry_result": _drop_empty_values(
                    {
                        "status": entry_result.get("status"),
                        "total_tests": entry_result.get("total_tests"),
                        "passed_tests": entry_result.get("passed_tests"),
                        "failed_tests": entry_result.get("failed_tests"),
                        "error_tests": entry_result.get("error_tests"),
                        "skipped_tests": entry_result.get("skipped_tests"),
                        "pass_rate": entry_result.get("pass_rate") or savepoint.get("entry_pass_rate"),
                    }
                ),
                "diff_stats": _drop_empty_values(dict(savepoint.get("diff_stats") or {})),
            }
        ),
        "private": _drop_empty_values(
            {
                "target_test_file": target_path,
                "target_selector": target_selector,
                "baseline_total_tests": entry_file.get("baseline_total_tests"),
                "baseline_pass_rate": entry_file.get("baseline_pass_rate"),
                "reference_patch": _reference_patch_for_prompt(
                    str(private.get("gold_patch_text") or "")
                ),
            }
        ),
    }


def _reference_patch_for_prompt(patch_text: str) -> str:
    filtered_patch = filter_noise_files_from_patch(patch_text)
    if len(filtered_patch) <= STAGE4_REFERENCE_PATCH_MAX_CHARS:
        return filtered_patch

    content_budget = (
        STAGE4_REFERENCE_PATCH_MAX_CHARS
        - len(STAGE4_REFERENCE_PATCH_TRUNCATION_MARKER)
    )
    head_chars = (content_budget + 1) // 2
    tail_chars = content_budget - head_chars
    return (
        filtered_patch[:head_chars]
        + STAGE4_REFERENCE_PATCH_TRUNCATION_MARKER
        + filtered_patch[-tail_chars:]
    )


def _drop_empty_values(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if value is not None and value != "" and value != {} and value != []
    }


def _prepare_issuer_support_files(
    *,
    workspace_dir: Path,
    runtime_dir: Path,
    role: str,
) -> dict[str, Any]:
    workspace_dir = workspace_dir.resolve()
    runtime_dir = runtime_dir.resolve()
    source_repo_dir = workspace_dir / "repo"
    if not source_repo_dir.is_dir():
        raise RuntimeError(f"stage4 repository is missing: {source_repo_dir}")
    run_script_host = workspace_dir / "assets" / "run_script.sh"
    if not run_script_host.is_file():
        raise RuntimeError(f"stage4 run_script.sh asset is missing: {run_script_host}")
    run_script_host.chmod(0o755)
    tool_workspace_host = runtime_dir / f"{role}_tool_workspace"
    if tool_workspace_host.exists():
        shutil.rmtree(tool_workspace_host)
    tool_workspace_host.mkdir(parents=True, exist_ok=True)
    isolated_repo_dir = tool_workspace_host / "repo"
    _copy_issuer_repo_tree(source_repo_dir, isolated_repo_dir)
    tool_python_root_host = tool_workspace_host / FEATURE_FACTORY_STAGE4_TOOL_ROOT
    tool_module_dir = tool_python_root_host / "feature_factory" / "stage4"
    tool_module_dir.mkdir(parents=True, exist_ok=True)
    for package_init in (
        tool_python_root_host / "feature_factory" / "__init__.py",
        tool_module_dir / "__init__.py",
    ):
        package_init.parent.mkdir(parents=True, exist_ok=True)
        package_init.write_text("", encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parent / "openhands_receive_tool.py",
        tool_module_dir / "openhands_receive_tool.py",
    )
    stage4_runtime_root = tool_workspace_host / FEATURE_FACTORY_STAGE4_RUNTIME_ROOT
    stage4_runtime_root.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(stage4_runtime_root)
    receive_root = stage4_runtime_root / FEATURE_FACTORY_STAGE4_RECEIVE_DIR
    receive_root.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(receive_root)
    request_dir_host = receive_root / FEATURE_FACTORY_STAGE4_RECEIVE_REQUESTS_DIRNAME
    result_dir_host = receive_root / FEATURE_FACTORY_STAGE4_RECEIVE_RESULTS_DIRNAME
    for directory in (request_dir_host, result_dir_host):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        _make_container_writable(directory)
    _make_container_writable(tool_python_root_host, recursive=True)
    return {
        "tool_workspace_host": tool_workspace_host,
        "tool_python_root_host": tool_python_root_host,
        "tool_python_root_container": _tool_workspace_mount_path_for_host_path(
            tool_python_root_host,
            tool_workspace_host=tool_workspace_host,
        ),
        "receive_request_dir_host": request_dir_host,
        "receive_request_dir_container": _tool_workspace_mount_path_for_host_path(
            request_dir_host,
            tool_workspace_host=tool_workspace_host,
        ),
        "receive_result_dir_host": result_dir_host,
        "receive_result_dir_container": _tool_workspace_mount_path_for_host_path(
            result_dir_host,
            tool_workspace_host=tool_workspace_host,
        ),
        "repo_host": isolated_repo_dir,
        "repo_container": FEATURE_FACTORY_STAGE4_CONTAINER_REPO_DIR,
        "run_script_host": run_script_host,
        "run_script_container": FEATURE_FACTORY_STAGE4_CONTAINER_RUN_SCRIPT_PATH,
    }


@contextmanager
def _issuer_support_files(
    *,
    workspace_dir: Path,
    runtime_dir: Path,
    role: str,
):
    isolated_repo_dir = runtime_dir.resolve() / f"{role}_tool_workspace" / "repo"
    try:
        support = _prepare_issuer_support_files(
            workspace_dir=workspace_dir,
            runtime_dir=runtime_dir,
            role=role,
        )
        yield support
    finally:
        shutil.rmtree(isolated_repo_dir, ignore_errors=True)


def _copy_issuer_repo_tree(source: Path, destination: Path) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir():
        raise RuntimeError(f"stage4 repository is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        shutil.rmtree(destination)
    copied = False
    if shutil.which("cp") is not None:
        command = (
            ["cp", "-cR", str(source), str(destination)]
            if sys.platform == "darwin"
            else ["cp", "-a", "--reflink=auto", str(source), str(destination)]
        )
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            completed = None
        copied = bool(completed is not None and completed.returncode == 0 and destination.is_dir())
        if not copied:
            shutil.rmtree(destination, ignore_errors=True)
    if not copied:
        shutil.copytree(source, destination, symlinks=True)
    _make_container_writable(destination, recursive=True)


def _issuer_extra_volumes(*, support: dict[str, Any]) -> list[str]:
    return [
        f"{Path(str(support['repo_host'])).resolve()}:{support['repo_container']}",
        f"{Path(str(support['run_script_host'])).resolve()}:{support['run_script_container']}:ro",
    ]


def _tool_workspace_mount_path_for_host_path(path: Path, *, tool_workspace_host: Path) -> str:
    relative = path.resolve().relative_to(tool_workspace_host.resolve())
    return str(Path(FEATURE_FACTORY_STAGE4_HOST_WORKSPACE_MOUNT) / relative)


def _run_issuer_conversation_until_terminal(
    *,
    conversation,
    events_path: Path,
    conversation_ref: dict[str, Any],
    receive_service: "_Stage4ReceiveService",
) -> None:
    paused_since = 0.0
    while True:
        receive_service.process_pending_requests()
        status = _conversation_execution_status(conversation, refresh=True)
        completed_bundle = receive_service.completed_bundle()
        terminal_failure_message = receive_service.terminal_failure_message()
        if completed_bundle is not None:
            if status == ConversationExecutionStatus.PAUSED:
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "issuer_agent",
                        "title": "Receive tool completion confirmed",
                        "message": "OpenHands issuer paused after the host accepted a complete receive-tool submission",
                        "payload": {
                            "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                            "token_usage": _tracked_token_usage(conversation_ref),
                        },
                    },
                )
                return
            if status in (
                ConversationExecutionStatus.FINISHED,
                ConversationExecutionStatus.ERROR,
                ConversationExecutionStatus.STUCK,
            ):
                raise RuntimeError(
                    "issuer conversation reached a terminal state before the receive-tool completion pause"
                )
        elif terminal_failure_message:
            if status == ConversationExecutionStatus.PAUSED:
                raise RuntimeError(terminal_failure_message)
            if status in (
                ConversationExecutionStatus.FINISHED,
                ConversationExecutionStatus.ERROR,
                ConversationExecutionStatus.STUCK,
            ):
                raise RuntimeError(terminal_failure_message)
        if status == ConversationExecutionStatus.FINISHED:
            raise RuntimeError("issuer conversation finished without a completed receive-tool submission")
        if status == ConversationExecutionStatus.ERROR:
            raise RuntimeError("issuer conversation ended with error")
        if status == ConversationExecutionStatus.STUCK:
            raise RuntimeError("issuer conversation got stuck")
        if status == ConversationExecutionStatus.PAUSED:
            if paused_since <= 0.0:
                paused_since = time.monotonic()
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "issuer_agent",
                        "title": "Issuer conversation paused",
                        "message": "OpenHands issuer paused before finishing",
                        "payload": {
                            "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                            "token_usage": _tracked_token_usage(conversation_ref),
                        },
                    },
                )
            elif time.monotonic() - paused_since > 60.0:
                raise RuntimeError("issuer conversation remained paused for 60s without a terminal receive-tool state")
        else:
            paused_since = 0.0
        time.sleep(FEATURE_FACTORY_STAGE4_STATUS_POLL_INTERVAL_SECONDS)


class _Stage4ReceiveService:
    def __init__(
        self,
        *,
        source_context: dict[str, Any],
        generation_style_id: str,
        events_path: Path,
        request_dir: Path,
        result_dir: Path,
    ) -> None:
        self.source_context = dict(source_context or {})
        self.events_path = events_path
        self.request_dir = request_dir.resolve()
        self.result_dir = result_dir.resolve()
        self.generation_style_id = str(generation_style_id or "").strip()
        stage4 = dict(self.source_context.get("stage4") or {})
        self.generation_spec = runtime_generation_spec(stage4, self.generation_style_id)
        self.gold_patch_text = str((self.source_context.get("private") or {}).get("gold_patch_text") or "")
        self.private_terms = _private_leakage_terms_for_source_context(self.source_context)
        self._processed_request_ids: set[str] = set()
        self._call_count = 0
        self._completed_bundle: dict[str, Any] | None = None
        self._terminal_failure_message: str | None = None

    def process_pending_requests(self) -> bool:
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.result_dir.mkdir(parents=True, exist_ok=True)
        _make_container_writable(self.request_dir)
        _make_container_writable(self.result_dir)
        return process_pending_validation_requests(
            request_dir=self.request_dir,
            processed_request_ids=self._processed_request_ids,
            handle_request=self._handle_request,
            handle_error=self._handle_request_failure,
            handle_error_failure=self._handle_request_failure_error,
        )

    def completed_bundle(self) -> dict[str, Any] | None:
        if self._completed_bundle is None:
            return None
        return dict(self._completed_bundle)

    def terminal_failure_message(self) -> str | None:
        return str(self._terminal_failure_message or "").strip() or None

    def _handle_request(self, *, request_id: str, request_path: Path) -> None:
        payload = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise RuntimeError("receive tool request payload must be an object")
        call_index = self._call_count + 1
        self._call_count = call_index
        title = str(payload.get("title") or "").strip()
        raw_fields = payload.get("fields")
        fields = dict(raw_fields) if isinstance(raw_fields, dict) else {}

        self._emit_event(
            title="Receive tool call received",
            message=f"Received a candidate {self.generation_style_id} task input",
            payload={
                "request_id": request_id,
                "receive_call_index": call_index,
            },
        )

        errors: list[dict[str, str]] = []
        missing: dict[str, int] = {}
        if not title:
            errors.append({"code": "MISSING_TITLE", "message": "Task input title must be a non-empty string."})
            missing["title"] = 1
        if not isinstance(raw_fields, dict):
            errors.append(
                {
                    "code": "MISSING_FIELDS" if raw_fields is None else "INVALID_FIELDS",
                    "message": "Task input fields must be an object matching the configured style schema.",
                }
            )
            missing["fields"] = 1
        rendered_content = ""
        style_validation: dict[str, Any] = {}
        if not errors:
            style_result = validate_and_render_style_output(
                spec=self.generation_spec,
                title=title,
                fields=fields,
                repository_name=str((self.source_context.get("repository") or {}).get("full_name") or "repository"),
                validation_context=self.source_context,
            )
            errors.extend(style_result.errors)
            missing.update(style_result.missing)
            rendered_content = style_result.rendered_markdown
            style_validation = style_result.validation
        leakage = None
        if not errors:
            leakage = _leakage_check(
                "\n".join(
                    (
                        title,
                        json.dumps(fields, ensure_ascii=False, sort_keys=True),
                        rendered_content,
                    )
                ),
                gold_patch_text=self.gold_patch_text,
                private_terms=self.private_terms,
            )
            if not leakage["passed"]:
                errors.append(
                    {
                        "code": "LEAKAGE_DETECTED",
                        "message": (
                            "Task input leaks private patch or diagnostic details: "
                            f'{", ".join(leakage["reasons"])}'
                        ),
                    }
                )

        complete = not errors
        if complete:
            self._completed_bundle = self._build_completed_bundle(
                title=title,
                content=rendered_content,
                fields=fields,
                leakage=dict(leakage or {}),
                style_validation=style_validation,
            )
            message = f"The host accepted the {self.generation_style_id} task input."
        else:
            message = "The task input was not accepted; correct the reported errors and submit it again."

        result_payload = self._result_payload(
            request_id=request_id,
            message=message,
            accepted=complete,
            errors=errors,
            missing=missing,
            complete=complete,
            final_failure=False,
        )
        self._write_result_payload(request_id, result_payload, allow_request_sidecar_fallback=True)
        self._emit_event(
            title="Receive tool call processed",
            message=message,
            payload={
                "request_id": request_id,
                "receive_call_index": call_index,
                "accepted": complete,
                "errors": errors,
                "complete": complete,
                "final_failure": False,
                "result_payload": result_payload,
            },
        )

    def _handle_request_failure(
        self,
        request_id: str,
        request_path: Path,
        exc: Exception,
    ) -> None:
        message = f"receive tool host failed while handling {request_path.name}: {exc}"
        self._terminal_failure_message = str(exc)
        result_payload = self._result_payload(
            request_id=request_id,
            message=str(exc),
            accepted=False,
            errors=[{"code": "HOST_FAILURE", "message": str(exc)}],
            missing={},
            complete=False,
            final_failure=True,
        )
        self._write_result_payload(request_id, result_payload, allow_request_sidecar_fallback=True)
        self._emit_event(
            title="Receive tool host failure",
            message=message,
            payload={
                "request_id": request_id,
                "request_path": str(request_path),
                "error_type": type(exc).__name__,
                "result_payload": result_payload,
            },
        )

    def _handle_request_failure_error(
        self,
        request_id: str,
        request_path: Path,
        request_exc: Exception,
        error_handler_exc: Exception,
    ) -> bool:
        del request_exc
        message = (
            f"receive tool host failed while publishing the failure result for {request_path.name}: "
            f"{error_handler_exc}"
        )
        result_payload = self._result_payload(
            request_id=request_id,
            message=message,
            accepted=False,
            errors=[{"code": "HOST_FAILURE", "message": message}],
            missing={},
            complete=False,
            final_failure=True,
        )
        try:
            self._write_result_payload(request_id, result_payload, allow_request_sidecar_fallback=True)
        except Exception:
            return False
        self._terminal_failure_message = message
        self._emit_event(
            title="Receive tool failure publication recovered",
            message=message,
            payload={
                "request_id": request_id,
                "request_path": str(request_path),
                "error_type": type(error_handler_exc).__name__,
                "result_payload": result_payload,
            },
        )
        return True

    def _result_payload(
        self,
        *,
        request_id: str,
        message: str,
        accepted: bool,
        errors: list[dict[str, str]],
        missing: dict[str, int],
        complete: bool,
        final_failure: bool,
    ) -> dict[str, Any]:
        feedback_status = "fatal" if final_failure else "complete" if complete else "incomplete"
        return {
            "request_id": request_id,
            "complete": complete,
            "final_failure": final_failure,
            "pause_for_terminal": complete or final_failure,
            "message": message,
            "feedback": {
                "status": feedback_status,
                "accepted": accepted,
                "errors": errors,
                "missing": {} if complete else missing,
            },
        }

    def _build_completed_bundle(
        self,
        *,
        title: str,
        content: str,
        fields: dict[str, Any],
        leakage: dict[str, Any],
        style_validation: dict[str, Any],
    ) -> dict[str, Any]:
        repository = dict(self.source_context.get("repository") or {})
        entry_file = dict(self.source_context.get("entry_file") or {})
        savepoint = dict(self.source_context.get("savepoint") or {})
        summary = (
            f"{repository.get('full_name') or 'repository'} savepoint depth "
            f"{savepoint.get('depth') or '-'} for {entry_file.get('test_file_path') or 'entry file'}"
        )
        return {
            "issue_variants": [
                {
                    "style": self.generation_style_id,
                    "title": title,
                    "issue_markdown": content,
                    "issue_json": {
                        "style": self.generation_style_id,
                        "title": title,
                        "content": content,
                        "fields": fields,
                        "submission_mode": "receive_tool",
                    },
                    "quality_json": {
                        "submission_mode": "receive_tool",
                        "style_validation": style_validation,
                    },
                    "leakage_check_json": leakage,
                }
            ],
            "source_summary": summary,
            "self_check_notes": (
                "Accepted through the receive tool after style-schema validation, template rendering, "
                "and host-side leakage checks."
            ),
        }

    def _emit_event(self, *, title: str, message: str, payload: dict[str, Any]) -> None:
        _append_event(
            self.events_path,
            {
                "actor": "receive_tool",
                "phase": f"{self.generation_style_id}_agent",
                "title": title,
                "message": message,
                "payload": {
                    "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                    "generation_style_id": self.generation_style_id,
                    **dict(payload or {}),
                },
            },
        )

    def _write_result_payload(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        allow_request_sidecar_fallback: bool = False,
    ) -> None:
        primary_path = self.result_dir / f"{request_id}.json"
        fallback_path = self.request_dir / f"{request_id}.result"
        temp_primary_path = self.result_dir / f".{request_id}.tmp"
        temp_primary_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        try:
            temp_primary_path.replace(primary_path)
            _make_container_readable_file(primary_path)
            return
        except Exception:
            if temp_primary_path.exists():
                temp_primary_path.unlink(missing_ok=True)
            if not allow_request_sidecar_fallback:
                raise
        fallback_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _make_container_readable_file(fallback_path)


def _event_payload(*, event, conversation_ref: dict[str, Any], archived_completion_file: Path | None = None) -> dict[str, Any]:
    generation_style_id = str(conversation_ref.get("generation_style_id") or "issuer")
    actor = f"{generation_style_id}_agent"
    phase = f"{generation_style_id}_agent"
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


def _append_issuer_conversation_failure_event(
    *,
    events_path: Path,
    conversation_ref: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    context = _conversation_failure_context(conversation_ref=conversation_ref, exc=exc)
    _append_event(
        events_path,
        {
            "actor": "issuer_agent",
            "phase": "issuer_agent",
            "title": "Issuer conversation failed",
            "message": _conversation_failure_message(
                "OpenHands issuer conversation failed",
                context,
                exc,
            ),
            "payload": {
                "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                **context,
            },
        },
    )
    return context


def _append_issuer_llm_completion_archive_event(
    events_path: Path,
    *,
    archive_dir: Path,
    role: str,
) -> None:
    summary = summarize_llm_completion_archive(events_path.parent, role)
    file_count = int(summary.get("file_count") or 0)
    _append_event(
        events_path,
        {
            "actor": "issuer_agent",
            "phase": f"{role}_agent",
            "title": f"{role} raw LLM completions archived",
            "message": (
                f"Archived {file_count} OpenHands LLM completion files at {archive_dir}"
                if file_count
                else f"No OpenHands LLM completion files were archived at {archive_dir}"
            ),
            "payload": {
                "operation": STAGE4_OPENHANDS_BRIDGE_ISSUER_OPERATION,
                "generation_style_id": role,
                "archive": summary,
            },
        },
    )


def _stage4_progress_log_callback(
    *,
    events_path: Path,
    title: str,
    operation: str,
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
                "phase": "issuer_agent",
                "title": title,
                "message": lines[-1],
                "payload": {
                    "operation": operation,
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


def _build_stage4_llm_retry_listener(*, model: str, events_path: Path):
    def listener(attempt_number: int, max_attempts: int, exc: BaseException | None) -> None:
        error_type = type(exc).__name__ if exc is not None else "UnknownError"
        error_message = _truncate_text(str(exc or ""), 600)
        next_attempt = int(attempt_number) + 1
        delay_seconds = _estimated_stage4_llm_retry_delay_seconds(attempt_number)
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
                    "phase": "issuer_agent",
                    "title": "OpenHands LLM retrying",
                    "message": message,
                    "payload": {
                        "operation": STAGE4_OPENHANDS_LLM_RETRY_OPERATION,
                        "parent_operation": STAGE4_OPENHANDS_LLM_RETRY_OPERATION,
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


def _estimated_stage4_llm_retry_delay_seconds(attempt_number: int) -> float | None:
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "..."


if __name__ == "__main__":
    raise SystemExit(main())