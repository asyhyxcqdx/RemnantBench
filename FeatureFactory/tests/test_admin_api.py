from datetime import UTC, datetime
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
import hashlib
import json
import shutil
import zipfile

import feature_factory.server as server_module
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from feature_factory.config import Settings
from feature_factory.models import (
    BatchTask,
    BatchTaskEntry,
    BatchTaskRepository,
    BatchTaskStatus,
    BatchTaskUnit,
    CrawlJob,
    CrawlJobStatus,
    CrawlPartition,
    CrawlPartitionQueryExecution,
    CrawlPartitionQueryStatus,
    CrawlPartitionStatus,
    DataPool,
    DataPoolAsset,
    GitHubRepository,
    GlobalRuntimeConfig,
    RepoDiscovery,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4HintVariant,
    Stage4DataPoolBackfillJob,
    Stage4IssueVariant,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.data_pool import DataPoolService
from feature_factory.server import create_app
from feature_factory.stage4.issue_styles import load_issue_style_catalog
from feature_factory.stage4.runner import Stage4RunRunner
from feature_factory.stage4.service import Stage4Service
from datetime import timedelta


class DummyRunner:
    def __init__(self) -> None:
        self.scheduled: list[tuple[str, int | None]] = []
        self.max_limit = 8
        self.max_workers = 2
        self.partition_concurrency_cap = 16
        self.partition_concurrency_default = 8
        self.running: set[str] = set()
        self.pause_requests: set[str] = set()

    def schedule_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> bool:
        self.scheduled.append((job_id, max_concurrent_partitions))
        return True

    def run_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        self.scheduled.append((job_id, max_concurrent_partitions))

    def is_running(self, job_id: str) -> bool:
        return job_id in self.running

    def is_scheduled(self, job_id: str) -> bool:
        return any(scheduled_job_id == job_id for scheduled_job_id, *_ in self.scheduled)

    def request_pause(self, job_id: str) -> bool:
        if job_id not in self.running:
            return False
        self.pause_requests.add(job_id)
        return True

    def is_pause_requested(self, job_id: str) -> bool:
        return job_id in self.pause_requests

    def cancel_job(self, job_id: str) -> bool:
        before = len(self.scheduled)
        self.scheduled = [item for item in self.scheduled if item[0] != job_id]
        self.pause_requests.discard(job_id)
        return len(self.scheduled) != before

    def get_max_workers(self) -> int:
        return self.max_workers

    def set_max_workers(self, max_workers: int) -> None:
        if max_workers > self.max_limit:
            raise ValueError("too large")
        self.max_workers = max_workers

    def get_default_max_concurrent_partitions(self) -> int:
        return self.partition_concurrency_default

    def validate_max_concurrent_partitions(self, requested: int | None) -> int:
        resolved = requested or self.partition_concurrency_default
        if resolved > self.partition_concurrency_cap:
            raise ValueError("too large")
        return resolved

    def shutdown(self) -> None:
        return None


class FalseScheduleRunner(DummyRunner):
    def schedule_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> bool:
        return False


class FalsePauseRunner(DummyRunner):
    def request_pause(self, job_id: str) -> bool:
        return False


class FalseScheduleStage4Runner:
    def __init__(self) -> None:
        self.max_limit = 128
        self.max_workers = 64
        self.scheduled: list[str] = []

    def backend_readiness(self) -> dict[str, object]:
        return {"ready": True, "message": "ready", "backend": "test"}

    def is_backend_ready(self) -> bool:
        return True

    def schedule_run(self, run_id: str) -> bool:
        self.scheduled.append(run_id)
        return False

    def interrupt_run(self, _run_id: str) -> bool:
        return False

    def get_max_workers(self) -> int:
        return self.max_workers

    def set_max_workers(self, max_workers: int) -> None:
        if max_workers > self.max_limit:
            raise ValueError("too large")
        self.max_workers = max_workers

    def reload_backend(self) -> None:
        return None

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        return []


class DummyBatchRunner:
    def __init__(self) -> None:
        self.scheduled: list[str] = []
        self.running: set[str] = set()

    def schedule_task(self, task_id: str) -> bool:
        self.scheduled.append(task_id)
        return True

    def is_running(self, task_id: str) -> bool:
        return task_id in self.running

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        return []


class NonCancelableScheduledRunner(DummyRunner):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled_ids: set[str] = set()

    def is_scheduled(self, job_id: str) -> bool:
        return job_id in self.scheduled_ids

    def cancel_job(self, job_id: str) -> bool:
        return False


class FailingForegroundRunner(DummyRunner):
    def __init__(self, *, fail_message: str = "sync boom") -> None:
        super().__init__()
        self.fail_message = fail_message
        self.session_factory = None

    def run_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        if self.session_factory is not None:
            session = self.session_factory()
            try:
                job = session.get(CrawlJob, job_id)
                assert job is not None
                job.status = CrawlJobStatus.failed.value
                job.error_message = self.fail_message
                session.commit()
            finally:
                session.close()
        raise RuntimeError(self.fail_message)


class DedupScheduledRunner(DummyRunner):
    def __init__(self) -> None:
        super().__init__()
        self.scheduled_ids: set[str] = set()

    def schedule_job(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> bool:
        if job_id in self.scheduled_ids:
            return False
        self.scheduled_ids.add(job_id)
        return True

    def is_scheduled(self, job_id: str) -> bool:
        return job_id in self.scheduled_ids


def test_create_app_auto_manages_local_postgres_lifecycle(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'auto-managed.db'}")
    runner = DummyRunner()
    events: list[str] = []

    @contextmanager
    def fake_managed_local_postgres(*args, **kwargs):
        events.append("enter")
        yield None
        events.append("exit")

    monkeypatch.setattr(server_module, "managed_local_postgres", fake_managed_local_postgres)

    app = create_app(settings, runner=runner)
    assert events == ["enter"]
    assert app.state.managed_postgres is None

    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200

    assert events == ["enter", "exit"]


def test_admin_api_can_create_and_list_jobs(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'admin.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    health = client.get("/api/health")
    assert health.status_code == 200

    runtime = client.get("/api/stage1/runtime")
    assert runtime.status_code == 200
    assert runtime.json()["max_concurrent_jobs"] == 2
    assert runtime.json()["max_concurrent_partitions_default"] == 8
    assert runtime.json()["max_concurrent_jobs_cap"] == 8
    assert runtime.json()["max_concurrent_partitions_cap"] == 16

    runtime_update = client.patch("/api/stage1/runtime", json={"max_concurrent_jobs": 3})
    assert runtime_update.status_code == 200
    assert runtime_update.json()["max_concurrent_jobs"] == 3

    runtime_over_cap = client.patch("/api/stage1/runtime", json={"max_concurrent_jobs": 25})
    assert runtime_over_cap.status_code == 422

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "api-job",
            "run_in_background": True,
            "max_concurrent_partitions": 2,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "repository_limit": 10,
            },
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]

    jobs = client.get("/api/stage1/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["id"] == job_id
    assert jobs.json()["jobs"][0]["status"] == "queued"
    assert jobs.json()["jobs"][0]["can_resume"] is False

    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["name"] == "api-job"
    assert detail.json()["job"]["stats"]["max_concurrent_partitions"] == 2
    assert detail.json()["job"]["filters"]["repository_limit"] == 10
    assert detail.json()["partitions"] == []
    assert len(runner.scheduled) == 1
    assert runner.scheduled[0][1] == 2

    runner.running.add(job_id)
    running_detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert running_detail.status_code == 200
    assert running_detail.json()["job"]["can_delete"] is False
    paused = client.post(f"/api/stage1/jobs/{job_id}/pause")
    assert paused.status_code == 200
    assert runner.is_pause_requested(job_id) is True
    runner.running.remove(job_id)

    session = client.app.state.session_factory()
    try:
        job = session.get(CrawlJob, job_id)
        assert job is not None
        job.status = CrawlJobStatus.completed.value
        session.commit()
    finally:
        session.close()

    resume = client.post(f"/api/stage1/jobs/{job_id}/resume")
    assert resume.status_code == 409

    delete = client.delete(f"/api/stage1/jobs/{job_id}")
    assert delete.status_code == 200

    jobs_after_delete = client.get("/api/stage1/jobs")
    assert jobs_after_delete.status_code == 200
    assert jobs_after_delete.json()["jobs"] == []


def test_stage1_target_repository_job_create_ignores_search_filters(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage1-target.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "target-repos",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "stars_min": 100,
                "exclude_forks": True,
                "exclude_archived": True,
                "licenses": ["mit"],
                "keywords": ["pytest"],
                "target_repositories": ["Owner/Repo", "owner/repo", "pallets/flask"],
            },
        },
    )

    assert response.status_code == 200
    job_id = response.json()["job"]["id"]
    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    filters = detail.json()["job"]["filters"]
    assert filters["target_repositories"] == ["Owner/Repo", "pallets/flask"]
    assert filters["language"] is None
    assert filters["languages"] == []
    assert filters["stars_min"] is None
    assert filters["licenses"] == []
    assert filters["keywords"] == []
    assert filters["exclude_forks"] is False
    assert filters["exclude_archived"] is False
    assert runner.scheduled == [(job_id, 8)]


