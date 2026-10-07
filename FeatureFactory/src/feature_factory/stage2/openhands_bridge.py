from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from openhands.sdk import Agent, Conversation, LLM
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.sdk.event import (
    ActionEvent,
    LLMCompletionLogEvent,
    MessageEvent,
    ObservationEvent,
)
from openhands.sdk.tool import Tool
from openhands.tools.preset.default import get_default_condenser, get_default_tools
from openhands.tools.preset.gpt5 import get_gpt5_condenser, get_gpt5_tools
from openhands.workspace import DockerWorkspace

from feature_factory.config import get_settings
from feature_factory.db import build_engine, build_session_factory
from feature_factory.docker_mirrors import (
    docker_io_mirror_host,
    env_value,
    ghcr_io_mirror_host,
    github_proxy_prefix,
)
from feature_factory.openhands_llm import (
    build_openhands_llm_config,
    openhands_llm_session_id,
    release_openhands_llm_resources,
)
from feature_factory.stage2.assets import asset_root, read_text_asset
from feature_factory.stage2.base_images import (
    ensure_base_image_built,
    materialize_selected_base_image_reference,
    prepare_base_image_asset_view,
    resolve_base_image_payload,
    selected_base_image_prompt_summary,
)
from feature_factory.stage2.bridge_events import (
    WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage2.image_assets import (
    ensure_agent_server_image_built,
    record_host_sdk_environment_ready,
)
from feature_factory.stage2.openhands_validate_tool import (
    VALIDATE_REQUEST_DIR_ENV,
    VALIDATE_RESULT_DIR_ENV,
    ValidateTool,
)
from feature_factory.stage2.raw_archive import (
    llm_completion_archive_dir,
    summarize_llm_completion_archive,
)
from feature_factory.stage2.resume_paths import rewrite_stage2_json_tree_path_references
from feature_factory.stage2.service import Stage2Service
from feature_factory.stage2.validation_feedback import validation_failure_message
from feature_factory.stage2.validator import (
    Stage2ValidationConfig,
    Stage2ValidationError,
    Stage2Validator,
    validate_artifact_schema,
)
from feature_factory.stage2.worker_validation_queue import (
    process_pending_validation_requests,
)

PLANNER_SANDBOX_BASE_IMAGE = "nikolaik/python-nodejs:python3.13-nodejs22-slim"
PLANNER_SANDBOX_HOME = "/home/openhands"
PLANNER_RESULT_FILENAME = "planner_result.json"
PLANNER_BASE_IMAGES_MOUNT = f"{PLANNER_SANDBOX_HOME}/base_images"
WORKER_SANDBOX_WORKSPACE = "/workspace"
FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE = (
    asset_root() / "openhands_agent_server.Dockerfile"
)
FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY = "feature-factory/openhands-agent-server"
FEATURE_FACTORY_AGENT_SERVER_TAG_COMPONENT_LIMIT = 72
FEATURE_FACTORY_DOCKER_LABEL_MANAGED = "feature_factory.managed"
FEATURE_FACTORY_DOCKER_LABEL_STAGE = "feature_factory.stage"
FEATURE_FACTORY_DOCKER_LABEL_COMPONENT = "feature_factory.component"
FEATURE_FACTORY_DOCKER_LABEL_ROLE = "feature_factory.role"
FEATURE_FACTORY_DOCKER_LABEL_RUN_ID = "feature_factory.run_id"
FEATURE_FACTORY_DOCKER_LABEL_REPOSITORY = "feature_factory.repository"
FEATURE_FACTORY_DOCKER_LABEL_APP_INSTANCE_ID = "feature_factory.app_instance_id"
FEATURE_FACTORY_DOCKER_LABEL_RUNTIME_DIR = "feature_factory.runtime_dir"
FEATURE_FACTORY_DOCKER_LABEL_CREATED_AT = "feature_factory.created_at"
FEATURE_FACTORY_OPENHANDS_CONVERSATIONS_PATH = (
    "/tmp/feature-factory-openhands/conversations"
)
FEATURE_FACTORY_OPENHANDS_BASH_EVENTS_DIR = "/tmp/feature-factory-openhands/bash_events"
FEATURE_FACTORY_OPENHANDS_FORWARD_ENV = [
    "DEBUG",
    "OH_CONVERSATIONS_PATH",
    "OH_BASH_EVENTS_DIR",
]
FEATURE_FACTORY_WORKER_TOOL_ROOT = ".feature_factory_openhands_tools"
FEATURE_FACTORY_WORKER_RUNTIME_ROOT = ".stage2"
FEATURE_FACTORY_WORKER_SELECTED_BASE_IMAGE_DIR = "base_image"
FEATURE_FACTORY_WORKER_VALIDATE_DIR = "validate_tool"
FEATURE_FACTORY_WORKER_VALIDATE_REQUESTS_DIRNAME = "requests"
FEATURE_FACTORY_WORKER_VALIDATE_RESULTS_DIRNAME = "results"
FEATURE_FACTORY_WORKER_SELECTED_BASE_IMAGE_DOCKERFILE = "selected_base_image.Dockerfile"
FEATURE_FACTORY_WORKER_TOOL_PYTHON_ROOT = (
    f"{WORKER_SANDBOX_WORKSPACE}/{FEATURE_FACTORY_WORKER_TOOL_ROOT}"
)
FEATURE_FACTORY_WORKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS = 60.0
FEATURE_FACTORY_WORKER_STATUS_POLL_INTERVAL_SECONDS = 0.2
FEATURE_FACTORY_CHINA_TIMEZONES = frozenset(
    {
        "Asia/Shanghai",
        "Asia/Chongqing",
        "Asia/Harbin",
        "Asia/Urumqi",
        "Asia/Hong_Kong",
        "Asia/Macau",
    }
)
FEATURE_FACTORY_CHINA_MIRROR_BUILD_ARGS = {
    "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
    "TORCH_CPU_FIND_LINKS": "https://mirrors.aliyun.com/pytorch-wheels/cpu",
    "UV_INDEX_URL": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/",
    "UV_PYTHON_INSTALL_MIRROR": (
        "https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download"
    ),
    "UV_HTTP_TIMEOUT": "300",
    "UV_HTTP_RETRIES": "8",
    "NPM_REGISTRY": "https://registry.npmmirror.com",
    "NODE_DIST_MIRROR": "https://cdn.npmmirror.com/binaries/node",
    "MINICONDA_DIST_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda",
    "DEBIAN_APT_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/debian",
    "DEBIAN_SECURITY_APT_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/debian-security",
    "DEBIAN_APT_MIRROR_BOOTSTRAP": "http://mirrors.tuna.tsinghua.edu.cn/debian",
    "DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP": "http://mirrors.tuna.tsinghua.edu.cn/debian-security",
    "UBUNTU_APT_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu",
    "UBUNTU_PORTS_APT_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports",
    "UBUNTU_APT_MIRROR_BOOTSTRAP": "http://mirrors.tuna.tsinghua.edu.cn/ubuntu",
    "UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP": "http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports",
}
FEATURE_FACTORY_CHINA_REGIONAL_CONTEXT_DEFAULTS = {
    **FEATURE_FACTORY_CHINA_MIRROR_BUILD_ARGS,
    "DOCKER_IO_MIRROR": "docker.1ms.run",
    "GHCR_IO_MIRROR": "ghcr.m.daocloud.io",
    "GITHUB_PROXY_PREFIX": "https://ghfast.top/",
    "MAVEN_MIRROR": "https://maven.aliyun.com/repository/public",
    "RUBYGEMS_MIRROR": "https://mirrors.tuna.tsinghua.edu.cn/rubygems/",
}
FEATURE_FACTORY_STAGE2_BUILD_ARG_ENV_NAMES = {
    "AGENT_PYTHON_IMAGE": ("FEATURE_FACTORY_STAGE2_AGENT_PYTHON_IMAGE",),
    "PIP_INDEX_URL": (
        "FEATURE_FACTORY_STAGE2_PIP_INDEX_URL",
        "FEATURE_FACTORY_STAGE2_PYPI_INDEX_URL",
    ),
    "TORCH_CPU_FIND_LINKS": ("FEATURE_FACTORY_STAGE2_TORCH_CPU_FIND_LINKS",),
    "UV_INDEX_URL": ("FEATURE_FACTORY_STAGE2_UV_INDEX_URL",),
    "UV_PYTHON_INSTALL_MIRROR": ("FEATURE_FACTORY_STAGE2_UV_PYTHON_INSTALL_MIRROR",),
    "UV_PYTHON_INSTALL_MIRROR_FALLBACKS": (
        "FEATURE_FACTORY_STAGE2_UV_PYTHON_INSTALL_MIRROR_FALLBACKS",
    ),
    "UV_HTTP_TIMEOUT": ("FEATURE_FACTORY_STAGE2_UV_HTTP_TIMEOUT",),
    "UV_HTTP_RETRIES": ("FEATURE_FACTORY_STAGE2_UV_HTTP_RETRIES",),
    "NPM_REGISTRY": ("FEATURE_FACTORY_STAGE2_NPM_REGISTRY",),
    "NODE_DIST_MIRROR": ("FEATURE_FACTORY_STAGE2_NODE_DIST_MIRROR",),
    "MINICONDA_DIST_MIRROR": ("FEATURE_FACTORY_STAGE2_MINICONDA_DIST_MIRROR",),
    "DEBIAN_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_DEBIAN_APT_MIRROR",),
    "DEBIAN_SECURITY_APT_MIRROR": (
        "FEATURE_FACTORY_STAGE2_DEBIAN_SECURITY_APT_MIRROR",
    ),
    "DEBIAN_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_DEBIAN_APT_MIRROR_BOOTSTRAP",
    ),
    "DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP",
    ),
    "UBUNTU_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_UBUNTU_APT_MIRROR",),
    "UBUNTU_PORTS_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_UBUNTU_PORTS_APT_MIRROR",),
    "UBUNTU_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_UBUNTU_APT_MIRROR_BOOTSTRAP",
    ),
    "UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP",
    ),
}
FEATURE_FACTORY_REGIONAL_CONTEXT_ENV_NAMES = {
    **FEATURE_FACTORY_STAGE2_BUILD_ARG_ENV_NAMES,
    "DOCKER_IO_MIRROR": ("FEATURE_FACTORY_DOCKER_IO_MIRROR",),
    "GHCR_IO_MIRROR": ("FEATURE_FACTORY_GHCR_IO_MIRROR",),
    "GITHUB_PROXY_PREFIX": ("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",),
    "MINICONDA_DIST_MIRROR": (
        "FEATURE_FACTORY_STAGE2_MINICONDA_DIST_MIRROR",
        "FEATURE_FACTORY_STAGE2_MINICONDA_DIST_URL",
    ),
    "MAVEN_MIRROR": ("FEATURE_FACTORY_STAGE2_MAVEN_MIRROR",),
    "RUBYGEMS_MIRROR": ("FEATURE_FACTORY_STAGE2_RUBYGEMS_MIRROR",),
}


def main() -> int:
    args = _parse_args()
    request = json.loads(Path(args.request).read_text())
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
                "phase": _phase_for_mode(str(request.get("mode") or "")),
                "title": "OpenHands bridge failed",
                "message": _truncate_text(str(exc), 1000),
                "payload": {
                    "operation": _bridge_operation(str(request.get("mode") or "")),
                    "error_type": type(exc).__name__,
                },
            },
        )
        raise


def _phase_for_mode(mode: str) -> str:
    return "host_planner" if mode == "planner" else "container_worker"


def _bridge_operation(mode: str) -> str:
    return "openhands_bridge_worker" if mode == "worker" else "openhands_bridge_planner"


def _agent_server_container_labels(
    *,
    request: dict[str, Any],
    runtime_dir: Path,
    role: str,
    stage: str = "stage2",
) -> dict[str, str]:
    repository = dict(request.get("repository") or {})
    workspace_dir = Path(str(request.get("workspace_dir") or "")).expanduser()
    run_id = str(workspace_dir.name or "").strip()
    return {
        FEATURE_FACTORY_DOCKER_LABEL_MANAGED: "true",
        FEATURE_FACTORY_DOCKER_LABEL_STAGE: str(stage or "stage2"),
        FEATURE_FACTORY_DOCKER_LABEL_COMPONENT: "agent-server",
        FEATURE_FACTORY_DOCKER_LABEL_ROLE: role,
        FEATURE_FACTORY_DOCKER_LABEL_RUN_ID: run_id,
        FEATURE_FACTORY_DOCKER_LABEL_REPOSITORY: str(
            repository.get("full_name") or ""
        ).strip(),
        FEATURE_FACTORY_DOCKER_LABEL_APP_INSTANCE_ID: str(
            request.get("app_instance_id") or ""
        ).strip(),
        FEATURE_FACTORY_DOCKER_LABEL_RUNTIME_DIR: str(runtime_dir.resolve()),
        FEATURE_FACTORY_DOCKER_LABEL_CREATED_AT: datetime.now(UTC)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def _run_request(*, request: dict[str, Any], events_path: Path) -> dict[str, Any]:
    mode = str(request.get("mode") or "")
    phase = _phase_for_mode(mode)
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": phase,
            "title": "OpenHands bridge initialized",
            "message": f"Bridge process is preparing {mode or 'unknown'} agent runtime",
            "payload": {
                "operation": _bridge_operation(mode),
                "mode": mode,
                "workspace_dir": str(request.get("workspace_dir") or ""),
            },
        },
    )
    record_host_sdk_environment_ready(
        log_tail=f"OpenHands bridge host Python environment started for {mode or 'unknown'} mode."
    )
    llm = _build_llm(mode=mode, events_path=events_path)
    try:
        completion_archive_dir = Path(str(llm.log_completions_folder))
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": phase,
                "title": "OpenHands LLM configured",
                "message": f"LLM configured for {mode or 'unknown'} agent: {llm.model}",
                "payload": {
                    "operation": _bridge_operation(mode),
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
            mode=mode,
        )
        max_iterations = int(request.get("max_iterations") or 150)
        workspace_dir = Path(str(request.get("workspace_dir") or "")).resolve()
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": phase,
                "title": "OpenHands agent constructed",
                "message": f"Agent preset {request.get('preset') or 'gpt5'} is ready with {max_iterations} max iterations",
                "payload": {
                    "operation": _bridge_operation(mode),
                    "preset": str(request.get("preset") or "gpt5"),
                    "max_iterations": max_iterations,
                },
            },
        )

        if mode == "planner":
            return _run_planner(
                request=request,
                workspace_dir=workspace_dir,
                agent=agent,
                llm=llm,
                max_iterations=max_iterations,
                events_path=events_path,
            )
        if mode == "worker":
            return _run_worker(
                request=request,
                workspace_dir=workspace_dir,
                agent=agent,
                llm=llm,
                max_iterations=max_iterations,
                events_path=events_path,
            )
        raise RuntimeError(f"unsupported bridge mode: {mode}")
    finally:
        _release_llm_session(
            llm=llm,
            events_path=events_path,
            phase=phase,
            operation=_bridge_operation(mode),
        )


