from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _first_nonempty_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://feature_factory:feature_factory@127.0.0.1:55432/feature_factory"
    server_observer_mode: bool = False
    database_pool_size: int = Field(default=24, ge=1, le=256)
    database_max_overflow: int = Field(default=24, ge=0, le=256)
    database_pool_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    database_pool_recycle_seconds: int = Field(default=1800, ge=30, le=86400)
    stage1_max_concurrent_jobs: int = Field(default=4, ge=1, le=24)
    stage1_max_concurrent_jobs_cap: int = Field(default=24, ge=1, le=24)
    stage1_default_max_concurrent_partitions_per_job: int = Field(default=8, ge=1, le=16)
    stage1_max_concurrent_partitions_per_job_cap: int = Field(default=16, ge=1, le=16)
    stage2_max_concurrent_runs: int = Field(default=24, ge=1, le=256)
    stage2_max_concurrent_runs_cap: int = Field(default=256, ge=1, le=256)
    stage2_default_task_max_concurrent_runs: int | None = Field(default=4, ge=1, le=256)
    stage2_enable_checkpoints: bool = True
    stage2_workspace_dir: Path = Path("./data/stage2")
    stage3_workspace_dir: Path = Path("./data/stage3")
    default_data_pool_name: str = "默认数据池"
    default_data_pool_root: Path = Path("./data-pools/default")
    stage3_max_concurrent_runs: int = Field(default=24, ge=1, le=256)
    stage3_max_concurrent_runs_cap: int = Field(default=256, ge=1, le=256)
    stage3_default_task_max_concurrent_runs: int | None = Field(default=4, ge=1, le=256)
    stage3_enable_checkpoints: bool = True
    stage3_runtime_image_auto_prune: bool = True
    stage3_runtime_image_retention_days: float = Field(default=7.0, ge=0.0)
    stage3_runtime_image_prune_interval_days: float = Field(default=1.0, gt=0.0)
    stage3_build_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=7200.0)
    stage3_run_test_timeout_seconds: float = Field(default=300.0, ge=10.0, le=7200.0)
    stage3_full_validation_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=28800.0)
    stage3_entry_pass_rate_ceiling: float = Field(default=0.5, ge=0.0, le=1.0)
    stage3_min_removed_code_lines: int = Field(default=10, ge=0, le=10000)
    stage3_agent_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=14400.0)
    stage3_openhands_sdk_root: Path = Path("./software-agent-sdk")
    stage3_openhands_preset: Literal["default", "gpt5"] = "default"
    stage3_openhands_max_iterations: int = Field(default=150, ge=10, le=1000)
    openhands_enable_encrypted_reasoning: bool = True
    llm_ssl_verify: bool | Path = Field(
        default=True,
        validation_alias=AliasChoices(
            "llm_ssl_verify",
            "FEATURE_FACTORY_LLM_SSL_VERIFY",
            "SSL_VERIFY",
        ),
    )
    stage3_llm_model: str | None = None
    stage3_llm_base_url: str | None = None
    stage3_llm_api_key: SecretStr | None = None
    stage4_workspace_dir: Path = Path("./data/stage4")
    stage4_max_concurrent_runs: int = Field(default=24, ge=1, le=256)
    stage4_max_concurrent_runs_cap: int = Field(default=256, ge=1, le=256)
    stage4_default_task_max_concurrent_runs: int | None = Field(default=4, ge=1, le=256)
    stage4_build_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=7200.0)
    stage4_agent_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=14400.0)
    stage4_openhands_sdk_root: Path = Path("./software-agent-sdk")
    stage4_openhands_preset: Literal["default", "gpt5"] = "default"
    stage4_openhands_max_iterations: int = Field(default=150, ge=10, le=1000)
    stage4_issue_styles: str | None = None
    stage4_llm_model: str | None = None
    stage4_llm_base_url: str | None = None
    stage4_llm_api_key: SecretStr | None = None
    stage2_quickcheck_sample_size: int = Field(default=10, ge=1, le=100)
    stage2_max_worker_attempts: int = Field(default=10, ge=1, le=10)
    stage2_entry_file_test_count_min: int = Field(default=-1, ge=-1)
    stage2_p2p_file_count_limit: int | None = Field(default=None, ge=1)
    stage2_p2p_sample_seed: str | None = None
    stage2_collect_timeout_seconds: float = Field(default=300.0, ge=10.0, le=7200.0)
    stage2_run_test_timeout_seconds: float = Field(default=300.0, ge=10.0, le=7200.0)
    stage2_build_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=7200.0)
    stage2_full_validation_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=28800.0)
    stage2_agent_timeout_seconds: float = Field(default=2400.0, ge=30.0, le=14400.0)
    stage2_planner_agent_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    stage2_worker_agent_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    stage2_docker_image_prefix: str = "feature-factory-stage2"
    stage2_openhands_sdk_root: Path = Path("./software-agent-sdk")
    stage2_openhands_preset: Literal["default", "gpt5"] = "default"
    stage2_openhands_max_iterations: int = Field(default=150, ge=10, le=1000)
    stage2_llm_model: str | None = None
    stage2_llm_base_url: str | None = None
    stage2_llm_api_key: SecretStr | None = None
    stage2_planner_openhands_preset: Literal["default", "gpt5"] | None = None
    stage2_planner_openhands_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    stage2_planner_llm_model: str | None = None
    stage2_planner_llm_base_url: str | None = None
    stage2_planner_llm_api_key: SecretStr | None = None
    stage2_worker_openhands_preset: Literal["default", "gpt5"] | None = None
    stage2_worker_openhands_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    stage2_worker_llm_model: str | None = None
    stage2_worker_llm_base_url: str | None = None
    stage2_worker_llm_api_key: SecretStr | None = None
    huawei_llm_model: str | None = None
    huawei_llm_base_url: str | None = None
    huawei_llm_api_key: SecretStr | None = None
    local_postgres_container_name: str = "feature-factory-postgres"
    local_postgres_image: str = "postgres:16-alpine"
    local_postgres_data_dir: Path = Path("./data/postgres")
    local_postgres_startup_timeout_seconds: float = Field(default=30.0, ge=1.0, le=300.0)
    sqlite_busy_timeout_seconds: float = Field(default=30.0, ge=0.0)
    sqlite_enable_wal: bool = True

    github_token: SecretStr | None = None
    github_api_base_url: str = "https://api.github.com"
    github_api_version: str = "2022-11-28"
    github_user_agent: str = "FeatureFactory/0.1"
    github_http_timeout_seconds: float = 30.0
    github_incomplete_results_retries: int = Field(default=3, ge=0, le=10)
    github_page_size: int = Field(default=100, ge=1, le=100)
    github_partition_result_limit: int = Field(default=900, ge=1, le=1000)
    github_partition_probe_max_span_days: int = Field(default=365, ge=0, le=3650)
    github_rate_limit_wait_seconds: float = Field(default=60.0, ge=0.0)
    github_max_concurrent_requests: int = Field(default=64, ge=1, le=100)

    model_config = SettingsConfigDict(
        env_prefix="FEATURE_FACTORY_",
        env_file=".env",
        extra="ignore",
    )

    def __init__(self, **values: Any) -> None:
        if "_env_file" not in values and _env_flag_enabled("FEATURE_FACTORY_DISABLE_DOTENV"):
            values["_env_file"] = None
        super().__init__(**values)

    @model_validator(mode="after")
    def validate_stage1_concurrency(self) -> "Settings":
        legacy_stage2_command_timeout = os.getenv("FEATURE_FACTORY_STAGE2_COMMAND_TIMEOUT_SECONDS", "").strip()
        if legacy_stage2_command_timeout:
            try:
                legacy_timeout_value = max(float(legacy_stage2_command_timeout), 10.0)
            except ValueError:
                legacy_timeout_value = None
            if legacy_timeout_value is not None:
                if "stage2_collect_timeout_seconds" not in self.model_fields_set:
                    self.stage2_collect_timeout_seconds = legacy_timeout_value
                if "stage2_run_test_timeout_seconds" not in self.model_fields_set:
                    self.stage2_run_test_timeout_seconds = legacy_timeout_value
        if self.stage2_llm_model is None:
            llm_model = self.huawei_llm_model or _first_nonempty_env(
                "LLM_MODEL",
                "LLM_MODEL_NAME",
                "FEATURE_FACTORY_LLM_MODEL",
                "FEATURE_FACTORY_LLM_MODEL_NAME",
            )
            if llm_model:
                self.stage2_llm_model = llm_model
        if self.stage2_llm_base_url is None:
            llm_base_url = self.huawei_llm_base_url or _first_nonempty_env(
                "LLM_BASE_URL",
                "LLM_API_BASE_URL",
                "LLM_API_BASE",
                "OPENAI_BASE_URL",
                "FEATURE_FACTORY_LLM_BASE_URL",
                "FEATURE_FACTORY_LLM_API_BASE_URL",
            )
            if llm_base_url:
                self.stage2_llm_base_url = llm_base_url
        if self.stage2_llm_api_key is None:
            llm_api_key = _first_nonempty_env(
                "LLM_API_KEY",
                "OPENAI_API_KEY",
                "FEATURE_FACTORY_LLM_API_KEY",
            )
            if self.huawei_llm_api_key is not None:
                self.stage2_llm_api_key = self.huawei_llm_api_key
            elif llm_api_key:
                self.stage2_llm_api_key = SecretStr(llm_api_key)
        if self.stage3_llm_model is None:
            llm_model = self.huawei_llm_model or _first_nonempty_env(
                "LLM_MODEL",
                "LLM_MODEL_NAME",
                "FEATURE_FACTORY_LLM_MODEL",
                "FEATURE_FACTORY_LLM_MODEL_NAME",
            )
            if llm_model:
                self.stage3_llm_model = llm_model
        if self.stage3_llm_base_url is None:
            llm_base_url = self.huawei_llm_base_url or _first_nonempty_env(
                "LLM_BASE_URL",
                "LLM_API_BASE_URL",
                "LLM_API_BASE",
                "OPENAI_BASE_URL",
                "FEATURE_FACTORY_LLM_BASE_URL",
                "FEATURE_FACTORY_LLM_API_BASE_URL",
            )
            if llm_base_url:
                self.stage3_llm_base_url = llm_base_url
        if self.stage3_llm_api_key is None:
            llm_api_key = _first_nonempty_env(
                "LLM_API_KEY",
                "OPENAI_API_KEY",
                "FEATURE_FACTORY_LLM_API_KEY",
            )
            if self.huawei_llm_api_key is not None:
                self.stage3_llm_api_key = self.huawei_llm_api_key
            elif llm_api_key:
                self.stage3_llm_api_key = SecretStr(llm_api_key)
        if self.stage4_llm_model is None:
            self.stage4_llm_model = self.stage3_llm_model or self.stage2_llm_model
            llm_model = self.huawei_llm_model or _first_nonempty_env(
                "LLM_MODEL",
                "LLM_MODEL_NAME",
                "FEATURE_FACTORY_LLM_MODEL",
                "FEATURE_FACTORY_LLM_MODEL_NAME",
            )
            if self.stage4_llm_model is None and llm_model:
                self.stage4_llm_model = llm_model
        if self.stage4_llm_base_url is None:
            self.stage4_llm_base_url = self.stage3_llm_base_url or self.stage2_llm_base_url
            llm_base_url = self.huawei_llm_base_url or _first_nonempty_env(
                "LLM_BASE_URL",
                "LLM_API_BASE_URL",
                "LLM_API_BASE",
                "OPENAI_BASE_URL",
                "FEATURE_FACTORY_LLM_BASE_URL",
                "FEATURE_FACTORY_LLM_API_BASE_URL",
            )
            if self.stage4_llm_base_url is None and llm_base_url:
                self.stage4_llm_base_url = llm_base_url
        if self.stage4_llm_api_key is None:
            self.stage4_llm_api_key = self.stage3_llm_api_key or self.stage2_llm_api_key
            llm_api_key = _first_nonempty_env(
                "LLM_API_KEY",
                "OPENAI_API_KEY",
                "FEATURE_FACTORY_LLM_API_KEY",
            )
            if self.stage4_llm_api_key is None and self.huawei_llm_api_key is not None:
                self.stage4_llm_api_key = self.huawei_llm_api_key
            elif self.stage4_llm_api_key is None and llm_api_key:
                self.stage4_llm_api_key = SecretStr(llm_api_key)
        if (
            self.stage1_default_max_concurrent_partitions_per_job
            > self.stage1_max_concurrent_partitions_per_job_cap
        ):
            raise ValueError(
                "stage1_default_max_concurrent_partitions_per_job cannot exceed "
                "stage1_max_concurrent_partitions_per_job_cap"
            )
        if (
            self.stage2_default_task_max_concurrent_runs is not None
            and self.stage2_default_task_max_concurrent_runs > self.stage2_max_concurrent_runs
        ):
            raise ValueError("stage2_default_task_max_concurrent_runs cannot exceed stage2_max_concurrent_runs")
        if (
            self.stage3_default_task_max_concurrent_runs is not None
            and self.stage3_default_task_max_concurrent_runs > self.stage3_max_concurrent_runs
        ):
            raise ValueError("stage3_default_task_max_concurrent_runs cannot exceed stage3_max_concurrent_runs")
        if (
            self.stage4_default_task_max_concurrent_runs is not None
            and self.stage4_default_task_max_concurrent_runs > self.stage4_max_concurrent_runs
        ):
            raise ValueError("stage4_default_task_max_concurrent_runs cannot exceed stage4_max_concurrent_runs")
        return self

    def stage2_agent_openhands_preset(self, agent: Literal["planner", "worker"]) -> Literal["default", "gpt5"]:
        if agent == "planner" and self.stage2_planner_openhands_preset is not None:
            return self.stage2_planner_openhands_preset
        if agent == "worker" and self.stage2_worker_openhands_preset is not None:
            return self.stage2_worker_openhands_preset
        return self.stage2_openhands_preset

    def stage2_agent_openhands_max_iterations(self, agent: Literal["planner", "worker"]) -> int:
        if agent == "planner" and self.stage2_planner_openhands_max_iterations is not None:
            return self.stage2_planner_openhands_max_iterations
        if agent == "worker" and self.stage2_worker_openhands_max_iterations is not None:
            return self.stage2_worker_openhands_max_iterations
        return self.stage2_openhands_max_iterations

    def stage2_agent_timeout(self, agent: Literal["planner", "worker"]) -> float:
        if agent == "planner" and self.stage2_planner_agent_timeout_seconds is not None:
            return self.stage2_planner_agent_timeout_seconds
        if agent == "worker" and self.stage2_worker_agent_timeout_seconds is not None:
            return self.stage2_worker_agent_timeout_seconds
        return self.stage2_agent_timeout_seconds

    def stage2_agent_llm_model(self, agent: Literal["planner", "worker"]) -> str | None:
        if agent == "planner" and self.stage2_planner_llm_model:
            return self.stage2_planner_llm_model
        if agent == "worker" and self.stage2_worker_llm_model:
            return self.stage2_worker_llm_model
        return self.stage2_llm_model

    def stage2_agent_llm_base_url(self, agent: Literal["planner", "worker"]) -> str | None:
        if agent == "planner" and self.stage2_planner_llm_base_url:
            return self.stage2_planner_llm_base_url
        if agent == "worker" and self.stage2_worker_llm_base_url:
            return self.stage2_worker_llm_base_url
        return self.stage2_llm_base_url

    def stage2_agent_llm_api_key(self, agent: Literal["planner", "worker"]) -> SecretStr | None:
        if agent == "planner" and self.stage2_planner_llm_api_key is not None:
            return self.stage2_planner_llm_api_key
        if agent == "worker" and self.stage2_worker_llm_api_key is not None:
            return self.stage2_worker_llm_api_key
        return self.stage2_llm_api_key

    def stage3_agent_openhands_preset(self) -> Literal["default", "gpt5"]:
        return self.stage3_openhands_preset

    def stage3_agent_openhands_max_iterations(self) -> int:
        return self.stage3_openhands_max_iterations

    def stage3_agent_timeout(self) -> float:
        return self.stage3_agent_timeout_seconds

    def stage3_agent_llm_model(self) -> str | None:
        return self.stage3_llm_model

    def stage3_agent_llm_base_url(self) -> str | None:
        return self.stage3_llm_base_url

    def stage3_agent_llm_api_key(self) -> SecretStr | None:
        return self.stage3_llm_api_key

    def stage4_agent_openhands_preset(self) -> Literal["default", "gpt5"]:
        return self.stage4_openhands_preset

    def stage4_agent_openhands_max_iterations(self) -> int:
        return self.stage4_openhands_max_iterations

    def stage4_agent_timeout(self) -> float:
        return self.stage4_agent_timeout_seconds

    def stage4_enabled_issue_style_ids(self) -> list[str] | None:
        raw = str(self.stage4_issue_styles or "").strip()
        if not raw:
            return None
        styles = [value.strip() for value in raw.split(",")]
        if any(not value for value in styles):
            raise ValueError("stage4_issue_styles must be a comma-separated list without empty values")
        if len(set(styles)) != len(styles):
            raise ValueError("stage4_issue_styles must not contain duplicates")
        return styles

    def stage4_agent_llm_model(self) -> str | None:
        return self.stage4_llm_model

    def stage4_agent_llm_base_url(self) -> str | None:
        return self.stage4_llm_base_url

    def stage4_agent_llm_api_key(self) -> SecretStr | None:
        return self.stage4_llm_api_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
