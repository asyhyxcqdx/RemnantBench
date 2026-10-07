from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from feature_factory.docker_mirrors import (
    dockerfile_with_mirrored_from_images,
    env_value,
    mirror_docker_image_ref,
    should_use_china_docker_mirrors,
)
from feature_factory.stage2.assets import asset_root
from feature_factory.stage2.base_images import (
    FEATURE_FACTORY_STAGE2_KIND_LABEL,
    FEATURE_FACTORY_STAGE2_LABEL_PREFIX,
    FEATURE_FACTORY_STAGE2_PLATFORM_LABEL,
    base_image_build_labels,
    cleanup_replaced_docker_image,
    docker_image_references,
    ensure_base_image_built,
    list_base_images,
    remove_docker_image_references,
    run_logged_subprocess,
    should_use_china_mirrors,
)


FEATURE_FACTORY_PROJECT_ROOT = Path(__file__).resolve().parents[3]
FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE = asset_root() / "openhands_agent_server.Dockerfile"
FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY = "feature-factory/openhands-agent-server"
FEATURE_FACTORY_AGENT_SERVER_TAG_COMPONENT_LIMIT = 72
FEATURE_FACTORY_STAGE2_HOST_SDK_PYTHON_VERSION = "3.13"
FEATURE_FACTORY_STAGE2_HOST_SDK_PREWARM_MARKER = (
    FEATURE_FACTORY_PROJECT_ROOT / "data" / "stage2" / "cache" / "software-agent-sdk-prewarm.json"
)
FEATURE_FACTORY_STAGE2_PLANNER_AGENT_SERVER_PREWARM_MARKER = (
    FEATURE_FACTORY_PROJECT_ROOT / "data" / "stage2" / "cache" / "planner-agent-server-prewarm.json"
)
FEATURE_FACTORY_STAGE2_HOST_SDK_RUNTIME_CONFIG_DIR = (
    FEATURE_FACTORY_PROJECT_ROOT / "data" / "stage2" / "cache" / "software-agent-sdk-runtime"
)
FEATURE_FACTORY_STAGE2_IMAGE_ASSET_CACHE_DIR = (
    FEATURE_FACTORY_PROJECT_ROOT / "data" / "stage2" / "cache" / "image-assets"
)
AGENT_SERVER_BUILDER_BASE_IMAGE = "python:3.13-bookworm"
PLANNER_AGENT_SERVER_BASE_IMAGE = "nikolaik/python-nodejs:python3.13-nodejs22-slim"
FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND = "openhands-agent-server"
FEATURE_FACTORY_STAGE2_AGENT_SERVER_DOCKERFILE_SHA_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.agent-server-dockerfile-sha256"
)
FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.base-image-ref"
)
FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_ID_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.base-image-id"
)
FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_SHA_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.sdk-git-sha"
)
FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_REF_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.sdk-git-ref"
)
FEATURE_FACTORY_STAGE2_AGENT_SERVER_MIRROR_PROFILE_LABEL = (
    f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.mirror-profile"
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
    "DEBIAN_SECURITY_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_DEBIAN_SECURITY_APT_MIRROR",),
    "DEBIAN_APT_MIRROR_BOOTSTRAP": ("FEATURE_FACTORY_STAGE2_DEBIAN_APT_MIRROR_BOOTSTRAP",),
    "DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP",
    ),
    "UBUNTU_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_UBUNTU_APT_MIRROR",),
    "UBUNTU_PORTS_APT_MIRROR": ("FEATURE_FACTORY_STAGE2_UBUNTU_PORTS_APT_MIRROR",),
    "UBUNTU_APT_MIRROR_BOOTSTRAP": ("FEATURE_FACTORY_STAGE2_UBUNTU_APT_MIRROR_BOOTSTRAP",),
    "UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP": (
        "FEATURE_FACTORY_STAGE2_UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP",
    ),
}


def stage2_image_asset_status(*, platform_name: str | None = None) -> dict[str, Any]:
    platform_name = platform_name or detect_platform()
    sdk = agent_server_sdk_metadata()
    git_sha = str(sdk.get("git_sha") or "unknown")
    mirror_profile, _mirror_build_args = resolve_agent_server_build_args()
    rows: list[dict[str, Any]] = []
    for base_payload in stage2_image_asset_payloads():
        asset_key = str(base_payload.get("asset_key") or "").strip()
        image_ref = str(base_payload.get("image_ref") or "").strip()
        asset_path = str(base_payload.get("asset_path") or "").strip()
        dockerfile_path = (asset_root() / asset_path).resolve() if asset_path else None
        base_status = base_image_status(
            asset_key=asset_key,
            image_ref=image_ref,
            dockerfile_path=dockerfile_path,
            platform_name=platform_name,
            base_image_payload=base_payload,
        )
        agent_image_ref = agent_server_image_tag(
            base_image=image_ref,
            platform_name=platform_name,
            git_sha=git_sha,
            mirror_profile=mirror_profile,
        )
        agent_status = agent_server_image_status(
            image_ref=agent_image_ref,
            base_image_ref=image_ref,
            platform_name=platform_name,
            git_sha=git_sha,
            git_ref=str(sdk.get("git_ref") or "unknown"),
            mirror_profile=mirror_profile,
        )
        rows.append(
            {
                "asset_key": asset_key,
                "image_id": base_payload.get("image_id"),
                "image_ref": image_ref,
                "asset_path": asset_path,
                "language_pack_family": base_payload.get("language_pack_family"),
                "runtime_family": base_payload.get("runtime_family"),
                "runtime_version": base_payload.get("runtime_version"),
                "os_family": base_payload.get("os_family"),
                "network_profile": base_payload.get("network_profile"),
                "description": base_payload.get("description"),
                "base_image": base_status,
                "agent_server_image": {
                    **agent_status,
                    "image_ref": agent_image_ref,
                    "base_image_ref": image_ref,
                },
            }
        )
    return {
        "platform": platform_name,
        "mirror_profile": mirror_profile,
        "sdk": sdk,
        "rows": rows,
    }


