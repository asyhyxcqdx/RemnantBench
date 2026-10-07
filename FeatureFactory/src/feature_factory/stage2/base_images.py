from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from feature_factory.docker_mirrors import (
    dockerfile_with_mirrored_from_images,
    env_value,
    should_use_china_docker_mirrors,
    should_use_china_mirrors as _should_use_china_mirrors,
)


FEATURE_FACTORY_STAGE2_LABEL_PREFIX = "com.feature-factory.stage2"
FEATURE_FACTORY_STAGE2_KIND_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.kind"
FEATURE_FACTORY_STAGE2_ASSET_KEY_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.asset-key"
FEATURE_FACTORY_STAGE2_IMAGE_ID_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.image-id"
FEATURE_FACTORY_STAGE2_IMAGE_REF_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.image-ref"
FEATURE_FACTORY_STAGE2_ASSET_PATH_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.asset-path"
FEATURE_FACTORY_STAGE2_DOCKERFILE_SHA_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.dockerfile-sha256"
FEATURE_FACTORY_STAGE2_PLATFORM_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.platform"
FEATURE_FACTORY_STAGE2_NETWORK_PROFILE_LABEL = f"{FEATURE_FACTORY_STAGE2_LABEL_PREFIX}.network-profile"
FEATURE_FACTORY_STAGE2_BASE_IMAGE_KIND = "base-image"
FEATURE_FACTORY_STAGE2_BASE_IMAGE_BUILD_ARG_ENV_NAMES = {
    "PIP_INDEX_URL": (
        "FEATURE_FACTORY_STAGE2_PIP_INDEX_URL",
        "FEATURE_FACTORY_STAGE2_PYPI_INDEX_URL",
    ),
    "TORCH_CPU_FIND_LINKS": ("FEATURE_FACTORY_STAGE2_TORCH_CPU_FIND_LINKS",),
    "UV_INDEX_URL": ("FEATURE_FACTORY_STAGE2_UV_INDEX_URL",),
    "UV_PYTHON_INSTALL_MIRROR": ("FEATURE_FACTORY_STAGE2_UV_PYTHON_INSTALL_MIRROR",),
    "NPM_REGISTRY": ("FEATURE_FACTORY_STAGE2_NPM_REGISTRY",),
    "MINICONDA_DIST_URL": (
        "FEATURE_FACTORY_STAGE2_MINICONDA_DIST_URL",
        "FEATURE_FACTORY_STAGE2_MINICONDA_DIST_MIRROR",
    ),
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
    "MAVEN_MIRROR": ("FEATURE_FACTORY_STAGE2_MAVEN_MIRROR",),
    "RUBYGEMS_MIRROR": ("FEATURE_FACTORY_STAGE2_RUBYGEMS_MIRROR",),
}


@dataclass(frozen=True, slots=True)
class BaseImageSpec:
    image_id: str
    image_ref: str
    china_image_ref: str | None
    language_pack_family: str
    runtime_family: str
    runtime_version: str
    os_family: str
    arch: str
    gpu_support: bool
    preinstalled_capabilities: tuple[str, ...]
    risk_tags: tuple[str, ...]
    description: str = ""
    asset_path: str | None = None
    china_asset_path: str | None = None

    def to_payload(self, *, use_china_mirrors: bool | None = None) -> dict[str, object]:
        payload = asdict(self)
        payload["preinstalled_capabilities"] = list(self.preinstalled_capabilities)
        payload["risk_tags"] = list(self.risk_tags)
        use_china_mirrors = should_use_china_mirrors() if use_china_mirrors is None else use_china_mirrors
        payload["network_profile"] = "cn" if use_china_mirrors else "default"
        if use_china_mirrors and self.china_asset_path:
            payload["asset_path"] = self.china_asset_path
        if use_china_mirrors and self.china_image_ref:
            payload["image_ref"] = self.china_image_ref
        payload.pop("china_asset_path", None)
        payload.pop("china_image_ref", None)
        return payload


