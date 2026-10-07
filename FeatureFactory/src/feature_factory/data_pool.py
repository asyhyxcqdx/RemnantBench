from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import cmp_to_key
from hashlib import sha256
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from feature_factory.diff_stats import core_patch_diff_line_stats
from feature_factory.models import (
    BatchTask,
    DataPool,
    DataPoolAsset,
    DataPoolDownloadSelection,
    GitHubRepository,
    Stage2Run,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3Savepoint,
    Stage4DataPoolBackfillJob,
    Stage4IssueVariant,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.stage2.raw_archive import (
    legacy_llm_completion_archive_dir,
    llm_completion_archive_dir,
    runtime_dir_from_workspace_path,
)
from feature_factory.stage4.issue_styles import runtime_issue_style_specs


DATA_POOL_ASSET_SORT_FIELDS = {
    "created_at",
    "repo",
    "commit",
    "language",
    "stars",
    "entry_file",
    "entry_pass_rate",
    "p2p_count",
    "diff_lines",
    "issue_count",
    "depth",
}
DATA_POOL_ASSET_SQL_SORT_FIELDS = {
    "created_at",
    "repo",
    "commit",
    "language",
    "stars",
    "entry_file",
    "depth",
}
DEFAULT_DATA_POOL_NAME = "默认数据池"
DEFAULT_DATA_POOL_DESCRIPTION = "FeatureFactory 默认数据池"
CURRENT_ISSUE_SCHEMA_VERSION = 2
DATA_POOL_ASSET_LOCK_TIMEOUT_SEC = 600.0
DATA_POOL_ASSET_LOCK_STALE_SEC = 6 * 60 * 60
DATA_POOL_DIRECTORY_REPLACEMENTS_KEY = "feature_factory_data_pool_directory_replacements"
DATA_POOL_ASSET_LOCKS_KEY = "feature_factory_data_pool_asset_locks"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_CORE_DATA_POOL_FORBIDDEN_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "data",
    "docs",
    "node_modules",
    "software-agent-sdk",
    "src",
    "tests",
}


class DataPoolDeleteConflictError(RuntimeError):
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


def _safe_name(value: str, *, max_length: int = 96) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-")
    if not normalized:
        normalized = "asset"
    return normalized[:max_length].strip("._-") or "asset"


def _resolve_root_path(root_path: str | Path) -> Path:
    raw = str(root_path or "").strip()
    if not raw:
        raise ValueError("data pool root_path is required")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        root = Path.cwd() / root
    return root.resolve()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_new_pool_root_path(root: Path) -> None:
    if root.exists():
        raise ValueError("new data pool root_path must not already exist")
    if root == PROJECT_ROOT:
        raise ValueError("new data pool root_path cannot be the project root")
    for dirname in sorted(PROJECT_CORE_DATA_POOL_FORBIDDEN_DIRS):
        protected = (PROJECT_ROOT / dirname).resolve()
        if root == protected or _is_relative_to(root, protected):
            raise ValueError(f"new data pool root_path cannot be under project core directory: {dirname}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _copy_llm_completion_archive(*, runtime_dir: Path | None, role: str, destination: Path) -> None:
    if runtime_dir is None:
        return
    source_dirs = [
        llm_completion_archive_dir(runtime_dir, role),
        legacy_llm_completion_archive_dir(runtime_dir, role),
    ]
    copied: set[str] = set()
    for source_dir in source_dirs:
        if not source_dir.exists() or not source_dir.is_dir():
            continue
        for source_path in sorted(source_dir.rglob("*.json")):
            if not source_path.is_file():
                continue
            relative = source_path.relative_to(source_dir)
            relative_key = str(relative)
            if relative_key in copied:
                continue
            copied.add(relative_key)
            target = destination / role / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, target)


def _replace_directory(source: Path, destination: Path, *, backup_root: Path) -> Path | None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup_dir: Path | None = None
    if destination.exists():
        backup_dir = backup_root / f".{destination.name}.backup-{uuid.uuid4().hex}"
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        destination.rename(backup_dir)
    try:
        source.rename(destination)
    except Exception:
        if backup_dir is not None and backup_dir.exists() and not destination.exists():
            backup_dir.rename(destination)
        raise
    return backup_dir


def _commit_replaced_directory(backup_dir: Path | None) -> None:
    if backup_dir is not None:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _rollback_replaced_directory(destination: Path, backup_dir: Path | None) -> None:
    if destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    if backup_dir is not None and backup_dir.exists():
        backup_dir.rename(destination)


def _register_replaced_directory(
    session: Session,
    *,
    destination: Path,
    backup_dir: Path | None,
) -> None:
    replacements = session.info.setdefault(DATA_POOL_DIRECTORY_REPLACEMENTS_KEY, [])
    replacements.append((destination, backup_dir))


@event.listens_for(Session, "after_commit")
def _commit_session_directory_replacements(session: Session) -> None:
    replacements = session.info.pop(DATA_POOL_DIRECTORY_REPLACEMENTS_KEY, [])
    for _, backup_dir in replacements:
        _commit_replaced_directory(backup_dir)
    for lock_dir in session.info.pop(DATA_POOL_ASSET_LOCKS_KEY, []):
        shutil.rmtree(lock_dir, ignore_errors=True)


@event.listens_for(Session, "after_rollback")
def _rollback_session_directory_replacements(session: Session) -> None:
    replacements = session.info.pop(DATA_POOL_DIRECTORY_REPLACEMENTS_KEY, [])
    for destination, backup_dir in reversed(replacements):
        _rollback_replaced_directory(destination, backup_dir)
    for lock_dir in session.info.pop(DATA_POOL_ASSET_LOCKS_KEY, []):
        shutil.rmtree(lock_dir, ignore_errors=True)


def _lock_is_stale(lock_dir: Path) -> bool:
    try:
        age = time.time() - lock_dir.stat().st_mtime
    except FileNotFoundError:
        return False
    return age > DATA_POOL_ASSET_LOCK_STALE_SEC


@contextmanager
def _asset_materialize_lock(lock_dir: Path, *, session: Session | None = None):
    retained_locks = session.info.setdefault(DATA_POOL_ASSET_LOCKS_KEY, []) if session is not None else []
    if lock_dir in retained_locks:
        yield
        return

    deadline = time.monotonic() + DATA_POOL_ASSET_LOCK_TIMEOUT_SEC
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    while not acquired:
        try:
            lock_dir.mkdir()
            _write_json(
                lock_dir / "owner.json",
                {
                    "created_at": serialize_datetime(_utcnow()),
                    "token": uuid.uuid4().hex,
                },
            )
            acquired = True
        except FileExistsError:
            if _lock_is_stale(lock_dir):
                shutil.rmtree(lock_dir, ignore_errors=True)
                continue
            if time.monotonic() >= deadline:
                raise TimeoutError(f"timed out waiting for data pool asset lock: {lock_dir}")
            time.sleep(0.1)

    try:
        yield
    except Exception:
        shutil.rmtree(lock_dir, ignore_errors=True)
        raise
    else:
        if session is None:
            shutil.rmtree(lock_dir, ignore_errors=True)
        else:
            current_locks = session.info.setdefault(DATA_POOL_ASSET_LOCKS_KEY, [])
            if lock_dir not in current_locks:
                current_locks.append(lock_dir)


