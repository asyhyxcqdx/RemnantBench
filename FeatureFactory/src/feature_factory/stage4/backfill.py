from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from sqlalchemy import and_, func, inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from feature_factory.config import Settings, get_settings
from feature_factory.data_pool import (
    CURRENT_ISSUE_SCHEMA_VERSION,
    DataPoolService,
    _asset_materialize_lock,
    _asset_materialize_lock_dir,
    _copy_llm_completion_archive,
    _register_replaced_directory,
)
from feature_factory.db import build_engine, build_session_factory, init_db
from feature_factory.models import (
    DataPool,
    DataPoolAsset,
    GitHubRepository,
    Stage2Run,
    Stage3SavepointFileResult,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3Savepoint,
    Stage4CleanupTombstone,
    Stage4DataPoolBackfillJob,
    Stage4DataPoolBackfillUnit,
    Stage4HintVariant,
    Stage4IssueVariant,
    Stage4Run,
    Stage4RunEvent,
    Stage4RunResult,
    Stage4RunStatus,
    Stage4RuntimeTemplate,
)
from feature_factory.stage2.raw_archive import runtime_dir_from_workspace_path
from feature_factory.stage4.backend import (
    evaluate_stage4_backend_readiness,
    stage4_settings_with_runtime_snapshot,
)
from feature_factory.stage4.issue_styles import load_issue_style_catalog
from feature_factory.stage4.runner import Stage4RunRunner
from feature_factory.stage4.service import Stage4RunConflictError, Stage4Service


REQUIRED_SOURCE_FILES = (
    "manifest.json",
    "issues.json",
    "hints.json",
    "dockerfile",
    "run_script.sh",
    "gold.patch",
    "original_p2p_files.json",
    "savepoint_feedback.json",
    "savepoint_full_validation.json",
    "hidden_f2p_files.json",
    "p2p_file_snapshots.json",
    "stage2_full_report.json",
)
NONEMPTY_SOURCE_FILES = (
    "manifest.json",
    "issues.json",
    "dockerfile",
    "run_script.sh",
    "gold.patch",
)
REQUIRED_TARGET_FILES = tuple(name for name in REQUIRED_SOURCE_FILES if name != "hints.json")
ACTIVE_STAGE4_STATUSES = {
    Stage4RunStatus.pending.value,
    Stage4RunStatus.queued.value,
    Stage4RunStatus.running.value,
}
TERMINAL_UNIT_STATUSES = {"completed", "skipped", "failed"}
LEASE_STALE_AFTER = timedelta(minutes=10)
REPORT_ROOT = Path("./data/stage4-backfills")
_RESUME_REQUIRED_MODELS = (
    GitHubRepository,
    Stage2Run,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3Savepoint,
    Stage3SavepointFileResult,
    Stage4RuntimeTemplate,
    Stage4CleanupTombstone,
    Stage4Run,
    Stage4RunEvent,
    Stage4IssueVariant,
    Stage4HintVariant,
    DataPool,
    DataPoolAsset,
    Stage4DataPoolBackfillJob,
    Stage4DataPoolBackfillUnit,
)
_RESUME_REQUIRED_ACTIVE_INDEXES = {
    "uq_stage2_runs_repository_active": ("stage2_runs", ("repository_id",)),
    "uq_stage3_runs_entry_file_active": ("stage3_runs", ("entry_file_id",)),
    "uq_stage4_runs_source_savepoint_active": ("stage4_runs", ("source_savepoint_id",)),
}


class BackfillError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EligibleAsset:
    asset_id: str
    source_stage2_run_id: str
    source_stage3_run_id: str
    source_stage4_run_id: str
    source_savepoint_id: int
    github_repo_id: int
    entry_file_path: str
    depth: int
    folder_name: str
    folder_path: str
    repo_full_name: str
    source_commit_sha: str

    def inventory_payload(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "source_stage2_run_id": self.source_stage2_run_id,
            "source_stage3_run_id": self.source_stage3_run_id,
            "source_stage4_run_id": self.source_stage4_run_id,
            "source_savepoint_id": self.source_savepoint_id,
            "github_repo_id": self.github_repo_id,
            "entry_file_path": self.entry_file_path,
            "depth": self.depth,
            "folder_name": self.folder_name,
            "folder_path": self.folder_path,
            "repo_full_name": self.repo_full_name,
            "source_commit_sha": self.source_commit_sha,
        }


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackfillError(f"failed to read JSON file {path}: {exc}") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_pool(session: Session, selector: str) -> DataPool:
    normalized = str(selector or "").strip()
    if not normalized:
        raise BackfillError("data pool name or id is required")
    pool = session.get(DataPool, normalized)
    if pool is None:
        pool = session.scalar(select(DataPool).where(DataPool.name == normalized).limit(1))
    if pool is None:
        raise BackfillError(f"data pool not found: {normalized}")
    return pool


def _resolve_runtime_template(session: Session, selector: str) -> Stage4RuntimeTemplate:
    normalized = str(selector or "").strip()
    if not normalized:
        raise BackfillError("--runtime-template is required")
    row = session.get(Stage4RuntimeTemplate, normalized)
    if row is None:
        row = session.scalar(
            select(Stage4RuntimeTemplate).where(Stage4RuntimeTemplate.name == normalized).limit(1)
        )
    if row is None:
        raise BackfillError(f"Stage4 runtime template not found: {normalized}")
    return row


def _parse_styles(raw: str | Sequence[str]) -> list[str]:
    values = raw.split(",") if isinstance(raw, str) else list(raw)
    styles = [str(value or "").strip() for value in values]
    if not styles or any(not value for value in styles):
        raise BackfillError("--styles must contain one or more comma-separated style ids")
    if len(set(styles)) != len(styles):
        raise BackfillError("--styles must not contain duplicates")
    try:
        load_issue_style_catalog(enabled_styles=styles)
    except Exception as exc:  # noqa: BLE001
        raise BackfillError(f"invalid Stage4 styles: {exc}") from exc
    return styles


def _runtime_with_concurrency(
    snapshot: dict[str, Any],
    concurrency: int | None,
    *,
    settings: Settings,
) -> dict[str, Any]:
    resolved = json.loads(json.dumps(snapshot or {}))
    if concurrency is None:
        return resolved
    value = int(concurrency)
    if value < 1 or value > int(settings.stage4_max_concurrent_runs_cap):
        raise BackfillError(
            f"concurrency must be between 1 and {settings.stage4_max_concurrent_runs_cap}"
        )
    runtime_concurrency = dict(resolved.get("concurrency") or {})
    runtime_concurrency["max_concurrent_runs"] = value
    resolved["concurrency"] = runtime_concurrency
    return resolved


