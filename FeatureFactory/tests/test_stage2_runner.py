from __future__ import annotations

import subprocess
import stat
import tarfile
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest

from feature_factory.config import Settings
from feature_factory.db import build_engine, build_session_factory, init_db
from feature_factory.docker_mirrors import reset_github_proxy_queue
from feature_factory.models import GitHubRepository, Stage2CleanupTombstone, Stage2RunResult, Stage2RunStatus
from feature_factory.stage2.backend import OpenHandsStage2Backend, WorkerExecutionResult
from feature_factory.stage2.planner import PlannerDecision
from feature_factory.stage2.runner import (
    Stage2RunRunner,
    _is_retryable_git_remote_error,
    _parse_git_progress_line,
    _settings_with_runtime_snapshot,
    _temporary_git_remote_url,
)
from feature_factory.stage2.service import Stage2Service
from feature_factory.stage2.validator import Stage2ValidationInterrupted, ValidationOutcome


class StubPlannerBackend:
    backend_name = "stub-planner"
    selection_detail = "Using stub planner backend."

    def __init__(self) -> None:
        self.observed_workspace_entries: list[list[str]] = []
        self.observed_repo_heads: list[str] = []
        self.observed_repo_origins: list[str] = []

    def plan_repository(self, repo_path: Path, repository: GitHubRepository, *, workspace_dir: Path, emit_event=None) -> PlannerDecision:
        self.observed_workspace_entries.append(sorted(path.name for path in workspace_dir.iterdir()))
        self.observed_repo_heads.append(_git_stdout(repo_path, "rev-parse", "HEAD"))
        self.observed_repo_origins.append(_git_stdout(repo_path, "remote", "get-url", "origin"))
        if emit_event is not None:
            emit_event(
                type(
                    "PlannerEvent",
                    (),
                    {
                        "actor": "planner_agent",
                        "phase": "host_planner",
                        "title": "Planner inspected repository",
                        "message": f"Read checkout for {repository.full_name}",
                        "payload": {
                            "workspace_entries": sorted(path.name for path in workspace_dir.iterdir()),
                            "repo_head": _git_stdout(repo_path, "rev-parse", "HEAD"),
                        },
                    },
                )()
            )
        return PlannerDecision(
            status="ready",
            base_image="python:3.11-jammy-builder",
            base_image_ref="feature-factory/python:3.11-jammy-builder",
            planner_model="stub-planner-model",
            planner_token_usage=17,
            worker_model="stub-worker-model",
            worker_token_usage=0,
            guidance="Inspect pyproject.toml and pytest configuration before choosing install strategy.",
            base_image_catalog=[
                {
                    "image_id": "python:3.11-jammy-builder",
                    "image_ref": "feature-factory/python:3.11-jammy-builder",
                    "asset_path": "base_images/python-3.11-jammy.Dockerfile",
                }
            ],
        )

    def max_worker_attempts(self) -> int:
        return 3

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        if emit_event is not None:
            emit_event(
                type(
                    "WorkerEvent",
                    (),
                    {
                        "actor": "worker_agent",
                        "phase": "container_worker",
                        "title": "Validate full completed",
                        "message": f"Worker validated {repository.full_name}",
                        "payload": {},
                    },
                )()
            )
        return WorkerExecutionResult(
            title="Stub worker run",
            description="Worker generated and validated artifacts.",
            strategy_payload={},
            dockerfile_text="FROM feature-factory/python:3.11-jammy-builder\n",
            run_script_text="#!/usr/bin/env bash\nprintf 'ok\\n'\n",
            model="stub-worker-model",
            token_usage=23,
            token_usage_details={"prompt_tokens": 20, "completion_tokens": 3},
            validation_attempts=[
                {
                    "attempt_index": 1,
                    "dockerfile_text": "FROM feature-factory/python:3.11-jammy-builder\n",
                    "run_script_text": "#!/usr/bin/env bash\nprintf 'ok\\n'\n",
                    "collect_report": {
                        "action": "collect",
                        "test_files": [{"path": "tests/test_demo.py"}],
                    },
                    "smoke_report": {
                        "validator": "smoke",
                        "status": "passed",
                        "phase": "sample",
                        "collect": {
                            "payload": {
                                "action": "collect",
                                "test_files": [{"path": "tests/test_demo.py"}],
                            }
                        },
                        "sample_size": 1,
                        "passed_files": 1,
                    },
                    "full_report": {
                        "validator": "full",
                        "status": "passed",
                        "phase": "full",
                        "summary": {
                            "total_files": 1,
                            "passed_files": 1,
                            "failed_files": 0,
                            "total_tests": 2,
                            "passed_tests": 2,
                            "failed_tests": 0,
                            "error_tests": 0,
                            "skipped_tests": 0,
                        },
                        "file_results": [
                            {
                                "test_file_path": "tests/test_demo.py",
                                "returncode": 0,
                                "result": {
                                    "action": "run",
                                    "status": "passed",
                                    "summary": {
                                        "collected": 2,
                                        "passed": 2,
                                        "failed": 0,
                                        "errors": 0,
                                        "skipped": 0,
                                    },
                                },
                            }
                        ],
                    },
                    "phase": "full",
                    "result": "passed",
                }
            ],
            final_validation={
                "status": "passed",
                "phase": "full",
                "passed": True,
                "full_report": {
                    "validator": "full",
                    "status": "passed",
                    "phase": "full",
                    "summary": {
                        "total_files": 1,
                        "passed_files": 1,
                        "failed_files": 0,
                        "total_tests": 2,
                        "passed_tests": 2,
                        "failed_tests": 0,
                        "error_tests": 0,
                        "skipped_tests": 0,
                    },
                    "file_results": [
                        {
                            "test_file_path": "tests/test_demo.py",
                            "returncode": 0,
                            "result": {
                                "action": "run",
                                "status": "passed",
                                "summary": {
                                    "collected": 2,
                                    "passed": 2,
                                    "failed": 0,
                                    "errors": 0,
                                    "skipped": 0,
                                },
                            },
                        }
                    ],
                },
            },
        )


class StubLivePersistingPlannerBackend(StubPlannerBackend):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        result = super().run_worker_attempt(
            repo_path,
            repository,
            workspace_dir=workspace_dir,
            decision=decision,
            attempt_index=attempt_index,
            resume_checkpoint=resume_checkpoint,
            last_smoke_report=last_smoke_report,
            emit_event=emit_event,
        )
        run_id = workspace_dir.name
        session = self.session_factory()
        try:
            service = Stage2Service(session)
            service.store_artifacts(
                run_id,
                dockerfile_text=result.dockerfile_text,
                run_script_text=result.run_script_text,
            )
            service.store_collect_report(
                run_id,
                report=dict(result.validation_attempts[0]["collect_report"] or {}),
            )
            service.store_smoke_report(
                run_id,
                report=dict(result.validation_attempts[0]["smoke_report"] or {}),
            )
            service.store_full_report(
                run_id,
                report=dict((result.final_validation or {}).get("full_report") or {}),
            )
            session.commit()
        finally:
            session.close()
        return WorkerExecutionResult(
            title=result.title,
            description=result.description,
            strategy_payload=result.strategy_payload,
            dockerfile_text=result.dockerfile_text,
            run_script_text=result.run_script_text,
            model=result.model,
            token_usage=result.token_usage,
            token_usage_details=result.token_usage_details,
            validation_attempts=result.validation_attempts,
            final_validation=result.final_validation,
            validation_attempts_persisted_live=True,
        )


class StubAbandonedPlannerBackend:
    backend_name = "stub-planner"
    selection_detail = "Using stub planner backend."

    def plan_repository(self, repo_path: Path, repository: GitHubRepository, *, workspace_dir: Path, emit_event=None) -> PlannerDecision:
        return PlannerDecision(
            status="abandoned",
            base_image=None,
            base_image_ref=None,
            planner_model="stub-planner-model",
            planner_token_usage=9,
            worker_model="stub-worker-model",
            worker_token_usage=0,
            reason="No cohesive unit-test suite was found; only a few scattered smoke scripts exist.",
        )


class StubDefectPlannerBackend:
    backend_name = "stub-planner"
    selection_detail = "Using stub planner backend."

    def plan_repository(self, repo_path: Path, repository: GitHubRepository, *, workspace_dir: Path, emit_event=None) -> PlannerDecision:
        return PlannerDecision(
            status="defect",
            base_image=None,
            base_image_ref=None,
            planner_model="stub-planner-model",
            planner_token_usage=11,
            worker_model="stub-worker-model",
            worker_token_usage=0,
            reason="The repo looks feasible, but the current catalog does not provide a suitable Rust base image.",
            upd_dockerfile="FROM rust:1.88-bookworm\nRUN rustc --version\n",
        )