def _asset_materialize_lock_dir(
    tmp_root: Path,
    *,
    pool_id: str,
    github_repo_id: int,
    entry_file_path: str,
    depth: int,
) -> Path:
    key = json.dumps(
        {
            "pool_id": pool_id,
            "github_repo_id": github_repo_id,
            "entry_file_path": entry_file_path,
            "depth": depth,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = sha256(key).hexdigest()
    return tmp_root / f".asset-{digest[:24]}.lock"


def _read_json_file(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_text_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _stage4_run_issue_style_contract(run: Stage4Run) -> tuple[int, tuple[str, ...], str] | None:
    runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
    generation_config = runtime_stage4.get("generation_config")
    if not isinstance(generation_config, dict):
        return None
    try:
        schema_version = int(generation_config.get("schema_version"))
    except (TypeError, ValueError):
        return None
    raw_styles = generation_config.get("enabled_styles")
    if not isinstance(raw_styles, list) or not raw_styles:
        return None
    styles = tuple(str(value or "").strip() for value in raw_styles)
    catalog_sha256 = str(generation_config.get("catalog_sha256") or "").strip()
    if any(not value for value in styles) or not catalog_sha256:
        return None
    return schema_version, styles, catalog_sha256


def _asset_matches_stage4_run_contract(
    session: Session,
    asset: DataPoolAsset,
    target_run: Stage4Run,
) -> bool:
    target_contract = _stage4_run_issue_style_contract(target_run)
    existing_run_id = str(asset.stage4_run_id or "").strip()
    existing_run = session.get(Stage4Run, existing_run_id) if existing_run_id else None
    if (
        target_contract is None
        or existing_run is None
        or existing_run.status != Stage4RunStatus.completed.value
        or existing_run.result != Stage4RunResult.generated.value
        or _stage4_run_issue_style_contract(existing_run) != target_contract
    ):
        return False

    folder = Path(asset.folder_path).expanduser()
    if not folder.is_dir():
        return False
    manifest = _read_json_file(folder / "manifest.json")
    issues = _read_json_file(folder / "issues.json")
    if not isinstance(manifest, dict) or not isinstance(issues, list):
        return False
    expected_manifest = {
        "asset_id": asset.id,
        "data_pool_id": asset.pool_id,
        "stage4_run_id": existing_run.id,
        "entry_file": asset.entry_file_path,
        "depth": int(asset.depth),
        "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
    }
    if any(manifest.get(key) != value for key, value in expected_manifest.items()):
        return False
    issue_styles = [str(item.get("style") or "") for item in issues if isinstance(item, dict)]
    if issue_styles != list(target_contract[1]):
        return False
    return all((folder / name).is_file() for name in ("dockerfile", "run_script.sh", "gold.patch"))


def _safe_relative_path(value: str) -> Path:
    raw = str(value or "").strip()
    path = Path(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValueError("invalid relative path")
    return path


def _stage4_broken_repo_dir(run: Stage4Run) -> Path | None:
    runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
    candidates = [
        runtime_stage4.get("broken_repo_dir"),
        dict(runtime_stage4.get("workspace") or {}).get("repo_dir"),
    ]
    for raw_path in candidates:
        value = str(raw_path or "").strip()
        if not value:
            continue
        path = Path(value).expanduser()
        if path.is_dir():
            return path.resolve()
    return None


def _write_repo_file_snapshots(
    asset_dir: Path,
    *,
    run: Stage4Run,
    repo_paths: list[str],
    manifest_name: str,
    storage_dir_name: str,
    label: str,
) -> None:
    manifest: list[dict[str, Any]] = []
    repo_dir = _stage4_broken_repo_dir(run)
    storage_dir = asset_dir / storage_dir_name
    seen: set[str] = set()

    if repo_dir is None and any(str(path or "").strip() for path in repo_paths):
        raise FileNotFoundError(
            f"Stage4 run {run.id} is missing a broken repo directory; "
            f"cannot export {label} file snapshots for Harbor rollout"
        )

    if repo_dir is not None:
        for raw_path in repo_paths:
            repo_path = str(raw_path or "").strip()
            if not repo_path or repo_path in seen:
                continue
            seen.add(repo_path)
            relative_path = _safe_relative_path(repo_path)
            source = (repo_dir / relative_path).resolve()
            try:
                source.relative_to(repo_dir)
            except ValueError as exc:
                raise ValueError(f"{label} file path escapes broken repo: {repo_path}") from exc
            if not source.is_file():
                raise FileNotFoundError(f"{label} file snapshot source is missing: {source}")

            data = source.read_bytes()
            digest = sha256(data).hexdigest()
            storage_name = f"{len(manifest) + 1:04d}-{digest[:16]}"
            target = storage_dir / storage_name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            manifest.append(
                {
                    "path": repo_path,
                    "storage_path": f"{storage_dir_name}/{storage_name}",
                    "sha256": digest,
                    "size_bytes": len(data),
                }
            )

    _write_json(asset_dir / manifest_name, manifest)


def _normalize_optional_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _normalize_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _diff_stats_from_patch_text(patch_text: str | None) -> dict[str, int]:
    stats = core_patch_diff_line_stats(patch_text)
    return {
        "files_changed": stats.files_changed,
        "lines_changed": stats.lines_changed,
        "lines_added": stats.lines_added,
        "lines_deleted": stats.lines_deleted,
    }


def _normalize_diff_stats(value: Any) -> dict[str, int]:
    payload = value if isinstance(value, dict) else {}
    files = _normalize_int(payload.get("files_changed", payload.get("file_count", 0)))
    added = _normalize_int(payload.get("lines_added", payload.get("added_lines", 0)))
    deleted = _normalize_int(payload.get("lines_deleted", payload.get("removed_lines", 0)))
    total = _normalize_int(payload.get("lines_changed", payload.get("total_changed_lines", added + deleted)))
    return {
        "files_changed": max(files, 0),
        "lines_changed": total if total >= 0 else added + deleted,
        "lines_added": max(added, 0),
        "lines_deleted": max(deleted, 0),
    }


def _percentile(values: list[float | int], quantile: float) -> float | None:
    numeric = sorted(float(value) for value in values)
    if not numeric:
        return None
    if len(numeric) == 1:
        return numeric[0]
    bounded = min(max(float(quantile), 0.0), 1.0)
    position = (len(numeric) - 1) * bounded
    lower = int(position)
    upper = min(lower + 1, len(numeric) - 1)
    if lower == upper:
        return numeric[lower]
    fraction = position - lower
    return numeric[lower] * (1.0 - fraction) + numeric[upper] * fraction


def _numeric_summary(values: list[float | int]) -> dict[str, float | int | None]:
    numeric = [float(value) for value in values]
    if not numeric:
        return {"min": None, "p50": None, "p90": None, "max": None, "avg": None}
    return {
        "min": min(numeric),
        "p50": _percentile(numeric, 0.5),
        "p90": _percentile(numeric, 0.9),
        "max": max(numeric),
        "avg": sum(numeric) / len(numeric),
    }


def _counter_distribution(counter: Counter[Any], *, value_key: str = "value") -> list[dict[str, Any]]:
    return [
        {value_key: key, "asset_count": int(count)}
        for key, count in sorted(counter.items(), key=lambda item: item[0])
    ]


def _histogram(
    values: list[float | int],
    buckets: list[tuple[str, float | None, float | None]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, lower, upper in buckets:
        count = 0
        for raw_value in values:
            value = float(raw_value)
            if lower is not None and value < lower:
                continue
            if upper is not None and value > upper:
                continue
            count += 1
        rows.append(
            {
                "label": label,
                "min": lower,
                "max": upper,
                "asset_count": count,
            }
        )
    return rows


def _compare_values(left_value: Any, right_value: Any, *, descending: bool = False) -> int:
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


def _data_pool_asset_sort_value(summary: dict[str, Any], sort_by: str) -> Any:
    if sort_by == "repo":
        return str(summary.get("repo_full_name") or "").lower()
    if sort_by == "language":
        return str(summary.get("language") or "").lower()
    if sort_by == "commit":
        return str(summary.get("source_commit_sha") or "").lower()
    if sort_by == "stars":
        return int(summary.get("stars") or 0)
    if sort_by == "entry_file":
        return str(summary.get("entry_file_path") or "").lower()
    if sort_by == "entry_pass_rate":
        return _normalize_optional_float(summary.get("entry_pass_rate"))
    if sort_by == "p2p_count":
        return (
            int(summary.get("p2p_count") or 0),
            int(summary.get("f2p_count") or 0),
        )
    if sort_by == "diff_lines":
        diff_stats = _normalize_diff_stats(summary.get("diff_stats"))
        return (
            int(diff_stats.get("lines_changed") or 0),
            int(diff_stats.get("lines_added") or 0),
            int(diff_stats.get("lines_deleted") or 0),
        )
    if sort_by == "issue_count":
        return int(summary.get("issue_count") or 0)
    if sort_by == "depth":
        return int(summary.get("depth") or 0)
    if sort_by == "created_at":
        return str(summary.get("created_at") or "") or None
    return str(summary.get("id") or "")


def _compare_data_pool_asset_summaries(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    sort_by: str,
    sort_order: str,
) -> int:
    descending = sort_order == "desc"
    primary = _compare_values(
        _data_pool_asset_sort_value(left, sort_by),
        _data_pool_asset_sort_value(right, sort_by),
        descending=descending,
    )
    if primary != 0:
        return primary
    repo_tie_breaker = _compare_values(
        str(left.get("repo_full_name") or "").lower(),
        str(right.get("repo_full_name") or "").lower(),
    )
    if repo_tie_breaker != 0:
        return repo_tie_breaker
    entry_tie_breaker = _compare_values(
        str(left.get("entry_file_path") or "").lower(),
        str(right.get("entry_file_path") or "").lower(),
    )
    if entry_tie_breaker != 0:
        return entry_tie_breaker
    depth_tie_breaker = _compare_values(int(left.get("depth") or 0), int(right.get("depth") or 0))
    if depth_tie_breaker != 0:
        return depth_tie_breaker
    return _compare_values(str(left.get("id") or ""), str(right.get("id") or ""))


class DataPoolService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_pool(self, *, name: str, root_path: str, description: str | None = None) -> DataPool:
        normalized_name = str(name or "").strip()
        if not normalized_name:
            raise ValueError("data pool name is required")
        existing_pool_id = self.session.scalar(select(DataPool.id).where(DataPool.name == normalized_name).limit(1))
        if existing_pool_id is not None:
            raise ValueError("data pool name already exists")
        root = _resolve_root_path(root_path)
        _validate_new_pool_root_path(root)
        try:
            root.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise ValueError("new data pool root_path must not already exist") from exc
        pool = DataPool(
            name=normalized_name,
            root_path=str(root),
            description=str(description or "").strip() or None,
            stats_json={},
        )
        self.session.add(pool)
        self.session.flush()
        self.refresh_pool_stats(pool.id)
        return pool

    def ensure_default_pool(
        self,
        *,
        name: str = DEFAULT_DATA_POOL_NAME,
        root_path: str | Path = Path("./data-pools/default"),
        description: str = DEFAULT_DATA_POOL_DESCRIPTION,
    ) -> DataPool:
        normalized_name = str(name or DEFAULT_DATA_POOL_NAME).strip() or DEFAULT_DATA_POOL_NAME
        root = _resolve_root_path(root_path)
        root.mkdir(parents=True, exist_ok=True)
        pool = self.session.scalar(select(DataPool).where(DataPool.name == normalized_name).limit(1))
        if pool is None:
            pool = DataPool(
                name=normalized_name,
                root_path=str(root),
                description=description,
                stats_json={},
            )
            self.session.add(pool)
            self.session.flush()
        else:
            if pool.root_path != str(root):
                pool.root_path = str(root)
            if not pool.description:
                pool.description = description
        self.refresh_pool_stats(pool.id)
        return pool

    def update_pool(
        self,
        pool_id: str,
        *,
        name: str | None = None,
        root_path: str | None = None,
        description: str | None = None,
    ) -> DataPool:
        pool = self.get_pool(pool_id)
        if name is not None:
            normalized_name = str(name or "").strip()
            if not normalized_name:
                raise ValueError("data pool name is required")
            pool.name = normalized_name
        if root_path is not None:
            root = _resolve_root_path(root_path)
            root.mkdir(parents=True, exist_ok=True)
            pool.root_path = str(root)
        if description is not None:
            pool.description = str(description or "").strip() or None
        self.session.flush()
        self.refresh_pool_stats(pool.id)
        return pool

    def delete_pool(self, pool_id: str) -> dict[str, Any]:
        pool = self.session.scalar(
            select(DataPool).where(DataPool.id == pool_id).with_for_update()
        )
        if pool is None:
            raise ValueError(f"data pool not found: {pool_id}")
        references = {
            "batch tasks": self.session.scalar(
                select(func.count(BatchTask.id)).where(BatchTask.data_pool_id == pool_id)
            )
            or 0,
            "stage4 backfill jobs as source": self.session.scalar(
                select(func.count(Stage4DataPoolBackfillJob.id)).where(
                    Stage4DataPoolBackfillJob.source_pool_id == pool_id
                )
            )
            or 0,
            "stage4 backfill jobs as target": self.session.scalar(
                select(func.count(Stage4DataPoolBackfillJob.id)).where(
                    Stage4DataPoolBackfillJob.target_pool_id == pool_id
                )
            )
            or 0,
            "download selections": self.session.scalar(
                select(func.count(DataPoolDownloadSelection.id)).where(
                    DataPoolDownloadSelection.pool_id == pool_id
                )
            )
            or 0,
        }
        blockers = [f"{label}={int(count)}" for label, count in references.items() if count]
        if blockers:
            raise DataPoolDeleteConflictError(
                f"cannot delete data pool {pool_id}; still referenced by {', '.join(blockers)}"
            )
        asset_ids = list(self.session.scalars(select(DataPoolAsset.id).where(DataPoolAsset.pool_id == pool_id)))
        deleted_assets = self.delete_assets(pool_id, asset_ids=asset_ids)
        self.session.delete(pool)
        self.session.flush()
        return {"deleted_pool_id": pool_id, **deleted_assets}

    def get_pool(self, pool_id: str) -> DataPool:
        pool = self.session.get(DataPool, pool_id)
        if pool is None:
            raise ValueError(f"data pool not found: {pool_id}")
        return pool

    def pool_root_status(self, pool: DataPool) -> dict[str, Any]:
        root = Path(pool.root_path).expanduser()
        root_exists = root.exists()
        root_is_dir = root.is_dir()
        return {
            "root_exists": root_exists,
            "root_is_dir": root_is_dir,
            "is_available": root_exists and root_is_dir,
        }

    def require_pool_available(self, pool_id: str) -> DataPool:
        pool = self.get_pool(pool_id)
        root = Path(pool.root_path).expanduser()
        if pool.name == DEFAULT_DATA_POOL_NAME and not root.exists():
            root.mkdir(parents=True, exist_ok=True)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"data pool root_path is missing or not a directory: {pool.root_path}")
        return pool

    def list_pools(self) -> list[DataPool]:
        rows = list(self.session.scalars(select(DataPool).order_by(DataPool.created_at.desc(), DataPool.id.desc())))
        return [pool for pool in rows if pool.name == DEFAULT_DATA_POOL_NAME] + [
            pool for pool in rows if pool.name != DEFAULT_DATA_POOL_NAME
        ]

    def serialize_pool(self, pool: DataPool) -> dict[str, Any]:
        stats = dict(pool.stats_json or {})
        root_status = self.pool_root_status(pool)
        return {
            "id": pool.id,
            "name": pool.name,
            "root_path": pool.root_path,
            "description": pool.description,
            "stats": stats,
            "is_default": pool.name == DEFAULT_DATA_POOL_NAME,
            **root_status,
            "asset_count": int(stats.get("asset_count") or 0),
            "repo_count": int(stats.get("repo_count") or 0),
            "created_at": serialize_datetime(pool.created_at),
            "updated_at": serialize_datetime(pool.updated_at),
        }

    def refresh_pool_stats(self, pool_id: str) -> dict[str, Any]:
        pool = self.get_pool(pool_id)
        asset_count = self.session.scalar(
            select(func.count(DataPoolAsset.id)).where(DataPoolAsset.pool_id == pool_id)
        ) or 0
        repo_count = self.session.scalar(
            select(func.count(func.distinct(DataPoolAsset.github_repo_id))).where(DataPoolAsset.pool_id == pool_id)
        ) or 0
        entry_count = self.session.scalar(
            select(func.count(func.distinct(DataPoolAsset.entry_file_path))).where(DataPoolAsset.pool_id == pool_id)
        ) or 0
        pool.stats_json = {
            "asset_count": int(asset_count),
            "repo_count": int(repo_count),
            "entry_file_count": int(entry_count),
            "updated_at": serialize_datetime(_utcnow()),
        }
        self.session.flush()
        return dict(pool.stats_json or {})

    def asset_exists(
        self,
        *,
        pool_id: str,
        github_repo_id: int,
        entry_file_path: str,
        depth: int,
    ) -> bool:
        return self.session.scalar(
            select(DataPoolAsset.id)
            .where(
                DataPoolAsset.pool_id == pool_id,
                DataPoolAsset.github_repo_id == int(github_repo_id),
                DataPoolAsset.entry_file_path == str(entry_file_path or ""),
                DataPoolAsset.depth == int(depth),
            )
            .limit(1)
        ) is not None

    def get_asset(self, pool_id: str, asset_id: str) -> DataPoolAsset:
        asset = self.session.scalar(
            select(DataPoolAsset).where(
                DataPoolAsset.pool_id == pool_id,
                DataPoolAsset.id == asset_id,
            )
        )
        if asset is None:
            raise ValueError(f"data pool asset not found: {asset_id}")
        return asset

    def list_assets(
        self,
        pool_id: str,
        *,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = None,
        depth_min: int | None = None,
        depth_max: int | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        self.get_pool(pool_id)
        page = max(1, int(page or 1))
        page_size = max(1, min(int(page_size or 25), 200))
        normalized_sort_by = sort_by if sort_by in DATA_POOL_ASSET_SORT_FIELDS else "created_at"
        normalized_sort_order = "asc" if sort_order == "asc" else "desc"
        conditions = self._asset_conditions(
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
        total = int(self.session.scalar(select(func.count(DataPoolAsset.id)).where(*conditions)) or 0)
        total_pages = max((total + page_size - 1) // page_size, 1)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        if normalized_sort_by in DATA_POOL_ASSET_SQL_SORT_FIELDS:
            sort_column = {
                "created_at": DataPoolAsset.created_at,
                "repo": DataPoolAsset.repo_full_name,
                "commit": DataPoolAsset.source_commit_sha,
                "language": DataPoolAsset.language,
                "stars": DataPoolAsset.stars,
                "entry_file": DataPoolAsset.entry_file_path,
                "depth": DataPoolAsset.depth,
            }.get(normalized_sort_by, DataPoolAsset.created_at)
            ordered = sort_column.asc() if normalized_sort_order == "asc" else sort_column.desc()
            tie_breaker = DataPoolAsset.id.asc() if normalized_sort_order == "asc" else DataPoolAsset.id.desc()
            rows = list(
                self.session.scalars(
                    select(DataPoolAsset)
                    .where(*conditions)
                    .order_by(ordered, tie_breaker)
                    .offset(offset)
                    .limit(page_size)
                )
            )
            assets_payload = [self.serialize_asset_summary(row) for row in rows]
        else:
            rows = list(
                self.session.scalars(
                    select(DataPoolAsset)
                    .where(*conditions)
                    .order_by(DataPoolAsset.id.desc())
                )
            )
            records = []
            for row in rows:
                metrics = self._asset_metrics(row)
                sort_summary = {
                    "id": row.id,
                    "repo_full_name": row.repo_full_name,
                    "language": row.language,
                    "source_commit_sha": row.source_commit_sha,
                    "stars": row.stars,
                    "entry_file_path": row.entry_file_path,
                    "depth": row.depth,
                    "created_at": serialize_datetime(row.created_at),
                    **metrics,
                }
                records.append((row, metrics, sort_summary))
            records.sort(
                key=cmp_to_key(
                    lambda left, right: _compare_data_pool_asset_summaries(
                        left[2],
                        right[2],
                        sort_by=normalized_sort_by,
                        sort_order=normalized_sort_order,
                    )
                )
            )
            assets_payload = [
                self._serialize_asset_summary(row, metrics)
                for row, metrics, _ in records[offset:offset + page_size]
            ]
        return {
            "assets": assets_payload,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
            },
        }

    def list_asset_ids(
        self,
        pool_id: str,
        *,
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = None,
        depth_min: int | None = None,
        depth_max: int | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        self.get_pool(pool_id)
        conditions = self._asset_conditions(
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
        asset_ids = [
            str(row)
            for row in self.session.scalars(
                select(DataPoolAsset.id)
                .where(*conditions)
                .order_by(DataPoolAsset.created_at.desc(), DataPoolAsset.id.desc())
            )
        ]
        return {
            "asset_ids": asset_ids,
            "total": len(asset_ids),
        }

    def preview_pool(self, pool_id: str) -> dict[str, Any]:
        pool = self.get_pool(pool_id)
        assets = list(
            self.session.scalars(
                select(DataPoolAsset)
                .where(DataPoolAsset.pool_id == pool_id)
                .order_by(DataPoolAsset.created_at.desc(), DataPoolAsset.id.desc())
            )
        )
        repo_stats: dict[int, dict[str, Any]] = {}
        entry_keys: set[tuple[int, str]] = set()
        language_counter: Counter[str] = Counter()
        depth_counter: Counter[int] = Counter()
        patch_file_values: list[int] = []
        patch_line_values: list[int] = []
        added_line_values: list[int] = []
        deleted_line_values: list[int] = []
        f2p_values: list[int] = []
        p2p_values: list[int] = []
        entry_pass_rate_values: list[float] = []
        for asset in assets:
            metrics = self._asset_metrics(asset)
            diff_stats = dict(metrics.get("diff_stats") or {})
            files_changed = max(_normalize_int(diff_stats.get("files_changed")), 0)
            lines_changed = max(_normalize_int(diff_stats.get("lines_changed")), 0)
            lines_added = max(_normalize_int(diff_stats.get("lines_added")), 0)
            lines_deleted = max(_normalize_int(diff_stats.get("lines_deleted")), 0)
            f2p_count = max(_normalize_int(metrics.get("f2p_count")), 0)
            p2p_count = max(_normalize_int(metrics.get("p2p_count")), 0)
            entry_pass_rate = _normalize_optional_float(metrics.get("entry_pass_rate"))
            depth = max(_normalize_int(asset.depth), 0)
            repo_id = int(asset.github_repo_id)
            repo_name = str(asset.repo_full_name or repo_id)
            entry_file_path = str(asset.entry_file_path or "")

            entry_keys.add((repo_id, entry_file_path))
            depth_counter[depth] += 1
            patch_file_values.append(files_changed)
            patch_line_values.append(lines_changed)
            added_line_values.append(lines_added)
            deleted_line_values.append(lines_deleted)
            f2p_values.append(f2p_count)
            p2p_values.append(p2p_count)
            if entry_pass_rate is not None:
                entry_pass_rate_values.append(entry_pass_rate)
            language = str(asset.language or "Unknown").strip() or "Unknown"
            language_counter[language] += 1

            repo_row = repo_stats.setdefault(
                repo_id,
                {
                    "github_repo_id": repo_id,
                    "repo_full_name": repo_name,
                    "language": asset.language,
                    "stars": int(asset.stars or 0),
                    "asset_count": 0,
                    "entry_files": set(),
                    "depths": [],
                },
            )
            repo_row["asset_count"] += 1
            repo_row["entry_files"].add(entry_file_path)
            repo_row["depths"].append(depth)

        repos = []
        for row in repo_stats.values():
            depths = list(row["depths"])
            repos.append(
                {
                    "github_repo_id": row["github_repo_id"],
                    "repo_full_name": row["repo_full_name"],
                    "language": row["language"],
                    "stars": row["stars"],
                    "asset_count": row["asset_count"],
                    "entry_count": len(row["entry_files"]),
                    "min_depth": min(depths) if depths else None,
                    "max_depth": max(depths) if depths else None,
                }
            )
        repos.sort(key=lambda row: (-int(row["asset_count"]), str(row["repo_full_name"])))
        repo_asset_counts = [int(row["asset_count"]) for row in repos]

        patch_buckets = [
            ("0-9", 0, 9),
            ("10-24", 10, 24),
            ("25-49", 25, 49),
            ("50-99", 50, 99),
            ("100-199", 100, 199),
            ("200+", 200, None),
        ]
        patch_file_buckets = [
            ("0", 0, 0),
            ("1", 1, 1),
            ("2", 2, 2),
            ("3-4", 3, 4),
            ("5-9", 5, 9),
            ("10+", 10, None),
        ]
        f2p_buckets = [
            ("0", 0, 0),
            ("1", 1, 1),
            ("2", 2, 2),
            ("3-4", 3, 4),
            ("5-9", 5, 9),
            ("10-19", 10, 19),
            ("20+", 20, None),
        ]
        repo_asset_buckets = [
            ("1", 1, 1),
            ("2", 2, 2),
            ("3-4", 3, 4),
            ("5-9", 5, 9),
            ("10-19", 10, 19),
            ("20+", 20, None),
        ]
        pass_rate_buckets = [
            ("0-20%", 0.0, 0.199999),
            ("20-40%", 0.2, 0.399999),
            ("40-60%", 0.4, 0.599999),
            ("60-80%", 0.6, 0.799999),
            ("80-100%", 0.8, 1.0),
        ]
        repo_asset_distribution_buckets = []
        for bucket in _histogram(repo_asset_counts, repo_asset_buckets):
            repo_asset_distribution_buckets.append(
                {
                    "label": bucket["label"],
                    "min": bucket["min"],
                    "max": bucket["max"],
                    "repo_count": bucket["asset_count"],
                }
            )

        asset_count = len(assets)
        repo_count = len(repo_stats)
        return {
            "pool": self.serialize_pool(pool),
            "summary": {
                "asset_count": asset_count,
                "repo_count": repo_count,
                "entry_count": len(entry_keys),
                "language_count": len(language_counter),
                "max_depth": max(depth_counter.keys()) if depth_counter else 0,
                "avg_assets_per_repo": (asset_count / repo_count) if repo_count else 0.0,
            },
            "language_distribution": [
                {"language": language, "asset_count": int(count)}
                for language, count in sorted(language_counter.items(), key=lambda item: (-item[1], item[0]))
            ],
            "depth_distribution": _counter_distribution(depth_counter, value_key="depth"),
            "patch_distribution": {
                "buckets": _histogram(patch_line_values, patch_buckets),
                "stats": {
                    **_numeric_summary(patch_line_values),
                    "added": _numeric_summary(added_line_values),
                    "deleted": _numeric_summary(deleted_line_values),
                },
            },
            "patch_file_distribution": {
                "buckets": _histogram(patch_file_values, patch_file_buckets),
                "stats": _numeric_summary(patch_file_values),
            },
            "repo_asset_distribution": {
                "buckets": repo_asset_distribution_buckets,
                "stats": _numeric_summary(repo_asset_counts),
                "repos": repos,
            },
            "quality": {
                "f2p_distribution": {
                    "buckets": _histogram(f2p_values, f2p_buckets),
                    "stats": _numeric_summary(f2p_values),
                },
                "p2p_stats": _numeric_summary(p2p_values),
                "entry_pass_rate_distribution": {
                    "buckets": _histogram(entry_pass_rate_values, pass_rate_buckets),
                    "stats": _numeric_summary(entry_pass_rate_values),
                },
            },
        }

    def create_download_selection(self, pool_id: str, *, asset_ids: list[str]) -> dict[str, Any]:
        self.get_pool(pool_id)
        normalized_ids = [str(asset_id).strip() for asset_id in list(asset_ids or []) if str(asset_id).strip()]
        if not normalized_ids:
            raise ValueError("No assets selected for download")
        assets = list(
            self.session.scalars(
                select(DataPoolAsset.id)
                .where(
                    DataPoolAsset.pool_id == pool_id,
                    DataPoolAsset.id.in_(normalized_ids),
                )
                .order_by(DataPoolAsset.created_at.desc(), DataPoolAsset.id.desc())
            )
        )
        valid_ids = [str(asset_id) for asset_id in assets]
        if not valid_ids:
            raise ValueError("No assets selected for download")
        selection = DataPoolDownloadSelection(
            pool_id=pool_id,
            asset_ids_json=valid_ids,
            asset_count=len(valid_ids),
        )
        self.session.add(selection)
        self.session.flush()
        return {
            "id": selection.id,
            "pool_id": selection.pool_id,
            "asset_count": selection.asset_count,
            "created_at": serialize_datetime(selection.created_at),
        }

    def get_download_selection(self, pool_id: str, selection_id: str) -> DataPoolDownloadSelection:
        selection = self.session.scalar(
            select(DataPoolDownloadSelection).where(
                DataPoolDownloadSelection.pool_id == pool_id,
                DataPoolDownloadSelection.id == str(selection_id or "").strip(),
            )
        )
        if selection is None:
            raise ValueError(f"data pool download selection not found: {selection_id}")
        return selection

    def _asset_metrics(self, asset: DataPoolAsset, *, folder: Path | None = None) -> dict[str, Any]:
        manifest = dict(asset.manifest_json or {})
        manifest_metrics = dict(manifest.get("metrics") or {}) if isinstance(manifest.get("metrics"), dict) else {}
        folder = folder or Path(asset.folder_path).expanduser()

        entry_pass_rate = _normalize_optional_float(manifest_metrics.get("entry_pass_rate"))
        p2p_count = _normalize_int(manifest_metrics.get("p2p_count", 0))
        f2p_count = _normalize_int(manifest_metrics.get("f2p_count", 0))
        issue_count = _normalize_int(manifest_metrics.get("issue_count", 0))
        diff_stats = _normalize_diff_stats(manifest_metrics.get("diff_stats"))

        needs_feedback = entry_pass_rate is None or (p2p_count <= 0 and f2p_count <= 0)
        if needs_feedback:
            feedback = _read_json_file(folder / "savepoint_feedback.json")
            feedback = feedback if isinstance(feedback, dict) else {}
            if entry_pass_rate is None:
                entry_pass_rate = _normalize_optional_float(feedback.get("entry_pass_rate"))
            if p2p_count <= 0 and f2p_count <= 0:
                p2p_count = _normalize_int(feedback.get("p2p_count", 0))
                f2p_count = _normalize_int(feedback.get("f2p_count", 0))

        if issue_count <= 0:
            issues = _read_json_file(folder / "issues.json")
            issue_count = _normalize_int(len(issues) if isinstance(issues, list) else 0)
        if diff_stats["lines_changed"] <= 0 or diff_stats["files_changed"] <= 0:
            diff_stats = _normalize_diff_stats(_diff_stats_from_patch_text(_read_text_file(folder / "gold.patch")))

        needs_db_fallback = (
            entry_pass_rate is None
            or (p2p_count <= 0 and f2p_count <= 0)
            or issue_count <= 0
            or diff_stats["lines_changed"] <= 0
            or diff_stats["files_changed"] <= 0
        )
        if needs_db_fallback and asset.stage4_run_id:
            run = self.session.scalar(
                select(Stage4Run)
                .where(Stage4Run.id == asset.stage4_run_id)
                .options(
                    selectinload(Stage4Run.issue_variants),
                    selectinload(Stage4Run.source_savepoint),
                )
            )
            if run is not None and run.source_savepoint is not None:
                savepoint = run.source_savepoint
                if entry_pass_rate is None:
                    entry_pass_rate = _normalize_optional_float(savepoint.entry_pass_rate)
                if p2p_count <= 0 and f2p_count <= 0:
                    p2p_count = len(list(savepoint.p2p_files_json or []))
                    f2p_count = len(list(savepoint.f2p_files_json or []))
                if diff_stats["lines_changed"] <= 0 or diff_stats["files_changed"] <= 0:
                    diff_stats = _normalize_diff_stats(_diff_stats_from_patch_text(savepoint.gold_patch_text))
                if issue_count <= 0:
                    issue_count = len(list(run.issue_variants or []))

        return {
            "entry_pass_rate": entry_pass_rate,
            "p2p_count": max(p2p_count, 0),
            "f2p_count": max(f2p_count, 0),
            "diff_stats": diff_stats,
            "issue_count": max(issue_count, 0),
        }

    def serialize_asset_summary(self, asset: DataPoolAsset) -> dict[str, Any]:
        metrics = self._asset_metrics(asset)
        return self._serialize_asset_summary(asset, metrics)

    def _serialize_asset_summary(self, asset: DataPoolAsset, metrics: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": asset.id,
            "pool_id": asset.pool_id,
            "repository_id": asset.repository_id,
            "github_repo_id": asset.github_repo_id,
            "repo_full_name": asset.repo_full_name,
            "source_commit_sha": asset.source_commit_sha,
            "language": asset.language,
            "stars": asset.stars,
            "entry_file_path": asset.entry_file_path,
            "depth": asset.depth,
            "stage2_run_id": asset.stage2_run_id,
            "stage3_run_id": asset.stage3_run_id,
            "stage3_savepoint_id": asset.stage3_savepoint_id,
            "stage4_run_id": asset.stage4_run_id,
            "folder_name": asset.folder_name,
            "folder_path": asset.folder_path,
            "manifest": dict(asset.manifest_json or {}),
            **metrics,
            "created_at": serialize_datetime(asset.created_at),
            "updated_at": serialize_datetime(asset.updated_at),
        }

    def serialize_asset_detail(self, asset: DataPoolAsset) -> dict[str, Any]:
        folder = Path(asset.folder_path).expanduser()
        metrics = self._asset_metrics(asset, folder=folder)
        files = {
            "manifest": _read_json_file(folder / "manifest.json") or dict(asset.manifest_json or {}),
            "issues": _read_json_file(folder / "issues.json"),
            "original_p2p_files": _read_json_file(folder / "original_p2p_files.json"),
            "savepoint_feedback": _read_json_file(folder / "savepoint_feedback.json"),
            "savepoint_full_validation": _read_json_file(folder / "savepoint_full_validation.json"),
            "hidden_f2p_files": _read_json_file(folder / "hidden_f2p_files.json"),
            "p2p_file_snapshots": _read_json_file(folder / "p2p_file_snapshots.json"),
            "dockerfile": (folder / "dockerfile").read_text(encoding="utf-8") if (folder / "dockerfile").is_file() else "",
            "run_script": (folder / "run_script.sh").read_text(encoding="utf-8") if (folder / "run_script.sh").is_file() else "",
            "gold_patch": (folder / "gold.patch").read_text(encoding="utf-8") if (folder / "gold.patch").is_file() else "",
        }
        llm_files: dict[str, list[str]] = {}
        llm_completions: dict[str, list[dict[str, Any]]] = {}
        llm_root = folder / "llm"
        if llm_root.exists():
            for role_dir in sorted(path for path in llm_root.iterdir() if path.is_dir()):
                role_files: list[str] = []
                role_payloads: list[dict[str, Any]] = []
                for path in sorted(role_dir.rglob("*.json")):
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    relative = str(path.relative_to(role_dir))
                    role_files.append(relative)
                    role_payloads.append(
                        {
                            "path": relative,
                            "size_bytes": stat.st_size,
                            "modified_at": serialize_datetime(datetime.fromtimestamp(stat.st_mtime, tz=UTC)),
                        }
                    )
                llm_files[role_dir.name] = role_files
                llm_completions[role_dir.name] = role_payloads
        return {
            **self._serialize_asset_summary(asset, metrics),
            "metrics": metrics,
            "files": files,
            "llm_completion_files": llm_files,
            "llm_completions": llm_completions,
        }

    def serialize_asset_llm_completion_file(
        self,
        pool_id: str,
        asset_id: str,
        role: str,
        file_path: str,
    ) -> dict[str, Any]:
        asset = self.get_asset(pool_id, asset_id)
        normalized_role = str(role or "").strip()
        if not normalized_role or "/" in normalized_role or "\\" in normalized_role or ".." in normalized_role:
            raise ValueError("invalid LLM completion role")
        relative = _safe_relative_path(file_path)
        role_dir = Path(asset.folder_path).expanduser() / "llm" / normalized_role
        path = (role_dir / relative).resolve()
        try:
            path.relative_to(role_dir.resolve())
        except ValueError as exc:
            raise ValueError("invalid LLM completion file path") from exc
        if not path.is_file():
            raise ValueError(f"LLM completion file not found: {file_path}")
        raw_text = path.read_text(encoding="utf-8")
        try:
            return {"kind": "json", "value": json.loads(raw_text)}
        except json.JSONDecodeError:
            return {"kind": "text", "text": raw_text}

    def delete_assets(
        self,
        pool_id: str,
        *,
        asset_ids: list[str] | None = None,
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = None,
        depth_min: int | None = None,
        depth_max: int | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> dict[str, Any]:
        self.get_pool(pool_id)
        conditions = self._asset_conditions(
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
        if asset_ids is not None:
            normalized_ids = [str(asset_id).strip() for asset_id in asset_ids if str(asset_id).strip()]
            if not normalized_ids:
                return {"deleted_count": 0, "failed_paths": []}
            conditions.append(DataPoolAsset.id.in_(normalized_ids))
        assets = list(self.session.scalars(select(DataPoolAsset).where(*conditions)))
        failed_paths: list[str] = []
        for asset in assets:
            folder = Path(asset.folder_path).expanduser()
            if folder.exists():
                try:
                    if folder.is_dir():
                        shutil.rmtree(folder)
                    else:
                        folder.unlink()
                except Exception:
                    failed_paths.append(str(folder))
                    continue
            self.session.delete(asset)
        self.session.flush()
        self.refresh_pool_stats(pool_id)
        return {
            "deleted_count": len(assets) - len(failed_paths),
            "failed_paths": failed_paths,
        }

    def build_pool_zip(self, pool_id: str, *, asset_ids: list[str] | None = None) -> tuple[Path, str]:
        pool = self.get_pool(pool_id)
        statement = (
            select(DataPoolAsset)
            .where(DataPoolAsset.pool_id == pool_id)
            .order_by(DataPoolAsset.repo_full_name.asc(), DataPoolAsset.entry_file_path.asc(), DataPoolAsset.depth.asc())
        )
        if asset_ids is not None:
            normalized_ids = [str(asset_id).strip() for asset_id in asset_ids if str(asset_id).strip()]
            if not normalized_ids:
                raise ValueError("No assets selected for download")
            statement = statement.where(DataPoolAsset.id.in_(normalized_ids))
        assets = list(self.session.scalars(statement))
        if asset_ids is not None and not assets:
            raise ValueError("No assets selected for download")
        temp = tempfile.NamedTemporaryFile(prefix="feature-factory-data-pool-", suffix=".zip", delete=False)
        temp_path = Path(temp.name)
        temp.close()
        try:
            with zipfile.ZipFile(temp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for asset in assets:
                    folder = Path(asset.folder_path).expanduser()
                    if not folder.exists() or not folder.is_dir():
                        continue
                    for path in sorted(folder.rglob("*")):
                        if path.is_file():
                            archive.write(path, arcname=str(Path(asset.folder_name) / path.relative_to(folder)))
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        suffix = "-selected" if asset_ids is not None else ""
        return temp_path, f"{_safe_name(pool.name, max_length=80)}{suffix}.zip"

    def build_download_selection_zip(self, pool_id: str, selection_id: str) -> tuple[Path, str]:
        selection = self.get_download_selection(pool_id, selection_id)
        return self.build_pool_zip(pool_id, asset_ids=list(selection.asset_ids_json or []))

    def materialize_stage4_run(
        self,
        *,
        pool_id: str,
        stage4_run_id: str,
        batch_task_id: str | None = None,
        reuse_compatible_existing: bool = False,
    ) -> DataPoolAsset:
        pool = self.require_pool_available(pool_id)
        run = self._get_generated_stage4_run(stage4_run_id)
        savepoint = run.source_savepoint
        stage3_run = savepoint.run
        entry_file = stage3_run.entry_file
        snapshot = entry_file.snapshot
        repository = snapshot.repository

        def query_existing_asset() -> DataPoolAsset | None:
            return self.session.scalar(
                select(DataPoolAsset)
                .where(
                    DataPoolAsset.pool_id == pool_id,
                    DataPoolAsset.github_repo_id == int(repository.github_repo_id),
                    DataPoolAsset.entry_file_path == entry_file.test_file_path,
                    DataPoolAsset.depth == int(savepoint.depth),
                )
                .limit(1)
            )

        root = Path(pool.root_path).expanduser()
        tmp_root = root / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        base_folder_name = self._asset_folder_name(
            repository=repository,
            snapshot=snapshot,
            entry_file=entry_file,
            depth=int(savepoint.depth),
        )
        asset_id = str(uuid.uuid4())
        tmp_dir = tmp_root / f"{asset_id}-{uuid.uuid4().hex[:8]}"
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        stage2_run = self.session.get(Stage2Run, snapshot.source_stage2_run_id)
        issues = [self._serialize_issue(row) for row in list(run.issue_variants or [])]
        savepoint_summary = dict(savepoint.summary_json or {})
        feedback = dict(savepoint_summary.get("feedback") or {})
        savepoint_feedback_payload = {
            "savepoint_id": savepoint.id,
            "depth": savepoint.depth,
            "feedback": feedback,
            "collateral": dict(savepoint.collateral_json or {}),
            "summary": savepoint_summary,
        }
        diff_stats = _diff_stats_from_patch_text(savepoint.gold_patch_text)
        savepoint_file_results = [
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
                "raw_result": dict(row.raw_result_json or {}),
            }
            for row in list(savepoint.file_results or [])
        ]
        materialized_at = _utcnow()
        manifest = {
            "schema_version": 1,
            "issue_schema_version": CURRENT_ISSUE_SCHEMA_VERSION,
            "batch_task_id": batch_task_id,
            "data_pool_id": pool_id,
            "asset_id": asset_id,
            "repository": {
                "id": repository.id,
                "github_repo_id": repository.github_repo_id,
                "full_name": repository.full_name,
                "html_url": repository.html_url,
                "language": repository.primary_language,
                "stars": repository.stargazers_count,
            },
            "commit": snapshot.source_commit_sha,
            "entry_file": entry_file.test_file_path,
            "target_selector": entry_file.target_selector or entry_file.test_file_path,
            "depth": savepoint.depth,
            "stage2_run_id": snapshot.source_stage2_run_id,
            "stage3_run_id": stage3_run.id,
            "stage3_savepoint_id": savepoint.id,
            "stage4_run_id": run.id,
            "metrics": {
                "entry_pass_rate": feedback.get("entry_pass_rate", savepoint.entry_pass_rate),
                "p2p_count": feedback.get("p2p_count", len(list(savepoint.p2p_files_json or []))),
                "f2p_count": feedback.get("f2p_count", len(list(savepoint.f2p_files_json or []))),
                "diff_stats": diff_stats,
                "issue_count": len(issues),
            },
            "created_at": serialize_datetime(materialized_at),
        }

        _write_json(tmp_dir / "manifest.json", manifest)
        _write_json(tmp_dir / "issues.json", issues)
        _write_text(tmp_dir / "dockerfile", snapshot.dockerfile_text)
        _write_text(tmp_dir / "run_script.sh", snapshot.run_script_text)
        _write_text(tmp_dir / "gold.patch", savepoint.gold_patch_text)
        _write_json(tmp_dir / "original_p2p_files.json", list(snapshot.original_p2p_files_json or []))
        _write_json(tmp_dir / "savepoint_feedback.json", savepoint_feedback_payload)
        _write_json(tmp_dir / "savepoint_full_validation.json", savepoint_file_results)
        _write_repo_file_snapshots(
            tmp_dir,
            run=run,
            repo_paths=[str(path) for path in list(savepoint.f2p_files_json or []) if str(path).strip()],
            manifest_name="hidden_f2p_files.json",
            storage_dir_name="hidden_f2p_files",
            label="hidden F2P",
        )
        _write_repo_file_snapshots(
            tmp_dir,
            run=run,
            repo_paths=[str(path) for path in list(savepoint.p2p_files_json or []) if str(path).strip()],
            manifest_name="p2p_file_snapshots.json",
            storage_dir_name="p2p_file_snapshots",
            label="P2P",
        )
        _write_json(tmp_dir / "stage2_full_report.json", dict(getattr(stage2_run, "full_report_json", {}) or {}))

        stage2_runtime = runtime_dir_from_workspace_path(getattr(stage2_run, "workspace_path", None), run_id=snapshot.source_stage2_run_id)
        stage3_runtime = runtime_dir_from_workspace_path(stage3_run.workspace_path, run_id=stage3_run.id)
        stage4_runtime = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        llm_destination = tmp_dir / "llm"
        for role in ("planner", "worker"):
            _copy_llm_completion_archive(runtime_dir=stage2_runtime, role=role, destination=llm_destination)
        _copy_llm_completion_archive(runtime_dir=stage3_runtime, role="breaker", destination=llm_destination)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        for spec in runtime_issue_style_specs(runtime_stage4):
            _copy_llm_completion_archive(
                runtime_dir=stage4_runtime,
                role=spec.id,
                destination=llm_destination,
            )

        lock_dir = _asset_materialize_lock_dir(
            tmp_root,
            pool_id=pool_id,
            github_repo_id=int(repository.github_repo_id),
            entry_file_path=entry_file.test_file_path,
            depth=int(savepoint.depth),
        )

        def apply_asset_payload(asset: DataPoolAsset, *, folder_name: str, final_dir: Path) -> None:
            asset.repository_id = repository.id
            asset.github_repo_id = repository.github_repo_id
            asset.repo_full_name = repository.full_name
            asset.source_commit_sha = snapshot.source_commit_sha
            asset.language = repository.primary_language
            asset.stars = repository.stargazers_count
            asset.entry_file_path = entry_file.test_file_path
            asset.depth = savepoint.depth
            asset.stage2_run_id = snapshot.source_stage2_run_id
            asset.stage3_run_id = stage3_run.id
            asset.stage3_savepoint_id = savepoint.id
            asset.stage4_run_id = run.id
            asset.folder_name = folder_name
            asset.folder_path = str(final_dir)
            asset.manifest_json = dict(manifest)
            asset.updated_at = materialized_at

        def bind_publish_target(existing_asset: DataPoolAsset | None) -> tuple[str, str, Path]:
            if existing_asset is not None:
                bound_asset_id = existing_asset.id
                bound_folder_name = str(existing_asset.folder_name or base_folder_name)
                bound_final_dir = (
                    Path(existing_asset.folder_path).expanduser()
                    if existing_asset.folder_path
                    else root / bound_folder_name
                )
            else:
                bound_asset_id = asset_id
                bound_folder_name = base_folder_name
                bound_final_dir = root / bound_folder_name
                if bound_final_dir.exists():
                    bound_folder_name = f"{bound_folder_name}-{bound_asset_id[:8]}"
                    bound_final_dir = root / bound_folder_name
            manifest["asset_id"] = bound_asset_id
            _write_json(tmp_dir / "manifest.json", manifest)
            return bound_asset_id, bound_folder_name, bound_final_dir

        try:
            with _asset_materialize_lock(lock_dir, session=self.session):
                existing = query_existing_asset()
                if (
                    existing is not None
                    and reuse_compatible_existing
                    and _asset_matches_stage4_run_contract(self.session, existing, run)
                ):
                    return existing
                bound_asset_id, folder_name, final_dir = bind_publish_target(existing)
                if existing is None:
                    asset = DataPoolAsset(
                        id=bound_asset_id,
                        pool_id=pool_id,
                        github_repo_id=repository.github_repo_id,
                        repo_full_name=repository.full_name,
                        entry_file_path=entry_file.test_file_path,
                        depth=savepoint.depth,
                        folder_name=folder_name,
                        folder_path=str(final_dir),
                        manifest_json=dict(manifest),
                    )
                    apply_asset_payload(asset, folder_name=folder_name, final_dir=final_dir)
                    self.session.add(asset)
                else:
                    asset = existing
                    apply_asset_payload(asset, folder_name=folder_name, final_dir=final_dir)
                try:
                    self.session.flush()
                except IntegrityError:
                    self.session.rollback()
                    existing_after_race = self.session.scalar(
                        select(DataPoolAsset)
                        .where(
                            DataPoolAsset.pool_id == pool_id,
                            DataPoolAsset.github_repo_id == int(repository.github_repo_id),
                            DataPoolAsset.entry_file_path == entry_file.test_file_path,
                            DataPoolAsset.depth == int(savepoint.depth),
                        )
                        .limit(1)
                    )
                    if existing_after_race is None:
                        raise
                    bound_asset_id, folder_name, final_dir = bind_publish_target(existing_after_race)
                    asset = existing_after_race
                    apply_asset_payload(asset, folder_name=folder_name, final_dir=final_dir)
                    self.session.flush()

                backup_dir: Path | None = None
                published = False
                try:
                    backup_dir = _replace_directory(tmp_dir, final_dir, backup_root=tmp_root)
                    published = True
                    self.refresh_pool_stats(pool_id)
                except Exception:
                    if published:
                        _rollback_replaced_directory(final_dir, backup_dir)
                    self.session.rollback()
                    raise
                _register_replaced_directory(
                    self.session,
                    destination=final_dir,
                    backup_dir=backup_dir,
                )
                return asset
        finally:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def _asset_conditions(
        self,
        pool_id: str,
        *,
        query: str | None = None,
        repo: str | None = None,
        commit: str | None = None,
        language: str | None = None,
        entry_file: str | None = None,
        depth: int | None = None,
        depth_min: int | None = None,
        depth_max: int | None = None,
        stars_min: int | None = None,
        stars_max: int | None = None,
        stage2_run_id: str | None = None,
        stage3_run_id: str | None = None,
        stage4_run_id: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
    ) -> list[Any]:
        conditions: list[Any] = [DataPoolAsset.pool_id == pool_id]
        normalized_query = str(query or "").strip()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            conditions.append(
                or_(
                    DataPoolAsset.repo_full_name.ilike(pattern),
                    DataPoolAsset.entry_file_path.ilike(pattern),
                    DataPoolAsset.source_commit_sha.ilike(pattern),
                    DataPoolAsset.stage2_run_id.ilike(pattern),
                    DataPoolAsset.stage3_run_id.ilike(pattern),
                    DataPoolAsset.stage4_run_id.ilike(pattern),
                    DataPoolAsset.folder_name.ilike(pattern),
                )
            )
        normalized_repo = str(repo or "").strip()
        if normalized_repo:
            conditions.append(DataPoolAsset.repo_full_name.ilike(f"%{normalized_repo}%"))
        normalized_commit = str(commit or "").strip()
        if normalized_commit:
            conditions.append(DataPoolAsset.source_commit_sha.ilike(f"%{normalized_commit}%"))
        normalized_language = str(language or "").strip()
        if normalized_language:
            conditions.append(func.lower(DataPoolAsset.language) == normalized_language.lower())
        normalized_entry = str(entry_file or "").strip()
        if normalized_entry:
            conditions.append(DataPoolAsset.entry_file_path.ilike(f"%{normalized_entry}%"))
        if depth is not None:
            conditions.append(DataPoolAsset.depth == int(depth))
        if depth_min is not None:
            conditions.append(DataPoolAsset.depth >= int(depth_min))
        if depth_max is not None:
            conditions.append(DataPoolAsset.depth <= int(depth_max))
        if stars_min is not None:
            conditions.append(DataPoolAsset.stars >= int(stars_min))
        if stars_max is not None:
            conditions.append(DataPoolAsset.stars <= int(stars_max))
        normalized_stage2_run_id = str(stage2_run_id or "").strip()
        if normalized_stage2_run_id:
            conditions.append(DataPoolAsset.stage2_run_id.ilike(f"%{normalized_stage2_run_id}%"))
        normalized_stage3_run_id = str(stage3_run_id or "").strip()
        if normalized_stage3_run_id:
            conditions.append(DataPoolAsset.stage3_run_id.ilike(f"%{normalized_stage3_run_id}%"))
        normalized_stage4_run_id = str(stage4_run_id or "").strip()
        if normalized_stage4_run_id:
            conditions.append(DataPoolAsset.stage4_run_id.ilike(f"%{normalized_stage4_run_id}%"))
        if created_after is not None:
            conditions.append(DataPoolAsset.created_at >= created_after)
        if created_before is not None:
            conditions.append(DataPoolAsset.created_at <= created_before)
        return conditions

    def _get_generated_stage4_run(self, run_id: str) -> Stage4Run:
        run = self.session.scalar(
            select(Stage4Run)
            .options(
                selectinload(Stage4Run.issue_variants),
                selectinload(Stage4Run.source_savepoint)
                .selectinload(Stage3Savepoint.file_results),
                selectinload(Stage4Run.source_savepoint)
                .selectinload(Stage3Savepoint.run)
                .selectinload(Stage3Run.entry_file)
                .selectinload(Stage3EntryFile.snapshot)
                .selectinload(Stage3CommitSnapshot.repository),
            )
            .where(Stage4Run.id == str(run_id))
        )
        if run is None:
            raise ValueError(f"stage4 run not found: {run_id}")
        if run.status != Stage4RunStatus.completed.value or run.result != Stage4RunResult.generated.value:
            raise ValueError(f"stage4 run is not generated: {run_id}")
        return run

    def _asset_folder_name(
        self,
        *,
        repository: GitHubRepository,
        snapshot: Stage3CommitSnapshot,
        entry_file: Stage3EntryFile,
        depth: int,
    ) -> str:
        repo = _safe_name(repository.full_name.replace("/", "__"), max_length=72)
        commit = _safe_name(str(snapshot.source_commit_sha or "commit")[:12], max_length=16)
        entry = _safe_name(entry_file.test_file_path.replace("/", "__"), max_length=96)
        return f"{repo}__{commit}__{entry}__depth-{int(depth)}"

    def _serialize_issue(self, row: Stage4IssueVariant) -> dict[str, Any]:
        return {
            "variant_index": row.variant_index,
            "style": row.style,
            "title": row.title,
            "issue_markdown": row.issue_markdown,
            "issue_json": dict(row.issue_json or {}),
            "quality": dict(row.quality_json or {}),
            "leakage_check": dict(row.leakage_check_json or {}),
        }