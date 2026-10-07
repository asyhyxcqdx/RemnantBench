from __future__ import annotations

import io
import json
import pytest
import shutil
import stat
import subprocess
import tarfile
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select, text

from feature_factory.config import Settings
from feature_factory.db import Base, build_engine, build_session_factory
from feature_factory.models import (
    GitHubRepository,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage2TestResult,
    Stage3CommitSnapshot,
    Stage3CleanupTombstone,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunEvent,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
)
from feature_factory.openhands_llm import DEPLOYMENT_LLM_MODEL_ENV
import feature_factory.server as server_module
from feature_factory.stage2 import image_assets
import feature_factory.stage3.backend as stage3_backend_module
import feature_factory.stage3.runner as stage3_runner_module
import feature_factory.stage3.service as stage3_service_module
from feature_factory.server import create_app
from feature_factory.stage3.backend import BreakerExecutionResult
from feature_factory.stage3.evaluator import Stage3EvaluationResult, Stage3FileEvaluation
from feature_factory.stage3.runner import Stage3RunRunner
from feature_factory.stage3.service import (
    Stage3RunConflictError,
    Stage3SavepointRejectedError,
    Stage3Service,
    _stage3_commented_out_code_findings,
    _stage3_patch_quality_findings,
)


_STAGE3_BASELINE_APP_TEXT = """def target_feature(items):
    total = 0
    weights = []
    for index, item in enumerate(items):
        value = int(item)
        adjusted = value * (index + 1)
        weights.append(adjusted)
        total += adjusted
    if not weights:
        return 0
    bonus = max(weights) - min(weights)
    return total + bonus
"""

_STAGE3_BREAKAGE_ONE_APP_TEXT = """def target_feature(items):
    total = 0
    for item in items:
        total += int(item)
    return total
"""

_STAGE3_BREAKAGE_TWO_APP_TEXT = """def target_feature(items):
    values = [int(item) for item in items]
    total = sum(values)
    penalty = len(values)
    return total - penalty
"""


def _write_stage3_app_breakage(repo_dir: Path, *, level: int) -> None:
    if level == 1:
        text = _STAGE3_BREAKAGE_ONE_APP_TEXT
    elif level == 2:
        text = _STAGE3_BREAKAGE_TWO_APP_TEXT
    else:
        raise ValueError(f"unknown stage3 app breakage level: {level}")
    (repo_dir / "app.py").write_text(text, encoding="utf-8")


def test_stage3_commented_out_code_detection_flags_line_comments() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def build_value():\n"
        "-    return request_context.copy()\n"
        "+def build_value():\n"
        "+    # return request_context.copy()\n"
    )

    findings = _stage3_commented_out_code_findings(patch_text)

    assert findings
    assert findings[0]["file_path"] == "app.py"
    assert findings[0]["removed_line"] == "return request_context.copy()"


def test_stage3_commented_out_code_detection_flags_block_comments() -> None:
    patch_text = (
        "diff --git a/app.js b/app.js\n"
        "--- a/app.js\n"
        "+++ b/app.js\n"
        "@@ -1,2 +1,4 @@\n"
        "-function computeValue() {\n"
        "-  return requestContext.clone();\n"
        "+function computeValue() {\n"
        "+  /*\n"
        "+  return requestContext.clone();\n"
        "+  */\n"
    )

    findings = _stage3_commented_out_code_findings(patch_text)

    assert findings
    assert findings[0]["file_path"] == "app.js"


def test_stage3_commented_out_code_detection_allows_normal_comments() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,3 @@\n"
        "-def build_value():\n"
        "-    return request_context.copy()\n"
        "+def build_value():\n"
        "+    # Intentionally use a fresh context for isolated runs.\n"
        "+    return {}\n"
    )

    assert _stage3_commented_out_code_findings(patch_text) == []


def test_stage3_patch_quality_rejects_trivial_stub() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,9 +1,3 @@\n"
        "-def target_feature(items):\n"
        "-    total = 0\n"
        "-    for item in items:\n"
        "-        total += int(item)\n"
        "-    if total > 0:\n"
        "-        return total\n"
        "-    return 0\n"
        "+def target_feature(items):\n"
        "+    pass\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "TRIVIAL_STUB_BREAKAGE"
    assert "stub-style breakage" in findings[0]["message"]


def test_stage3_patch_quality_rejects_test_file_edits() -> None:
    patch_text = (
        "diff --git a/tests/test_app.py b/tests/test_app.py\n"
        "--- a/tests/test_app.py\n"
        "+++ b/tests/test_app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-assert build_value() == 3\n"
        "+assert build_value() == 2\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "FORBIDDEN_PATCH_PATH"
    assert findings[0]["file_path"] == "tests/test_app.py"
    assert "test" in findings[0]["message"]


def test_stage3_patch_quality_ignores_package_metadata_for_deletion_depth() -> None:
    patch_text = (
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1,12 +1,2 @@\n"
        "-version = 1\n"
        "-revision = 3\n"
        "-requires-python = '>=3.13'\n"
        "-[[package]]\n"
        "-name = 'fastapi'\n"
        "-version = '0.115.0'\n"
        "-[[package]]\n"
        "-name = 'pytest'\n"
        "-version = '9.0.3'\n"
        "-source = 'registry'\n"
        "+version = 1\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "INSUFFICIENT_SUBSTANTIAL_DELETION"
    assert findings[0]["removed_code_line_count"] == 0


def test_stage3_patch_quality_ignores_binary_patch_content_for_deletion_depth() -> None:
    patch_text = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1234567..89abcde 100644\n"
        "GIT binary patch\n"
        "literal 4\n"
        "LcmZQzU|;|M00aO5\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "INSUFFICIENT_SUBSTANTIAL_DELETION"
    assert findings[0]["removed_code_line_count"] == 0


def test_stage3_patch_quality_allows_implementation_deletion_with_generated_noise() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,12 +1,5 @@\n"
        "-def target_feature(items):\n"
        "-    total = 0\n"
        "-    weights = []\n"
        "-    for index, item in enumerate(items):\n"
        "-        value = int(item)\n"
        "-        adjusted = value * (index + 1)\n"
        "-        weights.append(adjusted)\n"
        "-        total += adjusted\n"
        "-    if not weights:\n"
        "-        return 0\n"
        "-    bonus = max(weights) - min(weights)\n"
        "-    return total + bonus\n"
        "+def target_feature(items):\n"
        "+    total = 0\n"
        "+    for item in items:\n"
        "+        total += int(item)\n"
        "+    return total\n"
        "diff --git a/uv.lock b/uv.lock\n"
        "--- a/uv.lock\n"
        "+++ b/uv.lock\n"
        "@@ -1,4 +1,2 @@\n"
        "-version = 1\n"
        "-revision = 3\n"
        "+version = 1\n"
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1234567..89abcde 100644\n"
        "GIT binary patch\n"
        "literal 4\n"
        "LcmZQzU|;|M00aO5\n"
    )

    assert _stage3_patch_quality_findings(patch_text) == []


def test_stage3_patch_quality_rejects_sabotage_breadcrumbs() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,12 +1,5 @@\n"
        "-def target_feature(items):\n"
        "-    total = 0\n"
        "-    weights = []\n"
        "-    for index, item in enumerate(items):\n"
        "-        value = int(item)\n"
        "-        adjusted = value * (index + 1)\n"
        "-        weights.append(adjusted)\n"
        "-        total += adjusted\n"
        "-    if not weights:\n"
        "-        return 0\n"
        "-    bonus = max(weights) - min(weights)\n"
        "-    return total + bonus\n"
        "+def target_feature(items):\n"
        "+    total = 0\n"
        "+    marker = 'broken path'\n"
        "+    for item in items:\n"
        "+        total += int(item)\n"
        "+    return total\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "SABOTAGE_BREADCRUMB"
    assert "repair clue" in findings[0]["message"]


def test_stage3_patch_quality_requires_substantial_deletion() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-result = compute_value()\n"
        "+result = compute_other_value()\n"
    )

    findings = _stage3_patch_quality_findings(patch_text)

    assert findings[0]["code"] == "INSUFFICIENT_SUBSTANTIAL_DELETION"
    assert "too little substantive implementation code" in findings[0]["message"]
    assert findings[0]["removed_code_line_count"] == 1
    assert findings[0]["minimum_removed_code_line_count"] == 10


def test_stage3_patch_quality_accepts_configured_min_removed_code_lines() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-result = compute_value()\n"
        "+result = compute_other_value()\n"
    )

    assert _stage3_patch_quality_findings(patch_text, min_removed_code_lines=1) == []


def test_stage3_patch_quality_accepts_substantial_deletion_without_stub() -> None:
    patch_text = (
        "diff --git a/app.py b/app.py\n"
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -1,12 +1,5 @@\n"
        "-def target_feature(items):\n"
        "-    total = 0\n"
        "-    weights = []\n"
        "-    for index, item in enumerate(items):\n"
        "-        value = int(item)\n"
        "-        adjusted = value * (index + 1)\n"
        "-        weights.append(adjusted)\n"
        "-        total += adjusted\n"
        "-    if not weights:\n"
        "-        return 0\n"
        "-    bonus = max(weights) - min(weights)\n"
        "-    return total + bonus\n"
        "+def target_feature(items):\n"
        "+    total = 0\n"
        "+    for item in items:\n"
        "+        total += int(item)\n"
        "+    return total\n"
    )

    assert _stage3_patch_quality_findings(patch_text) == []


class NoopStage1Runner:
    def shutdown(self) -> None:
        return None


class DummyStage2Runner:
    def shutdown(self) -> None:
        return None

    def list_running_repository_ids(self) -> set[int]:
        return set()

    def get_max_workers(self) -> int:
        return 1


class DummyStage3Runner:
    def __init__(self, *, ready: bool = True, max_workers: int = 64) -> None:
        self.ready = ready
        self.max_workers = max_workers
        self.scheduled_run_ids: list[str] = []
        self.interrupted_run_ids: list[str] = []
        self.reload_count = 0

    def backend_readiness(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "message": "stage3 breaker backend is ready" if self.ready else "stage3 breaker backend is not ready",
        }

    def is_backend_ready(self) -> bool:
        return self.ready

    def schedule_run(self, run_id: str) -> bool:
        if not self.ready:
            return False
        self.scheduled_run_ids.append(run_id)
        return True

    def interrupt_run(self, run_id: str) -> bool:
        self.interrupted_run_ids.append(run_id)
        return False

    def is_run_running(self, run_id: str) -> bool:
        return False

    def get_max_workers(self) -> int:
        return self.max_workers

    def set_max_workers(self, value: int) -> None:
        self.max_workers = value

    def reload_backend(self) -> None:
        self.reload_count += 1

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        del timeout_seconds
        return []


class RejectStage3ScheduleRunner(DummyStage3Runner):
    def schedule_run(self, run_id: str) -> bool:
        self.scheduled_run_ids.append(run_id)
        return False


def test_stage3_bridge_env_uses_shared_host_sdk_runtime_mirror_config(
    monkeypatch,
    tmp_path,
) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    config_dir = tmp_path / "uv-runtime-config"
    monkeypatch.setenv("VIRTUAL_ENV", "/tmp/feature-factory-main-venv")
    monkeypatch.setenv("PYTHONPATH", "/tmp/existing-pythonpath")
    monkeypatch.setattr(image_assets, "FEATURE_FACTORY_STAGE2_HOST_SDK_RUNTIME_CONFIG_DIR", config_dir)
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
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-bridge-env.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
        stage3_openhands_sdk_root=sdk_root,
    )
    backend = stage3_backend_module.OpenHandsStage3Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )

    env = backend._build_bridge_env()

    assert "VIRTUAL_ENV" not in env
    assert env["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"] == str(sdk_root.resolve())
    assert env["FEATURE_FACTORY_STAGE3_OPENHANDS_SDK_ROOT"] == str(sdk_root.resolve())
    assert env["OPENHANDS_SUPPRESS_BANNER"] == "1"
    assert env["UV_CONFIG_FILE"] == str((config_dir / "uv.toml").resolve())
    assert env["UV_INDEX_URL"] == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
    assert env["UV_DEFAULT_INDEX"] == "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"
    assert env["PYTHONPATH"].startswith(str((Path(__file__).resolve().parents[1] / "src").resolve()))
    assert "/tmp/existing-pythonpath" in env["PYTHONPATH"]
    assert (config_dir / "uv.toml").read_text(encoding="utf-8") == (
        'python-install-mirror = "https://ghfast.top/'
        'https://github.com/astral-sh/python-build-standalone/releases/download"\n'
        "[[index]]\n"
        'url = "https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/"\n'
        "default = true\n"
    )


def test_stage3_agent_bridge_env_marks_bare_custom_endpoint_model(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    monkeypatch.delenv(DEPLOYMENT_LLM_MODEL_ENV, raising=False)
    monkeypatch.delenv("FEATURE_FACTORY_HUAWEI_LLM_MODEL", raising=False)
    monkeypatch.setattr(image_assets, "resolve_agent_server_build_args", lambda: ("default", {}))
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-bridge-agent-llm-env.db'}",
        _env_file=None,
        stage3_workspace_dir=tmp_path / "stage3",
        stage3_openhands_sdk_root=sdk_root,
        stage3_llm_model="kimi-k2.6-w4a8",
        stage3_llm_base_url="http://10.43.2.168:8073/v1",
        llm_ssl_verify=False,
    )
    backend = stage3_backend_module.OpenHandsStage3Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )

    env = backend._build_agent_bridge_env()

    assert env["LLM_MODEL"] == "kimi-k2.6-w4a8"
    assert env["LLM_BASE_URL"] == "http://10.43.2.168:8073/v1"
    assert env["FEATURE_FACTORY_LLM_SSL_VERIFY"] == "false"
    assert "SSL_VERIFY" not in env
    assert env[DEPLOYMENT_LLM_MODEL_ENV] == "kimi-k2.6-w4a8"


def test_stage3_backend_runtime_snapshot_overrides_min_removed_code_lines() -> None:
    settings = Settings(stage3_min_removed_code_lines=10)

    resolved = stage3_backend_module.stage3_settings_with_runtime_snapshot(
        settings,
        {"hyperparameters": {"min_removed_code_lines": 4}},
    )

    assert resolved.stage3_min_removed_code_lines == 4
    assert settings.stage3_min_removed_code_lines == 10


def _seed_repository(
    client: TestClient,
    *,
    github_repo_id: int = 1,
    full_name: str = "owner/stage3-ready-repo",
) -> GitHubRepository:
    session = client.app.state.session_factory()
    try:
        owner_login, name = full_name.split("/", 1)
        repository = GitHubRepository(
            github_repo_id=github_repo_id,
            full_name=full_name,
            owner_login=owner_login,
            name=name,
            html_url=f"https://github.com/{full_name}",
            api_url=f"https://api.github.com/repos/{full_name}",
            description="stage3 repo",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=42,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        return repository
    finally:
        session.close()


def _seed_stage2_passed_run(
    client: TestClient,
    *,
    repository_id: int,
    commit_sha: str,
    created_at: datetime,
    planner_guidance: str,
    dockerfile_text: str = "FROM python:3.13",
    run_script_text: str = "#!/usr/bin/env bash\npytest",
    test_files: list[tuple[str, int]] | None = None,
    runtime_snapshot_json: dict | None = None,
) -> Stage2Run:
    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            repository_id=repository_id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            phase="completed",
            target_branch="main",
            target_commit_sha=commit_sha,
            planner_guidance=planner_guidance,
            dockerfile_text=dockerfile_text,
            run_script_text=run_script_text,
            runtime_snapshot_json=runtime_snapshot_json or {},
            created_at=created_at,
            updated_at=created_at + timedelta(minutes=5),
            started_at=created_at,
            finished_at=created_at + timedelta(minutes=4),
        )
        session.add(run)
        session.flush()
        for item in test_files or []:
            test_file_path = item[0]
            total_tests = item[1]
            target_selector = item[2] if len(item) > 2 else test_file_path
            session.add(
                Stage2TestResult(
                    run_id=run.id,
                    test_file_path=test_file_path,
                    target_selector=target_selector,
                    status="passed",
                    total_tests=total_tests,
                    passed_tests=total_tests,
                    failed_tests=0,
                    error_tests=0,
                    skipped_tests=0,
                    exit_code=0,
                    raw_result_json={"status": "passed"},
                )
            )
        session.commit()
        session.refresh(run)
        return run
    finally:
        session.close()


def _git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return str(completed.stdout or "").strip()


