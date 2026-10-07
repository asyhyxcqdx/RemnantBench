from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class CrawlFilters(BaseModel):
    language: str | None = None
    languages: list[str] = Field(default_factory=list)
    created_after: datetime
    created_before: datetime | None = None
    pushed_after: datetime | None = None
    pushed_before: datetime | None = None
    stars_min: int | None = Field(default=None, ge=0)
    stars_max: int | None = Field(default=None, ge=0)
    repository_limit: int | None = Field(default=None, ge=1)
    exclude_forks: bool = True
    exclude_archived: bool = True
    public_only: bool = True
    licenses: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    target_repositories: list[str] = Field(default_factory=list)
    sort: Literal["updated", "stars"] = "updated"
    order: Literal["desc", "asc"] = "desc"

    @field_validator("created_after", "created_before", "pushed_after", "pushed_before", mode="before")
    @classmethod
    def parse_datetime(cls, value: datetime | str | None) -> datetime | None:
        if value is None or isinstance(value, datetime):
            return value
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("languages", mode="before")
    @classmethod
    def normalize_languages_input(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return list(value)

    @field_validator("licenses", "keywords", "target_repositories", mode="before")
    @classmethod
    def normalize_list_input(cls, value: str | list[str] | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        return list(value)

    @field_validator("languages")
    @classmethod
    def normalize_languages(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @field_validator("licenses")
    @classmethod
    def normalize_licenses(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip().lower()
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip()
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @field_validator("target_repositories")
    @classmethod
    def normalize_target_repositories(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            candidate = item.strip().strip("/")
            if candidate.startswith("https://github.com/"):
                candidate = candidate.removeprefix("https://github.com/").strip("/")
            elif candidate.startswith("http://github.com/"):
                candidate = candidate.removeprefix("http://github.com/").strip("/")
            parts = [part.strip() for part in candidate.split("/") if part.strip()]
            if len(parts) != 2:
                raise ValueError("target_repositories must use owner/repo format")
            owner, repo = parts
            if any(char in owner + repo for char in "?#\\"):
                raise ValueError("target_repositories must use owner/repo format")
            candidate = f"{owner}/{repo}"
            key = candidate.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(candidate)
        return normalized

    @field_validator("created_after", "created_before", "pushed_after", "pushed_before")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return ensure_utc(value).replace(microsecond=0)

    @model_validator(mode="after")
    def validate_ranges(self) -> "CrawlFilters":
        merged_languages: list[str] = []
        seen_languages: set[str] = set()
        for candidate in ([self.language] if self.language else []) + self.languages:
            if not candidate:
                continue
            key = candidate.casefold()
            if key in seen_languages:
                continue
            seen_languages.add(key)
            merged_languages.append(candidate)
        self.languages = merged_languages
        self.language = merged_languages[0] if len(merged_languages) == 1 else None

        created_before = self.created_before or datetime.now(UTC).replace(microsecond=0)
        if self.created_after > created_before:
            raise ValueError("created_after must be earlier than or equal to created_before")
        if self.pushed_after and self.pushed_before and self.pushed_after > self.pushed_before:
            raise ValueError("pushed_after must be earlier than or equal to pushed_before")
        if self.stars_min is not None and self.stars_max is not None and self.stars_min > self.stars_max:
            raise ValueError("stars_min must be less than or equal to stars_max")
        self.created_before = created_before
        return self


class TimePartition(BaseModel):
    start: datetime
    end: datetime
    depth: int = 0
    expected_count: int = 0

    @field_validator("start", "end")
    @classmethod
    def normalize_bound(cls, value: datetime) -> datetime:
        return ensure_utc(value).replace(microsecond=0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "TimePartition":
        if self.start > self.end:
            raise ValueError("partition start must be earlier than or equal to partition end")
        return self


class SearchResponse(BaseModel):
    total_count: int
    incomplete_results: bool = False
    items: list[dict]