def stage2_sdk_prewarm_status(*, platform_name: str | None = None) -> dict[str, Any]:
    resolved_platform = platform_name or detect_platform()
    sdk = agent_server_sdk_metadata()
    mirror_profile, _mirror_build_args = resolve_agent_server_build_args()
    return {
        "platform": resolved_platform,
        "mirror_profile": mirror_profile,
        "sdk": sdk,
        "host_environment": host_sdk_environment_status(sdk=sdk),
        "planner": planner_agent_server_prewarm_status(
            sdk=sdk,
            platform_name=resolved_platform,
            mirror_profile=mirror_profile,
        ),
    }


def host_sdk_environment_status(*, sdk: dict[str, Any] | None = None) -> dict[str, Any]:
    sdk = dict(sdk or agent_server_sdk_metadata())
    fingerprint = host_sdk_environment_fingerprint(sdk=sdk)
    marker = read_host_sdk_prewarm_marker()
    marker_fingerprint = marker.get("fingerprint") if isinstance(marker.get("fingerprint"), dict) else {}
    last_status = str(marker.get("status") or "").strip()
    prewarmed = bool(last_status == "passed" and marker_fingerprint == fingerprint)
    return {
        "python_version": FEATURE_FACTORY_STAGE2_HOST_SDK_PYTHON_VERSION,
        "prewarmed": prewarmed,
        "needs_update": bool(marker and not prewarmed),
        "last_status": last_status,
        "last_prewarmed_at": marker.get("prewarmed_at") or None,
        "last_error": marker.get("error") or "",
        "log_tail": marker_log_tail(marker),
    }


def host_sdk_environment_fingerprint(*, sdk: dict[str, Any] | None = None) -> dict[str, Any]:
    sdk = dict(sdk or agent_server_sdk_metadata())
    sdk_root = Path(str(sdk.get("root") or openhands_sdk_root())).expanduser().resolve()
    return {
        "sdk_root": str(sdk_root),
        "sdk_git_sha": sdk.get("git_sha") or "unknown",
        "project_root": str(project_root()),
        "python_version": FEATURE_FACTORY_STAGE2_HOST_SDK_PYTHON_VERSION,
        "sdk_uv_lock_sha256": file_sha256(sdk_root / "uv.lock"),
        "project_pyproject_sha256": file_sha256(project_root() / "pyproject.toml"),
    }


def host_sdk_prewarm_command() -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        str(openhands_sdk_root()),
        "--with-editable",
        str(project_root()),
        "--frozen",
        "--python",
        FEATURE_FACTORY_STAGE2_HOST_SDK_PYTHON_VERSION,
        "python",
        "-c",
        (
            "import feature_factory.stage2.openhands_bridge; "
            "import openhands.sdk; "
            "import openhands.tools; "
            "import openhands.workspace; "
            "print('software-agent-sdk host environment ready')"
        ),
    ]


def host_sdk_runtime_env_overrides() -> dict[str, str]:
    _mirror_profile, build_args = resolve_agent_server_build_args()
    overrides: dict[str, str] = {}

    pip_index_url = str(build_args.get("PIP_INDEX_URL") or "").strip()
    uv_index_url = str(build_args.get("UV_INDEX_URL") or "").strip()
    effective_uv_index_url = uv_index_url or pip_index_url
    uv_python_install_mirror = str(build_args.get("UV_PYTHON_INSTALL_MIRROR") or "").strip()
    uv_http_timeout = str(build_args.get("UV_HTTP_TIMEOUT") or "").strip()
    uv_http_retries = str(build_args.get("UV_HTTP_RETRIES") or "").strip()
    torch_cpu_find_links = str(build_args.get("TORCH_CPU_FIND_LINKS") or "").strip()

    if pip_index_url:
        overrides["PIP_INDEX_URL"] = pip_index_url
    if effective_uv_index_url:
        overrides["UV_INDEX_URL"] = effective_uv_index_url
        overrides["UV_DEFAULT_INDEX"] = effective_uv_index_url
    if uv_python_install_mirror:
        overrides["UV_PYTHON_INSTALL_MIRROR"] = uv_python_install_mirror
    if uv_http_timeout:
        overrides["UV_HTTP_TIMEOUT"] = uv_http_timeout
        overrides["UV_REQUEST_TIMEOUT"] = uv_http_timeout
    if uv_http_retries:
        overrides["UV_HTTP_RETRIES"] = uv_http_retries
    if torch_cpu_find_links:
        overrides["TORCH_CPU_FIND_LINKS"] = torch_cpu_find_links

    return overrides


def _write_host_sdk_uv_config(
    *,
    pip_index_url: str,
    uv_index_url: str,
    uv_python_install_mirror: str,
) -> str:
    config_dir = FEATURE_FACTORY_STAGE2_HOST_SDK_RUNTIME_CONFIG_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "uv.toml"

    effective_uv_index_url = uv_index_url or pip_index_url
    lines: list[str] = []
    if uv_python_install_mirror:
        lines.append(f'python-install-mirror = "{uv_python_install_mirror}"')
    if effective_uv_index_url:
        lines.extend(
            [
                "[[index]]",
                f'url = "{effective_uv_index_url}"',
                "default = true",
            ]
        )
    config_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return str(config_path)