def _install_fake_stage3_runtime(monkeypatch) -> None:
    def fake_materialize_repository_checkout(  # noqa: ANN001
        self,
        run,
        *,
        repository,
        target_commit_sha,
        emit_event=None,
        cancel_requested=None,
    ):
        del emit_event, cancel_requested
        manifest = self._workspace_manifest_for_run(run)
        repo_dir = Path(manifest["repo_dir"])
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        repo_dir.mkdir(parents=True, exist_ok=True)
        _git(repo_dir, "init")
        _git(repo_dir, "config", "user.name", "Stage3 Test")
        _git(repo_dir, "config", "user.email", "stage3-test@example.com")
        (repo_dir / "app.py").write_text(_STAGE3_BASELINE_APP_TEXT, encoding="utf-8")
        _git(repo_dir, "add", "app.py")
        _git(repo_dir, "commit", "-m", "baseline")
        baseline_commit_sha = _git(repo_dir, "rev-parse", "HEAD")
        refreshed_manifest = self._refresh_workspace_manifest(run)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3.update(
            {
                "repository_clone_url": repository.html_url,
                "target_commit_sha": target_commit_sha,
                "baseline_commit_sha": baseline_commit_sha,
                "workspace": refreshed_manifest,
            }
        )
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }

        return {
            "clone_url": repository.html_url,
            "target_commit_sha": target_commit_sha,
            "baseline_commit_sha": baseline_commit_sha,
            "repo_dir": str(repo_dir),
        }

    class FakeStage3Evaluator:
        def evaluate_original_p2p(
            self,
            *,
            run_id: str,
            workspace_dir: Path,
            repo_dir: Path,
            snapshot_id: str,
            source_stage2_run_id: str,
            source_commit_sha: str,
            base_image_id: str,
            dockerfile_text: str,
            run_script_text: str,
            original_p2p_files: list[Any],
            emit_event=None,
        ) -> Stage3EvaluationResult:
            del (
                run_id,
                workspace_dir,
                snapshot_id,
                source_stage2_run_id,
                source_commit_sha,
                base_image_id,
                dockerfile_text,
                run_script_text,
            )
            content = (repo_dir / "app.py").read_text(encoding="utf-8")
            if "return total - penalty" in content:
                entry_counts = (4, 0, 4, 0, 0)
            elif "for item in items:" in content and "weights.append" not in content:
                entry_counts = (4, 2, 2, 0, 0)
            else:
                entry_counts = (4, 4, 0, 0, 0)

            def normalize_original_file(item: Any) -> tuple[str, str]:
                if isinstance(item, dict):
                    path = str(item.get("path") or item.get("test_file_path") or "")
                    return path, str(item.get("target_selector") or path)
                path = str(item or "")
                return path, path

            def build_result(
                path: str,
                counts: tuple[int, int, int, int, int],
                *,
                target_selector: str | None = None,
            ) -> Stage3FileEvaluation:
                total, passed, failed, errors, skipped = counts
                status = "passed" if failed == 0 and errors == 0 and passed > 0 else "failed"
                if emit_event is not None:
                    emit_event(
                        "Fake evaluator completed",
                        f"Evaluated {path}",
                        {
                            "test_file_path": path,
                            "target_selector": target_selector or path,
                            "status": status,
                        },
                    )
                return Stage3FileEvaluation(
                    test_file_path=path,
                    target_selector=target_selector or path,
                    status=status,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                    error_tests=errors,
                    skipped_tests=skipped,
                    pass_rate=(passed / total) if total > 0 else 0.0,
                    raw_result_json={
                        "action": "run",
                        "status": status,
                        "summary": {
                            "collected": total,
                            "passed": passed,
                            "failed": failed,
                            "errors": errors,
                            "skipped": skipped,
                        },
                    },
                )

            file_results: list[Stage3FileEvaluation] = []
            for item in original_p2p_files:
                path, target_selector = normalize_original_file(item)
                if path == "tests/test_target_feature.py":
                    file_results.append(build_result(path, entry_counts, target_selector=target_selector))
                else:
                    file_results.append(build_result(path, (3, 3, 0, 0, 0), target_selector=target_selector))
            return Stage3EvaluationResult(image_tag="fake-stage3:test", file_results=file_results)

    monkeypatch.setattr(Stage3Service, "_materialize_repository_checkout", fake_materialize_repository_checkout)
    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: FakeStage3Evaluator())


def test_stage3_repository_row_status_keeps_created_run_pending() -> None:
    service = Stage3Service.__new__(Stage3Service)

    status = service._repository_row_status(
        {
            "pending_count": 1,
            "queued_count": 0,
            "running_count": 0,
            "succeeded_count": 0,
            "failed_count": 0,
            "interrupted_count": 0,
            "latest_operation_at": datetime(2026, 4, 16, tzinfo=UTC),
        }
    )

    assert status == "pending"


def test_stage3_repository_row_status_prefers_aggregated_success_over_failed_latest_run() -> None:
    service = Stage3Service.__new__(Stage3Service)

    status = service._repository_row_status(
        {
            "pending_count": 0,
            "queued_count": 0,
            "running_count": 0,
            "succeeded_count": 1,
            "failed_count": 1,
            "interrupted_count": 0,
            "latest_operation_at": datetime(2026, 4, 16, tzinfo=UTC),
        }
    )

    assert status == "succeeded"


def test_stage3_completed_without_data_is_failed(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-no-data-complete.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-no-data")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="nodata123",
        created_at=datetime(2024, 1, 9, tzinfo=UTC),
        planner_guidance="no data should not be success",
        test_files=[("tests/test_no_data.py", 3)],
    )
    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        completed_run = service.mark_run_completed(run_id, summary_updates={"runner_status": "completed"})
        session.commit()
        assert completed_run.result == Stage3RunResult.failed.value
        assert completed_run.error_message == "stage3 run completed without producing any data"
    finally:
        session.close()

    detail_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert detail_payload["selected_run"]["result"] == Stage3RunResult.failed.value
    assert detail_payload["selected_run"]["summary"]["savepoint_count"] == 0
    assert detail_payload["selected_entry_file"]["status"] == "failed"
    list_payload = client.get("/api/stage3/repos").json()
    assert list_payload["repositories"][0]["stage3"]["status"] == "failed"


def _create_stage3_savepoint_via_tool_path(
    client: TestClient,
    *,
    repository_id: int,
    run_id: str,
    milestone_summary: str | None = None,
    rationale: str | None = None,
) -> dict[str, object]:
    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        try:
            _run, feedback = service.create_savepoint(
                repository_id,
                run_id,
                milestone_summary=milestone_summary,
                rationale=rationale,
            )
        except Stage3SavepointRejectedError as exc:
            feedback = dict(exc.feedback or {})
        session.commit()
        return dict(feedback or {})
    finally:
        session.close()


def _start_stage3_run_via_runner_path(
    client: TestClient,
    *,
    repository_id: int,
    run_id: str,
) -> dict[str, object]:
    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        run = service.start_run(repository_id, run_id)
        payload = service.serialize_run_detail(run)
        session.commit()
        return dict(payload)
    finally:
        session.close()


def test_stage3_materialized_checkout_uses_stage2_commit_as_baseline(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-baseline.db'}",
        stage3_workspace_dir=Path("stage3"),
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    source_repo_dir = tmp_path / "source-repo"
    source_repo_dir.mkdir(parents=True)
    _git(source_repo_dir, "init")
    _git(source_repo_dir, "checkout", "-b", "main")
    _git(source_repo_dir, "config", "user.name", "Stage3 Test")
    _git(source_repo_dir, "config", "user.email", "stage3-test@example.com")
    (source_repo_dir / "app.py").write_text("baseline\n", encoding="utf-8")
    _git(source_repo_dir, "add", "app.py")
    _git(source_repo_dir, "commit", "-m", "baseline")
    stage2_commit_sha = _git(source_repo_dir, "rev-parse", "HEAD")

    session = build_session_factory(engine)()
    try:
        service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
        run_id = str(uuid.uuid4())
        workspace_dir = settings.stage3_workspace_dir / "runs" / run_id
        manifest = {
            "workspace_path": str(workspace_dir),
            "repo_dir": str(workspace_dir / "repo"),
            "metadata_dir": str(workspace_dir / ".stage3"),
            "assets_dir": str(workspace_dir / "assets"),
            "checkpoint_dir": str(workspace_dir / ".stage3" / "checkpoints"),
            "savepoint_dir": str(workspace_dir / ".stage3" / "savepoints"),
        }
        run = Stage3Run(
            id=run_id,
            entry_file_id=str(uuid.uuid4()),
            workspace_path=str(workspace_dir),
            runtime_snapshot_json={
                "stage3": {
                    "source_commit_sha": stage2_commit_sha,
                    "workspace": manifest,
                }
            },
        )
        repository = GitHubRepository(
            github_repo_id=991,
            full_name="owner/stage3-baseline",
            owner_login="owner",
            name="stage3-baseline",
            html_url=str(source_repo_dir),
            api_url="",
            default_branch="main",
        )

        payload = service._materialize_repository_checkout(  # noqa: SLF001
            run,
            repository=repository,
            target_commit_sha=stage2_commit_sha,
        )

        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        repo_dir = Path(payload["repo_dir"])
        assert repo_dir.is_absolute()
        assert repo_dir == (tmp_path / "stage3" / "runs" / run_id / "repo").resolve()
        assert (repo_dir / ".git").is_dir()
        assert not (workspace_dir / workspace_dir / "repo").exists()
        assert Path(runtime_stage3["workspace"]["workspace_path"]).is_absolute()
        assert Path(runtime_stage3["workspace"]["repo_dir"]).is_absolute()
        assert payload["target_commit_sha"] == stage2_commit_sha
        assert payload["baseline_commit_sha"] == stage2_commit_sha
        assert payload["resolved_head_commit_sha"] == stage2_commit_sha
        assert runtime_stage3["baseline_commit_sha"] == stage2_commit_sha
        assert runtime_stage3["resolved_head_commit_sha"] == stage2_commit_sha
        cache_dir = tmp_path / "data" / "stage2" / "cache" / "repos" / "owner" / "stage3-baseline.git"
        assert Path(runtime_stage3["repository_cache_path"]) == cache_dir
        assert cache_dir.is_dir()
        event_titles = [str(event.title or "") for event in run.events]
        assert "Repository cache miss" in event_titles
        assert "Repository cache clone started" in event_titles
        assert "Repository checkout materializing" in event_titles
        assert "Target commit checked out" in event_titles
    finally:
        session.close()


def test_stage3_openhands_bridge_has_module_entrypoint() -> None:
    bridge = (Path(__file__).resolve().parents[1] / "src/feature_factory/stage3/openhands_bridge.py").read_text(
        encoding="utf-8"
    )

    assert 'if __name__ == "__main__":' in bridge
    assert "raise SystemExit(main())" in bridge


def test_stage3_repository_clone_url_uses_cn_github_proxy(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-clone-url.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    session = build_session_factory(engine)()
    try:
        service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
        public_repo = GitHubRepository(
            github_repo_id=992,
            full_name="owner/stage3-proxy",
            owner_login="owner",
            name="stage3-proxy",
            html_url="https://github.com/owner/stage3-proxy",
            api_url="https://api.github.com/repos/owner/stage3-proxy",
        )
        public_git_repo = GitHubRepository(
            github_repo_id=993,
            full_name="owner/stage3-proxy-git",
            owner_login="owner",
            name="stage3-proxy-git",
            html_url="https://github.com/owner/stage3-proxy-git.git",
            api_url="https://api.github.com/repos/owner/stage3-proxy-git",
        )
        ssh_repo = GitHubRepository(
            github_repo_id=994,
            full_name="owner/stage3-ssh",
            owner_login="owner",
            name="stage3-ssh",
            html_url="git@github.com:owner/stage3-ssh.git",
            api_url="https://api.github.com/repos/owner/stage3-ssh",
        )
        local_repo = GitHubRepository(
            github_repo_id=995,
            full_name="owner/stage3-local",
            owner_login="owner",
            name="stage3-local",
            html_url=str(tmp_path / "local-repo.git"),
            api_url="",
        )

        assert service._repository_clone_url(public_repo) == (  # noqa: SLF001
            "https://ghfast.top/https://github.com/owner/stage3-proxy.git"
        )
        assert service._repository_clone_url(public_git_repo) == (  # noqa: SLF001
            "https://ghfast.top/https://github.com/owner/stage3-proxy-git.git"
        )
        assert service._repository_clone_url(ssh_repo) == "git@github.com:owner/stage3-ssh.git"  # noqa: SLF001
        assert service._repository_clone_url(local_repo) == str(tmp_path / "local-repo.git")  # noqa: SLF001
    finally:
        session.close()


def _store_fake_stage3_resume_checkpoint(
    client: TestClient,
    *,
    run_id: str,
    depth: int,
    source_snapshot_path: str,
    source_image_ref: str,
) -> dict[str, object]:
    stage3_root = client.app.state.settings.stage3_workspace_dir
    checkpoint_root = stage3_root / "runtime" / run_id / "breaker-checkpoints" / f"depth-{depth:03d}"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    snapshot_path = checkpoint_root / "workspace_snapshot.tar.gz"
    shutil.copy2(source_snapshot_path, snapshot_path)
    conversation_id = uuid.uuid4()
    conversations_path = checkpoint_root / "openhands" / "conversations"
    (conversations_path / conversation_id.hex).mkdir(parents=True, exist_ok=True)
    bash_events_dir = checkpoint_root / "openhands" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    (bash_events_dir / "events.json").write_text("[]\n", encoding="utf-8")
    checkpoint = {
        "schema_version": 1,
        "checkpoint_type": "stage3_breaker_cold_restore",
        "run_id": run_id,
        "depth": depth,
        "checkpoint_dir": str(checkpoint_root),
        "workspace_snapshot_path": str(snapshot_path),
        "conversation_id": str(conversation_id),
        "openhands": {
            "conversations_path": str(conversations_path),
            "bash_events_dir": str(bash_events_dir),
        },
        "docker_image_ref": source_image_ref,
        "docker_commit_status": "saved",
        "docker_commit": {"docker_image_ref": source_image_ref},
    }
    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=stage3_root,
            settings=client.app.state.settings,
        )
        service.store_savepoint_checkpoint(run_id, depth=depth, checkpoint=checkpoint)
        session.commit()
    finally:
        session.close()
    return checkpoint


def test_stage3_api_materializes_stage2_passed_commit_snapshot_and_entry_files(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-materialized")
    run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="abc123def456",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="Preserve the current test baseline.",
        test_files=[
            ("tests/test_feature_alpha.py", 12),
            ("tests/test_feature_beta.py", 7),
        ],
    )

    list_response = client.get("/api/stage3/repos")
    assert list_response.status_code == 200
    repositories = list_response.json()["repositories"]
    assert len(repositories) == 1
    assert repositories[0]["full_name"] == "owner/stage3-materialized"
    assert repositories[0]["stage3"]["eligible_commit_count"] == 1
    assert repositories[0]["stage3"]["entry_file_count"] == 2

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["selected_snapshot"]["source_stage2_run_id"] == run.id
    assert payload["selected_snapshot"]["source_commit_sha"] == "abc123def456"
    assert payload["selected_snapshot"]["planner_guidance"] == "Preserve the current test baseline."
    assert [row["test_file_path"] for row in payload["entry_files"]] == [
        "tests/test_feature_alpha.py",
        "tests/test_feature_beta.py",
    ]
    assert payload["selected_entry_file"]["baseline_pass_rate"] == 1.0

    session = client.app.state.session_factory()
    try:
        snapshots = list(session.scalars(select(Stage3CommitSnapshot)))
        entry_files = list(session.scalars(select(Stage3EntryFile).order_by(Stage3EntryFile.test_file_path.asc())))
    finally:
        session.close()

    assert len(snapshots) == 1
    assert snapshots[0].source_stage2_run_id == run.id
    initial_snapshot_updated_at = snapshots[0].updated_at
    initial_materialized_at = snapshots[0].summary_json["materialized_at"]
    assert [row.test_file_path for row in entry_files] == [
        "tests/test_feature_alpha.py",
        "tests/test_feature_beta.py",
    ]

    assert client.get(f"/api/stage3/repos/{repository.id}").status_code == 200
    session = client.app.state.session_factory()
    try:
        refreshed_snapshot = session.scalar(select(Stage3CommitSnapshot))
        assert refreshed_snapshot is not None
        assert refreshed_snapshot.updated_at == initial_snapshot_updated_at
        assert refreshed_snapshot.summary_json["materialized_at"] == initial_materialized_at
    finally:
        session.close()


def test_stage3_api_materializes_target_selector_from_stage2_results(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-target-selector.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-target-selector")
    run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="abc123selector",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="Preserve selector metadata.",
        test_files=[
            ("nltk/test/unit/lm/test_counter.py", 12, "unit/lm/test_counter.py"),
        ],
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert payload["selected_snapshot"]["source_stage2_run_id"] == run.id
    assert payload["entry_files"][0]["test_file_path"] == "nltk/test/unit/lm/test_counter.py"
    assert payload["entry_files"][0]["target_selector"] == "unit/lm/test_counter.py"

    session = client.app.state.session_factory()
    try:
        snapshot = session.scalar(select(Stage3CommitSnapshot))
        entry_file = session.scalar(select(Stage3EntryFile))
        assert snapshot is not None
        assert entry_file is not None
        assert entry_file.test_file_path == "nltk/test/unit/lm/test_counter.py"
        assert entry_file.target_selector == "unit/lm/test_counter.py"
        assert snapshot.original_p2p_files_json == [
            {
                "path": "nltk/test/unit/lm/test_counter.py",
                "target_selector": "unit/lm/test_counter.py",
                "baseline_total_tests": 12,
                "baseline_passed_tests": 12,
                "baseline_failed_tests": 0,
                "baseline_error_tests": 0,
                "baseline_skipped_tests": 0,
                "baseline_pass_rate": 1.0,
            }
        ]
    finally:
        session.close()


def test_stage3_api_filters_entry_files_by_stage2_test_count_min(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-filter.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-filtered")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="abc123def456",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="Preserve the current test baseline.",
        test_files=[
            ("tests/test_big.py", 5),
            ("tests/test_exact.py", 3),
            ("tests/test_small.py", 2),
        ],
        runtime_snapshot_json={
            "hyperparameters": {
                "entry_file_test_count_min": 3,
            },
        },
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert [row["test_file_path"] for row in payload["entry_files"]] == [
        "tests/test_big.py",
        "tests/test_exact.py",
    ]

    session = client.app.state.session_factory()
    try:
        snapshot = session.scalar(select(Stage3CommitSnapshot))
        assert snapshot is not None
        assert snapshot.summary_json["entry_file_count"] == 2
        assert snapshot.summary_json["source_entry_file_count"] == 3
        assert snapshot.summary_json["filtered_out_entry_file_count"] == 1
        assert snapshot.summary_json["entry_file_test_count_min"] == 3
        assert [row["path"] for row in snapshot.original_p2p_files_json] == [
            "tests/test_big.py",
            "tests/test_exact.py",
            "tests/test_small.py",
        ]
    finally:
        session.close()


def test_stage3_api_does_not_truncate_entry_files_by_stage2_p2p_file_count_limit(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-p2p-limit.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-p2p-limited")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="abc123def456",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="Preserve the current test baseline.",
        test_files=[
            ("tests/test_first.py", 5),
            ("tests/test_second.py", 4),
            ("tests/test_third.py", 3),
        ],
        runtime_snapshot_json={
            "hyperparameters": {
                "p2p_file_count_limit": 2,
            },
        },
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert [row["test_file_path"] for row in payload["entry_files"]] == [
        "tests/test_first.py",
        "tests/test_second.py",
        "tests/test_third.py",
    ]

    session = client.app.state.session_factory()
    try:
        snapshot = session.scalar(select(Stage3CommitSnapshot))
        assert snapshot is not None
        assert snapshot.summary_json["entry_file_count"] == 3
        assert snapshot.summary_json["source_entry_file_count"] == 3
        assert snapshot.summary_json["filtered_out_entry_file_count"] == 0
        assert "p2p_file_count_limit" not in snapshot.summary_json
        assert "p2p_file_count_before_limit" not in snapshot.summary_json
        assert "truncated_entry_file_count" not in snapshot.summary_json
        assert [row["path"] for row in snapshot.original_p2p_files_json] == [
            "tests/test_first.py",
            "tests/test_second.py",
            "tests/test_third.py",
        ]
    finally:
        session.close()


def test_stage3_api_collapses_duplicate_stage2_passed_runs_by_commit(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-duplicate-commit.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-duplicate-commit")
    older_run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="deadbeef1234",
        created_at=datetime(2024, 1, 8, tzinfo=UTC),
        planner_guidance="old guidance",
        test_files=[("tests/test_old.py", 3)],
    )
    newer_run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="deadbeef1234",
        created_at=datetime(2024, 1, 11, tzinfo=UTC),
        planner_guidance="new guidance",
        test_files=[("tests/test_new.py", 5)],
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    payload = detail_response.json()
    assert len(payload["commit_snapshots"]) == 1
    assert payload["selected_snapshot"]["source_stage2_run_id"] == newer_run.id
    assert payload["selected_snapshot"]["planner_guidance"] == "new guidance"
    assert [row["test_file_path"] for row in payload["entry_files"]] == ["tests/test_new.py"]

    session = client.app.state.session_factory()
    try:
        snapshots = list(session.scalars(select(Stage3CommitSnapshot)))
    finally:
        session.close()

    assert len(snapshots) == 1
    assert snapshots[0].source_stage2_run_id == newer_run.id
    assert snapshots[0].source_stage2_run_id != older_run.id


def test_stage3_materialize_freezes_existing_runs_but_defaults_new_runs_to_latest_snapshot(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-materialize-freeze.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-materialize-freeze")
    older_run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="freeze1234",
        created_at=datetime(2024, 1, 8, tzinfo=UTC),
        planner_guidance="old guidance",
        test_files=[("tests/test_old.py", 3)],
    )
    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = detail_payload["selected_entry_file"]["id"]
    created_run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]

    newer_run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="freeze1234",
        created_at=datetime(2024, 1, 11, tzinfo=UTC),
        planner_guidance="new guidance",
        test_files=[("tests/test_new.py", 5)],
    )

    refreshed_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    assert refreshed_payload["selected_snapshot"]["source_stage2_run_id"] == newer_run.id
    assert refreshed_payload["selected_snapshot"]["source_stage2_run_id"] != older_run.id
    assert [row["test_file_path"] for row in refreshed_payload["entry_files"]] == ["tests/test_new.py"]
    assert len(refreshed_payload["commit_snapshots"]) == 1
    repo_list_payload = client.get("/api/stage3/repos").json()
    repo_row = next(row for row in repo_list_payload["repositories"] if row["id"] == repository.id)
    assert repo_row["stage3"]["eligible_commit_count"] == 1

    frozen_run_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_run_id": created_run_id},
    ).json()
    assert frozen_run_payload["selected_snapshot"]["source_stage2_run_id"] == older_run.id
    assert frozen_run_payload["selected_run"]["id"] == created_run_id
    assert [row["test_file_path"] for row in frozen_run_payload["entry_files"]] == ["tests/test_old.py"]
    assert len(frozen_run_payload["commit_snapshots"]) == 2

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage3Run, created_run_id) is not None
        snapshots = list(
            session.scalars(
                select(Stage3CommitSnapshot).where(Stage3CommitSnapshot.repository_id == repository.id)
            )
        )
    finally:
        session.close()

    assert len(snapshots) == 2


