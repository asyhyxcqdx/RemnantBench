from datetime import UTC, datetime

from feature_factory.stage1.partitioning import PartitionPlanner
from feature_factory.stage1.schemas import CrawlFilters, TimePartition


def test_partition_planner_splits_until_under_limit() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
    )

    def estimator(_: CrawlFilters, partition: TimePartition) -> int:
        span = int((partition.end - partition.start).total_seconds())
        if span >= 3:
            return 1500
        return 200

    planner = PartitionPlanner(estimator, partition_result_limit=900)
    result = planner.plan(filters)
    partitions = result.partitions

    assert len(partitions) == 4
    assert result.checkpoint is None
    assert partitions[0].start == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert partitions[-1].end == datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC)
    assert all(partition.expected_count <= 900 for partition in partitions)
    for left, right in zip(partitions, partitions[1:]):
        assert (right.start - left.end).total_seconds() == 1


def test_partition_planner_presplits_large_ranges_before_counting() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        created_before=datetime(2024, 1, 10, 0, 0, 0, tzinfo=UTC),
    )
    probed_spans: list[int] = []

    def estimator(_: CrawlFilters, partition: TimePartition) -> int:
        probed_spans.append(int((partition.end - partition.start).total_seconds()))
        return 200

    planner = PartitionPlanner(estimator, partition_result_limit=900, probe_max_span_days=2)
    result = planner.plan(filters)
    partitions = result.partitions

    assert len(partitions) > 1
    assert result.checkpoint is None
    assert probed_spans
    assert all(span <= 2 * 24 * 60 * 60 for span in probed_spans)


def test_partition_planner_records_overflow_windows_without_dropping_safe_windows() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        created_before=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
    )

    def estimator(_: CrawlFilters, partition: TimePartition) -> int:
        key = (
            partition.start.isoformat(),
            partition.end.isoformat(),
        )
        counts = {
            ("2024-01-01T00:00:00+00:00", "2024-01-01T00:00:03+00:00"): 1500,
            ("2024-01-01T00:00:00+00:00", "2024-01-01T00:00:01+00:00"): 200,
            ("2024-01-01T00:00:02+00:00", "2024-01-01T00:00:03+00:00"): 1500,
            ("2024-01-01T00:00:02+00:00", "2024-01-01T00:00:02+00:00"): 1200,
            ("2024-01-01T00:00:03+00:00", "2024-01-01T00:00:03+00:00"): 200,
        }
        return counts[key]

    planner = PartitionPlanner(estimator, partition_result_limit=900)
    result = planner.plan(filters)

    assert result.checkpoint is None
    assert [
        (partition.start, partition.end, partition.expected_count)
        for partition in result.partitions
    ] == [
        (
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
            200,
        ),
        (
            datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
            200,
        ),
    ]
    assert [
        (partition.start, partition.end, partition.expected_count)
        for partition in result.overflow_partitions
    ] == [
        (
            datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
            1200,
        )
    ]
