from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import feature_factory.batch_runner as batch_runner_module
from feature_factory.batch import BatchTaskService
from feature_factory.batch_runner import BatchTaskRunner
from feature_factory.config import Settings
from feature_factory.db import build_engine, build_session_factory, init_db
from feature_factory.models import (
    BatchTask,
    BatchTaskEntry,
    BatchTaskRepository,
    BatchTaskUnit,
    CrawlJob,
    CrawlJobStatus,
    DataPool,
    DataPoolAsset,
    GitHubRepository,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage2TestResult,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.stage4.issue_styles import load_issue_style_catalog


class DummyStageRunner:
    def __init__(self) -> None:
        self.scheduled: list[str] = []
        self.interrupted: list[str] = []

    def schedule_run(self, run_id: str) -> bool:
        self.scheduled.append(run_id)
        return True

    def interrupt_run(self, run_id: str) -> bool:
        self.interrupted.append(run_id)
        return True


class NonInterruptingStageRunner(DummyStageRunner):
    def interrupt_run(self, run_id: str) -> bool:
        self.interrupted.append(run_id)
        return False


class VisibilityCheckingStageRunner(DummyStageRunner):
    def __init__(self, session_factory) -> None:
        super().__init__()
        self.session_factory = session_factory
        self.visible_run_ids: list[str] = []

    def schedule_run(self, run_id: str) -> bool:
        self.scheduled.append(run_id)
        session = self.session_factory()
        try:
            run = session.get(Stage2Run, run_id)
            if run is not None:
                self.visible_run_ids.append(run_id)
        finally:
            session.close()
        return True


def _make_repository() -> GitHubRepository:
    return GitHubRepository(
        github_repo_id=123456,
        full_name="owner/repo",
        owner_login="owner",
        name="repo",
        html_url="https://github.com/owner/repo",
        api_url="https://api.github.com/repos/owner/repo",
        default_branch="main",
        primary_language="Python",
        stargazers_count=42,
        discovered_at=datetime.now(UTC),
    )


def _make_runner(tmp_path):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'batch-runner.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage3_workspace_dir=tmp_path / "stage3",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    stage2_runner = DummyStageRunner()
    stage3_runner = DummyStageRunner()
    stage4_runner = DummyStageRunner()
    runner = BatchTaskRunner(
        session_factory,
        settings,
        stage2_runner=stage2_runner,
        stage3_runner=stage3_runner,
        stage4_runner=stage4_runner,
    )
    return settings, session_factory, runner, stage2_runner


def _seed_batch_entry(session, tmp_path):
    pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
    repository = _make_repository()
    session.add_all([pool, repository])
    session.flush()

    task = BatchTask(
        name="task",
        data_pool_id=pool.id,
        runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
        status="running",
        phase="pipeline",
    )
    session.add(task)
    session.flush()

    repo_row = BatchTaskRepository(
        task_id=task.id,
        repository_id=repository.id,
        github_repo_id=repository.github_repo_id,
        repo_full_name=repository.full_name,
        language=repository.primary_language,
        stars=repository.stargazers_count,
        status="running",
    )
    session.add(repo_row)
    session.flush()

    stage2_run = Stage2Run(
        repository_id=repository.id,
        status=Stage2RunStatus.completed.value,
        result=Stage2RunResult.passed.value,
        trigger_kind="manual",
        target_branch="main",
        target_commit_sha="abc123",
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(stage2_run)
    session.flush()

    snapshot = Stage3CommitSnapshot(
        repository_id=repository.id,
        source_stage2_run_id=stage2_run.id,
        source_commit_sha=stage2_run.target_commit_sha,
    )
    session.add(snapshot)
    session.flush()

    entry_file = Stage3EntryFile(
        snapshot_id=snapshot.id,
        test_file_path="tests/test_feature.py",
    )
    session.add(entry_file)
    session.flush()

    entry_row = BatchTaskEntry(
        task_id=task.id,
        task_repository_id=repo_row.id,
        stage3_entry_file_id=entry_file.id,
        entry_file_path=entry_file.test_file_path,
        stage3_run_id=None,
        status="pending",
    )
    session.add(entry_row)
    session.flush()
    return pool, repository, task, repo_row, stage2_run, snapshot, entry_file, entry_row


def test_batch_runner_marks_repo_partial_when_failed_units_have_assets(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        _pool, _repository, task, repo_row, _stage2_run, _snapshot, _entry_file, entry_row = _seed_batch_entry(
            session,
            tmp_path,
        )
        session.add_all(
            [
                BatchTaskUnit(
                    task_id=task.id,
                    task_repository_id=repo_row.id,
                    task_entry_id=entry_row.id,
                    depth=1,
                    status="completed",
                    data_pool_asset_id="asset-1",
                ),
                BatchTaskUnit(
                    task_id=task.id,
                    task_repository_id=repo_row.id,
                    task_entry_id=entry_row.id,
                    depth=2,
                    status="failed",
                    error_message="stage4 failed",
                ),
            ]
        )
        session.flush()

        runner._refresh_entry_status(entry_row)
        runner._refresh_repo_status(repo_row)
        BatchTaskService(session).refresh_task_stats(task.id)
        session.commit()

        assert entry_row.status == "completed"
        assert repo_row.status == "partial"
        assert repo_row.stage3_status == "completed"
        assert repo_row.stage4_status == "partial"
        assert repo_row.stats_json["asset_count"] == 1
        assert repo_row.stats_json["failed_unit_count"] == 1

        assert runner._finalize_if_terminal(task.id) is True
        session.expire_all()
        assert session.get(BatchTask, task.id).status == "partial"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_task_stats_include_running_stage_counts(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        _pool, repository, task, repo_row, _stage2_run, _snapshot, entry_file, entry_row = _seed_batch_entry(
            session,
            tmp_path,
        )
        running_stage2 = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="def456",
        )
        running_stage3 = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.running.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind="batch",
        )
        session.add_all([running_stage2, running_stage3])
        session.flush()
        savepoint = Stage3Savepoint(run_id=running_stage3.id, depth=1)
        session.add(savepoint)
        session.flush()
        running_stage4 = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.running.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="batch",
        )
        session.add(running_stage4)
        session.flush()

        repo_row.stage2_run_id = running_stage2.id
        entry_row.stage3_run_id = running_stage3.id
        session.add(
            BatchTaskUnit(
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                depth=1,
                status="running",
                stage3_savepoint_id=savepoint.id,
                stage4_run_id=running_stage4.id,
            )
        )
        session.flush()

        stats = BatchTaskService(session).refresh_task_stats(task.id)

        assert stats["stage2_running_count"] == 1
        assert stats["stage3_running_count"] == 1
        assert stats["stage4_running_count"] == 1
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_serializes_terminal_task_running_counts_as_zero(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        task = BatchTask(
            name="cancelled-with-stale-running-counts",
            data_pool_id=pool.id,
            status="cancelled",
            phase="cancelled",
            stats_json={
                "repository_count": 1,
                "stage2_running_count": 2,
                "stage3_running_count": 3,
                "stage4_running_count": 4,
            },
        )
        session.add(task)
        session.flush()

        payload = BatchTaskService(session).serialize_task_summary(task)

        assert payload["status"] == "cancelled"
        assert payload["stats"]["repository_count"] == 1
        assert payload["stats"]["stage2_running_count"] == 0
        assert payload["stats"]["stage3_running_count"] == 0
        assert payload["stats"]["stage4_running_count"] == 0
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_records_reused_pool_assets_as_completed_units(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool, repository, task, repo_row, stage2_run, _snapshot, entry_file, entry_row = _seed_batch_entry(
            session,
            tmp_path,
        )
        assets = [
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                source_commit_sha=stage2_run.target_commit_sha,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                entry_file_path=entry_file.test_file_path,
                depth=1,
                stage2_run_id=stage2_run.id,
                stage3_run_id="historical-stage3-run-1",
                stage3_savepoint_id=11,
                stage4_run_id="historical-stage4-run-1",
                folder_name="owner__repo__abc123__tests_test_feature_py__depth-1",
                folder_path=str(tmp_path / "pool" / "asset-depth-1"),
                manifest_json={},
            ),
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                source_commit_sha=stage2_run.target_commit_sha,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                entry_file_path=entry_file.test_file_path,
                depth=2,
                stage2_run_id=stage2_run.id,
                stage3_run_id="historical-stage3-run-2",
                stage3_savepoint_id=12,
                stage4_run_id="historical-stage4-run-2",
                folder_name="owner__repo__abc123__tests_test_feature_py__depth-2",
                folder_path=str(tmp_path / "pool" / "asset-depth-2"),
                manifest_json={},
            ),
        ]
        session.add_all(assets)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        runner._refresh_repo_status(repo_row)
        task_stats = BatchTaskService(session).refresh_task_stats(task.id)
        session.flush()

        units = list(
            session.scalars(
                select(BatchTaskUnit)
                .where(BatchTaskUnit.task_entry_id == entry_row.id)
                .order_by(BatchTaskUnit.depth.asc())
            )
        )
        stage3_runs = list(
            session.scalars(select(Stage3Run).where(Stage3Run.entry_file_id == entry_file.id))
        )
        payload = BatchTaskService(session).serialize_task_repository(repo_row)

        assert pending_schedules == []
        assert pending_materializations == []
        assert stage3_runs == []
        assert entry_row.stage3_run_id is None
        assert entry_row.status == "completed"
        assert repo_row.stage3_status == "completed"
        assert repo_row.stage4_status == "completed"
        assert repo_row.stats_json["asset_count"] == 2
        assert task_stats["unit_count"] == 2
        assert task_stats["asset_count"] == 2
        assert [unit.depth for unit in units] == [1, 2]
        assert [unit.status for unit in units] == ["completed", "completed"]
        assert [unit.data_pool_asset_id for unit in units] == [assets[0].id, assets[1].id]
        assert [unit.stage3_savepoint_id for unit in units] == [None, None]
        assert [unit.stage4_run_id for unit in units] == [None, None]
        assert payload["stage3_status"] == "completed"
        assert payload["stage4_status"] == "completed"
        assert len(payload["entries"][0]["units"]) == 2
        assert [unit["stage3_savepoint_id"] for unit in payload["entries"][0]["units"]] == [None, None]
        assert [unit["stage4_run_id"] for unit in payload["entries"][0]["units"]] == [None, None]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_serializes_failed_repo_with_assets_as_partial(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="historical-partial",
            data_pool_id=pool.id,
            status="partial",
            phase="partial",
        )
        session.add(task)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="failed",
            stats_json={"asset_count": 3, "failed_unit_count": 1},
        )
        session.add(repo_row)
        session.flush()
        session.add(
            BatchTaskEntry(
                task_id=task.id,
                task_repository_id=repo_row.id,
                entry_file_path="tests/test_stale_running.py",
                status="running",
            )
        )
        session.flush()

        payload = BatchTaskService(session).serialize_task_repository(repo_row)

        assert repo_row.status == "failed"
        assert payload["status"] == "partial"

        service = BatchTaskService(session)
        partial_payload = service.list_task_repositories(task.id, status="partial")
        assert [row["repo_full_name"] for row in partial_payload["repositories"]] == [repository.full_name]

        failed_payload = service.list_task_repositories(task.id, status="failed")
        assert failed_payload["repositories"] == []

        running_payload = service.list_task_repositories(task.id, status="running")
        assert running_payload["repositories"] == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_serializes_unstarted_running_repo_as_pending(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="waiting-stage2-capacity",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
            stage2_status="pending",
        )
        session.add(repo_row)
        session.flush()

        payload = BatchTaskService(session).serialize_task_repository(repo_row)

        assert repo_row.status == "running"
        assert payload["status"] == "pending"
        assert payload["stage2_status"] == "pending"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_uses_live_stage2_run_status_for_repository_status(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stage2-live-status",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
            stage2_status="queued",
            stage2_run_id=run.id,
        )
        session.add(repo_row)
        session.flush()

        service = BatchTaskService(session)
        payload = service.serialize_task_repository(repo_row)

        assert payload["status"] == "running"
        assert payload["stage2_status"] == "running"

        running_payload = service.list_task_repositories(task.id, status="running")
        assert [row["repo_full_name"] for row in running_payload["repositories"]] == [repository.full_name]

        waiting_payload = service.list_task_repositories(task.id, status="pending")
        assert waiting_payload["repositories"] == []

        stage2_running_payload = service.list_task_repositories(task.id, stage2_status="running")
        assert [row["repo_full_name"] for row in stage2_running_payload["repositories"]] == [repository.full_name]

        stage2_waiting_payload = service.list_task_repositories(task.id, stage2_status="pending")
        assert stage2_waiting_payload["repositories"] == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_uses_live_stage2_waiting_run_status_for_repository_status(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stage2-live-waiting-status",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.queued.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
            stage2_status="running",
            stage2_run_id=run.id,
        )
        session.add(repo_row)
        session.flush()

        service = BatchTaskService(session)
        payload = service.serialize_task_repository(repo_row)

        assert payload["status"] == "pending"
        assert payload["stage2_status"] == "queued"

        running_payload = service.list_task_repositories(task.id, status="running")
        assert running_payload["repositories"] == []

        waiting_payload = service.list_task_repositories(task.id, status="pending")
        assert [row["repo_full_name"] for row in waiting_payload["repositories"]] == [repository.full_name]

        stage2_running_payload = service.list_task_repositories(task.id, stage2_status="running")
        assert stage2_running_payload["repositories"] == []

        stage2_waiting_payload = service.list_task_repositories(task.id, stage2_status="pending")
        assert [row["repo_full_name"] for row in stage2_waiting_payload["repositories"]] == [repository.full_name]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_lists_running_repositories_before_waiting(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        running_repository = _make_repository()
        waiting_repository = GitHubRepository(
            github_repo_id=654321,
            full_name="owner/waiting",
            owner_login="owner",
            name="waiting",
            html_url="https://github.com/owner/waiting",
            api_url="https://api.github.com/repos/owner/waiting",
            default_branch="main",
            primary_language="Python",
            stargazers_count=24,
            discovered_at=datetime.now(UTC),
        )
        session.add_all([pool, running_repository, waiting_repository])
        session.flush()
        task = BatchTask(
            name="running-first",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        now = datetime.now(UTC)
        running_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=running_repository.id,
            github_repo_id=running_repository.github_repo_id,
            repo_full_name=running_repository.full_name,
            language=running_repository.primary_language,
            stars=running_repository.stargazers_count,
            status="pending",
            stage2_status="completed",
            stage3_status="running",
            updated_at=now - timedelta(minutes=10),
        )
        waiting_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=waiting_repository.id,
            github_repo_id=waiting_repository.github_repo_id,
            repo_full_name=waiting_repository.full_name,
            language=waiting_repository.primary_language,
            stars=waiting_repository.stargazers_count,
            status="running",
            stage2_status="completed",
            stage3_status="pending",
            updated_at=now,
        )
        session.add_all([running_row, waiting_row])
        session.flush()
        session.add_all(
            [
                BatchTaskEntry(
                    task_id=task.id,
                    task_repository_id=running_row.id,
                    entry_file_path="tests/test_running.py",
                    status="running",
                    updated_at=now - timedelta(minutes=10),
                ),
                BatchTaskEntry(
                    task_id=task.id,
                    task_repository_id=waiting_row.id,
                    entry_file_path="tests/test_waiting.py",
                    status="pending",
                    updated_at=now,
                ),
            ]
        )
        session.flush()

        payload = BatchTaskService(session).list_task_repositories(task.id)

        assert [row["repo_full_name"] for row in payload["repositories"]] == [
            running_repository.full_name,
            waiting_repository.full_name,
        ]
        assert [row["status"] for row in payload["repositories"]] == ["running", "pending"]

        waiting_payload = BatchTaskService(session).list_task_repositories(task.id, stage3_status="pending")
        assert [row["repo_full_name"] for row in waiting_payload["repositories"]] == [waiting_repository.full_name]

        running_payload = BatchTaskService(session).list_task_repositories(task.id, stage3_status="running")
        assert [row["repo_full_name"] for row in running_payload["repositories"]] == [running_repository.full_name]

        waiting_status_payload = BatchTaskService(session).list_task_repositories(task.id, status="pending")
        assert [row["repo_full_name"] for row in waiting_status_payload["repositories"]] == [
            waiting_repository.full_name
        ]

        running_status_payload = BatchTaskService(session).list_task_repositories(task.id, status="running")
        assert [row["repo_full_name"] for row in running_status_payload["repositories"]] == [
            running_repository.full_name
        ]

        non_waiting_payload = BatchTaskService(session).list_task_repositories(task.id, non_pending=True)
        assert [row["repo_full_name"] for row in non_waiting_payload["repositories"]] == [
            running_repository.full_name
        ]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_running_priority_handles_null_stage2_run_id_with_terminal_stage2_runs(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        running_repository = _make_repository()
        waiting_repository = GitHubRepository(
            github_repo_id=654321,
            full_name="owner/waiting",
            owner_login="owner",
            name="waiting",
            html_url="https://github.com/owner/waiting",
            api_url="https://api.github.com/repos/owner/waiting",
            default_branch="main",
            primary_language="Python",
            stargazers_count=24,
            discovered_at=datetime.now(UTC),
        )
        terminal_repository = GitHubRepository(
            github_repo_id=999999,
            full_name="owner/terminal",
            owner_login="owner",
            name="terminal",
            html_url="https://github.com/owner/terminal",
            api_url="https://api.github.com/repos/owner/terminal",
            default_branch="main",
            primary_language="Python",
            stargazers_count=1,
            discovered_at=datetime.now(UTC),
        )
        session.add_all([pool, running_repository, waiting_repository, terminal_repository])
        session.flush()
        task = BatchTask(
            name="running-first-null-stage2-run",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        now = datetime.now(UTC)
        session.add(
            Stage2Run(
                repository_id=terminal_repository.id,
                status=Stage2RunStatus.completed.value,
                result=Stage2RunResult.abandoned.value,
                trigger_kind="batch",
                target_branch="main",
                target_commit_sha="terminal123",
                finished_at=now,
            )
        )
        running_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=running_repository.id,
            github_repo_id=running_repository.github_repo_id,
            repo_full_name=running_repository.full_name,
            language=running_repository.primary_language,
            stars=running_repository.stargazers_count,
            status="running",
            stage2_status="completed",
            stage3_status="running",
            updated_at=now - timedelta(minutes=10),
        )
        waiting_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=waiting_repository.id,
            github_repo_id=waiting_repository.github_repo_id,
            repo_full_name=waiting_repository.full_name,
            language=waiting_repository.primary_language,
            stars=waiting_repository.stargazers_count,
            status="pending",
            stage2_status="completed",
            stage3_status="pending",
            updated_at=now,
        )
        session.add_all([running_row, waiting_row])
        session.flush()
        session.add_all(
            [
                BatchTaskEntry(
                    task_id=task.id,
                    task_repository_id=running_row.id,
                    entry_file_path="tests/test_running.py",
                    status="running",
                    updated_at=now - timedelta(minutes=10),
                ),
                BatchTaskEntry(
                    task_id=task.id,
                    task_repository_id=waiting_row.id,
                    entry_file_path="tests/test_waiting.py",
                    status="pending",
                    updated_at=now,
                ),
            ]
        )
        session.flush()

        payload = BatchTaskService(session).list_task_repositories(task.id)

        assert [row["repo_full_name"] for row in payload["repositories"]] == [
            running_repository.full_name,
            waiting_repository.full_name,
        ]
        assert [row["status"] for row in payload["repositories"]] == ["running", "pending"]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_serializes_completed_stage3_entry_with_active_stage4(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stage4-active",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="completed",
            stage2_status="completed",
            stage3_status="running",
            stage4_status="pending",
        )
        session.add(repo_row)
        session.flush()
        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id="stage2-run",
            source_commit_sha="abc123",
        )
        session.add(snapshot)
        session.flush()
        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_feature.py",
        )
        session.add(entry_file)
        session.flush()
        run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        savepoint = Stage3Savepoint(run_id=run.id, depth=1)
        session.add(savepoint)
        session.flush()
        stage4_run = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.running.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
        )
        session.add(stage4_run)
        session.flush()
        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=run.id,
            status="running",
        )
        session.add(entry_row)
        session.flush()
        session.add(
            BatchTaskUnit(
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                depth=1,
                status="queued",
                stage3_savepoint_id=savepoint.id,
                stage4_run_id=stage4_run.id,
            )
        )
        session.flush()

        payload = BatchTaskService(session).serialize_task_repository(repo_row)

        assert payload["status"] == "running"
        assert payload["stage3_status"] == "completed"
        assert payload["stage4_status"] == "running"
        assert payload["entries"][0]["status"] == "completed"
        assert payload["entries"][0]["units"][0]["status"] == "running"

        service = BatchTaskService(session)
        stage3_completed_payload = service.list_task_repositories(task.id, stage3_status="completed")
        assert [row["repo_full_name"] for row in stage3_completed_payload["repositories"]] == [
            repository.full_name
        ]

        stage3_running_payload = service.list_task_repositories(task.id, stage3_status="running")
        assert stage3_running_payload["repositories"] == []

        stage4_running_payload = service.list_task_repositories(task.id, stage4_status="running")
        assert [row["repo_full_name"] for row in stage4_running_payload["repositories"]] == [
            repository.full_name
        ]

        running_payload = service.list_task_repositories(task.id, status="running")
        assert [row["repo_full_name"] for row in running_payload["repositories"]] == [repository.full_name]

        completed_payload = service.list_task_repositories(task.id, status="completed")
        assert completed_payload["repositories"] == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