def test_stage1_github_token_templates_round_trip(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage1-token-templates.db'}")
    client = TestClient(create_app(settings, runner=DummyRunner()))

    empty_response = client.post(
        "/api/stage1/templates",
        json={"name": "empty-token-template", "github_tokens": ["", "   "]},
    )
    assert empty_response.status_code == 400

    create_response = client.post(
        "/api/stage1/templates",
        json={
            "name": "github-token-template-a",
            "github_tokens": ["ghp_abcdef123", " ghp_abcdef123 ", "ghp_uvwxyz456"],
        },
    )
    assert create_response.status_code == 200
    template_summary = create_response.json()
    template_id = template_summary["id"]
    assert template_summary["name"] == "github-token-template-a"
    assert template_summary["github_token_count"] == 2
    assert template_summary["github_token_previews"] == ["ghp_ab...", "ghp_uv..."]

    list_response = client.get("/api/stage1/templates")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["templates"]] == [template_id]

    detail_response = client.get(f"/api/stage1/templates/{template_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["snapshot"] == {
        "github_tokens": ["ghp_abcdef123", "ghp_uvwxyz456"],
    }

    update_response = client.post(
        "/api/stage1/templates",
        json={"name": "github-token-template-a", "github_tokens": ["ghp_newvalue789"]},
    )
    assert update_response.status_code == 200
    assert update_response.json()["id"] == template_id
    assert update_response.json()["github_token_count"] == 1

    delete_response = client.delete(f"/api/stage1/templates/{template_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["name"] == "github-token-template-a"

    list_after_delete = client.get("/api/stage1/templates")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["templates"] == []


def test_stage4_runtime_templates_round_trip(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage4-templates.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    runtime = client.get("/api/stage4/runtime")
    assert runtime.status_code == 200
    assert "defaults" not in runtime.json()

    create_response = client.post(
        "/api/stage4/runtime/templates",
        json={
            "name": "issuer-template-a",
            "max_concurrent_runs": 64,
            "issuer_model": "openai/gpt-5.4",
            "issuer_base_url": "https://example.com/v1",
            "issuer_api_key": "sk-stage4-secret",
            "issuer_preset": "gpt5",
            "issuer_max_iterations": 150,
            "issuer_timeout_seconds": 1800,
            "build_timeout_seconds": 2400,
        },
    )
    assert create_response.status_code == 200
    template_summary = create_response.json()
    assert template_summary["name"] == "issuer-template-a"
    assert template_summary["issuer_model"] == "openai/gpt-5.4"
    template_id = template_summary["id"]

    list_response = client.get("/api/stage4/runtime/templates")
    assert list_response.status_code == 200
    assert [item["id"] for item in list_response.json()["templates"]] == [template_id]

    detail_response = client.get(f"/api/stage4/runtime/templates/{template_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["snapshot"]["concurrency"]["max_concurrent_runs"] == 64
    assert detail_payload["snapshot"]["issuer"]["model"] == "openai/gpt-5.4"
    assert detail_payload["snapshot"]["issuer"]["api_key"] == "sk-stage4-secret"
    assert detail_payload["snapshot"]["issuer"]["api_key_preview"] == "sk-sta..."
    assert detail_payload["snapshot"]["hyperparameters"]["build_timeout_seconds"] == 2400
    assert "defaults" not in detail_payload["snapshot"]

    delete_response = client.delete(f"/api/stage4/runtime/templates/{template_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["name"] == "issuer-template-a"

    list_after_delete = client.get("/api/stage4/runtime/templates")
    assert list_after_delete.status_code == 200
    assert list_after_delete.json()["templates"] == []


def test_stage_runtime_config_persists_across_app_restart(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'stage-runtime-persistence.db'}"
    stage2_payload = {
        "max_concurrent_runs": 2,
        "planner_model": "planner-persisted",
        "planner_base_url": "https://planner.example/v1",
        "planner_api_key": "planner-key",
        "planner_preset": "gpt5",
        "planner_max_iterations": 101,
        "planner_timeout_seconds": 1201,
        "worker_model": "worker-persisted",
        "worker_base_url": "https://worker.example/v1",
        "worker_api_key": "worker-key",
        "worker_preset": "default",
        "worker_max_iterations": 202,
        "worker_timeout_seconds": 2202,
        "max_worker_attempts": 7,
        "quickcheck_sample_size": 13,
        "entry_file_test_count_min": -1,
        "p2p_file_count_limit": 5,
        "collect_timeout_seconds": 301,
        "run_test_timeout_seconds": 302,
        "build_timeout_seconds": 1801,
        "full_validation_timeout_seconds": 3601,
    }
    stage3_payload = {
        "max_concurrent_runs": 8,
        "breaker_model": "breaker-persisted",
        "breaker_base_url": "https://breaker.example/v1",
        "breaker_api_key": "breaker-key",
        "breaker_preset": "gpt5",
        "breaker_max_iterations": 303,
        "breaker_timeout_seconds": 3303,
        "build_timeout_seconds": 1803,
        "run_test_timeout_seconds": 403,
        "full_validation_timeout_seconds": 3603,
        "entry_pass_rate_ceiling": 0.25,
        "min_removed_code_lines": 17,
    }
    stage4_payload = {
        "max_concurrent_runs": 2,
        "issuer_model": "issuer-persisted",
        "issuer_base_url": "https://issuer.example/v1",
        "issuer_api_key": "issuer-key",
        "issuer_preset": "gpt5",
        "issuer_max_iterations": 404,
        "issuer_timeout_seconds": 4404,
        "build_timeout_seconds": 1804,
    }

    stage2_runner = DummyRunner()
    stage2_runner.max_limit = 24
    stage2_runner.max_workers = 24
    stage3_runner = DummyRunner()
    stage3_runner.max_limit = 24
    stage3_runner.max_workers = 24
    stage4_runner = FalseScheduleStage4Runner()

    with TestClient(
        create_app(
            Settings(database_url=database_url),
            runner=DummyRunner(),
            stage2_runner=stage2_runner,
            stage3_runner=stage3_runner,
            stage4_runner=stage4_runner,
            batch_runner=DummyBatchRunner(),
        )
    ) as client:
        stage2_response = client.patch(
            "/api/stage2/runtime",
            json=stage2_payload,
        )
        assert stage2_response.status_code == 200
        stage3_response = client.patch(
            "/api/stage3/runtime",
            json=stage3_payload,
        )
        assert stage3_response.status_code == 200
        stage4_response = client.patch(
            "/api/stage4/runtime",
            json=stage4_payload,
        )
        assert stage4_response.status_code == 200
        stage2_before_restart = stage2_response.json()
        stage3_before_restart = stage3_response.json()
        stage4_before_restart = stage4_response.json()

        session = client.app.state.session_factory()
        try:
            stage2_row = session.get(GlobalRuntimeConfig, server_module.CURRENT_STAGE2_RUNTIME_CONFIG_KEY)
            stage3_row = session.get(GlobalRuntimeConfig, server_module.CURRENT_STAGE3_RUNTIME_CONFIG_KEY)
            stage4_row = session.get(GlobalRuntimeConfig, server_module.CURRENT_STAGE4_RUNTIME_CONFIG_KEY)
            assert stage2_row is not None
            assert stage2_row.config_json["concurrency"]["max_concurrent_runs"] == 2
            assert stage2_row.config_json["planner"]["api_key"] == "planner-key"
            assert stage2_row.config_json["worker"]["api_key"] == "worker-key"
            assert stage2_row.config_json["planner"]["timeout_seconds"] == 1201
            assert stage2_row.config_json["worker"]["timeout_seconds"] == 2202
            assert stage2_row.config_json["hyperparameters"] == {
                "max_worker_attempts": 7,
                "quickcheck_sample_size": 13,
                "entry_file_test_count_min": -1,
                "p2p_file_count_limit": 5,
                "p2p_sample_seed": None,
                "collect_timeout_seconds": 301,
                "run_test_timeout_seconds": 302,
                "build_timeout_seconds": 1801,
                "full_validation_timeout_seconds": 3601,
            }
            assert stage3_row is not None
            assert stage3_row.config_json["concurrency"]["max_concurrent_runs"] == 8
            assert stage3_row.config_json["breaker"]["api_key"] == "breaker-key"
            assert stage3_row.config_json["breaker"]["timeout_seconds"] == 3303
            assert stage3_row.config_json["hyperparameters"] == {
                "build_timeout_seconds": 1803,
                "run_test_timeout_seconds": 403,
                "full_validation_timeout_seconds": 3603,
                "entry_pass_rate_ceiling": 0.25,
                "min_removed_code_lines": 17,
            }
            assert stage4_row is not None
            assert stage4_row.config_json["concurrency"]["max_concurrent_runs"] == 2
            assert stage4_row.config_json["issuer"]["api_key"] == "issuer-key"
            assert stage4_row.config_json["issuer"]["timeout_seconds"] == 4404
            assert stage4_row.config_json["hyperparameters"] == {
                "build_timeout_seconds": 1804,
            }
        finally:
            session.close()

    restarted_stage2_runner = DummyRunner()
    restarted_stage2_runner.max_limit = 24
    restarted_stage2_runner.max_workers = 24
    restarted_stage3_runner = DummyRunner()
    restarted_stage3_runner.max_limit = 24
    restarted_stage3_runner.max_workers = 24
    restarted_stage4_runner = FalseScheduleStage4Runner()

    with TestClient(
        create_app(
            Settings(database_url=database_url),
            runner=DummyRunner(),
            stage2_runner=restarted_stage2_runner,
            stage3_runner=restarted_stage3_runner,
            stage4_runner=restarted_stage4_runner,
            batch_runner=DummyBatchRunner(),
        )
    ) as restarted:
        stage2 = restarted.get("/api/stage2/runtime").json()
        stage3 = restarted.get("/api/stage3/runtime").json()
        stage4 = restarted.get("/api/stage4/runtime").json()

    assert stage2["concurrency"]["max_concurrent_runs"] == 2
    assert stage2["planner"] == stage2_before_restart["planner"]
    assert stage2["worker"] == stage2_before_restart["worker"]
    assert stage2["hyperparameters"] == stage2_before_restart["hyperparameters"]
    assert stage3["concurrency"]["max_concurrent_runs"] == 8
    assert stage3["breaker"] == stage3_before_restart["breaker"]
    assert stage3["hyperparameters"] == stage3_before_restart["hyperparameters"]
    assert stage4["concurrency"]["max_concurrent_runs"] == 2
    assert stage4["issuer"] == stage4_before_restart["issuer"]
    assert stage4["hyperparameters"] == stage4_before_restart["hyperparameters"]


def test_data_pool_service_materializes_generated_stage4_run_once(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-pool-materialize.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    savepoint_id = _seed_stage4_source_savepoint(client)
    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).create_pool(
            name="python-pool",
            root_path=str(tmp_path / "pool"),
            description="Python repos",
        )
        broken_repo = tmp_path / "stage4-run" / "repo"
        (broken_repo / "tests").mkdir(parents=True)
        f2p_content = b"def test_app_context():\n    assert broken_behavior()\n"
        p2p_content = b"def test_basic():\n    assert stable_behavior()\n"
        (broken_repo / "tests" / "test_appctx.py").write_bytes(f2p_content)
        (broken_repo / "tests" / "test_basic.py").write_bytes(p2p_content)
        run = Stage4Run(
            source_savepoint_id=savepoint_id,
            status=Stage4RunStatus.completed.value,
            result=Stage4RunResult.generated.value,
            trigger_kind="batch",
            phase="completed",
            issue_variant_count=3,
            hint_variant_count=0,
            summary_json={},
        )
        session.add(run)
        session.flush()
        runtime_dir = tmp_path / "stage4" / "runtime" / run.id
        run.workspace_path = str(tmp_path / "stage4" / "runs" / run.id)
        run.runtime_snapshot_json = {
            "stage4": {
                "broken_repo_dir": str(broken_repo),
                "workspace": {"repo_dir": str(broken_repo)},
                "generation_config": load_issue_style_catalog().snapshot(seed=run.id),
            }
        }
        for role in ("swe", "fb", "hint"):
            archive_file = runtime_dir / "llm-completions" / role / "completion.json"
            archive_file.parent.mkdir(parents=True, exist_ok=True)
            archive_file.write_text(json.dumps({"role": role}), encoding="utf-8")
        session.add(
            Stage4IssueVariant(
                run_id=run.id,
                variant_index=1,
                style="swe",
                title="Broken context handling",
                issue_markdown="Issue body",
                issue_json={"title": "Broken context handling"},
                quality_json={},
                leakage_check_json={},
            )
        )
        session.add(
            Stage4IssueVariant(
                run_id=run.id,
                variant_index=2,
                style="fb",
                title="Implement context handling",
                issue_markdown=(
                    "## Task\nRestore context handling.\n\n"
                    "## Interface Descriptions\nPreserve the existing public interface."
                ),
                issue_json={"title": "Implement context handling", "style": "fb"},
                quality_json={},
                leakage_check_json={},
            )
        )
        session.add(
            Stage4IssueVariant(
                run_id=run.id,
                variant_index=3,
                style="hint",
                title="Context handling fails in nested usage",
                issue_markdown="Issue body with focused diagnostic guidance.",
                issue_json={"title": "Context handling fails in nested usage", "style": "hint"},
                quality_json={},
                leakage_check_json={},
            )
        )
        session.flush()

        service = DataPoolService(session)
        asset = service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=run.id, batch_task_id="batch-1")
        duplicate = service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=run.id, batch_task_id="batch-1")
        session.commit()

        assert duplicate.id == asset.id
        asset_folder = Path(asset.folder_path)
        assert asset_folder.is_dir()
        assert (asset_folder / "manifest.json").is_file()
        assert (asset_folder / "issues.json").is_file()
        assert not (asset_folder / "hints.json").exists()
        manifest = json.loads((asset_folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["issue_schema_version"] == 2
        issues = json.loads((asset_folder / "issues.json").read_text(encoding="utf-8"))
        assert [issue["variant_index"] for issue in issues] == [1, 2, 3]
        assert [issue["style"] for issue in issues] == ["swe", "fb", "hint"]
        assert {
            path.relative_to(asset_folder).as_posix()
            for path in (asset_folder / "llm").rglob("*.json")
        } == {
            "llm/swe/completion.json",
            "llm/fb/completion.json",
            "llm/hint/completion.json",
        }
        assert all(
            {"variant_index", "style", "title", "issue_markdown", "issue_json", "quality", "leakage_check"}
            <= set(issue)
            for issue in issues
        )
        assert (asset_folder / "dockerfile").read_text(encoding="utf-8") == "FROM python:3.11\n"
        assert (asset_folder / "run_script.sh").read_text(encoding="utf-8") == "#!/usr/bin/env bash\nexit 0\n"
        hidden_manifest = json.loads((asset_folder / "hidden_f2p_files.json").read_text(encoding="utf-8"))
        assert hidden_manifest == [
            {
                "path": "tests/test_appctx.py",
                "storage_path": f"hidden_f2p_files/0001-{hashlib.sha256(f2p_content).hexdigest()[:16]}",
                "sha256": hashlib.sha256(f2p_content).hexdigest(),
                "size_bytes": len(f2p_content),
            }
        ]
        assert (asset_folder / hidden_manifest[0]["storage_path"]).read_bytes() == f2p_content
        p2p_manifest = json.loads((asset_folder / "p2p_file_snapshots.json").read_text(encoding="utf-8"))
        assert p2p_manifest == [
            {
                "path": "tests/test_basic.py",
                "storage_path": f"p2p_file_snapshots/0001-{hashlib.sha256(p2p_content).hexdigest()[:16]}",
                "sha256": hashlib.sha256(p2p_content).hexdigest(),
                "size_bytes": len(p2p_content),
            }
        ]
        assert (asset_folder / p2p_manifest[0]["storage_path"]).read_bytes() == p2p_content
        assert pool.stats_json["asset_count"] == 1
    finally:
        session.close()


def test_data_pool_materialize_overwrites_existing_asset_for_same_key(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-pool-materialize-overwrite.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    savepoint_id = _seed_stage4_source_savepoint(client)
    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).create_pool(
            name="python-pool",
            root_path=str(tmp_path / "pool"),
            description="Python repos",
        )

        def add_generated_run(run_name: str, *, issue_title: str, f2p_text: bytes, p2p_text: bytes) -> str:
            broken_repo = tmp_path / run_name / "repo"
            (broken_repo / "tests").mkdir(parents=True)
            (broken_repo / "tests" / "test_appctx.py").write_bytes(f2p_text)
            (broken_repo / "tests" / "test_basic.py").write_bytes(p2p_text)
            run = Stage4Run(
                source_savepoint_id=savepoint_id,
                status=Stage4RunStatus.completed.value,
                result=Stage4RunResult.generated.value,
                trigger_kind="manual",
                phase="completed",
                workspace_path=str(tmp_path / run_name),
                issue_variant_count=3,
                hint_variant_count=0,
                summary_json={},
            )
            session.add(run)
            session.flush()
            run.runtime_snapshot_json = {
                "stage4": {
                    "broken_repo_dir": str(broken_repo),
                    "workspace": {"repo_dir": str(broken_repo)},
                    "generation_config": load_issue_style_catalog().snapshot(seed=run.id),
                }
            }
            for variant_index, style in enumerate(("swe", "fb", "hint"), start=1):
                title = issue_title if variant_index == 1 else f"{issue_title} ({style})"
                session.add(
                    Stage4IssueVariant(
                        run_id=run.id,
                        variant_index=variant_index,
                        style=style,
                        title=title,
                        issue_markdown=f"{title} body",
                        issue_json={"title": title, "style": style},
                        quality_json={},
                        leakage_check_json={},
                    )
                )
            session.flush()
            return run.id

        first_run_id = add_generated_run(
            "stage4-run-first",
            issue_title="First issue",
            f2p_text=b"def test_app_context():\n    assert first_broken_behavior()\n",
            p2p_text=b"def test_basic():\n    assert first_stable_behavior()\n",
        )
        second_f2p = b"def test_app_context():\n    assert second_broken_behavior()\n"
        second_p2p = b"def test_basic():\n    assert second_stable_behavior()\n"
        second_run_id = add_generated_run(
            "stage4-run-second",
            issue_title="Second issue",
            f2p_text=second_f2p,
            p2p_text=second_p2p,
        )

        service = DataPoolService(session)
        first_asset = service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=first_run_id)
        first_asset_id = first_asset.id
        first_folder = Path(first_asset.folder_path)
        (first_folder / "stale-file.txt").write_text("old content", encoding="utf-8")

        reused_asset = service.materialize_stage4_run(
            pool_id=pool.id,
            stage4_run_id=second_run_id,
            reuse_compatible_existing=True,
        )
        assert reused_asset.id == first_asset_id
        assert reused_asset.stage4_run_id == first_run_id
        assert (first_folder / "stale-file.txt").is_file()

        second_asset = service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=second_run_id)
        session.commit()

        assert second_asset.id == first_asset_id
        asset_folder = Path(second_asset.folder_path)
        assert asset_folder == first_folder
        assert not (asset_folder / "stale-file.txt").exists()
        manifest = json.loads((asset_folder / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["asset_id"] == first_asset_id
        assert manifest["stage4_run_id"] == second_run_id
        assert json.loads((asset_folder / "issues.json").read_text(encoding="utf-8"))[0]["title"] == "Second issue"
        hidden_manifest = json.loads((asset_folder / "hidden_f2p_files.json").read_text(encoding="utf-8"))
        p2p_manifest = json.loads((asset_folder / "p2p_file_snapshots.json").read_text(encoding="utf-8"))
        assert (asset_folder / hidden_manifest[0]["storage_path"]).read_bytes() == second_f2p
        assert (asset_folder / p2p_manifest[0]["storage_path"]).read_bytes() == second_p2p
        assets = DataPoolService(session).list_assets(pool.id)
        assert assets["pagination"]["total"] == 1
        assert assets["assets"][0]["stage4_run_id"] == second_run_id
    finally:
        session.close()


def test_data_pool_materialize_restores_existing_asset_when_refresh_fails(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-pool-materialize-rollback.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    savepoint_id = _seed_stage4_source_savepoint(client)
    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).create_pool(
            name="python-pool",
            root_path=str(tmp_path / "pool"),
            description="Python repos",
        )

        def add_generated_run(run_name: str, *, issue_title: str, f2p_text: bytes, p2p_text: bytes) -> str:
            broken_repo = tmp_path / run_name / "repo"
            (broken_repo / "tests").mkdir(parents=True)
            (broken_repo / "tests" / "test_appctx.py").write_bytes(f2p_text)
            (broken_repo / "tests" / "test_basic.py").write_bytes(p2p_text)
            run = Stage4Run(
                source_savepoint_id=savepoint_id,
                status=Stage4RunStatus.completed.value,
                result=Stage4RunResult.generated.value,
                trigger_kind="manual",
                phase="completed",
                workspace_path=str(tmp_path / run_name),
                issue_variant_count=3,
                hint_variant_count=0,
                summary_json={},
            )
            session.add(run)
            session.flush()
            run.runtime_snapshot_json = {
                "stage4": {
                    "broken_repo_dir": str(broken_repo),
                    "workspace": {"repo_dir": str(broken_repo)},
                    "generation_config": load_issue_style_catalog().snapshot(seed=run.id),
                }
            }
            for variant_index, style in enumerate(("swe", "fb", "hint"), start=1):
                title = issue_title if variant_index == 1 else f"{issue_title} ({style})"
                session.add(
                    Stage4IssueVariant(
                        run_id=run.id,
                        variant_index=variant_index,
                        style=style,
                        title=title,
                        issue_markdown=f"{title} body",
                        issue_json={"title": title, "style": style},
                        quality_json={},
                        leakage_check_json={},
                    )
                )
            session.flush()
            return run.id

        first_f2p = b"def test_app_context():\n    assert first_broken_behavior()\n"
        first_p2p = b"def test_basic():\n    assert first_stable_behavior()\n"
        first_run_id = add_generated_run(
            "stage4-run-first",
            issue_title="First issue",
            f2p_text=first_f2p,
            p2p_text=first_p2p,
        )
        second_run_id = add_generated_run(
            "stage4-run-second",
            issue_title="Second issue",
            f2p_text=b"def test_app_context():\n    assert second_broken_behavior()\n",
            p2p_text=b"def test_basic():\n    assert second_stable_behavior()\n",
        )

        service = DataPoolService(session)
        first_asset = service.materialize_stage4_run(pool_id=pool.id, stage4_run_id=first_run_id)
        first_asset_id = first_asset.id
        asset_folder = Path(first_asset.folder_path)
        session.commit()

        DataPoolService(session).materialize_stage4_run(pool_id=pool.id, stage4_run_id=second_run_id)
        session.rollback()
        manifest = json.loads((asset_folder / "manifest.json").read_text(encoding="utf-8"))
        hidden_manifest = json.loads((asset_folder / "hidden_f2p_files.json").read_text(encoding="utf-8"))
        p2p_manifest = json.loads((asset_folder / "p2p_file_snapshots.json").read_text(encoding="utf-8"))
        db_asset = session.get(DataPoolAsset, first_asset_id)
        assert manifest["stage4_run_id"] == first_run_id
        assert (asset_folder / hidden_manifest[0]["storage_path"]).read_bytes() == first_f2p
        assert (asset_folder / p2p_manifest[0]["storage_path"]).read_bytes() == first_p2p
        assert db_asset is not None
        assert db_asset.stage4_run_id == first_run_id

        def fail_refresh(_self, pool_id):
            raise RuntimeError(f"refresh failed for {pool_id}")

        monkeypatch.setattr(DataPoolService, "refresh_pool_stats", fail_refresh)

        with pytest.raises(RuntimeError, match="refresh failed"):
            DataPoolService(session).materialize_stage4_run(pool_id=pool.id, stage4_run_id=second_run_id)

        manifest = json.loads((asset_folder / "manifest.json").read_text(encoding="utf-8"))
        hidden_manifest = json.loads((asset_folder / "hidden_f2p_files.json").read_text(encoding="utf-8"))
        p2p_manifest = json.loads((asset_folder / "p2p_file_snapshots.json").read_text(encoding="utf-8"))
        db_asset = session.get(DataPoolAsset, first_asset_id)

        assert manifest["stage4_run_id"] == first_run_id
        assert (asset_folder / hidden_manifest[0]["storage_path"]).read_bytes() == first_f2p
        assert (asset_folder / p2p_manifest[0]["storage_path"]).read_bytes() == first_p2p
        assert db_asset is not None
        assert db_asset.stage4_run_id == first_run_id
    finally:
        session.close()


def test_data_pool_materialize_fails_without_broken_repo_for_f2p(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-pool-missing-broken-repo.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    savepoint_id = _seed_stage4_source_savepoint(client)
    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).create_pool(
            name="python-pool",
            root_path=str(tmp_path / "pool"),
            description="Python repos",
        )
        run = Stage4Run(
            source_savepoint_id=savepoint_id,
            status=Stage4RunStatus.completed.value,
            result=Stage4RunResult.generated.value,
            trigger_kind="batch",
            phase="completed",
            workspace_path=str(tmp_path / "stage4-run-missing"),
            issue_variant_count=0,
            hint_variant_count=0,
            runtime_snapshot_json={"stage4": {"broken_repo_dir": str(tmp_path / "missing-repo")}},
            summary_json={},
        )
        session.add(run)
        session.flush()

        with pytest.raises(FileNotFoundError, match="missing a broken repo directory"):
            DataPoolService(session).materialize_stage4_run(
                pool_id=pool.id,
                stage4_run_id=run.id,
                batch_task_id="batch-1",
            )
    finally:
        session.close()


def test_data_pool_api_lists_downloads_and_deletes_assets(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'data-pool-api.db'}")
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    pool_root = tmp_path / "pool"

    create_response = client.post(
        "/api/data-pools",
        json={"name": "python-assets", "root_path": str(pool_root), "description": "test assets"},
    )
    assert create_response.status_code == 200
    pool_id = create_response.json()["pool"]["id"]

    session = client.app.state.session_factory()
    try:
        asset_a_dir = pool_root / "owner__repo__aaa111__tests_test_a_py__depth-1"
        asset_b_dir = pool_root / "owner__repo__bbb222__tests_test_b_py__depth-2"
        asset_c_dir = pool_root / "other__repo__ccc333__tests_test_c_py__depth-1"
        for folder, marker, issue_count, entry_pass_rate, p2p_count, f2p_count, patch_text in (
            (
                asset_a_dir,
                "a",
                0,
                0.5,
                10,
                2,
                "diff --git a/a b/a\n+line-a\n-line-b\n",
            ),
            (
                asset_b_dir,
                "b",
                1,
                0.25,
                19,
                3,
                "diff --git a/a b/a\n+line-a\n"
                "diff --git a/b b/b\n+line-b\n-line-c\n",
            ),
            (
                asset_c_dir,
                "c",
                0,
                0.1,
                5,
                5,
                "diff --git a/a b/a\n+line-a\n-line-b\n",
            ),
        ):
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(f'{{"marker": "{marker}"}}', encoding="utf-8")
            (folder / "issues.json").write_text(json.dumps([{"id": idx} for idx in range(issue_count)]), encoding="utf-8")
            (folder / "dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")
            (folder / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            (folder / "gold.patch").write_text(patch_text, encoding="utf-8")
            (folder / "savepoint_feedback.json").write_text(
                json.dumps(
                    {
                        "entry_pass_rate": entry_pass_rate,
                        "p2p_count": p2p_count,
                        "f2p_count": f2p_count,
                    }
                ),
                encoding="utf-8",
            )

        asset_a = DataPoolAsset(
            pool_id=pool_id,
            github_repo_id=9001,
            repo_full_name="owner/repo",
            source_commit_sha="aaa111",
            language="Python",
            stars=42,
            entry_file_path="tests/test_a.py",
            depth=1,
            stage2_run_id="stage2-a",
            stage3_run_id="stage3-a",
            stage3_savepoint_id=1,
            stage4_run_id="stage4-a",
            folder_name=asset_a_dir.name,
            folder_path=str(asset_a_dir),
            manifest_json={"marker": "a"},
        )
        asset_b = DataPoolAsset(
            pool_id=pool_id,
            github_repo_id=9001,
            repo_full_name="owner/repo",
            source_commit_sha="bbb222",
            language="Python",
            stars=42,
            entry_file_path="tests/test_b.py",
            depth=2,
            stage2_run_id="stage2-b",
            stage3_run_id="stage3-b",
            stage3_savepoint_id=2,
            stage4_run_id="stage4-b",
            folder_name=asset_b_dir.name,
            folder_path=str(asset_b_dir),
            manifest_json={"marker": "b"},
        )
        asset_c = DataPoolAsset(
            pool_id=pool_id,
            github_repo_id=9002,
            repo_full_name="other/repo",
            source_commit_sha="ccc333",
            language="TypeScript",
            stars=7,
            entry_file_path="tests/test_c.py",
            depth=1,
            stage2_run_id="stage2-c",
            stage3_run_id="stage3-c",
            stage3_savepoint_id=3,
            stage4_run_id="stage4-c",
            folder_name=asset_c_dir.name,
            folder_path=str(asset_c_dir),
            manifest_json={"marker": "c"},
        )
        session.add_all([asset_a, asset_b, asset_c])
        session.commit()
        asset_b_id = asset_b.id
    finally:
        session.close()

    list_response = client.get(f"/api/data-pools/{pool_id}/assets", params={"query": "owner/repo", "page_size": 1})
    assert list_response.status_code == 200
    assert list_response.json()["pagination"]["total"] == 2
    assert len(list_response.json()["assets"]) == 1

    filtered_response = client.get(
        f"/api/data-pools/{pool_id}/assets",
        params={
            "commit": "bbb222",
            "entry_file": "test_b",
            "depth_min": 2,
            "depth_max": 2,
            "stars_min": 40,
            "stars_max": 50,
            "stage4_run_id": "stage4-b",
        },
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["pagination"]["total"] == 1
    assert filtered_payload["assets"][0]["id"] == asset_b_id
    assert filtered_payload["assets"][0]["entry_pass_rate"] == 0.25
    assert filtered_payload["assets"][0]["p2p_count"] == 19
    assert filtered_payload["assets"][0]["f2p_count"] == 3
    assert filtered_payload["assets"][0]["issue_count"] == 1
    assert "hint_count" not in filtered_payload["assets"][0]
    assert filtered_payload["assets"][0]["diff_stats"]["lines_changed"] == 3
    assert filtered_payload["assets"][0]["diff_stats"]["files_changed"] == 2

    metric_sort_response = client.get(
        f"/api/data-pools/{pool_id}/assets",
        params={"sort_by": "entry_pass_rate", "sort_order": "asc"},
    )
    assert metric_sort_response.status_code == 200
    metric_sorted_assets = metric_sort_response.json()["assets"]
    assert [asset["entry_file_path"] for asset in metric_sorted_assets] == [
        "tests/test_c.py",
        "tests/test_b.py",
        "tests/test_a.py",
    ]

    issue_sort_response = client.get(
        f"/api/data-pools/{pool_id}/assets",
        params={"sort_by": "issue_count", "sort_order": "desc"},
    )
    assert issue_sort_response.status_code == 200
    issue_sorted_assets = issue_sort_response.json()["assets"]
    assert [asset["entry_file_path"] for asset in issue_sorted_assets] == [
        "tests/test_b.py",
        "tests/test_c.py",
        "tests/test_a.py",
    ]

    commit_sort_response = client.get(
        f"/api/data-pools/{pool_id}/assets",
        params={"sort_by": "commit", "sort_order": "asc"},
    )
    assert commit_sort_response.status_code == 200
    commit_sorted_assets = commit_sort_response.json()["assets"]
    assert [asset["source_commit_sha"] for asset in commit_sorted_assets] == [
        "aaa111",
        "bbb222",
        "ccc333",
    ]

    selection_response = client.get(
        f"/api/data-pools/{pool_id}/assets/selection",
        params={"query": "owner/repo"},
    )
    assert selection_response.status_code == 200
    selection_payload = selection_response.json()
    assert selection_payload["total"] == 2
    assert len(selection_payload["asset_ids"]) == 2
    assert asset_b_id in selection_payload["asset_ids"]

    detail_response = client.get(f"/api/data-pools/{pool_id}/assets/{asset_b_id}")
    assert detail_response.status_code == 200
    assert detail_response.json()["asset"]["files"]["manifest"] == {"marker": "b"}
    assert detail_response.json()["asset"]["entry_pass_rate"] == 0.25
    assert detail_response.json()["asset"]["issue_count"] == 1
    assert "hint_count" not in detail_response.json()["asset"]

    preview_response = client.get(f"/api/data-pools/{pool_id}/preview")
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["summary"]["asset_count"] == 3
    assert preview_payload["summary"]["repo_count"] == 2
    assert preview_payload["summary"]["entry_count"] == 3
    assert preview_payload["summary"]["max_depth"] == 2
    assert "latest_created_at" not in preview_payload["summary"]
    assert preview_payload["depth_distribution"] == [
        {"depth": 1, "asset_count": 2},
        {"depth": 2, "asset_count": 1},
    ]
    assert preview_payload["patch_distribution"]["buckets"][0]["label"] == "0-9"
    assert preview_payload["patch_distribution"]["buckets"][0]["asset_count"] == 3
    assert preview_payload["patch_file_distribution"]["buckets"][1]["label"] == "1"
    assert preview_payload["patch_file_distribution"]["buckets"][1]["asset_count"] == 2
    assert preview_payload["patch_file_distribution"]["buckets"][2]["label"] == "2"
    assert preview_payload["patch_file_distribution"]["buckets"][2]["asset_count"] == 1
    assert preview_payload["patch_file_distribution"]["stats"]["max"] == 2
    assert preview_payload["repo_asset_distribution"]["buckets"][0]["label"] == "1"
    assert preview_payload["repo_asset_distribution"]["buckets"][0]["repo_count"] == 1
    assert preview_payload["repo_asset_distribution"]["buckets"][1]["label"] == "2"
    assert preview_payload["repo_asset_distribution"]["buckets"][1]["repo_count"] == 1
    repos = preview_payload["repo_asset_distribution"]["repos"]
    assert [repo["repo_full_name"] for repo in repos] == ["owner/repo", "other/repo"]
    assert repos[0]["asset_count"] == 2
    assert repos[0]["entry_count"] == 2
    assert repos[1]["asset_count"] == 1
    assert preview_payload["quality"]["f2p_distribution"]["buckets"][2]["label"] == "2"
    assert preview_payload["quality"]["f2p_distribution"]["buckets"][2]["asset_count"] == 1
    assert "top_repos" not in preview_payload["repo_asset_distribution"]
    assert "risk_counts" not in preview_payload["quality"]
    assert "risky_assets" not in preview_payload
    assert "latest_assets" not in preview_payload

    download_response = client.get(f"/api/data-pools/{pool_id}/download.zip")
    assert download_response.status_code == 200
    with zipfile.ZipFile(BytesIO(download_response.content)) as archive:
        names = set(archive.namelist())
    assert f"{asset_b_dir.name}/manifest.json" in names
    assert f"{asset_a_dir.name}/gold.patch" in names

    selected_download_response = client.post(
        f"/api/data-pools/{pool_id}/download-selection.zip",
        json={"asset_ids": [asset_b_id]},
    )
    assert selected_download_response.status_code == 200
    with zipfile.ZipFile(BytesIO(selected_download_response.content)) as archive:
        selected_names = set(archive.namelist())
    assert f"{asset_b_dir.name}/manifest.json" in selected_names
    assert f"{asset_a_dir.name}/manifest.json" not in selected_names

    selection_create_response = client.post(
        f"/api/data-pools/{pool_id}/download-selections",
        json={"asset_ids": [asset_b_id]},
    )
    assert selection_create_response.status_code == 200
    selection_id = selection_create_response.json()["id"]

    selection_download_response = client.get(
        f"/api/data-pools/{pool_id}/download-selections/{selection_id}.zip",
    )
    assert selection_download_response.status_code == 200
    with zipfile.ZipFile(BytesIO(selection_download_response.content)) as archive:
        selection_route_names = set(archive.namelist())
    assert f"{asset_b_dir.name}/manifest.json" in selection_route_names
    assert f"{asset_a_dir.name}/manifest.json" not in selection_route_names

    delete_entry_response = client.post(
        f"/api/data-pools/{pool_id}/assets/delete",
        json={"repo": "owner/repo", "entry_file": "tests/test_a.py"},
    )
    assert delete_entry_response.status_code == 200
    assert delete_entry_response.json()["deleted_count"] == 1
    assert not asset_a_dir.exists()
    assert asset_b_dir.exists()

    delete_selected_response = client.post(
        f"/api/data-pools/{pool_id}/assets/delete",
        json={"asset_ids": [asset_b_id]},
    )
    assert delete_selected_response.status_code == 200
    assert delete_selected_response.json()["deleted_count"] == 1
    assert not asset_b_dir.exists()


def test_data_pool_api_rejects_existing_and_project_core_new_pool_paths(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'data-pool-path-rules.db'}",
        default_data_pool_root=tmp_path / "default-pool",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))

    existing_root = tmp_path / "existing-pool"
    existing_root.mkdir()
    existing_response = client.post(
        "/api/data-pools",
        json={"name": "existing-path", "root_path": str(existing_root)},
    )
    assert existing_response.status_code == 400
    assert "must not already exist" in existing_response.json()["detail"]

    protected_root = Path.cwd() / "src" / f"new-data-pool-for-test-{tmp_path.name}"
    assert not protected_root.exists()
    protected_response = client.post(
        "/api/data-pools",
        json={"name": "protected-path", "root_path": str(protected_root)},
    )
    assert protected_response.status_code == 400
    assert "project core directory: src" in protected_response.json()["detail"]

    valid_root = tmp_path / "valid-pool"
    valid_response = client.post(
        "/api/data-pools",
        json={"name": "valid-path", "root_path": str(valid_root)},
    )
    assert valid_response.status_code == 200
    assert valid_root.is_dir()


def test_batch_task_api_create_with_temporary_tokens_and_cancel(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-api.db'}")
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))

    pool_response = client.post(
        "/api/data-pools",
        json={"name": "batch-pool", "root_path": str(tmp_path / "batch-pool")},
    )
    assert pool_response.status_code == 200
    pool_id = pool_response.json()["pool"]["id"]

    create_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "nightly-python",
            "note": "smoke",
            "data_pool_id": pool_id,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "stars_min": 10,
                "repository_limit": 3,
                "exclude_forks": True,
                "exclude_archived": True,
                "licenses": ["mit"],
                "keywords": ["pytest"],
                "target_repositories": ["Owner/Repo", "owner/repo", "pallets/flask"],
            },
            "github_tokens": ["ghp_abcdef", "ghp_abcdef", "ghp_uvwxyz"],
            "max_concurrent_jobs": 10,
            "max_concurrent_partitions": 2,
            "stage2_runtime": {"planner_model": "planner-test"},
            "stage3_runtime": {"breaker_model": "breaker-test", "min_removed_code_lines": 13},
            "stage4_runtime": {"issuer_model": "issuer-test"},
        },
    )
    assert create_response.status_code == 200
    payload = create_response.json()
    task_id = payload["id"]
    assert payload["data_pool_id"] == pool_id
    assert payload["token_source"] == "temporary"
    assert payload["runtime_snapshot"]["stage1"]["max_concurrent_jobs"] == 10
    assert payload["runtime_snapshot"]["stage1"]["max_concurrent_partitions"] == 2
    assert payload["runtime_snapshot"]["pipeline"]["stop_after_stage"] == "stage4"
    assert payload["runtime_snapshot"]["stage2"]["planner"]["model"] == "planner-test"
    assert payload["runtime_snapshot"]["stage3"]["breaker"]["model"] == "breaker-test"
    assert payload["runtime_snapshot"]["stage3"]["hyperparameters"]["min_removed_code_lines"] == 13
    assert payload["runtime_snapshot"]["stage4"]["issuer"]["model"] == "issuer-test"
    assert "defaults" not in payload["runtime_snapshot"]["stage4"]
    assert payload["filters"]["target_repositories"] == ["Owner/Repo", "pallets/flask"]
    assert payload["filters"]["repository_limit"] == 3
    assert batch_runner.scheduled == [task_id]

    stage2_only_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "stage2-only",
            "data_pool_id": pool_id,
            "stop_after_stage": "stage2",
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "target_repositories": ["pallets/flask"],
            },
            "stage2_runtime": {"planner_model": "planner-luna"},
            "stage3_template_id": "unused-stage3-template",
            "stage4_template_id": "unused-stage4-template",
        },
    )
    assert stage2_only_response.status_code == 200
    stage2_only_payload = stage2_only_response.json()
    assert stage2_only_payload["runtime_snapshot"]["pipeline"]["stop_after_stage"] == "stage2"
    assert stage2_only_payload["runtime_snapshot"]["stage2"]["planner"]["model"] == "planner-luna"
    assert "stage3" not in stage2_only_payload["runtime_snapshot"]
    assert "stage4" not in stage2_only_payload["runtime_snapshot"]

    create_over_cap_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "too-wide-stage1",
            "data_pool_id": pool_id,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "target_repositories": ["pallets/flask"],
            },
            "max_concurrent_jobs": 25,
        },
    )
    assert create_over_cap_response.status_code == 422

    create_partition_over_cap_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "too-wide-stage1-partitions",
            "data_pool_id": pool_id,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "target_repositories": ["pallets/flask"],
            },
            "max_concurrent_partitions": 99,
        },
    )
    assert create_partition_over_cap_response.status_code == 400
    assert "max_concurrent_partitions cannot exceed" in create_partition_over_cap_response.json()["detail"]

    cancel_response = client.post(f"/api/batch/tasks/{task_id}/cancel")
    assert cancel_response.status_code == 200
    assert cancel_response.json()["cancel_requested"] is True


