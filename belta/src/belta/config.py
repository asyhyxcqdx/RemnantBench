from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
import os

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProjectConfig(BaseModel):
    run_name: str = Field(min_length=1)


class FeatureFactoryConfig(BaseModel):
    mode: Literal["library"] = "library"
    root: Path
    database_url: str
    auto_manage_postgres: bool = True
    postgres_container_name: str = "belta-ff-postgres"
    postgres_data_dir: Path = Path("data/cache/featurefactory/postgres")


class RepoDiscoveryConfig(BaseModel):
    backend: Literal["featurefactory_stage1"] = "featurefactory_stage1"
    name: str = Field(min_length=1)
    target_repositories: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    created_after: datetime | None = None
    created_before: datetime | None = None
    pushed_after: datetime | None = None
    pushed_before: datetime | None = None
    stars_min: int | None = Field(default=None, ge=0)
    stars_max: int | None = Field(default=None, ge=0)
    exclude_forks: bool = True
    exclude_archived: bool = True
    public_only: bool = True
    sort: Literal["updated", "stars"] = "updated"
    order: Literal["desc", "asc"] = "desc"
    max_concurrent_partitions: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def validate_mode(self) -> "RepoDiscoveryConfig":
        if not self.target_repositories and self.created_after is None:
            raise ValueError("repo_discovery.created_after is required when target_repositories is empty")
        return self


class GitHubConfig(BaseModel):
    token: str | None = None
    token_env: str = "GITHUB_TOKEN"

    @model_validator(mode="after")
    def validate_token_source(self) -> "GitHubConfig":
        if self.token and self.token.strip():
            self.token = self.token.strip()
            return self
        token = os.getenv(self.token_env, "").strip()
        if not token:
            raise ValueError(f"github.token is empty and environment variable {self.token_env} is not set")
        self.token = token
        return self


class CandidateOffsetRangeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int = Field(ge=1)
    stop: int = Field(ge=1)
    step: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_bounds(self) -> "CandidateOffsetRangeConfig":
        if self.stop < self.start:
            raise ValueError("candidate_pairs.offset_ranges[].stop must be >= start")
        return self

    def values(self) -> range:
        return range(self.start, self.stop + 1, self.step)


class CandidatePairsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset_ranges: list[CandidateOffsetRangeConfig] = Field(
        default_factory=lambda: [
            CandidateOffsetRangeConfig(start=1, stop=200, step=1)
        ],
        min_length=1,
    )
    max_concurrent_repos: int = Field(default=4, ge=1)
    max_concurrent_repo_downloads: int = Field(default=4, ge=1)
    min_implementation_units: int = Field(default=1, ge=0)
    min_reinsert_source_lines: int = Field(default=5, ge=0)
    max_implementation_units: int | None = Field(default=None, ge=0)
    max_reinsert_source_lines: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> "CandidatePairsConfig":
        values = self.offset_values()
        if any(current <= previous for previous, current in zip(values, values[1:])):
            raise ValueError(
                "candidate_pairs.offset_ranges must produce strictly increasing, "
                "non-overlapping offsets"
            )
        return self

    def offset_values(self) -> tuple[int, ...]:
        return tuple(
            offset
            for offset_range in self.offset_ranges
            for offset in offset_range.values()
        )


class RetireTaskConstructionConfig(BaseModel):
    max_concurrent_repo_baselines: int = Field(default=32, ge=1)
    max_concurrent_pairs: int = Field(default=32, ge=1)


class BeltaConfig(BaseModel):
    project: ProjectConfig
    featurefactory: FeatureFactoryConfig
    repo_discovery: RepoDiscoveryConfig
    github: GitHubConfig
    candidate_pairs: CandidatePairsConfig = Field(default_factory=CandidatePairsConfig)
    retire_task_construction: RetireTaskConstructionConfig = Field(
        default_factory=RetireTaskConstructionConfig
    )


def load_config(path: Path) -> BeltaConfig:
    load_dotenv(Path.cwd() / ".env")
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a YAML object: {path}")
    return BeltaConfig.model_validate(payload)


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