@pytest.mark.parametrize("entry_status", ["running", "failed", "partial"])
def test_batch_service_ignores_stale_stage3_open_entry_when_run_completed(
    tmp_path,
    entry_status: str,
) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stale-stage3-running",
            data_pool_id=pool.id,
            status="completed",
            phase="completed",
        )
        session.add(task)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="completed",
            stage2_status="completed",
            stage3_status="running",
            stage4_status="completed",
        )
        session.add(repo_row)
        session.flush()
        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id="stage2-run",
            source_commit_sha="abc123",
        )
        session.add(snapshot)
        session.flush()
        entry_file = Stage3EntryFile(snapshot_id=snapshot.id, test_file_path="tests/test_feature.py")
        session.add(entry_file)
        session.flush()
        run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=run.id,
            status=entry_status,
        )
        session.add(entry_row)
        session.flush()
        session.add(
            BatchTaskUnit(
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                depth=1,
                status="completed",
                data_pool_asset_id="asset-1",
            )
        )
        session.flush()

        service = BatchTaskService(session)
        payload = service.serialize_task_repository(repo_row)

        assert payload["status"] == "completed"
        assert payload["stage3_status"] == "completed"
        assert payload["stage4_status"] == "completed"
        assert payload["entries"][0]["status"] == "completed"

        running_payload = service.list_task_repositories(task.id, status="running")
        assert running_payload["repositories"] == []

        completed_payload = service.list_task_repositories(task.id, status="completed")
        assert [row["repo_full_name"] for row in completed_payload["repositories"]] == [repository.full_name]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_service_uses_live_stage4_waiting_run_status_for_unit_status(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stage4-live-waiting-status",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
            stage2_status="completed",
            stage3_status="completed",
            stage4_status="running",
        )
        session.add(repo_row)
        session.flush()
        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            entry_file_path="tests/test_feature.py",
            status="completed",
        )
        session.add(entry_row)
        session.flush()
        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id="stage2-run",
            source_commit_sha="abc123",
        )
        session.add(snapshot)
        session.flush()
        entry_file = Stage3EntryFile(snapshot_id=snapshot.id, test_file_path=entry_row.entry_file_path)
        session.add(entry_file)
        session.flush()
        stage3_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage3_run)
        session.flush()
        savepoint = Stage3Savepoint(run_id=stage3_run.id, depth=1)
        session.add(savepoint)
        session.flush()
        stage4_run = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.queued.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
        )
        session.add(stage4_run)
        session.flush()
        session.add(
            BatchTaskUnit(
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                depth=1,
                status="running",
                stage4_run_id=stage4_run.id,
            )
        )
        session.flush()

        service = BatchTaskService(session)
        payload = service.serialize_task_repository(repo_row)

        assert payload["status"] == "pending"
        assert payload["stage4_status"] == "pending"
        assert payload["entries"][0]["units"][0]["status"] == "queued"

        running_payload = service.list_task_repositories(task.id, status="running")
        assert running_payload["repositories"] == []

        waiting_payload = service.list_task_repositories(task.id, status="pending")
        assert [row["repo_full_name"] for row in waiting_payload["repositories"]] == [repository.full_name]

        stage4_running_payload = service.list_task_repositories(task.id, stage4_status="running")
        assert stage4_running_payload["repositories"] == []

        stage4_waiting_payload = service.list_task_repositories(task.id, stage4_status="pending")
        assert [row["repo_full_name"] for row in stage4_waiting_payload["repositories"]] == [repository.full_name]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