def _run_planner(
    *,
    request: dict[str, Any],
    workspace_dir: Path,
    agent,
    llm: LLM,
    max_iterations: int,
    events_path: Path,
) -> dict[str, Any]:
    host_result_file = workspace_dir / PLANNER_RESULT_FILENAME
    container_result_file = Path(f"{PLANNER_SANDBOX_HOME}/{PLANNER_RESULT_FILENAME}")
    host_result_file.unlink(missing_ok=True)
    prompt = _build_planner_prompt(request=request, result_file=container_result_file)

    conversation_ref: dict[str, Any] = {
        "llm_completion_archive_dir": str(llm.log_completions_folder),
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
                mode="planner",
                conversation_ref=conversation_ref,
                archived_completion_file=archived_file,
            ),
        )

    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "host_planner",
            "title": "Planner sandbox preparing",
            "message": f"Preparing Docker workspace from {PLANNER_SANDBOX_BASE_IMAGE}",
            "payload": {
                "operation": _bridge_operation("planner"),
                "host_workspace_dir": str(workspace_dir),
                "sandbox_home": PLANNER_SANDBOX_HOME,
                "sandbox_base_image": PLANNER_SANDBOX_BASE_IMAGE,
                "sandbox_base_images_path": PLANNER_BASE_IMAGES_MOUNT,
            },
        },
    )
    planner_agent_server_image_ref = (
        str(request.get("planner_agent_server_image_ref") or "").strip() or None
    )
    with _docker_workspace(
        workspace_dir,
        base_image=PLANNER_SANDBOX_BASE_IMAGE,
        server_image_override=planner_agent_server_image_ref,
        mount_target=PLANNER_SANDBOX_HOME,
        working_dir=PLANNER_SANDBOX_HOME,
        runtime_dir=events_path.parent,
        openhands_role="planner",
        agent_server_build_timeout_seconds=_build_timeout_from_request(request),
        extra_volumes=_planner_extra_volumes(
            runtime_dir=events_path.parent,
            base_image_catalog=list(request.get("base_image_catalog") or []),
        ),
        labels=_agent_server_container_labels(
            request=request,
            runtime_dir=events_path.parent,
            role="planner",
        ),
    ) as workspace:
        _append_event(
            events_path,
            {
                "actor": "planner_agent",
                "phase": "host_planner",
                "title": "Planner sandbox started",
                "message": f"OpenHands planner sandbox is ready for {request['repository']['full_name']}",
                "payload": {
                    "host_workspace_dir": str(workspace_dir),
                    "sandbox_home": PLANNER_SANDBOX_HOME,
                    "sandbox_repo_path": f"{PLANNER_SANDBOX_HOME}/repo",
                    "sandbox_base_images_path": PLANNER_BASE_IMAGES_MOUNT,
                    "repo_path": str(request["repo_path"]),
                    "sandbox_base_image": PLANNER_SANDBOX_BASE_IMAGE,
                    "server_image_ref": planner_agent_server_image_ref or "",
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
                "actor": "planner_agent",
                "phase": "host_planner",
                "title": "Planner conversation started",
                "message": f"OpenHands planner is inspecting {request['repository']['full_name']} in a sandbox",
                "payload": {
                    "host_workspace_dir": str(workspace_dir),
                    "sandbox_home": PLANNER_SANDBOX_HOME,
                    "sandbox_repo_path": f"{PLANNER_SANDBOX_HOME}/repo",
                    "sandbox_base_images_path": PLANNER_BASE_IMAGES_MOUNT,
                    "repo_path": str(request["repo_path"]),
                    "sandbox_base_image": PLANNER_SANDBOX_BASE_IMAGE,
                },
            },
        )
        try:
            conversation.send_message(prompt)
            conversation.run(blocking=False)
            _run_planner_conversation_until_terminal(
                conversation=conversation,
                conversation_ref=conversation_ref,
                events_path=events_path,
            )
        except Exception as exc:
            context = _append_conversation_failure_event(
                events_path=events_path,
                mode="planner",
                title="Planner conversation failed",
                message_prefix="OpenHands planner conversation failed",
                conversation_ref=conversation_ref,
                exc=exc,
            )
            raise RuntimeError(
                _conversation_failure_message(
                    "OpenHands planner conversation failed",
                    context,
                    exc,
                )
            ) from exc
        finally:
            _append_llm_completion_archive_event(
                events_path,
                mode="planner",
                archive_dir=Path(str(llm.log_completions_folder)),
            )

        planner_result = _load_json(host_result_file, label="planner result")
        _validate_planner_result(planner_result, request=request)
        host_result_file.unlink(missing_ok=True)
        return {
            "status": "completed",
            "mode": "planner",
            "model": llm.model,
            "token_usage": _token_usage_payload(conversation),
            "conversation_id": str(conversation.state.id),
            "execution_status": str(conversation.state.execution_status),
            "llm_completion_archive": summarize_llm_completion_archive(
                events_path.parent,
                "planner",
            ),
            "result": planner_result,
        }


def _run_planner_conversation_until_terminal(
    *,
    conversation,
    conversation_ref: dict[str, Any],
    events_path: Path,
) -> None:
    paused_since = 0.0
    while True:
        status = _conversation_execution_status(conversation, refresh=True)
        if status == ConversationExecutionStatus.FINISHED:
            return
        if status == ConversationExecutionStatus.ERROR:
            raise RuntimeError("planner conversation ended with error")
        if status == ConversationExecutionStatus.STUCK:
            raise RuntimeError("planner conversation got stuck")
        if status == ConversationExecutionStatus.PAUSED:
            if paused_since <= 0.0:
                paused_since = time.monotonic()
            elif time.monotonic() - paused_since > 60.0:
                raise RuntimeError("planner conversation remained paused for 60s")
        else:
            paused_since = 0.0
        time.sleep(FEATURE_FACTORY_WORKER_STATUS_POLL_INTERVAL_SECONDS)


def _run_worker(
    *,
    request: dict[str, Any],
    workspace_dir: Path,
    agent,
    llm: LLM,
    max_iterations: int,
    events_path: Path,
) -> dict[str, Any]:
    selected_base_image = resolve_base_image_payload(
        image_id=str((request.get("planner_decision") or {}).get("base_image") or ""),
        base_image_catalog=list(
            (request.get("planner_decision") or {}).get("base_image_catalog") or []
        ),
    )
    if selected_base_image is None:
        raise RuntimeError(
            "worker request does not contain the selected base image payload"
        )

    dockerfile_path = workspace_dir / "Dockerfile"
    run_script_path = workspace_dir / "run_script.sh"
    dockerfile_path.unlink(missing_ok=True)
    run_script_path.unlink(missing_ok=True)
    platform_name = _detect_platform()
    prebuilt_base_image_ref = str(request.get("worker_base_image_ref") or "").strip()
    if prebuilt_base_image_ref:
        base_image_ref = prebuilt_base_image_ref
    else:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": "container_worker",
                "title": "Worker base image preparing",
                "message": (
                    f"Ensuring selected base image {selected_base_image['image_ref']} "
                    f"from {selected_base_image['asset_path']}"
                ),
                "payload": {
                    "operation": "worker_base_image_prepare",
                    "image_id": selected_base_image["image_id"],
                    "image_ref": selected_base_image["image_ref"],
                    "asset_path": selected_base_image["asset_path"],
                    "platform": platform_name,
                },
            },
        )
        base_image_ref = ensure_base_image_built(
            source_root=asset_root(),
            base_image_payload=selected_base_image,
            platform_name=platform_name,
            timeout_seconds=_build_timeout_from_request(request),
        )
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "container_worker",
            "title": "Worker base image ready",
            "message": f"Selected base image {base_image_ref} is ready",
            "payload": {
                "operation": "worker_base_image_prepare",
                "image_id": selected_base_image["image_id"],
                "image_ref": base_image_ref,
                "asset_path": selected_base_image["asset_path"],
                "platform": platform_name,
                "source": "prebuilt" if prebuilt_base_image_ref else "bridge",
            },
        },
    )

    worker_support = _prepare_worker_support_files(
        workspace_dir=workspace_dir,
        selected_base_image=selected_base_image,
    )
    resume_checkpoint = dict(request.get("resume_checkpoint") or {})
    resume_server_image_ref = _resolve_worker_resume_server_image(
        checkpoint=resume_checkpoint,
        events_path=events_path,
    )
    worker_agent_server_image_ref = (
        str(request.get("worker_agent_server_image_ref") or "").strip() or None
    )
    server_image_ref = resume_server_image_ref or worker_agent_server_image_ref
    validation_service = _WorkerValidationService(
        run_id=workspace_dir.name,
        workspace_dir=workspace_dir,
        runtime_dir=events_path.parent,
        events_path=events_path,
        validation_config=_validation_config_from_request(request),
        max_validate_calls=int(request.get("max_validate_calls") or 1),
        request_dir=worker_support["validate_request_dir_host"],
        result_dir=worker_support["validate_result_dir_host"],
        initial_attempt_count=int(resume_checkpoint.get("attempt_index") or 0),
        checkpoint_enabled=bool(get_settings().stage2_enable_checkpoints),
    )

    prompt = _build_worker_prompt(
        request=request,
        dockerfile_path=dockerfile_path,
        run_script_path=run_script_path,
        selected_base_image=selected_base_image,
        selected_base_image_dockerfile_path=worker_support[
            "selected_base_image_dockerfile_container"
        ],
        selected_base_image_metadata_path=worker_support[
            "selected_base_image_metadata_container"
        ],
    )
    conversation_ref: dict[str, Any] = {
        "llm_completion_archive_dir": str(llm.log_completions_folder),
    }

    def callback(event) -> None:
        archived_file = _archive_llm_completion_event(
            event,
            archive_dir=Path(str(llm.log_completions_folder)),
        )
        validation_service.request_checkpoint_for_validate_observation(
            event=event,
        )
        _append_event(
            events_path,
            _event_payload(
                event=event,
                mode="worker",
                conversation_ref=conversation_ref,
                archived_completion_file=archived_file,
            ),
        )

    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "container_worker",
            "title": "Worker sandbox preparing",
            "message": (
                f"Preparing Docker workspace from {server_image_ref}"
                if server_image_ref
                else f"Preparing Docker workspace from {request['planner_decision']['base_image_ref']}"
            ),
            "payload": {
                "operation": _bridge_operation("worker"),
                "workspace_dir": str(workspace_dir),
                "repo_path": str(request["repo_path"]),
                "base_image_ref": base_image_ref,
                "server_image_ref": server_image_ref or "",
                "max_validate_calls": int(request.get("max_validate_calls") or 1),
                "resumed_from_checkpoint": bool(resume_checkpoint),
            },
        },
    )
    openhands_runtime_env = _openhands_server_runtime_env(
        runtime_dir=events_path.parent, role="worker"
    )
    conversation_id = _prepare_worker_resume_conversation_state(
        checkpoint=resume_checkpoint,
        runtime_env=openhands_runtime_env,
        events_path=events_path,
    )
    with _docker_workspace(
        workspace_dir,
        base_image=base_image_ref,
        server_image_override=server_image_ref,
        mount_target=WORKER_SANDBOX_WORKSPACE,
        working_dir=WORKER_SANDBOX_WORKSPACE,
        runtime_dir=events_path.parent,
        openhands_role="worker",
        agent_server_build_timeout_seconds=_build_timeout_from_request(request),
        extra_volumes=None,
        labels=_agent_server_container_labels(
            request=request,
            runtime_dir=events_path.parent,
            role="worker",
        ),
        forward_env_names=[
            *FEATURE_FACTORY_OPENHANDS_FORWARD_ENV,
            "PYTHONPATH",
            "FEATURE_FACTORY_STAGE2_ENABLE_CHECKPOINTS",
            VALIDATE_REQUEST_DIR_ENV,
            VALIDATE_RESULT_DIR_ENV,
        ],
        forward_env_updates={
            **openhands_runtime_env,
            "PYTHONPATH": str(worker_support["tool_python_root_container"]),
            "FEATURE_FACTORY_STAGE2_ENABLE_CHECKPOINTS": (
                "1" if get_settings().stage2_enable_checkpoints else "0"
            ),
            VALIDATE_REQUEST_DIR_ENV: str(
                worker_support["validate_request_dir_container"]
            ),
            VALIDATE_RESULT_DIR_ENV: str(
                worker_support["validate_result_dir_container"]
            ),
        },
    ) as workspace:
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
        validation_service.start()
        _append_event(
            events_path,
            {
                "actor": "worker_agent",
                "phase": "container_worker",
                "title": "Worker conversation started",
                "message": (
                    "OpenHands worker is generating artifacts on base image "
                    f"{base_image_ref}"
                ),
                "payload": {
                    "workspace_dir": str(workspace_dir),
                    "repo_path": str(request["repo_path"]),
                    "base_image": selected_base_image["image_id"],
                    "base_image_ref": base_image_ref,
                    "server_image_ref": server_image_ref or "",
                    "max_validate_calls": int(request.get("max_validate_calls") or 1),
                    "resumed_from_checkpoint": bool(resume_checkpoint),
                    "resume_attempt_index": int(
                        resume_checkpoint.get("attempt_index") or 0
                    ),
                },
            },
        )
        try:
            if not resume_checkpoint:
                conversation.send_message(prompt)
            conversation.run(blocking=False)
            _run_worker_conversation_until_terminal(
                conversation=conversation,
                validation_service=validation_service,
                conversation_ref=conversation_ref,
                events_path=events_path,
            )
        except Exception as exc:
            context = _append_conversation_failure_event(
                events_path=events_path,
                mode="worker",
                title="Worker conversation failed",
                message_prefix="OpenHands worker conversation failed",
                conversation_ref=conversation_ref,
                exc=exc,
            )
            raise RuntimeError(
                _conversation_failure_message(
                    "OpenHands worker conversation failed",
                    context,
                    exc,
                )
            ) from exc
        finally:
            validation_service.stop()
            _append_llm_completion_archive_event(
                events_path,
                mode="worker",
                archive_dir=Path(str(llm.log_completions_folder)),
            )

        validation_attempts = validation_service.history_payload()
        final_validation = validation_service.final_validation_payload()
        post_agent_full_validation_required = (
            validation_service.post_agent_full_validation_required()
        )
        artifacts_present = dockerfile_path.exists() and run_script_path.exists()
        worker_result = {
            "schema_version": 1,
            "summary": (
                (
                    "OpenHands worker generated stage2 artifacts and passed smoke validation."
                    if post_agent_full_validation_required
                    else "OpenHands worker generated stage2 artifacts."
                )
                if artifacts_present
                else validation_failure_message(
                    final_validation=final_validation,
                    description="worker did not produce Dockerfile and run_script.sh",
                    unvalidated_description="worker did not produce Dockerfile and run_script.sh",
                )
            ),
            "changes": [],
            "known_risks": [],
            "artifacts_present": artifacts_present,
        }
        return {
            "status": "completed",
            "mode": "worker",
            "model": llm.model,
            "token_usage": _token_usage_payload(conversation),
            "conversation_id": str(conversation.state.id),
            "execution_status": str(conversation.state.execution_status),
            "llm_completion_archive": summarize_llm_completion_archive(
                events_path.parent,
                "worker",
            ),
            "result": worker_result,
            "validation_attempts": validation_attempts,
            "validation_attempts_persisted_live": (
                validation_service.validation_attempts_persisted_live()
            ),
            "final_validation": final_validation,
            "post_agent_full_validation_required": post_agent_full_validation_required,
        }


def _validation_config_from_request(request: dict[str, Any]) -> Stage2ValidationConfig:
    validation = dict(request.get("validation") or {})
    return Stage2ValidationConfig(
        stage2_quickcheck_sample_size=int(
            validation.get("quickcheck_sample_size") or 10
        ),
        stage2_collect_timeout_seconds=float(
            validation.get("collect_timeout_seconds")
            or validation.get("command_timeout_seconds")
            or 300.0
        ),
        stage2_run_test_timeout_seconds=float(
            validation.get("run_test_timeout_seconds")
            or validation.get("command_timeout_seconds")
            or 300.0
        ),
        stage2_build_timeout_seconds=float(
            validation.get("build_timeout_seconds") or 600.0
        ),
        stage2_full_validation_timeout_seconds=float(
            validation.get("full_validation_timeout_seconds") or 2400.0
        ),
        stage2_docker_image_prefix=str(
            validation.get("docker_image_prefix") or "feature-factory-stage2"
        ),
        stage2_p2p_file_count_limit=(
            int(validation["p2p_file_count_limit"])
            if validation.get("p2p_file_count_limit") not in (None, "")
            else None
        ),
        stage2_p2p_sample_seed=str(validation.get("p2p_sample_seed") or "") or None,
    )