BASE_IMAGE_CATALOG: tuple[BaseImageSpec, ...] = (
    BaseImageSpec(
        image_id="python:3.10-jammy-builder",
        image_ref="feature-factory/python:3.10-jammy-builder",
        china_image_ref="feature-factory/python:3.10-jammy-builder-cn",
        language_pack_family="python",
        runtime_family="python",
        runtime_version="3.10",
        os_family="ubuntu-jammy",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=(
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
        ),
        risk_tags=("legacy-runtime", "large-image"),
        description=(
            "Ubuntu 22.04 based Python 3.10 worker image with Miniconda, uv, "
            "compiler toolchain, and common native/scientific build dependencies."
        ),
        asset_path="base_images/python-3.10-jammy.Dockerfile",
        china_asset_path="base_images/python-3.10-jammy-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="python:3.11-jammy-builder",
        image_ref="feature-factory/python:3.11-jammy-builder",
        china_image_ref="feature-factory/python:3.11-jammy-builder-cn",
        language_pack_family="python",
        runtime_family="python",
        runtime_version="3.11",
        os_family="ubuntu-jammy",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=(
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
        ),
        risk_tags=("large-image",),
        description=(
            "Ubuntu 22.04 based Python 3.11 worker image with Miniconda, uv, "
            "compiler toolchain, and common native/scientific build dependencies."
        ),
        asset_path="base_images/python-3.11-jammy.Dockerfile",
        china_asset_path="base_images/python-3.11-jammy-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="python:3.12-noble-builder",
        image_ref="feature-factory/python:3.12-noble-builder",
        china_image_ref="feature-factory/python:3.12-noble-builder-cn",
        language_pack_family="python",
        runtime_family="python",
        runtime_version="3.12",
        os_family="ubuntu-noble",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=(
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
        ),
        risk_tags=("newer-runtime", "large-image"),
        description=(
            "Ubuntu 24.04 based Python 3.12 worker image with Miniconda, uv, "
            "compiler toolchain, and common native/scientific build dependencies."
        ),
        asset_path="base_images/python-3.12-noble.Dockerfile",
        china_asset_path="base_images/python-3.12-noble-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="node:20-bookworm-builder",
        image_ref="feature-factory/node:20-bookworm-builder",
        china_image_ref="feature-factory/node:20-bookworm-builder-cn",
        language_pack_family="node",
        runtime_family="node",
        runtime_version="20",
        os_family="debian-bookworm",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("node", "npm", "corepack"),
        risk_tags=(),
        description="Node.js 20 runtime for npm, pnpm, and yarn based test environments.",
        asset_path="base_images/node-20-bookworm.Dockerfile",
        china_asset_path="base_images/node-20-bookworm-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="node:22-bookworm-builder",
        image_ref="feature-factory/node:22-bookworm-builder",
        china_image_ref="feature-factory/node:22-bookworm-builder-cn",
        language_pack_family="node",
        runtime_family="node",
        runtime_version="22",
        os_family="debian-bookworm",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("node", "npm", "corepack"),
        risk_tags=("newer-runtime",),
        description="Node.js 22 runtime for repositories already on the newer LTS line.",
        asset_path="base_images/node-22-bookworm.Dockerfile",
        china_asset_path="base_images/node-22-bookworm-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="golang:1.23-bookworm-builder",
        image_ref="feature-factory/golang:1.23-bookworm-builder",
        china_image_ref="feature-factory/golang:1.23-bookworm-builder-cn",
        language_pack_family="go",
        runtime_family="go",
        runtime_version="1.23",
        os_family="debian-bookworm",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("go", "go-test"),
        risk_tags=(),
        description="Go 1.23 runtime for standard `go test` workflows.",
        asset_path="base_images/go-1.23-bookworm.Dockerfile",
        china_asset_path="base_images/go-1.23-bookworm-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="rust:1.84-bookworm-builder",
        image_ref="feature-factory/rust:1.84-bookworm-builder",
        china_image_ref="feature-factory/rust:1.84-bookworm-builder-cn",
        language_pack_family="rust",
        runtime_family="rust",
        runtime_version="1.84",
        os_family="debian-bookworm",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("rustc", "cargo"),
        risk_tags=(),
        description="Rust toolchain with cargo for `cargo test` based repositories.",
        asset_path="base_images/rust-1.84-bookworm.Dockerfile",
        china_asset_path="base_images/rust-1.84-bookworm-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="ruby:3.3-bookworm-builder",
        image_ref="feature-factory/ruby:3.3-bookworm-builder",
        china_image_ref="feature-factory/ruby:3.3-bookworm-builder-cn",
        language_pack_family="ruby",
        runtime_family="ruby",
        runtime_version="3.3",
        os_family="debian-bookworm",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("ruby", "bundler", "gem"),
        risk_tags=(),
        description="Ruby 3.3 runtime for bundler and common test runners like rspec or minitest.",
        asset_path="base_images/ruby-3.3-bookworm.Dockerfile",
        china_asset_path="base_images/ruby-3.3-bookworm-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="java:17-jammy-builder",
        image_ref="feature-factory/java:17-jammy-builder",
        china_image_ref="feature-factory/java:17-jammy-builder-cn",
        language_pack_family="java",
        runtime_family="java",
        runtime_version="17",
        os_family="ubuntu-jammy",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("java", "javac", "jar"),
        risk_tags=(),
        description="Java 17 JDK for Maven and Gradle projects on the common LTS line.",
        asset_path="base_images/java-17-jammy.Dockerfile",
        china_asset_path="base_images/java-17-jammy-cn.Dockerfile",
    ),
    BaseImageSpec(
        image_id="java:21-jammy-builder",
        image_ref="feature-factory/java:21-jammy-builder",
        china_image_ref="feature-factory/java:21-jammy-builder-cn",
        language_pack_family="java",
        runtime_family="java",
        runtime_version="21",
        os_family="ubuntu-jammy",
        arch="multi-arch",
        gpu_support=False,
        preinstalled_capabilities=("java", "javac", "jar"),
        risk_tags=("newer-runtime",),
        description="Java 21 JDK for repositories already targeting the newer LTS release.",
        asset_path="base_images/java-21-jammy.Dockerfile",
        china_asset_path="base_images/java-21-jammy-cn.Dockerfile",
    ),
)


