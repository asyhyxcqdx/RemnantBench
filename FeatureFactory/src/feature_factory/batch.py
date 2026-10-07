from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from feature_factory.data_pool import DataPoolService
from feature_factory.models import (
    BatchTask,
    BatchTaskEntry,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    BatchTaskRepository,
    BatchTaskStatus,
    BatchTaskUnit,
    DataPool,
    GlobalRuntimeConfig,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage3Run,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4Run,
    Stage4RunStatus,
)

BATCH_GLOBAL_CONFIG_KEY = "batch"
BATCH_TASK_ACTIVE_STATUSES = {
    BatchTaskStatus.pending.value,
    BatchTaskStatus.running.value,
}
BATCH_TASK_TERMINAL_STATUSES = {
    BatchTaskStatus.completed.value,
    BatchTaskStatus.partial.value,
    BatchTaskStatus.failed.value,
    BatchTaskStatus.cancelled.value,
}
BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS = {
    Stage2RunResult.abandoned.value,
    Stage2RunResult.defect.value,
}
BATCH_WAITING_STATUSES = {"pending", "queued", "planning", "paused"}
BATCH_ENTRY_RUN_STATUS_FOLLOWS_STAGE3_RUN = {"pending", "queued", "running", "partial", "failed"}


class BatchTaskDeleteConflictError(RuntimeError):
    pass