def _build_timeout_from_request(request: dict[str, Any]) -> float | None:
    validation = dict(request.get("validation") or {})
    for value in (
        request.get("build_timeout_seconds"),
        validation.get("build_timeout_seconds"),
    ):
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _run_worker_conversation_until_terminal(
    *,
    conversation,
    validation_service: "_WorkerValidationService",
    conversation_ref: dict[str, Any],
    events_path: Path,
) -> None:
    checkpoint_pause_attempt_index = 0
    checkpoint_pause_requested_at = 0.0
    paused_without_pending_since = 0.0
    while True:
        checkpoint_error = validation_service.checkpoint_control_error()
        if checkpoint_error:
            raise RuntimeError(checkpoint_error)

        status = _conversation_execution_status(conversation, refresh=True)
        pending_attempt_index = validation_service.pending_checkpoint_attempt_index()

        if pending_attempt_index > 0:
            if checkpoint_pause_attempt_index != pending_attempt_index:
                checkpoint_pause_attempt_index = pending_attempt_index
                checkpoint_pause_requested_at = time.monotonic()
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "container_worker",
                        "title": "Worker checkpoint pause requested",
                        "message": (
                            "Validate observation returned; waiting for the worker "
                            f"conversation to pause at attempt {pending_attempt_index}"
                        ),
                        "payload": {
                            "attempt_index": pending_attempt_index,
                        },
                    },
                )

            if status == ConversationExecutionStatus.PAUSED:
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "container_worker",
                        "title": "Worker checkpoint pause confirmed",
                        "message": (
                            f"Worker conversation paused at validate attempt "
                            f"{pending_attempt_index}"
                        ),
                        "payload": {
                            "attempt_index": pending_attempt_index,
                        },
                    },
                )
                saved_attempt_index = validation_service.checkpoint_pending_attempt(
                    conversation_ref=conversation_ref,
                )
                if validation_service.should_exit_after_checkpoint(saved_attempt_index):
                    _append_event(
                        events_path,
                        {
                            "actor": "system",
                            "phase": "container_worker",
                            "title": "Worker conversation handoff completed",
                            "message": (
                                "Smoke validation passed; ending the worker agent run "
                                "after checkpointing so full validation can continue "
                                "outside the agent time budget"
                            ),
                            "payload": {
                                "attempt_index": saved_attempt_index,
                            },
                        },
                    )
                    return
                _append_event(
                    events_path,
                    {
                        "actor": "system",
                        "phase": "container_worker",
                        "title": "Worker checkpoint resume requested",
                        "message": (
                            f"Resuming worker conversation after checkpointing "
                            f"validate attempt {saved_attempt_index}"
                        ),
                        "payload": {
                            "attempt_index": saved_attempt_index,
                        },
                    },
                )
                conversation.run(blocking=False)
                checkpoint_pause_attempt_index = 0
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
                    "worker conversation reached a terminal state before the "
                    f"checkpoint pause completed for validate attempt {pending_attempt_index}"
                )

            if (
                checkpoint_pause_requested_at > 0.0
                and (time.monotonic() - checkpoint_pause_requested_at)
                > FEATURE_FACTORY_WORKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS
            ):
                raise RuntimeError(
                    "worker checkpoint pause timed out after "
                    f"{FEATURE_FACTORY_WORKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS:.0f}s "
                    f"for validate attempt {pending_attempt_index}"
                )
        else:
            checkpoint_pause_attempt_index = 0
            checkpoint_pause_requested_at = 0.0

            if status == ConversationExecutionStatus.FINISHED:
                return
            if status == ConversationExecutionStatus.ERROR:
                raise RuntimeError("worker conversation ended with error")
            if status == ConversationExecutionStatus.STUCK:
                raise RuntimeError("worker conversation got stuck")
            if status == ConversationExecutionStatus.PAUSED:
                if paused_without_pending_since <= 0.0:
                    paused_without_pending_since = time.monotonic()
                elif (
                    time.monotonic() - paused_without_pending_since
                    > FEATURE_FACTORY_WORKER_CHECKPOINT_PAUSE_TIMEOUT_SECONDS
                ):
                    raise RuntimeError(
                        "worker conversation remained paused without a pending "
                        "checkpoint request"
                    )
            else:
                paused_without_pending_since = 0.0

        time.sleep(FEATURE_FACTORY_WORKER_STATUS_POLL_INTERVAL_SECONDS)


def _prepare_worker_support_files(
    *,
    workspace_dir: Path,
    selected_base_image: dict[str, Any],
) -> dict[str, Any]:
    workspace_dir = workspace_dir.resolve()
    tool_python_root_host = workspace_dir / FEATURE_FACTORY_WORKER_TOOL_ROOT
    tool_module_dir = tool_python_root_host / "feature_factory" / "stage2"
    tool_module_dir.mkdir(parents=True, exist_ok=True)
    for package_init in (
        tool_python_root_host / "feature_factory" / "__init__.py",
        tool_module_dir / "__init__.py",
    ):
        package_init.parent.mkdir(parents=True, exist_ok=True)
        package_init.write_text("", encoding="utf-8")
    shutil.copyfile(
        Path(__file__).resolve().parent / "openhands_validate_tool.py",
        tool_module_dir / "openhands_validate_tool.py",
    )

    selected_base_image_dockerfile_host, selected_base_image_metadata_host = (
        materialize_selected_base_image_reference(
            source_root=asset_root(),
            workspace_dir=workspace_dir,
            base_image_payload=selected_base_image,
        )
    )
    stage2_support_dir = workspace_dir / FEATURE_FACTORY_WORKER_RUNTIME_ROOT
    stage2_support_dir.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(stage2_support_dir)
    validate_root = stage2_support_dir / FEATURE_FACTORY_WORKER_VALIDATE_DIR
    validate_root.mkdir(parents=True, exist_ok=True)
    _make_container_traversable(validate_root)
    request_dir_host = validate_root / FEATURE_FACTORY_WORKER_VALIDATE_REQUESTS_DIRNAME
    result_dir_host = validate_root / FEATURE_FACTORY_WORKER_VALIDATE_RESULTS_DIRNAME
    for directory in (request_dir_host, result_dir_host):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
        _make_container_writable(directory)
    _make_container_writable(tool_python_root_host, recursive=True)

    return {
        "tool_python_root_host": tool_python_root_host,
        "tool_python_root_container": _container_path_for_host_path(
            tool_python_root_host,
            workspace_dir=workspace_dir,
        ),
        "selected_base_image_dockerfile_host": selected_base_image_dockerfile_host,
        "selected_base_image_dockerfile_container": _container_path_for_host_path(
            selected_base_image_dockerfile_host,
            workspace_dir=workspace_dir,
        ),
        "selected_base_image_metadata_host": selected_base_image_metadata_host,
        "selected_base_image_metadata_container": _container_path_for_host_path(
            selected_base_image_metadata_host,
            workspace_dir=workspace_dir,
        ),
        "validate_request_dir_host": request_dir_host,
        "validate_request_dir_container": _container_path_for_host_path(
            request_dir_host,
            workspace_dir=workspace_dir,
        ),
        "validate_result_dir_host": result_dir_host,
        "validate_result_dir_container": _container_path_for_host_path(
            result_dir_host,
            workspace_dir=workspace_dir,
        ),
    }


def _container_path_for_host_path(path: Path, *, workspace_dir: Path) -> str:
    relative = path.resolve().relative_to(workspace_dir.resolve())
    return str(Path(WORKER_SANDBOX_WORKSPACE) / relative)


def _resolve_worker_container_path(path: str, *, workspace_dir: Path) -> Path:
    normalized = str(path or "").strip()
    workspace_root = Path(WORKER_SANDBOX_WORKSPACE)
    candidate = Path(normalized)
    if not candidate.is_absolute():
        raise RuntimeError(f"worker validate path must be absolute: {path}")
    relative = candidate.relative_to(workspace_root)
    resolved = (workspace_dir / relative).resolve()
    resolved.relative_to(workspace_dir.resolve())
    return resolved


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def _write_host_artifact_copy(path: Path, text: str, *, executable: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.write_text(text, encoding="utf-8")
    path.chmod(0o777 if executable else 0o666)


def _collect_summary(report: dict[str, Any]) -> dict[str, Any]:
    collect_payload = dict((((report.get("collect") or {}).get("payload")) or {}))
    test_files = list(collect_payload.get("test_files") or [])
    return {
        "test_file_count": len(test_files),
        "example_paths": [str(item.get("path") or "") for item in test_files[:5]],
    }


def _smoke_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": str(report.get("status") or ""),
        "phase": str(report.get("phase") or ""),
        "sample_size": int(report.get("sample_size") or 0),
        "passed_files": int(report.get("passed_files") or 0),
    }


def _full_summary(report: dict[str, Any]) -> dict[str, Any]:
    return dict(report.get("summary") or {})


_VALIDATE_BUDGET_EXEMPT_FEEDBACK_CODES = frozenset(
    {
        "VALIDATE_ARTIFACT_READ_FAILED",
        "ARTIFACT_SCHEMA_INVALID",
    }
)


def _feedback_code(feedback: dict[str, Any]) -> str:
    return str(feedback.get("code") or "").strip()


def _feedback_uses_validate_budget(feedback: dict[str, Any]) -> bool:
    return _feedback_code(feedback) not in _VALIDATE_BUDGET_EXEMPT_FEEDBACK_CODES


