from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

LLM_COMPLETION_ARCHIVE_ROOT = "llm-completions"
LLM_COMPLETION_ARCHIVE_ROLES = ("planner", "worker")
LLM_COMPLETION_ARCHIVE_FILE_PREVIEW_LIMIT: int | None = None

ArchiveRole = Literal["planner", "worker"]


def llm_completion_archive_dir(runtime_dir: Path, role: str) -> Path:
    return Path(runtime_dir) / LLM_COMPLETION_ARCHIVE_ROOT / role


def legacy_llm_completion_archive_dir(runtime_dir: Path, role: str) -> Path:
    return Path(runtime_dir) / f"{role}-llm-completions"


def summarize_llm_completion_archive(runtime_dir: Path | None, role: str) -> dict[str, Any]:
    if runtime_dir is None:
        return _empty_summary(role)

    runtime_dir = Path(runtime_dir)
    primary_dir = llm_completion_archive_dir(runtime_dir, role)
    legacy_dir = legacy_llm_completion_archive_dir(runtime_dir, role)
    archive_dirs = [primary_dir]
    if legacy_dir != primary_dir:
        archive_dirs.append(legacy_dir)

    files: list[Path] = []
    for archive_dir in archive_dirs:
        if archive_dir.exists():
            files.extend(path for path in archive_dir.rglob("*.json") if path.is_file())

    latest_modified_at: datetime | None = None
    total_bytes = 0
    file_payloads: list[dict[str, Any]] = []
    sorted_files = sorted(
        files,
        key=lambda item: (item.stat().st_mtime, str(item)),
        reverse=True,
    )
    for path in sorted_files:
        stat = path.stat()
        total_bytes += stat.st_size
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if latest_modified_at is None or modified_at > latest_modified_at:
            latest_modified_at = modified_at
        file_payloads.append(
            {
                "path": _relative_archive_path(path, runtime_dir),
                "size_bytes": stat.st_size,
                "modified_at": modified_at.isoformat(),
            }
        )

    return {
        "archive_type": "openhands_llm_completions",
        "role": role,
        "path": str(primary_dir),
        "legacy_path": str(legacy_dir),
        "exists": primary_dir.exists() or legacy_dir.exists(),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "latest_modified_at": latest_modified_at.isoformat() if latest_modified_at else None,
        "files": file_payloads,
        "preview_file_limit": LLM_COMPLETION_ARCHIVE_FILE_PREVIEW_LIMIT,
    }


def runtime_dir_from_workspace_path(workspace_path: str | None, *, run_id: str) -> Path | None:
    if not workspace_path:
        return None
    path = Path(workspace_path).expanduser()
    if path.name == run_id and path.parent.name == "runs":
        return path.parent.parent / "runtime" / run_id
    return None


def summarize_run_llm_completion_archives(
    *,
    workspace_path: str | None,
    run_id: str,
) -> dict[str, dict[str, Any]]:
    runtime_dir = runtime_dir_from_workspace_path(workspace_path, run_id=run_id)
    return {
        role: summarize_llm_completion_archive(runtime_dir, role)
        for role in LLM_COMPLETION_ARCHIVE_ROLES
    }


def _empty_summary(role: str) -> dict[str, Any]:
    return {
        "archive_type": "openhands_llm_completions",
        "role": role,
        "path": "",
        "legacy_path": "",
        "exists": False,
        "file_count": 0,
        "total_bytes": 0,
        "latest_modified_at": None,
        "files": [],
        "preview_file_limit": LLM_COMPLETION_ARCHIVE_FILE_PREVIEW_LIMIT,
    }


def _relative_archive_path(path: Path, runtime_dir: Path) -> str:
    try:
        return str(path.relative_to(runtime_dir))
    except ValueError:
        return str(path)
