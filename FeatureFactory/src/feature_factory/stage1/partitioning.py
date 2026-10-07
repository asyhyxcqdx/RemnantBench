from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from feature_factory.stage1.schemas import CrawlFilters, TimePartition


class PartitionOverflowError(RuntimeError):
    pass


class PartitionPlanningPaused(RuntimeError):
    pass


PlanningProgressCallback = Callable[["PartitionPlanningProgress"], None]


@dataclass(frozen=True, slots=True)
class PartitionCountEstimate:
    total_count: int
    limiting_count: int | None = None


CountEstimator = Callable[[CrawlFilters, TimePartition], int | PartitionCountEstimate]


@dataclass(frozen=True, slots=True)
class PartitionPlanningProgress:
    event: str
    current_partition: TimePartition
    processed_windows: int
    discovered_windows: int
    queued_windows: int
    split_windows: int
    planned_partitions: int
    overflow_windows: int
    empty_windows: int
    probe_count: int
    last_estimated_count: int | None = None


@dataclass(frozen=True, slots=True)
class PartitionPlanningCheckpoint:
    queued_windows: tuple[TimePartition, ...]
    processed_windows: int
    split_windows: int
    planned_partitions: int
    empty_windows: int
    probe_count: int


@dataclass(frozen=True, slots=True)
class PartitionPlanResult:
    partitions: list[TimePartition]
    overflow_partitions: list[TimePartition] = field(default_factory=list)
    checkpoint: PartitionPlanningCheckpoint | None = None