def test_stage3_create_run_uses_latest_snapshot_when_entry_file_id_is_stale(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-create-run-latest-snapshot.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-create-run-latest-snapshot")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="latestsnapshot123",
        created_at=datetime(2024, 1, 8, tzinfo=UTC),
        planner_guidance="old guidance",
        test_files=[("tests/test_shared.py", 3)],
    )
    old_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    old_entry_file_id = old_payload["selected_entry_file"]["id"]

    newer_run = _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="latestsnapshot123",
        created_at=datetime(2024, 1, 11, tzinfo=UTC),
        planner_guidance="new guidance",
        test_files=[("tests/test_shared.py", 5)],
    )

    create_response = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{old_entry_file_id}/runs"
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    assert payload["selected_snapshot"]["source_stage2_run_id"] == newer_run.id
    assert payload["selected_entry_file"]["test_file_path"] == "tests/test_shared.py"
    assert payload["selected_entry_file"]["id"] != old_entry_file_id
    assert payload["selected_run"]["runtime_snapshot"]["stage3"]["source_stage2_run_id"] == newer_run.id


def test_stage3_repository_image_prewarm_status_endpoint(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-image-prewarm-status.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-image-prewarm-status")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="feedface1234",
        created_at=datetime(2024, 1, 9, tzinfo=UTC),
        planner_guidance="prewarm",
        test_files=[("tests/test_status.py", 4)],
    )

    response = client.get(f"/api/stage3/repos/{repository.id}/image-prewarm")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repository_id"] == repository.id
    assert payload["snapshot"]["source_commit_sha"] == "feedface1234"
    assert payload["runtime_image"]["image_ref"].startswith("feature-factory/stage3-breaker-runtime:")
    assert payload["agent_server_image"]["image_ref"].startswith("feature-factory/openhands-agent-server:")


def test_stage3_repository_image_prewarm_post_materializes_snapshot_workspace(tmp_path, monkeypatch) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-image-prewarm-post.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-image-prewarm-post")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="c001d00d1234",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="prewarm",
        test_files=[("tests/test_prewarm.py", 2)],
    )

    monkeypatch.setattr(server_module, "detect_stage3_platform", lambda: "linux/amd64")

    def fake_runtime_build(**kwargs):  # noqa: ANN001
        log_callback = kwargs.get("log_callback")
        if callable(log_callback):
            log_callback("runtime build ok")
        return "feature-factory/stage3-breaker-runtime:test-linux-amd64"

    def fake_agent_build(**kwargs):  # noqa: ANN001
        log_callback = kwargs.get("log_callback")
        if callable(log_callback):
            log_callback("agent build ok")
        return "feature-factory/openhands-agent-server:test"

    monkeypatch.setattr(server_module, "ensure_stage3_breaker_runtime_image_built", fake_runtime_build)
    monkeypatch.setattr(server_module, "ensure_agent_server_image_built", fake_agent_build)

    response = client.post(
        f"/api/stage3/repos/{repository.id}/image-prewarm",
        json={},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] is True
    snapshot_id = str(payload["snapshot_id"])
    workspace_dir = settings.stage3_workspace_dir / "snapshots" / snapshot_id
    deadline = time.time() + 2.0
    while time.time() < deadline and not (workspace_dir / "assets" / "snapshot.json").exists():
        time.sleep(0.05)
    assert (workspace_dir / "assets" / "Dockerfile").exists()
    assert (workspace_dir / "assets" / "run_script.sh").exists()
    assert (workspace_dir / "assets" / "snapshot.json").exists()

