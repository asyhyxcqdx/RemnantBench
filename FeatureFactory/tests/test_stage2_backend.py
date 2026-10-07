from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tarfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

import feature_factory.docker_mirrors as docker_mirrors_module
from feature_factory.config import Settings
from feature_factory.models import GitHubRepository
from feature_factory.openhands_llm import DEPLOYMENT_LLM_MODEL_ENV
from feature_factory.server import _Stage2ImageAssetBuildJobManager, _Stage2SdkPrewarmJobManager
from feature_factory.stage2.assets import asset_root, read_text_asset
from feature_factory.stage2.base_images import (
    base_image_build_labels,
    ensure_base_image_built,
    list_base_images,
    materialize_selected_base_image_reference,
    prepare_base_image_asset_view,
    run_logged_subprocess,
    selected_base_image_prompt_summary,
)
from feature_factory.stage2.backend import (
    OPENHANDS_BRIDGE_PYTHON_VERSION,
    _BridgeTimeoutBudget,
    OpenHandsStage2Backend,
    Stage2BackendEvent,
    build_stage2_backend,
)
from feature_factory.stage2.bridge_events import (
    WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage2 import image_assets
import feature_factory.stage2.base_images as base_images_module
from feature_factory.stage2.image_assets import (
    agent_server_image_tag,
    agent_server_image_status,
    base_image_status,
    docker_image_status,
    parse_docker_datetime,
    prewarm_stage2_sdk,
    stage2_sdk_prewarm_status,
)
from feature_factory.stage2.planner import PlannerDecision
from feature_factory.stage2.validator import Stage2ValidationConfig

try:
    import feature_factory.stage2.openhands_bridge as openhands_bridge_module
    from feature_factory.stage2.openhands_bridge import (
        _WorkerValidationService,
        _agent_server_container_labels,
        _build_planner_prompt,
        _build_worker_prompt,
        _conversation_failure_context,
        _conversation_failure_message,
        _create_workspace_snapshot,
        _docker_workspace,
        _ensure_openhands_runtime_artifacts_writable,
        _make_container_writable,
        _openhands_server_runtime_env,
        _prepare_worker_resume_conversation_state,
        _regional_network_context_block,
        _read_worker_checkpoint_payload,
        _validation_config_from_request,
        _write_host_artifact_copy,
    )
except ModuleNotFoundError:
    openhands_bridge_module = None
    _WorkerValidationService = None
    _agent_server_container_labels = None
    _build_planner_prompt = None
    _build_worker_prompt = None
    _conversation_failure_context = None
    _conversation_failure_message = None
    _create_workspace_snapshot = None
    _docker_workspace = None
    _ensure_openhands_runtime_artifacts_writable = None
    _make_container_writable = None
    _openhands_server_runtime_env = None
    _prepare_worker_resume_conversation_state = None
    _regional_network_context_block = None
    _read_worker_checkpoint_payload = None
    _validation_config_from_request = None
    _write_host_artifact_copy = None


def _repository() -> GitHubRepository:
    return GitHubRepository(
        id=1,
        github_repo_id=9001,
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        api_url="https://api.github.com/repos/owner/demo-repo",
        description="demo repo",
        primary_language="TypeScript",
        default_branch="main",
        license_key="mit",
        stargazers_count=42,
        created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
        pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
        discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
    )


def _patch_stage2_backend_image_builds(monkeypatch) -> list[dict[str, object]]:
    build_calls: list[dict[str, object]] = []
    monkeypatch.setattr("feature_factory.stage2.backend.detect_platform", lambda: "linux/arm64")

    def fake_ensure_agent_server_image_built(**kwargs):
        build_calls.append({"kind": "agent", **kwargs})
        return f"agent:{kwargs['base_image']}"

    def fake_ensure_base_image_built(**kwargs):
        build_calls.append({"kind": "base", **kwargs})
        return str(kwargs["base_image_payload"]["image_ref"])

    monkeypatch.setattr(
        "feature_factory.stage2.backend.ensure_agent_server_image_built",
        fake_ensure_agent_server_image_built,
    )
    monkeypatch.setattr(
        "feature_factory.stage2.backend.ensure_base_image_built",
        fake_ensure_base_image_built,
    )
    return build_calls


def test_stage2_agent_server_image_tag_includes_sdk_base_platform_and_mirror() -> None:
    image_ref = agent_server_image_tag(
        base_image="feature-factory/python:3.11-jammy-builder-cn",
        platform_name="linux/arm64",
        git_sha="abcdef1234567890",
        mirror_profile="cn",
    )

    assert image_ref.startswith("feature-factory/openhands-agent-server:abcdef1-")
    assert "feature-factory_python_3.11-jammy-builder-cn" in image_ref
    assert "linux_arm64" in image_ref
    assert image_ref.endswith("-cn-agent-server")


def test_stage2_agent_server_cleanup_removes_stale_same_base_platform_tag(monkeypatch) -> None:
    base_image = image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE
    current_ref = (
        "feature-factory/openhands-agent-server:"
        "abcdef1-nikolaik_python-nodejs_python3.13-nodejs22-slim-linux_arm64-custom-feedface-agent-server"
    )
    stale_ref = (
        "feature-factory/openhands-agent-server:"
        "abcdef1-nikolaik_python-nodejs_python3.13-nodejs22-slim-linux_arm64-cn-agent-server"
    )
    other_ref = (
        "feature-factory/openhands-agent-server:"
        "abcdef1-feature-factory_python_3.11-jammy-builder-linux_arm64-cn-agent-server"
    )
    expected_labels = {
        image_assets.FEATURE_FACTORY_STAGE2_KIND_LABEL: image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND,
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL: base_image,
        image_assets.FEATURE_FACTORY_STAGE2_PLATFORM_LABEL: "linux/arm64",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_MIRROR_PROFILE_LABEL: "custom-feedface",
    }
    removed_refs: list[str] = []

    monkeypatch.setattr(
        image_assets,
        "agent_server_build_plan",
        lambda *, base_image, platform_name: {
            "image_tag": current_ref,
            "command": ["docker", "build", "."],
            "env": {},
            "mirror_profile": "custom-feedface",
            "labels": expected_labels,
            "image_override": False,
        },
    )
    monkeypatch.setattr(
        image_assets,
        "inspect_docker_image",
        lambda image_ref: {
            "present": image_ref in {current_ref, stale_ref, other_ref},
            "image_id": f"sha256:{image_ref}",
            "labels": expected_labels if image_ref == current_ref else {},
        },
    )
    monkeypatch.setattr(
        image_assets,
        "docker_image_references",
        lambda repository: [current_ref, stale_ref, other_ref],
    )
    monkeypatch.setattr(
        image_assets,
        "remove_docker_image_references",
        lambda refs, **_kwargs: removed_refs.extend(list(refs)) or list(refs),
    )
    monkeypatch.setattr(image_assets, "record_planner_agent_server_ready", lambda **_kwargs: None)
    monkeypatch.setattr(
        image_assets,
        "run_logged_subprocess",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("cached image should be reused")),
    )

    resolved = image_assets.ensure_agent_server_image_built(
        base_image=base_image,
        platform_name="linux/arm64",
    )

    assert resolved == current_ref
    assert removed_refs == [stale_ref]