class PartitionPlanner:
    def __init__(
        self,
        count_estimator: CountEstimator,
        partition_result_limit: int = 900,
        probe_max_span_days: int | None = None,
        progress_callback: PlanningProgressCallback | None = None,
    ) -> None:
        self.count_estimator = count_estimator
        self.partition_result_limit = partition_result_limit
        self.progress_callback = progress_callback
        if probe_max_span_days is None or probe_max_span_days <= 0:
            self.probe_max_span_seconds: int | None = None
        else:
            self.probe_max_span_seconds = int(timedelta(days=probe_max_span_days).total_seconds())

    def plan(
        self,
        filters: CrawlFilters,
        *,
        checkpoint: PartitionPlanningCheckpoint | None = None,
        should_pause: Callable[[], bool] | None = None,
    ) -> PartitionPlanResult:
        if checkpoint is None:
            root = TimePartition(start=filters.created_after, end=filters.created_before or filters.created_after)
            stack: list[TimePartition] = [root]
            processed_windows = 0
            split_windows = 0
            planned_partitions = 0
            overflow_partitions: list[TimePartition] = []
            empty_windows = 0
            probe_count = 0
        else:
            stack = [item.model_copy(deep=True) for item in checkpoint.queued_windows]
            processed_windows = checkpoint.processed_windows
            split_windows = checkpoint.split_windows
            planned_partitions = checkpoint.planned_partitions
            overflow_partitions = []
            empty_windows = checkpoint.empty_windows
            probe_count = checkpoint.probe_count

        partitions: list[TimePartition] = []

        while stack:
            if should_pause is not None and should_pause():
                return PartitionPlanResult(
                    partitions=partitions,
                    checkpoint=self._make_checkpoint(
                        stack=stack,
                        processed_windows=processed_windows,
                        split_windows=split_windows,
                        planned_partitions=planned_partitions,
                        empty_windows=empty_windows,
                        probe_count=probe_count,
                    ),
                )
            partition = stack.pop()
            span_seconds = int((partition.end - partition.start).total_seconds())
            if self.probe_max_span_seconds is not None and span_seconds > self.probe_max_span_seconds:
                left, right = self._bisect_partition(partition, span_seconds)
                stack.append(right)
                stack.append(left)
                processed_windows += 1
                split_windows += 1
                self._emit_progress(
                    event="presplit",
                    current_partition=partition,
                    processed_windows=processed_windows,
                    queued_windows=len(stack),
                    split_windows=split_windows,
                    planned_partitions=planned_partitions,
                    overflow_windows=len(overflow_partitions),
                    empty_windows=empty_windows,
                    probe_count=probe_count,
                )
                continue

            try:
                estimate = self._normalize_count_estimate(self.count_estimator(filters, partition))
            except PartitionPlanningPaused:
                stack.append(partition)
                return PartitionPlanResult(
                    partitions=partitions,
                    overflow_partitions=overflow_partitions,
                    checkpoint=self._make_checkpoint(
                        stack=stack,
                        processed_windows=processed_windows,
                        split_windows=split_windows,
                        planned_partitions=planned_partitions,
                        empty_windows=empty_windows,
                        probe_count=probe_count,
                    ),
                )
            probe_count += 1
            count = estimate.total_count
            limiting_count = estimate.total_count if estimate.limiting_count is None else estimate.limiting_count
            if count <= 0:
                processed_windows += 1
                empty_windows += 1
                self._emit_progress(
                    event="empty",
                    current_partition=partition,
                    processed_windows=processed_windows,
                    queued_windows=len(stack),
                    split_windows=split_windows,
                    planned_partitions=planned_partitions,
                    overflow_windows=len(overflow_partitions),
                    empty_windows=empty_windows,
                    probe_count=probe_count,
                    last_estimated_count=count,
                )
                continue

            partition.expected_count = count
            if limiting_count <= self.partition_result_limit:
                partitions.append(partition)
                processed_windows += 1
                planned_partitions += 1
                self._emit_progress(
                    event="planned",
                    current_partition=partition,
                    processed_windows=processed_windows,
                    queued_windows=len(stack),
                    split_windows=split_windows,
                    planned_partitions=planned_partitions,
                    overflow_windows=len(overflow_partitions),
                    empty_windows=empty_windows,
                    probe_count=probe_count,
                    last_estimated_count=count,
                )
                continue

            if span_seconds <= 0:
                overflow_partition = partition.model_copy(deep=True)
                overflow_partition.expected_count = count
                overflow_partitions.append(overflow_partition)
                processed_windows += 1
                self._emit_progress(
                    event="overflow",
                    current_partition=overflow_partition,
                    processed_windows=processed_windows,
                    queued_windows=len(stack),
                    split_windows=split_windows,
                    planned_partitions=planned_partitions,
                    overflow_windows=len(overflow_partitions),
                    empty_windows=empty_windows,
                    probe_count=probe_count,
                    last_estimated_count=count,
                )
                continue

            left, right = self._bisect_partition(partition, span_seconds)
            stack.append(right)
            stack.append(left)
            processed_windows += 1
            split_windows += 1
            self._emit_progress(
                event="split",
                current_partition=partition,
                processed_windows=processed_windows,
                queued_windows=len(stack),
                split_windows=split_windows,
                planned_partitions=planned_partitions,
                overflow_windows=len(overflow_partitions),
                empty_windows=empty_windows,
                probe_count=probe_count,
                last_estimated_count=count,
            )
        partitions.sort(key=lambda item: item.start)
        overflow_partitions.sort(key=lambda item: item.start)
        return PartitionPlanResult(
            partitions=partitions,
            overflow_partitions=overflow_partitions,
            checkpoint=None,
        )

    def _normalize_count_estimate(self, value: int | PartitionCountEstimate) -> PartitionCountEstimate:
        if isinstance(value, PartitionCountEstimate):
            limiting_count = value.total_count if value.limiting_count is None else value.limiting_count
            return PartitionCountEstimate(
                total_count=int(value.total_count),
                limiting_count=int(limiting_count),
            )
        count = int(value)
        return PartitionCountEstimate(total_count=count, limiting_count=count)

    def _bisect_partition(self, partition: TimePartition, span_seconds: int) -> tuple[TimePartition, TimePartition]:
        midpoint = partition.start + timedelta(seconds=span_seconds // 2)
        left = TimePartition(start=partition.start, end=midpoint, depth=partition.depth + 1)
        right = TimePartition(
            start=midpoint + timedelta(seconds=1),
            end=partition.end,
            depth=partition.depth + 1,
        )
        return left, right

    def _emit_progress(
        self,
        *,
        event: str,
        current_partition: TimePartition,
        processed_windows: int,
        queued_windows: int,
        split_windows: int,
        planned_partitions: int,
        overflow_windows: int,
        empty_windows: int,
        probe_count: int,
        last_estimated_count: int | None = None,
    ) -> None:
        if self.progress_callback is None:
            return
        self.progress_callback(
            PartitionPlanningProgress(
                event=event,
                current_partition=current_partition.model_copy(deep=True),
                processed_windows=processed_windows,
                discovered_windows=processed_windows + queued_windows,
                queued_windows=queued_windows,
                split_windows=split_windows,
                planned_partitions=planned_partitions,
                overflow_windows=overflow_windows,
                empty_windows=empty_windows,
                probe_count=probe_count,
                last_estimated_count=last_estimated_count,
            )
        )

    def _make_checkpoint(
        self,
        *,
        stack: list[TimePartition],
        processed_windows: int,
        split_windows: int,
        planned_partitions: int,
        empty_windows: int,
        probe_count: int,
    ) -> PartitionPlanningCheckpoint:
        queued_windows = tuple(item.model_copy(deep=True) for item in stack)
        return PartitionPlanningCheckpoint(
            queued_windows=queued_windows,
            processed_windows=processed_windows,
            split_windows=split_windows,
            planned_partitions=planned_partitions,
            empty_windows=empty_windows,
            probe_count=probe_count,
        )
