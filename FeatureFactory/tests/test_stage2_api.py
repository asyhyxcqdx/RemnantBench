from __future__ import annotations

import io
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import feature_factory.db as db_module
import feature_factory.server as server_module
import feature_factory.stage2.service as stage2_service_module
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session as SQLAlchemySession

from feature_factory.config import Settings
from feature_factory.db import Base, build_engine, build_session_factory
from feature_factory.docker_mirrors import reset_github_proxy_queue
from feature_factory.models import (
    GitHubRepository,
    Stage2CleanupTombstone,
    Stage2Run,
    Stage2RunEvent,
    Stage2RunResult,
    Stage2RunStatus,
    Stage2TestResult,
    Stage2ValidationAttempt,
)
from feature_factory.server import create_app
from feature_factory.stage2.service import Stage2Service


class NoopStage1Runner:
    def shutdown(self) -> None:
        return None


class DummyStage2Runner:
    def __init__(self) -> None:
        self.scheduled: list[str] = []
        self.interrupted: list[str] = []
        self.running_repository_ids: set[int] = set()
        self.reload_backend_calls = 0
        self.max_limit = 32
        self._max_workers = 16

    def schedule_run(self, run_id: str) -> bool:
        self.scheduled.append(run_id)
        return True

    def is_repository_running(self, repository_id: int) -> bool:
        return repository_id in self.running_repository_ids

    def interrupt_run(self, run_id: str) -> bool:
        self.interrupted.append(run_id)
        return True

    def list_running_repository_ids(self) -> set[int]:
        return set(self.running_repository_ids)

    def get_max_workers(self) -> int:
        return self._max_workers

    def set_max_workers(self, max_workers: int) -> None:
        if max_workers < 1:
            raise ValueError("max_concurrent_runs must be at least 1")
        if max_workers > self.max_limit:
            raise ValueError(f"max_concurrent_runs cannot exceed {self.max_limit}")
        self._max_workers = max_workers

    def reload_backend(self) -> None:
        self.reload_backend_calls += 1

    def shutdown(self) -> None:
        return None


class DummyLifecycleRunner:
    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        del timeout_seconds
        return []


def _seed_repository(
    client: TestClient,
    *,
    github_repo_id: int = 9001,
    full_name: str = "owner/stage2-repo",
    primary_language: str = "Python",
    default_branch: str = "main",
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
            description="stage2 repo",
            primary_language=primary_language,
            default_branch=default_branch,
            license_key="mit",
            stargazers_count=123,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
        return repository
    finally:
        session.close()


def test_stage2_run_detail_expands_truncated_openhands_bridge_error(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-expanded-error.db'}")
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    stderr_path = tmp_path / "worker-attempt-1-stderr.log"
    full_stderr = "OpenHands stderr starts\n" + ("y" * 5000) + "\nOpenHands stderr ends"
    stderr_path.write_text(full_stderr, encoding="utf-8")
    stdout_path = tmp_path / "worker-attempt-1-stdout.log"
    stdout_path.write_text("stdout fallback", encoding="utf-8")

    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=991001,
            full_name="owner/expanded-error",
            owner_login="owner",
            name="expanded-error",
            html_url="https://github.com/owner/expanded-error",
            api_url="https://api.github.com/repos/owner/expanded-error",
            description="stage2 repo",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
        )
        session.add(repository)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.failed.value,
            trigger_kind="manual",
            phase="completed",
            target_branch="main",
            target_commit_sha="abc123",
            error_message="OpenHands worker-attempt-1 failed: truncated stderr...",
        )
        session.add(run)
        session.flush()
        session.add(
            Stage2RunEvent(
                run_id=run.id,
                actor="system",
                phase="container_worker",
                title="OpenHands bridge output",
                message="stderr tail",
                payload_json={
                    "operation": "openhands_bridge_worker",
                    "stderr_log": str(stderr_path),
                    "stdout_log": str(stdout_path),
                },
            )
        )
        session.commit()

        loaded = Stage2Service(session).get_repository_run(repository.id, run.id, include_detail=True)
        payload = Stage2Service(session).serialize_run_detail(loaded)
    finally:
        session.close()

    assert payload["error_message"] == f"OpenHands worker-attempt-1 failed: {full_stderr}"
    assert payload["error_message"].endswith("OpenHands stderr ends")


def test_stage2_api_can_create_list_and_detail_runs(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-api.db'}",
        stage2_planner_llm_model="planner-v1",
        stage2_planner_llm_base_url="https://planner-v1.example.test/v1",
        stage2_planner_llm_api_key=SecretStr("planner-key-v1"),
        stage2_worker_llm_model="worker-v1",
        stage2_worker_llm_base_url="https://worker-v1.example.test/v1",
        stage2_worker_llm_api_key=SecretStr("worker-key-v1"),
    )
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")

    repos_before = client.get("/api/stage2/repos")
    assert repos_before.status_code == 200
    assert repos_before.json()["repositories"][0]["stage2"]["status"] == "pending"
    assert repos_before.json()["repositories"][0]["stage2"]["history_count"] == 0

    create = client.post(f"/api/stage2/repos/{repository.id}/runs")
    assert create.status_code == 200
    payload = create.json()
    assert payload["repository"]["stage2"]["status"] == "queued"
    assert payload["runs"][0]["target_commit_sha"] == "abc123def456"
    assert payload["runs"][0]["trigger_kind"] == "manual"
    assert payload["runs"][0]["display_status"] == "queued"
    assert stage2_runner.scheduled == [payload["runs"][0]["id"]]

    repos_after = client.get("/api/stage2/repos")
    assert repos_after.status_code == 200
    assert repos_after.json()["repositories"][0]["stage2"]["status"] == "queued"
    assert repos_after.json()["repositories"][0]["stage2"]["latest_run"]["target_commit_sha"] == "abc123def456"

    detail = client.get(f"/api/stage2/repos/{repository.id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["runs"][0]["target_commit_sha"] == "abc123def456"
    assert "events" not in detail_payload["runs"][0]

    run_detail = client.get(f"/api/stage2/repos/{repository.id}/runs/{detail_payload['runs'][0]['id']}")
    assert run_detail.status_code == 200
    assert run_detail.json()["run"]["events"][0]["title"] == "Run queued"
    assert run_detail.json()["run"]["runtime_snapshot"]["planner"]["model"] == "planner-v1"
    assert run_detail.json()["run"]["runtime_snapshot"]["planner"]["api_key_preview"] == "planne..."
    assert "api_key" not in run_detail.json()["run"]["runtime_snapshot"]["planner"]
    assert run_detail.json()["run"]["runtime_snapshot"]["worker"]["model"] == "worker-v1"
    assert run_detail.json()["run"]["runtime_snapshot"]["worker"]["api_key_preview"] == "worker..."
    assert "api_key" not in run_detail.json()["run"]["runtime_snapshot"]["worker"]

    settings.stage2_planner_llm_model = "planner-v2"
    settings.stage2_planner_llm_base_url = "https://planner-v2.example.test/v1"
    settings.stage2_planner_llm_api_key = SecretStr("planner-key-v2")
    settings.stage2_worker_llm_model = "worker-v2"
    settings.stage2_worker_llm_base_url = "https://worker-v2.example.test/v1"
    settings.stage2_worker_llm_api_key = SecretStr("worker-key-v2")

    session = client.app.state.session_factory()
    try:
        stored_run = Stage2Service(session).get_run(detail_payload["runs"][0]["id"])
        assert stored_run.runtime_snapshot_json["planner"]["model"] == "planner-v1"
        assert stored_run.runtime_snapshot_json["planner"]["api_key"] == "planner-key-v1"
        assert stored_run.runtime_snapshot_json["worker"]["model"] == "worker-v1"
        assert stored_run.runtime_snapshot_json["worker"]["api_key"] == "worker-key-v1"
    finally:
        session.close()

    duplicate = client.post(f"/api/stage2/repos/{repository.id}/runs")
    assert duplicate.status_code == 409


def test_stage2_api_rejects_run_when_remote_commit_cannot_be_resolved(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-api-resolve-error.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: None)

    response = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert response.status_code == 502
    assert "failed to resolve remote default-branch commit" in response.json()["detail"]
    assert stage2_runner.scheduled == []

    session = client.app.state.session_factory()
    try:
        assert Stage2Service(session).list_repository_runs(repository.id) == []
    finally:
        session.close()


def test_resolve_repo_head_commit_uses_temporary_remote_url_and_timeout(monkeypatch) -> None:
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="main",
    )
    calls: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")

    monkeypatch.setattr(
        stage2_service_module,
        "temporary_git_remote_url",
        lambda url: f"https://ghfast.top/{url}",
    )
    monkeypatch.setattr(stage2_service_module, "_git_ls_remote_timeout_seconds", lambda: 7.0)

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append((list(args), dict(kwargs)))
        return subprocess.CompletedProcess(args, 0, "abc123\trefs/heads/main\n", "")

    monkeypatch.setattr(stage2_service_module.subprocess, "run", fake_run)

    assert stage2_service_module.resolve_repo_head_commit(repository) == "abc123"
    assert calls == [
        (
            [
                "git",
                "ls-remote",
                "--heads",
                "https://ghfast.top/https://github.com/owner/demo-repo",
                "main",
            ],
            {
                "capture_output": True,
                "text": True,
                "check": False,
                "timeout": 7.0,
            },
        )
    ]


def test_resolve_repo_head_commit_times_out_quickly(monkeypatch) -> None:
    repository = GitHubRepository(
        full_name="owner/slow-repo",
        html_url="https://github.com/owner/slow-repo",
        default_branch="main",
    )
    calls: list[list[str]] = []

    monkeypatch.setattr(stage2_service_module, "temporary_git_remote_url", lambda url: url)
    monkeypatch.setattr(stage2_service_module, "_git_ls_remote_timeout_seconds", lambda: 1.0)

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append(list(args))
        raise subprocess.TimeoutExpired(args, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(stage2_service_module.subprocess, "run", fake_run)

    assert stage2_service_module.resolve_repo_head_commit(repository) is None
    assert calls == [
        ["git", "ls-remote", "--heads", "https://github.com/owner/slow-repo", "main"],
        ["git", "ls-remote", "https://github.com/owner/slow-repo", "HEAD"],
    ]


def test_resolve_repo_head_commit_rotates_proxy_immediately_after_timeout(monkeypatch) -> None:
    repository = GitHubRepository(
        full_name="owner/slow-repo",
        html_url="https://github.com/owner/slow-repo",
        default_branch="main",
    )
    calls: list[list[str]] = []
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test/","https://proxy-two.test/"]',
    )
    reset_github_proxy_queue()

    def fake_run(args, **kwargs):  # noqa: ANN001
        calls.append(list(args))
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(args, timeout=kwargs.get("timeout"))
        return subprocess.CompletedProcess(args, 0, "abc123\trefs/heads/main\n", "")

    monkeypatch.setattr(stage2_service_module.subprocess, "run", fake_run)

    assert stage2_service_module.resolve_repo_head_commit(repository) == "abc123"
    assert calls == [
        [
            "git",
            "ls-remote",
            "--heads",
            "https://proxy-one.test/https://github.com/owner/slow-repo",
            "main",
        ],
        [
            "git",
            "ls-remote",
            "--heads",
            "https://proxy-two.test/https://github.com/owner/slow-repo",
            "main",
        ],
    ]


def test_resolve_repo_head_commit_rotates_proxy_after_redirect_failure(monkeypatch) -> None:
    repository = GitHubRepository(
        full_name="owner/redirected-repo",
        html_url="https://github.com/owner/redirected-repo",
        default_branch="main",
    )
    calls: list[list[str]] = []
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_USE_CHINA_MIRRORS", "1")
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test/","https://proxy-two.test/"]',
    )
    reset_github_proxy_queue()

    def fake_run(args, **_kwargs):  # noqa: ANN001
        calls.append(list(args))
        if len(calls) == 1:
            return subprocess.CompletedProcess(
                args,
                128,
                "",
                (
                    "fatal: unable to update url base from redirection:\n"
                    "  asked for: https://proxy-one.test/https://github.com/owner/redirected-repo/info/refs"
                ),
            )
        return subprocess.CompletedProcess(args, 0, "abc123\trefs/heads/main\n", "")

    monkeypatch.setattr(stage2_service_module.subprocess, "run", fake_run)

    assert stage2_service_module.resolve_repo_head_commit(repository) == "abc123"
    assert calls == [
        [
            "git",
            "ls-remote",
            "--heads",
            "https://proxy-one.test/https://github.com/owner/redirected-repo",
            "main",
        ],
        [
            "git",
            "ls-remote",
            "--heads",
            "https://proxy-two.test/https://github.com/owner/redirected-repo",
            "main",
        ],
    ]


def test_resolve_repo_head_commit_treats_proxy_http_failures_as_retryable() -> None:
    for status_code in (429, 520, 522, 524):
        completed = subprocess.CompletedProcess(
            ["git", "ls-remote"],
            128,
            "",
            f"fatal: unable to access: The requested URL returned error: {status_code}",
        )
        assert stage2_service_module._is_retryable_git_ls_remote_failure(completed)  # noqa: SLF001


