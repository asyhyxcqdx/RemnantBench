from datetime import UTC, datetime
from threading import Event, Thread

from sqlalchemy import func, select

import feature_factory.stage1.service as stage1_service_module
from feature_factory.config import Settings
from feature_factory.db import build_engine, build_session_factory, init_db
from feature_factory.models import (
    CrawlJobStatus,
    CrawlPartition,
    CrawlPartitionQueryExecution,
    CrawlPartitionStatus,
    DataPool,
    DataPoolAsset,
    GitHubRepository,
    RepoDiscovery,
)
from feature_factory.stage1.github_client import GitHubIncompleteResultsError
from feature_factory.stage1.schemas import CrawlFilters
from feature_factory.stage1.service import CrawlService


class FakeGitHubClient:
    def count_repositories(self, query: str) -> int:
        return 2

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        class Response:
            total_count = 2
            items = [
                {
                    "id": 1,
                    "node_id": "R_1",
                    "full_name": "owner/repo-one",
                    "owner": {"login": "owner"},
                    "name": "repo-one",
                    "html_url": "https://github.com/owner/repo-one",
                    "url": "https://api.github.com/repos/owner/repo-one",
                    "description": "repo one",
                    "default_branch": "main",
                    "language": "Python",
                    "license": {"key": "mit"},
                    "visibility": "public",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 10,
                    "forks_count": 1,
                    "open_issues_count": 0,
                    "created_at": "2024-01-01T00:00:01Z",
                    "updated_at": "2024-01-01T00:00:02Z",
                    "pushed_at": "2024-01-01T00:00:03Z",
                },
                {
                    "id": 2,
                    "node_id": "R_2",
                    "full_name": "owner/repo-two",
                    "owner": {"login": "owner"},
                    "name": "repo-two",
                    "html_url": "https://github.com/owner/repo-two",
                    "url": "https://api.github.com/repos/owner/repo-two",
                    "description": "repo two",
                    "default_branch": "main",
                    "language": "Python",
                    "license": {"key": "apache-2.0"},
                    "visibility": "public",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 5,
                    "forks_count": 0,
                    "open_issues_count": 1,
                    "created_at": "2024-01-01T00:00:04Z",
                    "updated_at": "2024-01-01T00:00:05Z",
                    "pushed_at": "2024-01-01T00:00:06Z",
                },
            ]

        return Response()

    def close(self) -> None:
        return None


class TargetRepositoryClient:
    def __init__(self) -> None:
        self.count_queries: list[str] = []
        self.search_queries: list[str] = []
        self.repository_names: list[str] = []

    def count_repositories(self, query: str) -> int:
        self.count_queries.append(query)
        raise AssertionError("target repository jobs should not count search results")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.search_queries.append(query)
        raise AssertionError("target repository jobs should not use search")

    def get_repository(self, full_name: str) -> dict:
        self.repository_names.append(full_name)
        owner, name = full_name.split("/", 1)
        github_id = 10_000 + len(self.repository_names)
        return {
            "id": github_id,
            "node_id": f"R_target_{github_id}",
            "full_name": full_name,
            "owner": {"login": owner},
            "name": name,
            "html_url": f"https://github.com/{full_name}",
            "url": f"https://api.github.com/repos/{full_name}",
            "description": "target repo",
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
            "created_at": "2024-01-01T00:00:01Z",
            "updated_at": "2024-01-01T00:00:02Z",
            "pushed_at": "2024-01-01T00:00:03Z",
        }

    def close(self) -> None:
        return None


class SplitPartitionClient:
    def count_repositories(self, query: str) -> int:
        if "2024-01-01T00:00:00Z..2024-01-01T00:00:07Z" in query:
            return 1500
        return 200

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        class Response:
            total_count = 2
            items = [
                {
                    "id": abs(hash((query, page))) % 1_000_000,
                    "node_id": "R_split",
                    "full_name": f"owner/{abs(hash(query)) % 1000}-{page}",
                    "owner": {"login": "owner"},
                    "name": "repo-split",
                    "html_url": "https://github.com/owner/repo-split",
                    "url": "https://api.github.com/repos/owner/repo-split",
                    "description": "split repo",
                    "default_branch": "main",
                    "language": "Python",
                    "license": {"key": "mit"},
                    "visibility": "public",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 7,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "created_at": "2024-01-01T00:00:01Z",
                    "updated_at": "2024-01-01T00:00:02Z",
                    "pushed_at": "2024-01-01T00:00:03Z",
                }
            ]

        return Response()

    def close(self) -> None:
        return None


class EarlyExitCountClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def count_repositories(self, query: str) -> int:
        self.queries.append(query)
        if "language:Python" in query and "license:mit" in query:
            return 901
        raise AssertionError(f"unexpected extra count query: {query}")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise AssertionError("search_repositories should not be called during partition planning test")

    def close(self) -> None:
        return None


class MultiVariantSafePlanningClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def count_repositories(self, query: str) -> int:
        self.queries.append(query)
        return 300

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise AssertionError("search_repositories should not be called during partition planning test")

    def close(self) -> None:
        return None


class ResumePlanningCheckpointClient:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def count_repositories(self, query: str) -> int:
        self.queries.append(query)
        if "2024-01-01T00:00:00Z..2024-01-01T00:00:07Z" in query:
            if self.queries.count(query) > 1:
                raise AssertionError("root planning query should not be re-counted after resume")
            return 1500
        return 200

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        return type("Response", (), {"total_count": 0, "items": []})()

    def close(self) -> None:
        return None


class BlockingExecutionVisibilityClient:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()

    def count_repositories(self, query: str) -> int:
        return 1

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.started.set()
        self.release.wait(timeout=2.0)

        class Response:
            total_count = 1
            items = [
                {
                    "id": 9001,
                    "node_id": "R_blocking",
                    "full_name": "owner/blocking-repo",
                    "owner": {"login": "owner"},
                    "name": "blocking-repo",
                    "html_url": "https://github.com/owner/blocking-repo",
                    "url": "https://api.github.com/repos/owner/blocking-repo",
                    "description": "blocking repo",
                    "default_branch": "main",
                    "language": "Python",
                    "license": {"key": "mit"},
                    "visibility": "public",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 10,
                    "forks_count": 1,
                    "open_issues_count": 0,
                    "created_at": "2024-01-01T00:00:01Z",
                    "updated_at": "2024-01-01T00:00:02Z",
                    "pushed_at": "2024-01-01T00:00:03Z",
                }
            ]

        return Response()

    def close(self) -> None:
        return None


