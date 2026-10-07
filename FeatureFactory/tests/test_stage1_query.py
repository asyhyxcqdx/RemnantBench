from datetime import UTC, datetime

from feature_factory.stage1.query import build_search_queries, build_search_query
from feature_factory.stage1.schemas import CrawlFilters, TimePartition


def test_build_search_query_includes_core_qualifiers() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, tzinfo=UTC),
        created_before=datetime(2024, 1, 2, tzinfo=UTC),
        pushed_after=datetime(2024, 1, 10, 12, 0, tzinfo=UTC),
        stars_min=10,
        exclude_forks=True,
        exclude_archived=True,
        licenses=["mit"],
        keywords=["pytest"],
    )
    partition = TimePartition(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )

    query = build_search_query(filters, partition)

    assert "is:public" in query
    assert "language:Python" in query
    assert "fork:false" in query
    assert "archived:false" in query
    assert "created:2024-01-01T00:00:00Z..2024-01-01T12:00:00Z" in query
    assert "pushed:>=2024-01-10T12:00:00Z" in query
    assert "stars:>=10" in query
    assert "license:mit" in query
    assert "pytest" in query


def test_build_search_queries_splits_multi_license_filters_into_multiple_queries() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, tzinfo=UTC),
        created_before=datetime(2024, 1, 2, tzinfo=UTC),
        licenses=["mit", "apache-2.0"],
    )
    partition = TimePartition(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )

    queries = build_search_queries(filters, partition)

    assert len(queries) == 2
    assert any("license:mit" in query for query in queries)
    assert any("license:apache-2.0" in query for query in queries)
    assert all("created:2024-01-01T00:00:00Z..2024-01-01T12:00:00Z" in query for query in queries)


def test_build_search_queries_quotes_keywords_with_whitespace() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, tzinfo=UTC),
        created_before=datetime(2024, 1, 2, tzinfo=UTC),
        keywords=["unit test", "pytest"],
    )
    partition = TimePartition(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )

    query = build_search_queries(filters, partition)[0]

    assert '"unit test"' in query
    assert "pytest" in query


def test_build_search_queries_splits_multi_language_filters_into_multiple_queries() -> None:
    filters = CrawlFilters(
        languages=["Python", "Go"],
        created_after=datetime(2024, 1, 1, tzinfo=UTC),
        created_before=datetime(2024, 1, 2, tzinfo=UTC),
        licenses=["mit", "apache-2.0"],
    )
    partition = TimePartition(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )

    queries = build_search_queries(filters, partition)

    assert len(queries) == 4
    assert any("language:Python" in query and "license:mit" in query for query in queries)
    assert any("language:Python" in query and "license:apache-2.0" in query for query in queries)
    assert any("language:Go" in query and "license:mit" in query for query in queries)
    assert any("language:Go" in query and "license:apache-2.0" in query for query in queries)


def test_crawl_filters_normalize_and_dedupe_licenses_and_keywords() -> None:
    filters = CrawlFilters(
        language="Python",
        created_after=datetime(2024, 1, 1, tzinfo=UTC),
        created_before=datetime(2024, 1, 2, tzinfo=UTC),
        licenses=["MIT", "mit", " Apache-2.0 "],
        keywords=["pytest", " pytest ", "unit test", "Unit Test"],
        target_repositories=[
            "Owner/Repo",
            " owner/repo ",
            "https://github.com/pallets/flask",
        ],
    )
    partition = TimePartition(
        start=datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
        end=datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    )

    assert filters.licenses == ["mit", "apache-2.0"]
    assert filters.keywords == ["pytest", "unit test"]
    assert filters.target_repositories == ["Owner/Repo", "pallets/flask"]

    queries = build_search_queries(filters, partition)

    assert len(queries) == 2
    assert sum("license:mit" in query for query in queries) == 1
    assert sum("license:apache-2.0" in query for query in queries) == 1
    assert all('"unit test"' in query for query in queries)