@pytest.mark.parametrize("stage2_result", [Stage2RunResult.abandoned.value, Stage2RunResult.defect.value])
def test_batch_service_serializes_stage2_non_pipeline_terminal_rows(tmp_path, stage2_result: str) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="historical-stage2-terminal",
            data_pool_id=pool.id,
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=stage2_result,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="failed",
            stage2_status="failed",
            stage2_run_id=run.id,
        )
        session.add(repo_row)
        session.flush()

        payload = BatchTaskService(session).serialize_task_repository(repo_row)

        assert payload["status"] == stage2_result
        assert payload["stage2_status"] == stage2_result
        assert payload["stage3_status"] == "pending"
        assert payload["stage4_status"] == "pending"

        service = BatchTaskService(session)
        terminal_payload = service.list_task_repositories(task.id, status=stage2_result)
        assert [row["repo_full_name"] for row in terminal_payload["repositories"]] == [repository.full_name]

        failed_payload = service.list_task_repositories(task.id, status="failed")
        assert failed_payload["repositories"] == []

        stage2_terminal_payload = service.list_task_repositories(task.id, stage2_status=stage2_result)
        assert [row["repo_full_name"] for row in stage2_terminal_payload["repositories"]] == [repository.full_name]

        stage2_failed_payload = service.list_task_repositories(task.id, stage2_status="failed")
        assert stage2_failed_payload["repositories"] == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