def test_batch_task_api_retry_reuses_stage1_result_and_reschedules(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-retry.db'}")
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))
    client.app.state.settings.stage4_issue_styles = "swe,fb"

    pool_response = client.post(
        "/api/data-pools",
        json={"name": "batch-retry-pool", "root_path": str(tmp_path / "batch-retry-pool")},
    )
    assert pool_response.status_code == 200
    pool_id = pool_response.json()["pool"]["id"]

    create_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "nightly-python",
            "note": "retry-me",
            "data_pool_id": pool_id,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "stars_min": 10,
                "exclude_forks": True,
                "exclude_archived": True,
                "licenses": ["mit"],
                "keywords": ["pytest"],
                "target_repositories": ["pallets/flask"],
            },
            "github_tokens": ["ghp_retry_one", "ghp_retry_two"],
            "max_concurrent_jobs": 2,
            "max_concurrent_partitions": 4,
            "stage2_runtime": {"planner_model": "planner-retry"},
            "stage3_runtime": {"breaker_model": "breaker-retry"},
            "stage4_runtime": {"issuer_model": "issuer-retry"},
        },
    )
    assert create_response.status_code == 200
    created_payload = create_response.json()
    source_task_id = created_payload["id"]
    original_runtime_snapshot = created_payload["runtime_snapshot"]
    original_issue_style_config = original_runtime_snapshot["stage4"]["issue_style_config"]
    assert original_issue_style_config["enabled_styles"] == ["swe", "fb"]
    assert original_issue_style_config["catalog_sha256"]
    assert batch_runner.scheduled == [source_task_id]

    active_retry_response = client.post(f"/api/batch/tasks/{source_task_id}/retry")
    assert active_retry_response.status_code == 409

    session = client.app.state.session_factory()
    try:
        task = session.get(BatchTask, source_task_id)
        assert task is not None
        crawl_job = CrawlJob(
            name="old-batch-stage1",
            status=CrawlJobStatus.completed.value,
            filters_json={},
            stats_json={},
        )
        session.add(crawl_job)
        repository = GitHubRepository(
            github_repo_id=998877,
            full_name="pallets/flask",
            owner_login="pallets",
            name="flask",
            html_url="https://github.com/pallets/flask",
            api_url="https://api.github.com/repos/pallets/flask",
            primary_language="Python",
            stargazers_count=10,
            raw_payload={},
        )
        session.add(repository)
        session.flush()
        task.stage1_crawl_job_id = crawl_job.id
        task.status = BatchTaskStatus.failed.value
        task.phase = "failed"
        task.finished_at = datetime.now(UTC)
        repo_row = BatchTaskRepository(
            task_id=source_task_id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language="Python",
            stars=repository.stargazers_count,
            status="failed",
            stage2_status="failed",
            stage3_status="failed",
            stage4_status="failed",
            stage2_run_id="old-stage2-run",
            source_stage2_run_id="old-source-stage2-run",
            stage3_snapshot_id="old-stage3-snapshot",
            stats_json={"asset_count": 1},
            error_message="old pipeline failure",
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(repo_row)
        session.flush()
        session.add(
            BatchTaskEntry(
                task_id=source_task_id,
                task_repository_id=repo_row.id,
                entry_file_path="tests/test_old.py",
                status="failed",
                stage3_run_id="old-stage3-run",
                error_message="old stage3 failure",
            )
        )
        session.commit()
        old_crawl_job_id = crawl_job.id
        old_repo_row_id = repo_row.id
    finally:
        session.close()

    stage2_runtime_response = client.patch(
        "/api/stage2/runtime",
        json={
            "max_concurrent_runs": 2,
            "planner_model": "planner-current",
            "worker_model": "worker-current",
        },
    )
    assert stage2_runtime_response.status_code == 200
    stage3_runtime_response = client.patch(
        "/api/stage3/runtime",
        json={
            "max_concurrent_runs": 8,
            "breaker_model": "breaker-current",
        },
    )
    assert stage3_runtime_response.status_code == 200
    client.app.state.settings.stage4_issue_styles = "hint"
    stage4_runtime_response = client.patch(
        "/api/stage4/runtime",
        json={
            "max_concurrent_runs": 2,
            "issuer_model": "issuer-current",
        },
    )
    assert stage4_runtime_response.status_code == 200

    original_retry_response = client.post(f"/api/batch/tasks/{source_task_id}/retry")
    assert original_retry_response.status_code == 200
    original_retry_payload = original_retry_response.json()
    assert (
        original_retry_payload["runtime_snapshot"]["stage2"]
        == original_runtime_snapshot["stage2"]
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage3"]
        == original_runtime_snapshot["stage3"]
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage4"]
        == original_runtime_snapshot["stage4"]
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage2"]["planner"]["model"]
        == "planner-retry"
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage3"]["breaker"]["model"]
        == "breaker-retry"
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage4"]["issuer"]["model"]
        == "issuer-retry"
    )
    assert (
        original_retry_payload["runtime_snapshot"]["stage4"]["issue_style_config"]
        == original_issue_style_config
    )
    assert batch_runner.scheduled == [source_task_id, source_task_id]

    retry_session = client.app.state.session_factory()
    try:
        retried_task = retry_session.get(BatchTask, source_task_id)
        assert retried_task is not None
        retried_task.status = BatchTaskStatus.failed.value
        retried_task.phase = "failed"
        retried_task.finished_at = datetime.now(UTC)
        retry_session.commit()
    finally:
        retry_session.close()

    invalid_retry_response = client.post(
        f"/api/batch/tasks/{source_task_id}/retry",
        json={"runtime_config_source": "unexpected"},
    )
    assert invalid_retry_response.status_code == 422

    retry_response = client.post(
        f"/api/batch/tasks/{source_task_id}/retry",
        json={"runtime_config_source": "current"},
    )
    assert retry_response.status_code == 200
    payload = retry_response.json()
    retried_task_id = payload["id"]
    assert retried_task_id == source_task_id
    assert payload["name"] == "nightly-python"
    assert payload["note"] == "retry-me"
    assert payload["data_pool_id"] == pool_id
    assert payload["status"] == BatchTaskStatus.pending.value
    assert payload["phase"] == "created"
    assert payload["stage1_crawl_job_id"] == old_crawl_job_id
    assert payload["token_source"] == "temporary"
    assert payload["runtime_snapshot"]["stage1"]["max_concurrent_jobs"] == 2
    assert payload["runtime_snapshot"]["stage1"]["max_concurrent_partitions"] == 4
    assert payload["runtime_snapshot"]["stage2"]["concurrency"]["max_concurrent_runs"] == 2
    assert payload["runtime_snapshot"]["stage2"]["planner"]["model"] == "planner-current"
    assert payload["runtime_snapshot"]["stage2"]["worker"]["model"] == "worker-current"
    assert payload["runtime_snapshot"]["stage3"]["concurrency"]["max_concurrent_runs"] == 8
    assert payload["runtime_snapshot"]["stage3"]["breaker"]["model"] == "breaker-current"
    assert payload["runtime_snapshot"]["stage4"]["concurrency"]["max_concurrent_runs"] == 2
    assert payload["runtime_snapshot"]["stage4"]["issuer"]["model"] == "issuer-current"
    assert payload["runtime_snapshot"]["stage4"]["issue_style_config"]["enabled_styles"] == [
        "hint"
    ]
    assert (
        payload["runtime_snapshot"]["stage4"]["issue_style_config"]["catalog_sha256"]
        != original_issue_style_config["catalog_sha256"]
    )
    assert "defaults" not in payload["runtime_snapshot"]["stage4"]
    assert payload["filters"]["target_repositories"] == ["pallets/flask"]
    assert payload["stats"]["repository_count"] == 1
    assert payload["repositories"][0]["id"] == old_repo_row_id
    assert payload["repositories"][0]["status"] == "pending"
    assert payload["repositories"][0]["stage2_status"] == "pending"
    assert payload["repositories"][0]["stage3_status"] == "pending"
    assert payload["repositories"][0]["stage4_status"] == "pending"
    assert payload["repositories"][0]["entries"] == []
    assert batch_runner.scheduled == [source_task_id, source_task_id, source_task_id]
    list_response = client.get("/api/batch/tasks")
    assert list_response.status_code == 200
    assert [task["id"] for task in list_response.json()["tasks"]] == [source_task_id]

    verify = client.app.state.session_factory()
    try:
        source_task = verify.get(BatchTask, source_task_id)
        assert source_task is not None
        assert source_task.status == BatchTaskStatus.pending.value
        assert source_task.phase == "created"
        assert source_task.stage1_crawl_job_id == old_crawl_job_id
        assert source_task.cancel_requested is False
        assert source_task.error_message is None
        assert source_task.runtime_snapshot_json["stage1"]["max_concurrent_jobs"] == 2
        assert source_task.runtime_snapshot_json["stage1"]["max_concurrent_partitions"] == 4
        assert source_task.runtime_snapshot_json["stage2"]["concurrency"]["max_concurrent_runs"] == 2
        assert source_task.runtime_snapshot_json["stage3"]["concurrency"]["max_concurrent_runs"] == 8
        assert source_task.runtime_snapshot_json["stage4"]["concurrency"]["max_concurrent_runs"] == 2
        assert source_task.runtime_snapshot_json["github_tokens"] == ["ghp_retry_one", "ghp_retry_two"]
        assert verify.get(CrawlJob, old_crawl_job_id) is not None
        repo_row = verify.scalar(
            select(BatchTaskRepository.id).where(BatchTaskRepository.task_id == source_task_id)
        )
        assert repo_row == old_repo_row_id
        stored_repo = verify.get(BatchTaskRepository, old_repo_row_id)
        assert stored_repo is not None
        assert stored_repo.status == "pending"
        assert stored_repo.stage2_status == "pending"
        assert stored_repo.stage3_status == "pending"
        assert stored_repo.stage4_status == "pending"
        assert stored_repo.stage2_run_id is None
        assert stored_repo.source_stage2_run_id is None
        assert stored_repo.stage3_snapshot_id is None
        assert stored_repo.stats_json == {}
        assert stored_repo.error_message is None
        assert verify.scalar(
            select(BatchTaskEntry.id).where(BatchTaskEntry.task_id == source_task_id)
        ) is None
    finally:
        verify.close()


def test_batch_task_api_retries_only_failed_repository_in_active_task(
    tmp_path,
) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'batch-retry-failed-repository.db'}"
    )
    batch_runner = DummyBatchRunner()
    client = TestClient(
        create_app(
            settings,
            runner=DummyRunner(),
            batch_runner=batch_runner,
        )
    )

    session = client.app.state.session_factory()
    try:
        pool = DataPool(
            name="default",
            root_path=str(tmp_path / "batch-retry-failed-pool"),
        )
        completed_repository = GitHubRepository(
            github_repo_id=101,
            full_name="owner/completed",
            owner_login="owner",
            name="completed",
            html_url="https://github.com/owner/completed",
            api_url="https://api.github.com/repos/owner/completed",
            primary_language="Python",
            stargazers_count=10,
            raw_payload={},
        )
        failed_repository = GitHubRepository(
            github_repo_id=102,
            full_name="owner/failed",
            owner_login="owner",
            name="failed",
            html_url="https://github.com/owner/failed",
            api_url="https://api.github.com/repos/owner/failed",
            primary_language="Python",
            stargazers_count=20,
            raw_payload={},
        )
        session.add_all([pool, completed_repository, failed_repository])
        session.flush()
        task = BatchTask(
            name="retry-one-failed-repository",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "pipeline": {"stop_after_stage": "stage2"},
                "stage2": {},
            },
            status=BatchTaskStatus.running.value,
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        completed_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=completed_repository.id,
            github_repo_id=completed_repository.github_repo_id,
            repo_full_name=completed_repository.full_name,
            language="Python",
            stars=10,
            status="completed",
            stage2_status="completed",
            stage3_status="skipped",
            stage4_status="skipped",
            stage2_run_id="completed-stage2-run",
            finished_at=datetime.now(UTC),
        )
        failed_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=failed_repository.id,
            github_repo_id=failed_repository.github_repo_id,
            repo_full_name=failed_repository.full_name,
            language="Python",
            stars=20,
            status="failed",
            stage2_status="failed",
            stage3_status="skipped",
            stage4_status="skipped",
            stage2_run_id="failed-stage2-run",
            error_message="docker build failed",
            finished_at=datetime.now(UTC),
        )
        session.add_all([completed_row, failed_row])
        session.flush()
        session.add(
            BatchTaskEntry(
                task_id=task.id,
                task_repository_id=failed_row.id,
                entry_file_path="tests/test_failed.py",
                status="failed",
            )
        )
        session.commit()
        task_id = task.id
        completed_row_id = completed_row.id
        failed_row_id = failed_row.id
    finally:
        session.close()

    batch_runner.running.add(task_id)
    response = client.post(
        f"/api/batch/tasks/{task_id}/repositories/{failed_row_id}/retry"
    )
    assert response.status_code == 200
    assert batch_runner.scheduled == []

    verify = client.app.state.session_factory()
    try:
        task = verify.get(BatchTask, task_id)
        completed_row = verify.get(BatchTaskRepository, completed_row_id)
        failed_row = verify.get(BatchTaskRepository, failed_row_id)
        assert task is not None
        assert completed_row is not None
        assert failed_row is not None
        assert task.status == BatchTaskStatus.running.value
        assert completed_row.status == "completed"
        assert completed_row.stage2_run_id == "completed-stage2-run"
        assert failed_row.status == "pending"
        assert failed_row.stage2_status == "pending"
        assert failed_row.stage3_status == "pending"
        assert failed_row.stage4_status == "pending"
        assert failed_row.stage2_run_id is None
        assert failed_row.error_message is None
        assert failed_row.entries == []
    finally:
        verify.close()

    active_session = client.app.state.session_factory()
    try:
        failed_row = active_session.get(BatchTaskRepository, failed_row_id)
        assert failed_row is not None
        failed_row.status = "failed"
        failed_row.stage2_status = "failed"
        failed_row.stage2_run_id = None
        active_run = Stage2Run(
            repository_id=failed_row.repository_id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="rerun_commit",
            target_branch="main",
            target_commit_sha="abc123",
            runtime_snapshot_json={
                "planner": {"model": "openai/gpt-5.6-luna"}
            },
            created_at=datetime.now(UTC),
            started_at=datetime.now(UTC),
        )
        active_session.add(active_run)
        active_session.commit()
        active_run_id = active_run.id
    finally:
        active_session.close()

    active_response = client.post(
        f"/api/batch/tasks/{task_id}/repositories/{failed_row_id}/retry"
    )
    assert active_response.status_code == 200

    active_verify = client.app.state.session_factory()
    try:
        failed_row = active_verify.get(BatchTaskRepository, failed_row_id)
        assert failed_row is not None
        assert failed_row.status == "running"
        assert failed_row.stage2_status == Stage2RunStatus.running.value
        assert failed_row.stage2_run_id == active_run_id
    finally:
        active_verify.close()

    completed_response = client.post(
        f"/api/batch/tasks/{task_id}/repositories/{completed_row_id}/retry"
    )
    assert completed_response.status_code == 409