def test_stage3_repo_list_status_sort_filter_and_runtime_config(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-list.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repo_pending = _seed_repository(client, github_repo_id=11, full_name="owner/stage3-pending")
    repo_success = _seed_repository(client, github_repo_id=12, full_name="owner/stage3-success")
    repo_running = _seed_repository(client, github_repo_id=13, full_name="owner/stage3-running")
    repo_mixed = _seed_repository(client, github_repo_id=14, full_name="owner/stage3-mixed")
    repo_interrupted = _seed_repository(client, github_repo_id=15, full_name="owner/stage3-interrupted")

    _seed_stage2_passed_run(
        client,
        repository_id=repo_pending.id,
        commit_sha="pending123",
        created_at=datetime(2024, 1, 10, tzinfo=UTC),
        planner_guidance="pending",
        test_files=[("tests/test_pending.py", 3)],
    )
    _seed_stage2_passed_run(
        client,
        repository_id=repo_success.id,
        commit_sha="success123",
        created_at=datetime(2024, 1, 11, tzinfo=UTC),
        planner_guidance="success",
        test_files=[("tests/test_success.py", 5)],
    )
    _seed_stage2_passed_run(
        client,
        repository_id=repo_running.id,
        commit_sha="running123",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="running",
        test_files=[("tests/test_running.py", 7)],
    )
    _seed_stage2_passed_run(
        client,
        repository_id=repo_mixed.id,
        commit_sha="mixed123",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="mixed",
        test_files=[("tests/test_mixed.py", 4)],
    )
    _seed_stage2_passed_run(
        client,
        repository_id=repo_interrupted.id,
        commit_sha="interrupted123",
        created_at=datetime(2024, 1, 14, tzinfo=UTC),
        planner_guidance="interrupted",
        test_files=[("tests/test_interrupted.py", 2)],
    )

    materialize_response = client.get("/api/stage3/repos")
    assert materialize_response.status_code == 200

    session = client.app.state.session_factory()
    try:
        success_entry = session.scalar(
            select(Stage3EntryFile)
            .join(Stage3CommitSnapshot, Stage3CommitSnapshot.id == Stage3EntryFile.snapshot_id)
            .where(Stage3CommitSnapshot.repository_id == repo_success.id)
        )
        running_entry = session.scalar(
            select(Stage3EntryFile)
            .join(Stage3CommitSnapshot, Stage3CommitSnapshot.id == Stage3EntryFile.snapshot_id)
            .where(Stage3CommitSnapshot.repository_id == repo_running.id)
        )
        mixed_entry = session.scalar(
            select(Stage3EntryFile)
            .join(Stage3CommitSnapshot, Stage3CommitSnapshot.id == Stage3EntryFile.snapshot_id)
            .where(Stage3CommitSnapshot.repository_id == repo_mixed.id)
        )
        interrupted_entry = session.scalar(
            select(Stage3EntryFile)
            .join(Stage3CommitSnapshot, Stage3CommitSnapshot.id == Stage3EntryFile.snapshot_id)
            .where(Stage3CommitSnapshot.repository_id == repo_interrupted.id)
        )
        assert success_entry is not None
        assert running_entry is not None
        assert mixed_entry is not None
        assert interrupted_entry is not None

        success_run = Stage3Run(
            entry_file_id=success_entry.id,
            status=Stage3RunStatus.completed.value,
            result="archived",
            created_at=datetime(2024, 1, 20, 8, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 20, 9, 0, tzinfo=UTC),
            started_at=datetime(2024, 1, 20, 8, 5, tzinfo=UTC),
            finished_at=datetime(2024, 1, 20, 8, 55, tzinfo=UTC),
        )
        session.add(success_run)
        session.flush()
        session.add(
            Stage3Savepoint(
                run_id=success_run.id,
                depth=1,
                entry_pass_rate=0.4,
                p2p_files_json=[],
                f2p_files_json=["tests/test_success.py"],
                collateral_json={},
                gold_patch_text=(
                    "diff --git a/app.py b/app.py\n"
                    "--- a/app.py\n"
                    "+++ b/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                ),
                checkpoint_json={},
                summary_json={},
            )
        )

        mixed_success_run = Stage3Run(
            entry_file_id=mixed_entry.id,
            status=Stage3RunStatus.completed.value,
            result="archived",
            created_at=datetime(2024, 1, 19, 8, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 19, 8, 30, tzinfo=UTC),
            started_at=datetime(2024, 1, 19, 8, 5, tzinfo=UTC),
            finished_at=datetime(2024, 1, 19, 8, 25, tzinfo=UTC),
        )
        session.add(mixed_success_run)
        session.flush()
        session.add(
            Stage3Savepoint(
                run_id=mixed_success_run.id,
                depth=1,
                entry_pass_rate=0.2,
                p2p_files_json=[],
                f2p_files_json=["tests/test_mixed.py"],
                collateral_json={},
                gold_patch_text=(
                    "diff --git a/mixed.py b/mixed.py\n"
                    "--- a/mixed.py\n"
                    "+++ b/mixed.py\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                ),
                checkpoint_json={},
                summary_json={},
            )
        )
        session.add(
            Stage3Run(
                entry_file_id=mixed_entry.id,
                status=Stage3RunStatus.completed.value,
                result=Stage3RunResult.failed.value,
                error_message="breaker failed after prior archive",
                created_at=datetime(2024, 1, 19, 9, 0, tzinfo=UTC),
                updated_at=datetime(2024, 1, 19, 9, 30, tzinfo=UTC),
                started_at=datetime(2024, 1, 19, 9, 5, tzinfo=UTC),
                finished_at=datetime(2024, 1, 19, 9, 25, tzinfo=UTC),
            )
        )
        session.add(
            Stage3Run(
                entry_file_id=interrupted_entry.id,
                status=Stage3RunStatus.completed.value,
                result=Stage3RunResult.interrupted.value,
                error_message="stage3 run interrupted by user",
                created_at=datetime(2024, 1, 18, 8, 0, tzinfo=UTC),
                updated_at=datetime(2024, 1, 18, 8, 30, tzinfo=UTC),
                started_at=datetime(2024, 1, 18, 8, 5, tzinfo=UTC),
                finished_at=datetime(2024, 1, 18, 8, 25, tzinfo=UTC),
            )
        )

        running_run = Stage3Run(
            entry_file_id=running_entry.id,
            status=Stage3RunStatus.running.value,
            result="unknown",
            created_at=datetime(2024, 1, 21, 8, 0, tzinfo=UTC),
            updated_at=datetime(2024, 1, 21, 10, 0, tzinfo=UTC),
            started_at=datetime(2024, 1, 21, 8, 5, tzinfo=UTC),
        )
        session.add(running_run)
        session.commit()
    finally:
        session.close()

    list_response = client.get("/api/stage3/repos")
    assert list_response.status_code == 200
    repositories = list_response.json()["repositories"]
    assert [row["full_name"] for row in repositories] == [
        "owner/stage3-running",
        "owner/stage3-success",
        "owner/stage3-mixed",
        "owner/stage3-interrupted",
        "owner/stage3-pending",
    ]
    row_by_name = {row["full_name"]: row for row in repositories}
    assert row_by_name["owner/stage3-pending"]["stage3"]["status"] == "pending"
    assert row_by_name["owner/stage3-success"]["stage3"]["status"] == "succeeded"
    assert row_by_name["owner/stage3-mixed"]["stage3"]["status"] == "succeeded"
    assert row_by_name["owner/stage3-interrupted"]["stage3"]["status"] == "interrupted"
    assert row_by_name["owner/stage3-success"]["stage3"]["produced_entry_file_count"] == 1
    assert row_by_name["owner/stage3-success"]["stage3"]["entry_file_count"] == 1
    assert row_by_name["owner/stage3-success"]["stage3"]["produced_data_count"] == 1
    assert row_by_name["owner/stage3-success"]["stage3"]["latest_operation_at"] == "2024-01-20T09:00:00+00:00"
    assert row_by_name["owner/stage3-running"]["stage3"]["status"] == "running"

    detail_response = client.get(f"/api/stage3/repos/{repo_success.id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["selected_run"]["savepoints"][0]["summary"]["diff_stats"] == {
        "total_changed_lines": 2,
        "added_lines": 1,
        "removed_lines": 1,
    }

    running_only_response = client.get("/api/stage3/repos", params=[("statuses", "running")])
    assert running_only_response.status_code == 200
    running_only_rows = running_only_response.json()["repositories"]
    assert [row["full_name"] for row in running_only_rows] == ["owner/stage3-running"]

    interrupted_only_response = client.get("/api/stage3/repos", params=[("statuses", "interrupted")])
    assert interrupted_only_response.status_code == 200
    interrupted_only_rows = interrupted_only_response.json()["repositories"]
    assert [row["full_name"] for row in interrupted_only_rows] == ["owner/stage3-interrupted"]

    running_detail_response = client.get(f"/api/stage3/repos/{repo_running.id}")
    assert running_detail_response.status_code == 200
    running_entry_payload = running_detail_response.json()["entry_files"][0]
    assert running_entry_payload["latest_run_id"] == running_run.id
    assert running_entry_payload["latest_operation_at"] == "2024-01-21T10:00:00+00:00"

    produced_sort_response = client.get(
        "/api/stage3/repos",
        params={"sort_by": "produced_data_count", "sort_order": "desc"},
    )
    assert produced_sort_response.status_code == 200
    assert produced_sort_response.json()["repositories"][0]["full_name"] == "owner/stage3-mixed"

    runtime_response = client.get("/api/stage3/runtime")
    assert runtime_response.status_code == 200
    assert runtime_response.json()["concurrency"]["max_concurrent_runs"] == 4
    assert runtime_response.json()["concurrency"]["system_capacity"] == 64
    assert runtime_response.json()["breaker"]["model"] == ""
    assert runtime_response.json()["breaker"]["preset"] == "default"
    assert runtime_response.json()["hyperparameters"]["run_test_timeout_seconds"] == 300
    assert runtime_response.json()["hyperparameters"]["full_validation_timeout_seconds"] == 2400
    assert runtime_response.json()["hyperparameters"]["entry_pass_rate_ceiling"] == 0.5
    assert runtime_response.json()["hyperparameters"]["min_removed_code_lines"] == 10

    update_runtime_response = client.patch(
        "/api/stage3/runtime",
        json={
            "max_concurrent_runs": 3,
            "breaker_model": "openai/gpt-5.4",
            "breaker_base_url": "https://llm.example/v1",
            "breaker_api_key": "sk-stage3-test",
            "breaker_preset": "gpt5",
            "breaker_max_iterations": 222,
            "breaker_timeout_seconds": 2400,
            "build_timeout_seconds": 900,
            "run_test_timeout_seconds": 360,
            "full_validation_timeout_seconds": 1200,
            "entry_pass_rate_ceiling": 0.25,
            "min_removed_code_lines": 12,
        },
    )
    assert update_runtime_response.status_code == 200
    runtime_payload = update_runtime_response.json()
    assert runtime_payload["concurrency"]["max_concurrent_runs"] == 3
    assert runtime_payload["concurrency"]["system_capacity"] == 64
    assert runtime_payload["breaker"]["model"] == "openai/gpt-5.4"
    assert runtime_payload["breaker"]["base_url"] == "https://llm.example/v1"
    assert runtime_payload["breaker"]["api_key_configured"] is True
    assert runtime_payload["breaker"]["api_key_preview"].startswith("sk-sta")
    assert runtime_payload["breaker"]["preset"] == "gpt5"
    assert runtime_payload["breaker"]["max_iterations"] == 222
    assert runtime_payload["breaker"]["timeout_seconds"] == 2400
    assert runtime_payload["hyperparameters"]["build_timeout_seconds"] == 900
    assert runtime_payload["hyperparameters"]["run_test_timeout_seconds"] == 360
    assert runtime_payload["hyperparameters"]["full_validation_timeout_seconds"] == 1200
    assert runtime_payload["hyperparameters"]["entry_pass_rate_ceiling"] == 0.25
    assert runtime_payload["hyperparameters"]["min_removed_code_lines"] == 12


def test_stage3_runtime_templates_can_persist_overwrite_and_delete_snapshots(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-runtime-templates.db'}",
        stage3_workspace_dir=tmp_path / "stage3",
        stage3_llm_api_key=SecretStr("breaker-secret"),
    )
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )

    created = client.post(
        "/api/stage3/runtime/templates",
        json={
            "name": "Stage3 默认模板",
            "max_concurrent_runs": 32,
            "breaker_model": "openai/stage3-breaker-v1",
            "breaker_base_url": "https://breaker.example.test/v1",
            "breaker_preset": "gpt5",
            "breaker_max_iterations": 180,
            "breaker_timeout_seconds": 1200,
            "build_timeout_seconds": 900,
            "run_test_timeout_seconds": 360,
            "full_validation_timeout_seconds": 2400,
            "entry_pass_rate_ceiling": 0.4,
            "min_removed_code_lines": 14,
        },
    )

    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["name"] == "Stage3 默认模板"
    assert created_payload["breaker_model"] == "openai/stage3-breaker-v1"

    listed = client.get("/api/stage3/runtime/templates")
    assert listed.status_code == 200
    templates = listed.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["id"] == created_payload["id"]
    assert "breaker-secret" not in listed.text

    detail = client.get(f"/api/stage3/runtime/templates/{created_payload['id']}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["snapshot"]["concurrency"]["max_concurrent_runs"] == 32
    assert detail_payload["snapshot"]["breaker"]["model"] == "openai/stage3-breaker-v1"
    assert detail_payload["snapshot"]["breaker"]["api_key"] == "breaker-secret"
    assert detail_payload["snapshot"]["breaker"]["api_key_preview"] == "breake..."
    assert detail_payload["snapshot"]["hyperparameters"]["build_timeout_seconds"] == 900
    assert detail_payload["snapshot"]["hyperparameters"]["run_test_timeout_seconds"] == 360
    assert detail_payload["snapshot"]["hyperparameters"]["full_validation_timeout_seconds"] == 2400
    assert detail_payload["snapshot"]["hyperparameters"]["entry_pass_rate_ceiling"] == 0.4
    assert detail_payload["snapshot"]["hyperparameters"]["min_removed_code_lines"] == 14

    overwritten = client.post(
        "/api/stage3/runtime/templates",
        json={
            "name": "Stage3 默认模板",
            "max_concurrent_runs": 48,
            "breaker_model": "openai/stage3-breaker-v2",
        },
    )
    assert overwritten.status_code == 200
    overwritten_payload = overwritten.json()
    assert overwritten_payload["id"] == created_payload["id"]
    assert overwritten_payload["breaker_model"] == "openai/stage3-breaker-v2"

    updated_detail = client.get(f"/api/stage3/runtime/templates/{created_payload['id']}")
    assert updated_detail.status_code == 200
    updated_payload = updated_detail.json()
    assert updated_payload["snapshot"]["concurrency"]["max_concurrent_runs"] == 48
    assert updated_payload["snapshot"]["breaker"]["model"] == "openai/stage3-breaker-v2"
    assert updated_payload["snapshot"]["breaker"]["api_key"] == "breaker-secret"

    deleted = client.delete(f"/api/stage3/runtime/templates/{created_payload['id']}")
    assert deleted.status_code == 200
    assert deleted.json() == {
        "id": created_payload["id"],
        "name": "Stage3 默认模板",
    }

    relisted = client.get("/api/stage3/runtime/templates")
    assert relisted.status_code == 200
    assert relisted.json()["templates"] == []

    missing_detail = client.get(f"/api/stage3/runtime/templates/{created_payload['id']}")
    assert missing_detail.status_code == 404


def test_stage3_api_can_create_and_delete_entry_file_runs(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-runs.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client, full_name="owner/stage3-runs")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="feedface1234",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="preserve sibling tests",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )

    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    payload = create_response.json()
    assert len(payload["runs"]) == 1
    run_id = payload["selected_run"]["id"]
    assert payload["selected_run"]["status"] == Stage3RunStatus.pending.value
    assert payload["selected_run"]["can_start"] is True
    assert payload["selected_run"]["can_rerun"] is False
    assert payload["selected_run"]["source_stage2_run_id"]
    assert payload["selected_run"]["source_commit_sha"] == "feedface1234"
    assert payload["selected_run"]["baseline_commit_sha"] == "feedface1234"
    assert payload["selected_run"]["runtime_snapshot"]["stage3"]["entry_file_path"] == "tests/test_target_feature.py"
    assert "api_key" not in payload["selected_run"]["runtime_snapshot"]["stage3"]["runtime"]["breaker"]
    assert payload["selected_run"]["workspace_manifest"]["workspace_path"] == str(stage3_root / "runs" / run_id)
    assert payload["selected_run"]["workspace_manifest"]["generated_file_count"] >= 5
    assert payload["selected_run"]["events"][0]["title"] == "Run created"
    workspace_dir = stage3_root / "runs" / run_id
    assert workspace_dir.exists()
    assert (workspace_dir / "assets" / "entry_file.json").exists()
    assert (workspace_dir / "assets" / "original_p2p_files.json").exists()
    assert (workspace_dir / ".stage3" / "workspace_manifest.json").exists()

    duplicate_create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert duplicate_create_response.status_code == 409

    update_response = client.patch(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/runtime",
        json={
            "max_concurrent_runs": 72,
            "breaker_model": "openai/stage3-breaker-edited",
            "breaker_base_url": "https://stage3-llm.example/v1",
            "breaker_api_key": "secret-stage3-key",
            "breaker_preset": "gpt5",
            "breaker_max_iterations": 321,
            "breaker_timeout_seconds": 2222,
            "build_timeout_seconds": 1111,
            "run_test_timeout_seconds": 333,
            "full_validation_timeout_seconds": 2220,
            "entry_pass_rate_ceiling": 0.35,
            "min_removed_code_lines": 16,
        },
    )
    assert update_response.status_code == 200
    updated_run = update_response.json()["selected_run"]
    updated_runtime = updated_run["runtime_snapshot"]["stage3"]["runtime"]
    assert updated_runtime["concurrency"]["max_concurrent_runs"] == 72
    assert updated_runtime["breaker"]["model"] == "openai/stage3-breaker-edited"
    assert updated_runtime["breaker"]["base_url"] == "https://stage3-llm.example/v1"
    assert updated_runtime["breaker"]["api_key_preview"] == "secret..."
    assert "api_key" not in updated_runtime["breaker"]
    assert updated_runtime["breaker"]["preset"] == "gpt5"
    assert updated_runtime["breaker"]["max_iterations"] == 321
    assert updated_runtime["breaker"]["timeout_seconds"] == 2222
    assert updated_runtime["hyperparameters"]["build_timeout_seconds"] == 1111
    assert updated_runtime["hyperparameters"]["run_test_timeout_seconds"] == 333
    assert updated_runtime["hyperparameters"]["full_validation_timeout_seconds"] == 2220
    assert updated_runtime["hyperparameters"]["entry_pass_rate_ceiling"] == 0.35
    assert updated_runtime["hyperparameters"]["min_removed_code_lines"] == 16

    session = client.app.state.session_factory()
    try:
        stored_run = session.get(Stage3Run, run_id)
        stored_runtime = stored_run.runtime_snapshot_json["stage3"]["runtime"]
    finally:
        session.close()
    assert stored_runtime["breaker"]["api_key"] == "secret-stage3-key"
    assert stored_runtime["hyperparameters"]["entry_pass_rate_ceiling"] == 0.35
    assert stored_runtime["hyperparameters"]["min_removed_code_lines"] == 16

    completion_dir = stage3_root / "runtime" / run_id / "llm-completions" / "breaker"
    completion_dir.mkdir(parents=True)
    (completion_dir / "breaker-call-1.json").write_text(
        json.dumps({"messages": ["breaker"]}) + "\n",
        encoding="utf-8",
    )

    run_detail_response = client.get(f"/api/stage3/repos/{repository.id}/runs/{run_id}")
    assert run_detail_response.status_code == 200
    run_detail_payload = run_detail_response.json()["run"]
    assert run_detail_payload["runtime_snapshot"]["stage3"]["source_commit_sha"] == "feedface1234"
    breaker_archive = run_detail_payload["llm_completion_archives"]["breaker"]
    assert breaker_archive["file_count"] == 1
    assert breaker_archive["files"][0]["path"].endswith("llm-completions/breaker/breaker-call-1.json")

    completion_file_response = client.get(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/llm-completions/breaker/files",
        params={"path": breaker_archive["files"][0]["path"]},
    )
    assert completion_file_response.status_code == 200
    assert completion_file_response.json()["kind"] == "json"
    assert completion_file_response.json()["value"] == {"messages": ["breaker"]}

    archive_download_response = client.get(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/llm-completions/breaker.zip"
    )
    assert archive_download_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_download_response.content)) as archive:
        assert archive.namelist() == ["breaker/breaker-call-1.json"]
        assert archive.read("breaker/breaker-call-1.json") == b'{"messages": ["breaker"]}\n'

    delete_response = client.delete(f"/api/stage3/repos/{repository.id}/runs/{run_id}")
    assert delete_response.status_code == 200
    delete_payload = delete_response.json()
    assert delete_payload["deleted_run_id"] == run_id
    assert delete_payload["runs"] == []
    assert delete_payload["selected_run"] is None
    assert not workspace_dir.exists()


def test_stage3_api_can_create_and_autostart_entry_file_run(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-runs-autostart.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-runs-autostart")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="autostart1234",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="auto start this run",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    create_response = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs",
        params={"auto_start": 1},
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    run_id = payload["selected_run"]["id"]
    assert payload["selected_run"]["status"] == Stage3RunStatus.queued.value
    assert payload["selected_run"]["phase"] == "queued"
    assert payload["selected_run"]["can_start"] is False
    assert stage3_runner.scheduled_run_ids == [run_id]
    assert any(event["title"] == "Stage3 run queued" for event in payload["selected_run"]["events"])

    session = client.app.state.session_factory()
    try:
        runs = list(session.scalars(select(Stage3Run)))
    finally:
        session.close()
    assert [run.id for run in runs] == [run_id]
    assert runs[0].status == Stage3RunStatus.queued.value


def test_stage3_repair_duplicate_active_runs_handles_duplicate_pending_rows(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-active-run-invariant.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-active-run-invariant")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="active123",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="active run invariant",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    first_run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]

    session = client.app.state.session_factory()
    try:
        session.execute(text("DROP INDEX IF EXISTS uq_stage3_runs_entry_file_active"))
        session.commit()

        second_run_id = str(uuid.uuid4())
        duplicated_run = Stage3Run(
            id=second_run_id,
            entry_file_id=entry_file_id,
            status=Stage3RunStatus.pending.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind="manual",
            phase="created",
            workspace_path=str(stage3_root / "runs" / second_run_id),
            runtime_snapshot_json={"stage3": {"entry_file_id": entry_file_id}},
            summary_json={"runner_status": "created"},
        )
        session.add(duplicated_run)
        session.commit()

        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        repaired = service.repair_duplicate_active_runs()
        session.commit()
        assert repaired == 1

        rows = list(
            session.scalars(
                select(Stage3Run)
                .where(Stage3Run.entry_file_id == entry_file_id)
                .order_by(Stage3Run.created_at.asc())
            )
        )
        active_rows = [
            row
            for row in rows
            if row.status in {
                Stage3RunStatus.pending.value,
                Stage3RunStatus.queued.value,
                Stage3RunStatus.running.value,
            }
        ]
        assert len(active_rows) == 1
        assert active_rows[0].id in {first_run_id, second_run_id}
        inactive_rows = [row for row in rows if row.id != active_rows[0].id]
        assert len(inactive_rows) == 1
        assert inactive_rows[0].status == Stage3RunStatus.completed.value
        assert inactive_rows[0].result == Stage3RunResult.failed.value
    finally:
        session.close()


def test_stage3_delete_cleans_runtime_checkpoint_assets_and_tombstone(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    removed_image_refs: list[str] = []
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda references: removed_image_refs.extend(list(references or [])) or [],
    )
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-delete-cleanup.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-delete-cleanup")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="facefeed1234",
        created_at=datetime(2024, 1, 15, tzinfo=UTC),
        planner_guidance="cleanup runtime assets",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    running_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    workspace_dir = Path(str(running_run["workspace_path"]))
    repo_dir = Path(running_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
        milestone_summary="break logging behavior without touching sibling tests",
        rationale="captures the first meaningful regression boundary",
    )
    assert savepoint_feedback["accepted"] is True

    runtime_dir = stage3_root / "runtime" / run_id
    checkpoint_dir = runtime_dir / "breaker-checkpoints" / "depth-001"
    checkpoint_dir.mkdir(parents=True)
    snapshot_path = checkpoint_dir / "workspace_snapshot.tar.gz"
    snapshot_path.write_text("checkpoint", encoding="utf-8")
    image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    rejected_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-request-rejected-save-1"
    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        service.store_savepoint_checkpoint(
            run_id,
            depth=1,
            checkpoint={
                "run_id": run_id,
                "depth": 1,
                "checkpoint_dir": str(checkpoint_dir),
                "workspace_snapshot_path": str(snapshot_path),
                "docker_image_ref": image_ref,
            },
        )
        service.record_event(
            run_id,
            actor="system",
            phase="checkpoint",
            title="Breaker checkpoint saved",
            message="Saved a rejected save request checkpoint",
            payload={
                "docker_image_ref": rejected_image_ref,
            },
        )
        service.mark_run_failed(
            run_id,
            error_message="terminal before manual delete",
            result=Stage3RunResult.failed.value,
            summary_updates={"runner_status": "failed"},
        )
        session.commit()
    finally:
        session.close()

    delete_response = client.delete(f"/api/stage3/repos/{repository.id}/runs/{run_id}")
    assert delete_response.status_code == 200
    assert not workspace_dir.exists()
    assert not runtime_dir.exists()
    assert removed_image_refs == [image_ref, rejected_image_ref]

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage3CleanupTombstone, run_id) is None
    finally:
        session.close()


def test_stage3_success_cleanup_releases_breaker_checkpoints(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    removed_image_refs: list[str] = []
    monkeypatch.setattr(
        stage3_runner_module,
        "_remove_docker_images",
        lambda references: removed_image_refs.extend(list(references or [])) or [],
    )
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-success-checkpoint-cleanup.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    session_factory = client.app.state.session_factory
    repository = _seed_repository(client, full_name="owner/stage3-success-cleanup")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="successcleanup123",
        created_at=datetime(2024, 1, 18, tzinfo=UTC),
        planner_guidance="success cleanup",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    workspace_dir = Path(str(selected_run["workspace_path"]))
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
        milestone_summary="break logging behavior without touching sibling tests",
        rationale="captures the first meaningful regression boundary",
    )
    assert savepoint_feedback["accepted"] is True
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    accepted_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=savepoint_payload["selected_run"]["savepoints"][0]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref=accepted_image_ref,
    )
    rejected_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-request-rejected-save-1"
    rejected_checkpoint_dir = stage3_root / "runtime" / run_id / "breaker-checkpoints" / "request-rejected-save-1"
    rejected_checkpoint_dir.mkdir(parents=True)
    session = session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        service.record_event(
            run_id,
            actor="system",
            phase="checkpoint",
            title="Breaker checkpoint saved",
            message="Saved a rejected save request checkpoint",
            payload={
                "checkpoint_dir": str(rejected_checkpoint_dir),
                "docker_image_ref": rejected_image_ref,
            },
        )
        service.mark_run_completed(run_id, summary_updates={"runner_status": "completed"})
        session.commit()
    finally:
        session.close()

    runner = Stage3RunRunner(session_factory, settings, max_workers=1, max_limit=1)
    try:
        runner._cleanup_successful_run_breaker_checkpoints(run_id)  # noqa: SLF001
    finally:
        runner.shutdown(timeout_seconds=0)

    assert removed_image_refs == [accepted_image_ref, rejected_image_ref]
    assert not (workspace_dir / ".stage3" / "checkpoints").exists()
    assert not (stage3_root / "runtime" / run_id / "breaker-checkpoints").exists()

    session = session_factory()
    try:
        stored_run = session.get(Stage3Run, run_id)
        runtime_stage3 = dict((stored_run.runtime_snapshot_json or {}).get("stage3") or {})
        assert runtime_stage3.get("resume_checkpoint") == {}
        assert all(not dict(savepoint.checkpoint_json or {}) for savepoint in stored_run.savepoints)
    finally:
        session.close()