class _WorkerValidationService:
    def __init__(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        runtime_dir: Path,
        events_path: Path,
        validation_config: Stage2ValidationConfig,
        max_validate_calls: int,
        request_dir: Path,
        result_dir: Path,
        initial_attempt_count: int = 0,
        checkpoint_enabled: bool = True,
    ) -> None:
        self.run_id = run_id
        self.workspace_dir = workspace_dir.resolve()
        self.runtime_dir = runtime_dir.resolve()
        self.events_path = events_path.resolve()
        self.validator = Stage2Validator(validation_config)
        self.max_validate_calls = max_validate_calls
        self.checkpoint_enabled = bool(checkpoint_enabled)
        self.initial_attempt_count = max(int(initial_attempt_count or 0), 0)
        self.request_dir = request_dir.resolve()
        self.result_dir = result_dir.resolve()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._history: list[dict[str, Any]] = []
        self._last_result_payload: dict[str, Any] | None = None
        self._processed_request_ids: set[str] = set()
        self._lock = threading.RLock()
        self._pending_checkpoint_attempt_index: int | None = None
        self._checkpoint_control_error: str | None = None
        self._checkpointed_attempt_indexes: set[int] = set()
        self._exit_after_checkpoint_attempt_index: int | None = None
        self._live_persistence_available = True
        self._live_persistence_error: str = ""

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run_loop,
            name="feature-factory-stage2-validate-tool",
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

    def validation_attempts_persisted_live(self) -> bool:
        with self._lock:
            return bool(self._live_persistence_available)

    def _persist_live_best_effort(
        self,
        *,
        label: str,
        attempt_index: int,
        persist,
    ) -> None:
        try:
            persist()
            return
        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"
            should_emit = False
            with self._lock:
                if self._live_persistence_available:
                    self._live_persistence_available = False
                    should_emit = True
                self._live_persistence_error = error_message
            if should_emit:
                self._emit_event(
                    title="Validate live persistence unavailable",
                    message=(
                        "Bridge runtime cannot persist live validation data directly; "
                        "continuing and deferring persistence to the host runner"
                    ),
                    payload={
                        "attempt_index": attempt_index,
                        "operation": label,
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )

    def _remember_result_payload(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._last_result_payload = dict(payload or {})

    def _result_payload_paths(self, request_id: str) -> tuple[Path, Path]:
        return (
            self.result_dir / f"{request_id}.json",
            self.request_dir / f"{request_id}.result",
        )

    def _write_result_payload(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        allow_request_sidecar_fallback: bool = False,
    ) -> Path:
        primary_path, fallback_path = self._result_payload_paths(request_id)
        try:
            _atomic_write_json(primary_path, payload)
            _make_container_readable_file(primary_path)
            return primary_path
        except Exception:
            if not allow_request_sidecar_fallback:
                raise
        _atomic_write_json(fallback_path, payload)
        _make_container_readable_file(fallback_path)
        return fallback_path

    def _read_worker_artifact_text(
        self, host_path: Path, *, container_path: str
    ) -> str:
        del container_path
        _chmod_container_writable(host_path)
        return host_path.read_text(encoding="utf-8")

    def _lightweight_final_validation_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        feedback = dict(payload.get("feedback") or {})
        status = str(payload.get("result") or "failed")
        phase = str(payload.get("phase") or "")
        attempt_index = int(payload.get("attempt_index") or 0)
        message = str(payload.get("message") or "")
        return {
            "status": status,
            "phase": phase,
            "attempt_index": attempt_index,
            "passed": False,
            "message": message,
            "feedback": feedback,
            "collect_report": {},
            "smoke_report": {
                "status": status,
                "phase": phase,
                "message": message,
                "feedback": feedback,
            },
            "full_report": {},
        }

    def final_validation_payload(self) -> dict[str, Any]:
        with self._lock:
            last_result_payload = dict(self._last_result_payload or {})
            if not self._history:
                if last_result_payload:
                    return self._lightweight_final_validation_payload(
                        last_result_payload
                    )
                return {"status": "unvalidated", "passed": False}
            last = dict(self._history[-1])
            exit_after_checkpoint_attempt_index = int(
                self._exit_after_checkpoint_attempt_index or 0
            )
        last_result_attempt_index = int(last_result_payload.get("attempt_index") or 0)
        if last_result_attempt_index > int(last.get("attempt_index") or 0):
            return self._lightweight_final_validation_payload(last_result_payload)
        if (
            str(last.get("result") or "") == "smoke_passed"
            and int(last.get("attempt_index") or 0)
            == exit_after_checkpoint_attempt_index
        ):
            return {
                "status": "smoke_passed",
                "phase": str(last.get("phase") or ""),
                "attempt_index": int(last.get("attempt_index") or 0),
                "passed": False,
                "message": (
                    "Smoke validation passed. The host will checkpoint the worker "
                    "and continue full validation."
                    if self.checkpoint_enabled
                    else "Smoke validation passed. The host continued full validation without checkpointing."
                ),
                "feedback": {
                    "code": "FULL_VALIDATION_DEFERRED",
                    "message": (
                        "Smoke validation passed. The host is continuing full validation "
                        "outside the worker agent."
                        if self.checkpoint_enabled
                        else "Smoke validation passed. Checkpointing is disabled, so no worker resume checkpoint was written."
                    ),
                },
                "collect_report": dict(last.get("collect_report") or {}),
                "smoke_report": dict(last.get("smoke_report") or {}),
                "full_report": {},
            }
        return {
            "status": str(last.get("result") or "failed"),
            "phase": str(last.get("phase") or ""),
            "attempt_index": int(last.get("attempt_index") or 0),
            "passed": str(last.get("result") or "") == "passed",
            "message": "",
            "feedback": {},
            "collect_report": dict(last.get("collect_report") or {}),
            "smoke_report": dict(last.get("smoke_report") or {}),
            "full_report": dict(last.get("full_report") or {}),
        }

    def request_checkpoint_for_validate_observation(
        self,
        *,
        event,
    ) -> int:
        if not self.checkpoint_enabled:
            return 0
        attempt_index = _validate_observation_attempt_index(event)
        if attempt_index <= 0:
            return 0
        with self._lock:
            if (
                attempt_index in self._checkpointed_attempt_indexes
                or self._pending_checkpoint_attempt_index == attempt_index
            ):
                return 0
            attempt_payload = self._attempt_payload_locked(attempt_index)
            if attempt_payload is None:
                return 0
            self._pending_checkpoint_attempt_index = attempt_index
        return attempt_index

    def pending_checkpoint_attempt_index(self) -> int:
        with self._lock:
            return int(self._pending_checkpoint_attempt_index or 0)

    def checkpoint_control_error(self) -> str:
        with self._lock:
            return str(self._checkpoint_control_error or "")

    def should_exit_after_checkpoint(self, attempt_index: int) -> bool:
        with self._lock:
            return int(self._exit_after_checkpoint_attempt_index or 0) == int(
                attempt_index or 0
            )

    def post_agent_full_validation_required(self) -> bool:
        with self._lock:
            return int(self._exit_after_checkpoint_attempt_index or 0) > 0

    def record_checkpoint_control_error(self, message: str) -> None:
        with self._lock:
            self._checkpoint_control_error = str(message or "")
        self._emit_event(
            title="Worker checkpoint control failed",
            message=str(message or ""),
            payload={},
        )

    def checkpoint_pending_attempt(
        self,
        *,
        conversation_ref: dict[str, Any],
    ) -> int:
        with self._lock:
            attempt_index = int(self._pending_checkpoint_attempt_index or 0)
            if attempt_index <= 0:
                return 0
            attempt_payload = self._attempt_payload_locked(attempt_index)
            if attempt_payload is None:
                raise RuntimeError(
                    f"worker checkpoint attempt payload is unavailable for attempt {attempt_index}"
                )
        self._emit_event(
            title="Worker checkpoint started",
            message=f"Saving worker checkpoint after validate attempt {attempt_index}",
            payload={"attempt_index": attempt_index},
        )
        checkpoint = self._create_checkpoint(
            attempt_payload=attempt_payload,
            conversation_ref=conversation_ref,
        )
        attempt_payload["checkpoint"] = checkpoint
        self._persist_live_checkpoint(
            attempt_index=attempt_index,
            checkpoint=checkpoint,
        )
        with self._lock:
            self._checkpointed_attempt_indexes.add(attempt_index)
            self._pending_checkpoint_attempt_index = None
        self._emit_event(
            title="Worker checkpoint saved",
            message=f"Saved worker checkpoint for validate attempt {attempt_index}",
            payload={
                "attempt_index": attempt_index,
                "checkpoint_dir": checkpoint.get("checkpoint_dir"),
                "workspace_snapshot_path": checkpoint.get("workspace_snapshot_path"),
                "docker_image_ref": checkpoint.get("docker_image_ref"),
                "docker_commit_status": checkpoint.get("docker_commit_status"),
                "conversation_id": checkpoint.get("conversation_id"),
            },
        )
        return attempt_index

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

    def _handle_request_failure(
        self,
        request_id: str,
        request_path: Path,
        exc: Exception,
    ) -> None:
        attempt_index = self.initial_attempt_count + len(self._history) + 1
        message = (
            f"validate host worker crashed while handling {request_path.name}: {exc}"
        )
        result_payload = {
            "request_id": request_id,
            "result": "failed",
            "phase": "tool",
            "attempt_index": attempt_index,
            "message": message,
            "feedback": {
                "code": "VALIDATE_TOOL_HOST_ERROR",
                "message": str(exc),
                "retryable": False,
                "suggested_actions": [
                    "inspect validator host logs",
                    "fix the validate tool host-side error before retrying",
                ],
            },
            "pause_for_checkpoint": False,
            "collect_summary": {},
            "smoke_summary": {},
            "full_summary": {},
        }
        self._write_result_payload(
            request_id,
            result_payload,
            allow_request_sidecar_fallback=True,
        )
        self._remember_result_payload(result_payload)
        try:
            self._emit_event(
                title="Validate tool host failure",
                message=message,
                payload={
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "request_path": str(request_path),
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            return

    def _handle_request_failure_error(
        self,
        request_id: str,
        request_path: Path,
        request_exc: Exception,
        error_handler_exc: Exception,
    ) -> bool:
        attempt_index = self.initial_attempt_count + len(self._history) + 1
        message = (
            f"validate host failed while publishing the failure result for {request_path.name}: "
            f"{error_handler_exc}"
        )
        result_payload = {
            "request_id": request_id,
            "result": "failed",
            "phase": "tool",
            "attempt_index": attempt_index,
            "message": message,
            "feedback": {
                "code": "VALIDATE_TOOL_HOST_FAILURE_HANDLER_ERROR",
                "message": str(error_handler_exc),
                "retryable": False,
                "suggested_actions": [
                    "inspect validator host logs",
                    "inspect request/result directory write permissions and disk space",
                    "fix the validate tool host-side error before retrying",
                ],
            },
            "pause_for_checkpoint": False,
            "collect_summary": {},
            "smoke_summary": {},
            "full_summary": {},
        }
        result_written = False
        try:
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            self._remember_result_payload(result_payload)
            result_written = True
        except Exception:
            result_written = False
        try:
            self._emit_event(
                title="Validate tool host failure handling failed",
                message=message,
                payload={
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "request_error_type": type(request_exc).__name__,
                    "request_error": str(request_exc),
                    "error_handler_error_type": type(error_handler_exc).__name__,
                    "error_handler_error": str(error_handler_exc),
                    "result_written": result_written,
                },
            )
        except Exception:
            return result_written
        return result_written

    def _handle_request(self, *, request_id: str, request_path: Path) -> None:
        request = _load_json(request_path, label=f"validate request {request_id}")
        requested_dockerfile_path = str(request.get("dockerfile_path") or "").strip()
        requested_run_script_path = str(request.get("run_script_path") or "").strip()
        attempt_index = self.initial_attempt_count + len(self._history) + 1

        self._emit_event(
            title="Validate tool call received",
            message=(
                f"Worker requested validation for {requested_dockerfile_path} "
                f"and {requested_run_script_path}"
            ),
            payload={
                "request_id": request_id,
                "attempt_index": attempt_index,
                "dockerfile_path": requested_dockerfile_path,
                "run_script_path": requested_run_script_path,
            },
        )

        if attempt_index > self.max_validate_calls:
            result_payload = {
                "request_id": request_id,
                "result": "failed",
                "phase": "tool",
                "attempt_index": attempt_index,
                "message": (
                    f"validate call limit reached: {self.max_validate_calls} calls "
                    "have already been used"
                ),
                "feedback": {
                    "code": "VALIDATE_CALL_LIMIT_REACHED",
                    "message": "No more validate retries are available in this worker run.",
                    "retryable": False,
                    "suggested_actions": [
                        "finish with a concise failure summary",
                    ],
                },
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            }
            self._remember_result_payload(result_payload)
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            return

        try:
            dockerfile_path = _resolve_worker_container_path(
                requested_dockerfile_path,
                workspace_dir=self.workspace_dir,
            )
            run_script_path = _resolve_worker_container_path(
                requested_run_script_path,
                workspace_dir=self.workspace_dir,
            )
            dockerfile_text = self._read_worker_artifact_text(
                dockerfile_path,
                container_path=requested_dockerfile_path,
            )
            run_script_text = self._read_worker_artifact_text(
                run_script_path,
                container_path=requested_run_script_path,
            )
        except Exception as exc:
            can_retry = True
            result_payload = {
                "request_id": request_id,
                "result": "retry" if can_retry else "failed",
                "phase": "schema",
                "attempt_index": attempt_index,
                "message": f"validate tool could not read the requested artifacts: {exc}",
                "feedback": {
                    "code": "VALIDATE_ARTIFACT_READ_FAILED",
                    "message": str(exc),
                    "retryable": can_retry,
                    "suggested_actions": [
                        "ensure the requested Dockerfile and run_script.sh paths exist",
                        "write the artifacts before calling validate again",
                        "ensure the artifacts are readable from the host bind mount",
                    ],
                },
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            }
            self._remember_result_payload(result_payload)
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            return

        try:
            validate_artifact_schema(
                dockerfile_text=dockerfile_text,
                run_script_text=run_script_text,
            )
        except Stage2ValidationError as exc:
            result_payload = {
                "request_id": request_id,
                "result": "retry",
                "phase": "schema",
                "attempt_index": attempt_index,
                "message": str(exc),
                "feedback": {
                    "code": "ARTIFACT_SCHEMA_INVALID",
                    "message": str(exc),
                    "retryable": True,
                    "suggested_actions": [
                        "ensure Dockerfile declares a base image with FROM",
                        "ensure run_script.sh accepts --action and --out",
                    ],
                },
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            }
            self._emit_event(
                title="Validate artifact schema rejected",
                message=str(exc),
                payload={
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "feedback": result_payload["feedback"],
                },
            )
            self._remember_result_payload(result_payload)
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            return

        _write_host_artifact_copy(
            self.workspace_dir / "Dockerfile",
            dockerfile_text,
            executable=False,
        )
        _write_host_artifact_copy(
            self.workspace_dir / "run_script.sh",
            run_script_text,
            executable=True,
        )
        self._persist_live_artifacts(
            dockerfile_text=dockerfile_text,
            run_script_text=run_script_text,
            attempt_index=attempt_index,
        )

        validate_timeout_exempt_started_at = time.monotonic()
        self._emit_event(
            title="Validate smoke started",
            message=f"Running smoke validation attempt {attempt_index}",
            payload={
                "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
                "request_id": request_id,
                "attempt_index": attempt_index,
            },
        )
        collect_report: dict[str, Any] = {}

        def _handle_collect_validated(payload: dict[str, Any]) -> None:
            nonlocal collect_report
            collect_report = dict(payload or {})
            self._persist_live_collect(
                collect_report=collect_report,
                attempt_index=attempt_index,
            )
            self._emit_event(
                title="Validate collect completed",
                message=(
                    "Collected "
                    f"{len(list(collect_report.get('test_files') or []))} unit test files"
                ),
                payload={
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "collect_summary": _collect_summary(
                        {
                            "collect": {
                                "payload": collect_report,
                            }
                        }
                    ),
                },
            )

        try:
            smoke = self.validator.run_smoke(
                run_id=self.workspace_dir.name,
                workspace_dir=self.workspace_dir,
                dockerfile_text=dockerfile_text,
                run_script_text=run_script_text,
                on_collect_validated=_handle_collect_validated,
                emit_event=lambda title, message, payload: self._emit_event(
                    title=title,
                    message=message,
                    payload=payload,
                ),
                attempt_index=attempt_index,
            )
        except Exception as exc:
            self._emit_event(
                title="Validate smoke completed",
                message=f"Smoke validation failed with host error: {exc}",
                payload={
                    "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                    "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "duration_seconds": max(
                        0.0,
                        time.monotonic() - validate_timeout_exempt_started_at,
                    ),
                    "error_type": type(exc).__name__,
                },
            )
            raise
        validate_timeout_exempt_duration_seconds = max(
            0.0,
            time.monotonic() - validate_timeout_exempt_started_at,
        )
        attempt_payload = {
            "attempt_index": attempt_index,
            "request_id": request_id,
            "dockerfile_path": requested_dockerfile_path,
            "run_script_path": requested_run_script_path,
            "dockerfile_text": dockerfile_text,
            "run_script_text": run_script_text,
            "collect_report": dict(
                collect_report
                or (((smoke.report.get("collect") or {}).get("payload")) or {})
            ),
            "smoke_report": dict(smoke.report or {}),
            "full_report": {},
            "phase": str(smoke.report.get("phase") or "smoke"),
            "result": "failed",
        }

        if not smoke.passed:
            feedback = dict(smoke.report.get("feedback") or {})
            counts_toward_validate_budget = _feedback_uses_validate_budget(feedback)
            can_retry = bool(feedback.get("retryable")) and (
                attempt_index < self.max_validate_calls
                if counts_toward_validate_budget
                else True
            )
            attempt_payload["result"] = "retry" if can_retry else "failed"
            if counts_toward_validate_budget:
                self._persist_live_smoke(
                    smoke_report=attempt_payload["smoke_report"],
                    attempt_index=attempt_index,
                )
                with self._lock:
                    self._history.append(attempt_payload)
                self._archive_attempt(attempt_payload)
            result_payload = {
                "request_id": request_id,
                "result": attempt_payload["result"],
                "phase": str(smoke.report.get("phase") or "smoke"),
                "attempt_index": attempt_index,
                "message": str(
                    feedback.get("message")
                    or smoke.report.get("status")
                    or "smoke validation failed"
                ),
                "feedback": feedback,
                "pause_for_checkpoint": self.checkpoint_enabled and counts_toward_validate_budget,
                "collect_summary": _collect_summary(smoke.report),
                "smoke_summary": _smoke_summary(smoke.report),
                "full_summary": {},
            }
            self._emit_event(
                title="Validate smoke completed",
                message=result_payload["message"],
                payload={
                    "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                    "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                    "request_id": request_id,
                    "attempt_index": attempt_index,
                    "duration_seconds": validate_timeout_exempt_duration_seconds,
                    "result": result_payload["result"],
                    "feedback": feedback,
                    "smoke_summary": result_payload["smoke_summary"],
                },
            )
            self._remember_result_payload(result_payload)
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            return

        self._persist_live_smoke(
            smoke_report=attempt_payload["smoke_report"],
            attempt_index=attempt_index,
        )
        if not self.checkpoint_enabled:
            full = self.validator.run_full(
                run_id=self.workspace_dir.name,
                workspace_dir=self.workspace_dir,
                emit_event=lambda title, message, payload: self._emit_event(
                    title=title,
                    message=message,
                    payload=payload,
                ),
            )
            attempt_payload["result"] = "passed" if full.passed else "failed"
            attempt_payload["phase"] = str((full.report or {}).get("phase") or "full")
            attempt_payload["full_report"] = dict(full.report or {})
            self._persist_live_full_report(
                report=attempt_payload["full_report"],
                attempt_index=attempt_index,
            )
            with self._lock:
                self._history.append(attempt_payload)
            self._archive_attempt(attempt_payload)
            feedback = dict((full.report or {}).get("feedback") or {})
            result_payload = {
                "request_id": request_id,
                "result": attempt_payload["result"],
                "phase": attempt_payload["phase"],
                "attempt_index": attempt_index,
                "message": str(
                    feedback.get("message")
                    or (full.report or {}).get("status")
                    or "full validation completed"
                ),
                "feedback": feedback,
                "pause_for_checkpoint": False,
                "collect_summary": _collect_summary(smoke.report),
                "smoke_summary": _smoke_summary(smoke.report),
                "full_summary": _full_summary(attempt_payload["full_report"]),
            }
            self._remember_result_payload(result_payload)
            self._write_result_payload(
                request_id,
                result_payload,
                allow_request_sidecar_fallback=True,
            )
            return
        attempt_payload["result"] = "smoke_passed"
        with self._lock:
            self._history.append(attempt_payload)
            self._exit_after_checkpoint_attempt_index = attempt_index
        self._archive_attempt(attempt_payload)
        result_payload = {
            "request_id": request_id,
            "result": "smoke_passed",
            "phase": "smoke",
            "attempt_index": attempt_index,
            "message": (
                "Smoke validation passed. The host will checkpoint the worker, "
                "end the agent run, and continue full validation."
            ),
            "feedback": {
                "code": "FULL_VALIDATION_DEFERRED",
                "message": (
                    "Smoke validation passed. Stop editing the artifacts; the host "
                    "will continue full validation outside the agent time budget."
                ),
                "retryable": False,
                "suggested_actions": [
                    "stop editing the artifacts",
                    "wait for host full validation to complete",
                ],
            },
            "pause_for_checkpoint": True,
            "collect_summary": _collect_summary(smoke.report),
            "smoke_summary": _smoke_summary(smoke.report),
            "full_summary": {},
        }
        self._emit_event(
            title="Validate smoke completed",
            message=result_payload["message"],
            payload={
                "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                "request_id": request_id,
                "attempt_index": attempt_index,
                "duration_seconds": validate_timeout_exempt_duration_seconds,
                "result": "smoke_passed",
                "deferred_full_validation": True,
                "smoke_summary": result_payload["smoke_summary"],
            },
        )
        self._remember_result_payload(result_payload)
        self._write_result_payload(
            request_id,
            result_payload,
            allow_request_sidecar_fallback=True,
        )

    def _archive_attempt(self, attempt_payload: dict[str, Any]) -> None:
        archive_dir = (
            self.runtime_dir
            / "worker-validation-attempts"
            / f"attempt-{int(attempt_payload['attempt_index']):03d}"
        )
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "Dockerfile").write_text(
            str(attempt_payload.get("dockerfile_text") or ""),
            encoding="utf-8",
        )
        (archive_dir / "run_script.sh").write_text(
            str(attempt_payload.get("run_script_text") or ""),
            encoding="utf-8",
        )
        _atomic_write_json(
            archive_dir / "collect_report.json",
            dict(attempt_payload.get("collect_report") or {}),
        )
        _atomic_write_json(
            archive_dir / "smoke_report.json",
            dict(attempt_payload.get("smoke_report") or {}),
        )
        _atomic_write_json(
            archive_dir / "full_report.json",
            dict(attempt_payload.get("full_report") or {}),
        )
        _atomic_write_json(archive_dir / "attempt.json", attempt_payload)

    def _create_checkpoint(
        self,
        *,
        attempt_payload: dict[str, Any],
        conversation_ref: dict[str, Any],
    ) -> dict[str, Any]:
        attempt_index = int(attempt_payload.get("attempt_index") or 0)
        conversation = conversation_ref.get("conversation")
        conversation_state = getattr(conversation, "state", None)
        conversation_id = str(getattr(conversation_state, "id", "") or "")
        workspace = conversation_ref.get("workspace")
        checkpoint_root = self.runtime_dir / "worker-checkpoint"
        current_dir = checkpoint_root / "current"
        temp_dir = checkpoint_root / f".tmp-{attempt_index:03d}-{uuid.uuid4().hex}"
        previous_checkpoint = _read_worker_checkpoint_payload(
            current_dir / "checkpoint.json"
        )
        previous_image_ref = str(
            previous_checkpoint.get("docker_image_ref") or ""
        ).strip()
        backup_dir: Path | None = None
        new_image_ref = ""
        try:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)
            temp_dir.mkdir(parents=True, exist_ok=True)

            workspace_snapshot_path = temp_dir / "workspace_snapshot.tar.gz"
            snapshot_permission_payload = _make_worker_workspace_snapshot_readable(
                workspace_dir=self.workspace_dir,
                workspace=conversation_ref.get("workspace"),
            )
            snapshot_payload = _create_workspace_snapshot(
                workspace_dir=self.workspace_dir,
                output_path=workspace_snapshot_path,
            )
            if str(snapshot_payload.get("status") or "") != "saved":
                raise RuntimeError(
                    "failed to create worker workspace snapshot: "
                    f"{snapshot_payload.get('error') or 'unknown error'}"
                )
            docker_payload = _commit_worker_container_checkpoint(
                run_id=self.run_id,
                attempt_index=attempt_index,
                workspace=conversation_ref.get("workspace"),
            )
            new_image_ref = str(docker_payload.get("docker_image_ref") or "").strip()

            openhands_payload = {
                "host": str(getattr(workspace, "host", "") or ""),
                "host_port": getattr(workspace, "host_port", None),
                "working_dir": str(getattr(workspace, "working_dir", "") or ""),
                "container_id": str(getattr(workspace, "_container_id", "") or ""),
                "conversations_path": "",
                "bash_events_dir": "",
            }
            source_conversations_path = str(
                os.getenv("OH_CONVERSATIONS_PATH", "") or ""
            ).strip()
            if source_conversations_path and conversation_id:
                source_conversation_dir = (
                    Path(source_conversations_path).expanduser()
                    / uuid.UUID(conversation_id).hex
                )
                if source_conversation_dir.exists():
                    destination_conversations_path = (
                        temp_dir / "openhands" / "conversations"
                    )
                    destination_conversation_dir = (
                        destination_conversations_path / uuid.UUID(conversation_id).hex
                    )
                    shutil.copytree(
                        source_conversation_dir, destination_conversation_dir
                    )
                    openhands_payload["conversations_path"] = str(
                        current_dir / "openhands" / "conversations"
                    )
            source_bash_events_dir = str(
                os.getenv("OH_BASH_EVENTS_DIR", "") or ""
            ).strip()
            if source_bash_events_dir:
                source_bash_events_path = Path(source_bash_events_dir).expanduser()
                if source_bash_events_path.exists():
                    destination_bash_events_dir = temp_dir / "openhands" / "bash_events"
                    shutil.copytree(
                        source_bash_events_path, destination_bash_events_dir
                    )
                    openhands_payload["bash_events_dir"] = str(
                        current_dir / "openhands" / "bash_events"
                    )

            checkpoint = {
                "schema_version": 1,
                "checkpoint_type": "worker_validate_cold_restore",
                "run_id": self.run_id,
                "attempt_index": attempt_index,
                "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "checkpoint_dir": str(current_dir),
                "workspace_snapshot_path": str(
                    current_dir / "workspace_snapshot.tar.gz"
                ),
                "workspace_snapshot": {
                    **snapshot_payload,
                    "path": str(current_dir / "workspace_snapshot.tar.gz"),
                },
                "workspace_snapshot_permissions": snapshot_permission_payload,
                "conversation_id": conversation_id,
                "conversation_status": str(
                    getattr(conversation_state, "execution_status", "") or ""
                ),
                "workspace_dir": str(self.workspace_dir),
                "openhands": openhands_payload,
                "docker_image_ref": docker_payload.get("docker_image_ref"),
                "docker_commit_status": docker_payload.get("docker_commit_status"),
                "docker_commit": docker_payload,
                "validation": {
                    "result": str(attempt_payload.get("result") or ""),
                    "phase": str(attempt_payload.get("phase") or ""),
                    "request_id": str(attempt_payload.get("request_id") or ""),
                    "dockerfile_path": str(
                        attempt_payload.get("dockerfile_path") or ""
                    ),
                    "run_script_path": str(
                        attempt_payload.get("run_script_path") or ""
                    ),
                },
                "restore_notes": [
                    "This is a cold restore checkpoint, not a live process snapshot.",
                    "The repository checkout is excluded from the workspace tar and should be rematerialized from repo cache.",
                    "OpenHands conversation persistence is copied into the checkpoint directory so only the latest checkpoint remains resumable.",
                ],
            }
            _atomic_write_json(temp_dir / "checkpoint.json", checkpoint)

            checkpoint_root.mkdir(parents=True, exist_ok=True)
            if current_dir.exists():
                backup_dir = checkpoint_root / f".backup-{uuid.uuid4().hex}"
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                current_dir.replace(backup_dir)
            try:
                temp_dir.replace(current_dir)
            except Exception:
                if (
                    backup_dir is not None
                    and backup_dir.exists()
                    and not current_dir.exists()
                ):
                    backup_dir.replace(current_dir)
                raise
            if backup_dir is not None and backup_dir.exists():
                shutil.rmtree(backup_dir)
            if previous_image_ref and previous_image_ref != new_image_ref:
                _remove_worker_checkpoint_image(previous_image_ref)
            return checkpoint
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if (
                backup_dir is not None
                and backup_dir.exists()
                and not current_dir.exists()
            ):
                backup_dir.replace(current_dir)
            if new_image_ref and new_image_ref != previous_image_ref:
                _remove_worker_checkpoint_image(new_image_ref)
            raise

    def _emit_event(self, *, title: str, message: str, payload: dict[str, Any]) -> None:
        _append_event(
            self.events_path,
            {
                "actor": "validator",
                "phase": "container_worker",
                "title": title,
                "message": message,
                "payload": payload,
            },
        )

    def _attempt_payload_locked(self, attempt_index: int) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self._history
                if int(item.get("attempt_index") or 0) == attempt_index
            ),
            None,
        )

    def _persist_live_artifacts(
        self,
        *,
        dockerfile_text: str,
        run_script_text: str,
        attempt_index: int,
    ) -> None:
        self._persist_live_best_effort(
            label="store_artifacts",
            attempt_index=attempt_index,
            persist=lambda: _with_stage2_service(
                lambda service: service.store_artifacts(
                    self.run_id,
                    dockerfile_text=dockerfile_text,
                    run_script_text=run_script_text,
                    attempt_index=attempt_index,
                )
            ),
        )

    def _persist_live_collect(
        self, *, collect_report: dict[str, Any], attempt_index: int
    ) -> None:
        self._persist_live_best_effort(
            label="store_collect_report",
            attempt_index=attempt_index,
            persist=lambda: _with_stage2_service(
                lambda service: service.store_collect_report(
                    self.run_id,
                    report=collect_report,
                    attempt_index=attempt_index,
                )
            ),
        )

    def _persist_live_smoke(
        self, *, smoke_report: dict[str, Any], attempt_index: int
    ) -> None:
        self._persist_live_best_effort(
            label="store_smoke_report",
            attempt_index=attempt_index,
            persist=lambda: _with_stage2_service(
                lambda service: service.store_smoke_report(
                    self.run_id,
                    report=smoke_report,
                    attempt_index=attempt_index,
                )
            ),
        )

    def _persist_live_full_report(
        self, *, report: dict[str, Any], attempt_index: int
    ) -> None:
        self._persist_live_best_effort(
            label="store_full_report",
            attempt_index=attempt_index,
            persist=lambda: _with_stage2_service(
                lambda service: service.store_full_report(
                    self.run_id,
                    report=report,
                    attempt_index=attempt_index,
                )
            ),
        )

    def _persist_live_checkpoint(
        self, *, attempt_index: int, checkpoint: dict[str, Any]
    ) -> None:
        self._persist_live_best_effort(
            label="store_validation_checkpoint",
            attempt_index=attempt_index,
            persist=lambda: _with_stage2_service(
                lambda service: service.store_validation_checkpoint(
                    self.run_id,
                    attempt_index=attempt_index,
                    checkpoint=checkpoint,
                )
            ),
        )