def test_batch_task_api_delete_cleans_related_process_artifacts_and_keeps_data_pool_asset(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'batch-delete.db'}",
        default_data_pool_root=tmp_path / "default-pool",
        stage2_workspace_dir=tmp_path / "stage2",
        stage3_workspace_dir=tmp_path / "stage3",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))

    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).create_pool(
            name="batch-delete-pool",
            root_path=str(tmp_path / "batch-delete-pool"),
        )
        repository = GitHubRepository(
            github_repo_id=424242,
            full_name="owner/repo",
            owner_login="owner",
            name="repo",
            html_url="https://github.com/owner/repo",
            api_url="https://api.github.com/repos/owner/repo",
            primary_language="Python",
            stargazers_count=12,
        )
        session.add(repository)
        session.flush()

        crawl_job = CrawlJob(
            name="batch-crawl",
            status=CrawlJobStatus.completed.value,
            filters_json={},
            stats_json={},
        )
        session.add(crawl_job)
        session.flush()
        partition = CrawlPartition(
            job_id=crawl_job.id,
            status=CrawlPartitionStatus.completed.value,
            depth=0,
            range_start=datetime(2026, 4, 20, 0, 0, tzinfo=UTC),
            range_end=datetime(2026, 4, 20, 1, 0, tzinfo=UTC),
            query_string="language:Python",
        )
        session.add(partition)
        session.flush()
        session.add(
            RepoDiscovery(
                job_id=crawl_job.id,
                partition_id=partition.id,
                repository_id=repository.id,
                query_string="language:Python",
                raw_payload={},
            )
        )

        task = BatchTask(
            name="batch-delete-task",
            data_pool_id=pool.id,
            stage1_crawl_job_id=crawl_job.id,
            status=BatchTaskStatus.failed.value,
            phase="failed",
            token_source="temporary",
            filters_json={},
            runtime_snapshot_json={},
            stats_json={"repository_count": 1, "entry_file_count": 1, "unit_count": 1, "asset_count": 1},
        )
        session.add(task)
        session.flush()

        external_stage2 = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            phase="completed",
            target_branch="main",
            target_commit_sha="external123",
            runtime_snapshot_json={},
            summary_json={},
        )
        session.add(external_stage2)
        session.flush()

        batch_stage2_workspace = settings.stage2_workspace_dir / "runs" / "batch-stage2"
        batch_stage2_workspace.mkdir(parents=True, exist_ok=True)
        batch_stage2 = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="batch",
            phase="completed",
            target_branch="main",
            target_commit_sha="batch123",
            workspace_path=str(batch_stage2_workspace),
            runtime_snapshot_json={},
            summary_json={},
        )
        session.add(batch_stage2)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=batch_stage2.id,
            source_commit_sha="batch123",
            target_branch="main",
            summary_json={},
        )
        session.add(snapshot)
        session.flush()
        batch_stage3_snapshot_workspace = settings.stage3_workspace_dir / "snapshots" / snapshot.id
        batch_stage3_snapshot_workspace.mkdir(parents=True, exist_ok=True)
        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_batch.py",
            baseline_total_tests=2,
            baseline_passed_tests=2,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        session.add(entry_file)
        session.flush()

        batch_stage3_workspace = settings.stage3_workspace_dir / "runs" / "batch-stage3"
        batch_stage3_workspace.mkdir(parents=True, exist_ok=True)
        batch_stage3 = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.failed.value,
            trigger_kind="batch",
            phase="completed",
            workspace_path=str(batch_stage3_workspace),
            runtime_snapshot_json={},
            summary_json={},
        )
        session.add(batch_stage3)
        session.flush()
        batch_stage3_runtime = settings.stage3_workspace_dir / "runtime" / batch_stage3.id
        batch_stage3_runtime.mkdir(parents=True, exist_ok=True)

        batch_stage3_retry_workspace = settings.stage3_workspace_dir / "runs" / "batch-stage3-retry"
        batch_stage3_retry_workspace.mkdir(parents=True, exist_ok=True)
        batch_stage3_retry = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.failed.value,
            trigger_kind="batch",
            phase="completed",
            workspace_path=str(batch_stage3_retry_workspace),
            runtime_snapshot_json={},
            summary_json={},
        )
        session.add(batch_stage3_retry)
        session.flush()
        batch_stage3_retry_runtime = settings.stage3_workspace_dir / "runtime" / batch_stage3_retry.id
        batch_stage3_retry_runtime.mkdir(parents=True, exist_ok=True)

        savepoint = Stage3Savepoint(
            run_id=batch_stage3.id,
            depth=1,
            entry_pass_rate=0.5,
            gold_patch_text="diff --git a/file b/file",
            checkpoint_json={},
            summary_json={},
        )
        session.add(savepoint)
        session.flush()

        batch_stage4_workspace = settings.stage4_workspace_dir / "runs" / "batch-stage4"
        batch_stage4_workspace.mkdir(parents=True, exist_ok=True)
        batch_stage4 = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.completed.value,
            result=Stage4RunResult.generated.value,
            trigger_kind="batch",
            phase="completed",
            workspace_path=str(batch_stage4_workspace),
            issue_variant_count=1,
            hint_variant_count=1,
            runtime_snapshot_json={},
            summary_json={},
        )
        session.add(batch_stage4)
        session.flush()
        batch_stage4_runtime = settings.stage4_workspace_dir / "runtime" / batch_stage4.id
        batch_stage4_runtime.mkdir(parents=True, exist_ok=True)
        session.add(
            Stage4IssueVariant(
                run_id=batch_stage4.id,
                variant_index=1,
                style="bug_report",
                title="title",
                issue_markdown="issue",
                issue_json={},
                quality_json={},
                leakage_check_json={"passed": True, "reasons": []},
            )
        )
        session.add(
            Stage4HintVariant(
                run_id=batch_stage4.id,
                variant_index=1,
                strength="light",
                hint_markdown="hint",
                hint_json={},
                quality_json={},
                leakage_check_json={"passed": True, "reasons": []},
            )
        )

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language="Python",
            stars=repository.stargazers_count,
            status="failed",
            stage2_status="completed",
            stage3_status="completed",
            stage4_status="completed",
            stage2_run_id=batch_stage2.id,
            source_stage2_run_id=external_stage2.id,
            stage3_snapshot_id=snapshot.id,
            stats_json={"entry_file_count": 1, "asset_count": 1},
        )
        session.add(repo_row)
        session.flush()
        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            status="completed",
            stage3_run_id=batch_stage3.id,
            stats_json={},
        )
        session.add(entry_row)
        session.flush()

        asset_dir = Path(pool.root_path) / "asset-folder"
        asset_dir.mkdir(parents=True, exist_ok=True)
        asset = DataPoolAsset(
            pool_id=pool.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            source_commit_sha="batch123",
            language="Python",
            stars=repository.stargazers_count,
            entry_file_path=entry_file.test_file_path,
            depth=1,
            stage2_run_id=batch_stage2.id,
            stage3_run_id=batch_stage3.id,
            stage3_savepoint_id=savepoint.id,
            stage4_run_id=batch_stage4.id,
            folder_name=asset_dir.name,
            folder_path=str(asset_dir),
            manifest_json={"batch_task_id": task.id},
        )
        session.add(asset)
        session.flush()
        session.add(
            BatchTaskUnit(
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                depth=1,
                status="completed",
                stage3_savepoint_id=savepoint.id,
                stage4_run_id=batch_stage4.id,
                data_pool_asset_id=asset.id,
                stats_json={},
            )
        )
        session.commit()

        task_id = task.id
        crawl_job_id = crawl_job.id
        batch_stage2_run_id = batch_stage2.id
        external_stage2_run_id = external_stage2.id
        batch_stage3_run_id = batch_stage3.id
        batch_stage3_retry_run_id = batch_stage3_retry.id
        batch_stage4_run_id = batch_stage4.id
        snapshot_id = snapshot.id
        asset_id = asset.id
    finally:
        session.close()

    response = client.delete(f"/api/batch/tasks/{task_id}")
    assert response.status_code == 200
    payload = response.json()
    assert payload["deleted_task_id"] == task_id
    assert payload["deleted_stage1_crawl_job_id"] == crawl_job_id
    assert payload["cleanup_warnings"] is None

    verify = client.app.state.session_factory()
    try:
        assert verify.get(BatchTask, task_id) is None
        assert verify.get(CrawlJob, crawl_job_id) is None
        assert verify.get(Stage2Run, batch_stage2_run_id) is None
        assert verify.get(Stage2Run, external_stage2_run_id) is not None
        assert verify.get(Stage3Run, batch_stage3_run_id) is None
        assert verify.get(Stage3Run, batch_stage3_retry_run_id) is None
        assert verify.get(Stage3CommitSnapshot, snapshot_id) is None
        assert verify.get(Stage4Run, batch_stage4_run_id) is None
        assert verify.get(DataPoolAsset, asset_id) is not None
        assert verify.scalar(select(BatchTaskRepository.id).where(BatchTaskRepository.task_id == task_id)) is None
        assert verify.scalar(select(BatchTaskEntry.id).where(BatchTaskEntry.task_id == task_id)) is None
        assert verify.scalar(select(BatchTaskUnit.id).where(BatchTaskUnit.task_id == task_id)) is None
    finally:
        verify.close()

    assert not batch_stage2_workspace.exists()
    assert not batch_stage3_workspace.exists()
    assert not batch_stage3_runtime.exists()
    assert not batch_stage3_retry_workspace.exists()
    assert not batch_stage3_retry_runtime.exists()
    assert not batch_stage3_snapshot_workspace.exists()
    assert not batch_stage4_workspace.exists()
    assert not batch_stage4_runtime.exists()
    assert asset_dir.exists()