def test_stage3_success_cleanup_failure_creates_checkpoint_tombstone(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    monkeypatch.setattr(
        stage3_runner_module,
        "_remove_docker_images",
        lambda references: list(references or []),
    )
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-success-checkpoint-cleanup-failure.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    session_factory = client.app.state.session_factory
    repository = _seed_repository(client, full_name="owner/stage3-success-cleanup-failure")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="successcleanupfail123",
        created_at=datetime(2024, 1, 18, tzinfo=UTC),
        planner_guidance="success cleanup failure",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
        milestone_summary="break logging behavior without touching sibling tests",
        rationale="captures the first meaningful regression boundary",
    )
    assert savepoint_feedback["accepted"] is True
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    accepted_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=savepoint_payload["selected_run"]["savepoints"][0]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref=accepted_image_ref,
    )
    rejected_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-request-rejected-save-1"
    rejected_checkpoint_dir = stage3_root / "runtime" / run_id / "breaker-checkpoints" / "request-rejected-save-1"
    rejected_checkpoint_dir.mkdir(parents=True)
    session = session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        service.record_event(
            run_id,
            actor="system",
            phase="checkpoint",
            title="Breaker checkpoint saved",
            message="Saved a rejected save request checkpoint",
            payload={
                "checkpoint_dir": str(rejected_checkpoint_dir),
                "docker_image_ref": rejected_image_ref,
            },
        )
        service.mark_run_completed(run_id, summary_updates={"runner_status": "completed"})
        session.commit()
    finally:
        session.close()

    runner = Stage3RunRunner(session_factory, settings, max_workers=1, max_limit=1)
    try:
        runner._cleanup_successful_run_breaker_checkpoints(run_id)  # noqa: SLF001
    finally:
        runner.shutdown(timeout_seconds=0)

    session = session_factory()
    try:
        tombstone = session.get(Stage3CleanupTombstone, run_id)
        assert tombstone is not None
        assert tombstone.reason == "successful_breaker_checkpoint_cleanup"
        assert tombstone.archive_json["cleanup_scope"] == "breaker_checkpoint_assets"
        assert sorted(tombstone.archive_json["checkpoint_docker_image_refs"]) == sorted(
            [accepted_image_ref, rejected_image_ref]
        )
        stored_run = session.get(Stage3Run, run_id)
        runtime_stage3 = dict((stored_run.runtime_snapshot_json or {}).get("stage3") or {})
        assert runtime_stage3.get("resume_checkpoint")
        assert any(dict(savepoint.checkpoint_json or {}) for savepoint in stored_run.savepoints)
    finally:
        session.close()


def test_stage3_success_cleanup_bookkeeping_failure_does_not_fail_archived_run(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-success-cleanup-bookkeeping-failure.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    session_factory = client.app.state.session_factory
    repository = _seed_repository(client, full_name="owner/stage3-success-cleanup-bookkeeping-failure")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="successcleanupbookkeepingfail",
        created_at=datetime(2024, 1, 19, tzinfo=UTC),
        planner_guidance="success cleanup bookkeeping failure",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True

    class CompletedBackend:
        backend_name = "fake"
        selection_detail = "fake stage3 backend"

        def cancel_run(self, run_id: str) -> bool:
            del run_id
            return True

        def run_breaker(self, *args, **kwargs):  # noqa: ANN002, ANN003
            del args, kwargs
            return BreakerExecutionResult(
                summary="fake breaker completed",
                model="fake/model",
                token_usage=7,
                token_usage_details={"total": 7},
                llm_completion_archive={},
                execution_status="finished",
                savepoints=[],
            )

    def broken_clear_breaker_checkpoints(self, run_id: str):  # noqa: ANN001
        del self, run_id
        raise RuntimeError("checkpoint metadata write failed")

    monkeypatch.setattr(Stage3Service, "clear_breaker_checkpoints", broken_clear_breaker_checkpoints)

    session = session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        run = service.get_repository_run(repository.id, run_id, include_detail=True)
    finally:
        session.close()

    runner = Stage3RunRunner(session_factory, settings, max_workers=1, max_limit=1)
    runner.backend = CompletedBackend()
    try:
        runner._execute_run(repository, run)  # noqa: SLF001
    finally:
        runner.shutdown(timeout_seconds=0)

    refreshed_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    refreshed_run = refreshed_payload["selected_run"]
    assert refreshed_run["status"] == Stage3RunStatus.completed.value
    assert refreshed_run["result"] == Stage3RunResult.archived.value
    assert refreshed_run["phase"] == "completed"
    assert refreshed_run["error_message"] is None
    assert refreshed_run["summary"]["runner_status"] == "completed"
    assert any(event["title"] == "Breaker checkpoint cleanup failed" for event in refreshed_run["events"])


def test_stage3_active_run_unique_index_blocks_duplicate_pending_rows(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-active-run-unique-index.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-active-run-unique-index")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="unique123",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="active run unique index",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    first_run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]

    session = client.app.state.session_factory()
    try:
        duplicated_run = Stage3Run(
            id=str(uuid.uuid4()),
            entry_file_id=entry_file_id,
            status=Stage3RunStatus.pending.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind="manual",
            phase="created",
            workspace_path=str(stage3_root / "runs" / "duplicate"),
            runtime_snapshot_json={"stage3": {"entry_file_id": entry_file_id}},
            summary_json={"runner_status": "created"},
        )
        session.add(duplicated_run)
        with pytest.raises(Exception):
            session.commit()
        session.rollback()

        rows = list(session.scalars(select(Stage3Run).where(Stage3Run.entry_file_id == entry_file_id)))
        assert [row.id for row in rows] == [first_run_id]
    finally:
        session.close()


def test_stage3_resume_cleans_cloned_checkpoint_assets_on_post_clone_failure(
    tmp_path,
    monkeypatch,
) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-resume-cleanup.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-resume-cleanup")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="cleanup123",
        created_at=datetime(2024, 1, 14, tzinfo=UTC),
        planner_guidance="resume cleanup",
        test_files=[("tests/test_target_feature.py", 9)],
    )
    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    source_run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]

    checkpoint_root = stage3_root / "runtime" / source_run_id / "breaker-checkpoints" / "depth-001"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    source_snapshot_path = checkpoint_root / "workspace_snapshot.tar.gz"
    source_snapshot_path.write_bytes(b"fake checkpoint")
    conversation_id = uuid.uuid4()
    conversations_path = checkpoint_root / "openhands" / "conversations"
    (conversations_path / conversation_id.hex).mkdir(parents=True, exist_ok=True)
    bash_events_dir = checkpoint_root / "openhands" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    source_image_ref = f"feature-factory/stage3-breaker-checkpoint:{source_run_id}-depth-001"
    checkpoint = {
        "schema_version": 1,
        "checkpoint_type": "stage3_breaker_cold_restore",
        "run_id": source_run_id,
        "depth": 1,
        "checkpoint_dir": str(checkpoint_root),
        "workspace_snapshot_path": str(source_snapshot_path),
        "conversation_id": str(conversation_id),
        "openhands": {
            "conversations_path": str(conversations_path),
            "bash_events_dir": str(bash_events_dir),
        },
        "docker_image_ref": source_image_ref,
        "docker_commit_status": "saved",
        "docker_commit": {"docker_image_ref": source_image_ref},
    }

    session = client.app.state.session_factory()
    try:
        source_run = session.get(Stage3Run, source_run_id)
        assert source_run is not None
        source_run.status = Stage3RunStatus.completed.value
        source_run.result = Stage3RunResult.interrupted.value
        source_run.phase = "interrupted"
        source_run.savepoints.append(
            Stage3Savepoint(
                depth=1,
                entry_pass_rate=0.5,
                p2p_files_json=[],
                f2p_files_json=["tests/test_target_feature.py"],
                collateral_json={},
                gold_patch_text="",
                checkpoint_json=checkpoint,
                summary_json={},
            )
        )
        session.commit()
    finally:
        session.close()

    retagged_refs: list[str] = []
    removed_refs: list[str] = []

    def fake_retag_checkpoint_image(*, source_image_ref, destination_run_id, depth):  # noqa: ANN001
        destination = (
            f"feature-factory/stage3-breaker-checkpoint:{destination_run_id}-depth-{int(depth):03d}"
        )
        retagged_refs.append(destination)
        return destination

    def fake_copy_archives(**_kwargs):  # noqa: ANN001
        raise RuntimeError("copy failed after checkpoint clone")

    def fake_remove_docker_images(image_refs):  # noqa: ANN001
        removed_refs.extend(list(image_refs))
        return []

    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    monkeypatch.setattr(stage3_service_module, "_retag_checkpoint_image", fake_retag_checkpoint_image)
    monkeypatch.setattr(
        stage3_service_module,
        "_copy_stage3_llm_completion_archives_for_resume_run",
        fake_copy_archives,
    )
    monkeypatch.setattr(stage3_service_module, "_remove_docker_images", fake_remove_docker_images)

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        with pytest.raises(RuntimeError, match="copy failed after checkpoint clone"):
            service.resume_run(repository.id, source_run_id)
        session.rollback()
    finally:
        session.close()

    assert retagged_refs
    cloned_image_ref = retagged_refs[-1]
    cloned_run_id = cloned_image_ref.split(":", 1)[1].split("-depth-", 1)[0]
    assert cloned_image_ref in removed_refs
    assert not (stage3_root / "runtime" / cloned_run_id).exists()
    assert not (stage3_root / "runs" / cloned_run_id).exists()

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage3Run, cloned_run_id) is None
    finally:
        session.close()


def test_stage3_restore_checkpoint_normalizes_workspace_permissions(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-restore-permissions.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    session = build_session_factory(engine)()
    source_workspace = tmp_path / "checkpoint-source"
    source_workspace.mkdir()
    private_dir = source_workspace / ".stage3" / "private"
    private_dir.mkdir(parents=True)
    private_file = private_dir / "agent-state.json"
    private_file.write_text("{}", encoding="utf-8")
    private_dir.chmod(0o700)
    private_file.chmod(0o600)
    bash_history = source_workspace / ".bash_history"
    bash_history.write_text("python -m pytest\n", encoding="utf-8")
    bash_history.chmod(0o600)
    snapshot_path = tmp_path / "workspace_snapshot.tar.gz"
    with tarfile.open(snapshot_path, "w:gz") as archive:
        for path in sorted(source_workspace.rglob("*")):
            archive.add(path, arcname=str(path.relative_to(source_workspace)))

    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        run = Stage3Run(
            id=str(uuid.uuid4()),
            entry_file_id=str(uuid.uuid4()),
            runtime_snapshot_json={"stage3": {"source_commit_sha": "restorepermissions123"}},
        )
        service._restore_run_from_checkpoint(  # noqa: SLF001
            run,
            checkpoint={"workspace_snapshot_path": str(snapshot_path)},
        )
        restored_private_dir = stage3_root / "runs" / run.id / ".stage3" / "private"
        restored_private_file = restored_private_dir / "agent-state.json"
        restored_bash_history = stage3_root / "runs" / run.id / ".bash_history"
        assert stat.S_IMODE(restored_private_dir.stat().st_mode) & stat.S_IRWXG == stat.S_IRWXG
        assert stat.S_IMODE(restored_private_dir.stat().st_mode) & stat.S_IRWXO == stat.S_IRWXO
        for restored_file in (restored_private_file, restored_bash_history):
            mode = stat.S_IMODE(restored_file.stat().st_mode)
            assert mode & stat.S_IRGRP
            assert mode & stat.S_IWGRP
            assert mode & stat.S_IROTH
            assert mode & stat.S_IWOTH
    finally:
        session.close()


def test_stage3_resume_records_tombstone_when_post_clone_cleanup_fails(
    tmp_path,
    monkeypatch,
) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-resume-cleanup-tombstone.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-resume-cleanup-tombstone")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="cleanup-tombstone",
        created_at=datetime(2024, 1, 15, tzinfo=UTC),
        planner_guidance="resume cleanup tombstone",
        test_files=[("tests/test_target_feature.py", 9)],
    )
    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    source_run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]

    checkpoint_root = stage3_root / "runtime" / source_run_id / "breaker-checkpoints" / "depth-001"
    checkpoint_root.mkdir(parents=True, exist_ok=True)
    source_snapshot_path = checkpoint_root / "workspace_snapshot.tar.gz"
    source_snapshot_path.write_bytes(b"fake checkpoint")
    conversation_id = uuid.uuid4()
    conversations_path = checkpoint_root / "openhands" / "conversations"
    (conversations_path / conversation_id.hex).mkdir(parents=True, exist_ok=True)
    bash_events_dir = checkpoint_root / "openhands" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    source_image_ref = f"feature-factory/stage3-breaker-checkpoint:{source_run_id}-depth-001"
    checkpoint = {
        "schema_version": 1,
        "checkpoint_type": "stage3_breaker_cold_restore",
        "run_id": source_run_id,
        "depth": 1,
        "checkpoint_dir": str(checkpoint_root),
        "workspace_snapshot_path": str(source_snapshot_path),
        "conversation_id": str(conversation_id),
        "openhands": {
            "conversations_path": str(conversations_path),
            "bash_events_dir": str(bash_events_dir),
        },
        "docker_image_ref": source_image_ref,
        "docker_commit_status": "saved",
        "docker_commit": {"docker_image_ref": source_image_ref},
    }

    session = client.app.state.session_factory()
    try:
        source_run = session.get(Stage3Run, source_run_id)
        assert source_run is not None
        source_run.status = Stage3RunStatus.completed.value
        source_run.result = Stage3RunResult.interrupted.value
        source_run.phase = "interrupted"
        source_run.savepoints.append(
            Stage3Savepoint(
                depth=1,
                entry_pass_rate=0.5,
                p2p_files_json=[],
                f2p_files_json=["tests/test_target_feature.py"],
                collateral_json={},
                gold_patch_text="",
                checkpoint_json=checkpoint,
                summary_json={},
            )
        )
        session.commit()
    finally:
        session.close()

    retagged_refs: list[str] = []

    def fake_retag_checkpoint_image(*, source_image_ref, destination_run_id, depth):  # noqa: ANN001
        destination = (
            f"feature-factory/stage3-breaker-checkpoint:{destination_run_id}-depth-{int(depth):03d}"
        )
        retagged_refs.append(destination)
        return destination

    def fake_copy_archives(**_kwargs):  # noqa: ANN001
        raise RuntimeError("copy failed after checkpoint clone")

    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    monkeypatch.setattr(stage3_service_module, "_retag_checkpoint_image", fake_retag_checkpoint_image)
    monkeypatch.setattr(
        stage3_service_module,
        "_copy_stage3_llm_completion_archives_for_resume_run",
        fake_copy_archives,
    )
    monkeypatch.setattr(
        stage3_service_module,
        "_remove_docker_images",
        lambda image_refs: list(image_refs or []),
    )

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        with pytest.raises(RuntimeError, match="copy failed after checkpoint clone"):
            service.resume_run(repository.id, source_run_id)
    finally:
        session.close()

    assert retagged_refs
    cloned_image_ref = retagged_refs[-1]
    cloned_run_id = cloned_image_ref.split(":", 1)[1].split("-depth-", 1)[0]
    assert not (stage3_root / "runtime" / cloned_run_id).exists()
    assert not (stage3_root / "runs" / cloned_run_id).exists()

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage3Run, cloned_run_id) is None
        tombstone = session.get(Stage3CleanupTombstone, cloned_run_id)
        assert tombstone is not None
        assert tombstone.reason == "resume_prepare_failure"
        assert tombstone.status == "failed"
        assert tombstone.archive_json["run_id"] == cloned_run_id
        assert tombstone.archive_json["checkpoint_docker_image_refs"] == [cloned_image_ref]
        assert tombstone.failure_json["failed_checkpoint_docker_image_refs"] == [cloned_image_ref]
    finally:
        session.close()


def test_stage3_recover_interrupted_runs_marks_active_runs_failed(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-recover.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-recover")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="badcafe1234",
        created_at=datetime(2024, 1, 16, tzinfo=UTC),
        planner_guidance="recover active runs",
        test_files=[("tests/test_target_feature.py", 4)],
    )
    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        archives = service.recover_interrupted_runs(return_archives=True)
        session.commit()
    finally:
        session.close()
    assert len(archives) == 1
    assert archives[0]["cleanup_scope"] == "recovered_run_assets"

    payload = client.get(f"/api/stage3/repos/{repository.id}/runs/{run_id}").json()["run"]
    assert payload["status"] == Stage3RunStatus.completed.value
    assert payload["result"] == Stage3RunResult.failed.value
    assert payload["error_message"] == "stage3 run interrupted before execution"