def _validate_observation_attempt_index(event) -> int:
    if not isinstance(event, ObservationEvent):
        return 0
    if str(getattr(event, "tool_name", "") or "") != ValidateTool.name:
        return 0
    observation = getattr(event, "observation", None)
    try:
        return int(getattr(observation, "attempt_index", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _create_workspace_snapshot(
    *, workspace_dir: Path, output_path: Path
) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    excluded_roots = {"repo"}
    included_paths: list[str] = []

    try:
        if output_path.exists():
            output_path.unlink()
        with tarfile.open(output_path, "w:gz") as archive:
            for child in sorted(workspace_dir.iterdir(), key=lambda item: item.name):
                if child.name in excluded_roots:
                    continue
                archive.add(child, arcname=child.name, recursive=True)
                included_paths.append(child.name)
        stat = output_path.stat()
        return {
            "status": "saved",
            "path": str(output_path),
            "size_bytes": stat.st_size,
            "included_roots": included_paths,
            "excluded_roots": sorted(excluded_roots),
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
            "excluded_roots": sorted(excluded_roots),
            "error": str(exc),
        }


def _make_worker_workspace_snapshot_readable(
    *, workspace_dir: Path, workspace: Any
) -> dict[str, Any]:
    host_status = "ok"
    host_error = ""
    try:
        _make_container_writable(workspace_dir, recursive=True)
    except Exception as exc:
        host_status = "failed"
        host_error = str(exc)

    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        return {
            "status": "host_only",
            "host_status": host_status,
            "host_error": host_error,
            "container_id": "",
        }

    workspace_path = shlex.quote(WORKER_SANDBOX_WORKSPACE)
    script = (
        "set -u; "
        f"workspace={workspace_path}; "
        'for p in "$workspace"/* "$workspace"/.[!.]* "$workspace"/..?*; do '
        '[ -e "$p" ] || continue; '
        'name="$(basename "$p")"; '
        '[ "$name" = repo ] && continue; '
        'chmod -R a+rwX "$p" 2>/dev/null || true; '
        "done"
    )
    try:
        result = subprocess.run(
            ["docker", "exec", "--user", "root", container_id, "sh", "-lc", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
            check=False,
        )
        returncode = result.returncode
        stderr = result.stderr[-1000:]
        container_status = "ok" if result.returncode == 0 else "failed"
    except Exception as exc:
        returncode = None
        stderr = str(exc)
        container_status = "failed"
    return {
        "status": "ok"
        if host_status == "ok" and container_status == "ok"
        else "partial",
        "host_status": host_status,
        "host_error": host_error,
        "container_status": container_status,
        "container_id": container_id,
        "returncode": returncode,
        "stderr": stderr,
    }


def _read_worker_checkpoint_payload(checkpoint_json_path: Path) -> dict[str, Any]:
    if not checkpoint_json_path.exists():
        return {}
    try:
        raw = json.loads(checkpoint_json_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _remove_worker_checkpoint_image(image_ref: str) -> None:
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


def _commit_worker_container_checkpoint(
    *,
    run_id: str,
    attempt_index: int,
    workspace,
) -> dict[str, Any]:
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id:
        return {"docker_commit_status": "skipped", "reason": "container_id unavailable"}
    if _env_flag_disabled("FEATURE_FACTORY_STAGE2_WORKER_CHECKPOINT_DOCKER_COMMIT"):
        return {
            "docker_commit_status": "skipped",
            "reason": "docker commit disabled by environment",
        }

    safe_run_id = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-")
    image_ref = f"feature-factory/stage2-worker-checkpoint:{safe_run_id}-attempt-{attempt_index:03d}"
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
        "stdout": _truncate_text(completed.stdout.strip(), 2000),
        "stderr": _truncate_text(completed.stderr.strip(), 2000),
    }
    if completed.returncode != 0:
        payload["error"] = completed.stderr.strip() or completed.stdout.strip()
    return payload


def _env_flag_disabled(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"0", "false", "no", "off", "disabled"}


def _prepare_worker_resume_conversation_state(
    *,
    checkpoint: dict[str, Any],
    runtime_env: dict[str, str],
    events_path: Path,
) -> uuid.UUID | None:
    if not checkpoint:
        return None
    conversation_id_raw = str(checkpoint.get("conversation_id") or "").strip()
    if not conversation_id_raw:
        raise RuntimeError("resume checkpoint is missing conversation_id")
    try:
        conversation_id = uuid.UUID(conversation_id_raw)
    except ValueError as exc:
        raise RuntimeError(
            f"resume checkpoint has invalid conversation_id: {conversation_id_raw}"
        ) from exc

    source_conversations_path_raw = str(
        ((checkpoint.get("openhands") or {}).get("conversations_path")) or ""
    ).strip()
    if not source_conversations_path_raw:
        raise RuntimeError("resume checkpoint is missing OpenHands conversations_path")
    source_conversations_path = Path(source_conversations_path_raw).expanduser()
    if not source_conversations_path.exists():
        raise RuntimeError(
            f"resume checkpoint OpenHands conversation path is missing: {source_conversations_path}"
        )
    target_conversations_path_raw = str(
        runtime_env.get("OH_CONVERSATIONS_PATH") or ""
    ).strip()
    if not target_conversations_path_raw:
        raise RuntimeError("resume runtime is missing OH_CONVERSATIONS_PATH")
    target_conversations_path = (
        Path(target_conversations_path_raw).expanduser().resolve()
    )
    target_conversations_path.mkdir(parents=True, exist_ok=True)
    source_conversation_dir = source_conversations_path / conversation_id.hex
    if not source_conversation_dir.exists():
        raise RuntimeError(
            f"resume checkpoint conversation directory is missing: {source_conversation_dir}"
        )
    target_conversation_dir = target_conversations_path / conversation_id.hex
    if target_conversation_dir.exists():
        shutil.rmtree(target_conversation_dir)
    shutil.copytree(source_conversation_dir, target_conversation_dir)
    _make_container_writable(target_conversation_dir, recursive=True)
    rewrite_stage2_json_tree_path_references(
        target_conversation_dir,
        stage2_root=target_conversations_path.parents[4],
        destination_run_id=target_conversations_path.parents[2].name,
    )
    source_bash_events_path_raw = str(
        ((checkpoint.get("openhands") or {}).get("bash_events_dir")) or ""
    ).strip()
    source_bash_events_path = (
        Path(source_bash_events_path_raw).expanduser()
        if source_bash_events_path_raw
        else None
    )
    target_bash_events_path_raw = str(
        runtime_env.get("OH_BASH_EVENTS_DIR") or ""
    ).strip()
    target_bash_events_path = (
        Path(target_bash_events_path_raw).expanduser().resolve()
        if target_bash_events_path_raw
        else None
    )
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
        rewrite_stage2_json_tree_path_references(
            target_bash_events_path,
            stage2_root=target_bash_events_path.parents[4],
            destination_run_id=target_bash_events_path.parents[2].name,
        )
        bash_events_restored = True
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "container_worker",
            "title": "Worker resume state prepared",
            "message": f"Copied OpenHands conversation state for resume attempt {int(checkpoint.get('attempt_index') or 0)}",
            "payload": {
                "conversation_id": str(conversation_id),
                "source_conversation_dir": str(source_conversation_dir),
                "target_conversation_dir": str(target_conversation_dir),
                "source_bash_events_dir": (
                    str(source_bash_events_path)
                    if source_bash_events_path is not None
                    else ""
                ),
                "target_bash_events_dir": (
                    str(target_bash_events_path)
                    if target_bash_events_path is not None
                    else ""
                ),
                "bash_events_restored": bash_events_restored,
                "attempt_index": int(checkpoint.get("attempt_index") or 0),
            },
        },
    )
    return conversation_id


def _resolve_worker_resume_server_image(
    *,
    checkpoint: dict[str, Any],
    events_path: Path,
) -> str | None:
    if not checkpoint:
        return None
    docker_commit_status = str(checkpoint.get("docker_commit_status") or "").strip()
    docker_image_ref = str(checkpoint.get("docker_image_ref") or "").strip()
    if docker_commit_status != "saved" or not docker_image_ref:
        raise RuntimeError(
            "resume checkpoint is missing a saved docker commit image; "
            "exact worker resume is unavailable for this checkpoint"
        )
    inspect = subprocess.run(
        ["docker", "image", "inspect", docker_image_ref],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if inspect.returncode != 0:
        error = (
            inspect.stderr.strip()
            or inspect.stdout.strip()
            or "docker image inspect failed"
        )
        raise RuntimeError(
            f"resume checkpoint docker image is unavailable: {docker_image_ref}: {error}"
        )
    _append_event(
        events_path,
        {
            "actor": "system",
            "phase": "container_worker",
            "title": "Worker resume image verified",
            "message": f"Resuming worker from committed server image {docker_image_ref}",
            "payload": {
                "docker_image_ref": docker_image_ref,
                "docker_commit_status": docker_commit_status,
                "attempt_index": int(checkpoint.get("attempt_index") or 0),
            },
        },
    )
    return docker_image_ref


def _build_llm(*, mode: str, events_path: Path) -> LLM:
    llm_config = build_openhands_llm_config(usage_id=f"stage2-{mode}")
    completions_dir = llm_completion_archive_dir(events_path.parent, mode).resolve()
    completions_dir.mkdir(parents=True, exist_ok=True)
    return LLM(
        usage_id=f"stage2-{mode}",
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.base_url,
        enable_encrypted_reasoning=_openhands_enable_encrypted_reasoning(),
        log_completions=True,
        log_completions_folder=str(completions_dir),
        retry_listener=_build_llm_retry_listener(
            mode=mode,
            model=llm_config.model,
            events_path=events_path,
        ),
        **llm_config.optional_kwargs,
    )


def _release_llm_session(
    *,
    llm: LLM,
    events_path: Path,
    phase: str,
    operation: str,
) -> None:
    session_id = openhands_llm_session_id(llm)
    if not session_id:
        return

    def on_error(error: str) -> None:
        _append_event(
            events_path,
            {
                "actor": "system",
                "phase": phase,
                "title": "OpenHands LLM session release failed",
                "message": (
                    f"Could not release LLM session {session_id}: "
                    f"{_truncate_text(error, 600)}"
                ),
                "payload": {
                    "operation": operation,
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


def _build_llm_retry_listener(*, mode: str, model: str, events_path: Path):
    def listener(
        attempt_number: int, max_attempts: int, exc: BaseException | None
    ) -> None:
        error_type = type(exc).__name__ if exc is not None else "UnknownError"
        error_message = _truncate_text(str(exc or ""), 600)
        next_attempt = int(attempt_number) + 1
        delay_seconds = _estimated_llm_retry_delay_seconds(attempt_number)
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
                    "phase": _phase_for_mode(mode),
                    "title": "OpenHands LLM retrying",
                    "message": message,
                    "payload": {
                        "operation": _llm_retry_operation(mode),
                        "parent_operation": _llm_retry_operation(mode),
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


def _llm_retry_operation(mode: str) -> str:
    return (
        "openhands_llm_retry_worker"
        if mode == "worker"
        else "openhands_llm_retry_planner"
    )


def _estimated_llm_retry_delay_seconds(attempt_number: int) -> float | None:
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


@lru_cache(maxsize=1)
def _stage2_service_session_factory():
    engine = build_engine(get_settings())
    return build_session_factory(engine)


def _with_stage2_service(callback):
    session_factory = _stage2_service_session_factory()
    session = session_factory()
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


def _build_agent(*, llm: LLM, preset: str, mode: str):
    extra_tools: list[Tool] = []
    if mode == "worker":
        import feature_factory.stage2.openhands_validate_tool  # noqa: F401

        extra_tools.append(Tool(name=ValidateTool.name))
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


@contextmanager
def _docker_workspace(
    workspace_dir: Path,
    *,
    base_image: str,
    server_image_override: str | None = None,
    mount_target: str,
    working_dir: str,
    runtime_dir: Path,
    openhands_role: str,
    agent_server_build_timeout_seconds: float | None = None,
    extra_volumes: list[str] | None,
    labels: dict[str, str] | None = None,
    forward_env_names: list[str] | None = None,
    forward_env_updates: dict[str, str] | None = None,
    agent_server_log_callback: Callable[[str], None] | None = None,
    agent_server_cancel_requested: Callable[[], bool] | None = None,
):
    host_workspace_dir = workspace_dir.resolve()
    host_runtime_dir = runtime_dir.resolve()
    _make_container_writable(host_workspace_dir)
    runtime_env = dict(
        forward_env_updates
        or _openhands_server_runtime_env(
            runtime_dir=host_runtime_dir,
            role=openhands_role,
        )
    )
    volumes = [
        f"{host_workspace_dir}:{mount_target}",
        *_openhands_runtime_volume_mounts(
            runtime_env=runtime_env, runtime_dir=host_runtime_dir
        ),
    ]
    if extra_volumes:
        volumes.extend(extra_volumes)
    platform_name = _detect_platform()
    server_image = server_image_override or _ensure_feature_factory_agent_server_image(
        base_image=base_image,
        platform_name=platform_name,
        timeout_seconds=agent_server_build_timeout_seconds,
        log_callback=agent_server_log_callback,
        cancel_requested=agent_server_cancel_requested,
    )
    with _temporary_environ(runtime_env):
        with DockerWorkspace(
            server_image=server_image,
            platform=platform_name,
            volumes=volumes,
            labels=dict(labels or {}),
            working_dir=working_dir,
            forward_env=forward_env_names or FEATURE_FACTORY_OPENHANDS_FORWARD_ENV,
            detach_logs=False,
            extra_ports=False,
        ) as workspace:
            _ensure_openhands_runtime_artifacts_writable(
                runtime_env=runtime_env,
                workspace=workspace,
            )
            yield workspace


def _openhands_runtime_artifact_paths(runtime_env: dict[str, str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for key in ("OH_CONVERSATIONS_PATH", "OH_BASH_EVENTS_DIR"):
        raw_path = str(runtime_env.get(key) or "").strip()
        if not raw_path:
            continue
        path = Path(raw_path).expanduser().resolve()
        _validate_openhands_runtime_artifact_path(key=key, path=path)
        path_key = str(path)
        if path_key in seen:
            continue
        seen.add(path_key)
        paths.append(path)
    return paths


def _validate_openhands_runtime_artifact_path(*, key: str, path: Path) -> None:
    expected_names = {
        "OH_CONVERSATIONS_PATH": "conversations",
        "OH_BASH_EVENTS_DIR": "bash_events",
    }
    expected_name = expected_names[key]
    if len(path.parts) < 3 or path.name != expected_name:
        raise RuntimeError(
            f"{key} must point to a dedicated OpenHands {expected_name} "
            f"directory, got: {path}"
        )


def _ensure_openhands_runtime_artifacts_writable(
    *,
    runtime_env: dict[str, str],
    workspace: Any | None = None,
) -> None:
    artifact_paths = _openhands_runtime_artifact_paths(runtime_env)
    for path in artifact_paths:
        _make_container_writable(path, recursive=True)

    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if not container_id or not artifact_paths:
        return

    repair_script = _openhands_runtime_artifact_permission_script(
        artifact_paths,
        repair=True,
    )
    repair = subprocess.run(
        ["docker", "exec", "--user", "root", container_id, "sh", "-lc", repair_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if repair.returncode != 0:
        raise RuntimeError(
            "failed to repair OpenHands runtime artifact permissions: "
            f"{_truncate_text((repair.stderr or repair.stdout).strip(), 1000)}"
        )

    smoke_script = _openhands_runtime_artifact_permission_script(
        artifact_paths,
        repair=False,
    )
    smoke = subprocess.run(
        ["docker", "exec", container_id, "sh", "-lc", smoke_script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
        check=False,
    )
    if smoke.returncode != 0:
        raise RuntimeError(
            "OpenHands runtime artifact directories are not writable inside the "
            "agent-server container: "
            f"{_truncate_text((smoke.stderr or smoke.stdout).strip(), 1000)}"
        )


def _openhands_runtime_artifact_permission_script(
    paths: list[Path],
    *,
    repair: bool,
) -> str:
    quoted_paths = " ".join(shlex.quote(str(path)) for path in paths)
    repair_commands = 'mkdir -p "$p"; chmod -R a+rwX "$p"; ' if repair else ""
    return (
        "set -u; "
        f"for p in {quoted_paths}; do "
        '[ -n "$p" ] || continue; '
        f"{repair_commands}"
        'check="$p/.feature_factory_permission_check_$$"; '
        ': > "$check"; '
        'rm -f "$check"; '
        "done"
    )


def _openhands_runtime_volume_mounts(
    *, runtime_env: dict[str, str], runtime_dir: Path
) -> list[str]:
    openhands_root_dir = runtime_dir.resolve() / "openhands"
    runtime_paths = _openhands_runtime_artifact_paths(runtime_env)

    if not runtime_paths or all(
        path == openhands_root_dir or openhands_root_dir in path.parents
        for path in runtime_paths
    ):
        _make_container_traversable(openhands_root_dir)
        return [f"{openhands_root_dir}:{openhands_root_dir}"]

    volumes: list[str] = []
    seen: set[str] = set()
    for path in runtime_paths:
        _make_container_writable(path, recursive=True)
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        volumes.append(f"{path}:{path}")
    return volumes


@contextmanager
def _temporary_environ(updates: dict[str, str]):
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update(updates)
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _openhands_server_runtime_env(
    *,
    runtime_dir: Path | None = None,
    role: str = "openhands",
) -> dict[str, str]:
    configured_conversations_path = os.getenv(
        "FEATURE_FACTORY_STAGE2_OPENHANDS_CONVERSATIONS_PATH"
    )
    configured_bash_events_dir = os.getenv(
        "FEATURE_FACTORY_STAGE2_OPENHANDS_BASH_EVENTS_DIR"
    )
    if runtime_dir is None:
        return {
            "OH_CONVERSATIONS_PATH": configured_conversations_path
            or FEATURE_FACTORY_OPENHANDS_CONVERSATIONS_PATH,
            "OH_BASH_EVENTS_DIR": configured_bash_events_dir
            or FEATURE_FACTORY_OPENHANDS_BASH_EVENTS_DIR,
        }

    safe_role = re.sub(r"[^a-zA-Z0-9_.-]+", "-", role).strip("-") or "openhands"
    openhands_root_dir = runtime_dir.resolve() / "openhands"
    openhands_runtime_dir = openhands_root_dir / safe_role
    conversations_path = (
        Path(configured_conversations_path).expanduser().resolve()
        if configured_conversations_path
        else openhands_runtime_dir / "conversations"
    )
    bash_events_dir = (
        Path(configured_bash_events_dir).expanduser().resolve()
        if configured_bash_events_dir
        else openhands_runtime_dir / "bash_events"
    )
    _make_container_traversable(openhands_root_dir)
    _make_container_writable(openhands_runtime_dir)
    _make_container_writable(conversations_path, recursive=True)
    _make_container_writable(bash_events_dir, recursive=True)
    return {
        "OH_CONVERSATIONS_PATH": str(conversations_path),
        "OH_BASH_EVENTS_DIR": str(bash_events_dir),
    }


def _make_container_writable(path: Path, *, recursive: bool = False) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _chmod_container_writable(path)
    if not recursive:
        return
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        _chmod_container_writable(child)


def _make_container_traversable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        desired_mode = (
            current_mode
            | stat.S_IRWXU
            | stat.S_IRGRP
            | stat.S_IXGRP
            | stat.S_IROTH
            | stat.S_IXOTH
        ) & ~(stat.S_IWGRP | stat.S_IWOTH)
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


def _make_container_readable_file(path: Path) -> None:
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        desired_mode = current_mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


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


def _ensure_feature_factory_agent_server_image(
    *,
    base_image: str,
    platform_name: str,
    timeout_seconds: float | None = None,
    log_callback: Callable[[str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    return ensure_agent_server_image_built(
        base_image=base_image,
        platform_name=platform_name,
        timeout_seconds=timeout_seconds,
        log_callback=log_callback,
        cancel_requested=cancel_requested,
    )


def _resolve_agent_server_build_args() -> tuple[str, dict[str, str]]:
    mirror_profile = "cn" if _should_use_china_mirrors() else "default"
    build_args = (
        dict(FEATURE_FACTORY_CHINA_MIRROR_BUILD_ARGS) if mirror_profile == "cn" else {}
    )
    has_explicit_override = False
    for build_arg_name, env_names in FEATURE_FACTORY_STAGE2_BUILD_ARG_ENV_NAMES.items():
        for env_name in env_names:
            value = env_value(env_name)
            if value:
                build_args[build_arg_name] = value
                has_explicit_override = True
                break
    if has_explicit_override:
        fingerprint = hashlib.sha256(
            json.dumps(build_args, sort_keys=True).encode("utf-8")
        ).hexdigest()[:8]
        mirror_profile = f"custom-{fingerprint}"
    return mirror_profile, build_args


def _should_use_china_mirrors() -> bool:
    override = env_value("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS")
    if override is not None:
        return override.strip().lower() in {"1", "true", "yes", "on", "cn", "china"}

    local_timezone = _local_timezone_name()
    if local_timezone in FEATURE_FACTORY_CHINA_TIMEZONES:
        return True

    local_offset = datetime.now().astimezone().utcoffset()
    if local_offset != timedelta(hours=8):
        return False
    return any(name.upper() == "CST" for name in time.tzname)


def _local_timezone_name() -> str:
    tz_env = os.getenv("TZ")
    if tz_env:
        return tz_env.strip().lstrip(":")
    try:
        localtime_target = os.readlink("/etc/localtime")
    except OSError:
        localtime_target = ""
    marker = "zoneinfo/"
    if marker in localtime_target:
        return localtime_target.split(marker, 1)[1].strip()
    tzinfo = datetime.now().astimezone().tzinfo
    return str(tzinfo or "").strip()


def _agent_server_image_tag(
    *,
    base_image: str,
    platform_name: str,
    git_sha: str,
    mirror_profile: str,
) -> str:
    short_sha = git_sha[:7] if git_sha and git_sha != "unknown" else "unknown"
    base_component = _sanitize_image_tag_component(base_image)
    platform_component = _sanitize_image_tag_component(platform_name)
    mirror_component = _sanitize_image_tag_component(mirror_profile)
    return (
        f"{FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY}:"
        f"{short_sha}-{base_component}-{platform_component}-{mirror_component}-agent-server"
    )


def _sanitize_image_tag_component(value: str) -> str:
    component = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_.-").lower()
    if not component:
        return "image"
    component = component[:FEATURE_FACTORY_AGENT_SERVER_TAG_COMPONENT_LIMIT].rstrip(
        "_.-"
    )
    return component or "image"


def _openhands_sdk_root() -> Path:
    configured = os.getenv("FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (_project_root() / "software-agent-sdk").resolve()


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _git_stdout(cwd: Path, *args: str, fallback: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else fallback


def _detect_platform() -> str:
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


def _build_planner_prompt(*, request: dict[str, Any], result_file: Path) -> str:
    repository_payload = {
        "full_name": str((request.get("repository") or {}).get("full_name") or ""),
        "html_url": str((request.get("repository") or {}).get("html_url") or ""),
        "default_branch": str(
            (request.get("repository") or {}).get("default_branch") or ""
        ),
        "target_commit_sha": str(
            (request.get("repository") or {}).get("target_commit_sha") or ""
        ),
        "workspace_repo_path": f"{PLANNER_SANDBOX_HOME}/repo",
    }
    regional_network_context_block = _regional_network_context_block()
    prompt_template = read_text_asset("planner_prompt.md")
    return (
        prompt_template.replace("{{RESULT_FILE}}", str(result_file))
        .replace(
            "{{REPOSITORY_JSON}}",
            json.dumps(repository_payload, ensure_ascii=False, indent=2),
        )
        .replace(
            "{{BASE_IMAGE_CATALOG_JSON}}",
            json.dumps(
                request.get("base_image_catalog") or [], ensure_ascii=False, indent=2
            ),
        )
        .replace("{{REGIONAL_NETWORK_CONTEXT_BLOCK}}", regional_network_context_block)
    )


def _regional_network_context_block() -> str:
    if _should_use_china_mirrors():
        return (
            "Regional network context:\n"
            f"{_render_regional_network_context_cn().strip()}\n\n"
        )
    return ""


def _render_regional_network_context_cn() -> str:
    text = read_text_asset("regional_network_context_cn.md")
    values = _regional_network_context_values()
    for name, value in values.items():
        text = text.replace(f"{{{{{name}}}}}", value)
    return text


def _regional_network_context_values() -> dict[str, str]:
    values = dict(FEATURE_FACTORY_CHINA_REGIONAL_CONTEXT_DEFAULTS)
    _mirror_profile, build_args = _resolve_agent_server_build_args()
    values.update({name: value for name, value in build_args.items() if value})
    for value_name, env_names in FEATURE_FACTORY_REGIONAL_CONTEXT_ENV_NAMES.items():
        for env_name in env_names:
            value = env_value(env_name)
            if value:
                values[value_name] = value
                break
    values["DOCKER_IO_MIRROR"] = docker_io_mirror_host()
    values["GHCR_IO_MIRROR"] = ghcr_io_mirror_host()
    values["GITHUB_PROXY_PREFIX"] = github_proxy_prefix()
    return values


def _ensure_trailing_slash(value: str) -> str:
    stripped = str(value or "").strip()
    return stripped if stripped.endswith("/") else f"{stripped}/"


def _planner_extra_volumes(
    *,
    runtime_dir: Path,
    base_image_catalog: list[Any],
) -> list[str]:
    host_base_images_dir = prepare_base_image_asset_view(
        source_root=asset_root(),
        runtime_dir=runtime_dir,
        base_image_catalog=base_image_catalog,
    )
    return [f"{host_base_images_dir}:{PLANNER_BASE_IMAGES_MOUNT}:ro"]


def _build_worker_prompt(
    *,
    request: dict[str, Any],
    dockerfile_path: Path,
    run_script_path: Path,
    selected_base_image: dict[str, Any],
    selected_base_image_dockerfile_path: str,
    selected_base_image_metadata_path: str,
) -> str:
    planner = dict(request.get("planner_decision") or {})
    repository_payload = {
        "full_name": str((request.get("repository") or {}).get("full_name") or ""),
        "html_url": str((request.get("repository") or {}).get("html_url") or ""),
        "default_branch": str(
            (request.get("repository") or {}).get("default_branch") or ""
        ),
        "target_commit_sha": str(
            (request.get("repository") or {}).get("target_commit_sha") or ""
        ),
        "workspace_repo_path": f"{WORKER_SANDBOX_WORKSPACE}/repo",
    }
    prompt_template = read_text_asset("worker_prompt.md")
    return (
        prompt_template.replace(
            "{{DOCKERFILE_PATH}}",
            f"{WORKER_SANDBOX_WORKSPACE}/{dockerfile_path.name}",
        )
        .replace(
            "{{RUN_SCRIPT_PATH}}",
            f"{WORKER_SANDBOX_WORKSPACE}/{run_script_path.name}",
        )
        .replace(
            "{{REPOSITORY_JSON}}",
            json.dumps(repository_payload, ensure_ascii=False, indent=2),
        )
        .replace(
            "{{SELECTED_BASE_IMAGE_SUMMARY}}",
            selected_base_image_prompt_summary(selected_base_image),
        )
        .replace(
            "{{SELECTED_BASE_IMAGE_REF}}",
            str(selected_base_image.get("image_ref") or ""),
        )
        .replace(
            "{{SELECTED_BASE_IMAGE_DOCKERFILE_PATH}}",
            selected_base_image_dockerfile_path,
        )
        .replace(
            "{{SELECTED_BASE_IMAGE_METADATA_PATH}}",
            selected_base_image_metadata_path,
        )
        .replace("{{PLANNER_GUIDANCE}}", str(planner.get("guidance") or "").strip())
        .replace(
            "{{REGIONAL_NETWORK_CONTEXT_BLOCK}}", _regional_network_context_block()
        )
        .replace("{{VALIDATE_TOOL_NAME}}", ValidateTool.name)
    )


TOKEN_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)


def _event_payload(
    *,
    event,
    mode: str,
    conversation_ref: dict[str, Any],
    archived_completion_file: Path | None = None,
) -> dict[str, Any]:
    actor = "planner_agent" if mode == "planner" else "worker_agent"
    phase = "host_planner" if mode == "planner" else "container_worker"
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


def _llm_completion_event_payload(
    *,
    event: LLMCompletionLogEvent,
    actor: str,
    phase: str,
    conversation_ref: dict[str, Any],
    archived_completion_file: Path | None,
) -> dict[str, Any]:
    log_data = _parse_llm_completion_log(event.log_data)
    delta = _token_usage_from_llm_log(log_data)
    cumulative = _add_tracked_token_usage(conversation_ref, delta)
    model_name = str(getattr(event, "model_name", "") or "").strip() or "unknown"
    total_delta = int(delta.get("total_tokens") or 0)
    total_cumulative = int(cumulative.get("total_tokens") or 0)
    payload = {
        "sdk_event_type": type(event).__name__,
        "llm_completion": {
            "usage_id": getattr(event, "usage_id", None),
            "model_name": model_name,
            "cost": _safe_number(log_data.get("cost")),
            "latency_sec": _safe_number(log_data.get("latency_sec")),
            "archive_file": str(archived_completion_file)
            if archived_completion_file
            else None,
        },
        "token_usage_delta": delta,
        "token_usage": cumulative,
        "token_usage_total": total_cumulative,
    }
    return {
        "actor": actor,
        "phase": phase,
        "title": "LLM token usage",
        "message": (
            f"{model_name} used {total_delta} tokens in this call; "
            f"{total_cumulative} tokens accumulated"
        ),
        "payload": payload,
    }


def _archive_llm_completion_event(
    event,
    *,
    archive_dir: Path,
) -> Path | None:
    if not isinstance(event, LLMCompletionLogEvent):
        return None
    filename = _safe_completion_log_filename(str(event.filename or ""))
    if not filename:
        filename = f"llm-completion-{int(time.time() * 1000)}.json"
    try:
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / filename
        target.write_text(str(event.log_data or ""), encoding="utf-8")
        return target
    except Exception:
        return None


def _safe_completion_log_filename(filename: str) -> str:
    name = Path(filename).name.strip()
    if not name:
        return ""
    if not name.endswith(".json"):
        name = f"{name}.json"
    return name


def _append_llm_completion_archive_event(
    events_path: Path,
    *,
    mode: str,
    archive_dir: Path,
) -> None:
    summary = summarize_llm_completion_archive(events_path.parent, mode)
    actor = "planner_agent" if mode == "planner" else "worker_agent"
    phase = "host_planner" if mode == "planner" else "container_worker"
    file_count = int(summary.get("file_count") or 0)
    title_role = "Planner" if mode == "planner" else "Worker"
    _append_event(
        events_path,
        {
            "actor": actor,
            "phase": phase,
            "title": f"{title_role} raw LLM completions archived",
            "message": (
                f"Archived {file_count} OpenHands LLM completion files at {archive_dir}"
                if file_count
                else f"No OpenHands LLM completion files were archived at {archive_dir}"
            ),
            "payload": {
                "operation": _bridge_operation(mode),
                "archive": summary,
            },
        },
    )


def _compact_openhands_event_payload(event) -> dict[str, Any]:
    data = event.model_dump(mode="json")
    event_type = type(event).__name__
    payload: dict[str, Any] = {"sdk_event_type": event_type}
    source = getattr(event, "source", None)
    if source:
        payload["source"] = source

    if event_type == "SystemPromptEvent":
        payload.update(_system_prompt_event_payload(data))
        return payload
    if event_type == "ConversationStateUpdateEvent":
        payload.update(_conversation_state_event_payload(data))
        return payload
    if isinstance(event, ActionEvent):
        payload.update(_action_event_payload(data))
        return payload
    if isinstance(event, ObservationEvent):
        payload.update(_observation_event_payload(data))
        return payload
    if isinstance(event, MessageEvent):
        payload.update(_message_event_payload(data))
        return payload

    payload["event"] = _sanitize_openhands_data(data, limit=2000)
    return payload


def _system_prompt_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    system_prompt = data.get("system_prompt")
    if isinstance(system_prompt, dict):
        payload["system_prompt"] = {
            key: system_prompt.get(key)
            for key in ("type", "cache_prompt", "text")
            if key in system_prompt
        }
    tools = data.get("tools")
    if isinstance(tools, list):
        payload["tools"] = [
            {
                key: tool.get(key)
                for key in (
                    "title",
                    "kind",
                    "action_type",
                    "observation_type",
                    "description",
                )
                if isinstance(tool, dict) and key in tool and tool.get(key) is not None
            }
            for tool in tools
            if isinstance(tool, dict)
        ]
    return payload


def _conversation_state_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    state_key = data.get("key")
    value = data.get("value")
    if not isinstance(value, dict):
        return {"value": _sanitize_openhands_data(value, limit=2000)}

    if state_key == "stats":
        token_stats = _conversation_state_token_stats(value)
        return {"stats": token_stats} if token_stats else {}

    agent = value.get("agent")
    llm = agent.get("llm") if isinstance(agent, dict) else {}
    tools = agent.get("tools") if isinstance(agent, dict) else []
    workspace = value.get("workspace")
    stats = value.get("stats")
    payload: dict[str, Any] = {}

    if isinstance(llm, dict) or isinstance(tools, list):
        compact_agent: dict[str, Any] = {}
        if isinstance(llm, dict):
            for source_key, target_key in (
                ("model", "model"),
                ("base_url", "base_url"),
                ("reasoning_effort", "reasoning_effort"),
                ("max_input_tokens", "max_input_tokens"),
                ("max_output_tokens", "max_output_tokens"),
            ):
                value_item = llm.get(source_key)
                if value_item not in (None, ""):
                    compact_agent[target_key] = value_item
        if isinstance(tools, list):
            tool_names = [
                str(tool.get("name") or tool.get("title") or "").strip()
                for tool in tools
                if isinstance(tool, dict)
            ]
            compact_agent["tools"] = [name for name in tool_names if name]
        max_iterations = value.get("max_iterations")
        if max_iterations is not None:
            compact_agent["max_iterations"] = max_iterations
        if compact_agent:
            payload["agent"] = compact_agent

    if isinstance(workspace, dict):
        compact_workspace = {
            key: workspace.get(key)
            for key in ("working_dir", "kind")
            if workspace.get(key) not in (None, "")
        }
        if compact_workspace:
            payload["workspace"] = compact_workspace

    token_stats = _conversation_state_token_stats(stats)
    if token_stats:
        payload["stats"] = token_stats
    return payload


def _conversation_state_token_stats(stats: Any) -> dict[str, Any]:
    if not isinstance(stats, dict):
        return {}
    usage_to_metrics = stats.get("usage_to_metrics")
    if not isinstance(usage_to_metrics, dict):
        return {}
    compact: dict[str, Any] = {}
    for usage_id, metrics in usage_to_metrics.items():
        if not isinstance(metrics, dict):
            continue
        token_usage = metrics.get("accumulated_token_usage")
        if isinstance(token_usage, dict):
            compact[str(usage_id)] = _sanitize_openhands_data(token_usage, limit=2000)
    return compact


def _action_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("tool_name", "summary", "security_risk", "reasoning_content"):
        value = data.get(key)
        if value not in (None, ""):
            payload[key] = value

    action = data.get("action")
    if isinstance(action, dict):
        compact_action = {
            key: action.get(key)
            for key in ("kind", "command", "timeout", "is_input", "reset")
            if action.get(key) is not None
        }
        if compact_action:
            payload["action"] = _sanitize_openhands_data(compact_action, limit=4000)
    return payload


def _observation_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    tool_name = data.get("tool_name")
    if tool_name:
        payload["tool_name"] = tool_name

    observation = data.get("observation")
    if not isinstance(observation, dict):
        return payload

    for key in ("command", "exit_code", "timeout", "is_error"):
        value = observation.get(key)
        if value is not None:
            payload[key] = value

    metadata = observation.get("metadata")
    if isinstance(metadata, dict):
        working_dir = metadata.get("working_dir")
        if working_dir:
            payload["working_dir"] = working_dir

    text = _observation_text(observation)
    if text:
        payload["output"] = _text_preview_payload(text, limit=2000)
    return payload


def _message_event_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key in ("role", "source"):
        value = data.get(key)
        if value not in (None, ""):
            payload[key] = value
    reasoning_content = data.get("reasoning_content")
    if reasoning_content not in (None, ""):
        payload["reasoning_content"] = reasoning_content
    for key in ("content", "message"):
        value = data.get(key)
        if value not in (None, ""):
            payload["content"] = value
            break
    if "content" not in payload:
        payload["event"] = _sanitize_openhands_data(data, limit=4000)
    return payload


def _observation_text(observation: dict[str, Any]) -> str:
    content = observation.get("content")
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip()


def _text_preview_payload(text: str, *, limit: int) -> dict[str, Any]:
    if len(text) <= limit:
        return {"preview": text, "truncated": False}
    half = max(1, limit // 2)
    return {
        "preview": text[:half],
        "tail": text[-half:],
        "truncated": True,
    }


def _sanitize_openhands_data(value: Any, *, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, limit)
    if isinstance(value, list):
        return [_sanitize_openhands_data(item, limit=limit) for item in value[:20]]
    if isinstance(value, dict):
        ignored_keys = {
            "id",
            "timestamp",
            "kind",
            "event_id",
            "action_id",
            "tool_call_id",
            "conversation_id",
            "execution_status",
            "state_key",
            "api_key",
            "secret_registry",
        }
        return {
            str(key): _sanitize_openhands_data(item, limit=limit)
            for key, item in list(value.items())[:40]
            if str(key) not in ignored_keys and item is not None
        }
    return value


def _event_summary(event) -> tuple[str, str]:
    if isinstance(event, ActionEvent):
        summary = str(event.summary or event.tool_name or "agent action").strip()
        return f"Action: {event.tool_name}", _truncate_text(summary, 400)
    if isinstance(event, ObservationEvent):
        return f"Observation: {event.tool_name}", _truncate_text(str(event), 400)
    if isinstance(event, MessageEvent):
        return f"Message ({event.source})", _truncate_text(str(event), 400)
    return type(event).__name__, _truncate_text(str(event), 400)


def _conversation_metrics_payload(conversation) -> dict[str, Any]:
    if conversation is None:
        return {}
    state = getattr(conversation, "state", None)
    refresh_from_server = getattr(state, "refresh_from_server", None)
    if callable(refresh_from_server):
        try:
            # Stats streaming is best-effort in OpenHands; pull authoritative state
            # so each observed event can carry the latest cumulative token count.
            refresh_from_server()
        except Exception:
            pass
    return _token_usage_payload(conversation)


def _token_usage_from_event_payload(payload: dict[str, Any]) -> dict[str, int]:
    stats = payload.get("stats")
    if not isinstance(stats, dict):
        return {}
    usage = _empty_token_usage()
    for metrics in stats.values():
        if not isinstance(metrics, dict):
            continue
        for key in TOKEN_USAGE_KEYS:
            usage[key] += _int_from_any(metrics.get(key))
    usage["total_tokens"] = _token_usage_total(usage)
    return usage if _has_token_usage(usage) else {}


def _tracked_token_usage(conversation_ref: dict[str, Any]) -> dict[str, Any]:
    usage = conversation_ref.get("token_usage")
    if not isinstance(usage, dict):
        return {}
    return {
        key: int(usage.get(key, 0) or 0) for key in (*TOKEN_USAGE_KEYS, "total_tokens")
    }


def _add_tracked_token_usage(
    conversation_ref: dict[str, Any],
    delta: dict[str, Any],
) -> dict[str, Any]:
    current = _tracked_token_usage(conversation_ref)
    if not current:
        current = _empty_token_usage()
    for key in TOKEN_USAGE_KEYS:
        current[key] = int(current.get(key, 0) or 0) + int(delta.get(key, 0) or 0)
    current["total_tokens"] = _token_usage_total(current)
    conversation_ref["token_usage"] = current
    return dict(current)


def _empty_token_usage() -> dict[str, int]:
    payload = {key: 0 for key in TOKEN_USAGE_KEYS}
    payload["total_tokens"] = 0
    return payload


def _parse_llm_completion_log(log_data: Any) -> dict[str, Any]:
    if isinstance(log_data, dict):
        return log_data
    if not isinstance(log_data, str) or not log_data.strip():
        return {}
    try:
        payload = json.loads(log_data)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _token_usage_from_llm_log(log_data: dict[str, Any]) -> dict[str, int]:
    usage = log_data.get("usage_summary")
    if not isinstance(usage, dict):
        response = log_data.get("response")
        if isinstance(response, dict):
            usage = response.get("usage")
    if not isinstance(usage, dict):
        return _empty_token_usage()
    payload = {
        "prompt_tokens": _int_from_any(
            usage.get("prompt_tokens", usage.get("input_tokens", 0))
        ),
        "completion_tokens": _int_from_any(
            usage.get("completion_tokens", usage.get("output_tokens", 0))
        ),
        "cache_read_tokens": _int_from_any(
            usage.get("cache_read_tokens")
            or _nested_usage_int(
                usage,
                ("prompt_tokens_details", "input_tokens_details"),
                "cached_tokens",
            )
        ),
        "cache_write_tokens": _int_from_any(
            usage.get("cache_write_tokens") or usage.get("_cache_creation_input_tokens")
        ),
        "reasoning_tokens": _int_from_any(
            usage.get("reasoning_tokens")
            or _nested_usage_int(
                usage,
                ("completion_tokens_details", "output_tokens_details"),
                "reasoning_tokens",
            )
        ),
    }
    payload["total_tokens"] = _token_usage_total(payload)
    return payload


def _nested_usage_int(
    payload: dict[str, Any],
    container_keys: tuple[str, ...],
    value_key: str,
) -> int:
    for container_key in container_keys:
        container = payload.get(container_key)
        if isinstance(container, dict) and value_key in container:
            return _int_from_any(container.get(value_key))
    return 0


def _safe_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return int(number) if number.is_integer() else number
    return None


def _int_from_any(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _token_usage_total(payload: dict[str, Any]) -> int:
    return sum(int(payload.get(key, 0) or 0) for key in TOKEN_USAGE_KEYS)


def _has_token_usage(payload: dict[str, Any]) -> bool:
    return bool(payload) and _token_usage_total(payload) > 0


def _conversation_execution_status(
    conversation,
    *,
    refresh: bool = False,
) -> ConversationExecutionStatus:
    state = getattr(conversation, "state", None)
    if state is None:
        raise RuntimeError("conversation state is unavailable")
    if refresh:
        refresh_from_server = getattr(state, "refresh_from_server", None)
        if callable(refresh_from_server):
            refresh_from_server()
    status = getattr(state, "execution_status", None)
    if isinstance(status, ConversationExecutionStatus):
        return status
    return ConversationExecutionStatus(str(status or "idle"))


def _append_conversation_failure_event(
    *,
    events_path: Path,
    mode: str,
    title: str,
    message_prefix: str,
    conversation_ref: dict[str, Any],
    exc: BaseException,
) -> dict[str, Any]:
    context = _conversation_failure_context(conversation_ref=conversation_ref, exc=exc)
    actor = "planner_agent" if mode == "planner" else "worker_agent"
    phase = "host_planner" if mode == "planner" else "container_worker"
    _append_event(
        events_path,
        {
            "actor": actor,
            "phase": phase,
            "title": title,
            "message": _conversation_failure_message(message_prefix, context, exc),
            "payload": {
                "operation": _bridge_operation(mode),
                **context,
            },
        },
    )
    return context


def _conversation_failure_context(
    *,
    conversation_ref: dict[str, Any],
    exc: BaseException,
    include_docker_logs: bool = True,
) -> dict[str, Any]:
    conversation = conversation_ref.get("conversation")
    workspace = conversation_ref.get("workspace")
    state = getattr(conversation, "state", None)
    refresh_payload = _refresh_conversation_debug_state(state)
    events = _conversation_events_snapshot(state)
    error_events = [
        event_payload
        for event_payload in (
            _conversation_error_event_payload(event) for event in events
        )
        if event_payload
    ]
    recent_events = [_conversation_debug_event_payload(event) for event in events[-10:]]
    last_error_detail = _conversation_last_error_detail(
        conversation=conversation,
        error_events=error_events,
    )
    context: dict[str, Any] = {
        "conversation_id": _conversation_id_from_state_or_exception(state, exc),
        "execution_status": _conversation_status_string(state),
        "exception": _exception_payload(exc),
        "refresh": refresh_payload,
        "last_error_detail": last_error_detail,
        "error_events": error_events[-5:],
        "recent_events": recent_events,
    }
    container_id = str(getattr(workspace, "_container_id", "") or "").strip()
    if container_id:
        context["container_id"] = container_id
        if include_docker_logs:
            context["agent_server_logs"] = _docker_logs_tail(container_id)
    return _truncate_data(context, limit=8000)


def _refresh_conversation_debug_state(state: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state_refresh": "skipped",
        "event_reconcile": "skipped",
    }
    if state is None:
        payload["reason"] = "conversation state unavailable"
        return payload
    refresh_from_server = getattr(state, "refresh_from_server", None)
    if callable(refresh_from_server):
        try:
            refresh_from_server()
            payload["state_refresh"] = "ok"
        except Exception as exc:
            payload["state_refresh"] = "failed"
            payload["state_refresh_error"] = _truncate_text(str(exc), 1000)
    events = getattr(state, "events", None)
    reconcile = getattr(events, "reconcile", None)
    if callable(reconcile):
        try:
            payload["event_reconcile_added"] = reconcile()
            payload["event_reconcile"] = "ok"
        except Exception as exc:
            payload["event_reconcile"] = "failed"
            payload["event_reconcile_error"] = _truncate_text(str(exc), 1000)
    try:
        payload["event_count"] = len(events) if events is not None else 0
    except Exception:
        pass
    return payload


def _conversation_events_snapshot(state: Any) -> list[Any]:
    if state is None:
        return []
    events = getattr(state, "events", None)
    if events is None:
        return []
    try:
        return list(events)
    except Exception:
        return []


def _conversation_debug_event_payload(event: Any) -> dict[str, Any]:
    event_type = type(event).__name__
    if isinstance(event, LLMCompletionLogEvent):
        return {
            "sdk_event_type": event_type,
            "usage_id": getattr(event, "usage_id", None),
            "model_name": getattr(event, "model_name", None),
            "filename": getattr(event, "filename", None),
        }
    try:
        title, message = _event_summary(event)
    except Exception:
        title, message = event_type, _truncate_text(str(event), 400)
    try:
        payload = _compact_openhands_event_payload(event)
    except Exception:
        payload = {"sdk_event_type": event_type}
        model_dump = getattr(event, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump(mode="json")
            except TypeError:
                dumped = model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                payload["event"] = _sanitize_openhands_data(dumped, limit=2000)
    return {
        "sdk_event_type": event_type,
        "title": title,
        "message": message,
        "payload": _truncate_data(payload, limit=3000),
    }


def _conversation_error_event_payload(event: Any) -> dict[str, Any] | None:
    detail = _conversation_error_detail_from_event(event)
    if not detail:
        return None
    payload = _conversation_debug_event_payload(event)
    payload["error_detail"] = detail
    return payload


def _conversation_error_detail_from_event(event: Any) -> str:
    event_type = type(event).__name__
    code = _string_attr(event, "code")
    detail = _string_attr(event, "detail")
    error = _string_attr(event, "error")
    dumped: dict[str, Any] = {}
    model_dump = getattr(event, "model_dump", None)
    if callable(model_dump):
        try:
            raw = model_dump(mode="json")
        except TypeError:
            raw = model_dump()
        except Exception:
            raw = {}
        if isinstance(raw, dict):
            dumped = raw
    code = code or str(dumped.get("code") or "").strip()
    detail = detail or str(dumped.get("detail") or "").strip()
    error = error or str(dumped.get("error") or "").strip()
    if event_type == "ConversationErrorEvent" or code or detail:
        message = f"{code}: {detail}" if code and detail else detail or code
        return _truncate_text(message, 4000)
    if event_type == "AgentErrorEvent" or error:
        return _truncate_text(error, 4000)
    return ""


def _conversation_last_error_detail(
    *,
    conversation: Any,
    error_events: list[dict[str, Any]],
) -> str:
    get_last_error_detail = getattr(conversation, "_get_last_error_detail", None)
    if callable(get_last_error_detail):
        try:
            detail = str(get_last_error_detail() or "").strip()
        except Exception:
            detail = ""
        if detail:
            return _truncate_text(detail, 4000)
    for event_payload in reversed(error_events):
        detail = str(event_payload.get("error_detail") or "").strip()
        if detail:
            return _truncate_text(detail, 4000)
    return ""


def _conversation_id_from_state_or_exception(state: Any, exc: BaseException) -> str:
    try:
        state_id = str(getattr(state, "id", "") or "").strip()
    except Exception:
        state_id = ""
    if state_id:
        return state_id
    exc_id = str(getattr(exc, "conversation_id", "") or "").strip()
    return exc_id


def _conversation_status_string(state: Any) -> str:
    try:
        status = getattr(state, "execution_status", "") if state is not None else ""
    except Exception as exc:
        return f"unavailable: {_truncate_text(str(exc), 500)}"
    if isinstance(status, ConversationExecutionStatus):
        return status.value
    return str(status or "").strip()


def _exception_payload(exc: BaseException) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": _truncate_text(str(exc), 4000),
    }
    original = getattr(exc, "original_exception", None)
    if original is not None and original is not exc:
        payload["original_exception"] = {
            "type": type(original).__name__,
            "message": _truncate_text(str(original), 4000),
        }
    conversation_id = str(getattr(exc, "conversation_id", "") or "").strip()
    if conversation_id:
        payload["conversation_id"] = conversation_id
    persistence_dir = str(getattr(exc, "persistence_dir", "") or "").strip()
    if persistence_dir:
        payload["persistence_dir"] = persistence_dir
    return payload


def _docker_logs_tail(container_id: str) -> dict[str, Any]:
    command = ["docker", "logs", "--tail", "200", container_id]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except Exception as exc:
        return {
            "status": "failed",
            "command": command,
            "error": _truncate_text(str(exc), 1000),
        }
    return {
        "status": "ok" if completed.returncode == 0 else "failed",
        "command": command,
        "returncode": completed.returncode,
        "stdout": _text_preview_payload(completed.stdout.strip(), limit=6000)
        if completed.stdout.strip()
        else {},
        "stderr": _text_preview_payload(completed.stderr.strip(), limit=6000)
        if completed.stderr.strip()
        else {},
    }


def _conversation_failure_message(
    prefix: str,
    context: dict[str, Any],
    exc: BaseException,
) -> str:
    detail = str(context.get("last_error_detail") or "").strip()
    if not detail:
        original = getattr(exc, "original_exception", None)
        detail = str(original or exc).strip()
    log_line = _last_agent_server_log_line(context)
    message = f"{prefix}: {detail or type(exc).__name__}"
    if log_line:
        message = f"{message} | agent-server log tail: {log_line}"
    return _truncate_text(message, 1800)


def _last_agent_server_log_line(context: dict[str, Any]) -> str:
    logs = context.get("agent_server_logs")
    if not isinstance(logs, dict):
        return ""
    for stream_name in ("stderr", "stdout"):
        stream = logs.get(stream_name)
        if not isinstance(stream, dict):
            continue
        text = str(stream.get("tail") or stream.get("preview") or "").strip()
        if not text:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if lines:
            return _truncate_text(lines[-1], 500)
    return ""


def _string_attr(value: Any, name: str) -> str:
    return str(getattr(value, name, "") or "").strip()


def _token_usage_payload(conversation) -> dict[str, Any]:
    metrics = conversation.conversation_stats.get_combined_metrics()
    accumulated = metrics.accumulated_token_usage
    if accumulated is None:
        return {}
    payload = {
        "prompt_tokens": int(accumulated.prompt_tokens or 0),
        "completion_tokens": int(accumulated.completion_tokens or 0),
        "cache_read_tokens": int(accumulated.cache_read_tokens or 0),
        "cache_write_tokens": int(accumulated.cache_write_tokens or 0),
        "reasoning_tokens": int(accumulated.reasoning_tokens or 0),
        "total_tokens": int(
            (accumulated.prompt_tokens or 0)
            + (accumulated.completion_tokens or 0)
            + (accumulated.cache_read_tokens or 0)
            + (accumulated.cache_write_tokens or 0)
            + (accumulated.reasoning_tokens or 0)
        ),
    }
    return payload if _has_token_usage(payload) else {}


def _validate_planner_result(
    payload: dict[str, Any], *, request: dict[str, Any]
) -> None:
    status = str(payload.get("status") or "").strip()
    if status not in {"ready", "abandoned", "defect"}:
        raise RuntimeError("planner result has invalid status")

    if status == "ready":
        selected_base_image_id = str(
            payload.get("selected_base_image_id") or ""
        ).strip()
        if not selected_base_image_id:
            raise RuntimeError("planner result is missing selected_base_image_id")
        catalog_ids = {
            str(item.get("image_id") or "")
            for item in request.get("base_image_catalog") or []
        }
        if selected_base_image_id not in catalog_ids:
            raise RuntimeError(
                "planner selected_base_image_id is not in the provided base image catalog"
            )
        guidance = str(payload.get("guidance") or "").strip()
        if not guidance:
            raise RuntimeError("planner result is missing guidance")
        return

    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise RuntimeError("planner result is missing reason")
    if status == "defect":
        upd_dockerfile = str(payload.get("upd_dockerfile") or "").strip()
        if not upd_dockerfile:
            raise RuntimeError("planner defect result is missing upd_dockerfile")


def _load_json(
    path: Path, *, label: str, required: bool = True
) -> dict[str, Any] | None:
    if not path.exists():
        if required:
            raise RuntimeError(f"{label} file was not created: {path}")
        return None
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise RuntimeError(f"{label} file must contain a JSON object")
    return payload


def _append_event(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _truncate_data(value: Any, *, limit: int) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, limit)
    if isinstance(value, list):
        return [_truncate_data(item, limit=limit) for item in value[:20]]
    if isinstance(value, dict):
        return {
            str(key): _truncate_data(item, limit=limit)
            for key, item in list(value.items())[:40]
        }
    return value


def _truncate_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--events", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