def test_stage2_image_status_marks_dockerfile_newer_than_image_as_update(monkeypatch, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setattr(
        "feature_factory.stage2.image_assets.inspect_docker_image",
        lambda _image_ref: {
            "present": True,
            "image_id": "sha256:demo",
            "created_at": parse_docker_datetime("2024-01-01T00:00:00Z"),
        },
    )

    status = docker_image_status(
        image_ref="feature-factory/demo:latest",
        dockerfile_path=dockerfile,
    )

    assert status["present"] is True
    assert status["needs_update"] is True
    assert status["dockerfile_needs_update"] is True


def test_stage2_base_image_status_uses_dockerfile_snapshot_content(monkeypatch, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    snapshot_root = tmp_path / "snapshots"
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_STAGE2_IMAGE_ASSET_CACHE_DIR", snapshot_root)
    monkeypatch.setattr(image_assets, "detect_platform", lambda: "linux/amd64")
    base_payload = {
        "asset_key": "python:demo::default",
        "image_id": "python:demo",
        "image_ref": "feature-factory/python:demo",
        "asset_path": "base_images/demo.Dockerfile",
        "network_profile": "default",
    }
    labels = base_image_build_labels(
        base_image_payload=base_payload,
        dockerfile_path=dockerfile,
        platform_name="linux/amd64",
    )
    monkeypatch.setattr(
        image_assets,
        "inspect_docker_image",
        lambda _image_ref: {
            "present": True,
            "image_id": "sha256:demo",
            "created_at": parse_docker_datetime("2024-01-01T00:00:00Z"),
            "labels": labels,
        },
    )

    status = base_image_status(
        asset_key=str(base_payload["asset_key"]),
        image_ref="feature-factory/python:demo",
        dockerfile_path=dockerfile,
        platform_name="linux/amd64",
        base_image_payload=base_payload,
    )

    assert status["needs_update"] is False
    assert status["label_matches"] is True
    assert status["snapshot_matches"] is True

    dockerfile.write_text("FROM scratch\nRUN echo changed\n", encoding="utf-8")
    status = base_image_status(
        asset_key=str(base_payload["asset_key"]),
        image_ref="feature-factory/python:demo",
        dockerfile_path=dockerfile,
        platform_name="linux/amd64",
        base_image_payload=base_payload,
    )

    assert status["present"] is True
    assert status["needs_update"] is True
    assert status["dockerfile_needs_update"] is True


def test_stage2_base_image_status_marks_unlabelled_existing_image_as_update(monkeypatch, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_STAGE2_IMAGE_ASSET_CACHE_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(
        image_assets,
        "inspect_docker_image",
        lambda _image_ref: {
            "present": True,
            "image_id": "sha256:demo",
            "created_at": parse_docker_datetime("2024-01-01T00:00:00Z"),
            "labels": {},
        },
    )

    status = base_image_status(
        asset_key="python:demo::default",
        image_ref="feature-factory/python:demo",
        dockerfile_path=dockerfile,
        platform_name="linux/amd64",
        base_image_payload={
            "asset_key": "python:demo::default",
            "image_id": "python:demo",
            "image_ref": "feature-factory/python:demo",
            "asset_path": "base_images/demo.Dockerfile",
            "network_profile": "default",
        },
    )

    assert status["present"] is True
    assert status["needs_update"] is True
    assert status["label_matches"] is False


def test_ensure_base_image_built_removes_replaced_stale_image(monkeypatch, tmp_path) -> None:
    source_root = tmp_path / "assets"
    dockerfile = source_root / "base_images" / "demo.Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    base_payload = {
        "asset_key": "python:demo::default",
        "image_id": "python:demo",
        "image_ref": "feature-factory/python:demo",
        "asset_path": "base_images/demo.Dockerfile",
        "network_profile": "default",
    }
    image_ids = iter(["sha256:old", "sha256:new"])
    removed_refs: list[str] = []

    monkeypatch.setattr(base_images_module, "docker_image_labels_match", lambda **_kwargs: False)
    monkeypatch.setattr(base_images_module, "docker_image_id", lambda _image_ref: next(image_ids))
    monkeypatch.setattr(
        base_images_module,
        "run_logged_subprocess",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        base_images_module,
        "remove_docker_image_references",
        lambda refs, **_kwargs: removed_refs.extend(list(refs)) or list(refs),
    )

    resolved = ensure_base_image_built(
        source_root=source_root,
        base_image_payload=base_payload,
        platform_name="linux/amd64",
    )

    assert resolved == "feature-factory/python:demo"
    assert removed_refs == ["sha256:old"]


def test_stage2_agent_server_status_requires_matching_fingerprint(monkeypatch) -> None:
    expected_labels = {
        image_assets.FEATURE_FACTORY_STAGE2_KIND_LABEL: image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND,
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL: "feature-factory/python:demo",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_ID_LABEL: "sha256:base",
        image_assets.FEATURE_FACTORY_STAGE2_PLATFORM_LABEL: "linux/amd64",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_SHA_LABEL: "abcdef1234567890",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_REF_LABEL: "main",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_MIRROR_PROFILE_LABEL: "default",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_DOCKERFILE_SHA_LABEL: "agent-dockerfile-sha",
    }
    monkeypatch.setattr(image_assets, "file_sha256", lambda _path: "agent-dockerfile-sha")
    monkeypatch.setattr(
        image_assets,
        "inspect_docker_image",
        lambda image_ref: {
            "present": True,
            "image_id": f"sha256:{image_ref}",
            "created_at": parse_docker_datetime("2024-01-01T00:00:00Z"),
            "labels": expected_labels if image_ref != "feature-factory/python:demo" else {},
        }
        | ({"image_id": "sha256:base"} if image_ref == "feature-factory/python:demo" else {}),
    )

    status = agent_server_image_status(
        image_ref="feature-factory/openhands-agent-server:demo",
        base_image_ref="feature-factory/python:demo",
        platform_name="linux/amd64",
        git_sha="abcdef1234567890",
        git_ref="main",
        mirror_profile="default",
    )

    assert status["present"] is True
    assert status["physical_present"] is True
    assert status["fingerprint_matches"] is True


def test_stage2_sdk_prewarm_status_includes_planner_and_worker_summary(monkeypatch, tmp_path) -> None:
    base_payload = {
        "image_id": "python:demo",
        "image_ref": "feature-factory/python:demo",
        "asset_path": "base_images/python-demo.Dockerfile",
    }
    spec = SimpleNamespace(image_id=base_payload["image_id"], to_payload=lambda: dict(base_payload))

    monkeypatch.setattr(image_assets, "list_base_images", lambda: [spec])
    monkeypatch.setattr(image_assets, "detect_platform", lambda: "linux/amd64")
    monkeypatch.setattr(image_assets, "openhands_sdk_root", lambda: tmp_path)
    monkeypatch.setattr(image_assets, "resolve_agent_server_build_args", lambda: ("default", {}))
    monkeypatch.setattr(image_assets, "file_sha256", lambda _path: "agent-dockerfile-sha")
    monkeypatch.setattr(image_assets, "read_host_sdk_prewarm_marker", lambda: {})
    monkeypatch.setattr(
        image_assets,
        "git_stdout",
        lambda _cwd, *args, fallback: "main" if "symbolic-ref" in args else "abcdef1234567890",
    )
    planner_base_image = image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE
    planner_labels = {
        image_assets.FEATURE_FACTORY_STAGE2_KIND_LABEL: image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND,
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL: planner_base_image,
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_ID_LABEL: f"sha256:{planner_base_image}",
        image_assets.FEATURE_FACTORY_STAGE2_PLATFORM_LABEL: "linux/amd64",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_SHA_LABEL: "abcdef1234567890",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_SDK_REF_LABEL: "main",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_MIRROR_PROFILE_LABEL: "default",
        image_assets.FEATURE_FACTORY_STAGE2_AGENT_SERVER_DOCKERFILE_SHA_LABEL: "agent-dockerfile-sha",
    }

    def fake_inspect(image_ref):
        present = "openhands-agent-server" in image_ref
        return {
            "present": present,
            "image_id": f"sha256:{image_ref}",
            "created_at": parse_docker_datetime("2099-01-01T00:00:00Z"),
            "labels": planner_labels if present else {},
        }

    monkeypatch.setattr(image_assets, "inspect_docker_image", fake_inspect)

    status = stage2_sdk_prewarm_status(platform_name="linux/amd64")

    assert status["sdk"]["exists"] is True
    assert status["sdk"]["short_sha"] == "abcdef1"
    assert status["host_environment"]["prewarmed"] is False
    assert status["host_environment"]["python_version"] == "3.13"
    assert status["planner"]["base_image_ref"] == image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE
    assert status["planner"]["agent_server_image"]["present"] is True
    assert "worker" not in status


def test_stage2_image_asset_build_manager_marks_running_job_in_status_payload() -> None:
    manager = _Stage2ImageAssetBuildJobManager()
    entered = threading.Event()
    release = threading.Event()

    def callback(hooks) -> None:
        hooks.target_started("base")
        hooks.log("base build line 1")
        entered.set()
        assert release.wait(timeout=2.0)

    started = manager.start(
        asset_key="python:3.11-jammy-builder::default",
        include_base_image=True,
        include_agent_server_image=False,
        force=False,
        callback=callback,
    )

    assert started is True
    assert entered.wait(timeout=1.0) is True

    payload = manager.apply(
        {
            "rows": [
                {
                    "asset_key": "python:3.11-jammy-builder::default",
                    "base_image": {"present": False, "needs_update": False},
                    "agent_server_image": {"present": False, "needs_update": False},
                }
            ]
        }
    )

    row = payload["rows"][0]
    assert row["build_job"]["in_progress"] is True
    assert row["base_image"]["in_progress"] is True
    assert row["base_image"]["last_job_status"] == "running"
    assert "base build line 1" in row["base_image"]["job_log_tail"]
    assert row["agent_server_image"].get("in_progress") is not True

    detail_payload = manager.apply(
        {
            "row": {
                "asset_key": "python:3.11-jammy-builder::default",
                "base_image": {"present": False, "needs_update": False},
                "agent_server_image": {"present": False, "needs_update": False},
            }
        },
        include_logs=True,
    )
    detail_row = detail_payload["row"]
    assert detail_row["build_job"]["in_progress"] is True
    assert detail_row["base_image"]["in_progress"] is True
    assert "base build line 1" in detail_row["base_image"]["job_log_text"]

    release.set()


def test_stage2_image_asset_build_manager_records_failed_job_error() -> None:
    manager = _Stage2ImageAssetBuildJobManager()

    def callback(hooks) -> None:
        hooks.target_started("agent")
        hooks.log("agent build line 1")
        raise RuntimeError("image build boom")

    started = manager.start(
        asset_key="python:3.11-jammy-builder::cn",
        include_base_image=False,
        include_agent_server_image=True,
        force=False,
        callback=callback,
    )

    assert started is True

    payload = None
    for _ in range(50):
        payload = manager.apply(
            {
                "rows": [
                    {
                        "asset_key": "python:3.11-jammy-builder::cn",
                        "base_image": {"present": True, "needs_update": False},
                        "agent_server_image": {"present": False, "needs_update": False},
                    }
                ]
            }
        )
        row = payload["rows"][0]
        if row["agent_server_image"].get("last_job_status") == "failed":
            break
        time.sleep(0.02)

    assert payload is not None
    row = payload["rows"][0]
    assert row["build_job"]["in_progress"] is False
    assert row["agent_server_image"]["in_progress"] is False
    assert row["agent_server_image"]["last_job_status"] == "failed"
    assert "image build boom" in row["agent_server_image"]["last_job_error"]
    detail = manager.detail_payload("python:3.11-jammy-builder::cn")
    assert "agent build line 1" in detail["agent"]["log_text"]


def test_stage2_image_asset_build_manager_cancel_marks_job_cancelled() -> None:
    manager = _Stage2ImageAssetBuildJobManager()
    entered = threading.Event()
    cancelled_seen = threading.Event()

    def callback(hooks) -> None:
        hooks.target_started("base")
        entered.set()
        for _ in range(50):
            if hooks.cancel_requested():
                cancelled_seen.set()
                raise InterruptedError("cancelled")
            time.sleep(0.01)
        raise AssertionError("cancel request was not observed")

    assert manager.start(
        asset_key="python:3.11-jammy-builder::default",
        include_base_image=True,
        include_agent_server_image=False,
        force=False,
        callback=callback,
    ) is True
    assert entered.wait(timeout=1.0) is True
    assert manager.cancel("python:3.11-jammy-builder::default") is True
    assert cancelled_seen.wait(timeout=1.0) is True

    payload = None
    for _ in range(50):
        payload = manager.apply(
            {
                "rows": [
                    {
                        "asset_key": "python:3.11-jammy-builder::default",
                        "base_image": {"present": False, "needs_update": False},
                        "agent_server_image": {"present": False, "needs_update": False},
                    }
                ]
            }
        )
        row = payload["rows"][0]
        if row["base_image"].get("last_job_status") == "cancelled":
            break
        time.sleep(0.01)

    assert payload is not None
    row = payload["rows"][0]
    assert row["build_job"]["in_progress"] is False
    assert row["base_image"]["in_progress"] is False
    assert row["base_image"]["last_job_status"] == "cancelled"
    assert "后台下载任务已取消" in row["base_image"]["job_log_tail"]


def test_prewarm_stage2_sdk_builds_planner_agent_server(monkeypatch) -> None:
    calls: list[tuple[str, bool]] = []

    monkeypatch.setattr(
        image_assets,
        "prewarm_host_sdk_environment",
        lambda: {
            "target": "host_environment",
            "python_version": "3.13",
            "command": ["uv", "run"],
            "marker_path": "/tmp/marker.json",
            "stdout_tail": "ready",
            "stderr_tail": "",
        },
    )
    monkeypatch.setattr(
        image_assets,
        "prewarm_planner_agent_server_image",
        lambda **kwargs: calls.append(("planner", kwargs["force"]))
        or {
            "target": "planner",
            "base_image_ref": image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE,
            "agent_server_image_ref": f"agent:{image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE}",
            "log_tail": "planner ready",
        },
    )
    monkeypatch.setattr(image_assets, "stage2_sdk_prewarm_status", lambda **_kwargs: {"status": "ready"})

    payload = prewarm_stage2_sdk(
        force=True,
        platform_name="linux/amd64",
    )

    assert payload["status"] == {"status": "ready"}
    assert payload["built"] == [
        {
            "target": "host_environment",
            "python_version": "3.13",
            "command": ["uv", "run"],
            "marker_path": "/tmp/marker.json",
            "stdout_tail": "ready",
            "stderr_tail": "",
        },
        {
            "target": "planner",
            "base_image_ref": image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE,
            "agent_server_image_ref": f"agent:{image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE}",
            "log_tail": "planner ready",
        },
    ]
    assert calls == [("planner", True)]


def test_stage2_sdk_prewarm_manager_exposes_live_log_tail() -> None:
    manager = _Stage2SdkPrewarmJobManager()
    release = threading.Event()

    def callback(log_callback):
        log_callback("docker build line 1")
        log_callback("docker build line 2")
        release.wait(timeout=2)

    assert manager.start("planner", callback) is True

    for _ in range(50):
        payload = manager.apply({"planner": {}})
        if "docker build line 2" in payload["planner"].get("log_tail", ""):
            break
        time.sleep(0.01)
    else:
        raise AssertionError("live prewarm log tail was not exposed")

    assert payload["planner"]["in_progress"] is True
    assert "Planner agent-server 镜像预热任务正在后台执行" not in payload["planner"]["log_tail"]
    release.set()

    for _ in range(50):
        payload = manager.apply({"planner": {}})
        if not payload["planner"].get("in_progress"):
            break
        time.sleep(0.01)

    assert payload["planner"].get("in_progress") is not True


def test_stage2_sdk_prewarm_manager_cancel_sets_cancel_request() -> None:
    manager = _Stage2SdkPrewarmJobManager()
    entered = threading.Event()
    cancelled_seen = threading.Event()

    def callback(log_callback, cancel_requested):
        log_callback("docker build line 1")
        entered.set()
        for _ in range(50):
            if cancel_requested():
                cancelled_seen.set()
                raise InterruptedError("cancelled")
            time.sleep(0.01)
        raise AssertionError("cancel request was not observed")

    assert manager.start("planner", callback) is True
    assert entered.wait(timeout=1.0) is True
    assert manager.cancel("planner") is True
    assert cancelled_seen.wait(timeout=1.0) is True

    payload = None
    for _ in range(50):
        payload = manager.apply({"planner": {}})
        if payload["planner"].get("last_status") == "cancelled":
            break
        time.sleep(0.01)

    assert payload is not None
    assert payload["planner"].get("in_progress") is not True
    assert payload["planner"].get("last_status") == "cancelled"
    assert "后台预热任务已取消" in payload["planner"].get("log_tail", "")


def test_prewarm_host_sdk_environment_streams_log_callback(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    marker: dict[str, object] = {}
    monkeypatch.setattr(image_assets, "openhands_sdk_root", lambda: sdk_root)
    monkeypatch.setattr(image_assets, "project_root", lambda: tmp_path)
    monkeypatch.setattr(
        image_assets,
        "host_sdk_environment_fingerprint",
        lambda **_kwargs: {"fingerprint": "host"},
    )
    monkeypatch.setattr(
        image_assets,
        "write_host_sdk_prewarm_marker",
        lambda payload: marker.update(payload),
    )

    def fake_run_logged_subprocess(command, **kwargs):
        log_callback = kwargs["log_callback"]
        log_callback("uv install line 1")
        log_callback("uv install line 2")
        return subprocess.CompletedProcess(command, 0, stdout="uv install line 1\nuv install line 2", stderr="")

    monkeypatch.setattr(image_assets, "run_logged_subprocess", fake_run_logged_subprocess)
    logs: list[str] = []

    payload = image_assets.prewarm_host_sdk_environment(log_callback=logs.append)

    assert logs == ["uv install line 1", "uv install line 2"]
    assert "uv install line 2" in payload["log_tail"]
    assert "uv install line 2" in str(marker["log_tail"])


def test_prewarm_planner_agent_server_image_streams_log_callback(monkeypatch, tmp_path) -> None:
    marker: dict[str, object] = {}
    dockerfile = tmp_path / "openhands_agent_server.Dockerfile"
    dockerfile.write_text("# syntax=docker/dockerfile:1.7\nFROM ubuntu:22.04\n", encoding="utf-8")
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE", dockerfile)
    monkeypatch.setattr(image_assets, "detect_platform", lambda: "linux/arm64")
    monkeypatch.setattr(
        image_assets,
        "agent_server_build_plan",
        lambda *, base_image, platform_name: {
            "image_tag": "feature-factory/openhands-agent-server:test",
            "command": ["docker", "build", "."],
            "env": {"PATH": "/usr/bin"},
            "mirror_profile": "cn",
        },
    )
    monkeypatch.setattr(image_assets, "docker_image_status", lambda **_kwargs: {"present": False})
    monkeypatch.setattr(image_assets, "inspect_docker_image", lambda _image_ref: {"present": False})
    monkeypatch.setattr(
        image_assets,
        "planner_agent_server_fingerprint",
        lambda **_kwargs: {"fingerprint": "planner"},
    )
    monkeypatch.setattr(
        image_assets,
        "write_planner_agent_server_prewarm_marker",
        lambda payload: marker.update(payload),
    )

    def fake_run_logged_subprocess(command, **kwargs):
        log_callback = kwargs["log_callback"]
        log_callback("docker build line 1")
        log_callback("docker build line 2")
        return subprocess.CompletedProcess(command, 0, stdout="docker build line 1\ndocker build line 2", stderr="")

    monkeypatch.setattr(image_assets, "run_logged_subprocess", fake_run_logged_subprocess)
    logs: list[str] = []

    payload = image_assets.prewarm_planner_agent_server_image(log_callback=logs.append)

    assert logs[0].startswith("Using CN Docker image mirrors for public base images")
    assert logs[1:] == ["docker build line 1", "docker build line 2"]
    assert "docker build line 2" in payload["log_tail"]
    assert "docker build line 2" in str(marker["log_tail"])


def test_prewarm_planner_agent_server_image_uses_temporary_cn_mirrored_dockerfile(
    monkeypatch,
    tmp_path,
) -> None:
    dockerfile = tmp_path / "openhands_agent_server.Dockerfile"
    dockerfile.write_text(
        "# syntax=docker/dockerfile:1.7\nFROM ubuntu:22.04\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE", dockerfile)
    monkeypatch.setattr(image_assets, "detect_platform", lambda: "linux/amd64")
    monkeypatch.setattr(
        image_assets,
        "agent_server_build_plan",
        lambda *, base_image, platform_name: {
            "image_tag": "feature-factory/openhands-agent-server:test",
            "command": [
                "docker",
                "build",
                "--progress=plain",
                "--file",
                str(dockerfile),
                "--tag",
                "feature-factory/openhands-agent-server:test",
                str(tmp_path),
            ],
            "env": {"PATH": "/usr/bin"},
            "mirror_profile": "cn",
            "labels": {"demo": "label"},
        },
    )
    monkeypatch.setattr(image_assets, "inspect_docker_image", lambda _image_ref: {"present": False, "labels": {}})
    monkeypatch.setattr(
        image_assets,
        "planner_agent_server_fingerprint",
        lambda **_kwargs: {"fingerprint": "planner"},
    )
    monkeypatch.setattr(image_assets, "write_planner_agent_server_prewarm_marker", lambda _payload: None)
    monkeypatch.setattr(image_assets, "cleanup_replaced_docker_image", lambda **_kwargs: None)
    monkeypatch.setattr(image_assets, "cleanup_stale_agent_server_images", lambda **_kwargs: None)

    def fake_run_logged_subprocess(command, **_kwargs):
        dockerfile_arg = Path(command[command.index("--file") + 1])
        captured["command"] = list(command)
        captured["dockerfile_text"] = dockerfile_arg.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(image_assets, "run_logged_subprocess", fake_run_logged_subprocess)

    payload = image_assets.prewarm_planner_agent_server_image(log_callback=lambda _line: None)

    assert payload["agent_server_image_ref"] == "feature-factory/openhands-agent-server:test"
    assert captured["dockerfile_text"] == (
        "# syntax=docker/dockerfile:1.7\n"
        "FROM docker.1ms.run/library/ubuntu:22.04\n"
    )
    assert dockerfile.read_text(encoding="utf-8") == "# syntax=docker/dockerfile:1.7\nFROM ubuntu:22.04\n"


def test_agent_server_build_plan_uses_cn_docker_registry_mirrors(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    monkeypatch.delenv("FEATURE_FACTORY_STAGE2_AGENT_PYTHON_IMAGE", raising=False)
    monkeypatch.setattr(image_assets, "openhands_sdk_root", lambda: tmp_path)
    monkeypatch.setattr(
        image_assets,
        "resolve_agent_server_build_args",
        lambda: (
            "cn",
            {
                "AGENT_PYTHON_IMAGE": "docker.1ms.run/library/python:3.13-bookworm",
            },
        ),
    )
    monkeypatch.setattr(
        image_assets,
        "git_stdout",
        lambda _root, *args, fallback: "abcdef1234567890" if "rev-parse" in args else "main",
    )

    plan = image_assets.agent_server_build_plan(
        base_image=image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name="linux/arm64",
    )

    command = list(plan["command"])
    assert plan["mirror_profile"] == "cn"
    assert (
        "BASE_IMAGE=docker.1ms.run/nikolaik/python-nodejs:python3.13-nodejs22-slim"
        in command
    )
    assert "AGENT_PYTHON_IMAGE=docker.1ms.run/library/python:3.13-bookworm" in command


def test_agent_server_build_plan_keeps_docker_mirrors_for_custom_cn_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    monkeypatch.setattr(image_assets, "openhands_sdk_root", lambda: tmp_path)
    monkeypatch.setattr(
        image_assets,
        "resolve_agent_server_build_args",
        lambda: (
            "custom-deadbeef",
            {
                "AGENT_PYTHON_IMAGE": "docker.1ms.run/library/python:3.13-bookworm",
            },
        ),
    )
    monkeypatch.setattr(
        image_assets,
        "git_stdout",
        lambda _root, *args, fallback: "abcdef1234567890" if "rev-parse" in args else "main",
    )

    plan = image_assets.agent_server_build_plan(
        base_image=image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name="linux/amd64",
    )

    command = list(plan["command"])
    assert plan["mirror_profile"] == "custom-deadbeef"
    assert (
        "BASE_IMAGE=docker.1ms.run/nikolaik/python-nodejs:python3.13-nodejs22-slim"
        in command
    )
    assert "AGENT_PYTHON_IMAGE=docker.1ms.run/library/python:3.13-bookworm" in command


def test_agent_server_build_plan_keeps_local_base_image_with_cn_profile(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setattr(image_assets, "openhands_sdk_root", lambda: tmp_path)
    monkeypatch.setattr(
        image_assets,
        "git_stdout",
        lambda _root, *args, fallback: "abcdef1234567890" if "rev-parse" in args else "main",
    )

    plan = image_assets.agent_server_build_plan(
        base_image="feature-factory/stage3-breaker-runtime:snapshot-linux_arm64",
        platform_name="linux/arm64",
    )

    assert "BASE_IMAGE=feature-factory/stage3-breaker-runtime:snapshot-linux_arm64" in plan["command"]


def test_ensure_base_image_built_uses_temporary_cn_mirrored_dockerfile(monkeypatch, tmp_path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM ubuntu:22.04\n", encoding="utf-8")
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        "feature_factory.stage2.base_images.docker_image_labels_match",
        lambda **_kwargs: False,
    )

    def fake_run_logged_subprocess(command, **kwargs):
        dockerfile_arg = Path(command[command.index("--file") + 1])
        captured["command"] = list(command)
        captured["dockerfile_text"] = dockerfile_arg.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        "feature_factory.stage2.base_images.run_logged_subprocess",
        fake_run_logged_subprocess,
    )

    ensure_base_image_built(
        source_root=tmp_path,
        base_image_payload={
            "image_id": "python:demo",
            "image_ref": "feature-factory/python:demo-cn",
            "asset_path": "Dockerfile",
            "network_profile": "cn",
            "asset_key": "python:demo::cn",
        },
        platform_name="linux/arm64",
    )

    assert captured["dockerfile_text"] == "FROM docker.1ms.run/library/ubuntu:22.04\n"
    assert dockerfile.read_text(encoding="utf-8") == "FROM ubuntu:22.04\n"


def test_ensure_agent_server_image_built_uses_temporary_cn_mirrored_dockerfile(monkeypatch, tmp_path) -> None:
    dockerfile = tmp_path / "openhands_agent_server.Dockerfile"
    dockerfile.write_text(
        "# syntax=docker/dockerfile:1.7\nFROM ubuntu:22.04\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_AGENT_SERVER_DOCKERFILE", dockerfile)
    monkeypatch.setattr(
        image_assets,
        "agent_server_build_plan",
        lambda *, base_image, platform_name: {
            "image_tag": "feature-factory/openhands-agent-server:test",
            "command": [
                "docker",
                "build",
                "--progress=plain",
                "--file",
                str(dockerfile),
                "--tag",
                "feature-factory/openhands-agent-server:test",
                str(tmp_path),
            ],
            "env": {"PATH": "/usr/bin"},
            "mirror_profile": "cn",
            "labels": {"demo": "label"},
            "image_override": False,
        },
    )
    monkeypatch.setattr(image_assets, "inspect_docker_image", lambda _image_ref: {"present": False, "labels": {}})
    monkeypatch.setattr(image_assets, "record_planner_agent_server_ready", lambda **_kwargs: None)
    monkeypatch.setattr(image_assets, "cleanup_replaced_docker_image", lambda **_kwargs: None)
    monkeypatch.setattr(image_assets, "cleanup_stale_agent_server_images", lambda **_kwargs: None)

    def fake_run_logged_subprocess(command, **_kwargs):
        dockerfile_arg = Path(command[command.index("--file") + 1])
        captured["command"] = list(command)
        captured["dockerfile_text"] = dockerfile_arg.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(image_assets, "run_logged_subprocess", fake_run_logged_subprocess)

    resolved = image_assets.ensure_agent_server_image_built(
        base_image=image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE,
        platform_name="linux/amd64",
    )

    assert resolved == "feature-factory/openhands-agent-server:test"
    assert captured["dockerfile_text"] == (
        "# syntax=docker/dockerfile:1.7\n"
        "FROM docker.1ms.run/library/ubuntu:22.04\n"
    )
    assert dockerfile.read_text(encoding="utf-8") == "# syntax=docker/dockerfile:1.7\nFROM ubuntu:22.04\n"


def test_host_sdk_runtime_env_uses_temporary_cn_mirror_overrides(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    src_root = tmp_path / "src"
    src_root.mkdir()
    config_dir = tmp_path / "runtime-config"
    monkeypatch.setattr(
        image_assets,
        "resolve_agent_server_build_args",
        lambda: (
            "cn",
            {
                "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
                "UV_PYTHON_INSTALL_MIRROR": (
                    "https://ghfast.top/"
                    "https://github.com/astral-sh/python-build-standalone/releases/download"
                ),
            },
        ),
    )
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_STAGE2_HOST_SDK_RUNTIME_CONFIG_DIR", config_dir)

    env = image_assets.build_host_sdk_runtime_env(
        sdk_root=sdk_root,
        src_root=src_root,
        base_env={
            "PATH": "/usr/bin",
            "PYTHONPATH": "/tmp/existing-pythonpath",
            "VIRTUAL_ENV": "/tmp/main-venv",
        },
    )

    assert "VIRTUAL_ENV" not in env
    assert env["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"] == str(sdk_root.resolve())
    assert env["PYTHONUNBUFFERED"] == "1"
    assert env["PIP_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_DEFAULT_INDEX"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_PYTHON_INSTALL_MIRROR"].startswith("https://ghfast.top/")
    assert env["UV_CONFIG_FILE"] == str((config_dir / "uv.toml").resolve())
    assert env["PYTHONPATH"].startswith(str(src_root.resolve()))
    assert "/tmp/existing-pythonpath" in env["PYTHONPATH"]
    assert (config_dir / "uv.toml").read_text(encoding="utf-8") == (
        'python-install-mirror = "https://ghfast.top/'
        'https://github.com/astral-sh/python-build-standalone/releases/download"\n'
        "[[index]]\n"
        'url = "https://pypi.tuna.tsinghua.edu.cn/simple"\n'
        "default = true\n"
    )


def test_settings_stage2_llm_fields_fall_back_to_unprefixed_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-5.1-mini")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-token")

    settings = Settings(database_url=f"sqlite:///{tmp_path / 'settings.db'}")

    assert settings.stage2_llm_model == "openai/gpt-5.1-mini"
    assert settings.stage2_llm_base_url == "https://llm.example.test/v1"
    assert settings.stage2_llm_api_key is not None
    assert settings.stage2_llm_api_key.get_secret_value() == "secret-token"


def test_stage2_backend_reports_openhands_readiness_when_unavailable(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'backend-auto.db'}",
        stage2_openhands_sdk_root=tmp_path / "missing-openhands-sdk",
    )

    backend = build_stage2_backend(settings)

    assert isinstance(backend, OpenHandsStage2Backend)
    assert backend.backend_name == "openhands"
    assert "not ready" in backend.selection_detail


def test_openhands_bridge_failure_message_is_not_truncated(tmp_path) -> None:
    stderr_path = tmp_path / "worker-stderr.log"
    stdout_path = tmp_path / "worker-stdout.log"
    full_stderr = "bridge stderr begins\n" + ("x" * 5000) + "\nbridge stderr ends"
    stderr_path.write_text(full_stderr, encoding="utf-8")
    stdout_path.write_text("bridge stdout fallback", encoding="utf-8")
    backend = OpenHandsStage2Backend(
        Settings(database_url=f"sqlite:///{tmp_path / 'backend-failure.db'}"),
        selection_detail="test",
        ready=True,
        readiness_message="",
    )

    message = backend._format_bridge_failure(
        label="worker-attempt-1",
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    assert message == f"OpenHands worker-attempt-1 failed: {full_stderr}"
    assert message.endswith("bridge stderr ends")


def test_worker_timeout_budget_excludes_active_validate_tool_host_time() -> None:
    now = 0.0

    def clock() -> float:
        return now

    budget = _BridgeTimeoutBudget(timeout_seconds=10.0, clock=clock)

    now = 9.0
    assert budget.expired() is False
    budget.observe_event(
        Stage2BackendEvent(
            actor="validator",
            phase="container_worker",
            title="Validate smoke started",
            message="started",
            payload={
                "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
                "request_id": "request-1",
            },
        )
    )

    now = 109.0
    assert budget.expired() is False
    budget.observe_event(
        Stage2BackendEvent(
            actor="validator",
            phase="container_worker",
            title="Validate smoke completed",
            message="completed",
            payload={
                "operation": WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION,
                "status": WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                "request_id": "request-1",
                "duration_seconds": 100.0,
            },
        )
    )

    now = 109.9
    assert budget.expired() is False
    now = 110.1
    assert budget.expired() is True


def test_planner_prompt_template_mentions_readonly_base_image_assets() -> None:
    prompt_template = read_text_asset("planner_prompt.md")
    assert "$HOME/base_images" in prompt_template
    assert "base_images/node-22-bookworm.Dockerfile" in prompt_template
    assert '"status": "ready | abandoned | defect"' in prompt_template
    assert "GPU is intentionally unsupported" in prompt_template
    assert "CPU-only" in prompt_template
    assert "Runtime and Toolchain:" in prompt_template
    assert "Collect Strategy:" in prompt_template
    assert "Run Strategy:" in prompt_template
    assert "not identified from inspected repo" in prompt_template
    assert "none observed" in prompt_template
    assert "not the worker's final collected file set" in prompt_template
    assert "prefer deterministic discovery rules or filtering rules" in prompt_template
    assert "full clone or may use shallow target-commit fetch" in prompt_template
    assert "setuptools_scm" in prompt_template


def test_worker_prompt_template_mentions_validate_tool_and_base_image_reference() -> None:
    prompt_template = read_text_asset("worker_prompt.md")

    assert "{{VALIDATE_TOOL_NAME}}" in prompt_template
    assert "{{SELECTED_BASE_IMAGE_SUMMARY}}" in prompt_template
    assert "{{SELECTED_BASE_IMAGE_DOCKERFILE_PATH}}" in prompt_template
    assert "{{SELECTED_BASE_IMAGE_METADATA_PATH}}" in prompt_template
    assert "{{REGIONAL_NETWORK_CONTEXT_BLOCK}}" in prompt_template
    assert "{{SELECTED_BASE_IMAGE_JSON}}" not in prompt_template
    assert "{{RESULT_FILE}}" not in prompt_template
    assert "`result = \"smoke_passed\"`" in prompt_template
    assert "CPU-only" in prompt_template
    assert '"action": "collect"' in prompt_template
    assert '"action": "run"' in prompt_template
    assert '"status": "passed"' in prompt_template
    assert '"status": "failed"' in prompt_template
    assert '"log_preview"' in prompt_template
    assert "[truncated]" in prompt_template
    assert "no bind mounts" in prompt_template
    assert "ordinary test failures must still produce valid JSON" in prompt_template
    assert "Do not rely on a build-context directory named `repo`" in prompt_template
    assert "target_commit_sha" in prompt_template
    assert "Recommended working order:" in prompt_template
    assert "fall back to the canonical URL" not in prompt_template
    assert 'git clone "${GITHUB_PROXY_PREFIX}${REPOSITORY_URL}" /workspace/repo' in prompt_template
    assert '|| (rm -rf /workspace/repo && git clone "$REPOSITORY_URL" /workspace/repo)' not in prompt_template
    assert "Keep the full-clone pattern by default" in prompt_template
    assert 'git fetch --depth 1 origin "$REPOSITORY_COMMIT"' in prompt_template
    assert 'git remote add origin "${GITHUB_PROXY_PREFIX}${REPOSITORY_URL}"' in prompt_template


def test_openhands_agent_server_container_labels_include_run_and_app_instance() -> None:
    if _agent_server_container_labels is None:
        pytest.skip("openhands bridge is unavailable in this environment")

    runtime_dir = Path("/tmp/feature-factory-runtime")
    request = {
        "workspace_dir": "/tmp/stage2/runs/demo-run-id",
        "app_instance_id": "app-instance-123",
        "repository": {"full_name": "owner/demo-repo"},
    }

    labels = _agent_server_container_labels(
        request=request,
        runtime_dir=runtime_dir,
        role="worker",
    )

    assert labels["feature_factory.managed"] == "true"
    assert labels["feature_factory.stage"] == "stage2"
    assert labels["feature_factory.component"] == "agent-server"
    assert labels["feature_factory.role"] == "worker"
    assert labels["feature_factory.run_id"] == "demo-run-id"
    assert labels["feature_factory.repository"] == "owner/demo-repo"
    assert labels["feature_factory.app_instance_id"] == "app-instance-123"
    assert labels["feature_factory.runtime_dir"] == str(runtime_dir.resolve())
    assert labels["feature_factory.created_at"].endswith("Z")


def test_worker_resume_state_rewrites_stale_lineage_paths(tmp_path) -> None:
    if _prepare_worker_resume_conversation_state is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    current_run_id = "resume-target-run"
    stale_run_id = "resume-stale-run"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    source_checkpoint_root = tmp_path / "runtime" / current_run_id / "worker-checkpoint" / "current"
    source_conversations_path = source_checkpoint_root / "openhands" / "conversations"
    source_bash_events_path = source_checkpoint_root / "openhands" / "bash_events"
    source_conversation_dir = source_conversations_path / conversation_id.replace("-", "")
    source_conversation_dir.mkdir(parents=True, exist_ok=True)
    source_bash_events_path.mkdir(parents=True, exist_ok=True)
    (source_conversation_dir / "events").mkdir(parents=True, exist_ok=True)

    stale_runtime_root = tmp_path / "runtime" / stale_run_id
    stale_workspace_root = tmp_path / "runs" / stale_run_id
    (source_conversation_dir / "events" / "event-00001.json").write_text(
        json.dumps(
            {
                "observation": {
                    "full_output_save_dir": str(
                        stale_runtime_root
                        / "openhands"
                        / "worker"
                        / "conversations"
                        / conversation_id.replace("-", "")
                        / "observations"
                    ),
                    "metadata": {
                        "working_dir": str(stale_workspace_root / "repo"),
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    (source_bash_events_path / "bash-event-00001.json").write_text(
        json.dumps(
            {
                "log": {
                    "output_dir": str(stale_runtime_root / "openhands" / "worker" / "bash_events"),
                }
            }
        ),
        encoding="utf-8",
    )

    target_conversations_path = tmp_path / "runtime" / current_run_id / "openhands" / "worker" / "conversations"
    target_bash_events_path = tmp_path / "runtime" / current_run_id / "openhands" / "worker" / "bash_events"
    runtime_env = {
        "OH_CONVERSATIONS_PATH": str(target_conversations_path),
        "OH_BASH_EVENTS_DIR": str(target_bash_events_path),
    }

    _prepare_worker_resume_conversation_state(
        checkpoint={
            "conversation_id": conversation_id,
            "openhands": {
                "conversations_path": str(source_conversations_path),
                "bash_events_dir": str(source_bash_events_path),
            },
        },
        runtime_env=runtime_env,
        events_path=tmp_path / "events.jsonl",
    )

    rewritten_event = json.loads(
        (
            target_conversations_path
            / conversation_id.replace("-", "")
            / "events"
            / "event-00001.json"
        ).read_text(encoding="utf-8")
    )
    assert rewritten_event["observation"]["full_output_save_dir"] == str(
        (
            tmp_path
            / "runtime"
            / current_run_id
            / "openhands"
            / "worker"
            / "conversations"
            / conversation_id.replace("-", "")
            / "observations"
        ).resolve()
    )
    assert rewritten_event["observation"]["metadata"]["working_dir"] == str(
        (tmp_path / "runs" / current_run_id / "repo").resolve()
    )
    rewritten_bash_event = json.loads(
        (target_bash_events_path / "bash-event-00001.json").read_text(encoding="utf-8")
    )
    assert rewritten_bash_event["log"]["output_dir"] == str(
        (tmp_path / "runtime" / current_run_id / "openhands" / "worker" / "bash_events").resolve()
    )


def test_planner_regional_network_context_assets_are_editable_text() -> None:
    cn_context = read_text_asset("regional_network_context_cn.md")

    assert "{{GITHUB_PROXY_PREFIX}}https://github.com/..." in cn_context
    assert "git clone" in cn_context
    assert "raw.githubusercontent.com" in cn_context
    assert "China mirror mode" in cn_context
    assert "UBUNTU_APT_MIRROR={{UBUNTU_APT_MIRROR}}" in cn_context
    assert "PIP_INDEX_URL={{PIP_INDEX_URL}}" in cn_context
    assert "TORCH_CPU_FIND_LINKS={{TORCH_CPU_FIND_LINKS}}" in cn_context
    assert "UV_INDEX_URL={{UV_INDEX_URL}}" in cn_context
    assert "MINICONDA_DIST_MIRROR={{MINICONDA_DIST_MIRROR}}" in cn_context
    assert "NPM_REGISTRY={{NPM_REGISTRY}}" in cn_context
    assert "download.pytorch.org/whl/cpu" not in cn_context
    assert "fall back to the canonical URL" not in cn_context
    assert "mirror fails" not in cn_context
    assert "proxy fails" not in cn_context


def test_base_image_catalog_asset_paths_exist() -> None:
    root = asset_root()
    for image in list_base_images():
        assert image.asset_path is not None
        assert (root / image.asset_path).exists()
        assert image.china_asset_path is not None
        assert (root / image.china_asset_path).exists()


def test_non_python_base_images_include_python3_for_harbor_verifier() -> None:
    root = asset_root()
    for image in list_base_images():
        if image.runtime_family == "python":
            continue
        for asset_path in (image.asset_path, image.china_asset_path):
            assert asset_path is not None
            text = (root / asset_path).read_text(encoding="utf-8")
            assert "python3" in text


def test_base_image_catalog_switches_to_china_assets(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")

    payloads = [image.to_payload() for image in list_base_images()]

    assert payloads
    assert all(payload["network_profile"] == "cn" for payload in payloads)
    assert all(str(payload["asset_path"]).endswith("-cn.Dockerfile") for payload in payloads)
    assert all(str(payload["image_ref"]).endswith("-cn") for payload in payloads)
    assert all("china_asset_path" not in payload for payload in payloads)


def test_run_logged_subprocess_enforces_timeout_with_streamed_logs() -> None:
    logs: list[str] = []

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        run_logged_subprocess(
            [
                sys.executable,
                "-c",
                "import time; print('build started', flush=True); time.sleep(5)",
            ],
            timeout_seconds=1.0,
            log_callback=logs.append,
        )

    assert logs == ["build started"]
    assert "build started" in str(exc_info.value.output)


def test_run_logged_subprocess_honors_cancel_request_with_streamed_logs() -> None:
    logs: list[str] = []
    cancel = False

    def log_callback(line: str) -> None:
        nonlocal cancel
        logs.append(line)
        cancel = True

    with pytest.raises(InterruptedError, match="interrupted"):
        run_logged_subprocess(
            [
                sys.executable,
                "-c",
                "import time; print('build started', flush=True); time.sleep(5)",
            ],
            timeout_seconds=10.0,
            cancel_requested=lambda: cancel,
            log_callback=log_callback,
        )

    assert logs == ["build started"]


def test_planner_base_image_view_contains_only_active_catalog_assets(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    catalog = [image.to_payload() for image in list_base_images()]

    view_dir = prepare_base_image_asset_view(
        source_root=asset_root(),
        runtime_dir=tmp_path,
        base_image_catalog=catalog,
    )

    mounted_names = {path.name for path in view_dir.iterdir()}
    assert mounted_names
    assert all(name.endswith("-cn.Dockerfile") for name in mounted_names)
    assert "python-3.11-bookworm.Dockerfile" not in mounted_names
    assert "python-3.11-jammy.Dockerfile" not in mounted_names
    view_mode = stat.S_IMODE(view_dir.stat().st_mode)
    assert view_mode & stat.S_IROTH
    assert view_mode & stat.S_IXOTH
    for path in view_dir.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) & stat.S_IROTH


def test_cn_base_image_dockerfiles_bootstrap_apt_over_http_then_restore_https() -> None:
    root = asset_root() / "base_images"
    dockerfiles = sorted(root.glob("*-cn.Dockerfile"))

    assert dockerfiles
    for dockerfile in dockerfiles:
      text = dockerfile.read_text(encoding="utf-8")
      if "apt-get update" not in text:
          continue
      assert "_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/" in text
      assert "https://mirrors.tuna.tsinghua.edu.cn/" in text
      assert "ca-certificates" in text
      assert "|${" in text and "_BOOTSTRAP}|${" in text


def test_miniconda_dockerfiles_refresh_corrupt_cached_installers() -> None:
    root = asset_root()
    dockerfiles = [root / "openhands_agent_server.Dockerfile"]
    dockerfiles.extend(
        path
        for path in sorted((root / "base_images").glob("*.Dockerfile"))
        if "feature-factory-miniconda-installers" in path.read_text(encoding="utf-8")
    )

    assert len(dockerfiles) == 7
    for dockerfile in dockerfiles:
        text = dockerfile.read_text(encoding="utf-8")
        assert "download_miniconda_installer()" in text
        assert 'local installer_tmp="${installer_path}.download.$$"' in text
        assert "--retry 5 --retry-delay 2" in text
        assert 'mv -f "${installer_tmp}" "${installer_path}"' in text
        assert "install_miniconda()" in text
        assert 'rm -rf "${CONDA_DIR}"' in text
        assert "if ! install_miniconda; then" in text
        assert "Cached Miniconda installer failed; downloading a fresh copy" in text
        assert text.count('rm -f "${installer_path}"') >= 2


def test_regional_network_context_block_mentions_github_proxy_for_china(monkeypatch) -> None:
    if _regional_network_context_block is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")

    context = _regional_network_context_block()

    assert "Regional network context:" in context
    assert "ghfast.top" in context
    assert "git clone" in context
    assert "raw.githubusercontent.com" in context
    assert "gist.github.com" in context
    assert "SSH-key cloning" in context
    assert "apt/pip/conda/npm" in context
    assert "{{" not in context


def test_regional_network_context_block_uses_configured_mirror_values(monkeypatch) -> None:
    if _regional_network_context_block is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_IO_MIRROR", "https://docker.mirror.test")
    monkeypatch.setenv("FEATURE_FACTORY_GHCR_IO_MIRROR", "http://ghcr.mirror.test/")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_PIP_INDEX_URL", "https://pypi.mirror.test/simple")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://github-proxy.test")

    context = _regional_network_context_block()

    assert "docker.io/... -> docker.mirror.test/..." in context
    assert "ghcr.io/... -> ghcr.mirror.test/..." in context
    assert "PIP_INDEX_URL=https://pypi.mirror.test/simple" in context
    assert "TORCH_CPU_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cpu" in context
    assert "https://github-proxy.test/https://github.com/..." in context


def test_regional_network_context_block_uses_shared_github_proxy_queue_head(monkeypatch) -> None:
    if _regional_network_context_block is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test/","https://proxy-two.test/"]',
    )

    context = _regional_network_context_block()

    assert "https://proxy-one.test/https://github.com/..." in context
    assert "proxy-two.test" not in context
    assert '["https://' not in context


def test_regional_network_context_block_uses_dotenv_mirror_values(monkeypatch, tmp_path) -> None:
    if _regional_network_context_block is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    for env_name in (
        "FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS",
        "FEATURE_FACTORY_DOCKER_IO_MIRROR",
        "FEATURE_FACTORY_STAGE2_NPM_REGISTRY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setattr(docker_mirrors_module, "_PROJECT_ROOT", tmp_path)
    docker_mirrors_module._dotenv_values.cache_clear()
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS=1",
                "FEATURE_FACTORY_DOCKER_IO_MIRROR=dotenv-docker.mirror.test",
                "FEATURE_FACTORY_STAGE2_NPM_REGISTRY=https://npm.mirror.test",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    try:
        context = _regional_network_context_block()
    finally:
        docker_mirrors_module._dotenv_values.cache_clear()

    assert "docker.io/... -> dotenv-docker.mirror.test/..." in context
    assert "NPM_REGISTRY=https://npm.mirror.test" in context


def test_planner_prompt_omits_regional_network_context_when_not_in_china(monkeypatch) -> None:
    if _build_planner_prompt is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.delenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", raising=False)

    prompt = _build_planner_prompt(
        request={
            "repository": {
                "full_name": "owner/demo-repo",
                "html_url": "https://github.com/owner/demo-repo",
                "default_branch": "main",
                "target_commit_sha": "deadbeef",
            },
            "base_image_catalog": [],
        },
        result_file=Path("/tmp/planner_result.json"),
    )

    assert "Regional network context:" not in prompt
    assert "ghfast.top" not in prompt


def test_worker_prompt_includes_regional_network_context_when_in_china(monkeypatch) -> None:
    if _build_worker_prompt is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")

    prompt = _build_worker_prompt(
        request={
            "repository": {
                "full_name": "owner/demo-repo",
                "html_url": "https://github.com/owner/demo-repo",
                "default_branch": "main",
                "target_commit_sha": "deadbeef",
            },
            "planner_decision": {
                "guidance": "Runtime and Toolchain:\n- demo",
            },
        },
        dockerfile_path=Path("/tmp/Dockerfile"),
        run_script_path=Path("/tmp/run_script.sh"),
        selected_base_image={
            "image_ref": "feature-factory/python:3.12-noble-builder-cn",
            "runtime_family": "python",
            "runtime_version": "3.12",
            "os_family": "ubuntu-noble",
            "network_profile": "cn",
            "preinstalled_capabilities": ["python", "pip", "uv"],
        },
        selected_base_image_dockerfile_path="/workspace/.stage2/base_image/selected_base_image.Dockerfile",
        selected_base_image_metadata_path="/workspace/.stage2/base_image/selected_base_image.metadata.json",
    )

    assert "Regional network context:" in prompt
    assert "PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple" in prompt
    assert "TORCH_CPU_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cpu" in prompt
    assert "ghfast.top" in prompt


def test_worker_prompt_omits_regional_network_context_when_not_in_china(monkeypatch) -> None:
    if _build_worker_prompt is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.delenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", raising=False)

    prompt = _build_worker_prompt(
        request={
            "repository": {
                "full_name": "owner/demo-repo",
                "html_url": "https://github.com/owner/demo-repo",
                "default_branch": "main",
                "target_commit_sha": "deadbeef",
            },
            "planner_decision": {
                "guidance": "Runtime and Toolchain:\n- demo",
            },
        },
        dockerfile_path=Path("/tmp/Dockerfile"),
        run_script_path=Path("/tmp/run_script.sh"),
        selected_base_image={
            "image_ref": "feature-factory/python:3.12-noble-builder",
            "runtime_family": "python",
            "runtime_version": "3.12",
            "os_family": "ubuntu-noble",
            "network_profile": "default",
            "preinstalled_capabilities": ["python", "pip", "uv"],
        },
        selected_base_image_dockerfile_path="/workspace/.stage2/base_image/selected_base_image.Dockerfile",
        selected_base_image_metadata_path="/workspace/.stage2/base_image/selected_base_image.metadata.json",
    )

    assert "Regional network context:" not in prompt
    assert "ghfast.top" not in prompt


def test_selected_base_image_metadata_reference_is_trimmed(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    selected_base_image = next(
        image.to_payload()
        for image in list_base_images()
        if image.image_id == "python:3.12-noble-builder"
    )
    stage2_dir = tmp_path / ".stage2"
    stage2_dir.mkdir()
    stage2_dir.chmod(0o777)

    dockerfile_path, metadata_path = materialize_selected_base_image_reference(
        source_root=asset_root(),
        workspace_dir=tmp_path,
        base_image_payload=selected_base_image,
    )

    metadata = json.loads(metadata_path.read_text())

    assert metadata == {
        "image_ref": "feature-factory/python:3.12-noble-builder-cn",
        "runtime_family": "python",
        "runtime_version": "3.12",
        "os_family": "ubuntu-noble",
        "network_profile": "cn",
        "preinstalled_capabilities": [
            "ubuntu",
            "python",
            "pip",
            "venv",
            "miniconda",
            "conda",
            "uv",
            "build-essential",
            "gfortran",
            "cmake",
            "ninja",
            "pkg-config",
            "openblas",
            "lapack",
            "git",
            "curl",
            "wget",
        ],
    }
    stage2_dir_mode = stat.S_IMODE(stage2_dir.stat().st_mode)
    assert stage2_dir_mode & stat.S_IROTH
    assert stage2_dir_mode & stat.S_IXOTH
    assert not stage2_dir_mode & stat.S_IWGRP
    assert not stage2_dir_mode & stat.S_IWOTH
    support_dir_mode = stat.S_IMODE(metadata_path.parent.stat().st_mode)
    assert support_dir_mode & stat.S_IROTH
    assert support_dir_mode & stat.S_IXOTH
    assert not support_dir_mode & stat.S_IWGRP
    assert not support_dir_mode & stat.S_IWOTH
    assert stat.S_IMODE(dockerfile_path.stat().st_mode) & stat.S_IROTH
    assert stat.S_IMODE(metadata_path.stat().st_mode) & stat.S_IROTH
    assert not stat.S_IMODE(dockerfile_path.stat().st_mode) & stat.S_IWOTH
    assert not stat.S_IMODE(metadata_path.stat().st_mode) & stat.S_IWOTH


def test_selected_base_image_prompt_summary_is_trimmed(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    selected_base_image = next(
        image.to_payload()
        for image in list_base_images()
        if image.image_id == "python:3.12-noble-builder"
    )

    summary = selected_base_image_prompt_summary(selected_base_image)

    assert "feature-factory/python:3.12-noble-builder-cn" in summary
    assert "Runtime: `python 3.12`" in summary
    assert "OS family: `ubuntu-noble`" in summary
    assert "Network profile: `cn`" in summary
    assert "Preinstalled capabilities:" in summary
    assert "image_id" not in summary


def test_feature_factory_openhands_agent_server_dockerfile_is_headless() -> None:
    dockerfile = read_text_asset("openhands_agent_server.Dockerfile")
    bridge = (Path(__file__).resolve().parents[1] / "src/feature_factory/stage2/openhands_bridge.py").read_text()
    worker_prompt = read_text_asset("worker_prompt.md")

    assert "FROM ${BASE_IMAGE} AS agent-server" in dockerfile
    assert "ARG PIP_INDEX_URL=" in dockerfile
    assert "ARG TORCH_CPU_FIND_LINKS=" in dockerfile
    assert "ARG UV_INDEX_URL=" in dockerfile
    assert "ARG DEBIAN_APT_MIRROR=" in dockerfile
    assert "ARG MINICONDA_DIST_MIRROR=" in dockerfile
    assert "CONDA_DIR=/opt/conda" in dockerfile
    assert "python-install-mirror" in dockerfile
    assert 'mkdir -p "/home/${USERNAME}/.config/uv"; \\' in dockerfile
    assert 'if [ -n "${MINICONDA_DIST_MIRROR}" ]; then' in dockerfile
    assert "registry.npmmirror.com" in bridge
    assert "FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS" in bridge
    assert "OH_CONVERSATIONS_PATH" in bridge
    assert "FEATURE_FACTORY_STAGE2_OPENHANDS_CONVERSATIONS_PATH" in bridge
    assert "This system is CPU-only" in worker_prompt
    assert "llm_completion_archive_dir" in bridge
    assert "_VALIDATE_BUDGET_EXEMPT_FEEDBACK_CODES" in bridge
    assert '"code": "VALIDATE_ARTIFACT_READ_FAILED"' in bridge
    assert '"ARTIFACT_SCHEMA_INVALID"' in bridge
    assert "_feedback_uses_validate_budget(feedback)" in bridge
    assert '"pause_for_checkpoint": self.checkpoint_enabled and counts_toward_validate_budget' in bridge
    assert '"pause_for_checkpoint": False' in bridge
    assert '"result": "retry" if can_retry else "failed"' in bridge
    assert '"retryable": can_retry' in bridge
    assert "log_completions=True" in bridge
    assert "openhands.agent_server" in dockerfile
    assert "planner_agent_server_image_ref" in bridge
    assert "worker_base_image_ref" in bridge
    assert "worker_agent_server_image_ref" in bridge
    assert "timeout_seconds=_build_timeout_from_request(request)" in bridge
    assert "xfce4" not in dockerfile
    assert "chromium" not in dockerfile
    assert "novnc" not in dockerfile
    assert "download.docker.com" not in dockerfile
    assert "cli.github.com" not in dockerfile


def test_openhands_runtime_env_creates_container_writable_dirs(monkeypatch, tmp_path) -> None:
    if _openhands_server_runtime_env is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    monkeypatch.delenv("FEATURE_FACTORY_STAGE2_OPENHANDS_CONVERSATIONS_PATH", raising=False)
    monkeypatch.delenv("FEATURE_FACTORY_STAGE2_OPENHANDS_BASH_EVENTS_DIR", raising=False)

    runtime_dir = tmp_path / "runtime"
    openhands_root_dir = runtime_dir / "openhands"
    openhands_root_dir.mkdir(parents=True)
    openhands_root_dir.chmod(0o777)
    observation_dir = openhands_root_dir / "planner" / "conversations" / "conversation-id" / "observations"
    observation_dir.mkdir(parents=True)
    observation_path = observation_dir / "bash_output_deadbeef.txt"
    observation_path.write_text("large terminal output\n", encoding="utf-8")
    bash_event_dir = openhands_root_dir / "planner" / "bash_events" / "nested"
    bash_event_dir.mkdir(parents=True)
    bash_event_path = bash_event_dir / "event.json"
    bash_event_path.write_text("{}\n", encoding="utf-8")
    for path in (observation_dir, bash_event_dir):
        path.chmod(0o700)
    for path in (observation_path, bash_event_path):
        path.chmod(0o600)

    env = _openhands_server_runtime_env(runtime_dir=runtime_dir, role="planner")
    conversations_path = Path(env["OH_CONVERSATIONS_PATH"])
    bash_events_dir = Path(env["OH_BASH_EVENTS_DIR"])
    role_runtime_dir = conversations_path.parent

    root_mode = stat.S_IMODE(openhands_root_dir.stat().st_mode)
    assert root_mode & stat.S_IROTH
    assert root_mode & stat.S_IXOTH
    assert not root_mode & stat.S_IWGRP
    assert not root_mode & stat.S_IWOTH

    for path in (role_runtime_dir, conversations_path, bash_events_dir):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IWOTH
        assert mode & stat.S_IXOTH
    for path in (observation_dir, bash_event_dir):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IWOTH
        assert mode & stat.S_IXOTH
    for path in (observation_path, bash_event_path):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IWOTH


def test_docker_workspace_mounts_only_openhands_runtime_root(
    monkeypatch,
    tmp_path,
) -> None:
    if _docker_workspace is None or openhands_bridge_module is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    captured: dict[str, object] = {}

    class FakeDockerWorkspace:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def __enter__(self):
            return SimpleNamespace(container_id="fake-container")

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

    monkeypatch.setattr(openhands_bridge_module, "DockerWorkspace", FakeDockerWorkspace)
    monkeypatch.setattr(openhands_bridge_module, "_detect_platform", lambda: "linux/amd64")

    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    workspace_dir.mkdir()
    runtime_dir.mkdir()
    (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (runtime_dir / "llm-completions").mkdir()

    with _docker_workspace(
        workspace_dir,
        base_image="feature-factory/runtime:unused",
        server_image_override="feature-factory/agent-server:test",
        mount_target="/workspace",
        working_dir="/workspace",
        runtime_dir=runtime_dir,
        openhands_role="planner",
        extra_volumes=[],
        labels={},
    ):
        pass

    volumes = list(captured["volumes"])
    openhands_root_dir = runtime_dir / "openhands"
    assert f"{workspace_dir.resolve()}:/workspace" in volumes
    assert f"{runtime_dir.resolve()}:{runtime_dir.resolve()}" not in volumes
    assert f"{openhands_root_dir.resolve()}:{openhands_root_dir.resolve()}" in volumes
    assert not any("llm-completions" in volume for volume in volumes)
    assert not any("events.jsonl" in volume for volume in volumes)


def test_openhands_runtime_artifact_permission_repair_is_scoped(
    monkeypatch,
    tmp_path,
) -> None:
    if _ensure_openhands_runtime_artifacts_writable is None or openhands_bridge_module is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    conversations_path = tmp_path / "runtime" / "openhands" / "planner" / "conversations"
    bash_events_path = tmp_path / "runtime" / "openhands" / "planner" / "bash_events"
    unrelated_secret_path = tmp_path / "runtime" / "secret.txt"
    unrelated_secret_path.parent.mkdir(parents=True)
    unrelated_secret_path.write_text("do not expose\n", encoding="utf-8")
    unrelated_secret_path.chmod(0o600)

    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(openhands_bridge_module.subprocess, "run", fake_run)

    _ensure_openhands_runtime_artifacts_writable(
        runtime_env={
            "OH_CONVERSATIONS_PATH": str(conversations_path),
            "OH_BASH_EVENTS_DIR": str(bash_events_path),
        },
        workspace=SimpleNamespace(_container_id="container-1"),
    )

    assert len(calls) == 2
    repair_script = calls[0][-1]
    smoke_script = calls[1][-1]
    assert calls[0][:4] == ["docker", "exec", "--user", "root"]
    assert calls[1][:2] == ["docker", "exec"]
    assert "--user" not in calls[1]
    assert "chmod -R a+rwX" in repair_script
    assert "chmod -R a+rwX" not in smoke_script
    for script in (repair_script, smoke_script):
        assert str(conversations_path) in script
        assert str(bash_events_path) in script
        assert str(unrelated_secret_path) not in script
    assert stat.S_IMODE(unrelated_secret_path.stat().st_mode) == 0o600


def test_openhands_runtime_artifact_permission_rejects_broad_paths(tmp_path) -> None:
    if _ensure_openhands_runtime_artifacts_writable is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    broad_path = tmp_path / "runtime"
    broad_path.mkdir()

    with pytest.raises(RuntimeError, match="OH_CONVERSATIONS_PATH"):
        _ensure_openhands_runtime_artifacts_writable(
            runtime_env={
                "OH_CONVERSATIONS_PATH": str(broad_path),
                "OH_BASH_EVENTS_DIR": str(broad_path / "bash_events"),
            },
            workspace=None,
        )


def test_make_container_writable_updates_restored_conversation_tree(tmp_path) -> None:
    if _make_container_writable is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    root = tmp_path / "conversation"
    nested = root / "nested"
    nested.mkdir(parents=True)
    state_path = nested / "state.json"
    state_path.write_text('{"ok": true}\n', encoding="utf-8")
    root.chmod(0o700)
    nested.chmod(0o700)
    state_path.chmod(0o600)

    _make_container_writable(root, recursive=True)

    for path in (root, nested):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IWOTH
        assert mode & stat.S_IXOTH
    assert stat.S_IMODE(state_path.stat().st_mode) & stat.S_IWOTH


def test_worker_workspace_snapshot_includes_shell_history_after_permission_fix(tmp_path) -> None:
    if _create_workspace_snapshot is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    (workspace_dir / "Dockerfile").write_text("FROM example\n", encoding="utf-8")
    history_path = workspace_dir / ".bash_history"
    history_path.write_text("secret shell history\n", encoding="utf-8")
    history_path.chmod(0o666)
    output_path = tmp_path / "snapshot.tar.gz"

    payload = _create_workspace_snapshot(workspace_dir=workspace_dir, output_path=output_path)

    assert payload["status"] == "saved"
    with tarfile.open(output_path, "r:gz") as archive:
        names = archive.getnames()
    assert "Dockerfile" in names
    assert ".bash_history" in names


def test_openhands_bridge_conversation_failure_context_extracts_error_event(
    monkeypatch,
) -> None:
    if _conversation_failure_context is None or _conversation_failure_message is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    class FakeConversationErrorEvent:
        source = "environment"
        code = "RuntimeError"
        detail = "worker conversation crashed inside agent-server"

        def model_dump(self, mode="json"):  # noqa: ARG002
            return {
                "source": self.source,
                "code": self.code,
                "detail": self.detail,
            }

        def __str__(self) -> str:
            return f"{self.code}: {self.detail}"

    class FakeEvents(list):
        def reconcile(self) -> int:
            self.append(FakeConversationErrorEvent())
            return 1

    class FakeState:
        id = "conversation-123"
        execution_status = "error"

        def __init__(self) -> None:
            self.events = FakeEvents()

        def refresh_from_server(self) -> None:
            self.execution_status = "error"

    class FakeConversation:
        def __init__(self) -> None:
            self.state = FakeState()

        def _get_last_error_detail(self) -> str:
            return ""

    def fake_docker_logs(command, **kwargs):  # noqa: ARG001
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="agent-server booted\nfinal server traceback line\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_docker_logs)
    exc = RuntimeError("Remote conversation ended with error")
    context = _conversation_failure_context(
        conversation_ref={
            "conversation": FakeConversation(),
            "workspace": SimpleNamespace(_container_id="container-123"),
        },
        exc=exc,
    )

    assert context["conversation_id"] == "conversation-123"
    assert context["execution_status"] == "error"
    assert context["refresh"]["state_refresh"] == "ok"
    assert context["refresh"]["event_reconcile"] == "ok"
    assert context["last_error_detail"] == "RuntimeError: worker conversation crashed inside agent-server"
    assert context["error_events"][0]["error_detail"] == (
        "RuntimeError: worker conversation crashed inside agent-server"
    )
    assert context["agent_server_logs"]["stdout"]["preview"].endswith("final server traceback line")
    message = _conversation_failure_message("OpenHands worker conversation failed", context, exc)
    assert "worker conversation crashed inside agent-server" in message
    assert "final server traceback line" in message


def test_openhands_bridge_validation_config_includes_p2p_file_count_limit() -> None:
    if _validation_config_from_request is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")

    config = _validation_config_from_request(
        {
            "validation": {
                "quickcheck_sample_size": 7,
                "collect_timeout_seconds": 11,
                "run_test_timeout_seconds": 13,
                "build_timeout_seconds": 17,
                "full_validation_timeout_seconds": 19,
                "docker_image_prefix": "feature-factory/test",
                "p2p_file_count_limit": 23,
                "p2p_sample_seed": "stage2-p2p-seed",
            }
        }
    )

    assert config.stage2_quickcheck_sample_size == 7
    assert config.stage2_p2p_file_count_limit == 23
    assert config.stage2_p2p_sample_seed == "stage2-p2p-seed"


def test_worker_checkpoint_rotation_keeps_only_latest_current_checkpoint(monkeypatch, tmp_path) -> None:
    if _WorkerValidationService is None or _read_worker_checkpoint_payload is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    workspace_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    request_dir.mkdir(parents=True)
    result_dir.mkdir(parents=True)
    (workspace_dir / ".stage2").mkdir()
    (workspace_dir / ".stage2" / "state.json").write_text('{"checkpoint": true}\n', encoding="utf-8")

    conversation_id = "12345678-1234-5678-1234-567812345678"
    live_conversations_dir = tmp_path / "live-conversations"
    (live_conversations_dir / conversation_id.replace("-", "")).mkdir(parents=True)
    (live_conversations_dir / conversation_id.replace("-", "") / "state.json").write_text(
        '{"conversation": 1}\n',
        encoding="utf-8",
    )
    live_bash_events_dir = tmp_path / "live-bash-events"
    live_bash_events_dir.mkdir(parents=True)
    (live_bash_events_dir / "events.json").write_text('{"bash": 1}\n', encoding="utf-8")
    monkeypatch.setenv("OH_CONVERSATIONS_PATH", str(live_conversations_dir))
    monkeypatch.setenv("OH_BASH_EVENTS_DIR", str(live_bash_events_dir))

    removed_images: list[str] = []

    def fake_commit_worker_container_checkpoint(*, run_id, attempt_index, workspace):  # noqa: ARG001
        return {
            "docker_commit_status": "saved",
            "docker_image_ref": f"feature-factory/stage2-worker-checkpoint:{run_id}-attempt-{attempt_index:03d}",
        }

    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._commit_worker_container_checkpoint",
        fake_commit_worker_container_checkpoint,
    )
    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._remove_worker_checkpoint_image",
        lambda image_ref: removed_images.append(str(image_ref)),
    )

    service = _WorkerValidationService(
        run_id="checkpoint-rotation-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )
    conversation_ref = {
        "conversation": type(
            "ConversationRef",
            (),
            {
                "state": type(
                    "ConversationStateRef",
                    (),
                    {
                        "id": conversation_id,
                        "execution_status": "paused",
                    },
                )()
            },
        )(),
        "workspace": type(
            "WorkspaceRef",
            (),
            {
                "host": "127.0.0.1",
                "host_port": 12345,
                "working_dir": "/workspace",
                "_container_id": "container-123",
            },
        )(),
    }

    checkpoint_one = service._create_checkpoint(
        attempt_payload={
            "attempt_index": 1,
            "result": "retry",
            "phase": "smoke",
            "request_id": "request-1",
            "dockerfile_path": "/workspace/Dockerfile",
            "run_script_path": "/workspace/run_script.sh",
        },
        conversation_ref=conversation_ref,
    )
    checkpoint_two = service._create_checkpoint(
        attempt_payload={
            "attempt_index": 2,
            "result": "smoke_passed",
            "phase": "sample",
            "request_id": "request-2",
            "dockerfile_path": "/workspace/Dockerfile",
            "run_script_path": "/workspace/run_script.sh",
        },
        conversation_ref=conversation_ref,
    )

    current_checkpoint_dir = runtime_dir / "worker-checkpoint" / "current"
    current_checkpoint_payload = _read_worker_checkpoint_payload(current_checkpoint_dir / "checkpoint.json")

    assert checkpoint_one["checkpoint_dir"] == str(current_checkpoint_dir)
    assert checkpoint_two["checkpoint_dir"] == str(current_checkpoint_dir)
    assert current_checkpoint_payload["attempt_index"] == 2
    assert current_checkpoint_payload["docker_image_ref"].endswith("attempt-002")
    assert removed_images == [
        "feature-factory/stage2-worker-checkpoint:checkpoint-rotation-run-attempt-001"
    ]
    assert not any(path.name.startswith(".tmp-") for path in (runtime_dir / "worker-checkpoint").iterdir())
    assert (current_checkpoint_dir / "workspace_snapshot.tar.gz").exists()
    assert (
        current_checkpoint_dir / "openhands" / "conversations" / conversation_id.replace("-", "")
    ).exists()
    assert (current_checkpoint_dir / "openhands" / "bash_events" / "events.json").exists()


def test_worker_validate_schema_error_does_not_persist_attempt(monkeypatch, tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    (workspace_dir / "candidate.Dockerfile").write_text("RUN echo missing-from\n", encoding="utf-8")
    (workspace_dir / "candidate_run.sh").write_text("#!/usr/bin/env bash\n# --action --out\n", encoding="utf-8")

    def fail_if_persisted(callback):  # noqa: ARG001
        raise AssertionError("schema-invalid validate calls must not persist attempts")

    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._with_stage2_service",
        fail_if_persisted,
    )

    service = _WorkerValidationService(
        run_id="schema-invalid-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )
    request_path = request_dir / "request-schema-invalid.json"
    request_path.write_text(
        json.dumps(
            {
                "dockerfile_path": "/workspace/candidate.Dockerfile",
                "run_script_path": "/workspace/candidate_run.sh",
            }
        ),
        encoding="utf-8",
    )

    service._handle_request(request_id="request-schema-invalid", request_path=request_path)

    result = json.loads((result_dir / "request-schema-invalid.json").read_text(encoding="utf-8"))
    assert result["result"] == "retry"
    assert result["attempt_index"] == 1
    assert result["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert result["pause_for_checkpoint"] is False


def test_worker_validate_result_payloads_are_container_readable_under_strict_umask(tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    service = _WorkerValidationService(
        run_id="result-permission-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )
    payload = {"request_id": "request-primary", "result": "retry"}
    old_umask = os.umask(0o077)
    try:
        primary_path = service._write_result_payload("request-primary", payload)  # noqa: SLF001
        (result_dir / "request-fallback.json").mkdir()
        fallback_path = service._write_result_payload(  # noqa: SLF001
            "request-fallback",
            {"request_id": "request-fallback", "result": "failed"},
            allow_request_sidecar_fallback=True,
        )
    finally:
        os.umask(old_umask)

    for path in (primary_path, fallback_path):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IROTH
        assert not mode & stat.S_IWOTH


def test_worker_validation_checkpoint_disabled_does_not_arm_checkpoint(tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)

    service = _WorkerValidationService(
        run_id="checkpoint-disabled-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
        checkpoint_enabled=False,
    )

    checkpoint_index = service.request_checkpoint_for_validate_observation(event=object())

    assert checkpoint_index == 0
    assert service.pending_checkpoint_attempt_index() == 0
    assert service.history_payload() == []
    assert not (workspace_dir / "Dockerfile").exists()
    assert not (runtime_dir / "worker-validation-attempts").exists()


def test_worker_validation_checkpoint_disabled_smoke_retry_does_not_pause(monkeypatch, tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    (workspace_dir / "candidate.Dockerfile").write_text("FROM python:3.12-slim-bookworm\n", encoding="utf-8")
    (workspace_dir / "candidate_run.sh").write_text("#!/usr/bin/env bash\n# --action --out\n", encoding="utf-8")

    persisted_calls: list[tuple[str, int | None]] = []

    class FakeStage2Service:
        def store_artifacts(self, run_id, *, dockerfile_text, run_script_text, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("artifacts", attempt_index))

        def store_collect_report(self, run_id, *, report, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("collect", attempt_index))

        def store_smoke_report(self, run_id, *, report, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("smoke", attempt_index))

    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._with_stage2_service",
        lambda callback: callback(FakeStage2Service()),
    )

    service = _WorkerValidationService(
        run_id="checkpoint-disabled-smoke-retry-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
        checkpoint_enabled=False,
    )
    collect_payload = {"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]}

    def fake_run_smoke(
        *,
        run_id,
        workspace_dir,
        dockerfile_text,
        run_script_text,
        on_collect_validated=None,
        emit_event=None,
        attempt_index=None,
    ):  # noqa: ARG001
        if on_collect_validated is not None:
            on_collect_validated(dict(collect_payload))
        return SimpleNamespace(
            passed=False,
            report={
                "validator": "smoke",
                "status": "failed",
                "phase": "sample",
                "collect": {"payload": collect_payload},
                "feedback": {
                    "code": "SMOKE_SAMPLE_BELOW_THRESHOLD",
                    "message": "fewer than half of sampled test files passed",
                    "retryable": True,
                },
            },
        )

    service.validator.run_smoke = fake_run_smoke
    request_path = request_dir / "request-smoke-retry.json"
    request_path.write_text(
        json.dumps(
            {
                "dockerfile_path": "/workspace/candidate.Dockerfile",
                "run_script_path": "/workspace/candidate_run.sh",
            }
        ),
        encoding="utf-8",
    )

    service._handle_request(request_id="request-smoke-retry", request_path=request_path)

    result = json.loads((result_dir / "request-smoke-retry.json").read_text(encoding="utf-8"))
    assert result["result"] == "retry"
    assert result["feedback"]["code"] == "SMOKE_SAMPLE_BELOW_THRESHOLD"
    assert result["pause_for_checkpoint"] is False
    assert service.pending_checkpoint_attempt_index() == 0
    assert persisted_calls == [("artifacts", 1), ("collect", 1), ("smoke", 1)]
    assert [attempt["attempt_index"] for attempt in service.history_payload()] == [1]
    assert not (runtime_dir / "worker-checkpoint" / "current").exists()


def test_worker_validate_artifact_read_does_not_fallback_to_container(
    monkeypatch,
    tmp_path,
) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    dockerfile_path = workspace_dir / "Dockerfile"
    dockerfile_path.write_text("host copy is unreadable\n", encoding="utf-8")

    service = _WorkerValidationService(
        run_id="artifact-read-no-fallback-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )
    original_read_text = Path.read_text

    def fake_read_text(path: Path, *args, **kwargs):  # noqa: ANN001
        if path == dockerfile_path:
            raise PermissionError("permission denied by host")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fake_read_text)

    with pytest.raises(PermissionError, match="permission denied by host"):
        service._read_worker_artifact_text(  # noqa: SLF001
            dockerfile_path,
            container_path="/workspace/Dockerfile",
        )


def test_write_host_artifact_copy_replaces_unreadable_container_file(tmp_path) -> None:
    if _write_host_artifact_copy is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    artifact_path = tmp_path / "Dockerfile"
    artifact_path.write_text("old\n", encoding="utf-8")
    artifact_path.chmod(0)
    try:
        _write_host_artifact_copy(artifact_path, "FROM python:3.13\n", executable=False)
        assert artifact_path.read_text(encoding="utf-8") == "FROM python:3.13\n"
        assert stat.S_IMODE(artifact_path.stat().st_mode) == 0o666
    finally:
        artifact_path.chmod(0o666)


def test_worker_validate_schema_error_is_retained_as_final_validation(tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    (workspace_dir / "candidate.Dockerfile").write_text("RUN echo missing-from\n", encoding="utf-8")
    (workspace_dir / "candidate_run.sh").write_text("#!/usr/bin/env bash\n# --action --out\n", encoding="utf-8")

    service = _WorkerValidationService(
        run_id="schema-invalid-final-validation-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )
    request_path = request_dir / "request-schema-invalid-final.json"
    request_path.write_text(
        json.dumps(
            {
                "dockerfile_path": "/workspace/candidate.Dockerfile",
                "run_script_path": "/workspace/candidate_run.sh",
            }
        ),
        encoding="utf-8",
    )

    service._handle_request(request_id="request-schema-invalid-final", request_path=request_path)

    final_validation = service.final_validation_payload()
    assert final_validation["status"] == "retry"
    assert final_validation["phase"] == "schema"
    assert final_validation["attempt_index"] == 1
    assert final_validation["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert final_validation["smoke_report"]["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert final_validation["passed"] is False


def test_openhands_backend_prefers_validate_feedback_when_worker_artifacts_are_missing(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'worker-artifacts-missing.db'}",
        stage2_openhands_sdk_root=tmp_path / "missing-openhands-sdk",
    )
    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)
    monkeypatch.setattr(backend, "_ready", True)
    monkeypatch.setattr(backend, "_readiness_message", "")
    _patch_stage2_backend_image_builds(monkeypatch)

    def fake_run_bridge_request(**_kwargs) -> dict[str, object]:
        return {
            "model": "openai/worker-model",
            "token_usage": {},
            "result": {
                "summary": "worker validation requested retry",
            },
            "validation_attempts": [
                {
                    "attempt_index": 1,
                    "smoke_report": {
                        "feedback": {
                            "code": "ARTIFACT_SCHEMA_INVALID",
                            "message": "Dockerfile is missing a FROM instruction",
                        }
                    },
                }
            ],
            "final_validation": {
                "status": "retry",
                "phase": "schema",
                "feedback": {
                    "code": "ARTIFACT_SCHEMA_INVALID",
                    "message": "Dockerfile is missing a FROM instruction",
                },
                "smoke_report": {
                    "feedback": {
                        "code": "ARTIFACT_SCHEMA_INVALID",
                        "message": "Dockerfile is missing a FROM instruction",
                    }
                },
            },
            "post_agent_full_validation_required": False,
        }

    monkeypatch.setattr(backend, "_run_bridge_request", fake_run_bridge_request)

    workspace_dir = tmp_path / "workspace"
    repo_dir = workspace_dir / "repo"
    repo_dir.mkdir(parents=True)
    decision = PlannerDecision(
        status="ready",
        base_image="python:3.11-jammy-builder",
        base_image_ref="feature-factory/python:3.11-jammy-builder",
        planner_model="openai/planner-model",
        planner_token_usage=0,
        worker_model="openai/worker-model",
        worker_token_usage=0,
        guidance="Generate Dockerfile and run_script.sh, then call validate.",
        base_image_catalog=[
            image.to_payload()
            for image in list_base_images()
            if image.image_id == "python:3.11-jammy-builder"
        ],
    )

    with pytest.raises(RuntimeError, match="Dockerfile is missing a FROM instruction"):
        backend.run_worker_attempt(
            repo_dir,
            _repository(),
            workspace_dir=workspace_dir,
            decision=decision,
            attempt_index=1,
        )


def test_worker_validate_uses_resume_attempt_offset_for_live_persistence(monkeypatch, tmp_path) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    (workspace_dir / "candidate.Dockerfile").write_text("FROM python:3.12-slim-bookworm\n", encoding="utf-8")
    (workspace_dir / "candidate_run.sh").write_text("#!/usr/bin/env bash\n# --action --out\n", encoding="utf-8")

    persisted_calls: list[tuple[str, int | None]] = []

    class FakeStage2Service:
        def store_artifacts(self, run_id, *, dockerfile_text, run_script_text, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("artifacts", attempt_index))

        def store_collect_report(self, run_id, *, report, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("collect", attempt_index))

        def store_smoke_report(self, run_id, *, report, attempt_index=None):  # noqa: ARG002
            persisted_calls.append(("smoke", attempt_index))

    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._with_stage2_service",
        lambda callback: callback(FakeStage2Service()),
    )

    service = _WorkerValidationService(
        run_id="resume-offset-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=5,
        request_dir=request_dir,
        result_dir=result_dir,
        initial_attempt_count=2,
    )
    collect_payload = {"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]}

    def fake_run_smoke(
        *,
        run_id,
        workspace_dir,
        dockerfile_text,
        run_script_text,
        on_collect_validated=None,
        emit_event=None,
        attempt_index=None,
    ):  # noqa: ARG001
        if on_collect_validated is not None:
            on_collect_validated(dict(collect_payload))
        return SimpleNamespace(
            passed=False,
            report={
                "validator": "smoke",
                "status": "failed",
                "phase": "sample",
                "collect": {"payload": collect_payload},
                "feedback": {
                    "code": "SMOKE_SAMPLE_BELOW_THRESHOLD",
                    "message": "fewer than half of sampled test files passed",
                    "retryable": True,
                },
            },
        )

    service.validator.run_smoke = fake_run_smoke
    request_path = request_dir / "request-resume-offset.json"
    request_path.write_text(
        json.dumps(
            {
                "dockerfile_path": "/workspace/candidate.Dockerfile",
                "run_script_path": "/workspace/candidate_run.sh",
            }
        ),
        encoding="utf-8",
    )

    service._handle_request(request_id="request-resume-offset", request_path=request_path)

    result = json.loads((result_dir / "request-resume-offset.json").read_text(encoding="utf-8"))
    event_payloads = [
        json.loads(line)["payload"]
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    timeout_budget_events = [
        payload
        for payload in event_payloads
        if payload.get("operation") == WORKER_VALIDATE_TIMEOUT_EXEMPT_OPERATION
    ]
    assert persisted_calls == [("artifacts", 3), ("collect", 3), ("smoke", 3)]
    assert result["attempt_index"] == 3
    assert result["pause_for_checkpoint"] is True
    assert [attempt["attempt_index"] for attempt in service.history_payload()] == [3]
    assert (runtime_dir / "worker-validation-attempts" / "attempt-003" / "attempt.json").exists()
    assert [event["status"] for event in timeout_budget_events] == [
        WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_STARTED,
        WORKER_VALIDATE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    ]
    assert timeout_budget_events[0]["request_id"] == "request-resume-offset"
    assert timeout_budget_events[1]["duration_seconds"] >= 0.0


def test_worker_validate_live_persistence_failure_falls_back_without_crashing(
    monkeypatch,
    tmp_path,
) -> None:
    if _WorkerValidationService is None:
        pytest.skip("OpenHands SDK is unavailable in this test environment")
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = runtime_dir / "events.jsonl"
    for directory in (workspace_dir, runtime_dir, request_dir, result_dir):
        directory.mkdir(parents=True)
    (workspace_dir / "candidate.Dockerfile").write_text(
        "FROM python:3.12-slim-bookworm\n",
        encoding="utf-8",
    )
    (workspace_dir / "candidate_run.sh").write_text(
        "#!/usr/bin/env bash\n# --action --out\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "feature_factory.stage2.openhands_bridge._with_stage2_service",
        lambda _callback: (_ for _ in ()).throw(ModuleNotFoundError("No module named 'psycopg'")),
    )

    service = _WorkerValidationService(
        run_id="persistence-fallback-run",
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        events_path=events_path,
        validation_config=Stage2ValidationConfig(
            stage2_quickcheck_sample_size=10,
            stage2_collect_timeout_seconds=30.0,
            stage2_run_test_timeout_seconds=45.0,
            stage2_build_timeout_seconds=60.0,
            stage2_full_validation_timeout_seconds=600.0,
            stage2_docker_image_prefix="feature-factory/stage2",
        ),
        max_validate_calls=3,
        request_dir=request_dir,
        result_dir=result_dir,
    )

    collect_payload = {"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]}

    def fake_run_smoke(
        *,
        run_id,
        workspace_dir,
        dockerfile_text,
        run_script_text,
        on_collect_validated=None,
        emit_event=None,
        attempt_index=None,
    ):  # noqa: ARG001
        if on_collect_validated is not None:
            on_collect_validated(dict(collect_payload))
        return SimpleNamespace(
            passed=False,
            report={
                "validator": "smoke",
                "status": "failed",
                "phase": "sample",
                "collect": {"payload": collect_payload},
                "feedback": {
                    "code": "SMOKE_SAMPLE_BELOW_THRESHOLD",
                    "message": "fewer than half of sampled test files passed",
                    "retryable": True,
                },
            },
        )

    service.validator.run_smoke = fake_run_smoke
    request_path = request_dir / "request-persistence-fallback.json"
    request_path.write_text(
        json.dumps(
            {
                "dockerfile_path": "/workspace/candidate.Dockerfile",
                "run_script_path": "/workspace/candidate_run.sh",
            }
        ),
        encoding="utf-8",
    )

    service._handle_request(
        request_id="request-persistence-fallback",
        request_path=request_path,
    )

    result = json.loads(
        (result_dir / "request-persistence-fallback.json").read_text(encoding="utf-8")
    )
    assert result["result"] == "retry"
    assert result["phase"] == "sample"
    assert result["feedback"]["code"] == "SMOKE_SAMPLE_BELOW_THRESHOLD"
    assert service.validation_attempts_persisted_live() is False
    assert [attempt["attempt_index"] for attempt in service.history_payload()] == [1]
    events_text = events_path.read_text(encoding="utf-8")
    assert "Validate live persistence unavailable" in events_text
    assert "No module named 'psycopg'" in events_text


def test_openhands_backend_uses_bridge_payloads_for_planner_and_worker(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "0")
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "package.json").write_text(
        '{\n'
        '  "name": "demo",\n'
        '  "private": true,\n'
        '  "scripts": {\n'
        '    "test": "vitest run"\n'
        "  }\n"
        "}\n"
    )
    (repo_dir / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n")
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "demo.test.ts").write_text("import { expect, test } from 'vitest';\n")

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'openhands.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage2_openhands_sdk_root=sdk_root,
        stage2_planner_llm_model="openai/planner-model",
        stage2_planner_openhands_preset="default",
        stage2_planner_openhands_max_iterations=111,
        stage2_worker_llm_model="openai/worker-model",
        stage2_worker_openhands_preset="gpt5",
        stage2_worker_openhands_max_iterations=222,
        stage2_p2p_file_count_limit=37,
        stage2_p2p_sample_seed="request-p2p-seed",
        stage2_build_timeout_seconds=777.0,
    )
    monkeypatch.setattr("feature_factory.stage2.backend.shutil.which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr("feature_factory.stage2.backend.detect_platform", lambda: "linux/arm64")
    build_calls: list[dict[str, object]] = []

    def fake_ensure_agent_server_image_built(**kwargs):
        build_calls.append({"kind": "agent", **kwargs})
        return f"agent:{kwargs['base_image']}"

    def fake_ensure_base_image_built(**kwargs):
        build_calls.append({"kind": "base", **kwargs})
        return str(kwargs["base_image_payload"]["image_ref"])

    monkeypatch.setattr(
        "feature_factory.stage2.backend.ensure_agent_server_image_built",
        fake_ensure_agent_server_image_built,
    )
    monkeypatch.setattr(
        "feature_factory.stage2.backend.ensure_base_image_built",
        fake_ensure_base_image_built,
    )
    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)

    def fake_run_bridge_request(*, request, workspace_dir, label, emit_event):
        if label == "planner":
            assert "fingerprint" not in request
            assert "detected_runtime" not in request
            assert "language_pack" not in request
            assert request["preset"] == "default"
            assert request["max_iterations"] == 111
            assert request["repository"] == {
                "full_name": "owner/demo-repo",
                "html_url": "https://github.com/owner/demo-repo",
                "default_branch": "main",
                "target_commit_sha": "",
            }
            assert any(item["image_id"] == "node:22-bookworm-builder" for item in request["base_image_catalog"])
            assert (
                request["planner_agent_server_image_ref"]
                == f"agent:{image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE}"
            )
            assert request["build_timeout_seconds"] == settings.stage2_build_timeout_seconds
            assert "planner_result_file" not in request
            return {
                "model": "openai/planner-model",
                "token_usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 40,
                    "reasoning_tokens": 5,
                },
                "result": {
                    "status": "ready",
                    "selected_base_image_id": "node:22-bookworm-builder",
                    "guidance": (
                        "Observed package.json defines a vitest test script and pnpm-lock.yaml exists. "
                        "Use pnpm from the lockfile, install repo dependencies, collect **/*.test.ts files, "
                        "and run one selected file with vitest run <file>."
                    ),
                },
            }
        assert "fingerprint" not in request["planner_decision"]
        assert request["preset"] == "gpt5"
        assert request["max_iterations"] == 222
        assert request["repository"] == {
            "full_name": "owner/demo-repo",
            "html_url": "https://github.com/owner/demo-repo",
            "default_branch": "main",
            "target_commit_sha": "",
        }
        assert request["max_validate_calls"] == settings.stage2_max_worker_attempts
        assert request["validation"]["collect_timeout_seconds"] == settings.stage2_collect_timeout_seconds
        assert request["validation"]["run_test_timeout_seconds"] == settings.stage2_run_test_timeout_seconds
        assert request["validation"]["p2p_file_count_limit"] == settings.stage2_p2p_file_count_limit
        assert request["validation"]["p2p_sample_seed"] == settings.stage2_p2p_sample_seed
        assert request["validation"]["build_timeout_seconds"] == settings.stage2_build_timeout_seconds
        assert (
            request["validation"]["full_validation_timeout_seconds"]
            == settings.stage2_full_validation_timeout_seconds
        )
        assert "worker_brief" not in request["planner_decision"]
        assert "repository_profile" not in request["planner_decision"]
        assert "evidence" not in request["planner_decision"]
        assert request["worker_base_image_ref"] == "feature-factory/node:22-bookworm-builder"
        assert request["worker_agent_server_image_ref"] == "agent:feature-factory/node:22-bookworm-builder"
        (workspace_dir / "Dockerfile").write_text("FROM feature-factory/node:22-bookworm-builder\n")
        (workspace_dir / "run_script.sh").write_text("#!/usr/bin/env bash\nset -euo pipefail\n")
        return {
            "model": "openai/worker-model",
            "token_usage": {
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "reasoning_tokens": 3,
            },
            "validation_attempts_persisted_live": False,
            "validation_attempts": [
                {
                    "attempt_index": 1,
                    "dockerfile_text": "FROM feature-factory/node:22-bookworm-builder\n",
                    "run_script_text": "#!/usr/bin/env bash\nset -euo pipefail\n",
                    "collect_report": {"action": "collect", "test_files": [{"path": "src/demo.test.ts"}]},
                    "smoke_report": {"validator": "smoke", "status": "passed"},
                    "full_report": {"validator": "full", "status": "passed", "summary": {"total_files": 1}},
                    "phase": "full",
                    "result": "passed",
                }
            ],
            "final_validation": {
                "status": "passed",
                "phase": "full",
                "passed": True,
                "full_report": {"validator": "full", "status": "passed", "summary": {"total_files": 1}},
            },
            "result": {
                "summary": "Installed dependencies from pyproject and produced stage2 artifacts.",
                "changes": ["added Dockerfile", "added run_script.sh"],
                "known_risks": [],
            },
        }

    monkeypatch.setattr(backend, "_run_bridge_request", fake_run_bridge_request)
    repository = _repository()
    backend_events = []

    decision = backend.plan_repository(repo_dir, repository, workspace_dir=tmp_path, emit_event=backend_events.append)

    assert decision.base_image == "node:22-bookworm-builder"
    assert decision.base_image_ref == "feature-factory/node:22-bookworm-builder"
    assert decision.planner_model == "openai/planner-model"
    assert decision.planner_token_usage == 165
    assert "pnpm-lock.yaml exists" in decision.guidance
    assert backend_events[0].title == "Planner sandbox starting"
    assert backend_events[0].payload["workspace_dir"] == str(tmp_path)
    assert backend_events[0].payload["repo_path"] == str(repo_dir)
    assert backend_events[0].payload["model"] == "openai/planner-model"
    assert backend_events[0].payload["preset"] == "default"
    assert backend_events[0].payload["max_iterations"] == 111
    assert backend_events[0].payload["timeout_seconds"] == 2400.0

    worker_attempt = backend.run_worker_attempt(
        repo_dir,
        repository,
        workspace_dir=tmp_path,
        decision=decision,
        attempt_index=1,
        last_smoke_report={"feedback": {"code": "SMOKE_SAMPLE_BELOW_THRESHOLD"}},
        emit_event=None,
    )

    assert worker_attempt.model == "openai/worker-model"
    assert worker_attempt.token_usage == 103
    assert worker_attempt.validation_attempts_persisted_live is False
    assert worker_attempt.strategy_payload["validate_call_limit"] == settings.stage2_max_worker_attempts
    assert worker_attempt.final_validation["status"] == "passed"
    assert worker_attempt.dockerfile_text.startswith("FROM feature-factory/node:22-bookworm-builder")
    assert [
        (
            call["kind"],
            call.get("base_image") or call.get("base_image_payload", {}).get("image_ref"),
            call["timeout_seconds"],
        )
        for call in build_calls
    ] == [
        ("agent", image_assets.PLANNER_AGENT_SERVER_BASE_IMAGE, 777.0),
        ("base", "feature-factory/node:22-bookworm-builder", 777.0),
        ("agent", "feature-factory/node:22-bookworm-builder", 777.0),
    ]


def test_openhands_backend_bridge_subprocess_uses_frozen_python_and_clean_env(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'openhands-bridge-command.db'}",
        stage2_openhands_sdk_root=sdk_root,
        stage2_planner_llm_model="openai/planner-model",
        stage2_worker_llm_model="openai/worker-model",
    )
    monkeypatch.setattr("feature_factory.stage2.backend.shutil.which", lambda _name: "/usr/bin/uv")
    monkeypatch.setattr(
        image_assets,
        "resolve_agent_server_build_args",
        lambda: (
            "cn",
            {
                "PIP_INDEX_URL": "https://pypi.tuna.tsinghua.edu.cn/simple",
                "UV_INDEX_URL": "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/",
                "UV_PYTHON_INSTALL_MIRROR": (
                    "https://ghfast.top/"
                    "https://github.com/astral-sh/python-build-standalone/releases/download"
                ),
            },
        ),
    )
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/feature-factory-main-venv")
    monkeypatch.setenv("PYTHONPATH", "/tmp/existing-pythonpath")
    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)

    command = backend._build_bridge_command(
        request_path=tmp_path / "planner-request.json",
        result_path=tmp_path / "planner-result.json",
        events_path=tmp_path / "planner-events.jsonl",
    )
    env = backend._build_bridge_env()

    assert "--frozen" in command
    assert "--with-editable" in command
    assert "--python" in command
    editable_index = command.index("--with-editable")
    assert command[editable_index + 1] == str(Path(__file__).resolve().parents[1])
    python_index = command.index("--python")
    assert command[python_index + 1] == OPENHANDS_BRIDGE_PYTHON_VERSION
    assert "VIRTUAL_ENV" not in env
    assert env["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"] == str(sdk_root.resolve())
    assert env["OPENHANDS_SUPPRESS_BANNER"] == "1"
    assert env["PIP_INDEX_URL"] == "https://pypi.tuna.tsinghua.edu.cn/simple"
    assert env["UV_INDEX_URL"] == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
    assert env["UV_DEFAULT_INDEX"] == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
    assert env["UV_PYTHON_INSTALL_MIRROR"].startswith("https://ghfast.top/")
    assert str((Path(__file__).resolve().parents[1] / "src")) in env["PYTHONPATH"]
    assert "/tmp/existing-pythonpath" in env["PYTHONPATH"]


def test_openhands_backend_agent_bridge_env_marks_bare_custom_endpoint_model(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    monkeypatch.delenv("FEATURE_FACTORY_LLM_SSL_VERIFY", raising=False)
    monkeypatch.delenv("SSL_VERIFY", raising=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'openhands-bridge-llm-env.db'}",
        _env_file=None,
        stage2_openhands_sdk_root=sdk_root,
        stage2_planner_llm_model="kimi-k2.6-w4a8",
        stage2_planner_llm_base_url="http://10.43.2.168:8073/v1",
        llm_ssl_verify=False,
    )
    monkeypatch.delenv(DEPLOYMENT_LLM_MODEL_ENV, raising=False)
    monkeypatch.delenv("FEATURE_FACTORY_HUAWEI_LLM_MODEL", raising=False)
    monkeypatch.setattr("feature_factory.stage2.backend.shutil.which", lambda _name: "/usr/bin/uv")

    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)

    bridge_env = backend._build_bridge_env()
    env = backend._build_agent_bridge_env("planner")

    assert "SSL_VERIFY" not in bridge_env
    assert env["LLM_MODEL"] == "kimi-k2.6-w4a8"
    assert env["LLM_BASE_URL"] == "http://10.43.2.168:8073/v1"
    assert env["FEATURE_FACTORY_LLM_SSL_VERIFY"] == "false"
    assert "SSL_VERIFY" not in env
    assert env[DEPLOYMENT_LLM_MODEL_ENV] == "kimi-k2.6-w4a8"


def test_openhands_llm_ssl_verify_is_not_forwarded_to_sandbox() -> None:
    if openhands_bridge_module is None:
        pytest.skip("OpenHands SDK is unavailable")

    forwarded = openhands_bridge_module.FEATURE_FACTORY_OPENHANDS_FORWARD_ENV
    assert "FEATURE_FACTORY_LLM_SSL_VERIFY" not in forwarded
    assert "SSL_VERIFY" not in forwarded


def test_openhands_backend_maps_abandoned_planner_result(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'openhands-abandoned.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage2_openhands_sdk_root=sdk_root,
        stage2_planner_llm_model="openai/planner-model",
        stage2_worker_llm_model="openai/worker-model",
    )
    monkeypatch.setattr("feature_factory.stage2.backend.shutil.which", lambda _name: "/usr/bin/uv")
    _patch_stage2_backend_image_builds(monkeypatch)
    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)

    def fake_run_bridge_request(*, request, workspace_dir, label, emit_event):
        assert label == "planner"
        return {
            "model": "openai/planner-model",
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "result": {
                "status": "abandoned",
                "reason": "Observed only ad-hoc scripts under tools/ and no coherent unit-test directory or test runner config.",
            },
        }

    monkeypatch.setattr(backend, "_run_bridge_request", fake_run_bridge_request)

    decision = backend.plan_repository(repo_dir, _repository(), workspace_dir=tmp_path, emit_event=None)

    assert decision.status == "abandoned"
    assert decision.base_image is None
    assert decision.guidance is None
    assert "no coherent unit-test directory" in (decision.reason or "")


def test_openhands_backend_maps_defect_planner_result(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'openhands-defect.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage2_openhands_sdk_root=sdk_root,
        stage2_planner_llm_model="openai/planner-model",
        stage2_worker_llm_model="openai/worker-model",
    )
    monkeypatch.setattr("feature_factory.stage2.backend.shutil.which", lambda _name: "/usr/bin/uv")
    _patch_stage2_backend_image_builds(monkeypatch)
    backend = build_stage2_backend(settings)
    assert isinstance(backend, OpenHandsStage2Backend)

    def fake_run_bridge_request(*, request, workspace_dir, label, emit_event):
        assert label == "planner"
        return {
            "model": "openai/planner-model",
            "token_usage": {"prompt_tokens": 12, "completion_tokens": 4},
            "result": {
                "status": "defect",
                "reason": "Repository is Rust-based, but the base image catalog does not provide a Rust toolchain image.",
                "upd_dockerfile": "FROM rust:1.88-bookworm\nRUN rustc --version\n",
            },
        }

    monkeypatch.setattr(backend, "_run_bridge_request", fake_run_bridge_request)

    decision = backend.plan_repository(repo_dir, _repository(), workspace_dir=tmp_path, emit_event=None)

    assert decision.status == "defect"
    assert decision.base_image is None
    assert decision.guidance is None
    assert "Rust-based" in (decision.reason or "")
    assert decision.upd_dockerfile == "FROM rust:1.88-bookworm\nRUN rustc --version"