def test_stage3_start_requires_breaker_agent_backend(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-requires-agent.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner(ready=False)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-requires-agent")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="agent1234",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="requires agent",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 503
    assert "Stage3 breaker agent is not ready" in start_response.json()["detail"]
    assert stage3_runner.scheduled_run_ids == []

    run_payload = client.get(f"/api/stage3/repos/{repository.id}/runs/{run_id}").json()["run"]
    assert run_payload["status"] == Stage3RunStatus.pending.value


def test_stage3_start_persists_baseline_prepare_phase_before_checkout(tmp_path, monkeypatch) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-baseline-prepare-phase.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-baseline-phase")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="baselinephase1234",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="baseline phase persistence",
        test_files=[("tests/test_target_feature.py", 7)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200

    session_factory = client.app.state.session_factory

    def fake_materialize_repository_checkout(
        self,  # noqa: ANN001
        run,  # noqa: ANN001
        *,
        repository,  # noqa: ANN001
        target_commit_sha,  # noqa: ANN001
        emit_event=None,  # noqa: ANN001
        cancel_requested=None,  # noqa: ANN001
    ) -> dict[str, object]:
        del self, repository, emit_event, cancel_requested
        verify_session = session_factory()
        try:
            verify_service = Stage3Service(
                verify_session,
                workspace_root=client.app.state.settings.stage3_workspace_dir,
                settings=client.app.state.settings,
            )
            persisted_run = verify_service.get_run(run.id, include_detail=False)
            assert persisted_run.phase == "baseline_prepare"
            assert persisted_run.status == Stage3RunStatus.running.value
            assert dict(persisted_run.summary_json or {}).get("runner_status") == "running"
        finally:
            verify_session.close()
        return {"target_commit_sha": target_commit_sha}

    monkeypatch.setattr(
        Stage3Service,
        "_materialize_repository_checkout",
        fake_materialize_repository_checkout,
    )

    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert selected_run["phase"] == "breaker_running"
    assert any(event["phase"] == "baseline_prepare" for event in selected_run["events"])


def test_stage3_start_persists_resume_phase_before_checkpoint_restore(tmp_path, monkeypatch) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-resume-phase.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-resume-phase")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="resumephase1234",
        created_at=datetime(2024, 1, 14, tzinfo=UTC),
        planner_guidance="resume phase persistence",
        test_files=[("tests/test_target_feature.py", 6)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200

    session = client.app.state.session_factory()
    try:
        run = session.get(Stage3Run, run_id)
        assert run is not None
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["resume_checkpoint"] = {
            "workspace_snapshot_path": str(stage3_root / "runtime" / run_id / "resume-current.tar.gz")
        }
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        session.commit()
    finally:
        session.close()

    session_factory = client.app.state.session_factory

    def fake_restore_run_from_checkpoint(self, run, *, checkpoint):  # noqa: ANN001
        del self
        verify_session = session_factory()
        try:
            verify_service = Stage3Service(
                verify_session,
                workspace_root=client.app.state.settings.stage3_workspace_dir,
                settings=client.app.state.settings,
            )
            persisted_run = verify_service.get_run(run.id, include_detail=False)
            assert persisted_run.phase == "resume"
            assert persisted_run.status == Stage3RunStatus.running.value
            assert dict(persisted_run.summary_json or {}).get("runner_status") == "running"
        finally:
            verify_session.close()
        return {"workspace_snapshot_path": checkpoint["workspace_snapshot_path"]}

    monkeypatch.setattr(
        Stage3Service,
        "_restore_run_from_checkpoint",
        fake_restore_run_from_checkpoint,
    )

    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert selected_run["phase"] == "breaker_running"
    assert any(event["phase"] == "resume" for event in selected_run["events"])


def test_stage3_autostart_schedule_failure_does_not_leave_pending_run(tmp_path) -> None:
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-autostart-schedule-failed.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = RejectStage3ScheduleRunner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-autostart-schedule-failed")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="schedfail1234",
        created_at=datetime(2024, 1, 12, tzinfo=UTC),
        planner_guidance="autostart schedule failure cleanup",
        test_files=[("tests/test_target_feature.py", 9)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    create_response = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs",
        params={"auto_start": 1},
    )
    assert create_response.status_code == 503
    assert "failed to schedule the Stage3 breaker run" in create_response.json()["detail"]

    rejected_run_id = stage3_runner.scheduled_run_ids[-1]
    rejected_payload = client.get(f"/api/stage3/repos/{repository.id}/runs/{rejected_run_id}").json()["run"]
    assert rejected_payload["display_status"] == "schedule_failed"
    assert rejected_payload["status"] == Stage3RunStatus.completed.value
    assert rejected_payload["result"] == Stage3RunResult.failed.value
    assert rejected_payload["summary"]["schedule_failed"] is True
    assert not (stage3_root / "runs" / rejected_run_id).exists()
    assert not (stage3_root / "runtime" / rejected_run_id).exists()


def test_stage3_resume_schedule_failure_cleans_cloned_assets_and_reports_status(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fake_stage3_runtime(monkeypatch)

    class RejectSecondScheduleRunner(DummyStage3Runner):
        def schedule_run(self, run_id: str) -> bool:
            self.scheduled_run_ids.append(run_id)
            return len(self.scheduled_run_ids) == 1

    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-resume-schedule-failed.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = RejectSecondScheduleRunner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-resume-schedule-failed")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="resumeschedule123",
        created_at=datetime(2024, 1, 16, tzinfo=UTC),
        planner_guidance="resume schedule cleanup",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    source_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=savepoint_payload["selected_run"]["savepoints"][0]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref=source_image_ref,
    )
    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    monkeypatch.setattr(
        stage3_service_module,
        "_retag_checkpoint_image",
        lambda *, source_image_ref, destination_run_id, depth: (
            f"feature-factory/stage3-breaker-checkpoint:{destination_run_id}-depth-{int(depth):03d}"
        ),
    )

    interrupt_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/interrupt")
    assert interrupt_response.status_code == 200
    resume_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/resume")
    assert resume_response.status_code == 503
    assert "failed to schedule the resumed Stage3 breaker run" in resume_response.json()["detail"]

    rejected_run_id = stage3_runner.scheduled_run_ids[-1]
    assert rejected_run_id != run_id
    rejected_payload = client.get(f"/api/stage3/repos/{repository.id}/runs/{rejected_run_id}").json()["run"]
    assert rejected_payload["display_status"] == "schedule_failed"
    assert rejected_payload["status"] == Stage3RunStatus.completed.value
    assert rejected_payload["result"] == Stage3RunResult.failed.value
    assert rejected_payload["summary"]["schedule_failed"] is True
    assert not (stage3_root / "runs" / rejected_run_id).exists()
    assert not (stage3_root / "runtime" / rejected_run_id).exists()
    assert any(event["title"] == "Stage3 run assets cleaned" for event in rejected_payload["events"])


def test_stage3_run_can_start_savepoint_and_nested_resume(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-lifecycle.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-lifecycle")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="cafe1234beef",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="break only the target feature",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )

    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]

    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    start_payload = start_response.json()
    selected_run = start_payload["selected_run"]
    assert selected_run["status"] == Stage3RunStatus.queued.value
    assert selected_run["phase"] == "queued"
    assert selected_run["can_rerun"] is False
    assert stage3_runner.scheduled_run_ids == [run_id]
    active_runtime_update = client.patch(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/runtime",
        json={"breaker_model": "should-not-apply"},
    )
    assert active_runtime_update.status_code == 409

    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert selected_run["status"] == Stage3RunStatus.running.value
    assert selected_run["phase"] == "breaker_running"
    assert selected_run["can_rerun"] is False
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    assert (repo_dir / "app.py").read_text(encoding="utf-8") == _STAGE3_BASELINE_APP_TEXT

    manual_savepoint_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/savepoints")
    assert manual_savepoint_response.status_code == 409

    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
        milestone_summary="break logging behavior without touching sibling tests",
        rationale="captures the first meaningful regression boundary",
    )
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert savepoint_feedback["accepted"] is True
    assert savepoint_feedback["depth"] == 1
    assert len(savepoint_payload["selected_run"]["savepoints"]) == 1
    assert savepoint_payload["selected_run"]["savepoints"][0]["depth"] == 1
    assert savepoint_payload["selected_run"]["savepoints"][0]["checkpoint_status"] == "pending"
    assert savepoint_payload["selected_run"]["savepoints"][0]["reusable_checkpoint_ready"] is False
    assert savepoint_payload["selected_run"]["savepoints"][0]["feedback"]["code"] == "SAVEPOINT_ACCEPTED"
    assert (
        savepoint_payload["selected_run"]["savepoints"][0]["summary"]["milestone_summary"]
        == "break logging behavior without touching sibling tests"
    )
    assert (
        savepoint_payload["selected_run"]["savepoints"][0]["summary"]["rationale"]
        == "captures the first meaningful regression boundary"
    )
    assert savepoint_payload["selected_run"]["savepoints"][0]["summary"]["diff_stats"] == {
        "total_changed_lines": 13,
        "added_lines": 3,
        "removed_lines": 10,
    }
    assert savepoint_payload["selected_run"]["draft_savepoint"] is None
    session = client.app.state.session_factory()
    try:
        persisted_savepoint = session.scalar(
            select(Stage3Savepoint)
            .where(Stage3Savepoint.run_id == run_id, Stage3Savepoint.depth == 1)
        )
        assert persisted_savepoint is not None
        assert dict(persisted_savepoint.summary_json or {}).get("diff_stats") == {
            "total_changed_lines": 13,
            "added_lines": 3,
            "removed_lines": 10,
        }
    finally:
        session.close()

    rejected_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    rejected_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert rejected_feedback["accepted"] is False
    assert len(rejected_payload["selected_run"]["savepoints"]) == 1
    assert rejected_payload["selected_run"]["draft_savepoint"]["label"] == "深度 2 + 临时版本"
    assert rejected_payload["selected_run"]["draft_savepoint"]["feedback"]["code"] == "ENTRY_NOT_DEEPER"
    assert rejected_payload["selected_run"]["draft_savepoint"]["gold_patch_text"] == ""
    assert len(rejected_payload["selected_run"]["draft_savepoint"]["file_results"]) == 2

    _write_stage3_app_breakage(repo_dir, level=2)
    second_savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    second_savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert second_savepoint_feedback["accepted"] is True
    assert second_savepoint_feedback["depth"] == 2
    assert len(second_savepoint_payload["selected_run"]["savepoints"]) == 2
    assert second_savepoint_payload["selected_run"]["savepoints"][1]["feedback"]["code"] == "SAVEPOINT_ACCEPTED"
    assert second_savepoint_payload["selected_run"]["savepoints"][1]["summary"]["diff_stats"] == {
        "total_changed_lines": 15,
        "added_lines": 4,
        "removed_lines": 11,
    }
    assert second_savepoint_payload["selected_run"]["draft_savepoint"] is None
    source_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-002"
    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=2,
        source_snapshot_path=second_savepoint_payload["selected_run"]["savepoints"][1]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref=source_image_ref,
    )
    checkpoint_ready_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert checkpoint_ready_payload["selected_run"]["savepoints"][1]["checkpoint_status"] == "ready"
    assert checkpoint_ready_payload["selected_run"]["savepoints"][1]["reusable_checkpoint_ready"] is True
    retag_calls: list[tuple[str, str]] = []

    def fake_retag_checkpoint_image(*, source_image_ref, destination_run_id, depth):  # noqa: ANN001
        destination = (
            f"feature-factory/stage3-breaker-checkpoint:{destination_run_id}-depth-{int(depth):03d}"
        )
        retag_calls.append((source_image_ref, destination))
        return destination

    monkeypatch.setattr(stage3_service_module, "_retag_checkpoint_image", fake_retag_checkpoint_image)

    interrupt_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/interrupt")
    assert interrupt_response.status_code == 200
    interrupted_run = interrupt_response.json()["selected_run"]
    assert interrupted_run["result"] == "interrupted"
    assert interrupted_run["can_resume"] is True
    session = client.app.state.session_factory()
    try:
        source_run = session.get(Stage3Run, run_id)
        assert source_run is not None
        source_run.started_at = datetime(2024, 1, 20, 8, 0, tzinfo=UTC)
        source_run.finished_at = datetime(2024, 1, 20, 8, 10, tzinfo=UTC)
        source_run.summary_json = {
            **dict(source_run.summary_json or {}),
            "breaker_model": "openai/stage3-breaker",
            "breaker_token_usage": 1234,
        }
        runtime_stage3 = dict((source_run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = {
            "concurrency": {"max_concurrent_runs": 77},
            "breaker": {
                "model": "resume-source-breaker-model",
                "base_url": "https://resume-source-breaker.example/v1",
                "api_key": "resume-source-breaker-key",
                "api_key_preview": "resume...",
                "preset": "gpt5",
                "max_iterations": 333,
                "timeout_seconds": 4444.0,
            },
            "hyperparameters": {
                "build_timeout_seconds": 5555.0,
                "run_test_timeout_seconds": 777.0,
                "full_validation_timeout_seconds": 666.0,
            },
        }
        source_run.runtime_snapshot_json = {
            **dict(source_run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        session.commit()
    finally:
        session.close()

    resume_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/resume")
    assert resume_response.status_code == 200
    resume_payload = resume_response.json()
    resumed_run = resume_payload["selected_run"]
    assert resumed_run["trigger_kind"] == "resume"
    assert resumed_run["status"] == Stage3RunStatus.queued.value
    assert stage3_runner.scheduled_run_ids[-1] == resumed_run["id"]
    assert resumed_run["duration_seconds"] == 600
    assert resumed_run["token_usage_by_model"] == {"openai/stage3-breaker": 1234}
    resumed_runtime = resumed_run["runtime_snapshot"]["stage3"]["runtime"]
    assert resumed_runtime["concurrency"]["max_concurrent_runs"] == 77
    assert resumed_runtime["breaker"]["model"] == "resume-source-breaker-model"
    assert resumed_runtime["breaker"]["base_url"] == "https://resume-source-breaker.example/v1"
    assert resumed_runtime["breaker"]["api_key_preview"] == "resume..."
    assert "api_key" not in resumed_runtime["breaker"]
    assert resumed_runtime["breaker"]["preset"] == "gpt5"
    assert resumed_runtime["breaker"]["max_iterations"] == 333
    assert resumed_runtime["breaker"]["timeout_seconds"] == 4444.0
    assert resumed_runtime["hyperparameters"]["build_timeout_seconds"] == 5555.0
    assert resumed_runtime["hyperparameters"]["run_test_timeout_seconds"] == 777.0
    assert resumed_runtime["hyperparameters"]["full_validation_timeout_seconds"] == 666.0
    expected_resumed_image_ref = (
        f"feature-factory/stage3-breaker-checkpoint:{resumed_run['id']}-depth-002"
    )
    assert retag_calls[-1] == (source_image_ref, expected_resumed_image_ref)
    assert (
        resumed_run["runtime_snapshot"]["stage3"]["resume_checkpoint"]["docker_image_ref"]
        == expected_resumed_image_ref
    )
    assert len(resumed_run["savepoints"]) == 2
    assert resumed_run["savepoints"][0]["checkpoint"] == {}
    assert resumed_run["savepoints"][1]["checkpoint"] == {}
    assert resumed_run["savepoints"][0]["checkpoint_status"] == "service_snapshot_only"
    assert resumed_run["savepoints"][1]["checkpoint_status"] == "service_snapshot_only"
    assert resumed_run["savepoints"][0]["reusable_checkpoint_ready"] is False
    assert resumed_run["savepoints"][1]["reusable_checkpoint_ready"] is False

    session = client.app.state.session_factory()
    try:
        resumed_row = session.get(Stage3Run, resumed_run["id"])
        assert resumed_row is not None
        runtime_stage3 = dict((resumed_row.runtime_snapshot_json or {}).get("stage3") or {})
        materialized = dict(runtime_stage3.get("resume_materialized") or {})
        removed_duration = materialized.pop("duration_seconds", None)
        removed_tokens = materialized.pop("token_usage_by_model", None)
        assert removed_duration == 600
        assert removed_tokens == {"openai/stage3-breaker": 1234}
        runtime_stage3["resume_materialized"] = materialized
        resumed_row.runtime_snapshot_json = {
            **dict(resumed_row.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        session.commit()
    finally:
        session.close()

    fallback_resume_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": resumed_run["id"]},
    ).json()
    assert fallback_resume_payload["selected_run"]["duration_seconds"] == 600
    assert fallback_resume_payload["selected_run"]["token_usage_by_model"] == {
        "openai/stage3-breaker": 1234
    }

    session = client.app.state.session_factory()
    try:
        resumed_row = session.get(Stage3Run, resumed_run["id"])
        assert resumed_row is not None
        runtime_stage3 = dict((resumed_row.runtime_snapshot_json or {}).get("stage3") or {})
        materialized = dict(runtime_stage3.get("resume_materialized") or {})
        materialized["duration_seconds"] = 600
        materialized["token_usage_by_model"] = {"openai/stage3-breaker": 1234}
        runtime_stage3["resume_materialized"] = materialized
        resumed_row.runtime_snapshot_json = {
            **dict(resumed_row.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        session.commit()
    finally:
        session.close()

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        cleanup = service.breaker_checkpoint_cleanup_targets(resumed_run["id"])
    finally:
        session.close()
    assert all(f"/runtime/{run_id}/" not in path for path in cleanup["checkpoint_paths"])
    assert source_image_ref not in cleanup["checkpoint_docker_image_refs"]
    assert expected_resumed_image_ref in cleanup["checkpoint_docker_image_refs"]

    resumed_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=resumed_run["id"],
    )
    assert resumed_run["status"] == Stage3RunStatus.running.value
    assert len(resumed_run["savepoints"]) == 2
    assert resumed_run["summary"]["latest_depth"] == 2
    resumed_repo_dir = Path(resumed_run["workspace_manifest"]["repo_dir"])
    assert (resumed_repo_dir / "app.py").read_text(encoding="utf-8") == _STAGE3_BREAKAGE_TWO_APP_TEXT

    resume_run_id = resumed_run["id"]
    delete_source_response = client.delete(f"/api/stage3/repos/{repository.id}/runs/{run_id}")
    assert delete_source_response.status_code == 200
    post_delete_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": resume_run_id},
    ).json()
    assert len(post_delete_payload["selected_run"]["savepoints"]) == 2
    assert post_delete_payload["selected_entry_file"]["savepoint_count"] == 2
    assert post_delete_payload["repository"]["stage3"]["produced_data_count"] == 2

    nested_interrupt_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{resume_run_id}/interrupt")
    assert nested_interrupt_response.status_code == 200
    nested_resume_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{resume_run_id}/resume")
    assert nested_resume_response.status_code == 200
    nested_resumed_run = nested_resume_response.json()["selected_run"]
    assert nested_resumed_run["trigger_kind"] == "resume"
    assert stage3_runner.scheduled_run_ids[-1] == nested_resumed_run["id"]
    nested_runtime = nested_resumed_run["runtime_snapshot"]["stage3"]["runtime"]
    assert nested_runtime["concurrency"]["max_concurrent_runs"] == 77
    assert nested_runtime["breaker"]["model"] == "resume-source-breaker-model"
    assert nested_runtime["breaker"]["base_url"] == "https://resume-source-breaker.example/v1"
    assert nested_runtime["breaker"]["api_key_preview"] == "resume..."
    assert nested_runtime["breaker"]["preset"] == "gpt5"
    assert nested_runtime["breaker"]["max_iterations"] == 333
    assert nested_runtime["breaker"]["timeout_seconds"] == 4444.0
    assert nested_runtime["hyperparameters"]["build_timeout_seconds"] == 5555.0
    assert nested_runtime["hyperparameters"]["run_test_timeout_seconds"] == 777.0
    assert nested_runtime["hyperparameters"]["full_validation_timeout_seconds"] == 666.0
    nested_resumed_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=nested_resumed_run["id"],
    )
    assert len(nested_resumed_run["savepoints"]) == 2
    assert nested_resumed_run["summary"]["latest_depth"] == 2


def test_stage3_savepoint_rejects_commented_out_gold_patch(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-commented-out-gold-patch.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-commented-out")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="cafe1234beef",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="break only the target feature",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )
    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]
    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    (repo_dir / "app.py").write_text(
        "# weights = []\n" + _STAGE3_BREAKAGE_ONE_APP_TEXT,
        encoding="utf-8",
    )

    feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )

    payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert feedback["accepted"] is False
    assert feedback["code"] == "COMMENTED_OUT_CODE"
    assert feedback["commented_out_code_finding_count"] == 1
    assert payload["selected_run"]["savepoints"] == []
    assert payload["selected_run"]["draft_savepoint"]["feedback"]["code"] == "COMMENTED_OUT_CODE"
    assert "+# weights = []" in payload["selected_run"]["draft_savepoint"]["gold_patch_text"]


def test_stage3_savepoint_rejects_patch_quality_before_p2p_evaluation(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-preflight-quality.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-preflight-quality")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="preflight1234",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="break only the target feature",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )
    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]
    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    (repo_dir / "app.py").write_text("def target_feature(items):\n    pass\n", encoding="utf-8")

    class UnexpectedEvaluator:
        def evaluate_original_p2p(self, **kwargs):  # noqa: ANN001
            del kwargs
            raise AssertionError("patch quality preflight should reject before P2P evaluation")

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: UnexpectedEvaluator())

    feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )

    payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert feedback["accepted"] is False
    assert feedback["code"] == "TRIVIAL_STUB_BREAKAGE"
    assert "stub-style breakage" in feedback["message"]
    assert payload["selected_run"]["savepoints"] == []
    assert payload["selected_run"]["draft_savepoint"]["feedback"]["code"] == "TRIVIAL_STUB_BREAKAGE"
    assert "+    pass" in payload["selected_run"]["draft_savepoint"]["gold_patch_text"]


def test_stage3_savepoint_uses_runtime_min_removed_code_lines(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-runtime-min-removed.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-runtime-min-removed")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="runtime1234",
        created_at=datetime(2024, 1, 13, tzinfo=UTC),
        planner_guidance="break only the target feature",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )
    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]
    update_response = client.patch(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/runtime",
        json={"min_removed_code_lines": 11},
    )
    assert update_response.status_code == 200
    updated_runtime = update_response.json()["selected_run"]["runtime_snapshot"]["stage3"]["runtime"]
    updated_hyperparameters = updated_runtime["hyperparameters"]
    assert updated_hyperparameters["min_removed_code_lines"] == 11

    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)

    class UnexpectedEvaluator:
        def evaluate_original_p2p(self, **kwargs):  # noqa: ANN001
            del kwargs
            raise AssertionError("runtime min_removed_code_lines should reject before P2P evaluation")

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: UnexpectedEvaluator())

    feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )

    assert feedback["accepted"] is False
    assert feedback["code"] == "INSUFFICIENT_SUBSTANTIAL_DELETION"
    finding = feedback["patch_quality_findings"][0]
    assert finding["minimum_removed_code_line_count"] == 11
    assert finding["removed_code_line_count"] < 11