@pytest.mark.parametrize("stage2_result", [Stage2RunResult.abandoned.value, Stage2RunResult.defect.value])
def test_batch_runner_treats_stage2_non_pipeline_terminal_as_completed_task(
    tmp_path,
    stage2_result: str,
) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="stage2-terminal",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=stage2_result,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
            stage2_status="running",
            stage2_run_id=run.id,
        )
        session.add(repo_row)
        session.flush()

        runner._process_repository(session, task, repo_row, [], [])
        BatchTaskService(session).refresh_task_stats(task.id)
        session.commit()

        assert stage2_runner.scheduled == []
        assert repo_row.status == stage2_result
        assert repo_row.stage2_status == stage2_result
        assert repo_row.stage3_status == "pending"
        assert repo_row.stage4_status == "pending"
        assert repo_row.error_message is None
        assert repo_row.finished_at is not None

        assert runner._finalize_if_terminal(task.id) is True
        session.expire_all()
        assert session.get(BatchTask, task.id).status == "completed"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


@pytest.mark.parametrize("stage2_result", [Stage2RunResult.abandoned.value, Stage2RunResult.defect.value])
def test_batch_retry_reuses_stage2_non_pipeline_terminal_result(
    tmp_path,
    stage2_result: str,
) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="retry-stage2-terminal",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="partial",
            phase="partial",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=stage2_result,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status=stage2_result,
            stage2_status=stage2_result,
            stage2_run_id=run.id,
            finished_at=datetime.now(UTC),
        )
        session.add(repo_row)
        session.flush()

        BatchTaskService(session).retry_task(task.id)
        session.flush()
        assert repo_row.status == "pending"
        assert repo_row.stage2_status == "pending"
        assert repo_row.stage2_run_id is None
        assert repo_row.source_stage2_run_id is None

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        BatchTaskService(session).refresh_task_stats(task.id)
        session.commit()

        assert pending_schedules == []
        assert pending_materializations == []
        assert stage2_runner.scheduled == []
        assert repo_row.stage2_run_id is None
        assert repo_row.source_stage2_run_id == run.id
        assert repo_row.status == stage2_result
        assert repo_row.stage2_status == stage2_result
        assert repo_row.stage3_status == "pending"
        assert repo_row.stage4_status == "pending"
        assert repo_row.error_message is None
        assert repo_row.stats_json == {
            "entry_file_count": 0,
            "asset_count": 0,
            "failed_unit_count": 0,
        }

        assert runner._finalize_if_terminal(task.id) is True
        session.expire_all()
        assert session.get(BatchTask, task.id).status == "completed"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_stage1_uses_task_quota_up_to_stage1_cap(tmp_path, monkeypatch) -> None:
    settings, session_factory, runner, _ = _make_runner(tmp_path)
    settings.stage1_max_concurrent_jobs = 4
    settings.stage1_max_concurrent_jobs_cap = 24
    captured: dict[str, int | None] = {}

    class CapturingStage1JobRunner:
        def __init__(self, session_factory, settings, *, max_workers, max_limit, token_scheduler=None) -> None:
            del session_factory, settings, token_scheduler
            captured["max_workers"] = max_workers
            captured["max_limit"] = max_limit

        def run_job(self, job_id: str, *, max_concurrent_partitions: int | None = None) -> None:
            captured["max_concurrent_partitions"] = max_concurrent_partitions

        def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
            del timeout_seconds
            return []

    class NoopGitHubSearchClient:
        def __init__(self, settings, token_scheduler=None) -> None:
            del settings, token_scheduler

        def count_repositories(self, query: str) -> int:
            del query
            return 0

        def close(self) -> None:
            return None

    monkeypatch.setattr(batch_runner_module, "Stage1JobRunner", CapturingStage1JobRunner)
    monkeypatch.setattr(batch_runner_module, "GitHubSearchClient", NoopGitHubSearchClient)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        task = BatchTask(
            name="stage1-quota",
            data_pool_id=pool.id,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
                "created_before": "2020-01-01T00:00:00+00:00",
            },
            runtime_snapshot_json={
                "stage1": {
                    "max_concurrent_jobs": 10,
                    "max_concurrent_partitions": 3,
                },
            },
            status="running",
            phase="stage1",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    finally:
        session.close()

    try:
        runner._run_stage1(task_id)

        assert captured == {
            "max_workers": 10,
            "max_limit": 24,
            "max_concurrent_partitions": 3,
        }
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_batch_stage1_reuses_existing_target_repository_rows(tmp_path, monkeypatch) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    class FailingGitHubSearchClient:
        def __init__(self, settings, token_scheduler=None) -> None:
            del settings, token_scheduler

        def get_repository(self, full_name: str) -> dict:
            raise AssertionError(f"unexpected target repository fetch: {full_name}")

        def close(self) -> None:
            return None

    monkeypatch.setattr(batch_runner_module, "GitHubSearchClient", FailingGitHubSearchClient)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="reuse-target-stage1",
            data_pool_id=pool.id,
            filters_json={
                "created_after": "1970-01-01T00:00:00+00:00",
                "target_repositories": [repository.full_name],
            },
            runtime_snapshot_json={},
            status="running",
            phase="stage1",
        )
        session.add(task)
        session.flush()
        session.add(
            BatchTaskRepository(
                task_id=task.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                status="pending",
            )
        )
        session.commit()
        task_id = task.id
        repo_full_name = repository.full_name
    finally:
        session.close()

    try:
        runner._run_stage1(task_id)

        session = session_factory()
        try:
            task = session.get(BatchTask, task_id)
            rows = list(session.scalars(select(BatchTaskRepository).where(BatchTaskRepository.task_id == task_id)))
            assert task is not None
            assert task.phase == "pipeline"
            assert [row.repo_full_name for row in rows] == [repo_full_name]
        finally:
            session.close()
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_batch_stage1_skips_completed_crawl_job(tmp_path, monkeypatch) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    class FailingStage1JobRunner:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def run_job(self, job_id: str, *, max_concurrent_partitions: int | None = None) -> None:
            del max_concurrent_partitions
            raise AssertionError(f"unexpected stage1 rerun: {job_id}")

        def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
            del timeout_seconds
            return []

    monkeypatch.setattr(batch_runner_module, "Stage1JobRunner", FailingStage1JobRunner)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        crawl_job = CrawlJob(
            name="completed-stage1",
            status=CrawlJobStatus.completed.value,
            filters_json={
                "language": "Python",
                "languages": ["Python"],
                "created_after": "2020-01-01T00:00:00+00:00",
            },
            stats_json={},
            finished_at=datetime.now(UTC),
        )
        session.add_all([pool, crawl_job])
        session.flush()
        task = BatchTask(
            name="reuse-crawl-stage1",
            data_pool_id=pool.id,
            stage1_crawl_job_id=crawl_job.id,
            filters_json=crawl_job.filters_json,
            runtime_snapshot_json={},
            status="running",
            phase="stage1",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    finally:
        session.close()

    try:
        runner._run_stage1(task_id)

        session = session_factory()
        try:
            task = session.get(BatchTask, task_id)
            assert task is not None
            assert task.phase == "pipeline"
            assert session.get(CrawlJob, task.stage1_crawl_job_id).status == CrawlJobStatus.completed.value
        finally:
            session.close()
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_batch_target_repositories_apply_repository_limit(tmp_path, monkeypatch) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)
    fetched_repositories: list[str] = []

    class CapturingGitHubSearchClient:
        def __init__(self, settings, token_scheduler=None) -> None:
            del settings, token_scheduler

        def get_repository(self, full_name: str) -> dict:
            fetched_repositories.append(full_name)
            owner, name = full_name.split("/", 1)
            github_id = 50_000 + len(fetched_repositories)
            return {
                "id": github_id,
                "node_id": f"R_{github_id}",
                "full_name": full_name,
                "owner": {"login": owner},
                "name": name,
                "html_url": f"https://github.com/{full_name}",
                "url": f"https://api.github.com/repos/{full_name}",
                "description": "repo",
                "default_branch": "main",
                "language": "Python",
                "license": {"key": "mit"},
                "visibility": "public",
                "private": False,
                "fork": False,
                "archived": False,
                "stargazers_count": 10,
                "forks_count": 0,
                "open_issues_count": 0,
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "pushed_at": "2024-01-01T00:00:00Z",
            }

        def close(self) -> None:
            return None

    monkeypatch.setattr(batch_runner_module, "GitHubSearchClient", CapturingGitHubSearchClient)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        task = BatchTask(
            name="target-limit",
            data_pool_id=pool.id,
            filters_json={
                "created_after": "1970-01-01T00:00:00+00:00",
                "target_repositories": ["owner/repo-one", "owner/repo-two"],
                "repository_limit": 1,
            },
            runtime_snapshot_json={},
            status="running",
            phase="stage1",
        )
        session.add(task)
        session.commit()
        task_id = task.id
    finally:
        session.close()

    try:
        runner._run_stage1(task_id)

        session = session_factory()
        try:
            rows = list(
                session.scalars(
                    select(BatchTaskRepository).where(BatchTaskRepository.task_id == task_id)
                )
            )
            task = session.get(BatchTask, task_id)
            assert fetched_repositories == ["owner/repo-one"]
            assert [row.repo_full_name for row in rows] == ["owner/repo-one"]
            assert task is not None
            assert task.phase == "pipeline"
        finally:
            session.close()
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_reuses_historical_successful_stage2_when_stage3_snapshot_is_materializable(tmp_path) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
        )
        session.add(repo_row)

        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()
        session.add(
            Stage2TestResult(
                run_id=historical_run.id,
                test_file_path="tests/test_feature.py",
                status="passed",
                total_tests=4,
                passed_tests=4,
            )
        )
        session.commit()

        session.refresh(task)
        session.refresh(repo_row)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        session.commit()
        runner._dispatch_pending_schedules(pending_schedules)

        assert stage2_runner.scheduled == []
        assert repo_row.source_stage2_run_id == historical_run.id
        assert repo_row.stage2_run_id is None
        assert repo_row.stage3_snapshot_id is not None
        assert repo_row.stage2_status == "completed"
        assert repo_row.stage3_status == "pending"
        assert repo_row.status == "pending"
        assert repo_row.error_message is None
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_stage2_only_creates_fresh_run_and_stops_before_stage3(
    tmp_path,
    monkeypatch,
) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)
    monkeypatch.setattr(batch_runner_module, "resolve_repo_head_commit", lambda _repository: "abc123")

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="stage2-only",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "pipeline": {"stop_after_stage": "stage2"},
                "stage2": {"planner": {"model": "openai/gpt-5.6-luna"}},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
        )
        session.add(repo_row)

        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            runtime_snapshot_json={"planner": {"model": "old-model"}},
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, [])
        session.flush()

        fresh_run = session.get(Stage2Run, repo_row.stage2_run_id)
        assert fresh_run is not None
        assert fresh_run.id != historical_run.id
        assert fresh_run.status == Stage2RunStatus.queued.value
        assert fresh_run.runtime_snapshot_json["planner"]["model"] == "openai/gpt-5.6-luna"
        assert pending_schedules == [{"kind": "stage2", "run_id": fresh_run.id}]
        assert repo_row.source_stage2_run_id is None

        fresh_run.status = Stage2RunStatus.completed.value
        fresh_run.result = Stage2RunResult.passed.value
        fresh_run.finished_at = datetime.now(UTC)
        session.flush()

        runner._process_repository(session, task, repo_row, [], [])
        session.commit()

        assert stage2_runner.scheduled == []
        assert repo_row.status == "completed"
        assert repo_row.stage2_status == "completed"
        assert repo_row.stage3_status == "skipped"
        assert repo_row.stage4_status == "skipped"
        assert repo_row.finished_at is not None
        assert session.scalar(select(Stage3CommitSnapshot)) is None
        assert runner._finalize_if_terminal(task.id) is True
        session.expire_all()
        assert session.get(BatchTask, task.id).status == "completed"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_completes_repo_when_test_count_filter_removes_all_entries(tmp_path) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "stage2": {"hyperparameters": {"entry_file_test_count_min": 3}},
                "stage3": {},
                "stage4": {},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
        )
        session.add(repo_row)

        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            runtime_snapshot_json={"hyperparameters": {"entry_file_test_count_min": 3}},
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()
        session.add(
            Stage2TestResult(
                run_id=historical_run.id,
                test_file_path="tests/test_tiny.py",
                status="passed",
                total_tests=2,
                passed_tests=2,
            )
        )
        session.commit()

        session.refresh(task)
        session.refresh(repo_row)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        session.commit()

        snapshot = session.scalar(select(Stage3CommitSnapshot))
        assert snapshot is not None
        assert snapshot.summary_json["filtered_out_entry_file_count"] == 1
        assert [row["path"] for row in snapshot.original_p2p_files_json] == ["tests/test_tiny.py"]
        assert not list(snapshot.entry_files or [])
        assert stage2_runner.scheduled == []
        assert pending_schedules == []
        assert pending_materializations == []
        assert repo_row.stage3_snapshot_id == snapshot.id
        assert repo_row.status == "completed"
        assert repo_row.stage3_status == "completed"
        assert repo_row.stage4_status == "completed"
        assert repo_row.stats_json["entry_file_count"] == 0
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_does_not_apply_stage2_p2p_file_count_limit_to_existing_snapshot(tmp_path) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)
    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "stage2": {"hyperparameters": {"p2p_file_count_limit": 2}},
                "stage3": {},
                "stage4": {},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
        )
        session.add(repo_row)

        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            runtime_snapshot_json={"hyperparameters": {}},
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=historical_run.id,
            source_commit_sha="abc123",
            target_branch="main",
            original_p2p_files_json=[],
            summary_json={"entry_file_count": 3},
        )
        snapshot.entry_files = [
            Stage3EntryFile(test_file_path="tests/test_a.py", baseline_total_tests=5),
            Stage3EntryFile(test_file_path="tests/test_b.py", baseline_total_tests=5),
            Stage3EntryFile(test_file_path="tests/test_c.py", baseline_total_tests=5),
        ]
        session.add(snapshot)
        session.commit()

        session.refresh(task)
        session.refresh(repo_row)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        session.commit()

        entries = list(session.scalars(select(BatchTaskEntry).order_by(BatchTaskEntry.entry_file_path.asc())))
        assert [entry.entry_file_path for entry in entries] == [
            "tests/test_a.py",
            "tests/test_b.py",
            "tests/test_c.py",
        ]
        assert stage2_runner.scheduled == []
        assert repo_row.stage3_snapshot_id == snapshot.id
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_reruns_stage2_when_historical_successful_stage2_has_no_usable_stage3_snapshot(tmp_path) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {"planner": {"model": "planner-a"}}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="pending",
        )
        session.add(repo_row)

        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.commit()

        session.refresh(task)
        session.refresh(repo_row)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        session.commit()
        runner._dispatch_pending_schedules(pending_schedules)

        assert len(stage2_runner.scheduled) == 1
        assert repo_row.stage2_run_id == stage2_runner.scheduled[0]
        assert repo_row.source_stage2_run_id is None
        assert repo_row.stage2_status == "queued"
        assert repo_row.stage3_status == "pending"
        assert repo_row.stage3_snapshot_id is None
        assert repo_row.status == "pending"
        assert repo_row.error_message is None

        rerun = session.get(Stage2Run, repo_row.stage2_run_id)
        assert rerun is not None
        assert rerun.trigger_kind == "rerun_commit"
        assert rerun.target_commit_sha == historical_run.target_commit_sha
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_dispatches_stage2_only_after_commit(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'batch-runner.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage3_workspace_dir=tmp_path / "stage3",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    stage2_runner = VisibilityCheckingStageRunner(session_factory)
    runner = BatchTaskRunner(
        session_factory,
        settings,
        stage2_runner=stage2_runner,
        stage3_runner=DummyStageRunner(),
        stage4_runner=DummyStageRunner(),
    )

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        session.add(
            BatchTaskRepository(
                task_id=task.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                status="pending",
            )
        )
        historical_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.commit()

        runner._pipeline_tick(session, task.id)
        assert stage2_runner.scheduled
        assert stage2_runner.visible_run_ids == stage2_runner.scheduled
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_dispatch_respects_task_runtime_concurrency_limits(tmp_path) -> None:
    _, session_factory, runner, stage2_runner = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "stage2": {"concurrency": {"max_concurrent_runs": 1}},
                "stage3": {"concurrency": {"max_concurrent_runs": 2}},
                "stage4": {"concurrency": {"max_concurrent_runs": 1}},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.commit()

        pending_schedules = [
            {"kind": "stage2", "run_id": "stage2-a"},
            {"kind": "stage2", "run_id": "stage2-b"},
            {"kind": "stage3", "run_id": "stage3-a"},
            {"kind": "stage3", "run_id": "stage3-b"},
            {"kind": "stage3", "run_id": "stage3-c"},
            {"kind": "stage4", "run_id": "stage4-a"},
            {"kind": "stage4", "run_id": "stage4-b"},
        ]
        runner._dispatch_pending_schedules(pending_schedules, task=task)

        assert stage2_runner.scheduled == ["stage2-a"]
        assert runner.stage3_runner.scheduled == ["stage3-a", "stage3-b"]
        assert runner.stage4_runner.scheduled == ["stage4-a"]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_keeps_repo_pending_when_waiting_for_initial_stage2_capacity(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        occupied_repository = _make_repository()
        blocked_repository = GitHubRepository(
            github_repo_id=654321,
            full_name="owner/blocked",
            owner_login="owner",
            name="blocked",
            html_url="https://github.com/owner/blocked",
            api_url="https://api.github.com/repos/owner/blocked",
            default_branch="main",
            primary_language="Python",
            stargazers_count=24,
            discovered_at=datetime.now(UTC),
        )
        session.add_all([pool, occupied_repository, blocked_repository])
        session.flush()
        task = BatchTask(
            name="stage2-capacity",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "stage2": {"concurrency": {"max_concurrent_runs": 1}},
                "stage3": {},
                "stage4": {},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        active_run = Stage2Run(
            repository_id=occupied_repository.id,
            status=Stage2RunStatus.running.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
        )
        session.add(active_run)
        session.flush()
        session.add_all(
            [
                BatchTaskRepository(
                    task_id=task.id,
                    repository_id=occupied_repository.id,
                    github_repo_id=occupied_repository.github_repo_id,
                    repo_full_name=occupied_repository.full_name,
                    language=occupied_repository.primary_language,
                    stars=occupied_repository.stargazers_count,
                    status="running",
                    stage2_status="running",
                    stage2_run_id=active_run.id,
                ),
                BatchTaskRepository(
                    task_id=task.id,
                    repository_id=blocked_repository.id,
                    github_repo_id=blocked_repository.github_repo_id,
                    repo_full_name=blocked_repository.full_name,
                    language=blocked_repository.primary_language,
                    stars=blocked_repository.stargazers_count,
                    status="pending",
                    stage2_status="pending",
                ),
            ]
        )
        session.commit()

        task = BatchTaskService(session).get_task(task.id, include_detail=True)
        blocked_repo_row = next(repo for repo in task.repositories if repo.repository_id == blocked_repository.id)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []

        runner._process_repository(session, task, blocked_repo_row, pending_schedules, pending_materializations)
        session.flush()

        assert blocked_repo_row.status == "pending"
        assert blocked_repo_row.stage2_status == "pending"
        assert blocked_repo_row.stage2_run_id is None
        assert pending_schedules == []
        assert list(
            session.scalars(
                select(Stage2Run).where(Stage2Run.repository_id == blocked_repository.id)
            )
        ) == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_does_not_create_stage3_run_above_task_concurrency_limit(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={
                "stage2": {},
                "stage3": {"concurrency": {"max_concurrent_runs": 1}},
                "stage4": {},
            },
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
        )
        session.add(repo_row)
        session.flush()

        stage2_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage2_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=stage2_run.id,
            source_commit_sha="abc123",
        )
        session.add(snapshot)
        session.flush()
        blocked_entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_blocked.py",
        )
        occupied_entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_occupied.py",
        )
        session.add_all([blocked_entry_file, occupied_entry_file])
        session.flush()

        occupied_run = Stage3Run(
            entry_file_id=occupied_entry_file.id,
            status=Stage3RunStatus.queued.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
        )
        session.add(occupied_run)
        session.flush()
        occupied_entry = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=occupied_entry_file.id,
            entry_file_path=occupied_entry_file.test_file_path,
            stage3_run_id=occupied_run.id,
            status="queued",
        )
        blocked_entry = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=blocked_entry_file.id,
            entry_file_path=blocked_entry_file.test_file_path,
            stage3_run_id=None,
            status="pending",
        )
        session.add_all([occupied_entry, blocked_entry])
        session.commit()

        session.refresh(task)
        session.refresh(repo_row)
        session.refresh(blocked_entry)
        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=blocked_entry,
            entry_file=blocked_entry_file,
            stage3_runtime=dict(task.runtime_snapshot_json["stage3"]),
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        assert blocked_entry.stage3_run_id is None
        assert blocked_entry.status == "pending"
        assert pending_schedules == []
        assert list(
            session.scalars(
                select(Stage3Run).where(Stage3Run.entry_file_id == blocked_entry_file.id)
            )
        ) == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_cancel_forces_interrupt_of_orphan_queued_stage2_run(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'batch-runner.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage3_workspace_dir=tmp_path / "stage3",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    stage2_runner = NonInterruptingStageRunner()
    runner = BatchTaskRunner(
        session_factory,
        settings,
        stage2_runner=stage2_runner,
        stage3_runner=DummyStageRunner(),
        stage4_runner=DummyStageRunner(),
    )

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()
        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()
        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.queued.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind="batch",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
        )
        session.add(run)
        session.flush()
        session.add(
            BatchTaskRepository(
                task_id=task.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                status="running",
                stage2_status="queued",
                stage2_run_id=run.id,
            )
        )
        session.commit()

        runner._interrupt_known_runs(task.id)
        session.expire_all()

        interrupted_run = session.get(Stage2Run, run.id)
        assert interrupted_run is not None
        assert interrupted_run.status == Stage2RunStatus.completed.value
        assert interrupted_run.result == Stage2RunResult.failed.value
        assert "batch task cancelled" in str(interrupted_run.error_message or "")
        repo_row = session.scalar(select(BatchTaskRepository).where(BatchTaskRepository.task_id == task.id))
        assert repo_row is not None
        assert repo_row.status == "cancelled"
        assert repo_row.stage2_status == "cancelled"
        assert repo_row.error_message == "batch task cancelled by user"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_does_not_recreate_stage3_run_for_same_entry_after_completed_failure(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
        )
        session.add(repo_row)
        session.flush()

        stage2_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage2_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=stage2_run.id,
            source_commit_sha=stage2_run.target_commit_sha,
        )
        session.add(snapshot)
        session.flush()

        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_feature.py",
        )
        session.add(entry_file)
        session.flush()

        historical_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.failed.value,
            trigger_kind="batch",
            error_message="breaker failed",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()

        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=historical_run.id,
            status="running",
        )
        session.add(entry_row)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        stage3_runs = list(session.scalars(select(Stage3Run).where(Stage3Run.entry_file_id == entry_file.id)))
        assert len(stage3_runs) == 1
        assert pending_schedules == []
        assert entry_row.status == "failed"
        assert entry_row.error_message == "breaker failed"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_retry_task_can_create_new_stage3_run_after_prior_completed_failure(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
        )
        session.add(repo_row)
        session.flush()

        stage2_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage2_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=stage2_run.id,
            source_commit_sha=stage2_run.target_commit_sha,
        )
        session.add(snapshot)
        session.flush()

        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_feature.py",
        )
        session.add(entry_file)
        session.flush()

        historical_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.failed.value,
            trigger_kind="batch",
            error_message="breaker failed",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()

        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=None,
            status="pending",
        )
        session.add(entry_row)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        stage3_runs = list(session.scalars(select(Stage3Run).where(Stage3Run.entry_file_id == entry_file.id)))
        assert len(stage3_runs) == 2
        assert len(pending_schedules) == 1
        assert pending_schedules[0]["kind"] == "stage3"
        assert entry_row.stage3_run_id == pending_schedules[0]["run_id"]
        assert entry_row.status == "queued"
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_uses_existing_entry_asset_but_fills_missing_savepoint_depth(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
        )
        session.add(repo_row)
        session.flush()

        stage2_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage2_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=stage2_run.id,
            source_commit_sha=stage2_run.target_commit_sha,
        )
        session.add(snapshot)
        session.flush()

        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_feature.py",
        )
        session.add(entry_file)
        session.flush()

        historical_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()
        savepoint = Stage3Savepoint(
            run_id=historical_run.id,
            depth=2,
            gold_patch_text="diff --git a/file.py b/file.py\n",
        )
        session.add(savepoint)

        session.add(
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                source_commit_sha=stage2_run.target_commit_sha,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                entry_file_path=entry_file.test_file_path,
                depth=1,
                folder_name="owner__repo__abc123__tests_test_feature_py__depth-1",
                folder_path=str(tmp_path / "pool" / "asset"),
                manifest_json={},
            )
        )

        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=None,
            status="pending",
        )
        session.add(entry_row)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={
                "defaults": {
                    "issue_variant_count": 1,
                    "hint_variant_count": 2,
                },
                "hyperparameters": {
                    "build_timeout_seconds": 1234,
                    "issue_variant_count": 1,
                    "hint_variant_count": 2,
                },
            },
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        units = list(session.scalars(select(BatchTaskUnit).where(BatchTaskUnit.task_entry_id == entry_row.id)))
        stage4_runs = list(session.scalars(select(Stage4Run)))

        runner._refresh_repo_status(repo_row)

        assert entry_row.status == "completed"
        assert repo_row.status == "pending"
        assert repo_row.stage3_status == "completed"
        assert repo_row.stage4_status == "pending"
        assert entry_row.stage3_run_id is None
        assert len(pending_schedules) == 1
        assert pending_schedules[0]["kind"] == "stage4"
        assert pending_materializations == []
        assert len(units) == 1
        assert units[0].depth == 2
        assert units[0].stage3_savepoint_id == savepoint.id
        assert units[0].stage4_run_id == pending_schedules[0]["run_id"]
        assert units[0].status == "queued"
        assert len(stage4_runs) == 1
        assert stage4_runs[0].source_savepoint_id == savepoint.id
        assert stage4_runs[0].issue_variant_count == 3
        assert stage4_runs[0].hint_variant_count == 0
        runtime = dict((dict(stage4_runs[0].runtime_snapshot_json or {}).get("stage4") or {}).get("runtime") or {})
        hyperparameters = dict(runtime.get("hyperparameters") or {})
        assert hyperparameters["build_timeout_seconds"] == 1234
        assert "issue_variant_count" not in hyperparameters
        assert "hint_variant_count" not in hyperparameters
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_reuses_active_stage4_run_for_missing_depth(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        repository = _make_repository()
        session.add_all([pool, repository])
        session.flush()

        task = BatchTask(
            name="task",
            data_pool_id=pool.id,
            runtime_snapshot_json={"stage2": {}, "stage3": {}, "stage4": {}},
            status="running",
            phase="pipeline",
        )
        session.add(task)
        session.flush()

        repo_row = BatchTaskRepository(
            task_id=task.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            language=repository.primary_language,
            stars=repository.stargazers_count,
            status="running",
        )
        session.add(repo_row)
        session.flush()

        stage2_run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.completed.value,
            result=Stage2RunResult.passed.value,
            trigger_kind="manual",
            target_branch="main",
            target_commit_sha="abc123",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage2_run)
        session.flush()

        snapshot = Stage3CommitSnapshot(
            repository_id=repository.id,
            source_stage2_run_id=stage2_run.id,
            source_commit_sha=stage2_run.target_commit_sha,
        )
        session.add(snapshot)
        session.flush()

        entry_file = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_feature.py",
        )
        session.add(entry_file)
        session.flush()

        historical_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(historical_run)
        session.flush()

        savepoint = Stage3Savepoint(
            run_id=historical_run.id,
            depth=1,
            gold_patch_text="diff --git a/file.py b/file.py\n",
        )
        session.add(savepoint)
        session.flush()

        active_stage4 = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.running.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="manual",
            created_at=datetime.now(UTC),
        )
        session.add(active_stage4)
        session.flush()

        entry_row = BatchTaskEntry(
            task_id=task.id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            stage3_run_id=None,
            status="pending",
        )
        session.add(entry_row)
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        units = list(session.scalars(select(BatchTaskUnit).where(BatchTaskUnit.task_entry_id == entry_row.id)))
        stage4_runs = list(session.scalars(select(Stage4Run).where(Stage4Run.source_savepoint_id == savepoint.id)))

        runner._refresh_repo_status(repo_row)

        assert entry_row.status == "completed"
        assert repo_row.status == "running"
        assert repo_row.stage3_status == "completed"
        assert repo_row.stage4_status == "running"
        assert pending_schedules == []
        assert pending_materializations == []
        assert len(units) == 1
        assert units[0].stage4_run_id == active_stage4.id
        assert units[0].status == Stage4RunStatus.running.value
        assert len(stage4_runs) == 1
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_waits_for_incompatible_active_stage4_then_creates_matching_run(
    tmp_path,
) -> None:
    settings, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        (
            pool,
            repository,
            task,
            repo_row,
            _stage2_run,
            _snapshot,
            entry_file,
            entry_row,
        ) = _seed_batch_entry(session, tmp_path)
        catalog = load_issue_style_catalog(enabled_styles=["swe", "fb"])
        issue_style_config = {
            "schema_version": catalog.schema_version,
            "enabled_styles": ["swe", "fb"],
            "catalog_sha256": catalog.enabled_contract_sha256(),
        }
        task.runtime_snapshot_json = {
            **dict(task.runtime_snapshot_json or {}),
            "stage4": {"issue_style_config": issue_style_config},
        }
        settings.stage4_issue_styles = "hint"

        stage3_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(stage3_run)
        session.flush()
        savepoint = Stage3Savepoint(
            run_id=stage3_run.id,
            depth=1,
            gold_patch_text="diff --git a/file.py b/file.py\n",
        )
        session.add(savepoint)
        session.flush()
        incompatible_run = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.running.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="manual",
            runtime_snapshot_json={
                "stage4": {
                    "generation_config": {
                        "schema_version": catalog.schema_version,
                        "enabled_styles": ["hint"],
                        "catalog_sha256": "incompatible-catalog",
                    }
                }
            },
            created_at=datetime.now(UTC),
        )
        unit = BatchTaskUnit(
            task_id=task.id,
            task_repository_id=repo_row.id,
            task_entry_id=entry_row.id,
            depth=1,
            status="pending",
            stage3_savepoint_id=savepoint.id,
        )
        session.add(incompatible_run)
        session.flush()
        incompatible_asset = DataPoolAsset(
            pool_id=pool.id,
            repository_id=repository.id,
            github_repo_id=repository.github_repo_id,
            repo_full_name=repository.full_name,
            source_commit_sha="abc123",
            language=repository.primary_language,
            stars=repository.stargazers_count,
            entry_file_path=entry_file.test_file_path,
            depth=1,
            stage4_run_id=incompatible_run.id,
            folder_name="incompatible-stage4-asset",
            folder_path=str(tmp_path / "pool" / "incompatible-stage4-asset"),
            manifest_json={"issue_schema_version": 2},
        )
        session.add_all([incompatible_asset, unit])
        session.flush()
        session.refresh(savepoint, attribute_names=["stage4_runs"])

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_stage4_unit(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            unit=unit,
            savepoint=savepoint,
            stage4_runtime={"issue_style_config": issue_style_config},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )

        assert unit.status == "pending"
        assert unit.stage4_run_id is None
        assert pending_schedules == []
        assert pending_materializations == []

        incompatible_run.status = Stage4RunStatus.completed.value
        incompatible_run.result = Stage4RunResult.generated.value
        incompatible_run.finished_at = datetime.now(UTC)
        session.flush()
        runner._process_stage4_unit(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            unit=unit,
            savepoint=savepoint,
            stage4_runtime={"issue_style_config": issue_style_config},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        matching_run = session.get(Stage4Run, unit.stage4_run_id)
        assert matching_run is not None
        assert matching_run.id != incompatible_run.id
        matching_generation = dict(
            (matching_run.runtime_snapshot_json or {}).get("stage4", {}).get(
                "generation_config"
            )
            or {}
        )
        assert matching_generation["enabled_styles"] == ["swe", "fb"]
        assert matching_generation["catalog_sha256"] == catalog.enabled_contract_sha256()
        assert pending_schedules == [{"kind": "stage4", "run_id": matching_run.id}]
        assert pending_materializations == []
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_scans_only_latest_stage3_run_for_entry(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        _pool, _repository, task, repo_row, _stage2_run, _snapshot, entry_file, entry_row = _seed_batch_entry(
            session,
            tmp_path,
        )

        older_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            finished_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        newer_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
            finished_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
        session.add_all([older_run, newer_run])
        session.flush()
        old_savepoint = Stage3Savepoint(
            run_id=older_run.id,
            depth=2,
            gold_patch_text="diff --git a/old.py b/old.py\n",
        )
        new_savepoint = Stage3Savepoint(
            run_id=newer_run.id,
            depth=1,
            gold_patch_text="diff --git a/new.py b/new.py\n",
        )
        session.add_all([old_savepoint, new_savepoint])
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        units = list(session.scalars(select(BatchTaskUnit).where(BatchTaskUnit.task_entry_id == entry_row.id)))
        stage4_runs = list(session.scalars(select(Stage4Run)))

        assert [unit.depth for unit in units] == [1]
        assert units[0].stage3_savepoint_id == new_savepoint.id
        assert len(stage4_runs) == 1
        assert stage4_runs[0].source_savepoint_id == new_savepoint.id
        assert pending_schedules == [{"kind": "stage4", "run_id": stage4_runs[0].id}]
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)


def test_batch_runner_keeps_active_stage3_entry_open_after_current_depth_materializes(tmp_path) -> None:
    _, session_factory, runner, _ = _make_runner(tmp_path)

    session = session_factory()
    try:
        pool, repository, task, repo_row, stage2_run, _snapshot, entry_file, entry_row = _seed_batch_entry(
            session,
            tmp_path,
        )

        active_run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.running.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind="batch",
            created_at=datetime.now(UTC),
        )
        session.add(active_run)
        session.flush()

        first_savepoint = Stage3Savepoint(
            run_id=active_run.id,
            depth=1,
            gold_patch_text="diff --git a/first.py b/first.py\n",
        )
        session.add(first_savepoint)
        session.flush()

        session.add(
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                source_commit_sha=stage2_run.target_commit_sha,
                language=repository.primary_language,
                stars=repository.stargazers_count,
                entry_file_path=entry_file.test_file_path,
                depth=1,
                folder_name="owner__repo__abc123__tests_test_feature_py__depth-1",
                folder_path=str(tmp_path / "pool" / "asset-depth-1"),
                manifest_json={},
            )
        )
        session.commit()

        pending_schedules: list[dict[str, str]] = []
        pending_materializations: list[dict[str, str]] = []
        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        assert entry_row.status == Stage3RunStatus.running.value
        assert pending_schedules == []
        assert pending_materializations == []

        second_savepoint = Stage3Savepoint(
            run_id=active_run.id,
            depth=2,
            gold_patch_text="diff --git a/second.py b/second.py\n",
        )
        session.add(second_savepoint)
        session.commit()

        runner._process_entry(
            session,
            task=task,
            repo_row=repo_row,
            entry_row=entry_row,
            entry_file=entry_file,
            stage3_runtime={},
            stage4_runtime={},
            pending_schedules=pending_schedules,
            pending_materializations=pending_materializations,
        )
        session.flush()

        units = list(session.scalars(select(BatchTaskUnit).where(BatchTaskUnit.task_entry_id == entry_row.id)))
        stage4_runs = list(session.scalars(select(Stage4Run)))

        assert entry_row.status == "running"
        assert [unit.depth for unit in units] == [1, 2]
        assert units[0].status == "completed"
        assert units[1].stage3_savepoint_id == second_savepoint.id
        assert units[1].status == "queued"
        assert len(stage4_runs) == 1
        assert stage4_runs[0].source_savepoint_id == second_savepoint.id
    finally:
        session.close()
        runner.shutdown(timeout_seconds=0.0)