class ResumePlanningFailureClient:
    def count_repositories(self, query: str) -> int:
        raise RuntimeError("count boom")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise AssertionError("search_repositories should not be called during planning failure test")

    def close(self) -> None:
        return None


class EmptyPlanningClient:
    def count_repositories(self, query: str) -> int:
        return 0

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise AssertionError("search_repositories should not be called when planning is empty")

    def close(self) -> None:
        return None


class PersistedPartitionPausePlanningClient:
    def __init__(self) -> None:
        self.count_calls = 0
        self.search_queries: list[str] = []

    def count_repositories(self, query: str) -> int:
        self.count_calls += 1
        if "2024-01-01T00:00:00Z..2024-01-01T00:00:03Z" in query:
            return 1500
        if "2024-01-01T00:00:00Z..2024-01-01T00:00:01Z" in query:
            return 200
        if "2024-01-01T00:00:02Z..2024-01-01T00:00:03Z" in query:
            return 200
        raise AssertionError(f"unexpected planning query: {query}")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.search_queries.append(query)
        return type("Response", (), {"total_count": 0, "items": []})()

    def close(self) -> None:
        return None


class MixedOverflowPlanningClient:
    def count_repositories(self, query: str) -> int:
        if "created:2024-01-01T00:00:00Z..2024-01-01T00:00:03Z" in query:
            return 1500
        if "created:2024-01-01T00:00:00Z..2024-01-01T00:00:01Z" in query:
            return 200
        if "created:2024-01-01T00:00:02Z..2024-01-01T00:00:03Z" in query:
            return 1500
        if "created:2024-01-01T00:00:02Z..2024-01-01T00:00:02Z" in query:
            return 1200
        if "created:2024-01-01T00:00:03Z..2024-01-01T00:00:03Z" in query:
            return 200
        raise AssertionError(f"unexpected planning query: {query}")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        if page > 1:
            return type("Response", (), {"total_count": 1, "items": []})()
        repo_id = 100 if "created:2024-01-01T00:00:00Z..2024-01-01T00:00:01Z" in query else 101
        return type(
            "Response",
            (),
            {
                "total_count": 1,
                "items": [
                    {
                        "id": repo_id,
                        "node_id": f"R_{repo_id}",
                        "full_name": f"owner/repo-{repo_id}",
                        "owner": {"login": "owner"},
                        "name": f"repo-{repo_id}",
                        "html_url": f"https://github.com/owner/repo-{repo_id}",
                        "url": f"https://api.github.com/repos/owner/repo-{repo_id}",
                        "description": "overflow mix repo",
                        "default_branch": "main",
                        "language": "Python",
                        "license": {"key": "mit"},
                        "visibility": "public",
                        "private": False,
                        "fork": False,
                        "archived": False,
                        "stargazers_count": 42,
                        "forks_count": 1,
                        "open_issues_count": 0,
                        "created_at": "2024-01-01T00:00:01Z",
                        "updated_at": "2024-01-01T00:00:02Z",
                        "pushed_at": "2024-01-01T00:00:03Z",
                    }
                ],
            },
        )()

    def close(self) -> None:
        return None


class DuplicateItemClient:
    def count_repositories(self, query: str) -> int:
        return 2

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        duplicate_item = {
            "id": 11,
            "node_id": "R_dup",
            "full_name": "owner/repo-dup",
            "owner": {"login": "owner"},
            "name": "repo-dup",
            "html_url": "https://github.com/owner/repo-dup",
            "url": "https://api.github.com/repos/owner/repo-dup",
            "description": "repo dup",
            "default_branch": "main",
            "language": "Python",
            "license": {"key": "mit"},
            "visibility": "public",
            "private": False,
            "fork": False,
            "archived": False,
            "stargazers_count": 8,
            "forks_count": 0,
            "open_issues_count": 0,
            "created_at": "2024-01-01T00:00:01Z",
            "updated_at": "2024-01-01T00:00:02Z",
            "pushed_at": "2024-01-01T00:00:03Z",
        }

        class Response:
            total_count = 2
            items = [duplicate_item, dict(duplicate_item)]

        return Response()

    def close(self) -> None:
        return None


class PauseDuringPartitionClient:
    def __init__(self) -> None:
        self.search_calls = 0

    def count_repositories(self, query: str) -> int:
        return 250

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.search_calls += 1

        class Response:
            total_count = 250
            items = [
                {
                    "id": 7000 + page,
                    "node_id": f"R_pause_{page}",
                    "full_name": f"owner/repo-pause-{page}",
                    "owner": {"login": "owner"},
                    "name": f"repo-pause-{page}",
                    "html_url": "https://github.com/owner/repo-pause",
                    "url": "https://api.github.com/repos/owner/repo-pause",
                    "description": "repo pause",
                    "default_branch": "main",
                    "language": "Python",
                    "license": {"key": "mit"},
                    "visibility": "public",
                    "private": False,
                    "fork": False,
                    "archived": False,
                    "stargazers_count": 11,
                    "forks_count": 0,
                    "open_issues_count": 0,
                    "created_at": "2024-01-01T00:00:01Z",
                    "updated_at": "2024-01-01T00:00:02Z",
                    "pushed_at": "2024-01-01T00:00:03Z",
                }
            ]

        return Response()

    def close(self) -> None:
        return None


class DriftOnRetryClient:
    def __init__(self) -> None:
        self.mode = "first"
        self.search_calls = 0

    def count_repositories(self, query: str) -> int:
        return 2

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.search_calls += 1

        def build_item(repo_id: int, suffix: str) -> dict:
            return {
                "id": repo_id,
                "node_id": f"R_retry_{repo_id}",
                "full_name": f"owner/repo-{suffix}",
                "owner": {"login": "owner"},
                "name": f"repo-{suffix}",
                "html_url": f"https://github.com/owner/repo-{suffix}",
                "url": f"https://api.github.com/repos/owner/repo-{suffix}",
                "description": "repo retry",
                "default_branch": "main",
                "language": "Python",
                "license": {"key": "mit"},
                "visibility": "public",
                "private": False,
                "fork": False,
                "archived": False,
                "stargazers_count": 11,
                "forks_count": 0,
                "open_issues_count": 0,
                "created_at": "2024-01-01T00:00:01Z",
                "updated_at": "2024-01-01T00:00:02Z",
                "pushed_at": "2024-01-01T00:00:03Z",
            }

        if self.mode == "first":
            payload = {
                1: [build_item(7101, "first")],
                2: [build_item(7102, "stale-second-page")],
            }
            total_count = 2
        else:
            payload = {
                1: [build_item(7201, "second")],
                2: [],
            }
            total_count = 1
        return type("Response", (), {"total_count": total_count, "items": payload.get(page, [])})()

    def close(self) -> None:
        return None


