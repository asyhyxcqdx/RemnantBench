import feature_factory.stage1.github_client as github_client_module
from feature_factory.config import Settings
from feature_factory.stage1.github_client import GitHubIncompleteResultsError, GitHubSearchClient


class _StubHTTPResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_github_search_client_rejects_incomplete_results(monkeypatch) -> None:
    client = GitHubSearchClient(Settings(database_url="sqlite://"))
    monkeypatch.setattr(github_client_module.time, "sleep", lambda _seconds: None)
    client._request = lambda *args, **kwargs: _StubHTTPResponse(  # type: ignore[method-assign]
        {
            "total_count": 123,
            "incomplete_results": True,
            "items": [],
        }
    )

    try:
        try:
            client.search_repositories("language:Python")
        except GitHubIncompleteResultsError as exc:
            assert "incomplete_results" in str(exc)
        else:
            raise AssertionError("expected incomplete_results to raise an explicit error")
    finally:
        client.close()


def test_github_search_client_fetches_repository_by_full_name() -> None:
    client = GitHubSearchClient(Settings(database_url="sqlite://"))
    calls: list[tuple[str, str]] = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        return _StubHTTPResponse({"id": 1, "full_name": "owner/repo"})

    client._request = fake_request  # type: ignore[method-assign]

    try:
        payload = client.get_repository("owner/repo")
        assert payload["full_name"] == "owner/repo"
        assert calls == [("GET", "/repos/owner/repo")]
    finally:
        client.close()


def test_github_search_client_retries_incomplete_results_before_succeeding(monkeypatch) -> None:
    client = GitHubSearchClient(Settings(database_url="sqlite://"))
    payloads = [
        {
            "total_count": 123,
            "incomplete_results": True,
            "items": [],
        },
        {
            "total_count": 123,
            "incomplete_results": True,
            "items": [],
        },
        {
            "total_count": 123,
            "incomplete_results": False,
            "items": [{"id": 1}],
        },
    ]
    request_calls: list[dict] = []
    sleep_calls: list[float] = []

    def fake_request(*args, **kwargs):
        request_calls.append(kwargs)
        return _StubHTTPResponse(payloads.pop(0))

    monkeypatch.setattr(github_client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    client._request = fake_request  # type: ignore[method-assign]

    try:
        response = client.search_repositories("language:Python")
        assert response.total_count == 123
        assert len(response.items) == 1
        assert len(request_calls) == 3
        assert sleep_calls == [1.0, 2.0]
    finally:
        client.close()


def test_github_search_client_raises_after_exhausting_incomplete_results_retries(monkeypatch) -> None:
    client = GitHubSearchClient(Settings(database_url="sqlite://"))
    request_calls: list[dict] = []
    sleep_calls: list[float] = []

    def fake_request(*args, **kwargs):
        request_calls.append(kwargs)
        return _StubHTTPResponse(
            {
                "total_count": 123,
                "incomplete_results": True,
                "items": [],
            }
        )

    monkeypatch.setattr(github_client_module.time, "sleep", lambda seconds: sleep_calls.append(seconds))
    client._request = fake_request  # type: ignore[method-assign]

    try:
        try:
            client.search_repositories("language:Python")
        except GitHubIncompleteResultsError as exc:
            assert "incomplete_results" in str(exc)
            assert "after 4 attempts" in str(exc)
        else:
            raise AssertionError("expected incomplete_results to raise after retries are exhausted")
        assert len(request_calls) == 4
        assert sleep_calls == [1.0, 2.0, 3.0]
    finally:
        client.close()