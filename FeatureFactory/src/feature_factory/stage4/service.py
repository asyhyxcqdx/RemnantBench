from __future__ import annotations

import json
import re
import select as select_module
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from feature_factory.config import Settings, get_settings
from feature_factory.diff_stats import core_patch_diff_line_stats
from feature_factory.docker_mirrors import (
    github_proxy_retry_attempts,
    refresh_github_proxy_urls,
    rotate_failed_github_proxy_urls,
)
from feature_factory.host_repository_materializer import HostRepositoryMaterializer
from feature_factory.llm_usage import aggregate_token_usage_by_model
from feature_factory.models import (
    GitHubRepository,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3Savepoint,
    Stage4CleanupTombstone,
    Stage4IssueVariant,
    Stage4Run,
    Stage4RunEvent,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.stage2.raw_archive import (
    runtime_dir_from_workspace_path,
    summarize_llm_completion_archive,
)
from feature_factory.stage4.issue_styles import (
    IssueStyleConfigError,
    is_current_generation_config,
    load_issue_style_catalog,
    runtime_issue_style_ids,
    runtime_issue_style_specs,
)

STAGE4_ACTIVE_RUN_STATUSES = {
    Stage4RunStatus.pending.value,
    Stage4RunStatus.queued.value,
    Stage4RunStatus.running.value,
}
STAGE4_SOURCE_GROUP_SORT_FIELDS = {
    "repo",
    "commit",
    "language",
    "entry_count",
    "savepoint_count",
    "latest_operation_at",
    "status",
}
STAGE4_SOURCE_DETAIL_SORT_FIELDS = {
    "entry_file",
    "test_count",
    "depth",
    "entry_pass_rate",
    "p2p_count",
    "f2p_count",
    "diff_lines",
    "status",
    "latest_operation_at",
    "produced_variant_count",
}
STAGE4_RUN_SORT_FIELDS = {
    "created_at",
    "updated_at",
    "status",
    "result",
    "duration_seconds",
    "token_usage",
    "source_savepoint_id",
}
PRIVATE_DIAGNOSTIC_LEAKAGE_TERMS = (
    ("private_runner_reference", "/workspace/run_script.sh"),
    ("private_runner_reference", "--target-selector"),
    ("private_runner_reference", "/tmp/stage4-entry.json"),
)
_DIFF_OLD_FILE_RE = re.compile(r"(?m)^\s*---\s+a/\S+\s*$")
_DIFF_NEW_FILE_RE = re.compile(r"(?m)^\s*\+\+\+\s+b/\S+\s*$")
_DIFF_HUNK_RE = re.compile(
    r"(?m)^\s*@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@(?:\s.*)?$"
)
_STAGE4_RUNTIME_SNAPSHOT_SECTION_KEYS = (
    "concurrency",
    "issuer",
    "hyperparameters",
)
_GIT_PROGRESS_RE = re.compile(r"^(?P<stage>[A-Za-z][A-Za-z ]+):\s*(?P<percent>\d{1,3})%")
_GIT_OBJECT_COUNT_RE = re.compile(r"\((?P<current>\d+)\s*/\s*(?P<total>\d+)\)")
_GIT_PROGRESS_PERCENT_STEP = 10
_GIT_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
_GIT_REMOTE_MAX_ATTEMPTS = 3
_GIT_REMOTE_RETRY_DELAY_SECONDS = 3.0
_GIT_RETRYABLE_ERROR_MARKERS = (
    "SSL_ERROR_SYSCALL",
    "RPC failed",
    "Transferred a partial file",
    "early EOF",
    "unexpected disconnect",
    "invalid index-pack output",
    "Failed to connect",
    "Connection reset",
    "Connection refused",
    "Operation timed out",
    "command timed out",
    "timed out after",
    "Could not resolve host",
    "GnuTLS recv error",
    "TLS connection was non-properly terminated",
    "Empty reply from server",
    "requested URL returned error: 429",
    "requested URL returned error: 500",
    "requested URL returned error: 502",
    "requested URL returned error: 503",
    "requested URL returned error: 504",
    "requested URL returned error: 520",
    "requested URL returned error: 522",
    "requested URL returned error: 524",
    "curl 18",
    "curl 28",
    "curl 35",
    "curl 56",
    "HTTP/2 stream",
    "The remote end hung up unexpectedly",
)


class Stage4RunConflictError(RuntimeError):
    pass


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _runtime_issue_styles(stage4: dict[str, Any]) -> list[str]:
    return runtime_issue_style_ids(stage4)


def _stage3_diff_stats_from_patch_text(patch_text: str | None) -> dict[str, int]:
    stats = core_patch_diff_line_stats(patch_text)
    return {
        "files_changed": stats.files_changed,
        "lines_added": stats.lines_added,
        "lines_deleted": stats.lines_deleted,
        "lines_changed": stats.lines_changed,
    }


def _public_text_from_issue(item: dict[str, Any]) -> str:
    return "\n".join(
        [
            str(item.get("title") or ""),
            str(item.get("issue_markdown") or ""),
            json.dumps(item.get("issue_json") or {}, ensure_ascii=False),
        ]
    )


def _gold_patch_changed_lines_for_leak_warning(patch_text: str | None) -> list[str]:
    lines: list[str] = []
    for raw_line in str(patch_text or "").splitlines():
        if not raw_line.startswith(("+", "-")) or raw_line.startswith(("+++", "---")):
            continue
        line = raw_line[1:].strip()
        if len(line) < 40:
            continue
        lines.append(line)
    return lines


def _patch_structure_leakage_reasons(public_text: str) -> list[str]:
    has_file_header_pair = bool(
        _DIFF_OLD_FILE_RE.search(public_text) and _DIFF_NEW_FILE_RE.search(public_text)
    )
    has_hunk_header = bool(_DIFF_HUNK_RE.search(public_text))
    if has_hunk_header or has_file_header_pair:
        return ["patch_structure"]
    return []


def _contains_private_identifier(public_text: str, private_identifier: str) -> bool:
    return bool(
        re.search(
            rf"(?<!\w){re.escape(private_identifier)}(?!\w)",
            public_text,
            flags=re.IGNORECASE,
        )
    )


def _private_leakage_terms_for_source_context(source_context: dict[str, Any]) -> list[tuple[str, str]]:
    terms: list[tuple[str, str]] = list(PRIVATE_DIAGNOSTIC_LEAKAGE_TERMS)
    entry_file = dict(source_context.get("entry_file") or {})
    savepoint = dict(source_context.get("savepoint") or {})

    def add_test_path(reason: str, value: Any) -> None:
        path = str(value or "").strip()
        if len(path) < 8:
            return
        terms.append((reason, path))

    add_test_path("private_target_test_file", entry_file.get("test_file_path"))
    add_test_path("private_target_selector", entry_file.get("target_selector"))
    for path in list(savepoint.get("f2p_files") or []):
        add_test_path("private_failure_test_file", path)
    for row in list(savepoint.get("file_results") or []):
        if not isinstance(row, dict):
            continue
        if bool(row.get("is_entry_file")) or str(row.get("status") or "") != "passed":
            add_test_path("private_failure_test_file", row.get("test_file_path"))
            add_test_path("private_failure_target_selector", row.get("target_selector"))

    deduped: dict[tuple[str, str], None] = {}
    for reason, term in terms:
        normalized = str(term or "").strip()
        if normalized:
            deduped[(reason, normalized)] = None
    return list(deduped)


def _leakage_check(
    public_text: str,
    *,
    gold_patch_text: str | None,
    private_terms: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    normalized = str(public_text or "")
    reasons = _patch_structure_leakage_reasons(normalized)
    warnings: list[str] = []
    for reason, term in list(private_terms or []):
        private_term = str(term or "").strip()
        if private_term and _contains_private_identifier(normalized, private_term):
            reasons.append(reason)
    for line in _gold_patch_changed_lines_for_leak_warning(gold_patch_text):
        if line and line in normalized:
            warnings.append("gold_patch_line_overlap")
            break
    return {
        "passed": not reasons,
        "reasons": sorted(set(reasons)),
        "warnings": sorted(set(warnings)),
    }


def _parse_git_progress_line(raw_line: str) -> dict[str, Any] | None:
    line = raw_line.strip()
    if not line:
        return None
    if line.startswith("remote:"):
        line = line.removeprefix("remote:").strip()
    match = _GIT_PROGRESS_RE.search(line)
    if match is None:
        return None
    percent = max(0, min(100, int(match.group("percent"))))
    progress: dict[str, Any] = {
        "git_stage": match.group("stage").strip(),
        "percent": percent,
        "raw_line": line,
    }
    count_match = _GIT_OBJECT_COUNT_RE.search(line)
    if count_match is not None:
        progress["current"] = int(count_match.group("current"))
        progress["total"] = int(count_match.group("total"))
    return progress


def _is_retryable_git_remote_error(message: str) -> bool:
    lowered = message.lower()
    return any(marker.lower() in lowered for marker in _GIT_RETRYABLE_ERROR_MARKERS)


def merge_stage4_runtime_snapshot(
    snapshot: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(snapshot or {})
    payload = dict(incoming or {})
    merged: dict[str, Any] = {}
    for key in _STAGE4_RUNTIME_SNAPSHOT_SECTION_KEYS:
        section = dict(base.get(key) or {})
        section.update(dict(payload.get(key) or {}))
        if section:
            merged[key] = section
    for key, value in base.items():
        if key not in merged and key not in _STAGE4_RUNTIME_SNAPSHOT_SECTION_KEYS:
            merged[key] = value
    for key, value in payload.items():
        if key not in _STAGE4_RUNTIME_SNAPSHOT_SECTION_KEYS:
            merged[key] = value
    return merged


def _parse_iso_timestamp(value: Any) -> float:
    raw = str(value or "").strip()
    if not raw:
        return 0.0
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def _stage4_run_status_key(run: dict[str, Any] | None) -> str:
    if not run or not str(run.get("id") or "").strip():
        return "none"
    status = str(run.get("status") or "")
    result = str(run.get("result") or "")
    if status == Stage4RunStatus.completed.value and result == Stage4RunResult.generated.value:
        return "generated"
    if status == Stage4RunStatus.completed.value and result == Stage4RunResult.failed.value:
        return "failed"
    if status == Stage4RunStatus.completed.value and result == Stage4RunResult.interrupted.value:
        return "interrupted"
    return status or "none"


def _stage4_group_status_from_sources(sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "pending"
    queued_count = 0
    running_count = 0
    generated_count = 0
    failed_count = 0
    interrupted_count = 0
    for source in sources:
        savepoint = dict(source.get("savepoint") or {})
        status_key = _stage4_run_status_key(dict(savepoint.get("latest_stage4_run") or {}))
        if status_key == "running":
            running_count += 1
        elif status_key == "queued":
            queued_count += 1
        elif status_key == "generated":
            generated_count += 1
        elif status_key == "failed":
            failed_count += 1
        elif status_key == "interrupted":
            interrupted_count += 1
    if running_count > 0:
        return "running"
    if queued_count > 0:
        return "queued"
    if generated_count > 0:
        return "generated"
    if failed_count > 0:
        return "failed"
    if interrupted_count > 0:
        return "interrupted"
    if generated_count == 0 and failed_count == 0 and interrupted_count == 0:
        return "pending"
    return "failed"


def _stage4_group_status_sort_value(status: str) -> int:
    return {
        "pending": 0,
        "queued": 1,
        "running": 2,
        "failed": 3,
        "interrupted": 4,
        "generated": 5,
        "succeeded": 6,
    }.get(str(status or ""), -1)


def _stage4_savepoint_status_sort_value(status: str) -> int:
    return {
        "none": 0,
        "pending": 1,
        "queued": 2,
        "running": 3,
        "failed": 4,
        "interrupted": 5,
        "generated": 6,
    }.get(str(status or "none"), 99)


class Stage4Service:
    def __init__(
        self,
        session: Session,
        *,
        workspace_root: Path | str = Path("./data/stage4"),
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.settings = settings or get_settings()

    def _repository_materializer(self) -> HostRepositoryMaterializer:
        return HostRepositoryMaterializer(
            self.settings,
            emit_or_append_run_event=self._emit_or_append_run_event,
            run_checked_command=self._run_checked_command,
            run_command_with_git_progress=self._run_command_with_git_progress,
            run_git_remote_command_with_progress=self._run_git_remote_command_with_progress,
            raise_if_cancel_requested=self._raise_if_cancel_requested,
        )

    def get_source_savepoint(self, savepoint_id: int, *, include_detail: bool = True) -> Stage3Savepoint:
        stmt = select(Stage3Savepoint).where(Stage3Savepoint.id == int(savepoint_id))
        if include_detail:
            stmt = stmt.options(
                selectinload(Stage3Savepoint.file_results),
                selectinload(Stage3Savepoint.run)
                .selectinload(Stage3Run.entry_file)
                .selectinload(Stage3EntryFile.snapshot)
                .selectinload(Stage3CommitSnapshot.repository),
                selectinload(Stage3Savepoint.stage4_runs),
            )
        savepoint = self.session.scalar(stmt)
        if savepoint is None:
            raise ValueError(f"stage3 savepoint not found: {savepoint_id}")
        return savepoint

    def get_run(self, run_id: str, *, include_detail: bool = True) -> Stage4Run:
        stmt = select(Stage4Run).where(Stage4Run.id == str(run_id))
        if include_detail:
            stmt = stmt.options(
                selectinload(Stage4Run.events),
                selectinload(Stage4Run.issue_variants),
                selectinload(Stage4Run.hint_variants),
                selectinload(Stage4Run.source_savepoint)
                .selectinload(Stage3Savepoint.file_results),
                selectinload(Stage4Run.source_savepoint)
                .selectinload(Stage3Savepoint.stage4_runs),
                selectinload(Stage4Run.source_savepoint)
                .selectinload(Stage3Savepoint.run)
                .selectinload(Stage3Run.entry_file)
                .selectinload(Stage3EntryFile.snapshot)
                .selectinload(Stage3CommitSnapshot.repository),
            )
        run = self.session.scalar(stmt)
        if run is None:
            raise ValueError(f"stage4 run not found: {run_id}")
        return run

    def create_run(
        self,
        source_savepoint_id: int,
        *,
        trigger_kind: str = "manual",
        enabled_issue_styles: Sequence[str] | None = None,
        expected_issue_style_catalog_sha256: str | None = None,
    ) -> Stage4Run:
        savepoint = self.get_source_savepoint(source_savepoint_id, include_detail=True)
        if not str(savepoint.gold_patch_text or "").strip():
            raise Stage4RunConflictError("stage4 source savepoint is missing gold_patch_text")
        active_run = self.session.scalar(
            select(Stage4Run)
            .where(
                Stage4Run.source_savepoint_id == savepoint.id,
                Stage4Run.status.in_(tuple(STAGE4_ACTIVE_RUN_STATUSES)),
            )
            .order_by(Stage4Run.created_at.desc(), Stage4Run.id.desc())
            .limit(1)
        )
        if active_run is not None:
            raise Stage4RunConflictError(
                f"stage4 source savepoint already has an active run: {active_run.id}"
            )
        run_id = str(uuid.uuid4())
        try:
            issue_style_catalog = load_issue_style_catalog(
                enabled_styles=(
                    list(enabled_issue_styles)
                    if enabled_issue_styles is not None
                    else self.settings.stage4_enabled_issue_style_ids()
                ),
            )
        except (IssueStyleConfigError, ValueError) as exc:
            raise Stage4RunConflictError(f"invalid Stage4 issue style configuration: {exc}") from exc
        expected_catalog_sha256 = str(expected_issue_style_catalog_sha256 or "").strip()
        if (
            expected_catalog_sha256
            and issue_style_catalog.enabled_contract_sha256() != expected_catalog_sha256
        ):
            raise Stage4RunConflictError(
                "Stage4 issue style catalog changed after the batch task was created; "
                "retry the batch task with the current global configuration"
            )
        generation_config = issue_style_catalog.snapshot(seed=run_id)
        runtime_generation = {"generation_config": generation_config}
        issue_styles = _runtime_issue_styles(runtime_generation)
        issue_count = len(issue_styles)
        source_context = self._source_context(savepoint)
        runtime_seed = self._runtime_snapshot_seed()
        runtime_snapshot = {
            "stage4": {
                "source_savepoint_id": savepoint.id,
                "source_stage3_run_id": savepoint.run_id,
                "repository_id": source_context["repository"]["id"],
                "repository_full_name": source_context["repository"]["full_name"],
                "source_commit_sha": source_context["snapshot"]["source_commit_sha"],
                "entry_file_path": source_context["entry_file"]["test_file_path"],
                "depth": savepoint.depth,
                "issue_variant_count": issue_count,
                "issue_styles": issue_styles,
                "generation_config": generation_config,
                "runtime": {
                    **runtime_seed,
                },
            }
        }
        run = Stage4Run(
            id=run_id,
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.pending.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind=trigger_kind,
            phase="created",
            issue_variant_count=issue_count,
            hint_variant_count=0,
            runtime_snapshot_json=runtime_snapshot,
            summary_json={
                "runner_status": "pending",
                "issue_variant_count": issue_count,
                "source": source_context,
            },
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Stage4RunConflictError(
                "stage4 source savepoint already has an active run"
            ) from exc
        workspace_manifest = self._refresh_workspace_manifest(run)
        run.workspace_path = workspace_manifest["workspace_path"]
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage4": {
                **dict(runtime_snapshot["stage4"]),
                "workspace": dict(workspace_manifest),
            },
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="bootstrap",
                title="Stage4 run created",
                message=f"Created issue generation run for savepoint depth {savepoint.depth}",
                payload={
                    "source_savepoint_id": savepoint.id,
                    "issue_variant_count": issue_count,
                },
            )
        )
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def queue_run(self, run_id: str) -> Stage4Run:
        run = self.get_run(run_id, include_detail=True)
        if str(run.status or "") != Stage4RunStatus.pending.value:
            raise Stage4RunConflictError(f"stage4 run is not pending: {run_id}")
        self._ensure_current_generation_semantics(run)
        run.status = Stage4RunStatus.queued.value
        run.result = Stage4RunResult.unknown.value
        run.phase = "queued"
        run.error_message = None
        run.finished_at = None
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "queued",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="lifecycle",
                title="Stage4 run queued",
                message="Stage4 run is waiting for an issuer execution slot",
                payload={},
            )
        )
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Stage4RunConflictError(
                "stage4 source savepoint already has an active run"
            ) from exc
        return self.get_run(run.id, include_detail=True)

    def start_run(self, run_id: str) -> Stage4Run:
        run = self.get_run(run_id, include_detail=True)
        if str(run.status or "") != Stage4RunStatus.queued.value:
            raise Stage4RunConflictError(f"stage4 run is not queued: {run_id}")
        self._ensure_current_generation_semantics(run)
        run.status = Stage4RunStatus.running.value
        run.result = Stage4RunResult.unknown.value
        run.phase = "preparing_workspace"
        run.started_at = run.started_at or _utcnow()
        run.finished_at = None
        run.error_message = None
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "running",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="lifecycle",
                title="Stage4 run started",
                message="Stage4 run entered the configured issue generation phase",
                payload={},
            )
        )
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def _ensure_current_generation_semantics(self, run: Stage4Run) -> bool:
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        runtime_stage4 = dict(runtime_snapshot.get("stage4") or {})
        if is_current_generation_config(runtime_stage4):
            return False

        catalog = load_issue_style_catalog(
            enabled_styles=self.settings.stage4_enabled_issue_style_ids(),
        )
        generation_config = catalog.snapshot(seed=run.id)
        current_generation = {"generation_config": generation_config}
        issue_styles = _runtime_issue_styles(current_generation)
        runtime_stage4.pop("issue_style_config", None)
        runtime_stage4.pop("hint_strengths", None)
        runtime_stage4.pop("hint_variant_count", None)
        runtime_stage4.update(
            {
                "generation_config": generation_config,
                "issue_styles": issue_styles,
                "issue_variant_count": len(issue_styles),
            }
        )
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage4": runtime_stage4,
        }
        run.issue_variant_count = len(issue_styles)
        run.hint_variant_count = 0
        run.issue_variants.clear()
        run.hint_variants.clear()
        previous_summary = dict(run.summary_json or {})
        previous_summary.pop("hint_variant_count", None)
        run.summary_json = {
            **previous_summary,
            "issue_variant_count": len(issue_styles),
            "generation_config_schema_version": generation_config["schema_version"],
            "semantic_rebuild_count": int(previous_summary.get("semantic_rebuild_count") or 0) + 1,
        }
        runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        if runtime_dir is not None:
            shutil.rmtree(runtime_dir, ignore_errors=True)
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="bootstrap",
                title="Stage4 run rebuilt with current generation semantics",
                message=(
                    "Discarded the obsolete Stage4 prompt snapshot and rebuilt this run with "
                    "the independent task authors enabled by the current configuration"
                ),
                payload={
                    "schema_version": generation_config["schema_version"],
                    "enabled_styles": list(generation_config["enabled_styles"]),
                    "issue_variant_count": len(issue_styles),
                },
            )
        )
        self.session.flush()
        return True

    def prepare_run_workspace(
        self,
        run_id: str,
        *,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(run_id, include_detail=True)
        if str(run.status or "") != Stage4RunStatus.running.value:
            raise Stage4RunConflictError(f"stage4 run is not running: {run_id}")
        self._raise_if_cancel_requested(cancel_requested)
        savepoint = run.source_savepoint
        stage3_run = savepoint.run
        entry_file = stage3_run.entry_file
        snapshot = entry_file.snapshot
        repository = snapshot.repository
        manifest = self._refresh_workspace_manifest(run)
        workspace_dir = Path(manifest["workspace_path"])
        repo_dir = Path(manifest["repo_dir"])
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        baseline_commit = str(snapshot.source_commit_sha or "").strip()
        if not baseline_commit:
            raise Stage4RunConflictError("stage4 source snapshot is missing source_commit_sha")
        source_repo_dir = self._source_stage3_repo_dir(stage3_run)
        local_source_available = bool(source_repo_dir and (source_repo_dir / ".git").exists())
        materializer = self._repository_materializer()
        cache_dir = materializer.repository_cache_dir(repository)
        cache_revision = None
        if cache_dir.exists():
            with materializer.repo_cache_lock(cache_dir):
                cache_revision = materializer.try_rev_parse(cache_dir, f"{baseline_commit}^{{commit}}")
        clone_source = (
            str(cache_dir)
            if cache_revision
            else str(source_repo_dir)
            if local_source_available and source_repo_dir is not None
            else materializer.repository_clone_url(repository)
        )
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase="workspace",
            title="Broken repository materialization started",
            message=f"Preparing broken repo for {repository.full_name} at depth {savepoint.depth}",
            payload={
                "clone_source": clone_source,
                "repo_dir": str(repo_dir),
                "cache_path": str(cache_dir),
                "baseline_commit_sha": baseline_commit,
            },
            emit_event=emit_event,
        )
        materialized_checkout = None
        if cache_revision:
            try:
                with materializer.repo_cache_lock(cache_dir):
                    materialized_checkout = materializer.materialize_checkout_from_cache(
                        run,
                        repository=repository,
                        cache_dir=cache_dir,
                        checkout_dir=repo_dir,
                        target_commit_sha=baseline_commit,
                        phase="workspace",
                        emit_event=emit_event,
                        cancel_requested=cancel_requested,
                    )
            except Stage4RunConflictError as exc:
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase="workspace",
                    title="Repository cache fallback",
                    message=(
                        "Shared repository cache materialization failed; "
                        "falling back to the next available source"
                    ),
                    payload={
                        "cache_path": str(cache_dir),
                        "baseline_commit_sha": baseline_commit,
                        "error": str(exc)[-4000:],
                    },
                    emit_event=emit_event,
                )
        elif cache_dir.exists():
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase="workspace",
                title="Repository cache unavailable",
                message=(
                    f"Shared repository cache does not contain commit {baseline_commit}; "
                    "falling back to the next available source"
                ),
                payload={
                    "cache_path": str(cache_dir),
                    "baseline_commit_sha": baseline_commit,
                },
                emit_event=emit_event,
            )
        if materialized_checkout is None and local_source_available and source_repo_dir is not None:
            try:
                materialized_checkout = materializer.materialize_checkout_from_clone_source(
                    run,
                    repository=repository,
                    clone_source=str(source_repo_dir),
                    checkout_dir=repo_dir,
                    target_commit_sha=baseline_commit,
                    phase="workspace",
                    source_kind="stage3_local_repo",
                    source_label="the Stage3 local repository",
                    emit_event=emit_event,
                    cancel_requested=cancel_requested,
                )
            except Stage4RunConflictError as exc:
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase="workspace",
                    title="Stage3 local repo fallback",
                    message=(
                        "Local Stage3 repository materialization failed; "
                        "falling back to a remote clone"
                    ),
                    payload={
                        "clone_source": str(source_repo_dir),
                        "baseline_commit_sha": baseline_commit,
                        "error": str(exc)[-4000:],
                    },
                    emit_event=emit_event,
                )
        if materialized_checkout is None:
            materialized_checkout = materializer.materialize_checkout_from_clone_source(
                run,
                repository=repository,
                clone_source=materializer.repository_clone_url(repository),
                checkout_dir=repo_dir,
                target_commit_sha=baseline_commit,
                phase="workspace",
                source_kind="remote",
                source_label="the repository remote",
                emit_event=emit_event,
                cancel_requested=cancel_requested,
            )
        self._raise_if_cancel_requested(cancel_requested)
        patch_path = workspace_dir / ".stage4" / "gold.patch"
        patch_path.write_text(str(savepoint.gold_patch_text or ""), encoding="utf-8")
        if patch_path.read_text(encoding="utf-8").strip():
            self._run_checked_command(
                ["git", "-C", str(repo_dir), "apply", "--whitespace=nowarn", str(patch_path)],
                timeout_seconds=120.0,
                error_message="failed to apply stage3 gold patch while materializing stage4 broken repo",
            )
        assets_dir = Path(manifest["assets_dir"])
        dockerfile_text = str(snapshot.dockerfile_text or "")
        run_script_text = str(snapshot.run_script_text or "")
        if dockerfile_text:
            (assets_dir / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")
        if run_script_text:
            run_script_path = assets_dir / "run_script.sh"
            run_script_path.write_text(run_script_text, encoding="utf-8")
            run_script_path.chmod(0o755)
        source_context = self._source_context(savepoint)
        (workspace_dir / ".stage4" / "source_context.json").write_text(
            json.dumps(source_context, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest = self._refresh_workspace_manifest(run)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        runtime_stage4.update(
            {
                "baseline_commit_sha": baseline_commit,
                "broken_repo_dir": str(repo_dir),
                "gold_patch_path": str(patch_path),
                "repository_source_kind": materialized_checkout.source_kind,
                "repository_clone_source": materialized_checkout.clone_source,
                "repository_cache_path": materialized_checkout.cache_path,
                "resolved_head_commit_sha": materialized_checkout.resolved_head_commit_sha,
                "workspace": manifest,
            }
        )
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage4": runtime_stage4,
        }
        run.phase = "issuer_running"
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="workspace",
                title="Broken repository materialized",
                message="Stage4 broken repo is ready for issuer context generation",
                payload={
                    "repo_dir": str(repo_dir),
                    "baseline_commit_sha": baseline_commit,
                    "patch_path": str(patch_path),
                },
            )
        )
        self.session.flush()
        return self.source_context_for_run(run.id)

    def source_context_for_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id, include_detail=True)
        runtime_stage4 = dict((dict(run.runtime_snapshot_json or {}).get("stage4") or {}))
        return {
            **self._source_context(run.source_savepoint),
            "stage4": {
                "run_id": run.id,
                "issue_variant_count": run.issue_variant_count,
                "issue_styles": _runtime_issue_styles(runtime_stage4),
                "generation_config": dict(runtime_stage4.get("generation_config") or {}),
                "workspace": dict(runtime_stage4.get("workspace") or {}),
            },
            "private": {
                "gold_patch_text": str(run.source_savepoint.gold_patch_text or ""),
            },
        }

    def complete_run_with_bundle(
        self,
        run_id: str,
        *,
        bundle: dict[str, Any],
        model: str = "stage4-local-issuer",
        token_usage: int = 0,
        token_usage_details: dict[str, Any] | None = None,
    ) -> Stage4Run:
        run = self.get_run(run_id, include_detail=True)
        if str(run.status or "") != Stage4RunStatus.running.value:
            raise Stage4RunConflictError(f"stage4 run is not running: {run_id}")
        issue_rows, quality = self._validated_bundle_rows(run, bundle)
        run.issue_variants.clear()
        run.hint_variants.clear()
        run.issue_variants.extend(issue_rows)
        run.hint_variant_count = 0
        run.status = Stage4RunStatus.completed.value
        run.result = Stage4RunResult.generated.value
        run.phase = "completed"
        run.finished_at = _utcnow()
        run.error_message = None
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "completed",
            "issuer_model": model,
            "issuer_token_usage": int(token_usage or 0),
            "token_usage": dict(token_usage_details or {}),
            "issue_variant_count": len(issue_rows),
            "quality": quality,
        }
        run.events.append(
            self._make_run_event(
                actor="issuer_agent",
                phase="issuer_agent",
                title="Issue generation completed",
                message=f"Generated {len(issue_rows)} configured issue variant(s)",
                payload={
                    "issuer_model": model,
                    "issuer_token_usage": int(token_usage or 0),
                    "quality": quality,
                },
            )
        )
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def mark_run_failed(
        self,
        run_id: str,
        *,
        error_message: str,
        summary_updates: dict[str, Any] | None = None,
    ) -> Stage4Run:
        run = self.get_run(run_id, include_detail=True)
        run.status = Stage4RunStatus.completed.value
        run.result = Stage4RunResult.failed.value
        run.phase = "failed"
        run.finished_at = _utcnow()
        run.error_message = str(error_message or "")
        run.summary_json = {
            **dict(run.summary_json or {}),
            **dict(summary_updates or {}),
            "runner_status": "failed",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="failed",
                title="Stage4 run failed",
                message=run.error_message,
                payload={},
            )
        )
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def interrupt_run(self, run_id: str) -> Stage4Run:
        run = self.get_run(run_id, include_detail=True)
        previous_status = str(run.status or "")
        if previous_status not in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value}:
            raise Stage4RunConflictError("only queued or active stage4 runs can be interrupted")
        run.status = Stage4RunStatus.completed.value
        run.result = Stage4RunResult.interrupted.value
        run.phase = "interrupted"
        run.finished_at = _utcnow()
        run.error_message = "stage4 run interrupted by user"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "interrupted",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="interrupt",
                title="Stage4 run interrupted",
                message="Interrupted the queued or active Stage4 run",
                payload={"run_id": run_id, "previous_status": previous_status},
            )
        )
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def update_run_progress(
        self,
        run_id: str,
        *,
        phase: str | None = None,
        summary_updates: dict[str, Any] | None = None,
    ) -> Stage4Run:
        run = self.get_run(run_id, include_detail=False)
        if phase:
            run.phase = phase
        if summary_updates:
            run.summary_json = {
                **dict(run.summary_json or {}),
                **dict(summary_updates),
            }
        self.session.flush()
        return run

    def record_event(
        self,
        run_id: str,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> Stage4RunEvent:
        event = self._make_run_event(
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload=payload or {},
        )
        event.run_id = run_id
        self.session.add(event)
        self.session.commit()
        return event

    def prepared_run_cleanup_archive(self, run: Stage4Run) -> dict[str, Any]:
        source_savepoint = run.source_savepoint
        repository_id = None
        if source_savepoint is not None:
            stage3_run = source_savepoint.run
            entry_file = stage3_run.entry_file if stage3_run is not None else None
            snapshot = entry_file.snapshot if entry_file is not None else None
            repository_id = int(snapshot.repository_id) if snapshot is not None else None
        return {
            "run_id": run.id,
            "source_savepoint_id": run.source_savepoint_id,
            "repository_id": repository_id,
            "workspace_path": run.workspace_path,
            "runtime_path": str(self.workspace_root / "runtime" / run.id),
        }

    def delete_run(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id, include_detail=True)
        if str(run.status or "") in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value}:
            raise Stage4RunConflictError(f"cannot delete an active stage4 run: {run_id}")
        archive = self.prepared_run_cleanup_archive(run)
        self.upsert_cleanup_tombstone(archive, reason="manual_delete")
        self.session.delete(run)
        self.session.flush()
        return archive

    def repair_duplicate_active_runs(self, *, return_archives: bool = False) -> int | list[dict[str, Any]]:
        dirty = 0
        archives: list[dict[str, Any]] = []
        duplicate_savepoint_ids = list(
            self.session.scalars(
                select(Stage4Run.source_savepoint_id)
                .where(Stage4Run.status.in_(tuple(STAGE4_ACTIVE_RUN_STATUSES)))
                .group_by(Stage4Run.source_savepoint_id)
                .having(func.count(Stage4Run.id) > 1)
            )
        )
        if not duplicate_savepoint_ids:
            return archives if return_archives else dirty

        repaired_at = datetime.now(UTC)
        for savepoint_id in duplicate_savepoint_ids:
            active_run_priority = case(
                (Stage4Run.status == Stage4RunStatus.running.value, 0),
                (Stage4Run.status == Stage4RunStatus.queued.value, 1),
                else_=2,
            )
            active_runs = list(
                self.session.scalars(
                    select(Stage4Run)
                    .options(
                        selectinload(Stage4Run.source_savepoint)
                        .selectinload(Stage3Savepoint.run)
                        .selectinload(Stage3Run.entry_file)
                        .selectinload(Stage3EntryFile.snapshot)
                    )
                    .where(
                        Stage4Run.source_savepoint_id == savepoint_id,
                        Stage4Run.status.in_(tuple(STAGE4_ACTIVE_RUN_STATUSES)),
                    )
                    .order_by(
                        active_run_priority.asc(),
                        Stage4Run.created_at.desc(),
                        Stage4Run.id.desc(),
                    )
                )
            )
            for stale_run in active_runs[1:]:
                previous_status = stale_run.status
                previous_phase = stale_run.phase
                archive = self.prepared_run_cleanup_archive(stale_run)
                archives.append(archive)
                self.upsert_cleanup_tombstone(archive, reason="duplicate_active_repair")
                stale_run.status = Stage4RunStatus.completed.value
                stale_run.result = Stage4RunResult.failed.value
                stale_run.phase = "failed"
                stale_run.finished_at = repaired_at
                stale_run.error_message = (
                    stale_run.error_message
                    or "stage4 run invalidated while restoring the single-active-run invariant"
                )
                stale_run.summary_json = {
                    **dict(stale_run.summary_json or {}),
                    "runner_status": "failed",
                    "duplicate_active_repair": True,
                }
                stale_run.events.append(
                    self._make_run_event(
                        actor="system",
                        phase="failed",
                        title="Stage4 active-run invariant repaired",
                        message=stale_run.error_message,
                        payload={
                            "run_id": stale_run.id,
                            "source_savepoint_id": stale_run.source_savepoint_id,
                            "previous_status": previous_status,
                            "previous_phase": previous_phase,
                        },
                    )
                )
                dirty += 1
        if dirty:
            self.session.flush()
        return archives if return_archives else dirty

    def upsert_cleanup_tombstone(
        self,
        archive: dict[str, Any],
        *,
        reason: str,
    ) -> Stage4CleanupTombstone | None:
        run_id = str(archive.get("run_id") or "").strip()
        if not run_id:
            return None
        tombstone = self.session.get(Stage4CleanupTombstone, run_id)
        if tombstone is None:
            tombstone = Stage4CleanupTombstone(run_id=run_id)
        tombstone.source_savepoint_id = int(archive.get("source_savepoint_id") or 0) or None
        tombstone.repository_id = int(archive.get("repository_id") or 0) or None
        tombstone.reason = str(reason or "unknown")
        tombstone.status = "pending"
        tombstone.archive_json = dict(archive or {})
        tombstone.failure_json = {}
        self.session.add(tombstone)
        self.session.flush()
        return tombstone

    def cleanup_tombstone_archives(self) -> list[dict[str, Any]]:
        rows = list(
            self.session.scalars(
                select(Stage4CleanupTombstone)
                .where(Stage4CleanupTombstone.status.in_(["pending", "failed"]))
                .order_by(Stage4CleanupTombstone.created_at.asc())
            )
        )
        archives: list[dict[str, Any]] = []
        for row in rows:
            archive = dict(row.archive_json or {})
            archive.setdefault("run_id", row.run_id)
            archive.setdefault("source_savepoint_id", row.source_savepoint_id)
            archive.setdefault("repository_id", row.repository_id)
            archive["_cleanup_tombstone_reason"] = row.reason
            archive["_cleanup_tombstone_status"] = row.status
            archive["_cleanup_tombstone_attempt_count"] = row.attempt_count
            archives.append(archive)
        return archives

    def record_cleanup_tombstone_result(
        self,
        run_id: str,
        *,
        cleanup_result: dict[str, Any],
    ) -> None:
        tombstone = self.session.get(Stage4CleanupTombstone, run_id)
        if tombstone is None:
            return
        failure_payload = dict(cleanup_result or {})
        failed_paths = list(failure_payload.get("failed_paths") or [])
        tombstone.attempt_count = int(tombstone.attempt_count or 0) + 1
        tombstone.last_attempt_at = datetime.now(UTC)
        if failed_paths:
            tombstone.status = "failed"
            tombstone.failure_json = failure_payload
            self.session.add(tombstone)
        else:
            self.session.delete(tombstone)
        self.session.flush()

    def cleanup_run_archive(self, archive: dict[str, Any]) -> dict[str, list[str]]:
        failed_paths: list[str] = []
        root = self.workspace_root.expanduser().resolve()
        for raw_path in [
            str(archive.get("workspace_path") or ""),
            str(archive.get("runtime_path") or ""),
        ]:
            if not raw_path:
                continue
            path = Path(raw_path).expanduser()
            try:
                resolved = path.resolve()
                resolved.relative_to(root)
            except Exception:
                continue
            if not resolved.exists():
                continue
            try:
                shutil.rmtree(resolved) if resolved.is_dir() else resolved.unlink()
            except Exception:
                failed_paths.append(str(resolved))
        return {"failed_paths": failed_paths}

    def recover_interrupted_runs(self, *, return_archives: bool = False) -> int | list[dict[str, Any]]:
        dirty = 0
        archives: list[dict[str, Any]] = []
        rows = list(
            self.session.scalars(
                select(Stage4Run)
                .options(
                    selectinload(Stage4Run.source_savepoint)
                    .selectinload(Stage3Savepoint.run)
                    .selectinload(Stage3Run.entry_file)
                    .selectinload(Stage3EntryFile.snapshot),
                )
                .where(Stage4Run.status.in_([Stage4RunStatus.queued.value, Stage4RunStatus.running.value]))
            )
        )
        for row in rows:
            previous_status = str(row.status or "")
            archive = {
                **self.prepared_run_cleanup_archive(row),
                "cleanup_scope": "recovered_run_assets",
            }
            archives.append(archive)
            self.upsert_cleanup_tombstone(archive, reason="recovered_interrupted_run")
            row.status = Stage4RunStatus.completed.value
            row.result = Stage4RunResult.interrupted.value
            row.phase = "interrupted"
            row.finished_at = _utcnow()
            row.error_message = (
                row.error_message
                or (
                    "stage4 run interrupted before execution"
                    if previous_status == Stage4RunStatus.queued.value
                    else "stage4 run interrupted before completion"
                )
            )
            row.summary_json = {
                **dict(row.summary_json or {}),
                "runner_status": "interrupted",
            }
            row.events.append(
                self._make_run_event(
                    actor="system",
                    phase="interrupt",
                    title="Stage4 run recovered as interrupted",
                    message=row.error_message,
                    payload={
                        "run_id": row.id,
                        "previous_status": previous_status,
                    },
                )
            )
            dirty += 1
        if dirty:
            self.session.flush()
        return archives if return_archives else dirty

    def _latest_stage4_run_subquery(self):
        ranked = (
            select(
                Stage4Run.id.label("run_id"),
                Stage4Run.source_savepoint_id.label("source_savepoint_id"),
                Stage4Run.status.label("status"),
                Stage4Run.result.label("result"),
                Stage4Run.created_at.label("created_at"),
                Stage4Run.started_at.label("started_at"),
                Stage4Run.finished_at.label("finished_at"),
                Stage4Run.updated_at.label("updated_at"),
                func.row_number()
                .over(
                    partition_by=Stage4Run.source_savepoint_id,
                    order_by=(Stage4Run.created_at.desc(), Stage4Run.id.desc()),
                )
                .label("row_number"),
            )
            .subquery()
        )
        return (
            select(
                ranked.c.run_id,
                ranked.c.source_savepoint_id,
                ranked.c.status,
                ranked.c.result,
                ranked.c.created_at,
                ranked.c.started_at,
                ranked.c.finished_at,
                ranked.c.updated_at,
            )
            .where(ranked.c.row_number == 1)
            .subquery()
        )

    def _source_listing_stmt(self):
        return (
            select(Stage3Savepoint)
            .options(
                selectinload(Stage3Savepoint.file_results),
                selectinload(Stage3Savepoint.stage4_runs),
                selectinload(Stage3Savepoint.run)
                .selectinload(Stage3Run.entry_file)
                .selectinload(Stage3EntryFile.snapshot)
                .selectinload(Stage3CommitSnapshot.repository),
            )
            .where(
                Stage3Savepoint.gold_patch_text.is_not(None),
                Stage3Savepoint.gold_patch_text != "",
            )
            .order_by(Stage3Savepoint.updated_at.desc(), Stage3Savepoint.id.desc())
        )

    def _source_group_rows_query(
        self,
        *,
        name_query: str | None = None,
        languages: list[str] | None = None,
    ):
        latest_run = self._latest_stage4_run_subquery()
        latest_status_key = case(
            (latest_run.c.run_id.is_(None), "none"),
            (
                (latest_run.c.status == Stage4RunStatus.completed.value)
                & (latest_run.c.result == Stage4RunResult.generated.value),
                "generated",
            ),
            (
                (latest_run.c.status == Stage4RunStatus.completed.value)
                & (latest_run.c.result == Stage4RunResult.failed.value),
                "failed",
            ),
            (
                (latest_run.c.status == Stage4RunStatus.completed.value)
                & (latest_run.c.result == Stage4RunResult.interrupted.value),
                "interrupted",
            ),
            else_=func.coalesce(latest_run.c.status, "none"),
        )
        latest_operation_at = func.max(
            func.coalesce(
                latest_run.c.updated_at,
                latest_run.c.finished_at,
                latest_run.c.started_at,
                latest_run.c.created_at,
                Stage3Savepoint.updated_at,
                Stage3Savepoint.created_at,
            )
        )
        stmt = (
            select(
                GitHubRepository.id.label("repository_id"),
                GitHubRepository.full_name.label("repository_full_name"),
                GitHubRepository.html_url.label("repository_html_url"),
                GitHubRepository.default_branch.label("repository_default_branch"),
                GitHubRepository.primary_language.label("repository_primary_language"),
                Stage3CommitSnapshot.id.label("snapshot_id"),
                Stage3CommitSnapshot.source_commit_sha.label("source_commit_sha"),
                func.count(Stage3Savepoint.id).label("savepoint_count"),
                func.count(func.distinct(Stage3Run.entry_file_id)).label("entry_count"),
                latest_operation_at.label("latest_operation_at"),
                func.sum(case((latest_status_key == "running", 1), else_=0)).label("running_count"),
                func.sum(case((latest_status_key == "queued", 1), else_=0)).label("queued_count"),
                func.sum(case((latest_status_key == "generated", 1), else_=0)).label("generated_count"),
                func.sum(case((latest_status_key == "failed", 1), else_=0)).label("failed_count"),
                func.sum(case((latest_status_key == "interrupted", 1), else_=0)).label("interrupted_count"),
            )
            .select_from(Stage3Savepoint)
            .join(Stage3Run, Stage3Savepoint.run_id == Stage3Run.id)
            .join(Stage3EntryFile, Stage3Run.entry_file_id == Stage3EntryFile.id)
            .join(Stage3CommitSnapshot, Stage3EntryFile.snapshot_id == Stage3CommitSnapshot.id)
            .join(GitHubRepository, Stage3CommitSnapshot.repository_id == GitHubRepository.id)
            .outerjoin(latest_run, latest_run.c.source_savepoint_id == Stage3Savepoint.id)
            .where(
                Stage3Savepoint.gold_patch_text.is_not(None),
                Stage3Savepoint.gold_patch_text != "",
            )
            .group_by(
                GitHubRepository.id,
                GitHubRepository.full_name,
                GitHubRepository.html_url,
                GitHubRepository.default_branch,
                GitHubRepository.primary_language,
                Stage3CommitSnapshot.id,
                Stage3CommitSnapshot.source_commit_sha,
            )
        )
        normalized_query = str(name_query or "").strip().lower()
        normalized_languages = {
            str(value).strip()
            for value in list(languages or [])
            if str(value).strip()
        }
        if normalized_query:
            stmt = stmt.where(func.lower(GitHubRepository.full_name).contains(normalized_query))
        if normalized_languages:
            stmt = stmt.where(GitHubRepository.primary_language.in_(sorted(normalized_languages)))
        return stmt.subquery()

    def _group_serialized_sources(self, serialized_sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        groups: dict[tuple[int | None, int | None], dict[str, Any]] = {}
        for source in serialized_sources:
            repository = dict(source.get("repository") or {})
            snapshot = dict(source.get("snapshot") or {})
            entry_file = dict(source.get("entry_file") or {})
            savepoint = dict(source.get("savepoint") or {})
            group_key = (
                repository.get("id"),
                snapshot.get("id"),
            )
            group = groups.get(group_key)
            if group is None:
                group = {
                    "key": (
                        f"{repository.get('id') or repository.get('full_name') or 'repo'}::"
                        f"{snapshot.get('id') or snapshot.get('source_commit_sha') or 'commit'}"
                    ),
                    "repository": repository,
                    "snapshot": snapshot,
                    "sources": [],
                    "entry_paths": set(),
                    "language_values": set(),
                    "savepoint_count": 0,
                    "latest_operation_at": 0.0,
                }
                groups[group_key] = group
            group["sources"].append(source)
            test_file_path = str(entry_file.get("test_file_path") or "").strip()
            if test_file_path:
                group["entry_paths"].add(test_file_path)
            language = str(repository.get("primary_language") or "").strip()
            if language:
                group["language_values"].add(language)
            group["savepoint_count"] += 1
            group["latest_operation_at"] = max(
                float(group["latest_operation_at"] or 0.0),
                _parse_iso_timestamp(savepoint.get("updated_at")),
                _parse_iso_timestamp(savepoint.get("created_at")),
                _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("updated_at")),
                _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("finished_at")),
                _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("started_at")),
                _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("created_at")),
            )
        grouped_payloads: list[dict[str, Any]] = []
        for group in groups.values():
            sources = list(group["sources"])
            grouped_payloads.append(
                {
                    "key": group["key"],
                    "repository": group["repository"],
                    "snapshot": group["snapshot"],
                    "sources": sources,
                    "entry_count": len(group["entry_paths"]),
                    "language_list": sorted(group["language_values"]),
                    "savepoint_count": int(group["savepoint_count"] or 0),
                    "latest_operation_at": serialize_datetime(
                        datetime.fromtimestamp(group["latest_operation_at"], tz=UTC)
                    )
                    if group["latest_operation_at"]
                    else None,
                    "status": _stage4_group_status_from_sources(sources),
                }
            )
        return grouped_payloads

    def _sort_source_groups(
        self,
        groups: list[dict[str, Any]],
        *,
        sort_by: str,
        sort_order: str,
    ) -> list[dict[str, Any]]:
        reverse = sort_order == "desc"

        def sort_value(group: dict[str, Any]) -> Any:
            repository = dict(group.get("repository") or {})
            snapshot = dict(group.get("snapshot") or {})
            if sort_by == "repo":
                return str(repository.get("full_name") or "")
            if sort_by == "commit":
                return str(snapshot.get("source_commit_sha") or "")
            if sort_by == "language":
                return " ".join(group.get("language_list") or [])
            if sort_by == "entry_count":
                return int(group.get("entry_count") or 0)
            if sort_by == "savepoint_count":
                return int(group.get("savepoint_count") or 0)
            if sort_by == "latest_operation_at":
                return _parse_iso_timestamp(group.get("latest_operation_at"))
            if sort_by == "status":
                return _stage4_group_status_sort_value(str(group.get("status") or ""))
            return str(repository.get("full_name") or "")

        return sorted(
            groups,
            key=lambda group: (
                sort_value(group),
                str((group.get("repository") or {}).get("full_name") or ""),
                str((group.get("snapshot") or {}).get("source_commit_sha") or ""),
            ),
            reverse=reverse,
        )

    def _paginate_rows(self, rows: list[dict[str, Any]], *, page: int, page_size: int) -> dict[str, Any]:
        total = len(rows)
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size
        return {
            "rows": rows[offset : offset + page_size],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    def list_source_savepoints(self, *, limit: int | None = None) -> dict[str, Any]:
        stmt = self._source_listing_stmt()
        if limit is not None:
            stmt = stmt.limit(max(1, int(limit)))
        sources = [self.serialize_source_savepoint(row) for row in list(self.session.scalars(stmt))]
        return {
            "sources": sources,
            "total": len(sources),
        }

    def list_source_groups(
        self,
        *,
        page: int,
        page_size: int,
        sort_by: str,
        sort_order: str,
        name_query: str | None = None,
        languages: list[str] | None = None,
        statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        grouped_rows = self._source_group_rows_query(name_query=name_query, languages=languages)
        group_status = case(
            (grouped_rows.c.running_count > 0, "running"),
            (grouped_rows.c.queued_count > 0, "queued"),
            (grouped_rows.c.generated_count > 0, "generated"),
            (grouped_rows.c.failed_count > 0, "failed"),
            (grouped_rows.c.interrupted_count > 0, "interrupted"),
            (
                (grouped_rows.c.generated_count == 0)
                & (grouped_rows.c.failed_count == 0)
                & (grouped_rows.c.interrupted_count == 0),
                "pending",
            ),
            else_="failed",
        )
        stmt = select(
            grouped_rows.c.repository_id,
            grouped_rows.c.repository_full_name,
            grouped_rows.c.repository_html_url,
            grouped_rows.c.repository_default_branch,
            grouped_rows.c.repository_primary_language,
            grouped_rows.c.snapshot_id,
            grouped_rows.c.source_commit_sha,
            grouped_rows.c.entry_count,
            grouped_rows.c.savepoint_count,
            grouped_rows.c.latest_operation_at,
            group_status.label("status"),
        ).select_from(grouped_rows)
        normalized_statuses: set[str] = set()
        for value in list(statuses or []):
            normalized = str(value).strip()
            if not normalized:
                continue
            normalized_statuses.add(normalized)
            if normalized == "succeeded":
                normalized_statuses.add("generated")
        if normalized_statuses:
            stmt = stmt.where(group_status.in_(sorted(normalized_statuses)))
        status_sort_value = case(
            (group_status == "pending", 0),
            (group_status == "queued", 1),
            (group_status == "running", 2),
            (group_status == "failed", 3),
            (group_status == "interrupted", 4),
            (group_status == "generated", 5),
            (group_status == "succeeded", 6),
            else_=-1,
        )
        sort_expr = {
            "repo": grouped_rows.c.repository_full_name,
            "commit": grouped_rows.c.source_commit_sha,
            "language": grouped_rows.c.repository_primary_language,
            "entry_count": grouped_rows.c.entry_count,
            "savepoint_count": grouped_rows.c.savepoint_count,
            "latest_operation_at": grouped_rows.c.latest_operation_at,
            "status": status_sort_value,
        }.get(sort_by, grouped_rows.c.latest_operation_at)
        reverse = sort_order == "desc"
        if reverse:
            stmt = stmt.order_by(
                sort_expr.desc(),
                grouped_rows.c.repository_full_name.desc(),
                grouped_rows.c.source_commit_sha.desc(),
            )
        else:
            stmt = stmt.order_by(
                sort_expr.asc(),
                grouped_rows.c.repository_full_name.asc(),
                grouped_rows.c.source_commit_sha.asc(),
            )
        total = int(self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size
        rows = list(self.session.execute(stmt.offset(offset).limit(page_size)).mappings())
        return {
            "groups": [
                {
                    "key": f"{int(row.repository_id)}::{str(row.snapshot_id)}",
                    "repository": {
                        "id": int(row.repository_id),
                        "full_name": str(row.repository_full_name or ""),
                        "html_url": str(row.repository_html_url or ""),
                        "default_branch": str(row.repository_default_branch or ""),
                        "primary_language": str(row.repository_primary_language or ""),
                    },
                    "snapshot": {
                        "id": str(row.snapshot_id),
                        "source_commit_sha": str(row.source_commit_sha or ""),
                    },
                    "entry_count": int(row.entry_count or 0),
                    "savepoint_count": int(row.savepoint_count or 0),
                    "latest_operation_at": serialize_datetime(row.latest_operation_at),
                    "status": str(row.status or "pending"),
                    "language_list": [str(row.repository_primary_language)] if str(row.repository_primary_language or "").strip() else [],
                }
                for row in rows
            ],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            "sort": {
                "field": sort_by,
                "order": sort_order,
            },
            "filters": {
                "name_query": str(name_query or ""),
                "languages": list(languages or []),
                "statuses": list(statuses or []),
            },
        }

    def get_source_group_detail(
        self,
        repository_id: int,
        snapshot_id: str,
        *,
        sort_by: str,
        sort_order: str,
        entry_query: str | None = None,
        statuses: list[str] | None = None,
        test_count_min: int | None = None,
        test_count_max: int | None = None,
        entry_pass_rate_min: float | None = None,
        entry_pass_rate_max: float | None = None,
        diff_lines_min: int | None = None,
        diff_lines_max: int | None = None,
    ) -> dict[str, Any]:
        stmt = (
            self._source_listing_stmt()
            .join(Stage3Savepoint.run)
            .join(Stage3Run.entry_file)
            .join(Stage3EntryFile.snapshot)
            .where(
                Stage3CommitSnapshot.repository_id == int(repository_id),
                Stage3CommitSnapshot.id == str(snapshot_id),
            )
        )
        savepoints = list(self.session.scalars(stmt))
        if not savepoints:
            raise ValueError(
                f"stage4 source group not found for repository_id={repository_id}, snapshot_id={snapshot_id}"
            )
        serialized_sources = [self.serialize_source_savepoint(row) for row in savepoints]
        group = self._group_serialized_sources(serialized_sources)[0]
        normalized_query = str(entry_query or "").strip().lower()
        normalized_statuses = {str(value).strip() for value in list(statuses or []) if str(value).strip()}

        def include_source(source: dict[str, Any]) -> bool:
            entry_file = dict(source.get("entry_file") or {})
            savepoint = dict(source.get("savepoint") or {})
            if normalized_query and normalized_query not in str(entry_file.get("test_file_path") or "").lower():
                return False
            status_key = _stage4_run_status_key(dict(savepoint.get("latest_stage4_run") or {}))
            if normalized_statuses and status_key not in normalized_statuses:
                return False
            test_count = int(entry_file.get("baseline_total_tests") or 0)
            entry_pass_rate = float(savepoint.get("entry_pass_rate") or 0.0) * 100.0
            diff_lines = int((dict(savepoint.get("diff_stats") or {})).get("lines_changed") or 0)
            if test_count_min is not None and test_count < test_count_min:
                return False
            if test_count_max is not None and test_count > test_count_max:
                return False
            if entry_pass_rate_min is not None and entry_pass_rate < entry_pass_rate_min:
                return False
            if entry_pass_rate_max is not None and entry_pass_rate > entry_pass_rate_max:
                return False
            if diff_lines_min is not None and diff_lines < diff_lines_min:
                return False
            if diff_lines_max is not None and diff_lines > diff_lines_max:
                return False
            return True

        filtered_sources = [source for source in list(group.get("sources") or []) if include_source(source)]
        reverse = sort_order == "desc"

        def source_sort_value(source: dict[str, Any]) -> Any:
            entry_file = dict(source.get("entry_file") or {})
            savepoint = dict(source.get("savepoint") or {})
            if sort_by == "entry_file":
                return str(entry_file.get("test_file_path") or "")
            if sort_by == "test_count":
                return int(entry_file.get("baseline_total_tests") or 0)
            if sort_by == "depth":
                return int(savepoint.get("depth") or 0)
            if sort_by == "entry_pass_rate":
                return float(savepoint.get("entry_pass_rate") or 0.0)
            if sort_by == "p2p_count":
                return int(savepoint.get("p2p_count") or 0)
            if sort_by == "f2p_count":
                return int(savepoint.get("f2p_count") or 0)
            if sort_by == "diff_lines":
                return int((dict(savepoint.get("diff_stats") or {})).get("lines_changed") or 0)
            if sort_by == "status":
                return _stage4_savepoint_status_sort_value(
                    _stage4_run_status_key(dict(savepoint.get("latest_stage4_run") or {}))
                )
            if sort_by == "latest_operation_at":
                return max(
                    _parse_iso_timestamp(savepoint.get("updated_at")),
                    _parse_iso_timestamp(savepoint.get("created_at")),
                    _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("updated_at")),
                    _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("finished_at")),
                    _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("started_at")),
                    _parse_iso_timestamp((savepoint.get("latest_stage4_run") or {}).get("created_at")),
                )
            if sort_by == "produced_variant_count":
                return int(savepoint.get("produced_issue_variant_count") or 0)
            return str(entry_file.get("test_file_path") or "")

        ordered_sources = sorted(
            filtered_sources,
            key=lambda source: (
                source_sort_value(source),
                str((source.get("entry_file") or {}).get("test_file_path") or ""),
                int((source.get("savepoint") or {}).get("depth") or 0),
            ),
            reverse=reverse,
        )
        return {
            "group": {
                "key": group["key"],
                "repository": group["repository"],
                "snapshot": group["snapshot"],
                "entry_count": group["entry_count"],
                "savepoint_count": group["savepoint_count"],
                "latest_operation_at": group["latest_operation_at"],
                "status": group["status"],
                "language_list": list(group.get("language_list") or []),
            },
            "sources": ordered_sources,
            "sort": {
                "field": sort_by,
                "order": sort_order,
            },
            "filters": {
                "entry_query": str(entry_query or ""),
                "statuses": list(statuses or []),
                "test_count_min": test_count_min,
                "test_count_max": test_count_max,
                "entry_pass_rate_min": entry_pass_rate_min,
                "entry_pass_rate_max": entry_pass_rate_max,
                "diff_lines_min": diff_lines_min,
                "diff_lines_max": diff_lines_max,
            },
        }

    def list_runs(
        self,
        *,
        source_savepoint_id: int | None = None,
        limit: int | None = None,
        page: int | None = None,
        page_size: int | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        statuses: list[str] | None = None,
        results: list[str] | None = None,
    ) -> dict[str, Any]:
        stmt = select(Stage4Run)
        if source_savepoint_id is not None:
            stmt = stmt.where(Stage4Run.source_savepoint_id == int(source_savepoint_id))
        normalized_statuses = {str(value).strip() for value in list(statuses or []) if str(value).strip()}
        normalized_results = {str(value).strip() for value in list(results or []) if str(value).strip()}
        if normalized_statuses:
            stmt = stmt.where(Stage4Run.status.in_(sorted(normalized_statuses)))
        if normalized_results:
            stmt = stmt.where(Stage4Run.result.in_(sorted(normalized_results)))
        dialect_name = str(self.session.get_bind().dialect.name or "").lower()
        completed_at = func.coalesce(Stage4Run.finished_at, Stage4Run.updated_at, Stage4Run.started_at)
        if dialect_name == "sqlite":
            duration_sort_expr = case(
                (Stage4Run.started_at.is_(None), 0.0),
                else_=(func.julianday(completed_at) - func.julianday(Stage4Run.started_at)) * 86400.0,
            )
        else:
            duration_sort_expr = case(
                (Stage4Run.started_at.is_(None), 0.0),
                else_=func.extract("epoch", completed_at - Stage4Run.started_at),
            )
        token_usage_sort_expr = func.coalesce(
            Stage4Run.summary_json["issuer_token_usage"].as_integer(),
            0,
        )
        sort_expr = {
            "created_at": Stage4Run.created_at,
            "updated_at": Stage4Run.updated_at,
            "status": Stage4Run.status,
            "result": Stage4Run.result,
            "duration_seconds": duration_sort_expr,
            "token_usage": token_usage_sort_expr,
            "source_savepoint_id": Stage4Run.source_savepoint_id,
        }.get(sort_by, Stage4Run.created_at)
        reverse = sort_order == "desc"
        if reverse:
            stmt = stmt.order_by(sort_expr.desc(), Stage4Run.created_at.desc(), Stage4Run.id.desc())
        else:
            stmt = stmt.order_by(sort_expr.asc(), Stage4Run.created_at.asc(), Stage4Run.id.asc())
        total = int(self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0)
        if limit is not None:
            rows = list(self.session.scalars(stmt.limit(max(1, int(limit)))))
            serialized_rows = [self.serialize_run_summary(row) for row in rows]
            return {"runs": serialized_rows, "total": len(serialized_rows)}
        if page is None or page_size is None:
            rows = list(self.session.scalars(stmt))
            return {"runs": [self.serialize_run_summary(row) for row in rows], "total": total}
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size
        rows = list(self.session.scalars(stmt.offset(offset).limit(page_size)))
        return {
            "runs": [self.serialize_run_summary(row) for row in rows],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
            "sort": {
                "field": sort_by,
                "order": sort_order,
            },
            "filters": {
                "source_savepoint_id": source_savepoint_id,
                "statuses": list(statuses or []),
                "results": list(results or []),
            },
        }

    def serialize_source_savepoint(self, savepoint: Stage3Savepoint) -> dict[str, Any]:
        source = self._source_context(savepoint)
        stage4_runs = list(
            self.session.scalars(
                select(Stage4Run)
                .where(Stage4Run.source_savepoint_id == savepoint.id)
                .order_by(Stage4Run.created_at.desc(), Stage4Run.id.desc())
            )
        )
        latest_run = stage4_runs[0] if stage4_runs else None
        latest_generated_run = next(
            (
                row
                for row in stage4_runs
                if str(row.status or "") == Stage4RunStatus.completed.value
                and str(row.result or "") == Stage4RunResult.generated.value
            ),
            None,
        )
        return {
            **source,
            "savepoint": {
                **source["savepoint"],
                "stage4_run_count": len(stage4_runs),
                "latest_stage4_run": self.serialize_run_summary(latest_run) if latest_run is not None else None,
                "produced_issue_variant_count": int(latest_generated_run.issue_variant_count or 0) if latest_generated_run is not None else 0,
            },
        }

    def serialize_run_summary(self, run: Stage4Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        status = str(run.status or "")
        summary = dict(run.summary_json or {})
        usage_metrics = self._serialized_run_usage_metrics(run)
        return {
            "id": run.id,
            "source_savepoint_id": run.source_savepoint_id,
            "status": run.status,
            "result": run.result,
            "trigger_kind": run.trigger_kind,
            "phase": run.phase,
            "issue_variant_count": run.issue_variant_count,
            "summary": summary,
            "error_message": run.error_message,
            "created_at": serialize_datetime(run.created_at),
            "updated_at": serialize_datetime(run.updated_at),
            "started_at": serialize_datetime(run.started_at),
            "finished_at": serialize_datetime(run.finished_at),
            "duration_seconds": usage_metrics["duration_seconds"],
            "token_usage_by_model": dict(usage_metrics["token_usage_by_model"] or {}),
            "is_active": status in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value},
            "can_interrupt": status in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value},
            "can_delete": status not in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value},
        }

    def serialize_run_detail(self, run: Stage4Run) -> dict[str, Any]:
        payload = self.serialize_run_summary(run)
        run_history_rows = list(
            self.session.scalars(
                select(Stage4Run)
                .where(Stage4Run.source_savepoint_id == run.source_savepoint_id)
                .order_by(Stage4Run.created_at.desc(), Stage4Run.id.desc())
            )
        )
        run_history = [self.serialize_run_summary(row) for row in run_history_rows]
        source_context = self._source_context(run.source_savepoint)
        source_context["savepoint"] = {
            **dict(source_context.get("savepoint") or {}),
            "gold_patch_text": str(run.source_savepoint.gold_patch_text or ""),
        }
        payload.update(
            {
                "source": source_context,
                "run_history": run_history,
                "workspace_path": run.workspace_path,
                "runtime_snapshot": self._redacted_runtime_snapshot(run.runtime_snapshot_json),
                "workspace_manifest": dict((dict(run.runtime_snapshot_json or {}).get("stage4") or {}).get("workspace") or {}),
                "llm_completion_archives": self._serialized_llm_completion_archives(run),
                "events": [
                    {
                        "id": event.id,
                        "actor": event.actor,
                        "phase": event.phase,
                        "title": event.title,
                        "message": event.message,
                        "payload": dict(event.payload_json or {}),
                        "created_at": serialize_datetime(event.created_at),
                    }
                    for event in list(run.events or [])
                ],
                "issue_variants": [self._serialize_issue_variant(row) for row in list(run.issue_variants or [])],
            }
        )
        return payload

    def _serialized_llm_completion_archives(self, run: Stage4Run) -> dict[str, dict[str, Any]]:
        runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        try:
            roles = [spec.id for spec in runtime_issue_style_specs(runtime_stage4)]
        except IssueStyleConfigError:
            roles = []
        return {
            role: summarize_llm_completion_archive(runtime_dir, role)
            for role in roles
        }

    def update_run_runtime_snapshot(
        self,
        run_id: str,
        *,
        runtime_snapshot: dict[str, Any],
    ) -> Stage4Run:
        run = self.get_run(run_id, include_detail=False)
        if str(run.status or "") in {
            Stage4RunStatus.queued.value,
            Stage4RunStatus.running.value,
        }:
            raise Stage4RunConflictError(f"cannot modify runtime config for an active stage4 run: {run_id}")
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        if not is_current_generation_config(runtime_stage4):
            if str(run.status or "") == Stage4RunStatus.pending.value:
                self._ensure_current_generation_semantics(run)
                runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
            else:
                raise Stage4RunConflictError(
                    "cannot modify an obsolete completed Stage4 run; create a new run from the same savepoint"
                )
        merged_runtime = merge_stage4_runtime_snapshot(
            dict(runtime_stage4.get("runtime") or {}),
            runtime_snapshot,
        )
        hyperparameters = dict(merged_runtime.get("hyperparameters") or {})
        hyperparameters.pop("issue_variant_count", None)
        hyperparameters.pop("hint_variant_count", None)
        merged_runtime["hyperparameters"] = hyperparameters
        issue_styles = _runtime_issue_styles(runtime_stage4)
        run.issue_variant_count = len(issue_styles)
        run.hint_variant_count = 0
        runtime_stage4["runtime"] = merged_runtime
        runtime_stage4["issue_variant_count"] = len(issue_styles)
        runtime_stage4["issue_styles"] = issue_styles
        runtime_stage4.pop("hint_variant_count", None)
        runtime_stage4.pop("hint_strengths", None)
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage4": runtime_stage4,
        }
        run.summary_json = {
            **dict(run.summary_json or {}),
            "issue_variant_count": len(issue_styles),
        }
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def _validated_bundle_rows(
        self,
        run: Stage4Run,
        bundle: dict[str, Any],
    ) -> tuple[list[Stage4IssueVariant], dict[str, Any]]:
        issue_items = [dict(item) for item in list(bundle.get("issue_variants") or []) if isinstance(item, dict)]
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        expected_issue_styles = _runtime_issue_styles(runtime_stage4)
        if len(issue_items) != len(expected_issue_styles):
            raise Stage4RunConflictError(
                f"issuer returned {len(issue_items)} issue variant(s), expected {len(expected_issue_styles)}"
            )
        private_terms = _private_leakage_terms_for_source_context(self._source_context(run.source_savepoint))
        issue_rows: list[Stage4IssueVariant] = []
        for index, (item, expected_style) in enumerate(zip(issue_items, expected_issue_styles, strict=True), start=1):
            style = str(item.get("style") or expected_style).strip()
            if style != expected_style:
                raise Stage4RunConflictError(
                    f"issue variant {index} uses style {style!r}, expected {expected_style!r}"
                )
            title = str(item.get("title") or "").strip()
            markdown = str(item.get("issue_markdown") or "").strip()
            if not title or not markdown:
                raise Stage4RunConflictError(f"issue variant {index} is missing title or markdown")
            leakage = _leakage_check(
                _public_text_from_issue(item),
                gold_patch_text=run.source_savepoint.gold_patch_text,
                private_terms=private_terms,
            )
            if not leakage["passed"]:
                raise Stage4RunConflictError(
                    f"issue variant {index} leaks private patch or diagnostic details: {leakage['reasons']}"
                )
            issue_rows.append(
                Stage4IssueVariant(
                    variant_index=index,
                    style=style,
                    title=title,
                    issue_markdown=markdown,
                    issue_json=dict(item.get("issue_json") or {}),
                    quality_json=dict(item.get("quality_json") or {}),
                    leakage_check_json=leakage,
                )
            )
        return issue_rows, {
            "schema_valid": True,
            "leakage_valid": True,
        }

    def _source_context(self, savepoint: Stage3Savepoint) -> dict[str, Any]:
        run = savepoint.run
        entry_file = run.entry_file
        snapshot = entry_file.snapshot
        repository = snapshot.repository
        summary = dict(savepoint.summary_json or {})
        feedback = dict(summary.get("feedback") or {})
        diff_stats = _stage3_diff_stats_from_patch_text(savepoint.gold_patch_text)
        return {
            "repository": {
                "id": repository.id,
                "full_name": repository.full_name,
                "html_url": repository.html_url,
                "default_branch": repository.default_branch,
                "primary_language": repository.primary_language,
            },
            "snapshot": {
                "id": snapshot.id,
                "source_stage2_run_id": snapshot.source_stage2_run_id,
                "source_commit_sha": snapshot.source_commit_sha,
                "target_branch": snapshot.target_branch,
                "base_image": snapshot.base_image,
            },
            "entry_file": {
                "id": entry_file.id,
                "test_file_path": entry_file.test_file_path,
                "target_selector": entry_file.target_selector or entry_file.test_file_path,
                "baseline_total_tests": entry_file.baseline_total_tests,
                "baseline_pass_rate": entry_file.baseline_pass_rate,
            },
            "stage3_run": {
                "id": run.id,
                "status": run.status,
                "result": run.result,
            },
            "savepoint": {
                "id": savepoint.id,
                "depth": savepoint.depth,
                "entry_pass_rate": savepoint.entry_pass_rate,
                "p2p_files": list(savepoint.p2p_files_json or []),
                "f2p_files": list(savepoint.f2p_files_json or []),
                "p2p_count": len(list(savepoint.p2p_files_json or [])),
                "f2p_count": len(list(savepoint.f2p_files_json or [])),
                "feedback": feedback,
                "collateral": dict(savepoint.collateral_json or {}),
                "summary": {
                    key: value
                    for key, value in summary.items()
                    if key not in {"feedback"}
                },
                "diff_stats": diff_stats,
                "file_results": [
                    {
                        "test_file_path": row.test_file_path,
                        "target_selector": row.target_selector or row.test_file_path,
                        "is_entry_file": row.is_entry_file,
                        "status": row.status,
                        "total_tests": row.total_tests,
                        "passed_tests": row.passed_tests,
                        "failed_tests": row.failed_tests,
                        "error_tests": row.error_tests,
                        "skipped_tests": row.skipped_tests,
                        "pass_rate": row.pass_rate,
                    }
                    for row in list(savepoint.file_results or [])
                ],
                "created_at": serialize_datetime(savepoint.created_at),
                "updated_at": serialize_datetime(savepoint.updated_at),
            },
        }

    def _runtime_snapshot_seed(self) -> dict[str, Any]:
        api_key = self.settings.stage4_agent_llm_api_key()
        api_key_value = api_key.get_secret_value() if api_key is not None else ""
        return {
            "concurrency": {
                "max_concurrent_runs": self.settings.stage4_default_task_max_concurrent_runs
                or self.settings.stage4_max_concurrent_runs,
            },
            "issuer": {
                "model": self.settings.stage4_agent_llm_model() or "",
                "base_url": self.settings.stage4_agent_llm_base_url() or "",
                "api_key": api_key_value,
                "api_key_preview": f"{api_key_value[:6]}..." if api_key_value else "",
                "preset": self.settings.stage4_agent_openhands_preset(),
                "max_iterations": self.settings.stage4_agent_openhands_max_iterations(),
                "timeout_seconds": self.settings.stage4_agent_timeout(),
            },
            "hyperparameters": {
                "build_timeout_seconds": self.settings.stage4_build_timeout_seconds,
            },
        }

    def _refresh_workspace_manifest(self, run: Stage4Run) -> dict[str, Any]:
        workspace_dir = self.workspace_root / "runs" / run.id
        repo_dir = workspace_dir / "repo"
        metadata_dir = workspace_dir / ".stage4"
        assets_dir = workspace_dir / "assets"
        for directory in (workspace_dir, metadata_dir, assets_dir):
            directory.mkdir(parents=True, exist_ok=True)
        generated_files: list[str] = []
        for directory in (metadata_dir, assets_dir):
            if not directory.exists():
                continue
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    generated_files.append(str(path.relative_to(workspace_dir)))
        manifest = {
            "workspace_path": str(workspace_dir),
            "repo_dir": str(repo_dir),
            "metadata_dir": str(metadata_dir),
            "assets_dir": str(assets_dir),
            "generated_files": generated_files,
            "generated_file_count": len(generated_files),
            "prepared_at": serialize_datetime(_utcnow()),
        }
        (metadata_dir / "workspace_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        manifest["generated_files"] = sorted(set(generated_files + [".stage4/workspace_manifest.json"]))
        manifest["generated_file_count"] = len(manifest["generated_files"])
        run.workspace_path = str(workspace_dir)
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        runtime_stage4 = dict(runtime_snapshot.get("stage4") or {})
        runtime_stage4["workspace"] = manifest
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage4": runtime_stage4,
        }
        return manifest

    def _source_stage3_repo_dir(self, run: Stage3Run) -> Path | None:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        manifest = dict(runtime_stage3.get("workspace") or {})
        repo_dir = str(manifest.get("repo_dir") or "").strip()
        if repo_dir:
            return Path(repo_dir).expanduser()
        if run.workspace_path:
            return Path(str(run.workspace_path)).expanduser() / "repo"
        return None

    def _repository_clone_url(self, repository: GitHubRepository) -> str:
        return self._repository_materializer().repository_clone_url(repository)

    def _duration_seconds(self, run: Stage4Run) -> float | None:
        started_at = run.started_at
        finished_at = run.finished_at
        if started_at is None:
            return None
        if finished_at is None and str(run.status or "") in {Stage4RunStatus.queued.value, Stage4RunStatus.running.value}:
            finished_at = _utcnow()
        if finished_at is None:
            return None
        return max(0.0, (finished_at - started_at).total_seconds())

    def _serialized_run_usage_metrics(self, run: Stage4Run) -> dict[str, Any]:
        return {
            "duration_seconds": self._duration_seconds(run),
            "token_usage_by_model": self._own_run_token_usage_by_model(run),
        }

    def _own_run_token_usage_by_model(self, run: Stage4Run) -> dict[str, int]:
        summary = dict(run.summary_json or {})
        try:
            tokens = max(int(summary.get("issuer_token_usage") or 0), 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens <= 0:
            return {}
        model = str(summary.get("issuer_model") or "").strip()
        if model.lower() in {"", "unknown", "unknown_model"}:
            runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
            runtime_snapshot = dict(runtime_stage4.get("runtime") or {})
            model = str(((runtime_snapshot.get("issuer") or {}).get("model")) or "").strip()
        return aggregate_token_usage_by_model(
            [(model, tokens)],
            preferred_models=[self._runtime_token_usage_model(run)],
        )

    def _runtime_token_usage_model(self, run: Stage4Run) -> str:
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        runtime_snapshot = dict(runtime_stage4.get("runtime") or {})
        return str(((runtime_snapshot.get("issuer") or {}).get("model")) or "").strip()

    def _serialize_issue_variant(self, row: Stage4IssueVariant) -> dict[str, Any]:
        return {
            "id": row.id,
            "variant_index": row.variant_index,
            "style": row.style,
            "title": row.title,
            "issue_markdown": row.issue_markdown,
            "issue_json": dict(row.issue_json or {}),
            "quality": dict(row.quality_json or {}),
            "leakage_check": dict(row.leakage_check_json or {}),
            "created_at": serialize_datetime(row.created_at),
        }

    def _redacted_runtime_snapshot(self, snapshot: dict[str, Any] | None) -> dict[str, Any]:
        redacted = dict(snapshot or {})
        stage4 = dict(redacted.get("stage4") or {})
        runtime = dict(stage4.get("runtime") or {})
        issuer = dict(runtime.get("issuer") or {})
        api_key = str(issuer.get("api_key") or "")
        api_key_preview = str(issuer.get("api_key_preview") or "")
        issuer.pop("api_key", None)
        if api_key and not api_key_preview:
            api_key_preview = f"{api_key[:6]}..."
        if api_key_preview:
            issuer["api_key_preview"] = api_key_preview
        if issuer:
            runtime["issuer"] = issuer
        if runtime:
            stage4["runtime"] = runtime
        if stage4:
            redacted["stage4"] = stage4
        return redacted

    def _make_run_event(
        self,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> Stage4RunEvent:
        return Stage4RunEvent(
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload_json=dict(payload or {}),
        )

    def _emit_or_append_run_event(
        self,
        run: Stage4Run,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        if emit_event is not None:
            emit_event(actor=actor, phase=phase, title=title, message=message, payload=payload or {})
            return
        run.events.append(
            self._make_run_event(
                actor=actor,
                phase=phase,
                title=title,
                message=message,
                payload=payload or {},
            )
        )

    def _run_checked_command(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        error_message: str,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
        if completed.returncode != 0:
            output = "\n".join(
                part
                for part in [completed.stdout.strip(), completed.stderr.strip()]
                if part
            )
            if output:
                raise Stage4RunConflictError(f"{error_message}: {output[-4000:]}")
            raise Stage4RunConflictError(error_message)
        return completed

    def _run_command_with_git_progress(
        self,
        args: list[str],
        *,
        error_message: str,
        run: Stage4Run,
        phase: str | None,
        progress_title: str,
        progress_payload: dict[str, Any],
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        self._raise_if_cancel_requested(cancel_requested)
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        if process.stdout is None:
            raise Stage4RunConflictError(f"{error_message}: failed to capture git output")

        output_lines: list[str] = []
        line_buffer: list[str] = []
        last_emit_at = 0.0
        last_percent_by_stage: dict[str, int] = {}
        started_at = time.monotonic()

        def flush_line() -> None:
            nonlocal last_emit_at
            line = "".join(line_buffer).strip()
            line_buffer.clear()
            if not line:
                return
            output_lines.append(line)
            progress = _parse_git_progress_line(line)
            if progress is None:
                return
            if not self._should_emit_git_progress(progress, last_percent_by_stage, last_emit_at):
                return
            last_emit_at = time.monotonic()
            last_percent_by_stage[str(progress["git_stage"])] = int(progress["percent"])
            payload = dict(progress_payload)
            payload.update(progress)
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase=phase,
                title=progress_title,
                message=str(progress["raw_line"]),
                payload=payload,
                emit_event=emit_event,
            )

        while True:
            if cancel_requested is not None and cancel_requested():
                process.kill()
                process.wait(timeout=5)
                raise Stage4RunConflictError("stage4 run interrupted by user")
            if timeout_seconds > 0 and (time.monotonic() - started_at) > timeout_seconds:
                process.kill()
                process.wait(timeout=5)
                raise Stage4RunConflictError(f"{error_message}: command timed out after {timeout_seconds:.0f}s")
            ready, _, _ = select_module.select([process.stdout], [], [], 0.2)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            chunk = process.stdout.read(1)
            if chunk == "":
                if process.poll() is not None:
                    break
                continue
            if chunk in {"\r", "\n"}:
                flush_line()
            else:
                line_buffer.append(chunk)
        flush_line()

        return_code = process.wait()
        output = "\n".join(output_lines)
        if return_code != 0:
            details = "\n".join(output_lines[-20:]).strip() or "command failed"
            raise Stage4RunConflictError(f"{error_message}: {details}")
        return subprocess.CompletedProcess(args, return_code, stdout=output, stderr="")

    def _should_emit_git_progress(
        self,
        progress: dict[str, Any],
        last_percent_by_stage: dict[str, int],
        last_emit_at: float,
    ) -> bool:
        stage = str(progress["git_stage"])
        percent = int(progress["percent"])
        last_percent = last_percent_by_stage.get(stage)
        if last_percent is None:
            return True
        if percent >= 100 and last_percent < 100:
            return True
        if percent - last_percent >= _GIT_PROGRESS_PERCENT_STEP:
            return True
        return time.monotonic() - last_emit_at >= _GIT_PROGRESS_MIN_INTERVAL_SECONDS and percent != last_percent

    def _run_git_remote_command_with_progress(
        self,
        args: list[str],
        *,
        error_message: str,
        run: Stage4Run,
        phase: str | None,
        progress_title: str,
        retry_title: str,
        progress_payload: dict[str, Any],
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        cleanup_after_failed_attempt: Callable[[], None] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        max_attempts = github_proxy_retry_attempts(args, minimum=_GIT_REMOTE_MAX_ATTEMPTS)
        for attempt in range(1, max_attempts + 1):
            self._raise_if_cancel_requested(cancel_requested)
            refresh_github_proxy_urls(args)
            payload = dict(progress_payload)
            payload.update(
                {
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                }
            )
            try:
                return self._run_command_with_git_progress(
                    args,
                    error_message=error_message,
                    run=run,
                    phase=phase,
                    progress_title=progress_title,
                    progress_payload=payload,
                    emit_event=emit_event,
                    cancel_requested=cancel_requested,
                    cwd=cwd,
                    timeout_seconds=timeout_seconds,
                )
            except Stage4RunConflictError as exc:
                if cleanup_after_failed_attempt is not None:
                    cleanup_after_failed_attempt()
                retryable = _is_retryable_git_remote_error(str(exc))
                failed_proxy, next_proxy = (
                    rotate_failed_github_proxy_urls(args) if retryable else (None, None)
                )
                if attempt >= max_attempts or not retryable:
                    raise
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title=retry_title,
                    message=(
                        "Git remote operation failed with a retryable network error; "
                        f"retrying attempt {attempt + 1}/{max_attempts}"
                    ),
                    payload={
                        **progress_payload,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "retry_delay_seconds": _GIT_REMOTE_RETRY_DELAY_SECONDS,
                        "failed_github_proxy_prefix": failed_proxy,
                        "next_github_proxy_prefix": next_proxy,
                        "error": str(exc)[-4000:],
                    },
                    emit_event=emit_event,
                )
                for _ in range(int(_GIT_REMOTE_RETRY_DELAY_SECONDS * 10)):
                    self._raise_if_cancel_requested(cancel_requested)
                    time.sleep(0.1)
        raise Stage4RunConflictError(error_message)

    def _raise_if_cancel_requested(self, cancel_requested: Callable[[], bool] | None) -> None:
        if cancel_requested is not None and cancel_requested():
            raise Stage4RunConflictError("stage4 run interrupted by user")


def stage4_source_run_counts(session: Session) -> dict[int, int]:
    rows = session.execute(
        select(Stage4Run.source_savepoint_id, func.count(Stage4Run.id)).group_by(Stage4Run.source_savepoint_id)
    )
    return {int(savepoint_id): int(count or 0) for savepoint_id, count in rows}