class MultiPageClient:
    def __init__(self) -> None:
        self.search_calls = 0

    def count_repositories(self, query: str) -> int:
        return 250

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        self.search_calls += 1
        items = []
        if page <= 3:
            for item_index in range(3):
                repo_number = ((page - 1) * 3) + item_index + 1
                items.append(
                    {
                        "id": 8000 + repo_number,
                        "node_id": f"R_multi_{repo_number}",
                        "full_name": f"owner/repo-multi-{repo_number}",
                        "owner": {"login": "owner"},
                        "name": f"repo-multi-{repo_number}",
                        "html_url": "https://github.com/owner/repo-multi",
                        "url": "https://api.github.com/repos/owner/repo-multi",
                        "description": "repo multi",
                        "default_branch": "main",
                        "language": "Python",
                        "license": {"key": "mit"},
                        "visibility": "public",
                        "private": False,
                        "fork": False,
                        "archived": False,
                        "stargazers_count": 12,
                        "forks_count": 0,
                        "open_issues_count": 0,
                        "created_at": "2024-01-01T00:00:01Z",
                        "updated_at": "2024-01-01T00:00:02Z",
                        "pushed_at": "2024-01-01T00:00:03Z",
                    }
                )
        return type("Response", (), {"total_count": 250, "items": items})()

    def close(self) -> None:
        return None


class FailAfterOnePageClient:
    def count_repositories(self, query: str) -> int:
        return 250

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        if page == 2:
            raise RuntimeError("boom on page 2")

        return type(
            "Response",
            (),
            {
                "total_count": 250,
                "items": [
                    {
                        "id": 9001,
                        "node_id": "R_fail_1",
                        "full_name": "owner/repo-fail-1",
                        "owner": {"login": "owner"},
                        "name": "repo-fail-1",
                        "html_url": "https://github.com/owner/repo-fail-1",
                        "url": "https://api.github.com/repos/owner/repo-fail-1",
                        "description": "repo fail",
                        "default_branch": "main",
                        "language": "Python",
                        "license": {"key": "mit"},
                        "visibility": "public",
                        "private": False,
                        "fork": False,
                        "archived": False,
                        "stargazers_count": 1,
                        "forks_count": 0,
                        "open_issues_count": 0,
                        "created_at": "2024-01-01T00:00:01Z",
                        "updated_at": "2024-01-01T00:00:02Z",
                        "pushed_at": "2024-01-01T00:00:03Z",
                    }
                ],
            },
        )()

    def close(self) -> None:
        return None


class IncompletePlanningClient:
    def count_repositories(self, query: str) -> int:
        raise GitHubIncompleteResultsError("GitHub search returned incomplete_results during planning")

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise AssertionError("search_repositories should not be called for this planning test")

    def close(self) -> None:
        return None


class IncompleteExecutionClient:
    def count_repositories(self, query: str) -> int:
        return 1

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ):
        raise GitHubIncompleteResultsError("GitHub search returned incomplete_results during execution")

    def close(self) -> None:
        return None


def test_crawl_service_runs_partition_and_persists_results() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="test-job", filters=filters)
        service.run_job(job.id)
        session.refresh(job)

        assert job.status == "completed"
        assert job.stats_json["total_partitions"] == 1
        assert job.stats_json["unique_repositories"] == 2
        assert job.stats_json["repository_hits"] == 2
    finally:
        session.close()


def test_crawl_service_repository_limit_caps_search_discoveries() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
            repository_limit=1,
        )
        job = service.create_job(name="limited-job", filters=filters)
        service.run_job(job.id)
        session.refresh(job)

        repositories = service.list_job_repositories(job.id, limit=10)
        assert job.status == CrawlJobStatus.completed.value
        assert job.stats_json["unique_repositories"] == 1
        assert job.stats_json["repository_hits"] == 2
        assert [item["repository"].full_name for item in repositories] == ["owner/repo-one"]
        assert session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) == 1
    finally:
        session.close()


def test_crawl_service_runs_target_repository_job_without_search() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    target_client = TargetRepositoryClient()
    try:
        service = CrawlService(session, settings, target_client)
        filters = CrawlFilters(
            created_after=datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
            target_repositories=["owner/repo-one", "owner/repo-two"],
        )
        job = service.create_job(name="target-job", filters=filters)

        handled = service.run_target_repository_job(job.id)
        session.refresh(job)

        assert handled is True
        assert target_client.repository_names == ["owner/repo-one", "owner/repo-two"]
        assert target_client.count_queries == []
        assert target_client.search_queries == []
        assert job.status == CrawlJobStatus.completed.value
        assert job.stats_json["total_partitions"] == 2
        assert job.stats_json["unique_repositories"] == 2
        assert job.stats_json["repository_hits"] == 2
        assert session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) == 2
        assert {
            partition.query_string: partition.status
            for partition in service.list_partitions(job.id)
        } == {
            "owner/repo-one": CrawlPartitionStatus.completed.value,
            "owner/repo-two": CrawlPartitionStatus.completed.value,
        }
    finally:
        session.close()


def test_crawl_service_repository_limit_caps_target_repository_jobs() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    target_client = TargetRepositoryClient()
    try:
        service = CrawlService(session, settings, target_client)
        filters = CrawlFilters(
            created_after=datetime(1970, 1, 1, 0, 0, 0, tzinfo=UTC),
            target_repositories=["owner/repo-one", "owner/repo-two"],
            repository_limit=1,
        )
        job = service.create_job(name="target-limit-job", filters=filters)

        handled = service.run_target_repository_job(job.id)
        session.refresh(job)

        assert handled is True
        assert target_client.repository_names == ["owner/repo-one"]
        assert job.status == CrawlJobStatus.completed.value
        assert job.stats_json["total_partitions"] == 1
        assert job.stats_json["unique_repositories"] == 1
        assert job.stats_json["repository_hits"] == 1
        assert session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) == 1
    finally:
        session.close()


