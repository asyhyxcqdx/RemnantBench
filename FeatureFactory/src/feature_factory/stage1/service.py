from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from feature_factory.config import Settings
from feature_factory.models import (
    BatchTaskRepository,
    CrawlJob,
    CrawlJobStatus,
    CrawlPartition,
    CrawlPartitionQueryExecution,
    CrawlPartitionQueryStatus,
    CrawlPartitionStatus,
    DataPoolAsset,
    GitHubRepository,
    RepoDiscovery,
    Stage2Run,
    Stage3CommitSnapshot,
)
from feature_factory.stage1.github_client import GitHubSearchClient
from feature_factory.stage1.partitioning import (
    PartitionCountEstimate,
    PartitionPlanner,
    PartitionPlanningCheckpoint,
    PartitionPlanningPaused,
    PartitionPlanningProgress,
)
from feature_factory.stage1.query import build_search_queries
from feature_factory.stage1.schemas import CrawlFilters, TimePartition

REPOSITORY_SORT_FIELDS = {
    "full_name": GitHubRepository.full_name,
    "primary_language": GitHubRepository.primary_language,
    "stargazers_count": GitHubRepository.stargazers_count,
    "created_at_github": GitHubRepository.created_at_github,
    "pushed_at_github": GitHubRepository.pushed_at_github,
    "discovered_at": GitHubRepository.discovered_at,
}

REPOSITORY_ID_FILTER_BATCH_SIZE = 500


def parse_github_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _clone_stats(stats_json: dict | None) -> dict:
    if isinstance(stats_json, dict):
        return dict(stats_json)
    return {}

