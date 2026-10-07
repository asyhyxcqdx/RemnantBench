from __future__ import annotations

import hashlib
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from feature_factory.docker_mirrors import (
    dockerfile_with_mirrored_from_images,
    should_use_china_docker_mirrors,
)
from feature_factory.stage2.base_images import cleanup_replaced_docker_image, run_logged_subprocess
from feature_factory.stage2.image_assets import (
    inspect_docker_image,
    labels_match,
    sanitize_image_tag_component,
)


FEATURE_FACTORY_STAGE3_LABEL_PREFIX = "com.feature-factory.stage3"
FEATURE_FACTORY_STAGE3_KIND_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.kind"
FEATURE_FACTORY_STAGE3_PLATFORM_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.platform"
FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.snapshot-id"
FEATURE_FACTORY_STAGE3_SOURCE_STAGE2_RUN_ID_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.source-stage2-run-id"
FEATURE_FACTORY_STAGE3_SOURCE_COMMIT_SHA_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.source-commit-sha"
FEATURE_FACTORY_STAGE3_BASE_IMAGE_ID_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.base-image-id"
FEATURE_FACTORY_STAGE3_DOCKERFILE_SHA_LABEL = f"{FEATURE_FACTORY_STAGE3_LABEL_PREFIX}.dockerfile-sha256"
FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_KIND = "stage3-breaker-runtime-base"
FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY = "feature-factory/stage3-breaker-runtime"
FEATURE_FACTORY_STAGE3_ASSET_DOCKERFILE_RELATIVE_PATH = Path("assets") / "Dockerfile"