def test_stage3_running_token_usage_falls_back_to_runtime_breaker_model(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-token-fallback.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, github_repo_id=310, full_name="owner/stage3-token-fallback")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="a" * 40,
        created_at=datetime(2024, 2, 1, tzinfo=UTC),
        planner_guidance="use breaker fallback model",
        test_files=[("tests/test_entry.py", 3)],
    )

    materialize_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert materialize_response.status_code == 200
    entry_file_id = materialize_response.json()["entry_files"][0]["id"]

    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]

    session = client.app.state.session_factory()
    try:
        run = session.get(Stage3Run, run_id)
        assert run is not None
        run.status = Stage3RunStatus.running.value
        run.started_at = datetime(2024, 2, 1, 8, 0, tzinfo=UTC)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = {
            "breaker": {
                "model": "openai/stage3-runtime-breaker",
                "base_url": "https://example.invalid/v1",
                "api_key": "secret",
                "api_key_preview": "secret...",
                "preset": "default",
                "max_iterations": 100,
                "timeout_seconds": 1800.0,
            },
            "hyperparameters": {
                "build_timeout_seconds": 1800.0,
                "run_test_timeout_seconds": 300.0,
                "full_validation_timeout_seconds": 1800.0,
            },
        }
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        run.summary_json = {
            **dict(run.summary_json or {}),
            "breaker_model": "unknown",
            "breaker_token_usage": 137561,
        }
        session.commit()
    finally:
        session.close()

    detail_response = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    )
    assert detail_response.status_code == 200
    selected_run = detail_response.json()["selected_run"]
    assert selected_run["token_usage_by_model"] == {"openai/stage3-runtime-breaker": 137561}


def test_stage3_token_usage_merges_resume_model_aliases(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-token-alias.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, github_repo_id=311, full_name="owner/stage3-token-alias")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="b" * 40,
        created_at=datetime(2024, 2, 2, tzinfo=UTC),
        planner_guidance="merge breaker model aliases",
        test_files=[("tests/test_entry.py", 3)],
    )

    materialize_response = client.get(f"/api/stage3/repos/{repository.id}")
    assert materialize_response.status_code == 200
    entry_file_id = materialize_response.json()["entry_files"][0]["id"]

    create_response = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs")
    assert create_response.status_code == 200
    run_id = create_response.json()["selected_run"]["id"]

    session = client.app.state.session_factory()
    try:
        run = session.get(Stage3Run, run_id)
        assert run is not None
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = {
            "breaker": {
                "model": "glm-5-w4a8",
                "base_url": "https://example.invalid/v1",
                "api_key": "secret",
                "api_key_preview": "secret...",
                "preset": "default",
                "max_iterations": 100,
                "timeout_seconds": 1800.0,
            },
            "hyperparameters": {
                "build_timeout_seconds": 1800.0,
                "run_test_timeout_seconds": 300.0,
                "full_validation_timeout_seconds": 1800.0,
            },
        }
        runtime_stage3["resume_materialized"] = {
            "duration_seconds": 60,
            "token_usage_by_model": {"glm-5-w4a8": 48440},
        }
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        run.summary_json = {
            **dict(run.summary_json or {}),
            "breaker_model": "openai/glm-5-w4a8",
            "breaker_token_usage": 365729,
        }
        session.commit()
    finally:
        session.close()

    detail_response = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    )
    assert detail_response.status_code == 200
    selected_run = detail_response.json()["selected_run"]
    assert selected_run["token_usage_by_model"] == {"glm-5-w4a8": 414169}


def test_stage3_token_usage_model_ignores_unknown_completion_model() -> None:
    assert (
        stage3_runner_module._event_token_usage_model(
            {
                "llm_completion": {"model_name": "unknown"},
                "agent": {"model": "openai/stage3-breaker"},
            }
        )
        == "openai/stage3-breaker"
    )
    assert stage3_runner_module._event_token_usage_model({"llm_completion": {"model_name": "unknown"}}) == ""


def test_stage3_savepoint_archive_failure_restores_run_state(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-savepoint-archive-failure.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-savepoint-archive-failure")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="archivefail123",
        created_at=datetime(2024, 1, 20, tzinfo=UTC),
        planner_guidance="exercise savepoint archive rollback",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = detail_payload["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    checkpoint_dir = Path(selected_run["workspace_manifest"]["checkpoint_dir"])
    savepoint_dir = Path(selected_run["workspace_manifest"]["savepoint_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)

    def broken_persist_savepoint_assets(
        self,  # noqa: ANN001
        run,  # noqa: ANN001
        *,
        depth,  # noqa: ANN001
        gold_patch_text,  # noqa: ANN001
        checkpoint,  # noqa: ANN001
        evaluation,  # noqa: ANN001
        feedback,  # noqa: ANN001
        collateral,  # noqa: ANN001
    ) -> None:
        del evaluation, feedback, collateral, checkpoint
        partial_savepoint_dir = Path(self._workspace_manifest_for_run(run)["savepoint_dir"]) / f"depth-{int(depth):03d}"
        partial_savepoint_dir.mkdir(parents=True, exist_ok=True)
        (partial_savepoint_dir / "gold.patch").write_text(str(gold_patch_text), encoding="utf-8")
        raise RuntimeError("disk full while persisting savepoint assets")

    monkeypatch.setattr(Stage3Service, "_persist_savepoint_assets", broken_persist_savepoint_assets)

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        with pytest.raises(Stage3RunConflictError, match="disk full while persisting savepoint assets"):
            service.create_savepoint(repository.id, run_id)
    finally:
        session.close()

    refreshed_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    refreshed_run = refreshed_payload["selected_run"]
    assert refreshed_run["phase"] == "breaker_running"
    assert refreshed_run["draft_savepoint"] is None
    assert refreshed_run["savepoints"] == []
    assert any(event["title"] == "Savepoint archiving failed" for event in refreshed_run["events"])
    assert not (checkpoint_dir / "depth-001").exists()
    assert not (savepoint_dir / "depth-001").exists()


def test_stage3_savepoint_archive_failure_records_cleanup_tombstone_on_cleanup_failure(
    tmp_path,
    monkeypatch,
) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-savepoint-archive-cleanup-tombstone.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-savepoint-archive-cleanup-tombstone")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="archivecleanuptombstone",
        created_at=datetime(2024, 1, 21, tzinfo=UTC),
        planner_guidance="exercise savepoint archive cleanup tombstone",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = detail_payload["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()[
        "selected_run"
    ]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)

    def broken_persist_savepoint_assets(
        self,  # noqa: ANN001
        run,  # noqa: ANN001
        *,
        depth,  # noqa: ANN001
        gold_patch_text,  # noqa: ANN001
        checkpoint,  # noqa: ANN001
        evaluation,  # noqa: ANN001
        feedback,  # noqa: ANN001
        collateral,  # noqa: ANN001
    ) -> None:
        del evaluation, feedback, collateral, checkpoint
        partial_savepoint_dir = Path(self._workspace_manifest_for_run(run)["savepoint_dir"]) / f"depth-{int(depth):03d}"
        partial_savepoint_dir.mkdir(parents=True, exist_ok=True)
        (partial_savepoint_dir / "gold.patch").write_text(str(gold_patch_text), encoding="utf-8")
        raise RuntimeError("disk full while persisting savepoint assets")

    def fake_remove_paths(paths, *, root_dir=None):  # noqa: ANN001
        del root_dir
        return list(paths or [])

    monkeypatch.setattr(Stage3Service, "_persist_savepoint_assets", broken_persist_savepoint_assets)
    monkeypatch.setattr(stage3_service_module, "_remove_paths", fake_remove_paths)

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        with pytest.raises(Stage3RunConflictError, match="disk full while persisting savepoint assets"):
            service.create_savepoint(repository.id, run_id)
    finally:
        session.close()

    session = client.app.state.session_factory()
    try:
        tombstone = session.get(Stage3CleanupTombstone, run_id)
        assert tombstone is not None
        assert tombstone.reason == "savepoint_archive_failure"
        assert tombstone.status == "failed"
        assert tombstone.archive_json["cleanup_scope"] == "partial_savepoint_archive_assets"
        assert tombstone.failure_json["failed_paths"]
    finally:
        session.close()


def test_stage3_rejected_savepoint_exception_carries_collateral(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-rejected-collateral.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )

    class CollateralEvaluator:
        def evaluate_original_p2p(
            self,
            *,
            run_id: str,
            workspace_dir: Path,
            repo_dir: Path,
            snapshot_id: str,
            source_stage2_run_id: str,
            source_commit_sha: str,
            base_image_id: str,
            dockerfile_text: str,
            run_script_text: str,
            original_p2p_files: list[str],
            emit_event=None,
        ) -> Stage3EvaluationResult:
            del (
                run_id,
                workspace_dir,
                snapshot_id,
                source_stage2_run_id,
                source_commit_sha,
                base_image_id,
                dockerfile_text,
                run_script_text,
            )
            content = (repo_dir / "app.py").read_text(encoding="utf-8")
            if "for item in items:" in content and "weights.append" not in content:
                entry_counts = (4, 2, 2, 0, 0)
                sibling_counts = (3, 0, 3, 0, 0)
            else:
                entry_counts = (4, 4, 0, 0, 0)
                sibling_counts = (3, 3, 0, 0, 0)

            def build_result(path: str, counts: tuple[int, int, int, int, int]) -> Stage3FileEvaluation:
                total, passed, failed, errors, skipped = counts
                status = "passed" if failed == 0 and errors == 0 and passed > 0 else "failed"
                if emit_event is not None:
                    emit_event(
                        "Collateral evaluator completed",
                        f"Evaluated {path}",
                        {"test_file_path": path, "status": status},
                    )
                return Stage3FileEvaluation(
                    test_file_path=path,
                    status=status,
                    total_tests=total,
                    passed_tests=passed,
                    failed_tests=failed,
                    error_tests=errors,
                    skipped_tests=skipped,
                    pass_rate=(passed / total) if total > 0 else 0.0,
                    raw_result_json={
                        "action": "run",
                        "status": status,
                        "summary": {
                            "collected": total,
                            "passed": passed,
                            "failed": failed,
                            "errors": errors,
                            "skipped": skipped,
                        },
                    },
                )

            file_results: list[Stage3FileEvaluation] = []
            for path in original_p2p_files:
                if path == "tests/test_target_feature.py":
                    file_results.append(build_result(path, entry_counts))
                else:
                    file_results.append(build_result(path, sibling_counts))
            return Stage3EvaluationResult(image_tag="fake-stage3:test", file_results=file_results)

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: CollateralEvaluator())

    repository = _seed_repository(client, full_name="owner/stage3-rejected-collateral")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="feedface1234",
        created_at=datetime(2024, 1, 21, tzinfo=UTC),
        planner_guidance="carry collateral back to the agent",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )

    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)

    first_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert first_feedback["accepted"] is True

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        try:
            service.create_savepoint(repository.id, run_id)
            raise AssertionError("expected savepoint rejection")
        except Stage3SavepointRejectedError as exc:
            assert exc.feedback["code"] == "ENTRY_NOT_DEEPER"
            assert exc.collateral == {
                "non_entry_failed_count": 1,
                "non_entry_failed_files": [
                    {
                        "test_file_path": "tests/test_sibling_feature.py",
                        "status": "failed",
                        "pass_rate": 0.0,
                    }
                ],
            }
    finally:
        session.close()