class CrawlService:
    def __init__(self, session: Session, settings: Settings, client: GitHubSearchClient) -> None:
        self.session = session
        self.settings = settings
        self.client = client

    def create_job(
        self,
        name: str,
        filters: CrawlFilters,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> CrawlJob:
        job = CrawlJob(
            name=name,
            status=CrawlJobStatus.pending.value,
            filters_json=filters.model_dump(mode="json"),
            stats_json={},
        )
        self.session.add(job)
        self.session.flush()
        self._set_job_stats(job, max_concurrent_partitions=max_concurrent_partitions)
        return job

    def set_job_execution_options(
        self,
        job_id: str,
        *,
        max_concurrent_partitions: int | None = None,
    ) -> CrawlJob:
        job = self.get_job(job_id)
        self._set_job_stats(job, max_concurrent_partitions=max_concurrent_partitions)
        self.session.flush()
        return job

    def get_job(self, job_id: str) -> CrawlJob:
        job = self.session.get(CrawlJob, job_id)
        if job is None:
            raise ValueError(f"crawl job not found: {job_id}")
        return job

    def mark_job_queued(self, job_id: str) -> CrawlJob:
        job = self.get_job(job_id)
        planning_checkpoint = self._get_planning_checkpoint(job.stats_json)
        planning_progress = self._get_planning_progress(job.stats_json) if planning_checkpoint is not None else None
        job.status = CrawlJobStatus.queued.value
        job.finished_at = None
        job.error_message = None
        self._set_job_stats(
            job,
            planning_progress=planning_progress,
            planning_checkpoint=planning_checkpoint,
        )
        self.session.flush()
        return job

    def plan_partitions(self, job: CrawlJob) -> list[CrawlPartition]:
        return self._plan_partitions(job)

    def _plan_partitions(
        self,
        job: CrawlJob,
        *,
        persist_progress: Callable[[], None] | None = None,
        should_pause: Callable[[], bool] | None = None,
    ) -> list[CrawlPartition]:
        existing = self._list_partitions(job.id)
        planning_checkpoint = self._get_planning_checkpoint(job.stats_json)
        if existing and planning_checkpoint is None:
            return existing

        filters = CrawlFilters.model_validate(job.filters_json)
        job.status = CrawlJobStatus.planning.value
        job.started_at = job.started_at or datetime.now(UTC)
        job.finished_at = None
        job.error_message = None
        planning_progress = self._build_planning_progress(
            job.stats_json,
            filters=filters,
            checkpoint=planning_checkpoint,
        )
        self._set_job_stats(job, planning_progress=planning_progress, planning_checkpoint=planning_checkpoint)
        self.session.flush()
        if persist_progress is not None:
            persist_progress()

        partition_result_limit = self.settings.github_partition_result_limit

        def persist_planning_state() -> None:
            self._set_job_stats(
                job,
                planning_progress=planning_progress,
                planning_checkpoint=planning_checkpoint,
            )
            self.session.flush()
            if persist_progress is not None:
                persist_progress()

        def apply_planning_progress(progress: PartitionPlanningProgress) -> None:
            planning_progress["event"] = progress.event
            planning_progress["processed_windows"] = progress.processed_windows
            planning_progress["discovered_windows"] = progress.discovered_windows
            planning_progress["queued_windows"] = progress.queued_windows
            planning_progress["split_windows"] = progress.split_windows
            planning_progress["planned_partitions"] = progress.planned_partitions
            planning_progress["overflow_windows"] = progress.overflow_windows
            planning_progress["empty_windows"] = progress.empty_windows
            planning_progress["probe_count"] = progress.probe_count
            planning_progress["current_depth"] = progress.current_partition.depth
            planning_progress["current_range_start"] = serialize_datetime(progress.current_partition.start)
            planning_progress["current_range_end"] = serialize_datetime(progress.current_partition.end)
            planning_progress["last_estimated_count"] = progress.last_estimated_count
            planning_progress["progress_ratio"] = (
                progress.processed_windows / progress.discovered_windows if progress.discovered_windows else 0.0
            )
            planning_progress["updated_at"] = serialize_datetime(datetime.now(UTC))
            if progress.event in {"planned", "empty", "split", "presplit"}:
                planning_progress["current_query_index"] = None
                planning_progress["current_query_total"] = None
                planning_progress["current_query_string"] = None
            persist_planning_state()

        def count_estimator(active_filters: CrawlFilters, partition: TimePartition) -> PartitionCountEstimate:
            total = 0
            max_query_total = 0
            queries = build_search_queries(active_filters, partition)
            for query_index, query in enumerate(queries, start=1):
                if should_pause and should_pause():
                    raise PartitionPlanningPaused()
                planning_progress["current_depth"] = partition.depth
                planning_progress["current_range_start"] = serialize_datetime(partition.start)
                planning_progress["current_range_end"] = serialize_datetime(partition.end)
                planning_progress["event"] = "counting"
                planning_progress["current_query_index"] = query_index
                planning_progress["current_query_total"] = len(queries)
                planning_progress["current_query_string"] = query
                planning_progress["count_query_calls"] = int(planning_progress["count_query_calls"]) + 1
                planning_progress["updated_at"] = serialize_datetime(datetime.now(UTC))
                persist_planning_state()
                query_total = self.client.count_repositories(query)
                total += query_total
                max_query_total = max(max_query_total, query_total)
                planning_progress["last_estimated_count"] = total
                planning_progress["updated_at"] = serialize_datetime(datetime.now(UTC))
                persist_planning_state()
                if query_total > partition_result_limit:
                    return PartitionCountEstimate(total_count=total, limiting_count=max_query_total)
            return PartitionCountEstimate(total_count=total, limiting_count=max_query_total)

        planner = PartitionPlanner(
            count_estimator=count_estimator,
            partition_result_limit=self.settings.github_partition_result_limit,
            probe_max_span_days=self.settings.github_partition_probe_max_span_days,
            progress_callback=apply_planning_progress,
        )

        try:
            plan_result = planner.plan(
                filters,
                checkpoint=planning_checkpoint,
                should_pause=should_pause,
            )
        except PartitionPlanningPaused:
            raise AssertionError("PartitionPlanningPaused should be handled by PartitionPlanner") from None
        self._persist_planned_partitions(job, filters, plan_result.partitions)
        self._persist_planned_partitions(
            job,
            filters,
            plan_result.overflow_partitions,
            status=CrawlPartitionStatus.overflow.value,
            error_message=(
                "GitHub search returned more than the allowed results for a one-second partition. "
                "Add more filters or switch to a secondary split dimension."
            ),
        )
        if plan_result.checkpoint is not None:
            job.status = CrawlJobStatus.paused.value
            job.finished_at = datetime.now(UTC)
            self._set_job_stats(
                job,
                planning_progress=planning_progress,
                planning_checkpoint=plan_result.checkpoint,
            )
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            return self._list_partitions(job.id)

        partitions = self._list_partitions(job.id)
        has_pending_partitions = any(partition.status == CrawlPartitionStatus.pending.value for partition in partitions)
        has_overflow_partitions = any(
            partition.status == CrawlPartitionStatus.overflow.value for partition in partitions
        )
        if not partitions:
            job.status = CrawlJobStatus.completed.value
            job.finished_at = datetime.now(UTC)
            self._set_job_stats(job)
        elif has_pending_partitions:
            job.status = CrawlJobStatus.pending.value
            job.finished_at = None
            self._set_job_stats(job)
        elif has_overflow_partitions:
            job.status = CrawlJobStatus.partial.value
            job.finished_at = datetime.now(UTC)
            job.error_message = None
            self._set_job_stats(job)
        else:
            job.status = CrawlJobStatus.completed.value
            job.finished_at = datetime.now(UTC)
            self._set_job_stats(job)
        self.session.flush()
        return self._list_partitions(job.id)

    def run_job(
        self,
        job_id: str,
        *,
        should_pause: Callable[[], bool] | None = None,
        persist_progress: Callable[[], None] | None = None,
    ) -> CrawlJob:
        job = self.get_job(job_id)
        try:
            pending_partition_ids = self.prepare_job_run(
                job_id,
                should_pause=should_pause,
                persist_progress=persist_progress,
            )
        except Exception as exc:
            self.mark_job_failed(job_id, str(exc), persist_progress=persist_progress)
            if persist_progress is not None:
                persist_progress()
            raise
        if job.status == CrawlJobStatus.paused.value or not pending_partition_ids:
            return job

        for partition_id in pending_partition_ids:
            if self._repository_limit_reached(job):
                self._skip_remaining_partitions_for_repository_limit(job)
                if persist_progress is not None:
                    persist_progress()
                break
            if should_pause and should_pause():
                self.mark_job_paused(job_id, persist_progress=persist_progress)
                return job
            try:
                paused_during_partition = self.execute_partition(
                    job_id,
                    partition_id,
                    should_pause=should_pause,
                )
                if persist_progress is not None:
                    persist_progress()
                if paused_during_partition:
                    self.mark_job_paused(job_id, persist_progress=persist_progress)
                    return job
                if self._repository_limit_reached(job):
                    self._skip_remaining_partitions_for_repository_limit(job)
                    if persist_progress is not None:
                        persist_progress()
                    break
            except Exception as exc:
                self.mark_job_failed(job_id, str(exc), persist_progress=persist_progress)
                if persist_progress is not None:
                    persist_progress()
                raise

        return self.finalize_job(job_id, persist_progress=persist_progress)

    def run_target_repository_job(
        self,
        job_id: str,
        *,
        should_pause: Callable[[], bool] | None = None,
        persist_progress: Callable[[], None] | None = None,
    ) -> bool:
        job = self.get_job(job_id)
        filters = CrawlFilters.model_validate(job.filters_json)
        targets = list(filters.target_repositories)
        if filters.repository_limit is not None:
            targets = targets[:filters.repository_limit]
        if not targets:
            return False

        partitions = self._ensure_target_repository_partitions(job, targets)
        self._mark_job_running(job)
        if persist_progress is not None:
            persist_progress()

        first_error: str | None = None
        for partition in partitions:
            if partition.status == CrawlPartitionStatus.completed.value:
                continue
            if should_pause and should_pause():
                self.mark_job_paused(job_id, persist_progress=persist_progress)
                return True

            target = partition.query_string.strip()
            query_executions = self._ensure_partition_query_executions(partition, [target], reset=True)
            query_execution = query_executions[0]
            self._clear_partition_discoveries(partition)
            partition.status = CrawlPartitionStatus.running.value
            partition.started_at = datetime.now(UTC)
            partition.finished_at = None
            partition.error_message = None
            partition.expected_count = 1
            partition.fetched_count = 0
            partition.unique_count = 0
            query_execution.status = CrawlPartitionQueryStatus.running.value
            query_execution.started_at = datetime.now(UTC)
            query_execution.finished_at = None
            query_execution.error_message = None
            query_execution.reported_total_count = 1
            query_execution.current_page = 1
            query_execution.total_pages = 1
            query_execution.fetched_count = 0
            query_execution.unique_count = 0
            self.session.flush()
            if persist_progress is not None:
                persist_progress()

            try:
                item = self.client.get_repository(target)
                unique_count = self._persist_search_items(job, partition, [item], query=target)
                partition.fetched_count = 1
                partition.status = CrawlPartitionStatus.completed.value
                partition.finished_at = datetime.now(UTC)
                query_execution.status = CrawlPartitionQueryStatus.completed.value
                query_execution.fetched_count = 1
                query_execution.unique_count = unique_count
                query_execution.finished_at = datetime.now(UTC)
            except Exception as exc:
                message = str(exc)
                first_error = first_error or message
                partition.status = CrawlPartitionStatus.failed.value
                partition.error_message = message
                partition.finished_at = datetime.now(UTC)
                query_execution.status = CrawlPartitionQueryStatus.failed.value
                query_execution.error_message = message
                query_execution.finished_at = datetime.now(UTC)
            finally:
                self._refresh_partition_discovery_counts(partition, query_executions)
                self.session.flush()
                if persist_progress is not None:
                    persist_progress()

        self.reconcile_job(job, is_running=False)
        if first_error and job.status in {CrawlJobStatus.failed.value, CrawlJobStatus.partial.value}:
            job.error_message = first_error
        self._set_job_stats(job)
        self.session.flush()
        if persist_progress is not None:
            persist_progress()
        return True

    def prepare_job_run(
        self,
        job_id: str,
        *,
        should_pause: Callable[[], bool] | None = None,
        persist_progress: Callable[[], None] | None = None,
    ) -> list[str]:
        job = self.get_job(job_id)
        self._plan_partitions(job, persist_progress=persist_progress, should_pause=should_pause)
        if persist_progress is not None:
            persist_progress()
        if self._get_planning_checkpoint(job.stats_json) is not None:
            return []
        pending_partition_ids = [
            partition.id
            for partition in self._list_partitions(job.id)
            if partition.status in {CrawlPartitionStatus.pending.value, CrawlPartitionStatus.failed.value}
        ]

        if job.status == CrawlJobStatus.paused.value and not pending_partition_ids:
            return []

        if not pending_partition_ids:
            self.reconcile_job(job)
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            pending_partition_ids = [
                partition.id
                for partition in self._list_partitions(job.id)
                if partition.status in {CrawlPartitionStatus.pending.value, CrawlPartitionStatus.failed.value}
            ]
            if job.status == CrawlJobStatus.paused.value and not pending_partition_ids:
                return []
            if not pending_partition_ids:
                return []

        if self._repository_limit_reached(job):
            self._skip_remaining_partitions_for_repository_limit(job)
            self.reconcile_job(job)
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            return []

        self._mark_job_running(job)
        if persist_progress is not None:
            persist_progress()
        return pending_partition_ids

    def execute_partition(
        self,
        job_id: str,
        partition_id: str,
        *,
        should_pause: Callable[[], bool] | None = None,
        persist_progress: Callable[[], None] | None = None,
    ) -> bool:
        job = self.get_job(job_id)
        partition = self.session.get(CrawlPartition, partition_id)
        if partition is None or partition.job_id != job_id:
            raise ValueError(f"crawl partition not found: {partition_id}")
        if self._repository_limit_reached(job):
            self._mark_partition_skipped_for_repository_limit(partition)
            self._set_job_stats(job)
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            return False
        try:
            paused_during_partition = self._run_partition(
                job,
                partition,
                should_pause=should_pause,
                persist_progress=persist_progress,
            )
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            return paused_during_partition
        except Exception:
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            raise

    def finalize_job(
        self,
        job_id: str,
        *,
        persist_progress: Callable[[], None] | None = None,
        is_running: bool = False,
    ) -> CrawlJob:
        job = self.get_job(job_id)
        if self._repository_limit_reached(job):
            self._skip_remaining_partitions_for_repository_limit(job)
            is_running = False
        self.reconcile_job(job, is_running=is_running)
        self.session.flush()
        if persist_progress is not None:
            persist_progress()
        return job

    def mark_job_failed(
        self,
        job_id: str,
        message: str,
        *,
        persist_progress: Callable[[], None] | None = None,
    ) -> CrawlJob:
        job = self.get_job(job_id)
        job.status = CrawlJobStatus.failed.value
        job.error_message = message
        job.finished_at = datetime.now(UTC)
        self.reconcile_job(job, is_running=False)
        self.session.flush()
        if persist_progress is not None:
            persist_progress()
        return job

    def mark_job_paused(
        self,
        job_id: str,
        *,
        persist_progress: Callable[[], None] | None = None,
    ) -> CrawlJob:
        job = self.get_job(job_id)
        planning_checkpoint = self._get_planning_checkpoint(job.stats_json)
        planning_progress = self._get_planning_progress(job.stats_json) if planning_checkpoint is not None else None
        job.status = CrawlJobStatus.paused.value
        job.finished_at = datetime.now(UTC)
        job.error_message = None
        self._set_job_stats(
            job,
            planning_progress=planning_progress,
            planning_checkpoint=planning_checkpoint,
        )
        self.session.flush()
        if persist_progress is not None:
            persist_progress()
        return job

    def has_pending_partitions(self, job_id: str) -> bool:
        return any(
            partition.status in {CrawlPartitionStatus.pending.value, CrawlPartitionStatus.failed.value}
            for partition in self._list_partitions(job_id)
        )

    def get_repository_limit(self, job_id: str) -> int | None:
        job = self.get_job(job_id)
        return self._repository_limit_for_job(job)

    def _repository_limit_for_job(self, job: CrawlJob) -> int | None:
        return CrawlFilters.model_validate(job.filters_json).repository_limit

    def _repository_discovery_count(self, job_id: str) -> int:
        return int(
            self.session.scalar(
                select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job_id)
            )
            or 0
        )

    def _repository_limit_remaining(self, job: CrawlJob) -> int | None:
        limit = self._repository_limit_for_job(job)
        if limit is None:
            return None
        return max(limit - self._repository_discovery_count(job.id), 0)

    def _repository_limit_reached(self, job: CrawlJob) -> bool:
        remaining = self._repository_limit_remaining(job)
        return remaining is not None and remaining <= 0

    def _mark_partition_skipped_for_repository_limit(self, partition: CrawlPartition) -> None:
        if partition.status == CrawlPartitionStatus.completed.value:
            return
        now = datetime.now(UTC)
        partition.status = CrawlPartitionStatus.skipped.value
        partition.finished_at = now
        partition.error_message = None
        for query_execution in partition.query_executions:
            query_execution.status = CrawlPartitionQueryStatus.completed.value
            query_execution.finished_at = query_execution.finished_at or now
            query_execution.error_message = None

    def _complete_pending_query_executions_for_repository_limit(
        self,
        query_executions: list[CrawlPartitionQueryExecution],
    ) -> None:
        now = datetime.now(UTC)
        for query_execution in query_executions:
            if query_execution.status == CrawlPartitionQueryStatus.pending.value:
                query_execution.status = CrawlPartitionQueryStatus.completed.value
                query_execution.finished_at = now
                query_execution.error_message = None

    def _skip_remaining_partitions_for_repository_limit(self, job: CrawlJob) -> None:
        skippable_statuses = {
            CrawlPartitionStatus.pending.value,
            CrawlPartitionStatus.failed.value,
            CrawlPartitionStatus.overflow.value,
        }
        changed = False
        for partition in self._list_partitions(job.id):
            if partition.status not in skippable_statuses:
                continue
            self._mark_partition_skipped_for_repository_limit(partition)
            changed = True
        if changed:
            self._set_job_stats(job)
            self.session.flush()

    def _mark_job_running(self, job: CrawlJob) -> None:
        job.status = CrawlJobStatus.running.value
        job.started_at = job.started_at or datetime.now(UTC)
        job.finished_at = None
        job.error_message = None
        self._set_job_stats(job)
        self.session.flush()

    def list_repositories(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        sort_by: str = "discovered_at",
        sort_order: str = "desc",
        name_query: str | None = None,
        language: str | None = None,
        languages: list[str] | None = None,
        licenses: list[str] | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        pushed_after: datetime | None = None,
        pushed_before: datetime | None = None,
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
    ) -> tuple[list[GitHubRepository], int]:
        conditions, ordered, tie_breaker = self.build_repository_listing_parts(
            sort_by=sort_by,
            sort_order=sort_order,
            name_query=name_query,
            language=language,
            languages=languages,
            licenses=licenses,
            stars_min=stars_min,
            stars_max=stars_max,
            created_after=created_after,
            created_before=created_before,
            pushed_after=pushed_after,
            pushed_before=pushed_before,
            discovered_after=discovered_after,
            discovered_before=discovered_before,
        )
        stmt = (
            select(GitHubRepository)
            .where(*conditions)
            .order_by(ordered, tie_breaker)
            .offset(offset)
            .limit(limit)
        )
        total = self.session.scalar(select(func.count(GitHubRepository.id)).where(*conditions)) or 0
        return list(self.session.scalars(stmt)), int(total)

    def build_repository_listing_parts(
        self,
        *,
        sort_by: str = "discovered_at",
        sort_order: str = "desc",
        name_query: str | None = None,
        language: str | None = None,
        languages: list[str] | None = None,
        licenses: list[str] | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        pushed_after: datetime | None = None,
        pushed_before: datetime | None = None,
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
    ) -> tuple[list[Any], Any, Any]:
        sort_column = REPOSITORY_SORT_FIELDS.get(sort_by, GitHubRepository.discovered_at)
        ordered = sort_column.asc() if sort_order == "asc" else sort_column.desc()
        tie_breaker = GitHubRepository.id.asc() if sort_order == "asc" else GitHubRepository.id.desc()
        conditions = []

        normalized_name_query = (name_query or "").strip()
        if normalized_name_query:
            pattern = f"%{normalized_name_query}%"
            conditions.append(
                or_(
                    GitHubRepository.full_name.ilike(pattern),
                    GitHubRepository.owner_login.ilike(pattern),
                    GitHubRepository.name.ilike(pattern),
                    GitHubRepository.description.ilike(pattern),
                )
            )

        normalized_languages: list[str] = []
        seen_languages: set[str] = set()
        for candidate in ([language] if language else []) + list(languages or []):
            normalized = candidate.strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen_languages:
                continue
            seen_languages.add(key)
            normalized_languages.append(normalized)
        if normalized_languages:
            conditions.append(
                func.lower(GitHubRepository.primary_language).in_(
                    [normalized_language.lower() for normalized_language in normalized_languages]
                )
            )

        normalized_licenses: list[str] = []
        seen_licenses: set[str] = set()
        for candidate in list(licenses or []):
            normalized = candidate.strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key in seen_licenses:
                continue
            seen_licenses.add(key)
            normalized_licenses.append(normalized)
        if normalized_licenses:
            conditions.append(
                func.lower(GitHubRepository.license_key).in_(
                    [normalized_license.lower() for normalized_license in normalized_licenses]
                )
            )

        if stars_min is not None:
            conditions.append(GitHubRepository.stargazers_count >= stars_min)
        if stars_max is not None:
            conditions.append(GitHubRepository.stargazers_count <= stars_max)
        if created_after is not None:
            conditions.append(GitHubRepository.created_at_github >= created_after)
        if created_before is not None:
            conditions.append(GitHubRepository.created_at_github <= created_before)
        if pushed_after is not None:
            conditions.append(GitHubRepository.pushed_at_github >= pushed_after)
        if pushed_before is not None:
            conditions.append(GitHubRepository.pushed_at_github <= pushed_before)
        if discovered_after is not None:
            conditions.append(GitHubRepository.discovered_at >= discovered_after)
        if discovered_before is not None:
            conditions.append(GitHubRepository.discovered_at <= discovered_before)
        return conditions, ordered, tie_breaker

    def list_jobs(self, *, limit: int = 20) -> list[CrawlJob]:
        stmt = select(CrawlJob).order_by(CrawlJob.created_at.desc()).limit(limit)
        return list(self.session.scalars(stmt))

    def list_partitions(self, job_id: str) -> list[CrawlPartition]:
        return self._list_partitions(job_id)

    def list_job_repositories(self, job_id: str, *, limit: int = 50) -> list[dict]:
        stmt = (
            select(GitHubRepository, RepoDiscovery, CrawlPartition)
            .join(RepoDiscovery, RepoDiscovery.repository_id == GitHubRepository.id)
            .join(CrawlPartition, CrawlPartition.id == RepoDiscovery.partition_id)
            .where(RepoDiscovery.job_id == job_id)
            .order_by(RepoDiscovery.discovered_at.desc())
            .limit(limit)
        )
        rows = self.session.execute(stmt).all()
        return [
            {
                "repository": repository,
                "discovery": discovery,
                "partition": partition,
            }
            for repository, discovery, partition in rows
        ]

    def list_partition_query_audits(self, job_id: str) -> dict[str, list[dict]]:
        partitions = self._list_partitions(job_id)
        if not partitions:
            return {}

        partition_ids = [partition.id for partition in partitions]
        execution_rows = list(
            self.session.scalars(
                select(CrawlPartitionQueryExecution)
                .where(CrawlPartitionQueryExecution.partition_id.in_(partition_ids))
                .order_by(CrawlPartitionQueryExecution.partition_id.asc(), CrawlPartitionQueryExecution.query_index.asc())
            )
        )

        grouped_executions: dict[str, list[dict]] = {}
        for row in execution_rows:
            grouped_executions.setdefault(row.partition_id, []).append(
                {
                    "query_index": row.query_index,
                    "query_string": row.query_string,
                    "status": row.status,
                    "reported_total_count": row.reported_total_count,
                    "current_page": row.current_page,
                    "total_pages": row.total_pages,
                    "fetched_count": row.fetched_count,
                    "unique_count": row.unique_count,
                    "error_message": row.error_message,
                    "started_at": serialize_datetime(row.started_at),
                    "finished_at": serialize_datetime(row.finished_at),
                }
            )

        missing_partition_ids = [partition.id for partition in partitions if partition.id not in grouped_executions]
        if not missing_partition_ids:
            return grouped_executions

        unique_counts = {
            (partition_id, query_string): count
            for partition_id, query_string, count in self.session.execute(
                select(RepoDiscovery.partition_id, RepoDiscovery.query_string, func.count(RepoDiscovery.id))
                .where(RepoDiscovery.partition_id.in_(missing_partition_ids))
                .group_by(RepoDiscovery.partition_id, RepoDiscovery.query_string)
            ).all()
        }

        fallback_status_map = {
            CrawlPartitionStatus.completed.value: CrawlPartitionQueryStatus.completed.value,
            CrawlPartitionStatus.skipped.value: CrawlPartitionQueryStatus.completed.value,
            CrawlPartitionStatus.failed.value: CrawlPartitionQueryStatus.failed.value,
            CrawlPartitionStatus.overflow.value: CrawlPartitionQueryStatus.failed.value,
        }

        for partition in partitions:
            if partition.id in grouped_executions:
                continue
            grouped_executions[partition.id] = []
            query_lines = [line for line in partition.query_string.splitlines() if line.strip()]
            fallback_status = fallback_status_map.get(partition.status, CrawlPartitionQueryStatus.pending.value)
            for query_index, query in enumerate(query_lines):
                grouped_executions[partition.id].append(
                    {
                        "query_index": query_index,
                        "query_string": query,
                        "status": fallback_status,
                        "reported_total_count": None,
                        "current_page": None,
                        "total_pages": None,
                        "fetched_count": None,
                        "unique_count": unique_counts.get((partition.id, query), 0),
                        "error_message": partition.error_message if fallback_status == CrawlPartitionQueryStatus.failed.value else None,
                        "started_at": serialize_datetime(partition.started_at),
                        "finished_at": serialize_datetime(partition.finished_at),
                    }
                )

        return grouped_executions

    def reconcile_job(
        self,
        job: CrawlJob,
        *,
        is_running: bool = False,
        is_scheduled: bool = False,
    ) -> bool:
        partitions = self._list_partitions(job.id)
        changed = False

        if not is_running:
            for partition in partitions:
                if partition.status == CrawlPartitionStatus.running.value:
                    partition.status = CrawlPartitionStatus.pending.value
                    partition.finished_at = None
                    changed = True
            if self._reconcile_query_executions(partitions):
                changed = True

        statuses = {partition.status for partition in partitions}
        has_pending = CrawlPartitionStatus.pending.value in statuses
        has_failed_partition = CrawlPartitionStatus.failed.value in statuses
        has_overflow = CrawlPartitionStatus.overflow.value in statuses
        has_completed_or_skipped = bool(
            statuses
            & {
                CrawlPartitionStatus.completed.value,
                CrawlPartitionStatus.skipped.value,
            }
        )

        preserve_paused = (
            job.status == CrawlJobStatus.paused.value
            and not is_running
            and has_pending
        )

        planning_progress = self._get_planning_progress(job.stats_json)
        planning_checkpoint = self._get_planning_checkpoint(job.stats_json)

        next_status = job.status
        next_finished_at = job.finished_at
        next_error_message = job.error_message

        if is_running:
            if planning_checkpoint is not None:
                next_status = CrawlJobStatus.planning.value
            elif job.status == CrawlJobStatus.queued.value:
                next_status = CrawlJobStatus.planning.value if not partitions else CrawlJobStatus.running.value
            elif job.status == CrawlJobStatus.planning.value:
                next_status = CrawlJobStatus.planning.value
            else:
                next_status = CrawlJobStatus.running.value
            next_finished_at = None
            next_error_message = None
        elif not statuses:
            if job.status == CrawlJobStatus.queued.value:
                next_status = CrawlJobStatus.queued.value if is_scheduled else CrawlJobStatus.pending.value
                next_finished_at = None
            elif job.status in {
                CrawlJobStatus.pending.value,
                CrawlJobStatus.planning.value,
                CrawlJobStatus.running.value,
                CrawlJobStatus.paused.value,
            }:
                # If the runner context was lost before any partitions were persisted,
                # treat the job as resumable rather than completed.
                next_status = CrawlJobStatus.pending.value
                next_finished_at = None
            elif job.status == CrawlJobStatus.failed.value:
                next_status = CrawlJobStatus.failed.value
                next_finished_at = job.finished_at or datetime.now(UTC)
            elif job.status == CrawlJobStatus.partial.value:
                next_status = CrawlJobStatus.partial.value
                next_finished_at = job.finished_at or datetime.now(UTC)
            elif job.status == CrawlJobStatus.completed.value:
                next_status = CrawlJobStatus.completed.value
                next_finished_at = job.finished_at or datetime.now(UTC)
            elif job.started_at is None:
                next_status = CrawlJobStatus.pending.value
                next_finished_at = None
            else:
                next_status = CrawlJobStatus.pending.value
                next_finished_at = None
        elif job.status == CrawlJobStatus.failed.value and not has_failed_partition:
            has_terminal_progress = has_completed_or_skipped or has_overflow
            next_status = CrawlJobStatus.partial.value if has_terminal_progress else CrawlJobStatus.failed.value
            next_finished_at = job.finished_at or datetime.now(UTC)
        elif statuses <= {CrawlPartitionStatus.completed.value, CrawlPartitionStatus.skipped.value}:
            next_status = CrawlJobStatus.completed.value
            next_finished_at = job.finished_at or datetime.now(UTC)
            next_error_message = None
        elif has_pending and not has_failed_partition:
            next_status = CrawlJobStatus.paused.value if preserve_paused else CrawlJobStatus.pending.value
            next_finished_at = job.finished_at if preserve_paused else None
            next_error_message = None
        elif has_failed_partition:
            has_progress = bool(
                statuses
                & {
                    CrawlPartitionStatus.completed.value,
                    CrawlPartitionStatus.skipped.value,
                    CrawlPartitionStatus.pending.value,
                    CrawlPartitionStatus.overflow.value,
                }
            )
            next_status = CrawlJobStatus.partial.value if has_progress else CrawlJobStatus.failed.value
            next_finished_at = job.finished_at or datetime.now(UTC)
        elif has_overflow:
            next_status = CrawlJobStatus.partial.value
            next_finished_at = job.finished_at or datetime.now(UTC)
        else:
            next_status = CrawlJobStatus.running.value
            next_finished_at = None
            next_error_message = None

        if job.status != next_status:
            job.status = next_status
            changed = True
        if job.finished_at != next_finished_at:
            job.finished_at = next_finished_at
            changed = True
        if job.error_message != next_error_message:
            job.error_message = next_error_message
            changed = True

        keep_planning_progress = (
            planning_progress
            if planning_progress is not None
            and (
                next_status == CrawlJobStatus.planning.value
                or (
                    planning_checkpoint is not None
                    and next_status not in {
                        CrawlJobStatus.running.value,
                        CrawlJobStatus.completed.value,
                    }
                )
            )
            else None
        )
        keep_planning_checkpoint = (
            planning_checkpoint
            if planning_checkpoint is not None
            and next_status not in {
                CrawlJobStatus.running.value,
                CrawlJobStatus.completed.value,
            }
            else None
        )
        next_stats = self._compose_job_stats(
            job.id,
            stats_json=job.stats_json,
            planning_progress=keep_planning_progress,
            planning_checkpoint=keep_planning_checkpoint,
        )
        if job.stats_json != next_stats:
            job.stats_json = next_stats
            changed = True

        return changed

    def _reconcile_query_executions(self, partitions: list[CrawlPartition]) -> bool:
        if not partitions:
            return False

        partition_map = {partition.id: partition for partition in partitions}
        partition_ids = list(partition_map)
        running_rows = list(
            self.session.scalars(
                select(CrawlPartitionQueryExecution)
                .where(CrawlPartitionQueryExecution.partition_id.in_(partition_ids))
                .where(CrawlPartitionQueryExecution.status == CrawlPartitionQueryStatus.running.value)
            )
        )
        if not running_rows:
            return False

        changed = False
        completed_partition_statuses = {
            CrawlPartitionStatus.completed.value,
            CrawlPartitionStatus.skipped.value,
        }
        failed_partition_statuses = {
            CrawlPartitionStatus.failed.value,
            CrawlPartitionStatus.overflow.value,
        }

        for row in running_rows:
            partition = partition_map[row.partition_id]
            if partition.status in completed_partition_statuses:
                next_status = CrawlPartitionQueryStatus.completed.value
                next_finished_at = partition.finished_at or row.finished_at or datetime.now(UTC)
                next_error_message = None
            elif partition.status in failed_partition_statuses:
                next_status = CrawlPartitionQueryStatus.failed.value
                next_finished_at = partition.finished_at or row.finished_at or datetime.now(UTC)
                next_error_message = partition.error_message
            else:
                next_status = CrawlPartitionQueryStatus.pending.value
                next_finished_at = None
                next_error_message = None

            if row.status != next_status:
                row.status = next_status
                changed = True
            if row.finished_at != next_finished_at:
                row.finished_at = next_finished_at
                changed = True
            if row.error_message != next_error_message:
                row.error_message = next_error_message
                changed = True
            next_current_page = row.current_page if next_status != CrawlPartitionQueryStatus.pending.value else None
            if row.current_page != next_current_page:
                row.current_page = next_current_page
                changed = True
            next_total_pages = row.total_pages if next_status != CrawlPartitionQueryStatus.pending.value else None
            if row.total_pages != next_total_pages:
                row.total_pages = next_total_pages
                changed = True

        return changed

    def delete_job(self, job_id: str, *, delete_orphan_repositories: bool = True) -> dict[str, int]:
        job = self.get_job(job_id)

        repository_ids = list(
            self.session.scalars(
                select(RepoDiscovery.repository_id)
                .where(RepoDiscovery.job_id == job_id)
                .distinct()
            )
        )

        deleted_discoveries = self.session.execute(
            delete(RepoDiscovery).where(RepoDiscovery.job_id == job_id)
        ).rowcount or 0
        deleted_query_executions = self.session.execute(
            delete(CrawlPartitionQueryExecution).where(
                CrawlPartitionQueryExecution.partition_id.in_(
                    select(CrawlPartition.id).where(CrawlPartition.job_id == job_id)
                )
            )
        ).rowcount or 0
        deleted_partitions = self.session.execute(
            delete(CrawlPartition).where(CrawlPartition.job_id == job_id)
        ).rowcount or 0
        deleted_jobs = self.session.execute(
            delete(CrawlJob).where(CrawlJob.id == job.id)
        ).rowcount or 0

        deleted_repositories = (
            self._delete_orphan_repositories(repository_ids)
            if delete_orphan_repositories
            else 0
        )

        self.session.flush()
        return {
            "deleted_jobs": int(deleted_jobs),
            "deleted_partitions": int(deleted_partitions),
            "deleted_discoveries": int(deleted_discoveries),
            "deleted_query_executions": int(deleted_query_executions),
            "deleted_repositories": int(deleted_repositories),
        }

    def _run_partition(
        self,
        job: CrawlJob,
        partition: CrawlPartition,
        *,
        should_pause: Callable[[], bool] | None = None,
        persist_progress: Callable[[], None] | None = None,
    ) -> bool:
        filters = CrawlFilters.model_validate(job.filters_json)
        partition_state = TimePartition(
            start=partition.range_start,
            end=partition.range_end,
            depth=partition.depth,
            expected_count=partition.expected_count,
        )
        queries = build_search_queries(filters, partition_state)
        self._clear_partition_discoveries(partition)
        query_executions = self._ensure_partition_query_executions(partition, queries, reset=True)
        partition.status = CrawlPartitionStatus.running.value
        partition.error_message = None
        partition.started_at = datetime.now(UTC)
        partition.finished_at = None
        self.session.flush()
        if persist_progress is not None:
            persist_progress()

        active_query_execution: CrawlPartitionQueryExecution | None = None
        paused_during_partition = False
        try:
            page_size = self.settings.github_page_size
            fetched_count = 0
            observed_total = 0
            partition_complete = True

            for query_execution, query in zip(query_executions, queries, strict=False):
                if self._repository_limit_reached(job):
                    break
                if should_pause and should_pause():
                    partition_complete = False
                    paused_during_partition = True
                    break

                active_query_execution = query_execution
                query_execution.status = CrawlPartitionQueryStatus.running.value
                query_execution.started_at = datetime.now(UTC)
                query_execution.finished_at = None
                query_execution.error_message = None
                query_execution.reported_total_count = 0
                query_execution.current_page = 1
                query_execution.total_pages = None
                query_execution.fetched_count = 0
                query_execution.unique_count = 0
                self.session.flush()
                if persist_progress is not None:
                    persist_progress()
                page = 1
                query_total_pages: int | None = None
                while True:
                    if self._repository_limit_reached(job):
                        break
                    if should_pause and should_pause():
                        partition_complete = False
                        paused_during_partition = True
                        break

                    query_execution.current_page = page
                    self.session.flush()
                    if persist_progress is not None:
                        persist_progress()

                    response = self.client.search_repositories(
                        query,
                        page=page,
                        per_page=page_size,
                        sort=filters.sort,
                        order=filters.order,
                    )
                    if page == 1:
                        observed_total += response.total_count
                        query_total_pages = max(math.ceil(min(response.total_count, 1000) / page_size), 1)
                        query_execution.reported_total_count = response.total_count
                    if query_total_pages is not None:
                        query_execution.total_pages = query_total_pages

                    page_unique = self._persist_search_items(job, partition, response.items, query=query)
                    fetched_this_page = len(response.items)
                    fetched_count += fetched_this_page
                    query_execution.unique_count = (query_execution.unique_count or 0) + page_unique
                    query_execution.fetched_count = (query_execution.fetched_count or 0) + fetched_this_page
                    self.session.flush()
                    if persist_progress is not None:
                        persist_progress()

                    if self._repository_limit_reached(job):
                        break
                    if not response.items:
                        break
                    if query_total_pages is not None and page >= query_total_pages:
                        break
                    page += 1

                if partition_complete:
                    query_execution.status = CrawlPartitionQueryStatus.completed.value
                    query_execution.finished_at = datetime.now(UTC)
                else:
                    query_execution.status = CrawlPartitionQueryStatus.pending.value
                    query_execution.finished_at = None

                if not partition_complete:
                    break
                if self._repository_limit_reached(job):
                    break

            if partition_complete and self._repository_limit_reached(job):
                self._complete_pending_query_executions_for_repository_limit(query_executions)

            if partition.expected_count == 0 and observed_total:
                partition.expected_count = observed_total

            partition.fetched_count = fetched_count
            partition.status = (
                CrawlPartitionStatus.completed.value if partition_complete else CrawlPartitionStatus.pending.value
            )
            partition.finished_at = datetime.now(UTC) if partition_complete else None
            self._refresh_partition_discovery_counts(partition, query_executions)
        except Exception as exc:
            if (
                active_query_execution is not None
                and active_query_execution.status == CrawlPartitionQueryStatus.running.value
            ):
                active_query_execution.status = CrawlPartitionQueryStatus.failed.value
                active_query_execution.error_message = str(exc)
                active_query_execution.finished_at = datetime.now(UTC)
            partition.status = CrawlPartitionStatus.failed.value
            partition.error_message = str(exc)
            if partition.expected_count == 0 and observed_total:
                partition.expected_count = observed_total
            partition.fetched_count = fetched_count
            partition.finished_at = datetime.now(UTC)
            self._refresh_partition_discovery_counts(partition, query_executions)
            self.session.flush()
            if persist_progress is not None:
                persist_progress()
            raise
        return paused_during_partition

    def _clear_partition_discoveries(self, partition: CrawlPartition) -> None:
        repository_ids = list(
            self.session.scalars(
                select(RepoDiscovery.repository_id)
                .where(RepoDiscovery.partition_id == partition.id)
                .distinct()
            )
        )
        if not repository_ids:
            return
        self.session.execute(delete(RepoDiscovery).where(RepoDiscovery.partition_id == partition.id))
        self._delete_orphan_repositories(repository_ids)
        self.session.flush()

    def _persist_search_items(
        self,
        job: CrawlJob,
        partition: CrawlPartition,
        items: Iterable[dict],
        *,
        query: str,
    ) -> int:
        observed_at = datetime.now(UTC)
        unique_items: dict[int, dict[str, Any]] = {}
        for item in items:
            github_repo_id = int(item["id"])
            if github_repo_id not in unique_items:
                unique_items[github_repo_id] = item

        if not unique_items:
            return 0

        remaining = self._repository_limit_remaining(job)
        if remaining is not None:
            if remaining <= 0:
                return 0
            existing_github_repo_ids = set(
                self.session.scalars(
                    select(GitHubRepository.github_repo_id)
                    .join(RepoDiscovery, RepoDiscovery.repository_id == GitHubRepository.id)
                    .where(RepoDiscovery.job_id == job.id)
                    .where(GitHubRepository.github_repo_id.in_(list(unique_items)))
                )
            )
            limited_items: dict[int, dict[str, Any]] = {}
            for github_repo_id, item in unique_items.items():
                if github_repo_id in existing_github_repo_ids:
                    continue
                limited_items[github_repo_id] = item
                if len(limited_items) >= remaining:
                    break
            unique_items = limited_items
            if not unique_items:
                return 0

        repository_rows = [
            self._build_repository_upsert_row(item, observed_at=observed_at)
            for item in unique_items.values()
        ]
        repository_id_map = self._upsert_repositories(repository_rows)

        discovery_rows = [
            {
                "job_id": job.id,
                "partition_id": partition.id,
                "repository_id": repository_id_map[github_repo_id],
                "query_string": query,
                "discovered_at": observed_at,
                "raw_payload": item,
            }
            for github_repo_id, item in unique_items.items()
        ]
        return self._insert_discoveries(discovery_rows)

    def upsert_repository_items(self, items: Iterable[dict[str, Any]]) -> list[GitHubRepository]:
        observed_at = datetime.now(UTC)
        unique_items: dict[int, dict[str, Any]] = {}
        for item in items:
            unique_items[int(item["id"])] = item
        if not unique_items:
            return []

        repository_rows = [
            self._build_repository_upsert_row(item, observed_at=observed_at)
            for item in unique_items.values()
        ]
        repository_id_map = self._upsert_repositories(repository_rows)
        repository_ids = list(repository_id_map.values())
        if not repository_ids:
            return []
        return list(
            self.session.scalars(
                select(GitHubRepository)
                .where(GitHubRepository.id.in_(repository_ids))
                .order_by(GitHubRepository.full_name.asc())
            )
        )

    def _build_repository_upsert_row(
        self,
        item: dict[str, Any],
        *,
        observed_at: datetime,
    ) -> dict[str, Any]:
        return {
            "github_repo_id": int(item["id"]),
            "node_id": item.get("node_id"),
            "full_name": item.get("full_name", ""),
            "owner_login": (item.get("owner") or {}).get("login", ""),
            "name": item.get("name", ""),
            "html_url": item.get("html_url", ""),
            "api_url": item.get("url", ""),
            "description": item.get("description"),
            "homepage": item.get("homepage"),
            "default_branch": item.get("default_branch"),
            "primary_language": item.get("language"),
            "license_key": (item.get("license") or {}).get("key"),
            "visibility": item.get("visibility"),
            "is_private": bool(item.get("private", False)),
            "is_fork": bool(item.get("fork", False)),
            "is_archived": bool(item.get("archived", False)),
            "stargazers_count": int(item.get("stargazers_count") or 0),
            "forks_count": int(item.get("forks_count") or 0),
            "open_issues_count": int(item.get("open_issues_count") or 0),
            "created_at_github": parse_github_datetime(item.get("created_at")),
            "updated_at_github": parse_github_datetime(item.get("updated_at")),
            "pushed_at_github": parse_github_datetime(item.get("pushed_at")),
            "discovered_at": observed_at,
            "last_seen_at": observed_at,
            "raw_payload": item,
        }

    def _upsert_repositories(self, rows: list[dict[str, Any]]) -> dict[int, int]:
        if not rows:
            return {}

        base_insert = self._build_insert_statement(GitHubRepository)
        stmt = base_insert.values(rows).on_conflict_do_update(
            index_elements=[GitHubRepository.github_repo_id],
            set_={
                "node_id": base_insert.excluded.node_id,
                "full_name": base_insert.excluded.full_name,
                "owner_login": base_insert.excluded.owner_login,
                "name": base_insert.excluded.name,
                "html_url": base_insert.excluded.html_url,
                "api_url": base_insert.excluded.api_url,
                "description": base_insert.excluded.description,
                "homepage": base_insert.excluded.homepage,
                "default_branch": base_insert.excluded.default_branch,
                "primary_language": base_insert.excluded.primary_language,
                "license_key": base_insert.excluded.license_key,
                "visibility": base_insert.excluded.visibility,
                "is_private": base_insert.excluded.is_private,
                "is_fork": base_insert.excluded.is_fork,
                "is_archived": base_insert.excluded.is_archived,
                "stargazers_count": base_insert.excluded.stargazers_count,
                "forks_count": base_insert.excluded.forks_count,
                "open_issues_count": base_insert.excluded.open_issues_count,
                "created_at_github": base_insert.excluded.created_at_github,
                "updated_at_github": base_insert.excluded.updated_at_github,
                "pushed_at_github": base_insert.excluded.pushed_at_github,
                "last_seen_at": base_insert.excluded.last_seen_at,
                "raw_payload": base_insert.excluded.raw_payload,
            },
        )
        self.session.execute(stmt)

        github_repo_ids = [int(row["github_repo_id"]) for row in rows]
        return {
            github_repo_id: repository_id
            for github_repo_id, repository_id in self.session.execute(
                select(GitHubRepository.github_repo_id, GitHubRepository.id).where(
                    GitHubRepository.github_repo_id.in_(github_repo_ids)
                )
            ).all()
        }

    def _insert_discoveries(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0

        base_insert = self._build_insert_statement(RepoDiscovery)
        stmt = base_insert.values(rows).on_conflict_do_nothing(
            index_elements=[RepoDiscovery.job_id, RepoDiscovery.repository_id]
        ).returning(RepoDiscovery.repository_id)
        inserted_repository_ids = self.session.execute(stmt).scalars().all()
        return len(inserted_repository_ids)

    def _delete_orphan_repositories(self, repository_ids: list[int]) -> int:
        if not repository_ids:
            return 0

        unique_repository_ids = list(dict.fromkeys(int(repository_id) for repository_id in repository_ids))
        discovery_exists = (
            select(RepoDiscovery.id)
            .where(RepoDiscovery.repository_id == GitHubRepository.id)
            .exists()
        )
        stage2_run_exists = (
            select(Stage2Run.id)
            .where(Stage2Run.repository_id == GitHubRepository.id)
            .exists()
        )
        stage3_snapshot_exists = (
            select(Stage3CommitSnapshot.id)
            .where(Stage3CommitSnapshot.repository_id == GitHubRepository.id)
            .exists()
        )
        data_pool_asset_exists = (
            select(DataPoolAsset.id)
            .where(DataPoolAsset.repository_id == GitHubRepository.id)
            .exists()
        )
        batch_task_repository_exists = (
            select(BatchTaskRepository.id)
            .where(BatchTaskRepository.repository_id == GitHubRepository.id)
            .exists()
        )

        deleted_count = 0
        for offset in range(0, len(unique_repository_ids), REPOSITORY_ID_FILTER_BATCH_SIZE):
            repository_id_batch = unique_repository_ids[offset : offset + REPOSITORY_ID_FILTER_BATCH_SIZE]
            orphan_ids = list(
                self.session.scalars(
                    select(GitHubRepository.id)
                    .where(GitHubRepository.id.in_(repository_id_batch))
                    .where(~discovery_exists)
                    .where(~stage2_run_exists)
                    .where(~stage3_snapshot_exists)
                    .where(~data_pool_asset_exists)
                    .where(~batch_task_repository_exists)
                )
            )
            if not orphan_ids:
                continue
            deleted_count += (
                self.session.execute(
                    delete(GitHubRepository).where(GitHubRepository.id.in_(orphan_ids))
                ).rowcount
                or 0
            )
        return deleted_count

    def _build_insert_statement(self, model):
        bind = self.session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else None
        if dialect_name == "sqlite":
            return sqlite_insert(model)
        if dialect_name == "postgresql":
            return postgresql_insert(model)
        raise RuntimeError(f"unsupported database dialect for stage1 upserts: {dialect_name}")

    def _ensure_partition_query_executions(
        self,
        partition: CrawlPartition,
        queries: list[str],
        *,
        reset: bool = False,
    ) -> list[CrawlPartitionQueryExecution]:
        existing_rows = list(
            self.session.scalars(
                select(CrawlPartitionQueryExecution)
                .where(CrawlPartitionQueryExecution.partition_id == partition.id)
                .order_by(CrawlPartitionQueryExecution.query_index.asc())
            )
        )

        needs_rebuild = len(existing_rows) != len(queries) or any(
            row.query_index != index or row.query_string != query
            for index, (row, query) in enumerate(zip(existing_rows, queries, strict=False))
        )

        if needs_rebuild:
            self.session.execute(
                delete(CrawlPartitionQueryExecution).where(CrawlPartitionQueryExecution.partition_id == partition.id)
            )
            existing_rows = []

        if not existing_rows:
            existing_rows = [
                CrawlPartitionQueryExecution(
                    partition_id=partition.id,
                    query_index=index,
                    query_string=query,
                    status=CrawlPartitionQueryStatus.pending.value,
                )
                for index, query in enumerate(queries)
            ]
            self.session.add_all(existing_rows)
            self.session.flush()

        if reset:
            for row in existing_rows:
                row.status = CrawlPartitionQueryStatus.pending.value
                row.reported_total_count = 0
                row.current_page = None
                row.total_pages = None
                row.fetched_count = 0
                row.unique_count = 0
                row.error_message = None
                row.started_at = None
                row.finished_at = None
            self.session.flush()

        return existing_rows

    def _refresh_partition_discovery_counts(
        self,
        partition: CrawlPartition,
        query_executions: list[CrawlPartitionQueryExecution],
    ) -> None:
        partition.unique_count = int(
            self.session.scalar(
                select(func.count(RepoDiscovery.id)).where(RepoDiscovery.partition_id == partition.id)
            )
            or 0
        )
        query_unique_counts = {
            query_string: int(count)
            for query_string, count in self.session.execute(
                select(RepoDiscovery.query_string, func.count(RepoDiscovery.id))
                .where(RepoDiscovery.partition_id == partition.id)
                .group_by(RepoDiscovery.query_string)
            ).all()
        }
        for query_execution in query_executions:
            query_execution.unique_count = query_unique_counts.get(query_execution.query_string, 0)

    def _list_partitions(self, job_id: str) -> list[CrawlPartition]:
        stmt = (
            select(CrawlPartition)
            .where(CrawlPartition.job_id == job_id)
            .order_by(CrawlPartition.range_start.asc(), CrawlPartition.range_end.asc())
        )
        return list(self.session.scalars(stmt))

    def _get_planning_checkpoint(self, stats_json: dict | None) -> PartitionPlanningCheckpoint | None:
        stats = _clone_stats(stats_json)
        raw_checkpoint = stats.get("planning_checkpoint")
        if not isinstance(raw_checkpoint, dict):
            return None
        raw_windows = raw_checkpoint.get("queued_windows")
        if not isinstance(raw_windows, list):
            return None
        try:
            queued_windows = tuple(TimePartition.model_validate(item) for item in raw_windows)
            return PartitionPlanningCheckpoint(
                queued_windows=queued_windows,
                processed_windows=int(raw_checkpoint.get("processed_windows", 0)),
                split_windows=int(raw_checkpoint.get("split_windows", 0)),
                planned_partitions=int(raw_checkpoint.get("planned_partitions", 0)),
                empty_windows=int(raw_checkpoint.get("empty_windows", 0)),
                probe_count=int(raw_checkpoint.get("probe_count", 0)),
            )
        except Exception:
            return None

    def _get_planning_progress(self, stats_json: dict | None) -> dict | None:
        stats = _clone_stats(stats_json)
        planning_progress = stats.get("planning_progress")
        if isinstance(planning_progress, dict):
            return dict(planning_progress)
        return None

    def _build_planning_progress(
        self,
        stats_json: dict | None,
        *,
        filters: CrawlFilters,
        checkpoint: PartitionPlanningCheckpoint | None,
    ) -> dict:
        existing_progress = self._get_planning_progress(stats_json) or {}
        now = datetime.now(UTC)
        if checkpoint is None:
            return {
                "phase": "planning",
                "event": "starting",
                "processed_windows": 0,
                "discovered_windows": 1,
                "queued_windows": 1,
                "split_windows": 0,
                "planned_partitions": 0,
                "overflow_windows": int(existing_progress.get("overflow_windows") or 0),
                "empty_windows": 0,
                "probe_count": 0,
                "count_query_calls": int(existing_progress.get("count_query_calls") or 0),
                "current_depth": 0,
                "current_range_start": serialize_datetime(filters.created_after),
                "current_range_end": serialize_datetime(filters.created_before),
                "current_query_index": None,
                "current_query_total": None,
                "current_query_string": None,
                "last_estimated_count": existing_progress.get("last_estimated_count"),
                "progress_ratio": 0.0,
                "updated_at": serialize_datetime(now),
            }

        next_window = checkpoint.queued_windows[-1] if checkpoint.queued_windows else None
        discovered_windows = checkpoint.processed_windows + len(checkpoint.queued_windows)
        return {
            "phase": "planning",
            "event": "resuming",
            "processed_windows": checkpoint.processed_windows,
            "discovered_windows": discovered_windows,
            "queued_windows": len(checkpoint.queued_windows),
            "split_windows": checkpoint.split_windows,
            "planned_partitions": checkpoint.planned_partitions,
            "overflow_windows": int(existing_progress.get("overflow_windows") or 0),
            "empty_windows": checkpoint.empty_windows,
            "probe_count": checkpoint.probe_count,
            "count_query_calls": int(existing_progress.get("count_query_calls") or 0),
            "current_depth": next_window.depth if next_window is not None else 0,
            "current_range_start": serialize_datetime(next_window.start if next_window is not None else filters.created_after),
            "current_range_end": serialize_datetime(next_window.end if next_window is not None else filters.created_before),
            "current_query_index": None,
            "current_query_total": None,
            "current_query_string": None,
            "last_estimated_count": existing_progress.get("last_estimated_count"),
            "progress_ratio": (
                checkpoint.processed_windows / discovered_windows if discovered_windows else 0.0
            ),
            "updated_at": serialize_datetime(now),
        }

    def _serialize_planning_checkpoint(
        self,
        checkpoint: PartitionPlanningCheckpoint | None,
    ) -> dict | None:
        if checkpoint is None:
            return None
        return {
            "queued_windows": [
                {
                    "start": serialize_datetime(window.start),
                    "end": serialize_datetime(window.end),
                    "depth": window.depth,
                    "expected_count": window.expected_count,
                }
                for window in checkpoint.queued_windows
            ],
            "processed_windows": checkpoint.processed_windows,
            "split_windows": checkpoint.split_windows,
            "planned_partitions": checkpoint.planned_partitions,
            "empty_windows": checkpoint.empty_windows,
            "probe_count": checkpoint.probe_count,
        }

    def _compose_job_stats(
        self,
        job_id: str,
        *,
        stats_json: dict | None = None,
        planning_progress: dict | None = None,
        planning_checkpoint: PartitionPlanningCheckpoint | None = None,
        max_concurrent_partitions: int | None = None,
    ) -> dict:
        stats = self._build_job_stats(job_id)
        persisted_max_concurrent_partitions = (
            max_concurrent_partitions
            if max_concurrent_partitions is not None
            else self._get_max_concurrent_partitions(stats_json)
        )
        if persisted_max_concurrent_partitions is not None:
            stats["max_concurrent_partitions"] = persisted_max_concurrent_partitions
        if planning_progress is not None:
            stats["planning_progress"] = dict(planning_progress)
        serialized_checkpoint = self._serialize_planning_checkpoint(planning_checkpoint)
        if serialized_checkpoint is not None:
            stats["planning_checkpoint"] = serialized_checkpoint
        return stats

    def _set_job_stats(
        self,
        job: CrawlJob,
        *,
        planning_progress: dict | None = None,
        planning_checkpoint: PartitionPlanningCheckpoint | None = None,
        max_concurrent_partitions: int | None = None,
    ) -> None:
        job.stats_json = self._compose_job_stats(
            job.id,
            stats_json=job.stats_json,
            planning_progress=planning_progress,
            planning_checkpoint=planning_checkpoint,
            max_concurrent_partitions=max_concurrent_partitions,
        )

    def _get_max_concurrent_partitions(self, stats_json: dict | None) -> int | None:
        stats = _clone_stats(stats_json)
        value = stats.get("max_concurrent_partitions")
        try:
            normalized = int(value)
        except (TypeError, ValueError):
            return None
        return normalized if normalized >= 1 else None

    def _persist_planned_partitions(
        self,
        job: CrawlJob,
        filters: CrawlFilters,
        partitions: list[TimePartition],
        *,
        status: str = CrawlPartitionStatus.pending.value,
        error_message: str | None = None,
    ) -> None:
        existing_keys = {
            (partition.range_start, partition.range_end, partition.depth)
            for partition in self._list_partitions(job.id)
        }
        terminal_partition = status in {
            CrawlPartitionStatus.completed.value,
            CrawlPartitionStatus.skipped.value,
            CrawlPartitionStatus.overflow.value,
            CrawlPartitionStatus.failed.value,
        }
        for item in partitions:
            key = (item.start, item.end, item.depth)
            if key in existing_keys:
                continue
            partition_queries = build_search_queries(filters, item)
            partition = CrawlPartition(
                job_id=job.id,
                status=status,
                depth=item.depth,
                range_start=item.start,
                range_end=item.end,
                query_string="\n".join(partition_queries),
                expected_count=item.expected_count,
                error_message=error_message,
                finished_at=datetime.now(UTC) if terminal_partition else None,
            )
            self.session.add(partition)
            for query_index, query in enumerate(partition_queries):
                partition.query_executions.append(
                    CrawlPartitionQueryExecution(
                        query_index=query_index,
                        query_string=query,
                        status=(
                            CrawlPartitionQueryStatus.failed.value
                            if status == CrawlPartitionStatus.overflow.value
                            else CrawlPartitionQueryStatus.pending.value
                        ),
                        error_message=error_message if status == CrawlPartitionStatus.overflow.value else None,
                        finished_at=datetime.now(UTC) if status == CrawlPartitionStatus.overflow.value else None,
                    )
                )
            existing_keys.add(key)
        self.session.flush()

    def _ensure_target_repository_partitions(
        self,
        job: CrawlJob,
        targets: list[str],
    ) -> list[CrawlPartition]:
        existing_by_target = {
            partition.query_string.strip(): partition
            for partition in self._list_partitions(job.id)
            if partition.query_string.strip()
        }
        ordered: list[CrawlPartition] = []
        now = datetime.now(UTC)
        for index, target in enumerate(targets):
            partition = existing_by_target.get(target)
            if partition is None:
                partition = CrawlPartition(
                    job_id=job.id,
                    status=CrawlPartitionStatus.pending.value,
                    depth=index,
                    range_start=now,
                    range_end=now,
                    query_string=target,
                    expected_count=1,
                )
                self.session.add(partition)
                self.session.flush()
            self._ensure_partition_query_executions(partition, [target], reset=False)
            ordered.append(partition)
        self.session.flush()
        return ordered

    def _build_job_stats(self, job_id: str) -> dict:
        partition_counts = dict(
            self.session.execute(
                select(CrawlPartition.status, func.count(CrawlPartition.id))
                .where(CrawlPartition.job_id == job_id)
                .group_by(CrawlPartition.status)
            ).all()
        )
        unique_repositories = self.session.scalar(
            select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job_id)
        ) or 0
        total_partitions = self.session.scalar(
            select(func.count(CrawlPartition.id)).where(CrawlPartition.job_id == job_id)
        ) or 0
        fetched_count = self.session.scalar(
            select(func.coalesce(func.sum(CrawlPartition.fetched_count), 0)).where(CrawlPartition.job_id == job_id)
        ) or 0
        return {
            "total_partitions": total_partitions,
            "partition_status_counts": partition_counts,
            "repository_hits": int(fetched_count),
            "unique_repositories": int(unique_repositories),
        }