def test_crawl_service_deduplicates_duplicate_repositories_within_a_single_page() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, DuplicateItemClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="duplicate-page-job", filters=filters)
        service.run_job(job.id)
        session.refresh(job)
        partition = service.list_partitions(job.id)[0]

        assert job.status == "completed"
        assert job.stats_json["unique_repositories"] == 1
        assert job.stats_json["repository_hits"] == 2
        assert partition.unique_count == 1
        assert partition.fetched_count == 2
    finally:
        session.close()


def test_delete_job_removes_only_orphan_repositories() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job_one = service.create_job(name="job-one", filters=filters)
        service.run_job(job_one.id)

        job_two = service.create_job(name="job-two", filters=filters)
        service.run_job(job_two.id)

        assert session.query(GitHubRepository).count() == 2

        delete_one = service.delete_job(job_one.id)
        assert delete_one["deleted_jobs"] == 1
        assert delete_one["deleted_repositories"] == 0
        assert session.query(GitHubRepository).count() == 2

        delete_two = service.delete_job(job_two.id)
        assert delete_two["deleted_jobs"] == 1
        assert delete_two["deleted_repositories"] == 2
        assert session.query(GitHubRepository).count() == 0
    finally:
        session.close()


def test_delete_job_keeps_repositories_referenced_by_data_pool_assets(tmp_path) -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="data-pool-reference-job", filters=filters)
        service.run_job(job.id)

        repository = session.scalars(
            select(GitHubRepository).where(GitHubRepository.full_name == "owner/repo-one")
        ).one()
        pool = DataPool(name="default", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        session.add(
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=repository.id,
                github_repo_id=repository.github_repo_id,
                repo_full_name=repository.full_name,
                source_commit_sha="abc123",
                language=repository.primary_language,
                stars=repository.stargazers_count,
                entry_file_path="tests/test_feature.py",
                depth=1,
                folder_name="owner__repo-one__abc123__tests_test_feature_py__depth-1",
                folder_path=str(tmp_path / "pool" / "asset"),
                manifest_json={},
            )
        )
        session.flush()

        deleted = service.delete_job(job.id)

        assert deleted["deleted_jobs"] == 1
        assert deleted["deleted_repositories"] == 1
        repository_names = list(
            session.scalars(select(GitHubRepository.full_name).order_by(GitHubRepository.full_name))
        )
        assert repository_names == ["owner/repo-one"]
    finally:
        session.close()


def test_delete_orphan_repositories_batches_large_candidate_lists(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        monkeypatch.setattr(stage1_service_module, "REPOSITORY_ID_FILTER_BATCH_SIZE", 2)
        service = CrawlService(session, settings, FakeGitHubClient())
        repositories = [
            GitHubRepository(
                github_repo_id=10_000 + index,
                full_name=f"owner/repo-{index}",
                owner_login="owner",
                name=f"repo-{index}",
                html_url=f"https://github.com/owner/repo-{index}",
                api_url=f"https://api.github.com/repos/owner/repo-{index}",
                default_branch="main",
                primary_language="Python",
                stargazers_count=index,
                raw_payload={},
            )
            for index in range(5)
        ]
        session.add_all(repositories)
        session.flush()

        referenced_repository = repositories[2]
        pool = DataPool(name="batched-delete-pool", root_path=str(tmp_path / "pool"))
        session.add(pool)
        session.flush()
        session.add(
            DataPoolAsset(
                pool_id=pool.id,
                repository_id=referenced_repository.id,
                github_repo_id=referenced_repository.github_repo_id,
                repo_full_name=referenced_repository.full_name,
                source_commit_sha="abc123",
                language=referenced_repository.primary_language,
                stars=referenced_repository.stargazers_count,
                entry_file_path="tests/test_feature.py",
                depth=1,
                folder_name="owner__repo-2__abc123__tests_test_feature_py__depth-1",
                folder_path=str(tmp_path / "pool" / "asset"),
                manifest_json={},
            )
        )
        session.flush()

        repository_ids = [repository.id for repository in repositories]
        deleted = service._delete_orphan_repositories(repository_ids + [repository_ids[0]])

        assert deleted == 4
        remaining_repository_names = list(
            session.scalars(select(GitHubRepository.full_name).order_by(GitHubRepository.full_name))
        )
        assert remaining_repository_names == ["owner/repo-2"]
    finally:
        session.close()


def test_crawl_service_can_pause_before_running_next_partition() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="pause-job", filters=filters)
        service.run_job(job.id, should_pause=lambda: True)
        session.refresh(job)

        assert job.status == "paused"
        assert job.stats_json["total_partitions"] == 0
        assert job.stats_json["unique_repositories"] == 0
    finally:
        session.close()


def test_crawl_service_keeps_job_queued_when_runner_has_scheduled_it() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="queued-job", filters=filters)
        service.mark_job_queued(job.id)

        changed = service.reconcile_job(job, is_running=False, is_scheduled=True)

        assert changed is False
        assert job.status == "queued"
    finally:
        session.close()


def test_crawl_service_preserves_max_concurrent_partitions_in_job_stats() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(
            name="job-with-partition-concurrency",
            filters=filters,
            max_concurrent_partitions=4,
        )

        assert job.stats_json["max_concurrent_partitions"] == 4

        service.mark_job_queued(job.id)
        session.refresh(job)
        assert job.stats_json["max_concurrent_partitions"] == 4

        service.reconcile_job(job, is_running=False, is_scheduled=True)
        assert job.stats_json["max_concurrent_partitions"] == 4
    finally:
        session.close()