def _validate_source_asset_files(asset: DataPoolAsset) -> dict[str, Any]:
    folder = Path(asset.folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise BackfillError(f"source asset directory is missing: {folder}")
    for name in REQUIRED_SOURCE_FILES:
        path = folder / name
        if not path.is_file():
            raise BackfillError(f"source asset {asset.id} is missing {name}")
    for name in NONEMPTY_SOURCE_FILES:
        path = folder / name
        if path.stat().st_size <= 0:
            raise BackfillError(f"source asset {asset.id} has empty {name}")
    manifest = _read_json(folder / "manifest.json")
    if not isinstance(manifest, dict):
        raise BackfillError(f"source asset {asset.id} manifest.json must be an object")
    expected = {
        "asset_id": asset.id,
        "data_pool_id": asset.pool_id,
        "stage2_run_id": asset.stage2_run_id,
        "stage3_run_id": asset.stage3_run_id,
        "stage3_savepoint_id": asset.stage3_savepoint_id,
        "stage4_run_id": asset.stage4_run_id,
        "commit": asset.source_commit_sha,
        "entry_file": asset.entry_file_path,
        "depth": int(asset.depth),
    }
    mismatches = {
        key: {"manifest": manifest.get(key), "database": value}
        for key, value in expected.items()
        if manifest.get(key) != value
    }
    repository = dict(manifest.get("repository") or {})
    if repository.get("github_repo_id") != asset.github_repo_id:
        mismatches["repository.github_repo_id"] = {
            "manifest": repository.get("github_repo_id"),
            "database": asset.github_repo_id,
        }
    if mismatches:
        raise BackfillError(f"source asset {asset.id} manifest/database mismatch: {mismatches}")
    return manifest


def _expected_issue_style_contract(job: Stage4DataPoolBackfillJob) -> tuple[int, list[str], str]:
    styles = _parse_styles(list(job.styles_json or []))
    catalog = load_issue_style_catalog(enabled_styles=styles)
    catalog_sha256 = str(dict(job.stats_json or {}).get("issue_style_catalog_sha256") or "").strip()
    if not catalog_sha256:
        raise BackfillError("backfill job is missing its frozen issue-style catalog hash")
    return int(catalog.schema_version), styles, catalog_sha256


def _target_asset_has_issue_style_contract(
    session: Session,
    asset: DataPoolAsset,
    *,
    schema_version: int,
    styles: Sequence[str],
    catalog_sha256: str,
) -> bool:
    run_id = str(asset.stage4_run_id or "").strip()
    run = session.get(Stage4Run, run_id) if run_id else None
    if (
        run is None
        or run.status != Stage4RunStatus.completed.value
        or run.result != Stage4RunResult.generated.value
    ):
        return False
    generation_config = dict(
        dict((run.runtime_snapshot_json or {}).get("stage4") or {}).get("generation_config")
        or {}
    )
    if (
        generation_config.get("schema_version") != int(schema_version)
        or generation_config.get("enabled_styles") != list(styles)
        or str(generation_config.get("catalog_sha256") or "").strip() != catalog_sha256
    ):
        return False
    folder = Path(asset.folder_path).expanduser()
    manifest = _read_json(folder / "manifest.json") if (folder / "manifest.json").is_file() else None
    issues = _read_json(folder / "issues.json") if (folder / "issues.json").is_file() else None
    if not folder.is_dir() or not isinstance(manifest, dict) or not isinstance(issues, list):
        return False
    if (
        manifest.get("issue_schema_version") != CURRENT_ISSUE_SCHEMA_VERSION
        or manifest.get("asset_id") != asset.id
        or manifest.get("data_pool_id") != asset.pool_id
        or manifest.get("stage4_run_id") != run.id
    ):
        return False
    issue_styles = [str(item.get("style") or "") for item in issues if isinstance(item, dict)]
    return issue_styles == list(styles)


def _find_target_asset(
    session: Session,
    *,
    pool_id: str,
    github_repo_id: int,
    entry_file_path: str,
    depth: int,
) -> DataPoolAsset | None:
    return session.scalar(
        select(DataPoolAsset)
        .where(
            DataPoolAsset.pool_id == pool_id,
            DataPoolAsset.github_repo_id == int(github_repo_id),
            DataPoolAsset.entry_file_path == entry_file_path,
            DataPoolAsset.depth == int(depth),
        )
        .limit(1)
    )


def _validate_compatible_target_asset(
    session: Session,
    *,
    job: Stage4DataPoolBackfillJob,
    unit: Stage4DataPoolBackfillUnit,
    asset: DataPoolAsset,
) -> dict[str, Any]:
    schema_version, styles, catalog_sha256 = _expected_issue_style_contract(job)
    run_id = str(asset.stage4_run_id or "").strip()
    run = session.get(Stage4Run, run_id) if run_id else None
    if run is None:
        raise BackfillError(f"target asset {asset.id} has no persisted Stage4 run")
    if run.status != Stage4RunStatus.completed.value or run.result != Stage4RunResult.generated.value:
        raise BackfillError(f"target asset {asset.id} does not reference a generated Stage4 run")
    generation_config = dict(
        dict((run.runtime_snapshot_json or {}).get("stage4") or {}).get("generation_config")
        or {}
    )
    actual_contract = (
        generation_config.get("schema_version"),
        generation_config.get("enabled_styles"),
        str(generation_config.get("catalog_sha256") or "").strip(),
    )
    expected_contract = (schema_version, styles, catalog_sha256)
    if actual_contract != expected_contract:
        raise BackfillError(
            f"target asset {asset.id} has an incompatible issue-style contract: "
            f"expected={expected_contract}, actual={actual_contract}"
        )

    target_pool = session.get(DataPool, job.target_pool_id)
    if target_pool is None:
        raise BackfillError("backfill target pool disappeared")
    folder = Path(asset.folder_path).expanduser().resolve()
    target_root = Path(target_pool.root_path).expanduser().resolve()
    if folder.parent != target_root or folder.name != asset.folder_name or not folder.is_dir():
        raise BackfillError(f"target asset {asset.id} has an invalid or missing asset directory")
    for name in REQUIRED_TARGET_FILES:
        if not (folder / name).is_file():
            raise BackfillError(f"target asset {asset.id} is missing {name}")
    for name in NONEMPTY_SOURCE_FILES:
        if (folder / name).stat().st_size <= 0:
            raise BackfillError(f"target asset {asset.id} has empty {name}")

    manifest = _read_json(folder / "manifest.json")
    if not isinstance(manifest, dict):
        raise BackfillError(f"target asset {asset.id} manifest.json must be an object")
    expected_manifest = {
        "asset_id": asset.id,
        "data_pool_id": job.target_pool_id,
        "stage4_run_id": run.id,
        "entry_file": unit.entry_file_path,
        "depth": int(unit.depth),
        "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
    }
    mismatches = {
        key: {"manifest": manifest.get(key), "expected": value}
        for key, value in expected_manifest.items()
        if manifest.get(key) != value
    }
    repository = dict(manifest.get("repository") or {})
    if repository.get("github_repo_id") != int(unit.github_repo_id):
        mismatches["repository.github_repo_id"] = {
            "manifest": repository.get("github_repo_id"),
            "expected": int(unit.github_repo_id),
        }
    if mismatches:
        raise BackfillError(f"target asset {asset.id} manifest mismatch: {mismatches}")

    issues = _read_json(folder / "issues.json")
    if not isinstance(issues, list):
        raise BackfillError(f"target asset {asset.id} issues.json must be an array")
    issue_styles = [str(item.get("style") or "") for item in issues if isinstance(item, dict)]
    if issue_styles != styles:
        raise BackfillError(
            f"target asset {asset.id} issue styles do not match: expected={styles}, actual={issue_styles}"
        )
    for style in styles:
        archive_dir = folder / "llm" / style
        if not archive_dir.is_dir() or not any(archive_dir.rglob("*.json")):
            raise BackfillError(f"target asset {asset.id} is missing the {style} LLM completion archive")
    return {
        "passed": True,
        "mode": "reused_existing_target",
        "reason": "target_grain_already_exists",
        "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
        "styles": styles,
        "stage4_run_id": run.id,
    }


def _reuse_existing_target_asset(
    session: Session,
    *,
    job: Stage4DataPoolBackfillJob,
    unit: Stage4DataPoolBackfillUnit,
) -> DataPoolAsset | None:
    target_pool = session.get(DataPool, job.target_pool_id)
    if target_pool is None:
        raise BackfillError("backfill target pool disappeared")
    target_root = Path(target_pool.root_path).expanduser().resolve()
    lock_dir = _asset_materialize_lock_dir(
        target_root / ".tmp",
        pool_id=target_pool.id,
        github_repo_id=int(unit.github_repo_id),
        entry_file_path=unit.entry_file_path,
        depth=int(unit.depth),
    )
    with _asset_materialize_lock(lock_dir, session=session):
        asset = _find_target_asset(
            session,
            pool_id=target_pool.id,
            github_repo_id=unit.github_repo_id,
            entry_file_path=unit.entry_file_path,
            depth=unit.depth,
        )
        if asset is None:
            return None
        verification = _validate_compatible_target_asset(
            session,
            job=job,
            unit=unit,
            asset=asset,
        )
        unit.status = "skipped"
        unit.stage4_run_id = str(asset.stage4_run_id or "") or None
        unit.target_asset_id = asset.id
        unit.verification_json = verification
        unit.error_message = None
        unit.finished_at = _utcnow()
        session.flush()
        return asset


def select_eligible_assets(session: Session, source_pool_id: str) -> tuple[list[EligibleAsset], list[dict[str, str]]]:
    stmt = (
        select(DataPoolAsset)
        .join(
            Stage3Savepoint,
            and_(
                Stage3Savepoint.id == DataPoolAsset.stage3_savepoint_id,
                Stage3Savepoint.depth == DataPoolAsset.depth,
                func.length(func.trim(Stage3Savepoint.gold_patch_text)) > 0,
            ),
        )
        .join(
            Stage3Run,
            and_(
                Stage3Run.id == DataPoolAsset.stage3_run_id,
                Stage3Run.id == Stage3Savepoint.run_id,
            ),
        )
        .join(
            Stage3EntryFile,
            and_(
                Stage3EntryFile.id == Stage3Run.entry_file_id,
                Stage3EntryFile.test_file_path == DataPoolAsset.entry_file_path,
            ),
        )
        .join(
            Stage3CommitSnapshot,
            and_(
                Stage3CommitSnapshot.id == Stage3EntryFile.snapshot_id,
                Stage3CommitSnapshot.source_stage2_run_id == DataPoolAsset.stage2_run_id,
                Stage3CommitSnapshot.source_commit_sha == DataPoolAsset.source_commit_sha,
            ),
        )
        .join(Stage2Run, Stage2Run.id == DataPoolAsset.stage2_run_id)
        .join(
            GitHubRepository,
            and_(
                GitHubRepository.id == Stage3CommitSnapshot.repository_id,
                GitHubRepository.id == DataPoolAsset.repository_id,
                GitHubRepository.github_repo_id == DataPoolAsset.github_repo_id,
            ),
        )
        .join(
            Stage4Run,
            and_(
                Stage4Run.id == DataPoolAsset.stage4_run_id,
                Stage4Run.source_savepoint_id == Stage3Savepoint.id,
                Stage4Run.status == Stage4RunStatus.completed.value,
                Stage4Run.result == Stage4RunResult.generated.value,
            ),
        )
        .where(DataPoolAsset.pool_id == source_pool_id)
        .order_by(
            DataPoolAsset.repo_full_name.asc(),
            DataPoolAsset.entry_file_path.asc(),
            DataPoolAsset.depth.asc(),
            DataPoolAsset.id.asc(),
        )
    )
    candidates = list(session.scalars(stmt))
    eligible: list[EligibleAsset] = []
    rejected: list[dict[str, str]] = []
    for asset in candidates:
        try:
            _validate_source_asset_files(asset)
        except BackfillError as exc:
            rejected.append({"asset_id": asset.id, "reason": str(exc)})
            continue
        eligible.append(
            EligibleAsset(
                asset_id=asset.id,
                source_stage2_run_id=str(asset.stage2_run_id),
                source_stage3_run_id=str(asset.stage3_run_id),
                source_stage4_run_id=str(asset.stage4_run_id),
                source_savepoint_id=int(asset.stage3_savepoint_id),
                github_repo_id=int(asset.github_repo_id),
                entry_file_path=asset.entry_file_path,
                depth=int(asset.depth),
                folder_name=asset.folder_name,
                folder_path=str(Path(asset.folder_path).expanduser().resolve()),
                repo_full_name=asset.repo_full_name,
                source_commit_sha=str(asset.source_commit_sha or ""),
            )
        )
    return eligible, rejected


def _inventory_bytes(items: Sequence[EligibleAsset]) -> bytes:
    return b"".join(_json_bytes(item.inventory_payload()) + b"\n" for item in items)


def _inventory_sha256(items: Sequence[EligibleAsset]) -> str:
    return hashlib.sha256(_inventory_bytes(items)).hexdigest()


def _tree_size(paths: Iterable[Path]) -> int:
    total = 0
    for root in paths:
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_size
            except FileNotFoundError:
                continue
    return total


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_target(
    session: Session,
    *,
    source_pool: DataPool,
    target_name: str,
    target_root: Path,
) -> DataPool | None:
    source_root = Path(source_pool.root_path).expanduser().resolve()
    target_root = target_root.expanduser().resolve()
    if source_root == target_root or _is_relative_to(target_root, source_root) or _is_relative_to(source_root, target_root):
        raise BackfillError("source and target data pool roots must be disjoint")
    existing = session.scalar(select(DataPool).where(DataPool.name == target_name).limit(1))
    if existing is None:
        if target_root.exists() and (not target_root.is_dir() or any(target_root.iterdir())):
            raise BackfillError("new target root must not exist or must be an empty directory")
        return None
    if Path(existing.root_path).expanduser().resolve() != target_root:
        raise BackfillError(
            f"target pool {target_name!r} already uses a different root: {existing.root_path}"
        )
    bind = session.get_bind()
    if inspect(bind).has_table(Stage4DataPoolBackfillJob.__tablename__):
        existing_job_id = session.scalar(
            select(Stage4DataPoolBackfillJob.id)
            .where(Stage4DataPoolBackfillJob.target_pool_id == existing.id)
            .limit(1)
        )
        if existing_job_id is not None:
            raise BackfillError(
                f"target pool already belongs to backfill job {existing_job_id}; use resume"
            )
    return existing


def preflight(
    session: Session,
    settings: Settings,
    *,
    source_pool_selector: str,
    target_pool_name: str,
    target_root: Path,
    runtime_template_selector: str,
    styles: Sequence[str],
    concurrency: int | None = None,
    limit: int | None = None,
    calculate_size: bool = True,
) -> dict[str, Any]:
    source_pool = _resolve_pool(session, source_pool_selector)
    source_root = Path(source_pool.root_path).expanduser().resolve()
    if not source_root.is_dir():
        raise BackfillError(f"source pool root is missing: {source_root}")
    target = _validate_target(
        session,
        source_pool=source_pool,
        target_name=target_pool_name,
        target_root=target_root,
    )
    template = _resolve_runtime_template(session, runtime_template_selector)
    parsed_styles = _parse_styles(styles)
    catalog = load_issue_style_catalog(enabled_styles=parsed_styles)
    runtime_snapshot = _runtime_with_concurrency(
        dict(template.snapshot_json or {}),
        concurrency,
        settings=settings,
    )
    runtime_settings = stage4_settings_with_runtime_snapshot(settings, runtime_snapshot)
    readiness = evaluate_stage4_backend_readiness(runtime_settings)
    if not readiness.get("ready"):
        raise BackfillError(str(readiness.get("message") or "Stage4 backend is not ready"))
    full_inventory, rejected_files = select_eligible_assets(session, source_pool.id)
    if not full_inventory:
        raise BackfillError("source pool has no assets with a complete Stage4 rerun chain")
    source_eligible_count = len(full_inventory)
    already_satisfied: list[EligibleAsset] = []
    incompatible_targets: list[EligibleAsset] = []
    if target is not None:
        target_assets = {
            (int(asset.github_repo_id), asset.entry_file_path, int(asset.depth)): asset
            for asset in session.scalars(
                select(DataPoolAsset).where(DataPoolAsset.pool_id == target.id)
            )
        }
        remaining_inventory: list[EligibleAsset] = []
        for item in full_inventory:
            existing_asset = target_assets.get(
                (int(item.github_repo_id), item.entry_file_path, int(item.depth))
            )
            if existing_asset is None:
                remaining_inventory.append(item)
                continue
            if _target_asset_has_issue_style_contract(
                session,
                existing_asset,
                schema_version=int(catalog.schema_version),
                styles=parsed_styles,
                catalog_sha256=catalog.enabled_contract_sha256(),
            ):
                already_satisfied.append(item)
            else:
                incompatible_targets.append(item)
        full_inventory = remaining_inventory
    if incompatible_targets:
        raise BackfillError(
            f"target pool contains {len(incompatible_targets)} incompatible asset(s) at source inventory grains"
        )
    if not full_inventory:
        raise BackfillError(
            f"all eligible source assets are already satisfied in target pool ({len(already_satisfied)})"
        )
    normalized_limit = None if limit is None else int(limit)
    if normalized_limit is not None and normalized_limit < 1:
        raise BackfillError("--limit must be at least 1")
    inventory = (
        full_inventory[:normalized_limit]
        if normalized_limit is not None
        else full_inventory
    )
    active_count = session.scalar(
        select(func.count(Stage4Run.id)).where(
            Stage4Run.source_savepoint_id.in_([item.source_savepoint_id for item in inventory]),
            Stage4Run.status.in_(tuple(ACTIVE_STAGE4_STATUSES)),
        )
    ) or 0
    if active_count:
        raise BackfillError(
            f"{active_count} eligible savepoint(s) already have active Stage4 runs; wait for them to finish"
        )
    total_assets = session.scalar(
        select(func.count(DataPoolAsset.id)).where(DataPoolAsset.pool_id == source_pool.id)
    ) or 0
    payload_size = _tree_size(Path(item.folder_path) for item in inventory) if calculate_size else 0
    return {
        "source_pool": source_pool,
        "target_pool": target,
        "target_root": target_root.expanduser().resolve(),
        "runtime_template": template,
        "runtime_snapshot": runtime_snapshot,
        "styles": parsed_styles,
        "inventory": inventory,
        "inventory_sha256": _inventory_sha256(inventory),
        "total_source_assets": int(total_assets),
        "source_eligible_count": source_eligible_count,
        "eligible_count": len(inventory),
        "available_eligible_count": len(full_inventory),
        "already_satisfied_count": len(already_satisfied),
        "limited_out_count": len(full_inventory) - len(inventory),
        "limit": normalized_limit,
        "excluded_count": int(total_assets) - source_eligible_count,
        "rejected_file_count": len(rejected_files),
        "rejected_files": rejected_files,
        "payload_size_bytes": payload_size,
        "backend": readiness,
    }


def _ensure_target_pool(
    session: Session,
    *,
    existing: DataPool | None,
    name: str,
    root: Path,
    job_id: str,
) -> DataPool:
    if existing is not None:
        return existing
    if root.exists():
        if not root.is_dir() or any(root.iterdir()):
            raise BackfillError("target root changed after preflight")
    else:
        root.mkdir(parents=True)
    pool = DataPool(
        name=name,
        root_path=str(root),
        description=f"Stage4 DataPool backfill target for job {job_id}",
        stats_json={"asset_count": 0, "repo_count": 0, "entry_file_count": 0},
    )
    session.add(pool)
    session.flush()
    return pool


def _report_dir(job_id: str) -> Path:
    return REPORT_ROOT.expanduser().resolve() / job_id


def create_job(session: Session, preflight_result: dict[str, Any], *, target_name: str) -> Stage4DataPoolBackfillJob:
    job_id = str(uuid.uuid4())
    target_root = Path(preflight_result["target_root"])
    target_existed = target_root.exists()
    try:
        target_pool = _ensure_target_pool(
            session,
            existing=preflight_result["target_pool"],
            name=target_name,
            root=target_root,
            job_id=job_id,
        )
        source_pool: DataPool = preflight_result["source_pool"]
        template: Stage4RuntimeTemplate = preflight_result["runtime_template"]
        inventory: list[EligibleAsset] = preflight_result["inventory"]
        catalog = load_issue_style_catalog(enabled_styles=preflight_result["styles"])
        job = Stage4DataPoolBackfillJob(
            id=job_id,
            source_pool_id=source_pool.id,
            target_pool_id=target_pool.id,
            runtime_template_id=template.id,
            runtime_template_name=template.name,
            runtime_snapshot_json=dict(preflight_result["runtime_snapshot"]),
            styles_json=list(preflight_result["styles"]),
            inventory_sha256=preflight_result["inventory_sha256"],
            eligible_count=len(inventory),
            status="pending",
            stats_json={
                "total": len(inventory),
                "completed": 0,
                "skipped": 0,
                "migrated_count": 0,
                "already_present_count": 0,
                "satisfied_count": 0,
                "failed": 0,
                "inventory_limit": preflight_result.get("limit"),
                "available_eligible_count": int(
                    preflight_result.get("available_eligible_count", len(inventory))
                ),
                "preflight_already_satisfied_count": int(
                    preflight_result.get("already_satisfied_count", 0)
                ),
                "issue_style_catalog_sha256": catalog.enabled_contract_sha256(),
                "payload_size_bytes": int(preflight_result["payload_size_bytes"]),
            },
        )
        session.add(job)
        session.flush()
        for position, item in enumerate(inventory, start=1):
            session.add(
                Stage4DataPoolBackfillUnit(
                    job_id=job.id,
                    position=position,
                    source_asset_id=item.asset_id,
                    source_stage4_run_id=item.source_stage4_run_id,
                    source_savepoint_id=item.source_savepoint_id,
                    github_repo_id=item.github_repo_id,
                    entry_file_path=item.entry_file_path,
                    depth=item.depth,
                    source_folder_name=item.folder_name,
                    source_folder_path=item.folder_path,
                    status="pending",
                )
            )
        session.flush()
        session.commit()
    except Exception:
        session.rollback()
        if not target_existed and target_root.exists():
            shutil.rmtree(target_root, ignore_errors=True)
        raise

    report_dir = _report_dir(job_id)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "inventory.jsonl").write_bytes(_inventory_bytes(preflight_result["inventory"]))
    return job