def test_batch_task_api_delete_rejects_active_task(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-delete-active.db'}")
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))

    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).ensure_default_pool(
            name=settings.default_data_pool_name,
            root_path=settings.default_data_pool_root,
        )
        task = BatchTask(
            name="running-batch-task",
            data_pool_id=pool.id,
            status=BatchTaskStatus.running.value,
            phase="stage3",
            token_source="temporary",
            filters_json={},
            runtime_snapshot_json={},
            stats_json={},
        )
        session.add(task)
        session.commit()
        task_id = task.id
    finally:
        session.close()

    batch_runner.running.add(task_id)
    response = client.delete(f"/api/batch/tasks/{task_id}")
    assert response.status_code == 409
    assert "cannot delete a running batch task" in response.json()["detail"]

    verify = client.app.state.session_factory()
    try:
        assert verify.get(BatchTask, task_id) is not None
    finally:
        verify.close()


def test_managed_image_bulk_delete_starts_selected_jobs(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'managed-image-bulk-delete.db'}")

    def fake_managed_images(_session, _settings):  # noqa: ANN001
        return {
            "rows": [
                {"image_ref": "feature-factory/test:a", "size_bytes": 1024},
                {"image_ref": "feature-factory/test:b", "size_bytes": 2048},
            ],
            "total_size_bytes": 3072,
        }

    class FakeManagedImageDeleteJobs:
        def __init__(self) -> None:
            self.started: list[str] = []

        def start(self, image_ref, callback):  # noqa: ANN001
            self.started.append(image_ref)
            return True

        def apply(self, payload):  # noqa: ANN001
            return payload

    fake_jobs = FakeManagedImageDeleteJobs()
    monkeypatch.setattr(server_module, "_list_feature_factory_managed_images", fake_managed_images)
    monkeypatch.setattr(server_module, "_ManagedImageDeleteJobManager", lambda: fake_jobs)
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))

    response = client.post(
        "/api/assets/images/delete-selected",
        json={
            "image_refs": [
                "feature-factory/test:a",
                "feature-factory/test:b",
                "feature-factory/test:a",
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["started"] == 2
    assert payload["already_running"] == 0
    assert fake_jobs.started == ["feature-factory/test:a", "feature-factory/test:b"]

    unknown_response = client.post(
        "/api/assets/images/delete-selected",
        json={"image_refs": ["feature-factory/test:missing"]},
    )
    assert unknown_response.status_code == 404


def test_batch_task_api_uses_github_token_template_when_selected(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-token-template.db'}")
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))

    pool_response = client.post(
        "/api/data-pools",
        json={"name": "batch-pool", "root_path": str(tmp_path / "batch-pool")},
    )
    assert pool_response.status_code == 200
    pool_id = pool_response.json()["pool"]["id"]

    template_response = client.post(
        "/api/stage1/templates",
        json={"name": "token-template", "github_tokens": ["ghp_template_a", "ghp_template_b"]},
    )
    assert template_response.status_code == 200
    template_id = template_response.json()["id"]

    create_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "templated-token-batch",
            "data_pool_id": pool_id,
            "github_token_template_id": template_id,
            "github_tokens": ["ghp_should_not_win"],
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "stars_min": 10,
                "exclude_forks": True,
                "exclude_archived": True,
            },
        },
    )
    assert create_response.status_code == 200
    task_id = create_response.json()["id"]

    session = client.app.state.session_factory()
    try:
        task = session.get(BatchTask, task_id)
        assert task is not None
        assert task.runtime_snapshot_json["github_tokens"] == ["ghp_template_a", "ghp_template_b"]
    finally:
        session.close()


def test_batch_task_detail_repository_paginates_and_filters(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-detail-pagination.db'}")
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))

    session = client.app.state.session_factory()
    try:
        pool = DataPoolService(session).ensure_default_pool(
            name=settings.default_data_pool_name,
            root_path=settings.default_data_pool_root,
        )
        task = BatchTask(
            name="detail-pagination",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
            token_source="temporary",
            filters_json={},
            runtime_snapshot_json={},
            stats_json={
                "repository_count": 3,
                "entry_file_count": 3,
                "unit_count": 3,
                "asset_count": 1,
            },
        )
        session.add(task)
        session.flush()

        base_time = datetime(2026, 4, 22, 12, 0, tzinfo=UTC)
        repo_specs = [
            ("owner/alpha", "Python", 11, "completed", "completed", "completed", "completed", "tests/test_alpha.py", None),
            ("owner/flask-tools", "Python", 42, "failed", "completed", "failed", "failed", "tests/test_flask.py", "stage4 failed"),
            ("owner/zeta", "Go", 7, "pending", "pending", "pending", "pending", "tests/test_zeta.py", None),
        ]
        for index, spec in enumerate(repo_specs, start=1):
            full_name, language, stars, status, stage2_status, stage3_status, stage4_status, entry_path, error_message = spec
            repository = GitHubRepository(
                github_repo_id=9000 + index,
                full_name=full_name,
                owner_login=full_name.split("/")[0],
                name=full_name.split("/")[1],
                html_url=f"https://github.com/{full_name}",
                api_url=f"https://api.github.com/repos/{full_name}",
                primary_language=language,
                stargazers_count=stars,
            )
            session.add(repository)
            session.flush()
            repo_row = BatchTaskRepository(
                task_id=task.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=full_name,
                language=language,
                stars=stars,
                status=status,
                stage2_status=stage2_status,
                stage3_status=stage3_status,
                stage4_status=stage4_status,
                error_message=error_message,
                stats_json={
                    "entry_file_count": 1,
                    "asset_count": 1 if stage4_status == "completed" else 0,
                    "failed_unit_count": 1 if status == "failed" else 0,
                },
                updated_at=base_time + timedelta(minutes=index),
            )
            session.add(repo_row)
            session.flush()
            entry_row = BatchTaskEntry(
                task_id=task.id,
                task_repository_id=repo_row.id,
                entry_file_path=entry_path,
                status="completed" if stage3_status == "completed" else "failed" if stage3_status == "failed" else "pending",
                stage3_run_id=f"stage3-{index}",
            )
            session.add(entry_row)
            session.flush()
            session.add(
                BatchTaskUnit(
                    task_id=task.id,
                    task_repository_id=repo_row.id,
                    task_entry_id=entry_row.id,
                    depth=index,
                    status="completed" if stage4_status == "completed" else "failed" if stage4_status == "failed" else "pending",
                    stage4_run_id=f"stage4-{index}",
                    error_message="unit failed" if status == "failed" else None,
                )
            )
        session.commit()
        task_id = task.id
    finally:
        session.close()

    response = client.get(f"/api/batch/tasks/{task_id}", params={"repo_page": 1, "repo_page_size": 2})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["repositories"]) == 2
    assert payload["repository_pagination"]["total"] == 3
    assert payload["repository_pagination"]["total_pages"] == 2

    filtered_response = client.get(
        f"/api/batch/tasks/{task_id}",
        params={"repo_query": "flask", "repo_has_errors": "true"},
    )
    assert filtered_response.status_code == 200
    filtered_payload = filtered_response.json()
    assert filtered_payload["repository_pagination"]["total"] == 1
    assert [row["repo_full_name"] for row in filtered_payload["repositories"]] == ["owner/flask-tools"]

    entry_query_response = client.get(
        f"/api/batch/tasks/{task_id}",
        params={"repo_query": "test_zeta"},
    )
    assert entry_query_response.status_code == 200
    assert entry_query_response.json()["repository_pagination"]["total"] == 0

    non_pending_response = client.get(
        f"/api/batch/tasks/{task_id}",
        params={"repo_non_pending": "true"},
    )
    assert non_pending_response.status_code == 200
    assert non_pending_response.json()["repository_pagination"]["total"] == 2