def test_crawl_service_preserves_planning_checkpoint_while_job_is_requeued() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="queued-checkpoint-job", filters=filters)
        job.status = "paused"
        job.stats_json = {
            "total_partitions": 0,
            "partition_status_counts": {},
            "repository_hits": 0,
            "unique_repositories": 0,
            "planning_progress": {
                "phase": "planning",
                "event": "counting",
                "processed_windows": 1,
            },
            "planning_checkpoint": {
                "queued_windows": [
                    {
                        "start": "2024-01-01T00:00:00+00:00",
                        "end": "2024-01-01T00:00:03+00:00",
                        "depth": 1,
                        "expected_count": 0,
                    }
                ],
                "processed_windows": 1,
                "split_windows": 1,
                "planned_partitions": 0,
                "empty_windows": 0,
                "probe_count": 1,
            },
        }
        session.flush()

        service.mark_job_queued(job.id)
        session.refresh(job)

        assert job.status == "queued"
        assert "planning_checkpoint" in (job.stats_json or {})
        assert "planning_progress" in (job.stats_json or {})

        changed = service.reconcile_job(job, is_running=False, is_scheduled=True)

        assert changed is False
        assert job.status == "queued"
        assert "planning_checkpoint" in (job.stats_json or {})
        assert "planning_progress" in (job.stats_json or {})
    finally:
        session.close()


def test_reconcile_job_keeps_planning_status_while_runner_resumes_checkpointed_planning() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="queued-checkpoint-with-partition-job", filters=filters)
        job.status = "queued"
        job.started_at = datetime.now(UTC)
        job.stats_json = {
            "total_partitions": 1,
            "partition_status_counts": {"pending": 1},
            "repository_hits": 0,
            "unique_repositories": 0,
            "planning_checkpoint": {
                "queued_windows": [
                    {
                        "start": "2024-01-01T00:00:04+00:00",
                        "end": "2024-01-01T00:00:10+00:00",
                        "depth": 1,
                        "expected_count": 0,
                    }
                ],
                "processed_windows": 1,
                "split_windows": 1,
                "planned_partitions": 1,
                "empty_windows": 0,
                "probe_count": 1,
            },
        }
        session.add(
            CrawlPartition(
                job_id=job.id,
                status=CrawlPartitionStatus.pending.value,
                depth=1,
                range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                range_end=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
                query_string="language:Python",
                expected_count=10,
            )
        )
        session.flush()

        changed = service.reconcile_job(job, is_running=True, is_scheduled=True)

        assert changed is True
        assert job.status == CrawlJobStatus.planning.value
        assert "planning_checkpoint" in (job.stats_json or {})
    finally:
        session.close()


def test_crawl_service_preserves_planning_checkpoint_when_resumed_planning_fails() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, ResumePlanningFailureClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="resume-planning-failure-job", filters=filters)
        session.add(
            CrawlPartition(
                job_id=job.id,
                status="pending",
                depth=1,
                range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                range_end=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
                query_string="language:Python",
            )
        )
        job.status = "paused"
        job.stats_json = {
            "total_partitions": 1,
            "partition_status_counts": {"pending": 1},
            "repository_hits": 0,
            "unique_repositories": 0,
            "planning_progress": {
                "phase": "planning",
                "event": "resuming",
                "count_query_calls": 0,
            },
            "planning_checkpoint": {
                "queued_windows": [
                    {
                        "start": "2024-01-01T00:00:04+00:00",
                        "end": "2024-01-01T00:00:07+00:00",
                        "depth": 1,
                        "expected_count": 0,
                    }
                ],
                "processed_windows": 1,
                "split_windows": 1,
                "planned_partitions": 1,
                "empty_windows": 0,
                "probe_count": 1,
            },
        }
        session.commit()

        try:
            service.run_job(job.id, persist_progress=session.commit)
        except RuntimeError as exc:
            assert str(exc) == "count boom"
        else:
            raise AssertionError("expected resumed planning failure to bubble up")

        session.refresh(job)

        assert job.status == "failed"
        assert job.error_message == "count boom"
        assert job.stats_json["planning_checkpoint"]["processed_windows"] == 1
        assert len(job.stats_json["planning_checkpoint"]["queued_windows"]) == 1
        assert service.list_partitions(job.id)[0].status == "pending"
    finally:
        session.close()


def test_crawl_service_marks_orphaned_planning_job_without_partitions_as_pending() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="orphan-planning-job", filters=filters)
        job.status = "planning"
        job.started_at = datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)
        session.flush()

        changed = service.reconcile_job(job, is_running=False, is_scheduled=False)

        assert changed is True
        assert job.status == "pending"
        assert job.finished_at is None
    finally:
        session.close()


def test_crawl_service_preserves_failed_job_without_partitions_during_reconcile() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="failed-job", filters=filters)
        job.status = "failed"
        job.started_at = datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)
        job.finished_at = datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
        job.error_message = "boom"
        session.flush()

        service.reconcile_job(job, is_running=False, is_scheduled=False)

        assert job.status == "failed"
        assert job.error_message == "boom"
        assert job.finished_at == datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
    finally:
        session.close()


def test_crawl_service_clears_stale_error_message_after_successful_empty_retry() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, EmptyPlanningClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="retry-empty-job", filters=filters)
        job.status = "failed"
        job.error_message = "boom"
        job.finished_at = datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)
        session.commit()

        service.run_job(job.id)
        session.refresh(job)

        assert job.status == "completed"
        assert job.error_message is None
    finally:
        session.close()


def test_crawl_service_mark_job_failed_preserves_failed_state_with_pending_partitions() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="failed-pending-job", filters=filters)
        session.add(
            CrawlPartition(
                job_id=job.id,
                status="pending",
                depth=0,
                range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                range_end=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                query_string="language:Python",
            )
        )
        session.flush()

        service.mark_job_failed(job.id, "boom")
        session.refresh(job)

        assert job.status == "failed"
        assert job.error_message == "boom"
        assert job.finished_at is not None
    finally:
        session.close()


def test_crawl_service_marks_overflow_and_failed_partitions_as_partial() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="overflow-failed-job", filters=filters)
        session.add_all(
            [
                CrawlPartition(
                    job_id=job.id,
                    status="overflow",
                    depth=0,
                    range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                    range_end=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
                    query_string="language:Python",
                    error_message="overflow",
                    finished_at=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                ),
                CrawlPartition(
                    job_id=job.id,
                    status="failed",
                    depth=0,
                    range_start=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                    range_end=datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
                    query_string="language:Python",
                    error_message="failed",
                    finished_at=datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC),
                ),
            ]
        )
        session.flush()

        changed = service.reconcile_job(job, is_running=False)

        assert changed is True
        assert job.status == "partial"
        assert job.finished_at is not None
    finally:
        session.close()