def test_stage2_api_rejects_run_when_commit_already_has_success_record(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-api-existing-success.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id="stage2-success-old",
                    repository_id=repository.id,
                    status=Stage2RunStatus.completed.value,
                    result=Stage2RunResult.passed.value,
                    phase="completed",
                    target_branch="main",
                    target_commit_sha="abc123def456",
                    created_at=datetime(2026, 4, 10, tzinfo=UTC),
                    finished_at=datetime(2026, 4, 10, 0, 5, tzinfo=UTC),
                ),
                Stage2Run(
                    id="stage2-success-newer-different-commit",
                    repository_id=repository.id,
                    status=Stage2RunStatus.completed.value,
                    result=Stage2RunResult.passed.value,
                    phase="completed",
                    target_branch="main",
                    target_commit_sha="fff999eee888",
                    created_at=datetime(2026, 4, 11, tzinfo=UTC),
                    finished_at=datetime(2026, 4, 11, 0, 5, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")

    response = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert response.status_code == 409
    assert "already successfully built for commit abc123def456" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_can_update_completed_run_runtime_snapshot(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-run-runtime-update.db'}",
        stage2_planner_llm_model="global-planner-model",
        stage2_planner_llm_api_key=SecretStr("global-planner-key"),
        stage2_worker_llm_model="global-worker-model",
        stage2_worker_llm_api_key=SecretStr("global-worker-key"),
    )
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    run_id = "stage2-run-runtime-update"
    source_runtime_snapshot = {
        "concurrency": {
            "max_concurrent_runs": 16,
        },
        "planner": {
            "model": "source-planner-model",
            "base_url": "https://source-planner.example.test/v1",
            "api_key": "source-planner-key",
            "api_key_preview": "source...",
            "preset": "default",
            "max_iterations": 111,
            "timeout_seconds": 1111.0,
        },
        "worker": {
            "model": "source-worker-model",
            "base_url": "https://source-worker.example.test/v1",
            "api_key": "source-worker-key",
            "api_key_preview": "worker-...",
            "preset": "gpt5",
            "max_iterations": 222,
            "timeout_seconds": 2222.0,
        },
        "hyperparameters": {
            "max_worker_attempts": 7,
            "quickcheck_sample_size": 9,
            "entry_file_test_count_min": 3,
            "collect_timeout_seconds": 210.0,
            "run_test_timeout_seconds": 240.0,
            "build_timeout_seconds": 510.0,
            "full_validation_timeout_seconds": 2400.0,
        },
    }

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="deadbeef1234",
                runtime_snapshot_json=source_runtime_snapshot,
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.patch(
        f"/api/stage2/repos/{repository.id}/runs/{run_id}/runtime",
        json={
            "max_concurrent_runs": 8,
            "worker_model": "edited-worker-model",
            "worker_api_key": "edited-worker-key",
            "collect_timeout_seconds": 333,
            "run_test_timeout_seconds": 444,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    selected_run = payload["selected_run"]
    assert selected_run["id"] == run_id
    assert selected_run["runtime_snapshot"]["concurrency"]["max_concurrent_runs"] == 8
    assert selected_run["runtime_snapshot"]["planner"]["model"] == "source-planner-model"
    assert selected_run["runtime_snapshot"]["planner"]["api_key_preview"] == "source..."
    assert "api_key" not in selected_run["runtime_snapshot"]["planner"]
    assert selected_run["runtime_snapshot"]["worker"]["model"] == "edited-worker-model"
    assert selected_run["runtime_snapshot"]["worker"]["api_key_preview"] == "edited..."
    assert "api_key" not in selected_run["runtime_snapshot"]["worker"]
    assert selected_run["runtime_snapshot"]["hyperparameters"]["collect_timeout_seconds"] == 333
    assert selected_run["runtime_snapshot"]["hyperparameters"]["run_test_timeout_seconds"] == 444

    session = client.app.state.session_factory()
    try:
        updated_run = Stage2Service(session).get_run(run_id)
        assert updated_run.runtime_snapshot_json["concurrency"]["max_concurrent_runs"] == 8
        assert updated_run.runtime_snapshot_json["planner"]["model"] == "source-planner-model"
        assert updated_run.runtime_snapshot_json["planner"]["api_key"] == "source-planner-key"
        assert updated_run.runtime_snapshot_json["worker"]["model"] == "edited-worker-model"
        assert updated_run.runtime_snapshot_json["worker"]["api_key"] == "edited-worker-key"
        assert updated_run.runtime_snapshot_json["hyperparameters"]["collect_timeout_seconds"] == 333
        assert updated_run.runtime_snapshot_json["hyperparameters"]["run_test_timeout_seconds"] == 444
    finally:
        session.close()


def test_stage2_api_rejects_runtime_update_for_active_run(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-run-runtime-active.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    run_id = "stage2-run-runtime-active"

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.queued.value,
                result=Stage2RunResult.unknown.value,
                phase="queued",
                target_branch="main",
                target_commit_sha="deadbeef5678",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.patch(
        f"/api/stage2/repos/{repository.id}/runs/{run_id}/runtime",
        json={"worker_model": "edited-worker-model"},
    )

    assert response.status_code == 409
    assert "cannot modify runtime config for an active stage2 run" in response.json()["detail"]


def test_stage2_runtime_config_masks_keys_and_reloads_backend(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runtime.db'}",
        stage2_llm_model="fallback-model",
        stage2_llm_api_key=SecretStr("fallback-secret"),
    )
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))

    initial = client.get("/api/stage2/runtime")

    assert initial.status_code == 200
    assert initial.json()["concurrency"]["max_concurrent_runs"] == 4
    assert initial.json()["concurrency"]["system_capacity"] == 16
    assert initial.json()["planner"]["model"] == "fallback-model"
    assert initial.json()["planner"]["api_key_configured"] is True
    assert initial.json()["planner"]["api_key_preview"] == "fallba..."
    assert initial.json()["worker"]["api_key_preview"] == "fallba..."
    assert initial.json()["hyperparameters"]["entry_file_test_count_min"] == -1
    assert initial.json()["hyperparameters"]["p2p_file_count_limit"] is None
    assert "fallback-secret" not in initial.text

    updated = client.patch(
        "/api/stage2/runtime",
        json={
            "max_concurrent_runs": 6,
            "planner_model": "planner-model",
            "planner_base_url": "https://planner.example.test/v1",
            "planner_api_key": "planner-secret",
            "planner_preset": "default",
            "planner_max_iterations": 77,
            "worker_model": "worker-model",
            "worker_base_url": "https://worker.example.test/v1",
            "worker_api_key": "worker-secret",
            "worker_preset": "gpt5",
            "worker_max_iterations": 88,
            "planner_timeout_seconds": 600,
            "worker_timeout_seconds": 900,
            "max_worker_attempts": 4,
            "quickcheck_sample_size": 7,
            "entry_file_test_count_min": 3,
            "p2p_file_count_limit": 42,
            "collect_timeout_seconds": 120,
            "run_test_timeout_seconds": 180,
            "build_timeout_seconds": 900,
            "full_validation_timeout_seconds": 2400,
        },
    )

    assert updated.status_code == 200
    payload = updated.json()
    assert payload["concurrency"]["max_concurrent_runs"] == 6
    assert payload["concurrency"]["system_capacity"] == 16
    assert payload["planner"]["model"] == "planner-model"
    assert payload["planner"]["base_url"] == "https://planner.example.test/v1"
    assert payload["planner"]["api_key_configured"] is True
    assert payload["planner"]["api_key_preview"] == "planne..."
    assert payload["planner"]["preset"] == "default"
    assert payload["planner"]["max_iterations"] == 77
    assert payload["planner"]["timeout_seconds"] == 600
    assert payload["worker"]["model"] == "worker-model"
    assert payload["worker"]["base_url"] == "https://worker.example.test/v1"
    assert payload["worker"]["api_key_configured"] is True
    assert payload["worker"]["api_key_preview"] == "worker..."
    assert payload["worker"]["max_iterations"] == 88
    assert payload["worker"]["timeout_seconds"] == 900
    assert payload["hyperparameters"]["quickcheck_sample_size"] == 7
    assert payload["hyperparameters"]["entry_file_test_count_min"] == 3
    assert payload["hyperparameters"]["p2p_file_count_limit"] == 42
    assert "planner-secret" not in updated.text
    assert "worker-secret" not in updated.text
    assert stage2_runner.reload_backend_calls == 1
    assert settings.stage2_planner_llm_api_key is not None
    assert settings.stage2_planner_llm_api_key.get_secret_value() == "planner-secret"
    assert settings.stage2_worker_llm_api_key is not None
    assert settings.stage2_worker_llm_api_key.get_secret_value() == "worker-secret"
    assert stage2_runner.get_max_workers() == 16
    assert settings.stage2_default_task_max_concurrent_runs == 6
    assert settings.stage2_p2p_file_count_limit == 42

    cleared_limit = client.patch("/api/stage2/runtime", json={"p2p_file_count_limit": None})

    assert cleared_limit.status_code == 200
    assert cleared_limit.json()["hyperparameters"]["p2p_file_count_limit"] is None
    assert settings.stage2_p2p_file_count_limit is None

    blank_key_update = client.patch("/api/stage2/runtime", json={"planner_api_key": "", "worker_api_key": ""})

    assert blank_key_update.status_code == 200
    assert settings.stage2_planner_llm_api_key.get_secret_value() == "planner-secret"
    assert settings.stage2_worker_llm_api_key.get_secret_value() == "worker-secret"
    assert stage2_runner.reload_backend_calls == 3


def test_stage2_runtime_templates_can_persist_and_overwrite_snapshots(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runtime-templates.db'}",
        stage2_planner_llm_api_key=SecretStr("planner-secret"),
        stage2_worker_llm_api_key=SecretStr("worker-secret"),
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))

    created = client.post(
        "/api/stage2/runtime/templates",
        json={
            "name": "默认模板",
            "max_concurrent_runs": 7,
            "planner_model": "planner-model-v1",
            "worker_model": "worker-model-v1",
            "planner_base_url": "https://planner.example.test/v1",
            "worker_base_url": "https://worker.example.test/v1",
            "planner_preset": "default",
            "worker_preset": "gpt5",
            "planner_max_iterations": 120,
            "worker_max_iterations": 130,
            "planner_timeout_seconds": 600,
            "worker_timeout_seconds": 900,
            "max_worker_attempts": 4,
            "quickcheck_sample_size": 9,
            "entry_file_test_count_min": 3,
            "p2p_file_count_limit": 21,
            "collect_timeout_seconds": 180,
            "run_test_timeout_seconds": 240,
            "build_timeout_seconds": 1200,
            "full_validation_timeout_seconds": 3600,
        },
    )

    assert created.status_code == 200
    created_payload = created.json()
    assert created_payload["name"] == "默认模板"
    assert created_payload["planner_model"] == "planner-model-v1"
    assert created_payload["worker_model"] == "worker-model-v1"

    listed = client.get("/api/stage2/runtime/templates")

    assert listed.status_code == 200
    templates = listed.json()["templates"]
    assert len(templates) == 1
    assert templates[0]["id"] == created_payload["id"]
    assert "planner-secret" not in listed.text
    assert "worker-secret" not in listed.text

    detail = client.get(f"/api/stage2/runtime/templates/{created_payload['id']}")

    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["snapshot"]["concurrency"]["max_concurrent_runs"] == 7
    assert detail_payload["snapshot"]["planner"]["model"] == "planner-model-v1"
    assert detail_payload["snapshot"]["planner"]["api_key"] == "planner-secret"
    assert detail_payload["snapshot"]["planner"]["api_key_preview"] == "planne..."
    assert detail_payload["snapshot"]["worker"]["model"] == "worker-model-v1"
    assert detail_payload["snapshot"]["worker"]["api_key"] == "worker-secret"
    assert detail_payload["snapshot"]["worker"]["api_key_preview"] == "worker..."
    assert detail_payload["snapshot"]["hyperparameters"]["entry_file_test_count_min"] == 3
    assert detail_payload["snapshot"]["hyperparameters"]["p2p_file_count_limit"] == 21
    assert detail_payload["snapshot"]["hyperparameters"]["build_timeout_seconds"] == 1200

    overwritten = client.post(
        "/api/stage2/runtime/templates",
        json={
            "name": "默认模板",
            "max_concurrent_runs": 5,
            "planner_model": "planner-model-v2",
            "worker_model": "worker-model-v2",
        },
    )

    assert overwritten.status_code == 200
    overwritten_payload = overwritten.json()
    assert overwritten_payload["id"] == created_payload["id"]
    assert overwritten_payload["planner_model"] == "planner-model-v2"
    assert overwritten_payload["worker_model"] == "worker-model-v2"

    relisted = client.get("/api/stage2/runtime/templates")
    assert relisted.status_code == 200
    assert len(relisted.json()["templates"]) == 1

    updated_detail = client.get(f"/api/stage2/runtime/templates/{created_payload['id']}")
    assert updated_detail.status_code == 200
    updated_payload = updated_detail.json()
    assert updated_payload["snapshot"]["concurrency"]["max_concurrent_runs"] == 5
    assert updated_payload["snapshot"]["planner"]["model"] == "planner-model-v2"
    assert updated_payload["snapshot"]["worker"]["model"] == "worker-model-v2"
    assert updated_payload["snapshot"]["planner"]["api_key"] == "planner-secret"
    assert updated_payload["snapshot"]["worker"]["api_key"] == "worker-secret"


def test_stage2_runtime_templates_can_be_deleted(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-runtime-template-delete.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))

    created = client.post(
        "/api/stage2/runtime/templates",
        json={
            "name": "可删除模板",
            "planner_model": "planner-model-v1",
            "worker_model": "worker-model-v1",
        },
    )
    assert created.status_code == 200
    template_id = created.json()["id"]

    deleted = client.delete(f"/api/stage2/runtime/templates/{template_id}")

    assert deleted.status_code == 200
    assert deleted.json() == {
        "id": template_id,
        "name": "可删除模板",
    }

    listed = client.get("/api/stage2/runtime/templates")
    assert listed.status_code == 200
    assert listed.json()["templates"] == []

    detail = client.get(f"/api/stage2/runtime/templates/{template_id}")
    assert detail.status_code == 404


def test_stage2_runtime_rejects_task_quota_above_system_capacity(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-runtime-cap.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))

    response = client.patch("/api/stage2/runtime", json={"max_concurrent_runs": 17})

    assert response.status_code == 400
    assert "stage2 system capacity 16" in response.json()["detail"]