Stage3DockerEvent = Callable[[str, str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class Stage3DockerBuildResult:
    image_ref: str
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    reused: bool = False


def stage3_workspace_dockerfile_path(*, workspace_dir: Path) -> Path:
    return workspace_dir.resolve() / FEATURE_FACTORY_STAGE3_ASSET_DOCKERFILE_RELATIVE_PATH


def detect_stage3_platform() -> str:
    machine = platform.machine().lower()
    if "arm" in machine or "aarch64" in machine:
        return "linux/arm64"
    return "linux/amd64"


def build_stage3_workspace_image(
    *,
    image_ref: str,
    workspace_dir: Path,
    timeout_seconds: float,
    platform_name: str | None = None,
    labels: dict[str, str] | None = None,
    log_callback: Callable[[str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> Stage3DockerBuildResult:
    resolved_workspace_dir = workspace_dir.resolve()
    dockerfile_path = stage3_workspace_dockerfile_path(workspace_dir=resolved_workspace_dir)
    if not dockerfile_path.is_file():
        raise RuntimeError(f"stage3 Dockerfile asset is missing: {dockerfile_path}")

    build_env = os.environ.copy()
    build_env["DOCKER_BUILDKIT"] = "1"
    try:
        with dockerfile_with_mirrored_from_images(
            dockerfile_path,
            enabled=should_use_china_docker_mirrors(),
            log_callback=log_callback,
        ) as build_dockerfile_path:
            command = [
                "docker",
                "build",
                "--progress=plain",
            ]
            if platform_name:
                command.extend(["--platform", platform_name])
            command.extend(["--file", str(build_dockerfile_path)])
            for label_name, label_value in dict(labels or {}).items():
                command.extend(["--label", f"{label_name}={label_value}"])
            command.extend(["--tag", image_ref, str(resolved_workspace_dir)])

            if log_callback is None and cancel_requested is None:
                completed = subprocess.run(
                    command,
                    cwd=str(resolved_workspace_dir),
                    env=build_env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=timeout_seconds,
                )
            else:
                completed = run_logged_subprocess(
                    command,
                    cwd=str(resolved_workspace_dir),
                    env=build_env,
                    check=False,
                    timeout_seconds=timeout_seconds,
                    log_callback=log_callback,
                    cancel_requested=cancel_requested,
                )
        return Stage3DockerBuildResult(
            image_ref=image_ref,
            command=command,
            returncode=completed.returncode,
            stdout=str(completed.stdout or ""),
            stderr=str(completed.stderr or ""),
            timed_out=False,
        )
    except subprocess.TimeoutExpired as exc:
        command = [
            "docker",
            "build",
            "--progress=plain",
            "--file",
            str(dockerfile_path),
            "--tag",
            image_ref,
            str(resolved_workspace_dir),
        ]
        return Stage3DockerBuildResult(
            image_ref=image_ref,
            command=command,
            returncode=124,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            timed_out=True,
        )


def ensure_stage3_breaker_runtime_image_built(
    *,
    workspace_dir: Path,
    snapshot_id: str,
    source_stage2_run_id: str | None,
    source_commit_sha: str | None,
    base_image_id: str | None,
    platform_name: str,
    timeout_seconds: float,
    emit_event: Stage3DockerEvent | None = None,
    log_callback: Callable[[str], None] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    resolved_workspace_dir = workspace_dir.resolve()
    if not str(snapshot_id or "").strip():
        raise RuntimeError("stage3 breaker request is missing snapshot_id for runtime image build")
    image_ref = stage3_breaker_runtime_image_ref(
        snapshot_id=snapshot_id,
        platform_name=platform_name,
    )
    labels = stage3_breaker_runtime_image_labels(
        workspace_dir=resolved_workspace_dir,
        snapshot_id=snapshot_id,
        source_stage2_run_id=source_stage2_run_id,
        source_commit_sha=source_commit_sha,
        base_image_id=base_image_id,
        platform_name=platform_name,
    )
    inspect = inspect_docker_image(image_ref)
    previous_image_id = str(inspect.get("image_id") or "")
    if labels_match(
        labels=dict(inspect.get("labels") or {}),
        expected_labels=labels,
    ):
        if emit_event is not None:
            emit_event(
                "Stage3 runtime image ready",
                f"Reusing cached Stage3 runtime image {image_ref}",
                {
                    "image_ref": image_ref,
                    "snapshot_id": snapshot_id,
                    "platform": platform_name,
                    "reused": True,
                },
            )
        return image_ref

    if emit_event is not None:
        emit_event(
            "Stage3 runtime image building",
            f"Building Stage3 runtime image {image_ref} from the frozen Stage2 Dockerfile",
            {
                "image_ref": image_ref,
                "snapshot_id": snapshot_id,
                "platform": platform_name,
                "dockerfile_path": str(stage3_workspace_dockerfile_path(workspace_dir=resolved_workspace_dir)),
            },
        )

    result = build_stage3_workspace_image(
        image_ref=image_ref,
        workspace_dir=resolved_workspace_dir,
        timeout_seconds=timeout_seconds,
        platform_name=platform_name,
        labels=labels,
        log_callback=log_callback,
        cancel_requested=cancel_requested,
    )
    if result.returncode != 0:
        details = _truncate_text((result.stderr or result.stdout).strip(), limit=4000)
        raise RuntimeError(
            f"failed to build Stage3 runtime image {image_ref}: {details or 'docker build failed'}"
        )
    cleanup_replaced_docker_image(
        previous_image_id=previous_image_id,
        current_image_ref=image_ref,
        log_callback=log_callback,
    )
    if emit_event is not None:
        emit_event(
            "Stage3 runtime image ready",
            f"Built Stage3 runtime image {image_ref}",
            {
                "image_ref": image_ref,
                "snapshot_id": snapshot_id,
                "platform": platform_name,
                "reused": False,
            },
        )
    return image_ref


def stage3_breaker_runtime_image_ref(*, snapshot_id: str, platform_name: str) -> str:
    snapshot_component = sanitize_image_tag_component(snapshot_id)
    platform_component = sanitize_image_tag_component(platform_name)
    return (
        f"{FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY}:"
        f"{snapshot_component}-{platform_component}"
    )


def stage3_breaker_runtime_image_labels(
    *,
    workspace_dir: Path,
    snapshot_id: str,
    source_stage2_run_id: str | None,
    source_commit_sha: str | None,
    base_image_id: str | None,
    platform_name: str,
) -> dict[str, str]:
    dockerfile_path = stage3_workspace_dockerfile_path(workspace_dir=workspace_dir)
    if not dockerfile_path.is_file():
        raise RuntimeError(f"stage3 Dockerfile asset is missing: {dockerfile_path}")
    return stage3_breaker_runtime_image_labels_for_dockerfile(
        dockerfile_text=dockerfile_path.read_text(encoding="utf-8"),
        snapshot_id=snapshot_id,
        source_stage2_run_id=source_stage2_run_id,
        source_commit_sha=source_commit_sha,
        base_image_id=base_image_id,
        platform_name=platform_name,
    )


def stage3_breaker_runtime_image_labels_for_dockerfile(
    *,
    dockerfile_text: str,
    snapshot_id: str,
    source_stage2_run_id: str | None,
    source_commit_sha: str | None,
    base_image_id: str | None,
    platform_name: str,
) -> dict[str, str]:
    dockerfile_sha = hashlib.sha256(str(dockerfile_text or "").encode("utf-8")).hexdigest()
    return {
        FEATURE_FACTORY_STAGE3_KIND_LABEL: FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_KIND,
        FEATURE_FACTORY_STAGE3_PLATFORM_LABEL: platform_name,
        FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL: str(snapshot_id or ""),
        FEATURE_FACTORY_STAGE3_SOURCE_STAGE2_RUN_ID_LABEL: str(source_stage2_run_id or ""),
        FEATURE_FACTORY_STAGE3_SOURCE_COMMIT_SHA_LABEL: str(source_commit_sha or ""),
        FEATURE_FACTORY_STAGE3_BASE_IMAGE_ID_LABEL: str(base_image_id or ""),
        FEATURE_FACTORY_STAGE3_DOCKERFILE_SHA_LABEL: dockerfile_sha,
    }


def stage3_breaker_runtime_image_status(
    *,
    snapshot_id: str,
    source_stage2_run_id: str | None,
    source_commit_sha: str | None,
    base_image_id: str | None,
    platform_name: str,
    dockerfile_text: str | None,
) -> dict[str, Any]:
    image_ref = stage3_breaker_runtime_image_ref(
        snapshot_id=snapshot_id,
        platform_name=platform_name,
    )
    inspect = inspect_docker_image(image_ref)
    physical_present = bool(inspect.get("present"))
    expected_labels = (
        stage3_breaker_runtime_image_labels_for_dockerfile(
            dockerfile_text=str(dockerfile_text or ""),
            snapshot_id=snapshot_id,
            source_stage2_run_id=source_stage2_run_id,
            source_commit_sha=source_commit_sha,
            base_image_id=base_image_id,
            platform_name=platform_name,
        )
        if str(dockerfile_text or "").strip()
        else {}
    )
    fingerprint_matches = bool(
        expected_labels
        and labels_match(
            labels=dict(inspect.get("labels") or {}),
            expected_labels=expected_labels,
        )
    )
    needs_update = bool(physical_present and expected_labels and not fingerprint_matches)
    return {
        "image_ref": image_ref,
        "present": bool(physical_present and (fingerprint_matches or not expected_labels)),
        "physical_present": physical_present,
        "fingerprint_matches": fingerprint_matches,
        "needs_update": needs_update,
        "image_id": inspect.get("image_id") or "",
        "created_at": inspect.get("created_at"),
        "architecture": inspect.get("architecture") or "",
        "os": inspect.get("os") or "",
        "labels": inspect.get("labels") or {},
        "expected_labels": expected_labels,
        "inspect_error": inspect.get("error") or "",
        "dockerfile_missing": not bool(str(dockerfile_text or "").strip()),
    }


def _truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(limit - 1, 0)] + "…"