def test_crawl_service_pauses_mid_partition_at_request_boundary() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = PauseDuringPartitionClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="pause-job", filters=filters)
        service.run_job(job.id, should_pause=lambda: client.search_calls >= 1)
        session.refresh(job)
        partition = service.list_partitions(job.id)[0]

        assert job.status == "paused"
        assert partition.status == "pending"
        assert partition.fetched_count == 1
        assert client.search_calls == 1
    finally:
        session.close()


def test_crawl_service_can_resume_paused_job_with_existing_partitions() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = PauseDuringPartitionClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="resume-paused-job", filters=filters)
        service.run_job(job.id, should_pause=lambda: client.search_calls >= 1)
        session.refresh(job)
        paused_partition = service.list_partitions(job.id)[0]

        assert job.status == "paused"
        assert paused_partition.status == "pending"

        service.run_job(job.id)
        session.refresh(job)
        resumed_partition = service.list_partitions(job.id)[0]

        assert job.status == "completed"
        assert resumed_partition.status == "completed"
        assert resumed_partition.unique_count == 3
        assert resumed_partition.fetched_count == 3
    finally:
        session.close()


def test_crawl_service_retry_replaces_stale_partition_discoveries(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'retry-replaces-stale.db'}",
        github_page_size=1,
    )
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = DriftOnRetryClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        job = service.create_job(name="retry-replaces-stale-job", filters=filters)

        service.run_job(job.id, should_pause=lambda: client.search_calls >= 1)
        session.refresh(job)
        assert job.status == CrawlJobStatus.paused.value

        client.mode = "second"
        service.run_job(job.id)
        session.refresh(job)

        partition = service.list_partitions(job.id)[0]
        discovery_count = session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) or 0
        repository_names = list(session.scalars(select(GitHubRepository.full_name).order_by(GitHubRepository.full_name)))

        assert job.status == CrawlJobStatus.completed.value
        assert partition.unique_count == 1
        assert partition.fetched_count == 1
        assert discovery_count == 1
        assert repository_names == ["owner/repo-second"]
    finally:
        session.close()


def test_crawl_service_recomputes_unique_counts_after_resuming_partial_partition() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = MultiPageClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="resume-counts-job", filters=filters)
        service.run_job(job.id, should_pause=lambda: client.search_calls >= 1)
        session.refresh(job)

        service.run_job(job.id)
        session.refresh(job)

        partition = service.list_partitions(job.id)[0]
        query_execution = session.scalar(
            select(CrawlPartitionQueryExecution)
            .where(CrawlPartitionQueryExecution.partition_id == partition.id)
            .order_by(CrawlPartitionQueryExecution.query_index.asc())
        )
        repo_discoveries = session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) or 0

        assert job.status == "completed"
        assert partition.unique_count == 9
        assert partition.fetched_count == 9
        assert query_execution is not None
        assert query_execution.unique_count == 9
        assert repo_discoveries == 9
        assert job.stats_json["unique_repositories"] == 9
        assert job.stats_json["repository_hits"] == 9
    finally:
        session.close()


def test_crawl_service_marks_job_failed_and_preserves_progress_when_partition_errors() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FailAfterOnePageClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="fail-mid-page-job", filters=filters)

        try:
            service.run_job(job.id)
        except RuntimeError as exc:
            assert str(exc) == "boom on page 2"
        else:
            raise AssertionError("expected run_job to re-raise partition failure")

        session.refresh(job)
        partition = service.list_partitions(job.id)[0]
        discoveries = session.scalar(select(func.count(RepoDiscovery.id)).where(RepoDiscovery.job_id == job.id)) or 0

        assert job.status == "failed"
        assert job.error_message == "boom on page 2"
        assert job.stats_json["partition_status_counts"] == {"failed": 1}
        assert job.stats_json["repository_hits"] == 1
        assert job.stats_json["unique_repositories"] == 1
        assert partition.status == "failed"
        assert partition.fetched_count == 1
        assert partition.unique_count == 1
        assert discoveries == 1
    finally:
        session.close()