class BatchTaskRetryConflictError(RuntimeError):
    pass


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _token_preview(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    return f"{value[:6]}..."


def _normalize_tokens(tokens: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for token in list(tokens or []):
        value = str(token or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _task_can_delete(status: str | None) -> bool:
    return str(status or "") in BATCH_TASK_TERMINAL_STATUSES


class BatchTaskService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_global_config(self) -> GlobalRuntimeConfig:
        row = self.session.get(GlobalRuntimeConfig, BATCH_GLOBAL_CONFIG_KEY)
        if row is None:
            row = GlobalRuntimeConfig(
                key=BATCH_GLOBAL_CONFIG_KEY,
                config_json={"github_tokens": []},
            )
            self.session.add(row)
            self.session.flush()
        return row

    def update_global_config(self, *, github_tokens: list[str] | None = None) -> GlobalRuntimeConfig:
        row = self.get_global_config()
        config = dict(row.config_json or {})
        if github_tokens is not None:
            config["github_tokens"] = _normalize_tokens(github_tokens)
        row.config_json = config
        self.session.flush()
        return row

    def global_github_tokens(self) -> list[str]:
        row = self.get_global_config()
        return _normalize_tokens(list((row.config_json or {}).get("github_tokens") or []))

    def serialize_global_config(self, row: GlobalRuntimeConfig | None = None) -> dict[str, Any]:
        config = dict((row or self.get_global_config()).config_json or {})
        tokens = _normalize_tokens(list(config.get("github_tokens") or []))
        return {
            "github_token_count": len(tokens),
            "github_token_previews": [_token_preview(token) for token in tokens],
            "updated_at": serialize_datetime((row or self.get_global_config()).updated_at),
        }

    def create_task(
        self,
        *,
        name: str,
        note: str | None,
        data_pool_id: str,
        filters: dict[str, Any],
        token_source: str,
        runtime_snapshot: dict[str, Any],
        max_concurrent_jobs: int | None = None,
        max_concurrent_partitions: int | None = None,
        temporary_github_tokens: list[str] | None = None,
    ) -> BatchTask:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("batch task name is required")
        pool = self.session.get(DataPool, data_pool_id)
        if pool is None:
            raise ValueError(f"data pool not found: {data_pool_id}")
        normalized_token_source = str(token_source or "temporary").strip() or "temporary"
        if normalized_token_source not in {"global", "temporary"}:
            raise ValueError("token_source must be global or temporary")
        runtime_payload = dict(runtime_snapshot or {})
        runtime_payload["stage1"] = {
            **dict(runtime_payload.get("stage1") or {}),
            "max_concurrent_jobs": max_concurrent_jobs,
            "max_concurrent_partitions": max_concurrent_partitions,
        }
        if normalized_token_source == "temporary":
            runtime_payload["github_tokens"] = _normalize_tokens(temporary_github_tokens)
        task = BatchTask(
            name=normalized_name,
            note=str(note or "").strip() or None,
            data_pool_id=data_pool_id,
            status=BatchTaskStatus.pending.value,
            phase="created",
            token_source=normalized_token_source,
            filters_json=dict(filters or {}),
            runtime_snapshot_json=runtime_payload,
            stats_json={},
        )
        self.session.add(task)
        self.session.flush()
        self.refresh_task_stats(task.id)
        return task

    def get_task(self, task_id: str, *, include_detail: bool = True) -> BatchTask:
        stmt = select(BatchTask).where(BatchTask.id == str(task_id))
        if include_detail:
            stmt = stmt.options(
                selectinload(BatchTask.pool),
                selectinload(BatchTask.repositories)
                .selectinload(BatchTaskRepository.entries)
                .selectinload(BatchTaskEntry.units),
            )
        else:
            stmt = stmt.options(selectinload(BatchTask.pool))
        task = self.session.scalar(stmt)
        if task is None:
            raise ValueError(f"batch task not found: {task_id}")
        return task

    def list_tasks(self, *, limit: int = 50) -> list[BatchTask]:
        return list(
            self.session.scalars(
                select(BatchTask)
                .options(selectinload(BatchTask.pool))
                .order_by(BatchTask.created_at.desc(), BatchTask.id.desc())
                .limit(max(1, min(int(limit or 50), 200)))
            )
        )

    def request_cancel(self, task_id: str) -> BatchTask:
        task = self.get_task(task_id, include_detail=False)
        if _task_can_delete(task.status):
            return task
        task.cancel_requested = True
        task.phase = "cancelling"
        self.session.flush()
        return task

    def retry_task(
        self,
        task_id: str,
        *,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> BatchTask:
        task = self.get_task(task_id, include_detail=True)
        if str(task.status or "") in BATCH_TASK_ACTIVE_STATUSES:
            raise BatchTaskRetryConflictError(f"cannot retry an active batch task: {task_id}")

        if runtime_snapshot is not None:
            task.runtime_snapshot_json = dict(runtime_snapshot or {})

        for repo_row in list(task.repositories or []):
            repo_row.entries.clear()
            repo_row.status = "pending"
            repo_row.stage2_status = "pending"
            repo_row.stage3_status = "pending"
            repo_row.stage4_status = "pending"
            repo_row.stage2_run_id = None
            repo_row.source_stage2_run_id = None
            repo_row.stage3_snapshot_id = None
            repo_row.stats_json = {}
            repo_row.error_message = None
            repo_row.started_at = None
            repo_row.finished_at = None
        task.status = BatchTaskStatus.pending.value
        task.phase = "created"
        task.stats_json = {}
        task.error_message = None
        task.cancel_requested = False
        task.started_at = None
        task.finished_at = None
        self.session.flush()
        self.refresh_task_stats(task.id)
        return task

    def retry_failed_repository(
        self,
        task_id: str,
        task_repository_id: str,
    ) -> tuple[BatchTask, BatchTaskRepository]:
        task = self.get_task(task_id, include_detail=True)
        repo_row = next(
            (
                row
                for row in list(task.repositories or [])
                if str(row.id) == str(task_repository_id)
            ),
            None,
        )
        if repo_row is None:
            raise ValueError(
                f"batch task repository not found: {task_repository_id}"
            )
        if bool(task.cancel_requested):
            raise BatchTaskRetryConflictError(
                f"cannot retry a repository while batch task cancellation is pending: {task_id}"
            )
        if str(repo_row.status or "") != "failed":
            raise BatchTaskRetryConflictError(
                f"batch task repository is not failed: {task_repository_id}"
            )

        active_stage2_run = self.session.scalar(
            select(Stage2Run)
            .where(
                Stage2Run.repository_id == repo_row.repository_id,
                Stage2Run.status.in_(
                    {
                        Stage2RunStatus.queued.value,
                        Stage2RunStatus.running.value,
                    }
                ),
            )
            .order_by(
                Stage2Run.created_at.desc(),
                Stage2Run.id.desc(),
            )
            .limit(1)
        )
        repo_row.entries.clear()
        repo_row.status = (
            "running"
            if active_stage2_run is not None
            and active_stage2_run.status == Stage2RunStatus.running.value
            else "pending"
        )
        repo_row.stage2_status = (
            str(active_stage2_run.status)
            if active_stage2_run is not None
            else "pending"
        )
        repo_row.stage3_status = "pending"
        repo_row.stage4_status = "pending"
        repo_row.stage2_run_id = (
            active_stage2_run.id
            if active_stage2_run is not None
            else None
        )
        repo_row.source_stage2_run_id = None
        repo_row.stage3_snapshot_id = None
        repo_row.stats_json = {}
        repo_row.error_message = None
        repo_row.started_at = None
        repo_row.finished_at = None

        if str(task.status or "") not in BATCH_TASK_ACTIVE_STATUSES:
            task.status = BatchTaskStatus.pending.value
            task.phase = "created"
            task.error_message = None
            task.cancel_requested = False
            task.finished_at = None

        self.session.flush()
        self.refresh_task_stats(task.id)
        return task, repo_row

    def delete_task_cleanup_plan(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, include_detail=True)
        if not _task_can_delete(task.status):
            raise BatchTaskDeleteConflictError(f"cannot delete an active batch task: {task_id}")

        stage1_crawl_job_id = str(task.stage1_crawl_job_id or "").strip() or None
        stage2_runs: list[dict[str, Any]] = []
        stage3_runs: list[dict[str, Any]] = []
        stage3_snapshots: list[dict[str, Any]] = []
        stage4_runs: list[dict[str, Any]] = []
        seen_stage2_run_ids: set[str] = set()
        seen_stage3_run_ids: set[str] = set()
        seen_stage3_snapshot_ids: set[str] = set()
        seen_stage4_run_ids: set[str] = set()

        for repo_row in list(task.repositories or []):
            repository_id = int(repo_row.repository_id)

            stage2_run_id = str(repo_row.stage2_run_id or "").strip()
            if stage2_run_id and stage2_run_id not in seen_stage2_run_ids:
                run = self.session.get(Stage2Run, stage2_run_id)
                if run is not None and str(run.trigger_kind or "") in {"batch", "batch_retry"}:
                    seen_stage2_run_ids.add(stage2_run_id)
                    stage2_runs.append(
                        {
                            "repository_id": repository_id,
                            "run_id": stage2_run_id,
                        }
                    )

            for entry_row in list(repo_row.entries or []):
                stage3_entry_file_id = str(entry_row.stage3_entry_file_id or "").strip()
                snapshot_id = str(repo_row.stage3_snapshot_id or "").strip()
                if stage3_entry_file_id:
                    entry_file = self.session.get(Stage3EntryFile, stage3_entry_file_id)
                    if entry_file is not None:
                        if not snapshot_id:
                            snapshot_id = str(entry_file.snapshot_id or "").strip()
                        stage3_entry_runs = list(
                            self.session.scalars(
                                select(Stage3Run).where(Stage3Run.entry_file_id == stage3_entry_file_id)
                            )
                        )
                    else:
                        stage3_entry_runs = []
                else:
                    stage3_entry_runs = []

                stage3_run_id = str(entry_row.stage3_run_id or "").strip()
                if stage3_run_id and all(str(run.id) != stage3_run_id for run in stage3_entry_runs):
                    run = self.session.get(Stage3Run, stage3_run_id)
                    if run is not None:
                        stage3_entry_runs.append(run)

                for run in stage3_entry_runs:
                    run_id = str(run.id or "").strip()
                    if not run_id or run_id in seen_stage3_run_ids:
                        continue
                    if str(run.trigger_kind or "") not in {"batch", "batch_retry"}:
                        continue
                    seen_stage3_run_ids.add(run_id)
                    stage3_runs.append(
                        {
                            "repository_id": repository_id,
                            "run_id": run_id,
                        }
                    )

                if snapshot_id and snapshot_id not in seen_stage3_snapshot_ids:
                    snapshot = self.session.get(Stage3CommitSnapshot, snapshot_id)
                    if (
                        snapshot is not None
                        and str(snapshot.source_stage2_run_id or "").strip() in seen_stage2_run_ids
                    ):
                        seen_stage3_snapshot_ids.add(snapshot_id)
                        stage3_snapshots.append({"snapshot_id": snapshot_id})

                for unit_row in list(entry_row.units or []):
                    stage4_run_id = str(unit_row.stage4_run_id or "").strip()
                    if stage4_run_id and stage4_run_id not in seen_stage4_run_ids:
                        run = self.session.get(Stage4Run, stage4_run_id)
                        if run is not None and str(run.trigger_kind or "") in {"batch", "batch_retry"}:
                            seen_stage4_run_ids.add(stage4_run_id)
                            stage4_runs.append({"run_id": stage4_run_id})

        return {
            "task_id": task.id,
            "stage1_crawl_job_id": stage1_crawl_job_id,
            "stage2_runs": stage2_runs,
            "stage3_runs": stage3_runs,
            "stage3_snapshots": stage3_snapshots,
            "stage4_runs": stage4_runs,
        }

    def delete_task_record(self, task_id: str) -> BatchTask:
        task = self.get_task(task_id, include_detail=False)
        self.session.delete(task)
        self.session.flush()
        return task

    def mark_running(self, task_id: str, *, phase: str) -> BatchTask:
        task = self.get_task(task_id, include_detail=False)
        task.status = BatchTaskStatus.running.value
        task.phase = phase
        task.started_at = task.started_at or _utcnow()
        task.finished_at = None
        task.error_message = None
        self.session.flush()
        return task

    def mark_terminal(self, task_id: str, *, status: str, phase: str, error_message: str | None = None) -> BatchTask:
        task = self.get_task(task_id, include_detail=False)
        task.status = status
        task.phase = phase
        task.error_message = error_message
        task.finished_at = _utcnow()
        self.refresh_task_stats(task_id)
        self.session.flush()
        return task

    def refresh_task_stats(self, task_id: str) -> dict[str, Any]:
        task = self.get_task(task_id, include_detail=False)
        repo_count = int(
            self.session.scalar(
                select(func.count(BatchTaskRepository.id)).where(BatchTaskRepository.task_id == task_id)
            )
            or 0
        )
        entry_count = int(
            self.session.scalar(select(func.count(BatchTaskEntry.id)).where(BatchTaskEntry.task_id == task_id)) or 0
        )
        unit_count = int(
            self.session.scalar(select(func.count(BatchTaskUnit.id)).where(BatchTaskUnit.task_id == task_id)) or 0
        )
        asset_count = int(
            self.session.scalar(
                select(func.count(BatchTaskUnit.id)).where(
                    BatchTaskUnit.task_id == task_id,
                    BatchTaskUnit.data_pool_asset_id.is_not(None),
                )
            )
            or 0
        )
        failed_units = int(
            self.session.scalar(
                select(func.count(BatchTaskUnit.id)).where(
                    BatchTaskUnit.task_id == task_id,
                    BatchTaskUnit.status == "failed",
                )
            )
            or 0
        )
        stage2_running = int(
            self.session.scalar(
                select(func.count(func.distinct(Stage2Run.id)))
                .join(
                    BatchTaskRepository,
                    or_(
                        BatchTaskRepository.stage2_run_id == Stage2Run.id,
                        BatchTaskRepository.source_stage2_run_id == Stage2Run.id,
                    ),
                )
                .where(
                    BatchTaskRepository.task_id == task_id,
                    Stage2Run.status == Stage2RunStatus.running.value,
                )
            )
            or 0
        )
        stage3_running = int(
            self.session.scalar(
                select(func.count(func.distinct(Stage3Run.id)))
                .join(BatchTaskEntry, BatchTaskEntry.stage3_run_id == Stage3Run.id)
                .where(
                    BatchTaskEntry.task_id == task_id,
                    Stage3Run.status == Stage3RunStatus.running.value,
                )
            )
            or 0
        )
        stage4_running = int(
            self.session.scalar(
                select(func.count(func.distinct(Stage4Run.id)))
                .join(BatchTaskUnit, BatchTaskUnit.stage4_run_id == Stage4Run.id)
                .where(
                    BatchTaskUnit.task_id == task_id,
                    Stage4Run.status == Stage4RunStatus.running.value,
                )
            )
            or 0
        )
        stats = {
            "repository_count": repo_count,
            "entry_file_count": entry_count,
            "unit_count": unit_count,
            "asset_count": asset_count,
            "failed_unit_count": failed_units,
            "stage2_running_count": stage2_running,
            "stage3_running_count": stage3_running,
            "stage4_running_count": stage4_running,
            "updated_at": serialize_datetime(_utcnow()),
        }
        task.stats_json = stats
        self.session.flush()
        return stats

    def serialize_task_summary(self, task: BatchTask) -> dict[str, Any]:
        status = str(task.status or "")
        stats = dict(task.stats_json or {})
        if status not in BATCH_TASK_ACTIVE_STATUSES:
            stats["stage2_running_count"] = 0
            stats["stage3_running_count"] = 0
            stats["stage4_running_count"] = 0
        return {
            "id": task.id,
            "name": task.name,
            "note": task.note,
            "status": status,
            "phase": task.phase,
            "data_pool_id": task.data_pool_id,
            "data_pool": DataPoolService(self.session).serialize_pool(task.pool) if task.pool is not None else None,
            "stage1_crawl_job_id": task.stage1_crawl_job_id,
            "token_source": task.token_source,
            "stats": stats,
            "error_message": task.error_message,
            "cancel_requested": bool(task.cancel_requested),
            "created_at": serialize_datetime(task.created_at),
            "updated_at": serialize_datetime(task.updated_at),
            "started_at": serialize_datetime(task.started_at),
            "finished_at": serialize_datetime(task.finished_at),
            "can_cancel": status in BATCH_TASK_ACTIVE_STATUSES,
            "can_delete": _task_can_delete(task.status),
            "can_retry": status not in BATCH_TASK_ACTIVE_STATUSES,
        }

    def serialize_task_detail(self, task: BatchTask) -> dict[str, Any]:
        return self.serialize_task_detail_page(task)

    def serialize_task_detail_page(
        self,
        task: BatchTask,
        *,
        repo_page: int = 1,
        repo_page_size: int = 15,
        repo_query: str | None = None,
        repo_language: str | None = None,
        repo_stars_min: int | None = None,
        repo_stars_max: int | None = None,
        repo_status: str | None = None,
        repo_stage2_status: str | None = None,
        repo_stage3_status: str | None = None,
        repo_stage4_status: str | None = None,
        repo_has_errors: bool = False,
        repo_non_pending: bool = False,
    ) -> dict[str, Any]:
        payload = self.serialize_task_summary(task)
        payload["filters"] = dict(task.filters_json or {})
        payload["runtime_snapshot"] = self._redacted_runtime_snapshot(task.runtime_snapshot_json)
        repository_page = self.list_task_repositories(
            task.id,
            page=repo_page,
            page_size=repo_page_size,
            query=repo_query,
            language=repo_language,
            stars_min=repo_stars_min,
            stars_max=repo_stars_max,
            status=repo_status,
            stage2_status=repo_stage2_status,
            stage3_status=repo_stage3_status,
            stage4_status=repo_stage4_status,
            has_errors=repo_has_errors,
            non_pending=repo_non_pending,
        )
        payload["repositories"] = repository_page["repositories"]
        payload["repository_pagination"] = repository_page["pagination"]
        payload["repository_filters"] = repository_page["filters"]
        return payload

    def list_task_repositories(
        self,
        task_id: str,
        *,
        page: int = 1,
        page_size: int = 15,
        query: str | None = None,
        language: str | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        status: str | None = None,
        stage2_status: str | None = None,
        stage3_status: str | None = None,
        stage4_status: str | None = None,
        has_errors: bool = False,
        non_pending: bool = False,
    ) -> dict[str, Any]:
        self.get_task(task_id, include_detail=False)
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 15), 100))
        conditions = [BatchTaskRepository.task_id == str(task_id)]

        normalized_query = str(query or "").strip()
        if normalized_query:
            like = f"%{normalized_query}%"
            conditions.append(BatchTaskRepository.repo_full_name.ilike(like))

        normalized_language = str(language or "").strip()
        if normalized_language:
            conditions.append(func.lower(func.coalesce(BatchTaskRepository.language, "")) == normalized_language.lower())
        if stars_min is not None:
            conditions.append(BatchTaskRepository.stars >= int(stars_min))
        if stars_max is not None:
            conditions.append(BatchTaskRepository.stars <= int(stars_max))

        repository_running_condition = self._repository_running_condition()
        repository_waiting_condition = self._repository_waiting_condition(repository_running_condition)
        stage2_non_pipeline_terminal_condition = self._repository_stage2_non_pipeline_terminal_condition()

        normalized_status = str(status or "").strip()
        if normalized_status:
            if normalized_status in BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS:
                conditions.append(
                    or_(
                        self._repository_stage2_non_pipeline_terminal_condition({normalized_status}),
                        and_(
                            BatchTaskRepository.status == normalized_status,
                            ~stage2_non_pipeline_terminal_condition,
                        ),
                    )
                )
            elif normalized_status in BATCH_WAITING_STATUSES:
                conditions.append(repository_waiting_condition)
            elif normalized_status == "running":
                conditions.append(repository_running_condition)
            elif normalized_status == "completed":
                conditions.append(
                    self._repository_completed_condition(
                        repository_running_condition,
                        repository_waiting_condition,
                    )
                )
            elif normalized_status == "partial":
                conditions.append(self._repository_partial_condition(repository_running_condition))
            elif normalized_status == "failed":
                conditions.append(self._repository_failed_condition())
            else:
                conditions.append(BatchTaskRepository.status == normalized_status)

        normalized_stage2_status = str(stage2_status or "").strip()
        if normalized_stage2_status:
            stage2_running_condition = self._repository_stage2_running_condition()
            if normalized_stage2_status in BATCH_WAITING_STATUSES:
                conditions.append(
                    and_(
                        ~stage2_running_condition,
                        self._repository_stage2_waiting_condition(),
                    )
                )
            elif normalized_stage2_status == "running":
                conditions.append(stage2_running_condition)
            elif normalized_stage2_status in BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS:
                conditions.append(self._repository_stage2_non_pipeline_terminal_condition({normalized_stage2_status}))
            else:
                conditions.append(
                    and_(
                        BatchTaskRepository.stage2_status == normalized_stage2_status,
                        ~stage2_non_pipeline_terminal_condition,
                    )
                )

        normalized_stage3_status = str(stage3_status or "").strip()
        if normalized_stage3_status:
            stage3_running_condition = self._repository_stage3_running_condition()
            stage3_waiting_condition = self._repository_stage3_waiting_condition()
            if normalized_stage3_status in BATCH_WAITING_STATUSES:
                conditions.append(
                    and_(
                        ~stage3_running_condition,
                        or_(
                            BatchTaskRepository.stage3_status.in_(BATCH_WAITING_STATUSES),
                            stage3_waiting_condition,
                        ),
                    )
                )
            elif normalized_stage3_status == "running":
                conditions.append(stage3_running_condition)
            elif normalized_stage3_status == "completed":
                conditions.append(
                    self._repository_stage3_completed_condition(
                        stage3_running_condition,
                        stage3_waiting_condition,
                    )
                )
            else:
                conditions.append(BatchTaskRepository.stage3_status == normalized_stage3_status)

        normalized_stage4_status = str(stage4_status or "").strip()
        if normalized_stage4_status:
            stage4_running_condition = self._repository_stage4_running_condition()
            stage4_waiting_condition = self._repository_stage4_waiting_condition()
            if normalized_stage4_status in BATCH_WAITING_STATUSES:
                conditions.append(
                    and_(
                        ~stage4_running_condition,
                        or_(
                            BatchTaskRepository.stage4_status.in_(BATCH_WAITING_STATUSES),
                            stage4_waiting_condition,
                        ),
                    )
                )
            elif normalized_stage4_status == "running":
                conditions.append(stage4_running_condition)
            elif normalized_stage4_status == "completed":
                conditions.append(
                    self._repository_stage4_completed_condition(
                        stage4_running_condition,
                        stage4_waiting_condition,
                    )
                )
            else:
                conditions.append(BatchTaskRepository.stage4_status == normalized_stage4_status)

        if non_pending:
            conditions.append(~repository_waiting_condition)

        if has_errors:
            conditions.append(
                or_(
                    BatchTaskRepository.error_message.is_not(None),
                    BatchTaskRepository.status.in_(["failed", "partial"]),
                    BatchTaskRepository.id.in_(
                        select(BatchTaskEntry.task_repository_id).where(
                            or_(
                                BatchTaskEntry.error_message.is_not(None),
                                BatchTaskEntry.status.in_(["failed", "partial"]),
                            )
                        )
                    ),
                    BatchTaskRepository.id.in_(
                        select(BatchTaskUnit.task_repository_id).where(
                            or_(
                                BatchTaskUnit.error_message.is_not(None),
                                BatchTaskUnit.status == "failed",
                            )
                        )
                    ),
                )
            )

        total = int(self.session.scalar(select(func.count(BatchTaskRepository.id)).where(*conditions)) or 0)
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = min(page, total_pages)
        running_repository_order = case(
            (
                repository_running_condition,
                0,
            ),
            else_=1,
        )
        rows = list(
            self.session.scalars(
                select(BatchTaskRepository)
                .options(selectinload(BatchTaskRepository.entries).selectinload(BatchTaskEntry.units))
                .where(*conditions)
                .order_by(
                    running_repository_order.asc(),
                    BatchTaskRepository.updated_at.desc(),
                    BatchTaskRepository.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        return {
            "repositories": [self.serialize_task_repository(row) for row in rows],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            "filters": {
                "query": normalized_query,
                "language": normalized_language,
                "stars_min": int(stars_min) if stars_min is not None else None,
                "stars_max": int(stars_max) if stars_max is not None else None,
                "status": normalized_status or None,
                "stage2_status": normalized_stage2_status or None,
                "stage3_status": normalized_stage3_status or None,
                "stage4_status": normalized_stage4_status or None,
                "has_errors": bool(has_errors),
                "non_pending": bool(non_pending),
            },
        }

    def _repository_stage2_running_condition(self):
        waiting_stage2_run_ids = select(Stage2Run.id).where(Stage2Run.status.in_(BATCH_WAITING_STATUSES))
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            or_(
                and_(
                    BatchTaskRepository.stage2_status == "running",
                    or_(
                        BatchTaskRepository.stage2_run_id.is_(None),
                        ~BatchTaskRepository.stage2_run_id.in_(waiting_stage2_run_ids),
                    ),
                ),
                BatchTaskRepository.stage2_run_id.in_(
                    select(Stage2Run.id).where(Stage2Run.status == Stage2RunStatus.running.value)
                ),
            ),
        )

    def _repository_stage2_waiting_condition(self):
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            or_(
                BatchTaskRepository.stage2_status.in_(BATCH_WAITING_STATUSES),
                BatchTaskRepository.stage2_run_id.in_(
                    select(Stage2Run.id).where(Stage2Run.status.in_(BATCH_WAITING_STATUSES))
                ),
            ),
        )

    def _repository_stage2_non_pipeline_terminal_condition(self, results: set[str] | None = None):
        result_values = set(results or BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS)
        raw_stage2_terminal_condition = BatchTaskRepository.stage2_status.in_(BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS)
        live_stage2_terminal_run_ids = select(Stage2Run.id).where(
            Stage2Run.status == Stage2RunStatus.completed.value,
            Stage2Run.result.in_(result_values),
        )
        live_stage2_terminal_condition = and_(
            BatchTaskRepository.stage2_run_id.is_not(None),
            BatchTaskRepository.stage2_run_id.in_(live_stage2_terminal_run_ids),
        )
        return or_(
            BatchTaskRepository.stage2_status.in_(result_values),
            and_(
                ~raw_stage2_terminal_condition,
                live_stage2_terminal_condition,
            ),
        )

    def _repository_stage3_running_condition(self):
        entry_has_units = BatchTaskEntry.id.in_(select(BatchTaskUnit.task_entry_id))
        running_stage3_run_ids = select(Stage3Run.id).where(Stage3Run.status == Stage3RunStatus.running.value)
        return BatchTaskRepository.id.in_(
            select(BatchTaskEntry.task_repository_id).where(
                or_(
                    and_(BatchTaskEntry.status == "running", ~entry_has_units),
                    and_(
                        entry_has_units,
                        BatchTaskEntry.status.in_(BATCH_ENTRY_RUN_STATUS_FOLLOWS_STAGE3_RUN),
                        BatchTaskEntry.stage3_run_id.in_(running_stage3_run_ids),
                    ),
                )
            )
        )

    def _repository_stage3_waiting_condition(self):
        waiting_stage3_run_ids = select(Stage3Run.id).where(Stage3Run.status.in_(BATCH_WAITING_STATUSES))
        return BatchTaskRepository.id.in_(
            select(BatchTaskEntry.task_repository_id).where(
                or_(
                    BatchTaskEntry.status.in_(BATCH_WAITING_STATUSES),
                    and_(
                        BatchTaskEntry.id.in_(select(BatchTaskUnit.task_entry_id)),
                        BatchTaskEntry.status.in_(BATCH_ENTRY_RUN_STATUS_FOLLOWS_STAGE3_RUN),
                        BatchTaskEntry.stage3_run_id.in_(waiting_stage3_run_ids),
                    ),
                )
            )
        )

    def _repository_stage3_completed_condition(self, running_condition=None, waiting_condition=None):
        running_condition = running_condition if running_condition is not None else self._repository_stage3_running_condition()
        waiting_condition = waiting_condition if waiting_condition is not None else self._repository_stage3_waiting_condition()
        entry_has_units = BatchTaskEntry.id.in_(select(BatchTaskUnit.task_entry_id))
        has_entries = BatchTaskRepository.id.in_(select(BatchTaskEntry.task_repository_id))
        has_problem_entries = BatchTaskRepository.id.in_(
            select(BatchTaskEntry.task_repository_id).where(
                or_(
                    BatchTaskEntry.status == "cancelled",
                    and_(BatchTaskEntry.status.in_({"failed", "partial"}), ~entry_has_units),
                )
            )
        )
        return and_(
            ~running_condition,
            ~waiting_condition,
            or_(
                BatchTaskRepository.stage3_status == "completed",
                and_(has_entries, ~has_problem_entries),
            ),
        )

    def _repository_stage4_running_condition(self):
        running_stage4_run_ids = select(Stage4Run.id).where(Stage4Run.status == Stage4RunStatus.running.value)
        waiting_stage4_run_ids = select(Stage4Run.id).where(Stage4Run.status.in_(BATCH_WAITING_STATUSES))
        return BatchTaskRepository.id.in_(
            select(BatchTaskUnit.task_repository_id).where(
                or_(
                    and_(
                        BatchTaskUnit.status == "running",
                        or_(
                            BatchTaskUnit.stage4_run_id.is_(None),
                            ~BatchTaskUnit.stage4_run_id.in_(waiting_stage4_run_ids),
                        ),
                    ),
                    and_(
                        BatchTaskUnit.status.in_({*BATCH_WAITING_STATUSES, "running"}),
                        BatchTaskUnit.stage4_run_id.in_(running_stage4_run_ids),
                    ),
                )
            )
        )

    def _repository_stage4_waiting_condition(self):
        waiting_stage4_run_ids = select(Stage4Run.id).where(Stage4Run.status.in_(BATCH_WAITING_STATUSES))
        return BatchTaskRepository.id.in_(
            select(BatchTaskUnit.task_repository_id).where(
                or_(
                    BatchTaskUnit.status.in_(BATCH_WAITING_STATUSES),
                    and_(
                        BatchTaskUnit.status.in_({*BATCH_WAITING_STATUSES, "running"}),
                        BatchTaskUnit.stage4_run_id.in_(waiting_stage4_run_ids),
                    ),
                )
            )
        )

    def _repository_stage4_completed_condition(self, running_condition=None, waiting_condition=None):
        running_condition = running_condition if running_condition is not None else self._repository_stage4_running_condition()
        waiting_condition = waiting_condition if waiting_condition is not None else self._repository_stage4_waiting_condition()
        has_units = BatchTaskRepository.id.in_(select(BatchTaskUnit.task_repository_id))
        has_problem_units = BatchTaskRepository.id.in_(
            select(BatchTaskUnit.task_repository_id).where(BatchTaskUnit.status.in_({"failed", "cancelled"}))
        )
        return and_(
            ~running_condition,
            ~waiting_condition,
            or_(
                BatchTaskRepository.stage4_status == "completed",
                and_(has_units, ~has_problem_units),
            ),
        )

    def _repository_running_condition(self):
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            ~BatchTaskRepository.status.in_({"failed", "cancelled"}),
            or_(
                self._repository_stage2_running_condition(),
                self._repository_stage3_running_condition(),
                self._repository_stage4_running_condition(),
            ),
        )

    def _repository_waiting_condition(self, running_condition=None):
        running_condition = running_condition if running_condition is not None else self._repository_running_condition()
        has_waiting_work = or_(
            self._repository_stage2_waiting_condition(),
            self._repository_stage3_waiting_condition(),
            self._repository_stage4_waiting_condition(),
        )
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            ~running_condition,
            or_(
                BatchTaskRepository.status.in_(BATCH_WAITING_STATUSES),
                and_(BatchTaskRepository.status == "running", has_waiting_work),
                and_(BatchTaskRepository.status == "completed", has_waiting_work),
            ),
        )

    def _repository_completed_condition(self, running_condition=None, waiting_condition=None):
        running_condition = running_condition if running_condition is not None else self._repository_running_condition()
        waiting_condition = waiting_condition if waiting_condition is not None else self._repository_waiting_condition(
            running_condition
        )
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            BatchTaskRepository.status == "completed",
            ~running_condition,
            ~waiting_condition,
        )

    def _repository_partial_signal_condition(self):
        return or_(
            func.coalesce(BatchTaskRepository.stats_json["asset_count"].as_integer(), 0) > 0,
            BatchTaskRepository.id.in_(
                select(BatchTaskUnit.task_repository_id).where(BatchTaskUnit.data_pool_asset_id.is_not(None))
            ),
            BatchTaskRepository.id.in_(
                select(BatchTaskEntry.task_repository_id).where(BatchTaskEntry.status.in_({"completed", "partial"}))
            ),
        )

    def _repository_partial_condition(self, running_condition=None):
        running_condition = running_condition if running_condition is not None else self._repository_running_condition()
        partial_signal = self._repository_partial_signal_condition()
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            or_(
                and_(BatchTaskRepository.status == "partial", ~running_condition),
                and_(BatchTaskRepository.status == "failed", partial_signal),
            ),
        )

    def _repository_failed_condition(self):
        return and_(
            ~self._repository_stage2_non_pipeline_terminal_condition(),
            BatchTaskRepository.status == "failed",
            ~self._repository_partial_signal_condition(),
        )

    def serialize_task_repository(self, row: BatchTaskRepository) -> dict[str, Any]:
        stage2_status = self._effective_stage2_status(row)
        entries = list(row.entries or [])
        serialized_entries = [self.serialize_task_entry(entry) for entry in entries]
        return {
            "id": row.id,
            "repository_id": row.repository_id,
            "github_repo_id": row.github_repo_id,
            "repo_full_name": row.repo_full_name,
            "language": row.language,
            "stars": row.stars,
            "status": self._effective_repository_status(
                row,
                stage2_status=stage2_status,
                serialized_entries=serialized_entries,
            ),
            "stage2_status": stage2_status,
            "stage3_status": self._effective_stage3_status(row, serialized_entries=serialized_entries),
            "stage4_status": self._effective_stage4_status(row),
            "stage2_run_id": row.stage2_run_id,
            "source_stage2_run_id": row.source_stage2_run_id,
            "stage3_snapshot_id": row.stage3_snapshot_id,
            "stats": dict(row.stats_json or {}),
            "error_message": row.error_message,
            "entries": serialized_entries,
            "created_at": serialize_datetime(row.created_at),
            "updated_at": serialize_datetime(row.updated_at),
        }

    def _effective_stage2_status(self, row: BatchTaskRepository) -> str:
        status = str(row.stage2_status or "")
        if status in BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS:
            return status
        run_id = str(row.stage2_run_id or "").strip()
        if run_id:
            run = self.session.get(Stage2Run, run_id)
            if run is not None and str(run.status or "") in {*BATCH_WAITING_STATUSES, Stage2RunStatus.running.value}:
                return str(run.status or "")
            if (
                run is not None
                and str(run.status or "") == Stage2RunStatus.completed.value
                and str(run.result or "") in BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS
            ):
                return str(run.result or "")
        return status

    def _effective_repository_status(
        self,
        row: BatchTaskRepository,
        *,
        stage2_status: str | None = None,
        serialized_entries: list[dict[str, Any]] | None = None,
    ) -> str:
        if str(stage2_status or "") in BATCH_STAGE2_NON_PIPELINE_TERMINAL_RESULTS:
            return str(stage2_status or "")
        status = str(row.status or "")
        entries = list(row.entries or [])
        units = [unit for entry in entries for unit in list(entry.units or [])]
        entry_statuses = (
            {str(entry.get("status") or "") for entry in serialized_entries}
            if serialized_entries is not None
            else {self._effective_entry_status(entry) for entry in entries}
        )
        unit_statuses = {self._effective_unit_status(unit) for unit in units}
        has_running_stage = (
            str(stage2_status or "") == "running"
            or "running" in entry_statuses
            or "running" in unit_statuses
        )
        has_waiting_work = (
            str(stage2_status or "") in BATCH_WAITING_STATUSES
            or any(entry_status in BATCH_WAITING_STATUSES for entry_status in entry_statuses)
            or any(unit_status in BATCH_WAITING_STATUSES for unit_status in unit_statuses)
        )
        if (
            status == "running"
            and str(stage2_status or "") == "pending"
            and not str(row.stage2_run_id or "").strip()
            and not str(row.source_stage2_run_id or "").strip()
            and not str(row.stage3_snapshot_id or "").strip()
            and not entries
        ):
            return "pending"
        if has_running_stage and status not in {"failed", "cancelled"}:
            return "running"
        if status == "running" and not has_running_stage and has_waiting_work:
            return "pending"
        if status == "completed" and has_waiting_work:
            return "pending"
        if status != "failed":
            return status
        stats = dict(row.stats_json or {})
        asset_count = int(stats.get("asset_count") or 0)
        if asset_count <= 0:
            asset_count = sum(1 for unit in units if unit.data_pool_asset_id)
        if asset_count > 0 or any(str(entry.status or "") in {"completed", "partial"} for entry in entries):
            return "partial"
        return status

    def _effective_stage3_status(
        self,
        row: BatchTaskRepository,
        *,
        serialized_entries: list[dict[str, Any]],
    ) -> str:
        statuses = [str(entry.get("status") or "") for entry in serialized_entries]
        if not statuses:
            return str(row.stage3_status or "pending")
        if any(status == "running" for status in statuses):
            return "running"
        if any(status in BATCH_WAITING_STATUSES for status in statuses):
            return "pending"
        if all(status == "completed" for status in statuses):
            return "completed"
        if any(status in {"failed", "partial", "cancelled"} for status in statuses):
            return "partial" if any(status in {"completed", "partial"} for status in statuses) else "failed"
        return str(row.stage3_status or "pending")

    def _effective_stage4_status(self, row: BatchTaskRepository) -> str:
        units = [unit for entry in list(row.entries or []) for unit in list(entry.units or [])]
        if not units:
            return str(row.stage4_status or "pending")
        statuses = [self._effective_unit_status(unit) for unit in units]
        if any(status == "running" for status in statuses):
            return "running"
        if any(status in BATCH_WAITING_STATUSES for status in statuses):
            return "pending"
        if all(status == "completed" for status in statuses):
            return "completed"
        if any(status in {"failed", "cancelled"} for status in statuses):
            asset_count = sum(1 for unit in units if unit.data_pool_asset_id)
            return "partial" if asset_count > 0 or any(status == "completed" for status in statuses) else "failed"
        return str(row.stage4_status or "pending")

    def serialize_task_entry(self, row: BatchTaskEntry) -> dict[str, Any]:
        return {
            "id": row.id,
            "stage3_entry_file_id": row.stage3_entry_file_id,
            "entry_file_path": row.entry_file_path,
            "status": self._effective_entry_status(row),
            "stage3_run_id": row.stage3_run_id,
            "stats": dict(row.stats_json or {}),
            "error_message": row.error_message,
            "units": [self.serialize_task_unit(unit) for unit in list(row.units or [])],
            "created_at": serialize_datetime(row.created_at),
            "updated_at": serialize_datetime(row.updated_at),
        }

    def _effective_entry_status(self, row: BatchTaskEntry) -> str:
        status = str(row.status or "")
        units = list(row.units or [])
        if units and status in {"pending", "queued", "running", "partial", "failed"}:
            run_id = str(row.stage3_run_id or "").strip()
            run = self.session.get(Stage3Run, run_id) if run_id else None
            if run is not None and str(run.status or "") in {"pending", "queued", "running"}:
                return str(run.status or "")
            if status in {"running", "partial", "failed"}:
                return "completed"
        return status

    def serialize_task_unit(self, row: BatchTaskUnit) -> dict[str, Any]:
        stage3_savepoint_id = row.stage3_savepoint_id
        if stage3_savepoint_id is not None and self.session.get(Stage3Savepoint, stage3_savepoint_id) is None:
            stage3_savepoint_id = None
        stage4_run_id = str(row.stage4_run_id or "").strip() or None
        if stage4_run_id is not None and self.session.get(Stage4Run, stage4_run_id) is None:
            stage4_run_id = None
        return {
            "id": row.id,
            "depth": row.depth,
            "status": self._effective_unit_status(row),
            "stage3_savepoint_id": stage3_savepoint_id,
            "stage4_run_id": stage4_run_id,
            "data_pool_asset_id": row.data_pool_asset_id,
            "stats": dict(row.stats_json or {}),
            "error_message": row.error_message,
            "created_at": serialize_datetime(row.created_at),
            "updated_at": serialize_datetime(row.updated_at),
        }

    def _effective_unit_status(self, row: BatchTaskUnit) -> str:
        status = str(row.status or "")
        if status in {*BATCH_WAITING_STATUSES, "running"}:
            run_id = str(row.stage4_run_id or "").strip()
            run = self.session.get(Stage4Run, run_id) if run_id else None
            if run is not None and str(run.status or "") in {*BATCH_WAITING_STATUSES, Stage4RunStatus.running.value}:
                return str(run.status or "")
        return status

    def _redacted_runtime_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        redacted = dict(snapshot or {})
        redacted.pop("github_tokens", None)
        for stage_key, agent_keys in {
            "stage2": ("planner", "worker"),
            "stage3": ("breaker",),
            "stage4": ("issuer",),
        }.items():
            stage = dict(redacted.get(stage_key) or {})
            for agent_key in agent_keys:
                agent = dict(stage.get(agent_key) or {})
                api_key = str(agent.get("api_key") or "")
                if api_key:
                    agent["api_key_preview"] = agent.get("api_key_preview") or _token_preview(api_key)
                agent.pop("api_key", None)
                if agent:
                    stage[agent_key] = agent
            if stage:
                redacted[stage_key] = stage
        return redacted