def list_base_images(*, language_pack_family: str | None = None) -> list[BaseImageSpec]:
    images = list(BASE_IMAGE_CATALOG)
    if language_pack_family is None:
        return images
    return [image for image in images if image.language_pack_family == language_pack_family]


def resolve_base_image_payload(
    *,
    image_id: str,
    base_image_catalog: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for item in base_image_catalog:
        if str(item.get("image_id") or "") == image_id:
            return item
    return None


def should_use_china_mirrors() -> bool:
    return _should_use_china_mirrors()


def prepare_base_image_asset_view(
    *,
    source_root: Path,
    runtime_dir: Path,
    base_image_catalog: list[Any],
) -> Path:
    """Create a run-local view containing only active catalog Dockerfiles."""
    view_dir = runtime_dir / "base_images"
    if view_dir.exists():
        shutil.rmtree(view_dir)
    view_dir.mkdir(parents=True, exist_ok=True)
    _chmod_container_readable(view_dir)

    copied: set[str] = set()
    for item in base_image_catalog:
        if not isinstance(item, dict):
            continue
        asset_path = str(item.get("asset_path") or "").strip()
        if not asset_path:
            continue
        source = (source_root / asset_path).resolve()
        if not source.is_file():
            raise RuntimeError(f"base image asset does not exist: {asset_path}")
        target_name = Path(asset_path).name
        if target_name in copied:
            continue
        target = view_dir / target_name
        shutil.copyfile(source, target)
        _chmod_container_readable(target)
        copied.add(target_name)

    return view_dir.resolve()


def materialize_selected_base_image_reference(
    *,
    source_root: Path,
    workspace_dir: Path,
    base_image_payload: dict[str, Any],
) -> tuple[Path, Path]:
    support_dir = workspace_dir / ".stage2" / "base_image"
    if support_dir.exists():
        shutil.rmtree(support_dir)
    support_dir.mkdir(parents=True, exist_ok=True)
    _chmod_container_readable(support_dir.parent)
    _chmod_container_readable(support_dir)

    asset_path = str(base_image_payload.get("asset_path") or "").strip()
    if not asset_path:
        raise RuntimeError("selected base image payload is missing asset_path")
    source = (source_root / asset_path).resolve()
    if not source.is_file():
        raise RuntimeError(f"selected base image asset does not exist: {asset_path}")

    dockerfile_path = support_dir / Path(asset_path).name
    shutil.copyfile(source, dockerfile_path)
    _chmod_container_readable(dockerfile_path)
    metadata_path = support_dir / "selected_base_image.json"
    metadata_path.write_text(
        json_dumps(selected_base_image_metadata_payload(base_image_payload)) + "\n",
        encoding="utf-8",
    )
    _chmod_container_readable(metadata_path)
    return dockerfile_path.resolve(), metadata_path.resolve()


def _chmod_container_readable(path: Path) -> None:
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            desired_mode = (
                current_mode
                | stat.S_IRWXU
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            ) & ~(stat.S_IWGRP | stat.S_IWOTH)
        else:
            desired_mode = (
                current_mode
                | stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IRGRP
                | stat.S_IROTH
            ) & ~(stat.S_IWGRP | stat.S_IWOTH)
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


def ensure_base_image_built(
    *,
    source_root: Path,
    base_image_payload: dict[str, Any],
    platform_name: str | None = None,
    force: bool = False,
    log_callback: Callable[[str], None] | None = None,
    timeout_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> str:
    image_ref = str(base_image_payload.get("image_ref") or "").strip()
    asset_path = str(base_image_payload.get("asset_path") or "").strip()
    if not image_ref:
        raise RuntimeError("base image payload is missing image_ref")
    if not asset_path:
        raise RuntimeError("base image payload is missing asset_path")

    dockerfile_path = (source_root / asset_path).resolve()
    if not dockerfile_path.is_file():
        raise RuntimeError(f"base image Dockerfile does not exist: {asset_path}")
    labels = base_image_build_labels(
        base_image_payload=base_image_payload,
        dockerfile_path=dockerfile_path,
        platform_name=platform_name,
    )
    if not force:
        if docker_image_labels_match(image_ref=image_ref, expected_labels=labels):
            return image_ref
    previous_image_id = docker_image_id(image_ref)

    use_docker_mirrors = (
        str(base_image_payload.get("network_profile") or "").strip() == "cn"
        and should_use_china_docker_mirrors()
    )
    with dockerfile_with_mirrored_from_images(
        dockerfile_path,
        enabled=use_docker_mirrors,
        log_callback=log_callback,
    ) as build_dockerfile_path:
        build_env = dict(os.environ)
        build_env["DOCKER_BUILDKIT"] = "1"
        command = [
            "docker",
            "build",
            "--tag",
            image_ref,
            "--file",
            str(build_dockerfile_path),
        ]
        for label_name, label_value in labels.items():
            command.extend(["--label", f"{label_name}={label_value}"])
        for build_arg_name, value in base_image_mirror_build_args().items():
            command.extend(["--build-arg", f"{build_arg_name}={value}"])
        if platform_name:
            command.extend(["--platform", platform_name])
        command.append(str(dockerfile_path.parent))
        run_logged_subprocess(
            command,
            env=build_env,
            log_callback=log_callback,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
        )
    cleanup_replaced_docker_image(
        previous_image_id=previous_image_id,
        current_image_ref=image_ref,
        log_callback=log_callback,
    )
    return image_ref


def base_image_mirror_build_args() -> dict[str, str]:
    build_args: dict[str, str] = {}
    for build_arg_name, env_names in FEATURE_FACTORY_STAGE2_BASE_IMAGE_BUILD_ARG_ENV_NAMES.items():
        for env_name in env_names:
            value = env_value(env_name)
            if value:
                build_args[build_arg_name] = value
                break
    return build_args


def base_image_build_labels(
    *,
    base_image_payload: dict[str, Any],
    dockerfile_path: Path,
    platform_name: str | None,
) -> dict[str, str]:
    image_id = str(base_image_payload.get("image_id") or "").strip()
    image_ref = str(base_image_payload.get("image_ref") or "").strip()
    asset_path = str(base_image_payload.get("asset_path") or "").strip()
    network_profile = str(base_image_payload.get("network_profile") or "default").strip() or "default"
    asset_key = str(base_image_payload.get("asset_key") or "").strip()
    if not asset_key:
        asset_key = f"{image_id or image_ref}::{network_profile}"
    return {
        FEATURE_FACTORY_STAGE2_KIND_LABEL: FEATURE_FACTORY_STAGE2_BASE_IMAGE_KIND,
        FEATURE_FACTORY_STAGE2_ASSET_KEY_LABEL: asset_key,
        FEATURE_FACTORY_STAGE2_IMAGE_ID_LABEL: image_id,
        FEATURE_FACTORY_STAGE2_IMAGE_REF_LABEL: image_ref,
        FEATURE_FACTORY_STAGE2_ASSET_PATH_LABEL: asset_path,
        FEATURE_FACTORY_STAGE2_DOCKERFILE_SHA_LABEL: file_sha256(dockerfile_path),
        FEATURE_FACTORY_STAGE2_PLATFORM_LABEL: str(platform_name or ""),
        FEATURE_FACTORY_STAGE2_NETWORK_PROFILE_LABEL: network_profile,
    }


def docker_image_labels_match(*, image_ref: str, expected_labels: dict[str, str]) -> bool:
    labels = docker_image_labels(image_ref)
    if not labels:
        return False
    for label_name, expected_value in expected_labels.items():
        if str(labels.get(label_name) or "") != str(expected_value):
            return False
    return True


def docker_image_labels(image_ref: str) -> dict[str, str]:
    reference = str(image_ref or "").strip()
    if not reference:
        return {}
    completed = subprocess.run(
        [
            "docker",
            "image",
            "inspect",
            reference,
            "--format",
            "{{json .Config.Labels}}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return {}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return {str(key): str(value) for key, value in payload.items() if value is not None}


def docker_image_id(image_ref: str) -> str:
    reference = str(image_ref or "").strip()
    if not reference:
        return ""
    try:
        completed = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                reference,
                "--format",
                "{{.Id}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return str(completed.stdout or "").strip()


def docker_image_references(repository: str) -> list[str]:
    normalized_repository = str(repository or "").strip()
    if not normalized_repository:
        return []
    try:
        completed = subprocess.run(
            [
                "docker",
                "image",
                "ls",
                normalized_repository,
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    refs: list[str] = []
    for line in str(completed.stdout or "").splitlines():
        ref = line.strip()
        if not ref or "<none>" in ref:
            continue
        refs.append(ref)
    return refs


def remove_docker_image_references(
    image_refs: list[str] | tuple[str, ...],
    *,
    log_callback: Callable[[str], None] | None = None,
) -> list[str]:
    removed: list[str] = []
    seen: set[str] = set()
    for image_ref in image_refs:
        reference = str(image_ref or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        try:
            completed = subprocess.run(
                ["docker", "image", "rm", reference],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            if callable(log_callback):
                log_callback(f"Failed to remove stale Docker image {reference}: {exc}")
            continue
        output = str(completed.stderr or completed.stdout or "").strip()
        if completed.returncode == 0:
            removed.append(reference)
            if callable(log_callback):
                log_callback(f"Removed stale Docker image {reference}")
            continue
        if callable(log_callback):
            detail = output or f"exit code {completed.returncode}"
            log_callback(f"Failed to remove stale Docker image {reference}: {detail}")
    return removed


def cleanup_replaced_docker_image(
    *,
    previous_image_id: str,
    current_image_ref: str,
    log_callback: Callable[[str], None] | None = None,
) -> list[str]:
    previous_id = str(previous_image_id or "").strip()
    if not previous_id:
        return []
    current_id = docker_image_id(current_image_ref)
    if not current_id or current_id == previous_id:
        return []
    return remove_docker_image_references([previous_id], log_callback=log_callback)


def run_logged_subprocess(
    command: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    log_callback: Callable[[str], None] | None = None,
    check: bool = True,
    timeout_seconds: float | None = None,
    cancel_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    if log_callback is None and cancel_requested is None:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            text=True,
            check=check,
            timeout=timeout_seconds,
        )

    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE if log_callback is not None else None,
        stderr=subprocess.STDOUT if log_callback is not None else None,
        text=True,
        bufsize=1,
    )
    output_lines: list[str] = []

    def read_output() -> None:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip("\n")
            output_lines.append(line)
            log_callback(line)

    reader: threading.Thread | None = None
    if log_callback is not None:
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
    deadline = (
        time.monotonic() + timeout_seconds
        if timeout_seconds is not None
        else None
    )
    try:
        while True:
            if cancel_requested is not None and cancel_requested():
                process.kill()
                process.wait(timeout=5)
                raise InterruptedError("subprocess interrupted by user")
            wait_timeout = 0.25
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    process.kill()
                    process.wait(timeout=5)
                    raise subprocess.TimeoutExpired(
                        command,
                        timeout_seconds,
                        output="\n".join(output_lines),
                        stderr="",
                    )
                wait_timeout = min(wait_timeout, remaining)
            try:
                returncode = process.wait(timeout=wait_timeout)
                break
            except subprocess.TimeoutExpired:
                continue
    finally:
        if reader is not None:
            reader.join(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
    completed = subprocess.CompletedProcess(
        command,
        returncode,
        stdout="\n".join(output_lines),
        stderr="",
    )
    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode,
            command,
            output=completed.stdout,
            stderr=completed.stderr,
        )
    return completed


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2)


def selected_base_image_prompt_summary(base_image_payload: dict[str, Any]) -> str:
    image_ref = str(base_image_payload.get("image_ref") or "").strip()
    runtime_family = str(base_image_payload.get("runtime_family") or "").strip()
    runtime_version = str(base_image_payload.get("runtime_version") or "").strip()
    os_family = str(base_image_payload.get("os_family") or "").strip()
    network_profile = str(base_image_payload.get("network_profile") or "").strip()
    capabilities = [
        str(item).strip()
        for item in list(base_image_payload.get("preinstalled_capabilities") or [])
        if str(item).strip()
    ]

    runtime_label = " ".join(part for part in (runtime_family, runtime_version) if part).strip()
    lines: list[str] = []
    if image_ref:
        lines.append(f"- Exact parent image: `{image_ref}`")
    if runtime_label:
        lines.append(f"- Runtime: `{runtime_label}`")
    if os_family:
        lines.append(f"- OS family: `{os_family}`")
    if network_profile:
        lines.append(f"- Network profile: `{network_profile}`")
    if capabilities:
        capabilities_text = ", ".join(f"`{item}`" for item in capabilities)
        lines.append(f"- Preinstalled capabilities: {capabilities_text}")
    return "\n".join(lines)


def selected_base_image_metadata_payload(base_image_payload: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "image_ref": str(base_image_payload.get("image_ref") or ""),
        "runtime_family": str(base_image_payload.get("runtime_family") or ""),
        "runtime_version": str(base_image_payload.get("runtime_version") or ""),
        "os_family": str(base_image_payload.get("os_family") or ""),
        "network_profile": str(base_image_payload.get("network_profile") or ""),
    }
    capabilities = list(base_image_payload.get("preinstalled_capabilities") or [])
    if capabilities:
        metadata["preinstalled_capabilities"] = capabilities
    return metadata