def test_crawl_service_persist_progress_exposes_partitions_before_job_finishes(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'progress.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    snapshots: list[dict[str, object]] = []

    try:
        service = CrawlService(session, settings, SplitPartitionClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="progress-job", filters=filters)
        session.commit()

        def persist_progress() -> None:
            session.commit()
            reader = session_factory()
            try:
                observed_job = reader.get(type(job), job.id)
                partition_count = reader.scalar(
                    select(func.count(CrawlPartition.id)).where(CrawlPartition.job_id == job.id)
                ) or 0
                snapshots.append(
                    {
                        "status": observed_job.status if observed_job else None,
                        "started_at": observed_job.started_at if observed_job else None,
                        "partition_count": partition_count,
                        "planning_progress": (observed_job.stats_json or {}).get("planning_progress") if observed_job else None,
                    }
                )
            finally:
                reader.close()

        service.run_job(job.id, persist_progress=persist_progress)

        assert any(snapshot["status"] == "planning" for snapshot in snapshots)
        assert any(snapshot["planning_progress"] for snapshot in snapshots)
        assert any(
            snapshot["planning_progress"] and snapshot["planning_progress"]["count_query_calls"] >= 1
            for snapshot in snapshots
        )
        assert any(snapshot["partition_count"] == 2 for snapshot in snapshots)
        assert any(snapshot["started_at"] is not None for snapshot in snapshots)
        assert "planning_progress" not in (job.stats_json or {})
    finally:
        session.close()


def test_reconcile_job_preserves_active_planning_progress_without_checkpoint(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'reconcile-planning-progress.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    session = session_factory()

    try:
        service = CrawlService(session, settings, SplitPartitionClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="reconcile-planning-progress-job", filters=filters)
        job.status = "planning"
        job.stats_json = {
            "planning_progress": {
                "phase": "planning",
                "event": "counting",
                "processed_windows": 1,
                "discovered_windows": 3,
                "queued_windows": 2,
                "split_windows": 1,
                "planned_partitions": 0,
                "overflow_windows": 0,
                "empty_windows": 0,
                "probe_count": 1,
                "count_query_calls": 2,
                "current_depth": 1,
                "current_range_start": "2024-01-01T00:00:00+00:00",
                "current_range_end": "2024-01-01T00:00:07+00:00",
                "current_query_index": 1,
                "current_query_total": 2,
                "current_query_string": "language:Python",
                "last_estimated_count": 12,
                "progress_ratio": 0.3333333333,
                "updated_at": "2024-01-01T00:00:10+00:00",
            }
        }

        changed = service.reconcile_job(job, is_running=True, is_scheduled=True)

        assert changed is True
        assert job.status == CrawlJobStatus.planning.value
        assert job.stats_json is not None
        assert job.stats_json["planning_progress"]["progress_ratio"] == 0.3333333333
        assert "planning_checkpoint" not in job.stats_json
    finally:
        session.close()


def test_crawl_service_records_actual_executed_queries_for_partitions_and_discoveries() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            languages=["Python", "Go"],
            licenses=["mit", "apache-2.0"],
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="audit-job", filters=filters)
        service.run_job(job.id)

        partition = service.list_partitions(job.id)[0]
        partition_queries = partition.query_string.splitlines()
        discovery_queries = list(session.scalars(select(RepoDiscovery.query_string).where(RepoDiscovery.job_id == job.id)))
        query_executions = list(
            session.scalars(
                select(CrawlPartitionQueryExecution)
                .where(CrawlPartitionQueryExecution.partition_id == partition.id)
                .order_by(CrawlPartitionQueryExecution.query_index.asc())
            )
        )

        assert len(partition_queries) == 4
        assert all(" OR " not in query for query in partition_queries)
        assert all(query in partition_queries for query in discovery_queries)
        assert [item.query_string for item in query_executions] == partition_queries
        assert all(item.status == "completed" for item in query_executions)
        assert all(item.current_page == 1 for item in query_executions)
        assert all(item.total_pages == 1 for item in query_executions)
        assert sum(item.unique_count or 0 for item in query_executions) == 2
        assert sum(item.fetched_count or 0 for item in query_executions) == 8
    finally:
        session.close()


def test_crawl_service_persists_running_partition_and_query_statuses_mid_execution(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'running-visibility.db'}")
    engine = build_engine(settings)
    init_db(engine)
    session_factory = build_session_factory(engine)
    setup_session = session_factory()
    try:
        setup_service = CrawlService(setup_session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = setup_service.create_job(name="running-visibility-job", filters=filters)
        partition = CrawlPartition(
            job_id=job.id,
            status=CrawlPartitionStatus.pending.value,
            depth=0,
            range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
            query_string="placeholder",
            expected_count=1,
        )
        setup_session.add(partition)
        setup_session.commit()
        job_id = job.id
        partition_id = partition.id
    finally:
        setup_session.close()

    client = BlockingExecutionVisibilityClient()
    failure: list[Exception] = []

    def run_partition() -> None:
        session = session_factory()
        try:
            service = CrawlService(session, settings, client)
            service.execute_partition(
                job_id,
                partition_id,
                persist_progress=session.commit,
            )
            session.commit()
        except Exception as exc:  # pragma: no cover - test failure path
            failure.append(exc)
        finally:
            session.close()

    thread = Thread(target=run_partition)
    thread.start()
    try:
        assert client.started.wait(timeout=2.0) is True

        observer_session = session_factory()
        try:
            observer_service = CrawlService(observer_session, settings, FakeGitHubClient())
            persisted_partition = observer_session.get(CrawlPartition, partition_id)
            assert persisted_partition is not None
            assert persisted_partition.status == CrawlPartitionStatus.running.value
            audits = observer_service.list_partition_query_audits(job_id)
            assert audits[partition_id][0]["status"] == "running"
            assert audits[partition_id][0]["current_page"] == 1
            assert audits[partition_id][0]["total_pages"] is None
        finally:
            observer_session.close()
    finally:
        client.release.set()
        thread.join(timeout=2.0)

    if failure:
        raise failure[0]


def test_crawl_service_stops_counting_remaining_query_variants_once_partition_is_over_limit() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = EarlyExitCountClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            languages=["Python", "Go"],
            licenses=["mit", "apache-2.0"],
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="early-exit-job", filters=filters)
        partitions = service.plan_partitions(job)
        session.refresh(job)

        assert client.queries
        assert all("language:Python" in query and "license:mit" in query for query in client.queries)
        assert job.status == "partial"
        assert partitions
        assert all(partition.status == "overflow" for partition in partitions)
    finally:
        session.close()


def test_crawl_service_plans_partition_when_each_query_variant_is_under_limit() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = MultiVariantSafePlanningClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            languages=["Python", "Go"],
            licenses=["mit", "apache-2.0"],
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
        )
        job = service.create_job(name="safe-multi-variant-job", filters=filters)
        partitions = service.plan_partitions(job)
        session.refresh(job)

        assert len(client.queries) == 4
        assert job.status == CrawlJobStatus.pending.value
        assert len(partitions) == 1
        assert partitions[0].status == CrawlPartitionStatus.pending.value
        assert partitions[0].expected_count == 1200
    finally:
        session.close()


def test_crawl_service_persists_planning_checkpoint_on_pause_and_resumes_from_it() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = ResumePlanningCheckpointClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="planning-checkpoint-job", filters=filters)

        service.run_job(job.id, should_pause=lambda: len(client.queries) >= 1)
        session.refresh(job)

        assert job.status == "paused"
        assert job.stats_json["planning_checkpoint"]["processed_windows"] == 1
        assert len(job.stats_json["planning_checkpoint"]["queued_windows"]) == 2
        assert job.stats_json["planning_progress"]["event"] in {"split", "resuming", "counting", "presplit"}
        assert service.list_partitions(job.id) == []

        service.run_job(job.id)
        session.refresh(job)
        partitions = service.list_partitions(job.id)

        assert job.status == "completed"
        assert len(partitions) == 2
        assert "planning_checkpoint" not in (job.stats_json or {})
        assert client.queries.count(
            "is:public fork:false archived:false created:2024-01-01T00:00:00Z..2024-01-01T00:00:07Z language:Python"
        ) == 1
    finally:
        session.close()