def build_host_sdk_runtime_env(
    *,
    sdk_root: Path | None = None,
    src_root: Path | None = None,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    resolved_sdk_root = (sdk_root or openhands_sdk_root()).expanduser().resolve()
    resolved_src_root = (src_root or (project_root() / "src")).expanduser().resolve()
    env = dict(base_env or os.environ)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONUNBUFFERED"] = "1"
    env["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"] = str(resolved_sdk_root)
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(resolved_src_root)
        if not existing_pythonpath
        else os.pathsep.join([str(resolved_src_root), existing_pythonpath])
    )
    overrides = host_sdk_runtime_env_overrides()
    env.update(overrides)
    pip_index_url = str(overrides.get("PIP_INDEX_URL") or "").strip()
    uv_index_url = str(overrides.get("UV_INDEX_URL") or "").strip()
    uv_python_install_mirror = str(overrides.get("UV_PYTHON_INSTALL_MIRROR") or "").strip()
    if pip_index_url or uv_index_url or uv_python_install_mirror:
        env["UV_CONFIG_FILE"] = _write_host_sdk_uv_config(
            pip_index_url=pip_index_url,
            uv_index_url=uv_index_url,
            uv_python_install_mirror=uv_python_install_mirror,
        )
    return env


def _forwarding_log_callback(log_callback: Any | None) -> Callable[[str], None]:
    def append(line: str) -> None:
        if callable(log_callback):
            log_callback(line)

    return append


