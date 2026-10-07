from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
import json
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import cmp_to_key
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, select
from starlette.background import BackgroundTask
from feature_factory.batch import (
    BatchTaskDeleteConflictError,
    BatchTaskRetryConflictError,
    BatchTaskService,
)
from feature_factory.batch_runner import BatchTaskRunner
from feature_factory.admin_runner import Stage1JobRunner
from feature_factory.admin_ui import INDEX_HTML
from feature_factory.config import Settings, get_settings
from feature_factory.data_pool import DataPoolDeleteConflictError, DataPoolService
from feature_factory.db import InitDbRepairCleanupError, build_engine, build_session_factory, init_db
from feature_factory.local_postgres import managed_local_postgres
from feature_factory.models import (
    BatchTask,
    CrawlJob,
    CrawlJobStatus,
    CrawlPartition,
    CrawlPartitionStatus,
    BatchTaskStatus,
    GitHubRepository,
    GlobalRuntimeConfig,
    Stage1CrawlTemplate,
    Stage2Run,
    Stage2RunStatus,
    Stage2RuntimeTemplate,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage3RuntimeTemplate,
    Stage4Run,
    Stage4RunStatus,
    Stage4RuntimeTemplate,
)
from feature_factory.stage2.base_images import (
    FEATURE_FACTORY_STAGE2_BASE_IMAGE_KIND,
    FEATURE_FACTORY_STAGE2_KIND_LABEL,
)
from feature_factory.stage2.runner import Stage2RunRunner
from feature_factory.stage2.image_assets import (
    FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY,
    FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL,
    FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND,
    PLANNER_AGENT_SERVER_BASE_IMAGE,
    agent_server_build_plan,
    build_stage2_image_assets,
    ensure_agent_server_image_built,
    inspect_docker_image,
    labels_match,
    prewarm_host_sdk_environment,
    prewarm_planner_agent_server_image,
    stage2_sdk_prewarm_status,
    stage2_image_asset_status,
)
from feature_factory.stage2.raw_archive import (
    legacy_llm_completion_archive_dir,
    llm_completion_archive_dir,
    runtime_dir_from_workspace_path,
)
from feature_factory.stage2.service import (
    Stage2CommitResolutionError,
    Stage2CommitUnchangedError,
    Stage2RunDeleteConflictError,
    Stage2RunInterruptConflictError,
    Stage2RunRuntimeConflictError,
    Stage2WorkerRerunConflictError,
    Stage2WorkerResumeConflictError,
    Stage2Service,
    resolve_repo_head_commit,
)
from feature_factory.stage3.service import (
    Stage3RunConflictError,
    Stage3Service,
)
from feature_factory.stage3.runtime_images import (
    FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_KIND,
    FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY,
    FEATURE_FACTORY_STAGE3_KIND_LABEL,
    FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL,
    detect_stage3_platform,
    ensure_stage3_breaker_runtime_image_built,
    stage3_breaker_runtime_image_ref,
    stage3_breaker_runtime_image_status,
)
from feature_factory.stage3.runner import Stage3RunRunner
from feature_factory.stage4.runner import Stage4RunRunner
from feature_factory.stage4.issue_styles import (
    IssueStyleConfigError,
    load_issue_style_catalog,
    runtime_issue_style_specs,
)
from feature_factory.stage4.service import (
    STAGE4_RUN_SORT_FIELDS,
    STAGE4_SOURCE_DETAIL_SORT_FIELDS,
    STAGE4_SOURCE_GROUP_SORT_FIELDS,
    Stage4RunConflictError,
    Stage4Service,
)
from feature_factory.stage1.github_client import GitHubSearchClient
from feature_factory.stage1.schemas import CrawlFilters
from feature_factory.stage1.service import CrawlService
from feature_factory.stage1.token_scheduler import GitHubTokenScheduler

REPO_SORT_FIELDS = {
    "full_name": GitHubRepository.full_name,
    "primary_language": GitHubRepository.primary_language,
    "stargazers_count": GitHubRepository.stargazers_count,
    "created_at_github": GitHubRepository.created_at_github,
    "pushed_at_github": GitHubRepository.pushed_at_github,
    "discovered_at": GitHubRepository.discovered_at,
}
STAGE2_REPO_SORT_FIELDS = {
    **REPO_SORT_FIELDS,
    "latest_operation_at": None,
}
STAGE3_REPO_SORT_FIELDS = {
    **REPO_SORT_FIELDS,
    "eligible_commit_count": None,
    "latest_operation_at": None,
    "produced_entry_file_count": None,
    "produced_data_count": None,
    "status": None,
}
STAGE3_REPO_STATUS_ORDER = {
    "pending": 0,
    "queued": 1,
    "running": 2,
    "succeeded": 3,
    "failed": 4,
    "interrupted": 5,
}

STAGE1_ACTIVE_JOB_STATUSES = {
    CrawlJobStatus.queued.value,
    CrawlJobStatus.planning.value,
    CrawlJobStatus.running.value,
}
SSE_POLL_INTERVAL_SECONDS = 1.0
SSE_KEEPALIVE_INTERVAL_SECONDS = 15.0
FRONTEND_SESSION_ID = uuid4().hex
APP_SHUTDOWN_RUNNER_WAIT_SECONDS = 5.0
STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED = "feature_factory.managed"
STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE = "feature_factory.stage"
STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT = "feature_factory.component"
STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID = "feature_factory.run_id"
STAGE2_AGENT_SERVER_DOCKER_LABEL_APP_INSTANCE_ID = "feature_factory.app_instance_id"
CURRENT_STAGE2_RUNTIME_CONFIG_KEY = "stage2_current_runtime"
CURRENT_STAGE3_RUNTIME_CONFIG_KEY = "stage3_current_runtime"
CURRENT_STAGE4_RUNTIME_CONFIG_KEY = "stage4_current_runtime"
FEATURE_FACTORY_STAGE3_EVAL_COMPONENT = "stage3-eval"
FEATURE_FACTORY_STAGE3_EVAL_CONTAINER_NAME_PREFIX = "feature-factory-stage3-eval-"
FEATURE_FACTORY_STAGE2_WORKER_CHECKPOINT_REPOSITORY = "feature-factory/stage2-worker-checkpoint"
FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_REPOSITORY = "feature-factory/stage3-breaker-checkpoint"
FEATURE_FACTORY_IMAGE_REF_RE = re.compile(
    r"\b(feature-factory(?:/|-)[A-Za-z0-9._/-]+:[A-Za-z0-9][A-Za-z0-9._-]*)\b"
)


def _serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _stage2_repository_ordering(sort_by: str, sort_order: str, latest_runs: Any) -> tuple[Any, Any]:
    if sort_by == "latest_operation_at":
        sort_column = latest_runs.c.latest_operation_at
        ordered = sort_column.asc().nullslast() if sort_order == "asc" else sort_column.desc().nullslast()
    else:
        sort_column = REPO_SORT_FIELDS.get(sort_by, GitHubRepository.discovered_at)
        ordered = sort_column.asc() if sort_order == "asc" else sort_column.desc()
    tie_breaker = GitHubRepository.id.asc() if sort_order == "asc" else GitHubRepository.id.desc()
    return ordered, tie_breaker


def _normalize_stage3_status_filters(values: list[str] | None) -> list[str]:
    allowed = set(STAGE3_REPO_STATUS_ORDER.keys())
    normalized: list[str] = []
    for value in values or []:
        key = str(value or "").strip()
        if not key or key not in allowed or key in normalized:
            continue
        normalized.append(key)
    return normalized


def _stage3_repository_sort_value(row: dict[str, Any], sort_by: str) -> Any:
    stage3 = dict(row.get("stage3") or {})
    if sort_by == "eligible_commit_count":
        return int(stage3.get("eligible_commit_count") or 0)
    if sort_by == "latest_operation_at":
        return str(stage3.get("latest_operation_at") or "") or None
    if sort_by == "produced_entry_file_count":
        return (
            float(stage3.get("produced_entry_file_ratio") or 0.0),
            int(stage3.get("produced_entry_file_count") or 0),
            int(stage3.get("entry_file_count") or 0),
        )
    if sort_by == "produced_data_count":
        return int(stage3.get("produced_data_count") or 0)
    if sort_by == "status":
        return int(STAGE3_REPO_STATUS_ORDER.get(str(stage3.get("status") or ""), -1))
    if sort_by == "stargazers_count":
        return int(row.get("stargazers_count") or 0)
    if sort_by in {"created_at_github", "pushed_at_github", "discovered_at"}:
        return str(row.get(sort_by) or "") or None
    return str(row.get(sort_by) or "").lower()


def _compare_stage3_repository_rows(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    sort_by: str,
    sort_order: str,
) -> int:
    def compare_values(left_value: Any, right_value: Any, *, descending: bool = False) -> int:
        if left_value is None and right_value is None:
            return 0
        if left_value is None:
            return 1
        if right_value is None:
            return -1
        if left_value < right_value:
            return -1 if not descending else 1
        if left_value > right_value:
            return 1 if not descending else -1
        return 0

    primary = compare_values(
        _stage3_repository_sort_value(left, sort_by),
        _stage3_repository_sort_value(right, sort_by),
        descending=sort_order == "desc",
    )
    if primary != 0:
        return primary
    name_tie_breaker = compare_values(
        str(left.get("full_name") or "").lower(),
        str(right.get("full_name") or "").lower(),
    )
    if name_tie_breaker != 0:
        return name_tie_breaker
    return compare_values(int(left.get("id") or 0), int(right.get("id") or 0))


def _job_can_resume(job: CrawlJob, *, is_running: bool = False) -> bool:
    if is_running:
        return False
    if job.status in {
        CrawlJobStatus.queued.value,
        CrawlJobStatus.planning.value,
        CrawlJobStatus.completed.value,
    }:
        return False

    stats = job.stats_json or {}
    if isinstance(stats.get("planning_checkpoint"), dict):
        return True

    raw_partition_counts = stats.get("partition_status_counts")
    partition_counts = raw_partition_counts if isinstance(raw_partition_counts, dict) else {}
    pending_like = int(partition_counts.get(CrawlPartitionStatus.pending.value, 0)) + int(
        partition_counts.get(CrawlPartitionStatus.failed.value, 0)
    )
    if pending_like > 0:
        return True

    if job.status in {
        CrawlJobStatus.pending.value,
        CrawlJobStatus.paused.value,
    }:
        return True

    if job.status in {
        CrawlJobStatus.failed.value,
        CrawlJobStatus.partial.value,
    }:
        return sum(int(count) for count in partition_counts.values()) == 0

    return False


def _static_asset_version(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return FRONTEND_SESSION_ID
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


def _job_can_pause(*, is_running: bool = False, pause_requested: bool = False) -> bool:
    return is_running and not pause_requested


def _job_can_delete(*, is_running: bool = False) -> bool:
    return not is_running


def _serialize_job(
    job: CrawlJob,
    *,
    is_running: bool = False,
    pause_requested: bool = False,
) -> dict[str, Any]:
    return {
        "id": job.id,
        "name": job.name,
        "status": job.status,
        "filters": job.filters_json,
        "stats": job.stats_json or {},
        "error_message": job.error_message,
        "created_at": _serialize_datetime(job.created_at),
        "updated_at": _serialize_datetime(job.updated_at),
        "started_at": _serialize_datetime(job.started_at),
        "finished_at": _serialize_datetime(job.finished_at),
        "is_running": is_running,
        "pause_requested": pause_requested,
        "can_resume": _job_can_resume(job, is_running=is_running),
        "can_pause": _job_can_pause(is_running=is_running, pause_requested=pause_requested),
        "can_delete": _job_can_delete(is_running=is_running),
    }


def _serialize_partition(partition: CrawlPartition) -> dict[str, Any]:
    return {
        "id": partition.id,
        "status": partition.status,
        "depth": partition.depth,
        "range_start": _serialize_datetime(partition.range_start),
        "range_end": _serialize_datetime(partition.range_end),
        "query_string": partition.query_string,
        "expected_count": partition.expected_count,
        "fetched_count": partition.fetched_count,
        "unique_count": partition.unique_count,
        "error_message": partition.error_message,
        "started_at": _serialize_datetime(partition.started_at),
        "finished_at": _serialize_datetime(partition.finished_at),
    }


def _serialize_repo(repository: GitHubRepository) -> dict[str, Any]:
    return {
        "github_repo_id": repository.github_repo_id,
        "full_name": repository.full_name,
        "primary_language": repository.primary_language,
        "stargazers_count": repository.stargazers_count,
        "html_url": repository.html_url,
        "created_at_github": _serialize_datetime(repository.created_at_github),
        "pushed_at_github": _serialize_datetime(repository.pushed_at_github),
        "discovered_at": _serialize_datetime(repository.discovered_at),
    }


def _serialize_repository_hit(item: dict[str, Any]) -> dict[str, Any]:
    repository = item["repository"]
    discovery = item["discovery"]
    partition = item["partition"]
    payload = _serialize_repo(repository)
    payload.update(
        {
            "source_query": discovery.query_string,
            "source_partition_id": partition.id,
            "source_range_start": _serialize_datetime(partition.range_start),
            "source_range_end": _serialize_datetime(partition.range_end),
            "job_discovered_at": _serialize_datetime(discovery.discovered_at),
        }
    )
    return payload


def _serialize_sse_event(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _json_signature(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stage2_repository_detail_signature(payload: dict[str, Any]) -> str:
    return _json_signature(payload)


def _stage3_repository_detail_signature(payload: dict[str, Any]) -> str:
    return _json_signature(payload)


STAGE2_REPOSITORY_STATUSES = {
    "pending",
    "queued",
    "running",
    "succeeded",
    "abandoned",
    "defect",
    "failed",
}


def _normalize_stage2_status_filters(statuses: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in statuses or []:
        value = candidate.strip().lower()
        if not value:
            continue
        if value not in STAGE2_REPOSITORY_STATUSES:
            raise HTTPException(status_code=400, detail=f"unsupported stage2 status filter: {candidate}")
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _raise_foreground_job_failure(
    *,
    session_factory,
    job_id: str,
    job_runner: Stage1JobRunner,
    message: str,
) -> None:
    session = session_factory()
    try:
        job = session.get(CrawlJob, job_id)
        detail: dict[str, Any] = {"message": message}
        if job is not None:
            detail["job"] = _serialize_job(
                job,
                is_running=job_runner.is_running(job_id),
                pause_requested=job_runner.is_pause_requested(job_id),
            )
        raise HTTPException(status_code=500, detail=detail)
    finally:
        session.close()


def _validate_datetime_range(
    start: datetime | None,
    end: datetime | None,
    *,
    start_name: str,
    end_name: str,
) -> None:
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=400, detail=f"{start_name} must be earlier than or equal to {end_name}")


class CreateCrawlJobRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    filters: CrawlFilters
    run_in_background: bool = True
    max_concurrent_partitions: int | None = Field(default=None, ge=1)


class ResumeCrawlJobRequest(BaseModel):
    max_concurrent_partitions: int | None = Field(default=None, ge=1)
    run_in_background: bool = True


class UpdateStage1RuntimeRequest(BaseModel):
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=24)
    github_tokens: list[str] | None = None


class UpdateStage2RuntimeRequest(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=256)
    planner_model: str | None = Field(default=None, max_length=255)
    planner_base_url: str | None = Field(default=None, max_length=1024)
    planner_api_key: str | None = Field(default=None, max_length=4096)
    planner_preset: Literal["default", "gpt5"] | None = None
    planner_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    worker_model: str | None = Field(default=None, max_length=255)
    worker_base_url: str | None = Field(default=None, max_length=1024)
    worker_api_key: str | None = Field(default=None, max_length=4096)
    worker_preset: Literal["default", "gpt5"] | None = None
    worker_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    agent_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    planner_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    worker_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    max_worker_attempts: int | None = Field(default=None, ge=1, le=10)
    quickcheck_sample_size: int | None = Field(default=None, ge=1, le=100)
    entry_file_test_count_min: int | None = Field(default=None, ge=-1)
    p2p_file_count_limit: int | None = Field(default=None, ge=1)
    collect_timeout_seconds: float | None = Field(default=None, ge=10.0, le=7200.0)
    run_test_timeout_seconds: float | None = Field(default=None, ge=10.0, le=7200.0)
    build_timeout_seconds: float | None = Field(default=None, ge=30.0, le=7200.0)
    full_validation_timeout_seconds: float | None = Field(default=None, ge=30.0, le=28800.0)


class SaveStage2RuntimeTemplateRequest(UpdateStage2RuntimeRequest):
    name: str = Field(min_length=1, max_length=255)


class BuildStage2ImageAssetsRequest(BaseModel):
    image_ids: list[str] = Field(min_length=1)
    include_base_image: bool = True
    include_agent_server_image: bool = True
    force: bool = False


class CancelStage2ImageAssetBuildRequest(BaseModel):
    asset_key: str = Field(min_length=1, max_length=512)


class PrewarmStage2SdkRequest(BaseModel):
    force: bool = False


class DeleteManagedImageRequest(BaseModel):
    image_ref: str = Field(min_length=1, max_length=512)


class DeleteManagedImagesRequest(BaseModel):
    image_refs: list[str] = Field(min_length=1, max_length=500)


class PrewarmStage3SnapshotImagesRequest(BaseModel):
    selected_snapshot_id: str | None = Field(default=None, max_length=64)
    force: bool = False


class UpdateStage3RuntimeRequest(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=256)
    breaker_model: str | None = Field(default=None, max_length=255)
    breaker_base_url: str | None = Field(default=None, max_length=1024)
    breaker_api_key: str | None = Field(default=None, max_length=4096)
    breaker_preset: Literal["default", "gpt5"] | None = None
    breaker_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    breaker_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    build_timeout_seconds: float | None = Field(default=None, ge=30.0, le=7200.0)
    run_test_timeout_seconds: float | None = Field(default=None, ge=10.0, le=7200.0)
    full_validation_timeout_seconds: float | None = Field(default=None, ge=30.0, le=28800.0)
    entry_pass_rate_ceiling: float | None = Field(default=None, ge=0.0, le=1.0)
    min_removed_code_lines: int | None = Field(default=None, ge=0, le=10000)


class SaveStage3RuntimeTemplateRequest(UpdateStage3RuntimeRequest):
    name: str = Field(min_length=1, max_length=255)


class UpdateStage4RuntimeRequest(BaseModel):
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=256)
    issuer_model: str | None = Field(default=None, max_length=255)
    issuer_base_url: str | None = Field(default=None, max_length=1024)
    issuer_api_key: str | None = Field(default=None, max_length=4096)
    issuer_preset: Literal["default", "gpt5"] | None = None
    issuer_max_iterations: int | None = Field(default=None, ge=10, le=1000)
    issuer_timeout_seconds: float | None = Field(default=None, ge=30.0, le=14400.0)
    build_timeout_seconds: float | None = Field(default=None, ge=30.0, le=7200.0)


class SaveStage4RuntimeTemplateRequest(UpdateStage4RuntimeRequest):
    name: str = Field(min_length=1, max_length=255)


class CreateStage4RunRequest(BaseModel):
    source_savepoint_id: int = Field(ge=1)


class CreateDataPoolRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    root_path: str = Field(min_length=1, max_length=4096)
    description: str | None = Field(default=None, max_length=4096)


class UpdateDataPoolRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    root_path: str | None = Field(default=None, min_length=1, max_length=4096)
    description: str | None = Field(default=None, max_length=4096)


class DeleteDataPoolAssetsRequest(BaseModel):
    asset_ids: list[str] | None = None
    query: str | None = Field(default=None, max_length=1024)
    repo: str | None = Field(default=None, max_length=255)
    commit: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=128)
    entry_file: str | None = Field(default=None, max_length=4096)
    depth: int | None = Field(default=None, ge=0)
    depth_min: int | None = Field(default=None, ge=0)
    depth_max: int | None = Field(default=None, ge=0)
    stars_min: int | None = Field(default=None, ge=0)
    stars_max: int | None = Field(default=None, ge=0)
    stage2_run_id: str | None = Field(default=None, max_length=36)
    stage3_run_id: str | None = Field(default=None, max_length=36)
    stage4_run_id: str | None = Field(default=None, max_length=36)
    created_after: datetime | None = None
    created_before: datetime | None = None


class DownloadDataPoolAssetsRequest(BaseModel):
    asset_ids: list[str] | None = None


class CreateDataPoolDownloadSelectionRequest(BaseModel):
    asset_ids: list[str] | None = None


class UpdateBatchGlobalConfigRequest(BaseModel):
    github_tokens: list[str] | None = None


class SaveStage1CrawlTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    github_tokens: list[str] | None = None


class BatchDataPoolSelectionRequest(BaseModel):
    id: str | None = Field(default=None, max_length=36)
    name: str | None = Field(default=None, max_length=255)
    root_path: str | None = Field(default=None, max_length=4096)
    description: str | None = Field(default=None, max_length=4096)


class CreateBatchTaskRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    note: str | None = Field(default=None, max_length=4096)
    stop_after_stage: Literal["stage2", "stage4"] = "stage4"
    data_pool_id: str | None = Field(default=None, max_length=36)
    data_pool: BatchDataPoolSelectionRequest | None = None
    filters: CrawlFilters
    token_source: Literal["global", "temporary"] = "temporary"
    github_tokens: list[str] | None = None
    github_token_template_id: str | None = Field(default=None, max_length=36)
    max_concurrent_jobs: int | None = Field(default=None, ge=1, le=24)
    max_concurrent_partitions: int | None = Field(default=None, ge=1)
    stage2_runtime: UpdateStage2RuntimeRequest | None = None
    stage2_template_id: str | None = Field(default=None, max_length=36)
    stage3_runtime: UpdateStage3RuntimeRequest | None = None
    stage3_template_id: str | None = Field(default=None, max_length=36)
    stage4_runtime: UpdateStage4RuntimeRequest | None = None
    stage4_template_id: str | None = Field(default=None, max_length=36)


