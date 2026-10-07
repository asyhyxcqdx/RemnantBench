from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from feature_factory.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class CrawlJobStatus(str, enum.Enum):
    queued = "queued"
    pending = "pending"
    planning = "planning"
    running = "running"
    paused = "paused"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class CrawlPartitionStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    skipped = "skipped"
    overflow = "overflow"
    failed = "failed"


class CrawlPartitionQueryStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class Stage2RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"


class Stage2RunResult(str, enum.Enum):
    unknown = "unknown"
    passed = "passed"
    abandoned = "abandoned"
    defect = "defect"
    failed = "failed"


class Stage3RunStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    completed = "completed"


class Stage3RunResult(str, enum.Enum):
    unknown = "unknown"
    archived = "archived"
    interrupted = "interrupted"
    failed = "failed"


class Stage4RunStatus(str, enum.Enum):
    pending = "pending"
    queued = "queued"
    running = "running"
    completed = "completed"


class Stage4RunResult(str, enum.Enum):
    unknown = "unknown"
    generated = "generated"
    interrupted = "interrupted"
    failed = "failed"


class BatchTaskStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class Stage1CrawlTemplate(Base):
    __tablename__ = "stage1_crawl_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_stage1_crawl_templates_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage2RuntimeTemplate(Base):
    __tablename__ = "stage2_runtime_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_stage2_runtime_templates_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage3RuntimeTemplate(Base):
    __tablename__ = "stage3_runtime_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_stage3_runtime_templates_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage4RuntimeTemplate(Base):
    __tablename__ = "stage4_runtime_templates"
    __table_args__ = (
        UniqueConstraint("name", name="uq_stage4_runtime_templates_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage2CleanupTombstone(Base):
    __tablename__ = "stage2_cleanup_tombstones"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    reason: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    archive_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage3CleanupTombstone(Base):
    __tablename__ = "stage3_cleanup_tombstones"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    repository_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    reason: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    archive_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Stage4CleanupTombstone(Base):
    __tablename__ = "stage4_cleanup_tombstones"

    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_savepoint_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    repository_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    reason: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    archive_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class CrawlJob(Base):
    __tablename__ = "crawl_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default=CrawlJobStatus.pending.value, index=True)
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    partitions: Mapped[list["CrawlPartition"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    discoveries: Mapped[list["RepoDiscovery"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class CrawlPartition(Base):
    __tablename__ = "crawl_partitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=CrawlPartitionStatus.pending.value, index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    range_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    query_string: Mapped[str] = mapped_column(Text)
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0)
    unique_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    job: Mapped["CrawlJob"] = relationship(back_populates="partitions")
    discoveries: Mapped[list["RepoDiscovery"]] = relationship(back_populates="partition", cascade="all, delete-orphan")
    query_executions: Mapped[list["CrawlPartitionQueryExecution"]] = relationship(
        back_populates="partition",
        cascade="all, delete-orphan",
        order_by="CrawlPartitionQueryExecution.query_index",
    )


class CrawlPartitionQueryExecution(Base):
    __tablename__ = "crawl_partition_query_executions"
    __table_args__ = (
        UniqueConstraint("partition_id", "query_index", name="uq_partition_query_executions_partition_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    partition_id: Mapped[str] = mapped_column(ForeignKey("crawl_partitions.id"), index=True)
    query_index: Mapped[int] = mapped_column(Integer)
    query_string: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=CrawlPartitionQueryStatus.pending.value, index=True)
    reported_total_count: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    current_page: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    total_pages: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    fetched_count: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    unique_count: Mapped[int | None] = mapped_column(Integer, default=None, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    partition: Mapped["CrawlPartition"] = relationship(back_populates="query_executions")


class GitHubRepository(Base):
    __tablename__ = "github_repositories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    node_id: Mapped[str | None] = mapped_column(String(255), default=None)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    owner_login: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(255))
    html_url: Mapped[str] = mapped_column(Text)
    api_url: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    homepage: Mapped[str | None] = mapped_column(Text, default=None)
    default_branch: Mapped[str | None] = mapped_column(String(255), default=None)
    primary_language: Mapped[str | None] = mapped_column(String(128), default=None)
    license_key: Mapped[str | None] = mapped_column(String(128), default=None)
    visibility: Mapped[str | None] = mapped_column(String(32), default=None)
    is_private: Mapped[bool] = mapped_column(default=False)
    is_fork: Mapped[bool] = mapped_column(default=False)
    is_archived: Mapped[bool] = mapped_column(default=False)
    stargazers_count: Mapped[int] = mapped_column(Integer, default=0)
    forks_count: Mapped[int] = mapped_column(Integer, default=0)
    open_issues_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at_github: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    updated_at_github: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    pushed_at_github: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    discoveries: Mapped[list["RepoDiscovery"]] = relationship(back_populates="repository")
    stage2_runs: Mapped[list["Stage2Run"]] = relationship(back_populates="repository", cascade="all, delete-orphan")
    stage3_commit_snapshots: Mapped[list["Stage3CommitSnapshot"]] = relationship(
        back_populates="repository",
        cascade="all, delete-orphan",
    )


class RepoDiscovery(Base):
    __tablename__ = "repo_discoveries"
    __table_args__ = (
        UniqueConstraint("job_id", "repository_id", name="uq_repo_discoveries_job_repository"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("crawl_jobs.id"), index=True)
    partition_id: Mapped[str] = mapped_column(ForeignKey("crawl_partitions.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"), index=True)
    query_string: Mapped[str] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    job: Mapped["CrawlJob"] = relationship(back_populates="discoveries")
    partition: Mapped["CrawlPartition"] = relationship(back_populates="discoveries")
    repository: Mapped["GitHubRepository"] = relationship(back_populates="discoveries")


class Stage2Run(Base):
    __tablename__ = "stage2_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=Stage2RunStatus.queued.value, index=True)
    result: Mapped[str] = mapped_column(String(32), default=Stage2RunResult.unknown.value, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(32), default="manual")
    phase: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    target_branch: Mapped[str | None] = mapped_column(String(255), default=None)
    target_commit_sha: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    base_image: Mapped[str | None] = mapped_column(String(255), default=None)
    planner_model: Mapped[str | None] = mapped_column(String(255), default=None)
    planner_token_usage: Mapped[int] = mapped_column(Integer, default=0)
    worker_model: Mapped[str | None] = mapped_column(String(255), default=None)
    worker_token_usage: Mapped[int] = mapped_column(Integer, default=0)
    planner_guidance: Mapped[str | None] = mapped_column(Text, default=None)
    dockerfile_text: Mapped[str | None] = mapped_column(Text, default=None)
    run_script_text: Mapped[str | None] = mapped_column(Text, default=None)
    workspace_path: Mapped[str | None] = mapped_column(Text, default=None)
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    collect_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    smoke_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    full_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    repository: Mapped["GitHubRepository"] = relationship(back_populates="stage2_runs")
    events: Mapped[list["Stage2RunEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage2RunEvent.id.asc()",
    )
    test_results: Mapped[list["Stage2TestResult"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage2TestResult.test_file_path.asc()",
    )
    validation_attempts: Mapped[list["Stage2ValidationAttempt"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage2ValidationAttempt.attempt_index.asc()",
    )


class Stage2ValidationAttempt(Base):
    __tablename__ = "stage2_validation_attempts"
    __table_args__ = (
        UniqueConstraint("run_id", "attempt_index", name="uq_stage2_validation_attempts_run_attempt"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage2_runs.id"), index=True)
    attempt_index: Mapped[int] = mapped_column(Integer)
    dockerfile_text: Mapped[str | None] = mapped_column(Text, default=None)
    run_script_text: Mapped[str | None] = mapped_column(Text, default=None)
    collect_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    smoke_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    full_report_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    version_finalized: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    validator_status: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    run: Mapped["Stage2Run"] = relationship(back_populates="validation_attempts")


class Stage2RunEvent(Base):
    __tablename__ = "stage2_run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage2_runs.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    phase: Mapped[str | None] = mapped_column(String(64), default=None)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage2Run"] = relationship(back_populates="events")


class Stage2TestResult(Base):
    __tablename__ = "stage2_test_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage2_runs.id"), index=True)
    test_file_path: Mapped[str] = mapped_column(Text, index=True)
    target_selector: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), index=True)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    error_tests: Mapped[int] = mapped_column(Integer, default=0)
    skipped_tests: Mapped[int] = mapped_column(Integer, default=0)
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    raw_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage2Run"] = relationship(back_populates="test_results")


class Stage3CommitSnapshot(Base):
    __tablename__ = "stage3_commit_snapshots"
    __table_args__ = (
        UniqueConstraint("source_stage2_run_id", name="uq_stage3_commit_snapshots_source_stage2_run"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"), index=True)
    source_stage2_run_id: Mapped[str] = mapped_column(String(36), index=True)
    source_commit_sha: Mapped[str] = mapped_column(String(64), index=True)
    target_branch: Mapped[str | None] = mapped_column(String(255), default=None)
    base_image: Mapped[str | None] = mapped_column(String(255), default=None)
    planner_guidance: Mapped[str | None] = mapped_column(Text, default=None)
    dockerfile_text: Mapped[str | None] = mapped_column(Text, default=None)
    run_script_text: Mapped[str | None] = mapped_column(Text, default=None)
    original_p2p_files_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    source_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    repository: Mapped["GitHubRepository"] = relationship(back_populates="stage3_commit_snapshots")
    entry_files: Mapped[list["Stage3EntryFile"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="Stage3EntryFile.test_file_path.asc()",
    )


class Stage3EntryFile(Base):
    __tablename__ = "stage3_entry_files"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "test_file_path", name="uq_stage3_entry_files_snapshot_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("stage3_commit_snapshots.id"), index=True)
    test_file_path: Mapped[str] = mapped_column(Text, index=True)
    target_selector: Mapped[str | None] = mapped_column(Text, default=None)
    baseline_total_tests: Mapped[int] = mapped_column(Integer, default=0)
    baseline_passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    baseline_failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    baseline_error_tests: Mapped[int] = mapped_column(Integer, default=0)
    baseline_skipped_tests: Mapped[int] = mapped_column(Integer, default=0)
    baseline_pass_rate: Mapped[float] = mapped_column(default=0.0)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    snapshot: Mapped["Stage3CommitSnapshot"] = relationship(back_populates="entry_files")
    runs: Mapped[list["Stage3Run"]] = relationship(
        back_populates="entry_file",
        cascade="all, delete-orphan",
        order_by="Stage3Run.created_at.desc()",
    )


class Stage3Run(Base):
    __tablename__ = "stage3_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entry_file_id: Mapped[str] = mapped_column(ForeignKey("stage3_entry_files.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=Stage3RunStatus.queued.value, index=True)
    result: Mapped[str] = mapped_column(String(32), default=Stage3RunResult.unknown.value, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(32), default="manual")
    phase: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    workspace_path: Mapped[str | None] = mapped_column(Text, default=None)
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    entry_file: Mapped["Stage3EntryFile"] = relationship(back_populates="runs")
    events: Mapped[list["Stage3RunEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage3RunEvent.id.asc()",
    )
    savepoints: Mapped[list["Stage3Savepoint"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage3Savepoint.depth.asc()",
    )


class Stage3RunEvent(Base):
    __tablename__ = "stage3_run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage3_runs.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    phase: Mapped[str | None] = mapped_column(String(64), default=None)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage3Run"] = relationship(back_populates="events")


class Stage3Savepoint(Base):
    __tablename__ = "stage3_savepoints"
    __table_args__ = (
        UniqueConstraint("run_id", "depth", name="uq_stage3_savepoints_run_depth"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage3_runs.id"), index=True)
    depth: Mapped[int] = mapped_column(Integer)
    entry_pass_rate: Mapped[float | None] = mapped_column(default=None)
    p2p_files_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    f2p_files_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    collateral_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    gold_patch_text: Mapped[str | None] = mapped_column(Text, default=None)
    checkpoint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    run: Mapped["Stage3Run"] = relationship(back_populates="savepoints")
    file_results: Mapped[list["Stage3SavepointFileResult"]] = relationship(
        back_populates="savepoint",
        cascade="all, delete-orphan",
        order_by="Stage3SavepointFileResult.test_file_path.asc()",
    )
    stage4_runs: Mapped[list["Stage4Run"]] = relationship(
        back_populates="source_savepoint",
        cascade="all, delete-orphan",
        order_by="Stage4Run.created_at.desc()",
    )


class Stage3SavepointFileResult(Base):
    __tablename__ = "stage3_savepoint_file_results"
    __table_args__ = (
        UniqueConstraint("savepoint_id", "test_file_path", name="uq_stage3_savepoint_file_results_savepoint_path"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    savepoint_id: Mapped[int] = mapped_column(ForeignKey("stage3_savepoints.id"), index=True)
    test_file_path: Mapped[str] = mapped_column(Text, index=True)
    target_selector: Mapped[str | None] = mapped_column(Text, default=None)
    is_entry_file: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(32), index=True)
    total_tests: Mapped[int] = mapped_column(Integer, default=0)
    passed_tests: Mapped[int] = mapped_column(Integer, default=0)
    failed_tests: Mapped[int] = mapped_column(Integer, default=0)
    error_tests: Mapped[int] = mapped_column(Integer, default=0)
    skipped_tests: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[float] = mapped_column(default=0.0)
    raw_result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    savepoint: Mapped["Stage3Savepoint"] = relationship(back_populates="file_results")


class Stage4Run(Base):
    __tablename__ = "stage4_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_savepoint_id: Mapped[int] = mapped_column(ForeignKey("stage3_savepoints.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default=Stage4RunStatus.pending.value, index=True)
    result: Mapped[str] = mapped_column(String(32), default=Stage4RunResult.unknown.value, index=True)
    trigger_kind: Mapped[str] = mapped_column(String(32), default="manual")
    phase: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    workspace_path: Mapped[str | None] = mapped_column(Text, default=None)
    issue_variant_count: Mapped[int] = mapped_column(Integer, default=3)
    # Retained only so existing databases can be upgraded without dropping legacy rows.
    hint_variant_count: Mapped[int] = mapped_column(Integer, default=0)
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    source_savepoint: Mapped["Stage3Savepoint"] = relationship(back_populates="stage4_runs")
    events: Mapped[list["Stage4RunEvent"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage4RunEvent.id.asc()",
    )
    issue_variants: Mapped[list["Stage4IssueVariant"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage4IssueVariant.variant_index.asc()",
    )
    hint_variants: Mapped[list["Stage4HintVariant"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Stage4HintVariant.variant_index.asc()",
    )


class Stage4RunEvent(Base):
    __tablename__ = "stage4_run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage4_runs.id"), index=True)
    actor: Mapped[str] = mapped_column(String(64), index=True)
    phase: Mapped[str | None] = mapped_column(String(64), default=None)
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage4Run"] = relationship(back_populates="events")


class Stage4IssueVariant(Base):
    __tablename__ = "stage4_issue_variants"
    __table_args__ = (
        UniqueConstraint("run_id", "variant_index", name="uq_stage4_issue_variants_run_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage4_runs.id"), index=True)
    variant_index: Mapped[int] = mapped_column(Integer)
    style: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(Text)
    issue_markdown: Mapped[str] = mapped_column(Text)
    issue_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    leakage_check_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage4Run"] = relationship(back_populates="issue_variants")


class Stage4HintVariant(Base):
    __tablename__ = "stage4_hint_variants"
    __table_args__ = (
        UniqueConstraint("run_id", "variant_index", name="uq_stage4_hint_variants_run_index"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("stage4_runs.id"), index=True)
    variant_index: Mapped[int] = mapped_column(Integer)
    strength: Mapped[str] = mapped_column(String(64), index=True)
    hint_markdown: Mapped[str] = mapped_column(Text)
    hint_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    leakage_check_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped["Stage4Run"] = relationship(back_populates="hint_variants")


class DataPool(Base):
    __tablename__ = "data_pools"
    __table_args__ = (
        UniqueConstraint("name", name="uq_data_pools_name"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    root_path: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    assets: Mapped[list["DataPoolAsset"]] = relationship(
        back_populates="pool",
        cascade="all, delete-orphan",
        order_by="DataPoolAsset.created_at.desc()",
    )
    batch_tasks: Mapped[list["BatchTask"]] = relationship(back_populates="pool")


class DataPoolAsset(Base):
    __tablename__ = "data_pool_assets"
    __table_args__ = (
        UniqueConstraint(
            "pool_id",
            "github_repo_id",
            "entry_file_path",
            "depth",
            name="uq_data_pool_assets_pool_repo_entry_depth",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    pool_id: Mapped[str] = mapped_column(ForeignKey("data_pools.id"), index=True)
    repository_id: Mapped[int | None] = mapped_column(ForeignKey("github_repositories.id"), index=True, default=None)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    source_commit_sha: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    language: Mapped[str | None] = mapped_column(String(128), index=True, default=None)
    stars: Mapped[int] = mapped_column(Integer, default=0, index=True)
    entry_file_path: Mapped[str] = mapped_column(Text, index=True)
    depth: Mapped[int] = mapped_column(Integer, index=True)
    stage2_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stage3_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stage3_savepoint_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    stage4_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    folder_name: Mapped[str] = mapped_column(Text)
    folder_path: Mapped[str] = mapped_column(Text)
    manifest_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pool: Mapped["DataPool"] = relationship(back_populates="assets")
    repository: Mapped["GitHubRepository | None"] = relationship()


class Stage4DataPoolBackfillJob(Base):
    __tablename__ = "stage4_data_pool_backfill_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_pool_id: Mapped[str] = mapped_column(ForeignKey("data_pools.id"), index=True)
    target_pool_id: Mapped[str] = mapped_column(ForeignKey("data_pools.id"), index=True)
    runtime_template_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    runtime_template_name: Mapped[str] = mapped_column(String(255))
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    styles_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    inventory_sha256: Mapped[str] = mapped_column(String(64), index=True)
    eligible_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    lease_owner: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    lease_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    units: Mapped[list["Stage4DataPoolBackfillUnit"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Stage4DataPoolBackfillUnit.position.asc()",
    )


class Stage4DataPoolBackfillUnit(Base):
    __tablename__ = "stage4_data_pool_backfill_units"
    __table_args__ = (
        UniqueConstraint("job_id", "source_asset_id", name="uq_stage4_backfill_units_job_source_asset"),
        UniqueConstraint(
            "job_id",
            "github_repo_id",
            "entry_file_path",
            "depth",
            name="uq_stage4_backfill_units_job_grain",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(ForeignKey("stage4_data_pool_backfill_jobs.id"), index=True)
    position: Mapped[int] = mapped_column(Integer, index=True)
    source_asset_id: Mapped[str] = mapped_column(ForeignKey("data_pool_assets.id"), index=True)
    source_stage4_run_id: Mapped[str] = mapped_column(String(36), index=True)
    source_savepoint_id: Mapped[int] = mapped_column(Integer, index=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, index=True)
    entry_file_path: Mapped[str] = mapped_column(Text, index=True)
    depth: Mapped[int] = mapped_column(Integer, index=True)
    source_folder_name: Mapped[str] = mapped_column(Text)
    source_folder_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    stage4_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    target_asset_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    verification_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    job: Mapped["Stage4DataPoolBackfillJob"] = relationship(back_populates="units")


class DataPoolDownloadSelection(Base):
    __tablename__ = "data_pool_download_selections"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    pool_id: Mapped[str] = mapped_column(ForeignKey("data_pools.id"), index=True)
    asset_ids_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    asset_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    pool: Mapped["DataPool"] = relationship()


class GlobalRuntimeConfig(Base):
    __tablename__ = "global_runtime_configs"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class BatchTask(Base):
    __tablename__ = "batch_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), index=True)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    data_pool_id: Mapped[str] = mapped_column(ForeignKey("data_pools.id"), index=True)
    stage1_crawl_job_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    status: Mapped[str] = mapped_column(String(32), default=BatchTaskStatus.pending.value, index=True)
    phase: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    token_source: Mapped[str] = mapped_column(String(32), default="global")
    filters_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    runtime_snapshot_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    pool: Mapped["DataPool"] = relationship(back_populates="batch_tasks")
    repositories: Mapped[list["BatchTaskRepository"]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="BatchTaskRepository.repo_full_name.asc()",
    )


class BatchTaskRepository(Base):
    __tablename__ = "batch_task_repositories"
    __table_args__ = (
        UniqueConstraint("task_id", "repository_id", name="uq_batch_task_repositories_task_repo"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("batch_tasks.id"), index=True)
    repository_id: Mapped[int] = mapped_column(ForeignKey("github_repositories.id"), index=True)
    github_repo_id: Mapped[int] = mapped_column(BigInteger, index=True)
    repo_full_name: Mapped[str] = mapped_column(String(255), index=True)
    language: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage2_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage3_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage4_status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage2_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    source_stage2_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stage3_snapshot_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    task: Mapped["BatchTask"] = relationship(back_populates="repositories")
    repository: Mapped["GitHubRepository"] = relationship()
    entries: Mapped[list["BatchTaskEntry"]] = relationship(
        back_populates="task_repository",
        cascade="all, delete-orphan",
        order_by="BatchTaskEntry.entry_file_path.asc()",
    )


class BatchTaskEntry(Base):
    __tablename__ = "batch_task_entries"
    __table_args__ = (
        UniqueConstraint("task_repository_id", "entry_file_path", name="uq_batch_task_entries_repo_entry"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("batch_tasks.id"), index=True)
    task_repository_id: Mapped[str] = mapped_column(ForeignKey("batch_task_repositories.id"), index=True)
    stage3_entry_file_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    entry_file_path: Mapped[str] = mapped_column(Text, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage3_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    task_repository: Mapped["BatchTaskRepository"] = relationship(back_populates="entries")
    units: Mapped[list["BatchTaskUnit"]] = relationship(
        back_populates="task_entry",
        cascade="all, delete-orphan",
        order_by="BatchTaskUnit.depth.asc()",
    )


class BatchTaskUnit(Base):
    __tablename__ = "batch_task_units"
    __table_args__ = (
        UniqueConstraint("task_entry_id", "depth", name="uq_batch_task_units_entry_depth"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    task_id: Mapped[str] = mapped_column(ForeignKey("batch_tasks.id"), index=True)
    task_repository_id: Mapped[str] = mapped_column(ForeignKey("batch_task_repositories.id"), index=True)
    task_entry_id: Mapped[str] = mapped_column(ForeignKey("batch_task_entries.id"), index=True)
    depth: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    stage3_savepoint_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    stage4_run_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    data_pool_asset_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    task_entry: Mapped["BatchTaskEntry"] = relationship(back_populates="units")