def test_stage2_api_exposes_versioned_validation_attempt_assets(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-validation-attempts.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.failed.value,
            phase="completed",
            target_branch="main",
            target_commit_sha="abc123def456",
        )
        session.add(run)
        session.flush()
        service = Stage2Service(session)
        service.store_artifacts(
            run.id,
            dockerfile_text="FROM python:3.11-slim-bookworm\n",
            run_script_text="#!/usr/bin/env bash\necho v1\n",
        )
        service.store_collect_report(run.id, report={"version": 1, "test_files": ["tests/test_v1.py"]})
        service.store_smoke_report(run.id, report={"version": 1, "passed_files": 1})
        service.store_validation_checkpoint(
            run.id,
            attempt_index=1,
            checkpoint={"workspace_snapshot_path": str(tmp_path / "checkpoint-v1.tar.gz")},
        )
        service.store_artifacts(
            run.id,
            dockerfile_text="FROM python:3.12-slim-bookworm\n",
            run_script_text="#!/usr/bin/env bash\necho v2\n",
        )
        service.store_collect_report(run.id, report={"version": 2, "test_files": ["tests/test_v2.py"]})
        service.store_smoke_report(run.id, report={"version": 2, "passed_files": 2})
        service.store_validation_checkpoint(
            run.id,
            attempt_index=2,
            checkpoint={"workspace_snapshot_path": str(tmp_path / "checkpoint-v2.tar.gz")},
        )
        run_id = run.id
        session.commit()
    finally:
        session.close()

    response = client.get(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()["run"]
    assert payload["dockerfile_text"] == "FROM python:3.12-slim-bookworm\n"
    assert payload["run_script_text"] == "#!/usr/bin/env bash\necho v2\n"
    assert payload["collect_report"]["version"] == 2
    assert payload["smoke_report"]["version"] == 2
    attempts = payload["validation_attempts"]
    assert [attempt["attempt_index"] for attempt in attempts] == [1, 2]
    assert attempts[0]["dockerfile_text"] == "FROM python:3.11-slim-bookworm\n"
    assert attempts[0]["collect_report"]["version"] == 1
    assert attempts[0]["smoke_report"]["version"] == 1
    assert attempts[1]["dockerfile_text"] == "FROM python:3.12-slim-bookworm\n"
    assert attempts[1]["collect_report"]["version"] == 2
    assert attempts[1]["smoke_report"]["version"] == 2


def test_stage2_service_can_persist_reports_to_explicit_attempt_index(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-explicit-attempt.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            phase="container_worker",
            target_branch="main",
            target_commit_sha="abc123def456",
        )
        session.add(run)
        session.flush()
        service = Stage2Service(session)
        service.store_artifacts(
            run.id,
            dockerfile_text="FROM python:3.12-slim-bookworm\n",
            run_script_text="#!/usr/bin/env bash\n# --action --out\n",
            attempt_index=3,
        )
        service.store_collect_report(
            run.id,
            report={"action": "collect", "test_files": [{"path": "tests/test_explicit.py"}]},
            attempt_index=3,
        )
        service.store_smoke_report(
            run.id,
            report={"validator": "smoke", "status": "failed"},
            attempt_index=3,
        )
        service.store_validation_checkpoint(
            run.id,
            attempt_index=3,
            checkpoint={"workspace_snapshot_path": str(tmp_path / "checkpoint-explicit.tar.gz")},
        )
        session.commit()
        run_id = run.id
    finally:
        session.close()

    response = client.get(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()["run"]
    assert [attempt["attempt_index"] for attempt in payload["validation_attempts"]] == [3]
    assert payload["summary"]["latest_attempt_index"] == 3
    assert payload["validation_attempts"][0]["dockerfile_text"] == "FROM python:3.12-slim-bookworm\n"
    assert payload["validation_attempts"][0]["collect_report"]["test_files"][0]["path"] == "tests/test_explicit.py"


def test_stage2_api_can_filter_repositories_by_stage2_status(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-status-filter.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    pending_repo = _seed_repository(client, github_repo_id=9101, full_name="owner/pending-repo")
    failed_repo = _seed_repository(client, github_repo_id=9102, full_name="owner/failed-repo")
    succeeded_repo = _seed_repository(client, github_repo_id=9103, full_name="owner/succeeded-repo")

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                repository_id=failed_repo.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="deadbeef01",
            )
        )
        session.add(
            Stage2Run(
                repository_id=succeeded_repo.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.passed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="deadbeef02",
            )
        )
        session.commit()
    finally:
        session.close()

    failed_only = client.get("/api/stage2/repos?statuses=failed")
    assert failed_only.status_code == 200
    assert [row["full_name"] for row in failed_only.json()["repositories"]] == ["owner/failed-repo"]
    assert failed_only.json()["pagination"]["total"] == 1

    pending_only = client.get("/api/stage2/repos?statuses=pending")
    assert pending_only.status_code == 200
    assert [row["full_name"] for row in pending_only.json()["repositories"]] == ["owner/pending-repo"]
    assert pending_only.json()["pagination"]["total"] == 1

    mixed = client.get("/api/stage2/repos?statuses=succeeded&statuses=failed")
    assert mixed.status_code == 200
    assert {row["full_name"] for row in mixed.json()["repositories"]} == {"owner/failed-repo", "owner/succeeded-repo"}
    assert mixed.json()["pagination"]["total"] == 2


def test_stage2_api_can_sort_repositories_by_latest_operation_time(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-sort-latest-operation.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    no_run_repo = _seed_repository(client, github_repo_id=9201, full_name="owner/no-run-repo")
    older_repo = _seed_repository(client, github_repo_id=9202, full_name="owner/older-run-repo")
    newer_repo = _seed_repository(client, github_repo_id=9203, full_name="owner/newer-run-repo")

    session = client.app.state.session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    repository_id=older_repo.id,
                    status=Stage2RunStatus.completed.value,
                    result=Stage2RunResult.failed.value,
                    phase="completed",
                    target_branch="main",
                    target_commit_sha="deadbeef10",
                    created_at=datetime(2024, 1, 7, 0, 0, tzinfo=UTC),
                    updated_at=datetime(2024, 1, 7, 12, 0, tzinfo=UTC),
                ),
                Stage2Run(
                    repository_id=newer_repo.id,
                    status=Stage2RunStatus.completed.value,
                    result=Stage2RunResult.passed.value,
                    phase="completed",
                    target_branch="main",
                    target_commit_sha="deadbeef11",
                    created_at=datetime(2024, 1, 8, 0, 0, tzinfo=UTC),
                    updated_at=datetime(2024, 1, 8, 12, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    default_order = client.get("/api/stage2/repos")
    assert default_order.status_code == 200
    assert [row["full_name"] for row in default_order.json()["repositories"]] == [
        newer_repo.full_name,
        older_repo.full_name,
        no_run_repo.full_name,
    ]

    descending = client.get("/api/stage2/repos?sort_by=latest_operation_at&sort_order=desc")
    assert descending.status_code == 200
    assert [row["full_name"] for row in descending.json()["repositories"]] == [
        newer_repo.full_name,
        older_repo.full_name,
        no_run_repo.full_name,
    ]

    ascending = client.get("/api/stage2/repos?sort_by=latest_operation_at&sort_order=asc")
    assert ascending.status_code == 200
    assert [row["full_name"] for row in ascending.json()["repositories"]] == [
        older_repo.full_name,
        newer_repo.full_name,
        no_run_repo.full_name,
    ]


def test_stage2_api_detail_includes_archived_run_payloads(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-detail.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.failed.value,
            trigger_kind="rerun",
            phase="completed",
            target_branch="main",
            target_commit_sha="feedface1234",
            base_image="python:3.11-bookworm-builder",
            planner_model="openhands-stage2-host-planner",
            planner_token_usage=0,
            worker_model="openhands-stage2-container-worker",
            worker_token_usage=0,
            planner_guidance="use editable install",
            dockerfile_text="FROM python:3.11-slim-bookworm\n",
            run_script_text="#!/usr/bin/env bash\n",
            collect_report_json={"test_files": [{"path": "tests/test_alpha.py"}]},
            smoke_report_json={"feedback": {"code": "SMOKE_SAMPLE_BELOW_THRESHOLD"}},
            full_report_json={"summary": {"total_files": 1, "passed_files": 0, "failed_files": 1}},
            summary_json={"total_files": 1, "passed_files": 0, "failed_files": 1},
            error_message="sample tests failed",
            started_at=datetime(2024, 1, 7, 0, 0, 0, tzinfo=UTC),
            finished_at=datetime(2024, 1, 7, 0, 5, 0, tzinfo=UTC),
        )
        session.add(run)
        session.flush()
        workspace_dir = settings.stage2_workspace_dir / "runs" / run.id
        runtime_dir = settings.stage2_workspace_dir / "runtime" / run.id
        planner_archive_dir = runtime_dir / "llm-completions" / "planner"
        worker_archive_dir = runtime_dir / "llm-completions" / "worker"
        planner_archive_dir.mkdir(parents=True)
        worker_archive_dir.mkdir(parents=True)
        (planner_archive_dir / "planner-call-1.json").write_text('{"messages":[]}\n', encoding="utf-8")
        (worker_archive_dir / "worker-call-1.json").write_text('{"messages":[]}\n', encoding="utf-8")
        run.workspace_path = str(workspace_dir)
        session.add(
            Stage2RunEvent(
                run_id=run.id,
                actor="validator_smoke",
                phase="validator_smoke",
                title="Smoke validation failed for attempt 1",
                message="fewer than half of sampled test files passed",
                payload_json={"attempt_index": 1},
                created_at=datetime(2024, 1, 7, 0, 2, 0, tzinfo=UTC),
            )
        )
        session.add(
            Stage2TestResult(
                run_id=run.id,
                test_file_path="tests/test_alpha.py",
                status="failed",
                total_tests=3,
                passed_tests=1,
                failed_tests=2,
                error_tests=0,
                skipped_tests=0,
                exit_code=1,
                raw_result_json={"status": "failed"},
            )
        )
        session.commit()
    finally:
        session.close()

    repos = client.get("/api/stage2/repos")
    assert repos.status_code == 200
    repo_row = repos.json()["repositories"][0]
    assert repo_row["stage2"]["status"] == "failed"
    assert repo_row["stage2"]["can_rerun"] is True
    assert repo_row["stage2"]["latest_run"]["base_image"] == "python:3.11-bookworm-builder"
    assert repo_row["stage2"]["latest_run"]["display_status"] == "worker_failed"

    detail = client.get(f"/api/stage2/repos/{repository.id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    run_summary = detail_payload["runs"][0]
    assert run_summary["display_status"] == "worker_failed"
    assert "planner_guidance" not in run_summary
    assert run_summary["duration_seconds"] == 300.0

    run_detail = client.get(f"/api/stage2/repos/{repository.id}/runs/{run_summary['id']}")
    assert run_detail.status_code == 200
    run_payload = run_detail.json()["run"]
    assert run_payload["planner_guidance"] == "use editable install"
    assert run_payload["display_status"] == "worker_failed"
    assert run_payload["validation_attempts"] == []
    assert run_payload["draft_validation_attempt"]["label"] == "临时文件"
    assert run_payload["draft_validation_attempt"]["dockerfile_text"] == "FROM python:3.11-slim-bookworm\n"
    assert run_payload["collect_report"]["test_files"][0]["path"] == "tests/test_alpha.py"
    assert run_payload["events"][0]["title"] == "Smoke validation failed for attempt 1"
    assert run_payload["test_results"][0]["test_file_path"] == "tests/test_alpha.py"
    assert run_payload["llm_completion_archives"]["planner"]["file_count"] == 1
    assert run_payload["llm_completion_archives"]["planner"]["files"][0]["path"].endswith(
        "llm-completions/planner/planner-call-1.json"
    )
    assert run_payload["llm_completion_archives"]["worker"]["file_count"] == 1

    download = client.get(
        f"/api/stage2/repos/{repository.id}/runs/{run_summary['id']}/llm-completions/planner.zip"
    )
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.namelist() == ["planner/planner-call-1.json"]
        assert archive.read("planner/planner-call-1.json") == b'{"messages":[]}\n'

    preview = client.get(
        f"/api/stage2/repos/{repository.id}/runs/{run_summary['id']}/llm-completions/planner/files",
        params={"path": "llm-completions/planner/planner-call-1.json"},
    )
    assert preview.status_code == 200
    assert preview.json()["kind"] == "json"
    assert preview.json()["filename"] == "planner-call-1.json"
    assert preview.json()["value"] == {"messages": []}

    traversal = client.get(
        f"/api/stage2/repos/{repository.id}/runs/{run_summary['id']}/llm-completions/planner/files",
        params={"path": "../worker/worker-call-1.json"},
    )
    assert traversal.status_code == 400


def test_stage2_api_can_delete_archived_run_payloads(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-delete-run.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    run_id = "delete-run-1"
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    workspace_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (workspace_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    (runtime_dir / "trace.jsonl").write_text("{}", encoding="utf-8")
    removed_images: list[list[str]] = []

    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            id=run_id,
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.failed.value,
            phase="completed",
            target_branch="main",
            target_commit_sha="feedface1234",
            workspace_path=str(workspace_dir),
            planner_guidance="guidance",
        )
        session.add(run)
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=run_id,
                attempt_index=1,
                dockerfile_text="FROM python:3.11\n",
                run_script_text="#!/usr/bin/env bash\n",
                collect_report_json={"test_files": [{"path": "tests/test_alpha.py"}]},
                smoke_report_json={
                    "status": "failed",
                    "image_tag": "feature-factory-stage2:delete-run-1",
                },
                checkpoint_json={
                    "docker_image_ref": "feature-factory/stage2-worker-checkpoint:delete-run-1-attempt-001",
                },
                validator_status="failed",
            )
        )
        session.add(
            Stage2RunEvent(
                run_id=run_id,
                actor="planner",
                phase="planner",
                title="Planner finished",
                message="done",
                payload_json={},
            )
        )
        session.add(
            Stage2TestResult(
                run_id=run_id,
                test_file_path="tests/test_alpha.py",
                status="failed",
                total_tests=1,
                passed_tests=0,
                failed_tests=1,
                error_tests=0,
                skipped_tests=0,
                exit_code=1,
                raw_result_json={"status": "failed"},
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "feature_factory.server.subprocess.run",
        lambda command, **kwargs: removed_images.append(list(command))
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    response = client.delete(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_run_id"] == run_id
    assert payload["runs"] == []
    assert payload["selected_run"] is None
    assert payload["repository"]["stage2"]["status"] == "pending"
    assert payload["repository"]["stage2"]["history_count"] == 0
    assert not workspace_dir.exists()
    assert not runtime_dir.exists()
    assert removed_images == [
        [
            "docker",
            "image",
            "rm",
            "-f",
            "feature-factory/stage2-worker-checkpoint:delete-run-1-attempt-001",
        ],
        [
            "docker",
            "image",
            "rm",
            "-f",
            "feature-factory-stage2:delete-run-1",
        ],
    ]

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage2Run, run_id) is None
        assert session.scalar(select(Stage2ValidationAttempt).where(Stage2ValidationAttempt.run_id == run_id)) is None
        assert session.scalar(select(Stage2RunEvent).where(Stage2RunEvent.run_id == run_id)) is None
        assert session.scalar(select(Stage2TestResult).where(Stage2TestResult.run_id == run_id)) is None
        assert session.get(Stage2CleanupTombstone, run_id) is None
    finally:
        session.close()


def test_stage2_api_deletes_run_record_even_when_asset_cleanup_is_incomplete(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-delete-run-cleanup-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    run_id = "delete-run-cleanup-failure"
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    workspace_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (workspace_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    (runtime_dir / "trace.jsonl").write_text("{}", encoding="utf-8")

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface9999",
                workspace_path=str(workspace_dir),
                planner_guidance="guidance",
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        server_module,
        "_remove_paths",
        lambda paths, root_dir=None: [str(runtime_dir)],
    )
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: [],
    )

    response = client.delete(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_run_id"] == run_id
    assert (
        payload["cleanup_warnings"]["message"]
        == "stage2 run record was deleted, but some assets could not be removed"
    )
    assert payload["cleanup_warnings"]["failed_paths"] == [str(runtime_dir)]
    assert payload["cleanup_warnings"]["failed_checkpoint_docker_image_refs"] == []
    assert payload["cleanup_warnings"]["failed_validator_image_refs"] == []

    session = client.app.state.session_factory()
    try:
        run = session.get(Stage2Run, run_id)
        assert run is None
        tombstone = session.get(Stage2CleanupTombstone, run_id)
        assert tombstone is not None
        assert tombstone.status == "failed"
        assert tombstone.reason == "manual_delete"
        assert tombstone.archive_json["run_id"] == run_id
        assert tombstone.failure_json["failed_paths"] == [str(runtime_dir)]
    finally:
        session.close()


def test_stage2_startup_retries_cleanup_tombstones(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-delete-tombstone-retry.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    run_id = "delete-tombstone-retry"
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    workspace_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (workspace_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    (runtime_dir / "trace.jsonl").write_text("{}", encoding="utf-8")

    session = app.state.session_factory()
    try:
        session.add(
            Stage2CleanupTombstone(
                run_id=run_id,
                repository_id=123,
                reason="manual_delete",
                status="failed",
                archive_json={
                    "run_id": run_id,
                    "repository_id": 123,
                    "workspace_path": str(workspace_dir),
                    "checkpoint_docker_image_refs": [],
                    "validator_image_refs": [],
                },
                failure_json={"failed_paths": [str(runtime_dir)]},
                attempt_count=1,
            )
        )
        session.commit()
    finally:
        session.close()

    with TestClient(app):
        pass

    assert not workspace_dir.exists()
    assert not runtime_dir.exists()
    session = app.state.session_factory()
    try:
        assert session.get(Stage2CleanupTombstone, run_id) is None
    finally:
        session.close()


def test_stage2_startup_retries_terminal_validator_cleanup_tombstones_without_removing_run_paths(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-terminal-validator-tombstone.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    run_id = "terminal-validator-tombstone-retry"
    validator_image_ref = f"feature-factory-stage2:{run_id}"
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    workspace_dir.mkdir(parents=True)
    runtime_dir.mkdir(parents=True)
    (workspace_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    (runtime_dir / "trace.jsonl").write_text("{}", encoding="utf-8")

    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9002,
            full_name="owner/terminal-validator-cleanup",
            owner_login="owner",
            name="terminal-validator-cleanup",
            html_url="https://github.com/owner/terminal-validator-cleanup",
            api_url="https://api.github.com/repos/owner/terminal-validator-cleanup",
            description="stage2 repo",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=123,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.flush()
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="deadbeef0002",
                workspace_path=str(workspace_dir),
                smoke_report_json={"image_tag": validator_image_ref},
            )
        )
        session.add(
            Stage2CleanupTombstone(
                run_id=run_id,
                repository_id=repository.id,
                reason="terminal_validator_image_cleanup",
                status="failed",
                archive_json={
                    "run_id": run_id,
                    "repository_id": repository.id,
                    "workspace_path": "",
                    "checkpoint_docker_image_refs": [],
                    "validator_image_refs": [validator_image_ref],
                    "cleanup_paths": False,
                },
                failure_json={"failed_validator_image_refs": [validator_image_ref]},
                attempt_count=1,
            )
        )
        session.commit()
    finally:
        session.close()

    removed_path_batches: list[list[str]] = []
    removed_image_batches: list[list[str]] = []
    monkeypatch.setattr(
        server_module,
        "_remove_paths",
        lambda paths, root_dir=None: removed_path_batches.append(list(paths)) or [],
    )
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: removed_image_batches.append(list(image_refs)) or [],
    )

    with TestClient(app):
        pass

    assert workspace_dir.exists()
    assert runtime_dir.exists()
    assert removed_path_batches == [[]]
    non_empty_image_batches = [batch for batch in removed_image_batches if batch]
    assert non_empty_image_batches == [[validator_image_ref]]

    session = app.state.session_factory()
    try:
        assert session.get(Stage2CleanupTombstone, run_id) is None
        assert session.get(Stage2Run, run_id) is not None
    finally:
        session.close()


def test_stage2_api_rejects_deleting_active_run(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-delete-active-run.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    run_id = "active-run-1"

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.running.value,
                result=Stage2RunResult.unknown.value,
                phase="planner",
                target_branch="main",
                target_commit_sha="feedface1234",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.delete(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert response.status_code == 409
    assert "cannot delete an active stage2 run" in response.json()["detail"]
    session = client.app.state.session_factory()
    try:
        assert session.get(Stage2Run, run_id) is not None
    finally:
        session.close()


def test_stage2_api_can_interrupt_active_run(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-interrupt-run.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    run_id = "interrupt-run-1"

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.running.value,
                result=Stage2RunResult.unknown.value,
                phase="host_planner",
                target_branch="main",
                target_commit_sha="feedface1234",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{run_id}/interrupt")

    assert response.status_code == 200
    assert response.json()["interrupted_run_id"] == run_id
    assert stage2_runner.interrupted == [run_id]
    session = client.app.state.session_factory()
    try:
        event = session.scalar(select(Stage2RunEvent).where(Stage2RunEvent.run_id == run_id))
        assert event is not None
        assert event.title == "Stage2 interrupt requested"
    finally:
        session.close()


def test_stage2_api_reruns_exact_history_commit_even_if_previously_successful(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-rerun-history-commit.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-1"

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.passed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/rerun")

    assert response.status_code == 200
    payload = response.json()
    new_run_id = payload["created_run_id"]
    assert payload["source_run_id"] == source_run_id
    assert stage2_runner.scheduled == [new_run_id]
    assert payload["selected_run"]["id"] == new_run_id
    assert payload["selected_run"]["target_commit_sha"] == "feedface1234"
    assert payload["selected_run"]["trigger_kind"] == "rerun_commit"


def test_stage2_api_successful_run_blocks_same_remote_commit(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-successful-commit.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.passed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="abc123def456",
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")

    repos = client.get("/api/stage2/repos")
    assert repos.status_code == 200
    repo_row = repos.json()["repositories"][0]
    assert repo_row["stage2"]["status"] == "succeeded"
    assert repo_row["stage2"]["can_rerun"] is True
    assert repo_row["stage2"]["latest_run"]["display_status"] == "succeeded"

    response = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert response.status_code == 409
    assert "already successfully built" in response.json()["detail"]


def test_stage2_api_failed_run_does_not_block_same_remote_commit(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-failed-commit.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="abc123def456",
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")

    repos = client.get("/api/stage2/repos")
    assert repos.status_code == 200
    repo_row = repos.json()["repositories"][0]
    assert repo_row["stage2"]["status"] == "failed"
    assert repo_row["stage2"]["can_rerun"] is True

    response = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runs"][0]["target_commit_sha"] == "abc123def456"
    assert payload["runs"][0]["trigger_kind"] == "rerun"
    assert stage2_runner.scheduled == [payload["runs"][0]["id"]]


def test_stage2_api_rerun_worker_reuses_source_run_runtime_snapshot(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-rerun-worker.db'}",
        stage2_planner_llm_model="global-planner-model",
        stage2_planner_llm_base_url="https://global-planner.example.test/v1",
        stage2_planner_llm_api_key=SecretStr("global-planner-key"),
        stage2_worker_llm_model="global-worker-model",
        stage2_worker_llm_base_url="https://global-worker.example.test/v1",
        stage2_worker_llm_api_key=SecretStr("global-worker-key"),
    )
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-rerun-worker"
    source_runtime_snapshot = {
        "planner": {
            "model": "source-planner-model",
            "base_url": "https://source-planner.example.test/v1",
            "api_key": "source-planner-key",
            "api_key_preview": "source...",
            "preset": "default",
            "max_iterations": 111,
            "timeout_seconds": 1111.0,
        },
        "worker": {
            "model": "source-worker-model",
            "base_url": "https://source-worker.example.test/v1",
            "api_key": "source-worker-key",
            "api_key_preview": "worker-...",
            "preset": "gpt5",
            "max_iterations": 222,
            "timeout_seconds": 2222.0,
        },
        "hyperparameters": {
            "max_worker_attempts": 7,
            "quickcheck_sample_size": 9,
            "entry_file_test_count_min": 3,
            "collect_timeout_seconds": 210.0,
            "run_test_timeout_seconds": 240.0,
            "build_timeout_seconds": 510.0,
            "full_validation_timeout_seconds": 2400.0,
        },
    }
    source_workspace_dir = (settings.stage2_workspace_dir / "runs" / source_run_id).resolve()
    source_runtime_dir = (settings.stage2_workspace_dir / "runtime" / source_run_id).resolve()
    source_planner_archive_dir = source_runtime_dir / "llm-completions" / "planner"
    source_worker_archive_dir = source_runtime_dir / "llm-completions" / "worker"
    source_planner_archive_dir.mkdir(parents=True, exist_ok=True)
    source_worker_archive_dir.mkdir(parents=True, exist_ok=True)
    (source_planner_archive_dir / "planner-call-1.json").write_text(
        '{"messages":["planner-rerun"]}\n',
        encoding="utf-8",
    )
    (source_worker_archive_dir / "worker-call-1.json").write_text(
        '{"messages":["worker-rerun"]}\n',
        encoding="utf-8",
    )

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface9999",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Reuse the previous planner result and restart worker execution.",
                planner_model="source-planner-model",
                planner_token_usage=123,
                worker_model="source-worker-model",
                worker_token_usage=456,
                started_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
                finished_at=datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC),
                workspace_path=str(source_workspace_dir),
                runtime_snapshot_json=source_runtime_snapshot,
            )
        )
        session.commit()
    finally:
        session.close()

    detail = client.get(f"/api/stage2/repos/{repository.id}")
    assert detail.status_code == 200
    run_summary = detail.json()["runs"][0]
    assert run_summary["display_status"] == "worker_failed"
    assert run_summary["can_rerun_worker"] is True
    assert run_summary["can_resume_worker"] is False

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/rerun-worker")

    assert response.status_code == 200
    payload = response.json()
    new_run_id = payload["created_run_id"]
    assert payload["source_run_id"] == source_run_id
    assert stage2_runner.scheduled == [new_run_id]
    assert payload["selected_run"]["id"] == new_run_id
    assert payload["selected_run"]["trigger_kind"] == "rerun_worker"
    assert payload["selected_run"]["runtime_snapshot"]["planner"]["model"] == "source-planner-model"
    assert payload["selected_run"]["runtime_snapshot"]["planner"]["api_key_preview"] == "source..."
    assert "api_key" not in payload["selected_run"]["runtime_snapshot"]["planner"]
    assert payload["selected_run"]["runtime_snapshot"]["worker"]["model"] == "source-worker-model"
    assert payload["selected_run"]["runtime_snapshot"]["worker"]["api_key_preview"] == "worker-..."
    assert "api_key" not in payload["selected_run"]["runtime_snapshot"]["worker"]
    assert payload["selected_run"]["duration_seconds"] == 300.0
    assert payload["selected_run"]["token_usage_by_model"] == {
        "source-planner-model": 123,
        "source-worker-model": 456,
    }
    assert payload["selected_run"]["llm_completion_archives"]["planner"]["file_count"] == 1
    assert payload["selected_run"]["llm_completion_archives"]["planner"]["files"][0]["path"].endswith(
        "llm-completions/planner/planner-call-1.json"
    )
    assert payload["selected_run"]["llm_completion_archives"]["worker"]["file_count"] == 1
    assert payload["selected_run"]["llm_completion_archives"]["worker"]["files"][0]["path"].endswith(
        "llm-completions/worker/worker-call-1.json"
    )

    session = client.app.state.session_factory()
    try:
        created_run = Stage2Service(session).get_run(new_run_id)
        assert created_run.runtime_snapshot_json["planner"]["model"] == "source-planner-model"
        assert created_run.runtime_snapshot_json["planner"]["api_key"] == "source-planner-key"
        assert created_run.runtime_snapshot_json["worker"]["model"] == "source-worker-model"
        assert created_run.runtime_snapshot_json["worker"]["api_key"] == "source-worker-key"
        assert created_run.runtime_snapshot_json["resume_materialized"]["source_kind"] == "rerun_worker"
        assert created_run.runtime_snapshot_json["resume_materialized"]["source_run_id"] == source_run_id
        assert created_run.runtime_snapshot_json["hyperparameters"]["p2p_sample_seed"] == source_run_id
    finally:
        session.close()


def test_stage2_api_checkpoint_disabled_hides_worker_resume_actions(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-checkpoint-disabled.db'}",
        stage2_enable_checkpoints=False,
    )
    monkeypatch.setattr(stage2_service_module, "get_settings", lambda: settings)
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id="source-run-checkpoint-disabled",
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface9999",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Worker can normally be restarted from this planner result.",
            )
        )
        session.commit()
    finally:
        session.close()

    detail = client.get(f"/api/stage2/repos/{repository.id}")

    assert detail.status_code == 200
    run_summary = detail.json()["runs"][0]
    assert run_summary["can_rerun_worker"] is False
    assert run_summary["can_resume_worker"] is False
    assert run_summary["resume_kind"] is None

    resume_response = client.post(
        f"/api/stage2/repos/{repository.id}/runs/source-run-checkpoint-disabled/resume-worker"
    )
    assert resume_response.status_code == 409
    assert resume_response.json()["detail"] == "stage2 checkpoint resume is disabled"


def test_stage2_api_can_resume_worker_from_checkpoint(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-1"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint" / "conversations"
    source_conversation_dir = conversations_path / conversation_id.replace("-", "")
    source_conversation_dir.mkdir(parents=True, exist_ok=True)
    source_conversation_events_dir = source_conversation_dir / "events"
    source_conversation_events_dir.mkdir(parents=True, exist_ok=True)
    redacted_conversation_state = {
        "agent": {
            "llm": {
                "model": "old-worker-model",
                "base_url": "https://old-worker.example/v1",
                "api_key": "**********",
                "log_completions_folder": "/old/runtime/llm-completions/worker",
            },
            "condenser": {
                "llm": {
                    "model": "old-worker-model",
                    "base_url": "https://old-worker.example/v1",
                    "api_key": "**********",
                    "log_completions_folder": "/old/runtime/llm-completions/worker",
                }
            },
        }
    }
    for state_filename in ("base_state.json", "meta.json"):
        (source_conversation_dir / state_filename).write_text(
            json.dumps(redacted_conversation_state),
            encoding="utf-8",
        )
    stale_run_id = "older-lineage-run"
    stale_runtime_root = (settings.stage2_workspace_dir / "runtime" / stale_run_id).resolve()
    stale_workspace_root = (settings.stage2_workspace_dir / "runs" / stale_run_id).resolve()
    (source_conversation_events_dir / "event-00001.json").write_text(
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
    bash_events_dir = tmp_path / "worker-checkpoint" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    docker_tag_calls: list[list[str]] = []
    source_runtime_snapshot = {
        "planner": {
            "model": "source-planner-model",
            "base_url": "https://source-planner.example/v1",
            "api_key": "source-planner-key",
            "api_key_preview": "source...",
            "preset": "default",
            "max_iterations": 111,
            "timeout_seconds": 1111.0,
        },
        "worker": {
            "model": "source-worker-model",
            "base_url": "https://source-worker.example/v1",
            "api_key": "source-worker-key",
            "api_key_preview": "worker-...",
            "preset": "gpt5",
            "max_iterations": 222,
            "timeout_seconds": 2222.0,
        },
        "hyperparameters": {
            "max_worker_attempts": 7,
            "quickcheck_sample_size": 9,
            "entry_file_test_count_min": 3,
            "collect_timeout_seconds": 210.0,
            "run_test_timeout_seconds": 240.0,
            "build_timeout_seconds": 510.0,
            "full_validation_timeout_seconds": 2400.0,
        },
    }
    source_runtime_dir = (settings.stage2_workspace_dir / "runtime" / source_run_id).resolve()
    source_planner_archive_dir = source_runtime_dir / "llm-completions" / "planner"
    source_worker_archive_dir = source_runtime_dir / "llm-completions" / "worker"
    source_planner_archive_dir.mkdir(parents=True, exist_ok=True)
    source_worker_archive_dir.mkdir(parents=True, exist_ok=True)
    (source_planner_archive_dir / "planner-call-1.json").write_text(
        '{"messages":["planner"]}\n',
        encoding="utf-8",
    )
    (source_worker_archive_dir / "worker-call-1.json").write_text(
        '{"messages":["worker"]}\n',
        encoding="utf-8",
    )

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Keep the previous planner guidance and continue worker validation.",
                planner_model="source-planner-model",
                planner_token_usage=111,
                worker_model="source-worker-model",
                worker_token_usage=222,
                started_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                finished_at=datetime(2024, 1, 1, 0, 5, 0, tzinfo=UTC),
                runtime_snapshot_json=source_runtime_snapshot,
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                dockerfile_text="FROM python:3.11\n",
                run_script_text="#!/usr/bin/env bash\necho attempt1\n",
                smoke_report_json={"status": "failed", "attempt_index": 1},
                version_finalized=True,
            )
        )
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=2,
                validator_status="smoke_failed",
                dockerfile_text="FROM python:3.11\nRUN echo attempt2\n",
                run_script_text="#!/usr/bin/env bash\necho attempt2\n",
                smoke_report_json={"status": "failed", "attempt_index": 2},
                version_finalized=True,
                checkpoint_json={
                    "run_id": source_run_id,
                    "attempt_index": 2,
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "workspace_dir": str((settings.stage2_workspace_dir / "runs" / source_run_id).resolve()),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": (
                        "feature-factory/stage2-worker-checkpoint:"
                        "source-run-resume-1-attempt-002"
                    ),
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda image_ref: True)
    monkeypatch.setattr(
        "feature_factory.stage2.service.subprocess.run",
        lambda command, **kwargs: docker_tag_calls.append(list(command))
        or subprocess.CompletedProcess(command, 0, "", ""),
    )

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 200
    payload = response.json()
    new_run_id = payload["created_run_id"]
    expected_checkpoint_root = (
        settings.stage2_workspace_dir / "runtime" / new_run_id / "worker-checkpoint" / "current"
    ).resolve()
    assert payload["source_run_id"] == source_run_id
    assert stage2_runner.scheduled == [new_run_id]
    assert payload["selected_run"]["id"] == new_run_id
    assert payload["selected_run"]["trigger_kind"] == "resume_worker"
    assert payload["selected_run"]["target_commit_sha"] == "feedface1234"
    assert payload["selected_run"]["runtime_snapshot"]["resume_kind"] == "worker"
    assert "resume_materialized" not in payload["selected_run"]["runtime_snapshot"]
    assert payload["selected_run"]["runtime_snapshot"]["resume_source_run_id"] == source_run_id
    assert payload["selected_run"]["runtime_snapshot"]["resume_source_attempt_index"] == 2
    assert payload["selected_run"]["summary"]["latest_attempt_index"] == 2
    assert payload["selected_run"]["duration_seconds"] == 300.0
    assert payload["selected_run"]["token_usage_by_model"] == {
        "source-planner-model": 111,
        "source-worker-model": 222,
    }
    assert payload["selected_run"]["llm_completion_archives"]["planner"]["file_count"] == 1
    assert payload["selected_run"]["llm_completion_archives"]["planner"]["files"][0]["path"].endswith(
        "llm-completions/planner/planner-call-1.json"
    )
    assert payload["selected_run"]["llm_completion_archives"]["worker"]["file_count"] == 1
    assert payload["selected_run"]["llm_completion_archives"]["worker"]["files"][0]["path"].endswith(
        "llm-completions/worker/worker-call-1.json"
    )
    assert [attempt["attempt_index"] for attempt in payload["selected_run"]["validation_attempts"]] == [1, 2]
    assert payload["selected_run"]["validation_attempts"][0]["dockerfile_text"] == "FROM python:3.11\n"
    assert payload["selected_run"]["validation_attempts"][1]["run_script_text"] == (
        "#!/usr/bin/env bash\necho attempt2\n"
    )
    assert payload["selected_run"]["validation_attempts"][0]["checkpoint"] == {}
    assert payload["selected_run"]["validation_attempts"][1]["checkpoint"]["run_id"] == new_run_id
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["conversation_id"] == conversation_id
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["run_id"] == new_run_id
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["checkpoint_dir"] == str(expected_checkpoint_root)
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["workspace_snapshot_path"] == str(
        expected_checkpoint_root / "workspace_snapshot.tar.gz"
    )
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["workspace_dir"] == str(
        (settings.stage2_workspace_dir / "runs" / new_run_id).resolve()
    )
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["docker_image_ref"] == (
        f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-002"
    )
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["openhands"]["conversations_path"] == str(
        expected_checkpoint_root / "openhands" / "conversations"
    )
    assert payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["openhands"]["bash_events_dir"] == str(
        expected_checkpoint_root / "openhands" / "bash_events"
    )
    assert payload["selected_run"]["runtime_snapshot"]["planner"]["model"] == source_runtime_snapshot["planner"]["model"]
    assert payload["selected_run"]["runtime_snapshot"]["planner"]["base_url"] == source_runtime_snapshot["planner"]["base_url"]
    assert payload["selected_run"]["runtime_snapshot"]["planner"]["api_key_preview"] == "source..."
    assert "api_key" not in payload["selected_run"]["runtime_snapshot"]["planner"]
    assert payload["selected_run"]["runtime_snapshot"]["worker"]["model"] == source_runtime_snapshot["worker"]["model"]
    assert payload["selected_run"]["runtime_snapshot"]["worker"]["base_url"] == source_runtime_snapshot["worker"]["base_url"]
    assert payload["selected_run"]["runtime_snapshot"]["worker"]["api_key_preview"] == "worker-..."
    assert "api_key" not in payload["selected_run"]["runtime_snapshot"]["worker"]
    assert payload["selected_run"]["runtime_snapshot"]["hyperparameters"] == {
        **source_runtime_snapshot["hyperparameters"],
        "p2p_sample_seed": source_run_id,
    }
    assert docker_tag_calls == [
        [
            "docker",
            "tag",
            "feature-factory/stage2-worker-checkpoint:source-run-resume-1-attempt-002",
            f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-002",
        ]
    ]
    assert (expected_checkpoint_root / "workspace_snapshot.tar.gz").exists()
    cloned_conversation_dir = expected_checkpoint_root / "openhands" / "conversations" / conversation_id.replace("-", "")
    expected_completion_dir = (
        settings.stage2_workspace_dir / "runtime" / new_run_id / "llm-completions" / "worker"
    ).resolve()
    for state_filename in ("base_state.json", "meta.json"):
        cloned_state = json.loads((cloned_conversation_dir / state_filename).read_text(encoding="utf-8"))
        assert cloned_state["agent"]["llm"]["model"] == "source-worker-model"
        assert cloned_state["agent"]["llm"]["base_url"] == "https://source-worker.example/v1"
        assert cloned_state["agent"]["llm"]["api_key"] == "source-worker-key"
        assert cloned_state["agent"]["llm"]["log_completions"] is True
        assert cloned_state["agent"]["llm"]["log_completions_folder"] == str(expected_completion_dir)
        assert cloned_state["agent"]["condenser"]["llm"]["model"] == "source-worker-model"
        assert cloned_state["agent"]["condenser"]["llm"]["base_url"] == "https://source-worker.example/v1"
        assert cloned_state["agent"]["condenser"]["llm"]["api_key"] == "source-worker-key"
        assert cloned_state["agent"]["condenser"]["llm"]["log_completions"] is True
        assert cloned_state["agent"]["condenser"]["llm"]["log_completions_folder"] == str(expected_completion_dir)
    cloned_event = json.loads(
        (cloned_conversation_dir / "events" / "event-00001.json").read_text(encoding="utf-8")
    )
    assert cloned_event["observation"]["full_output_save_dir"] == str(
        (
            settings.stage2_workspace_dir
            / "runtime"
            / new_run_id
            / "openhands"
            / "worker"
            / "conversations"
            / conversation_id.replace("-", "")
            / "observations"
        ).resolve()
    )
    assert cloned_event["observation"]["metadata"]["working_dir"] == str(
        (settings.stage2_workspace_dir / "runs" / new_run_id / "repo").resolve()
    )
    assert (
        expected_checkpoint_root / "openhands" / "conversations" / conversation_id.replace("-", "")
    ).exists()
    assert (expected_checkpoint_root / "openhands" / "bash_events").exists()

    session = client.app.state.session_factory()
    try:
        service = Stage2Service(session)
        service.delete_repository_run(repository.id, source_run_id)
        session.commit()
    finally:
        session.close()

    after_delete = client.get(f"/api/stage2/repos/{repository.id}/runs/{new_run_id}")
    assert after_delete.status_code == 200
    after_delete_payload = after_delete.json()["run"]
    assert after_delete_payload["summary"]["latest_attempt_index"] == 2
    assert after_delete_payload["duration_seconds"] == 300.0
    assert after_delete_payload["token_usage_by_model"] == {
        "source-planner-model": 111,
        "source-worker-model": 222,
    }
    assert after_delete_payload["llm_completion_archives"]["planner"]["file_count"] == 1
    assert after_delete_payload["llm_completion_archives"]["worker"]["file_count"] == 1
    assert [attempt["attempt_index"] for attempt in after_delete_payload["validation_attempts"]] == [1, 2]
    assert after_delete_payload["validation_attempts"][0]["dockerfile_text"] == "FROM python:3.11\n"
    assert after_delete_payload["validation_attempts"][1]["checkpoint"]["run_id"] == new_run_id
    planner_archive_download = client.get(
        f"/api/stage2/repos/{repository.id}/runs/{new_run_id}/llm-completions/planner.zip"
    )
    assert planner_archive_download.status_code == 200
    with zipfile.ZipFile(io.BytesIO(planner_archive_download.content)) as archive:
        assert archive.namelist() == ["planner/planner-call-1.json"]
        assert archive.read("planner/planner-call-1.json") == b'{"messages":["planner"]}\n'

    session = client.app.state.session_factory()
    try:
        service = Stage2Service(session)
        resumed_run = service.get_run(new_run_id, include_detail=True)
        resumed_run.status = Stage2RunStatus.completed.value
        resumed_run.result = Stage2RunResult.failed.value
        resumed_run.phase = "completed"
        resumed_run.error_message = "worker agent crashed before attempt3"
        resumed_run.worker_model = "resume-worker-model"
        resumed_run.worker_token_usage = 333
        resumed_run.started_at = datetime(2024, 1, 1, 0, 5, 0, tzinfo=UTC)
        resumed_run.finished_at = datetime(2024, 1, 1, 0, 7, 0, tzinfo=UTC)
        session.commit()
    finally:
        session.close()

    session = client.app.state.session_factory()
    try:
        service = Stage2Service(session)
        resumed_run = service.get_run(new_run_id, include_detail=True)
        assert service.can_resume_worker_for_run(resumed_run) is True
        assert service.serialize_run_card_summary(resumed_run)["display_status"] == "worker_failed_before_attempt3"
    finally:
        session.close()

    nested_response = client.post(f"/api/stage2/repos/{repository.id}/runs/{new_run_id}/resume-worker")

    assert nested_response.status_code == 200
    nested_payload = nested_response.json()
    nested_run_id = nested_payload["created_run_id"]
    nested_checkpoint_root = (
        settings.stage2_workspace_dir / "runtime" / nested_run_id / "worker-checkpoint" / "current"
    ).resolve()
    assert nested_payload["source_run_id"] == new_run_id
    assert stage2_runner.scheduled == [new_run_id, nested_run_id]
    assert nested_payload["selected_run"]["id"] == nested_run_id
    assert nested_payload["selected_run"]["runtime_snapshot"]["resume_source_run_id"] == new_run_id
    assert nested_payload["selected_run"]["runtime_snapshot"]["resume_source_attempt_index"] == 2
    assert nested_payload["selected_run"]["summary"]["latest_attempt_index"] == 2
    assert nested_payload["selected_run"]["duration_seconds"] == 420.0
    assert nested_payload["selected_run"]["token_usage_by_model"] == {
        "resume-worker-model": 333,
        "source-planner-model": 111,
        "source-worker-model": 222,
    }
    assert nested_payload["selected_run"]["llm_completion_archives"]["planner"]["file_count"] == 1
    assert nested_payload["selected_run"]["llm_completion_archives"]["worker"]["file_count"] == 1
    assert [attempt["attempt_index"] for attempt in nested_payload["selected_run"]["validation_attempts"]] == [1, 2]
    assert nested_payload["selected_run"]["validation_attempts"][1]["checkpoint"]["run_id"] == nested_run_id
    assert nested_payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["run_id"] == nested_run_id
    assert nested_payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["checkpoint_dir"] == str(
        nested_checkpoint_root
    )
    assert nested_payload["selected_run"]["runtime_snapshot"]["resume_checkpoint"]["docker_image_ref"] == (
        f"feature-factory/stage2-worker-checkpoint:{nested_run_id}-attempt-002"
    )
    assert docker_tag_calls == [
        [
            "docker",
            "tag",
            "feature-factory/stage2-worker-checkpoint:source-run-resume-1-attempt-002",
            f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-002",
        ],
        [
            "docker",
            "tag",
            f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-002",
            f"feature-factory/stage2-worker-checkpoint:{nested_run_id}-attempt-002",
        ],
    ]


def test_stage2_api_can_resume_full_validation_from_smoke_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-full-validation.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-full-validation"
    workspace_snapshot_path = tmp_path / "full-validation-checkpoint" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface9999",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume host full validation from the smoke-passed checkpoint.",
                planner_model="openai/planner-model",
                worker_model="openai/worker-model",
                error_message="stage2 run interrupted before completion",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                dockerfile_text="FROM feature-factory/python:3.11-jammy-builder\n",
                run_script_text="#!/usr/bin/env bash\nprintf 'resume-full'\\n\n",
                collect_report_json={"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]},
                smoke_report_json={
                    "validator": "smoke",
                    "status": "passed",
                    "phase": "sample",
                    "sample_size": 1,
                    "passed_files": 1,
                },
                validator_status="smoke_completed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "validation": {
                        "result": "smoke_passed",
                        "phase": "sample",
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    detail = client.get(f"/api/stage2/repos/{repository.id}")
    assert detail.status_code == 200
    run_summary = detail.json()["runs"][0]
    assert run_summary["display_status"] == "full_test_error"
    assert run_summary["can_resume_worker"] is True
    assert run_summary["resume_kind"] == "full_validation"

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 200
    payload = response.json()
    new_run_id = payload["created_run_id"]
    assert stage2_runner.scheduled == [new_run_id]
    assert payload["selected_run"]["id"] == new_run_id
    assert payload["selected_run"]["trigger_kind"] == "resume_full_validation"
    assert payload["selected_run"]["runtime_snapshot"]["resume_kind"] == "full_validation"
    assert payload["selected_run"]["runtime_snapshot"]["resume_source_run_id"] == source_run_id
    assert payload["selected_run"]["runtime_snapshot"]["resume_source_attempt_index"] == 1
    assert payload["selected_run"]["collect_report"]["test_files"][0]["path"] == "tests/test_demo.py"
    assert payload["selected_run"]["smoke_report"]["validator"] == "smoke"
    assert payload["selected_run"]["validation_attempts"][0]["checkpoint"]["validation"]["result"] == "smoke_passed"


def test_stage2_api_resume_worker_requires_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker-conflict.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-conflict"

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Planner is ready but no checkpoint exists yet.",
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 409
    assert "reusable worker checkpoint" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_resume_worker_requires_saved_docker_commit_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker-docker-commit.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-no-docker-commit"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-no-docker-commit" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-no-docker-commit" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-no-docker-commit" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Planner is ready but this old checkpoint has no docker commit image.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 409
    assert "reusable worker checkpoint" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_resume_worker_requires_available_checkpoint_docker_image(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker-missing-image.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-missing-image"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-missing-image" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-missing-image" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-missing-image" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Planner is ready but the checkpoint image was pruned.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": "feature-factory/stage2-worker-checkpoint:missing-image-attempt-001",
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda image_ref: False)

    detail = client.get(f"/api/stage2/repos/{repository.id}")
    assert detail.status_code == 200
    run_summary = detail.json()["runs"][0]
    assert run_summary["can_resume_worker"] is False

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 409
    assert "reusable worker checkpoint" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_resume_worker_requires_checkpoint_conversation_dir(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker-missing-conversation.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-missing-conversation"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-missing-conversation" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-missing-conversation" / "conversations"
    conversations_path.mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-missing-conversation" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Planner is ready but the saved conversation directory is gone.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": "feature-factory/stage2-worker-checkpoint:missing-conversation-attempt-001",
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 409
    assert "reusable worker checkpoint" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_resume_worker_requires_checkpoint_bash_events_dir(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-resume-worker-missing-bash-events.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-missing-bash-events"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-missing-bash-events" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-missing-bash-events" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface1234",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Planner is ready but the saved bash events directory is gone.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": "feature-factory/stage2-worker-checkpoint:missing-bash-events-attempt-001",
                    "openhands": {
                        "conversations_path": str(conversations_path),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 409
    assert "reusable worker checkpoint" in response.json()["detail"]
    assert stage2_runner.scheduled == []


def test_stage2_api_failed_display_status_distinguishes_planner_and_worker(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-failure-display-status.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))

    session = client.app.state.session_factory()
    try:
        planner_repository = GitHubRepository(
            github_repo_id=9101,
            full_name="owner/planner-failed-repo",
            owner_login="owner",
            name="planner-failed-repo",
            html_url="https://github.com/owner/planner-failed-repo",
            api_url="https://api.github.com/repos/owner/planner-failed-repo",
            description="planner failed repo",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=10,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        worker_repository = GitHubRepository(
            github_repo_id=9102,
            full_name="owner/worker-failed-repo",
            owner_login="owner",
            name="worker-failed-repo",
            html_url="https://github.com/owner/worker-failed-repo",
            api_url="https://api.github.com/repos/owner/worker-failed-repo",
            description="worker failed repo",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=11,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add_all([planner_repository, worker_repository])
        session.flush()
        session.add(
            Stage2Run(
                repository_id=planner_repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                error_message="planner crashed",
            )
        )
        session.add(
            Stage2Run(
                repository_id=worker_repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                base_image="python:3.11-jammy-builder",
                planner_guidance="worker should continue from here",
                error_message="worker crashed before first validate",
            )
        )
        session.commit()
    finally:
        session.close()

    repos = client.get("/api/stage2/repos")

    assert repos.status_code == 200
    rows_by_name = {row["full_name"]: row for row in repos.json()["repositories"]}
    assert rows_by_name["owner/planner-failed-repo"]["stage2"]["latest_run"]["display_status"] == "planner_failed"
    assert rows_by_name["owner/worker-failed-repo"]["stage2"]["latest_run"]["display_status"] == "worker_failed"


def test_stage2_api_recovered_queued_run_displays_as_interrupted(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-recovered-queued.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    session = client.app.state.session_factory()
    try:
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.queued.value,
            result=Stage2RunResult.unknown.value,
            phase="queued",
            target_branch="main",
            target_commit_sha="deadbeef1234",
        )
        session.add(run)
        session.flush()
        run_id = run.id

        recovered = Stage2Service(session).recover_interrupted_runs()
        session.commit()
    finally:
        session.close()

    assert recovered == 1

    detail = client.get(f"/api/stage2/repos/{repository.id}/runs/{run_id}")

    assert detail.status_code == 200
    run_payload = detail.json()["run"]
    assert run_payload["status"] == Stage2RunStatus.completed.value
    assert run_payload["result"] == Stage2RunResult.failed.value
    assert run_payload["display_status"] == "interrupted"
    assert run_payload["error_message"] == "stage2 run interrupted before execution"
    assert run_payload["events"][0]["title"] == "Stage2 run recovered as interrupted"
    assert run_payload["events"][0]["payload"]["previous_status"] == Stage2RunStatus.queued.value


def test_stage2_observer_mode_preserves_active_run_and_blocks_mutations(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-observer.db'}",
        server_observer_mode=True,
    )
    app = create_app(
        settings,
        runner=NoopStage1Runner(),
        stage2_runner=DummyStage2Runner(),
    )
    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9990,
            full_name="owner/stage2-observer",
            owner_login="owner",
            name="stage2-observer",
            html_url="https://github.com/owner/stage2-observer",
            api_url="https://api.github.com/repos/owner/stage2-observer",
            description="stage2 observer",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            phase="worker",
            target_branch="main",
            target_commit_sha="deadbeefobserver",
        )
        session.add(run)
        session.commit()
        repository_id = repository.id
        run_id = run.id
    finally:
        session.close()

    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["observer_mode"] is True

        detail = client.get(
            f"/api/stage2/repos/{repository_id}/runs/{run_id}"
        )
        assert detail.status_code == 200
        assert detail.json()["run"]["status"] == Stage2RunStatus.running.value

        mutation = client.post(
            f"/api/stage2/repos/{repository_id}/runs/{run_id}/interrupt"
        )
        assert mutation.status_code == 403
        assert "observer mode" in mutation.json()["detail"]

    session = app.state.session_factory()
    try:
        preserved = session.get(Stage2Run, run_id)
        assert preserved is not None
        assert preserved.status == Stage2RunStatus.running.value
        assert preserved.result == Stage2RunResult.unknown.value
        assert preserved.error_message is None
    finally:
        session.close()


def test_stage2_startup_recovers_interrupted_runs_without_deleting_resume_assets(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-startup-recovery.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9991,
            full_name="owner/stage2-startup-recovery",
            owner_login="owner",
            name="stage2-startup-recovery",
            html_url="https://github.com/owner/stage2-startup-recovery",
            api_url="https://api.github.com/repos/owner/stage2-startup-recovery",
            description="stage2 startup recovery",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    run_id = "startup-recovery-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    workspace_dir = workspace_root / "runs" / run_id
    runtime_dir = workspace_root / "runtime" / run_id
    workspace_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (workspace_dir / "repo").mkdir()
    (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    checkpoint_root = runtime_dir / "worker-checkpoint" / "current"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    (checkpoint_root / "openhands" / "conversations" / conversation_id.replace("-", "")).mkdir(
        parents=True,
        exist_ok=True,
    )
    (checkpoint_root / "openhands" / "bash_events").mkdir(parents=True, exist_ok=True)
    (checkpoint_root / "workspace_snapshot.tar.gz").write_bytes(b"checkpoint")
    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda _image_ref: True)
    removed_images: list[list[str]] = []
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: removed_images.append(list(image_refs)) or [],
    )

    session = app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.running.value,
                result=Stage2RunResult.unknown.value,
                phase="worker",
                target_branch="main",
                target_commit_sha="deadbeef9999",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume from the latest worker checkpoint.",
                workspace_path=str(workspace_dir),
                smoke_report_json={"image_tag": f"feature-factory-stage2:{run_id}"},
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=run_id,
                attempt_index=1,
                smoke_report_json={"image_tag": f"feature-factory-stage2:{run_id}"},
                checkpoint_json={
                    "checkpoint_dir": str(checkpoint_root),
                    "workspace_snapshot_path": str(checkpoint_root / "workspace_snapshot.tar.gz"),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": "feature-factory/stage2-worker-checkpoint:startup-recovery-run-attempt-001",
                    "openhands": {
                        "conversations_path": str(checkpoint_root / "openhands" / "conversations"),
                        "bash_events_dir": str(checkpoint_root / "openhands" / "bash_events"),
                    },
                    "validation": {"result": "smoke_failed"},
                },
            )
        )
        session.commit()
    finally:
        session.close()

    cleanup_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        server_module,
        "_cleanup_stage2_run_archive",
        lambda settings_obj, archive: cleanup_calls.append(dict(archive)),
    )

    with TestClient(app):
        pass

    assert cleanup_calls == []
    assert workspace_dir.exists()
    assert not (workspace_dir / "repo").exists()
    assert runtime_dir.exists()
    assert (runtime_dir / "events.jsonl").exists()
    assert (checkpoint_root / "workspace_snapshot.tar.gz").exists()
    assert removed_images == [[f"feature-factory-stage2:{run_id}"]]

    session = app.state.session_factory()
    try:
        service = Stage2Service(session)
        recovered_run = service.get_run(run_id)
        assert recovered_run.status == Stage2RunStatus.completed.value
        assert recovered_run.result == Stage2RunResult.failed.value
        assert recovered_run.error_message == "stage2 run interrupted before completion"
        assert service.can_resume_worker_for_run(recovered_run) is True
    finally:
        session.close()


def test_stage2_startup_janitor_removes_orphaned_agent_server_containers(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-startup-janitor.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    monkeypatch.setattr(server_module, "uuid4", lambda: SimpleNamespace(hex="current-app-instance"))
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())

    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9998,
            full_name="owner/stage2-startup-janitor",
            owner_login="owner",
            name="stage2-startup-janitor",
            html_url="https://github.com/owner/stage2-startup-janitor",
            api_url="https://api.github.com/repos/owner/stage2-startup-janitor",
            description="stage2 startup janitor",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    active_run_id = "startup-janitor-active-run"
    terminal_run_id = "startup-janitor-terminal-run"
    session = app.state.session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id=active_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.running.value,
                    result=Stage2RunResult.unknown.value,
                    phase="worker",
                    target_branch="main",
                    target_commit_sha="deadbeef7777",
                ),
                Stage2Run(
                    id=terminal_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.completed.value,
                    result=Stage2RunResult.failed.value,
                    phase="completed",
                    target_branch="main",
                    target_commit_sha="deadbeef8888",
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    removed_container_ids: list[str] = []
    managed_container_ids = [
        "container-stale-active",
        "container-terminal-same-instance",
    ]
    original_subprocess_run = server_module.subprocess.run

    def fake_subprocess_run(command, *args, **kwargs):  # type: ignore[no-untyped-def]
        if command[:3] == ["docker", "ps", "-aq"]:
            return subprocess.CompletedProcess(command, 0, "\n".join(managed_container_ids), "")
        if command[:2] == ["docker", "inspect"]:
            container_id = command[2]
            run_id = active_run_id if container_id == "container-stale-active" else terminal_run_id
            app_instance_id = "old-app-instance" if container_id == "container-stale-active" else "current-app-instance"
            payload = [
                {
                    "Config": {
                        "Labels": {
                            server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED: "true",
                            server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE: "stage2",
                            server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT: "agent-server",
                            server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID: run_id,
                            server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_APP_INSTANCE_ID: app_instance_id,
                        }
                    }
                }
            ]
            return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[:3] == ["docker", "rm", "-f"]:
            removed_container_ids.append(command[3])
            return subprocess.CompletedProcess(command, 0, command[3], "")
        return original_subprocess_run(command, *args, **kwargs)

    monkeypatch.setattr(server_module.subprocess, "run", fake_subprocess_run)

    with TestClient(app):
        pass

    assert removed_container_ids == managed_container_ids


def test_startup_janitor_removes_orphaned_stage3_eval_containers(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage3-eval-startup-janitor.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage3_workspace_dir=tmp_path / "stage3",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    app = create_app(
        settings,
        runner=NoopStage1Runner(),
        stage2_runner=DummyStage2Runner(),
        stage3_runner=DummyLifecycleRunner(),
        stage4_runner=DummyLifecycleRunner(),
    )

    managed_eval_id = "container-managed-stage3-eval"
    legacy_eval_id = "container-legacy-stage3-eval"
    removed_container_ids: list[str] = []
    original_subprocess_run = server_module.subprocess.run

    def fake_subprocess_run(command, *args, **kwargs):  # type: ignore[no-untyped-def]
        if command[:3] == ["docker", "ps", "-aq"]:
            filters = [
                str(command[index + 1])
                for index, item in enumerate(command)
                if item == "--filter" and index + 1 < len(command)
            ]
            if (
                f"label={server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT}="
                f"{server_module.FEATURE_FACTORY_STAGE3_EVAL_COMPONENT}"
            ) in filters:
                return subprocess.CompletedProcess(command, 0, f"{managed_eval_id}\n", "")
            if f"name={server_module.FEATURE_FACTORY_STAGE3_EVAL_CONTAINER_NAME_PREFIX}" in filters:
                return subprocess.CompletedProcess(command, 0, f"{legacy_eval_id}\n", "")
            return subprocess.CompletedProcess(command, 0, "", "")
        if command[:2] == ["docker", "inspect"]:
            container_id = command[2]
            if container_id == managed_eval_id:
                payload = [
                    {
                        "Name": "/feature-factory-stage3-eval-managed",
                        "Config": {
                            "Labels": {
                                server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED: "true",
                                server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE: "stage3",
                                server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT: (
                                    server_module.FEATURE_FACTORY_STAGE3_EVAL_COMPONENT
                                ),
                                server_module.STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID: "missing-stage3-run",
                            }
                        },
                    }
                ]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
            if container_id == legacy_eval_id:
                payload = [
                    {
                        "Name": f"/{server_module.FEATURE_FACTORY_STAGE3_EVAL_CONTAINER_NAME_PREFIX}abc123",
                        "Config": {"Labels": {}},
                    }
                ]
                return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")
        if command[:3] == ["docker", "rm", "-f"]:
            removed_container_ids.append(command[3])
            return subprocess.CompletedProcess(command, 0, command[3], "")
        return original_subprocess_run(command, *args, **kwargs)

    monkeypatch.setattr(server_module.subprocess, "run", fake_subprocess_run)

    with TestClient(app):
        pass

    assert set(removed_container_ids) == {managed_eval_id, legacy_eval_id}


def test_stage2_startup_records_recovered_cleanup_failures(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-startup-recovery-cleanup-failures.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9992,
            full_name="owner/stage2-startup-recovery-cleanup-failures",
            owner_login="owner",
            name="stage2-startup-recovery-cleanup-failures",
            html_url="https://github.com/owner/stage2-startup-recovery-cleanup-failures",
            api_url="https://api.github.com/repos/owner/stage2-startup-recovery-cleanup-failures",
            description="stage2 startup recovery cleanup failure",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    run_id = "startup-recovery-cleanup-failure-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    workspace_dir = workspace_root / "runs" / run_id
    repo_dir = workspace_dir / "repo"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)

    validator_image_ref = f"feature-factory-stage2:{run_id}"
    failed_repo_path = str(repo_dir)
    monkeypatch.setattr(
        server_module,
        "_remove_paths",
        lambda paths, root_dir=None: [failed_repo_path],
    )
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: [validator_image_ref],
    )

    session = app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.running.value,
                result=Stage2RunResult.unknown.value,
                phase="worker",
                target_branch="main",
                target_commit_sha="deadbeef0001",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume from the latest worker checkpoint.",
                workspace_path=str(workspace_dir),
                smoke_report_json={"image_tag": validator_image_ref},
            )
        )
        session.commit()
    finally:
        session.close()

    with TestClient(app):
        pass

    session = app.state.session_factory()
    try:
        service = Stage2Service(session)
        recovered_run = service.get_run(run_id)
        matching_events = [
            event
            for event in recovered_run.events
            if event.title == "Recovered run asset cleanup incomplete"
        ]
        assert len(matching_events) == 1
        payload = dict(matching_events[0].payload_json or {})
        assert payload["failed_repo_checkout_paths"] == [failed_repo_path]
        assert payload["failed_validator_image_refs"] == [validator_image_ref]
    finally:
        session.close()


def test_stage2_recovered_run_cleanup_tombstone_survives_commit_cleanup_gap(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-recovered-cleanup-tombstone-gap.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    session = app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9997,
            full_name="owner/stage2-recovered-cleanup-tombstone-gap",
            owner_login="owner",
            name="stage2-recovered-cleanup-tombstone-gap",
            html_url="https://github.com/owner/stage2-recovered-cleanup-tombstone-gap",
            api_url="https://api.github.com/repos/owner/stage2-recovered-cleanup-tombstone-gap",
            description="stage2 recovered cleanup tombstone gap",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    run_id = "recovered-cleanup-gap-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    workspace_dir = workspace_root / "runs" / run_id
    repo_dir = workspace_dir / "repo"
    runtime_dir = workspace_root / "runtime" / run_id
    repo_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    validator_image_ref = f"feature-factory-stage2:{run_id}"
    removed_images: list[list[str]] = []
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: removed_images.append(list(image_refs)) or [],
    )

    session = app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.running.value,
                result=Stage2RunResult.unknown.value,
                phase="worker",
                target_branch="main",
                target_commit_sha="deadbeef2222",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume from the latest worker checkpoint.",
                workspace_path=str(workspace_dir),
                smoke_report_json={"image_tag": validator_image_ref},
            )
        )
        session.flush()
        service = Stage2Service(session)
        recovered_archives = service.recover_interrupted_runs(return_archives=True)
        assert len(recovered_archives) == 1
        session.commit()
    finally:
        session.close()

    session = app.state.session_factory()
    try:
        tombstone = session.get(Stage2CleanupTombstone, run_id)
        assert tombstone is not None
        assert tombstone.reason == "recovered_interrupted_run"
        assert tombstone.status == "pending"
        assert tombstone.archive_json["cleanup_scope"] == "recovered_run_assets"
    finally:
        session.close()

    with TestClient(app):
        pass

    assert not repo_dir.exists()
    assert runtime_dir.exists()
    assert removed_images == [[validator_image_ref]]

    session = app.state.session_factory()
    try:
        assert session.get(Stage2CleanupTombstone, run_id) is None
    finally:
        session.close()


def test_stage2_startup_repairs_duplicate_active_runs_and_cleans_stale_archives(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-duplicate-active-repair.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    import feature_factory.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9992,
            full_name="owner/stage2-duplicate-active-repair",
            owner_login="owner",
            name="stage2-duplicate-active-repair",
            html_url="https://github.com/owner/stage2-duplicate-active-repair",
            api_url="https://api.github.com/repos/owner/stage2-duplicate-active-repair",
            description="stage2 duplicate active repair",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    stale_run_id = "duplicate-active-stale-run"
    latest_run_id = "duplicate-active-latest-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    stale_workspace_dir = workspace_root / "runs" / stale_run_id
    stale_runtime_dir = workspace_root / "runtime" / stale_run_id
    latest_workspace_dir = workspace_root / "runs" / latest_run_id
    latest_runtime_dir = workspace_root / "runtime" / latest_run_id
    for directory in (
        stale_workspace_dir,
        stale_runtime_dir,
        latest_workspace_dir,
        latest_runtime_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (stale_workspace_dir / "repo").mkdir()
    (latest_workspace_dir / "repo").mkdir()
    (stale_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (latest_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

    session = session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id=stale_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.running.value,
                    result=Stage2RunResult.unknown.value,
                    phase="worker",
                    target_branch="main",
                    target_commit_sha="deadbeef1111",
                    workspace_path=str(stale_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 0, 0, tzinfo=UTC),
                ),
                Stage2Run(
                    id=latest_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.running.value,
                    result=Stage2RunResult.unknown.value,
                    phase="worker",
                    target_branch="main",
                    target_commit_sha="deadbeef2222",
                    workspace_path=str(latest_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 1, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    with TestClient(app):
        pass

    assert not stale_workspace_dir.exists()
    assert not stale_runtime_dir.exists()
    assert latest_workspace_dir.exists()
    assert latest_runtime_dir.exists()

    session = app.state.session_factory()
    try:
        service = Stage2Service(session)
        stale_run = service.get_run(stale_run_id)
        latest_run = service.get_run(latest_run_id)
        assert stale_run.status == Stage2RunStatus.completed.value
        assert stale_run.result == Stage2RunResult.failed.value
        assert (
            stale_run.error_message
            == "stage2 run invalidated while restoring the single-active-run invariant"
        )
        assert latest_run.status == Stage2RunStatus.completed.value
        assert latest_run.result == Stage2RunResult.failed.value
        assert latest_run.error_message == "stage2 run interrupted before completion"
    finally:
        session.close()


def test_stage2_startup_duplicate_active_repair_prefers_running_over_newer_queued(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-duplicate-active-priority.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    import feature_factory.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9994,
            full_name="owner/stage2-duplicate-active-priority",
            owner_login="owner",
            name="stage2-duplicate-active-priority",
            html_url="https://github.com/owner/stage2-duplicate-active-priority",
            api_url="https://api.github.com/repos/owner/stage2-duplicate-active-priority",
            description="stage2 duplicate active priority",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    running_run_id = "duplicate-priority-running"
    queued_run_id = "duplicate-priority-queued"
    workspace_root = Path(settings.stage2_workspace_dir)
    running_workspace_dir = workspace_root / "runs" / running_run_id
    running_runtime_dir = workspace_root / "runtime" / running_run_id
    queued_workspace_dir = workspace_root / "runs" / queued_run_id
    queued_runtime_dir = workspace_root / "runtime" / queued_run_id
    for directory in (
        running_workspace_dir,
        running_runtime_dir,
        queued_workspace_dir,
        queued_runtime_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (running_workspace_dir / "repo").mkdir()
    (queued_workspace_dir / "repo").mkdir()
    (running_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (queued_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

    session = session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id=running_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.running.value,
                    result=Stage2RunResult.unknown.value,
                    phase="worker",
                    target_branch="main",
                    target_commit_sha="deadbeef5555",
                    workspace_path=str(running_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 0, 0, tzinfo=UTC),
                ),
                Stage2Run(
                    id=queued_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.queued.value,
                    result=Stage2RunResult.unknown.value,
                    phase="queued",
                    target_branch="main",
                    target_commit_sha="deadbeef6666",
                    workspace_path=str(queued_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 1, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    app = create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())
    with TestClient(app):
        pass

    assert running_workspace_dir.exists()
    assert running_runtime_dir.exists()
    assert not queued_workspace_dir.exists()
    assert not queued_runtime_dir.exists()

    session = app.state.session_factory()
    try:
        service = Stage2Service(session)
        running_run = service.get_run(running_run_id)
        queued_run = service.get_run(queued_run_id)
        assert running_run.status == Stage2RunStatus.completed.value
        assert running_run.result == Stage2RunResult.failed.value
        assert running_run.error_message == "stage2 run interrupted before completion"
        assert queued_run.status == Stage2RunStatus.completed.value
        assert queued_run.result == Stage2RunResult.failed.value
        assert (
            queued_run.error_message
            == "stage2 run invalidated while restoring the single-active-run invariant"
        )
    finally:
        session.close()


def test_stage2_create_app_cleans_repaired_duplicate_archives_before_lifespan(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-duplicate-active-prelifespan.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    import feature_factory.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9993,
            full_name="owner/stage2-prelifespan-cleanup",
            owner_login="owner",
            name="stage2-prelifespan-cleanup",
            html_url="https://github.com/owner/stage2-prelifespan-cleanup",
            api_url="https://api.github.com/repos/owner/stage2-prelifespan-cleanup",
            description="stage2 pre-lifespan cleanup",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    stale_run_id = "duplicate-prelifespan-stale-run"
    latest_run_id = "duplicate-prelifespan-latest-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    stale_workspace_dir = workspace_root / "runs" / stale_run_id
    stale_runtime_dir = workspace_root / "runtime" / stale_run_id
    latest_workspace_dir = workspace_root / "runs" / latest_run_id
    latest_runtime_dir = workspace_root / "runtime" / latest_run_id
    for directory in (
        stale_workspace_dir,
        stale_runtime_dir,
        latest_workspace_dir,
        latest_runtime_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (stale_workspace_dir / "repo").mkdir()
    (latest_workspace_dir / "repo").mkdir()
    (stale_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (latest_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

    session = session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id=stale_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.queued.value,
                    result=Stage2RunResult.unknown.value,
                    phase="queued",
                    target_branch="main",
                    target_commit_sha="deadbeef3333",
                    workspace_path=str(stale_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 0, 0, tzinfo=UTC),
                ),
                Stage2Run(
                    id=latest_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.queued.value,
                    result=Stage2RunResult.unknown.value,
                    phase="queued",
                    target_branch="main",
                    target_commit_sha="deadbeef4444",
                    workspace_path=str(latest_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 1, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        server_module,
        "GitHubTokenScheduler",
        lambda _settings: (_ for _ in ()).throw(RuntimeError("scheduler bootstrap failed")),
    )

    with pytest.raises(RuntimeError, match="scheduler bootstrap failed"):
        create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())

    assert not stale_workspace_dir.exists()
    assert not stale_runtime_dir.exists()
    assert latest_workspace_dir.exists()
    assert latest_runtime_dir.exists()


def test_stage2_create_app_cleans_duplicate_archives_when_init_db_fails_after_repair(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-duplicate-active-init-db-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    import feature_factory.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9996,
            full_name="owner/stage2-init-db-failure-cleanup",
            owner_login="owner",
            name="stage2-init-db-failure-cleanup",
            html_url="https://github.com/owner/stage2-init-db-failure-cleanup",
            api_url="https://api.github.com/repos/owner/stage2-init-db-failure-cleanup",
            description="stage2 init-db failure cleanup",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=1,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(repository)
        session.commit()
        session.refresh(repository)
    finally:
        session.close()

    stale_run_id = "duplicate-init-db-failure-stale-run"
    latest_run_id = "duplicate-init-db-failure-latest-run"
    workspace_root = Path(settings.stage2_workspace_dir)
    stale_workspace_dir = workspace_root / "runs" / stale_run_id
    stale_runtime_dir = workspace_root / "runtime" / stale_run_id
    latest_workspace_dir = workspace_root / "runs" / latest_run_id
    latest_runtime_dir = workspace_root / "runtime" / latest_run_id
    for directory in (
        stale_workspace_dir,
        stale_runtime_dir,
        latest_workspace_dir,
        latest_runtime_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (stale_workspace_dir / "repo").mkdir()
    (latest_workspace_dir / "repo").mkdir()
    (stale_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")
    (latest_runtime_dir / "events.jsonl").write_text("", encoding="utf-8")

    session = session_factory()
    try:
        session.add_all(
            [
                Stage2Run(
                    id=stale_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.queued.value,
                    result=Stage2RunResult.unknown.value,
                    phase="queued",
                    target_branch="main",
                    target_commit_sha="deadbeef7777",
                    workspace_path=str(stale_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 0, 0, tzinfo=UTC),
                ),
                Stage2Run(
                    id=latest_run_id,
                    repository_id=repository.id,
                    status=Stage2RunStatus.queued.value,
                    result=Stage2RunResult.unknown.value,
                    phase="queued",
                    target_branch="main",
                    target_commit_sha="deadbeef8888",
                    workspace_path=str(latest_workspace_dir),
                    created_at=datetime(2024, 1, 7, 0, 1, 0, tzinfo=UTC),
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def fail_optional_indexes(_engine):
        raise RuntimeError("index bootstrap failed")

    monkeypatch.setattr(db_module, "_ensure_optional_schema_indexes", fail_optional_indexes)

    with pytest.raises(db_module.InitDbRepairCleanupError, match="database initialization failed"):
        create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner())

    assert not stale_workspace_dir.exists()
    assert not stale_runtime_dir.exists()
    assert latest_workspace_dir.exists()
    assert latest_runtime_dir.exists()

    session = session_factory()
    try:
        assert session.get(Stage2CleanupTombstone, stale_run_id) is None
        stale_run = session.get(Stage2Run, stale_run_id)
        assert stale_run is not None
        assert stale_run.status == Stage2RunStatus.completed.value
        assert stale_run.result == Stage2RunResult.failed.value
    finally:
        session.close()


def test_stage2_api_marks_run_terminal_when_initial_schedule_fails(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-schedule-failure.db'}")
    stage2_runner = DummyStage2Runner()
    stage2_runner.schedule_run = lambda _run_id: False  # type: ignore[method-assign]
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")

    response = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert response.status_code == 503
    assert response.json()["detail"] == "failed to schedule stage2 run"

    session = client.app.state.session_factory()
    try:
        service = Stage2Service(session)
        runs = service.list_repository_runs(repository.id, include_detail=True)
        assert len(runs) == 1
        run = runs[0]
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.failed.value
        assert run.error_message == "failed to schedule stage2 run"
        assert service.serialize_run_card_summary(run)["display_status"] == "schedule_failed"
        assert service.active_run_for_repository(repository.id) is None
        assert [event.title for event in run.events][-1] == "Stage2 run scheduling failed"
    finally:
        session.close()


def test_stage2_api_resume_schedule_failure_cleans_cloned_worker_checkpoint(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-resume-schedule-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    stage2_runner = DummyStage2Runner()
    stage2_runner.schedule_run = lambda _run_id: False  # type: ignore[method-assign]
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-schedule-failure"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-source" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-source" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-source" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    docker_tag_calls: list[list[str]] = []
    docker_rm_calls: list[list[str]] = []

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface5678",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume worker execution from the saved checkpoint.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": (
                        "feature-factory/stage2-worker-checkpoint:"
                        "source-run-resume-schedule-failure-attempt-001"
                    ),
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda _image_ref: True)
    monkeypatch.setattr(
        "feature_factory.stage2.service._retag_checkpoint_image",
        lambda *, source_image_ref, destination_run_id, attempt_index: docker_tag_calls.append(
            [
                "docker",
                "tag",
                source_image_ref,
                f"feature-factory/stage2-worker-checkpoint:{destination_run_id}-attempt-{int(attempt_index):03d}",
            ]
        )
        or f"feature-factory/stage2-worker-checkpoint:{destination_run_id}-attempt-{int(attempt_index):03d}",
    )
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: docker_rm_calls.extend(
            [["docker", "image", "rm", "-f", image_ref] for image_ref in image_refs]
        )
        or [],
    )

    response = client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert response.status_code == 503
    assert response.json()["detail"] == "failed to schedule stage2 resume"
    assert stage2_runner.scheduled == []

    session = client.app.state.session_factory()
    try:
        service = Stage2Service(session)
        runs = service.list_repository_runs(repository.id, include_detail=True)
        assert len(runs) == 2
        new_run = next(run for run in runs if run.id != source_run_id)
        expected_checkpoint_root = (
            settings.stage2_workspace_dir / "runtime" / new_run.id / "worker-checkpoint" / "current"
        ).resolve()
        assert new_run.status == Stage2RunStatus.completed.value
        assert new_run.result == Stage2RunResult.failed.value
        assert service.serialize_run_card_summary(new_run)["display_status"] == "schedule_failed"
        assert dict(new_run.runtime_snapshot_json or {}).get("resume_checkpoint") == {}
        assert not expected_checkpoint_root.parent.exists()
        assert docker_tag_calls == [
            [
                "docker",
                "tag",
                (
                    "feature-factory/stage2-worker-checkpoint:"
                    "source-run-resume-schedule-failure-attempt-001"
                ),
                f"feature-factory/stage2-worker-checkpoint:{new_run.id}-attempt-001",
            ]
        ]
        assert docker_rm_calls == [
            [
                "docker",
                "image",
                "rm",
                "-f",
                f"feature-factory/stage2-worker-checkpoint:{new_run.id}-attempt-001",
            ]
        ]
        titles = [event.title for event in new_run.events]
        assert "Stage2 run scheduling failed" in titles
        assert "Worker checkpoint cleaned" in titles
    finally:
        session.close()


def test_stage2_api_resume_commit_failure_cleans_cloned_worker_checkpoint(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-resume-commit-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-commit-failure"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-source" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-source" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-source" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    destination_run_ids: list[str] = []
    docker_rm_calls: list[list[str]] = []

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface7777",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume worker execution from the saved checkpoint.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": (
                        "feature-factory/stage2-worker-checkpoint:"
                        "source-run-resume-commit-failure-attempt-001"
                    ),
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda _image_ref: True)
    monkeypatch.setattr(
        "feature_factory.stage2.service._retag_checkpoint_image",
        lambda *, source_image_ref, destination_run_id, attempt_index: destination_run_ids.append(
            destination_run_id
        )
        or f"feature-factory/stage2-worker-checkpoint:{destination_run_id}-attempt-{int(attempt_index):03d}",
    )
    monkeypatch.setattr(
        server_module,
        "_remove_docker_images",
        lambda image_refs: docker_rm_calls.extend(
            [["docker", "image", "rm", "-f", image_ref] for image_ref in image_refs]
        )
        or [],
    )

    original_commit = SQLAlchemySession.commit
    fail_next_commit = {"value": True}

    def fail_resume_commit_once(self):
        if fail_next_commit["value"]:
            fail_next_commit["value"] = False
            raise RuntimeError("resume commit failed")
        return original_commit(self)

    monkeypatch.setattr(SQLAlchemySession, "commit", fail_resume_commit_once)

    with pytest.raises(RuntimeError, match="resume commit failed"):
        client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert len(destination_run_ids) == 1
    new_run_id = destination_run_ids[0]
    assert not (
        settings.stage2_workspace_dir / "runtime" / new_run_id / "worker-checkpoint"
    ).exists()
    assert docker_rm_calls == [
        [
            "docker",
            "image",
            "rm",
            "-f",
            f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-001",
        ]
    ]


def test_stage2_api_resume_internal_prepare_failure_cleans_cloned_worker_checkpoint(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-resume-internal-prepare-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-internal-prepare-failure"
    conversation_id = "12345678-1234-5678-1234-567812345678"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-source-internal" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-source-internal" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-source-internal" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    destination_run_ids: list[str] = []
    docker_rm_calls: list[list[str]] = []

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface9999",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume worker execution from the saved checkpoint.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": (
                        "feature-factory/stage2-worker-checkpoint:"
                        "source-run-resume-internal-prepare-failure-attempt-001"
                    ),
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr("feature_factory.stage2.service._docker_image_exists", lambda _image_ref: True)
    monkeypatch.setattr(
        "feature_factory.stage2.service._retag_checkpoint_image",
        lambda *, source_image_ref, destination_run_id, attempt_index: destination_run_ids.append(
            destination_run_id
        )
        or f"feature-factory/stage2-worker-checkpoint:{destination_run_id}-attempt-{int(attempt_index):03d}",
    )
    monkeypatch.setattr(
        "feature_factory.stage2.service._remove_docker_images",
        lambda image_refs: docker_rm_calls.extend(
            [["docker", "image", "rm", "-f", image_ref] for image_ref in image_refs]
        )
        or [],
    )

    original_record_event = Stage2Service.record_event

    def fail_after_clone(self, run_id, *, actor, phase, title, message, payload):
        if title == "Worker resume prepared":
            raise RuntimeError("resume prepare event failed")
        return original_record_event(
            self,
            run_id,
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload=payload,
        )

    monkeypatch.setattr(Stage2Service, "record_event", fail_after_clone)

    with pytest.raises(RuntimeError, match="resume prepare event failed"):
        client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert len(destination_run_ids) == 1
    new_run_id = destination_run_ids[0]
    assert not (
        settings.stage2_workspace_dir / "runtime" / new_run_id / "worker-checkpoint"
    ).exists()
    assert docker_rm_calls == [
        [
            "docker",
            "image",
            "rm",
            "-f",
            f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-001",
        ]
    ]


def test_stage2_api_resume_internal_failure_cleans_cloned_worker_checkpoint(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-resume-internal-failure.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)
    source_run_id = "source-run-resume-internal-failure"
    conversation_id = "12345678-1234-5678-1234-567812345679"
    workspace_snapshot_path = tmp_path / "worker-checkpoint-source-internal" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    workspace_snapshot_path.write_bytes(b"checkpoint")
    conversations_path = tmp_path / "worker-checkpoint-source-internal" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-source-internal" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)
    destination_run_ids: list[str] = []
    removed_images: list[str] = []

    session = client.app.state.session_factory()
    try:
        session.add(
            Stage2Run(
                id=source_run_id,
                repository_id=repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.failed.value,
                phase="completed",
                target_branch="main",
                target_commit_sha="feedface7778",
                base_image="python:3.11-jammy-builder",
                planner_guidance="Resume worker execution from the saved checkpoint.",
            )
        )
        session.flush()
        session.add(
            Stage2ValidationAttempt(
                run_id=source_run_id,
                attempt_index=1,
                validator_status="smoke_failed",
                checkpoint_json={
                    "workspace_snapshot_path": str(workspace_snapshot_path),
                    "conversation_id": conversation_id,
                    "docker_commit_status": "saved",
                    "docker_image_ref": (
                        "feature-factory/stage2-worker-checkpoint:"
                        "source-run-resume-internal-failure-attempt-001"
                    ),
                    "openhands": {
                        "conversations_path": str(conversations_path),
                        "bash_events_dir": str(bash_events_dir),
                    },
                },
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(stage2_service_module, "_docker_image_exists", lambda _image_ref: True)
    monkeypatch.setattr(
        stage2_service_module,
        "_retag_checkpoint_image",
        lambda *, source_image_ref, destination_run_id, attempt_index: destination_run_ids.append(
            destination_run_id
        )
        or f"feature-factory/stage2-worker-checkpoint:{destination_run_id}-attempt-{int(attempt_index):03d}",
    )
    monkeypatch.setattr(
        stage2_service_module,
        "_remove_docker_images",
        lambda image_refs: removed_images.extend(list(image_refs)) or [],
    )

    original_record_event = Stage2Service.record_event
    record_event_calls = {"value": 0}

    def fail_second_record_event(self, *args, **kwargs):  # noqa: ANN001
        record_event_calls["value"] += 1
        if record_event_calls["value"] == 2:
            raise RuntimeError("resume post-clone failure")
        return original_record_event(self, *args, **kwargs)

    monkeypatch.setattr(Stage2Service, "record_event", fail_second_record_event)

    with pytest.raises(RuntimeError, match="resume post-clone failure"):
        client.post(f"/api/stage2/repos/{repository.id}/runs/{source_run_id}/resume-worker")

    assert len(destination_run_ids) == 1
    new_run_id = destination_run_ids[0]
    assert not (
        settings.stage2_workspace_dir / "runtime" / new_run_id / "worker-checkpoint"
    ).exists()
    assert removed_images == [
        f"feature-factory/stage2-worker-checkpoint:{new_run_id}-attempt-001"
    ]

    session = client.app.state.session_factory()
    try:
        assert session.get(Stage2Run, new_run_id) is None
    finally:
        session.close()


def test_stage2_api_rejects_duplicate_active_run_when_db_constraint_catches_race(
    monkeypatch, tmp_path
) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-active-run-invariant.db'}")
    stage2_runner = DummyStage2Runner()
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=stage2_runner))
    repository = _seed_repository(client)

    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "abc123def456")
    first = client.post(f"/api/stage2/repos/{repository.id}/runs")
    assert first.status_code == 200

    monkeypatch.setattr(Stage2Service, "active_run_for_repository", lambda self, _repository_id: None)
    monkeypatch.setattr(server_module, "resolve_repo_head_commit", lambda _repository: "fedcba654321")

    second = client.post(f"/api/stage2/repos/{repository.id}/runs")

    assert second.status_code == 409
    assert "already has an active stage2 run" in second.json()["detail"]

    session = client.app.state.session_factory()
    try:
        rows = list(
            session.scalars(
                select(Stage2Run).where(Stage2Run.repository_id == repository.id).order_by(Stage2Run.created_at.asc())
            )
        )
        active_rows = [
            row for row in rows if row.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value}
        ]
        assert len(active_rows) == 1
        assert active_rows[0].target_commit_sha == "abc123def456"
    finally:
        session.close()


def test_stage2_api_surfaces_abandoned_and_defect_terminal_statuses(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-terminal-statuses.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))

    session = client.app.state.session_factory()
    try:
        abandoned_repository = GitHubRepository(
            github_repo_id=9001,
            full_name="owner/stage2-repo-abandoned",
            owner_login="owner",
            name="stage2-repo-abandoned",
            html_url="https://github.com/owner/stage2-repo-abandoned",
            api_url="https://api.github.com/repos/owner/stage2-repo-abandoned",
            description="stage2 repo abandoned",
            primary_language="Python",
            default_branch="main",
            license_key="mit",
            stargazers_count=123,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        defect_repository = GitHubRepository(
            github_repo_id=9002,
            full_name="owner/stage2-repo-defect",
            owner_login="owner",
            name="stage2-repo-defect",
            html_url="https://github.com/owner/stage2-repo-defect",
            api_url="https://api.github.com/repos/owner/stage2-repo-defect",
            description="stage2 repo defect",
            primary_language="Rust",
            default_branch="main",
            license_key="mit",
            stargazers_count=456,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 5, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add(abandoned_repository)
        session.add(defect_repository)
        session.flush()
        session.add(
            Stage2Run(
                repository_id=abandoned_repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.abandoned.value,
                phase="completed",
                error_message="no coherent unit-test suite",
            )
        )
        defect_run = Stage2Run(
            repository_id=defect_repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.defect.value,
            phase="completed",
            error_message="missing suitable base image",
        )
        session.add(defect_run)
        session.flush()
        session.add(
            Stage2RunEvent(
                run_id=defect_run.id,
                actor="planner",
                phase="host_planner",
                title="Planner reported base image defect",
                message="missing suitable base image",
                payload_json={
                    "upd_dockerfile": "FROM rust:1.88-bookworm\nRUN rustc --version\n",
                },
            )
        )
        session.commit()
    finally:
        session.close()

    repos = client.get("/api/stage2/repos")

    assert repos.status_code == 200
    rows_by_name = {row["full_name"]: row for row in repos.json()["repositories"]}
    assert rows_by_name["owner/stage2-repo-abandoned"]["stage2"]["status"] == "abandoned"
    assert rows_by_name["owner/stage2-repo-abandoned"]["stage2"]["can_rerun"] is True
    assert rows_by_name["owner/stage2-repo-abandoned"]["stage2"]["latest_run"]["display_status"] == "abandoned"
    assert rows_by_name["owner/stage2-repo-defect"]["stage2"]["status"] == "defect"
    assert rows_by_name["owner/stage2-repo-defect"]["stage2"]["can_rerun"] is True
    assert rows_by_name["owner/stage2-repo-defect"]["stage2"]["latest_run"]["display_status"] == "defect"

    defect_run_id = rows_by_name["owner/stage2-repo-defect"]["stage2"]["latest_run"]["id"]
    defect_detail = client.get(f"/api/stage2/repos/{defect_repository.id}/runs/{defect_run_id}")
    assert defect_detail.status_code == 200
    assert (
        defect_detail.json()["run"]["planner_upd_dockerfile"]
        == "FROM rust:1.88-bookworm\nRUN rustc --version\n"
    )


def test_stage2_api_repository_events_stream_terminal_for_pending_repo(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-events.db'}")
    client = TestClient(create_app(settings, runner=NoopStage1Runner(), stage2_runner=DummyStage2Runner()))
    repository = _seed_repository(client)

    response = client.get(f"/api/stage2/repos/{repository.id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: terminal" in response.text
    assert repository.full_name in response.text
