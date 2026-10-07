from __future__ import annotations

import time
from typing import Any

import httpx

from feature_factory.config import Settings
from feature_factory.stage1.schemas import SearchResponse
from feature_factory.stage1.token_scheduler import GitHubTokenScheduler


class GitHubAPIError(RuntimeError):
    pass


class GitHubIncompleteResultsError(GitHubAPIError):
    pass


class GitHubSearchClient:
    def __init__(self, settings: Settings, token_scheduler: GitHubTokenScheduler | None = None) -> None:
        self.settings = settings
        self.token_scheduler = token_scheduler or GitHubTokenScheduler(settings)
        self.client = httpx.Client(
            base_url=settings.github_api_base_url,
            timeout=settings.github_http_timeout_seconds,
            headers=self._base_headers(),
        )

    def _base_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.settings.github_user_agent,
            "X-GitHub-Api-Version": self.settings.github_api_version,
        }

    def close(self) -> None:
        self.client.close()

    def search_repositories(
        self,
        query: str,
        *,
        page: int = 1,
        per_page: int | None = None,
        sort: str = "updated",
        order: str = "desc",
    ) -> SearchResponse:
        per_page = per_page or self.settings.github_page_size
        params = {
            "q": query,
            "sort": sort,
            "order": order,
            "page": page,
            "per_page": per_page,
        }

        for retry_index in range(self.settings.github_incomplete_results_retries + 1):
            response = self._request(
                "GET",
                "/search/repositories",
                params=params,
            )
            parsed = SearchResponse.model_validate(response.json())
            if not parsed.incomplete_results:
                return parsed
            if retry_index >= self.settings.github_incomplete_results_retries:
                break
            time.sleep(self._incomplete_results_retry_delay_seconds(retry_index + 1))

        raise GitHubIncompleteResultsError(
            "GitHub search returned incomplete_results "
            f"after {self.settings.github_incomplete_results_retries + 1} attempts "
            f"for query page {page}: {query}"
        )

    def count_repositories(self, query: str) -> int:
        response = self.search_repositories(query, page=1, per_page=1)
        return response.total_count

    def get_repository(self, full_name: str) -> dict[str, Any]:
        normalized = str(full_name or "").strip().strip("/")
        parts = [part.strip() for part in normalized.split("/") if part.strip()]
        if len(parts) != 2:
            raise GitHubAPIError(f"repository must use owner/repo format: {full_name}")
        owner, repo = parts
        response = self._request("GET", f"/repos/{owner}/{repo}")
        return dict(response.json())

    def _incomplete_results_retry_delay_seconds(self, retry_attempt: int) -> float:
        return float(retry_attempt)

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        request_kwargs = dict(kwargs)
        extra_headers = dict(request_kwargs.pop("headers", {}) or {})
        last_error: str | None = None

        for _ in range(max(self.token_scheduler.get_total_token_count(), 1) + 3):
            lease = self.token_scheduler.acquire()
            released = False
            try:
                response = self.client.request(
                    method,
                    url,
                    headers=self.token_scheduler.build_headers(self._base_headers() | extra_headers, lease),
                    **request_kwargs,
                )
                if self._is_rate_limited(response):
                    cooldown = self.token_scheduler.cooldown_for_response(dict(response.headers))
                    self.token_scheduler.release(lease, cooldown_seconds=cooldown)
                    released = True
                    last_error = f"GitHub API rate limited request with status={response.status_code}"
                    continue
                if response.is_error:
                    raise GitHubAPIError(
                        f"GitHub API request failed with status={response.status_code}: {response.text}"
                    )
                return response
            finally:
                if not released:
                    self.token_scheduler.release(lease)

        if last_error is not None:
            raise GitHubAPIError(last_error)
        raise GitHubAPIError("GitHub API request failed without a response")

    def _is_rate_limited(self, response: httpx.Response) -> bool:
        if response.status_code not in {403, 429}:
            return False
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        retry_after = response.headers.get("retry-after")
        if retry_after:
            return True
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        message = str(payload.get("message", "")).lower()
        return "secondary rate limit" in message or "rate limit" in message