def test_data_pool_api_ensures_default_pool_and_batch_can_use_it(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'default-pool.db'}",
        default_data_pool_root=tmp_path / "data",
    )
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))

    pools_response = client.get("/api/data-pools")
    assert pools_response.status_code == 200
    payload = pools_response.json()
    assert payload["pools"][0]["is_default"] is True
    assert payload["pools"][0]["name"] == "默认数据池"
    assert Path(payload["pools"][0]["root_path"]) == (tmp_path / "data").resolve()
    assert payload["default_pool_id"] == payload["pools"][0]["id"]

    create_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "default-pool-batch",
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "exclude_forks": True,
                "exclude_archived": True,
            },
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["data_pool_id"] == payload["default_pool_id"]
    assert batch_runner.scheduled == [create_response.json()["id"]]


def test_deleted_custom_data_pool_root_is_reported_unavailable_without_pruning(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'missing-custom-pool.db'}",
        default_data_pool_root=tmp_path / "default-data",
    )
    batch_runner = DummyBatchRunner()
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner))

    pool_root = tmp_path / "custom-pool"
    create_pool_response = client.post(
        "/api/data-pools",
        json={"name": "custom-pool", "root_path": str(pool_root)},
    )
    assert create_pool_response.status_code == 200
    pool_id = create_pool_response.json()["pool"]["id"]
    assert pool_root.is_dir()

    asset_dir = pool_root / "owner__repo__abc123__tests_test_py__depth-1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "manifest.json").write_text("{}", encoding="utf-8")
    session = client.app.state.session_factory()
    try:
        asset = DataPoolAsset(
            pool_id=pool_id,
            github_repo_id=9001,
            repo_full_name="owner/repo",
            source_commit_sha="abc123",
            language="Python",
            stars=42,
            entry_file_path="tests/test.py",
            depth=1,
            stage2_run_id="stage2-a",
            stage3_run_id="stage3-a",
            stage3_savepoint_id=1,
            stage4_run_id="stage4-a",
            folder_name=asset_dir.name,
            folder_path=str(asset_dir),
            manifest_json={"marker": "missing-root"},
        )
        session.add(asset)
        session.commit()
        asset_id = asset.id
    finally:
        session.close()

    shutil.rmtree(pool_root)
    pools_response = client.get("/api/data-pools")
    assert pools_response.status_code == 200
    payload = pools_response.json()
    assert payload["deleted_pool_ids"] == []
    missing_pool = next(pool for pool in payload["pools"] if pool["id"] == pool_id)
    assert missing_pool["root_exists"] is False
    assert missing_pool["root_is_dir"] is False
    assert missing_pool["is_available"] is False

    create_batch_response = client.post(
        "/api/batch/tasks",
        json={
            "name": "should-not-use-missing-pool",
            "data_pool_id": pool_id,
            "filters": {
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "exclude_forks": True,
                "exclude_archived": True,
            },
        },
    )
    assert create_batch_response.status_code == 400
    assert "root_path is missing or not a directory" in create_batch_response.json()["detail"]
    assert batch_runner.scheduled == []
    assert not pool_root.exists()

    verify = client.app.state.session_factory()
    try:
        assert verify.get(DataPool, pool_id) is not None
        assert verify.get(DataPoolAsset, asset_id) is not None
    finally:
        verify.close()


def test_delete_data_pool_rejects_backfill_reference_before_deleting_assets(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'referenced-custom-pool.db'}",
        default_data_pool_root=tmp_path / "default-data",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    source_pool_id = client.get("/api/data-pools").json()["default_pool_id"]

    pool_root = tmp_path / "backfill-target"
    create_pool_response = client.post(
        "/api/data-pools",
        json={"name": "backfill-target", "root_path": str(pool_root)},
    )
    assert create_pool_response.status_code == 200
    pool_id = create_pool_response.json()["pool"]["id"]

    asset_dir = pool_root / "owner__repo__abc123__tests_test_py__depth-1"
    asset_dir.mkdir(parents=True)
    (asset_dir / "manifest.json").write_text("{}", encoding="utf-8")
    session = client.app.state.session_factory()
    try:
        asset = DataPoolAsset(
            pool_id=pool_id,
            github_repo_id=9002,
            repo_full_name="owner/repo",
            source_commit_sha="abc123",
            language="Python",
            stars=42,
            entry_file_path="tests/test.py",
            depth=1,
            folder_name=asset_dir.name,
            folder_path=str(asset_dir),
            manifest_json={"marker": "must-survive-conflict"},
        )
        job = Stage4DataPoolBackfillJob(
            source_pool_id=source_pool_id,
            target_pool_id=pool_id,
            runtime_template_name="test-template",
            runtime_snapshot_json={},
            styles_json=[],
            inventory_sha256="0" * 64,
            eligible_count=0,
            status="completed",
        )
        session.add_all([asset, job])
        session.commit()
        asset_id = asset.id
        job_id = job.id
    finally:
        session.close()

    delete_response = client.delete(f"/api/data-pools/{pool_id}")
    assert delete_response.status_code == 409
    assert "stage4 backfill jobs as target=1" in delete_response.json()["detail"]
    assert asset_dir.is_dir()
    assert (asset_dir / "manifest.json").is_file()

    verify = client.app.state.session_factory()
    try:
        assert verify.get(DataPool, pool_id) is not None
        assert verify.get(DataPoolAsset, asset_id) is not None
        assert verify.get(Stage4DataPoolBackfillJob, job_id) is not None
    finally:
        verify.close()


def test_manual_stage4_generated_run_materializes_to_default_pool(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'manual-stage4-default-pool.db'}",
        default_data_pool_root=tmp_path / "data",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    client = TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner()))
    savepoint_id = _seed_stage4_source_savepoint(client)
    session_factory = client.app.state.session_factory
    session = session_factory()
    try:
        broken_repo = tmp_path / "stage4-run-manual" / "repo"
        (broken_repo / "tests").mkdir(parents=True)
        (broken_repo / "tests" / "test_appctx.py").write_text("def test_app_context(): pass\n", encoding="utf-8")
        (broken_repo / "tests" / "test_basic.py").write_text("def test_basic(): pass\n", encoding="utf-8")
        run = Stage4Run(
            source_savepoint_id=savepoint_id,
            status=Stage4RunStatus.completed.value,
            result=Stage4RunResult.generated.value,
            trigger_kind="manual",
            phase="completed",
            issue_variant_count=3,
            hint_variant_count=0,
            summary_json={},
        )
        session.add(run)
        session.flush()
        workspace_path = tmp_path / "stage4" / "runs" / run.id
        run.workspace_path = str(workspace_path)
        run.runtime_snapshot_json = {
            "stage4": {
                "broken_repo_dir": str(broken_repo),
                "workspace": {"repo_dir": str(broken_repo)},
                "generation_config": load_issue_style_catalog().snapshot(seed=run.id),
            }
        }
        for variant_index, style in enumerate(("swe", "fb", "hint"), start=1):
            session.add(
                Stage4IssueVariant(
                    run_id=run.id,
                    variant_index=variant_index,
                    style=style,
                    title=f"Manual {style} issue",
                    issue_markdown=f"Manual {style} issue body",
                    issue_json={"style": style},
                    quality_json={},
                    leakage_check_json={},
                )
            )
            archive_file = tmp_path / "stage4" / "runtime" / run.id / "llm-completions" / style / "completion.json"
            archive_file.parent.mkdir(parents=True, exist_ok=True)
            archive_file.write_text(json.dumps({"role": style}), encoding="utf-8")
        session.commit()
        run_id = run.id
    finally:
        session.close()

    runner = Stage4RunRunner(
        session_factory,
        settings,
        max_workers=1,
        max_limit=1,
        app_instance_id="test",
    )
    try:
        runner._materialize_default_data_pool_if_generated(run_id)
    finally:
        runner.shutdown(timeout_seconds=0.0)

    verify = session_factory()
    try:
        default_pool = DataPoolService(verify).ensure_default_pool(
            name=settings.default_data_pool_name,
            root_path=settings.default_data_pool_root,
        )
        assets = DataPoolService(verify).list_assets(default_pool.id)
        assert assets["pagination"]["total"] == 1
        assert Path(assets["assets"][0]["folder_path"]).is_dir()
    finally:
        verify.close()


def _seed_stage4_source_savepoint(client: TestClient) -> int:
    session = client.app.state.session_factory()
    try:
        repository = GitHubRepository(
            github_repo_id=4101,
            full_name="pallets/flask",
            owner_login="pallets",
            name="flask",
            html_url="https://github.com/pallets/flask",
            api_url="https://api.github.com/repos/pallets/flask",
            description="test repo",
            primary_language="Python",
            default_branch="main",
            license_key="bsd-3-clause",
            stargazers_count=71000,
            created_at_github=datetime(2020, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2020, 1, 2, tzinfo=UTC),
            discovered_at=datetime(2020, 1, 3, tzinfo=UTC),
        )
        session.add(repository)
        session.flush()
        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id="stage2-run",
            source_commit_sha="2ac89889f4cc1234567890abcdef",
            target_branch="main",
            base_image="python:3.11-jammy-builder",
            dockerfile_text="FROM python:3.11\n",
            run_script_text="#!/usr/bin/env bash\nexit 0\n",
            original_p2p_files_json=["tests/test_basic.py"],
            summary_json={},
            source_created_at=datetime(2020, 1, 4, tzinfo=UTC),
            source_finished_at=datetime(2020, 1, 5, tzinfo=UTC),
        )
        session.add(snapshot)
        session.flush()
        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_appctx.py",
            baseline_total_tests=15,
            baseline_passed_tests=15,
            baseline_failed_tests=0,
            baseline_error_tests=0,
            baseline_skipped_tests=0,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        session.add(entry_file)
        session.flush()
        stage3_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            phase="completed",
            runtime_snapshot_json={"stage3": {"workspace": {}}},
            summary_json={},
        )
        session.add(stage3_run)
        session.flush()
        savepoint = Stage3Savepoint(
            run_id=stage3_run.id,
            depth=2,
            entry_pass_rate=0.4,
            p2p_files_json=["tests/test_basic.py"],
            f2p_files_json=["tests/test_appctx.py"],
            collateral_json={"non_entry_failed_count": 0},
            gold_patch_text=(
                "diff --git a/src/flask/ctx.py b/src/flask/ctx.py\n"
                "--- a/src/flask/ctx.py\n"
                "+++ b/src/flask/ctx.py\n"
                "@@ -1 +1 @@\n"
                "-old behavior line with enough text to be considered private\n"
                "+new broken line with enough text to be considered private\n"
            ),
            checkpoint_json={},
            summary_json={},
        )
        session.add(savepoint)
        session.flush()
        session.commit()
        return savepoint.id
    finally:
        session.close()


def _seed_stage4_run_for_runtime_edit(client: TestClient) -> tuple[int, str]:
    savepoint_id = _seed_stage4_source_savepoint(client)
    session = client.app.state.session_factory()
    try:
        service = Stage4Service(
            session,
            workspace_root=client.app.state.settings.stage4_workspace_dir,
            settings=client.app.state.settings,
        )
        run = service.create_run(savepoint_id)
        session.commit()
        return savepoint_id, run.id
    finally:
        session.close()