class RetryBatchTaskRequest(BaseModel):
    runtime_config_source: Literal["original", "current"] = "original"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_stage1_template_values(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    return normalized


def _token_preview(value: str) -> str:
    token = str(value or "").strip()
    return f"{token[:6]}..." if token else ""


def _stage1_crawl_template_snapshot_from_request(payload: SaveStage1CrawlTemplateRequest) -> dict[str, Any]:
    return {
        "github_tokens": _normalize_stage1_template_values(payload.github_tokens),
    }


def _serialize_stage1_crawl_template_summary(row: Stage1CrawlTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    github_tokens = _normalize_stage1_template_values(snapshot.get("github_tokens"))
    return {
        "id": row.id,
        "name": row.name,
        "github_token_count": len(github_tokens),
        "github_token_previews": [_token_preview(token) for token in github_tokens],
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
    }


def _serialize_stage1_crawl_template_detail(row: Stage1CrawlTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    return {
        **_serialize_stage1_crawl_template_summary(row),
        "snapshot": {
            "github_tokens": _normalize_stage1_template_values(snapshot.get("github_tokens")),
        },
    }


def _stage2_secret_is_configured(secret: SecretStr | None) -> bool:
    return bool(secret and secret.get_secret_value())


def _stage2_secret_preview(secret: SecretStr | None) -> str:
    if secret is None:
        return ""
    value = secret.get_secret_value()
    if not value:
        return ""
    return value[:6] + "..."


def _stage2_secret_value(secret: SecretStr | None) -> str | None:
    if secret is None:
        return None
    value = secret.get_secret_value()
    return value or None


def _default_stage2_task_concurrency(settings: Settings) -> int:
    return int(settings.stage2_default_task_max_concurrent_runs or settings.stage2_max_concurrent_runs)


def _default_stage3_task_concurrency(settings: Settings) -> int:
    return int(settings.stage3_default_task_max_concurrent_runs or settings.stage3_max_concurrent_runs)


def _default_stage4_task_concurrency(settings: Settings) -> int:
    return int(settings.stage4_default_task_max_concurrent_runs or settings.stage4_max_concurrent_runs)


def _stage2_runtime_snapshot_from_request(
    payload: UpdateStage2RuntimeRequest,
    *,
    settings: Settings,
    base_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(base_snapshot or {})
    base_concurrency = dict(base.get("concurrency") or {})
    base_planner = dict(base.get("planner") or {})
    base_worker = dict(base.get("worker") or {})
    base_hyperparameters = dict(base.get("hyperparameters") or {})
    legacy_command_timeout = base_hyperparameters.get("command_timeout_seconds")
    fields_set = payload.model_fields_set
    planner_current_key = _stage2_secret_value(settings.stage2_agent_llm_api_key("planner"))
    worker_current_key = _stage2_secret_value(settings.stage2_agent_llm_api_key("worker"))
    planner_api_key = _normalize_optional_text(payload.planner_api_key) if payload.planner_api_key is not None else None
    worker_api_key = _normalize_optional_text(payload.worker_api_key) if payload.worker_api_key is not None else None
    resolved_planner_api_key = planner_api_key or str(base_planner.get("api_key") or "") or planner_current_key or ""
    resolved_worker_api_key = worker_api_key or str(base_worker.get("api_key") or "") or worker_current_key or ""
    return {
        "concurrency": {
            "max_concurrent_runs": (
                payload.max_concurrent_runs
                or base_concurrency.get("max_concurrent_runs")
                or _default_stage2_task_concurrency(settings)
            ),
        },
        "planner": {
            "model": _normalize_optional_text(payload.planner_model) or str(base_planner.get("model") or "") or settings.stage2_agent_llm_model("planner") or "",
            "base_url": _normalize_optional_text(payload.planner_base_url) or str(base_planner.get("base_url") or "") or settings.stage2_agent_llm_base_url("planner") or "",
            "api_key": resolved_planner_api_key,
            "api_key_preview": f"{resolved_planner_api_key[:6]}..." if resolved_planner_api_key else "",
            "preset": payload.planner_preset or str(base_planner.get("preset") or "") or settings.stage2_agent_openhands_preset("planner"),
            "max_iterations": payload.planner_max_iterations or base_planner.get("max_iterations") or settings.stage2_agent_openhands_max_iterations("planner"),
            "timeout_seconds": payload.planner_timeout_seconds or base_planner.get("timeout_seconds") or settings.stage2_agent_timeout("planner"),
        },
        "worker": {
            "model": _normalize_optional_text(payload.worker_model) or str(base_worker.get("model") or "") or settings.stage2_agent_llm_model("worker") or "",
            "base_url": _normalize_optional_text(payload.worker_base_url) or str(base_worker.get("base_url") or "") or settings.stage2_agent_llm_base_url("worker") or "",
            "api_key": resolved_worker_api_key,
            "api_key_preview": f"{resolved_worker_api_key[:6]}..." if resolved_worker_api_key else "",
            "preset": payload.worker_preset or str(base_worker.get("preset") or "") or settings.stage2_agent_openhands_preset("worker"),
            "max_iterations": payload.worker_max_iterations or base_worker.get("max_iterations") or settings.stage2_agent_openhands_max_iterations("worker"),
            "timeout_seconds": payload.worker_timeout_seconds or base_worker.get("timeout_seconds") or settings.stage2_agent_timeout("worker"),
        },
        "hyperparameters": {
            "max_worker_attempts": payload.max_worker_attempts or base_hyperparameters.get("max_worker_attempts") or settings.stage2_max_worker_attempts,
            "quickcheck_sample_size": payload.quickcheck_sample_size or base_hyperparameters.get("quickcheck_sample_size") or settings.stage2_quickcheck_sample_size,
            "entry_file_test_count_min": (
                payload.entry_file_test_count_min
                if payload.entry_file_test_count_min is not None
                else base_hyperparameters.get("entry_file_test_count_min", settings.stage2_entry_file_test_count_min)
            ),
            "p2p_file_count_limit": (
                payload.p2p_file_count_limit
                if "p2p_file_count_limit" in fields_set
                else base_hyperparameters.get("p2p_file_count_limit", settings.stage2_p2p_file_count_limit)
            ),
            "p2p_sample_seed": str(base_hyperparameters.get("p2p_sample_seed") or "") or None,
            "collect_timeout_seconds": payload.collect_timeout_seconds or base_hyperparameters.get("collect_timeout_seconds") or legacy_command_timeout or settings.stage2_collect_timeout_seconds,
            "run_test_timeout_seconds": payload.run_test_timeout_seconds or base_hyperparameters.get("run_test_timeout_seconds") or legacy_command_timeout or settings.stage2_run_test_timeout_seconds,
            "build_timeout_seconds": payload.build_timeout_seconds or base_hyperparameters.get("build_timeout_seconds") or settings.stage2_build_timeout_seconds,
            "full_validation_timeout_seconds": payload.full_validation_timeout_seconds or base_hyperparameters.get("full_validation_timeout_seconds") or settings.stage2_full_validation_timeout_seconds,
        },
    }


def _serialize_stage2_runtime_template_summary(row: Stage2RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    planner = dict(snapshot.get("planner") or {})
    worker = dict(snapshot.get("worker") or {})
    return {
        "id": row.id,
        "name": row.name,
        "planner_model": planner.get("model") or "",
        "worker_model": worker.get("model") or "",
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
    }


def _serialize_stage2_runtime_template_detail(row: Stage2RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    planner = dict(snapshot.get("planner") or {})
    worker = dict(snapshot.get("worker") or {})
    planner_api_key = str(planner.get("api_key") or "")
    worker_api_key = str(worker.get("api_key") or "")
    return {
        "id": row.id,
        "name": row.name,
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
        "snapshot": {
            "concurrency": dict(snapshot.get("concurrency") or {}),
            "planner": {
                **planner,
                "api_key": planner_api_key,
                "api_key_preview": f"{planner_api_key[:6]}..." if planner_api_key else "",
            },
            "worker": {
                **worker,
                "api_key": worker_api_key,
                "api_key_preview": f"{worker_api_key[:6]}..." if worker_api_key else "",
            },
            "hyperparameters": dict(snapshot.get("hyperparameters") or {}),
        },
    }


def _stage3_runtime_snapshot_from_request(
    payload: UpdateStage3RuntimeRequest,
    *,
    settings: Settings,
    base_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(base_snapshot or {})
    base_concurrency = dict(base.get("concurrency") or {})
    base_breaker = dict(base.get("breaker") or {})
    base_hyperparameters = dict(base.get("hyperparameters") or {})
    current_key = _stage2_secret_value(settings.stage3_agent_llm_api_key())
    breaker_api_key = _normalize_optional_text(payload.breaker_api_key) if payload.breaker_api_key is not None else None
    resolved_breaker_api_key = breaker_api_key or str(base_breaker.get("api_key") or "") or current_key or ""
    return {
        "concurrency": {
            "max_concurrent_runs": (
                payload.max_concurrent_runs
                or base_concurrency.get("max_concurrent_runs")
                or _default_stage3_task_concurrency(settings)
            ),
        },
        "breaker": {
            "model": (
                _normalize_optional_text(payload.breaker_model)
                or str(base_breaker.get("model") or "")
                or settings.stage3_agent_llm_model()
                or ""
            ),
            "base_url": (
                _normalize_optional_text(payload.breaker_base_url)
                or str(base_breaker.get("base_url") or "")
                or settings.stage3_agent_llm_base_url()
                or ""
            ),
            "api_key": resolved_breaker_api_key,
            "api_key_preview": f"{resolved_breaker_api_key[:6]}..." if resolved_breaker_api_key else "",
            "preset": payload.breaker_preset
            or str(base_breaker.get("preset") or "")
            or settings.stage3_agent_openhands_preset(),
            "max_iterations": (
                payload.breaker_max_iterations
                or base_breaker.get("max_iterations")
                or settings.stage3_agent_openhands_max_iterations()
            ),
            "timeout_seconds": (
                payload.breaker_timeout_seconds
                or base_breaker.get("timeout_seconds")
                or settings.stage3_agent_timeout()
            ),
        },
        "hyperparameters": {
            "build_timeout_seconds": (
                payload.build_timeout_seconds
                or base_hyperparameters.get("build_timeout_seconds")
                or settings.stage3_build_timeout_seconds
            ),
            "run_test_timeout_seconds": (
                payload.run_test_timeout_seconds
                or base_hyperparameters.get("run_test_timeout_seconds")
                or settings.stage3_run_test_timeout_seconds
            ),
            "full_validation_timeout_seconds": (
                payload.full_validation_timeout_seconds
                or base_hyperparameters.get("full_validation_timeout_seconds")
                or settings.stage3_full_validation_timeout_seconds
            ),
            "entry_pass_rate_ceiling": (
                payload.entry_pass_rate_ceiling
                if payload.entry_pass_rate_ceiling is not None
                else (
                    base_hyperparameters.get("entry_pass_rate_ceiling")
                    if base_hyperparameters.get("entry_pass_rate_ceiling") is not None
                    else settings.stage3_entry_pass_rate_ceiling
                )
            ),
            "min_removed_code_lines": (
                payload.min_removed_code_lines
                if payload.min_removed_code_lines is not None
                else (
                    base_hyperparameters.get("min_removed_code_lines")
                    if base_hyperparameters.get("min_removed_code_lines") is not None
                    else settings.stage3_min_removed_code_lines
                )
            ),
        },
    }


def _serialize_stage3_runtime_template_summary(row: Stage3RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    breaker = dict(snapshot.get("breaker") or {})
    return {
        "id": row.id,
        "name": row.name,
        "breaker_model": breaker.get("model") or "",
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
    }


def _serialize_stage3_runtime_template_detail(row: Stage3RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    breaker = dict(snapshot.get("breaker") or {})
    breaker_api_key = str(breaker.get("api_key") or "")
    return {
        "id": row.id,
        "name": row.name,
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
        "snapshot": {
            "concurrency": dict(snapshot.get("concurrency") or {}),
            "breaker": {
                **breaker,
                "api_key": breaker_api_key,
                "api_key_preview": f"{breaker_api_key[:6]}..." if breaker_api_key else "",
            },
            "hyperparameters": dict(snapshot.get("hyperparameters") or {}),
        },
    }


def _stage4_runtime_snapshot_from_request(
    payload: UpdateStage4RuntimeRequest,
    *,
    settings: Settings,
    base_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(base_snapshot or {})
    base_concurrency = dict(base.get("concurrency") or {})
    base_issuer = dict(base.get("issuer") or {})
    base_hyperparameters = dict(base.get("hyperparameters") or {})
    current_key = _stage2_secret_value(settings.stage4_agent_llm_api_key())
    issuer_api_key = _normalize_optional_text(payload.issuer_api_key) if payload.issuer_api_key is not None else None
    resolved_issuer_api_key = issuer_api_key or str(base_issuer.get("api_key") or "") or current_key or ""
    issue_style_catalog = load_issue_style_catalog(
        enabled_styles=settings.stage4_enabled_issue_style_ids(),
    )
    return {
        "concurrency": {
            "max_concurrent_runs": (
                payload.max_concurrent_runs
                or base_concurrency.get("max_concurrent_runs")
                or _default_stage4_task_concurrency(settings)
            ),
        },
        "issuer": {
            "model": (
                _normalize_optional_text(payload.issuer_model)
                or str(base_issuer.get("model") or "")
                or settings.stage4_agent_llm_model()
                or ""
            ),
            "base_url": (
                _normalize_optional_text(payload.issuer_base_url)
                or str(base_issuer.get("base_url") or "")
                or settings.stage4_agent_llm_base_url()
                or ""
            ),
            "api_key": resolved_issuer_api_key,
            "api_key_preview": f"{resolved_issuer_api_key[:6]}..." if resolved_issuer_api_key else "",
            "preset": payload.issuer_preset
            or str(base_issuer.get("preset") or "")
            or settings.stage4_agent_openhands_preset(),
            "max_iterations": (
                payload.issuer_max_iterations
                or base_issuer.get("max_iterations")
                or settings.stage4_agent_openhands_max_iterations()
            ),
            "timeout_seconds": (
                payload.issuer_timeout_seconds
                or base_issuer.get("timeout_seconds")
                or settings.stage4_agent_timeout()
            ),
        },
        "hyperparameters": {
            "build_timeout_seconds": (
                payload.build_timeout_seconds
                or base_hyperparameters.get("build_timeout_seconds")
                or settings.stage4_build_timeout_seconds
            ),
        },
        "issue_style_config": {
            "schema_version": issue_style_catalog.schema_version,
            "enabled_styles": list(issue_style_catalog.enabled_styles),
            "catalog_sha256": issue_style_catalog.enabled_contract_sha256(),
        },
    }


def _stage4_run_runtime_snapshot_from_request(
    payload: UpdateStage4RuntimeRequest,
    *,
    settings: Settings,
    base_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = dict(base_snapshot or {})
    base_issuer = dict(base.get("issuer") or {})
    base_hyperparameters = dict(base.get("hyperparameters") or {})
    current_key = _stage2_secret_value(settings.stage4_agent_llm_api_key())
    issuer_api_key = _normalize_optional_text(payload.issuer_api_key) if payload.issuer_api_key is not None else None
    resolved_issuer_api_key = issuer_api_key or str(base_issuer.get("api_key") or "") or current_key or ""
    hyperparameters = {
        **base_hyperparameters,
    }
    hyperparameters.pop("issue_variant_count", None)
    hyperparameters.pop("hint_variant_count", None)
    if payload.build_timeout_seconds is not None:
        hyperparameters["build_timeout_seconds"] = payload.build_timeout_seconds
    return {
        "issuer": {
            "model": (
                _normalize_optional_text(payload.issuer_model)
                or str(base_issuer.get("model") or "")
                or settings.stage4_agent_llm_model()
                or ""
            ),
            "base_url": (
                _normalize_optional_text(payload.issuer_base_url)
                or str(base_issuer.get("base_url") or "")
                or settings.stage4_agent_llm_base_url()
                or ""
            ),
            "api_key": resolved_issuer_api_key,
            "api_key_preview": f"{resolved_issuer_api_key[:6]}..." if resolved_issuer_api_key else "",
            "preset": payload.issuer_preset
            or str(base_issuer.get("preset") or "")
            or settings.stage4_agent_openhands_preset(),
            "max_iterations": (
                payload.issuer_max_iterations
                or base_issuer.get("max_iterations")
                or settings.stage4_agent_openhands_max_iterations()
            ),
            "timeout_seconds": (
                payload.issuer_timeout_seconds
                or base_issuer.get("timeout_seconds")
                or settings.stage4_agent_timeout()
            ),
        },
        "hyperparameters": hyperparameters,
    }


def _serialize_stage4_runtime_template_summary(row: Stage4RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    issuer = dict(snapshot.get("issuer") or {})
    return {
        "id": row.id,
        "name": row.name,
        "issuer_model": issuer.get("model") or "",
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
    }


def _serialize_stage4_runtime_template_detail(row: Stage4RuntimeTemplate) -> dict[str, Any]:
    snapshot = dict(row.snapshot_json or {})
    issuer = dict(snapshot.get("issuer") or {})
    issuer_api_key = str(issuer.get("api_key") or "")
    return {
        "id": row.id,
        "name": row.name,
        "created_at": _serialize_datetime(row.created_at),
        "updated_at": _serialize_datetime(row.updated_at),
        "snapshot": {
            "concurrency": dict(snapshot.get("concurrency") or {}),
            "issuer": {
                **issuer,
                "api_key": issuer_api_key,
                "api_key_preview": f"{issuer_api_key[:6]}..." if issuer_api_key else "",
            },
            "hyperparameters": dict(snapshot.get("hyperparameters") or {}),
            "issue_style_config": dict(snapshot.get("issue_style_config") or {}),
        },
    }


def _positive_int(value: Any, *, maximum: int | None = None) -> int | None:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        return None
    resolved = max(1, resolved)
    if maximum is not None:
        resolved = min(resolved, max(1, int(maximum)))
    return resolved


def _non_negative_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _int_at_least(value: Any, minimum: int) -> int | None:
    try:
        return max(int(minimum), int(value))
    except (TypeError, ValueError):
        return None


def _float_value(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _runtime_preset(value: Any) -> Literal["default", "gpt5"] | None:
    normalized = str(value or "").strip()
    return normalized if normalized in {"default", "gpt5"} else None


def _secret_from_snapshot(value: Any) -> SecretStr | None:
    normalized = _normalize_optional_text(str(value or ""))
    return SecretStr(normalized) if normalized else None


def _apply_stage2_runtime_snapshot_to_settings(settings: Settings, snapshot: dict[str, Any]) -> None:
    concurrency = dict(snapshot.get("concurrency") or {})
    max_concurrent_runs = _positive_int(
        concurrency.get("max_concurrent_runs"),
        maximum=settings.stage2_max_concurrent_runs,
    )
    if max_concurrent_runs is not None:
        settings.stage2_default_task_max_concurrent_runs = max_concurrent_runs

    planner = dict(snapshot.get("planner") or {})
    worker = dict(snapshot.get("worker") or {})
    if "model" in planner:
        settings.stage2_planner_llm_model = _normalize_optional_text(str(planner.get("model") or ""))
    if "base_url" in planner:
        settings.stage2_planner_llm_base_url = _normalize_optional_text(str(planner.get("base_url") or ""))
    if "api_key" in planner:
        settings.stage2_planner_llm_api_key = _secret_from_snapshot(planner.get("api_key"))
    planner_preset = _runtime_preset(planner.get("preset"))
    if planner_preset is not None:
        settings.stage2_planner_openhands_preset = planner_preset
    planner_max_iterations = _positive_int(planner.get("max_iterations"))
    if planner_max_iterations is not None:
        settings.stage2_planner_openhands_max_iterations = planner_max_iterations
    planner_timeout = _float_value(planner.get("timeout_seconds"))
    if planner_timeout is not None:
        settings.stage2_planner_agent_timeout_seconds = planner_timeout

    if "model" in worker:
        settings.stage2_worker_llm_model = _normalize_optional_text(str(worker.get("model") or ""))
    if "base_url" in worker:
        settings.stage2_worker_llm_base_url = _normalize_optional_text(str(worker.get("base_url") or ""))
    if "api_key" in worker:
        settings.stage2_worker_llm_api_key = _secret_from_snapshot(worker.get("api_key"))
    worker_preset = _runtime_preset(worker.get("preset"))
    if worker_preset is not None:
        settings.stage2_worker_openhands_preset = worker_preset
    worker_max_iterations = _positive_int(worker.get("max_iterations"))
    if worker_max_iterations is not None:
        settings.stage2_worker_openhands_max_iterations = worker_max_iterations
    worker_timeout = _float_value(worker.get("timeout_seconds"))
    if worker_timeout is not None:
        settings.stage2_worker_agent_timeout_seconds = worker_timeout

    hyperparameters = dict(snapshot.get("hyperparameters") or {})
    max_worker_attempts = _positive_int(hyperparameters.get("max_worker_attempts"))
    if max_worker_attempts is not None:
        settings.stage2_max_worker_attempts = max_worker_attempts
    quickcheck_sample_size = _positive_int(hyperparameters.get("quickcheck_sample_size"))
    if quickcheck_sample_size is not None:
        settings.stage2_quickcheck_sample_size = quickcheck_sample_size
    entry_file_test_count_min = _int_at_least(hyperparameters.get("entry_file_test_count_min"), -1)
    if entry_file_test_count_min is not None:
        settings.stage2_entry_file_test_count_min = entry_file_test_count_min
    if "p2p_file_count_limit" in hyperparameters:
        settings.stage2_p2p_file_count_limit = _positive_int(hyperparameters.get("p2p_file_count_limit"))
    collect_timeout = _float_value(hyperparameters.get("collect_timeout_seconds"))
    if collect_timeout is not None:
        settings.stage2_collect_timeout_seconds = collect_timeout
    run_test_timeout = _float_value(hyperparameters.get("run_test_timeout_seconds"))
    if run_test_timeout is not None:
        settings.stage2_run_test_timeout_seconds = run_test_timeout
    build_timeout = _float_value(hyperparameters.get("build_timeout_seconds"))
    if build_timeout is not None:
        settings.stage2_build_timeout_seconds = build_timeout
    full_validation_timeout = _float_value(hyperparameters.get("full_validation_timeout_seconds"))
    if full_validation_timeout is not None:
        settings.stage2_full_validation_timeout_seconds = full_validation_timeout


def _apply_stage3_runtime_snapshot_to_settings(settings: Settings, snapshot: dict[str, Any]) -> None:
    concurrency = dict(snapshot.get("concurrency") or {})
    max_concurrent_runs = _positive_int(
        concurrency.get("max_concurrent_runs"),
        maximum=settings.stage3_max_concurrent_runs,
    )
    if max_concurrent_runs is not None:
        settings.stage3_default_task_max_concurrent_runs = max_concurrent_runs

    breaker = dict(snapshot.get("breaker") or {})
    if "model" in breaker:
        settings.stage3_llm_model = _normalize_optional_text(str(breaker.get("model") or ""))
    if "base_url" in breaker:
        settings.stage3_llm_base_url = _normalize_optional_text(str(breaker.get("base_url") or ""))
    if "api_key" in breaker:
        settings.stage3_llm_api_key = _secret_from_snapshot(breaker.get("api_key"))
    breaker_preset = _runtime_preset(breaker.get("preset"))
    if breaker_preset is not None:
        settings.stage3_openhands_preset = breaker_preset
    breaker_max_iterations = _positive_int(breaker.get("max_iterations"))
    if breaker_max_iterations is not None:
        settings.stage3_openhands_max_iterations = breaker_max_iterations
    breaker_timeout = _float_value(breaker.get("timeout_seconds"))
    if breaker_timeout is not None:
        settings.stage3_agent_timeout_seconds = breaker_timeout

    hyperparameters = dict(snapshot.get("hyperparameters") or {})
    build_timeout = _float_value(hyperparameters.get("build_timeout_seconds"))
    if build_timeout is not None:
        settings.stage3_build_timeout_seconds = build_timeout
    run_test_timeout = _float_value(hyperparameters.get("run_test_timeout_seconds"))
    if run_test_timeout is not None:
        settings.stage3_run_test_timeout_seconds = run_test_timeout
    full_validation_timeout = _float_value(hyperparameters.get("full_validation_timeout_seconds"))
    if full_validation_timeout is not None:
        settings.stage3_full_validation_timeout_seconds = full_validation_timeout
    entry_pass_rate_ceiling = _float_value(hyperparameters.get("entry_pass_rate_ceiling"))
    if entry_pass_rate_ceiling is not None:
        settings.stage3_entry_pass_rate_ceiling = min(max(entry_pass_rate_ceiling, 0.0), 1.0)
    min_removed_code_lines = _non_negative_int(hyperparameters.get("min_removed_code_lines"))
    if min_removed_code_lines is not None:
        settings.stage3_min_removed_code_lines = min_removed_code_lines


def _apply_stage4_runtime_snapshot_to_settings(settings: Settings, snapshot: dict[str, Any]) -> None:
    concurrency = dict(snapshot.get("concurrency") or {})
    max_concurrent_runs = _positive_int(
        concurrency.get("max_concurrent_runs"),
        maximum=settings.stage4_max_concurrent_runs,
    )
    if max_concurrent_runs is not None:
        settings.stage4_default_task_max_concurrent_runs = max_concurrent_runs

    issuer = dict(snapshot.get("issuer") or {})
    if "model" in issuer:
        settings.stage4_llm_model = _normalize_optional_text(str(issuer.get("model") or ""))
    if "base_url" in issuer:
        settings.stage4_llm_base_url = _normalize_optional_text(str(issuer.get("base_url") or ""))
    if "api_key" in issuer:
        settings.stage4_llm_api_key = _secret_from_snapshot(issuer.get("api_key"))
    issuer_preset = _runtime_preset(issuer.get("preset"))
    if issuer_preset is not None:
        settings.stage4_openhands_preset = issuer_preset
    issuer_max_iterations = _positive_int(issuer.get("max_iterations"))
    if issuer_max_iterations is not None:
        settings.stage4_openhands_max_iterations = issuer_max_iterations
    issuer_timeout = _float_value(issuer.get("timeout_seconds"))
    if issuer_timeout is not None:
        settings.stage4_agent_timeout_seconds = issuer_timeout

    hyperparameters = dict(snapshot.get("hyperparameters") or {})
    build_timeout = _float_value(hyperparameters.get("build_timeout_seconds"))
    if build_timeout is not None:
        settings.stage4_build_timeout_seconds = build_timeout


def _apply_persisted_stage_runtime_settings(session: Any, settings: Settings) -> None:
    for key, apply_snapshot in (
        (CURRENT_STAGE2_RUNTIME_CONFIG_KEY, _apply_stage2_runtime_snapshot_to_settings),
        (CURRENT_STAGE3_RUNTIME_CONFIG_KEY, _apply_stage3_runtime_snapshot_to_settings),
        (CURRENT_STAGE4_RUNTIME_CONFIG_KEY, _apply_stage4_runtime_snapshot_to_settings),
    ):
        row = session.get(GlobalRuntimeConfig, key)
        if row is None:
            continue
        snapshot = dict(row.config_json or {})
        if snapshot:
            apply_snapshot(settings, snapshot)


def _persist_stage_runtime_config(session: Any, *, key: str, snapshot: dict[str, Any]) -> GlobalRuntimeConfig:
    row = session.get(GlobalRuntimeConfig, key)
    if row is None:
        row = GlobalRuntimeConfig(key=key, config_json=dict(snapshot or {}))
        session.add(row)
    else:
        row.config_json = dict(snapshot or {})
    session.flush()
    return row


def _serialize_stage2_runtime(settings: Settings, env_runner: Stage2RunRunner | None = None) -> dict[str, Any]:
    system_capacity = env_runner.get_max_workers() if env_runner is not None else settings.stage2_max_concurrent_runs
    max_concurrent_runs = int(settings.stage2_default_task_max_concurrent_runs or system_capacity)
    return {
        "concurrency": {
            "max_concurrent_runs": max_concurrent_runs,
            "system_capacity": system_capacity,
        },
        "planner": {
            "model": settings.stage2_agent_llm_model("planner") or "",
            "base_url": settings.stage2_agent_llm_base_url("planner") or "",
            "api_key_configured": _stage2_secret_is_configured(settings.stage2_agent_llm_api_key("planner")),
            "api_key_preview": _stage2_secret_preview(settings.stage2_agent_llm_api_key("planner")),
            "preset": settings.stage2_agent_openhands_preset("planner"),
            "max_iterations": settings.stage2_agent_openhands_max_iterations("planner"),
            "timeout_seconds": settings.stage2_agent_timeout("planner"),
        },
        "worker": {
            "model": settings.stage2_agent_llm_model("worker") or "",
            "base_url": settings.stage2_agent_llm_base_url("worker") or "",
            "api_key_configured": _stage2_secret_is_configured(settings.stage2_agent_llm_api_key("worker")),
            "api_key_preview": _stage2_secret_preview(settings.stage2_agent_llm_api_key("worker")),
            "preset": settings.stage2_agent_openhands_preset("worker"),
            "max_iterations": settings.stage2_agent_openhands_max_iterations("worker"),
            "timeout_seconds": settings.stage2_agent_timeout("worker"),
        },
        "hyperparameters": {
            "max_worker_attempts": settings.stage2_max_worker_attempts,
            "quickcheck_sample_size": settings.stage2_quickcheck_sample_size,
            "entry_file_test_count_min": settings.stage2_entry_file_test_count_min,
            "p2p_file_count_limit": settings.stage2_p2p_file_count_limit,
            "collect_timeout_seconds": settings.stage2_collect_timeout_seconds,
            "run_test_timeout_seconds": settings.stage2_run_test_timeout_seconds,
            "build_timeout_seconds": settings.stage2_build_timeout_seconds,
            "full_validation_timeout_seconds": settings.stage2_full_validation_timeout_seconds,
        },
    }


def _serialize_stage3_runtime(settings: Settings, env_runner: Stage3RunRunner | None = None) -> dict[str, Any]:
    system_capacity = env_runner.get_max_workers() if env_runner is not None else settings.stage3_max_concurrent_runs
    max_concurrent_runs = int(settings.stage3_default_task_max_concurrent_runs or system_capacity)
    return {
        "concurrency": {
            "max_concurrent_runs": max_concurrent_runs,
            "system_capacity": system_capacity,
        },
        "breaker": {
            "model": settings.stage3_agent_llm_model() or "",
            "base_url": settings.stage3_agent_llm_base_url() or "",
            "api_key_configured": _stage2_secret_is_configured(settings.stage3_agent_llm_api_key()),
            "api_key_preview": _stage2_secret_preview(settings.stage3_agent_llm_api_key()),
            "preset": settings.stage3_agent_openhands_preset(),
            "max_iterations": settings.stage3_agent_openhands_max_iterations(),
            "timeout_seconds": settings.stage3_agent_timeout(),
        },
        "hyperparameters": {
            "build_timeout_seconds": settings.stage3_build_timeout_seconds,
            "run_test_timeout_seconds": settings.stage3_run_test_timeout_seconds,
            "full_validation_timeout_seconds": settings.stage3_full_validation_timeout_seconds,
            "entry_pass_rate_ceiling": settings.stage3_entry_pass_rate_ceiling,
            "min_removed_code_lines": settings.stage3_min_removed_code_lines,
        },
        "workspace": {
            "root_dir": str(Path(settings.stage3_workspace_dir).expanduser()),
        },
    }


def _serialize_stage4_runtime(settings: Settings, env_runner: Stage4RunRunner | None = None) -> dict[str, Any]:
    system_capacity = env_runner.get_max_workers() if env_runner is not None else settings.stage4_max_concurrent_runs
    max_concurrent_runs = int(settings.stage4_default_task_max_concurrent_runs or system_capacity)
    readiness = env_runner.backend_readiness() if env_runner is not None else {"ready": True, "backend": "local"}
    issue_style_catalog = load_issue_style_catalog(
        enabled_styles=settings.stage4_enabled_issue_style_ids(),
    )
    return {
        "concurrency": {
            "max_concurrent_runs": max_concurrent_runs,
            "system_capacity": system_capacity,
        },
        "issuer": {
            "model": settings.stage4_agent_llm_model() or "",
            "base_url": settings.stage4_agent_llm_base_url() or "",
            "api_key_configured": _stage2_secret_is_configured(settings.stage4_agent_llm_api_key()),
            "api_key_preview": _stage2_secret_preview(settings.stage4_agent_llm_api_key()),
            "preset": settings.stage4_agent_openhands_preset(),
            "max_iterations": settings.stage4_agent_openhands_max_iterations(),
            "timeout_seconds": settings.stage4_agent_timeout(),
        },
        "hyperparameters": {
            "build_timeout_seconds": settings.stage4_build_timeout_seconds,
        },
        "issue_style_config": {
            "schema_version": issue_style_catalog.schema_version,
            "enabled_styles": list(issue_style_catalog.enabled_styles),
            "catalog_sha256": issue_style_catalog.enabled_contract_sha256(),
        },
        "backend": readiness,
        "workspace": {
            "root_dir": str(Path(settings.stage4_workspace_dir).expanduser()),
        },
    }


def _remove_docker_images(image_refs: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    failed: list[str] = []
    for image_ref in image_refs:
        reference = str(image_ref or "").strip()
        if not reference or reference in seen:
            continue
        seen.add(reference)
        try:
            completed = subprocess.run(
                ["docker", "image", "rm", "-f", reference],
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
            )
        except Exception:
            failed.append(reference)
            continue
        stderr = str(completed.stderr or "").strip().lower()
        stdout = str(completed.stdout or "").strip().lower()
        if completed.returncode != 0 and "no such image" not in stderr and "no such image" not in stdout:
            failed.append(reference)
    return failed


def _remove_paths(paths: list[str] | tuple[str, ...], *, root_dir: Path | None = None) -> list[str]:
    seen: set[str] = set()
    failed: list[str] = []
    normalized_root = root_dir.expanduser().resolve() if root_dir is not None else None
    for candidate in paths:
        path = Path(str(candidate or "")).expanduser()
        if not str(path):
            continue
        try:
            resolved = path.resolve()
        except Exception:
            continue
        resolved_key = str(resolved)
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        if normalized_root is not None:
            if resolved == normalized_root or normalized_root not in resolved.parents:
                continue
        if not resolved.exists():
            continue
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        except Exception:
            failed.append(str(resolved))
    return failed


def _cleanup_stage2_run_archive(settings: Settings, archive: dict[str, Any]) -> dict[str, list[str]]:
    run_id = str(archive.get("run_id") or "")
    if not run_id:
        return {"failed_paths": [], "failed_checkpoint_docker_image_refs": [], "failed_validator_image_refs": []}

    stage2_root = Path(settings.stage2_workspace_dir).expanduser().resolve()
    candidates: list[Path] = []
    cleanup_paths = archive.get("cleanup_paths")
    cleanup_paths_enabled = True if cleanup_paths is None else bool(cleanup_paths)
    if cleanup_paths_enabled:
        workspace_path = archive.get("workspace_path")
        if workspace_path:
            candidates.append(Path(str(workspace_path)).expanduser())
        candidates.extend(
            [
                stage2_root / "runs" / run_id,
                stage2_root / "runtime" / run_id,
            ]
        )

    failed_paths = list(
        _remove_paths([str(candidate) for candidate in candidates], root_dir=stage2_root) or []
    )
    checkpoint_image_refs = list(archive.get("checkpoint_docker_image_refs") or [])
    validator_image_refs = list(archive.get("validator_image_refs") or [])
    failed_checkpoint_docker_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
    failed_validator_image_refs = list(_remove_docker_images(validator_image_refs) or [])
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_checkpoint_docker_image_refs,
        "failed_validator_image_refs": failed_validator_image_refs,
    }


def _cleanup_recovered_stage2_run_assets(settings: Settings, archive: dict[str, Any]) -> dict[str, list[str]]:
    run_id = str(archive.get("run_id") or "")
    if not run_id:
        return {"failed_repo_checkout_paths": [], "failed_validator_image_refs": []}

    stage2_root = Path(settings.stage2_workspace_dir).expanduser().resolve()
    repo_checkout_candidates: list[str] = []
    workspace_path = str(archive.get("workspace_path") or "").strip()
    if workspace_path:
        repo_checkout_candidates.append(str(Path(workspace_path).expanduser() / "repo"))
    repo_checkout_candidates.append(str(stage2_root / "runs" / run_id / "repo"))

    failed_repo_checkout_paths = list(
        _remove_paths(repo_checkout_candidates, root_dir=stage2_root) or []
    )
    failed_validator_image_refs = list(
        _remove_docker_images(list(archive.get("validator_image_refs") or [])) or []
    )
    return {
        "failed_repo_checkout_paths": failed_repo_checkout_paths,
        "failed_validator_image_refs": failed_validator_image_refs,
    }


def _cleanup_stage2_archive_by_scope(
    settings: Settings,
    archive: dict[str, Any],
) -> dict[str, list[str]]:
    cleanup_scope = str(archive.get("cleanup_scope") or "").strip()
    if cleanup_scope == "recovered_run_assets":
        return _cleanup_recovered_stage2_run_assets(settings, archive)
    return _cleanup_stage2_run_archive(settings, archive)


def _cleanup_stage3_run_archive(settings: Settings, archive: dict[str, Any]) -> dict[str, list[str]]:
    run_id = str(archive.get("run_id") or "")
    if not run_id:
        return {"failed_paths": [], "failed_checkpoint_docker_image_refs": []}

    stage3_root = Path(settings.stage3_workspace_dir).expanduser().resolve()
    candidates: list[Path] = []
    workspace_path = archive.get("workspace_path")
    if workspace_path:
        candidates.append(Path(str(workspace_path)).expanduser())
    runtime_path = archive.get("runtime_path")
    if runtime_path:
        candidates.append(Path(str(runtime_path)).expanduser())
    candidates.extend(
        [
            stage3_root / "runs" / run_id,
            stage3_root / "runtime" / run_id,
        ]
    )

    failed_paths = list(
        _remove_paths([str(candidate) for candidate in candidates], root_dir=stage3_root) or []
    )
    checkpoint_image_refs = list(archive.get("checkpoint_docker_image_refs") or [])
    failed_checkpoint_docker_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_checkpoint_docker_image_refs,
    }


def _cleanup_recovered_stage3_run_assets(settings: Settings, archive: dict[str, Any]) -> dict[str, list[str]]:
    run_id = str(archive.get("run_id") or "")
    if not run_id:
        return {"failed_repo_checkout_paths": []}

    stage3_root = Path(settings.stage3_workspace_dir).expanduser().resolve()
    repo_checkout_candidates: list[str] = []
    workspace_path = str(archive.get("workspace_path") or "").strip()
    if workspace_path:
        repo_checkout_candidates.append(str(Path(workspace_path).expanduser() / "repo"))
    repo_checkout_candidates.append(str(stage3_root / "runs" / run_id / "repo"))

    failed_repo_checkout_paths = list(
        _remove_paths(repo_checkout_candidates, root_dir=stage3_root) or []
    )
    return {"failed_repo_checkout_paths": failed_repo_checkout_paths}


def _cleanup_stage3_breaker_checkpoint_assets(
    settings: Settings,
    archive: dict[str, Any],
) -> dict[str, list[str]]:
    stage3_root = Path(settings.stage3_workspace_dir).expanduser().resolve()
    checkpoint_paths = list(archive.get("checkpoint_paths") or [])
    checkpoint_image_refs = list(archive.get("checkpoint_docker_image_refs") or [])
    failed_paths = list(_remove_paths(checkpoint_paths, root_dir=stage3_root) or [])
    failed_checkpoint_docker_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_checkpoint_docker_image_refs,
    }


def _cleanup_stage3_partial_savepoint_archive_assets(
    settings: Settings,
    archive: dict[str, Any],
) -> dict[str, list[str]]:
    stage3_root = Path(settings.stage3_workspace_dir).expanduser().resolve()
    cleanup_paths = [
        *list(archive.get("checkpoint_paths") or []),
        *list(archive.get("savepoint_paths") or []),
    ]
    checkpoint_image_refs = list(archive.get("checkpoint_docker_image_refs") or [])
    failed_paths = list(_remove_paths(cleanup_paths, root_dir=stage3_root) or [])
    failed_checkpoint_docker_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_checkpoint_docker_image_refs,
    }


def _cleanup_stage3_archive_by_scope(
    settings: Settings,
    archive: dict[str, Any],
) -> dict[str, list[str]]:
    cleanup_scope = str(archive.get("cleanup_scope") or "").strip()
    if cleanup_scope == "recovered_run_assets":
        return _cleanup_recovered_stage3_run_assets(settings, archive)
    if cleanup_scope == "breaker_checkpoint_assets":
        return _cleanup_stage3_breaker_checkpoint_assets(settings, archive)
    if cleanup_scope == "partial_savepoint_archive_assets":
        return _cleanup_stage3_partial_savepoint_archive_assets(settings, archive)
    return _cleanup_stage3_run_archive(settings, archive)


def _cleanup_result_has_failures(cleanup_result: dict[str, Any]) -> bool:
    return any(
        isinstance(value, list) and len(value) > 0
        for value in dict(cleanup_result or {}).values()
    )


def _build_llm_completion_zip(
    *,
    repository_full_name: str,
    run_id: str,
    role: str,
    archive_dir: Path,
    allowed_roles: set[str],
) -> tuple[Path, str]:
    if role not in allowed_roles:
        raise HTTPException(status_code=404, detail=f"unknown LLM completion archive role: {role}")
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"LLM completion archive not found for {role}")

    files = sorted(path for path in archive_dir.rglob("*.json") if path.is_file())
    if not files:
        raise HTTPException(status_code=404, detail=f"LLM completion archive is empty for {role}")

    safe_repo = repository_full_name.replace("/", "__")
    safe_run = run_id.replace("/", "_")
    filename = f"{safe_repo}-{safe_run}-{role}-llm-completions.zip"
    temp = tempfile.NamedTemporaryFile(prefix="feature-factory-llm-completions-", suffix=".zip", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    try:
        with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in files:
                archive.write(path, arcname=str(Path(role) / path.relative_to(archive_dir)))
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return temp_path, filename


def _build_stage2_llm_completion_zip(
    *,
    repository_full_name: str,
    run_id: str,
    role: str,
    archive_dir: Path,
) -> tuple[Path, str]:
    return _build_llm_completion_zip(
        repository_full_name=repository_full_name,
        run_id=run_id,
        role=role,
        archive_dir=archive_dir,
        allowed_roles={"planner", "worker"},
    )


def _build_stage3_llm_completion_zip(
    *,
    repository_full_name: str,
    run_id: str,
    role: str,
    archive_dir: Path,
) -> tuple[Path, str]:
    return _build_llm_completion_zip(
        repository_full_name=repository_full_name,
        run_id=run_id,
        role=role,
        archive_dir=archive_dir,
        allowed_roles={"breaker"},
    )


def _build_stage4_llm_completion_zip(
    *,
    repository_full_name: str,
    run_id: str,
    role: str,
    archive_dir: Path,
    allowed_roles: set[str],
) -> tuple[Path, str]:
    return _build_llm_completion_zip(
        repository_full_name=repository_full_name,
        run_id=run_id,
        role=role,
        archive_dir=archive_dir,
        allowed_roles=allowed_roles,
    )


def _stage2_archive_has_json_files(path: Path) -> bool:
    return path.exists() and path.is_dir() and any(item.is_file() for item in path.rglob("*.json"))


def _stage2_llm_completion_archive_dir(runtime_dir: Path, role: str) -> Path:
    archive_dir = llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(archive_dir):
        return archive_dir
    legacy_dir = legacy_llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(legacy_dir):
        return legacy_dir
    return archive_dir


def _stage3_llm_completion_archive_dir(runtime_dir: Path, role: str) -> Path:
    archive_dir = llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(archive_dir):
        return archive_dir
    legacy_dir = legacy_llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(legacy_dir):
        return legacy_dir
    return archive_dir


def _stage4_llm_completion_archive_dir(runtime_dir: Path, role: str) -> Path:
    archive_dir = llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(archive_dir):
        return archive_dir
    legacy_dir = legacy_llm_completion_archive_dir(runtime_dir, role)
    if _stage2_archive_has_json_files(legacy_dir):
        return legacy_dir
    return archive_dir


def _resolve_stage2_llm_completion_file(
    *,
    runtime_dir: Path,
    role: str,
    requested_path: str,
) -> Path:
    if role not in {"planner", "worker"}:
        raise HTTPException(status_code=404, detail=f"unknown LLM completion archive role: {role}")

    archive_dir = _stage2_llm_completion_archive_dir(runtime_dir, role)
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"LLM completion archive not found for {role}")

    relative_path = Path(requested_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=400, detail="invalid LLM completion file path")

    archive_root = archive_dir.resolve()
    candidates = [
        archive_dir / relative_path,
        runtime_dir / relative_path,
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if archive_root not in resolved.parents:
            continue
        if resolved.is_file() and resolved.suffix == ".json":
            return resolved

    raise HTTPException(status_code=404, detail=f"LLM completion file not found: {requested_path}")


def _resolve_stage3_llm_completion_file(
    *,
    runtime_dir: Path,
    role: str,
    requested_path: str,
) -> Path:
    if role not in {"breaker"}:
        raise HTTPException(status_code=404, detail=f"unknown LLM completion archive role: {role}")

    archive_dir = _stage3_llm_completion_archive_dir(runtime_dir, role)
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"LLM completion archive not found for {role}")

    relative_path = Path(requested_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=400, detail="invalid LLM completion file path")

    archive_root = archive_dir.resolve()
    candidates = [
        archive_dir / relative_path,
        runtime_dir / relative_path,
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if archive_root not in resolved.parents:
            continue
        if resolved.is_file() and resolved.suffix == ".json":
            return resolved

    raise HTTPException(status_code=404, detail=f"LLM completion file not found: {requested_path}")


def _resolve_stage4_llm_completion_file(
    *,
    runtime_dir: Path,
    role: str,
    requested_path: str,
    allowed_roles: set[str],
) -> Path:
    if role not in allowed_roles:
        raise HTTPException(status_code=404, detail=f"unknown LLM completion archive role: {role}")

    archive_dir = _stage4_llm_completion_archive_dir(runtime_dir, role)
    if not archive_dir.exists() or not archive_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"LLM completion archive not found for {role}")

    relative_path = Path(requested_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise HTTPException(status_code=400, detail="invalid LLM completion file path")

    archive_root = archive_dir.resolve()
    candidates = [
        archive_dir / relative_path,
        runtime_dir / relative_path,
    ]
    for candidate in candidates:
        resolved = candidate.resolve()
        if archive_root not in resolved.parents:
            continue
        if resolved.is_file() and resolved.suffix == ".json":
            return resolved

    raise HTTPException(status_code=404, detail=f"LLM completion file not found: {requested_path}")


def _stage4_llm_completion_roles(run: Stage4Run) -> set[str]:
    runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
    try:
        return {spec.id for spec in runtime_issue_style_specs(runtime_stage4)}
    except IssueStyleConfigError:
        return set()


def _list_stage2_agent_server_container_ids() -> list[str]:
    return _list_agent_server_container_ids(stage="stage2")


def _list_stage3_agent_server_container_ids() -> list[str]:
    return _list_agent_server_container_ids(stage="stage3")


def _list_stage4_agent_server_container_ids() -> list[str]:
    return _list_agent_server_container_ids(stage="stage4")


def _list_stage3_eval_container_ids() -> list[str]:
    container_ids: set[str] = set()
    container_ids.update(
        _list_docker_container_ids(
            [
                f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED}=true",
                f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE}=stage3",
                f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT}={FEATURE_FACTORY_STAGE3_EVAL_COMPONENT}",
            ],
            description="managed stage3 eval",
        )
    )
    container_ids.update(
        _list_docker_container_ids(
            [f"name={FEATURE_FACTORY_STAGE3_EVAL_CONTAINER_NAME_PREFIX}"],
            description="legacy stage3 eval",
        )
    )
    return sorted(container_ids)


def _list_agent_server_container_ids(*, stage: str) -> list[str]:
    return _list_docker_container_ids(
        [
            f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED}=true",
            f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE}={stage}",
            f"label={STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT}=agent-server",
        ],
        description=f"managed {stage} agent-server",
    )


def _list_docker_container_ids(filters: list[str], *, description: str) -> list[str]:
    command = [
        "docker",
        "ps",
        "-aq",
    ]
    for item in filters:
        command.extend(["--filter", item])
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        print(
            f"[feature_factory] failed to list {description} containers: "
            f"{completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}",
            flush=True,
        )
        return []
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _inspect_stage2_agent_server_container(container_id: str) -> dict[str, Any] | None:
    return _inspect_docker_container(container_id, description="managed agent-server")


def _inspect_docker_container(container_id: str, *, description: str) -> dict[str, Any] | None:
    completed = subprocess.run(
        ["docker", "inspect", container_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        print(
            f"[feature_factory] failed to inspect {description} container "
            f"{container_id}: {completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}",
            flush=True,
        )
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        print(
            f"[feature_factory] failed to parse docker inspect output for {description} "
            f"container {container_id}: {exc}",
            flush=True,
        )
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    return payload[0]


def _remove_stage2_agent_server_container(container_id: str) -> bool:
    return _remove_docker_container(container_id, description="managed agent-server")


def _remove_docker_container(container_id: str, *, description: str) -> bool:
    completed = subprocess.run(
        ["docker", "rm", "-f", container_id],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode == 0:
        return True
    print(
        f"[feature_factory] failed to remove {description} container "
        f"{container_id}: {completed.stderr.strip() or completed.stdout.strip() or 'unknown error'}",
        flush=True,
    )
    return False


class _Stage2SdkPrewarmJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._last_errors: dict[str, str] = {}
        self._last_statuses: dict[str, str] = {}
        self._log_lines: dict[str, list[str]] = {}
        self._log_line_limit = 1200

    def start(
        self,
        target: str,
        callback: Callable[[Callable[[str], None], Callable[[], bool]], Any],
    ) -> bool:
        normalized_target = str(target or "").strip()
        if not normalized_target:
            return False
        with self._lock:
            self._prune_locked()
            if normalized_target in self._active:
                return False
            self._log_lines[normalized_target] = []
            self._last_statuses[normalized_target] = "running"
            self._last_errors.pop(normalized_target, None)
            cancel_event = threading.Event()
            thread = threading.Thread(
                target=self._run,
                args=(normalized_target, callback, cancel_event),
                name=f"stage2-sdk-prewarm-{normalized_target}",
                daemon=True,
            )
            self._active[normalized_target] = thread
            self._cancel_events[normalized_target] = cancel_event
            thread.start()
            return True

    def cancel(self, target: str) -> bool:
        normalized_target = str(target or "").strip()
        if not normalized_target:
            return False
        with self._lock:
            self._prune_locked()
            cancel_event = self._cancel_events.get(normalized_target)
            if normalized_target not in self._active or cancel_event is None:
                return False
            cancel_event.set()
            self._last_statuses[normalized_target] = "cancelling"
            self._append_log_locked(normalized_target, "取消请求已发送，正在停止后台预热任务...")
            return True

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._prune_locked()
            active_targets = set(self._active)
            cancelling_targets = {
                target
                for target, event in self._cancel_events.items()
                if event.is_set()
            }
            last_errors = dict(self._last_errors)
            last_statuses = dict(self._last_statuses)
            log_tails = {
                target: "\n".join(lines[-40:])
                for target, lines in self._log_lines.items()
                if lines
            }
        if "host" in active_targets:
            host_payload = payload.setdefault("host_environment", {})
            host_payload["in_progress"] = True
            host_payload["cancel_requested"] = "host" in cancelling_targets
            host_payload["last_status"] = "cancelling" if "host" in cancelling_targets else "running"
            host_payload["log_tail"] = log_tails.get("host") or "Host Python 环境预热任务正在后台执行。"
        elif last_errors.get("host") or last_statuses.get("host"):
            host_payload = payload.setdefault("host_environment", {})
            host_payload["in_progress"] = False
            host_payload["cancel_requested"] = False
            host_payload["last_status"] = last_statuses.get("host") or "failed"
            host_payload["last_error"] = last_errors.get("host") or ""
            host_payload["log_tail"] = log_tails.get("host") or last_errors.get("host") or ""
        if "planner" in active_targets:
            planner_payload = payload.setdefault("planner", {})
            planner_payload["in_progress"] = True
            planner_payload["cancel_requested"] = "planner" in cancelling_targets
            planner_payload["last_status"] = "cancelling" if "planner" in cancelling_targets else "running"
            planner_payload["log_tail"] = (
                log_tails.get("planner") or "Planner agent-server 镜像预热任务正在后台执行。"
            )
        elif last_errors.get("planner") or last_statuses.get("planner"):
            planner_payload = payload.setdefault("planner", {})
            planner_payload["in_progress"] = False
            planner_payload["cancel_requested"] = False
            planner_payload["last_status"] = last_statuses.get("planner") or "failed"
            planner_payload["last_error"] = last_errors.get("planner") or ""
            planner_payload["log_tail"] = log_tails.get("planner") or last_errors.get("planner") or ""
        return payload

    def _run(
        self,
        target: str,
        callback: Callable[[Callable[[str], None], Callable[[], bool]], Any],
        cancel_event: threading.Event,
    ) -> None:
        try:
            log_callback = self._append_log_callback(target)
            parameters = inspect.signature(callback).parameters
            accepts_cancel = any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in parameters.values()
            ) or len(parameters) >= 2
            if accepts_cancel:
                callback(log_callback, cancel_event.is_set)
            else:
                callback(log_callback)
        except InterruptedError:
            with self._lock:
                self._last_statuses[target] = "cancelled"
                self._last_errors.pop(target, None)
                self._append_log_locked(target, "后台预热任务已取消。")
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                error = _format_background_job_error(exc)
                self._last_statuses[target] = "failed"
                self._last_errors[target] = error
                if error:
                    self._append_log_locked(target, error)
        else:
            with self._lock:
                self._last_statuses[target] = "succeeded"
                self._last_errors.pop(target, None)
        finally:
            with self._lock:
                self._active.pop(target, None)
                self._cancel_events.pop(target, None)

    def _append_log_callback(self, target: str) -> Callable[[str], None]:
        def append(line: str) -> None:
            with self._lock:
                self._append_log_locked(target, line)

        return append

    def _append_log_locked(self, target: str, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        lines = self._log_lines.setdefault(target, [])
        lines.append(text)
        if len(lines) > self._log_line_limit:
            self._log_lines[target] = lines[-self._log_line_limit :]

    def _prune_locked(self) -> None:
        inactive = [target for target, thread in self._active.items() if not thread.is_alive()]
        for target in inactive:
            self._active.pop(target, None)
            self._cancel_events.pop(target, None)


def _format_background_job_error(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        command = " ".join(str(part) for part in (exc.cmd or []))
        detail = f"exit code {exc.returncode}"
        if command:
            detail = f"{detail}: {command}"
        output = str(exc.stderr or exc.output or "").strip()
        if output:
            detail = f"{detail}\n{output[-4000:]}"
        return detail
    return str(exc) or type(exc).__name__


def _latest_datetime(*values: datetime | None) -> datetime | None:
    latest: datetime | None = None
    for value in values:
        if value is None:
            continue
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if latest is None or normalized > latest:
            latest = normalized
    return latest


def _iter_feature_factory_image_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, str):
        refs.extend(match.strip() for match in FEATURE_FACTORY_IMAGE_REF_RE.findall(value))
    elif isinstance(value, dict):
        for child in value.values():
            refs.extend(_iter_feature_factory_image_refs(child))
    elif isinstance(value, (list, tuple, set)):
        for child in value:
            refs.extend(_iter_feature_factory_image_refs(child))
    return refs


def _remember_image_usage(mapping: dict[str, datetime], image_ref: str, used_at: datetime | None) -> None:
    normalized_ref = str(image_ref or "").strip()
    if not normalized_ref or used_at is None:
        return
    normalized_used_at = used_at.replace(tzinfo=UTC) if used_at.tzinfo is None else used_at.astimezone(UTC)
    existing = mapping.get(normalized_ref)
    if existing is None or normalized_used_at > existing:
        mapping[normalized_ref] = normalized_used_at


def _run_usage_at(row: dict[str, Any]) -> datetime | None:
    return _latest_datetime(
        row.get("finished_at"),
        row.get("updated_at"),
        row.get("started_at"),
        row.get("created_at"),
    )


def _load_managed_image_usage(session: Any) -> dict[str, Any]:
    usage: dict[str, Any] = {
        "image_refs": {},
        "stage2_runs": {},
        "stage3_runs": {},
        "stage3_snapshots": {},
    }
    stage2_rows = session.execute(
        select(
            Stage2Run.id,
            Stage2Run.base_image,
            Stage2Run.runtime_snapshot_json,
            Stage2Run.created_at,
            Stage2Run.updated_at,
            Stage2Run.started_at,
            Stage2Run.finished_at,
        )
    ).all()
    for row in stage2_rows:
        payload = dict(row._mapping)
        used_at = _run_usage_at(payload)
        run_id = str(payload.get("id") or "")
        if run_id and used_at is not None:
            usage["stage2_runs"][run_id] = used_at
        runtime_image_refs = _iter_feature_factory_image_refs(payload.get("runtime_snapshot_json"))
        for image_ref in [payload.get("base_image"), *runtime_image_refs]:
            _remember_image_usage(usage["image_refs"], str(image_ref or ""), used_at)

    stage3_rows = session.execute(
        select(
            Stage3Run.id,
            Stage3Run.runtime_snapshot_json,
            Stage3Run.created_at,
            Stage3Run.updated_at,
            Stage3Run.started_at,
            Stage3Run.finished_at,
            Stage3EntryFile.snapshot_id,
        ).join(Stage3EntryFile, Stage3Run.entry_file_id == Stage3EntryFile.id)
    ).all()
    for row in stage3_rows:
        payload = dict(row._mapping)
        used_at = _run_usage_at(payload)
        run_id = str(payload.get("id") or "")
        snapshot_id = str(payload.get("snapshot_id") or "")
        if run_id and used_at is not None:
            usage["stage3_runs"][run_id] = used_at
        if snapshot_id:
            _remember_image_usage(usage["stage3_snapshots"], snapshot_id, used_at)
        for image_ref in _iter_feature_factory_image_refs(payload.get("runtime_snapshot_json")):
            _remember_image_usage(usage["image_refs"], image_ref, used_at)

    snapshot_rows = session.execute(
        select(
            Stage3CommitSnapshot.id,
            Stage3CommitSnapshot.created_at,
            Stage3CommitSnapshot.updated_at,
        )
    ).all()
    for row in snapshot_rows:
        payload = dict(row._mapping)
        snapshot_id = str(payload.get("id") or "")
        if not snapshot_id or snapshot_id in usage["stage3_snapshots"]:
            continue
        used_at = _latest_datetime(payload.get("updated_at"), payload.get("created_at"))
        if used_at is not None:
            usage["stage3_snapshots"][snapshot_id] = used_at
    return usage


def _docker_image_ls_payloads() -> list[dict[str, Any]]:
    completed = subprocess.run(
        ["docker", "image", "ls", "--no-trunc", "--format", "{{json .}}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=30,
    )
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"docker image ls failed: {detail or f'exit code {completed.returncode}'}"
        )
    rows: list[dict[str, Any]] = []
    for line in str(completed.stdout or "").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _is_feature_factory_image(repository: str, labels: dict[str, str], settings: Settings) -> bool:
    normalized_repository = str(repository or "").strip()
    if not normalized_repository or normalized_repository == "<none>":
        return False
    return (
        normalized_repository.startswith("feature-factory/")
        or normalized_repository.startswith("feature-factory-")
        or normalized_repository == settings.stage2_docker_image_prefix
        or any(str(key).startswith("com.feature-factory.") for key in labels)
    )


def _classify_openhands_agent_image(base_image_ref: str) -> dict[str, str]:
    base_ref = str(base_image_ref or "").strip()
    if base_ref == PLANNER_AGENT_SERVER_BASE_IMAGE:
        return {
            "category": "openhands_planner_image",
            "category_label": "Stage2 Planner 包装镜像",
            "stage": "stage2",
        }
    if base_ref.startswith(f"{FEATURE_FACTORY_STAGE2_WORKER_CHECKPOINT_REPOSITORY}:"):
        return {
            "category": "openhands_checkpoint_image",
            "category_label": "Stage2 Checkpoint 包装镜像",
            "stage": "stage2",
        }
    if base_ref.startswith(f"{FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_REPOSITORY}:"):
        return {
            "category": "openhands_checkpoint_image",
            "category_label": "Stage3 Checkpoint 包装镜像",
            "stage": "stage3",
        }
    if base_ref.startswith(f"{FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY}:"):
        return {
            "category": "openhands_stage3_breaker_image",
            "category_label": "Stage3 Breaker 包装镜像",
            "stage": "stage3",
        }
    if base_ref.startswith("feature-factory/"):
        return {
            "category": "openhands_stage2_worker_image",
            "category_label": "Stage2 Worker 包装镜像",
            "stage": "stage2",
        }
    return {
        "category": "openhands_agent_image",
        "category_label": "OpenHands 包装镜像",
        "stage": "",
    }


def _classify_managed_image(
    *,
    repository: str,
    labels: dict[str, str],
    settings: Settings,
) -> dict[str, str]:
    stage2_kind = str(labels.get(FEATURE_FACTORY_STAGE2_KIND_LABEL) or "")
    stage3_kind = str(labels.get(FEATURE_FACTORY_STAGE3_KIND_LABEL) or "")
    base_image_ref = str(labels.get(FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL) or "")
    if repository == FEATURE_FACTORY_STAGE2_WORKER_CHECKPOINT_REPOSITORY:
        return {"category": "checkpoint_image", "category_label": "Stage2 Checkpoint 镜像", "stage": "stage2"}
    if repository == FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_REPOSITORY:
        return {"category": "checkpoint_image", "category_label": "Stage3 Checkpoint 镜像", "stage": "stage3"}
    if (
        stage3_kind == FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_KIND
        or repository == FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY
    ):
        return {
            "category": "stage3_runtime_image",
            "category_label": "Stage3 Runtime 镜像",
            "stage": "stage3",
        }
    if stage2_kind == FEATURE_FACTORY_STAGE2_BASE_IMAGE_KIND:
        return {"category": "base_image", "category_label": "base image", "stage": "stage2"}
    if stage2_kind == FEATURE_FACTORY_STAGE2_AGENT_SERVER_KIND:
        return _classify_openhands_agent_image(base_image_ref)
    if repository == FEATURE_FACTORY_AGENT_SERVER_IMAGE_REPOSITORY:
        return _classify_openhands_agent_image(base_image_ref)
    if repository == settings.stage2_docker_image_prefix or repository.startswith("feature-factory-stage2"):
        return {"category": "validator_image", "category_label": "validator image", "stage": "stage2"}
    if repository.startswith("feature-factory/"):
        return {"category": "base_image", "category_label": "base image", "stage": "stage2"}
    return {"category": "feature_factory_image", "category_label": "FeatureFactory image", "stage": ""}


def _infer_managed_image_last_used_at(
    *,
    image_ref: str,
    repository: str,
    tag: str,
    labels: dict[str, str],
    usage: dict[str, Any],
    settings: Settings,
) -> tuple[datetime | None, str]:
    direct_used_at = usage["image_refs"].get(image_ref)
    if direct_used_at is not None:
        return direct_used_at, "runtime_reference"
    if repository == settings.stage2_docker_image_prefix or repository.startswith("feature-factory-stage2"):
        used_at = usage["stage2_runs"].get(tag)
        return (used_at, "stage2_run") if used_at is not None else (None, "")
    if repository == FEATURE_FACTORY_STAGE2_WORKER_CHECKPOINT_REPOSITORY:
        match = re.match(r"(?P<run_id>.+)-attempt-\d+$", tag)
        used_at = usage["stage2_runs"].get(match.group("run_id")) if match else None
        return (used_at, "stage2_run") if used_at is not None else (None, "")
    if repository == FEATURE_FACTORY_STAGE3_BREAKER_CHECKPOINT_REPOSITORY:
        match = re.match(r"(?P<run_id>.+)-depth-\d+$", tag)
        used_at = usage["stage3_runs"].get(match.group("run_id")) if match else None
        return (used_at, "stage3_run") if used_at is not None else (None, "")
    snapshot_id = str(labels.get(FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL) or "")
    if snapshot_id:
        used_at = usage["stage3_snapshots"].get(snapshot_id)
        return (used_at, "stage3_snapshot") if used_at is not None else (None, "")
    base_ref = str(labels.get(FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL) or "")
    if base_ref.startswith(f"{FEATURE_FACTORY_STAGE3_BREAKER_RUNTIME_IMAGE_REPOSITORY}:"):
        base_inspect = inspect_docker_image(base_ref)
        base_labels = dict(base_inspect.get("labels") or {})
        base_snapshot_id = str(base_labels.get(FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL) or "")
        if base_snapshot_id:
            used_at = usage["stage3_snapshots"].get(base_snapshot_id)
            return (used_at, "stage3_snapshot") if used_at is not None else (None, "")
    return None, ""


def _list_feature_factory_managed_images(session: Any, settings: Settings) -> dict[str, Any]:
    usage = _load_managed_image_usage(session)
    rows: list[dict[str, Any]] = []
    size_by_image_id: dict[str, int] = {}
    for payload in _docker_image_ls_payloads():
        repository = str(payload.get("Repository") or "").strip()
        tag = str(payload.get("Tag") or "").strip()
        if not repository or not tag or repository == "<none>" or tag == "<none>":
            continue
        image_ref = f"{repository}:{tag}"
        inspect_payload = inspect_docker_image(image_ref)
        labels = dict(inspect_payload.get("labels") or {})
        if not _is_feature_factory_image(repository, labels, settings):
            continue
        classification = _classify_managed_image(repository=repository, labels=labels, settings=settings)
        last_used_at, last_used_source = _infer_managed_image_last_used_at(
            image_ref=image_ref,
            repository=repository,
            tag=tag,
            labels=labels,
            usage=usage,
            settings=settings,
        )
        image_id = str(inspect_payload.get("image_id") or payload.get("ID") or "")
        size_bytes = int(inspect_payload.get("size_bytes") or 0)
        if image_id and size_bytes > 0:
            size_by_image_id[image_id] = max(size_by_image_id.get(image_id, 0), size_bytes)
        rows.append(
            {
                "image_ref": image_ref,
                "repository": repository,
                "tag": tag,
                "image_id": image_id,
                "created_at": _serialize_datetime(inspect_payload.get("created_at")),
                "last_used_at": _serialize_datetime(last_used_at),
                "last_used_source": last_used_source,
                "size": str(payload.get("Size") or payload.get("VirtualSize") or ""),
                "size_bytes": size_bytes,
                "containers": str(payload.get("Containers") or ""),
                "labels": labels,
                **classification,
            }
        )
    rows.sort(
        key=lambda row: (
            str(row.get("last_used_at") or ""),
            str(row.get("created_at") or ""),
            str(row.get("image_ref") or ""),
        ),
        reverse=True,
    )
    return {
        "generated_at": _serialize_datetime(datetime.now(UTC)),
        "count": len(rows),
        "total_size_bytes": sum(size_by_image_id.values()),
        "rows": rows,
    }


def _delete_local_docker_image(image_ref: str) -> None:
    normalized_ref = str(image_ref or "").strip()
    if not normalized_ref:
        raise ValueError("missing image ref")
    completed = subprocess.run(
        ["docker", "image", "rm", "-f", normalized_ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        raise RuntimeError(
            f"docker image rm failed for {normalized_ref}: {detail or f'exit code {completed.returncode}'}"
        )


def _parse_image_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _image_retention_reference(row: dict[str, Any]) -> datetime | None:
    return _parse_image_datetime(row.get("last_used_at")) or _parse_image_datetime(row.get("created_at"))


def _stage3_runtime_prune_cutoff(settings: Settings, *, now: datetime | None = None) -> datetime:
    retention = max(float(settings.stage3_runtime_image_retention_days or 0.0), 0.0)
    current = now or datetime.now(UTC)
    current = current.replace(tzinfo=UTC) if current.tzinfo is None else current.astimezone(UTC)
    return current - timedelta(days=retention)


def _active_stage3_runtime_snapshot_ids(session: Any) -> set[str]:
    active_stage3_statuses = {
        Stage3RunStatus.pending.value,
        Stage3RunStatus.queued.value,
        Stage3RunStatus.running.value,
    }
    active_stage4_statuses = {
        Stage4RunStatus.pending.value,
        Stage4RunStatus.queued.value,
        Stage4RunStatus.running.value,
    }
    snapshot_ids = set(
        str(value or "").strip()
        for value in session.scalars(
            select(Stage3EntryFile.snapshot_id)
            .join(Stage3Run, Stage3Run.entry_file_id == Stage3EntryFile.id)
            .where(Stage3Run.status.in_(active_stage3_statuses))
        )
    )
    snapshot_ids.update(
        str(value or "").strip()
        for value in session.scalars(
            select(Stage3EntryFile.snapshot_id)
            .join(Stage3Run, Stage3Run.entry_file_id == Stage3EntryFile.id)
            .join(Stage3Savepoint, Stage3Savepoint.run_id == Stage3Run.id)
            .join(Stage4Run, Stage4Run.source_savepoint_id == Stage3Savepoint.id)
            .where(Stage4Run.status.in_(active_stage4_statuses))
        )
    )
    return {snapshot_id for snapshot_id in snapshot_ids if snapshot_id}


def _docker_image_has_running_containers(image_ref: str) -> bool:
    normalized_ref = str(image_ref or "").strip()
    if not normalized_ref:
        return False
    try:
        completed = subprocess.run(
            ["docker", "ps", "-q", "--filter", f"ancestor={normalized_ref}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception:
        return False
    return completed.returncode == 0 and bool(str(completed.stdout or "").strip())


def _stage3_runtime_image_prune_plan(
    session: Any,
    settings: Settings,
    *,
    status_payload: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = status_payload or _list_feature_factory_managed_images(session, settings)
    rows = [dict(row or {}) for row in list(payload.get("rows") or [])]
    active_snapshot_ids = _active_stage3_runtime_snapshot_ids(session)
    cutoff = _stage3_runtime_prune_cutoff(settings, now=now)
    runtime_rows: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    expired_runtime_refs: set[str] = set()
    for row in rows:
        if str(row.get("category") or "") != "stage3_runtime_image":
            continue
        image_ref = str(row.get("image_ref") or "").strip()
        labels = dict(row.get("labels") or {})
        snapshot_id = str(labels.get(FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL) or "").strip()
        reference_at = _image_retention_reference(row)
        if not image_ref:
            continue
        if snapshot_id and snapshot_id in active_snapshot_ids:
            skipped.append({"image_ref": image_ref, "reason": "active_snapshot", "snapshot_id": snapshot_id})
            continue
        if reference_at is None:
            skipped.append({"image_ref": image_ref, "reason": "missing_usage_timestamp", "snapshot_id": snapshot_id})
            continue
        if reference_at >= cutoff:
            skipped.append({"image_ref": image_ref, "reason": "within_retention", "snapshot_id": snapshot_id})
            continue
        runtime_rows.append(row)
        expired_runtime_refs.add(image_ref)

    dependent_agent_rows = [
        row
        for row in rows
        if str(row.get("category") or "") == "openhands_stage3_breaker_image"
        and str((dict(row.get("labels") or {})).get(FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL) or "").strip()
        in expired_runtime_refs
    ]
    delete_rows = dependent_agent_rows + runtime_rows
    return {
        "cutoff": cutoff,
        "runtime_refs": sorted(expired_runtime_refs),
        "delete_refs": [str(row.get("image_ref") or "").strip() for row in delete_rows if str(row.get("image_ref") or "").strip()],
        "skipped": skipped,
    }


def _prune_stage3_runtime_images_once(session: Any, settings: Settings) -> dict[str, Any]:
    plan = _stage3_runtime_image_prune_plan(session, settings)
    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    skipped_running_containers: list[str] = []
    seen: set[str] = set()
    for image_ref in list(plan.get("delete_refs") or []):
        normalized_ref = str(image_ref or "").strip()
        if not normalized_ref or normalized_ref in seen:
            continue
        seen.add(normalized_ref)
        if _docker_image_has_running_containers(normalized_ref):
            skipped_running_containers.append(normalized_ref)
            continue
        try:
            _delete_local_docker_image(normalized_ref)
        except Exception as exc:  # noqa: BLE001
            failed.append({"image_ref": normalized_ref, "error": str(exc) or type(exc).__name__})
            continue
        deleted.append(normalized_ref)
    return {
        "cutoff": _serialize_datetime(plan.get("cutoff")),
        "deleted": deleted,
        "failed": failed,
        "skipped_running_containers": skipped_running_containers,
        "skipped": list(plan.get("skipped") or []),
    }


@dataclass
class _Stage2ImageAssetTargetState:
    target: str
    selected: bool = False
    in_progress: bool = False
    last_status: str = ""
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log_lines: list[str] = field(default_factory=list)
    log_line_limit: int = 1200
    log_truncated: bool = False

    def reset_for_run(self) -> None:
        self.selected = True
        self.in_progress = False
        self.last_status = "queued"
        self.last_error = ""
        self.started_at = None
        self.finished_at = None
        self.log_lines = []
        self.log_truncated = False

    def append_log(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        self.log_lines.append(text)
        if len(self.log_lines) > self.log_line_limit:
            self.log_lines = self.log_lines[-self.log_line_limit :]
            self.log_truncated = True

    def as_payload(self, *, include_logs: bool) -> dict[str, Any]:
        payload = {
            "target": self.target,
            "selected": self.selected,
            "in_progress": self.in_progress,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "started_at": _serialize_datetime(self.started_at),
            "finished_at": _serialize_datetime(self.finished_at),
            "log_line_count": len(self.log_lines),
            "log_truncated": self.log_truncated,
        }
        if include_logs:
            payload["log_text"] = "\n".join(self.log_lines)
        else:
            payload["log_tail"] = "\n".join(self.log_lines[-40:])
        return payload


@dataclass
class _Stage2ImageAssetBuildJob:
    asset_key: str
    include_base_image: bool
    include_agent_server_image: bool
    force: bool
    created_at: datetime
    current_target: str | None = None
    thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    base: _Stage2ImageAssetTargetState = field(
        default_factory=lambda: _Stage2ImageAssetTargetState(target="base")
    )
    agent: _Stage2ImageAssetTargetState = field(
        default_factory=lambda: _Stage2ImageAssetTargetState(target="agent")
    )

    def any_in_progress(self) -> bool:
        return bool(self.base.in_progress or self.agent.in_progress)

    def is_active(self) -> bool:
        return bool(self.thread and self.thread.is_alive()) or self.any_in_progress()

    def target_state(self, target: str) -> _Stage2ImageAssetTargetState:
        return self.base if target == "base" else self.agent

    def as_payload(self, *, include_logs: bool = False) -> dict[str, Any]:
        return {
            "asset_key": self.asset_key,
            "include_base_image": self.include_base_image,
            "include_agent_server_image": self.include_agent_server_image,
            "force": self.force,
            "in_progress": self.is_active(),
            "cancel_requested": self.cancel_event.is_set(),
            "current_target": self.current_target or "",
            "created_at": _serialize_datetime(self.created_at),
            "base": self.base.as_payload(include_logs=include_logs),
            "agent": self.agent.as_payload(include_logs=include_logs),
        }


class _Stage2ImageAssetBuildHooks:
    def __init__(self, manager: "_Stage2ImageAssetBuildJobManager", asset_key: str) -> None:
        self._manager = manager
        self._asset_key = asset_key
        self._current_target: str | None = None

    def target_started(self, target: str) -> None:
        normalized_target = "agent" if str(target or "").strip() == "agent" else "base"
        self._current_target = normalized_target
        self._manager.mark_target_started(self._asset_key, normalized_target)

    def target_finished(self, target: str) -> None:
        normalized_target = "agent" if str(target or "").strip() == "agent" else "base"
        self._manager.mark_target_succeeded(self._asset_key, normalized_target)
        if self._current_target == normalized_target:
            self._current_target = None

    def log(self, line: str) -> None:
        target = self._current_target or "base"
        self._manager.append_log(self._asset_key, target, line)

    def cancel_requested(self) -> bool:
        return self._manager.is_cancel_requested(self._asset_key)


class _Stage2ImageAssetBuildJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Stage2ImageAssetBuildJob] = {}

    def start(
        self,
        *,
        asset_key: str,
        include_base_image: bool,
        include_agent_server_image: bool,
        force: bool,
        callback: Callable[[_Stage2ImageAssetBuildHooks], Any],
    ) -> bool:
        normalized_asset_key = str(asset_key or "").strip()
        if not normalized_asset_key:
            raise ValueError("missing image asset key")
        if not include_base_image and not include_agent_server_image:
            raise ValueError("at least one image target must be selected")
        with self._lock:
            existing = self._jobs.get(normalized_asset_key)
            if existing and existing.is_active():
                return False
            job = existing or _Stage2ImageAssetBuildJob(
                asset_key=normalized_asset_key,
                include_base_image=include_base_image,
                include_agent_server_image=include_agent_server_image,
                force=force,
                created_at=datetime.now(UTC),
            )
            job.include_base_image = include_base_image
            job.include_agent_server_image = include_agent_server_image
            job.force = force
            job.created_at = datetime.now(UTC)
            job.current_target = None
            job.cancel_event = threading.Event()
            if include_base_image:
                job.base.reset_for_run()
            if include_agent_server_image:
                job.agent.reset_for_run()
            thread = threading.Thread(
                target=self._run,
                args=(normalized_asset_key, callback),
                name=f"stage2-image-asset-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', normalized_asset_key)[:80]}",
                daemon=True,
            )
            job.thread = thread
            self._jobs[normalized_asset_key] = job
            thread.start()
            return True

    def cancel(self, asset_key: str) -> bool:
        normalized_asset_key = str(asset_key or "").strip()
        if not normalized_asset_key:
            return False
        with self._lock:
            job = self._jobs.get(normalized_asset_key)
            if job is None or not job.is_active():
                return False
            job.cancel_event.set()
            target = job.current_target or (
                "agent" if job.agent.in_progress else "base"
            )
            state = job.target_state(target)
            state.append_log("取消请求已发送，正在停止后台下载任务...")
            return True

    def is_cancel_requested(self, asset_key: str) -> bool:
        with self._lock:
            job = self._jobs.get(str(asset_key or "").strip())
            return bool(job is not None and job.cancel_event.is_set())

    def apply(self, payload: dict[str, Any], *, include_logs: bool = False) -> dict[str, Any]:
        with self._lock:
            job_payloads = {
                asset_key: job.as_payload(include_logs=include_logs)
                for asset_key, job in self._jobs.items()
            }

        def apply_job_to_row(row: dict[str, Any]) -> None:
            asset_key = str(row.get("asset_key") or row.get("image_id") or "").strip()
            job = job_payloads.get(asset_key)
            if not job:
                return
            row["build_job"] = job
            self._apply_job_status(row.setdefault("base_image", {}), job.get("base") or {})
            self._apply_job_status(row.setdefault("agent_server_image", {}), job.get("agent") or {})

        rows = payload.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                apply_job_to_row(row)
        row = payload.get("row")
        if isinstance(row, dict):
            apply_job_to_row(row)
        payload["build_jobs"] = list(job_payloads.values())
        return payload

    def detail_payload(self, asset_key: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(asset_key or "").strip())
            return job.as_payload(include_logs=True) if job is not None else {}

    def mark_target_started(self, asset_key: str, target: str) -> None:
        with self._lock:
            job = self._jobs.get(asset_key)
            if job is None:
                return
            target_state = job.target_state(target)
            target_state.in_progress = True
            target_state.last_status = "running"
            target_state.last_error = ""
            target_state.started_at = datetime.now(UTC)
            target_state.finished_at = None
            job.current_target = target

    def mark_target_succeeded(self, asset_key: str, target: str) -> None:
        with self._lock:
            job = self._jobs.get(asset_key)
            if job is None:
                return
            target_state = job.target_state(target)
            target_state.in_progress = False
            target_state.last_status = "succeeded"
            target_state.last_error = ""
            target_state.finished_at = datetime.now(UTC)
            if job.current_target == target:
                job.current_target = None

    def mark_target_failed(self, asset_key: str, target: str, error: str) -> None:
        with self._lock:
            job = self._jobs.get(asset_key)
            if job is None:
                return
            target_state = job.target_state(target)
            target_state.in_progress = False
            target_state.last_status = "failed"
            target_state.last_error = error
            target_state.finished_at = datetime.now(UTC)
            if job.current_target == target:
                job.current_target = None

    def append_log(self, asset_key: str, target: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(asset_key)
            if job is None:
                return
            job.target_state(target).append_log(line)

    def _apply_job_status(self, status_payload: dict[str, Any], job: dict[str, Any]) -> None:
        status_payload["job"] = job
        status_payload["in_progress"] = bool(job.get("in_progress"))
        status_payload["cancel_requested"] = bool(job.get("cancel_requested"))
        status_payload["last_job_status"] = str(job.get("last_status") or "")
        status_payload["last_job_error"] = str(job.get("last_error") or "")
        status_payload["last_job_started_at"] = job.get("started_at")
        status_payload["last_job_finished_at"] = job.get("finished_at")
        if "log_tail" in job:
            status_payload["job_log_tail"] = str(job.get("log_tail") or "")
        if "log_text" in job:
            status_payload["job_log_text"] = str(job.get("log_text") or "")
        status_payload["job_log_line_count"] = int(job.get("log_line_count") or 0)
        status_payload["job_log_truncated"] = bool(job.get("log_truncated"))

    def _run(self, asset_key: str, callback: Callable[[_Stage2ImageAssetBuildHooks], Any]) -> None:
        hooks = _Stage2ImageAssetBuildHooks(self, asset_key)
        try:
            callback(hooks)
        except InterruptedError:
            with self._lock:
                job = self._jobs.get(asset_key)
                if job is not None:
                    targets = [
                        target
                        for target in ("base", "agent")
                        if job.target_state(target).in_progress or job.target_state(target).last_status == "queued"
                    ]
                    if not targets:
                        targets = [job.current_target or "base"]
                    for target in targets:
                        target_state = job.target_state(target)
                        target_state.in_progress = False
                        target_state.last_status = "cancelled"
                        target_state.last_error = ""
                        target_state.finished_at = datetime.now(UTC)
                        target_state.append_log("后台下载任务已取消。")
                    job.current_target = None
                    job.thread = None
        except Exception as exc:  # noqa: BLE001
            formatted_error = _format_background_job_error(exc)
            with self._lock:
                job = self._jobs.get(asset_key)
                if job is not None:
                    failed_target = job.current_target or (
                        "agent" if job.include_agent_server_image and not job.include_base_image else "base"
                    )
                    target_state = job.target_state(failed_target)
                    target_state.in_progress = False
                    target_state.last_status = "failed"
                    target_state.last_error = formatted_error
                    target_state.finished_at = datetime.now(UTC)
                    target_state.append_log(formatted_error)
                    if job.current_target == failed_target:
                        job.current_target = None
                    job.thread = None
        else:
            with self._lock:
                job = self._jobs.get(asset_key)
                if job is not None:
                    if job.include_base_image and job.base.last_status == "queued":
                        job.base.last_status = "succeeded"
                        job.base.finished_at = datetime.now(UTC)
                    if job.include_agent_server_image and job.agent.last_status == "queued":
                        job.agent.last_status = "succeeded"
                        job.agent.finished_at = datetime.now(UTC)
                    job.thread = None


@dataclass
class _Stage3SnapshotImagePrewarmTargetState:
    target: str
    selected: bool = True
    in_progress: bool = False
    last_status: str = ""
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    log_lines: list[str] = field(default_factory=list)
    log_line_limit: int = 1200
    log_truncated: bool = False

    def reset_for_run(self) -> None:
        self.in_progress = False
        self.last_status = "queued"
        self.last_error = ""
        self.started_at = None
        self.finished_at = None
        self.log_lines = []
        self.log_truncated = False

    def append_log(self, line: str) -> None:
        text = str(line or "")
        if not text:
            return
        self.log_lines.append(text)
        if len(self.log_lines) > self.log_line_limit:
            self.log_lines = self.log_lines[-self.log_line_limit :]
            self.log_truncated = True

    def as_payload(self, *, include_logs: bool) -> dict[str, Any]:
        payload = {
            "target": self.target,
            "selected": self.selected,
            "in_progress": self.in_progress,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "started_at": _serialize_datetime(self.started_at),
            "finished_at": _serialize_datetime(self.finished_at),
            "log_line_count": len(self.log_lines),
            "log_truncated": self.log_truncated,
        }
        if include_logs:
            payload["log_text"] = "\n".join(self.log_lines)
        else:
            payload["log_tail"] = "\n".join(self.log_lines[-40:])
        return payload


@dataclass
class _Stage3SnapshotImagePrewarmJob:
    repository_id: int
    snapshot_id: str
    force: bool
    created_at: datetime
    current_target: str | None = None
    thread: threading.Thread | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    runtime: _Stage3SnapshotImagePrewarmTargetState = field(
        default_factory=lambda: _Stage3SnapshotImagePrewarmTargetState(target="runtime")
    )
    agent: _Stage3SnapshotImagePrewarmTargetState = field(
        default_factory=lambda: _Stage3SnapshotImagePrewarmTargetState(target="agent")
    )

    def is_active(self) -> bool:
        return bool(self.thread and self.thread.is_alive()) or self.runtime.in_progress or self.agent.in_progress

    def target_state(self, target: str) -> _Stage3SnapshotImagePrewarmTargetState:
        return self.agent if str(target or "").strip() == "agent" else self.runtime

    def as_payload(self, *, include_logs: bool = False) -> dict[str, Any]:
        return {
            "repository_id": self.repository_id,
            "snapshot_id": self.snapshot_id,
            "force": self.force,
            "in_progress": self.is_active(),
            "cancel_requested": self.cancel_event.is_set(),
            "current_target": self.current_target or "",
            "created_at": _serialize_datetime(self.created_at),
            "runtime": self.runtime.as_payload(include_logs=include_logs),
            "agent": self.agent.as_payload(include_logs=include_logs),
        }


class _Stage3SnapshotImagePrewarmHooks:
    def __init__(self, manager: "_Stage3SnapshotImagePrewarmJobManager", snapshot_id: str) -> None:
        self._manager = manager
        self._snapshot_id = snapshot_id
        self._current_target: str | None = None

    def target_started(self, target: str) -> None:
        normalized_target = "agent" if str(target or "").strip() == "agent" else "runtime"
        self._current_target = normalized_target
        self._manager.mark_target_started(self._snapshot_id, normalized_target)

    def target_finished(self, target: str) -> None:
        normalized_target = "agent" if str(target or "").strip() == "agent" else "runtime"
        self._manager.mark_target_succeeded(self._snapshot_id, normalized_target)
        if self._current_target == normalized_target:
            self._current_target = None

    def log(self, line: str) -> None:
        self._manager.append_log(self._snapshot_id, self._current_target or "runtime", line)

    def cancel_requested(self) -> bool:
        return self._manager.is_cancel_requested(self._snapshot_id)


class _Stage3SnapshotImagePrewarmJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _Stage3SnapshotImagePrewarmJob] = {}

    def start(
        self,
        *,
        repository_id: int,
        snapshot_id: str,
        force: bool,
        callback: Callable[[_Stage3SnapshotImagePrewarmHooks], Any],
    ) -> bool:
        normalized_snapshot_id = str(snapshot_id or "").strip()
        if not normalized_snapshot_id:
            raise ValueError("missing snapshot id")
        with self._lock:
            existing = self._jobs.get(normalized_snapshot_id)
            if existing and existing.is_active():
                return False
            job = existing or _Stage3SnapshotImagePrewarmJob(
                repository_id=repository_id,
                snapshot_id=normalized_snapshot_id,
                force=force,
                created_at=datetime.now(UTC),
            )
            job.repository_id = int(repository_id)
            job.force = force
            job.created_at = datetime.now(UTC)
            job.current_target = None
            job.cancel_event = threading.Event()
            job.runtime.reset_for_run()
            job.agent.reset_for_run()
            thread = threading.Thread(
                target=self._run,
                args=(normalized_snapshot_id, callback),
                name=f"stage3-image-prewarm-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', normalized_snapshot_id)[:80]}",
                daemon=True,
            )
            job.thread = thread
            self._jobs[normalized_snapshot_id] = job
            thread.start()
            return True

    def cancel(self, snapshot_id: str) -> bool:
        normalized_snapshot_id = str(snapshot_id or "").strip()
        if not normalized_snapshot_id:
            return False
        with self._lock:
            job = self._jobs.get(normalized_snapshot_id)
            if job is None or not job.is_active():
                return False
            job.cancel_event.set()
            target = job.current_target or (
                "agent" if job.agent.in_progress else "runtime"
            )
            state = job.target_state(target)
            state.append_log("取消请求已发送，正在停止镜像预热任务...")
            return True

    def is_cancel_requested(self, snapshot_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(str(snapshot_id or "").strip())
            return bool(job is not None and job.cancel_event.is_set())

    def detail_payload(self, snapshot_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(str(snapshot_id or "").strip())
            return job.as_payload(include_logs=True) if job is not None else {}

    def apply(self, payload: dict[str, Any], *, include_logs: bool = False) -> dict[str, Any]:
        snapshot_id = str(payload.get("snapshot_id") or "").strip()
        if not snapshot_id:
            payload["job"] = {}
            return payload
        with self._lock:
            job = self._jobs.get(snapshot_id)
            job_payload = job.as_payload(include_logs=include_logs) if job is not None else {}
        payload["job"] = job_payload
        if job_payload:
            self._apply_target_status(payload.setdefault("runtime_image", {}), job_payload.get("runtime") or {})
            self._apply_target_status(payload.setdefault("agent_server_image", {}), job_payload.get("agent") or {})
        return payload

    def mark_target_started(self, snapshot_id: str, target: str) -> None:
        with self._lock:
            job = self._jobs.get(snapshot_id)
            if job is None:
                return
            target_state = job.target_state(target)
            target_state.in_progress = True
            target_state.last_status = "running"
            target_state.last_error = ""
            target_state.started_at = datetime.now(UTC)
            target_state.finished_at = None
            job.current_target = target

    def mark_target_succeeded(self, snapshot_id: str, target: str) -> None:
        with self._lock:
            job = self._jobs.get(snapshot_id)
            if job is None:
                return
            target_state = job.target_state(target)
            target_state.in_progress = False
            target_state.last_status = "succeeded"
            target_state.last_error = ""
            target_state.finished_at = datetime.now(UTC)
            if job.current_target == target:
                job.current_target = None

    def append_log(self, snapshot_id: str, target: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(snapshot_id)
            if job is None:
                return
            job.target_state(target).append_log(line)

    def _apply_target_status(self, status_payload: dict[str, Any], job: dict[str, Any]) -> None:
        status_payload["job"] = job
        status_payload["in_progress"] = bool(job.get("in_progress"))
        status_payload["cancel_requested"] = bool(job.get("cancel_requested"))
        status_payload["last_job_status"] = str(job.get("last_status") or "")
        status_payload["last_job_error"] = str(job.get("last_error") or "")
        status_payload["last_job_started_at"] = job.get("started_at")
        status_payload["last_job_finished_at"] = job.get("finished_at")
        if "log_tail" in job:
            status_payload["job_log_tail"] = str(job.get("log_tail") or "")
        if "log_text" in job:
            status_payload["job_log_text"] = str(job.get("log_text") or "")
        status_payload["job_log_line_count"] = int(job.get("log_line_count") or 0)
        status_payload["job_log_truncated"] = bool(job.get("log_truncated"))

    def _run(self, snapshot_id: str, callback: Callable[[_Stage3SnapshotImagePrewarmHooks], Any]) -> None:
        hooks = _Stage3SnapshotImagePrewarmHooks(self, snapshot_id)
        try:
            callback(hooks)
        except InterruptedError:
            with self._lock:
                job = self._jobs.get(snapshot_id)
                if job is not None:
                    targets = [
                        target
                        for target in ("runtime", "agent")
                        if job.target_state(target).in_progress or job.target_state(target).last_status == "queued"
                    ]
                    if not targets:
                        targets = [job.current_target or "runtime"]
                    for target in targets:
                        target_state = job.target_state(target)
                        target_state.in_progress = False
                        target_state.last_status = "cancelled"
                        target_state.last_error = ""
                        target_state.finished_at = datetime.now(UTC)
                        target_state.append_log("镜像预热任务已取消。")
                    job.current_target = None
                    job.thread = None
        except Exception as exc:  # noqa: BLE001
            formatted_error = _format_background_job_error(exc)
            with self._lock:
                job = self._jobs.get(snapshot_id)
                if job is not None:
                    failed_target = job.current_target or "runtime"
                    target_state = job.target_state(failed_target)
                    target_state.in_progress = False
                    target_state.last_status = "failed"
                    target_state.last_error = formatted_error
                    target_state.finished_at = datetime.now(UTC)
                    target_state.append_log(formatted_error)
                    if job.current_target == failed_target:
                        job.current_target = None
                    job.thread = None
        else:
            with self._lock:
                job = self._jobs.get(snapshot_id)
                if job is not None:
                    if job.runtime.last_status == "queued":
                        job.runtime.last_status = "succeeded"
                        job.runtime.finished_at = datetime.now(UTC)
                    if job.agent.last_status == "queued":
                        job.agent.last_status = "succeeded"
                        job.agent.finished_at = datetime.now(UTC)
                    job.thread = None


@dataclass
class _ManagedImageDeleteJob:
    image_ref: str
    created_at: datetime
    thread: threading.Thread | None = None
    last_status: str = "queued"
    last_error: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def is_active(self) -> bool:
        return bool(self.thread and self.thread.is_alive()) or self.last_status in {
            "queued",
            "running",
        }

    def as_payload(self) -> dict[str, Any]:
        return {
            "image_ref": self.image_ref,
            "in_progress": self.is_active(),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "created_at": _serialize_datetime(self.created_at),
            "started_at": _serialize_datetime(self.started_at),
            "finished_at": _serialize_datetime(self.finished_at),
        }


class _ManagedImageDeleteJobManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, _ManagedImageDeleteJob] = {}

    def start(self, image_ref: str, callback: Callable[[], Any]) -> bool:
        normalized_ref = str(image_ref or "").strip()
        if not normalized_ref:
            raise ValueError("missing image ref")
        with self._lock:
            existing = self._jobs.get(normalized_ref)
            if existing and existing.is_active():
                return False
            job = _ManagedImageDeleteJob(
                image_ref=normalized_ref,
                created_at=datetime.now(UTC),
            )
            thread = threading.Thread(
                target=self._run,
                args=(normalized_ref, callback),
                name=f"managed-image-delete-{re.sub(r'[^a-zA-Z0-9_.-]+', '-', normalized_ref)[:80]}",
                daemon=True,
            )
            job.thread = thread
            self._jobs[normalized_ref] = job
            thread.start()
            return True

    def apply(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            job_payloads = {
                image_ref: job.as_payload()
                for image_ref, job in self._jobs.items()
            }
        rows = payload.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                image_ref = str(row.get("image_ref") or "").strip()
                job = job_payloads.get(image_ref)
                if job:
                    row["delete_job"] = job
        payload["delete_jobs"] = list(job_payloads.values())
        return payload

    def _run(self, image_ref: str, callback: Callable[[], Any]) -> None:
        with self._lock:
            job = self._jobs.get(image_ref)
            if job is not None:
                job.last_status = "running"
                job.last_error = ""
                job.started_at = datetime.now(UTC)
        try:
            callback()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job = self._jobs.get(image_ref)
                if job is not None:
                    job.last_status = "failed"
                    job.last_error = _format_background_job_error(exc)
                    job.finished_at = datetime.now(UTC)
                    job.thread = None
        else:
            with self._lock:
                job = self._jobs.get(image_ref)
                if job is not None:
                    job.last_status = "succeeded"
                    job.last_error = ""
                    job.finished_at = datetime.now(UTC)
                    job.thread = None


def create_app(
    settings: Settings | None = None,
    *,
    runner: Stage1JobRunner | None = None,
    stage2_runner: Stage2RunRunner | None = None,
    stage3_runner: Stage3RunRunner | None = None,
    stage4_runner: Stage4RunRunner | None = None,
    batch_runner: BatchTaskRunner | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    stage2_app_instance_id = uuid4().hex
    sdk_prewarm_jobs = _Stage2SdkPrewarmJobManager()
    image_asset_build_jobs = _Stage2ImageAssetBuildJobManager()
    stage3_image_prewarm_jobs = _Stage3SnapshotImagePrewarmJobManager()
    managed_image_delete_jobs = _ManagedImageDeleteJobManager()
    postgres_manager = managed_local_postgres(
        settings,
        log=lambda message: print(f"[feature_factory] {message}", flush=True),
    )
    postgres_manager_entered = False
    session_factory = None
    repaired_duplicate_archives: list[dict[str, Any]] = []

    def _record_stage2_cleanup_tombstone_result(
        run_id: str,
        cleanup_result: dict[str, Any],
    ) -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage2Service(session)
            service.record_cleanup_tombstone_result(
                run_id,
                cleanup_result=cleanup_result,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to update stage2 cleanup tombstone "
                f"{run_id}: {exc}",
                flush=True,
            )
        finally:
            session.close()

    def _cleanup_stage2_run_archive_and_record_tombstone(archive: dict[str, Any]) -> dict[str, list[str]]:
        cleanup_result = _cleanup_stage2_archive_by_scope(settings, archive)
        run_id = str(archive.get("run_id") or "").strip()
        if run_id:
            _record_stage2_cleanup_tombstone_result(run_id, cleanup_result)
        return cleanup_result

    def _cleanup_repaired_duplicate_stage2_archives(archives: list[dict[str, Any]]) -> None:
        for archive in archives:
            try:
                cleanup_result = _cleanup_stage2_run_archive_and_record_tombstone(archive)
            except Exception as exc:  # noqa: BLE001
                print(
                    "[feature_factory] failed to cleanup repaired duplicate stage2 run "
                    f"{archive.get('run_id')}: {exc}",
                    flush=True,
                )
                continue
            failed_paths = list(cleanup_result.get("failed_paths") or [])
            failed_checkpoint_images = list(
                cleanup_result.get("failed_checkpoint_docker_image_refs") or []
            )
            failed_validator_images = list(
                cleanup_result.get("failed_validator_image_refs") or []
            )
            if failed_paths or failed_checkpoint_images or failed_validator_images:
                print(
                    "[feature_factory] failed to fully cleanup repaired duplicate stage2 run "
                    f"{archive.get('run_id')}: "
                    f"failed_paths={failed_paths}, "
                    f"failed_checkpoint_docker_image_refs={failed_checkpoint_images}, "
                    f"failed_validator_image_refs={failed_validator_images}",
                    flush=True,
                )

    def _cleanup_stage2_cleanup_tombstones() -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage2Service(session)
            archives = service.cleanup_tombstone_archives()
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to load stage2 cleanup tombstones: "
                f"{exc}",
                flush=True,
            )
            return
        finally:
            session.close()
        for archive in archives:
            try:
                cleanup_result = _cleanup_stage2_run_archive_and_record_tombstone(archive)
            except Exception as exc:  # noqa: BLE001
                print(
                    "[feature_factory] failed to cleanup stage2 tombstone "
                    f"{archive.get('run_id')}: {exc}",
                    flush=True,
                )
                continue
            if _cleanup_result_has_failures(cleanup_result):
                print(
                    "[feature_factory] stage2 cleanup tombstone retry remains incomplete "
                    f"{archive.get('run_id')}: "
                    f"{cleanup_result}",
                    flush=True,
                )

    def _record_stage3_cleanup_tombstone_result(
        run_id: str,
        cleanup_result: dict[str, Any],
        *,
        archive: dict[str, Any] | None = None,
    ) -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            cleanup_scope = str((archive or {}).get("cleanup_scope") or "").strip()
            if cleanup_scope == "breaker_checkpoint_assets" and not _cleanup_result_has_failures(cleanup_result):
                service.clear_breaker_checkpoints(run_id)
            service.record_cleanup_tombstone_result(
                run_id,
                cleanup_result=cleanup_result,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to update stage3 cleanup tombstone "
                f"{run_id}: {exc}",
                flush=True,
            )
        finally:
            session.close()

    def _cleanup_stage3_run_archive_and_record_tombstone(archive: dict[str, Any]) -> dict[str, list[str]]:
        cleanup_result = _cleanup_stage3_archive_by_scope(settings, archive)
        run_id = str(archive.get("run_id") or "").strip()
        if run_id:
            _record_stage3_cleanup_tombstone_result(run_id, cleanup_result, archive=archive)
        return cleanup_result

    def _upsert_stage3_cleanup_tombstone(archive: dict[str, Any], *, reason: str) -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            service.upsert_cleanup_tombstone(archive, reason=reason)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to record stage3 cleanup tombstone: "
                f"run_id={archive.get('run_id')}, reason={reason}, error={exc}",
                flush=True,
            )
        finally:
            session.close()

    def _cleanup_stage3_run_archive_and_tombstone_on_failure(
        archive: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, list[str]]:
        cleanup_result = _cleanup_stage3_archive_by_scope(settings, archive)
        if _cleanup_result_has_failures(cleanup_result):
            _upsert_stage3_cleanup_tombstone(archive, reason=reason)
        return cleanup_result

    def _cleanup_stage3_cleanup_tombstones() -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            archives = service.cleanup_tombstone_archives()
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to load stage3 cleanup tombstones: "
                f"{exc}",
                flush=True,
            )
            return
        finally:
            session.close()
        for archive in archives:
            try:
                cleanup_result = _cleanup_stage3_run_archive_and_record_tombstone(archive)
            except Exception as exc:  # noqa: BLE001
                print(
                    "[feature_factory] failed to cleanup stage3 tombstone "
                    f"{archive.get('run_id')}: {exc}",
                    flush=True,
                )
                continue
            if _cleanup_result_has_failures(cleanup_result):
                print(
                    "[feature_factory] stage3 cleanup tombstone retry remains incomplete "
                    f"{archive.get('run_id')}: "
                    f"{cleanup_result}",
                    flush=True,
                )

    def _record_stage4_cleanup_tombstone_result(
        run_id: str,
        cleanup_result: dict[str, Any],
    ) -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            service.record_cleanup_tombstone_result(
                run_id,
                cleanup_result=cleanup_result,
            )
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to update stage4 cleanup tombstone "
                f"{run_id}: {exc}",
                flush=True,
            )
        finally:
            session.close()

    def _cleanup_stage4_run_archive_and_record_tombstone(archive: dict[str, Any]) -> dict[str, list[str]]:
        if session_factory is None:
            return {"failed_paths": []}
        cleanup_session = session_factory()
        try:
            cleanup_service = Stage4Service(
                cleanup_session,
                workspace_root=settings.stage4_workspace_dir,
                settings=settings,
            )
            cleanup_result = cleanup_service.cleanup_run_archive(archive)
        finally:
            cleanup_session.close()
        run_id = str(archive.get("run_id") or "").strip()
        if run_id:
            _record_stage4_cleanup_tombstone_result(run_id, cleanup_result)
        return cleanup_result

    def _upsert_stage4_cleanup_tombstone(archive: dict[str, Any], *, reason: str) -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            service.upsert_cleanup_tombstone(archive, reason=reason)
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to record stage4 cleanup tombstone: "
                f"run_id={archive.get('run_id')}, reason={reason}, error={exc}",
                flush=True,
            )
        finally:
            session.close()

    def _cleanup_stage4_run_archive_and_tombstone_on_failure(
        archive: dict[str, Any],
        *,
        reason: str,
    ) -> dict[str, list[str]]:
        if session_factory is None:
            return {"failed_paths": []}
        cleanup_session = session_factory()
        try:
            cleanup_service = Stage4Service(
                cleanup_session,
                workspace_root=settings.stage4_workspace_dir,
                settings=settings,
            )
            cleanup_result = cleanup_service.cleanup_run_archive(archive)
        finally:
            cleanup_session.close()
        if _cleanup_result_has_failures(cleanup_result):
            _upsert_stage4_cleanup_tombstone(archive, reason=reason)
        return cleanup_result

    def _cleanup_stage4_cleanup_tombstones() -> None:
        if session_factory is None:
            return
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            archives = service.cleanup_tombstone_archives()
            session.commit()
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            print(
                "[feature_factory] failed to load stage4 cleanup tombstones: "
                f"{exc}",
                flush=True,
            )
            return
        finally:
            session.close()
        for archive in archives:
            try:
                cleanup_result = _cleanup_stage4_run_archive_and_record_tombstone(archive)
            except Exception as exc:  # noqa: BLE001
                print(
                    "[feature_factory] failed to cleanup stage4 tombstone "
                    f"{archive.get('run_id')}: {exc}",
                    flush=True,
                )
                continue
            if _cleanup_result_has_failures(cleanup_result):
                print(
                    "[feature_factory] stage4 cleanup tombstone retry remains incomplete "
                    f"{archive.get('run_id')}: "
                    f"{cleanup_result}",
                    flush=True,
                )

    def _cleanup_orphan_stage2_agent_server_containers() -> None:
        if session_factory is None:
            return
        container_ids = _list_stage2_agent_server_container_ids()
        if not container_ids:
            return
        session = session_factory()
        try:
            for container_id in container_ids:
                inspect_payload = _inspect_stage2_agent_server_container(container_id)
                if inspect_payload is None:
                    continue
                labels = dict(((inspect_payload.get("Config") or {}).get("Labels")) or {})
                if str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE) or "") != "stage2":
                    continue
                owner_app_instance_id = str(
                    labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_APP_INSTANCE_ID) or ""
                ).strip()
                run_id = str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID) or "").strip()
                run = session.get(Stage2Run, run_id) if run_id else None
                if run is None:
                    reason = "missing_run"
                elif run.status == Stage2RunStatus.completed.value:
                    reason = "terminal_run"
                else:
                    if owner_app_instance_id == stage2_app_instance_id:
                        continue
                    reason = "stale_active_run"
                if _remove_stage2_agent_server_container(container_id):
                    print(
                        "[feature_factory] removed orphaned stage2 agent-server container "
                        f"{container_id} (reason={reason}, run_id={run_id or '<unknown>'}, "
                        f"owner_app_instance_id={owner_app_instance_id or '<unknown>'})",
                        flush=True,
                    )
        finally:
            session.close()

    def _cleanup_orphan_stage3_agent_server_containers() -> None:
        if session_factory is None:
            return
        container_ids = _list_stage3_agent_server_container_ids()
        if not container_ids:
            return
        session = session_factory()
        try:
            for container_id in container_ids:
                inspect_payload = _inspect_stage2_agent_server_container(container_id)
                if inspect_payload is None:
                    continue
                labels = dict(((inspect_payload.get("Config") or {}).get("Labels")) or {})
                if str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE) or "") != "stage3":
                    continue
                owner_app_instance_id = str(
                    labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_APP_INSTANCE_ID) or ""
                ).strip()
                run_id = str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID) or "").strip()
                run = session.get(Stage3Run, run_id) if run_id else None
                if run is None:
                    reason = "missing_run"
                elif run.status == Stage3RunStatus.completed.value:
                    reason = "terminal_run"
                else:
                    if owner_app_instance_id == stage2_app_instance_id:
                        continue
                    reason = "stale_active_run"
                if _remove_stage2_agent_server_container(container_id):
                    print(
                        "[feature_factory] removed orphaned stage3 agent-server container "
                        f"{container_id} (reason={reason}, run_id={run_id or '<unknown>'}, "
                        f"owner_app_instance_id={owner_app_instance_id or '<unknown>'})",
                        flush=True,
                    )
        finally:
            session.close()

    def _cleanup_orphan_stage3_eval_containers() -> None:
        if session_factory is None:
            return
        container_ids = _list_stage3_eval_container_ids()
        if not container_ids:
            return
        session = session_factory()
        try:
            for container_id in container_ids:
                inspect_payload = _inspect_docker_container(
                    container_id,
                    description="stage3 eval",
                )
                if inspect_payload is None:
                    continue
                name = str((inspect_payload.get("Name") or "")).lstrip("/")
                labels = dict(((inspect_payload.get("Config") or {}).get("Labels")) or {})
                component = str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_COMPONENT) or "")
                is_managed_eval = (
                    str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_MANAGED) or "") == "true"
                    and str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE) or "") == "stage3"
                    and component == FEATURE_FACTORY_STAGE3_EVAL_COMPONENT
                )
                is_legacy_eval = name.startswith(FEATURE_FACTORY_STAGE3_EVAL_CONTAINER_NAME_PREFIX)
                if not is_managed_eval and not is_legacy_eval:
                    continue

                run_id = str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID) or "").strip()
                run = session.get(Stage3Run, run_id) if run_id else None
                if run is None:
                    reason = "legacy_or_missing_run"
                elif run.status == Stage3RunStatus.completed.value:
                    reason = "terminal_run"
                else:
                    reason = "stale_active_run"
                if _remove_docker_container(container_id, description="stage3 eval"):
                    print(
                        "[feature_factory] removed orphaned stage3 eval container "
                        f"{container_id} (reason={reason}, run_id={run_id or '<unknown>'})",
                        flush=True,
                    )
        finally:
            session.close()

    def _cleanup_orphan_stage4_agent_server_containers() -> None:
        if session_factory is None:
            return
        container_ids = _list_stage4_agent_server_container_ids()
        if not container_ids:
            return
        session = session_factory()
        try:
            for container_id in container_ids:
                inspect_payload = _inspect_stage2_agent_server_container(container_id)
                if inspect_payload is None:
                    continue
                labels = dict(((inspect_payload.get("Config") or {}).get("Labels")) or {})
                if str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_STAGE) or "") != "stage4":
                    continue
                owner_app_instance_id = str(
                    labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_APP_INSTANCE_ID) or ""
                ).strip()
                run_id = str(labels.get(STAGE2_AGENT_SERVER_DOCKER_LABEL_RUN_ID) or "").strip()
                run = session.get(Stage4Run, run_id) if run_id else None
                if run is None:
                    reason = "missing_run"
                elif run.status == Stage4RunStatus.completed.value:
                    reason = "terminal_run"
                else:
                    if owner_app_instance_id == stage2_app_instance_id:
                        continue
                    reason = "stale_active_run"
                if _remove_stage2_agent_server_container(container_id):
                    print(
                        "[feature_factory] removed orphaned stage4 agent-server container "
                        f"{container_id} (reason={reason}, run_id={run_id or '<unknown>'}, "
                        f"owner_app_instance_id={owner_app_instance_id or '<unknown>'})",
                        flush=True,
                    )
        finally:
            session.close()

    def _shutdown_runner_with_timeout(target: Any, *, timeout_seconds: float) -> None:
        shutdown = getattr(target, "shutdown", None)
        if not callable(shutdown):
            return
        try:
            parameters = inspect.signature(shutdown).parameters
        except (TypeError, ValueError):
            shutdown()
            return
        if "timeout_seconds" not in parameters:
            shutdown()
            return
        unfinished = shutdown(timeout_seconds=timeout_seconds)
        if unfinished:
            print(
                "[feature_factory] runner shutdown timed out with unfinished tasks: "
                f"{unfinished}",
                flush=True,
            )

    def _cleanup_and_finalize_cancelled_batch_task(task_id: str) -> None:
        interrupt_known_runs = getattr(env_batch_runner, "_interrupt_known_runs", None)
        if callable(interrupt_known_runs):
            interrupt_known_runs(task_id)

        session = session_factory()
        try:
            task = session.get(BatchTask, task_id)
            if task is None:
                return
            status = str(task.status or "")
            phase = str(task.phase or "")
            if status == BatchTaskStatus.cancelled.value and task.finished_at is not None:
                return
            should_mark_cancelled = (
                status in {BatchTaskStatus.pending.value, BatchTaskStatus.running.value}
                or status == BatchTaskStatus.cancelled.value
                or phase == "cancelling"
            )
            if not should_mark_cancelled:
                return
            BatchTaskService(session).mark_terminal(
                task_id,
                status=BatchTaskStatus.cancelled.value,
                phase="cancelled",
                error_message="batch task cancelled by user",
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    try:
        managed_postgres_spec = postgres_manager.__enter__()
        postgres_manager_entered = True
        engine = build_engine(settings)
        session_factory = build_session_factory(engine)
        try:
            repaired_duplicate_archives = list(
                init_db(
                    engine,
                    repair_active_runs=not settings.server_observer_mode,
                )
                or []
            )
        except InitDbRepairCleanupError as exc:
            repaired_duplicate_archives = list(exc.repaired_duplicate_archives)
            raise
        default_pool_session = session_factory()
        try:
            DataPoolService(default_pool_session).ensure_default_pool(
                name=settings.default_data_pool_name,
                root_path=settings.default_data_pool_root,
            )
            default_pool_session.commit()
        except Exception:
            default_pool_session.rollback()
            raise
        finally:
            default_pool_session.close()
        runtime_config_session = session_factory()
        try:
            _apply_persisted_stage_runtime_settings(runtime_config_session, settings)
        finally:
            runtime_config_session.close()
        token_scheduler = GitHubTokenScheduler(settings)
        job_runner = runner or Stage1JobRunner(
            session_factory,
            settings,
            max_workers=settings.stage1_max_concurrent_jobs,
            max_limit=settings.stage1_max_concurrent_jobs_cap,
            token_scheduler=token_scheduler,
        )
        env_runner = stage2_runner or Stage2RunRunner(
            session_factory,
            settings,
            max_workers=settings.stage2_max_concurrent_runs,
            max_limit=settings.stage2_max_concurrent_runs_cap,
            app_instance_id=stage2_app_instance_id,
        )
        env_stage3_runner = stage3_runner or Stage3RunRunner(
            session_factory,
            settings,
            max_workers=settings.stage3_max_concurrent_runs,
            max_limit=settings.stage3_max_concurrent_runs_cap,
            app_instance_id=stage2_app_instance_id,
        )
        env_stage4_runner = stage4_runner or Stage4RunRunner(
            session_factory,
            settings,
            max_workers=settings.stage4_max_concurrent_runs,
            max_limit=settings.stage4_max_concurrent_runs_cap,
            app_instance_id=stage2_app_instance_id,
        )
        env_batch_runner = batch_runner or BatchTaskRunner(
            session_factory,
            settings,
            stage2_runner=env_runner,
            stage3_runner=env_stage3_runner,
            stage4_runner=env_stage4_runner,
        )
    except Exception as exc:
        if repaired_duplicate_archives:
            _cleanup_repaired_duplicate_stage2_archives(repaired_duplicate_archives)
        if postgres_manager_entered:
            postgres_manager.__exit__(type(exc), exc, exc.__traceback__)
        raise

    def _record_recovered_stage2_cleanup_failure(
        archive: dict[str, Any],
        cleanup_result: dict[str, list[str]],
    ) -> None:
        run_id = str(archive.get("run_id") or "").strip()
        failed_repo_checkout_paths = list(cleanup_result.get("failed_repo_checkout_paths") or [])
        failed_validator_image_refs = list(cleanup_result.get("failed_validator_image_refs") or [])
        if not run_id or (not failed_repo_checkout_paths and not failed_validator_image_refs):
            return
        print(
            "[feature_factory] failed to fully cleanup recovered stage2 run "
            f"{run_id}: "
            f"failed_repo_checkout_paths={failed_repo_checkout_paths}, "
            f"failed_validator_image_refs={failed_validator_image_refs}",
            flush=True,
        )
        session = session_factory()
        try:
            service = Stage2Service(session)
            service.record_event(
                run_id,
                actor="system",
                phase="cleanup",
                title="Recovered run asset cleanup incomplete",
                message="Failed to remove some non-resume assets while recovering an interrupted run",
                payload={
                    "run_id": run_id,
                    "repository_id": archive.get("repository_id"),
                    "failed_repo_checkout_paths": failed_repo_checkout_paths,
                    "failed_validator_image_refs": failed_validator_image_refs,
                },
            )
            session.commit()
        except Exception as exc:
            session.rollback()
            print(
                "[feature_factory] failed to record recovered stage2 cleanup failure "
                f"for {run_id}: {exc}",
                flush=True,
            )
        finally:
            session.close()

    def _prune_stage3_runtime_images_background_once() -> None:
        if not settings.stage3_runtime_image_auto_prune:
            return
        session = session_factory()
        try:
            result = _prune_stage3_runtime_images_once(session, settings)
        except Exception as exc:  # noqa: BLE001
            print(
                "[feature_factory] stage3 runtime image auto-prune failed: "
                f"{exc}",
                flush=True,
            )
            return
        finally:
            session.close()
        deleted = list(result.get("deleted") or [])
        failed = list(result.get("failed") or [])
        skipped_running = list(result.get("skipped_running_containers") or [])
        if deleted or failed or skipped_running:
            print(
                "[feature_factory] stage3 runtime image auto-prune completed: "
                f"deleted={deleted}, failed={failed}, skipped_running_containers={skipped_running}",
                flush=True,
            )

    async def _stage3_runtime_image_prune_loop() -> None:
        interval_days = max(float(settings.stage3_runtime_image_prune_interval_days or 1.0), 0.000001)
        interval_seconds = interval_days * 24.0 * 60.0 * 60.0
        while True:
            await asyncio.to_thread(_prune_stage3_runtime_images_background_once)
            await asyncio.sleep(interval_seconds)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        stage3_runtime_prune_task: asyncio.Task | None = None
        try:
            if settings.server_observer_mode:
                print(
                    "[feature_factory] observer mode enabled; "
                    "startup recovery and background maintenance are disabled",
                    flush=True,
                )
                yield
                return

            recovery_session = session_factory()
            recovery_client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
            try:
                recovery_service = CrawlService(recovery_session, settings, recovery_client)
                recoverable_statuses = {
                    CrawlJobStatus.queued.value,
                    CrawlJobStatus.planning.value,
                    CrawlJobStatus.running.value,
                }
                dirty = False
                jobs = list(
                    recovery_session.scalars(
                        select(CrawlJob).where(CrawlJob.status.in_(recoverable_statuses))
                    )
                )
                for job in jobs:
                    if recovery_service.reconcile_job(job, is_running=False, is_scheduled=False):
                        dirty = True
                if dirty:
                    recovery_session.commit()
            finally:
                recovery_client.close()
                recovery_session.close()

            if repaired_duplicate_archives:
                _cleanup_repaired_duplicate_stage2_archives(repaired_duplicate_archives)
            _cleanup_stage2_cleanup_tombstones()
            _cleanup_orphan_stage2_agent_server_containers()
            _cleanup_orphan_stage3_agent_server_containers()
            _cleanup_orphan_stage3_eval_containers()
            _cleanup_orphan_stage4_agent_server_containers()
            stage2_session = session_factory()
            try:
                stage2_service = Stage2Service(stage2_session)
                recovered_stage2_archives = list(stage2_service.recover_interrupted_runs(return_archives=True))
                if recovered_stage2_archives:
                    stage2_session.commit()
            finally:
                stage2_session.close()
            for recovered_archive in recovered_stage2_archives:
                cleanup_result = _cleanup_stage2_run_archive_and_record_tombstone(recovered_archive)
                _record_recovered_stage2_cleanup_failure(recovered_archive, cleanup_result)
            _cleanup_stage3_cleanup_tombstones()
            stage3_session = session_factory()
            try:
                stage3_service = Stage3Service(
                    stage3_session,
                    workspace_root=settings.stage3_workspace_dir,
                    settings=settings,
                )
                recovered_stage3_archives = list(stage3_service.recover_interrupted_runs(return_archives=True))
                if recovered_stage3_archives:
                    stage3_session.commit()
            finally:
                stage3_session.close()
            for recovered_archive in recovered_stage3_archives:
                cleanup_result = _cleanup_stage3_run_archive_and_record_tombstone(recovered_archive)
                if _cleanup_result_has_failures(cleanup_result):
                    print(
                        "[feature_factory] failed to fully cleanup recovered stage3 run "
                        f"{recovered_archive.get('run_id')}: {cleanup_result}",
                        flush=True,
                    )
            _cleanup_stage4_cleanup_tombstones()
            stage4_session = session_factory()
            try:
                stage4_service = Stage4Service(stage4_session, workspace_root=settings.stage4_workspace_dir, settings=settings)
                recovered_stage4_archives = list(stage4_service.recover_interrupted_runs(return_archives=True))
                if recovered_stage4_archives:
                    stage4_session.commit()
            finally:
                stage4_session.close()
            for recovered_archive in recovered_stage4_archives:
                cleanup_result = _cleanup_stage4_run_archive_and_record_tombstone(recovered_archive)
                if _cleanup_result_has_failures(cleanup_result):
                    print(
                        "[feature_factory] failed to fully cleanup recovered stage4 run "
                        f"{recovered_archive.get('run_id')}: {cleanup_result}",
                        flush=True,
                    )
            batch_recovery_session = session_factory()
            try:
                cancelled_batch_task_ids = list(
                    batch_recovery_session.scalars(
                        select(BatchTask.id).where(
                            BatchTask.cancel_requested.is_(True)
                            | (BatchTask.status == BatchTaskStatus.cancelled.value)
                            | (BatchTask.phase == "cancelling")
                        )
                    )
                )
                active_batch_task_ids = list(
                    batch_recovery_session.scalars(
                        select(BatchTask.id).where(
                            BatchTask.status.in_(
                                [
                                    BatchTaskStatus.pending.value,
                                    BatchTaskStatus.running.value,
                                ]
                            )
                        )
                    )
                )
            finally:
                batch_recovery_session.close()
            cancelled_batch_task_id_set = set(cancelled_batch_task_ids)
            for cancelled_task_id in cancelled_batch_task_ids:
                try:
                    _cleanup_and_finalize_cancelled_batch_task(cancelled_task_id)
                except Exception as exc:
                    print(
                        "[feature_factory] failed to cleanup and finalize cancelled batch task "
                        f"{cancelled_task_id}: {exc}",
                        flush=True,
                    )
            for active_task_id in active_batch_task_ids:
                if active_task_id in cancelled_batch_task_id_set:
                    continue
                try:
                    env_batch_runner.schedule_task(active_task_id)
                except Exception as exc:
                    print(
                        "[feature_factory] failed to recover batch task "
                        f"{active_task_id}: {exc}",
                        flush=True,
                    )
            if settings.stage3_runtime_image_auto_prune:
                stage3_runtime_prune_task = asyncio.create_task(_stage3_runtime_image_prune_loop())
            yield
        finally:
            if stage3_runtime_prune_task is not None:
                stage3_runtime_prune_task.cancel()
                try:
                    await stage3_runtime_prune_task
                except asyncio.CancelledError:
                    pass
            _shutdown_runner_with_timeout(
                job_runner,
                timeout_seconds=APP_SHUTDOWN_RUNNER_WAIT_SECONDS,
            )
            _shutdown_runner_with_timeout(
                env_runner,
                timeout_seconds=APP_SHUTDOWN_RUNNER_WAIT_SECONDS,
            )
            _shutdown_runner_with_timeout(
                env_stage3_runner,
                timeout_seconds=APP_SHUTDOWN_RUNNER_WAIT_SECONDS,
            )
            _shutdown_runner_with_timeout(
                env_stage4_runner,
                timeout_seconds=APP_SHUTDOWN_RUNNER_WAIT_SECONDS,
            )
            _shutdown_runner_with_timeout(
                env_batch_runner,
                timeout_seconds=APP_SHUTDOWN_RUNNER_WAIT_SECONDS,
            )
            if postgres_manager_entered:
                postgres_manager.__exit__(None, None, None)

    app = FastAPI(title="FeatureFactory 管理后台", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.observer_mode = settings.server_observer_mode
    app.state.session_factory = session_factory
    app.state.job_runner = job_runner
    app.state.stage2_runner = env_runner
    app.state.stage3_runner = env_stage3_runner
    app.state.stage4_runner = env_stage4_runner
    app.state.batch_runner = env_batch_runner
    app.state.stage2_app_instance_id = stage2_app_instance_id

    @app.middleware("http")
    async def block_observer_mode_mutations(request: Request, call_next):
        if (
            settings.server_observer_mode
            and request.url.path.startswith("/api/")
            and request.method.upper() not in {"GET", "HEAD", "OPTIONS"}
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "detail": (
                        "FeatureFactory server is running in observer mode; "
                        "mutating API requests are disabled"
                    )
                },
            )
        return await call_next(request)
    app.state.token_scheduler = token_scheduler
    app.state.managed_postgres = managed_postgres_spec
    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def load_stage1_job_detail_payload(
        job_id: str,
        *,
        is_running_override: bool | None = None,
        is_scheduled_override: bool | None = None,
        pause_requested_override: bool | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            try:
                job = service.get_job(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            is_running = is_running_override if is_running_override is not None else job_runner.is_running(job.id)
            is_scheduled = (
                is_scheduled_override if is_scheduled_override is not None else job_runner.is_scheduled(job.id)
            )
            service.reconcile_job(job, is_running=is_running, is_scheduled=is_scheduled)
            partitions = service.list_partitions(job_id)
            partition_queries = service.list_partition_query_audits(job_id)
            repositories = service.list_job_repositories(job_id, limit=30)
            return {
                "job": _serialize_job(
                    job,
                    is_running=is_running,
                    pause_requested=(
                        pause_requested_override
                        if pause_requested_override is not None
                        else job_runner.is_pause_requested(job.id)
                    ),
                ),
                "partitions": [
                    {
                        **_serialize_partition(partition),
                        "queries": partition_queries.get(partition.id, []),
                    }
                    for partition in partitions
                ],
                "repositories": [_serialize_repository_hit(repository) for repository in repositories],
            }
        finally:
            client.close()
            session.close()

    def load_stage2_repository_summary_payload(repository_id: int) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                return service.repository_summary_payload(
                    repository_id,
                    running_repository_ids=env_runner.list_running_repository_ids(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    def load_stage2_repository_detail_payload(
        repository_id: int,
        *,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                return service.repository_detail_payload(
                    repository_id,
                    selected_run_id=selected_run_id,
                    running_repository_ids=env_runner.list_running_repository_ids(),
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    def _cleanup_stage2_worker_checkpoint_asset_targets(cleanup: dict[str, Any]) -> dict[str, list[str]]:
        checkpoint_paths = list(cleanup.get("checkpoint_paths") or [])
        checkpoint_image_refs = list(cleanup.get("checkpoint_docker_image_refs") or [])
        failed_paths = list(
            _remove_paths(
                checkpoint_paths,
                root_dir=Path(settings.stage2_workspace_dir).expanduser().resolve(),
            )
            or []
        )
        failed_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
        return {
            "failed_paths": failed_paths,
            "failed_checkpoint_docker_image_refs": failed_image_refs,
        }

    def _cleanup_stage2_run_worker_checkpoints(run_id: str) -> None:
        session = session_factory()
        try:
            service = Stage2Service(session)
            cleanup = service.worker_checkpoint_cleanup_targets(run_id)
            session.commit()
        except Exception:
            session.rollback()
            return
        finally:
            session.close()

        checkpoint_paths = list(cleanup.get("checkpoint_paths") or [])
        checkpoint_image_refs = list(cleanup.get("checkpoint_docker_image_refs") or [])
        if not checkpoint_paths and not checkpoint_image_refs:
            return

        cleanup_result = _cleanup_stage2_worker_checkpoint_asset_targets(cleanup)
        failed_paths = list(cleanup_result.get("failed_paths") or [])
        failed_image_refs = list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])

        session = session_factory()
        try:
            service = Stage2Service(session)
            if failed_paths or failed_image_refs:
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Worker checkpoint cleanup incomplete",
                    message="Some worker checkpoint assets could not be removed after scheduling failed",
                    payload={
                        "checkpoint_paths": checkpoint_paths,
                        "checkpoint_docker_image_refs": checkpoint_image_refs,
                        "failed_paths": failed_paths,
                        "failed_checkpoint_docker_image_refs": failed_image_refs,
                    },
                )
            else:
                service.clear_worker_checkpoints(run_id)
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Worker checkpoint cleaned",
                    message="Released worker checkpoint files and docker images after scheduling failed",
                    payload={
                        "checkpoint_paths": checkpoint_paths,
                        "checkpoint_docker_image_refs": checkpoint_image_refs,
                    },
                )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _cleanup_stage3_run_schedule_assets(run_id: str) -> None:
        archive: dict[str, Any] | None = None
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.get_run(run_id, include_detail=True)
                archive = service.prepared_run_cleanup_archive(
                    repository_id=run.entry_file.snapshot.repository_id,
                    run=run,
                )
                session.commit()
            except ValueError:
                session.rollback()
                archive = None
            except Exception:
                session.rollback()
                archive = None
        finally:
            session.close()

        if not archive:
            return

        cleanup_result = _cleanup_stage3_run_archive_and_tombstone_on_failure(
            archive,
            reason="schedule_failed",
        )
        failed_paths = list(cleanup_result.get("failed_paths") or [])
        failed_image_refs = list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])

        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            if failed_paths or failed_image_refs:
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Stage3 run asset cleanup incomplete",
                    message="Some Stage3 workspace or checkpoint assets could not be removed after scheduling failed",
                    payload={
                        "workspace_path": archive.get("workspace_path"),
                        "runtime_path": archive.get("runtime_path"),
                        "checkpoint_docker_image_refs": archive.get("checkpoint_docker_image_refs"),
                        "failed_paths": failed_paths,
                        "failed_checkpoint_docker_image_refs": failed_image_refs,
                    },
                )
            else:
                service.clear_breaker_checkpoints(run_id)
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Stage3 run assets cleaned",
                    message="Released Stage3 workspace and checkpoint assets after scheduling failed",
                    payload={
                        "workspace_path": archive.get("workspace_path"),
                        "runtime_path": archive.get("runtime_path"),
                        "checkpoint_docker_image_refs": archive.get("checkpoint_docker_image_refs"),
                    },
                )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _cleanup_stage4_run_schedule_assets(run_id: str) -> None:
        archive: dict[str, Any] | None = None
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                run = service.get_run(run_id, include_detail=True)
                archive = service.prepared_run_cleanup_archive(run)
                session.commit()
            except ValueError:
                session.rollback()
                archive = None
            except Exception:
                session.rollback()
                archive = None
        finally:
            session.close()

        if not archive:
            return

        cleanup_result = _cleanup_stage4_run_archive_and_tombstone_on_failure(
            archive,
            reason="schedule_failed",
        )
        failed_paths = list(cleanup_result.get("failed_paths") or [])

        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            if failed_paths:
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Stage4 run asset cleanup incomplete",
                    message="Some Stage4 workspace assets could not be removed after scheduling failed",
                    payload={
                        "workspace_path": archive.get("workspace_path"),
                        "runtime_path": archive.get("runtime_path"),
                        "failed_paths": failed_paths,
                    },
                )
            else:
                service.record_event(
                    run_id,
                    actor="system",
                    phase="cleanup",
                    title="Stage4 run assets cleaned",
                    message="Released Stage4 workspace assets after scheduling failed",
                    payload={
                        "workspace_path": archive.get("workspace_path"),
                        "runtime_path": archive.get("runtime_path"),
                    },
                )
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def _schedule_stage2_run_or_raise(run_id: str, *, detail: str) -> None:
        schedule_error: Exception | None = None
        try:
            scheduled = env_runner.schedule_run(run_id)
        except Exception as exc:  # noqa: BLE001
            scheduled = False
            schedule_error = exc
        if scheduled:
            return

        error_message = (
            f"{detail}: {schedule_error}"
            if schedule_error is not None and str(schedule_error or "").strip()
            else detail
        )
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                service.mark_run_schedule_failed(
                    run_id,
                    error_message=error_message,
                )
                session.commit()
            except ValueError:
                session.rollback()
        finally:
            session.close()

        _cleanup_stage2_run_worker_checkpoints(run_id)

        if schedule_error is not None:
            raise HTTPException(status_code=503, detail=detail) from schedule_error
        raise HTTPException(status_code=503, detail=detail)

    def load_stage2_run_detail_payload(repository_id: int, run_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                return service.run_detail_payload(repository_id, run_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    def load_stage3_repository_detail_payload(
        repository_id: int,
        *,
        selected_snapshot_id: str | None = None,
        selected_entry_file_id: str | None = None,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                payload = service.repository_detail_payload(
                    repository_id,
                    selected_snapshot_id=selected_snapshot_id,
                    selected_entry_file_id=selected_entry_file_id,
                    selected_run_id=selected_run_id,
                )
                session.commit()
                return payload
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    def load_stage3_run_detail_payload(repository_id: int, run_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                return service.run_detail_payload(repository_id, run_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    def _serialize_stage3_image_status(payload: dict[str, Any]) -> dict[str, Any]:
        status = dict(payload or {})
        status["created_at"] = _serialize_datetime(status.get("created_at"))
        return status

    def _summarize_stage3_repository_image_prewarm(payload: dict[str, Any]) -> dict[str, Any]:
        runtime_status = dict(payload.get("runtime_image") or {})
        agent_status = dict(payload.get("agent_server_image") or {})
        target_statuses = [runtime_status, agent_status]

        def _is_running(status: dict[str, Any]) -> bool:
            return bool(status.get("in_progress")) or str(status.get("last_job_status") or "") in {
                "queued",
                "running",
            }

        def _is_ready(status: dict[str, Any]) -> bool:
            return bool(status.get("present")) and not bool(status.get("needs_update"))

        any_running = any(_is_running(status) for status in target_statuses)
        all_ready = all(_is_ready(status) for status in target_statuses)
        any_cancelled = any(str(status.get("last_job_status") or "") == "cancelled" for status in target_statuses)
        any_failed = any(str(status.get("last_job_status") or "") == "failed" for status in target_statuses)
        any_needs_update = any(bool(status.get("needs_update")) for status in target_statuses)

        if any_running:
            status = "running"
            label = "预热中"
        elif all_ready:
            status = "succeeded"
            label = "已预热"
        elif any_cancelled:
            status = "cancelled"
            label = "已取消"
        elif any_failed:
            status = "failed"
            label = "失败"
        elif any_needs_update:
            status = "queued"
            label = "需要更新"
        else:
            status = "pending"
            label = "未预热"

        return {
            "snapshot_id": str(payload.get("snapshot_id") or ""),
            "status": status,
            "label": label,
            "can_cancel": any_running,
            "runtime": {
                "status": "running" if _is_running(runtime_status) else (
                    "succeeded" if _is_ready(runtime_status) else (
                        "cancelled" if str(runtime_status.get("last_job_status") or "") == "cancelled" else (
                            "failed" if str(runtime_status.get("last_job_status") or "") == "failed" else (
                                "queued" if bool(runtime_status.get("needs_update")) else "pending"
                            )
                        )
                    )
                ),
                "label": (
                    "预热中" if _is_running(runtime_status) else (
                        "已预热" if _is_ready(runtime_status) else (
                            "已取消" if str(runtime_status.get("last_job_status") or "") == "cancelled" else (
                                "失败" if str(runtime_status.get("last_job_status") or "") == "failed" else (
                                    "需要更新" if bool(runtime_status.get("needs_update")) else "未预热"
                                )
                            )
                        )
                    )
                ),
            },
            "agent_server": {
                "status": "running" if _is_running(agent_status) else (
                    "succeeded" if _is_ready(agent_status) else (
                        "cancelled" if str(agent_status.get("last_job_status") or "") == "cancelled" else (
                            "failed" if str(agent_status.get("last_job_status") or "") == "failed" else (
                                "queued" if bool(agent_status.get("needs_update")) else "pending"
                            )
                        )
                    )
                ),
                "label": (
                    "预热中" if _is_running(agent_status) else (
                        "已预热" if _is_ready(agent_status) else (
                            "已取消" if str(agent_status.get("last_job_status") or "") == "cancelled" else (
                                "失败" if str(agent_status.get("last_job_status") or "") == "failed" else (
                                    "需要更新" if bool(agent_status.get("needs_update")) else "未预热"
                                )
                            )
                        )
                    )
                ),
            },
        }

    def load_stage3_repository_image_prewarm_payload(
        repository_id: int,
        *,
        selected_snapshot_id: str | None = None,
        include_logs: bool = True,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                snapshot = service.get_repository_snapshot(repository_id, snapshot_id=selected_snapshot_id)
                session.commit()
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

        platform_name = detect_stage3_platform()
        runtime_status = _serialize_stage3_image_status(
            stage3_breaker_runtime_image_status(
                snapshot_id=snapshot.id,
                source_stage2_run_id=snapshot.source_stage2_run_id,
                source_commit_sha=snapshot.source_commit_sha,
                base_image_id=snapshot.base_image,
                platform_name=platform_name,
                dockerfile_text=snapshot.dockerfile_text,
            )
        )
        runtime_dependency_ready = bool(runtime_status.get("present")) and not bool(runtime_status.get("needs_update"))
        agent_plan = agent_server_build_plan(
            base_image=str(runtime_status.get("image_ref") or ""),
            platform_name=platform_name,
        )
        agent_inspect = inspect_docker_image(str(agent_plan.get("image_tag") or ""))
        agent_physical_present = bool(agent_inspect.get("present"))
        agent_fingerprint_matches = bool(
            agent_physical_present
            and labels_match(
                labels=dict(agent_inspect.get("labels") or {}),
                expected_labels=dict(agent_plan.get("labels") or {}),
            )
        )
        agent_dependency_needs_update = not runtime_dependency_ready
        agent_status = {
            "image_ref": str(agent_plan.get("image_tag") or ""),
            "present": bool(
                agent_physical_present and agent_fingerprint_matches and not agent_dependency_needs_update
            ),
            "physical_present": agent_physical_present,
            "fingerprint_matches": agent_fingerprint_matches,
            "needs_update": bool(
                agent_physical_present
                and (not agent_fingerprint_matches or agent_dependency_needs_update)
            ),
            "dependency_needs_update": agent_dependency_needs_update,
            "created_at": _serialize_datetime(agent_inspect.get("created_at")),
            "image_id": str(agent_inspect.get("image_id") or ""),
            "architecture": str(agent_inspect.get("architecture") or ""),
            "os": str(agent_inspect.get("os") or ""),
            "labels": dict(agent_inspect.get("labels") or {}),
            "expected_labels": dict(agent_plan.get("labels") or {}),
            "inspect_error": str(agent_inspect.get("error") or ""),
            "mirror_profile": str(agent_plan.get("mirror_profile") or ""),
        }
        payload = {
            "repository_id": repository_id,
            "snapshot_id": snapshot.id,
            "snapshot": {
                "id": snapshot.id,
                "source_stage2_run_id": snapshot.source_stage2_run_id,
                "source_commit_sha": snapshot.source_commit_sha,
                "base_image": snapshot.base_image,
                "entry_file_count": len(snapshot.entry_files or []),
            },
            "platform": platform_name,
            "runtime_image": runtime_status,
            "agent_server_image": agent_status,
        }
        return stage3_image_prewarm_jobs.apply(payload, include_logs=include_logs)

    def build_stage3_snapshot_image_assets(
        repository_id: int,
        *,
        selected_snapshot_id: str | None = None,
        force: bool = False,
        log_callback: Callable[[str], None] | None = None,
        hooks: _Stage3SnapshotImagePrewarmHooks | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        def raise_if_cancelled() -> None:
            if cancel_requested is not None and cancel_requested():
                raise InterruptedError("stage3 image prewarm cancelled by user")

        raise_if_cancelled()
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            snapshot = service.get_repository_snapshot(repository_id, snapshot_id=selected_snapshot_id)
            workspace_manifest = service.prepare_snapshot_workspace(repository_id, snapshot_id=snapshot.id)
            session.commit()
        finally:
            session.close()

        resolved_snapshot_id = str(snapshot.id)
        platform_name = detect_stage3_platform()
        runtime_status = stage3_breaker_runtime_image_status(
            snapshot_id=resolved_snapshot_id,
            source_stage2_run_id=snapshot.source_stage2_run_id,
            source_commit_sha=snapshot.source_commit_sha,
            base_image_id=snapshot.base_image,
            platform_name=platform_name,
            dockerfile_text=snapshot.dockerfile_text,
        )
        runtime_needs_refresh = bool(force or not runtime_status.get("present") or runtime_status.get("needs_update"))
        if callable(log_callback):
            log_callback(f"Snapshot workspace prepared: {workspace_manifest['workspace_path']}")
        raise_if_cancelled()

        if hooks is not None:
            hooks.target_started("runtime")
        runtime_image_ref = ensure_stage3_breaker_runtime_image_built(
            workspace_dir=Path(str(workspace_manifest["workspace_path"])),
            snapshot_id=resolved_snapshot_id,
            source_stage2_run_id=snapshot.source_stage2_run_id,
            source_commit_sha=snapshot.source_commit_sha,
            base_image_id=snapshot.base_image,
            platform_name=platform_name,
            timeout_seconds=float(settings.stage3_build_timeout_seconds),
            log_callback=log_callback,
            cancel_requested=cancel_requested,
        )
        if hooks is not None:
            hooks.target_finished("runtime")

        if callable(log_callback):
            log_callback(f"Stage3 runtime image ready: {runtime_image_ref}")
        raise_if_cancelled()
        if hooks is not None:
            hooks.target_started("agent")
        agent_image_ref = ensure_agent_server_image_built(
            base_image=runtime_image_ref,
            platform_name=platform_name,
            force=bool(force or runtime_needs_refresh),
            log_callback=log_callback,
            timeout_seconds=float(settings.stage3_build_timeout_seconds),
            cancel_requested=cancel_requested,
        )
        if hooks is not None:
            hooks.target_finished("agent")
        if callable(log_callback):
            log_callback(f"OpenHands 包装镜像已就绪: {agent_image_ref}")
        return {
            "snapshot_id": resolved_snapshot_id,
            "runtime_image_ref": runtime_image_ref,
            "agent_server_image_ref": agent_image_ref,
        }

    def _schedule_stage3_run_or_raise(run_id: str, *, detail: str) -> None:
        schedule_error: Exception | None = None
        try:
            scheduled = env_stage3_runner.schedule_run(run_id)
        except Exception as exc:  # noqa: BLE001
            scheduled = False
            schedule_error = exc
        if scheduled:
            return

        error_message = (
            f"{detail}: {schedule_error}"
            if schedule_error is not None and str(schedule_error or "").strip()
            else detail
        )
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                service.mark_run_failed(
                    run_id,
                    error_message=error_message,
                    summary_updates={"schedule_failed": True, "runner_status": "failed"},
                )
                service.record_event(
                    run_id,
                    actor="system",
                    phase="failed",
                    title="Stage3 run scheduling failed",
                    message=error_message,
                    payload={"run_id": run_id},
                )
                session.commit()
            except ValueError:
                session.rollback()
        finally:
            session.close()

        _cleanup_stage3_run_schedule_assets(run_id)

        if schedule_error is not None:
            raise HTTPException(status_code=503, detail=detail) from schedule_error
        raise HTTPException(status_code=503, detail=detail)

    def _stage3_backend_readiness() -> dict[str, Any]:
        backend_readiness = getattr(env_stage3_runner, "backend_readiness", None)
        if callable(backend_readiness):
            try:
                return dict(backend_readiness())
            except Exception as exc:  # noqa: BLE001
                return {
                    "ready": False,
                    "message": str(exc) or type(exc).__name__,
                }
        is_ready = getattr(env_stage3_runner, "is_backend_ready", None)
        ready = bool(is_ready()) if callable(is_ready) else False
        return {
            "ready": ready,
            "message": "OpenHands backend is ready" if ready else "Stage3 breaker backend is not ready",
        }

    def _require_stage3_breaker_agent_ready() -> None:
        readiness = _stage3_backend_readiness()
        if readiness.get("ready"):
            return
        message = str(readiness.get("message") or "Stage3 breaker backend is not ready").strip()
        raise HTTPException(
            status_code=503,
            detail=f"Stage3 breaker agent is not ready: {message}",
        )

    def _schedule_stage4_run_or_raise(run_id: str, *, detail: str) -> None:
        schedule_error: Exception | None = None
        try:
            scheduled = env_stage4_runner.schedule_run(run_id)
        except Exception as exc:  # noqa: BLE001
            scheduled = False
            schedule_error = exc
        if scheduled:
            return

        error_message = (
            f"{detail}: {schedule_error}"
            if schedule_error is not None and str(schedule_error or "").strip()
            else detail
        )
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                service.mark_run_failed(
                    run_id,
                    error_message=error_message,
                    summary_updates={"schedule_failed": True, "runner_status": "failed"},
                )
                session.commit()
            except ValueError:
                session.rollback()
        finally:
            session.close()

        _cleanup_stage4_run_schedule_assets(run_id)

        if schedule_error is not None:
            raise HTTPException(status_code=503, detail=detail) from schedule_error
        raise HTTPException(status_code=503, detail=detail)

    def _stage4_backend_readiness() -> dict[str, Any]:
        backend_readiness = getattr(env_stage4_runner, "backend_readiness", None)
        if callable(backend_readiness):
            try:
                return dict(backend_readiness())
            except Exception as exc:  # noqa: BLE001
                return {"ready": False, "message": str(exc) or type(exc).__name__}
        is_ready = getattr(env_stage4_runner, "is_backend_ready", None)
        ready = bool(is_ready()) if callable(is_ready) else False
        return {
            "ready": ready,
            "message": "Stage4 issuer backend is ready" if ready else "Stage4 issuer backend is not ready",
        }

    def _require_stage4_issuer_ready() -> None:
        readiness = _stage4_backend_readiness()
        if readiness.get("ready"):
            return
        message = str(readiness.get("message") or "Stage4 issuer backend is not ready").strip()
        raise HTTPException(
            status_code=503,
            detail=f"Stage4 issuer backend is not ready: {message}",
        )

    def is_stage1_job_payload_active(payload: dict[str, Any]) -> bool:
        job = payload["job"]
        return bool(job.get("is_running") or job.get("status") in STAGE1_ACTIVE_JOB_STATUSES)

    def is_stage2_repository_payload_active(payload: dict[str, Any]) -> bool:
        repository_stage2 = payload.get("repository", {}).get("stage2") or {}
        return repository_stage2.get("status") in {"queued", "running"}

    def is_stage3_repository_payload_active(payload: dict[str, Any]) -> bool:
        repository_stage3 = payload.get("repository", {}).get("stage3") or {}
        return repository_stage3.get("status") in {"queued", "running"}

    def is_stage4_run_payload_active(payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "")
        return bool(payload.get("is_active")) or status in {"queued", "running"}

    def is_stage4_source_payload_active(payload: dict[str, Any]) -> bool:
        group_status = str(dict(payload.get("group") or {}).get("status") or "")
        if group_status in {"queued", "running"}:
            return True
        for source in list(payload.get("sources") or []):
            savepoint = dict((source or {}).get("savepoint") or {})
            latest_run = dict(savepoint.get("latest_stage4_run") or {})
            status = str(latest_run.get("status") or "")
            if status in {"queued", "running"} or bool(latest_run.get("is_active")):
                return True
        return False

    async def stream_detail_events(
        request: Request,
        *,
        initial_payload: dict[str, Any],
        fetch_payload,
        is_active,
        signature_of,
    ):
        loop = asyncio.get_running_loop()
        current_payload = initial_payload
        current_signature = signature_of(initial_payload)
        if is_active(initial_payload):
            yield _serialize_sse_event("snapshot", initial_payload)
        else:
            yield _serialize_sse_event("terminal", initial_payload)
            return

        next_keepalive_at = loop.time() + SSE_KEEPALIVE_INTERVAL_SECONDS
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(SSE_POLL_INTERVAL_SECONDS)
            if await request.is_disconnected():
                return
            try:
                latest_payload = await asyncio.to_thread(fetch_payload)
            except HTTPException as exc:
                if exc.status_code == 404:
                    yield _serialize_sse_event("deleted", {"message": str(exc.detail)})
                else:
                    yield _serialize_sse_event("error", {"message": str(exc.detail)})
                return
            except Exception as exc:
                yield _serialize_sse_event("error", {"message": str(exc)})
                return

            latest_signature = signature_of(latest_payload)
            if latest_signature != current_signature:
                current_payload = latest_payload
                current_signature = latest_signature
                if is_active(latest_payload):
                    yield _serialize_sse_event("snapshot", latest_payload)
                else:
                    yield _serialize_sse_event("terminal", latest_payload)
                    return
                next_keepalive_at = loop.time() + SSE_KEEPALIVE_INTERVAL_SECONDS
                continue

            if loop.time() >= next_keepalive_at:
                yield ": keepalive\n\n"
                next_keepalive_at = loop.time() + SSE_KEEPALIVE_INTERVAL_SECONDS

    @app.get("/", response_class=HTMLResponse)
    def admin_home() -> str:
        admin_js_version = _static_asset_version(static_dir / "admin.js")
        return (
            INDEX_HTML.replace("__FEATURE_FACTORY_FRONTEND_SESSION_ID__", FRONTEND_SESSION_ID)
            .replace("__FEATURE_FACTORY_ADMIN_JS_VERSION__", admin_js_version)
        )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "time": datetime.now(UTC).isoformat(),
            "observer_mode": settings.server_observer_mode,
        }

    def load_batch_task_detail_payload(
        task_id: str,
        *,
        repo_page: int = 1,
        repo_page_size: int = 15,
        repo_query: str | None = None,
        repo_language: str | None = None,
        repo_stars_min: int | None = None,
        repo_stars_max: int | None = None,
        repo_status: str | None = None,
        repo_stage2_status: str | None = None,
        repo_stage3_status: str | None = None,
        repo_stage4_status: str | None = None,
        repo_has_errors: bool = False,
        repo_non_pending: bool = False,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = BatchTaskService(session)
            try:
                task = service.get_task(task_id, include_detail=False)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            payload = service.serialize_task_detail_page(
                task,
                repo_page=repo_page,
                repo_page_size=repo_page_size,
                repo_query=repo_query,
                repo_language=repo_language,
                repo_stars_min=repo_stars_min,
                repo_stars_max=repo_stars_max,
                repo_status=repo_status,
                repo_stage2_status=repo_stage2_status,
                repo_stage3_status=repo_stage3_status,
                repo_stage4_status=repo_stage4_status,
                repo_has_errors=repo_has_errors,
                repo_non_pending=repo_non_pending,
            )
            is_batch_running = env_batch_runner.is_running(task_id)
            payload["is_running"] = is_batch_running
            if task.stage1_crawl_job_id:
                stage1_running = (
                    is_batch_running
                    and task.status == BatchTaskStatus.running.value
                    and str(task.phase or "") == "stage1"
                )
                try:
                    payload["stage1_detail"] = load_stage1_job_detail_payload(
                        task.stage1_crawl_job_id,
                        is_running_override=stage1_running,
                        is_scheduled_override=stage1_running,
                        pause_requested_override=False,
                    )
                except HTTPException:
                    payload["stage1_detail"] = None
            return payload
        finally:
            session.close()

    def _batch_task_payload_active(payload: dict[str, Any]) -> bool:
        status = str(payload.get("status") or "")
        return bool(payload.get("is_running")) or status in {
            BatchTaskStatus.pending.value,
            BatchTaskStatus.running.value,
        }

    def _resolve_batch_data_pool_id(session, payload: CreateBatchTaskRequest) -> str:
        service = DataPoolService(session)
        data_pool_id = str(payload.data_pool_id or "").strip()
        if data_pool_id:
            pool = service.require_pool_available(data_pool_id)
            return str(pool.id)
        selection = payload.data_pool
        if selection is None:
            pool = service.ensure_default_pool(
                name=settings.default_data_pool_name,
                root_path=settings.default_data_pool_root,
            )
            return str(pool.id)
        selected_id = str(selection.id or "").strip()
        if selected_id:
            pool = service.require_pool_available(selected_id)
            return str(pool.id)
        if not selection.name or not selection.root_path:
            raise HTTPException(status_code=400, detail="new data pool requires name and root_path")
        pool = service.create_pool(
            name=selection.name,
            root_path=selection.root_path,
            description=selection.description,
        )
        return str(pool.id)

    def _runtime_template_snapshot(session, template_model, template_id: str | None) -> dict[str, Any] | None:
        normalized_id = str(template_id or "").strip()
        if not normalized_id:
            return None
        row = session.get(template_model, normalized_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"runtime template not found: {normalized_id}")
        return dict(row.snapshot_json or {})

    def _batch_github_tokens(session, payload: CreateBatchTaskRequest) -> list[str] | None:
        normalized_template_id = str(payload.github_token_template_id or "").strip()
        if not normalized_template_id:
            return payload.github_tokens
        row = session.get(Stage1CrawlTemplate, normalized_template_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"github token template not found: {normalized_template_id}")
        return _normalize_stage1_template_values(dict(row.snapshot_json or {}).get("github_tokens"))

    def _persist_current_stage_runtime_config(*, key: str, snapshot: dict[str, Any]) -> None:
        persist_session = session_factory()
        try:
            _persist_stage_runtime_config(persist_session, key=key, snapshot=snapshot)
            persist_session.commit()
        except Exception:
            persist_session.rollback()
            raise
        finally:
            persist_session.close()

    def _validate_batch_runtime_snapshot_capacity(snapshot: dict[str, Any]) -> None:
        stage_capacities = {
            "stage2": env_runner.get_max_workers(),
            "stage3": env_stage3_runner.get_max_workers(),
            "stage4": env_stage4_runner.get_max_workers(),
        }
        for stage_name, system_capacity in stage_capacities.items():
            max_concurrent_runs = int(
                ((snapshot.get(stage_name) or {}).get("concurrency") or {}).get("max_concurrent_runs") or 0
            )
            if max_concurrent_runs > system_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"{stage_name} max_concurrent_runs cannot exceed system capacity {system_capacity}",
                )

    def _batch_runtime_snapshot(session, payload: CreateBatchTaskRequest) -> dict[str, Any]:
        stage2_base = _runtime_template_snapshot(session, Stage2RuntimeTemplate, payload.stage2_template_id)
        snapshot = {
            "pipeline": {"stop_after_stage": payload.stop_after_stage},
            "stage1": {
                "max_concurrent_jobs": payload.max_concurrent_jobs or settings.stage1_max_concurrent_jobs,
            },
            "stage2": _stage2_runtime_snapshot_from_request(
                payload.stage2_runtime or UpdateStage2RuntimeRequest(),
                settings=settings,
                base_snapshot=stage2_base,
            ),
        }
        if payload.stop_after_stage == "stage4":
            stage3_base = _runtime_template_snapshot(session, Stage3RuntimeTemplate, payload.stage3_template_id)
            stage4_base = _runtime_template_snapshot(session, Stage4RuntimeTemplate, payload.stage4_template_id)
            snapshot["stage3"] = _stage3_runtime_snapshot_from_request(
                payload.stage3_runtime or UpdateStage3RuntimeRequest(),
                settings=settings,
                base_snapshot=stage3_base,
            )
            snapshot["stage4"] = _stage4_runtime_snapshot_from_request(
                payload.stage4_runtime or UpdateStage4RuntimeRequest(),
                settings=settings,
                base_snapshot=stage4_base,
            )
        _validate_batch_runtime_snapshot_capacity(snapshot)
        return snapshot

    def _batch_retry_runtime_snapshot(
        task: BatchTask,
        *,
        runtime_config_source: Literal["original", "current"],
    ) -> dict[str, Any]:
        snapshot = dict(task.runtime_snapshot_json or {})
        stage1_snapshot = dict(snapshot.get("stage1") or {})
        stage1_snapshot.setdefault("max_concurrent_jobs", settings.stage1_max_concurrent_jobs)
        snapshot["stage1"] = stage1_snapshot
        current_stage_snapshots = {
            "stage2": lambda: _stage2_runtime_snapshot_from_request(
                UpdateStage2RuntimeRequest(),
                settings=settings,
            ),
            "stage3": lambda: _stage3_runtime_snapshot_from_request(
                UpdateStage3RuntimeRequest(),
                settings=settings,
            ),
            "stage4": lambda: _stage4_runtime_snapshot_from_request(
                UpdateStage4RuntimeRequest(),
                settings=settings,
            ),
        }
        stop_after_stage = str(((snapshot.get("pipeline") or {}).get("stop_after_stage") or "stage4"))
        for stage_name, current_snapshot_factory in current_stage_snapshots.items():
            if stop_after_stage == "stage2" and stage_name in {"stage3", "stage4"}:
                snapshot.pop(stage_name, None)
                continue
            original_stage_snapshot = snapshot.get(stage_name)
            if (
                runtime_config_source == "current"
                or not isinstance(original_stage_snapshot, dict)
                or not original_stage_snapshot
            ):
                snapshot[stage_name] = current_snapshot_factory()
        _validate_batch_runtime_snapshot_capacity(snapshot)
        return snapshot

    def _effective_crawl_filters(filters: CrawlFilters) -> dict[str, Any]:
        payload = filters.model_dump(mode="json")
        if not filters.target_repositories:
            return payload
        payload.update(
            {
                "language": None,
                "languages": [],
                "created_after": "1970-01-01T00:00:00Z",
                "created_before": None,
                "pushed_after": None,
                "pushed_before": None,
                "stars_min": None,
                "stars_max": None,
                "exclude_forks": False,
                "exclude_archived": False,
                "licenses": [],
                "keywords": [],
                "target_repositories": filters.target_repositories,
            }
        )
        return payload

    @app.get("/api/batch/global-config")
    def get_batch_global_config() -> dict[str, Any]:
        session = session_factory()
        try:
            service = BatchTaskService(session)
            return service.serialize_global_config()
        finally:
            session.close()

    @app.patch("/api/batch/global-config")
    def update_batch_global_config(payload: UpdateBatchGlobalConfigRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            service = BatchTaskService(session)
            row = service.update_global_config(github_tokens=payload.github_tokens)
            session.commit()
            return service.serialize_global_config(row)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/batch/tasks")
    def list_batch_tasks(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        session = session_factory()
        try:
            service = BatchTaskService(session)
            tasks = service.list_tasks(limit=limit)
            return {"tasks": [service.serialize_task_summary(task) for task in tasks]}
        finally:
            session.close()

    @app.post("/api/batch/tasks")
    def create_batch_task(payload: CreateBatchTaskRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            try:
                if (
                    payload.max_concurrent_jobs is not None
                    and payload.max_concurrent_jobs > settings.stage1_max_concurrent_jobs_cap
                ):
                    raise ValueError(
                        f"max_concurrent_jobs cannot exceed stage1 system capacity {settings.stage1_max_concurrent_jobs_cap}"
                    )
                if (
                    payload.max_concurrent_partitions is not None
                    and payload.max_concurrent_partitions > settings.stage1_max_concurrent_partitions_per_job_cap
                ):
                    raise ValueError(
                        "max_concurrent_partitions cannot exceed stage1 partition capacity "
                        f"{settings.stage1_max_concurrent_partitions_per_job_cap}"
                    )
                data_pool_id = _resolve_batch_data_pool_id(session, payload)
                runtime_snapshot = _batch_runtime_snapshot(session, payload)
                github_tokens = _batch_github_tokens(session, payload)
                task = BatchTaskService(session).create_task(
                    name=payload.name,
                    note=payload.note,
                    data_pool_id=data_pool_id,
                    filters=_effective_crawl_filters(payload.filters),
                    token_source="temporary",
                    runtime_snapshot=runtime_snapshot,
                    max_concurrent_jobs=payload.max_concurrent_jobs or settings.stage1_max_concurrent_jobs,
                    max_concurrent_partitions=payload.max_concurrent_partitions,
                    temporary_github_tokens=github_tokens,
                )
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            session.commit()
            task_id = task.id
        finally:
            session.close()
        if not env_batch_runner.schedule_task(task_id):
            fail_session = session_factory()
            try:
                BatchTaskService(fail_session).mark_terminal(
                    task_id,
                    status=BatchTaskStatus.failed.value,
                    phase="schedule_failed",
                    error_message="failed to schedule batch task",
                )
                fail_session.commit()
            finally:
                fail_session.close()
            raise HTTPException(status_code=503, detail="failed to schedule batch task")
        return load_batch_task_detail_payload(task_id)

    @app.get("/api/batch/tasks/{task_id}")
    def get_batch_task(
        task_id: str,
        repo_page: int = Query(default=1, ge=1),
        repo_page_size: int = Query(default=15, ge=1, le=100),
        repo_query: str | None = None,
        repo_language: str | None = None,
        repo_stars_min: int | None = Query(default=None, ge=0),
        repo_stars_max: int | None = Query(default=None, ge=0),
        repo_status: str | None = None,
        repo_stage2_status: str | None = None,
        repo_stage3_status: str | None = None,
        repo_stage4_status: str | None = None,
        repo_has_errors: bool = False,
        repo_non_pending: bool = False,
    ) -> dict[str, Any]:
        return load_batch_task_detail_payload(
            task_id,
            repo_page=repo_page,
            repo_page_size=repo_page_size,
            repo_query=repo_query,
            repo_language=repo_language,
            repo_stars_min=repo_stars_min,
            repo_stars_max=repo_stars_max,
            repo_status=repo_status,
            repo_stage2_status=repo_stage2_status,
            repo_stage3_status=repo_stage3_status,
            repo_stage4_status=repo_stage4_status,
            repo_has_errors=repo_has_errors,
            repo_non_pending=repo_non_pending,
        )

    @app.get("/api/batch/tasks/{task_id}/events")
    async def stream_batch_task_events(
        task_id: str,
        request: Request,
        repo_page: int = Query(default=1, ge=1),
        repo_page_size: int = Query(default=15, ge=1, le=100),
        repo_query: str | None = None,
        repo_language: str | None = None,
        repo_stars_min: int | None = Query(default=None, ge=0),
        repo_stars_max: int | None = Query(default=None, ge=0),
        repo_status: str | None = None,
        repo_stage2_status: str | None = None,
        repo_stage3_status: str | None = None,
        repo_stage4_status: str | None = None,
        repo_has_errors: bool = False,
        repo_non_pending: bool = False,
    ) -> StreamingResponse:
        initial_payload = load_batch_task_detail_payload(
            task_id,
            repo_page=repo_page,
            repo_page_size=repo_page_size,
            repo_query=repo_query,
            repo_language=repo_language,
            repo_stars_min=repo_stars_min,
            repo_stars_max=repo_stars_max,
            repo_status=repo_status,
            repo_stage2_status=repo_stage2_status,
            repo_stage3_status=repo_stage3_status,
            repo_stage4_status=repo_stage4_status,
            repo_has_errors=repo_has_errors,
            repo_non_pending=repo_non_pending,
        )
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: load_batch_task_detail_payload(
                    task_id,
                    repo_page=repo_page,
                    repo_page_size=repo_page_size,
                    repo_query=repo_query,
                    repo_language=repo_language,
                    repo_stars_min=repo_stars_min,
                    repo_stars_max=repo_stars_max,
                    repo_status=repo_status,
                    repo_stage2_status=repo_stage2_status,
                    repo_stage3_status=repo_stage3_status,
                    repo_stage4_status=repo_stage4_status,
                    repo_has_errors=repo_has_errors,
                    repo_non_pending=repo_non_pending,
                ),
                is_active=_batch_task_payload_active,
                signature_of=_json_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    @app.post("/api/batch/tasks/{task_id}/cancel")
    def cancel_batch_task(task_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            try:
                task = BatchTaskService(session).request_cancel(task_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.commit()
            return load_batch_task_detail_payload(task.id)
        finally:
            session.close()

    @app.post("/api/batch/tasks/{task_id}/retry")
    def retry_batch_task(
        task_id: str,
        payload: RetryBatchTaskRequest | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = BatchTaskService(session)
            try:
                existing_task = service.get_task(task_id, include_detail=False)
                if str(existing_task.status or "") in {
                    BatchTaskStatus.pending.value,
                    BatchTaskStatus.running.value,
                }:
                    raise BatchTaskRetryConflictError(f"cannot retry an active batch task: {task_id}")
                task = service.retry_task(
                    task_id,
                    runtime_snapshot=_batch_retry_runtime_snapshot(
                        existing_task,
                        runtime_config_source=(
                            payload.runtime_config_source
                            if payload is not None
                            else "original"
                        ),
                    ),
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except BatchTaskRetryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
            retried_task_id = task.id
        finally:
            session.close()
        if not env_batch_runner.schedule_task(retried_task_id):
            fail_session = session_factory()
            try:
                BatchTaskService(fail_session).mark_terminal(
                    retried_task_id,
                    status=BatchTaskStatus.failed.value,
                    phase="schedule_failed",
                    error_message="failed to schedule batch task retry",
                )
                fail_session.commit()
            finally:
                fail_session.close()
            raise HTTPException(status_code=503, detail="failed to schedule batch task retry")
        return load_batch_task_detail_payload(retried_task_id)

    @app.post(
        "/api/batch/tasks/{task_id}/repositories/{task_repository_id}/retry"
    )
    def retry_failed_batch_task_repository(
        task_id: str,
        task_repository_id: str,
    ) -> dict[str, Any]:
        runner_was_active = env_batch_runner.is_running(task_id)
        session = session_factory()
        try:
            try:
                task, _ = BatchTaskService(session).retry_failed_repository(
                    task_id,
                    task_repository_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except BatchTaskRetryConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
            retried_task_id = task.id
        finally:
            session.close()

        if not runner_was_active and not env_batch_runner.schedule_task(
            retried_task_id
        ):
            fail_session = session_factory()
            try:
                BatchTaskService(fail_session).mark_terminal(
                    retried_task_id,
                    status=BatchTaskStatus.failed.value,
                    phase="schedule_failed",
                    error_message="failed to schedule failed repository retry",
                )
                fail_session.commit()
            finally:
                fail_session.close()
            raise HTTPException(
                status_code=503,
                detail="failed to schedule failed repository retry",
            )
        return load_batch_task_detail_payload(retried_task_id)

    @app.delete("/api/batch/tasks/{task_id}")
    def delete_batch_task(task_id: str) -> dict[str, Any]:
        if env_batch_runner.is_running(task_id):
            raise HTTPException(status_code=409, detail="cannot delete a running batch task")

        stage2_archives: list[dict[str, Any]] = []
        stage3_archives: list[dict[str, Any]] = []
        stage4_archives: list[dict[str, Any]] = []
        stage3_snapshot_archives: list[dict[str, Any]] = []
        deleted_stage1_crawl_job_id: str | None = None
        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            batch_service = BatchTaskService(session)
            stage2_service = Stage2Service(session)
            stage3_service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            stage4_service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            crawl_service = CrawlService(session, settings, client)
            try:
                cleanup_plan = batch_service.delete_task_cleanup_plan(task_id)
                for item in list(cleanup_plan.get("stage4_runs") or []):
                    run_id = str(item.get("run_id") or "").strip()
                    if run_id:
                        stage4_archives.append(stage4_service.delete_run(run_id))
                if stage4_archives:
                    session.expire_all()
                for item in list(cleanup_plan.get("stage3_runs") or []):
                    repository_id = int(item.get("repository_id") or 0)
                    run_id = str(item.get("run_id") or "").strip()
                    if repository_id and run_id:
                        stage3_archives.append(stage3_service.delete_repository_run(repository_id, run_id))
                for item in list(cleanup_plan.get("stage3_snapshots") or []):
                    snapshot_id = str(item.get("snapshot_id") or "").strip()
                    if not snapshot_id:
                        continue
                    archive = stage3_service.delete_snapshot_if_orphaned(snapshot_id)
                    if archive is not None:
                        stage3_snapshot_archives.append(archive)
                for item in list(cleanup_plan.get("stage2_runs") or []):
                    repository_id = int(item.get("repository_id") or 0)
                    run_id = str(item.get("run_id") or "").strip()
                    if repository_id and run_id:
                        stage2_archives.append(stage2_service.delete_repository_run(repository_id, run_id))

                batch_service.delete_task_record(task_id)

                stage1_crawl_job_id = str(cleanup_plan.get("stage1_crawl_job_id") or "").strip()
                if stage1_crawl_job_id and session.get(CrawlJob, stage1_crawl_job_id) is not None:
                    crawl_service.delete_job(stage1_crawl_job_id, delete_orphan_repositories=False)
                    deleted_stage1_crawl_job_id = stage1_crawl_job_id
            except BatchTaskDeleteConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Stage2RunDeleteConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Stage4RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.commit()
        finally:
            client.close()
            session.close()

        cleanup_warnings = {
            "message": "batch task record was deleted, but some runtime assets could not be removed",
            "failed_paths": [],
            "failed_repo_checkout_paths": [],
            "failed_checkpoint_docker_image_refs": [],
            "failed_validator_image_refs": [],
        }
        for archive in stage4_archives:
            cleanup_result = _cleanup_stage4_run_archive_and_record_tombstone(archive)
            cleanup_warnings["failed_paths"].extend(list(cleanup_result.get("failed_paths") or []))
        for archive in stage3_archives:
            cleanup_result = _cleanup_stage3_run_archive_and_record_tombstone(archive)
            cleanup_warnings["failed_paths"].extend(list(cleanup_result.get("failed_paths") or []))
            cleanup_warnings["failed_checkpoint_docker_image_refs"].extend(
                list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])
            )
        for archive in stage3_snapshot_archives:
            cleanup_warnings["failed_paths"].extend(
                list(
                    _remove_paths(
                        [str(archive.get("workspace_path") or "")],
                        root_dir=settings.stage3_workspace_dir,
                    )
                    or []
                )
            )
        for archive in stage2_archives:
            cleanup_result = _cleanup_stage2_run_archive_and_record_tombstone(archive)
            cleanup_warnings["failed_paths"].extend(list(cleanup_result.get("failed_paths") or []))
            cleanup_warnings["failed_repo_checkout_paths"].extend(
                list(cleanup_result.get("failed_repo_checkout_paths") or [])
            )
            cleanup_warnings["failed_checkpoint_docker_image_refs"].extend(
                list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])
            )
            cleanup_warnings["failed_validator_image_refs"].extend(
                list(cleanup_result.get("failed_validator_image_refs") or [])
            )

        for key in (
            "failed_paths",
            "failed_repo_checkout_paths",
            "failed_checkpoint_docker_image_refs",
            "failed_validator_image_refs",
        ):
            seen: set[str] = set()
            normalized: list[str] = []
            for value in cleanup_warnings[key]:
                item = str(value or "").strip()
                if not item or item in seen:
                    continue
                seen.add(item)
                normalized.append(item)
            cleanup_warnings[key] = normalized

        if not _cleanup_result_has_failures(cleanup_warnings):
            cleanup_warnings = None

        return {
            "deleted_task_id": task_id,
            "deleted_stage1_crawl_job_id": deleted_stage1_crawl_job_id,
            "cleanup_warnings": cleanup_warnings,
        }

    @app.get("/api/data-pools")
    def list_data_pools() -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            pools = service.list_pools()
            default_pool = next(
                (pool for pool in pools if pool.name == settings.default_data_pool_name),
                None,
            )
            return {
                "default_pool_id": default_pool.id if default_pool is not None else None,
                "deleted_pool_ids": [],
                "pools": [service.serialize_pool(pool) for pool in pools],
            }
        finally:
            session.close()

    @app.post("/api/data-pools")
    def create_data_pool(payload: CreateDataPoolRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            try:
                pool = DataPoolService(session).create_pool(
                    name=payload.name,
                    root_path=payload.root_path,
                    description=payload.description,
                )
                session.commit()
                return {"pool": DataPoolService(session).serialize_pool(pool)}
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}")
    def get_data_pool(pool_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return {"pool": service.serialize_pool(service.get_pool(pool_id))}
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/preview")
    def preview_data_pool(pool_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return service.preview_pool(pool_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.patch("/api/data-pools/{pool_id}")
    def update_data_pool(pool_id: str, payload: UpdateDataPoolRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                pool = service.update_pool(
                    pool_id,
                    name=payload.name,
                    root_path=payload.root_path,
                    description=payload.description,
                )
                session.commit()
                return {"pool": service.serialize_pool(pool)}
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=400 if "required" in str(exc) else 404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.delete("/api/data-pools/{pool_id}")
    def delete_data_pool(pool_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                payload = service.delete_pool(pool_id)
                session.commit()
                return payload
            except DataPoolDeleteConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/assets")
    def list_data_pool_assets(
        pool_id: str,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=25, ge=1, le=200),
        sort_by: str = "created_at",
        sort_order: str = "desc",
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = Query(default=None, ge=0),
        depth_min: int | None = Query(default=None, ge=0),
        depth_max: int | None = Query(default=None, ge=0),
        stars_min: int | None = Query(default=None, ge=0),
        stars_max: int | None = Query(default=None, ge=0),
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return service.list_assets(
                    pool_id,
                    page=page,
                    page_size=page_size,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    query=query,
                    repo=repo,
                    commit=commit,
                    language=language,
                    entry_file=entry_file,
                    depth=depth,
                    depth_min=depth_min,
                    depth_max=depth_max,
                    stars_min=stars_min,
                    stars_max=stars_max,
                    stage2_run_id=stage2_run_id,
                    stage3_run_id=stage3_run_id,
                    stage4_run_id=stage4_run_id,
                    created_after=created_after,
                    created_before=created_before,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/assets/selection")
    def list_data_pool_asset_ids(
        pool_id: str,
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = Query(default=None, ge=0),
        depth_min: int | None = Query(default=None, ge=0),
        depth_max: int | None = Query(default=None, ge=0),
        stars_min: int | None = Query(default=None, ge=0),
        stars_max: int | None = Query(default=None, ge=0),
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return service.list_asset_ids(
                    pool_id,
                    query=query,
                    repo=repo,
                    commit=commit,
                    language=language,
                    entry_file=entry_file,
                    depth=depth,
                    depth_min=depth_min,
                    depth_max=depth_max,
                    stars_min=stars_min,
                    stars_max=stars_max,
                    stage2_run_id=stage2_run_id,
                    stage3_run_id=stage3_run_id,
                    stage4_run_id=stage4_run_id,
                    created_after=created_after,
                    created_before=created_before,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/assets/{asset_id}")
    def get_data_pool_asset(pool_id: str, asset_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return {"asset": service.serialize_asset_detail(service.get_asset(pool_id, asset_id))}
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/assets/{asset_id}/llm-completions/{role}/files")
    def get_data_pool_asset_llm_completion_file(
        pool_id: str,
        asset_id: str,
        role: str,
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                return service.serialize_asset_llm_completion_file(pool_id, asset_id, role, path)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.post("/api/data-pools/{pool_id}/assets/delete")
    def delete_data_pool_assets(pool_id: str, payload: DeleteDataPoolAssetsRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                result = service.delete_assets(
                    pool_id,
                    asset_ids=payload.asset_ids,
                    query=payload.query,
                    repo=payload.repo,
                    commit=payload.commit,
                    language=payload.language,
                    entry_file=payload.entry_file,
                    depth=payload.depth,
                    depth_min=payload.depth_min,
                    depth_max=payload.depth_max,
                    stars_min=payload.stars_min,
                    stars_max=payload.stars_max,
                    stage2_run_id=payload.stage2_run_id,
                    stage3_run_id=payload.stage3_run_id,
                    stage4_run_id=payload.stage4_run_id,
                    created_after=payload.created_after,
                    created_before=payload.created_before,
                )
                session.commit()
                return result
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/download.zip")
    def download_data_pool_zip(pool_id: str) -> FileResponse:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                archive_path, filename = service.build_pool_zip(pool_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda path=archive_path: path.unlink(missing_ok=True)),
        )

    @app.post("/api/data-pools/{pool_id}/download-selection.zip")
    def download_selected_data_pool_assets_zip(pool_id: str, payload: DownloadDataPoolAssetsRequest) -> FileResponse:
        asset_ids = [str(asset_id).strip() for asset_id in list(payload.asset_ids or []) if str(asset_id).strip()]
        if not asset_ids:
            raise HTTPException(status_code=400, detail="No assets selected for download")
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                archive_path, filename = service.build_pool_zip(pool_id, asset_ids=asset_ids)
            except ValueError as exc:
                detail = str(exc)
                if detail == "No assets selected for download":
                    raise HTTPException(status_code=400, detail=detail) from exc
                raise HTTPException(status_code=404, detail=detail) from exc
        finally:
            session.close()
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda path=archive_path: path.unlink(missing_ok=True)),
        )

    @app.post("/api/data-pools/{pool_id}/download-selections")
    def create_data_pool_download_selection(
        pool_id: str,
        payload: CreateDataPoolDownloadSelectionRequest,
    ) -> dict[str, Any]:
        asset_ids = [str(asset_id).strip() for asset_id in list(payload.asset_ids or []) if str(asset_id).strip()]
        if not asset_ids:
            raise HTTPException(status_code=400, detail="No assets selected for download")
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                result = service.create_download_selection(pool_id, asset_ids=asset_ids)
                session.commit()
                return result
            except ValueError as exc:
                session.rollback()
                detail = str(exc)
                if detail == "No assets selected for download":
                    raise HTTPException(status_code=400, detail=detail) from exc
                raise HTTPException(status_code=404, detail=detail) from exc
        finally:
            session.close()

    @app.get("/api/data-pools/{pool_id}/download-selections/{selection_id}.zip")
    def download_data_pool_selection_zip(pool_id: str, selection_id: str) -> FileResponse:
        session = session_factory()
        try:
            service = DataPoolService(session)
            try:
                archive_path, filename = service.build_download_selection_zip(pool_id, selection_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()
        return FileResponse(
            archive_path,
            media_type="application/zip",
            filename=filename,
            background=BackgroundTask(lambda path=archive_path: path.unlink(missing_ok=True)),
        )

    @app.get("/api/assets/stage2/images")
    def get_stage2_image_assets() -> dict[str, Any]:
        try:
            return image_asset_build_jobs.apply(stage2_image_asset_status())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/assets/images")
    def get_managed_images() -> dict[str, Any]:
        session = session_factory()
        try:
            return managed_image_delete_jobs.apply(
                _list_feature_factory_managed_images(session, settings)
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            session.close()

    @app.post("/api/assets/images/delete")
    def delete_managed_image(payload: DeleteManagedImageRequest) -> dict[str, Any]:
        normalized_ref = str(payload.image_ref or "").strip()
        if not normalized_ref or any(char.isspace() for char in normalized_ref):
            raise HTTPException(status_code=400, detail="invalid image ref")
        session = session_factory()
        try:
            status_payload = _list_feature_factory_managed_images(session, settings)
            if not any(
                str(row.get("image_ref") or "") == normalized_ref
                for row in status_payload.get("rows", [])
            ):
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown FeatureFactory image: {normalized_ref}",
                )
            started = managed_image_delete_jobs.start(
                normalized_ref,
                lambda image_ref=normalized_ref: _delete_local_docker_image(image_ref),
            )
            return {
                "started": started,
                "status": managed_image_delete_jobs.apply(
                    _list_feature_factory_managed_images(session, settings)
                ),
            }
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            session.close()

    @app.post("/api/assets/images/delete-selected")
    def delete_selected_managed_images(payload: DeleteManagedImagesRequest) -> dict[str, Any]:
        normalized_refs: list[str] = []
        seen_refs: set[str] = set()
        for image_ref in list(payload.image_refs or []):
            normalized_ref = str(image_ref or "").strip()
            if not normalized_ref or any(char.isspace() for char in normalized_ref):
                raise HTTPException(status_code=400, detail="invalid image ref")
            if normalized_ref in seen_refs:
                continue
            seen_refs.add(normalized_ref)
            normalized_refs.append(normalized_ref)
        if not normalized_refs:
            raise HTTPException(status_code=400, detail="no images selected")
        session = session_factory()
        try:
            status_payload = _list_feature_factory_managed_images(session, settings)
            known_refs = {
                str(row.get("image_ref") or "")
                for row in list(status_payload.get("rows") or [])
            }
            unknown_refs = [image_ref for image_ref in normalized_refs if image_ref not in known_refs]
            if unknown_refs:
                raise HTTPException(
                    status_code=404,
                    detail=f"unknown FeatureFactory images: {', '.join(unknown_refs[:5])}",
                )
            started_refs: list[str] = []
            already_running_refs: list[str] = []
            for image_ref in normalized_refs:
                started = managed_image_delete_jobs.start(
                    image_ref,
                    lambda image_ref=image_ref: _delete_local_docker_image(image_ref),
                )
                (started_refs if started else already_running_refs).append(image_ref)
            return {
                "started": len(started_refs),
                "already_running": len(already_running_refs),
                "started_refs": started_refs,
                "already_running_refs": already_running_refs,
                "status": managed_image_delete_jobs.apply(
                    _list_feature_factory_managed_images(session, settings)
                ),
            }
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/assets/stage2/images/detail")
    def get_stage2_image_asset_detail(asset_key: str = Query(..., min_length=1)) -> dict[str, Any]:
        try:
            normalized_asset_key = str(asset_key or "").strip()
            status_payload = stage2_image_asset_status()
            rows = status_payload.get("rows")
            selected_row = None
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if str(row.get("asset_key") or row.get("image_id") or "").strip() == normalized_asset_key:
                        selected_row = row
                        break
            if selected_row is None:
                raise HTTPException(status_code=404, detail=f"unknown image asset: {normalized_asset_key}")
            detail_payload = {
                "asset_key": normalized_asset_key,
                "platform": status_payload.get("platform"),
                "mirror_profile": status_payload.get("mirror_profile"),
                "sdk": status_payload.get("sdk"),
                "row": selected_row,
                "job": image_asset_build_jobs.detail_payload(normalized_asset_key),
            }
            return image_asset_build_jobs.apply(detail_payload, include_logs=True)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/assets/stage2/sdk-prewarm")
    def get_stage2_sdk_prewarm_status() -> dict[str, Any]:
        try:
            return sdk_prewarm_jobs.apply(stage2_sdk_prewarm_status())
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/stage2/sdk-prewarm")
    def prewarm_stage2_sdk_assets(payload: PrewarmStage2SdkRequest) -> dict[str, Any]:
        try:
            host_started = sdk_prewarm_jobs.start(
                "host",
                lambda log_callback, cancel_requested: prewarm_host_sdk_environment(
                    log_callback=log_callback,
                    cancel_requested=cancel_requested,
                ),
            )
            planner_started = sdk_prewarm_jobs.start(
                "planner",
                lambda log_callback, cancel_requested: prewarm_planner_agent_server_image(
                    force=payload.force,
                    log_callback=log_callback,
                    cancel_requested=cancel_requested,
                ),
            )
            return {
                "started": {
                    "host": host_started,
                    "planner": planner_started,
                },
                "status": sdk_prewarm_jobs.apply(stage2_sdk_prewarm_status()),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            output = str(exc.stderr or exc.output or "").strip()
            detail = (
                f"SDK prewarm failed with exit code {exc.returncode}: "
                f"{' '.join(str(part) for part in exc.cmd)}"
            )
            if output:
                detail = f"{detail}\n{output[-4000:]}"
            raise HTTPException(
                status_code=500,
                detail=detail,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/stage2/sdk-prewarm/host")
    def prewarm_stage2_host_sdk_environment(_payload: PrewarmStage2SdkRequest) -> dict[str, Any]:
        try:
            started = sdk_prewarm_jobs.start(
                "host",
                lambda log_callback, cancel_requested: prewarm_host_sdk_environment(
                    log_callback=log_callback,
                    cancel_requested=cancel_requested,
                ),
            )
            return {
                "started": started,
                "status": sdk_prewarm_jobs.apply(stage2_sdk_prewarm_status()),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            output = str(exc.stderr or exc.output or "").strip()
            detail = (
                f"Host Python environment prewarm failed with exit code {exc.returncode}: "
                f"{' '.join(str(part) for part in exc.cmd)}"
            )
            if output:
                detail = f"{detail}\n{output[-4000:]}"
            raise HTTPException(status_code=500, detail=detail) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/stage2/sdk-prewarm/planner")
    def prewarm_stage2_planner_agent_server(payload: PrewarmStage2SdkRequest) -> dict[str, Any]:
        try:
            started = sdk_prewarm_jobs.start(
                "planner",
                lambda log_callback, cancel_requested: prewarm_planner_agent_server_image(
                    force=payload.force,
                    log_callback=log_callback,
                    cancel_requested=cancel_requested,
                ),
            )
            return {
                "started": started,
                "status": sdk_prewarm_jobs.apply(stage2_sdk_prewarm_status()),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            output = str(exc.stderr or exc.output or "").strip()
            detail = (
                f"Planner agent-server image prewarm failed with exit code {exc.returncode}: "
                f"{' '.join(str(part) for part in exc.cmd)}"
            )
            if output:
                detail = f"{detail}\n{output[-4000:]}"
            raise HTTPException(status_code=500, detail=detail) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/stage2/sdk-prewarm/{target}/cancel")
    def cancel_stage2_sdk_prewarm(target: str) -> dict[str, Any]:
        normalized_target = str(target or "").strip()
        if normalized_target not in {"host", "planner"}:
            raise HTTPException(status_code=400, detail="target must be host or planner")
        cancelled = sdk_prewarm_jobs.cancel(normalized_target)
        return {
            "cancelled": cancelled,
            "status": sdk_prewarm_jobs.apply(stage2_sdk_prewarm_status()),
        }

    @app.post("/api/assets/stage2/images/build")
    def build_stage2_images(payload: BuildStage2ImageAssetsRequest) -> dict[str, Any]:
        try:
            normalized_ids = [str(image_id or "").strip() for image_id in payload.image_ids if str(image_id or "").strip()]
            if not normalized_ids:
                raise ValueError("at least one image_id is required")
            if not payload.include_base_image and not payload.include_agent_server_image:
                raise ValueError("at least one image target must be selected")
            build_stage2_image_assets(
                image_ids=normalized_ids,
                include_base_image=False,
                include_agent_server_image=False,
                force=False,
            )
            started: list[dict[str, Any]] = []
            for asset_key in normalized_ids:
                job_started = image_asset_build_jobs.start(
                    asset_key=asset_key,
                    include_base_image=payload.include_base_image,
                    include_agent_server_image=payload.include_agent_server_image,
                    force=payload.force,
                    callback=lambda hooks, asset_key=asset_key: build_stage2_image_assets(
                        image_ids=[asset_key],
                        include_base_image=payload.include_base_image,
                        include_agent_server_image=payload.include_agent_server_image,
                        force=payload.force,
                        log_callback=hooks.log,
                        target_started_callback=hooks.target_started,
                        target_finished_callback=hooks.target_finished,
                        cancel_requested=hooks.cancel_requested,
                    ),
                )
                started.append(
                    {
                        "asset_key": asset_key,
                        "include_base_image": payload.include_base_image,
                        "include_agent_server_image": payload.include_agent_server_image,
                        "started": job_started,
                    }
                )
            return {
                "started": started,
                "status": image_asset_build_jobs.apply(stage2_image_asset_status()),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/assets/stage2/images/cancel")
    def cancel_stage2_image_asset_build(payload: CancelStage2ImageAssetBuildRequest) -> dict[str, Any]:
        try:
            cancelled = image_asset_build_jobs.cancel(payload.asset_key)
            return {
                "cancelled": cancelled,
                "status": image_asset_build_jobs.apply(stage2_image_asset_status()),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/stage1/jobs")
    def list_stage1_jobs(limit: int = 30) -> dict[str, Any]:
        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            jobs = service.list_jobs(limit=limit)
            serialized_jobs: list[dict[str, Any]] = []
            for job in jobs:
                is_running = job_runner.is_running(job.id)
                is_scheduled = job_runner.is_scheduled(job.id)
                service.reconcile_job(job, is_running=is_running, is_scheduled=is_scheduled)
                serialized_jobs.append(
                    _serialize_job(
                        job,
                        is_running=is_running,
                        pause_requested=job_runner.is_pause_requested(job.id),
                    )
                )
            return {
                "jobs": serialized_jobs
            }
        finally:
            client.close()
            session.close()

    @app.get("/api/stage1/runtime")
    def get_stage1_runtime() -> dict[str, int]:
        return {
            "max_concurrent_jobs": job_runner.get_max_workers(),
            "max_concurrent_jobs_cap": job_runner.max_limit,
            "max_concurrent_partitions_default": job_runner.get_default_max_concurrent_partitions(),
            "max_concurrent_partitions_cap": job_runner.partition_concurrency_cap,
            "github_token_count": token_scheduler.get_total_token_count(),
            "runtime_github_token_count": token_scheduler.get_runtime_token_count(),
        }

    @app.patch("/api/stage1/runtime")
    def update_stage1_runtime(payload: UpdateStage1RuntimeRequest) -> dict[str, int]:
        try:
            if payload.max_concurrent_jobs is not None:
                job_runner.set_max_workers(payload.max_concurrent_jobs)
            if payload.github_tokens is not None:
                token_scheduler.set_runtime_tokens(payload.github_tokens)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "max_concurrent_jobs": job_runner.get_max_workers(),
            "max_concurrent_jobs_cap": job_runner.max_limit,
            "max_concurrent_partitions_default": job_runner.get_default_max_concurrent_partitions(),
            "max_concurrent_partitions_cap": job_runner.partition_concurrency_cap,
            "github_token_count": token_scheduler.get_total_token_count(),
            "runtime_github_token_count": token_scheduler.get_runtime_token_count(),
        }

    @app.get("/api/stage1/templates")
    def list_stage1_crawl_templates() -> dict[str, Any]:
        session = session_factory()
        try:
            rows = list(
                session.scalars(
                    select(Stage1CrawlTemplate).order_by(
                        Stage1CrawlTemplate.updated_at.desc(),
                        Stage1CrawlTemplate.name.asc(),
                    )
                )
            )
            return {"templates": [_serialize_stage1_crawl_template_summary(row) for row in rows]}
        finally:
            session.close()

    @app.get("/api/stage1/templates/{template_id}")
    def get_stage1_crawl_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage1CrawlTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage1 crawl template not found: {template_id}")
            return _serialize_stage1_crawl_template_detail(row)
        finally:
            session.close()

    @app.post("/api/stage1/templates")
    def save_stage1_crawl_template(payload: SaveStage1CrawlTemplateRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            existing = session.scalar(
                select(Stage1CrawlTemplate).where(Stage1CrawlTemplate.name == payload.name.strip())
            )
            snapshot = _stage1_crawl_template_snapshot_from_request(payload)
            if not snapshot["github_tokens"]:
                raise HTTPException(status_code=400, detail="github_tokens is required for token template")
            if existing is None:
                row = Stage1CrawlTemplate(name=payload.name.strip(), snapshot_json=snapshot)
                session.add(row)
            else:
                row = existing
                row.snapshot_json = snapshot
            session.commit()
            session.refresh(row)
            return _serialize_stage1_crawl_template_summary(row)
        finally:
            session.close()

    @app.delete("/api/stage1/templates/{template_id}")
    def delete_stage1_crawl_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage1CrawlTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage1 crawl template not found: {template_id}")
            payload = _serialize_stage1_crawl_template_summary(row)
            session.delete(row)
            session.commit()
            return payload
        finally:
            session.close()

    @app.get("/api/stage2/runtime")
    def get_stage2_runtime() -> dict[str, Any]:
        return _serialize_stage2_runtime(settings, env_runner)

    @app.get("/api/stage2/runtime/templates")
    def list_stage2_runtime_templates() -> dict[str, Any]:
        session = session_factory()
        try:
            rows = list(
                session.scalars(
                    select(Stage2RuntimeTemplate).order_by(Stage2RuntimeTemplate.updated_at.desc(), Stage2RuntimeTemplate.name.asc())
                )
            )
            return {
                "templates": [_serialize_stage2_runtime_template_summary(row) for row in rows],
            }
        finally:
            session.close()

    @app.get("/api/stage2/runtime/templates/{template_id}")
    def get_stage2_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage2RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage2 runtime template not found: {template_id}")
            return _serialize_stage2_runtime_template_detail(row)
        finally:
            session.close()

    @app.post("/api/stage2/runtime/templates")
    def save_stage2_runtime_template(payload: SaveStage2RuntimeTemplateRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            existing = session.scalar(
                select(Stage2RuntimeTemplate).where(Stage2RuntimeTemplate.name == payload.name.strip())
            )
            snapshot = _stage2_runtime_snapshot_from_request(payload, settings=settings)
            if existing is None:
                row = Stage2RuntimeTemplate(
                    name=payload.name.strip(),
                    snapshot_json=snapshot,
                )
                session.add(row)
            else:
                row = existing
                row.snapshot_json = snapshot
            session.commit()
            session.refresh(row)
            return _serialize_stage2_runtime_template_summary(row)
        finally:
            session.close()

    @app.delete("/api/stage2/runtime/templates/{template_id}")
    def delete_stage2_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage2RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage2 runtime template not found: {template_id}")
            payload = {
                "id": row.id,
                "name": row.name,
            }
            session.delete(row)
            session.commit()
            return payload
        finally:
            session.close()

    @app.patch("/api/stage2/runtime")
    def update_stage2_runtime(payload: UpdateStage2RuntimeRequest) -> dict[str, Any]:
        if payload.max_concurrent_runs is not None:
            system_capacity = env_runner.get_max_workers()
            if payload.max_concurrent_runs > system_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"max_concurrent_runs cannot exceed stage2 system capacity {system_capacity}",
                )
            settings.stage2_default_task_max_concurrent_runs = payload.max_concurrent_runs

        fields_set = payload.model_fields_set
        if "planner_model" in fields_set:
            settings.stage2_planner_llm_model = _normalize_optional_text(payload.planner_model)
        if "planner_base_url" in fields_set:
            settings.stage2_planner_llm_base_url = _normalize_optional_text(payload.planner_base_url)
        if "planner_api_key" in fields_set:
            planner_api_key = _normalize_optional_text(payload.planner_api_key)
            if planner_api_key:
                settings.stage2_planner_llm_api_key = SecretStr(planner_api_key)
        if "planner_preset" in fields_set:
            settings.stage2_planner_openhands_preset = payload.planner_preset
        if "planner_max_iterations" in fields_set:
            settings.stage2_planner_openhands_max_iterations = payload.planner_max_iterations
        if "planner_timeout_seconds" in fields_set:
            settings.stage2_planner_agent_timeout_seconds = payload.planner_timeout_seconds

        if "worker_model" in fields_set:
            settings.stage2_worker_llm_model = _normalize_optional_text(payload.worker_model)
        if "worker_base_url" in fields_set:
            settings.stage2_worker_llm_base_url = _normalize_optional_text(payload.worker_base_url)
        if "worker_api_key" in fields_set:
            worker_api_key = _normalize_optional_text(payload.worker_api_key)
            if worker_api_key:
                settings.stage2_worker_llm_api_key = SecretStr(worker_api_key)
        if "worker_preset" in fields_set:
            settings.stage2_worker_openhands_preset = payload.worker_preset
        if "worker_max_iterations" in fields_set:
            settings.stage2_worker_openhands_max_iterations = payload.worker_max_iterations
        if "worker_timeout_seconds" in fields_set:
            settings.stage2_worker_agent_timeout_seconds = payload.worker_timeout_seconds

        if payload.agent_timeout_seconds is not None:
            settings.stage2_agent_timeout_seconds = payload.agent_timeout_seconds
        if payload.max_worker_attempts is not None:
            settings.stage2_max_worker_attempts = payload.max_worker_attempts
        if payload.quickcheck_sample_size is not None:
            settings.stage2_quickcheck_sample_size = payload.quickcheck_sample_size
        if payload.entry_file_test_count_min is not None:
            settings.stage2_entry_file_test_count_min = payload.entry_file_test_count_min
        if "p2p_file_count_limit" in fields_set:
            settings.stage2_p2p_file_count_limit = payload.p2p_file_count_limit
        if payload.collect_timeout_seconds is not None:
            settings.stage2_collect_timeout_seconds = payload.collect_timeout_seconds
        if payload.run_test_timeout_seconds is not None:
            settings.stage2_run_test_timeout_seconds = payload.run_test_timeout_seconds
        if payload.build_timeout_seconds is not None:
            settings.stage2_build_timeout_seconds = payload.build_timeout_seconds
        if payload.full_validation_timeout_seconds is not None:
            settings.stage2_full_validation_timeout_seconds = payload.full_validation_timeout_seconds

        runtime_snapshot = _stage2_runtime_snapshot_from_request(UpdateStage2RuntimeRequest(), settings=settings)
        _persist_current_stage_runtime_config(
            key=CURRENT_STAGE2_RUNTIME_CONFIG_KEY,
            snapshot=runtime_snapshot,
        )
        reload_backend = getattr(env_runner, "reload_backend", None)
        if callable(reload_backend):
            reload_backend()
        return _serialize_stage2_runtime(settings, env_runner)

    @app.get("/api/stage3/runtime")
    def get_stage3_runtime() -> dict[str, Any]:
        return _serialize_stage3_runtime(settings, env_stage3_runner)

    @app.get("/api/stage3/runtime/templates")
    def list_stage3_runtime_templates() -> dict[str, Any]:
        session = session_factory()
        try:
            rows = list(
                session.scalars(
                    select(Stage3RuntimeTemplate).order_by(
                        Stage3RuntimeTemplate.updated_at.desc(),
                        Stage3RuntimeTemplate.name.asc(),
                    )
                )
            )
            return {
                "templates": [_serialize_stage3_runtime_template_summary(row) for row in rows],
            }
        finally:
            session.close()

    @app.get("/api/stage3/runtime/templates/{template_id}")
    def get_stage3_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage3RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage3 runtime template not found: {template_id}")
            return _serialize_stage3_runtime_template_detail(row)
        finally:
            session.close()

    @app.post("/api/stage3/runtime/templates")
    def save_stage3_runtime_template(payload: SaveStage3RuntimeTemplateRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            existing = session.scalar(
                select(Stage3RuntimeTemplate).where(Stage3RuntimeTemplate.name == payload.name.strip())
            )
            snapshot = _stage3_runtime_snapshot_from_request(payload, settings=settings)
            if existing is None:
                row = Stage3RuntimeTemplate(
                    name=payload.name.strip(),
                    snapshot_json=snapshot,
                )
                session.add(row)
            else:
                row = existing
                row.snapshot_json = snapshot
            session.commit()
            session.refresh(row)
            return _serialize_stage3_runtime_template_summary(row)
        finally:
            session.close()

    @app.delete("/api/stage3/runtime/templates/{template_id}")
    def delete_stage3_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage3RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage3 runtime template not found: {template_id}")
            payload = {
                "id": row.id,
                "name": row.name,
            }
            session.delete(row)
            session.commit()
            return payload
        finally:
            session.close()

    @app.patch("/api/stage3/runtime")
    def update_stage3_runtime(payload: UpdateStage3RuntimeRequest) -> dict[str, Any]:
        if payload.max_concurrent_runs is not None:
            system_capacity = env_stage3_runner.get_max_workers()
            if payload.max_concurrent_runs > system_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"max_concurrent_runs cannot exceed stage3 system capacity {system_capacity}",
                )
            settings.stage3_default_task_max_concurrent_runs = payload.max_concurrent_runs
        if payload.breaker_model is not None:
            settings.stage3_llm_model = _normalize_optional_text(payload.breaker_model)
        if payload.breaker_base_url is not None:
            settings.stage3_llm_base_url = _normalize_optional_text(payload.breaker_base_url)
        if payload.breaker_api_key is not None:
            api_key = _normalize_optional_text(payload.breaker_api_key)
            settings.stage3_llm_api_key = SecretStr(api_key) if api_key else None
        if payload.breaker_preset is not None:
            settings.stage3_openhands_preset = payload.breaker_preset
        if payload.breaker_max_iterations is not None:
            settings.stage3_openhands_max_iterations = payload.breaker_max_iterations
        if payload.breaker_timeout_seconds is not None:
            settings.stage3_agent_timeout_seconds = payload.breaker_timeout_seconds
        if payload.build_timeout_seconds is not None:
            settings.stage3_build_timeout_seconds = payload.build_timeout_seconds
        if payload.run_test_timeout_seconds is not None:
            settings.stage3_run_test_timeout_seconds = payload.run_test_timeout_seconds
        if payload.full_validation_timeout_seconds is not None:
            settings.stage3_full_validation_timeout_seconds = payload.full_validation_timeout_seconds
        if payload.entry_pass_rate_ceiling is not None:
            settings.stage3_entry_pass_rate_ceiling = payload.entry_pass_rate_ceiling
        if payload.min_removed_code_lines is not None:
            settings.stage3_min_removed_code_lines = payload.min_removed_code_lines
        runtime_snapshot = _stage3_runtime_snapshot_from_request(UpdateStage3RuntimeRequest(), settings=settings)
        _persist_current_stage_runtime_config(
            key=CURRENT_STAGE3_RUNTIME_CONFIG_KEY,
            snapshot=runtime_snapshot,
        )
        reload_backend = getattr(env_stage3_runner, "reload_backend", None)
        if callable(reload_backend):
            reload_backend()
        return _serialize_stage3_runtime(settings, env_stage3_runner)

    @app.get("/api/stage4/runtime")
    def get_stage4_runtime() -> dict[str, Any]:
        return _serialize_stage4_runtime(settings, env_stage4_runner)

    @app.get("/api/stage4/runtime/templates")
    def list_stage4_runtime_templates() -> dict[str, Any]:
        session = session_factory()
        try:
            rows = list(
                session.scalars(
                    select(Stage4RuntimeTemplate).order_by(
                        Stage4RuntimeTemplate.updated_at.desc(),
                        Stage4RuntimeTemplate.name.asc(),
                    )
                )
            )
            return {
                "templates": [_serialize_stage4_runtime_template_summary(row) for row in rows],
            }
        finally:
            session.close()

    @app.get("/api/stage4/runtime/templates/{template_id}")
    def get_stage4_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage4RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage4 runtime template not found: {template_id}")
            return _serialize_stage4_runtime_template_detail(row)
        finally:
            session.close()

    @app.post("/api/stage4/runtime/templates")
    def save_stage4_runtime_template(payload: SaveStage4RuntimeTemplateRequest) -> dict[str, Any]:
        session = session_factory()
        try:
            existing = session.scalar(
                select(Stage4RuntimeTemplate).where(Stage4RuntimeTemplate.name == payload.name.strip())
            )
            snapshot = _stage4_runtime_snapshot_from_request(payload, settings=settings)
            if existing is None:
                row = Stage4RuntimeTemplate(
                    name=payload.name.strip(),
                    snapshot_json=snapshot,
                )
                session.add(row)
            else:
                row = existing
                row.snapshot_json = snapshot
            session.commit()
            session.refresh(row)
            return _serialize_stage4_runtime_template_summary(row)
        finally:
            session.close()

    @app.delete("/api/stage4/runtime/templates/{template_id}")
    def delete_stage4_runtime_template(template_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            row = session.get(Stage4RuntimeTemplate, template_id)
            if row is None:
                raise HTTPException(status_code=404, detail=f"stage4 runtime template not found: {template_id}")
            payload = {
                "id": row.id,
                "name": row.name,
            }
            session.delete(row)
            session.commit()
            return payload
        finally:
            session.close()

    @app.patch("/api/stage4/runtime")
    def update_stage4_runtime(payload: UpdateStage4RuntimeRequest) -> dict[str, Any]:
        if payload.max_concurrent_runs is not None:
            system_capacity = env_stage4_runner.get_max_workers()
            if payload.max_concurrent_runs > system_capacity:
                raise HTTPException(
                    status_code=400,
                    detail=f"max_concurrent_runs cannot exceed stage4 system capacity {system_capacity}",
                )
            settings.stage4_default_task_max_concurrent_runs = payload.max_concurrent_runs
        if payload.issuer_model is not None:
            settings.stage4_llm_model = _normalize_optional_text(payload.issuer_model)
        if payload.issuer_base_url is not None:
            settings.stage4_llm_base_url = _normalize_optional_text(payload.issuer_base_url)
        if payload.issuer_api_key is not None:
            api_key = _normalize_optional_text(payload.issuer_api_key)
            settings.stage4_llm_api_key = SecretStr(api_key) if api_key else None
        if payload.issuer_preset is not None:
            settings.stage4_openhands_preset = payload.issuer_preset
        if payload.issuer_max_iterations is not None:
            settings.stage4_openhands_max_iterations = payload.issuer_max_iterations
        if payload.issuer_timeout_seconds is not None:
            settings.stage4_agent_timeout_seconds = payload.issuer_timeout_seconds
        if payload.build_timeout_seconds is not None:
            settings.stage4_build_timeout_seconds = payload.build_timeout_seconds
        runtime_snapshot = _stage4_runtime_snapshot_from_request(UpdateStage4RuntimeRequest(), settings=settings)
        _persist_current_stage_runtime_config(
            key=CURRENT_STAGE4_RUNTIME_CONFIG_KEY,
            snapshot=runtime_snapshot,
        )
        reload_backend = getattr(env_stage4_runner, "reload_backend", None)
        if callable(reload_backend):
            reload_backend()
        return _serialize_stage4_runtime(settings, env_stage4_runner)

    def load_stage4_source_group_detail_payload(
        repository_id: int,
        snapshot_id: str,
        *,
        sort_by: str = "depth",
        sort_order: str = "desc",
        entry_query: str | None = None,
        statuses: list[str] | None = None,
        test_count_min: int | None = None,
        test_count_max: int | None = None,
        entry_pass_rate_min: float | None = None,
        entry_pass_rate_max: float | None = None,
        diff_lines_min: int | None = None,
        diff_lines_max: int | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            return service.get_source_group_detail(
                repository_id,
                snapshot_id,
                sort_by=sort_by,
                sort_order=sort_order,
                entry_query=entry_query,
                statuses=statuses,
                test_count_min=test_count_min,
                test_count_max=test_count_max,
                entry_pass_rate_min=entry_pass_rate_min,
                entry_pass_rate_max=entry_pass_rate_max,
                diff_lines_min=diff_lines_min,
                diff_lines_max=diff_lines_max,
            )
        finally:
            session.close()

    @app.get("/api/stage4/sources")
    def list_stage4_sources(
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "latest_operation_at",
        sort_order: str = "desc",
        name_query: str | None = None,
        statuses: list[str] | None = Query(default=None),
        languages: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be at least 1")
        if page_size < 1 or page_size > 200:
            raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")
        if sort_by not in STAGE4_SOURCE_GROUP_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            return service.list_source_groups(
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
                name_query=name_query,
                languages=languages,
                statuses=statuses,
            )
        finally:
            session.close()

    @app.get("/api/stage4/sources/{repository_id}/snapshots/{snapshot_id}")
    def get_stage4_source_group_detail(
        repository_id: int,
        snapshot_id: str,
        sort_by: str = "depth",
        sort_order: str = "desc",
        entry_query: str | None = None,
        statuses: list[str] | None = Query(default=None),
        test_count_min: int | None = Query(default=None, ge=0),
        test_count_max: int | None = Query(default=None, ge=0),
        entry_pass_rate_min: float | None = Query(default=None, ge=0.0, le=100.0),
        entry_pass_rate_max: float | None = Query(default=None, ge=0.0, le=100.0),
        diff_lines_min: int | None = Query(default=None, ge=0),
        diff_lines_max: int | None = Query(default=None, ge=0),
    ) -> dict[str, Any]:
        if sort_by not in STAGE4_SOURCE_DETAIL_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        if test_count_min is not None and test_count_max is not None and test_count_min > test_count_max:
            raise HTTPException(status_code=400, detail="test_count_min must be less than or equal to test_count_max")
        if (
            entry_pass_rate_min is not None
            and entry_pass_rate_max is not None
            and entry_pass_rate_min > entry_pass_rate_max
        ):
            raise HTTPException(
                status_code=400,
                detail="entry_pass_rate_min must be less than or equal to entry_pass_rate_max",
            )
        if diff_lines_min is not None and diff_lines_max is not None and diff_lines_min > diff_lines_max:
            raise HTTPException(status_code=400, detail="diff_lines_min must be less than or equal to diff_lines_max")
        try:
            return load_stage4_source_group_detail_payload(
                repository_id,
                snapshot_id,
                sort_by=sort_by,
                sort_order=sort_order,
                entry_query=entry_query,
                statuses=statuses,
                test_count_min=test_count_min,
                test_count_max=test_count_max,
                entry_pass_rate_min=entry_pass_rate_min,
                entry_pass_rate_max=entry_pass_rate_max,
                diff_lines_min=diff_lines_min,
                diff_lines_max=diff_lines_max,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/stage4/sources/{repository_id}/snapshots/{snapshot_id}/events")
    async def stream_stage4_source_group_events(
        repository_id: int,
        snapshot_id: str,
        request: Request,
        sort_by: str = "depth",
        sort_order: str = "desc",
        entry_query: str | None = None,
        statuses: list[str] | None = Query(default=None),
        test_count_min: int | None = Query(default=None, ge=0),
        test_count_max: int | None = Query(default=None, ge=0),
        entry_pass_rate_min: float | None = Query(default=None, ge=0.0, le=100.0),
        entry_pass_rate_max: float | None = Query(default=None, ge=0.0, le=100.0),
        diff_lines_min: int | None = Query(default=None, ge=0),
        diff_lines_max: int | None = Query(default=None, ge=0),
    ) -> StreamingResponse:
        initial_payload = get_stage4_source_group_detail(
            repository_id,
            snapshot_id,
            sort_by=sort_by,
            sort_order=sort_order,
            entry_query=entry_query,
            statuses=statuses,
            test_count_min=test_count_min,
            test_count_max=test_count_max,
            entry_pass_rate_min=entry_pass_rate_min,
            entry_pass_rate_max=entry_pass_rate_max,
            diff_lines_min=diff_lines_min,
            diff_lines_max=diff_lines_max,
        )
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: load_stage4_source_group_detail_payload(
                    repository_id,
                    snapshot_id,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    entry_query=entry_query,
                    statuses=statuses,
                    test_count_min=test_count_min,
                    test_count_max=test_count_max,
                    entry_pass_rate_min=entry_pass_rate_min,
                    entry_pass_rate_max=entry_pass_rate_max,
                    diff_lines_min=diff_lines_min,
                    diff_lines_max=diff_lines_max,
                ),
                is_active=is_stage4_source_payload_active,
                signature_of=_json_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/stage4/runs")
    def list_stage4_runs(
        source_savepoint_id: int | None = Query(default=None, ge=1),
        page: int = 1,
        page_size: int = 200,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        statuses: list[str] | None = Query(default=None),
        results: list[str] | None = Query(default=None),
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be at least 1")
        if page_size < 1 or page_size > 5000:
            raise HTTPException(status_code=400, detail="page_size must be between 1 and 5000")
        if sort_by not in STAGE4_RUN_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            return service.list_runs(
                source_savepoint_id=source_savepoint_id,
                page=page,
                page_size=page_size,
                sort_by=sort_by,
                sort_order=sort_order,
                statuses=statuses,
                results=results,
            )
        finally:
            session.close()

    @app.post("/api/stage4/runs")
    def create_stage4_run(payload: CreateStage4RunRequest) -> dict[str, Any]:
        _require_stage4_issuer_ready()
        session = session_factory()
        run_id: str
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                run = service.create_run(payload.source_savepoint_id)
                run = service.queue_run(run.id)
                run_id = run.id
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage4RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()
        _schedule_stage4_run_or_raise(run_id, detail="failed to schedule the Stage4 issuer run")
        return get_stage4_run_detail(run_id)

    @app.get("/api/stage4/runs/{run_id}")
    def get_stage4_run_detail(run_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                return service.serialize_run_detail(service.get_run(run_id, include_detail=True))
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/stage4/runs/{run_id}/events")
    async def stream_stage4_run_events(run_id: str, request: Request) -> StreamingResponse:
        initial_payload = get_stage4_run_detail(run_id)
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: get_stage4_run_detail(run_id),
                is_active=is_stage4_run_payload_active,
                signature_of=_json_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.patch("/api/stage4/runs/{run_id}/runtime")
    def update_stage4_run_runtime(
        run_id: str,
        payload: UpdateStage4RuntimeRequest,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                run = service.get_run(run_id, include_detail=False)
                runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
                runtime_snapshot = _stage4_run_runtime_snapshot_from_request(
                    payload,
                    settings=settings,
                    base_snapshot=dict(runtime_stage4.get("runtime") or {}),
                )
                service.update_run_runtime_snapshot(
                    run_id,
                    runtime_snapshot=runtime_snapshot,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage4RunConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()
        return get_stage4_run_detail(run_id)

    @app.post("/api/stage4/runs/{run_id}/interrupt")
    def interrupt_stage4_run(run_id: str) -> dict[str, Any]:
        if env_stage4_runner.interrupt_run(run_id):
            return get_stage4_run_detail(run_id)
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                service.interrupt_run(run_id)
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage4RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()
        return get_stage4_run_detail(run_id)

    @app.delete("/api/stage4/runs/{run_id}")
    def delete_stage4_run(run_id: str) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                archive = service.delete_run(run_id)
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage4RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        cleanup_result = _cleanup_stage4_run_archive_and_record_tombstone(archive)
        return {
            "deleted_run_id": run_id,
            "cleanup_warnings": cleanup_result if cleanup_result.get("failed_paths") else None,
        }

    @app.get("/api/stage4/runs/{run_id}/llm-completions/{role}.zip")
    def download_stage4_llm_completion_archive(
        run_id: str,
        role: str,
    ) -> FileResponse:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                run = service.get_run(run_id, include_detail=True)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage4 runtime directory is unknown for this run")

            repository = run.source_savepoint.run.entry_file.snapshot.repository
            allowed_roles = _stage4_llm_completion_roles(run)
            if role not in allowed_roles:
                raise HTTPException(status_code=404, detail=f"unknown LLM completion archive role: {role}")
            archive_dir = _stage4_llm_completion_archive_dir(runtime_dir, role)
            zip_path, filename = _build_stage4_llm_completion_zip(
                repository_full_name=repository.full_name,
                run_id=run.id,
                role=role,
                archive_dir=archive_dir,
                allowed_roles=allowed_roles,
            )
            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename=filename,
                background=BackgroundTask(lambda path=zip_path: Path(path).unlink(missing_ok=True)),
            )
        finally:
            session.close()

    @app.get("/api/stage4/runs/{run_id}/llm-completions/{role}/files")
    def get_stage4_llm_completion_file(
        run_id: str,
        role: str,
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
            try:
                run = service.get_run(run_id, include_detail=False)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage4 runtime directory is unknown for this run")

            completion_file = _resolve_stage4_llm_completion_file(
                runtime_dir=runtime_dir,
                role=role,
                requested_path=path,
                allowed_roles=_stage4_llm_completion_roles(run),
            )
            raw_text = completion_file.read_text(encoding="utf-8")
            stat = completion_file.stat()
            try:
                value = json.loads(raw_text)
            except json.JSONDecodeError:
                content_kind = "text"
                value = None
            else:
                content_kind = "json"

            return {
                "path": path,
                "filename": completion_file.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "kind": content_kind,
                "value": value,
                "text": raw_text if content_kind == "text" else None,
            }
        finally:
            session.close()

    @app.post("/api/stage1/jobs")
    def create_stage1_job(payload: CreateCrawlJobRequest) -> dict[str, Any]:
        try:
            resolved_max_concurrent_partitions = job_runner.validate_max_concurrent_partitions(
                payload.max_concurrent_partitions
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            effective_filters = CrawlFilters.model_validate(_effective_crawl_filters(payload.filters))
            job = service.create_job(
                payload.name,
                effective_filters,
                max_concurrent_partitions=resolved_max_concurrent_partitions,
            )
            session.commit()
            session.refresh(job)
        finally:
            client.close()
            session.close()

        if payload.run_in_background:
            queue_session = session_factory()
            queue_client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
            try:
                queue_service = CrawlService(queue_session, settings, queue_client)
                queue_service.mark_job_queued(job.id)
                queue_session.commit()
            finally:
                queue_client.close()
                queue_session.close()

            scheduled = job_runner.schedule_job(
                job.id,
                max_concurrent_partitions=resolved_max_concurrent_partitions,
            )
            if not scheduled:
                recover_session = session_factory()
                recover_client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
                try:
                    recover_service = CrawlService(recover_session, settings, recover_client)
                    recover_job = recover_service.get_job(job.id)
                    recover_service.reconcile_job(
                        recover_job,
                        is_running=job_runner.is_running(job.id),
                        is_scheduled=job_runner.is_scheduled(job.id),
                    )
                    recover_session.commit()
                finally:
                    recover_client.close()
                    recover_session.close()
                raise HTTPException(status_code=503, detail="failed to schedule crawl job")
        else:
            try:
                job_runner.run_job(
                    job.id,
                    max_concurrent_partitions=resolved_max_concurrent_partitions,
                )
            except Exception as exc:
                _raise_foreground_job_failure(
                    session_factory=session_factory,
                    job_id=job.id,
                    job_runner=job_runner,
                    message=str(exc),
                )

        session = session_factory()
        try:
            refreshed_job = session.get(CrawlJob, job.id)
            return {
                "job": _serialize_job(
                    refreshed_job,
                    is_running=job_runner.is_running(job.id),
                    pause_requested=job_runner.is_pause_requested(job.id),
                )
            }
        finally:
            session.close()

    @app.get("/api/stage1/jobs/{job_id}")
    def get_stage1_job(job_id: str) -> dict[str, Any]:
        return load_stage1_job_detail_payload(job_id)

    @app.get("/api/stage1/jobs/{job_id}/events")
    async def stream_stage1_job_events(job_id: str, request: Request) -> StreamingResponse:
        initial_payload = load_stage1_job_detail_payload(job_id)
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: load_stage1_job_detail_payload(job_id),
                is_active=is_stage1_job_payload_active,
                signature_of=_json_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/stage1/jobs/{job_id}/resume")
    def resume_stage1_job(job_id: str, payload: ResumeCrawlJobRequest | None = None) -> dict[str, Any]:
        body = payload or ResumeCrawlJobRequest()
        try:
            resolved_max_concurrent_partitions = job_runner.validate_max_concurrent_partitions(
                body.max_concurrent_partitions
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            try:
                job = service.get_job(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            is_running = job_runner.is_running(job.id)
            is_scheduled = job_runner.is_scheduled(job.id)
            if service.reconcile_job(job, is_running=is_running, is_scheduled=is_scheduled):
                session.commit()
            if is_running:
                raise HTTPException(status_code=409, detail="crawl job is already running")
            if job.status == CrawlJobStatus.completed.value:
                raise HTTPException(status_code=409, detail="crawl job is already completed")
            if not is_scheduled and not _job_can_resume(job, is_running=is_running):
                raise HTTPException(status_code=409, detail="crawl job has no resumable work")
            service.set_job_execution_options(
                job.id,
                max_concurrent_partitions=resolved_max_concurrent_partitions,
            )
            session.commit()
        finally:
            client.close()
            session.close()

        if body.run_in_background:
            queue_session = session_factory()
            queue_client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
            try:
                queue_service = CrawlService(queue_session, settings, queue_client)
                queue_service.mark_job_queued(job.id)
                queue_session.commit()
            finally:
                queue_client.close()
                queue_session.close()

            scheduled = job_runner.schedule_job(
                job.id,
                max_concurrent_partitions=resolved_max_concurrent_partitions,
            )
            if not scheduled:
                recover_session = session_factory()
                recover_client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
                try:
                    recover_service = CrawlService(recover_session, settings, recover_client)
                    recover_job = recover_service.get_job(job.id)
                    recover_service.reconcile_job(
                        recover_job,
                        is_running=job_runner.is_running(job.id),
                        is_scheduled=job_runner.is_scheduled(job.id),
                    )
                    recover_session.commit()
                finally:
                    recover_client.close()
                    recover_session.close()
                raise HTTPException(status_code=409, detail="crawl job could not be scheduled")
        else:
            try:
                job_runner.run_job(
                    job.id,
                    max_concurrent_partitions=resolved_max_concurrent_partitions,
                )
            except Exception as exc:
                _raise_foreground_job_failure(
                    session_factory=session_factory,
                    job_id=job.id,
                    job_runner=job_runner,
                    message=str(exc),
                )

        session = session_factory()
        try:
            refreshed_job = session.get(CrawlJob, job.id)
            return {
                "job": _serialize_job(
                    refreshed_job,
                    is_running=job_runner.is_running(job.id),
                    pause_requested=job_runner.is_pause_requested(job.id),
                )
            }
        finally:
            session.close()

    @app.post("/api/stage1/jobs/{job_id}/pause")
    def pause_stage1_job(job_id: str) -> dict[str, Any]:
        if not job_runner.request_pause(job_id):
            raise HTTPException(status_code=409, detail="crawl job is not running")
        return {"ok": True, "pause_requested": True}

    @app.delete("/api/stage1/jobs/{job_id}")
    def delete_stage1_job(job_id: str) -> dict[str, Any]:
        if job_runner.is_running(job_id):
            raise HTTPException(status_code=409, detail="cannot delete a running crawl job")
        if job_runner.is_scheduled(job_id):
            cancelled = job_runner.cancel_job(job_id)
            if not cancelled:
                if job_runner.is_running(job_id):
                    raise HTTPException(status_code=409, detail="cannot delete a running crawl job")
                if job_runner.is_scheduled(job_id):
                    raise HTTPException(status_code=409, detail="cannot delete a scheduled crawl job")

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            try:
                stats = service.delete_job(job_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.commit()
            return {
                "ok": True,
                "deleted": stats,
            }
        finally:
            client.close()
            session.close()

    @app.get("/api/stage1/repos")
    def list_stage1_repositories(
        page: int = 1,
        page_size: int = 50,
        sort_by: str = "discovered_at",
        sort_order: str = "desc",
        name_query: str | None = None,
        language: str | None = None,
        languages: list[str] | None = Query(default=None),
        licenses: list[str] | None = Query(default=None),
        stars_min: int | None = None,
        stars_max: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        pushed_after: datetime | None = None,
        pushed_before: datetime | None = None,
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be at least 1")
        if page_size < 1 or page_size > 200:
            raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")
        if sort_by not in REPO_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        if stars_min is not None and stars_max is not None and stars_min > stars_max:
            raise HTTPException(status_code=400, detail="stars_min must be less than or equal to stars_max")
        _validate_datetime_range(
            created_after,
            created_before,
            start_name="created_after",
            end_name="created_before",
        )
        _validate_datetime_range(
            pushed_after,
            pushed_before,
            start_name="pushed_after",
            end_name="pushed_before",
        )
        _validate_datetime_range(
            discovered_after,
            discovered_before,
            start_name="discovered_after",
            end_name="discovered_before",
        )

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            service = CrawlService(session, settings, client)
            offset = (page - 1) * page_size
            repositories, total = service.list_repositories(
                limit=page_size,
                offset=offset,
                sort_by=sort_by,
                sort_order=sort_order,
                name_query=name_query,
                language=language,
                languages=languages,
                licenses=licenses,
                stars_min=stars_min,
                stars_max=stars_max,
                created_after=created_after,
                created_before=created_before,
                pushed_after=pushed_after,
                pushed_before=pushed_before,
                discovered_after=discovered_after,
                discovered_before=discovered_before,
            )
            total_pages = max((total + page_size - 1) // page_size, 1)
            return {
                "repositories": [_serialize_repo(repository) for repository in repositories],
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                },
                "sort": {
                    "field": sort_by,
                    "order": sort_order,
                    "fields": list(REPO_SORT_FIELDS.keys()),
                },
                "filters": {
                    "name_query": name_query,
                    "language": language,
                    "languages": languages or ([] if language is None else [language]),
                    "licenses": licenses or [],
                    "stars_min": stars_min,
                    "stars_max": stars_max,
                    "created_after": created_after.isoformat() if created_after else None,
                    "created_before": created_before.isoformat() if created_before else None,
                    "pushed_after": pushed_after.isoformat() if pushed_after else None,
                    "pushed_before": pushed_before.isoformat() if pushed_before else None,
                    "discovered_after": discovered_after.isoformat() if discovered_after else None,
                    "discovered_before": discovered_before.isoformat() if discovered_before else None,
                },
            }
        finally:
            client.close()
            session.close()

    @app.get("/api/stage2/repos")
    def list_stage2_repositories(
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "latest_operation_at",
        sort_order: str = "desc",
        name_query: str | None = None,
        language: str | None = None,
        statuses: list[str] | None = Query(default=None),
        languages: list[str] | None = Query(default=None),
        licenses: list[str] | None = Query(default=None),
        stars_min: int | None = None,
        stars_max: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        pushed_after: datetime | None = None,
        pushed_before: datetime | None = None,
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be at least 1")
        if page_size < 1 or page_size > 200:
            raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")
        if sort_by not in STAGE2_REPO_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        if stars_min is not None and stars_max is not None and stars_min > stars_max:
            raise HTTPException(status_code=400, detail="stars_min must be less than or equal to stars_max")
        _validate_datetime_range(created_after, created_before, start_name="created_after", end_name="created_before")
        _validate_datetime_range(pushed_after, pushed_before, start_name="pushed_after", end_name="pushed_before")
        _validate_datetime_range(
            discovered_after,
            discovered_before,
            start_name="discovered_after",
            end_name="discovered_before",
        )

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            crawl_service = CrawlService(session, settings, client)
            stage2_service = Stage2Service(session)
            running_repository_ids = env_runner.list_running_repository_ids()
            offset = (page - 1) * page_size
            normalized_statuses = _normalize_stage2_status_filters(statuses)
            filter_kwargs = {
                "name_query": name_query,
                "language": language,
                "languages": languages,
                "licenses": licenses,
                "stars_min": stars_min,
                "stars_max": stars_max,
                "created_after": created_after,
                "created_before": created_before,
                "pushed_after": pushed_after,
                "pushed_before": pushed_before,
                "discovered_after": discovered_after,
                "discovered_before": discovered_before,
            }

            if normalized_statuses or sort_by == "latest_operation_at":
                conditions, _, _ = crawl_service.build_repository_listing_parts(
                    sort_by=sort_by if sort_by in REPO_SORT_FIELDS else "discovered_at",
                    sort_order=sort_order,
                    **filter_kwargs,
                )
                if normalized_statuses:
                    latest_runs, status_clause = stage2_service.build_status_filter_clause(
                        normalized_statuses,
                        running_repository_ids=running_repository_ids,
                    )
                    if status_clause is None:
                        rows = []
                        total = 0
                    else:
                        ordered, tie_breaker = _stage2_repository_ordering(sort_by, sort_order, latest_runs)
                        repository_stmt = (
                            select(GitHubRepository)
                            .outerjoin(latest_runs, latest_runs.c.repository_id == GitHubRepository.id)
                            .where(*conditions, status_clause)
                        )
                        repositories = list(
                            session.scalars(
                                repository_stmt
                                .order_by(ordered, tie_breaker)
                                .offset(offset)
                                .limit(page_size)
                            )
                        )
                        total = int(
                            session.scalar(
                                select(func.count(GitHubRepository.id))
                                .select_from(GitHubRepository)
                                .outerjoin(latest_runs, latest_runs.c.repository_id == GitHubRepository.id)
                                .where(*conditions, status_clause)
                            )
                            or 0
                        )
                        rows = stage2_service.list_repository_stage2_rows(
                            repositories,
                            running_repository_ids=running_repository_ids,
                        )
                else:
                    latest_runs = stage2_service.latest_run_listing_subquery()
                    ordered, tie_breaker = _stage2_repository_ordering(sort_by, sort_order, latest_runs)
                    repository_stmt = (
                        select(GitHubRepository)
                        .outerjoin(latest_runs, latest_runs.c.repository_id == GitHubRepository.id)
                        .where(*conditions)
                    )
                    repositories = list(
                        session.scalars(
                            repository_stmt
                            .order_by(ordered, tie_breaker)
                            .offset(offset)
                            .limit(page_size)
                        )
                    )
                    total = int(
                        session.scalar(
                            select(func.count(GitHubRepository.id))
                            .select_from(GitHubRepository)
                            .outerjoin(latest_runs, latest_runs.c.repository_id == GitHubRepository.id)
                            .where(*conditions)
                        )
                        or 0
                    )
                    rows = stage2_service.list_repository_stage2_rows(
                        repositories,
                        running_repository_ids=running_repository_ids,
                    )
            else:
                repositories, total = crawl_service.list_repositories(
                    limit=page_size,
                    offset=offset,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    **filter_kwargs,
                )
                rows = stage2_service.list_repository_stage2_rows(
                    repositories,
                    running_repository_ids=running_repository_ids,
                )

            total_pages = max((total + page_size - 1) // page_size, 1)
            return {
                "repositories": rows,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                },
            }
        finally:
            client.close()
            session.close()

    @app.get("/api/stage2/repos/{repository_id}")
    def get_stage2_repository(repository_id: int) -> dict[str, Any]:
        return load_stage2_repository_summary_payload(repository_id)

    @app.get("/api/stage2/repos/{repository_id}/runs/{run_id}")
    def get_stage2_run_detail(repository_id: int, run_id: str) -> dict[str, Any]:
        return load_stage2_run_detail_payload(repository_id, run_id)

    @app.patch("/api/stage2/repos/{repository_id}/runs/{run_id}/runtime")
    def update_stage2_run_runtime(
        repository_id: int,
        run_id: str,
        payload: UpdateStage2RuntimeRequest,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
                runtime_snapshot = _stage2_runtime_snapshot_from_request(
                    payload,
                    settings=settings,
                    base_snapshot=dict(run.runtime_snapshot_json or {}),
                )
                service.update_repository_run_runtime_snapshot(
                    repository_id,
                    run_id,
                    runtime_snapshot=runtime_snapshot,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage2RunRuntimeConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        return load_stage2_repository_detail_payload(repository_id, selected_run_id=run_id)

    @app.get("/api/stage3/repos")
    def list_stage3_repositories(
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "latest_operation_at",
        sort_order: str = "desc",
        include_prewarm: bool = False,
        name_query: str | None = None,
        language: str | None = None,
        statuses: list[str] | None = Query(default=None),
        languages: list[str] | None = Query(default=None),
        licenses: list[str] | None = Query(default=None),
        stars_min: int | None = None,
        stars_max: int | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        pushed_after: datetime | None = None,
        pushed_before: datetime | None = None,
        discovered_after: datetime | None = None,
        discovered_before: datetime | None = None,
    ) -> dict[str, Any]:
        if page < 1:
            raise HTTPException(status_code=400, detail="page must be at least 1")
        if page_size < 1 or page_size > 200:
            raise HTTPException(status_code=400, detail="page_size must be between 1 and 200")
        if sort_by not in STAGE3_REPO_SORT_FIELDS:
            raise HTTPException(status_code=400, detail=f"unsupported sort field: {sort_by}")
        if sort_order not in {"asc", "desc"}:
            raise HTTPException(status_code=400, detail="sort_order must be asc or desc")
        if stars_min is not None and stars_max is not None and stars_min > stars_max:
            raise HTTPException(status_code=400, detail="stars_min must be less than or equal to stars_max")
        _validate_datetime_range(created_after, created_before, start_name="created_after", end_name="created_before")
        _validate_datetime_range(pushed_after, pushed_before, start_name="pushed_after", end_name="pushed_before")
        _validate_datetime_range(
            discovered_after,
            discovered_before,
            start_name="discovered_after",
            end_name="discovered_before",
        )

        session = session_factory()
        client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
        try:
            crawl_service = CrawlService(session, settings, client)
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            ready_repo_ids = service.stage3_ready_repository_ids_stmt()
            normalized_statuses = _normalize_stage3_status_filters(statuses)
            filter_kwargs = {
                "name_query": name_query,
                "language": language,
                "languages": languages,
                "licenses": licenses,
                "stars_min": stars_min,
                "stars_max": stars_max,
                "created_after": created_after,
                "created_before": created_before,
                "pushed_after": pushed_after,
                "pushed_before": pushed_before,
                "discovered_after": discovered_after,
                "discovered_before": discovered_before,
            }
            conditions, _, _ = crawl_service.build_repository_listing_parts(
                sort_by=sort_by if sort_by in REPO_SORT_FIELDS else "discovered_at",
                sort_order=sort_order,
                **filter_kwargs,
            )
            repositories = list(
                session.scalars(
                    select(GitHubRepository)
                    .join(ready_repo_ids, ready_repo_ids.c.repository_id == GitHubRepository.id)
                    .where(*conditions)
                )
            )
            if repositories:
                service.ensure_materialized(repository_ids=[repository.id for repository in repositories])
                session.commit()
            rows = service.list_repository_stage3_rows(repositories)
            if normalized_statuses:
                rows = [
                    row
                    for row in rows
                    if str((row.get("stage3") or {}).get("status") or "") in normalized_statuses
                ]
            rows.sort(
                key=cmp_to_key(
                    lambda left, right: _compare_stage3_repository_rows(
                        left,
                        right,
                        sort_by=sort_by,
                        sort_order=sort_order,
                    )
                )
            )
            total = len(rows)
            offset = (page - 1) * page_size
            rows = rows[offset : offset + page_size]
            if include_prewarm:
                for row in rows:
                    repository_id = int(row.get("id") or 0)
                    latest_snapshot = dict((row.get("stage3") or {}).get("latest_snapshot") or {})
                    snapshot_id = str(latest_snapshot.get("id") or "").strip()
                    if repository_id <= 0 or not snapshot_id:
                        continue
                    try:
                        prewarm_payload = load_stage3_repository_image_prewarm_payload(
                            repository_id,
                            selected_snapshot_id=snapshot_id,
                            include_logs=False,
                        )
                    except HTTPException:
                        continue
                    row.setdefault("stage3", {})["image_prewarm"] = _summarize_stage3_repository_image_prewarm(
                        prewarm_payload
                    )
            total_pages = max((total + page_size - 1) // page_size, 1)
            return {
                "repositories": rows,
                "pagination": {
                    "page": page,
                    "page_size": page_size,
                    "total": total,
                    "total_pages": total_pages,
                },
            }
        finally:
            client.close()
            session.close()

    @app.get("/api/stage3/repos/{repository_id}")
    def get_stage3_repository(
        repository_id: int,
        selected_snapshot_id: str | None = None,
        selected_entry_file_id: str | None = None,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        return load_stage3_repository_detail_payload(
            repository_id,
            selected_snapshot_id=selected_snapshot_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=selected_run_id,
        )

    @app.get("/api/stage3/repos/{repository_id}/events")
    async def stream_stage3_repository_events(
        repository_id: int,
        request: Request,
        selected_snapshot_id: str | None = None,
        selected_entry_file_id: str | None = None,
        selected_run_id: str | None = None,
    ) -> StreamingResponse:
        initial_payload = load_stage3_repository_detail_payload(
            repository_id,
            selected_snapshot_id=selected_snapshot_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=selected_run_id,
        )
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: load_stage3_repository_detail_payload(
                    repository_id,
                    selected_snapshot_id=selected_snapshot_id,
                    selected_entry_file_id=selected_entry_file_id,
                    selected_run_id=selected_run_id,
                ),
                is_active=is_stage3_repository_payload_active,
                signature_of=_stage3_repository_detail_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.get("/api/stage3/repos/{repository_id}/image-prewarm")
    def get_stage3_repository_image_prewarm_status(
        repository_id: int,
        selected_snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        return load_stage3_repository_image_prewarm_payload(
            repository_id,
            selected_snapshot_id=selected_snapshot_id,
            include_logs=True,
        )

    @app.post("/api/stage3/repos/{repository_id}/image-prewarm")
    def prewarm_stage3_repository_images(
        repository_id: int,
        payload: PrewarmStage3SnapshotImagesRequest,
    ) -> dict[str, Any]:
        try:
            session = session_factory()
            try:
                service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
                snapshot = service.get_repository_snapshot(repository_id, snapshot_id=payload.selected_snapshot_id)
                session.commit()
            finally:
                session.close()

            started = stage3_image_prewarm_jobs.start(
                repository_id=repository_id,
                snapshot_id=snapshot.id,
                force=payload.force,
                callback=lambda hooks: build_stage3_snapshot_image_assets(
                    repository_id,
                    selected_snapshot_id=snapshot.id,
                    force=payload.force,
                    log_callback=hooks.log,
                    hooks=hooks,
                    cancel_requested=hooks.cancel_requested,
                ),
            )
            return {
                "repository_id": repository_id,
                "snapshot_id": snapshot.id,
                "started": started,
                "status": load_stage3_repository_image_prewarm_payload(
                    repository_id,
                    selected_snapshot_id=snapshot.id,
                    include_logs=True,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/stage3/repos/{repository_id}/image-prewarm/cancel")
    def cancel_stage3_repository_image_prewarm(
        repository_id: int,
        payload: PrewarmStage3SnapshotImagesRequest,
    ) -> dict[str, Any]:
        try:
            session = session_factory()
            try:
                service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
                snapshot = service.get_repository_snapshot(repository_id, snapshot_id=payload.selected_snapshot_id)
                session.commit()
            finally:
                session.close()

            cancelled = stage3_image_prewarm_jobs.cancel(snapshot.id)
            return {
                "repository_id": repository_id,
                "snapshot_id": snapshot.id,
                "cancelled": cancelled,
                "status": load_stage3_repository_image_prewarm_payload(
                    repository_id,
                    selected_snapshot_id=snapshot.id,
                    include_logs=True,
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.get("/api/stage3/repos/{repository_id}/runs/{run_id}")
    def get_stage3_run_detail(repository_id: int, run_id: str) -> dict[str, Any]:
        return load_stage3_run_detail_payload(repository_id, run_id)

    @app.patch("/api/stage3/repos/{repository_id}/runs/{run_id}/runtime")
    def update_stage3_run_runtime(
        repository_id: int,
        run_id: str,
        payload: UpdateStage3RuntimeRequest,
    ) -> dict[str, Any]:
        session = session_factory()
        selected_entry_file_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
                selected_entry_file_id = run.entry_file_id
                runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
                runtime_snapshot = _stage3_runtime_snapshot_from_request(
                    payload,
                    settings=settings,
                    base_snapshot=dict(runtime_stage3.get("runtime") or {}),
                )
                service.update_repository_run_runtime_snapshot(
                    repository_id,
                    run_id,
                    runtime_snapshot=runtime_snapshot,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=run_id,
        )

    @app.post("/api/stage3/repos/{repository_id}/entry-files/{entry_file_id}/runs")
    def create_stage3_run(
        repository_id: int,
        entry_file_id: str,
        auto_start: bool = False,
    ) -> dict[str, Any]:
        if auto_start:
            _require_stage3_breaker_agent_ready()
        session = session_factory()
        run_id: str | None = None
        selected_entry_file_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.create_run_for_entry_file(
                    repository_id,
                    entry_file_id,
                    prefer_latest_snapshot=True,
                )
                if auto_start:
                    run = service.queue_run(repository_id, run.id)
                run_id = run.id
                selected_entry_file_id = run.entry_file_id
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        if auto_start and run_id is not None:
            _schedule_stage3_run_or_raise(run_id, detail="failed to schedule the Stage3 breaker run")

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=run_id,
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/start")
    def start_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        _require_stage3_breaker_agent_ready()
        session = session_factory()
        selected_entry_file_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.queue_run(repository_id, run_id)
                selected_entry_file_id = run.entry_file_id
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()
        _schedule_stage3_run_or_raise(run_id, detail="failed to schedule the Stage3 breaker run")

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=run_id,
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/savepoints")
    def create_stage3_savepoint(repository_id: int, run_id: str) -> dict[str, Any]:
        del repository_id, run_id
        raise HTTPException(
            status_code=409,
            detail="Stage3 savepoints are created by the breaker agent save tool",
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/interrupt")
    def interrupt_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        if env_stage3_runner.interrupt_run(run_id):
            return load_stage3_repository_detail_payload(
                repository_id,
                selected_run_id=run_id,
            )
        session = session_factory()
        selected_entry_file_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.interrupt_run(repository_id, run_id)
                selected_entry_file_id = run.entry_file_id
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=run_id,
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/complete")
    def complete_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        del repository_id, run_id
        raise HTTPException(
            status_code=409,
            detail="Stage3 runs are completed automatically when the breaker agent exits",
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/resume")
    def resume_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        if not settings.stage3_enable_checkpoints:
            raise HTTPException(status_code=409, detail="stage3 checkpoint resume is disabled")
        _require_stage3_breaker_agent_ready()
        session = session_factory()
        selected_entry_file_id: str | None = None
        new_run_id: str | None = None
        resume_cleanup_archive: dict[str, Any] | None = None
        commit_error: Exception | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.resume_run(repository_id, run_id)
                selected_entry_file_id = run.entry_file_id
                new_run_id = run.id
                resume_cleanup_archive = service.prepared_run_cleanup_archive(
                    repository_id=repository_id,
                    run=run,
                )
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            try:
                session.commit()
            except Exception as exc:  # noqa: BLE001
                commit_error = exc
                session.rollback()
        finally:
            session.close()
        if commit_error is not None:
            if resume_cleanup_archive is not None:
                _cleanup_stage3_run_archive_and_tombstone_on_failure(
                    resume_cleanup_archive,
                    reason="resume_commit_failed",
                )
            raise commit_error
        if new_run_id:
            _schedule_stage3_run_or_raise(new_run_id, detail="failed to schedule the resumed Stage3 breaker run")

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=new_run_id,
        )

    @app.post("/api/stage3/repos/{repository_id}/runs/{run_id}/rerun")
    def rerun_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        _require_stage3_breaker_agent_ready()
        session = session_factory()
        selected_entry_file_id: str | None = None
        new_run_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.rerun_run(repository_id, run_id)
                selected_entry_file_id = run.entry_file_id
                new_run_id = run.id
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()
        if new_run_id:
            _schedule_stage3_run_or_raise(new_run_id, detail="failed to schedule the rerun Stage3 breaker run")

        return load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
            selected_run_id=new_run_id,
        )

    @app.delete("/api/stage3/repos/{repository_id}/runs/{run_id}")
    def delete_stage3_run(repository_id: int, run_id: str) -> dict[str, Any]:
        session = session_factory()
        selected_entry_file_id: str | None = None
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
                selected_entry_file_id = run.entry_file_id
                archive = service.delete_repository_run(repository_id, run_id)
            except ValueError as exc:
                session.rollback()
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage3RunConflictError as exc:
                session.rollback()
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        cleanup_result = _cleanup_stage3_run_archive_and_record_tombstone(archive)
        failed_paths = list(cleanup_result.get("failed_paths") or [])
        failed_checkpoint_images = list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])
        if failed_paths or failed_checkpoint_images:
            print(
                "[feature_factory] deleted stage3 run record but some assets could not be removed: "
                f"run_id={run_id}, "
                f"failed_paths={failed_paths}, "
                f"failed_checkpoint_docker_image_refs={failed_checkpoint_images}",
                flush=True,
            )

        payload = load_stage3_repository_detail_payload(
            repository_id,
            selected_entry_file_id=selected_entry_file_id,
        )
        payload["deleted_run_id"] = run_id
        if failed_paths or failed_checkpoint_images:
            payload["cleanup_warnings"] = {
                "message": "stage3 run record was deleted, but some assets could not be removed",
                "failed_paths": failed_paths,
                "failed_checkpoint_docker_image_refs": failed_checkpoint_images,
            }
        return payload

    @app.get("/api/stage3/repos/{repository_id}/runs/{run_id}/llm-completions/{role}.zip")
    def download_stage3_llm_completion_archive(
        repository_id: int,
        run_id: str,
        role: Literal["breaker"],
    ) -> FileResponse:
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
                repository = service.get_repository(repository_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage3 runtime directory is unknown for this run")

            archive_dir = _stage3_llm_completion_archive_dir(runtime_dir, role)
            zip_path, filename = _build_stage3_llm_completion_zip(
                repository_full_name=repository.full_name,
                run_id=run.id,
                role=role,
                archive_dir=archive_dir,
            )
            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename=filename,
                background=BackgroundTask(lambda path=zip_path: Path(path).unlink(missing_ok=True)),
            )
        finally:
            session.close()

    @app.get("/api/stage3/repos/{repository_id}/runs/{run_id}/llm-completions/{role}/files")
    def get_stage3_llm_completion_file(
        repository_id: int,
        run_id: str,
        role: Literal["breaker"],
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage3Service(session, workspace_root=settings.stage3_workspace_dir, settings=settings)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage3 runtime directory is unknown for this run")

            completion_file = _resolve_stage3_llm_completion_file(
                runtime_dir=runtime_dir,
                role=role,
                requested_path=path,
            )
            raw_text = completion_file.read_text(encoding="utf-8")
            stat = completion_file.stat()
            try:
                value = json.loads(raw_text)
            except json.JSONDecodeError:
                content_kind = "text"
                value = None
            else:
                content_kind = "json"

            return {
                "path": path,
                "filename": completion_file.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "kind": content_kind,
                "value": value,
                "text": raw_text if content_kind == "text" else None,
            }
        finally:
            session.close()

    @app.get("/api/stage2/repos/{repository_id}/runs/{run_id}/llm-completions/{role}.zip")
    def download_stage2_llm_completion_archive(
        repository_id: int,
        run_id: str,
        role: Literal["planner", "worker"],
    ) -> FileResponse:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
                repository = service.get_repository(repository_id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage2 runtime directory is unknown for this run")

            archive_dir = _stage2_llm_completion_archive_dir(runtime_dir, role)

            zip_path, filename = _build_stage2_llm_completion_zip(
                repository_full_name=repository.full_name,
                run_id=run.id,
                role=role,
                archive_dir=archive_dir,
            )
            return FileResponse(
                zip_path,
                media_type="application/zip",
                filename=filename,
                background=BackgroundTask(lambda path=zip_path: Path(path).unlink(missing_ok=True)),
            )
        finally:
            session.close()

    @app.get("/api/stage2/repos/{repository_id}/runs/{run_id}/llm-completions/{role}/files")
    def get_stage2_llm_completion_file(
        repository_id: int,
        run_id: str,
        role: Literal["planner", "worker"],
        path: str = Query(..., min_length=1),
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                run = service.get_repository_run(repository_id, run_id, include_detail=False)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc

            runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
            if runtime_dir is None:
                raise HTTPException(status_code=404, detail="stage2 runtime directory is unknown for this run")

            completion_file = _resolve_stage2_llm_completion_file(
                runtime_dir=runtime_dir,
                role=role,
                requested_path=path,
            )
            raw_text = completion_file.read_text(encoding="utf-8")
            stat = completion_file.stat()
            try:
                value = json.loads(raw_text)
            except json.JSONDecodeError:
                content_kind = "text"
                value = None
            else:
                content_kind = "json"

            return {
                "path": path,
                "filename": completion_file.name,
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                "kind": content_kind,
                "value": value,
                "text": raw_text if content_kind == "text" else None,
            }
        finally:
            session.close()

    @app.delete("/api/stage2/repos/{repository_id}/runs/{run_id}")
    def delete_stage2_run_archive(
        repository_id: int,
        run_id: str,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                archive = service.delete_repository_run(repository_id, run_id)
            except Stage2RunDeleteConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        cleanup_result = _cleanup_stage2_run_archive_and_record_tombstone(archive)
        failed_paths = list(cleanup_result.get("failed_paths") or [])
        failed_checkpoint_images = list(cleanup_result.get("failed_checkpoint_docker_image_refs") or [])
        failed_validator_images = list(cleanup_result.get("failed_validator_image_refs") or [])
        if failed_paths or failed_checkpoint_images or failed_validator_images:
            print(
                "[feature_factory] deleted stage2 run record but some assets could not be removed: "
                f"run_id={run_id}, "
                f"failed_paths={failed_paths}, "
                f"failed_checkpoint_docker_image_refs={failed_checkpoint_images}, "
                f"failed_validator_image_refs={failed_validator_images}",
                flush=True,
            )

        payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=selected_run_id)
        payload["deleted_run_id"] = run_id
        if failed_paths or failed_checkpoint_images or failed_validator_images:
            payload["cleanup_warnings"] = {
                "message": "stage2 run record was deleted, but some assets could not be removed",
                "failed_paths": failed_paths,
                "failed_checkpoint_docker_image_refs": failed_checkpoint_images,
                "failed_validator_image_refs": failed_validator_images,
            }
        return payload

    @app.post("/api/stage2/repos/{repository_id}/runs/{run_id}/interrupt")
    def interrupt_stage2_run(
        repository_id: int,
        run_id: str,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                service.request_run_interrupt(repository_id, run_id)
            except Stage2RunInterruptConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        accepted = env_runner.interrupt_run(run_id)
        if not accepted:
            fallback_session = session_factory()
            try:
                fallback_service = Stage2Service(fallback_session)
                fallback_service.mark_run_interrupted(
                    run_id,
                    error_message="stage2 run interrupted by user before runner could find an active task",
                )
                fallback_session.commit()
            except Exception:
                fallback_session.rollback()
                raise HTTPException(status_code=409, detail="stage2 run is not currently interruptible")
            finally:
                fallback_session.close()

        payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=selected_run_id or run_id)
        payload["interrupted_run_id"] = run_id
        return payload

    @app.post("/api/stage2/repos/{repository_id}/runs/{run_id}/rerun")
    def rerun_stage2_run_commit(repository_id: int, run_id: str) -> dict[str, Any]:
        if env_runner.is_repository_running(repository_id):
            raise HTTPException(status_code=409, detail="stage2 run is already in progress for this repository")
        runtime_snapshot = _stage2_runtime_snapshot_from_request(UpdateStage2RuntimeRequest(), settings=settings)
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                new_run = service.create_run_from_existing_commit(
                    repository_id,
                    run_id,
                    runtime_snapshot=runtime_snapshot,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage2CommitUnchangedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        _schedule_stage2_run_or_raise(
            new_run.id,
            detail="failed to schedule stage2 run",
        )

        payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=new_run.id)
        payload["created_run_id"] = new_run.id
        payload["source_run_id"] = run_id
        return payload

    @app.post("/api/stage2/repos/{repository_id}/runs/{run_id}/rerun-worker")
    def rerun_stage2_worker_only(repository_id: int, run_id: str) -> dict[str, Any]:
        if env_runner.is_repository_running(repository_id):
            raise HTTPException(status_code=409, detail="stage2 run is already in progress for this repository")
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                new_run = service.create_run_from_existing_worker_plan(
                    repository_id,
                    run_id,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage2WorkerRerunConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Stage2CommitUnchangedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        _schedule_stage2_run_or_raise(
            new_run.id,
            detail="failed to schedule stage2 worker rerun",
        )

        payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=new_run.id)
        payload["created_run_id"] = new_run.id
        payload["source_run_id"] = run_id
        return payload

    @app.post("/api/stage2/repos/{repository_id}/runs/{run_id}/resume-worker")
    def resume_stage2_worker(repository_id: int, run_id: str) -> dict[str, Any]:
        if not settings.stage2_enable_checkpoints:
            raise HTTPException(status_code=409, detail="stage2 checkpoint resume is disabled")
        if env_runner.is_repository_running(repository_id):
            raise HTTPException(status_code=409, detail="stage2 run is already in progress for this repository")
        resume_checkpoint_cleanup: dict[str, Any] = {}
        new_run_id = ""
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                new_run = service.create_run_from_worker_checkpoint(
                    repository_id,
                    run_id,
                    stage2_workspace_dir=settings.stage2_workspace_dir,
                )
                new_run_id = new_run.id
                resume_checkpoint_cleanup = service.worker_checkpoint_cleanup_targets(new_run.id)
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage2WorkerResumeConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            except Stage2CommitUnchangedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        except Exception:
            session.rollback()
            if resume_checkpoint_cleanup:
                cleanup_result = _cleanup_stage2_worker_checkpoint_asset_targets(
                    resume_checkpoint_cleanup
                )
                failed_paths = list(cleanup_result.get("failed_paths") or [])
                failed_image_refs = list(
                    cleanup_result.get("failed_checkpoint_docker_image_refs") or []
                )
                if failed_paths or failed_image_refs:
                    print(
                        "[feature_factory] failed to cleanup cloned worker checkpoint after "
                        f"resume commit failure: run_id={new_run_id}, "
                        f"failed_paths={failed_paths}, "
                        f"failed_checkpoint_docker_image_refs={failed_image_refs}",
                        flush=True,
                    )
            raise
        finally:
            session.close()

        _schedule_stage2_run_or_raise(
            new_run.id,
            detail="failed to schedule stage2 resume",
        )

        payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=new_run.id)
        payload["created_run_id"] = new_run.id
        payload["source_run_id"] = run_id
        return payload

    @app.get("/api/stage2/repos/{repository_id}/events")
    async def stream_stage2_repository_events(
        repository_id: int,
        request: Request,
        selected_run_id: str | None = None,
    ) -> StreamingResponse:
        initial_payload = load_stage2_repository_detail_payload(repository_id, selected_run_id=selected_run_id)
        return StreamingResponse(
            stream_detail_events(
                request,
                initial_payload=initial_payload,
                fetch_payload=lambda: load_stage2_repository_detail_payload(
                    repository_id,
                    selected_run_id=selected_run_id,
                ),
                is_active=is_stage2_repository_payload_active,
                signature_of=_stage2_repository_detail_signature,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/stage2/repos/{repository_id}/runs")
    def create_stage2_run(repository_id: int) -> dict[str, Any]:
        if env_runner.is_repository_running(repository_id):
            raise HTTPException(status_code=409, detail="stage2 run is already in progress for this repository")
        runtime_snapshot = _stage2_runtime_snapshot_from_request(UpdateStage2RuntimeRequest(), settings=settings)
        session = session_factory()
        try:
            service = Stage2Service(session)
            try:
                trigger_kind = "rerun" if service.latest_run_for_repository(repository_id) is not None else "manual"
                run = service.create_run(
                    repository_id,
                    trigger_kind=trigger_kind,
                    commit_resolver=resolve_repo_head_commit,
                    runtime_snapshot=runtime_snapshot,
                )
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except Stage2CommitResolutionError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            except Stage2CommitUnchangedError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            session.commit()
        finally:
            session.close()

        _schedule_stage2_run_or_raise(
            run.id,
            detail="failed to schedule stage2 run",
        )

        detail_session = session_factory()
        try:
            detail_service = Stage2Service(detail_session)
            return detail_service.repository_summary_payload(
                repository_id,
                running_repository_ids=env_runner.list_running_repository_ids(),
            )
        finally:
            detail_session.close()

    return app