def prewarm_host_sdk_environment(
    *,
    log_callback: Any | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    sdk_root = openhands_sdk_root()
    if not sdk_root.exists():
        raise ValueError(f"software-agent-sdk root not found at {sdk_root}")
    command = host_sdk_prewarm_command()
    env = build_host_sdk_runtime_env(
        sdk_root=sdk_root,
        src_root=project_root() / "src",
        base_env=os.environ.copy(),
    )
    completed = run_logged_subprocess(
        command,
        cwd=str(project_root()),
        env=env,
        check=False,
        log_callback=_forwarding_log_callback(log_callback),
        cancel_requested=cancel_requested,
    )
    marker = {
        "status": "passed" if completed.returncode == 0 else "failed",
        "prewarmed_at": datetime.now(UTC).isoformat(),
        "fingerprint": host_sdk_environment_fingerprint(),
        "returncode": completed.returncode,
        "stdout_tail": text_tail(completed.stdout),
        "stderr_tail": text_tail(completed.stderr),
        "log_tail": command_log_tail(completed.stdout, completed.stderr),
        "error": "" if completed.returncode == 0 else text_tail(completed.stderr or completed.stdout),
    }
    write_host_sdk_prewarm_marker(marker)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return {
        "target": "host_environment",
        "python_version": FEATURE_FACTORY_STAGE2_HOST_SDK_PYTHON_VERSION,
        "command": command,
        "log_tail": marker["log_tail"],
    }


def record_host_sdk_environment_ready(*, log_tail: str | None = None) -> None:
    marker_log = log_tail or "OpenHands bridge host Python environment is ready."
    marker = {
        "status": "passed",
        "prewarmed_at": datetime.now(UTC).isoformat(),
        "fingerprint": host_sdk_environment_fingerprint(),
        "returncode": 0,
        "stdout_tail": text_tail(marker_log),
        "stderr_tail": "",
        "log_tail": text_tail(marker_log),
        "error": "",
    }
    write_host_sdk_prewarm_marker(marker)


def read_host_sdk_prewarm_marker() -> dict[str, Any]:
    marker_path = FEATURE_FACTORY_STAGE2_HOST_SDK_PREWARM_MARKER
    if not marker_path.exists():
        return {}
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_host_sdk_prewarm_marker(payload: dict[str, Any]) -> None:
    marker_path = FEATURE_FACTORY_STAGE2_HOST_SDK_PREWARM_MARKER
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def planner_agent_server_prewarm_status(
    *,
    sdk: dict[str, Any] | None = None,
    platform_name: str | None = None,
    mirror_profile: str | None = None,
) -> dict[str, Any]:
    sdk = dict(sdk or agent_server_sdk_metadata())
    resolved_platform = platform_name or detect_platform()
    plan = agent_server_build_plan(
        base_image=PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name=resolved_platform,
    )
    image_ref = str(plan["image_tag"])
    resolved_mirror_profile = mirror_profile or str(plan["mirror_profile"])
    image_status = agent_server_image_status(
        image_ref=image_ref,
        base_image_ref=PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name=resolved_platform,
        git_sha=str(sdk.get("git_sha") or "unknown"),
        git_ref=str(sdk.get("git_ref") or "unknown"),
        mirror_profile=resolved_mirror_profile,
    )
    marker = read_planner_agent_server_prewarm_marker()
    marker_fingerprint = marker.get("fingerprint") if isinstance(marker.get("fingerprint"), dict) else {}
    fingerprint = planner_agent_server_fingerprint(
        sdk=sdk,
        platform_name=resolved_platform,
        mirror_profile=resolved_mirror_profile,
    )
    marker_matches = bool(marker.get("status") == "passed" and marker_fingerprint == fingerprint)
    prewarmed = bool(image_status.get("present") and not image_status.get("needs_update"))
    return {
        "base_image_ref": PLANNER_AGENT_SERVER_BASE_IMAGE,
        "agent_server_image": {
            **image_status,
            "image_ref": image_ref,
            "base_image_ref": PLANNER_AGENT_SERVER_BASE_IMAGE,
        },
        "prewarmed": prewarmed,
        "marker_matches": marker_matches,
        "last_status": marker.get("status") or "",
        "last_prewarmed_at": marker.get("prewarmed_at") or None,
        "last_error": marker.get("error") or "",
        "log_tail": marker_log_tail(marker),
    }


def planner_agent_server_fingerprint(
    *,
    sdk: dict[str, Any] | None = None,
    platform_name: str | None = None,
    mirror_profile: str | None = None,
) -> dict[str, Any]:
    sdk = dict(sdk or agent_server_sdk_metadata())
    resolved_platform = platform_name or detect_platform()
    resolved_mirror_profile = mirror_profile or resolve_agent_server_build_args()[0]
    return {
        "base_image": PLANNER_AGENT_SERVER_BASE_IMAGE,
        "platform": resolved_platform,
        "mirror_profile": resolved_mirror_profile,
        "sdk_root": sdk.get("root") or str(openhands_sdk_root()),
        "sdk_git_sha": sdk.get("git_sha") or "unknown",
        "sdk_uv_lock_sha256": file_sha256(openhands_sdk_root() / "uv.lock"),
        "agent_server_dockerfile_sha256": file_sha256(FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE),
    }


def read_planner_agent_server_prewarm_marker() -> dict[str, Any]:
    marker_path = FEATURE_FACTORY_STAGE2_PLANNER_AGENT_SERVER_PREWARM_MARKER
    if not marker_path.exists():
        return {}
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_planner_agent_server_prewarm_marker(payload: dict[str, Any]) -> None:
    marker_path = FEATURE_FACTORY_STAGE2_PLANNER_AGENT_SERVER_PREWARM_MARKER
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_planner_agent_server_ready(
    *,
    image_ref: str,
    platform_name: str,
    mirror_profile: str,
    log_tail: str | None = None,
) -> None:
    marker_log = log_tail or f"planner agent-server image ready: {image_ref}"
    marker = {
        "status": "passed",
        "prewarmed_at": datetime.now(UTC).isoformat(),
        "fingerprint": planner_agent_server_fingerprint(
            platform_name=platform_name,
            mirror_profile=mirror_profile,
        ),
        "returncode": 0,
        "image_ref": image_ref,
        "stdout_tail": text_tail(marker_log),
        "stderr_tail": "",
        "log_tail": text_tail(marker_log),
        "error": "",
    }
    write_planner_agent_server_prewarm_marker(marker)


def prewarm_planner_agent_server_image(
    *,
    force: bool = False,
    platform_name: str | None = None,
    log_callback: Any | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    platform_name = platform_name or detect_platform()
    plan = agent_server_build_plan(
        base_image=PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name=platform_name,
    )
    image_tag = str(plan["image_tag"])
    expected_labels = dict(plan.get("labels") or {})
    current_inspect = inspect_docker_image(image_tag)
    previous_image_id = str(current_inspect.get("image_id") or "")
    current_matches = bool(
        current_inspect.get("present")
        and expected_labels
        and labels_match(labels=dict(current_inspect.get("labels") or {}), expected_labels=expected_labels)
    )
    should_build = bool(force or not current_matches)
    if should_build:
        with dockerfile_with_mirrored_from_images(
            FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE,
            enabled=should_use_china_docker_mirrors(),
            log_callback=log_callback if callable(log_callback) else None,
        ) as build_dockerfile_path:
            command = list(plan["command"])
            if "--file" in command:
                dockerfile_index = command.index("--file") + 1
                if dockerfile_index < len(command):
                    command[dockerfile_index] = str(build_dockerfile_path)
            completed = run_logged_subprocess(
                command,
                cwd=str(project_root()),
                env=plan["env"],
                check=False,
                log_callback=_forwarding_log_callback(log_callback),
                cancel_requested=cancel_requested,
            )
        log_tail = command_log_tail(completed.stdout, completed.stderr)
        status = "passed" if completed.returncode == 0 else "failed"
    else:
        if callable(log_callback):
            log_callback(f"planner agent-server image already present: {image_tag}")
        completed = subprocess.CompletedProcess(
            args=plan["command"],
            returncode=0,
            stdout=f"planner agent-server image already present: {image_tag}\n",
            stderr="",
        )
        log_tail = command_log_tail(completed.stdout, completed.stderr)
        status = "passed"

    marker = {
        "status": status,
        "prewarmed_at": datetime.now(UTC).isoformat(),
        "fingerprint": planner_agent_server_fingerprint(
            platform_name=platform_name,
            mirror_profile=str(plan["mirror_profile"]),
        ),
        "returncode": completed.returncode,
        "image_ref": image_tag,
        "stdout_tail": text_tail(completed.stdout),
        "stderr_tail": text_tail(completed.stderr),
        "log_tail": log_tail,
        "error": "" if completed.returncode == 0 else text_tail(completed.stderr or completed.stdout),
    }
    write_planner_agent_server_prewarm_marker(marker)
    if completed.returncode != 0:
        raise subprocess.CalledProcessError(
            completed.returncode,
            completed.args,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    cleanup_replaced_docker_image(
        previous_image_id=previous_image_id,
        current_image_ref=image_tag,
        log_callback=log_callback if callable(log_callback) else None,
    )
    cleanup_stale_agent_server_images(
        base_image=PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name=platform_name,
        current_image_ref=image_tag,
        expected_labels=expected_labels,
        log_callback=log_callback if callable(log_callback) else None,
    )
    return {
        "target": "planner",
        "base_image_ref": PLANNER_AGENT_SERVER_BASE_IMAGE,
        "agent_server_image_ref": image_tag,
        "log_tail": log_tail,
    }


def agent_server_sdk_metadata() -> dict[str, Any]:
    sdk_root = openhands_sdk_root()
    git_sha = git_stdout(sdk_root, "rev-parse", "--verify", "HEAD", fallback="unknown")
    git_ref = git_stdout(
        sdk_root,
        "symbolic-ref",
        "-q",
        "--short",
        "HEAD",
        fallback="detached",
    )
    return {
        "root": str(sdk_root),
        "exists": sdk_root.exists(),
        "git_sha": git_sha,
        "git_ref": git_ref,
        "short_sha": git_sha[:7] if git_sha and git_sha != "unknown" else "unknown",
    }


def prewarm_stage2_sdk(
    *,
    force: bool = False,
    platform_name: str | None = None,
) -> dict[str, Any]:
    platform_name = platform_name or detect_platform()
    host_environment = prewarm_host_sdk_environment()
    planner = prewarm_planner_agent_server_image(force=force, platform_name=platform_name)

    return {
        "platform": platform_name,
        "built": [
            host_environment,
            planner,
        ],
        "status": stage2_sdk_prewarm_status(platform_name=platform_name),
    }


def build_stage2_image_assets(
    *,
    image_ids: list[str],
    include_base_image: bool,
    include_agent_server_image: bool,
    force: bool = False,
    platform_name: str | None = None,
    log_callback: Any | None = None,
    target_started_callback: Any | None = None,
    target_finished_callback: Any | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    platform_name = platform_name or detect_platform()
    requested_ids = {str(image_id or "").strip() for image_id in image_ids if str(image_id or "").strip()}
    if not requested_ids:
        raise ValueError("at least one image_id is required")

    payloads = stage2_image_asset_payloads()
    current_profile_payloads = {
        str(spec.image_id): spec.to_payload()
        for spec in list_base_images()
    }
    payloads_by_id = {
        str(payload.get("asset_key") or ""): payload
        for payload in payloads
        if str(payload.get("asset_key") or "")
    }
    for image_id, payload in current_profile_payloads.items():
        payloads_by_id.setdefault(image_id, payload)
    unknown_ids = sorted(image_id for image_id in requested_ids if image_id not in payloads_by_id)
    if unknown_ids:
        raise ValueError(f"unknown base image ids: {', '.join(unknown_ids)}")

    built: list[dict[str, Any]] = []
    for image_id in sorted(requested_ids):
        base_payload = dict(payloads_by_id[image_id])
        asset_key = str(base_payload.get("asset_key") or image_id).strip()
        image_ref = str(base_payload.get("image_ref") or "").strip()
        item: dict[str, Any] = {
            "image_id": base_payload.get("image_id") or image_id,
            "asset_key": asset_key,
            "base_image_ref": image_ref,
            "base_image_built": False,
            "agent_server_image_ref": "",
            "agent_server_image_built": False,
        }
        if include_base_image:
            if callable(target_started_callback):
                target_started_callback("base")
            if callable(log_callback):
                log_callback(f"[base] building {image_ref}")
            ensure_base_image_built(
                source_root=asset_root(),
                base_image_payload=base_payload,
                platform_name=platform_name,
                force=force,
                log_callback=log_callback if callable(log_callback) else None,
                cancel_requested=cancel_requested,
            )
            write_base_image_snapshot(
                asset_key=asset_key,
                image_ref=image_ref,
                dockerfile_path=(asset_root() / str(base_payload.get("asset_path") or "")).resolve(),
                platform_name=platform_name,
                source="build",
            )
            item["base_image_built"] = True
            if callable(target_finished_callback):
                target_finished_callback("base")
        if include_agent_server_image:
            if not include_base_image:
                prepared_base_status = base_image_status(
                    asset_key=asset_key,
                    image_ref=image_ref,
                    dockerfile_path=(asset_root() / str(base_payload.get("asset_path") or "")).resolve(),
                    platform_name=platform_name,
                    base_image_payload=base_payload,
                )
                if (
                    not prepared_base_status.get("present")
                    or prepared_base_status.get("needs_update")
                ):
                    raise ValueError(
                        f"base image must be downloaded and current before wrapping: {image_ref}"
                    )
            if callable(target_started_callback):
                target_started_callback("agent")
            if callable(log_callback):
                log_callback(f"[agent] building wrapper for {image_ref}")
            item["agent_server_image_ref"] = ensure_agent_server_image_built(
                base_image=image_ref,
                platform_name=platform_name,
                force=force,
                log_callback=log_callback if callable(log_callback) else None,
                cancel_requested=cancel_requested,
            )
            item["agent_server_image_built"] = True
            if callable(target_finished_callback):
                target_finished_callback("agent")
        built.append(item)

    return {
        "platform": platform_name,
        "built": built,
        "status": stage2_image_asset_status(platform_name=platform_name),
    }


def stage2_image_asset_payloads() -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for spec in list_base_images():
        for use_china_mirrors in (False, True):
            payload = dict(spec.to_payload(use_china_mirrors=use_china_mirrors))
            image_ref = str(payload.get("image_ref") or "").strip()
            asset_path = str(payload.get("asset_path") or "").strip()
            network_profile = str(payload.get("network_profile") or "").strip()
            key = (str(payload.get("image_id") or ""), image_ref, asset_path)
            if not image_ref or not asset_path or key in seen:
                continue
            seen.add(key)
            payload["asset_key"] = f"{payload.get('image_id')}::{network_profile or 'default'}"
            payloads.append(payload)
    return payloads


def base_image_status(
    *,
    asset_key: str,
    image_ref: str,
    dockerfile_path: Path | None,
    platform_name: str,
    base_image_payload: dict[str, Any],
) -> dict[str, Any]:
    status = docker_image_status(
        image_ref=image_ref,
        dockerfile_path=None,
    )
    dockerfile_sha = file_sha256(dockerfile_path) if dockerfile_path is not None else ""
    snapshot_path = base_image_snapshot_path(image_ref=image_ref, platform_name=platform_name)
    snapshot = read_json_file(snapshot_path)
    snapshot_sha = str(snapshot.get("dockerfile_sha256") or "").strip()
    labels = dict(status.get("labels") or {})
    expected_labels = (
        base_image_build_labels(
            base_image_payload=base_image_payload,
            dockerfile_path=dockerfile_path,
            platform_name=platform_name,
        )
        if dockerfile_path is not None
        else {}
    )
    label_matches = bool(
        status.get("present")
        and expected_labels
        and labels_match(labels=labels, expected_labels=expected_labels)
    )
    snapshot_matches = bool(
        status.get("present")
        and dockerfile_sha
        and snapshot_sha
        and snapshot_sha == dockerfile_sha
        and str(snapshot.get("platform") or "") == str(platform_name or "")
    )
    snapshot_missing = bool(status.get("present") and label_matches and dockerfile_sha and not snapshot_sha)
    if status.get("present") and label_matches and dockerfile_path is not None and not snapshot_matches:
        write_base_image_snapshot(
            asset_key=asset_key,
            image_ref=image_ref,
            dockerfile_path=dockerfile_path,
            platform_name=platform_name,
            source="labelled-image",
        )
        snapshot = read_json_file(snapshot_path)
        snapshot_sha = str(snapshot.get("dockerfile_sha256") or "").strip()
        snapshot_missing = False
        snapshot_matches = bool(
            snapshot_sha == dockerfile_sha
            and str(snapshot.get("platform") or "") == str(platform_name or "")
        )
    usable = bool(label_matches and snapshot_matches)
    dockerfile_needs_update = bool(status.get("present") and not usable)
    status.update(
        {
            "needs_update": dockerfile_needs_update,
            "dockerfile_sha256": dockerfile_sha,
            "dockerfile_snapshot_sha256": snapshot_sha,
            "dockerfile_snapshot_path": str(snapshot_path),
            "dockerfile_snapshot_missing": snapshot_missing,
            "fingerprint_matches": usable,
            "label_matches": label_matches,
            "snapshot_matches": snapshot_matches,
            "dockerfile_needs_update": dockerfile_needs_update,
            "dependency_needs_update": False,
        }
    )
    return status


def base_image_snapshot_path(*, image_ref: str, platform_name: str) -> Path:
    digest = hashlib.sha256(f"{image_ref}\n{platform_name}".encode("utf-8")).hexdigest()
    return FEATURE_FACTORY_STAGE2_IMAGE_ASSET_CACHE_DIR / "base" / f"{digest}.json"


def labels_match(*, labels: dict[str, Any], expected_labels: dict[str, str]) -> bool:
    if not labels or not expected_labels:
        return False
    for label_name, expected_value in expected_labels.items():
        if str(labels.get(label_name) or "") != str(expected_value):
            return False
    return True


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def write_base_image_snapshot(
    *,
    asset_key: str,
    image_ref: str,
    dockerfile_path: Path,
    platform_name: str,
    source: str,
) -> None:
    if not dockerfile_path.is_file():
        return
    dockerfile_text = dockerfile_path.read_text(encoding="utf-8")
    payload = {
        "asset_key": asset_key,
        "image_ref": image_ref,
        "dockerfile_path": str(dockerfile_path),
        "dockerfile_sha256": hashlib.sha256(dockerfile_text.encode("utf-8")).hexdigest(),
        "dockerfile_text": dockerfile_text,
        "platform": platform_name,
        "source": source,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    snapshot_path = base_image_snapshot_path(image_ref=image_ref, platform_name=platform_name)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def docker_image_status(
    *,
    image_ref: str,
    dockerfile_path: Path | None,
    dependency_needs_update: bool = False,
) -> dict[str, Any]:
    inspect = inspect_docker_image(image_ref)
    created_at = inspect.get("created_at")
    dockerfile_mtime = file_modified_at(dockerfile_path) if dockerfile_path is not None else None
    dockerfile_needs_update = bool(created_at and dockerfile_mtime and dockerfile_mtime > created_at)
    needs_update = bool(inspect.get("present") and (dockerfile_needs_update or dependency_needs_update))
    return {
        "image_ref": image_ref,
        "present": bool(inspect.get("present")),
        "needs_update": needs_update,
        "image_id": inspect.get("image_id") or "",
        "created_at": serialize_datetime(created_at),
        "architecture": inspect.get("architecture") or "",
        "os": inspect.get("os") or "",
        "labels": inspect.get("labels") or {},
        "dockerfile_modified_at": serialize_datetime(dockerfile_mtime),
        "dockerfile_needs_update": dockerfile_needs_update,
        "dependency_needs_update": bool(dependency_needs_update),
        "inspect_error": inspect.get("error") or "",
    }


def agent_server_image_status(
    *,
    image_ref: str,
    base_image_ref: str,
    platform_name: str,
    git_sha: str,
    git_ref: str,
    mirror_profile: str,
) -> dict[str, Any]:
    status = docker_image_status(
        image_ref=image_ref,
        dockerfile_path=None,
    )
    expected_labels = agent_server_build_labels(
        base_image=base_image_ref,
        platform_name=platform_name,
        git_sha=git_sha,
        git_ref=git_ref,
        mirror_profile=mirror_profile,
    )
    physical_present = bool(status.get("present"))
    label_matches = labels_match(
        labels=dict(status.get("labels") or {}),
        expected_labels=expected_labels,
    )
    status.update(
        {
            "present": bool(physical_present and label_matches),
            "physical_present": physical_present,
            "fingerprint_matches": bool(label_matches),
            "needs_update": False,
            "expected_labels": expected_labels,
        }
    )
    return status


def cleanup_stale_agent_server_images(
    *,
    base_image: str,
    platform_name: str,
    current_image_ref: str,
    expected_labels: dict[str, str],
    log_callback: Callable[[str], None] | None = None,
) -> list[str]:
    if not expected_labels:
        return []
    current_ref = str(current_image_ref or "").strip()
    base_component = sanitize_image_tag_component(base_image)
    platform_component = sanitize_image_tag_component(platform_name)
    current_inspect = inspect_docker_image(current_ref)
    if not current_inspect.get("present"):
        return []
    current_image_id = str(current_inspect.get("image_id") or "")
    stale_refs: list[str] = []
    for candidate_ref in docker_image_references(FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY):
        if candidate_ref == current_ref:
            continue
        tag = _image_ref_tag(candidate_ref, repository=FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY)
        if not tag or not _agent_server_tag_matches_target(
            tag=tag,
            base_component=base_component,
            platform_component=platform_component,
        ):
            continue
        candidate = inspect_docker_image(candidate_ref)
        if not candidate.get("present"):
            continue
        candidate_image_id = str(candidate.get("image_id") or "")
        if current_image_id and candidate_image_id == current_image_id:
            continue
        candidate_labels = dict(candidate.get("labels") or {})
        if labels_match(labels=candidate_labels, expected_labels=expected_labels):
            continue
        stale_refs.append(candidate_ref)
    return remove_docker_image_references(
        stale_refs,
        log_callback=log_callback if callable(log_callback) else None,
    )


def _image_ref_tag(image_ref: str, *, repository: str) -> str:
    prefix = f"{repository}:"
    if not image_ref.startswith(prefix):
        return ""
    return image_ref[len(prefix) :]


def _agent_server_tag_matches_target(
    *,
    tag: str,
    base_component: str,
    platform_component: str,
) -> bool:
    prefix_remainder = tag.split("-", 1)[1] if "-" in tag else ""
    return bool(
        prefix_remainder
        and prefix_remainder.startswith(f"{base_component}-{platform_component}-")
        and tag.endswith("-agent-server")
    )


def inspect_docker_image(image_ref: str) -> dict[str, Any]:
    reference = str(image_ref or "").strip()
    if not reference:
        return {"present": False, "error": "missing image ref"}
    try:
        completed = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                reference,
                "--format",
                "{{json .}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"present": False, "error": str(exc)}
    if completed.returncode != 0:
        return {"present": False, "error": str(completed.stderr or completed.stdout).strip()}
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return {"present": False, "error": f"invalid docker inspect json: {exc}"}
    if not isinstance(payload, dict):
        return {"present": False, "error": "invalid docker inspect payload"}
    config = payload.get("Config") if isinstance(payload.get("Config"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    return {
        "present": True,
        "image_id": str(payload.get("Id") or ""),
        "created_at": parse_docker_datetime(str(payload.get("Created") or "")),
        "architecture": str(payload.get("Architecture") or ""),
        "os": str(payload.get("Os") or ""),
        "size_bytes": int(payload.get("Size") or 0),
        "labels": {str(key): str(value) for key, value in labels.items() if value is not None},
    }


def ensure_agent_server_image_built(
    *,
    base_image: str,
    platform_name: str,
    force: bool = False,
    log_callback: Any | None = None,
    timeout_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    plan = agent_server_build_plan(
        base_image=base_image,
        platform_name=platform_name,
    )
    image_tag = str(plan["image_tag"])
    inspect = inspect_docker_image(image_tag)
    previous_image_id = str(inspect.get("image_id") or "")
    if plan.get("image_override"):
        if base_image == PLANNER_AGENT_SERVER_BASE_IMAGE and inspect.get("present"):
            record_planner_agent_server_ready(
                image_ref=image_tag,
                platform_name=platform_name,
                mirror_profile=str(plan["mirror_profile"]),
                log_tail=f"planner agent-server override image present: {image_tag}",
            )
        return image_tag
    if not force and labels_match(
        labels=dict(inspect.get("labels") or {}),
        expected_labels=dict(plan.get("labels") or {}),
    ):
        if base_image == PLANNER_AGENT_SERVER_BASE_IMAGE:
            record_planner_agent_server_ready(
                image_ref=image_tag,
                platform_name=platform_name,
                mirror_profile=str(plan["mirror_profile"]),
                log_tail=f"planner agent-server image already present: {image_tag}",
            )
        cleanup_stale_agent_server_images(
            base_image=base_image,
            platform_name=platform_name,
            current_image_ref=image_tag,
            expected_labels=dict(plan.get("labels") or {}),
            log_callback=log_callback if callable(log_callback) else None,
        )
        return image_tag

    with dockerfile_with_mirrored_from_images(
        FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE,
        enabled=should_use_china_docker_mirrors(),
        log_callback=log_callback if callable(log_callback) else None,
    ) as build_dockerfile_path:
        command = list(plan["command"])
        if "--file" in command:
            dockerfile_index = command.index("--file") + 1
            if dockerfile_index < len(command):
                command[dockerfile_index] = str(build_dockerfile_path)
        run_logged_subprocess(
            command,
            cwd=str(project_root()),
            env=plan["env"],
            log_callback=log_callback if callable(log_callback) else None,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )
    if base_image == PLANNER_AGENT_SERVER_BASE_IMAGE:
        record_planner_agent_server_ready(
            image_ref=image_tag,
            platform_name=platform_name,
            mirror_profile=str(plan["mirror_profile"]),
            log_tail=f"planner agent-server image build completed: {image_tag}",
        )
    cleanup_replaced_docker_image(
        previous_image_id=previous_image_id,
        current_image_ref=image_tag,
        log_callback=log_callback if callable(log_callback) else None,
    )
    cleanup_stale_agent_server_images(
        base_image=base_image,
        platform_name=platform_name,
        current_image_ref=image_tag,
        expected_labels=dict(plan.get("labels") or {}),
        log_callback=log_callback if callable(log_callback) else None,
    )
    return image_tag


def agent_server_build_plan(
    *,
    base_image: str,
    platform_name: str,
) -> dict[str, Any]:
    image_override = os.getenv("FEATURE_FACTORY_STAGE2_OPENHANDS_AGENT_SERVER_IMAGE")
    if image_override:
        return {
            "image_tag": image_override,
            "command": ["true"],
            "env": os.environ.copy(),
            "mirror_profile": "override",
            "image_override": True,
        }

    sdk_root = openhands_sdk_root()
    git_sha = git_stdout(sdk_root, "rev-parse", "--verify", "HEAD", fallback="unknown")
    git_ref = git_stdout(
        sdk_root,
        "symbolic-ref",
        "-q",
        "--short",
        "HEAD",
        fallback="detached",
    )
    mirror_profile, mirror_build_args = resolve_agent_server_build_args()
    use_docker_mirrors = should_use_china_docker_mirrors()
    build_base_image = mirror_docker_image_ref(
        base_image,
        enabled=use_docker_mirrors,
    )
    image_tag = agent_server_image_tag(
        base_image=base_image,
        platform_name=platform_name,
        git_sha=git_sha,
        mirror_profile=mirror_profile,
    )

    command = [
        "docker",
        "build",
        "--progress=plain",
        "--file",
        str(FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE),
        "--target",
        "agent-server",
        "--platform",
        platform_name,
        "--build-arg",
        f"BASE_IMAGE={build_base_image}",
        "--build-arg",
        f"OPENHANDS_BUILD_GIT_SHA={git_sha}",
        "--build-arg",
        f"OPENHANDS_BUILD_GIT_REF={git_ref}",
        "--build-arg",
        "BUILDKIT_INLINE_CACHE=1",
    ]
    for build_arg_name, value in mirror_build_args.items():
        command.extend(["--build-arg", f"{build_arg_name}={value}"])
    labels = agent_server_build_labels(
        base_image=base_image,
        platform_name=platform_name,
        git_sha=git_sha,
        git_ref=git_ref,
        mirror_profile=mirror_profile,
    )
    for label_name, label_value in labels.items():
        command.extend(["--label", f"{label_name}={label_value}"])
    command.extend(["--tag", image_tag, str(sdk_root)])
    build_env = os.environ.copy()
    build_env["DOCKER_BUILDKIT"] = "1"
    return {
        "image_tag": image_tag,
        "command": command,
        "env": build_env,
        "mirror_profile": mirror_profile,
        "build_base_image": build_base_image,
        "labels": labels,
        "image_override": False,
    }


def agent_server_build_labels(
    *,
    base_image: str,
    platform_name: str,
    git_sha: str,
    git_ref: str,
    mirror_profile: str,
) -> dict[str, str]:
    base_inspect = inspect_docker_image(base_image)
    return {
        FEATURE_FACTORY_STAGE2_KIND_LABEL: FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL: base_image,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_ID_LABEL: str(base_inspect.get("image_id") or ""),
        FEATURE_FACTORY_STAGE2_PLATFORM_LABEL: platform_name,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_SHA_LABEL: git_sha,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_REF_LABEL: git_ref,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_MIRROR_PROFILE_LABEL: mirror_profile,
        FEATURE_FACTORY_STAGE2_AGENT_SERVER_DOCKERFILE_SHA_LABEL: file_sha256(
            FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE
        ),
    }


def resolve_agent_server_build_args() -> tuple[str, dict[str, str]]:
    mirror_profile = "cn" if should_use_china_mirrors() else "default"
    build_args = (
        dict(FEATURE_FACTORY_CHINA_MIRROR_BUILD_ARGS)
        if mirror_profile == "cn"
        else {}
    )
    if mirror_profile == "cn":
        if should_use_china_docker_mirrors():
            build_args["AGENT_PYTHON_IMAGE"] = mirror_docker_image_ref(
                AGENT_SERVER_BUILDER_BASE_IMAGE,
                enabled=True,
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


def agent_server_image_tag(
    *,
    base_image: str,
    platform_name: str,
    git_sha: str,
    mirror_profile: str,
) -> str:
    short_sha = git_sha[:7] if git_sha and git_sha != "unknown" else "unknown"
    base_component = sanitize_image_tag_component(base_image)
    platform_component = sanitize_image_tag_component(platform_name)
    mirror_component = sanitize_image_tag_component(mirror_profile)
    return (
        f"{FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY}:"
        f"{short_sha}-{base_component}-{platform_component}-{mirror_component}-agent-server"
    )


def sanitize_image_tag_component(value: str) -> str:
    component = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("_.-").lower()
    if not component:
        return "image"
    component = component[:FEATURE_FACTORY_AGENT_SERVER_TAG_COMPONENT_LIMIT].rstrip("_.-")
    return component or "image"


def detect_platform() -> str:
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


def openhands_sdk_root() -> Path:
    configured = os.getenv("FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return (project_root() / "software-agent-sdk").resolve()


def project_root() -> Path:
    return FEATURE_FACTORY_PROJECT_ROOT


def git_stdout(cwd: Path, *args: str, fallback: str) -> str:
    if not cwd.exists():
        return fallback
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


def file_modified_at(path: Path | None) -> datetime | None:
    if path is None or not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def file_sha256(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_tail(value: str, *, limit: int = 4000) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[-limit:]


def command_log_tail(stdout: str, stderr: str, *, limit: int = 4000) -> str:
    parts: list[str] = []
    if stdout:
        parts.append(str(stdout).rstrip())
    if stderr:
        parts.append(str(stderr).rstrip())
    return text_tail("\n".join(part for part in parts if part), limit=limit)


def marker_log_tail(marker: dict[str, Any]) -> str:
    value = str(marker.get("log_tail") or "")
    if value:
        return value
    return command_log_tail(
        str(marker.get("stdout_tail") or ""),
        str(marker.get("stderr_tail") or ""),
    )


def parse_docker_datetime(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()