class StubDeferredFullPlannerBackend(StubPlannerBackend):
    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        smoke_report = {
            "validator": "smoke",
            "status": "passed",
            "phase": "sample",
            "collect": {
                "payload": {
                    "action": "collect",
                    "test_files": [{"path": "tests/test_demo.py"}],
                }
            },
            "sample_size": 1,
            "passed_files": 1,
        }
        return WorkerExecutionResult(
            title="Stub deferred full worker run",
            description="Worker generated artifacts and passed smoke validation.",
            strategy_payload={},
            dockerfile_text="FROM feature-factory/python:3.11-jammy-builder\n",
            run_script_text="#!/usr/bin/env bash\nprintf 'ok\\n'\n",
            model="stub-worker-model",
            token_usage=17,
            token_usage_details={"prompt_tokens": 14, "completion_tokens": 3},
            validation_attempts=[
                {
                    "attempt_index": 1,
                    "dockerfile_text": "FROM feature-factory/python:3.11-jammy-builder\n",
                    "run_script_text": "#!/usr/bin/env bash\nprintf 'ok\\n'\n",
                    "collect_report": {
                        "action": "collect",
                        "test_files": [{"path": "tests/test_demo.py"}],
                    },
                    "smoke_report": smoke_report,
                    "full_report": {},
                    "phase": "sample",
                    "result": "smoke_passed",
                }
            ],
            final_validation={
                "status": "smoke_passed",
                "phase": "sample",
                "passed": False,
                "collect_report": {
                    "action": "collect",
                    "test_files": [{"path": "tests/test_demo.py"}],
                },
                "smoke_report": smoke_report,
                "full_report": {},
            },
            post_agent_full_validation_required=True,
        )


class StubFallbackPersistPlannerBackend(StubPlannerBackend):
    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        return WorkerExecutionResult(
            title="Stub fallback-persist worker run",
            description="Worker generated artifacts but full validation still failed after host persistence fallback.",
            strategy_payload={"source": "fallback"},
            dockerfile_text="FROM feature-factory/python:3.11-jammy-builder\n",
            run_script_text="#!/usr/bin/env bash\nprintf 'ok\\n'\n",
            model="stub-worker-model",
            token_usage=31,
            token_usage_details={"prompt_tokens": 22, "completion_tokens": 9},
            validation_attempts=[
                {
                    "attempt_index": 1,
                    "dockerfile_text": "FROM feature-factory/python:3.11-jammy-builder\n",
                    "run_script_text": "#!/usr/bin/env bash\nprintf 'ok\\n'\n",
                    "collect_report": {
                        "action": "collect",
                        "test_files": [{"path": "tests/test_demo.py"}],
                    },
                    "smoke_report": {
                        "validator": "smoke",
                        "status": "passed",
                        "phase": "sample",
                    },
                    "full_report": {
                        "validator": "full",
                        "status": "failed",
                        "phase": "full",
                        "summary": {
                            "total_files": 1,
                            "passed_files": 0,
                            "failed_files": 1,
                            "total_tests": 2,
                            "passed_tests": 1,
                            "failed_tests": 1,
                            "error_tests": 0,
                            "skipped_tests": 0,
                        },
                    },
                    "checkpoint": {
                        "checkpoint_type": "worker_validate_cold_restore",
                        "attempt_index": 1,
                        "checkpoint_dir": "/tmp/checkpoint/current",
                        "workspace_snapshot_path": "/tmp/checkpoint/current/workspace_snapshot.tar.gz",
                        "docker_image_ref": "feature-factory/stage2-worker-checkpoint:test-attempt-001",
                        "docker_commit_status": "saved",
                    },
                    "phase": "full",
                    "result": "passed",
                }
            ],
            final_validation={
                "status": "failed",
                "phase": "full",
                "passed": False,
                "message": "one test failed after fallback persistence",
                "full_report": {
                    "validator": "full",
                    "status": "failed",
                    "phase": "full",
                    "summary": {
                        "total_files": 1,
                        "passed_files": 0,
                        "failed_files": 1,
                        "total_tests": 2,
                        "passed_tests": 1,
                        "failed_tests": 1,
                        "error_tests": 0,
                        "skipped_tests": 0,
                    },
                },
            },
            validation_attempts_persisted_live=False,
        )


class StubResumeFullValidationBackend(StubPlannerBackend):
    def plan_repository(self, repo_path: Path, repository: GitHubRepository, *, workspace_dir: Path, emit_event=None) -> PlannerDecision:  # noqa: ARG002
        raise AssertionError("plan_repository should not be called when resuming full validation")

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        raise AssertionError("run_worker_attempt should not be called when resuming full validation")


class BlockingShutdownBackend(StubPlannerBackend):
    def __init__(self) -> None:
        super().__init__()
        self.worker_started = Event()
        self.cancel_seen = Event()
        self.release = Event()

    def run_worker_attempt(
        self,
        repo_path: Path,
        repository: GitHubRepository,
        *,
        workspace_dir: Path,
        decision: PlannerDecision,
        attempt_index: int,
        resume_checkpoint=None,
        last_smoke_report=None,
        emit_event=None,
    ) -> WorkerExecutionResult:
        self.worker_started.set()
        self.release.wait(timeout=2.0)
        return super().run_worker_attempt(
            repo_path,
            repository,
            workspace_dir=workspace_dir,
            decision=decision,
            attempt_index=attempt_index,
            resume_checkpoint=resume_checkpoint,
            last_smoke_report=last_smoke_report,
            emit_event=emit_event,
        )

    def cancel_run(self, run_id: str) -> bool:  # noqa: ARG002
        self.cancel_seen.set()
        return True


def test_stage2_runtime_snapshot_applies_entry_file_filters() -> None:
    settings = Settings(
        stage2_entry_file_test_count_min=-1,
        stage2_p2p_file_count_limit=None,
        stage2_p2p_sample_seed=None,
    )

    resolved = _settings_with_runtime_snapshot(
        settings,
        {
            "hyperparameters": {
                "entry_file_test_count_min": 3,
                "p2p_file_count_limit": 12,
                "p2p_sample_seed": "runtime-p2p-seed",
            }
        },
    )
    assert resolved.stage2_entry_file_test_count_min == 3
    assert resolved.stage2_p2p_file_count_limit == 12
    assert resolved.stage2_p2p_sample_seed == "runtime-p2p-seed"

    unlimited = _settings_with_runtime_snapshot(
        Settings(stage2_p2p_file_count_limit=9, stage2_p2p_sample_seed="global-seed"),
        {
            "hyperparameters": {
                "entry_file_test_count_min": -1,
                "p2p_file_count_limit": None,
                "p2p_sample_seed": None,
            }
        },
    )
    assert unlimited.stage2_entry_file_test_count_min == -1
    assert unlimited.stage2_p2p_file_count_limit is None
    assert unlimited.stage2_p2p_sample_seed is None