def test_crawl_service_preserves_planning_checkpoint_when_pause_happens_after_partial_partition_persist() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    client = PersistedPartitionPausePlanningClient()
    try:
        service = CrawlService(session, settings, client)
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
        )
        job = service.create_job(name="planning-checkpoint-with-persisted-partition", filters=filters)

        service.run_job(job.id, should_pause=lambda: client.count_calls >= 2)
        session.refresh(job)
        partitions = service.list_partitions(job.id)

        assert job.status == "paused"
        assert len(partitions) == 1
        assert partitions[0].status == "pending"
        assert job.stats_json["planning_checkpoint"]["processed_windows"] == 2
        assert len(job.stats_json["planning_checkpoint"]["queued_windows"]) == 1
        assert client.search_queries == []

        service.run_job(job.id)
        session.refresh(job)
        resumed_partitions = service.list_partitions(job.id)

        assert job.status == "completed"
        assert len(resumed_partitions) == 2
        assert all(partition.status == "completed" for partition in resumed_partitions)
        assert "planning_checkpoint" not in (job.stats_json or {})
        assert client.count_calls == 3
        assert len(client.search_queries) == 2
    finally:
        session.close()


def test_reconcile_job_preserves_planning_checkpoint_for_resumable_orphan() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 7, tzinfo=UTC),
        )
        job = service.create_job(name="orphan-planning-checkpoint", filters=filters)
        job.status = "planning"
        job.started_at = datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC)
        job.stats_json = {
            "total_partitions": 0,
            "partition_status_counts": {},
            "repository_hits": 0,
            "unique_repositories": 0,
            "planning_progress": {
                "phase": "planning",
                "event": "counting",
                "processed_windows": 1,
                "discovered_windows": 3,
                "queued_windows": 2,
                "split_windows": 1,
                "planned_partitions": 0,
                "empty_windows": 0,
                "probe_count": 1,
                "count_query_calls": 1,
                "current_depth": 1,
                "current_range_start": "2024-01-01T00:00:00+00:00",
                "current_range_end": "2024-01-01T00:00:03+00:00",
                "current_query_index": None,
                "current_query_total": None,
                "current_query_string": None,
                "last_estimated_count": 1500,
                "progress_ratio": 0.3333333333,
                "updated_at": "2024-01-01T00:00:02+00:00",
            },
            "planning_checkpoint": {
                "queued_windows": [
                    {
                        "start": "2024-01-01T00:00:04+00:00",
                        "end": "2024-01-01T00:00:07+00:00",
                        "depth": 1,
                        "expected_count": 0,
                    },
                    {
                        "start": "2024-01-01T00:00:00+00:00",
                        "end": "2024-01-01T00:00:03+00:00",
                        "depth": 1,
                        "expected_count": 0,
                    },
                ],
                "processed_windows": 1,
                "split_windows": 1,
                "planned_partitions": 0,
                "empty_windows": 0,
                "probe_count": 1,
            },
        }
        session.flush()

        changed = service.reconcile_job(job, is_running=False, is_scheduled=False)

        assert changed is True
        assert job.status == "pending"
        assert "planning_checkpoint" in (job.stats_json or {})
        assert job.stats_json["planning_progress"]["processed_windows"] == 1
    finally:
        session.close()


def test_crawl_service_marks_job_failed_when_planning_hits_incomplete_results() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, IncompletePlanningClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="incomplete-planning-job", filters=filters)

        try:
            service.run_job(job.id)
        except GitHubIncompleteResultsError as exc:
            assert "incomplete_results" in str(exc)
        else:
            raise AssertionError("expected incomplete planning results to fail the job")

        session.refresh(job)
        assert job.status == "failed"
        assert "incomplete_results" in (job.error_message or "")
        assert job.stats_json["total_partitions"] == 0
    finally:
        session.close()


def test_crawl_service_persists_overflow_partitions_and_finishes_partial_after_running_safe_windows() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, MixedOverflowPlanningClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 3, tzinfo=UTC),
        )
        job = service.create_job(name="mixed-overflow-job", filters=filters)

        planned = service.plan_partitions(job)
        session.refresh(job)

        assert job.status == "pending"
        assert [partition.status for partition in planned] == ["pending", "overflow", "pending"]

        service.reconcile_job(job, is_running=False, is_scheduled=False)
        assert job.status == "pending"

        service.run_job(job.id)
        session.refresh(job)
        partitions = service.list_partitions(job.id)
        overflow_partition = next(partition for partition in partitions if partition.status == "overflow")
        query_audits = service.list_partition_query_audits(job.id)

        assert job.status == "partial"
        assert job.stats_json["partition_status_counts"] == {"completed": 2, "overflow": 1}
        assert job.stats_json["unique_repositories"] == 2
        assert overflow_partition.range_start == datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
        assert overflow_partition.range_end == datetime(2024, 1, 1, 0, 0, 2, tzinfo=UTC)
        assert overflow_partition.expected_count == 1200
        assert "one-second partition" in (overflow_partition.error_message or "")
        assert query_audits[overflow_partition.id][0]["status"] == "failed"
        assert "one-second partition" in (query_audits[overflow_partition.id][0]["error_message"] or "")
    finally:
        session.close()


def test_crawl_service_marks_partition_failed_when_execution_hits_incomplete_results() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, IncompleteExecutionClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="incomplete-execution-job", filters=filters)

        try:
            service.run_job(job.id)
        except GitHubIncompleteResultsError as exc:
            assert "incomplete_results" in str(exc)
        else:
            raise AssertionError("expected incomplete execution results to fail the job")

        session.refresh(job)
        partition = service.list_partitions(job.id)[0]
        assert job.status == "failed"
        assert partition.status == "failed"
        assert "incomplete_results" in (job.error_message or "")
        assert "incomplete_results" in (partition.error_message or "")
    finally:
        session.close()


def test_prepare_job_run_recovers_stale_running_partitions_in_a_single_call() -> None:
    settings = Settings(database_url="sqlite://")
    engine = build_engine(settings)
    init_db(engine)
    session = build_session_factory(engine)()
    try:
        service = CrawlService(session, settings, FakeGitHubClient())
        filters = CrawlFilters(
            language="Python",
            created_after=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            created_before=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
        )
        job = service.create_job(name="recover-running-partitions", filters=filters)
        partition = CrawlPartition(
            job_id=job.id,
            status="running",
            depth=0,
            range_start=datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            range_end=datetime(2024, 1, 1, 0, 0, 10, tzinfo=UTC),
            query_string="language:Python",
            expected_count=1,
        )
        session.add(partition)
        session.flush()

        pending_partition_ids = service.prepare_job_run(job.id)

        assert pending_partition_ids == [partition.id]
        session.refresh(partition)
        assert partition.status == "pending"
    finally:
        session.close()