def test_stage3_savepoint_rejects_entry_error_breakage(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-entry-error-breakage.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )

    class EntryErrorEvaluator:
        def evaluate_original_p2p(
            self,
            *,
            run_id: str,
            workspace_dir: Path,
            repo_dir: Path,
            snapshot_id: str,
            source_stage2_run_id: str,
            source_commit_sha: str,
            base_image_id: str,
            dockerfile_text: str,
            run_script_text: str,
            original_p2p_files: list[str],
            emit_event=None,
        ) -> Stage3EvaluationResult:
            del (
                run_id,
                workspace_dir,
                repo_dir,
                snapshot_id,
                source_stage2_run_id,
                source_commit_sha,
                base_image_id,
                dockerfile_text,
                run_script_text,
                emit_event,
            )
            file_results: list[Stage3FileEvaluation] = []
            for path in original_p2p_files:
                if path == "tests/test_target_feature.py":
                    file_results.append(
                        Stage3FileEvaluation(
                            test_file_path=path,
                            status="failed",
                            total_tests=1,
                            passed_tests=0,
                            failed_tests=0,
                            error_tests=1,
                            skipped_tests=0,
                            pass_rate=0.0,
                            raw_result_json={
                                "action": "run",
                                "status": "error",
                                "summary": {
                                    "collected": 1,
                                    "passed": 0,
                                    "failed": 0,
                                    "errors": 1,
                                    "skipped": 0,
                                },
                                "message": "test runner broke",
                            },
                        )
                    )
                else:
                    file_results.append(
                        Stage3FileEvaluation(
                            test_file_path=path,
                            status="passed",
                            total_tests=3,
                            passed_tests=3,
                            failed_tests=0,
                            error_tests=0,
                            skipped_tests=0,
                            pass_rate=1.0,
                            raw_result_json={
                                "action": "run",
                                "status": "passed",
                                "summary": {
                                    "collected": 3,
                                    "passed": 3,
                                    "failed": 0,
                                    "errors": 0,
                                    "skipped": 0,
                                },
                            },
                        )
                    )
            return Stage3EvaluationResult(image_tag="fake-stage3:test", file_results=file_results)

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: EntryErrorEvaluator())

    repository = _seed_repository(client, full_name="owner/stage3-entry-error")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="deadbeef1234",
        created_at=datetime(2024, 1, 22, tzinfo=UTC),
        planner_guidance="reject test-runner breakage",
        test_files=[
            ("tests/test_target_feature.py", 4),
            ("tests/test_sibling_feature.py", 3),
        ],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = next(
        row["id"]
        for row in detail_payload["entry_files"]
        if row["test_file_path"] == "tests/test_target_feature.py"
    )

    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    start_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start")
    assert start_response.status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)

    rejected_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    rejected_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert rejected_feedback["accepted"] is False
    assert rejected_feedback["code"] == "ENTRY_INVALID_BREAKAGE"
    assert rejected_feedback["entry_status"] == "failed"
    assert rejected_feedback["entry_error_tests"] == 1
    assert rejected_payload["selected_run"]["savepoints"] == []
    assert rejected_payload["selected_run"]["draft_savepoint"]["feedback"]["code"] == "ENTRY_INVALID_BREAKAGE"


def test_stage3_savepoint_evaluation_events_are_committed_live(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-savepoint-live-events.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )

    observed_live_event_counts: list[int] = []

    class LiveEventEvaluator:
        def evaluate_original_p2p(
            self,
            *,
            run_id: str,
            workspace_dir: Path,
            repo_dir: Path,
            snapshot_id: str,
            source_stage2_run_id: str,
            source_commit_sha: str,
            base_image_id: str,
            dockerfile_text: str,
            run_script_text: str,
            original_p2p_files: list[str],
            emit_event=None,
        ) -> Stage3EvaluationResult:
            del (
                workspace_dir,
                repo_dir,
                snapshot_id,
                source_stage2_run_id,
                source_commit_sha,
                base_image_id,
                dockerfile_text,
                run_script_text,
                original_p2p_files,
            )
            if emit_event is not None:
                emit_event(
                    "Live savepoint progress",
                    "Progress should be visible before evaluation returns",
                    {"index": 1, "total": 1},
                )
            session = client.app.state.session_factory()
            try:
                observed_live_event_counts.append(
                    len(
                        session.scalars(
                            select(Stage3RunEvent).where(
                                Stage3RunEvent.run_id == run_id,
                                Stage3RunEvent.title == "Live savepoint progress",
                            )
                        ).all()
                    )
                )
            finally:
                session.close()
            return Stage3EvaluationResult(
                image_tag="fake-stage3:test",
                file_results=[
                    Stage3FileEvaluation(
                        test_file_path="tests/test_target_feature.py",
                        status="failed",
                        total_tests=4,
                        passed_tests=2,
                        failed_tests=2,
                        error_tests=0,
                        skipped_tests=0,
                        pass_rate=0.5,
                        raw_result_json={
                            "action": "run",
                            "status": "failed",
                            "summary": {
                                "collected": 4,
                                "passed": 2,
                                "failed": 2,
                                "errors": 0,
                                "skipped": 0,
                            },
                        },
                    )
                ],
            )

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: LiveEventEvaluator())
    repository = _seed_repository(client, full_name="owner/stage3-live-events")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="liveevent1234",
        created_at=datetime(2024, 1, 23, tzinfo=UTC),
        planner_guidance="commit savepoint progress events immediately",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = detail_payload["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)

    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )

    assert savepoint_feedback["accepted"] is True
    assert observed_live_event_counts == [1]


def test_stage3_savepoint_unexpected_evaluation_error_restores_run_phase(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-savepoint-unexpected-error.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )

    class ExplodingEvaluator:
        def evaluate_original_p2p(self, **kwargs):  # noqa: ANN001
            del kwargs
            raise RuntimeError("unexpected evaluator boom")

    monkeypatch.setattr(Stage3Service, "_evaluator", lambda self: ExplodingEvaluator())
    repository = _seed_repository(client, full_name="owner/stage3-unexpected-error")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="unexpectederror1234",
        created_at=datetime(2024, 1, 24, tzinfo=UTC),
        planner_guidance="restore run phase after unexpected evaluation errors",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    detail_payload = client.get(f"/api/stage3/repos/{repository.id}").json()
    entry_file_id = detail_payload["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        with pytest.raises(Stage3RunConflictError, match="unexpected evaluator boom"):
            service.create_savepoint(repository.id, run_id)
    finally:
        session.close()

    refreshed_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    refreshed_run = refreshed_payload["selected_run"]
    assert refreshed_run["phase"] == "breaker_running"
    assert refreshed_run["error_message"] == "unexpected evaluator boom"
    failure_events = [
        event
        for event in refreshed_run["events"]
        if event["title"] == "Savepoint evaluation failed"
    ]
    assert failure_events
    assert failure_events[-1]["payload"]["code"] == "EVALUATION_UNEXPECTED_ERROR"


def test_stage3_resume_requires_available_checkpoint_docker_image(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-resume-missing-image.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-resume-missing-image")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="missingimage123",
        created_at=datetime(2024, 1, 17, tzinfo=UTC),
        planner_guidance="resume requires checkpoint image",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=savepoint_payload["selected_run"]["savepoints"][0]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref="feature-factory/stage3-breaker-checkpoint:missing-image-depth-001",
    )
    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: False)

    interrupt_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/interrupt")
    assert interrupt_response.status_code == 200
    interrupted_run = interrupt_response.json()["selected_run"]
    assert interrupted_run["can_resume"] is False

    resume_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/resume")
    assert resume_response.status_code == 409
    assert "reusable checkpoint" in resume_response.json()["detail"]
    assert stage3_runner.scheduled_run_ids == [run_id]


def test_stage3_resume_disabled_by_checkpoint_setting(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-resume-disabled.db'}",
        stage3_enable_checkpoints=False,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )

    response = client.post("/api/stage3/repos/1/runs/missing-run/resume")

    assert response.status_code == 409
    assert response.json()["detail"] == "stage3 checkpoint resume is disabled"


def test_stage3_clear_breaker_checkpoints_updates_savepoint_status(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-clear-checkpoint-status.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-clear-checkpoint-status")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="clearstatus123",
        created_at=datetime(2024, 1, 18, tzinfo=UTC),
        planner_guidance="clear checkpoint status",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    _write_stage3_app_breakage(Path(selected_run["workspace_manifest"]["repo_dir"]), level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True
    savepoint_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    source_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=savepoint_payload["selected_run"]["savepoints"][0]["checkpoint"][
            "workspace_snapshot_path"
        ],
        source_image_ref=source_image_ref,
    )
    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    ready_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert ready_payload["selected_run"]["savepoints"][0]["checkpoint_status"] == "ready"
    assert ready_payload["selected_run"]["savepoints"][0]["reusable_checkpoint_ready"] is True

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(session, workspace_root=stage3_root, settings=settings)
        service.clear_breaker_checkpoints(run_id)
        session.commit()
    finally:
        session.close()

    cleaned_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert cleaned_payload["selected_run"]["savepoints"][0]["checkpoint_status"] == "cleaned"
    assert cleaned_payload["selected_run"]["savepoints"][0]["reusable_checkpoint_ready"] is False
    assert cleaned_payload["selected_run"]["can_resume"] is False


def test_stage3_store_savepoint_checkpoint_keeps_only_latest_checkpoint(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-latest-checkpoint.db'}",
        stage3_workspace_dir=stage3_root,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-latest-checkpoint")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="latestcheckpoint123",
        created_at=datetime(2024, 1, 18, tzinfo=UTC),
        planner_guidance="keep only the latest reusable checkpoint",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    monkeypatch.setattr(stage3_service_module, "_docker_image_exists", lambda image_ref: True)
    removed_images: list[list[str]] = []

    def fake_remove_docker_images(image_refs):  # noqa: ANN001
        refs = [str(image_ref or "").strip() for image_ref in image_refs if str(image_ref or "").strip()]
        if refs:
            removed_images.append(refs)
        return []

    monkeypatch.setattr(stage3_service_module, "_remove_docker_images", fake_remove_docker_images)

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(
        f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs"
    ).json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])

    _write_stage3_app_breakage(repo_dir, level=1)
    first_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert first_feedback["accepted"] is True
    first_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    first_service_snapshot_path = first_payload["selected_run"]["savepoints"][0]["checkpoint"][
        "workspace_snapshot_path"
    ]
    first_service_checkpoint_dir = Path(first_service_snapshot_path).parent
    first_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-001"
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=1,
        source_snapshot_path=first_service_snapshot_path,
        source_image_ref=first_image_ref,
    )
    first_ready_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    assert first_ready_payload["selected_run"]["savepoints"][0]["checkpoint_status"] == "ready"
    assert first_ready_payload["selected_run"]["savepoints"][0]["reusable_checkpoint_ready"] is True
    assert not first_service_checkpoint_dir.exists()

    _write_stage3_app_breakage(repo_dir, level=2)
    second_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert second_feedback["accepted"] is True
    second_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    first_runtime_checkpoint_dir = stage3_root / "runtime" / run_id / "breaker-checkpoints" / "depth-001"
    second_service_snapshot_path = second_payload["selected_run"]["savepoints"][1]["checkpoint"][
        "workspace_snapshot_path"
    ]
    second_service_checkpoint_dir = Path(second_service_snapshot_path).parent
    second_image_ref = f"feature-factory/stage3-breaker-checkpoint:{run_id}-depth-002"
    assert first_runtime_checkpoint_dir.exists()
    _store_fake_stage3_resume_checkpoint(
        client,
        run_id=run_id,
        depth=2,
        source_snapshot_path=second_service_snapshot_path,
        source_image_ref=second_image_ref,
    )

    checkpoint_ready_payload = client.get(
        f"/api/stage3/repos/{repository.id}",
        params={"selected_entry_file_id": entry_file_id, "selected_run_id": run_id},
    ).json()
    savepoints = checkpoint_ready_payload["selected_run"]["savepoints"]
    assert savepoints[0]["checkpoint"] == {}
    assert savepoints[0]["checkpoint_status"] == "cleaned"
    assert savepoints[0]["reusable_checkpoint_ready"] is False
    assert savepoints[1]["checkpoint_status"] == "ready"
    assert savepoints[1]["reusable_checkpoint_ready"] is True
    assert checkpoint_ready_payload["selected_run"]["runtime_snapshot"]["stage3"]["resume_checkpoint"][
        "docker_image_ref"
    ] == second_image_ref
    assert not first_runtime_checkpoint_dir.exists()
    assert not second_service_checkpoint_dir.exists()
    assert any(first_image_ref in refs for refs in removed_images)


def test_stage3_run_can_rerun_and_complete(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-rerun.db'}",
        stage3_workspace_dir=stage3_root,
        stage3_max_concurrent_runs=27,
        stage3_default_task_max_concurrent_runs=27,
        stage3_llm_model="global-breaker-model",
        stage3_llm_base_url="https://global-breaker.example/v1",
        stage3_llm_api_key=SecretStr("global-breaker-key"),
        stage3_openhands_preset="default",
        stage3_openhands_max_iterations=333,
        stage3_agent_timeout_seconds=444.0,
        stage3_build_timeout_seconds=555.0,
        stage3_run_test_timeout_seconds=666.0,
        stage3_full_validation_timeout_seconds=777.0,
        stage3_entry_pass_rate_ceiling=0.5,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    stage3_runner = DummyStage3Runner()
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=stage3_runner,
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-rerun")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="decaf1234567",
        created_at=datetime(2024, 1, 14, tzinfo=UTC),
        planner_guidance="baseline rerun",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200
    assert stage3_runner.scheduled_run_ids == [run_id]
    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True
    complete_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/complete")
    assert complete_response.status_code == 409

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        source_run = service.get_run(run_id)
        runtime_stage3 = dict((source_run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = {
            "concurrency": {"max_concurrent_runs": 41},
            "breaker": {
                "model": "rerun-source-breaker-model",
                "base_url": "https://rerun-source-breaker.example/v1",
                "api_key": "rerun-source-breaker-key",
                "api_key_preview": "rerun-...",
                "preset": "gpt5",
                "max_iterations": 246,
                "timeout_seconds": 1357.0,
            },
                "hyperparameters": {
                    "build_timeout_seconds": 975.0,
                    "run_test_timeout_seconds": 753.0,
                    "full_validation_timeout_seconds": 864.0,
                    "entry_pass_rate_ceiling": 0.9,
                },
            }
        source_run.runtime_snapshot_json = {
            **dict(source_run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        service.mark_run_completed(run_id, summary_updates={"runner_status": "completed"})
        session.commit()
    finally:
        session.close()

    completed_run = client.get(f"/api/stage3/repos/{repository.id}/runs/{run_id}").json()["run"]
    assert completed_run["result"] == "archived"
    assert completed_run["can_resume"] is False

    rerun_response = client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/rerun")
    assert rerun_response.status_code == 200
    rerun_payload = rerun_response.json()
    rerun_run = rerun_payload["selected_run"]
    assert rerun_run["trigger_kind"] == "rerun"
    assert rerun_run["status"] == Stage3RunStatus.queued.value
    assert stage3_runner.scheduled_run_ids[-1] == rerun_run["id"]
    assert rerun_run["summary"]["latest_depth"] == 0
    rerun_runtime = rerun_run["runtime_snapshot"]["stage3"]["runtime"]
    assert rerun_runtime["concurrency"]["max_concurrent_runs"] == 27
    assert rerun_runtime["breaker"]["model"] == "global-breaker-model"
    assert rerun_runtime["breaker"]["base_url"] == "https://global-breaker.example/v1"
    assert rerun_runtime["breaker"]["api_key_preview"] == "global..."
    assert "api_key" not in rerun_runtime["breaker"]
    assert rerun_runtime["breaker"]["preset"] == "default"
    assert rerun_runtime["breaker"]["max_iterations"] == 333
    assert rerun_runtime["breaker"]["timeout_seconds"] == 444.0
    assert rerun_runtime["hyperparameters"]["build_timeout_seconds"] == 555.0
    assert rerun_runtime["hyperparameters"]["run_test_timeout_seconds"] == 666.0
    assert rerun_runtime["hyperparameters"]["full_validation_timeout_seconds"] == 777.0
    assert rerun_runtime["hyperparameters"]["entry_pass_rate_ceiling"] == 0.5


def test_stage3_completion_records_threshold_notice_when_entry_pass_rate_ceiling_is_unmet(tmp_path, monkeypatch) -> None:
    _install_fake_stage3_runtime(monkeypatch)
    stage3_root = tmp_path / "stage3"
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-api-threshold.db'}",
        stage3_workspace_dir=stage3_root,
        stage3_entry_pass_rate_ceiling=0.5,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    client = TestClient(
        create_app(
            settings,
            runner=NoopStage1Runner(),
            stage2_runner=DummyStage2Runner(),
            stage3_runner=DummyStage3Runner(),
        )
    )
    repository = _seed_repository(client, full_name="owner/stage3-threshold")
    _seed_stage2_passed_run(
        client,
        repository_id=repository.id,
        commit_sha="threshold1234",
        created_at=datetime(2024, 1, 19, tzinfo=UTC),
        planner_guidance="threshold baseline",
        test_files=[("tests/test_target_feature.py", 4)],
    )

    entry_file_id = client.get(f"/api/stage3/repos/{repository.id}").json()["selected_entry_file"]["id"]
    run_id = client.post(f"/api/stage3/repos/{repository.id}/entry-files/{entry_file_id}/runs").json()["selected_run"]["id"]
    update_response = client.patch(
        f"/api/stage3/repos/{repository.id}/runs/{run_id}/runtime",
        json={"entry_pass_rate_ceiling": 0.25},
    )
    assert update_response.status_code == 200
    assert client.post(f"/api/stage3/repos/{repository.id}/runs/{run_id}/start").status_code == 200

    selected_run = _start_stage3_run_via_runner_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    repo_dir = Path(selected_run["workspace_manifest"]["repo_dir"])
    _write_stage3_app_breakage(repo_dir, level=1)
    savepoint_feedback = _create_stage3_savepoint_via_tool_path(
        client,
        repository_id=repository.id,
        run_id=run_id,
    )
    assert savepoint_feedback["accepted"] is True
    assert savepoint_feedback["entry_pass_rate"] == 0.5

    session = client.app.state.session_factory()
    try:
        service = Stage3Service(
            session,
            workspace_root=client.app.state.settings.stage3_workspace_dir,
            settings=client.app.state.settings,
        )
        completed_run = service.mark_run_completed(run_id, summary_updates={"runner_status": "completed"})
        session.commit()
    finally:
        session.close()

    assert completed_run.result == Stage3RunResult.archived.value
    assert completed_run.error_message is None

    completed_payload = client.get(f"/api/stage3/repos/{repository.id}/runs/{run_id}").json()["run"]
    assert completed_payload["result"] == Stage3RunResult.archived.value
    assert completed_payload["summary"]["completion_notice"] == "ENTRY_PASS_RATE_THRESHOLD_UNMET"
    assert completed_payload["summary"]["entry_pass_rate_ceiling_met"] is False
    assert completed_payload["summary"]["latest_entry_pass_rate"] == 0.5
    assert completed_payload["summary"]["entry_pass_rate_ceiling"] == 0.25