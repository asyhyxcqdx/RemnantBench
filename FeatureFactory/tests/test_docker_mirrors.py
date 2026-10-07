from __future__ import annotations

from pathlib import Path

import feature_factory.docker_mirrors as docker_mirrors_module
from feature_factory.docker_mirrors import (
    dockerfile_with_mirrored_from_images,
    env_value,
    github_proxy_prefix,
    github_proxy_prefixes,
    mirror_docker_image_ref,
    reset_github_proxy_queue,
    rotate_failed_github_proxy_urls,
    rewrite_dockerfile_from_images,
    should_use_china_docker_mirrors,
    should_use_china_mirrors,
    temporary_git_remote_url,
)


def test_github_proxy_list_is_a_shared_rotating_queue(monkeypatch) -> None:
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test","https://proxy-two.test/","https://proxy-three.test/"]',
    )
    reset_github_proxy_queue()

    assert github_proxy_prefixes() == (
        "https://proxy-one.test/",
        "https://proxy-two.test/",
        "https://proxy-three.test/",
    )
    first_url = temporary_git_remote_url(
        "https://github.com/owner/repo.git",
        enabled=True,
    )
    assert first_url == "https://proxy-one.test/https://github.com/owner/repo.git"

    args = ["git", "clone", first_url, "repo.git"]
    assert rotate_failed_github_proxy_urls(args) == (
        "https://proxy-one.test/",
        "https://proxy-two.test/",
    )
    assert args[2] == "https://proxy-two.test/https://github.com/owner/repo.git"
    assert github_proxy_prefixes() == (
        "https://proxy-two.test/",
        "https://proxy-three.test/",
        "https://proxy-one.test/",
    )
    assert github_proxy_prefix() == "https://proxy-two.test/"
    assert temporary_git_remote_url(
        "https://github.com/another/repo.git",
        enabled=True,
    ).startswith("https://proxy-two.test/")


def test_github_proxy_queue_rotates_lfs_config_assignments(monkeypatch) -> None:
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test/","https://proxy-two.test/"]',
    )
    reset_github_proxy_queue()
    args = [
        "git",
        "-c",
        "lfs.url=https://proxy-one.test/https://github.com/owner/repo/info/lfs",
        "checkout",
        "abc123",
    ]

    assert rotate_failed_github_proxy_urls(args) == (
        "https://proxy-one.test/",
        "https://proxy-two.test/",
    )
    assert args[2] == (
        "lfs.url=https://proxy-two.test/https://github.com/owner/repo/info/lfs"
    )


def test_github_proxy_list_supports_comma_separated_and_legacy_single_values(monkeypatch) -> None:
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        "https://proxy-one.test, https://proxy-two.test/",
    )
    reset_github_proxy_queue()
    assert github_proxy_prefixes() == (
        "https://proxy-one.test/",
        "https://proxy-two.test/",
    )

    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        "https://legacy-proxy.test",
    )
    assert github_proxy_prefixes() == ("https://legacy-proxy.test/",)


def test_mirror_docker_image_ref_rewrites_docker_hub_and_ghcr() -> None:
    assert mirror_docker_image_ref("ubuntu:22.04", enabled=True) == "docker.1ms.run/library/ubuntu:22.04"
    assert (
        mirror_docker_image_ref("nikolaik/python-nodejs:python3.13-nodejs22-slim", enabled=True)
        == "docker.1ms.run/nikolaik/python-nodejs:python3.13-nodejs22-slim"
    )
    assert (
        mirror_docker_image_ref("docker.io/library/python:3.13-bookworm", enabled=True)
        == "docker.1ms.run/library/python:3.13-bookworm"
    )
    assert (
        mirror_docker_image_ref("ghcr.io/astral-sh/uv:0.11.6", enabled=True)
        == "ghcr.m.daocloud.io/astral-sh/uv:0.11.6"
    )


def test_mirror_docker_image_ref_keeps_local_and_unknown_registry_refs() -> None:
    assert (
        mirror_docker_image_ref("feature-factory/python:3.12-noble-builder-cn", enabled=True)
        == "feature-factory/python:3.12-noble-builder-cn"
    )
    assert (
        mirror_docker_image_ref("feature-factory-stage2:run-1", enabled=True)
        == "feature-factory-stage2:run-1"
    )
    assert mirror_docker_image_ref("localhost:5000/demo:latest", enabled=True) == "localhost:5000/demo:latest"
    assert mirror_docker_image_ref("scratch", enabled=True) == "scratch"


def test_rewrite_dockerfile_from_images_preserves_stage_aliases_and_variables() -> None:
    dockerfile = """FROM python:3.13-bookworm AS builder
RUN true
FROM builder AS copied
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.11.6 AS uv
FROM ${BASE_IMAGE} AS runtime
FROM feature-factory/python:3.12-noble-builder-cn AS local
"""

    assert rewrite_dockerfile_from_images(dockerfile, enabled=True) == """FROM docker.1ms.run/library/python:3.13-bookworm AS builder
RUN true
FROM builder AS copied
FROM --platform=$BUILDPLATFORM ghcr.m.daocloud.io/astral-sh/uv:0.11.6 AS uv
FROM ${BASE_IMAGE} AS runtime
FROM feature-factory/python:3.12-noble-builder-cn AS local
"""


def test_rewrite_dockerfile_from_images_preserves_syntax_frontend_image() -> None:
    dockerfile = """# syntax=docker/dockerfile:1.7
FROM ubuntu:22.04
"""

    assert rewrite_dockerfile_from_images(dockerfile, enabled=True) == """# syntax=docker/dockerfile:1.7
FROM docker.1ms.run/library/ubuntu:22.04
"""


def test_dockerfile_with_mirrored_from_images_uses_temporary_file(tmp_path: Path) -> None:
    dockerfile_path = tmp_path / "Dockerfile"
    dockerfile_path.write_text("FROM ubuntu:24.04\n", encoding="utf-8")
    observed_logs: list[str] = []

    with dockerfile_with_mirrored_from_images(
        dockerfile_path,
        enabled=True,
        log_callback=observed_logs.append,
    ) as build_path:
        assert build_path != dockerfile_path.resolve()
        assert build_path.read_text(encoding="utf-8") == "FROM docker.1ms.run/library/ubuntu:24.04\n"

    assert dockerfile_path.read_text(encoding="utf-8") == "FROM ubuntu:24.04\n"
    assert observed_logs


def test_docker_mirror_override_does_not_disable_general_cn_profile(monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "0")

    assert should_use_china_mirrors() is True
    assert should_use_china_docker_mirrors() is False


def test_mirror_configuration_can_come_from_dotenv(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("FEATURE_FACTORY_DOCKER_IO_MIRROR", raising=False)
    monkeypatch.setattr(docker_mirrors_module, "_PROJECT_ROOT", tmp_path)
    docker_mirrors_module._dotenv_values.cache_clear()
    (tmp_path / ".env").write_text(
        "FEATURE_FACTORY_DOCKER_IO_MIRROR=mirror.example\n",
        encoding="utf-8",
    )

    try:
        assert env_value("FEATURE_FACTORY_DOCKER_IO_MIRROR") == "mirror.example"
        assert mirror_docker_image_ref("ubuntu:22.04", enabled=True) == (
            "mirror.example/library/ubuntu:22.04"
        )
    finally:
        docker_mirrors_module._dotenv_values.cache_clear()
