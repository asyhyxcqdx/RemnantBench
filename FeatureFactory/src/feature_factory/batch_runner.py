from __future__ import annotations

import time
from concurrent.futures import Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from threading import Lock
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session, selectinload, sessionmaker

from feature_factory.admin_runner import Stage1JobRunner
from feature_factory.batch import BatchTaskService
from feature_factory.config import Settings
from feature_factory.data_pool import DataPoolService
from feature_factory.models import (
    BatchTask,
    BatchTaskEntry,
    BatchTaskRepository,
    BatchTaskStatus,
    BatchTaskUnit,
    CrawlJob,
    CrawlJobStatus,
    DataPoolAsset,
    GitHubRepository,
    RepoDiscovery,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.stage1.github_client import GitHubSearchClient
from feature_factory.stage1.schemas import CrawlFilters
from feature_factory.stage1.service import CrawlService
from feature_factory.stage1.token_scheduler import GitHubTokenScheduler
from feature_factory.stage2.runner import Stage2RunRunner
from feature_factory.stage2.service import (
    Stage2CommitResolutionError,
    Stage2CommitUnchangedError,
    Stage2RunInterruptConflictError,
    Stage2Service,
    resolve_repo_head_commit,
)
from feature_factory.stage3.runner import Stage3RunRunner
from feature_factory.stage3.service import Stage3RunConflictError, Stage3Service
from feature_factory.stage4.runner import Stage4RunRunner
from feature_factory.stage4.service import Stage4RunConflictError, Stage4Service

BATCH_POLL_INTERVAL_SECONDS = 2.0
_BATCH_SCHEDULABLE_KINDS = {"stage2", "stage3", "stage4"}
_BATCH_ACTIVE_RUN_STATUSES = {"pending", "queued", "running"}
_BATCH_RUNNING_RUN_STATUSES = {"running"}
_BATCH_REPOSITORY_TERMINAL_STATUSES = {
    "completed",
    "failed",
    "partial",
    "cancelled",
    Stage2RunResult.abandoned.value,
    Stage2RunResult.defect.value,
}
_BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS = {
    Stage2RunResult.abandoned.value,
    Stage2RunResult.defect.value,
}
_BATCH_REUSABLE_STAGE2_TERMINAL_RESULTS = {
    Stage2RunResult.passed.value,
    Stage2RunResult.abandoned.value,
    Stage2RunResult.defect.value,
}


def _normalized_stage4_issue_style_config(
    stage4_runtime: dict[str, Any],
) -> dict[str, Any] | None:
    raw_config = stage4_runtime.get("issue_style_config")
    if raw_config is None:
        return None
    if not isinstance(raw_config, dict):
        raise ValueError("stage4 issue_style_config must be an object")
    raw_styles = raw_config.get("enabled_styles")
    if not isinstance(raw_styles, list) or not raw_styles:
        raise ValueError("stage4 issue_style_config enabled_styles must be a non-empty array")
    enabled_styles = [str(value or "").strip() for value in raw_styles]
    if any(not value for value in enabled_styles):
        raise ValueError("stage4 issue_style_config enabled_styles contains an empty style")
    if len(set(enabled_styles)) != len(enabled_styles):
        raise ValueError("stage4 issue_style_config enabled_styles contains duplicates")
    catalog_sha256 = str(raw_config.get("catalog_sha256") or "").strip()
    if not catalog_sha256:
        raise ValueError("stage4 issue_style_config is missing catalog_sha256")
    try:
        schema_version = int(raw_config.get("schema_version"))
    except (TypeError, ValueError) as exc:
        raise ValueError("stage4 issue_style_config has an invalid schema_version") from exc
    return {
        "schema_version": schema_version,
        "enabled_styles": enabled_styles,
        "catalog_sha256": catalog_sha256,
    }


def _stage4_run_matches_issue_style_config(
    run: Stage4Run,
    issue_style_config: dict[str, Any] | None,
) -> bool:
    if issue_style_config is None:
        return True
    runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
    generation_config = runtime_stage4.get("generation_config")
    if not isinstance(generation_config, dict):
        return False
    return (
        generation_config.get("schema_version") == issue_style_config["schema_version"]
        and generation_config.get("enabled_styles") == issue_style_config["enabled_styles"]
        and str(generation_config.get("catalog_sha256") or "").strip()
        == issue_style_config["catalog_sha256"]
    )


def _data_pool_asset_matches_issue_style_config(
    session: Session,
    asset: DataPoolAsset,
    issue_style_config: dict[str, Any] | None,
) -> bool:
    if issue_style_config is None:
        return True
    run_id = str(asset.stage4_run_id or "").strip()
    if not run_id:
        return False
    run = session.get(Stage4Run, run_id)
    return bool(
        run is not None
        and _stage4_run_matches_issue_style_config(run, issue_style_config)
    )


class BatchTaskRunner:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        stage2_runner: Stage2RunRunner,
        stage3_runner: Stage3RunRunner,
        stage4_runner: Stage4RunRunner,
        max_workers: int = 1,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.stage2_runner = stage2_runner
        self.stage3_runner = stage3_runner
        self.stage4_runner = stage4_runner
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ff-batch")
        self._lock = Lock()
        self._futures: dict[str, Future[None]] = {}

    def schedule_task(self, task_id: str) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            if future is not None and not future.done():
                return False
            future = self.executor.submit(self._run_future, task_id)
            self._futures[task_id] = future
            future.add_done_callback(lambda completed, target=task_id: self._cleanup_future(target, completed))
            return True

    def is_running(self, task_id: str) -> bool:
        with self._lock:
            future = self._futures.get(task_id)
            return future is not None and not future.done()

    def shutdown(self, *, timeout_seconds: float | None = None) -> list[str]:
        with self._lock:
            active = {task_id: future for task_id, future in self._futures.items() if not future.done()}
        if timeout_seconds is None:
            self.executor.shutdown(wait=True, cancel_futures=True)
            return []
        self.executor.shutdown(wait=False, cancel_futures=True)
        if not active:
            return []
        _, not_done = wait(tuple(active.values()), timeout=max(timeout_seconds, 0.0))
        return [task_id for task_id, future in active.items() if future in not_done and not future.done()]

    def _cleanup_future(self, task_id: str, future: Future[None]) -> None:
        with self._lock:
            current = self._futures.get(task_id)
            if current is future and future.done():
                self._futures.pop(task_id, None)

    def _run_future(self, task_id: str) -> None:
        try:
            self._run_task(task_id)
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            self._with_session(
                lambda session: BatchTaskService(session).mark_terminal(
                    task_id,
                    status=BatchTaskStatus.failed.value,
                    phase="failed",
                    error_message=error_message,
                )
            )

    def _run_task(self, task_id: str) -> None:
        self._mark_task_running(task_id, "stage1")
        if self._is_cancel_requested(task_id):
            self._mark_cancelled(task_id)
            return
        self._run_stage1(task_id)
        if self._is_cancel_requested(task_id):
            self._mark_cancelled(task_id)
            return
        self._mark_task_running(task_id, "pipeline")
        self._sync_repositories_from_stage1(task_id)

        while True:
            if self._is_cancel_requested(task_id):
                self._interrupt_known_runs(task_id)
                self._mark_cancelled(task_id)
                return
            terminal = self._with_session(lambda session: self._pipeline_tick(session, task_id))
            if terminal:
                return
            time.sleep(BATCH_POLL_INTERVAL_SECONDS)

    def _run_stage1(self, task_id: str) -> None:
        task = self._with_session(lambda session: BatchTaskService(session).get_task(task_id, include_detail=False))
        if self._can_reuse_stage1_result(task_id, task):
            self._mark_task_phase(task_id, "pipeline")
            return
        filters = CrawlFilters.model_validate(task.filters_json)
        if filters.target_repositories:
            self._run_target_repositories(task_id, filters)
            return
        runtime = dict(task.runtime_snapshot_json or {})
        stage1_runtime = dict(runtime.get("stage1") or {})
        system_capacity = max(1, int(self.settings.stage1_max_concurrent_jobs_cap))
        max_concurrent_jobs = min(int(stage1_runtime.get("max_concurrent_jobs") or 1), system_capacity)
        max_concurrent_partitions = stage1_runtime.get("max_concurrent_partitions")
        tokens = self._github_tokens_for_task(task)
        token_scheduler = GitHubTokenScheduler(self.settings, runtime_tokens=tokens)

        if not task.stage1_crawl_job_id:
            session = self.session_factory()
            client = GitHubSearchClient(self.settings, token_scheduler=token_scheduler)
            try:
                service = CrawlService(session, self.settings, client)
                job = service.create_job(
                    task.name,
                    filters,
                    max_concurrent_partitions=max_concurrent_partitions,
                )
                task_row = session.get(BatchTask, task_id)
                if task_row is not None:
                    task_row.stage1_crawl_job_id = job.id
                    task_row.phase = "stage1"
                service.mark_job_queued(job.id)
                session.commit()
                stage1_job_id = job.id
            finally:
                client.close()
                session.close()
        else:
            stage1_job_id = task.stage1_crawl_job_id

        stage1_runner = Stage1JobRunner(
            self.session_factory,
            self.settings,
            max_workers=max_concurrent_jobs,
            max_limit=system_capacity,
            token_scheduler=token_scheduler,
        )
        try:
            stage1_runner.run_job(
                stage1_job_id,
                max_concurrent_partitions=max_concurrent_partitions,
            )
        finally:
            stage1_runner.shutdown(timeout_seconds=0.0)

    def _can_reuse_stage1_result(self, task_id: str, task: BatchTask) -> bool:
        if self._with_session(
            lambda session: session.scalar(
                select(BatchTaskRepository.id)
                .where(BatchTaskRepository.task_id == task_id)
                .limit(1)
            )
        ):
            return True
        stage1_crawl_job_id = str(task.stage1_crawl_job_id or "").strip()
        if not stage1_crawl_job_id:
            return False
        status = self._with_session(
            lambda session: session.scalar(
                select(CrawlJob.status).where(CrawlJob.id == stage1_crawl_job_id)
            )
        )
        return str(status or "") in {
            CrawlJobStatus.completed.value,
            CrawlJobStatus.partial.value,
        }

    def _run_target_repositories(self, task_id: str, filters: CrawlFilters) -> None:
        target_repositories = list(filters.target_repositories)
        if filters.repository_limit is not None:
            target_repositories = target_repositories[:filters.repository_limit]
        task = self._with_session(lambda session: BatchTaskService(session).get_task(task_id, include_detail=False))
        tokens = self._github_tokens_for_task(task)
        token_scheduler = GitHubTokenScheduler(self.settings, runtime_tokens=tokens)
        session = self.session_factory()
        client = GitHubSearchClient(self.settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, self.settings, client)
            items: list[dict[str, Any]] = []
            errors: list[str] = []
            for full_name in target_repositories:
                try:
                    items.append(client.get_repository(full_name))
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{full_name}: {exc}")
            repositories = service.upsert_repository_items(items)
            existing_repo_ids = set(
                session.scalars(
                    select(BatchTaskRepository.repository_id).where(BatchTaskRepository.task_id == task_id)
                )
            )
            for repository in repositories:
                if repository.id in existing_repo_ids:
                    continue
                session.add(
                    BatchTaskRepository(
                        task_id=task_id,
                        repository_id=repository.id,
                        github_repo_id=repository.github_repo_id,
                        repo_full_name=repository.full_name,
                        language=repository.primary_language,
                        stars=repository.stargazers_count,
                        status="pending",
                    )
                )
            task_row = session.get(BatchTask, task_id)
            if task_row is not None:
                task_row.phase = "pipeline"
                if errors:
                    task_row.error_message = "部分指定仓库抓取失败: " + "; ".join(errors)
            BatchTaskService(session).refresh_task_stats(task_id)
            session.commit()
            if errors and not repositories:
                raise RuntimeError("指定仓库抓取失败: " + "; ".join(errors))
        except Exception:
            session.rollback()
            raise
        finally:
            client.close()
            session.close()

    def _sync_repositories_from_stage1(self, task_id: str) -> None:
        session = self.session_factory()
        try:
            task = BatchTaskService(session).get_task(task_id, include_detail=False)
            if not task.stage1_crawl_job_id:
                return
            rows = list(
                session.execute(
                    select(GitHubRepository)
                    .join(RepoDiscovery, RepoDiscovery.repository_id == GitHubRepository.id)
                    .where(RepoDiscovery.job_id == task.stage1_crawl_job_id)
                    .order_by(GitHubRepository.full_name.asc())
                )
                .scalars()
            )
            existing_repo_ids = set(
                session.scalars(
                    select(BatchTaskRepository.repository_id).where(BatchTaskRepository.task_id == task_id)
                )
            )
            for repository in rows:
                if repository.id in existing_repo_ids:
                    continue
                session.add(
                    BatchTaskRepository(
                        task_id=task_id,
                        repository_id=repository.id,
                        github_repo_id=repository.github_repo_id,
                        repo_full_name=repository.full_name,
                        language=repository.primary_language,
                        stars=repository.stargazers_count,
                        status="pending",
                    )
                )
            task.phase = "pipeline"
            BatchTaskService(session).refresh_task_stats(task_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _pipeline_tick(self, session: Session, task_id: str) -> bool:
        pending_schedules: list[dict[str, Any]] = []
        pending_materializations: list[dict[str, Any]] = []
        task = BatchTaskService(session).get_task(task_id, include_detail=True)
        for repo_row in list(task.repositories or []):
            self._process_repository(session, task, repo_row, pending_schedules, pending_materializations)
        BatchTaskService(session).refresh_task_stats(task_id)
        session.commit()
        self._dispatch_pending_schedules(pending_schedules, task=task)
        self._materialize_pending_stage4_runs(pending_materializations)
        return self._finalize_if_terminal(task_id)

    def _process_repository(
        self,
        session: Session,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        pending_schedules: list[dict[str, Any]],
        pending_materializations: list[dict[str, Any]],
    ) -> None:
        stop_after_stage2 = self._stops_after_stage2(task)
        if repo_row.status in _BATCH_REPOSITORY_TERMINAL_STATUSES and not self._repo_has_active_units(repo_row):
            return
        repository = session.get(GitHubRepository, repo_row.repository_id)
        if repository is None:
            repo_row.status = "failed"
            repo_row.error_message = "repository record is missing"
            if stop_after_stage2:
                self._mark_downstream_stages_skipped(repo_row)
            return
        repo_row.started_at = repo_row.started_at or datetime.now(UTC)
        repo_row.status = "running"
        runtime = dict(task.runtime_snapshot_json or {})
        stage2_runtime = dict(runtime.get("stage2") or {})
        stage3_runtime = dict(runtime.get("stage3") or {})
        stage4_runtime = dict(runtime.get("stage4") or {})

        source_stage2 = self._ensure_stage2(
            session,
            task,
            repo_row,
            repository,
            stage2_runtime,
            pending_schedules,
        )
        if source_stage2 is None:
            if stop_after_stage2 and repo_row.status in _BATCH_REPOSITORY_TERMINAL_STATUSES:
                self._mark_downstream_stages_skipped(repo_row)
            return
        if stop_after_stage2:
            self._complete_repository_after_stage2(repo_row)
            return
        snapshot = self._ensure_stage3_snapshot(
            session,
            task,
            repo_row,
            repository,
            source_stage2,
            stage2_runtime,
            pending_schedules,
        )
        if snapshot is None:
            return
        entry_files = [
            entry_file
            for entry_file in list(snapshot.entry_files or [])
            if self._entry_file_meets_stage2_test_count_min(entry_file, stage2_runtime)
        ]
        if not entry_files:
            repo_row.stage3_snapshot_id = snapshot.id
            repo_row.stage3_status = "completed"
            repo_row.stage4_status = "completed"
            repo_row.status = "completed"
            repo_row.error_message = None
            repo_row.finished_at = repo_row.finished_at or datetime.now(UTC)
            repo_row.stats_json = {
                "entry_file_count": 0,
                "asset_count": 0,
                "failed_unit_count": 0,
            }
            return
        for entry_file in entry_files:
            entry = self._ensure_entry_row(session, repo_row, entry_file)
            self._process_entry(
                session,
                task=task,
                repo_row=repo_row,
                entry_row=entry,
                entry_file=entry_file,
                stage3_runtime=stage3_runtime,
                stage4_runtime=stage4_runtime,
                pending_schedules=pending_schedules,
                pending_materializations=pending_materializations,
            )
        self._refresh_repo_status(repo_row)

    @staticmethod
    def _stops_after_stage2(task: BatchTask) -> bool:
        runtime = dict(task.runtime_snapshot_json or {})
        pipeline = dict(runtime.get("pipeline") or {})
        return str(pipeline.get("stop_after_stage") or "stage4") == "stage2"

    @staticmethod
    def _mark_downstream_stages_skipped(repo_row: BatchTaskRepository) -> None:
        repo_row.stage3_status = "skipped"
        repo_row.stage4_status = "skipped"

    def _complete_repository_after_stage2(self, repo_row: BatchTaskRepository) -> None:
        self._mark_downstream_stages_skipped(repo_row)
        repo_row.status = "completed"
        repo_row.error_message = None
        repo_row.finished_at = repo_row.finished_at or datetime.now(UTC)
        repo_row.stats_json = {
            "entry_file_count": 0,
            "asset_count": 0,
            "failed_unit_count": 0,
        }

    def _entry_file_meets_stage2_test_count_min(
        self,
        entry_file: Stage3EntryFile,
        stage2_runtime: dict[str, Any],
    ) -> bool:
        hyperparameters = dict(stage2_runtime.get("hyperparameters") or {})
        value = hyperparameters.get(
            "entry_file_test_count_min",
            self.settings.stage2_entry_file_test_count_min,
        )
        try:
            minimum = int(value)
        except (TypeError, ValueError):
            minimum = int(self.settings.stage2_entry_file_test_count_min)
        if minimum < 0:
            return True
        return int(entry_file.baseline_total_tests or 0) >= minimum

    def _ensure_stage2(
        self,
        session: Session,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        repository: GitHubRepository,
        runtime_snapshot: dict[str, Any],
        pending_schedules: list[dict[str, Any]],
    ) -> Stage2Run | None:
        service = Stage2Service(session)

        if repo_row.stage2_run_id:
            run = session.get(Stage2Run, repo_row.stage2_run_id)
            if run is None:
                repo_row.stage2_status = "failed"
                repo_row.error_message = "stage2 run disappeared"
                return None
            if run.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value}:
                repo_row.stage2_status = run.status
                if run.status == Stage2RunStatus.queued.value:
                    repo_row.status = "pending"
                    self._queue_schedule_request(
                        pending_schedules,
                        kind="stage2",
                        run_id=run.id,
                    )
                return None
            if run.result in _BATCH_REUSABLE_STAGE2_TERMINAL_RESULTS:
                return self._apply_reusable_stage2_terminal_result(
                    repo_row,
                    run,
                    reuse_as_source=False,
                )
            repo_row.stage2_status = "failed"
            repo_row.status = "failed"
            repo_row.error_message = run.error_message or f"stage2 result: {run.result}"
            return None

        if repo_row.source_stage2_run_id:
            run = session.get(Stage2Run, repo_row.source_stage2_run_id)
            if (
                run is not None
                and run.status == Stage2RunStatus.completed.value
                and run.result in _BATCH_REUSABLE_STAGE2_TERMINAL_RESULTS
            ):
                return self._apply_reusable_stage2_terminal_result(
                    repo_row,
                    run,
                    reuse_as_source=True,
                )
        reusable_run = None
        if not self._stops_after_stage2(task):
            reusable_run = session.scalar(
                select(Stage2Run)
                .where(
                    Stage2Run.repository_id == repository.id,
                    Stage2Run.status == Stage2RunStatus.completed.value,
                    Stage2Run.result.in_(_BATCH_REUSABLE_STAGE2_TERMINAL_RESULTS),
                )
                .order_by(
                    Stage2Run.finished_at.desc().nullslast(),
                    Stage2Run.created_at.desc(),
                    Stage2Run.id.desc(),
                )
                .limit(1)
            )
        if reusable_run is not None:
            return self._apply_reusable_stage2_terminal_result(
                repo_row,
                reusable_run,
                reuse_as_source=True,
            )

        if not self._task_has_schedule_capacity(task, "stage2", pending_schedules):
            repo_row.stage2_status = "pending"
            if not repo_row.stage2_run_id and not repo_row.source_stage2_run_id:
                repo_row.status = "pending"
            return None

        try:
            trigger_kind = "batch_retry" if service.latest_run_for_repository(repository.id) is not None else "batch"
            run = service.create_run(
                repository.id,
                trigger_kind=trigger_kind,
                commit_resolver=resolve_repo_head_commit,
                runtime_snapshot=runtime_snapshot,
                allow_existing_successful_commit=self._stops_after_stage2(task),
            )
        except (Stage2CommitResolutionError, Stage2CommitUnchangedError, ValueError) as exc:
            repo_row.stage2_status = "failed"
            repo_row.status = "failed"
            repo_row.error_message = str(exc)
            return None
        repo_row.stage2_run_id = run.id
        repo_row.source_stage2_run_id = None
        repo_row.stage2_status = "queued"
        repo_row.status = "pending"
        repo_row.error_message = None
        session.flush()
        self._queue_schedule_request(
            pending_schedules,
            kind="stage2",
            run_id=run.id,
        )
        return None

    def _ensure_stage3_snapshot(
        self,
        session: Session,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        repository: GitHubRepository,
        source_stage2: Stage2Run,
        stage2_runtime: dict[str, Any],
        pending_schedules: list[dict[str, Any]],
    ) -> Stage3CommitSnapshot | None:
        service = Stage3Service(session, workspace_root=self.settings.stage3_workspace_dir, settings=self.settings)
        service.ensure_materialized(repository_ids=[repo_row.repository_id])
        snapshot = session.scalar(
            select(Stage3CommitSnapshot)
            .options(selectinload(Stage3CommitSnapshot.entry_files).selectinload(Stage3EntryFile.runs).selectinload(Stage3Run.savepoints))
            .where(Stage3CommitSnapshot.source_stage2_run_id == source_stage2.id)
            .limit(1)
        )
        if snapshot is None:
            if repo_row.stage2_run_id != source_stage2.id:
                self._schedule_stage2_rerun_from_existing_commit(
                    session,
                    task,
                    repo_row,
                    repository,
                    source_stage2,
                    stage2_runtime,
                    pending_schedules,
                )
                return None
            repo_row.stage3_status = "failed"
            repo_row.status = "failed"
            repo_row.error_message = "stage2 run did not produce stage3 entry files"
            return None
        if not list(snapshot.entry_files or []):
            summary = dict(snapshot.summary_json or {})
            filtered_out_count = int(summary.get("filtered_out_entry_file_count") or 0)
            if filtered_out_count > 0:
                repo_row.stage3_snapshot_id = snapshot.id
                repo_row.stage3_status = "completed"
                repo_row.stage4_status = "completed"
                repo_row.error_message = None
                return snapshot
            if repo_row.stage2_run_id != source_stage2.id:
                self._schedule_stage2_rerun_from_existing_commit(
                    session,
                    task,
                    repo_row,
                    repository,
                    source_stage2,
                    stage2_runtime,
                    pending_schedules,
                )
                return None
            repo_row.stage3_status = "failed"
            repo_row.status = "failed"
            repo_row.error_message = "stage2 run did not produce stage3 entry files"
            return None
        repo_row.stage3_snapshot_id = snapshot.id
        repo_row.stage3_status = "running"
        repo_row.error_message = None
        return snapshot

    def _schedule_stage2_rerun_from_existing_commit(
        self,
        session: Session,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        repository: GitHubRepository,
        source_stage2: Stage2Run,
        runtime_snapshot: dict[str, Any],
        pending_schedules: list[dict[str, Any]],
    ) -> None:
        service = Stage2Service(session)
        if not self._task_has_schedule_capacity(task, "stage2", pending_schedules):
            repo_row.stage2_status = "pending"
            repo_row.stage3_status = "pending"
            return
        try:
            run = service.create_run_from_existing_commit(
                repository.id,
                source_stage2.id,
                runtime_snapshot=runtime_snapshot,
            )
        except (Stage2CommitResolutionError, Stage2CommitUnchangedError, ValueError) as exc:
            repo_row.stage2_status = "failed"
            repo_row.stage3_status = "failed"
            repo_row.status = "failed"
            repo_row.error_message = str(exc)
            return
        repo_row.stage2_run_id = run.id
        repo_row.source_stage2_run_id = None
        repo_row.stage3_snapshot_id = None
        repo_row.stage2_status = "queued"
        repo_row.stage3_status = "pending"
        repo_row.status = "pending"
        repo_row.error_message = None
        session.flush()
        self._queue_schedule_request(
            pending_schedules,
            kind="stage2",
            run_id=run.id,
        )

    def _apply_reusable_stage2_terminal_result(
        self,
        repo_row: BatchTaskRepository,
        run: Stage2Run,
        *,
        reuse_as_source: bool,
    ) -> Stage2Run | None:
        result = str(run.result or "")
        if reuse_as_source:
            repo_row.source_stage2_run_id = run.id
            repo_row.stage2_run_id = None
        if result == Stage2RunResult.passed.value:
            repo_row.stage2_status = "completed"
            return run
        if result in _BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS:
            repo_row.stage2_status = result
            repo_row.stage3_status = "pending"
            repo_row.stage4_status = "pending"
            repo_row.status = result
            repo_row.error_message = None
            repo_row.finished_at = repo_row.finished_at or datetime.now(UTC)
            repo_row.stats_json = {
                "entry_file_count": 0,
                "asset_count": 0,
                "failed_unit_count": 0,
            }
        return None

    def _ensure_entry_row(
        self,
        session: Session,
        repo_row: BatchTaskRepository,
        entry_file: Stage3EntryFile,
    ) -> BatchTaskEntry:
        existing = session.scalar(
            select(BatchTaskEntry)
            .options(selectinload(BatchTaskEntry.units))
            .where(
                BatchTaskEntry.task_repository_id == repo_row.id,
                BatchTaskEntry.entry_file_path == entry_file.test_file_path,
            )
            .limit(1)
        )
        if existing is not None:
            existing.stage3_entry_file_id = entry_file.id
            return existing
        row = BatchTaskEntry(
            task_id=repo_row.task_id,
            task_repository_id=repo_row.id,
            stage3_entry_file_id=entry_file.id,
            entry_file_path=entry_file.test_file_path,
            status="pending",
        )
        session.add(row)
        session.flush()
        return row

    def _process_entry(
        self,
        session: Session,
        *,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        entry_row: BatchTaskEntry,
        entry_file: Stage3EntryFile,
        stage3_runtime: dict[str, Any],
        stage4_runtime: dict[str, Any],
        pending_schedules: list[dict[str, Any]],
        pending_materializations: list[dict[str, Any]],
    ) -> None:
        entry_row.started_at = entry_row.started_at or datetime.now(UTC)
        entry_row.status = "running"
        try:
            issue_style_config = _normalized_stage4_issue_style_config(stage4_runtime)
        except ValueError as exc:
            entry_row.status = "failed"
            entry_row.error_message = str(exc)
            entry_row.finished_at = entry_row.finished_at or datetime.now(UTC)
            return
        existing_assets = [
            asset
            for asset in list(
                session.scalars(
                    select(DataPoolAsset)
                    .where(
                        DataPoolAsset.pool_id == task.data_pool_id,
                        DataPoolAsset.github_repo_id == repo_row.github_repo_id,
                        DataPoolAsset.entry_file_path == entry_file.test_file_path,
                    )
                    .order_by(DataPoolAsset.depth.asc(), DataPoolAsset.id.asc())
                )
            )
            if _data_pool_asset_matches_issue_style_config(
                session,
                asset,
                issue_style_config,
            )
        ]
        runs = list(
            session.scalars(
                select(Stage3Run)
                .join(Stage3EntryFile, Stage3Run.entry_file_id == Stage3EntryFile.id)
                .join(Stage3CommitSnapshot, Stage3EntryFile.snapshot_id == Stage3CommitSnapshot.id)
                .where(
                    Stage3CommitSnapshot.repository_id == repo_row.repository_id,
                    Stage3EntryFile.test_file_path == entry_file.test_file_path,
                )
                .order_by(Stage3Run.created_at.desc(), Stage3Run.id.desc())
            )
        )
        latest_run = runs[0] if runs else None
        active_run = (
            latest_run
            if latest_run is not None
            and latest_run.status
            in {Stage3RunStatus.pending.value, Stage3RunStatus.queued.value, Stage3RunStatus.running.value}
            else None
        )
        processed_depths: set[int] = set()
        savepoints = (
            list(
                session.scalars(
                    select(Stage3Savepoint)
                    .options(selectinload(Stage3Savepoint.stage4_runs))
                    .where(Stage3Savepoint.run_id == latest_run.id)
                    .order_by(Stage3Savepoint.depth.asc())
                )
            )
            if latest_run is not None
            else []
        )
        for savepoint in savepoints:
            if not str(savepoint.gold_patch_text or "").strip():
                continue
            depth = int(savepoint.depth)
            if depth in processed_depths:
                continue
            processed_depths.add(depth)
            unit = self._ensure_unit_row(session, task, repo_row, entry_row, savepoint)
            self._process_stage4_unit(
                session,
                task=task,
                repo_row=repo_row,
                entry_row=entry_row,
                unit=unit,
                savepoint=savepoint,
                stage4_runtime=stage4_runtime,
                pending_schedules=pending_schedules,
                pending_materializations=pending_materializations,
            )

        session.flush()
        units = list(
            session.scalars(
                select(BatchTaskUnit)
                .where(BatchTaskUnit.task_entry_id == entry_row.id)
                .order_by(BatchTaskUnit.depth.asc())
            )
        )
        if active_run is not None:
            entry_row.stage3_run_id = active_run.id
            entry_row.status = active_run.status
            if active_run.status == Stage3RunStatus.queued.value:
                self._queue_schedule_request(
                    pending_schedules,
                    kind="stage3",
                    run_id=active_run.id,
                )
            return
        if units:
            entry_row.status = "completed"
            entry_row.error_message = None
            entry_row.finished_at = entry_row.finished_at or datetime.now(UTC)
            return
        if entry_row.stage3_run_id:
            tracked_run = session.get(Stage3Run, entry_row.stage3_run_id)
            latest = tracked_run or latest_run
            entry_row.finished_at = entry_row.finished_at or datetime.now(UTC)
            entry_row.status = "failed"
            entry_row.error_message = (
                getattr(latest, "error_message", None)
                or f"stage3 completed without materializing data ({getattr(latest, 'result', 'unknown')})"
            )
            return
        if existing_assets:
            self._ensure_units_for_existing_assets(
                session,
                task=task,
                repo_row=repo_row,
                entry_row=entry_row,
                assets=existing_assets,
            )
            entry_row.status = "completed"
            entry_row.error_message = None
            entry_row.finished_at = entry_row.finished_at or datetime.now(UTC)
            return
        if latest_run is None or latest_run.status == Stage3RunStatus.completed.value:
            if not self._task_has_schedule_capacity(task, "stage3", pending_schedules):
                entry_row.status = "pending"
                return
            try:
                service = Stage3Service(session, workspace_root=self.settings.stage3_workspace_dir, settings=self.settings)
                run = service.create_run_for_entry_file(
                    repo_row.repository_id,
                    entry_file.id,
                    trigger_kind="batch",
                    prefer_latest_snapshot=True,
                    runtime_snapshot=stage3_runtime,
                )
                run = service.queue_run(repo_row.repository_id, run.id)
                entry_row.stage3_run_id = run.id
                entry_row.status = "queued"
                session.flush()
                self._queue_schedule_request(
                    pending_schedules,
                    kind="stage3",
                    run_id=run.id,
                )
            except (Stage3RunConflictError, ValueError) as exc:
                entry_row.status = "failed"
                entry_row.error_message = str(exc)
            return
        latest = runs[0] if runs else None
        entry_row.status = "failed"
        entry_row.error_message = getattr(latest, "error_message", None) or "stage3 did not produce data"

    def _ensure_unit_row(
        self,
        session: Session,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        entry_row: BatchTaskEntry,
        savepoint: Stage3Savepoint,
    ) -> BatchTaskUnit:
        existing = session.scalar(
            select(BatchTaskUnit)
            .where(
                BatchTaskUnit.task_entry_id == entry_row.id,
                BatchTaskUnit.depth == int(savepoint.depth),
            )
            .limit(1)
        )
        if existing is not None:
            existing.stage3_savepoint_id = savepoint.id
            return existing
        row = BatchTaskUnit(
            task_id=task.id,
            task_repository_id=repo_row.id,
            task_entry=entry_row,
            depth=int(savepoint.depth),
            status="pending",
            stage3_savepoint_id=savepoint.id,
        )
        session.add(row)
        session.flush()
        return row

    def _ensure_units_for_existing_assets(
        self,
        session: Session,
        *,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        entry_row: BatchTaskEntry,
        assets: list[DataPoolAsset],
    ) -> list[BatchTaskUnit]:
        units: list[BatchTaskUnit] = []
        for asset in assets:
            depth = int(asset.depth)
            stage3_savepoint_id = self._existing_stage3_savepoint_id(session, asset.stage3_savepoint_id)
            stage4_run_id = self._existing_stage4_run_id(session, asset.stage4_run_id)
            existing = session.scalar(
                select(BatchTaskUnit)
                .where(
                    BatchTaskUnit.task_entry_id == entry_row.id,
                    BatchTaskUnit.depth == depth,
                )
                .limit(1)
            )
            if existing is None:
                existing = BatchTaskUnit(
                    task_id=task.id,
                    task_repository_id=repo_row.id,
                    task_entry=entry_row,
                    depth=depth,
                    status="completed",
                    stage3_savepoint_id=stage3_savepoint_id,
                    stage4_run_id=stage4_run_id,
                    data_pool_asset_id=asset.id,
                    stats_json={"reused_data_pool_asset": True},
                )
                session.add(existing)
            else:
                existing.status = "completed"
                existing.data_pool_asset_id = asset.id
                existing.stage3_savepoint_id = stage3_savepoint_id
                existing.stage4_run_id = stage4_run_id
                existing.stats_json = {
                    **dict(existing.stats_json or {}),
                    "reused_data_pool_asset": True,
                }
            existing.error_message = None
            existing.finished_at = existing.finished_at or datetime.now(UTC)
            units.append(existing)
        session.flush()
        return units

    def _existing_stage3_savepoint_id(self, session: Session, value: int | None) -> int | None:
        if value is None:
            return None
        try:
            savepoint_id = int(value)
        except (TypeError, ValueError):
            return None
        return savepoint_id if session.get(Stage3Savepoint, savepoint_id) is not None else None

    def _existing_stage4_run_id(self, session: Session, value: str | None) -> str | None:
        run_id = str(value or "").strip()
        if not run_id:
            return None
        return run_id if session.get(Stage4Run, run_id) is not None else None

    def _process_stage4_unit(
        self,
        session: Session,
        *,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        entry_row: BatchTaskEntry,
        unit: BatchTaskUnit,
        savepoint: Stage3Savepoint,
        stage4_runtime: dict[str, Any],
        pending_schedules: list[dict[str, Any]],
        pending_materializations: list[dict[str, Any]],
    ) -> None:
        try:
            issue_style_config = _normalized_stage4_issue_style_config(stage4_runtime)
        except ValueError as exc:
            unit.status = "failed"
            unit.error_message = str(exc)
            unit.finished_at = unit.finished_at or datetime.now(UTC)
            return

        asset = session.scalar(
            select(DataPoolAsset)
            .where(
                DataPoolAsset.pool_id == task.data_pool_id,
                DataPoolAsset.github_repo_id == repo_row.github_repo_id,
                DataPoolAsset.entry_file_path == entry_row.entry_file_path,
                DataPoolAsset.depth == int(savepoint.depth),
            )
            .limit(1)
        )
        if asset is not None and _data_pool_asset_matches_issue_style_config(
            session,
            asset,
            issue_style_config,
        ):
            unit.status = "completed"
            unit.data_pool_asset_id = asset.id
            unit.finished_at = unit.finished_at or datetime.now(UTC)
            return
        unit.data_pool_asset_id = None

        incompatible_active_run = False
        if unit.stage4_run_id:
            run = session.get(Stage4Run, unit.stage4_run_id)
            if run is None:
                unit.status = "failed"
                unit.error_message = "stage4 run disappeared"
                return
            if _stage4_run_matches_issue_style_config(run, issue_style_config):
                if self._advance_existing_stage4_run(
                    run,
                    task=task,
                    repo_row=repo_row,
                    entry_row=entry_row,
                    unit=unit,
                    pending_schedules=pending_schedules,
                    pending_materializations=pending_materializations,
                ):
                    return
                unit.status = "failed"
                unit.error_message = run.error_message or f"stage4 result: {run.result}"
                unit.finished_at = unit.finished_at or datetime.now(UTC)
                return
            incompatible_active_run = run.status in {
                Stage4RunStatus.pending.value,
                Stage4RunStatus.queued.value,
                Stage4RunStatus.running.value,
            }
            unit.stage4_run_id = None

        stage4_runs = sorted(
            list(savepoint.stage4_runs or []),
            key=lambda candidate: (
                candidate.created_at or datetime.min.replace(tzinfo=UTC),
                candidate.id,
            ),
            reverse=True,
        )
        for run in stage4_runs:
            if not _stage4_run_matches_issue_style_config(run, issue_style_config):
                if run.status in {
                    Stage4RunStatus.pending.value,
                    Stage4RunStatus.queued.value,
                    Stage4RunStatus.running.value,
                }:
                    incompatible_active_run = True
                continue
            if self._advance_existing_stage4_run(
                run,
                task=task,
                repo_row=repo_row,
                entry_row=entry_row,
                unit=unit,
                pending_schedules=pending_schedules,
                pending_materializations=pending_materializations,
            ):
                unit.stage4_run_id = run.id
                return

        if incompatible_active_run:
            unit.status = "pending"
            unit.error_message = None
            return

        if not self._task_has_schedule_capacity(task, "stage4", pending_schedules):
            unit.status = "pending"
            return

        try:
            service = Stage4Service(session, workspace_root=self.settings.stage4_workspace_dir, settings=self.settings)
            hyperparameters = dict(stage4_runtime.get("hyperparameters") or {})
            hyperparameters.pop("issue_variant_count", None)
            hyperparameters.pop("hint_variant_count", None)
            run = service.create_run(
                savepoint.id,
                trigger_kind="batch",
                enabled_issue_styles=(
                    list(issue_style_config["enabled_styles"])
                    if issue_style_config is not None
                    else None
                ),
                expected_issue_style_catalog_sha256=(
                    str(issue_style_config["catalog_sha256"])
                    if issue_style_config is not None
                    else None
                ),
            )
            runtime_for_run = {
                key: value
                for key, value in dict(stage4_runtime or {}).items()
                if key in {"concurrency", "issuer", "hyperparameters"}
            }
            if "hyperparameters" in runtime_for_run:
                runtime_for_run["hyperparameters"] = hyperparameters
            if runtime_for_run:
                run = service.update_run_runtime_snapshot(
                    run.id,
                    runtime_snapshot=runtime_for_run,
                )
            run = service.queue_run(run.id)
            unit.stage4_run_id = run.id
            unit.status = "queued"
            session.flush()
            self._queue_schedule_request(
                pending_schedules,
                kind="stage4",
                run_id=run.id,
            )
        except (Stage4RunConflictError, ValueError) as exc:
            unit.status = "failed"
            unit.error_message = str(exc)

    def _advance_existing_stage4_run(
        self,
        run: Stage4Run,
        *,
        task: BatchTask,
        repo_row: BatchTaskRepository,
        entry_row: BatchTaskEntry,
        unit: BatchTaskUnit,
        pending_schedules: list[dict[str, Any]],
        pending_materializations: list[dict[str, Any]],
    ) -> bool:
        if run.status in {Stage4RunStatus.pending.value, Stage4RunStatus.queued.value, Stage4RunStatus.running.value}:
            unit.status = run.status
            unit.error_message = None
            if run.status == Stage4RunStatus.queued.value:
                self._queue_schedule_request(
                    pending_schedules,
                    kind="stage4",
                    run_id=run.id,
                )
            return True
        if run.result == Stage4RunResult.generated.value:
            unit.status = "running"
            unit.error_message = None
            self._queue_materialization_request(
                pending_materializations,
                task_id=task.id,
                task_repository_id=repo_row.id,
                task_entry_id=entry_row.id,
                task_unit_id=unit.id,
                stage4_run_id=run.id,
            )
            return True
        return False

    def _refresh_repo_status(self, repo_row: BatchTaskRepository) -> None:
        entries = list(repo_row.entries or [])
        if not entries:
            return
        units = [unit for entry in entries for unit in list(entry.units or [])]
        asset_count = sum(1 for unit in units if unit.data_pool_asset_id)
        failed_unit_count = sum(1 for unit in units if unit.status == "failed")
        active_entry_statuses = {"pending", "queued", "running"}
        terminal_problem_entry_statuses = {"failed", "partial", "cancelled"}
        active_unit_statuses = {"pending", "queued", "running"}
        terminal_problem_unit_statuses = {"failed", "cancelled"}

        has_active_entry = any(entry.status in active_entry_statuses for entry in entries)
        has_problem_entry = any(entry.status in terminal_problem_entry_statuses for entry in entries)
        has_active_unit = any(unit.status in active_unit_statuses for unit in units)
        has_problem_unit = any(unit.status in terminal_problem_unit_statuses for unit in units)

        if has_active_entry:
            repo_row.stage3_status = (
                "running"
                if any(entry.status == "running" for entry in entries)
                else "pending"
            )
        elif has_problem_entry:
            repo_row.stage3_status = (
                "partial"
                if any(entry.status in {"completed", "partial"} for entry in entries)
                else "failed"
            )
        else:
            repo_row.stage3_status = "completed"

        if not units:
            repo_row.stage4_status = "completed" if repo_row.stage3_status == "completed" else "pending"
        elif any(unit.status == "running" for unit in units):
            repo_row.stage4_status = "running"
        elif any(unit.status in {"pending", "queued"} for unit in units):
            repo_row.stage4_status = "pending"
        elif has_problem_unit:
            repo_row.stage4_status = (
                "partial"
                if asset_count > 0 or any(unit.status == "completed" for unit in units)
                else "failed"
            )
        else:
            repo_row.stage4_status = "completed"

        if has_active_entry or has_active_unit:
            repo_row.status = (
                "running"
                if any(entry.status == "running" for entry in entries)
                or any(unit.status == "running" for unit in units)
                else "pending"
            )
        elif has_problem_entry or has_problem_unit:
            repo_row.status = (
                "partial"
                if asset_count > 0 or any(entry.status in {"completed", "partial"} for entry in entries)
                else "failed"
            )
            repo_row.finished_at = repo_row.finished_at or datetime.now(UTC)
        else:
            repo_row.status = "completed"
            repo_row.finished_at = repo_row.finished_at or datetime.now(UTC)
        repo_row.stats_json = {
            "entry_file_count": len(entries),
            "asset_count": asset_count,
            "failed_unit_count": failed_unit_count,
        }

    def _repo_has_active_units(self, repo_row: BatchTaskRepository) -> bool:
        for entry in list(repo_row.entries or []):
            if entry.status in {"pending", "queued", "running"}:
                return True
            if any(unit.status in {"pending", "queued", "running"} for unit in list(entry.units or [])):
                return True
        return False

    def _finalize_if_terminal(self, task_id: str) -> bool:
        session = self.session_factory()
        try:
            service = BatchTaskService(session)
            task = service.get_task(task_id, include_detail=True)
            repos = list(task.repositories or [])
            if any(repo.status in {"pending", "queued", "running"} for repo in repos):
                return False
            if any(self._repo_has_active_units(repo) for repo in repos):
                return False
            has_problem_repo = any(repo.status in {"failed", "partial"} for repo in repos)
            status = BatchTaskStatus.partial.value if has_problem_repo else BatchTaskStatus.completed.value
            if not repos:
                status = BatchTaskStatus.completed.value
            service.mark_terminal(task_id, status=status, phase=status)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _interrupt_known_runs(self, task_id: str) -> None:
        session = self.session_factory()
        cancel_message = "batch task cancelled by user"
        try:
            task_service = BatchTaskService(session)
            task = task_service.get_task(task_id, include_detail=True)
            stage2_service = Stage2Service(session)
            stage3_service = Stage3Service(
                session,
                workspace_root=self.settings.stage3_workspace_dir,
                settings=self.settings,
            )
            stage4_service = Stage4Service(
                session,
                workspace_root=self.settings.stage4_workspace_dir,
                settings=self.settings,
            )
            now = datetime.now(UTC)
            for repo in list(task.repositories or []):
                if repo.stage2_run_id:
                    accepted = self.stage2_runner.interrupt_run(repo.stage2_run_id)
                    if not accepted:
                        self._force_interrupt_stage2_run(
                            stage2_service,
                            repository_id=repo.repository_id,
                            run_id=repo.stage2_run_id,
                            error_message=f"{cancel_message} before runner found an active stage2 task",
                        )
                if repo.status in {"pending", "queued", "running"}:
                    repo.status = "cancelled"
                    repo.finished_at = repo.finished_at or now
                    repo.error_message = repo.error_message or cancel_message
                if repo.stage2_status in {"pending", "queued", "running"}:
                    repo.stage2_status = "cancelled"
                if repo.stage3_status in {"pending", "queued", "running"}:
                    repo.stage3_status = "cancelled"
                if repo.stage4_status in {"pending", "queued", "running"}:
                    repo.stage4_status = "cancelled"
                for entry in list(repo.entries or []):
                    if entry.stage3_run_id:
                        accepted = self.stage3_runner.interrupt_run(entry.stage3_run_id)
                        if not accepted:
                            self._force_interrupt_stage3_run(
                                stage3_service,
                                repository_id=repo.repository_id,
                                run_id=entry.stage3_run_id,
                            )
                    if entry.status in {"pending", "queued", "running"}:
                        entry.status = "cancelled"
                        entry.finished_at = entry.finished_at or now
                        entry.error_message = entry.error_message or cancel_message
                    for unit in list(entry.units or []):
                        if unit.stage4_run_id:
                            accepted = self.stage4_runner.interrupt_run(unit.stage4_run_id)
                            if not accepted:
                                self._force_interrupt_stage4_run(stage4_service, run_id=unit.stage4_run_id)
                        if unit.status in {"pending", "queued", "running"}:
                            unit.status = "cancelled"
                            unit.finished_at = unit.finished_at or now
                            unit.error_message = unit.error_message or cancel_message
            task_service.refresh_task_stats(task_id)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _queue_schedule_request(
        self,
        pending_schedules: list[dict[str, Any]],
        *,
        kind: str,
        run_id: str,
    ) -> None:
        normalized_kind = str(kind or "").strip()
        normalized_run_id = str(run_id or "").strip()
        if normalized_kind not in {"stage2", "stage3", "stage4"} or not normalized_run_id:
            return
        duplicate = next(
            (
                item
                for item in pending_schedules
                if item.get("kind") == normalized_kind and item.get("run_id") == normalized_run_id
            ),
            None,
        )
        if duplicate is not None:
            return
        pending_schedules.append({"kind": normalized_kind, "run_id": normalized_run_id})

    def _dispatch_pending_schedules(
        self,
        pending_schedules: list[dict[str, Any]],
        *,
        task: BatchTask | None = None,
    ) -> None:
        attempted_by_kind: dict[str, set[str]] = {kind: set() for kind in _BATCH_SCHEDULABLE_KINDS}
        running_by_kind: dict[str, set[str]] = (
            {
                kind: self._task_stage_run_ids(task, kind, statuses=_BATCH_RUNNING_RUN_STATUSES)
                for kind in _BATCH_SCHEDULABLE_KINDS
            }
            if task is not None
            else {}
        )
        for request in list(pending_schedules):
            kind = str(request.get("kind") or "").strip()
            run_id = str(request.get("run_id") or "").strip()
            if not kind or not run_id:
                continue
            if task is not None:
                limit = self._task_stage_schedule_limit(task, kind)
                occupied = set(running_by_kind.get(kind) or set())
                occupied.update(attempted_by_kind.get(kind) or set())
                if len(occupied) >= limit:
                    continue
            try:
                if kind == "stage2":
                    self.stage2_runner.schedule_run(run_id)
                elif kind == "stage3":
                    self.stage3_runner.schedule_run(run_id)
                elif kind == "stage4":
                    self.stage4_runner.schedule_run(run_id)
                if kind in attempted_by_kind:
                    attempted_by_kind[kind].add(run_id)
            except Exception as exc:  # noqa: BLE001
                print(f"[feature_factory] failed to schedule {kind} run {run_id}: {exc}", flush=True)

    def _task_has_schedule_capacity(
        self,
        task: BatchTask,
        kind: str,
        pending_schedules: list[dict[str, Any]] | None = None,
    ) -> bool:
        active_ids = self._task_stage_run_ids(task, kind, statuses=_BATCH_ACTIVE_RUN_STATUSES)
        pending_ids = {
            str(item.get("run_id") or "").strip()
            for item in list(pending_schedules or [])
            if str(item.get("kind") or "").strip() == kind and str(item.get("run_id") or "").strip()
        }
        return len(active_ids | pending_ids) < self._task_stage_schedule_limit(task, kind)

    def _task_stage_schedule_limit(self, task: BatchTask, kind: str) -> int:
        runtime = dict(task.runtime_snapshot_json or {})
        if kind == "stage2":
            default = int(self.settings.stage2_default_task_max_concurrent_runs or self.settings.stage2_max_concurrent_runs)
            capacity = self._stage_runner_capacity(self.stage2_runner, default=self.settings.stage2_max_concurrent_runs)
        elif kind == "stage3":
            default = int(self.settings.stage3_default_task_max_concurrent_runs or self.settings.stage3_max_concurrent_runs)
            capacity = self._stage_runner_capacity(self.stage3_runner, default=self.settings.stage3_max_concurrent_runs)
        elif kind == "stage4":
            default = int(self.settings.stage4_default_task_max_concurrent_runs or self.settings.stage4_max_concurrent_runs)
            capacity = self._stage_runner_capacity(self.stage4_runner, default=self.settings.stage4_max_concurrent_runs)
        else:
            return 1
        concurrency = dict((runtime.get(kind) or {}).get("concurrency") or {})
        raw_value = concurrency.get("max_concurrent_runs")
        try:
            value = int(raw_value if raw_value is not None else default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(value, max(1, capacity)))

    def _stage_runner_capacity(self, runner: Any, *, default: int) -> int:
        get_max_workers = getattr(runner, "get_max_workers", None)
        if callable(get_max_workers):
            try:
                return max(1, int(get_max_workers()))
            except (TypeError, ValueError):
                return max(1, int(default))
        return max(1, int(default))

    def _task_stage_run_ids(self, task: BatchTask, kind: str, *, statuses: set[str]) -> set[str]:
        run_ids: set[str] = set()
        if kind == "stage2":
            for repo in list(task.repositories or []):
                if str(repo.stage2_status or "") not in statuses:
                    continue
                run_id = str(repo.stage2_run_id or "").strip()
                if run_id:
                    run_ids.add(run_id)
            return run_ids
        if kind == "stage3":
            for repo in list(task.repositories or []):
                for entry in list(repo.entries or []):
                    if str(entry.status or "") not in statuses:
                        continue
                    run_id = str(entry.stage3_run_id or "").strip()
                    if run_id:
                        run_ids.add(run_id)
            return run_ids
        if kind == "stage4":
            for repo in list(task.repositories or []):
                for entry in list(repo.entries or []):
                    for unit in list(entry.units or []):
                        if str(unit.status or "") not in statuses:
                            continue
                        run_id = str(unit.stage4_run_id or "").strip()
                        if run_id:
                            run_ids.add(run_id)
        return run_ids

    def _queue_materialization_request(
        self,
        pending_materializations: list[dict[str, Any]],
        *,
        task_id: str,
        task_repository_id: str,
        task_entry_id: str,
        task_unit_id: str,
        stage4_run_id: str,
    ) -> None:
        normalized_run_id = str(stage4_run_id or "").strip()
        if not normalized_run_id:
            return
        duplicate = next(
            (
                item
                for item in pending_materializations
                if str(item.get("stage4_run_id") or "").strip() == normalized_run_id
            ),
            None,
        )
        if duplicate is not None:
            return
        pending_materializations.append(
            {
                "task_id": str(task_id or "").strip(),
                "task_repository_id": str(task_repository_id or "").strip(),
                "task_entry_id": str(task_entry_id or "").strip(),
                "task_unit_id": str(task_unit_id or "").strip(),
                "stage4_run_id": normalized_run_id,
            }
        )

    def _materialize_pending_stage4_runs(self, pending_materializations: list[dict[str, Any]]) -> None:
        for request in list(pending_materializations):
            self._materialize_stage4_run_result(request)

    def _materialize_stage4_run_result(self, request: dict[str, Any]) -> None:
        task_id = str(request.get("task_id") or "").strip()
        repo_row_id = str(request.get("task_repository_id") or "").strip()
        entry_row_id = str(request.get("task_entry_id") or "").strip()
        unit_id = str(request.get("task_unit_id") or "").strip()
        stage4_run_id = str(request.get("stage4_run_id") or "").strip()
        if not all([task_id, repo_row_id, entry_row_id, unit_id, stage4_run_id]):
            return

        def callback(session: Session) -> None:
            task = session.get(BatchTask, task_id)
            repo_row = session.get(BatchTaskRepository, repo_row_id)
            entry_row = session.get(BatchTaskEntry, entry_row_id)
            unit = session.get(BatchTaskUnit, unit_id)
            run = session.get(Stage4Run, stage4_run_id)
            if task is None or repo_row is None or entry_row is None or unit is None or run is None:
                return
            if unit.data_pool_asset_id:
                if unit.status != "completed":
                    unit.status = "completed"
                    unit.finished_at = unit.finished_at or datetime.now(UTC)
                self._refresh_entry_status(entry_row)
                self._refresh_repo_status(repo_row)
                BatchTaskService(session).refresh_task_stats(task.id)
                return
            if run.result != Stage4RunResult.generated.value:
                return
            materialized = DataPoolService(session).materialize_stage4_run(
                pool_id=task.data_pool_id,
                stage4_run_id=run.id,
                batch_task_id=task.id,
                reuse_compatible_existing=True,
            )
            unit.status = "completed"
            unit.error_message = None
            unit.data_pool_asset_id = materialized.id
            unit.finished_at = unit.finished_at or datetime.now(UTC)
            self._refresh_entry_status(entry_row)
            self._refresh_repo_status(repo_row)
            BatchTaskService(session).refresh_task_stats(task.id)

        self._with_session(callback)

    def _refresh_entry_status(self, entry_row: BatchTaskEntry) -> None:
        units = list(entry_row.units or [])
        if not units:
            return
        session = object_session(entry_row)
        if session is not None and entry_row.stage3_run_id:
            run = session.get(Stage3Run, entry_row.stage3_run_id)
            if run is not None and run.status in {
                Stage3RunStatus.pending.value,
                Stage3RunStatus.queued.value,
                Stage3RunStatus.running.value,
            }:
                entry_row.status = run.status
                return
        entry_row.status = "completed"
        entry_row.error_message = None
        entry_row.finished_at = entry_row.finished_at or datetime.now(UTC)

    def _force_interrupt_stage2_run(
        self,
        service: Stage2Service,
        *,
        repository_id: int,
        run_id: str,
        error_message: str,
    ) -> None:
        try:
            service.request_run_interrupt(repository_id, run_id)
        except (Stage2RunInterruptConflictError, ValueError):
            return
        service.mark_run_interrupted(run_id, error_message=error_message)

    def _force_interrupt_stage3_run(
        self,
        service: Stage3Service,
        *,
        repository_id: int,
        run_id: str,
    ) -> None:
        try:
            service.interrupt_run(repository_id, run_id)
        except (Stage3RunConflictError, ValueError):
            return

    def _force_interrupt_stage4_run(
        self,
        service: Stage4Service,
        *,
        run_id: str,
    ) -> None:
        try:
            service.interrupt_run(run_id)
        except (Stage4RunConflictError, ValueError):
            return

    def _github_tokens_for_task(self, task: BatchTask) -> list[str]:
        runtime = dict(task.runtime_snapshot_json or {})
        return [str(token) for token in list(runtime.get("github_tokens") or []) if str(token).strip()]

    def _is_cancel_requested(self, task_id: str) -> bool:
        return bool(
            self._with_session(
                lambda session: session.scalar(select(BatchTask.cancel_requested).where(BatchTask.id == task_id))
            )
        )

    def _mark_task_running(self, task_id: str, phase: str) -> None:
        self._with_session(lambda session: BatchTaskService(session).mark_running(task_id, phase=phase))

    def _mark_task_phase(self, task_id: str, phase: str) -> None:
        def update_phase(session: Session) -> None:
            task = session.get(BatchTask, task_id)
            if task is not None:
                task.phase = phase

        self._with_session(update_phase)

    def _mark_cancelled(self, task_id: str) -> None:
        self._with_session(
            lambda session: BatchTaskService(session).mark_terminal(
                task_id,
                status=BatchTaskStatus.cancelled.value,
                phase="cancelled",
                error_message="batch task cancelled by user",
            )
        )

    def _with_session(self, callback):
        session = self.session_factory()
        try:
            result = callback(session)
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