def _seed_stage4_listing_data(client: TestClient) -> dict[str, object]:
    session = client.app.state.session_factory()
    try:
        repo_a = GitHubRepository(
            github_repo_id=5101,
            full_name="pallets/flask",
            owner_login="pallets",
            name="flask",
            html_url="https://github.com/pallets/flask",
            api_url="https://api.github.com/repos/pallets/flask",
            description="repo a",
            primary_language="Python",
            default_branch="main",
            license_key="bsd-3-clause",
            stargazers_count=71000,
            created_at_github=datetime(2020, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2020, 1, 2, tzinfo=UTC),
            discovered_at=datetime(2020, 1, 3, tzinfo=UTC),
        )
        repo_b = GitHubRepository(
            github_repo_id=5102,
            full_name="encode/starlette",
            owner_login="encode",
            name="starlette",
            html_url="https://github.com/encode/starlette",
            api_url="https://api.github.com/repos/encode/starlette",
            description="repo b",
            primary_language="Python",
            default_branch="main",
            license_key="bsd-3-clause",
            stargazers_count=41000,
            created_at_github=datetime(2020, 2, 1, tzinfo=UTC),
            pushed_at_github=datetime(2020, 2, 2, tzinfo=UTC),
            discovered_at=datetime(2020, 2, 3, tzinfo=UTC),
        )
        session.add_all([repo_a, repo_b])
        session.flush()

        snapshot_a = Stage3CommitSnapshot(
            repository_id=repo_a.id,
            source_stage2_run_id="stage2-run-a",
            source_commit_sha="2ac89889f4ccaaaa1111222233334444",
            target_branch="main",
            base_image="python:3.11-jammy-builder",
            dockerfile_text="FROM python:3.11\n",
            run_script_text="#!/usr/bin/env bash\nexit 0\n",
            original_p2p_files_json=["tests/test_basic.py"],
            summary_json={},
            source_created_at=datetime(2020, 1, 4, tzinfo=UTC),
            source_finished_at=datetime(2020, 1, 5, tzinfo=UTC),
        )
        snapshot_b = Stage3CommitSnapshot(
            repository_id=repo_b.id,
            source_stage2_run_id="stage2-run-b",
            source_commit_sha="9f9f8888bbbbcccc1111222233334444",
            target_branch="main",
            base_image="python:3.11-jammy-builder",
            dockerfile_text="FROM python:3.11\n",
            run_script_text="#!/usr/bin/env bash\nexit 0\n",
            original_p2p_files_json=["tests/test_routes.py"],
            summary_json={},
            source_created_at=datetime(2020, 2, 4, tzinfo=UTC),
            source_finished_at=datetime(2020, 2, 5, tzinfo=UTC),
        )
        session.add_all([snapshot_a, snapshot_b])
        session.flush()

        entry_a1 = Stage3EntryFile(
            snapshot_id=snapshot_a.id,
            test_file_path="tests/test_appctx.py",
            baseline_total_tests=15,
            baseline_passed_tests=15,
            baseline_failed_tests=0,
            baseline_error_tests=0,
            baseline_skipped_tests=0,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        entry_a2 = Stage3EntryFile(
            snapshot_id=snapshot_a.id,
            test_file_path="tests/test_basic.py",
            baseline_total_tests=132,
            baseline_passed_tests=132,
            baseline_failed_tests=0,
            baseline_error_tests=0,
            baseline_skipped_tests=0,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        entry_b1 = Stage3EntryFile(
            snapshot_id=snapshot_b.id,
            test_file_path="tests/test_routing.py",
            baseline_total_tests=24,
            baseline_passed_tests=24,
            baseline_failed_tests=0,
            baseline_error_tests=0,
            baseline_skipped_tests=0,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        session.add_all([entry_a1, entry_a2, entry_b1])
        session.flush()

        run_a = Stage3Run(
            entry_file_id=entry_a1.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            phase="completed",
            runtime_snapshot_json={"stage3": {"workspace": {}}},
            summary_json={},
        )
        run_b = Stage3Run(
            entry_file_id=entry_b1.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            phase="completed",
            runtime_snapshot_json={"stage3": {"workspace": {}}},
            summary_json={},
        )
        session.add_all([run_a, run_b])
        session.flush()

        savepoint_a1 = Stage3Savepoint(
            run_id=run_a.id,
            depth=1,
            entry_pass_rate=0.2,
            p2p_files_json=["tests/test_basic.py", "tests/test_json.py"],
            f2p_files_json=["tests/test_appctx.py"],
            collateral_json={"non_entry_failed_count": 0},
            gold_patch_text="diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\n+new\n",
            checkpoint_json={},
            summary_json={},
        )
        savepoint_a2 = Stage3Savepoint(
            run_id=run_a.id,
            depth=2,
            entry_pass_rate=0.6,
            p2p_files_json=["tests/test_basic.py"],
            f2p_files_json=["tests/test_appctx.py", "tests/test_signals.py"],
            collateral_json={"non_entry_failed_count": 1},
            gold_patch_text=(
                "diff --git a/b b/b\n--- a/b\n+++ b/b\n"
                "@@ -1,2 +1,2 @@\n-old1\n-old2\n+new1\n+new2\n"
            ),
            checkpoint_json={},
            summary_json={},
        )
        savepoint_b1 = Stage3Savepoint(
            run_id=run_b.id,
            depth=1,
            entry_pass_rate=0.8,
            p2p_files_json=["tests/test_responses.py"],
            f2p_files_json=["tests/test_routing.py"],
            collateral_json={"non_entry_failed_count": 0},
            gold_patch_text="diff --git a/c b/c\n--- a/c\n+++ b/c\n@@ -1 +1 @@\n-old\n+newer\n",
            checkpoint_json={},
            summary_json={},
        )
        session.add_all([savepoint_a1, savepoint_a2, savepoint_b1])
        session.flush()

        service = Stage4Service(
            session,
            workspace_root=client.app.state.settings.stage4_workspace_dir,
            settings=client.app.state.settings,
        )
        generated_run = service.create_run(savepoint_a1.id)
        generated_run.status = Stage4RunStatus.completed.value
        generated_run.result = Stage4RunResult.generated.value
        generated_run.summary_json = {"schema_valid": True, "leakage_passed": True}
        generated_run.started_at = datetime(2020, 1, 6, 0, 0, tzinfo=UTC)
        generated_run.finished_at = datetime(2020, 1, 6, 0, 5, tzinfo=UTC)
        generated_run.updated_at = datetime(2020, 1, 6, 0, 5, tzinfo=UTC)

        pending_run = service.create_run(savepoint_a2.id)
        pending_run.updated_at = datetime(2020, 1, 6, 0, 10, tzinfo=UTC)

        other_run = service.create_run(savepoint_b1.id)
        other_run.status = Stage4RunStatus.completed.value
        other_run.result = Stage4RunResult.failed.value
        other_run.started_at = datetime(2020, 2, 6, 0, 0, tzinfo=UTC)
        other_run.finished_at = datetime(2020, 2, 6, 0, 7, tzinfo=UTC)
        other_run.updated_at = datetime(2020, 2, 6, 0, 7, tzinfo=UTC)

        session.commit()
        return {
            "repo_a_id": repo_a.id,
            "repo_b_id": repo_b.id,
            "snapshot_a_id": snapshot_a.id,
            "snapshot_b_id": snapshot_b.id,
            "savepoint_a1_id": savepoint_a1.id,
            "savepoint_a2_id": savepoint_a2.id,
            "generated_run_id": generated_run.id,
            "pending_run_id": pending_run.id,
            "other_run_id": other_run.id,
        }
    finally:
        session.close()


def test_stage4_run_runtime_can_be_edited(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage4-run-runtime.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    _, run_id = _seed_stage4_run_for_runtime_edit(client)

    response = client.patch(
        f"/api/stage4/runs/{run_id}/runtime",
        json={
            "issuer_model": "openai/gpt-5.4-mini",
            "issuer_base_url": "https://issuer.example/v1",
            "issuer_api_key": "sk-stage4-run-secret",
            "issuer_preset": "gpt5",
            "issuer_max_iterations": 222,
            "issuer_timeout_seconds": 2400,
            "build_timeout_seconds": 2500,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["issue_variant_count"] == 3
    assert "hint_variant_count" not in payload
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["model"] == "openai/gpt-5.4-mini"
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["base_url"] == "https://issuer.example/v1"
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["api_key_preview"] == "sk-sta..."
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["preset"] == "gpt5"
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["max_iterations"] == 222
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["issuer"]["timeout_seconds"] == 2400
    assert payload["runtime_snapshot"]["stage4"]["runtime"]["hyperparameters"]["build_timeout_seconds"] == 2500
    assert "issue_variant_count" not in payload["runtime_snapshot"]["stage4"]["runtime"]["hyperparameters"]
    assert "hint_variant_count" not in payload["runtime_snapshot"]["stage4"]["runtime"]["hyperparameters"]

    session = client.app.state.session_factory()
    try:
        service = Stage4Service(
            session,
            workspace_root=client.app.state.settings.stage4_workspace_dir,
            settings=client.app.state.settings,
        )
        stored = service.get_run(run_id, include_detail=False)
        runtime_stage4 = dict((stored.runtime_snapshot_json or {}).get("stage4") or {})
        runtime = dict(runtime_stage4.get("runtime") or {})
        assert stored.issue_variant_count == 3
        assert stored.hint_variant_count == 0
        assert dict(runtime.get("issuer") or {}).get("model") == "openai/gpt-5.4-mini"
        assert dict(runtime.get("issuer") or {}).get("api_key") == "sk-stage4-run-secret"
        assert dict(runtime.get("hyperparameters") or {}).get("build_timeout_seconds") == 2500
        assert "issue_variant_count" not in dict(runtime.get("hyperparameters") or {})
        assert "hint_variant_count" not in dict(runtime.get("hyperparameters") or {})
    finally:
        session.close()


def test_stage4_schedule_failure_cleans_prepared_run_assets(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-schedule-failed.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    stage4_runner = FalseScheduleStage4Runner()
    client = TestClient(create_app(settings, runner=DummyRunner(), stage4_runner=stage4_runner))

    savepoint_id = _seed_stage4_source_savepoint(client)

    response = client.post("/api/stage4/runs", json={"source_savepoint_id": savepoint_id})
    assert response.status_code == 503
    assert len(stage4_runner.scheduled) == 1
    run_id = stage4_runner.scheduled[0]

    session = client.app.state.session_factory()
    try:
        service = Stage4Service(
            session,
            workspace_root=client.app.state.settings.stage4_workspace_dir,
            settings=client.app.state.settings,
        )
        run = service.get_run(run_id, include_detail=True)
        assert run.status == Stage4RunStatus.completed.value
        assert run.result == Stage4RunResult.failed.value
        assert dict(run.summary_json or {})["schedule_failed"] is True
        workspace_path = Path(str(run.workspace_path or ""))
        assert str(run.workspace_path or "")
        assert not workspace_path.exists()
        assert any(event.title == "Stage4 run assets cleaned" for event in run.events)
    finally:
        session.close()


def test_stage4_run_detail_exposes_llm_completion_archives_and_downloads(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage4-run-llm.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    _, run_id = _seed_stage4_run_for_runtime_edit(client)
    runtime_dir = client.app.state.settings.stage4_workspace_dir / "runtime" / run_id / "llm-completions" / "swe"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    completion_path = runtime_dir / "issuer.json"
    completion_path.write_text('{"message": "ok"}\n', encoding="utf-8")

    detail = client.get(f"/api/stage4/runs/{run_id}")
    assert detail.status_code == 200
    archive = detail.json()["llm_completion_archives"]["swe"]
    assert archive["file_count"] == 1
    assert archive["files"][0]["path"] == "llm-completions/swe/issuer.json"

    file_response = client.get(
        f"/api/stage4/runs/{run_id}/llm-completions/swe/files",
        params={"path": "llm-completions/swe/issuer.json"},
    )
    assert file_response.status_code == 200
    assert file_response.json()["kind"] == "json"
    assert file_response.json()["value"] == {"message": "ok"}

    archive_response = client.get(f"/api/stage4/runs/{run_id}/llm-completions/swe.zip")
    assert archive_response.status_code == 200
    assert archive_response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(archive_response.content)) as zf:
        assert zf.namelist() == ["swe/issuer.json"]
        assert zf.read("swe/issuer.json").decode("utf-8") == '{"message": "ok"}\n'


def test_stage4_sources_and_runs_support_pagination_sort_and_filters(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage4-listing.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    seeded = _seed_stage4_listing_data(client)

    source_groups = client.get(
        "/api/stage4/sources",
        params={
            "page": 1,
            "page_size": 1,
            "sort_by": "latest_operation_at",
            "sort_order": "desc",
            "statuses": "generated",
            "name_query": "pallets/",
        },
    )
    assert source_groups.status_code == 200
    source_payload = source_groups.json()
    assert source_payload["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 1,
        "total_pages": 1,
    }
    assert len(source_payload["groups"]) == 1
    assert source_payload["groups"][0]["repository"]["full_name"] == "pallets/flask"
    assert source_payload["groups"][0]["status"] == "generated"

    source_detail = client.get(
        f"/api/stage4/sources/{seeded['repo_a_id']}/snapshots/{seeded['snapshot_a_id']}",
        params={
            "sort_by": "depth",
            "sort_order": "desc",
            "entry_pass_rate_max": 50,
            "diff_lines_min": 1,
            "diff_lines_max": 10,
        },
    )
    assert source_detail.status_code == 200
    detail_payload = source_detail.json()
    assert detail_payload["group"]["repository"]["full_name"] == "pallets/flask"
    assert [item["savepoint"]["depth"] for item in detail_payload["sources"]] == [1]
    assert detail_payload["sources"][0]["entry_file"]["test_file_path"] == "tests/test_appctx.py"
    assert detail_payload["sources"][0]["savepoint"]["produced_issue_variant_count"] == 3
    assert "produced_hint_variant_count" not in detail_payload["sources"][0]["savepoint"]

    run_list = client.get(
        "/api/stage4/runs",
        params={
            "page": 1,
            "page_size": 1,
            "sort_by": "created_at",
            "sort_order": "desc",
            "results": "generated",
            "source_savepoint_id": seeded["savepoint_a1_id"],
        },
    )
    assert run_list.status_code == 200
    run_payload = run_list.json()
    assert run_payload["pagination"] == {
        "page": 1,
        "page_size": 1,
        "total": 1,
        "total_pages": 1,
    }
    assert [run["id"] for run in run_payload["runs"]] == [seeded["generated_run_id"]]
    assert run_payload["runs"][0]["result"] == Stage4RunResult.generated.value


def test_admin_repo_list_and_static_assets(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'admin-repos.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        repo_a = GitHubRepository(
            github_repo_id=101,
            full_name="owner/a-repo",
            owner_login="owner",
            name="a-repo",
            html_url="https://github.com/owner/a-repo",
            api_url="https://api.github.com/repos/owner/a-repo",
            description="A small python crawler",
            primary_language="Python",
            license_key="mit",
            stargazers_count=10,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 3, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 5, tzinfo=UTC),
        )
        repo_b = GitHubRepository(
            github_repo_id=102,
            full_name="owner/b-repo",
            owner_login="owner",
            name="b-repo",
            html_url="https://github.com/owner/b-repo",
            api_url="https://api.github.com/repos/owner/b-repo",
            description="A Go service",
            primary_language="Go",
            license_key="apache-2.0",
            stargazers_count=20,
            created_at_github=datetime(2024, 1, 2, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 4, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 6, tzinfo=UTC),
        )
        session.add_all([repo_a, repo_b])
        session.commit()
    finally:
        session.close()

    repos = client.get("/api/stage1/repos?page=1&page_size=1&sort_by=stargazers_count&sort_order=desc")
    assert repos.status_code == 200
    assert repos.json()["pagination"]["total"] == 2
    assert repos.json()["repositories"][0]["full_name"] == "owner/b-repo"

    filtered_repos = client.get(
        "/api/stage1/repos?page=1&page_size=15&sort_by=full_name&sort_order=asc"
        "&language=python&stars_max=15&name_query=crawler"
    )
    assert filtered_repos.status_code == 200
    assert filtered_repos.json()["pagination"]["total"] == 1
    assert filtered_repos.json()["repositories"][0]["full_name"] == "owner/a-repo"

    pushed_filtered_repos = client.get(
        "/api/stage1/repos?page=1&page_size=15&sort_by=discovered_at&sort_order=desc"
        "&pushed_after=2024-01-03T12:00:00Z"
    )
    assert pushed_filtered_repos.status_code == 200
    assert pushed_filtered_repos.json()["pagination"]["total"] == 1
    assert pushed_filtered_repos.json()["repositories"][0]["full_name"] == "owner/b-repo"

    multi_filtered_repos = client.get(
        "/api/stage1/repos?page=1&page_size=15&sort_by=full_name&sort_order=asc"
        "&languages=Python&languages=Go&licenses=mit"
    )
    assert multi_filtered_repos.status_code == 200
    assert multi_filtered_repos.json()["pagination"]["total"] == 1
    assert multi_filtered_repos.json()["repositories"][0]["full_name"] == "owner/a-repo"
    assert multi_filtered_repos.json()["filters"]["languages"] == ["Python", "Go"]
    assert multi_filtered_repos.json()["filters"]["licenses"] == ["mit"]

    page = client.get("/")
    assert page.status_code == 200
    assert "FeatureFactory 管理后台" in page.text
    assert "/static/feature_factory-logo.png" in page.text
    assert "/static/admin.css" in page.text
    assert "/static/admin.js" in page.text
    assert "/static/admin.js?v=" in page.text
    assert "__FEATURE_FACTORY_ADMIN_JS_VERSION__" not in page.text
    assert 'id="theme-toggle"' in page.text
    assert "最近操作时间" in page.text
    assert 'id="batch-retry-popover"' in page.text
    assert 'name="stop_after_stage"' in page.text
    assert "仅环境构造（Stage2 完成后停止）" in page.text
    assert "沿用原任务配置" in page.text
    assert "使用当前全局配置" in page.text
    assert "保留该任务原本的 Stage2 / Stage3 / Stage4" not in page.text
    assert "两种方式都会复用已有 Stage1 抓取结果" not in page.text

    favicon = client.get("/static/feature_factory-logo.png")
    assert favicon.status_code == 200
    admin_css = client.get("/static/admin.css")
    assert admin_css.status_code == 200
    admin_js = client.get("/static/admin.js")
    assert admin_js.status_code == 200
    assert "stage3-run-min-removed-code-lines" in admin_js.text
    assert "最少删除实现代码行数" in admin_js.text
    assert "runtime_config_source" in admin_js.text
    assert "openBatchRetryPopover" in admin_js.text
    assert 'stop_after_stage: form.get("stop_after_stage") || "stage4"' in admin_js.text


def test_admin_api_stage1_job_events_stream_terminal_for_inactive_job(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage1-events.db'}")
    client = TestClient(create_app(settings, runner=DummyRunner()))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="inactive-job",
            status=CrawlJobStatus.completed.value,
            filters_json={"language": "Python"},
            stats_json={},
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    response = client.get(f"/api/stage1/jobs/{job_id}/events")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: terminal" in response.text
    assert job_id in response.text


def test_admin_api_reconciles_inconsistent_job_status_on_read(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'reconcile.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    create = client.post(
        "/api/stage1/jobs",
        json={
            "name": "reconcile-job",
            "run_in_background": False,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert create.status_code == 200
    job_id = create.json()["job"]["id"]

    session = client.app.state.session_factory()
    try:
        job = session.get(CrawlJob, job_id)
        assert job is not None
        session.add(
            CrawlPartition(
                job_id=job_id,
                status=CrawlPartitionStatus.pending.value,
                depth=0,
                range_start=datetime(2024, 1, 1, tzinfo=UTC),
                range_end=datetime(2024, 1, 2, tzinfo=UTC),
                query_string="language:Python",
                expected_count=10,
            )
        )
        job.status = CrawlJobStatus.completed.value
        job.stats_json = {"partition_status_counts": {"pending": 1}}
        session.commit()
    finally:
        session.close()

    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["status"] == "pending"

    verify_session = client.app.state.session_factory()
    try:
        persisted_job = verify_session.get(CrawlJob, job_id)
        assert persisted_job is not None
        assert persisted_job.status == CrawlJobStatus.completed.value
    finally:
        verify_session.close()


def test_admin_api_shows_planning_when_runner_has_started_but_job_has_no_partitions_yet(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'planning-display.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "planning-job",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]

    runner.running.add(job_id)

    jobs = client.get("/api/stage1/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["status"] == "planning"

    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["status"] == "planning"
    assert detail.json()["partitions"] == []


def test_admin_api_preserves_active_planning_progress_without_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'planning-progress.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "planning-progress-job",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert response.status_code == 200
    job_id = response.json()["job"]["id"]

    runner.running.add(job_id)

    session = client.app.state.session_factory()
    try:
        job = session.get(CrawlJob, job_id)
        assert job is not None
        job.status = CrawlJobStatus.planning.value
        job.stats_json = {
            "planning_progress": {
                "phase": "planning",
                "event": "counting",
                "processed_windows": 1,
                "discovered_windows": 4,
                "queued_windows": 3,
                "split_windows": 1,
                "planned_partitions": 0,
                "overflow_windows": 0,
                "empty_windows": 0,
                "probe_count": 1,
                "count_query_calls": 2,
                "current_depth": 1,
                "current_range_start": "2024-01-01T00:00:00+00:00",
                "current_range_end": "2024-01-02T00:00:00+00:00",
                "current_query_index": 1,
                "current_query_total": 2,
                "current_query_string": "language:Python created:2024-01-01..2024-01-02",
                "last_estimated_count": 123,
                "progress_ratio": 0.25,
                "updated_at": "2024-01-01T00:00:10+00:00",
            }
        }
        session.commit()
    finally:
        session.close()

    jobs = client.get("/api/stage1/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["status"] == "planning"
    assert jobs.json()["jobs"][0]["stats"]["planning_progress"]["progress_ratio"] == 0.25

    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["stats"]["planning_progress"]["count_query_calls"] == 2


def test_admin_api_preserves_failed_job_without_partitions_on_read(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'failed-read.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="failed-job",
            status=CrawlJobStatus.failed.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
            error_message="boom",
            started_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            finished_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    detail = client.get(f"/api/stage1/jobs/{job_id}")

    assert detail.status_code == 200
    assert detail.json()["job"]["status"] == "failed"
    assert detail.json()["job"]["error_message"] == "boom"


def test_admin_api_marks_overflow_only_partial_job_as_not_resumable(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'overflow-resume-read.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="overflow-only-partial",
            status=CrawlJobStatus.partial.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
            started_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            finished_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        session.add(job)
        session.flush()
        session.add(
            CrawlPartition(
                job_id=job.id,
                status=CrawlPartitionStatus.overflow.value,
                depth=0,
                range_start=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
                range_end=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
                query_string="language:Python",
                expected_count=1200,
                error_message="GitHub search returned more than the allowed results for a one-second partition.",
                finished_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
            )
        )
        session.commit()
        job_id = job.id
    finally:
        session.close()

    jobs = client.get("/api/stage1/jobs")
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["status"] == "partial"
    assert jobs.json()["jobs"][0]["can_resume"] is False

    detail = client.get(f"/api/stage1/jobs/{job_id}")
    assert detail.status_code == 200
    assert detail.json()["job"]["can_resume"] is False

    resume = client.post(f"/api/stage1/jobs/{job_id}/resume")
    assert resume.status_code == 409
    assert resume.json()["detail"] == "crawl job has no resumable work"


def test_admin_api_recovers_orphaned_planning_job_without_partitions_on_startup(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'startup-recover.db'}")
    bootstrap_client = TestClient(create_app(settings, runner=DummyRunner()))

    session = bootstrap_client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="orphan-planning-job",
            status=CrawlJobStatus.planning.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
            started_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()
        bootstrap_client.close()

    with TestClient(create_app(settings, runner=DummyRunner())) as client:
        jobs = client.get("/api/stage1/jobs")
        assert jobs.status_code == 200
        assert jobs.json()["jobs"][0]["id"] == job_id
        assert jobs.json()["jobs"][0]["status"] == "pending"
        assert jobs.json()["jobs"][0]["can_resume"] is True

        verify_session = client.app.state.session_factory()
        try:
            persisted_job = verify_session.get(CrawlJob, job_id)
            assert persisted_job is not None
            assert persisted_job.status == CrawlJobStatus.pending.value
            assert persisted_job.finished_at is None
        finally:
            verify_session.close()


def test_admin_api_finalizes_cancelling_batch_task_on_startup(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'batch-cancelling-recover.db'}")
    pool_root = tmp_path / "batch-pool"
    pool_root.mkdir()

    with TestClient(create_app(settings, runner=DummyRunner(), batch_runner=DummyBatchRunner())) as bootstrap_client:
        session = bootstrap_client.app.state.session_factory()
        try:
            pool = DataPool(name="batch-pool", root_path=str(pool_root))
            session.add(pool)
            session.flush()
            task = BatchTask(
                name="stuck-cancelling",
                data_pool_id=pool.id,
                status=BatchTaskStatus.running.value,
                phase="cancelling",
                cancel_requested=True,
                started_at=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
            )
            session.add(task)
            session.commit()
            task_id = task.id
        finally:
            session.close()

    batch_runner = DummyBatchRunner()
    with TestClient(create_app(settings, runner=DummyRunner(), batch_runner=batch_runner)) as client:
        response = client.get(f"/api/batch/tasks/{task_id}")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == BatchTaskStatus.cancelled.value
        assert payload["phase"] == "cancelled"
        assert payload["error_message"] == "batch task cancelled by user"
        assert payload["can_cancel"] is False
        assert payload["can_delete"] is True
        assert payload["can_retry"] is True
        assert batch_runner.scheduled == []

        verify_session = client.app.state.session_factory()
        try:
            persisted_task = verify_session.get(BatchTask, task_id)
            assert persisted_task is not None
            assert persisted_task.status == BatchTaskStatus.cancelled.value
            assert persisted_task.finished_at is not None
        finally:
            verify_session.close()


def test_admin_api_returns_error_when_background_schedule_fails(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'schedule-fail.db'}")
    client = TestClient(create_app(settings, runner=FalseScheduleRunner()))

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "api-job",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )

    assert response.status_code == 503


def test_admin_api_rejects_too_large_partition_concurrency(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'partition-concurrency-too-large.db'}")
    client = TestClient(create_app(settings, runner=DummyRunner()))

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "api-job",
            "run_in_background": True,
            "max_concurrent_partitions": 99,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )

    assert response.status_code == 400


def test_admin_api_returns_conflict_when_pause_request_cannot_be_registered(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pause-fail.db'}")
    client = TestClient(create_app(settings, runner=FalsePauseRunner()))

    response = client.post("/api/stage1/jobs/some-job-id/pause")

    assert response.status_code == 409


def test_admin_api_rejects_pause_for_queued_job(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'pause-queued.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    create = client.post(
        "/api/stage1/jobs",
        json={
            "name": "queued-job",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert create.status_code == 200
    job_id = create.json()["job"]["id"]

    response = client.post(f"/api/stage1/jobs/{job_id}/pause")

    assert response.status_code == 409
    assert runner.is_pause_requested(job_id) is False


def test_admin_api_rejects_delete_when_job_is_still_scheduled_but_not_cancelable(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'scheduled-delete-conflict.db'}")
    runner = NonCancelableScheduledRunner()
    client = TestClient(create_app(settings, runner=runner))

    create = client.post(
        "/api/stage1/jobs",
        json={
            "name": "delete-conflict-job",
            "run_in_background": False,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert create.status_code == 200
    job_id = create.json()["job"]["id"]
    runner.scheduled_ids.add(job_id)

    response = client.delete(f"/api/stage1/jobs/{job_id}")

    assert response.status_code == 409
    assert response.json()["detail"] == "cannot delete a scheduled crawl job"


def test_admin_api_preserves_queued_status_when_resume_fails_for_already_scheduled_job(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'resume-queued-conflict.db'}")
    runner = DedupScheduledRunner()
    client = TestClient(create_app(settings, runner=runner))

    create = client.post(
        "/api/stage1/jobs",
        json={
            "name": "resume-queued-job",
            "run_in_background": True,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )
    assert create.status_code == 200
    job_id = create.json()["job"]["id"]
    assert create.json()["job"]["status"] == "queued"

    resume = client.post(f"/api/stage1/jobs/{job_id}/resume")

    assert resume.status_code == 409
    assert resume.json()["detail"] == "crawl job could not be scheduled"

    verify_session = client.app.state.session_factory()
    try:
        persisted_job = verify_session.get(CrawlJob, job_id)
        assert persisted_job is not None
        assert persisted_job.status == CrawlJobStatus.queued.value
    finally:
        verify_session.close()


def test_admin_api_returns_structured_error_when_foreground_create_fails(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'foreground-create-fail.db'}")
    runner = FailingForegroundRunner()
    client = TestClient(create_app(settings, runner=runner), raise_server_exceptions=False)
    runner.session_factory = client.app.state.session_factory

    response = client.post(
        "/api/stage1/jobs",
        json={
            "name": "foreground-create-fail",
            "run_in_background": False,
            "filters": {
                "language": "Python",
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
            },
        },
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["message"] == "sync boom"
    assert detail["job"]["name"] == "foreground-create-fail"
    assert detail["job"]["status"] == "failed"
    assert detail["job"]["error_message"] == "sync boom"


def test_admin_api_persists_resolved_partition_concurrency_on_resume(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'resume-partition-concurrency.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="resume-partition-concurrency",
            status=CrawlJobStatus.pending.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={"max_concurrent_partitions": 2},
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    response = client.post(
        f"/api/stage1/jobs/{job_id}/resume",
        json={"run_in_background": False, "max_concurrent_partitions": 5},
    )

    assert response.status_code == 200
    assert response.json()["job"]["stats"]["max_concurrent_partitions"] == 5
    assert runner.scheduled[-1] == (job_id, 5)

    verify_session = client.app.state.session_factory()
    try:
        persisted_job = verify_session.get(CrawlJob, job_id)
        assert persisted_job is not None
        assert persisted_job.stats_json["max_concurrent_partitions"] == 5
    finally:
        verify_session.close()


def test_admin_api_returns_structured_error_when_foreground_resume_fails(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'foreground-resume-fail.db'}")
    runner = FailingForegroundRunner(fail_message="resume boom")
    client = TestClient(create_app(settings, runner=runner), raise_server_exceptions=False)
    runner.session_factory = client.app.state.session_factory

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="foreground-resume-fail",
            status=CrawlJobStatus.pending.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
        )
        session.add(job)
        session.commit()
        job_id = job.id
    finally:
        session.close()

    response = client.post(
        f"/api/stage1/jobs/{job_id}/resume",
        json={"run_in_background": False},
    )

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["message"] == "resume boom"
    assert detail["job"]["id"] == job_id
    assert detail["job"]["status"] == "failed"
    assert detail["job"]["error_message"] == "resume boom"


def test_admin_api_exposes_query_audit_and_repository_provenance_for_job_detail(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'job-audit.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="audit-job",
            status=CrawlJobStatus.completed.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "licenses": [],
                "keywords": [],
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "sort": "updated",
                "order": "desc",
            },
            stats_json={"total_partitions": 1, "partition_status_counts": {"completed": 1}, "unique_repositories": 1},
            started_at=datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC),
            finished_at=datetime(2024, 1, 1, 1, 5, 0, tzinfo=UTC),
        )
        session.add(job)
        session.flush()

        partition = CrawlPartition(
            job_id=job.id,
            status=CrawlPartitionStatus.completed.value,
            depth=0,
            range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, 0, 0, 0, tzinfo=UTC),
            query_string="language:Python\nlanguage:Go",
            expected_count=2,
            fetched_count=2,
            unique_count=1,
            started_at=datetime(2024, 1, 1, 1, 0, 0, tzinfo=UTC),
            finished_at=datetime(2024, 1, 1, 1, 5, 0, tzinfo=UTC),
        )
        session.add(partition)
        session.flush()

        repository = GitHubRepository(
            github_repo_id=3001,
            full_name="owner/audit-repo",
            owner_login="owner",
            name="audit-repo",
            html_url="https://github.com/owner/audit-repo",
            api_url="https://api.github.com/repos/owner/audit-repo",
            description="audit repo",
            primary_language="Python",
            license_key="mit",
            stargazers_count=42,
            created_at_github=datetime(2024, 1, 1, tzinfo=UTC),
            pushed_at_github=datetime(2024, 1, 2, tzinfo=UTC),
            discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
        )
        session.add(repository)
        session.flush()

        session.add(
            RepoDiscovery(
                job_id=job.id,
                partition_id=partition.id,
                repository_id=repository.id,
                query_string="language:Python",
                discovered_at=datetime(2024, 1, 3, tzinfo=UTC),
            )
        )
        session.commit()
    finally:
        session.close()

    response = client.get(f"/api/stage1/jobs/{job.id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["partitions"][0]["queries"] == [
        {
            "query_index": 0,
            "query_string": "language:Python",
            "status": "completed",
            "reported_total_count": None,
            "current_page": None,
            "total_pages": None,
            "fetched_count": None,
            "unique_count": 1,
            "error_message": None,
            "started_at": "2024-01-01T01:00:00+00:00",
            "finished_at": "2024-01-01T01:05:00+00:00",
        },
        {
            "query_index": 1,
            "query_string": "language:Go",
            "status": "completed",
            "reported_total_count": None,
            "current_page": None,
            "total_pages": None,
            "fetched_count": None,
            "unique_count": 0,
            "error_message": None,
            "started_at": "2024-01-01T01:00:00+00:00",
            "finished_at": "2024-01-01T01:05:00+00:00",
        },
    ]
    assert payload["repositories"][0]["full_name"] == "owner/audit-repo"
    assert payload["repositories"][0]["source_query"] == "language:Python"
    assert payload["repositories"][0]["source_partition_id"] == partition.id
    assert payload["repositories"][0]["source_range_start"] == "2024-01-01T00:00:00+00:00"
    assert payload["repositories"][0]["source_range_end"] == "2024-01-02T00:00:00+00:00"


def test_admin_api_reconciles_running_query_audit_for_recovered_partition(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'recover-query-audit.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="recovered-query-job",
            status=CrawlJobStatus.running.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "pushed_after": None,
                "pushed_before": None,
                "stars_min": None,
                "stars_max": None,
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "licenses": [],
                "keywords": [],
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
            started_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        session.add(job)
        session.flush()

        partition = CrawlPartition(
            job_id=job.id,
            status=CrawlPartitionStatus.running.value,
            depth=0,
            range_start=datetime(2024, 1, 1, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, tzinfo=UTC),
            query_string="language:Python",
            started_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        session.add(partition)
        session.flush()

        session.add(
            CrawlPartitionQueryExecution(
                partition_id=partition.id,
                query_index=0,
                query_string="language:Python",
                status="running",
                current_page=2,
                total_pages=5,
                started_at=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
            )
        )
        session.commit()
        job_id = job.id
        partition_id = partition.id
    finally:
        session.close()

    response = client.get(f"/api/stage1/jobs/{job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["status"] == "pending"
    assert payload["partitions"][0]["status"] == "pending"
    assert payload["partitions"][0]["queries"][0]["status"] == "pending"
    assert payload["partitions"][0]["queries"][0]["current_page"] is None
    assert payload["partitions"][0]["queries"][0]["total_pages"] is None
    assert payload["partitions"][0]["queries"][0]["finished_at"] is None

    verify_session = client.app.state.session_factory()
    try:
        persisted_query = verify_session.scalars(
            select(CrawlPartitionQueryExecution).where(
                CrawlPartitionQueryExecution.partition_id == partition_id
            )
        ).one()
        assert persisted_query.status == CrawlPartitionQueryStatus.running.value
    finally:
        verify_session.close()


def test_admin_api_preserves_running_partition_and_query_status_for_active_job(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'active-running-query-audit.db'}")
    runner = DummyRunner()
    client = TestClient(create_app(settings, runner=runner))

    session = client.app.state.session_factory()
    try:
        job = CrawlJob(
            name="active-running-query-job",
            status=CrawlJobStatus.running.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2024-01-01T00:00:00Z",
                "created_before": "2024-01-02T00:00:00Z",
                "pushed_after": None,
                "pushed_before": None,
                "stars_min": None,
                "stars_max": None,
                "exclude_forks": True,
                "exclude_archived": True,
                "public_only": True,
                "licenses": [],
                "keywords": [],
                "sort": "updated",
                "order": "desc",
            },
            stats_json={},
            started_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        )
        session.add(job)
        session.flush()

        partition = CrawlPartition(
            job_id=job.id,
            status=CrawlPartitionStatus.running.value,
            depth=0,
            range_start=datetime(2024, 1, 1, tzinfo=UTC),
            range_end=datetime(2024, 1, 2, tzinfo=UTC),
            query_string="language:Python",
            started_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
        )
        session.add(partition)
        session.flush()

        session.add(
            CrawlPartitionQueryExecution(
                partition_id=partition.id,
                query_index=0,
                query_string="language:Python",
                status=CrawlPartitionQueryStatus.running.value,
                current_page=3,
                total_pages=9,
                started_at=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
            )
        )
        session.commit()
        job_id = job.id
        runner.running.add(job_id)
    finally:
        session.close()

    response = client.get(f"/api/stage1/jobs/{job_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job"]["status"] == "running"
    assert payload["partitions"][0]["status"] == "running"
    assert payload["partitions"][0]["queries"][0]["status"] == "running"
    assert payload["partitions"][0]["queries"][0]["current_page"] == 3
    assert payload["partitions"][0]["queries"][0]["total_pages"] == 9