def _redacted_runtime(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = json.loads(json.dumps(snapshot))
    issuer = dict(payload.get("issuer") or {})
    if "api_key" in issuer:
        issuer["api_key"] = "<redacted>" if issuer.get("api_key") else ""
    if "api_key_preview" in issuer:
        issuer["api_key_preview"] = "<redacted>" if issuer.get("api_key_preview") else ""
    payload["issuer"] = issuer
    return payload


def _unit_counts(session: Session, job_id: str) -> dict[str, int]:
    rows = session.execute(
        select(Stage4DataPoolBackfillUnit.status, func.count(Stage4DataPoolBackfillUnit.id))
        .where(Stage4DataPoolBackfillUnit.job_id == job_id)
        .group_by(Stage4DataPoolBackfillUnit.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    counts["total"] = sum(counts.values())
    attempt_count, attempted_units = session.execute(
        select(
            func.coalesce(func.sum(Stage4DataPoolBackfillUnit.attempt_count), 0),
            func.count(Stage4DataPoolBackfillUnit.id),
        ).where(
            Stage4DataPoolBackfillUnit.job_id == job_id,
            Stage4DataPoolBackfillUnit.attempt_count > 0,
        )
    ).one()
    counts["attempts"] = int(attempt_count or 0)
    counts["retries"] = max(int(attempt_count or 0) - int(attempted_units or 0), 0)
    return counts


def _refresh_job_stats(session: Session, job: Stage4DataPoolBackfillJob) -> dict[str, int]:
    counts = _unit_counts(session, job.id)
    migrated = counts.get("completed", 0)
    already_present = counts.get("skipped", 0)
    job.stats_json = {
        **dict(job.stats_json or {}),
        **counts,
        "completed": migrated,
        "skipped": already_present,
        "migrated_count": migrated,
        "already_present_count": already_present,
        "satisfied_count": migrated + already_present,
        "failed": counts.get("failed", 0),
        "updated_at": _serialize_datetime(_utcnow()),
    }
    session.flush()
    return counts


def _override_job_concurrency(
    session: Session,
    job: Stage4DataPoolBackfillJob,
    concurrency: int | None,
    *,
    settings: Settings,
) -> None:
    if concurrency is None:
        return
    now = _utcnow()
    if (
        job.lease_owner
        and job.lease_heartbeat_at is not None
        and job.lease_heartbeat_at >= now - LEASE_STALE_AFTER
    ):
        raise BackfillError(
            "backfill job is still running; press Ctrl-C in its terminal and wait for it to exit before overriding concurrency"
        )
    previous = int(
        dict(dict(job.runtime_snapshot_json or {}).get("concurrency") or {}).get(
            "max_concurrent_runs"
        )
        or 0
    )
    job.runtime_snapshot_json = _runtime_with_concurrency(
        dict(job.runtime_snapshot_json or {}),
        concurrency,
        settings=settings,
    )
    history = list(dict(job.stats_json or {}).get("concurrency_override_history") or [])
    history.append(
        {
            "previous": previous,
            "current": int(concurrency),
            "updated_at": _serialize_datetime(now),
        }
    )
    job.stats_json = {
        **dict(job.stats_json or {}),
        "concurrency_override_history": history,
    }
    session.commit()


def _clean_all_report_history(
    job: Stage4DataPoolBackfillJob,
    *,
    cleaned_at: datetime,
    audit_rows: Sequence[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    report_dir = _report_dir(job.id)
    history_dir = report_dir / "clean-all-history" / cleaned_at.strftime("%Y%m%dT%H%M%S%fZ")
    history_dir.mkdir(parents=True, exist_ok=False)
    for name in ("summary.json", "failures.jsonl", "verification.json"):
        source = report_dir / name
        if source.is_file():
            shutil.copy2(source, history_dir / name)
            source.unlink()
    _write_json(history_dir / "clean-all.json", summary)
    with (history_dir / "units.jsonl").open("w", encoding="utf-8") as handle:
        for row in audit_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def clean_all_job(session: Session, job: Stage4DataPoolBackfillJob) -> dict[str, Any]:
    """Empty one backfill job's target pool and reset every unit for a fresh resume."""

    now = _utcnow()
    heartbeat = job.lease_heartbeat_at
    if heartbeat is not None and heartbeat.tzinfo is None:
        heartbeat = heartbeat.replace(tzinfo=UTC)
    if job.lease_owner and heartbeat is not None and heartbeat >= now - LEASE_STALE_AFTER:
        raise BackfillError(
            "backfill job is still running; stop its executor and wait for the lease to be released before using --clean-all"
        )

    source_pool = session.get(DataPool, job.source_pool_id)
    target_pool = session.get(DataPool, job.target_pool_id)
    if source_pool is None or target_pool is None:
        raise BackfillError("backfill source or target pool disappeared")
    if source_pool.id == target_pool.id:
        raise BackfillError("--clean-all refuses to clean a source pool")

    raw_source_root = Path(source_pool.root_path).expanduser()
    raw_target_root = Path(target_pool.root_path).expanduser()
    if raw_target_root.is_symlink():
        raise BackfillError("--clean-all refuses to clean a symlinked target pool root")
    source_root = raw_source_root.resolve()
    target_root = raw_target_root.resolve()
    if (
        source_root == target_root
        or _is_relative_to(target_root, source_root)
        or _is_relative_to(source_root, target_root)
    ):
        raise BackfillError("--clean-all requires disjoint source and target pool roots")
    if not target_root.is_dir():
        raise BackfillError(f"backfill target pool root is missing: {target_root}")

    units = list(
        session.scalars(
            select(Stage4DataPoolBackfillUnit)
            .where(Stage4DataPoolBackfillUnit.job_id == job.id)
            .order_by(Stage4DataPoolBackfillUnit.position.asc())
        )
    )
    if len(units) != int(job.eligible_count):
        raise BackfillError(
            f"backfill unit count changed: expected {job.eligible_count}, found {len(units)}"
        )
    skipped_count = sum(1 for unit in units if unit.status == "skipped")
    if skipped_count:
        raise BackfillError(
            f"--clean-all refuses to delete {skipped_count} target asset(s) produced outside this backfill job"
        )
    run_ids = {str(unit.stage4_run_id) for unit in units if unit.stage4_run_id}
    active_run_ids = (
        list(
            session.scalars(
                select(Stage4Run.id).where(
                    Stage4Run.id.in_(run_ids),
                    Stage4Run.status.in_(tuple(ACTIVE_STAGE4_STATUSES)),
                )
            )
        )
        if run_ids
        else []
    )
    if active_run_ids:
        raise BackfillError(
            f"--clean-all refuses to clean a job with {len(active_run_ids)} active Stage4 run(s)"
        )

    inventory, rejected = select_eligible_assets(session, source_pool.id)
    raw_limit = dict(job.stats_json or {}).get("inventory_limit")
    if raw_limit is not None:
        try:
            inventory = inventory[: int(raw_limit)]
        except (TypeError, ValueError) as exc:
            raise BackfillError("backfill job has an invalid frozen inventory limit") from exc
    inventory_sha256 = _inventory_sha256(inventory)
    if rejected:
        raise BackfillError(
            f"source inventory now contains {len(rejected)} invalid asset(s); refusing --clean-all"
        )
    if len(inventory) != int(job.eligible_count) or inventory_sha256 != job.inventory_sha256:
        raise BackfillError(
            "source inventory changed after the backfill job was created; refusing --clean-all"
        )
    if [unit.source_asset_id for unit in units] != [item.asset_id for item in inventory]:
        raise BackfillError("backfill units no longer match the frozen source inventory")

    target_assets = list(
        session.scalars(
            select(DataPoolAsset)
            .where(DataPoolAsset.pool_id == target_pool.id)
            .order_by(DataPoolAsset.folder_name.asc(), DataPoolAsset.id.asc())
        )
    )
    referenced_target_ids = [str(unit.target_asset_id) for unit in units if unit.target_asset_id]
    if len(referenced_target_ids) != len(set(referenced_target_ids)):
        raise BackfillError("multiple backfill units reference the same target asset")
    expected_target_ids = set(referenced_target_ids)
    actual_target_ids = {asset.id for asset in target_assets}
    if actual_target_ids != expected_target_ids:
        raise BackfillError(
            "target pool contains assets that are not owned exactly by this backfill job; refusing --clean-all"
        )
    for unit in units:
        if unit.status == "completed" and not unit.target_asset_id:
            raise BackfillError("completed backfill unit is missing its target asset")
        if unit.target_asset_id and unit.status != "completed":
            raise BackfillError("non-completed backfill unit unexpectedly owns a target asset")

    expected_folder_names: set[str] = set()
    for asset in target_assets:
        raw_folder = Path(asset.folder_path).expanduser()
        if raw_folder.is_symlink():
            raise BackfillError(
                f"--clean-all refuses to clean a symlinked asset directory: {raw_folder}"
            )
        folder = raw_folder.resolve()
        if folder.parent != target_root or folder.name != asset.folder_name:
            raise BackfillError(f"target asset path escapes its pool root: {folder}")
        if not folder.is_dir():
            raise BackfillError(f"target asset directory is missing: {folder}")
        if asset.folder_name in expected_folder_names:
            raise BackfillError("multiple target assets reference the same directory")
        expected_folder_names.add(asset.folder_name)

    unknown_entries = sorted(
        entry.name
        for entry in target_root.iterdir()
        if entry.name != ".tmp" and entry.name not in expected_folder_names
    )
    if unknown_entries:
        raise BackfillError(
            f"target pool root contains unknown entries; refusing --clean-all: {unknown_entries}"
        )
    tmp_root = target_root / ".tmp"
    if tmp_root.exists():
        if tmp_root.is_symlink() or not tmp_root.is_dir():
            raise BackfillError("target pool .tmp path is not a regular directory")
        unknown_tmp_entries = sorted(
            entry.name for entry in tmp_root.iterdir() if entry.name != job.id
        )
        if unknown_tmp_entries:
            raise BackfillError(
                "target pool .tmp contains data owned by another job; refusing --clean-all: "
                f"{unknown_tmp_entries}"
            )

    styles = _parse_styles(list(job.styles_json or []))
    catalog = load_issue_style_catalog(enabled_styles=styles)
    previous_stats = dict(job.stats_json or {})
    previous_catalog_sha256 = str(previous_stats.get("issue_style_catalog_sha256") or "")
    current_catalog_sha256 = catalog.enabled_contract_sha256()
    previous_counts = _unit_counts(session, job.id)
    audit_rows = [
        {
            "position": unit.position,
            "source_asset_id": unit.source_asset_id,
            "previous_status": unit.status,
            "previous_stage4_run_id": unit.stage4_run_id,
            "previous_target_asset_id": unit.target_asset_id,
            "previous_attempt_count": unit.attempt_count,
            "previous_error": unit.error_message,
        }
        for unit in units
    ]
    clean_event = {
        "cleaned_at": _serialize_datetime(now),
        "previous_status": job.status,
        "reset_unit_count": len(units),
        "removed_target_asset_count": len(target_assets),
        "previous_counts": previous_counts,
        "previous_issue_style_catalog_sha256": previous_catalog_sha256,
        "current_issue_style_catalog_sha256": current_catalog_sha256,
    }

    quarantine_root = tmp_root / job.id / f"clean-all-{uuid.uuid4().hex}"
    try:
        for asset in target_assets:
            destination = Path(asset.folder_path).expanduser().resolve()
            backup_dir = quarantine_root / destination.name
            backup_dir.parent.mkdir(parents=True, exist_ok=True)
            destination.rename(backup_dir)
            _register_replaced_directory(
                session,
                destination=destination,
                backup_dir=backup_dir,
            )
            session.delete(asset)

        for unit in units:
            unit.status = "pending"
            unit.attempt_count = 0
            unit.stage4_run_id = None
            unit.target_asset_id = None
            unit.verification_json = {}
            unit.error_message = None
            unit.started_at = None
            unit.finished_at = None

        clean_history = list(previous_stats.get("clean_all_history") or [])
        clean_history.append(clean_event)
        job.status = "pending"
        job.error_message = None
        job.lease_owner = None
        job.lease_heartbeat_at = None
        job.started_at = None
        job.finished_at = None
        job.stats_json = {
            "total": int(job.eligible_count),
            "completed": 0,
            "skipped": 0,
            "migrated_count": 0,
            "already_present_count": 0,
            "satisfied_count": 0,
            "failed": 0,
            "pending": int(job.eligible_count),
            "attempts": 0,
            "retries": 0,
            "issue_style_catalog_sha256": current_catalog_sha256,
            "payload_size_bytes": int(previous_stats.get("payload_size_bytes") or 0),
            "concurrency_override_history": list(
                previous_stats.get("concurrency_override_history") or []
            ),
            "clean_all_history": clean_history,
            "updated_at": _serialize_datetime(now),
            **{
                key: previous_stats[key]
                for key in (
                    "inventory_limit",
                    "available_eligible_count",
                    "preflight_already_satisfied_count",
                )
                if key in previous_stats
            },
        }
        session.flush()
        DataPoolService(session).refresh_pool_stats(target_pool.id)
        session.commit()
    except Exception:
        session.rollback()
        raise

    shutil.rmtree(tmp_root / job.id, ignore_errors=True)
    try:
        if tmp_root.exists() and not any(tmp_root.iterdir()):
            tmp_root.rmdir()
    except OSError:
        pass
    try:
        _clean_all_report_history(
            job,
            cleaned_at=now,
            audit_rows=audit_rows,
            summary=clean_event,
        )
    except OSError as exc:
        # Cleaning the target is already committed. A report-archive failure must
        # not strand the freshly reset job instead of resuming it.
        clean_event["report_warning"] = f"failed to archive previous reports: {exc}"
    return clean_event


def _acquire_lease(session: Session, job: Stage4DataPoolBackfillJob, owner: str) -> bool:
    now = _utcnow()
    heartbeat = job.lease_heartbeat_at
    if (
        job.lease_owner
        and job.lease_owner != owner
        and heartbeat is not None
        and heartbeat >= now - LEASE_STALE_AFTER
    ):
        raise BackfillError(f"backfill job is already running under lease {job.lease_owner}")
    reclaimed = bool(job.lease_owner and job.lease_owner != owner)
    job.lease_owner = owner
    job.lease_heartbeat_at = now
    session.commit()
    return reclaimed


def _heartbeat(session: Session, job: Stage4DataPoolBackfillJob, owner: str) -> None:
    session.refresh(job)
    if job.lease_owner != owner:
        raise BackfillError("backfill job lease was lost")
    job.lease_heartbeat_at = _utcnow()
    session.commit()


def _release_lease(session: Session, job: Stage4DataPoolBackfillJob, owner: str) -> None:
    session.refresh(job)
    if job.lease_owner == owner:
        job.lease_owner = None
        job.lease_heartbeat_at = None
        session.commit()


def _stage4_paths(styles: Sequence[str]) -> set[str]:
    return {
        "manifest.json",
        "issues.json",
        "hints.json",
        "llm/issuer",
        "llm/hint",
        *(f"llm/{style}" for style in styles),
    }


def _path_is_stage4(relative: Path, styles: Sequence[str]) -> bool:
    raw = relative.as_posix()
    return any(raw == prefix or raw.startswith(prefix + "/") for prefix in _stage4_paths(styles))


def _tree_fingerprint(root: Path, *, styles: Sequence[str]) -> tuple[str, int]:
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root)
        if _path_is_stage4(relative, styles):
            continue
        stat = path.lstat()
        row: dict[str, Any] = {
            "path": relative.as_posix(),
            "mode": stat.st_mode & 0o7777,
            "kind": "symlink" if path.is_symlink() else "dir" if path.is_dir() else "file",
        }
        if path.is_symlink():
            row["target"] = os.readlink(path)
        elif path.is_file():
            row["size"] = stat.st_size
            row["sha256"] = _sha256_file(path)
        rows.append(row)
    return hashlib.sha256(_json_bytes(rows)).hexdigest(), len(rows)


def _normalized_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = json.loads(json.dumps(payload))
    for key in ("issue_schema_version", "data_pool_id", "asset_id", "stage4_run_id", "created_at"):
        normalized.pop(key, None)
    metrics = dict(normalized.get("metrics") or {})
    metrics.pop("issue_count", None)
    metrics.pop("hint_count", None)
    normalized["metrics"] = metrics
    return normalized


def _verify_materialized_tree(
    source: Path,
    target: Path,
    *,
    styles: Sequence[str],
    expected_asset_id: str,
    expected_pool_id: str,
    expected_stage4_run_id: str,
) -> dict[str, Any]:
    source_manifest = _read_json(source / "manifest.json")
    target_manifest = _read_json(target / "manifest.json")
    if _normalized_manifest(source_manifest) != _normalized_manifest(target_manifest):
        raise BackfillError("materialized manifest changed outside the Stage4 allowlist")
    expected_manifest = {
        "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
        "data_pool_id": expected_pool_id,
        "asset_id": expected_asset_id,
        "stage4_run_id": expected_stage4_run_id,
    }
    mismatches = {
        key: {"actual": target_manifest.get(key), "expected": value}
        for key, value in expected_manifest.items()
        if target_manifest.get(key) != value
    }
    metrics = dict(target_manifest.get("metrics") or {})
    if metrics.get("issue_count") != len(styles):
        mismatches["metrics.issue_count"] = {
            "actual": metrics.get("issue_count"),
            "expected": len(styles),
        }
    if "hint_count" in metrics:
        mismatches["metrics.hint_count"] = {"actual": metrics.get("hint_count"), "expected": "absent"}
    if mismatches:
        raise BackfillError(f"materialized manifest Stage4 fields are invalid: {mismatches}")
    issues = _read_json(target / "issues.json")
    if not isinstance(issues, list) or [str(item.get("style") or "") for item in issues] != list(styles):
        raise BackfillError(f"materialized issues.json does not contain styles in order: {list(styles)}")
    if (target / "hints.json").exists():
        raise BackfillError("materialized schema 2 asset must not contain hints.json")
    source_fingerprint, source_entries = _tree_fingerprint(source, styles=styles)
    target_fingerprint, target_entries = _tree_fingerprint(target, styles=styles)
    if source_fingerprint != target_fingerprint or source_entries != target_entries:
        raise BackfillError("non-Stage4 payload differs from the source asset")
    return {
        "source_payload_sha256": source_fingerprint,
        "target_payload_sha256": target_fingerprint,
        "verified_entry_count": source_entries,
        "verified_at": _serialize_datetime(_utcnow()),
    }


def _serialize_issues(run: Stage4Run) -> list[dict[str, Any]]:
    return [
        {
            "variant_index": row.variant_index,
            "style": row.style,
            "title": row.title,
            "issue_markdown": row.issue_markdown,
            "issue_json": dict(row.issue_json or {}),
            "quality": dict(row.quality_json or {}),
            "leakage_check": dict(row.leakage_check_json or {}),
        }
        for row in list(run.issue_variants or [])
    ]


def materialize_unit(
    session: Session,
    *,
    job: Stage4DataPoolBackfillJob,
    unit: Stage4DataPoolBackfillUnit,
    _lock_acquired: bool = False,
) -> DataPoolAsset:
    if not _lock_acquired:
        existing = _reuse_existing_target_asset(session, job=job, unit=unit)
        if existing is not None:
            return existing
        return materialize_unit(
            session,
            job=job,
            unit=unit,
            _lock_acquired=True,
        )

    source_asset = session.get(DataPoolAsset, unit.source_asset_id)
    run = session.get(Stage4Run, unit.stage4_run_id)
    target_pool = session.get(DataPool, job.target_pool_id)
    if source_asset is None or run is None or target_pool is None:
        raise BackfillError("backfill materialization source, run, or target pool disappeared")
    if source_asset.pool_id != job.source_pool_id:
        raise BackfillError("backfill source asset moved to a different pool")
    frozen_source = {
        "folder_name": unit.source_folder_name,
        "folder_path": str(Path(unit.source_folder_path).expanduser().resolve()),
        "github_repo_id": unit.github_repo_id,
        "entry_file_path": unit.entry_file_path,
        "depth": unit.depth,
        "stage3_savepoint_id": unit.source_savepoint_id,
        "stage4_run_id": unit.source_stage4_run_id,
    }
    current_source = {
        "folder_name": source_asset.folder_name,
        "folder_path": str(Path(source_asset.folder_path).expanduser().resolve()),
        "github_repo_id": source_asset.github_repo_id,
        "entry_file_path": source_asset.entry_file_path,
        "depth": source_asset.depth,
        "stage3_savepoint_id": source_asset.stage3_savepoint_id,
        "stage4_run_id": source_asset.stage4_run_id,
    }
    if current_source != frozen_source:
        raise BackfillError("backfill source asset changed after the inventory was frozen")
    if run.result != Stage4RunResult.generated.value or run.status != Stage4RunStatus.completed.value:
        raise BackfillError(f"Stage4 run is not generated: {run.id}")
    styles = [str(value) for value in list(job.styles_json or [])]
    issues = _serialize_issues(run)
    if [str(item.get("style") or "") for item in issues] != styles:
        raise BackfillError(f"Stage4 run styles do not match the frozen job styles: {styles}")

    source_dir = Path(source_asset.folder_path).expanduser().resolve()
    _validate_source_asset_files(source_asset)
    target_root = Path(target_pool.root_path).expanduser().resolve()
    target_dir = target_root / source_asset.folder_name
    if target_dir.exists():
        raise BackfillError(f"target asset directory already exists: {target_dir}")
    target_asset_id = str(uuid.uuid4())
    tmp_dir = target_root / ".tmp" / job.id / f"{unit.id}-{uuid.uuid4().hex[:8]}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source_dir, tmp_dir, symlinks=True, copy_function=shutil.copy2)
        (tmp_dir / "hints.json").unlink(missing_ok=True)
        llm_root = tmp_dir / "llm"
        for role in {"issuer", "swe", "fb", "hint", *styles}:
            shutil.rmtree(llm_root / role, ignore_errors=True)
        runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        for style in styles:
            _copy_llm_completion_archive(runtime_dir=runtime_dir, role=style, destination=llm_root)
            if not any((llm_root / style).rglob("*.json")):
                raise BackfillError(
                    f"Stage4 run {run.id} is missing the {style} LLM completion archive"
                )
        _write_json(tmp_dir / "issues.json", issues)
        manifest = dict(_read_json(source_dir / "manifest.json"))
        manifest.update(
            {
                "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
                "data_pool_id": target_pool.id,
                "asset_id": target_asset_id,
                "stage4_run_id": run.id,
                "created_at": _serialize_datetime(_utcnow()),
            }
        )
        metrics = dict(manifest.get("metrics") or {})
        metrics["issue_count"] = len(issues)
        metrics.pop("hint_count", None)
        manifest["metrics"] = metrics
        _write_json(tmp_dir / "manifest.json", manifest)
        verification = _verify_materialized_tree(
            source_dir,
            tmp_dir,
            styles=styles,
            expected_asset_id=target_asset_id,
            expected_pool_id=target_pool.id,
            expected_stage4_run_id=run.id,
        )
        target_dir.parent.mkdir(parents=True, exist_ok=True)
        tmp_dir.rename(target_dir)
        _register_replaced_directory(session, destination=target_dir, backup_dir=None)
        target_asset = DataPoolAsset(
            id=target_asset_id,
            pool_id=target_pool.id,
            repository_id=source_asset.repository_id,
            github_repo_id=source_asset.github_repo_id,
            repo_full_name=source_asset.repo_full_name,
            source_commit_sha=source_asset.source_commit_sha,
            language=source_asset.language,
            stars=source_asset.stars,
            entry_file_path=source_asset.entry_file_path,
            depth=source_asset.depth,
            stage2_run_id=source_asset.stage2_run_id,
            stage3_run_id=source_asset.stage3_run_id,
            stage3_savepoint_id=source_asset.stage3_savepoint_id,
            stage4_run_id=run.id,
            folder_name=source_asset.folder_name,
            folder_path=str(target_dir),
            manifest_json=manifest,
        )
        session.add(target_asset)
        unit.target_asset_id = target_asset.id
        unit.verification_json = verification
        unit.status = "completed"
        unit.error_message = None
        unit.finished_at = _utcnow()
        session.flush()
        return target_asset
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


class ProgressPrinter:
    def __init__(self, total: int) -> None:
        self.total = max(int(total), 1)
        self.started_at = time.monotonic()
        self.last_print_at = 0.0

    def render(self, counts: dict[str, int], *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self.last_print_at < (0.25 if sys.stderr.isatty() else 30.0):
            return
        self.last_print_at = now
        completed = int(counts.get("completed", 0))
        skipped = int(counts.get("skipped", 0))
        satisfied = completed + skipped
        width = 28
        filled = min(width, int(width * satisfied / self.total))
        elapsed = max(now - self.started_at, 0.001)
        rate = satisfied / elapsed
        remaining = self.total - satisfied
        eta = remaining / rate if rate > 0 else 0
        line = (
            f"[{'=' * filled}{'.' * (width - filled)}] {satisfied}/{self.total} "
            f"migrated={completed} skipped={skipped} "
            f"running={counts.get('running', 0) + counts.get('queued', 0)} "
            f"retries={counts.get('retries', 0)} failed={counts.get('failed', 0)} "
            f"elapsed={elapsed / 60:.1f}m "
            f"eta={eta / 60:.1f}m"
        )
        ending = "\r" if sys.stderr.isatty() and not force else "\n"
        print(line, end=ending, file=sys.stderr, flush=True)


class BackfillExecutor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        *,
        job_id: str,
        retries: int,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.job_id = job_id
        self.retries = max(int(retries), 0)
        self.owner = f"{os.getpid()}-{uuid.uuid4()}"
        self.stop_requested = False
        self._previous_signal_handlers: dict[int, Any] = {}

    def _request_stop(self, _signum: int, _frame: Any) -> None:
        self.stop_requested = True

    def _install_signal_handlers(self) -> None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            self._previous_signal_handlers[signum] = signal.getsignal(signum)
            signal.signal(signum, self._request_stop)

    def _restore_signal_handlers(self) -> None:
        for signum, handler in self._previous_signal_handlers.items():
            signal.signal(signum, handler)

    def run(self) -> int:
        session = self.session_factory()
        runner: Stage4RunRunner | None = None
        running_ids: set[str] = set()
        try:
            job = session.get(Stage4DataPoolBackfillJob, self.job_id)
            if job is None:
                raise BackfillError(f"backfill job not found: {self.job_id}")
            if job.status == "completed":
                return 0
            previous_job_status = str(job.status or "")
            reclaimed_stale_lease = _acquire_lease(session, job, self.owner)
            runtime_snapshot = dict(job.runtime_snapshot_json or {})
            runtime_settings = stage4_settings_with_runtime_snapshot(self.settings, runtime_snapshot)
            concurrency = int(
                dict(runtime_snapshot.get("concurrency") or {}).get("max_concurrent_runs")
                or runtime_settings.stage4_default_task_max_concurrent_runs
                or 1
            )
            runner = Stage4RunRunner(
                self.session_factory,
                runtime_settings,
                max_workers=concurrency,
                max_limit=concurrency,
                app_instance_id=f"stage4-backfill-{job.id[:8]}",
            )
            if reclaimed_stale_lease or previous_job_status in {"interrupted", "failed", "partial"}:
                stale_units = list(
                    session.scalars(
                        select(Stage4DataPoolBackfillUnit).where(
                            Stage4DataPoolBackfillUnit.job_id == job.id,
                            Stage4DataPoolBackfillUnit.status.in_(
                                ("queued", "running", "materializing")
                            ),
                        )
                    )
                )
                service = Stage4Service(
                    session,
                    workspace_root=runtime_settings.stage4_workspace_dir,
                    settings=runtime_settings,
                )
                for unit in stale_units:
                    run = session.get(Stage4Run, unit.stage4_run_id) if unit.stage4_run_id else None
                    if run is not None and run.status in {
                        Stage4RunStatus.queued.value,
                        Stage4RunStatus.running.value,
                    }:
                        try:
                            service.interrupt_run(run.id)
                        except Stage4RunConflictError:
                            pass
                    unit.status = "failed"
                    unit.error_message = "recovered after a stale backfill executor lease"
                    unit.finished_at = _utcnow()
                target_pool = session.get(DataPool, job.target_pool_id)
                if target_pool is not None:
                    shutil.rmtree(
                        Path(target_pool.root_path) / ".tmp" / job.id,
                        ignore_errors=True,
                    )
                session.commit()
            self._install_signal_handlers()
            job.status = "running"
            job.started_at = job.started_at or _utcnow()
            job.finished_at = None
            job.error_message = None
            session.commit()
            progress = ProgressPrinter(job.eligible_count)
            attempts_this_invocation: dict[str, int] = {}
            last_heartbeat = time.monotonic()

            while True:
                session.expire_all()
                job = session.get(Stage4DataPoolBackfillJob, self.job_id)
                if job is None:
                    raise BackfillError("backfill job disappeared")
                units = list(
                    session.scalars(
                        select(Stage4DataPoolBackfillUnit)
                        .where(Stage4DataPoolBackfillUnit.job_id == job.id)
                        .order_by(Stage4DataPoolBackfillUnit.position.asc())
                    )
                )
                for unit in units:
                    if unit.status not in {"queued", "running", "materializing"} or not unit.stage4_run_id:
                        continue
                    run = session.get(Stage4Run, unit.stage4_run_id)
                    if run is None:
                        unit.status = "failed"
                        unit.error_message = "Stage4 run disappeared"
                        unit.finished_at = _utcnow()
                        continue
                    if run.status in ACTIVE_STAGE4_STATUSES:
                        unit.status = "running" if run.status == Stage4RunStatus.running.value else "queued"
                        running_ids.add(run.id)
                        continue
                    running_ids.discard(run.id)
                    if run.result == Stage4RunResult.generated.value:
                        unit.status = "materializing"
                        try:
                            materialize_unit(session, job=job, unit=unit)
                            session.commit()
                        except Exception as exc:  # noqa: BLE001
                            session.rollback()
                            unit = session.get(Stage4DataPoolBackfillUnit, unit.id)
                            if unit is not None:
                                unit.status = "failed"
                                unit.error_message = f"materialization failed: {exc}"
                                unit.finished_at = _utcnow()
                                session.commit()
                    else:
                        unit.status = "failed"
                        unit.error_message = run.error_message or f"Stage4 result: {run.result}"
                        unit.finished_at = _utcnow()
                session.commit()

                if self.stop_requested:
                    break

                capacity = max(concurrency - len(running_ids), 0)
                launchable = [
                    unit
                    for unit in units
                    if unit.status in {"pending", "failed"}
                    and attempts_this_invocation.get(unit.id, 0) < 1 + self.retries
                ][:capacity]
                for unit in launchable:
                    if self.stop_requested:
                        break
                    try:
                        existing_target = _reuse_existing_target_asset(
                            session,
                            job=job,
                            unit=unit,
                        )
                        if existing_target is not None:
                            session.commit()
                            continue
                        previous_run = (
                            session.get(Stage4Run, unit.stage4_run_id)
                            if unit.stage4_run_id
                            else None
                        )
                        if (
                            previous_run is not None
                            and previous_run.status == Stage4RunStatus.completed.value
                            and previous_run.result == Stage4RunResult.generated.value
                        ):
                            unit.status = "materializing"
                            unit.error_message = None
                            materialize_unit(session, job=job, unit=unit)
                            session.commit()
                            continue
                        service = Stage4Service(
                            session,
                            workspace_root=runtime_settings.stage4_workspace_dir,
                            settings=runtime_settings,
                        )
                        expected_catalog = str(
                            dict(job.stats_json or {}).get("issue_style_catalog_sha256") or ""
                        )
                        run = service.create_run(
                            unit.source_savepoint_id,
                            trigger_kind="data_pool_backfill",
                            enabled_issue_styles=list(job.styles_json or []),
                            expected_issue_style_catalog_sha256=expected_catalog,
                        )
                        run = service.update_run_runtime_snapshot(
                            run.id,
                            runtime_snapshot=dict(job.runtime_snapshot_json or {}),
                        )
                        run = service.queue_run(run.id)
                        unit.stage4_run_id = run.id
                        unit.status = "queued"
                        unit.attempt_count += 1
                        unit.started_at = unit.started_at or _utcnow()
                        unit.finished_at = None
                        unit.error_message = None
                        attempts_this_invocation[unit.id] = attempts_this_invocation.get(unit.id, 0) + 1
                        session.commit()
                        if not runner.schedule_run(run.id):
                            raise BackfillError(f"Stage4 runner refused to schedule {run.id}")
                        running_ids.add(run.id)
                    except Exception as exc:  # noqa: BLE001
                        session.rollback()
                        unit = session.get(Stage4DataPoolBackfillUnit, unit.id)
                        if unit is not None:
                            unit.status = "failed"
                            unit.error_message = str(exc)
                            unit.finished_at = _utcnow()
                            attempts_this_invocation[unit.id] = attempts_this_invocation.get(unit.id, 0) + 1
                            session.commit()

                counts = _refresh_job_stats(session, job)
                session.commit()
                progress.render(counts)
                satisfied_count = counts.get("completed", 0) + counts.get("skipped", 0)
                if satisfied_count == job.eligible_count:
                    job.status = "completed"
                    job.finished_at = _utcnow()
                    DataPoolService(session).refresh_pool_stats(job.target_pool_id)
                    _refresh_job_stats(session, job)
                    session.commit()
                    progress.render(_unit_counts(session, job.id), force=True)
                    self._write_reports(session, job)
                    return 0
                if not running_ids and not launchable:
                    job.status = "partial"
                    job.finished_at = _utcnow()
                    _refresh_job_stats(session, job)
                    session.commit()
                    progress.render(_unit_counts(session, job.id), force=True)
                    self._write_reports(session, job)
                    return 2
                if time.monotonic() - last_heartbeat >= 20.0:
                    _heartbeat(session, job, self.owner)
                    last_heartbeat = time.monotonic()
                time.sleep(0.5)

            for run_id in list(running_ids):
                runner.interrupt_run(run_id)
            job.status = "interrupted"
            job.finished_at = _utcnow()
            _refresh_job_stats(session, job)
            session.commit()
            self._write_reports(session, job)
            return 2
        except Exception as exc:
            session.rollback()
            job = session.get(Stage4DataPoolBackfillJob, self.job_id)
            if job is not None:
                job.status = "failed"
                job.error_message = str(exc)
                job.finished_at = _utcnow()
                _refresh_job_stats(session, job)
                session.commit()
                self._write_reports(session, job)
            raise
        finally:
            if runner is not None:
                runner.shutdown(timeout_seconds=30.0)
            job = session.get(Stage4DataPoolBackfillJob, self.job_id)
            if job is not None:
                try:
                    _release_lease(session, job, self.owner)
                except Exception:
                    session.rollback()
            self._restore_signal_handlers()
            session.close()

    def _write_reports(self, session: Session, job: Stage4DataPoolBackfillJob) -> None:
        report_dir = _report_dir(job.id)
        report_dir.mkdir(parents=True, exist_ok=True)
        counts = _unit_counts(session, job.id)
        summary = {
            "job_id": job.id,
            "status": job.status,
            "source_pool_id": job.source_pool_id,
            "target_pool_id": job.target_pool_id,
            "runtime_template_id": job.runtime_template_id,
            "runtime_template_name": job.runtime_template_name,
            "runtime_snapshot": _redacted_runtime(dict(job.runtime_snapshot_json or {})),
            "styles": list(job.styles_json or []),
            "inventory_sha256": job.inventory_sha256,
            "eligible_count": job.eligible_count,
            "counts": counts,
            "created_at": _serialize_datetime(job.created_at),
            "started_at": _serialize_datetime(job.started_at),
            "finished_at": _serialize_datetime(job.finished_at),
            "error": job.error_message,
        }
        _write_json(report_dir / "summary.json", summary)
        failed = list(
            session.scalars(
                select(Stage4DataPoolBackfillUnit)
                .where(
                    Stage4DataPoolBackfillUnit.job_id == job.id,
                    Stage4DataPoolBackfillUnit.status == "failed",
                )
                .order_by(Stage4DataPoolBackfillUnit.position.asc())
            )
        )
        with (report_dir / "failures.jsonl").open("w", encoding="utf-8") as handle:
            for unit in failed:
                handle.write(
                    json.dumps(
                        {
                            "position": unit.position,
                            "source_asset_id": unit.source_asset_id,
                            "stage4_run_id": unit.stage4_run_id,
                            "attempt_count": unit.attempt_count,
                            "error": unit.error_message,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        satisfied = list(
            session.scalars(
                select(Stage4DataPoolBackfillUnit)
                .where(
                    Stage4DataPoolBackfillUnit.job_id == job.id,
                    Stage4DataPoolBackfillUnit.status.in_(("completed", "skipped")),
                )
                .order_by(Stage4DataPoolBackfillUnit.position.asc())
            )
        )
        migrated_count = sum(1 for unit in satisfied if unit.status == "completed")
        already_present_count = sum(1 for unit in satisfied if unit.status == "skipped")
        verification = {
            "job_id": job.id,
            "verified_count": len(satisfied),
            "eligible_count": job.eligible_count,
            "migrated_count": migrated_count,
            "already_present_count": already_present_count,
            "all_satisfied": len(satisfied) == job.eligible_count,
            "units": [
                {
                    "source_asset_id": unit.source_asset_id,
                    "target_asset_id": unit.target_asset_id,
                    "status": unit.status,
                    **dict(unit.verification_json or {}),
                }
                for unit in satisfied
            ],
        }
        _write_json(report_dir / "verification.json", verification)


def verify_job(session: Session, job: Stage4DataPoolBackfillJob) -> dict[str, Any]:
    units = list(
        session.scalars(
            select(Stage4DataPoolBackfillUnit)
            .where(Stage4DataPoolBackfillUnit.job_id == job.id)
            .order_by(Stage4DataPoolBackfillUnit.position.asc())
        )
    )
    failures: list[dict[str, str]] = []
    migrated_count = 0
    already_present_count = 0
    for unit in units:
        if unit.status not in {"completed", "skipped"} or not unit.target_asset_id:
            failures.append({"source_asset_id": unit.source_asset_id, "reason": f"status={unit.status}"})
            continue
        source = session.get(DataPoolAsset, unit.source_asset_id)
        target = session.get(DataPoolAsset, unit.target_asset_id)
        if source is None or target is None:
            failures.append({"source_asset_id": unit.source_asset_id, "reason": "source or target DB row missing"})
            continue
        try:
            if unit.status == "skipped":
                _validate_compatible_target_asset(
                    session,
                    job=job,
                    unit=unit,
                    asset=target,
                )
                already_present_count += 1
            else:
                _verify_materialized_tree(
                    Path(source.folder_path),
                    Path(target.folder_path),
                    styles=list(job.styles_json or []),
                    expected_asset_id=target.id,
                    expected_pool_id=job.target_pool_id,
                    expected_stage4_run_id=str(unit.stage4_run_id or ""),
                )
                migrated_count += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"source_asset_id": unit.source_asset_id, "reason": str(exc)})
    return {
        "job_id": job.id,
        "eligible_count": job.eligible_count,
        "verified_count": len(units) - len(failures),
        "migrated_count": migrated_count,
        "already_present_count": already_present_count,
        "passed": not failures and len(units) == job.eligible_count,
        "failures": failures,
    }


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rerun Stage4 for complete DataPool assets")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-templates", help="List saved Stage4 runtime templates")

    for command in ("preflight", "run"):
        child = subparsers.add_parser(command)
        child.add_argument("--source-pool", required=True)
        child.add_argument("--target-pool", required=True)
        child.add_argument("--target-root", type=Path, required=True)
        child.add_argument("--runtime-template", required=True)
        child.add_argument("--styles", default="swe,fb")
        child.add_argument(
            "--limit",
            type=int,
            help="Freeze only the first N eligible assets into this backfill job",
        )
        child.add_argument(
            "--concurrency",
            type=int,
            help="Override max_concurrent_runs from the saved runtime template",
        )
        if command == "run":
            child.add_argument("--retries", type=int, default=2)

    resume = subparsers.add_parser("resume")
    resume.add_argument("--job-id", required=True)
    resume.add_argument("--retries", type=int, default=2)
    resume.add_argument(
        "--concurrency",
        type=int,
        help="Override the frozen job concurrency before resuming",
    )
    resume.add_argument(
        "--clean-all",
        action="store_true",
        help=(
            "Delete every asset owned by this job from its target pool, reset all units, "
            "and adopt the current issue-style catalog before resuming"
        ),
    )
    for command in ("status", "verify"):
        child = subparsers.add_parser(command)
        child.add_argument("--job-id", required=True)
    return parser


def _validate_resume_schema(engine: Engine) -> None:
    """Validate the existing schema without taking DDL locks."""

    inspector = inspect(engine)
    problems: list[str] = []
    existing_tables = set(inspector.get_table_names())
    for model in _RESUME_REQUIRED_MODELS:
        table = model.__table__
        if table.name not in existing_tables:
            problems.append(f"missing table {table.name}")
            continue
        existing_columns = {
            str(column.get("name") or "")
            for column in inspector.get_columns(table.name)
        }
        missing_columns = sorted(
            column.name for column in table.columns if column.name not in existing_columns
        )
        if missing_columns:
            problems.append(
                f"table {table.name} is missing column(s): {', '.join(missing_columns)}"
            )

    for index_name, (table_name, expected_columns) in _RESUME_REQUIRED_ACTIVE_INDEXES.items():
        if table_name not in existing_tables:
            continue
        indexes = {
            str(index.get("name") or ""): index
            for index in inspector.get_indexes(table_name)
        }
        index = indexes.get(index_name)
        if index is None:
            problems.append(f"missing unique index {index_name}")
            continue
        actual_columns = tuple(str(value) for value in index.get("column_names") or ())
        if not bool(index.get("unique")) or actual_columns != expected_columns:
            problems.append(
                f"index {index_name} has incompatible definition "
                f"(unique={bool(index.get('unique'))}, columns={actual_columns})"
            )

    if problems:
        raise BackfillError(
            "backfill resume schema preflight failed; run schema initialization during a "
            "maintenance window before resuming: " + "; ".join(problems)
        )


def _session_factory(
    settings: Settings,
    *,
    initialize: bool,
    validate_resume_schema: bool = False,
) -> sessionmaker[Session]:
    engine = build_engine(settings)
    if initialize:
        init_db(engine)
    elif validate_resume_schema:
        _validate_resume_schema(engine)
    return build_session_factory(engine)


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    settings = get_settings()
    session: Session | None = None
    try:
        factory = _session_factory(
            settings,
            initialize=args.command == "run",
            validate_resume_schema=args.command == "resume",
        )
        session = factory()
        if args.command == "list-templates":
            rows = list(
                session.scalars(
                    select(Stage4RuntimeTemplate).order_by(
                        Stage4RuntimeTemplate.name.asc(), Stage4RuntimeTemplate.id.asc()
                    )
                )
            )
            for row in rows:
                snapshot = dict(row.snapshot_json or {})
                issuer = dict(snapshot.get("issuer") or {})
                concurrency = dict(snapshot.get("concurrency") or {})
                print(
                    json.dumps(
                        {
                            "id": row.id,
                            "name": row.name,
                            "model": issuer.get("model") or "",
                            "max_concurrent_runs": concurrency.get("max_concurrent_runs"),
                        },
                        ensure_ascii=False,
                    )
                )
            return 0

        if args.command in {"preflight", "run"}:
            result = preflight(
                session,
                settings,
                source_pool_selector=args.source_pool,
                target_pool_name=args.target_pool,
                target_root=args.target_root,
                runtime_template_selector=args.runtime_template,
                styles=_parse_styles(args.styles),
                concurrency=args.concurrency,
                limit=args.limit,
            )
            print(
                json.dumps(
                    {
                        "source_pool": result["source_pool"].name,
                        "target_pool": args.target_pool,
                        "target_root": str(result["target_root"]),
                        "runtime_template": result["runtime_template"].name,
                        "runtime": _redacted_runtime(result["runtime_snapshot"]),
                        "styles": result["styles"],
                        "total_source_assets": result["total_source_assets"],
                        "source_eligible_count": result["source_eligible_count"],
                        "eligible_count": result["eligible_count"],
                        "available_eligible_count": result["available_eligible_count"],
                        "already_satisfied_count": result["already_satisfied_count"],
                        "limited_out_count": result["limited_out_count"],
                        "limit": result["limit"],
                        "excluded_count": result["excluded_count"],
                        "rejected_file_count": result["rejected_file_count"],
                        "inventory_sha256": result["inventory_sha256"],
                        "payload_size": _format_bytes(result["payload_size_bytes"]),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            if args.command == "preflight":
                return 0
            job = create_job(session, result, target_name=args.target_pool)
            print(f"backfill_job_id={job.id}", flush=True)
            session.close()
            session = None  # type: ignore[assignment]
            return BackfillExecutor(
                factory,
                settings,
                job_id=job.id,
                retries=args.retries,
            ).run()

        job = session.get(Stage4DataPoolBackfillJob, args.job_id)
        if job is None:
            raise BackfillError(f"backfill job not found: {args.job_id}")
        if args.command == "status":
            print(
                json.dumps(
                    {
                        "job_id": job.id,
                        "status": job.status,
                        "eligible_count": job.eligible_count,
                        "counts": _unit_counts(session, job.id),
                        "styles": list(job.styles_json or []),
                        "runtime_template": job.runtime_template_name,
                        "concurrency": dict(
                            dict(job.runtime_snapshot_json or {}).get("concurrency") or {}
                        ).get("max_concurrent_runs"),
                        "inventory_sha256": job.inventory_sha256,
                        "error": job.error_message,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "verify":
            result = verify_job(session, job)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["passed"] else 2
        if args.command == "resume":
            if args.clean_all:
                clean_result = clean_all_job(session, job)
                print(json.dumps({"clean_all": clean_result}, ensure_ascii=False, indent=2), flush=True)
            _override_job_concurrency(
                session,
                job,
                args.concurrency,
                settings=settings,
            )
            session.close()
            session = None  # type: ignore[assignment]
            return BackfillExecutor(
                factory,
                settings,
                job_id=job.id,
                retries=args.retries,
            ).run()
        raise BackfillError(f"unsupported command: {args.command}")
    except BackfillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if session is not None:
            session.close()


if __name__ == "__main__":
    raise SystemExit(main())