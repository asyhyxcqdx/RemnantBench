from __future__ import annotations

from datetime import UTC, datetime

from feature_factory.stage1.schemas import CrawlFilters, TimePartition


def format_github_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_search_keyword(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return ""
    escaped = normalized.replace('"', '\\"')
    if any(char.isspace() for char in normalized):
        return f'"{escaped}"'
    return escaped


def _build_common_terms(filters: CrawlFilters, partition: TimePartition) -> list[str]:
    terms: list[str] = []

    if filters.public_only:
        terms.append("is:public")
    if filters.exclude_forks:
        terms.append("fork:false")
    if filters.exclude_archived:
        terms.append("archived:false")

    terms.append(
        f"created:{format_github_datetime(partition.start)}..{format_github_datetime(partition.end)}"
    )

    if filters.pushed_after and filters.pushed_before:
        terms.append(
            f"pushed:{format_github_datetime(filters.pushed_after)}..{format_github_datetime(filters.pushed_before)}"
        )
    elif filters.pushed_after:
        terms.append(f"pushed:>={format_github_datetime(filters.pushed_after)}")
    elif filters.pushed_before:
        terms.append(f"pushed:<={format_github_datetime(filters.pushed_before)}")

    if filters.stars_min is not None and filters.stars_max is not None:
        terms.append(f"stars:{filters.stars_min}..{filters.stars_max}")
    elif filters.stars_min is not None:
        terms.append(f"stars:>={filters.stars_min}")
    elif filters.stars_max is not None:
        terms.append(f"stars:<={filters.stars_max}")

    terms.extend(
        formatted
        for formatted in (format_search_keyword(keyword) for keyword in filters.keywords)
        if formatted
    )

    return terms


def build_search_queries(filters: CrawlFilters, partition: TimePartition) -> list[str]:
    base_terms = _build_common_terms(filters, partition)
    queries: list[str] = []
    language_terms = [f"language:{language}" for language in filters.languages] or [None]
    license_terms = [f"license:{license_key}" for license_key in filters.licenses] or [None]

    for language_term in language_terms:
        for license_term in license_terms:
            terms = [*base_terms]
            if language_term:
                terms.append(language_term)
            if license_term:
                terms.append(license_term)
            queries.append(" ".join(terms))
    return queries


def build_search_query(filters: CrawlFilters, partition: TimePartition) -> str:
    queries = build_search_queries(filters, partition)
    if len(queries) == 1:
        return queries[0]
    return " OR ".join(queries)