def test_stage2_service_assigns_and_inherits_p2p_sample_seed(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-p2p-sample-seed.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-p2p-sample-seed"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("p2p sample seed\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")
    repository_id = _seed_repository(session_factory, remote_repo)

    session = session_factory()
    try:
        service = Stage2Service(session)
        source = service.create_run(
            repository_id,
            trigger_kind="manual",
            commit_resolver=lambda _repository: commit_sha,
            runtime_snapshot={"hyperparameters": {"p2p_file_count_limit": 20}},
        )
        source.status = Stage2RunStatus.completed.value
        source.result = Stage2RunResult.failed.value
        source.phase = "completed"
        source_seed = source.runtime_snapshot_json["hyperparameters"]["p2p_sample_seed"]
        assert source_seed == source.id
        session.flush()

        rerun = service.create_run_from_existing_commit(
            repository_id,
            source.id,
            runtime_snapshot={"hyperparameters": {"p2p_file_count_limit": 10}},
        )
        assert rerun.runtime_snapshot_json["hyperparameters"]["p2p_sample_seed"] == source_seed
    finally:
        session.close()


def test_stage2_runner_uses_repo_cache_and_keeps_workspace_minimal(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage2_planner_llm_api_key="planner-live-key",
        stage2_worker_llm_api_key="worker-live-key",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("version one\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_one = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_one_id = _create_run(session_factory, repository_id, target_commit_sha=commit_one)

    runner = Stage2RunRunner(session_factory, settings)
    backend = StubPlannerBackend()
    runner.backend = backend

    runner._run_future(run_one_id)

    cache_dir = settings.stage2_workspace_dir / "cache" / "repos" / "owner" / "demo-repo.git"
    workspace_one = settings.stage2_workspace_dir / "runs" / run_one_id
    assert cache_dir.exists()
    assert {path.name for path in workspace_one.iterdir()} == set()
    assert backend.observed_workspace_entries[0] == ["repo"]
    assert backend.observed_repo_heads[0] == commit_one
    assert backend.observed_repo_origins[0] == str(remote_repo)

    (remote_repo / "README.md").write_text("version two\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "second")
    commit_two = _git_stdout(remote_repo, "rev-parse", "HEAD")

    run_two_id = _create_run(session_factory, repository_id, target_commit_sha=commit_two)
    runner._run_future(run_two_id)

    workspace_two = settings.stage2_workspace_dir / "runs" / run_two_id
    assert {path.name for path in workspace_two.iterdir()} == set()
    assert backend.observed_workspace_entries[1] == ["repo"]
    assert backend.observed_repo_heads[1] == commit_two
    assert backend.observed_repo_origins[1] == str(remote_repo)
    assert _git_stdout(cache_dir, "rev-parse", "refs/heads/main", git_dir=True) == commit_two
    assert _git_config_value(cache_dir, "remote.origin.mirror", git_dir=True) is None
    assert _git_symbolic_ref(cache_dir, "HEAD", git_dir=True) == "refs/heads/main"

    session = session_factory()
    try:
        service = Stage2Service(session)
        first_run = service.get_run(run_one_id)
        second_run = service.get_run(run_two_id)
        first_titles = [event.title for event in first_run.events]
        second_titles = [event.title for event in second_run.events]
        assert first_run.status == Stage2RunStatus.completed.value
        assert first_run.result == Stage2RunResult.passed.value
        assert first_run.base_image == "python:3.11-jammy-builder"
        assert first_run.target_commit_sha == commit_one
        assert first_run.workspace_path == str(workspace_one)
        assert first_run.validation_attempts[0].full_report_json["validator"] == "full"
        assert first_run.runtime_snapshot_json["planner"]["timeout_seconds"] == 2400.0
        assert first_run.runtime_snapshot_json["planner"]["preset"] == "default"
        assert first_run.runtime_snapshot_json["planner"]["max_iterations"] == 150
        assert first_run.runtime_snapshot_json["planner"]["api_key"] == "planner-live-key"
        assert first_run.runtime_snapshot_json["planner"]["api_key_preview"] == "planne..."
        assert "Repository cache miss" in first_titles
        assert "Repository cache clone started" in first_titles
        assert "Repository cache synchronized" in first_titles
        assert "Repository checkout materializing" in first_titles
        assert "Target commit checkout started" in first_titles
        assert "Target commit checked out" in first_titles
        assert "Workspace repo cleanup started" in first_titles
        assert "Workspace repo cleaned" in first_titles
        assert "Worker validation passed" in first_titles

        assert second_run.status == Stage2RunStatus.completed.value
        assert second_run.result == Stage2RunResult.passed.value
        assert second_run.target_commit_sha == commit_two
        assert second_run.workspace_path == str(workspace_two)
        assert second_run.runtime_snapshot_json["worker"]["timeout_seconds"] == 2400.0
        assert second_run.runtime_snapshot_json["worker"]["api_key"] == "worker-live-key"
        assert second_run.runtime_snapshot_json["worker"]["api_key_preview"] == "worker..."
        assert "Repository cache hit" in second_titles
        assert "Repository cache refresh started" in second_titles
        assert "Repository cache synchronized" in second_titles
        assert "Workspace repo cleanup started" in second_titles
        assert "Workspace repo cleaned" in second_titles
    finally:
        session.close()


def test_stage2_runner_makes_checkout_container_writable_without_exposing_workspace_siblings(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-checkout-permissions.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    runner = Stage2RunRunner(session_factory, settings)
    repository = GitHubRepository(
        github_repo_id=9001,
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        api_url="https://api.github.com/repos/owner/demo-repo",
        description="demo repo",
        primary_language="Python",
        default_branch="main",
        license_key="mit",
        stargazers_count=42,
        created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
        pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
        discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
    )
    workspace_dir = tmp_path / "workspace"
    checkout_dir = workspace_dir / "repo"
    secret_file = workspace_dir / "secret.txt"
    workspace_dir.mkdir()
    secret_file.write_text("not for the agent\n", encoding="utf-8")
    secret_file.chmod(0o600)
    cache_dir = tmp_path / "cache" / "repo.git"

    def fake_materialize_checkout_from_cache(*_args, **_kwargs) -> None:
        nested_dir = checkout_dir / "package"
        nested_dir.mkdir(parents=True)
        source_file = nested_dir / "module.py"
        source_file.write_text("print('ok')\n", encoding="utf-8")
        checkout_dir.chmod(0o700)
        nested_dir.chmod(0o700)
        source_file.chmod(0o600)

    monkeypatch.setattr(runner, "_repository_cache_dir", lambda _repository: cache_dir)
    monkeypatch.setattr(runner, "_repo_cache_lock", lambda _cache_dir: nullcontext())
    monkeypatch.setattr(runner, "_sync_repository_cache", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_resolve_cached_target_commit", lambda *_args, **_kwargs: "abc123")
    monkeypatch.setattr(runner, "_materialize_checkout_from_cache", fake_materialize_checkout_from_cache)

    target_commit_sha, returned_cache_dir = runner._prepare_repository_checkout(
        repository,
        run_id="run-checkout-permissions",
        checkout_dir=checkout_dir,
        requested_commit_sha=None,
    )

    assert target_commit_sha == "abc123"
    assert returned_cache_dir == cache_dir
    assert stat.S_IMODE(checkout_dir.stat().st_mode) & stat.S_IWOTH
    assert stat.S_IMODE(checkout_dir.stat().st_mode) & stat.S_IXOTH
    assert stat.S_IMODE((checkout_dir / "package").stat().st_mode) & stat.S_IWOTH
    assert stat.S_IMODE((checkout_dir / "package" / "module.py").stat().st_mode) & stat.S_IWOTH
    assert not stat.S_IMODE(secret_file.stat().st_mode) & stat.S_IROTH
    assert not stat.S_IMODE(secret_file.stat().st_mode) & stat.S_IWOTH


def test_stage2_runner_marks_abandoned_planner_result_as_failed(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-abandoned.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-abandoned"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubAbandonedPlannerBackend()
    runner._run_future(run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.abandoned.value
        assert run.error_message == "No cohesive unit-test suite was found; only a few scattered smoke scripts exist."
        assert any(event.title == "Planner abandoned repository" for event in run.events)
        assert any(event.title == "Workspace repo cleaned" for event in run.events)
    finally:
        session.close()


def test_stage2_runner_marks_defect_planner_result_as_failed(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-defect.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-defect"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubDefectPlannerBackend()
    runner._run_future(run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.defect.value
        assert run.error_message == "The repo looks feasible, but the current catalog does not provide a suitable Rust base image."
        defect_event = next(event for event in run.events if event.title == "Planner reported base image defect")
        assert defect_event.payload_json["upd_dockerfile"] == "FROM rust:1.88-bookworm\nRUN rustc --version\n"
        assert any(event.title == "Workspace repo cleaned" for event in run.events)
    finally:
        session.close()


def test_stage2_runner_skips_duplicate_validation_attempt_persistence_when_backend_persists_live(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-live-persist.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-live-persist"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubLivePersistingPlannerBackend(session_factory)
    runner._run_future(run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
        assert len(run.validation_attempts) == 1
        attempt = run.validation_attempts[0]
        assert attempt.attempt_index == 1
        assert attempt.dockerfile_text == "FROM feature-factory/python:3.11-jammy-builder\n"
        assert attempt.collect_report_json["action"] == "collect"
        assert attempt.smoke_report_json["validator"] == "smoke"
        assert attempt.full_report_json["validator"] == "full"
        assert run.full_report_json["validator"] == "full"
    finally:
        session.close()


def test_stage2_runner_backfills_validation_attempts_when_live_persistence_is_unavailable(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-fallback-persist.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-fallback-persist"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubFallbackPersistPlannerBackend()
    runner._run_future(run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.failed.value
        assert run.dockerfile_text == "FROM feature-factory/python:3.11-jammy-builder\n"
        assert run.run_script_text == "#!/usr/bin/env bash\nprintf 'ok\\n'\n"
        assert len(run.validation_attempts) == 1
        attempt = run.validation_attempts[0]
        assert attempt.attempt_index == 1
        assert attempt.collect_report_json["action"] == "collect"
        assert attempt.smoke_report_json["validator"] == "smoke"
        assert attempt.full_report_json["validator"] == "full"
        assert attempt.checkpoint_json["docker_image_ref"] == (
            "feature-factory/stage2-worker-checkpoint:test-attempt-001"
        )
    finally:
        session.close()


def test_stage2_runner_continues_full_validation_after_worker_handoff(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-deferred-full.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-deferred-full"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    def fake_run_full(self, *, run_id, workspace_dir, cancel_requested=None):  # noqa: ARG001
        return ValidationOutcome(
            passed=True,
            report={
                "validator": "full",
                "status": "passed",
                "phase": "full",
                "summary": {
                    "total_files": 1,
                    "passed_files": 1,
                    "failed_files": 0,
                    "total_tests": 2,
                    "passed_tests": 2,
                    "failed_tests": 0,
                    "error_tests": 0,
                    "skipped_tests": 0,
                },
                "file_results": [
                    {
                        "test_file_path": "tests/test_demo.py",
                        "returncode": 0,
                        "result": {
                            "action": "run",
                            "status": "passed",
                            "summary": {
                                "collected": 2,
                                "passed": 2,
                                "failed": 0,
                                "errors": 0,
                                "skipped": 0,
                            },
                        },
                    }
                ],
            },
        )

    monkeypatch.setattr("feature_factory.stage2.runner.Stage2Validator.run_full", fake_run_full)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubDeferredFullPlannerBackend()
    runner._run_future(run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        titles = [event.title for event in run.events]
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
        assert run.phase == "completed"
        assert run.smoke_report_json["validator"] == "smoke"
        assert run.full_report_json["validator"] == "full"
        assert len(run.validation_attempts) == 1
        assert run.validation_attempts[0].smoke_report_json["validator"] == "smoke"
        assert run.validation_attempts[0].full_report_json["validator"] == "full"
        assert "Host full validation started" in titles
        assert "Worker validation passed" in titles
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_cleans_validator_images_when_run_completes(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-cleanup-images.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-cleanup-images"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)
    repo_checkout_dir = settings.stage2_workspace_dir / "runs" / run_id / "repo"
    repo_checkout_dir.mkdir(parents=True, exist_ok=True)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        run.smoke_report_json = {"image_tag": f"feature-factory-stage2:{run_id}"}
        session.commit()
    finally:
        session.close()

    removed_images: list[list[str]] = []

    def capture_removed_images(image_refs):  # noqa: ANN001
        refs = list(image_refs)
        if refs:
            removed_images.append(refs)

    monkeypatch.setattr("feature_factory.stage2.runner._remove_docker_images", capture_removed_images)

    runner = Stage2RunRunner(session_factory, settings)
    runner._complete_run(run_id, repo_checkout_dir=repo_checkout_dir, passed=True)

    assert removed_images == [[f"feature-factory-stage2:{run_id}"]]
    assert not repo_checkout_dir.exists()

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_records_tombstone_when_validator_image_cleanup_fails(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-cleanup-images-failed.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-cleanup-images-failed"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)
    repo_checkout_dir = settings.stage2_workspace_dir / "runs" / run_id / "repo"
    repo_checkout_dir.mkdir(parents=True, exist_ok=True)
    validator_image_ref = f"feature-factory-stage2:{run_id}"

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        run.smoke_report_json = {"image_tag": validator_image_ref}
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "feature_factory.stage2.runner._remove_docker_images",
        lambda image_refs: list(image_refs),
    )

    runner = Stage2RunRunner(session_factory, settings)
    runner._complete_run(run_id, repo_checkout_dir=repo_checkout_dir, passed=False)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        titles = [event.title for event in run.events]
        assert "Validator image cleanup incomplete" in titles
        tombstone = session.get(Stage2CleanupTombstone, run_id)
        assert tombstone is not None
        assert tombstone.reason == "terminal_validator_image_cleanup"
        assert tombstone.status == "failed"
        assert tombstone.attempt_count == 1
        assert tombstone.archive_json["validator_image_refs"] == [validator_image_ref]
        assert tombstone.archive_json["cleanup_paths"] is False
        assert tombstone.failure_json["failed_validator_image_refs"] == [validator_image_ref]
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_releases_worker_checkpoint_after_success(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-release-checkpoint.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-release-checkpoint"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    repo_checkout_dir = workspace_dir / "repo"
    repo_checkout_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    checkpoint_root = runtime_dir / "worker-checkpoint" / "current"
    (checkpoint_root / "openhands" / "conversations" / "12345678123456781234567812345678").mkdir(
        parents=True,
        exist_ok=True,
    )
    (checkpoint_root / "openhands" / "bash_events").mkdir(parents=True, exist_ok=True)
    (checkpoint_root / "workspace_snapshot.tar.gz").write_bytes(b"checkpoint")
    checkpoint_image_ref = f"feature-factory/stage2-worker-checkpoint:{run_id}-attempt-001"
    checkpoint_payload = {
        "checkpoint_dir": str(checkpoint_root),
        "workspace_snapshot_path": str(checkpoint_root / "workspace_snapshot.tar.gz"),
        "conversation_id": "12345678-1234-5678-1234-567812345678",
        "docker_commit_status": "saved",
        "docker_image_ref": checkpoint_image_ref,
        "openhands": {
            "conversations_path": str(checkpoint_root / "openhands" / "conversations"),
            "bash_events_dir": str(checkpoint_root / "openhands" / "bash_events"),
        },
        "validation": {"result": "smoke_passed"},
    }

    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.get_run(run_id)
        run.workspace_path = str(workspace_dir)
        run.runtime_snapshot_json = {"resume_checkpoint": dict(checkpoint_payload)}
        service.store_validation_checkpoint(
            run_id,
            attempt_index=1,
            checkpoint=checkpoint_payload,
        )
        session.commit()
    finally:
        session.close()

    removed_images: list[list[str]] = []

    def capture_removed_images(image_refs):  # noqa: ANN001
        refs = list(image_refs)
        if refs:
            removed_images.append(refs)

    monkeypatch.setattr("feature_factory.stage2.runner._remove_docker_images", capture_removed_images)

    runner = Stage2RunRunner(session_factory, settings)
    runner._complete_run(run_id, repo_checkout_dir=repo_checkout_dir, passed=True)

    assert not (runtime_dir / "worker-checkpoint").exists()
    assert removed_images == [[checkpoint_image_ref]]

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
        assert dict(run.runtime_snapshot_json or {}).get("resume_checkpoint") == {}
        assert len(run.validation_attempts) == 1
        assert run.validation_attempts[0].checkpoint_json == {}
        titles = [event.title for event in run.events]
        assert "Worker checkpoint cleaned" in titles
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_preserves_checkpoint_refs_when_cleanup_is_incomplete(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-incomplete-checkpoint-cleanup.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-incomplete-checkpoint-cleanup"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)
    workspace_dir = settings.stage2_workspace_dir / "runs" / run_id
    repo_checkout_dir = workspace_dir / "repo"
    repo_checkout_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir = settings.stage2_workspace_dir / "runtime" / run_id
    checkpoint_root = runtime_dir / "worker-checkpoint" / "current"
    (checkpoint_root / "openhands" / "conversations" / "12345678123456781234567812345678").mkdir(
        parents=True,
        exist_ok=True,
    )
    (checkpoint_root / "openhands" / "bash_events").mkdir(parents=True, exist_ok=True)
    (checkpoint_root / "workspace_snapshot.tar.gz").write_bytes(b"checkpoint")
    checkpoint_image_ref = f"feature-factory/stage2-worker-checkpoint:{run_id}-attempt-001"
    checkpoint_payload = {
        "checkpoint_dir": str(checkpoint_root),
        "workspace_snapshot_path": str(checkpoint_root / "workspace_snapshot.tar.gz"),
        "conversation_id": "12345678-1234-5678-1234-567812345678",
        "docker_commit_status": "saved",
        "docker_image_ref": checkpoint_image_ref,
        "openhands": {
            "conversations_path": str(checkpoint_root / "openhands" / "conversations"),
            "bash_events_dir": str(checkpoint_root / "openhands" / "bash_events"),
        },
        "validation": {"result": "smoke_passed"},
    }

    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.get_run(run_id)
        run.workspace_path = str(workspace_dir)
        run.runtime_snapshot_json = {"resume_checkpoint": dict(checkpoint_payload)}
        service.store_validation_checkpoint(
            run_id,
            attempt_index=1,
            checkpoint=checkpoint_payload,
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "feature_factory.stage2.runner._remove_docker_images",
        lambda image_refs: list(image_refs),
    )

    runner = Stage2RunRunner(session_factory, settings)
    runner._complete_run(run_id, repo_checkout_dir=repo_checkout_dir, passed=True)

    assert not (runtime_dir / "worker-checkpoint").exists()

    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.get_run(run_id)
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
        assert dict(run.runtime_snapshot_json or {}).get("resume_checkpoint", {}).get("docker_image_ref") == (
            checkpoint_image_ref
        )
        assert len(run.validation_attempts) == 1
        assert run.validation_attempts[0].checkpoint_json.get("docker_image_ref") == checkpoint_image_ref
        titles = [event.title for event in run.events]
        assert "Worker checkpoint cleanup incomplete" in titles
        assert "Worker checkpoint cleaned" not in titles

        archive = service.delete_repository_run(repository_id, run_id)
        assert checkpoint_image_ref in archive["checkpoint_docker_image_refs"]
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_interrupts_during_post_agent_full_validation(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-interrupt-full.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-interrupt-full"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    def fake_run_full(self, *, run_id, workspace_dir, cancel_requested=None):  # noqa: ARG001
        while cancel_requested is not None and not cancel_requested():
            time.sleep(0.02)
        raise Stage2ValidationInterrupted("stage2 validation interrupted by user")

    monkeypatch.setattr("feature_factory.stage2.runner.Stage2Validator.run_full", fake_run_full)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubDeferredFullPlannerBackend()
    assert runner.schedule_run(run_id) is True

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        session = session_factory()
        try:
            run = Stage2Service(session).get_run(run_id)
            titles = [event.title for event in run.events]
            if "Host full validation started" in titles:
                assert run.phase == "validator_full"
                break
        finally:
            session.close()
        time.sleep(0.05)
    else:
        raise AssertionError("timed out waiting for host full validation to start")

    assert runner.interrupt_run(run_id) is True

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and runner.is_run_running(run_id):
        time.sleep(0.05)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        titles = [event.title for event in run.events]
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.failed.value
        assert run.error_message == "stage2 run interrupted by user"
        assert "Stage2 interrupt accepted" in titles
        assert "Stage2 run interrupted" in titles
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_shutdown_respects_timeout(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-shutdown-timeout.db'}",
        stage2_workspace_dir=tmp_path / 'stage2',
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-shutdown-timeout"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("demo\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    runner = Stage2RunRunner(session_factory, settings)
    backend = BlockingShutdownBackend()
    runner.backend = backend

    try:
        assert runner.schedule_run(run_id) is True
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and not backend.worker_started.is_set():
            time.sleep(0.05)
        assert backend.worker_started.is_set() is True

        started_at = time.monotonic()
        unfinished = runner.shutdown(timeout_seconds=0.05)
        elapsed = time.monotonic() - started_at

        assert elapsed < 0.5
        assert unfinished == [run_id]
        assert backend.cancel_seen.is_set() is True
    finally:
        backend.release.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and runner.is_run_running(run_id):
            time.sleep(0.05)


def test_stage2_runner_resumes_full_validation_without_reopening_worker(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-resume-full.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-resume-full"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("resume full validation\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    source_run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    snapshot_root = tmp_path / "resume-full-snapshot-root"
    snapshot_root.mkdir()
    (snapshot_root / ".stage2").mkdir()
    (snapshot_root / ".stage2" / "state.json").write_text('{"resumed": true}\n', encoding="utf-8")
    (snapshot_root / "Dockerfile").write_text("FROM feature-factory/python:3.11-jammy-builder\n", encoding="utf-8")
    (snapshot_root / "run_script.sh").write_text("#!/usr/bin/env bash\nprintf 'resume-full'\\n\n", encoding="utf-8")
    snapshot_path = tmp_path / "resume-full-checkpoint" / "workspace_snapshot.tar.gz"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot_path, "w:gz") as archive:
        archive.add(snapshot_root / ".stage2", arcname=".stage2")
        archive.add(snapshot_root / "Dockerfile", arcname="Dockerfile")
        archive.add(snapshot_root / "run_script.sh", arcname="run_script.sh")

    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.get_run(source_run_id)
        run.status = Stage2RunStatus.completed.value
        run.result = Stage2RunResult.failed.value
        run.phase = "completed"
        run.base_image = "python:3.11-jammy-builder"
        run.planner_guidance = "Reuse the saved smoke checkpoint and continue full validation."
        run.planner_model = "stub-planner-model"
        run.worker_model = "stub-worker-model"
        run.error_message = "stage2 run interrupted before completion"
        run.collect_report_json = {"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]}
        run.smoke_report_json = {
            "validator": "smoke",
            "status": "passed",
            "phase": "sample",
            "collect": {"payload": {"action": "collect", "test_files": [{"path": "tests/test_demo.py"}]}},
            "sample_size": 1,
            "passed_files": 1,
        }
        service.store_validation_checkpoint(
            source_run_id,
            attempt_index=1,
            checkpoint={
                "workspace_snapshot_path": str(snapshot_path),
                "attempt_index": 1,
                "validation": {
                    "result": "smoke_passed",
                    "phase": "sample",
                },
            },
        )
        attempt = service.get_run(source_run_id).validation_attempts[0]
        attempt.dockerfile_text = "FROM feature-factory/python:3.11-jammy-builder\n"
        attempt.run_script_text = "#!/usr/bin/env bash\nprintf 'resume-full'\\n\n"
        attempt.collect_report_json = dict(run.collect_report_json or {})
        attempt.smoke_report_json = dict(run.smoke_report_json or {})
        attempt.validator_status = "smoke_completed"
        session.flush()
        resume_run = service.create_run_from_worker_checkpoint(
            repository_id,
            source_run_id,
            stage2_workspace_dir=settings.stage2_workspace_dir,
        )
        resume_run_id = resume_run.id
        session.commit()
    finally:
        session.close()

    def fake_run_full(self, *, run_id, workspace_dir, cancel_requested=None):  # noqa: ARG001
        assert (workspace_dir / "Dockerfile").exists()
        assert (workspace_dir / "run_script.sh").exists()
        assert (workspace_dir / ".stage2" / "state.json").exists()
        assert (workspace_dir / "repo" / "README.md").exists()
        return ValidationOutcome(
            passed=True,
            report={
                "validator": "full",
                "status": "passed",
                "phase": "full",
                "summary": {
                    "total_files": 2,
                    "passed_files": 1,
                    "failed_files": 1,
                    "total_tests": 4,
                    "passed_tests": 3,
                    "failed_tests": 1,
                    "error_tests": 0,
                    "skipped_tests": 0,
                },
                "file_results": [],
            },
        )

    monkeypatch.setattr("feature_factory.stage2.runner.Stage2Validator.run_full", fake_run_full)

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = StubResumeFullValidationBackend()
    runner._run_future(resume_run_id)

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(resume_run_id)
        titles = [event.title for event in run.events]
        assert run.trigger_kind == "resume_full_validation"
        assert run.status == Stage2RunStatus.completed.value
        assert run.result == Stage2RunResult.passed.value
        assert run.full_report_json["validator"] == "full"
        assert run.full_report_json["summary"]["passed_files"] == 1
        assert run.full_report_json["summary"]["failed_files"] == 1
        assert run.smoke_report_json["validator"] == "smoke"
        assert len(run.validation_attempts) == 1
        assert run.validation_attempts[0].checkpoint_json == {}
        assert dict(run.runtime_snapshot_json or {}).get("resume_checkpoint") == {}
        assert not (settings.stage2_workspace_dir / "runtime" / resume_run_id / "worker-checkpoint").exists()
        assert "Full validation resume started" in titles
        assert "Host full validation started" in titles
        assert "Resumed full validation passed" in titles
        assert "Worker checkpoint cleaned" in titles
        assert "Worker conversation completed" not in titles
    finally:
        session.close()
        runner.shutdown()


def test_stage2_runner_resume_worker_uses_source_runtime_snapshot_and_preserves_lineage(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-resume-runtime.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage2_planner_llm_model="current-planner-model",
        stage2_planner_llm_base_url="https://current-planner.example/v1",
        stage2_planner_llm_api_key="current-planner-key",
        stage2_planner_openhands_preset="gpt5",
        stage2_planner_openhands_max_iterations=333,
        stage2_planner_agent_timeout_seconds=3333.0,
        stage2_worker_llm_model="current-worker-model",
        stage2_worker_llm_base_url="https://current-worker.example/v1",
        stage2_worker_llm_api_key="current-worker-key",
        stage2_worker_openhands_preset="default",
        stage2_worker_openhands_max_iterations=444,
        stage2_worker_agent_timeout_seconds=4444.0,
        stage2_max_worker_attempts=6,
        stage2_quickcheck_sample_size=12,
        stage2_p2p_file_count_limit=18,
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=150.0,
        stage2_build_timeout_seconds=480.0,
        stage2_full_validation_timeout_seconds=1800.0,
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-resume-runtime"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("resume runtime\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    workspace_snapshot_path = tmp_path / "worker-checkpoint-resume-runtime" / "workspace_snapshot.tar.gz"
    workspace_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(workspace_snapshot_path, "w:gz"):
        pass

    conversation_id = "12345678-1234-5678-1234-567812345678"
    conversations_path = tmp_path / "worker-checkpoint-resume-runtime" / "conversations"
    (conversations_path / conversation_id.replace("-", "")).mkdir(parents=True, exist_ok=True)
    bash_events_dir = tmp_path / "worker-checkpoint-resume-runtime" / "bash_events"
    bash_events_dir.mkdir(parents=True, exist_ok=True)

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
            "p2p_file_count_limit": 13,
            "collect_timeout_seconds": 210.0,
            "run_test_timeout_seconds": 240.0,
            "build_timeout_seconds": 510.0,
            "full_validation_timeout_seconds": 2400.0,
        },
    }

    session = session_factory()
    try:
        service = Stage2Service(session)
        source_run = service.create_run(
            repository_id,
            trigger_kind="manual",
            commit_resolver=lambda _repository: commit_sha,
        )
        source_run.status = Stage2RunStatus.completed.value
        source_run.result = Stage2RunResult.failed.value
        source_run.phase = "completed"
        source_run.base_image = "python:3.11-jammy-builder"
        source_run.planner_guidance = "Reuse the saved planner guidance and continue worker validation."
        source_run.planner_model = "source-planner-model"
        source_run.runtime_snapshot_json = dict(source_runtime_snapshot)
        service.store_validation_checkpoint(
            source_run.id,
            attempt_index=1,
            checkpoint={
                "workspace_snapshot_path": str(workspace_snapshot_path),
                "conversation_id": conversation_id,
                "docker_commit_status": "saved",
                "docker_image_ref": "feature-factory/stage2-worker-checkpoint:resume-runtime-attempt-001",
                "openhands": {
                    "conversations_path": str(conversations_path),
                    "bash_events_dir": str(bash_events_dir),
                },
            },
        )
        session.commit()
        source_run_id = source_run.id
    finally:
        session.close()

    session = session_factory()
    try:
        service = Stage2Service(session)
        monkeypatch.setattr(
            "feature_factory.stage2.service.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "", ""),
        )
        resume_run = service.create_run_from_worker_checkpoint(
            repository_id,
            source_run_id,
            stage2_workspace_dir=settings.stage2_workspace_dir,
        )
        session.commit()
        resume_run_id = resume_run.id
    finally:
        session.close()

    class CapturingResumeBackend(StubPlannerBackend):
        def __init__(self, runtime_settings: Settings) -> None:
            super().__init__()
            self.runtime_settings = runtime_settings
            self.resume_checkpoints: list[dict[str, object]] = []

        def run_worker_attempt(
            self,
            repo_path: Path,
            repository: GitHubRepository,
            *,
            workspace_dir: Path,
            decision: PlannerDecision,
            attempt_index: int,
            resume_checkpoint=None,
            last_smoke_report=None,
            emit_event=None,
        ) -> WorkerExecutionResult:
            self.resume_checkpoints.append(dict(resume_checkpoint or {}))
            return super().run_worker_attempt(
                repo_path,
                repository,
                workspace_dir=workspace_dir,
                decision=decision,
                attempt_index=attempt_index,
                resume_checkpoint=resume_checkpoint,
                last_smoke_report=last_smoke_report,
                emit_event=emit_event,
            )

    captured_backends: list[CapturingResumeBackend] = []

    def fake_build_stage2_backend(runtime_settings: Settings, *, app_instance_id: str = ""):
        backend = CapturingResumeBackend(runtime_settings)
        captured_backends.append(backend)
        return backend

    runner = Stage2RunRunner(session_factory, settings)
    runner.backend = OpenHandsStage2Backend(
        settings,
        selection_detail="Using OpenHands stage2 planner backend.",
        ready=True,
        readiness_message="ready",
    )
    monkeypatch.setattr("feature_factory.stage2.runner.build_stage2_backend", fake_build_stage2_backend)

    runner._run_future(resume_run_id)

    assert len(captured_backends) == 1
    backend = captured_backends[0]
    assert backend.runtime_settings.stage2_agent_llm_model("planner") == "source-planner-model"
    assert backend.runtime_settings.stage2_agent_llm_base_url("planner") == "https://source-planner.example/v1"
    assert backend.runtime_settings.stage2_agent_llm_api_key("planner").get_secret_value() == "source-planner-key"
    assert backend.runtime_settings.stage2_agent_openhands_preset("planner") == "default"
    assert backend.runtime_settings.stage2_agent_openhands_max_iterations("planner") == 111
    assert backend.runtime_settings.stage2_agent_timeout("planner") == 1111.0
    assert backend.runtime_settings.stage2_agent_llm_model("worker") == "source-worker-model"
    assert backend.runtime_settings.stage2_agent_llm_base_url("worker") == "https://source-worker.example/v1"
    assert backend.runtime_settings.stage2_agent_llm_api_key("worker").get_secret_value() == "source-worker-key"
    assert backend.runtime_settings.stage2_agent_openhands_preset("worker") == "gpt5"
    assert backend.runtime_settings.stage2_agent_openhands_max_iterations("worker") == 222
    assert backend.runtime_settings.stage2_agent_timeout("worker") == 2222.0
    assert backend.runtime_settings.stage2_max_worker_attempts == 7
    assert backend.runtime_settings.stage2_quickcheck_sample_size == 9
    assert backend.runtime_settings.stage2_p2p_file_count_limit == 13
    assert backend.runtime_settings.stage2_p2p_sample_seed == source_run_id
    assert backend.runtime_settings.stage2_collect_timeout_seconds == 210.0
    assert backend.runtime_settings.stage2_run_test_timeout_seconds == 240.0
    assert backend.runtime_settings.stage2_build_timeout_seconds == 510.0
    assert backend.runtime_settings.stage2_full_validation_timeout_seconds == 2400.0
    expected_resume_checkpoint_root = (
        settings.stage2_workspace_dir / "runtime" / resume_run_id / "worker-checkpoint" / "current"
    )

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(resume_run_id)
        assert run.trigger_kind == "resume_worker"
        assert run.runtime_snapshot_json["resume_source_run_id"] == source_run_id
        assert run.runtime_snapshot_json["resume_source_attempt_index"] == 1
        assert run.runtime_snapshot_json["resume_checkpoint"]["conversation_id"] == conversation_id
        assert run.runtime_snapshot_json["resume_checkpoint"]["checkpoint_dir"] == str(expected_resume_checkpoint_root)
        assert run.runtime_snapshot_json["resume_checkpoint"]["workspace_snapshot_path"] == str(
            expected_resume_checkpoint_root / "workspace_snapshot.tar.gz"
        )
        assert run.runtime_snapshot_json["resume_checkpoint"]["openhands"]["conversations_path"] == str(
            expected_resume_checkpoint_root / "openhands" / "conversations"
        )
        assert run.runtime_snapshot_json["resume_checkpoint"]["openhands"]["bash_events_dir"] == str(
            expected_resume_checkpoint_root / "openhands" / "bash_events"
        )
        assert run.runtime_snapshot_json["resume_checkpoint"]["docker_image_ref"] == (
            f"feature-factory/stage2-worker-checkpoint:{resume_run_id}-attempt-001"
        )
        assert run.runtime_snapshot_json["planner"]["model"] == source_runtime_snapshot["planner"]["model"]
        assert run.runtime_snapshot_json["planner"]["base_url"] == source_runtime_snapshot["planner"]["base_url"]
        assert run.runtime_snapshot_json["planner"]["preset"] == source_runtime_snapshot["planner"]["preset"]
        assert run.runtime_snapshot_json["planner"]["max_iterations"] == source_runtime_snapshot["planner"]["max_iterations"]
        assert run.runtime_snapshot_json["planner"]["timeout_seconds"] == source_runtime_snapshot["planner"]["timeout_seconds"]
        assert run.runtime_snapshot_json["planner"]["api_key_preview"] == source_runtime_snapshot["planner"]["api_key_preview"]
        assert run.runtime_snapshot_json["planner"]["api_key"] == source_runtime_snapshot["planner"]["api_key"]
        assert run.runtime_snapshot_json["worker"]["model"] == source_runtime_snapshot["worker"]["model"]
        assert run.runtime_snapshot_json["worker"]["base_url"] == source_runtime_snapshot["worker"]["base_url"]
        assert run.runtime_snapshot_json["worker"]["preset"] == source_runtime_snapshot["worker"]["preset"]
        assert run.runtime_snapshot_json["worker"]["max_iterations"] == source_runtime_snapshot["worker"]["max_iterations"]
        assert run.runtime_snapshot_json["worker"]["timeout_seconds"] == source_runtime_snapshot["worker"]["timeout_seconds"]
        assert run.runtime_snapshot_json["worker"]["api_key_preview"] == source_runtime_snapshot["worker"]["api_key_preview"]
        assert run.runtime_snapshot_json["worker"]["api_key"] == source_runtime_snapshot["worker"]["api_key"]
        assert run.runtime_snapshot_json["hyperparameters"] == {
            **source_runtime_snapshot["hyperparameters"],
            "p2p_sample_seed": source_run_id,
        }
    finally:
        session.close()


def test_stage2_runner_reuses_stored_selected_base_image_payload_for_worker_rerun(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-rerun-base-image-ref.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-rerun-base-image-ref"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("rerun base image ref\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")

    repository_id = _seed_repository(session_factory, remote_repo)
    stored_selected_base_image = {
        "image_id": "python:3.11-jammy-builder",
        "image_ref": "feature-factory/python:3.11-jammy-builder-cn",
        "asset_path": "base_images/python-3.11-jammy-cn.Dockerfile",
        "network_profile": "cn",
    }

    session = session_factory()
    try:
        service = Stage2Service(session)
        source_run = service.create_run(
            repository_id,
            trigger_kind="manual",
            commit_resolver=lambda _repository: commit_sha,
        )
        source_run.status = Stage2RunStatus.completed.value
        source_run.result = Stage2RunResult.failed.value
        source_run.phase = "completed"
        source_run.base_image = "python:3.11-jammy-builder"
        source_run.planner_guidance = "Reuse the previous planner result and rerun the worker."
        source_run.runtime_snapshot_json = {
            "planner": {"model": "source-planner-model"},
            "worker": {"model": "source-worker-model"},
            "selection": {
                "base_image_ref": stored_selected_base_image["image_ref"],
                "selected_base_image": dict(stored_selected_base_image),
            },
        }
        session.commit()
        source_run_id = source_run.id
    finally:
        session.close()

    session = session_factory()
    try:
        service = Stage2Service(session)
        rerun = service.create_run_from_existing_worker_plan(repository_id, source_run_id)
        rerun_id = rerun.id
        session.commit()
    finally:
        session.close()

    class FakeBaseImage:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = dict(payload)

        def to_payload(self, *, use_china_mirrors=None) -> dict[str, object]:  # noqa: ARG002
            return dict(self._payload)

    monkeypatch.setattr(
        "feature_factory.stage2.runner.list_base_images",
        lambda: [
            FakeBaseImage(
                {
                    "image_id": "python:3.11-jammy-builder",
                    "image_ref": "feature-factory/python:3.11-jammy-builder-renamed",
                    "asset_path": "base_images/python-3.11-jammy.Dockerfile",
                    "network_profile": "default",
                }
            )
        ],
    )

    session = session_factory()
    try:
        rerun = Stage2Service(session).get_run(rerun_id)
        assert rerun.runtime_snapshot_json["selection"]["selected_base_image"]["image_ref"] == (
            stored_selected_base_image["image_ref"]
        )
        runner = Stage2RunRunner(session_factory, settings)
        decision = runner._reuse_planner_decision(rerun)
    finally:
        session.close()

    assert decision is not None
    assert decision.base_image_ref == stored_selected_base_image["image_ref"]
    assert decision.base_image_catalog == [stored_selected_base_image]


def test_stage2_service_store_validation_checkpoint_keeps_only_latest_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage2-latest-checkpoint.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    remote_repo = tmp_path / "remote-repo-latest-checkpoint"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("latest checkpoint\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")
    repository_id = _seed_repository(session_factory, remote_repo)

    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.create_run(
            repository_id,
            trigger_kind="manual",
            commit_resolver=lambda _repository: commit_sha,
        )
        service.store_validation_checkpoint(
            run.id,
            attempt_index=1,
            checkpoint={"workspace_snapshot_path": "/tmp/checkpoint-1.tar.gz"},
        )
        service.store_validation_checkpoint(
            run.id,
            attempt_index=2,
            checkpoint={"workspace_snapshot_path": "/tmp/checkpoint-2.tar.gz"},
        )
        session.commit()
        reloaded = service.get_run(run.id)
        checkpoints = {
            attempt.attempt_index: dict(attempt.checkpoint_json or {})
            for attempt in reloaded.validation_attempts
        }
        assert checkpoints[1] == {}
        assert checkpoints[2]["workspace_snapshot_path"] == "/tmp/checkpoint-2.tar.gz"
    finally:
        session.close()


def test_stage2_runner_restore_worker_checkpoint_restores_snapshot_without_touching_repo(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-restore-checkpoint.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = Stage2RunRunner(session_factory, settings)
    remote_repo = tmp_path / "remote-repo-restore-checkpoint"
    remote_repo.mkdir()
    _init_git_repo(remote_repo)
    (remote_repo / "README.md").write_text("restore checkpoint\n")
    _git(remote_repo, "add", "README.md")
    _git(remote_repo, "commit", "-m", "initial")
    commit_sha = _git_stdout(remote_repo, "rev-parse", "HEAD")
    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha=commit_sha)

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = workspace_dir / "repo"
    repo_dir.mkdir()
    (repo_dir / "keep.txt").write_text("repo-state\n", encoding="utf-8")

    snapshot_root = tmp_path / "snapshot-root"
    snapshot_root.mkdir()
    (snapshot_root / ".stage2").mkdir()
    (snapshot_root / ".stage2" / "state.json").write_text('{"checkpoint": true}\n', encoding="utf-8")
    (snapshot_root / "Dockerfile").write_text("FROM feature-factory/python:3.11-jammy-builder\n", encoding="utf-8")
    (snapshot_root / "run_script.sh").write_text("#!/usr/bin/env bash\nprintf 'resume'\\n\n", encoding="utf-8")

    snapshot_path = tmp_path / "worker-checkpoint" / "workspace_snapshot.tar.gz"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(snapshot_path, "w:gz") as archive:
        archive.add(snapshot_root / ".stage2", arcname=".stage2")
        archive.add(snapshot_root / "Dockerfile", arcname="Dockerfile")
        archive.add(snapshot_root / "run_script.sh", arcname="run_script.sh")

    runner._restore_worker_checkpoint(
        run_id,
        workspace_dir=workspace_dir,
        checkpoint={
            "workspace_snapshot_path": str(snapshot_path),
            "attempt_index": 3,
        },
    )

    assert (workspace_dir / ".stage2" / "state.json").read_text(encoding="utf-8") == '{"checkpoint": true}\n'
    assert (workspace_dir / "Dockerfile").read_text(encoding="utf-8").startswith(
        "FROM feature-factory/python:3.11-jammy-builder"
    )
    assert (workspace_dir / "run_script.sh").read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")
    assert (repo_dir / "keep.txt").read_text(encoding="utf-8") == "repo-state\n"


def test_stage2_runner_parses_git_progress_lines() -> None:
    remote_progress = _parse_git_progress_line("remote: Counting objects:  42% (42/100)")
    assert remote_progress == {
        "git_stage": "Counting objects",
        "percent": 42,
        "raw_line": "Counting objects:  42% (42/100)",
        "current": 42,
        "total": 100,
    }

    receive_progress = _parse_git_progress_line("Receiving objects: 100% (10/10), 1.23 MiB | 2.34 MiB/s")
    assert receive_progress == {
        "git_stage": "Receiving objects",
        "percent": 100,
        "raw_line": "Receiving objects: 100% (10/10), 1.23 MiB | 2.34 MiB/s",
        "current": 10,
        "total": 10,
    }

    assert _parse_git_progress_line("Cloning into bare repository 'demo.git'...") is None


def test_stage2_runner_retries_retryable_git_remote_failures(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-retry.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-retry"
    remote_repo.mkdir()
    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha="abc123")
    runner = Stage2RunRunner(session_factory, settings)

    attempts: list[int] = []

    def fake_run_command_with_git_progress(args, **kwargs):
        attempts.append(kwargs["progress_payload"]["attempt"])
        if len(attempts) == 1:
            raise RuntimeError("failed to clone: LibreSSL SSL_connect: SSL_ERROR_SYSCALL")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_run_command_with_git_progress", fake_run_command_with_git_progress)
    monkeypatch.setattr("feature_factory.stage2.runner.time.sleep", lambda _seconds: None)

    completed = runner._run_git_remote_command_with_progress(
        ["git", "clone", "https://example.test/repo.git", "repo.git"],
        error_message="failed to clone",
        run_id=run_id,
        phase="host_planner",
        progress_title="Repository cache clone progress",
        retry_title="Repository cache clone retrying",
        progress_payload={"operation": "cache_clone"},
    )

    assert completed.returncode == 0
    assert attempts == [1, 2]
    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        retry_event = next(event for event in run.events if event.title == "Repository cache clone retrying")
        assert retry_event.payload_json["next_attempt"] == 2
        assert "SSL_ERROR_SYSCALL" in retry_event.payload_json["error"]
    finally:
        session.close()


def test_stage2_runner_git_progress_command_enforces_timeout(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-git-timeout.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    runner = Stage2RunRunner(build_session_factory(engine), settings)
    started_at = time.monotonic()

    with pytest.raises(RuntimeError, match="command timed out after"):
        runner._run_command_with_git_progress(
            ["sh", "-c", "sleep 5"],
            error_message="git operation failed",
            run_id="run-1",
            phase="host_planner",
            progress_title="Git progress",
            progress_payload={"operation": "timeout_test"},
            timeout_seconds=0.1,
        )

    assert time.monotonic() - started_at < 2.0


def test_stage2_runner_rotates_shared_github_proxy_queue_after_timeouts(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-proxy-rotation.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    remote_repo = tmp_path / "remote-repo-proxy-rotation"
    remote_repo.mkdir()
    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha="abc123")
    runner = Stage2RunRunner(session_factory, settings)
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        '["https://proxy-one.test/","https://proxy-two.test/","https://proxy-three.test/","https://proxy-four.test/"]',
    )
    reset_github_proxy_queue()
    attempted_urls: list[str] = []

    def fake_run_command_with_git_progress(args, **_kwargs):
        attempted_urls.append(args[2])
        if len(attempted_urls) < 4:
            raise RuntimeError("failed to clone: command timed out after 600s")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(runner, "_run_command_with_git_progress", fake_run_command_with_git_progress)
    monkeypatch.setattr("feature_factory.stage2.runner.time.sleep", lambda _seconds: None)

    completed = runner._run_git_remote_command_with_progress(
        [
            "git",
            "clone",
            "https://proxy-one.test/https://github.com/owner/repo.git",
            "repo.git",
        ],
        error_message="failed to clone",
        run_id=run_id,
        phase="host_planner",
        progress_title="Repository cache clone progress",
        retry_title="Repository cache clone retrying",
        progress_payload={"operation": "cache_clone"},
    )

    assert completed.returncode == 0
    assert attempted_urls == [
        "https://proxy-one.test/https://github.com/owner/repo.git",
        "https://proxy-two.test/https://github.com/owner/repo.git",
        "https://proxy-three.test/https://github.com/owner/repo.git",
        "https://proxy-four.test/https://github.com/owner/repo.git",
    ]


def test_stage2_runner_cn_remote_url_rewrite_only_applies_to_public_github(monkeypatch) -> None:
    monkeypatch.setattr("feature_factory.stage2.runner.should_use_china_mirrors", lambda: True)
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")

    assert _temporary_git_remote_url("https://github.com/owner/demo-repo") == (
        "https://ghfast.top/https://github.com/owner/demo-repo"
    )
    assert _temporary_git_remote_url("https://example.com/owner/demo-repo") == "https://example.com/owner/demo-repo"
    assert _temporary_git_remote_url("ssh://git@github.com/owner/demo-repo.git") == (
        "ssh://git@github.com/owner/demo-repo.git"
    )


def test_stage2_runner_repository_cache_clone_uses_temporary_github_proxy(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-cache-proxy-clone.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = Stage2RunRunner(session_factory, settings)
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="main",
    )
    captured_args: list[str] = []

    monkeypatch.setattr("feature_factory.stage2.runner.should_use_china_mirrors", lambda: True)
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")
    monkeypatch.setattr(
        runner,
        "_run_git_remote_command_with_progress",
        lambda args, **_kwargs: captured_args.extend(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, "", ""),
    )

    runner._create_repository_cache(repository, tmp_path / "cache.git", default_branch="main", run_id="run-1")

    assert captured_args[:7] == [
        "git",
        "clone",
        "--progress",
        "--bare",
        "--single-branch",
        "--no-tags",
        "--branch",
    ]
    assert captured_args[8] == "https://ghfast.top/https://github.com/owner/demo-repo"


def test_stage2_runner_repository_cache_refresh_keeps_origin_canonical_and_fetches_via_proxy(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-cache-proxy-refresh.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = Stage2RunRunner(session_factory, settings)
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="",
    )
    run_commands: list[list[str]] = []
    fetch_commands: list[list[str]] = []

    monkeypatch.setattr("feature_factory.stage2.runner.should_use_china_mirrors", lambda: True)
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")
    monkeypatch.setattr(
        runner,
        "_run_command",
        lambda args, **_kwargs: run_commands.append(list(args)) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    monkeypatch.setattr(
        runner,
        "_run_git_remote_command_with_progress",
        lambda args, **_kwargs: fetch_commands.append(list(args)) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    runner._refresh_repository_cache(repository, tmp_path / "cache.git", default_branch=None, run_id="run-1")

    assert run_commands == [
        [
            "git",
            f"--git-dir={tmp_path / 'cache.git'}",
            "remote",
            "set-url",
            "origin",
            "https://github.com/owner/demo-repo",
        ]
    ]
    assert fetch_commands == [
        [
            "git",
            f"--git-dir={tmp_path / 'cache.git'}",
            "fetch",
            "--progress",
            "--prune",
            "--no-tags",
            "https://ghfast.top/https://github.com/owner/demo-repo",
        ]
    ]


def test_stage2_runner_requested_commit_fetch_uses_temporary_github_proxy(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-cache-proxy-requested-commit.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = Stage2RunRunner(session_factory, settings)
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="main",
    )
    fetch_commands: list[list[str]] = []
    seen_revisions: list[str] = []

    monkeypatch.setattr("feature_factory.stage2.runner.should_use_china_mirrors", lambda: True)
    monkeypatch.setenv("FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX", "https://ghfast.top/")
    monkeypatch.setattr(runner, "_record_event", lambda *args, **kwargs: None)

    def fake_try_rev_parse(_cache_dir: Path, revision: str) -> str | None:
        seen_revisions.append(revision)
        return "abc123" if len(seen_revisions) > 1 else None

    monkeypatch.setattr(runner, "_try_rev_parse", fake_try_rev_parse)
    monkeypatch.setattr(
        runner,
        "_run_git_remote_command_with_progress",
        lambda args, **_kwargs: fetch_commands.append(list(args)) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    resolved = runner._resolve_cached_target_commit(
        repository,
        cache_dir=tmp_path / "cache.git",
        requested_commit_sha="abc123",
        run_id="run-1",
    )

    assert resolved == "abc123"
    assert fetch_commands == [
        [
            "git",
            f"--git-dir={tmp_path / 'cache.git'}",
            "fetch",
            "--progress",
            "--no-tags",
            "https://ghfast.top/https://github.com/owner/demo-repo",
            "abc123",
        ]
    ]


def test_stage2_runner_target_checkout_uses_remote_retry_path(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv(
        "FEATURE_FACTORY_STAGE2_GITHUB_PROXY_PREFIX",
        "https://ghfast.top/",
    )
    reset_github_proxy_queue()
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-checkout-retry.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    runner = Stage2RunRunner(session_factory, settings)
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="main",
    )
    checkout_dir = tmp_path / "checkout"
    commands: list[list[str]] = []
    kwargs_seen: list[dict[str, object]] = []

    def run_with_retry(args, **kwargs):
        commands.append(list(args))
        kwargs_seen.append(dict(kwargs))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(
        runner,
        "_run_git_remote_command_with_progress",
        run_with_retry,
    )
    monkeypatch.setattr(runner, "_record_event", lambda *args, **kwargs: None)

    runner._repository_materializer()._checkout_target_commit(
        "run-1",
        repository=repository,
        checkout_dir=checkout_dir,
        target_commit_sha="abc123",
        phase="host_planner",
    )

    assert commands == [
        [
            "git",
            "-c",
            "lfs.url=https://ghfast.top/https://github.com/owner/demo-repo.git/info/lfs",
            "-C",
            str(checkout_dir),
            "checkout",
            "--force",
            "--detach",
            "abc123",
        ]
    ]
    assert kwargs_seen[0]["retry_title"] == "Target commit checkout retrying"
    assert kwargs_seen[0]["timeout_seconds"] == 120.0


def test_stage2_cache_materialization_clones_without_checkout_before_target_commit(
    monkeypatch,
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-materializer.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    runner = Stage2RunRunner(build_session_factory(engine), settings)
    materializer = runner._repository_materializer()
    repository = GitHubRepository(
        full_name="owner/demo-repo",
        owner_login="owner",
        name="demo-repo",
        html_url="https://github.com/owner/demo-repo",
        default_branch="main",
    )
    cache_dir = tmp_path / "cache.git"
    cache_dir.mkdir()
    checkout_dir = tmp_path / "checkout"
    commands: list[list[str]] = []

    def run_progress(args, **_kwargs):
        commands.append(list(args))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(materializer, "_run_command_with_git_progress", run_progress)
    monkeypatch.setattr(materializer, "_restore_origin_remote", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(materializer, "_checkout_target_commit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        materializer,
        "_run_checked_command_cancelable",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            _args[0], 0, "abc123\n", ""
        ),
    )

    materializer.materialize_checkout_from_cache(
        "run-1",
        repository=repository,
        cache_dir=cache_dir,
        checkout_dir=checkout_dir,
        target_commit_sha="abc123",
        phase="host_planner",
        emit_event=lambda **_kwargs: None,
    )

    assert commands == [
        [
            "git",
            "clone",
            "--progress",
            "--no-checkout",
            str(cache_dir),
            str(checkout_dir),
        ]
    ]


def test_stage2_runner_updates_live_token_usage_from_backend_events(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage2-runner-token-usage.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)

    remote_repo = tmp_path / "remote-repo-token-usage"
    remote_repo.mkdir()
    repository_id = _seed_repository(session_factory, remote_repo)
    run_id = _create_run(session_factory, repository_id, target_commit_sha="abc123")
    runner = Stage2RunRunner(session_factory, settings)

    runner._record_backend_event(
        run_id,
        type(
            "PlannerEvent",
            (),
            {
                "actor": "planner_agent",
                "phase": "host_planner",
                "title": "Action: bash",
                "message": "Inspecting repository manifests",
                "payload": {
                    "token_usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "reasoning_tokens": 2,
                    }
                },
            },
        )(),
    )

    session = session_factory()
    try:
        run = Stage2Service(session).get_run(run_id)
        assert run.planner_token_usage == 16
        assert run.events[-1].title == "Action: bash"
    finally:
        session.close()


def test_stage2_runner_identifies_retryable_git_remote_errors() -> None:
    assert _is_retryable_git_remote_error("fatal: unable to access: SSL_ERROR_SYSCALL")
    assert _is_retryable_git_remote_error("fatal: early EOF")
    for status_code in (429, 520, 522, 524):
        assert _is_retryable_git_remote_error(
            f"fatal: unable to access: The requested URL returned error: {status_code}"
        )
    assert _is_retryable_git_remote_error("smudge filter lfs failed")
    assert not _is_retryable_git_remote_error("fatal: Remote branch missing not found in upstream origin")


def _seed_repository(session_factory, remote_repo: Path) -> int:
    session = session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=9001,
            full_name="owner/demo-repo",
            owner_login="owner",
            name="demo-repo",
            html_url=str(remote_repo),
            api_url="https://api.github.com/repos/owner/demo-repo",
            description="demo repo",
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
        return repository.id
    finally:
        session.close()


def _create_run(session_factory, repository_id: int, *, target_commit_sha: str) -> str:
    session = session_factory()
    try:
        service = Stage2Service(session)
        run = service.create_run(
            repository_id,
            trigger_kind="manual",
            commit_resolver=lambda _repository: target_commit_sha,
        )
        session.commit()
        return run.id
    finally:
        session.close()


def _init_git_repo(path: Path) -> None:
    # `git init --initial-branch` requires Git 2.28+, while some supported
    # development hosts still ship 2.27. Point the unborn HEAD at main using
    # commands available in older Git releases.
    _git(path, "init")
    _git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(path, "config", "user.name", "FeatureFactory Tests")
    _git(path, "config", "user.email", "tests@example.com")


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(details)
    return completed.stdout.strip()


def _git_stdout(path: Path, *args: str, git_dir: bool = False) -> str:
    command = ["git", f"--git-dir={path}", *args] if git_dir else ["git", "-C", str(path), *args]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RuntimeError(details)
    return completed.stdout.strip()


def _git_config_value(path: Path, key: str, *, git_dir: bool = False) -> str | None:
    command = ["git", f"--git-dir={path}", "config", "--get", key] if git_dir else ["git", "-C", str(path), "config", "--get", key]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _git_symbolic_ref(path: Path, revision: str, *, git_dir: bool = False) -> str | None:
    command = ["git", f"--git-dir={path}", "symbolic-ref", "-q", revision] if git_dir else ["git", "-C", str(path), "symbolic-ref", "-q", revision]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None
