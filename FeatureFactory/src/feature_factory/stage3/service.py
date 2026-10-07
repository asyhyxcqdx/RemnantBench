from __future__ import annotations

import json
import os
import re
import select as select_module
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

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
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage2TestResult,
    Stage3CommitSnapshot,
    Stage3CleanupTombstone,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunEvent,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage3SavepointFileResult,
)
from feature_factory.stage2.raw_archive import (
    legacy_llm_completion_archive_dir,
    llm_completion_archive_dir,
    runtime_dir_from_workspace_path,
    summarize_llm_completion_archive,
)
from feature_factory.stage3.evaluator import Stage3EvaluationError, Stage3Evaluator
from feature_factory.stage2.validator import Stage2ValidationError

_STAGE3_RUNTIME_SNAPSHOT_SECTION_KEYS = (
    "concurrency",
    "breaker",
    "hyperparameters",
)
_STAGE3_UNSET = object()
STAGE3_LLM_COMPLETION_ARCHIVE_ROLES = ("breaker",)
_STAGE3_ACTIVE_RUN_STATUSES = (
    Stage3RunStatus.pending.value,
    Stage3RunStatus.queued.value,
    Stage3RunStatus.running.value,
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
_STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES = 10
_STAGE3_STUB_LINE_RE = re.compile(
    r"^\s*(?:pass|return\s+(?:None|False|True|[\"']{2})|"
    r"raise\s+(?:AssertionError|NotImplementedError)\b|assert\s+False\b)\s*(?:#.*)?$",
    re.IGNORECASE,
)
_STAGE3_SABOTAGE_MARKER_RE = re.compile(
    r"\b(?:broken|sabotage|stubbed?|not\s+implemented|temporary\s+stub)\b",
    re.IGNORECASE,
)
_STAGE3_ADDED_COMMENT_BREADCRUMB_RE = re.compile(r"\b(?:TODO|FIXME|HACK)\b")
_STAGE3_QUALITY_NOISE_FILENAMES = {
    "build.gradle",
    "build.gradle.kts",
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "package-lock.json",
    "package.json",
    "pnpm-lock.yaml",
    "poetry.lock",
    "pom.xml",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "uv.lock",
    "yarn.lock",
}
# Keep this stricter than quality/diff-stat noise: lockfiles are ignored for
# scoring and can be skipped by rLLM apply fallback, but generated packaging
# metadata should never be archived as part of the reproducible gold patch.
_STAGE3_GOLD_PATCH_GENERATED_METADATA_EXCLUDED_GLOB_PATHS = (
    "*.egg-info",
    "*.egg-info/**",
    "**/*.egg-info",
    "**/*.egg-info/**",
    "*.dist-info",
    "*.dist-info/**",
    "**/*.dist-info",
    "**/*.dist-info/**",
    ".eggs",
    ".eggs/**",
    "**/.eggs",
    "**/.eggs/**",
    "__pycache__",
    "__pycache__/**",
    "**/__pycache__",
    "**/__pycache__/**",
)


def _target_selector_for(row: Any) -> str:
    if isinstance(row, dict):
        test_file_path = str(row.get("test_file_path") or row.get("path") or "").strip()
        return str(row.get("target_selector") or test_file_path).strip()
    test_file_path = str(getattr(row, "test_file_path", "") or "").strip()
    return str(getattr(row, "target_selector", None) or test_file_path).strip()


def _p2p_file_payload(path: str, target_selector: str | None = None, **extra: Any) -> dict[str, Any]:
    normalized_path = str(path or "").strip()
    normalized_selector = str(target_selector or normalized_path).strip()
    payload = {
        "path": normalized_path,
        "target_selector": normalized_selector,
    }
    payload.update(extra)
    return payload


def _normalize_p2p_file_payload(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        path = str(item.get("path") or item.get("test_file_path") or "").strip()
        target_selector = str(item.get("target_selector") or path).strip()
        payload = dict(item)
        payload["path"] = path
        payload["target_selector"] = target_selector or path
        return payload
    return _p2p_file_payload(str(item or "").strip())


def _p2p_file_evaluator_input(item: dict[str, Any]) -> str | dict[str, Any]:
    path = str(item.get("path") or "").strip()
    target_selector = str(item.get("target_selector") or path).strip()
    if not target_selector or target_selector == path:
        return path
    return dict(item)


def _stage3_diff_stats_from_patch_text(patch_text: str | None) -> dict[str, int]:
    stats = core_patch_diff_line_stats(patch_text)
    return {
        "total_changed_lines": stats.lines_changed,
        "added_lines": stats.lines_added,
        "removed_lines": stats.lines_deleted,
    }


def _stage3_comment_body_from_added_line(line: str, *, in_block_comment: bool) -> tuple[str | None, bool]:
    stripped = str(line or "").strip()
    if not stripped:
        return (None, in_block_comment)
    if in_block_comment:
        next_in_block = not ("*/" in stripped or "-->" in stripped)
        body = re.sub(r"(\*/|-->)\s*$", "", stripped).strip()
        if body.startswith("*"):
            body = body[1:].strip()
        return (body or None, next_in_block)
    for marker in ("#", "//", "--", ";"):
        if stripped.startswith(marker):
            return (stripped[len(marker) :].strip() or None, False)
    if stripped.startswith("/*"):
        body = stripped[2:].strip()
        next_in_block = "*/" not in body
        body = re.sub(r"\*/\s*$", "", body).strip()
        return (body or None, next_in_block)
    if stripped.startswith("<!--"):
        body = stripped[4:].strip()
        next_in_block = "-->" not in body
        body = re.sub(r"-->\s*$", "", body).strip()
        return (body or None, next_in_block)
    return (None, False)


def _stage3_normalized_code_for_comment_detection(line: str) -> str:
    return re.sub(r"\s+", "", str(line or "").strip()).lower()


def _stage3_removed_code_candidate(line: str) -> str | None:
    stripped = str(line or "").strip()
    if not stripped:
        return None
    comment_body, _in_block = _stage3_comment_body_from_added_line(stripped, in_block_comment=False)
    if comment_body is not None:
        return None
    normalized = _stage3_normalized_code_for_comment_detection(stripped)
    if len(normalized) < 6:
        return None
    return stripped


def _stage3_patch_header_path(line: str) -> str:
    value = str(line or "")[4:].strip().split("\t", 1)[0]
    if value == "/dev/null":
        return ""
    if value.startswith("b/"):
        return value[2:]
    return value


def _stage3_patch_diff_path(line: str) -> str:
    parts = str(line or "").strip().split()
    if len(parts) >= 4 and parts[3].startswith("b/"):
        return parts[3][2:]
    return ""


def _stage3_patch_path_parts(path: str) -> tuple[str, list[str], str]:
    normalized = str(path or "").replace("\\", "/").strip("/")
    parts = [part.lower() for part in normalized.split("/") if part]
    filename = parts[-1] if parts else ""
    return normalized, parts, filename


def stage3_gold_patch_diff_pathspec_args() -> list[str]:
    return [
        "--",
        ".",
        *[
            f":(exclude,glob){pathspec}"
            for pathspec in _STAGE3_GOLD_PATCH_GENERATED_METADATA_EXCLUDED_GLOB_PATHS
        ],
    ]


def _stage3_patch_path_rejection(path: str) -> dict[str, Any] | None:
    normalized, parts, filename = _stage3_patch_path_parts(path)
    if not normalized:
        return None
    if filename in {"run_script.sh", "runner.sh"}:
        return {
            "code": "FORBIDDEN_PATCH_PATH",
            "message": "The gold patch modifies a test runner path. Break implementation code only.",
            "file_path": normalized,
        }
    if any(part in {"test", "tests", "t", "fixture", "fixtures", "testdata"} for part in parts[:-1]):
        return {
            "code": "FORBIDDEN_PATCH_PATH",
            "message": "The gold patch modifies a test, fixture, or test-data path. Break implementation code only.",
            "file_path": normalized,
        }
    if filename.startswith("test_") or filename.endswith("_test.py"):
        return {
            "code": "FORBIDDEN_PATCH_PATH",
            "message": "The gold patch modifies a test file. Break implementation code only.",
            "file_path": normalized,
        }
    return None


def _stage3_patch_path_is_quality_noise(path: str) -> bool:
    _normalized, parts, filename = _stage3_patch_path_parts(path)
    if filename in _STAGE3_QUALITY_NOISE_FILENAMES:
        return True
    if any(
        part == ".eggs" or part.endswith(".egg-info") or part.endswith(".dist-info")
        for part in parts
    ):
        return True
    return any(
        part in {
            ".cache",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "__pycache__",
            "build",
            "coverage",
            "dist",
            "node_modules",
        }
        for part in parts[:-1]
    )


def _stage3_patch_code_line_candidate(line: str) -> str | None:
    stripped = str(line or "").strip()
    if not stripped:
        return None
    comment_body, _in_block = _stage3_comment_body_from_added_line(
        stripped,
        in_block_comment=False,
    )
    if comment_body is not None:
        return None
    if re.fullmatch(r"[{}\[\](),.;:]+", stripped):
        return None
    return stripped


def _stage3_patch_quality_findings(
    patch_text: str | None,
    *,
    min_removed_code_lines: int = _STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    current_path = ""
    hunk_header = ""
    removed_code_line_count = 0
    forbidden_paths: set[str] = set()
    current_path_is_quality_noise = False
    resolved_min_removed_code_lines = _stage3_min_removed_code_lines(
        min_removed_code_lines,
        default=_STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES,
    )

    for raw_line in str(patch_text or "").splitlines():
        if raw_line.startswith("diff --git "):
            hunk_header = ""
            current_path = _stage3_patch_diff_path(raw_line)
            current_path_is_quality_noise = _stage3_patch_path_is_quality_noise(current_path)
            path_rejection = _stage3_patch_path_rejection(current_path)
            if path_rejection is not None and current_path not in forbidden_paths:
                forbidden_paths.add(current_path)
                findings.append(path_rejection)
            continue
        if raw_line.startswith("+++ "):
            next_path = _stage3_patch_header_path(raw_line)
            if next_path:
                current_path = next_path
                current_path_is_quality_noise = _stage3_patch_path_is_quality_noise(current_path)
            path_rejection = _stage3_patch_path_rejection(current_path)
            if path_rejection is not None and current_path not in forbidden_paths:
                forbidden_paths.add(current_path)
                findings.append(path_rejection)
            continue
        if raw_line.startswith("@@"):
            hunk_header = raw_line
            continue
        if raw_line.startswith("GIT binary patch") or raw_line.startswith("Binary files "):
            continue
        if raw_line.startswith("-") and not raw_line.startswith("--- "):
            if (
                not current_path_is_quality_noise
                and _stage3_patch_code_line_candidate(raw_line[1:]) is not None
            ):
                removed_code_line_count += 1
            continue
        if not raw_line.startswith("+") or raw_line.startswith("+++ "):
            continue
        if current_path_is_quality_noise:
            continue

        added_line = raw_line[1:]
        stripped = added_line.strip()
        if not stripped:
            continue
        if _STAGE3_STUB_LINE_RE.match(added_line):
            findings.append(
                {
                    "code": "TRIVIAL_STUB_BREAKAGE",
                    "message": (
                        "The gold patch adds an obvious stub-style breakage. Delete or rewrite "
                        "a complete capability path instead of replacing logic with pass, empty "
                        "returns, or assertion-only failures."
                    ),
                    "file_path": current_path,
                    "hunk": hunk_header,
                    "line": stripped,
                }
            )
            continue
        comment_body, _in_block = _stage3_comment_body_from_added_line(
            added_line,
            in_block_comment=False,
        )
        if _STAGE3_SABOTAGE_MARKER_RE.search(stripped) or (
            comment_body is not None and _STAGE3_ADDED_COMMENT_BREADCRUMB_RE.search(comment_body)
        ):
            findings.append(
                {
                    "code": "SABOTAGE_BREADCRUMB",
                    "message": (
                        "The gold patch adds an obvious sabotage marker or repair clue. "
                        "Do not leave grepable answer breadcrumbs."
                    ),
                    "file_path": current_path,
                    "hunk": hunk_header,
                    "line": stripped,
                }
            )

    if removed_code_line_count < resolved_min_removed_code_lines:
        findings.append(
            {
                "code": "INSUFFICIENT_SUBSTANTIAL_DELETION",
                "message": (
                    "The gold patch removes too little substantive implementation code. "
                    "Delete or rewrite a complete capability path instead of making a tiny edit."
                ),
                "removed_code_line_count": removed_code_line_count,
                "minimum_removed_code_line_count": resolved_min_removed_code_lines,
            }
        )
    return findings


def _stage3_patch_rejection_feedback(
    patch_text: str | None,
    *,
    base_feedback: dict[str, Any] | None = None,
    min_removed_code_lines: int = _STAGE3_DEFAULT_MIN_REMOVED_CODE_LINES,
) -> dict[str, Any] | None:
    feedback = dict(base_feedback or {})
    normalized_patch_text = str(patch_text or "")
    if not normalized_patch_text.strip():
        return {
            **feedback,
            "accepted": False,
            "code": "EMPTY_DIFF",
            "message": (
                "The current workspace has no effective diff against the baseline, "
                "so no Stage3 savepoint can be archived."
            ),
        }

    commented_out_findings = _stage3_commented_out_code_findings(normalized_patch_text)
    if commented_out_findings:
        return {
            **feedback,
            "accepted": False,
            "code": "COMMENTED_OUT_CODE",
            "message": (
                "The gold patch appears to keep original implementation code as comments. "
                "Delete or rewrite the code instead of leaving repair breadcrumbs."
            ),
            "commented_out_code_findings": commented_out_findings[:5],
            "commented_out_code_finding_count": len(commented_out_findings),
        }

    patch_quality_findings = _stage3_patch_quality_findings(
        normalized_patch_text,
        min_removed_code_lines=min_removed_code_lines,
    )
    if patch_quality_findings:
        first_finding = dict(patch_quality_findings[0])
        return {
            **feedback,
            "accepted": False,
            "code": str(first_finding.get("code") or "PATCH_QUALITY_REJECTED"),
            "message": str(
                first_finding.get("message")
                or (
                    "The gold patch does not satisfy Stage3 breakage quality requirements. "
                    "Delete or rewrite a complete capability path."
                )
            ),
            "patch_quality_findings": patch_quality_findings[:8],
            "patch_quality_finding_count": len(patch_quality_findings),
        }

    return None


def _stage3_commented_out_code_findings(patch_text: str | None) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    current_path = ""
    hunk_header = ""
    removed_lines: list[str] = []
    added_comment_lines: list[str] = []
    in_added_block_comment = False

    def flush_hunk() -> None:
        nonlocal removed_lines, added_comment_lines
        if not removed_lines or not added_comment_lines:
            removed_lines = []
            added_comment_lines = []
            return
        removed_candidates = [
            candidate
            for line in removed_lines
            if (candidate := _stage3_removed_code_candidate(line)) is not None
        ]
        for comment in added_comment_lines:
            comment_norm = _stage3_normalized_code_for_comment_detection(comment)
            if len(comment_norm) < 6:
                continue
            for removed in removed_candidates:
                removed_norm = _stage3_normalized_code_for_comment_detection(removed)
                if removed_norm in comment_norm or comment_norm in removed_norm:
                    findings.append(
                        {
                            "file_path": current_path,
                            "hunk": hunk_header,
                            "removed_line": removed,
                            "comment_line": comment,
                        }
                    )
                    break
        removed_lines = []
        added_comment_lines = []

    for raw_line in str(patch_text or "").splitlines():
        if raw_line.startswith("diff --git "):
            flush_hunk()
            hunk_header = ""
            in_added_block_comment = False
            continue
        if raw_line.startswith("+++ "):
            current_path = _stage3_patch_header_path(raw_line)
            continue
        if raw_line.startswith("@@"):
            flush_hunk()
            hunk_header = raw_line
            in_added_block_comment = False
            continue
        if not hunk_header:
            continue
        if raw_line.startswith("-") and not raw_line.startswith("--- "):
            removed_lines.append(raw_line[1:])
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++ "):
            comment_body, in_added_block_comment = _stage3_comment_body_from_added_line(
                raw_line[1:],
                in_block_comment=in_added_block_comment,
            )
            if comment_body:
                added_comment_lines.append(comment_body)
            continue
        if in_added_block_comment and raw_line.startswith(" "):
            in_added_block_comment = False
    flush_hunk()
    return findings


def _stage3_entry_pass_rate_ceiling(raw_value: Any, *, default: float) -> float:
    try:
        parsed = float(raw_value)
    except (TypeError, ValueError):
        return float(default)
    if not parsed == parsed or parsed < 0.0 or parsed > 1.0:
        return float(default)
    return parsed


def _stage3_min_removed_code_lines(raw_value: Any, *, default: int) -> int:
    if isinstance(raw_value, bool):
        return int(default)
    try:
        parsed = int(raw_value)
    except (TypeError, ValueError, OverflowError):
        return int(default)
    if parsed < 0:
        return int(default)
    return parsed


def _copy_stage3_llm_completion_archives_for_resume_run(
    *,
    source_run: Stage3Run,
    stage3_root: Path,
    destination_run_id: str,
) -> None:
    source_runtime_dir = runtime_dir_from_workspace_path(source_run.workspace_path, run_id=source_run.id)
    if source_runtime_dir is None:
        source_runtime_dir = stage3_root / "runtime" / str(source_run.id)
    source_runtime_dir = source_runtime_dir.expanduser().resolve()
    if not source_runtime_dir.exists():
        return

    destination_runtime_dir = (stage3_root / "runtime" / destination_run_id).expanduser().resolve()
    for role in STAGE3_LLM_COMPLETION_ARCHIVE_ROLES:
        destination_archive_dir = llm_completion_archive_dir(destination_runtime_dir, role)
        destination_archive_dir.mkdir(parents=True, exist_ok=True)
        copied_relative_paths: set[str] = set()
        for source_archive_dir in (
            llm_completion_archive_dir(source_runtime_dir, role),
            legacy_llm_completion_archive_dir(source_runtime_dir, role),
        ):
            if not source_archive_dir.exists():
                continue
            for source_path in sorted(source_archive_dir.rglob("*.json")):
                if not source_path.is_file():
                    continue
                relative_path = source_path.relative_to(source_archive_dir)
                relative_key = str(relative_path)
                if relative_key in copied_relative_paths:
                    continue
                copied_relative_paths.add(relative_key)
                destination_path = destination_archive_dir / relative_path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_path, destination_path)


def _checkpoint_image_ref_for_run(*, run_id: str, depth: int) -> str:
    safe_run_id = re.sub(r"[^a-z0-9_.-]+", "-", str(run_id or "").lower()).strip("-")
    return f"feature-factory/stage3-breaker-checkpoint:{safe_run_id}-depth-{int(depth or 0):03d}"


def _checkpoint_payload_identity(checkpoint: dict[str, Any] | None) -> tuple[str, str, str, str, int]:
    payload = dict(checkpoint or {})
    return (
        str(payload.get("checkpoint_dir") or "").strip(),
        str(payload.get("workspace_snapshot_path") or "").strip(),
        str(payload.get("docker_image_ref") or "").strip(),
        str(payload.get("conversation_id") or "").strip(),
        int(payload.get("depth") or 0),
    )


def _docker_image_exists(image_ref: str) -> bool:
    normalized_ref = str(image_ref or "").strip()
    if not normalized_ref:
        return False
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", normalized_ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except Exception:
        return False
    return completed.returncode == 0


def _retag_checkpoint_image(*, source_image_ref: str, destination_run_id: str, depth: int) -> str:
    source = str(source_image_ref or "").strip()
    if not source:
        raise RuntimeError("resume checkpoint is missing docker_image_ref")
    destination = _checkpoint_image_ref_for_run(run_id=destination_run_id, depth=depth)
    if source == destination:
        return destination
    completed = subprocess.run(
        ["docker", "tag", source, destination],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip() or "docker tag failed"
        raise RuntimeError(
            f"failed to clone breaker checkpoint image {source} -> {destination}: {error}"
        )
    return destination


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
        if normalized_root is not None and (resolved == normalized_root or normalized_root not in resolved.parents):
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


def _cleanup_result_has_failures(cleanup_result: dict[str, Any]) -> bool:
    return any(
        isinstance(value, list) and len(value) > 0
        for value in dict(cleanup_result or {}).values()
    )


def _chmod_container_writable(path: Path) -> None:
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        if path.is_dir():
            desired_mode = current_mode | stat.S_IRWXU | stat.S_IRWXG | stat.S_IRWXO
        else:
            desired_mode = (
                current_mode
                | stat.S_IRUSR
                | stat.S_IWUSR
                | stat.S_IRGRP
                | stat.S_IWGRP
                | stat.S_IROTH
                | stat.S_IWOTH
            )
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


def _make_container_writable(path: Path, *, recursive: bool = False) -> None:
    if not path.exists():
        return
    _chmod_container_writable(path)
    if not recursive:
        return
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        _chmod_container_writable(child)


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
    return any(marker.lower() in message.lower() for marker in _GIT_RETRYABLE_ERROR_MARKERS)


def _raise_if_cancel_requested(cancel_requested: Callable[[], bool] | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise Stage3RunConflictError("stage3 run interrupted")


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


class Stage3RunConflictError(RuntimeError):
    pass


class Stage3SavepointRejectedError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        feedback: dict[str, Any] | None = None,
        collateral: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.feedback = dict(feedback or {})
        self.collateral = dict(collateral or {})


def extract_stage3_runtime_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    extracted: dict[str, Any] = {}
    for key in _STAGE3_RUNTIME_SNAPSHOT_SECTION_KEYS:
        value = snapshot.get(key)
        if isinstance(value, dict):
            extracted[key] = dict(value)
    return extracted


def merge_stage3_runtime_snapshot(
    existing_snapshot: dict[str, Any] | None,
    updated_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing_snapshot or {})
    for key, value in dict(updated_snapshot or {}).items():
        if key in _STAGE3_RUNTIME_SNAPSHOT_SECTION_KEYS and isinstance(value, dict):
            merged[key] = {
                **dict(merged.get(key) or {}),
                **dict(value),
            }
            continue
        merged[key] = value
    return merged


def redact_stage3_runtime_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    redacted = dict(snapshot or {})
    breaker = dict(redacted.get("breaker") or {})
    api_key = str(breaker.get("api_key") or "")
    api_key_preview = str(breaker.get("api_key_preview") or "")
    if api_key and not api_key_preview:
        api_key_preview = f"{api_key[:6]}..."
    breaker.pop("api_key", None)
    if api_key_preview:
        breaker["api_key_preview"] = api_key_preview
    if breaker:
        redacted["breaker"] = breaker
    return redacted


def _safe_extract_tar(archive: tarfile.TarFile, target_dir: Path) -> None:
    target_dir = target_dir.resolve()
    for member in archive.getmembers():
        member_path = (target_dir / member.name).resolve()
        member_path.relative_to(target_dir)
    archive.extractall(target_dir, filter="data")


class Stage3Service:
    def __init__(
        self,
        session: Session,
        *,
        workspace_root: Path | str = Path("./data/stage3"),
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.settings = settings or get_settings()
        self.evaluator = None
        self._docker_image_exists_cache: dict[str, bool] = {}

    def _repository_materializer(self) -> HostRepositoryMaterializer:
        return HostRepositoryMaterializer(
            self.settings,
            emit_or_append_run_event=self._emit_or_append_run_event,
            run_checked_command=self._run_checked_command,
            run_command_with_git_progress=self._run_command_with_git_progress,
            run_git_remote_command_with_progress=self._run_git_remote_command_with_progress,
            raise_if_cancel_requested=_raise_if_cancel_requested,
        )

    def get_repository(self, repository_id: int) -> GitHubRepository:
        repository = self.session.get(GitHubRepository, repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {repository_id}")
        return repository

    def get_entry_file(self, entry_file_id: str, *, include_runs: bool = False) -> Stage3EntryFile:
        stmt = select(Stage3EntryFile).where(Stage3EntryFile.id == entry_file_id)
        if include_runs:
            stmt = stmt.options(
                selectinload(Stage3EntryFile.snapshot),
                selectinload(Stage3EntryFile.runs).selectinload(Stage3Run.events),
                selectinload(Stage3EntryFile.runs)
                .selectinload(Stage3Run.savepoints)
                .selectinload(Stage3Savepoint.file_results),
            )
        else:
            stmt = stmt.options(selectinload(Stage3EntryFile.snapshot))
        entry_file = self.session.scalar(stmt)
        if entry_file is None:
            raise ValueError(f"stage3 entry file not found: {entry_file_id}")
        return entry_file

    def get_repository_entry_file(
        self,
        repository_id: int,
        entry_file_id: str,
        *,
        include_runs: bool = False,
    ) -> Stage3EntryFile:
        entry_file = self.get_entry_file(entry_file_id, include_runs=include_runs)
        if entry_file.snapshot.repository_id != repository_id:
            raise ValueError(f"stage3 entry file {entry_file_id} does not belong to repository {repository_id}")
        return entry_file

    def get_run(self, run_id: str, *, include_detail: bool = True) -> Stage3Run:
        stmt = select(Stage3Run).where(Stage3Run.id == run_id)
        if include_detail:
            stmt = stmt.options(
                selectinload(Stage3Run.events),
                selectinload(Stage3Run.savepoints).selectinload(Stage3Savepoint.file_results),
                selectinload(Stage3Run.entry_file).selectinload(Stage3EntryFile.snapshot),
            )
        else:
            stmt = stmt.options(selectinload(Stage3Run.entry_file).selectinload(Stage3EntryFile.snapshot))
        run = self.session.scalar(stmt)
        if run is None:
            raise ValueError(f"stage3 run not found: {run_id}")
        return run

    def get_repository_run(self, repository_id: int, run_id: str, *, include_detail: bool = True) -> Stage3Run:
        run = self.get_run(run_id, include_detail=include_detail)
        if run.entry_file.snapshot.repository_id != repository_id:
            raise ValueError(f"stage3 run {run_id} does not belong to repository {repository_id}")
        return run

    def get_repository_snapshot(
        self,
        repository_id: int,
        snapshot_id: str | None = None,
    ) -> Stage3CommitSnapshot:
        self.get_repository(repository_id)
        self.ensure_materialized(repository_ids=[repository_id])
        snapshots = list(
            self.session.scalars(
                select(Stage3CommitSnapshot)
                .options(selectinload(Stage3CommitSnapshot.entry_files))
                .where(Stage3CommitSnapshot.repository_id == repository_id)
            )
        )
        snapshots.sort(key=self._snapshot_sort_key, reverse=True)
        if not snapshots:
            raise ValueError(f"repository has no stage3 commit snapshots: {repository_id}")
        if snapshot_id is None:
            return snapshots[0]
        selected_snapshot = next(
            (snapshot for snapshot in snapshots if str(snapshot.id) == str(snapshot_id)),
            None,
        )
        if selected_snapshot is None:
            raise ValueError(f"stage3 commit snapshot not found for repository {repository_id}: {snapshot_id}")
        return selected_snapshot

    def prepare_snapshot_workspace(
        self,
        repository_id: int,
        *,
        snapshot_id: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.get_repository_snapshot(repository_id, snapshot_id=snapshot_id)
        workspace_dir = self._snapshot_workspace_dir(snapshot.id)
        return self._prepare_snapshot_workspace(workspace_dir=workspace_dir, snapshot=snapshot)

    def _latest_snapshots_per_commit(
        self,
        snapshots: list[Stage3CommitSnapshot],
    ) -> list[Stage3CommitSnapshot]:
        latest_by_commit: dict[str, Stage3CommitSnapshot] = {}
        ordered = sorted(snapshots, key=self._snapshot_sort_key, reverse=True)
        for snapshot in ordered:
            commit_sha = str(snapshot.source_commit_sha or "").strip()
            key = commit_sha or f"snapshot:{snapshot.id}"
            latest_by_commit.setdefault(key, snapshot)
        return list(latest_by_commit.values())

    def _resolve_latest_entry_file_for_new_run(
        self,
        entry_file: Stage3EntryFile,
        *,
        include_runs: bool,
    ) -> Stage3EntryFile:
        snapshot = entry_file.snapshot
        commit_sha = str(snapshot.source_commit_sha or "").strip()
        latest_snapshots = list(
            self.session.scalars(
                select(Stage3CommitSnapshot)
                .options(selectinload(Stage3CommitSnapshot.entry_files))
                .where(
                    Stage3CommitSnapshot.repository_id == snapshot.repository_id,
                    Stage3CommitSnapshot.source_commit_sha == commit_sha,
                )
            )
        )
        latest_snapshots.sort(key=self._snapshot_sort_key, reverse=True)
        latest_snapshot = latest_snapshots[0] if latest_snapshots else None
        if latest_snapshot is None or str(latest_snapshot.id) == str(snapshot.id):
            return entry_file

        latest_entry = next(
            (
                row
                for row in list(latest_snapshot.entry_files or [])
                if str(row.test_file_path or "") == str(entry_file.test_file_path or "")
            ),
            None,
        )
        if latest_entry is None:
            raise Stage3RunConflictError(
                "this entry file is not present in the latest Stage2 artifacts; reload the repository detail first"
            )
        return self.get_repository_entry_file(
            snapshot.repository_id,
            latest_entry.id,
            include_runs=include_runs,
        )

    def stage3_ready_repository_ids_stmt(self) -> Any:
        eligible_stage2_repository_ids = (
            select(Stage2Run.repository_id)
            .join(Stage2TestResult, Stage2TestResult.run_id == Stage2Run.id)
            .where(
                Stage2Run.status == Stage2RunStatus.completed.value,
                Stage2Run.result == Stage2RunResult.passed.value,
                Stage2Run.target_commit_sha.is_not(None),
                Stage2TestResult.status == "passed",
            )
            .group_by(Stage2Run.repository_id)
        )
        materialized_repository_ids = select(Stage3CommitSnapshot.repository_id).group_by(
            Stage3CommitSnapshot.repository_id
        )
        return eligible_stage2_repository_ids.union(materialized_repository_ids).subquery()

    def ensure_materialized(self, *, repository_ids: list[int] | None = None) -> None:
        stage2_runs = list(self._eligible_stage2_runs(repository_ids=repository_ids))
        if not stage2_runs:
            return

        latest_runs_by_commit: dict[tuple[int, str], Stage2Run] = {}
        for run in stage2_runs:
            commit_sha = str(run.target_commit_sha or "").strip()
            if not commit_sha:
                continue
            if not self._passed_test_results_for_run(run):
                continue
            latest_runs_by_commit.setdefault((run.repository_id, commit_sha), run)

        if not latest_runs_by_commit:
            return

        existing_stmt = select(Stage3CommitSnapshot).options(selectinload(Stage3CommitSnapshot.entry_files))
        if repository_ids:
            existing_stmt = existing_stmt.where(Stage3CommitSnapshot.repository_id.in_(repository_ids))
        existing_snapshots = {
            str(snapshot.source_stage2_run_id): snapshot
            for snapshot in self.session.scalars(existing_stmt)
        }

        dirty = False
        for run in latest_runs_by_commit.values():
            snapshot = existing_snapshots.get(str(run.id))
            changed = self._upsert_snapshot_from_stage2_run(snapshot=snapshot, run=run)
            dirty = dirty or changed

        if dirty:
            try:
                self.session.flush()
            except IntegrityError:
                self.session.rollback()
                raise

    def list_repository_stage3_rows(self, repositories: list[GitHubRepository]) -> list[dict[str, Any]]:
        if not repositories:
            return []
        repo_ids = [repository.id for repository in repositories]
        snapshots = list(
            self.session.scalars(
                select(Stage3CommitSnapshot)
                .options(selectinload(Stage3CommitSnapshot.entry_files))
                .where(Stage3CommitSnapshot.repository_id.in_(repo_ids))
            )
        )
        snapshots_by_repo: dict[int, list[Stage3CommitSnapshot]] = {}
        for snapshot in snapshots:
            snapshots_by_repo.setdefault(snapshot.repository_id, []).append(snapshot)

        repo_run_stats: dict[int, dict[str, Any]] = {
            repository_id: {
                "pending_count": 0,
                "queued_count": 0,
                "running_count": 0,
                "succeeded_count": 0,
                "failed_count": 0,
                "interrupted_count": 0,
                "latest_operation_at": None,
                "latest_run_id": "",
            }
            for repository_id in repo_ids
        }
        run_count_by_repo: dict[int, int] = {repository_id: 0 for repository_id in repo_ids}
        savepoint_keys_by_repo: dict[int, set[str]] = {repository_id: set() for repository_id in repo_ids}
        produced_entry_file_keys_by_repo: dict[int, set[tuple[str, str]]] = {repository_id: set() for repository_id in repo_ids}
        stage3_runs = list(
            self.session.scalars(
                select(Stage3Run)
                .options(
                    selectinload(Stage3Run.savepoints),
                    selectinload(Stage3Run.entry_file).selectinload(Stage3EntryFile.snapshot),
                )
                .join(Stage3EntryFile, Stage3Run.entry_file_id == Stage3EntryFile.id)
                .join(Stage3CommitSnapshot, Stage3EntryFile.snapshot_id == Stage3CommitSnapshot.id)
                .where(Stage3CommitSnapshot.repository_id.in_(repo_ids))
            )
        )
        for run in stage3_runs:
            entry_file = run.entry_file
            snapshot = entry_file.snapshot if entry_file is not None else None
            if snapshot is None:
                continue
            repository_id = int(snapshot.repository_id)
            run_id = str(run.id or "")
            status = str(run.status or "")
            result = str(run.result or "")
            updated_at = run.updated_at
            effective_savepoint_keys = self._effective_savepoint_identity_keys(run)
            run_count_by_repo[repository_id] = run_count_by_repo.get(repository_id, 0) + 1
            if effective_savepoint_keys:
                produced_entry_file_keys_by_repo.setdefault(repository_id, set()).add(
                    (
                        str(snapshot.source_commit_sha or ""),
                        str(entry_file.test_file_path or ""),
                    )
                )
                savepoint_keys_by_repo.setdefault(repository_id, set()).update(
                    f"{entry_file.id}:{key}" for key in effective_savepoint_keys
                )
            stats = repo_run_stats.setdefault(
                repository_id,
                {
                    "pending_count": 0,
                    "queued_count": 0,
                    "running_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "interrupted_count": 0,
                    "latest_operation_at": None,
                    "latest_run_id": "",
                },
            )
            normalized_status_key = self._repository_row_run_status_key(
                status=str(status or ""),
                result=str(result or ""),
                savepoint_count=len(effective_savepoint_keys),
            )
            if normalized_status_key == "pending":
                stats["pending_count"] = int(stats.get("pending_count") or 0) + 1
            elif normalized_status_key == "queued":
                stats["queued_count"] = int(stats.get("queued_count") or 0) + 1
            elif normalized_status_key == "running":
                stats["running_count"] = int(stats.get("running_count") or 0) + 1
            elif normalized_status_key == "succeeded":
                stats["succeeded_count"] = int(stats.get("succeeded_count") or 0) + 1
            elif normalized_status_key == "failed":
                stats["failed_count"] = int(stats.get("failed_count") or 0) + 1
            elif normalized_status_key == "interrupted":
                stats["interrupted_count"] = int(stats.get("interrupted_count") or 0) + 1
            latest_operation_at = stats.get("latest_operation_at")
            normalized_updated_at = updated_at if isinstance(updated_at, datetime) else None
            if (
                normalized_updated_at is not None
                and (
                    latest_operation_at is None
                    or normalized_updated_at > latest_operation_at
                    or (
                        normalized_updated_at == latest_operation_at
                        and str(run_id or "") > str(stats.get("latest_run_id") or "")
                    )
                )
            ):
                stats["latest_operation_at"] = normalized_updated_at
                stats["latest_run_id"] = str(run_id or "")

        payloads: list[dict[str, Any]] = []
        for repository in repositories:
            repo_snapshots_all = sorted(
                snapshots_by_repo.get(repository.id, []),
                key=self._snapshot_sort_key,
                reverse=True,
            )
            repo_snapshots = self._latest_snapshots_per_commit(repo_snapshots_all)
            current_entry_keys = {
                (
                    str(snapshot.source_commit_sha or ""),
                    str(entry_file.test_file_path or ""),
                )
                for snapshot in repo_snapshots
                for entry_file in list(snapshot.entry_files or [])
            }
            entry_file_count = len(current_entry_keys)
            latest_snapshot = repo_snapshots_all[0] if repo_snapshots_all else None
            produced_entry_file_count = len(
                produced_entry_file_keys_by_repo.get(repository.id, set()) & current_entry_keys
            )
            produced_data_count = len(savepoint_keys_by_repo.get(repository.id, set()))
            latest_run_stats = repo_run_stats.get(repository.id, {})
            latest_operation_at = latest_run_stats.get("latest_operation_at")
            repo_status = self._repository_row_status(latest_run_stats)
            payloads.append(
                {
                    "id": repository.id,
                    "github_repo_id": repository.github_repo_id,
                    "full_name": repository.full_name,
                    "primary_language": repository.primary_language,
                    "stargazers_count": repository.stargazers_count,
                    "html_url": repository.html_url,
                    "default_branch": repository.default_branch,
                    "created_at_github": serialize_datetime(repository.created_at_github),
                    "pushed_at_github": serialize_datetime(repository.pushed_at_github),
                    "discovered_at": serialize_datetime(repository.discovered_at),
                    "stage3": {
                        "eligible_commit_count": len(repo_snapshots),
                        "entry_file_count": entry_file_count,
                        "produced_entry_file_count": produced_entry_file_count,
                        "produced_entry_file_ratio": (
                            float(produced_entry_file_count) / float(entry_file_count)
                            if entry_file_count > 0
                            else 0.0
                        ),
                        "produced_data_count": produced_data_count,
                        "run_count": run_count_by_repo.get(repository.id, 0),
                        "savepoint_count": produced_data_count,
                        "completed_entry_file_count": produced_entry_file_count,
                        "latest_operation_at": serialize_datetime(latest_operation_at),
                        "status": repo_status,
                        "latest_snapshot": self.serialize_commit_snapshot_summary(latest_snapshot)
                        if latest_snapshot is not None
                        else None,
                    },
                }
            )
        return payloads

    def _repository_row_run_status_key(self, *, status: str, result: str, savepoint_count: int) -> str:
        normalized_status = str(status or "")
        if normalized_status == Stage3RunStatus.pending.value:
            return "pending"
        if normalized_status == Stage3RunStatus.queued.value:
            return "queued"
        if normalized_status == Stage3RunStatus.running.value:
            return "running"
        if str(result or "") == Stage3RunResult.archived.value and int(savepoint_count or 0) > 0:
            return "succeeded"
        if str(result or "") == Stage3RunResult.interrupted.value:
            return "interrupted"
        return "failed"

    def repository_detail_payload(
        self,
        repository_id: int,
        *,
        selected_snapshot_id: str | None = None,
        selected_entry_file_id: str | None = None,
        selected_run_id: str | None = None,
    ) -> dict[str, Any]:
        repository = self.get_repository(repository_id)

        self.ensure_materialized(repository_ids=[repository_id])
        snapshots = list(
            self.session.scalars(
                select(Stage3CommitSnapshot)
                .options(selectinload(Stage3CommitSnapshot.entry_files))
                .where(Stage3CommitSnapshot.repository_id == repository_id)
            )
        )
        snapshots.sort(key=self._snapshot_sort_key, reverse=True)
        if not snapshots:
            raise ValueError(f"repository has no stage3 commit snapshots: {repository_id}")

        if selected_run_id is not None:
            try:
                selected_run_hint = self.get_repository_run(repository_id, selected_run_id, include_detail=False)
            except ValueError:
                selected_run_hint = None
            if selected_run_hint is not None:
                selected_snapshot_id = str(selected_run_hint.entry_file.snapshot_id)
                selected_entry_file_id = str(selected_run_hint.entry_file_id)
        elif selected_entry_file_id is not None and selected_snapshot_id is None:
            try:
                selected_entry_hint = self.get_repository_entry_file(
                    repository_id,
                    selected_entry_file_id,
                    include_runs=False,
                )
            except ValueError:
                selected_entry_hint = None
            if selected_entry_hint is not None:
                selected_snapshot_id = str(selected_entry_hint.snapshot_id)

        selected_snapshot = next(
            (snapshot for snapshot in snapshots if str(snapshot.id) == str(selected_snapshot_id)),
            None,
        )
        if selected_snapshot is None:
            selected_snapshot = snapshots[0]
        visible_snapshots = self._latest_snapshots_per_commit(snapshots)
        if all(str(snapshot.id) != str(selected_snapshot.id) for snapshot in visible_snapshots):
            visible_snapshots.append(selected_snapshot)
            visible_snapshots.sort(key=self._snapshot_sort_key, reverse=True)

        entry_files = list(selected_snapshot.entry_files or [])
        entry_files.sort(key=lambda row: str(row.test_file_path or ""))
        entry_file_ids = [entry_file.id for entry_file in entry_files]
        run_count_by_entry = self._run_count_by_entry_file(entry_file_ids)
        savepoint_count_by_entry = self._savepoint_count_by_entry_file(entry_file_ids)
        status_by_entry = self._entry_file_status_by_entry_file(entry_file_ids)
        latest_operation_by_entry = self._entry_file_latest_operation_by_entry_file(entry_file_ids)

        selected_entry_file = next(
            (entry_file for entry_file in entry_files if str(entry_file.id) == str(selected_entry_file_id)),
            None,
        )
        if selected_entry_file is None and entry_files:
            selected_entry_file = entry_files[0]

        runs: list[Stage3Run] = []
        if selected_entry_file is not None:
            runs = list(
                self.session.scalars(
                    select(Stage3Run)
                    .options(
                        selectinload(Stage3Run.events),
                        selectinload(Stage3Run.savepoints).selectinload(Stage3Savepoint.file_results),
                    )
                    .where(Stage3Run.entry_file_id == selected_entry_file.id)
                    .order_by(Stage3Run.created_at.desc(), Stage3Run.id.desc())
                )
            )
        selected_run = next(
            (run for run in runs if str(run.id) == str(selected_run_id)),
            None,
        )
        if selected_run is None and runs:
            selected_run = runs[0]

        repository_summary = self.list_repository_stage3_rows([repository])[0]
        return {
            "repository": repository_summary,
            "commit_snapshots": [self.serialize_commit_snapshot_summary(snapshot) for snapshot in visible_snapshots],
            "selected_snapshot": self.serialize_commit_snapshot_detail(selected_snapshot),
            "entry_files": [
                self.serialize_entry_file_summary(
                    entry_file,
                    run_count=run_count_by_entry.get(entry_file.id, 0),
                    savepoint_count=savepoint_count_by_entry.get(entry_file.id, 0),
                    status=status_by_entry.get(entry_file.id, "pending"),
                    latest_operation=latest_operation_by_entry.get(entry_file.id, {}),
                )
                for entry_file in entry_files
            ],
            "selected_entry_file": (
                self.serialize_entry_file_detail(
                    selected_entry_file,
                    run_count=run_count_by_entry.get(selected_entry_file.id, 0),
                    savepoint_count=savepoint_count_by_entry.get(selected_entry_file.id, 0),
                    status=status_by_entry.get(selected_entry_file.id, "pending"),
                    latest_operation=latest_operation_by_entry.get(selected_entry_file.id, {}),
                )
                if selected_entry_file is not None
                else None
            ),
            "runs": [self.serialize_run_summary(run) for run in runs],
            "selected_run": self.serialize_run_detail(selected_run) if selected_run is not None else None,
        }

    def run_detail_payload(self, repository_id: int, run_id: str) -> dict[str, Any]:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        return {"run": self.serialize_run_detail(run)}

    def create_run_for_entry_file(
        self,
        repository_id: int,
        entry_file_id: str,
        *,
        trigger_kind: str = "manual",
        prefer_latest_snapshot: bool = False,
        runtime_snapshot: dict[str, Any] | None = None,
        runtime_stage3_updates: dict[str, Any] | None = None,
        inherited_savepoints: list[dict[str, Any]] | None = None,
    ) -> Stage3Run:
        if prefer_latest_snapshot:
            self.ensure_materialized(repository_ids=[repository_id])
        entry_file = self.get_repository_entry_file(repository_id, entry_file_id, include_runs=True)
        if prefer_latest_snapshot:
            entry_file = self._resolve_latest_entry_file_for_new_run(entry_file, include_runs=True)
        active_run = next(
            (
                run
                for run in list(entry_file.runs or [])
                if str(run.status or "") in _STAGE3_ACTIVE_RUN_STATUSES
            ),
            None,
        )
        if active_run is not None:
            raise Stage3RunConflictError("this entry file already has an unfinished stage3 run")

        snapshot = entry_file.snapshot
        inherited_savepoints = list(inherited_savepoints or [])
        inherited_latest_depth = self._max_savepoint_depth(inherited_savepoints)
        resolved_runtime_snapshot = (
            extract_stage3_runtime_snapshot(runtime_snapshot)
            if isinstance(runtime_snapshot, dict)
            else {}
        )
        runtime_snapshot = {
            "stage3": {
                "repository_id": repository_id,
                "snapshot_id": snapshot.id,
                "source_stage2_run_id": snapshot.source_stage2_run_id,
                "source_commit_sha": snapshot.source_commit_sha,
                "baseline_commit_sha": snapshot.source_commit_sha,
                "target_branch": snapshot.target_branch,
                "entry_file_id": entry_file.id,
                "entry_file_path": entry_file.test_file_path,
                "target_selector": entry_file.target_selector or entry_file.test_file_path,
                "baseline_total_tests": entry_file.baseline_total_tests,
                "baseline_pass_rate": entry_file.baseline_pass_rate,
                "planner_guidance": snapshot.planner_guidance,
                "base_image": snapshot.base_image,
                "dockerfile_text": snapshot.dockerfile_text,
                "run_script_text": snapshot.run_script_text,
                "original_p2p_files": [
                    _normalize_p2p_file_payload(item)
                    for item in list(snapshot.original_p2p_files_json or [])
                ],
                "runtime": resolved_runtime_snapshot or self._runtime_snapshot_seed(),
                "resume_materialized": {
                    "latest_depth": inherited_latest_depth,
                    "savepoints": inherited_savepoints,
                },
            }
        }
        if runtime_stage3_updates:
            runtime_snapshot["stage3"].update(dict(runtime_stage3_updates))
        run = Stage3Run(
            entry_file_id=entry_file.id,
            status=Stage3RunStatus.pending.value,
            result=Stage3RunResult.unknown.value,
            trigger_kind=trigger_kind,
            phase="created",
            runtime_snapshot_json=runtime_snapshot,
            summary_json={
                "latest_depth": inherited_latest_depth,
                "savepoint_count": len(inherited_savepoints),
                "runner_status": "pending",
            },
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Stage3RunConflictError(
                "this entry file already has an unfinished stage3 run"
            ) from exc
        workspace_manifest = self._prepare_run_workspace(run, snapshot=snapshot, entry_file=entry_file)
        run.workspace_path = workspace_manifest["workspace_path"]
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage3": {
                **dict(runtime_snapshot.get("stage3") or {}),
                "workspace": dict(workspace_manifest),
            },
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="bootstrap",
                title="Run created",
                message="Stage3 run record created for this entry file",
                payload={
                    "snapshot_id": snapshot.id,
                    "entry_file_id": entry_file.id,
                    "entry_file_path": entry_file.test_file_path,
                    "target_selector": entry_file.target_selector or entry_file.test_file_path,
                    "source_stage2_run_id": snapshot.source_stage2_run_id,
                    "source_commit_sha": snapshot.source_commit_sha,
                },
            )
        )
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="bootstrap",
                title="Workspace materialized",
                message=f"Prepared stage3 workspace at {workspace_manifest['workspace_path']}",
                payload=dict(workspace_manifest),
            )
        )
        if inherited_savepoints:
            run.events.append(
                self._make_run_event(
                    actor="system",
                    phase="bootstrap",
                    title="Inherited savepoints materialized",
                    message=(
                        f"Materialized {len(inherited_savepoints)} inherited savepoint(s) "
                        f"before continuing from depth {inherited_latest_depth}"
                    ),
                    payload={
                        "inherited_savepoint_count": len(inherited_savepoints),
                        "latest_depth": inherited_latest_depth,
                    },
                )
            )
        run.summary_json = {
            **dict(run.summary_json or {}),
            "workspace_ready": True,
            "workspace_file_count": workspace_manifest["generated_file_count"],
        }
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def queue_run(self, repository_id: int, run_id: str) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(run.status or "") != Stage3RunStatus.pending.value:
            raise Stage3RunConflictError(f"stage3 run is not pending: {run_id}")

        run.status = Stage3RunStatus.queued.value
        run.result = Stage3RunResult.unknown.value
        run.phase = "queued"
        run.finished_at = None
        run.error_message = None
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "queued",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="lifecycle",
                title="Stage3 run queued",
                message="Stage3 run is waiting for a breaker execution slot",
                payload={
                    "trigger_kind": run.trigger_kind,
                    "workspace_path": run.workspace_path,
                },
            )
        )
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Stage3RunConflictError(
                "this entry file already has an unfinished stage3 run"
            ) from exc
        return self.get_run(run.id, include_detail=True)

    def start_run(
        self,
        repository_id: int,
        run_id: str,
        *,
        prepare_workspace: bool = True,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(run.status or "") != Stage3RunStatus.queued.value:
            raise Stage3RunConflictError(f"stage3 run is not queued: {run_id}")

        snapshot = run.entry_file.snapshot
        repository = self.get_repository(repository_id)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        resume_checkpoint = dict(runtime_stage3.get("resume_checkpoint") or {})

        run.status = Stage3RunStatus.running.value
        run.result = Stage3RunResult.unknown.value
        run.phase = "preparing_workspace"
        run.started_at = run.started_at or datetime.now(UTC)
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
                title="Stage3 run started",
                message="Stage3 run entered the active breaking phase",
                payload={
                    "trigger_kind": run.trigger_kind,
                    "workspace_path": run.workspace_path,
                },
            )
        )

        self.session.flush()
        if not prepare_workspace:
            return self.get_run(run.id, include_detail=True)

        return self._prepare_started_run_for_breaker(
            run,
            repository=repository,
            snapshot=snapshot,
            resume_checkpoint=resume_checkpoint,
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def prepare_run_for_breaker(
        self,
        repository_id: int,
        run_id: str,
        *,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(run.status or "") != Stage3RunStatus.running.value:
            raise Stage3RunConflictError(f"stage3 run is not running: {run_id}")
        snapshot = run.entry_file.snapshot
        repository = self.get_repository(repository_id)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        resume_checkpoint = dict(runtime_stage3.get("resume_checkpoint") or {})
        return self._prepare_started_run_for_breaker(
            run,
            repository=repository,
            snapshot=snapshot,
            resume_checkpoint=resume_checkpoint,
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def _prepare_started_run_for_breaker(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        snapshot: Stage3CommitSnapshot,
        resume_checkpoint: dict[str, Any],
        emit_event: Callable[..., None] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> Stage3Run:
        _raise_if_cancel_requested(cancel_requested)
        if resume_checkpoint:
            run.phase = "resume"
            self.session.commit()
            restored = self._restore_run_from_checkpoint(run, checkpoint=resume_checkpoint)
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase="resume",
                title="Checkpoint restored",
                message=f"Restored stage3 workspace from {restored['workspace_snapshot_path']}",
                payload=dict(restored),
                emit_event=emit_event,
            )
        else:
            run.phase = "baseline_prepare"
            self.session.commit()
            checkout_payload = self._materialize_repository_checkout(
                run,
                repository=repository,
                target_commit_sha=snapshot.source_commit_sha,
                emit_event=emit_event,
                cancel_requested=cancel_requested,
            )
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase="baseline_prepare",
                title="Repository checkout prepared",
                message=(
                    f"Prepared baseline repository checkout at "
                    f"{checkout_payload['target_commit_sha']}"
                ),
                payload=dict(checkout_payload),
                emit_event=emit_event,
            )

        _raise_if_cancel_requested(cancel_requested)
        run.phase = "breaker_running"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "running",
            "baseline_commit_sha": str(
                ((run.runtime_snapshot_json or {}).get("stage3") or {}).get("baseline_commit_sha") or ""
            ),
        }
        self.session.flush()
        return self.get_run(run.id, include_detail=True)

    def interrupt_run(self, repository_id: int, run_id: str) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        previous_status = str(run.status or "")
        if previous_status not in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}:
            raise Stage3RunConflictError("only queued or active stage3 runs can be interrupted")
        run.status = Stage3RunStatus.completed.value
        run.result = Stage3RunResult.interrupted.value
        run.phase = "interrupted"
        run.finished_at = datetime.now(UTC)
        run.error_message = "stage3 run interrupted by user"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "runner_status": "interrupted",
        }
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="interrupt",
                title="Stage3 run interrupted",
                message="Interrupted the queued or active stage3 run",
                payload={"run_id": run_id, "previous_status": previous_status},
            )
        )
        return self.get_run(run.id, include_detail=True)

    def rerun_run(self, repository_id: int, run_id: str) -> Stage3Run:
        source_run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(source_run.status or "") in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}:
            raise Stage3RunConflictError("cannot rerun an active stage3 run")
        rerun = self.create_run_for_entry_file(
            repository_id,
            source_run.entry_file_id,
            trigger_kind="rerun",
        )
        return self.queue_run(repository_id, rerun.id)

    def resume_run(self, repository_id: int, run_id: str) -> Stage3Run:
        source_run = self.get_repository_run(repository_id, run_id, include_detail=True)
        checkpoint = self._latest_reusable_resume_checkpoint(source_run)
        if not checkpoint:
            raise Stage3RunConflictError("this stage3 run does not have a reusable checkpoint")
        if str(source_run.status or "") in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}:
            raise Stage3RunConflictError("active stage3 runs cannot create a nested resume run")

        materialized_resume = self._build_materialized_resume_state(source_run)
        inherited_savepoints = list(materialized_resume.get("savepoints") or [])
        resume_depth = int(checkpoint.get("depth") or 0)
        source_runtime_stage3 = dict((source_run.runtime_snapshot_json or {}).get("stage3") or {})
        source_runtime_snapshot = extract_stage3_runtime_snapshot(dict(source_runtime_stage3.get("runtime") or {}))
        resumed_run: Stage3Run | None = None
        cloned_checkpoint: dict[str, Any] | None = None
        try:
            resumed_run = self.create_run_for_entry_file(
                repository_id,
                source_run.entry_file_id,
                trigger_kind="resume",
                runtime_snapshot=source_runtime_snapshot,
                runtime_stage3_updates={
                    "resume_source_run_id": source_run.id,
                    "resume_source_depth": resume_depth,
                    "resume_materialized": materialized_resume,
                },
                inherited_savepoints=inherited_savepoints,
            )
            cloned_checkpoint = self._clone_checkpoint_for_resume_run(
                source_run=source_run,
                destination_run=resumed_run,
                checkpoint=checkpoint,
            )
            _copy_stage3_llm_completion_archives_for_resume_run(
                source_run=source_run,
                stage3_root=self.workspace_root,
                destination_run_id=resumed_run.id,
            )
            runtime_stage3 = dict((resumed_run.runtime_snapshot_json or {}).get("stage3") or {})
            runtime_stage3["resume_checkpoint"] = cloned_checkpoint
            resumed_run.runtime_snapshot_json = {
                **dict(resumed_run.runtime_snapshot_json or {}),
                "stage3": runtime_stage3,
            }
            resumed_run.events.append(
                self._make_run_event(
                    actor="system",
                    phase="resume",
                    title="Resume prepared",
                    message=(
                        f"Prepared a resumed stage3 run from depth {resume_depth} "
                        f"of run {source_run.id}"
                    ),
                    payload={
                        "resume_source_run_id": source_run.id,
                        "resume_source_depth": resume_depth,
                    },
                )
            )
            self.session.flush()
            return self.queue_run(repository_id, resumed_run.id)
        except Exception as exc:
            if resumed_run is not None:
                cleanup_archive = self._prepared_run_cleanup_archive_from_parts(
                    repository_id=repository_id,
                    run_id=resumed_run.id,
                    entry_file_id=resumed_run.entry_file_id,
                    workspace_path=resumed_run.workspace_path,
                    checkpoint_payload=cloned_checkpoint,
                )
                cleanup_result = self._cleanup_prepared_run_assets(
                    resumed_run.id,
                    checkpoint_payload=cloned_checkpoint,
                )
                if _cleanup_result_has_failures(cleanup_result):
                    try:
                        self.session.rollback()
                        self._record_cleanup_tombstone_outside_transaction(
                            cleanup_archive,
                            reason="resume_prepare_failure",
                            cleanup_result=cleanup_result,
                        )
                    except Exception as cleanup_exc:  # noqa: BLE001
                        if hasattr(exc, "add_note"):
                            exc.add_note(
                                "failed to record stage3 resume cleanup tombstone: "
                                f"{cleanup_exc}"
                            )
            raise

    def _reject_savepoint(
        self,
        run: Stage3Run,
        *,
        depth: int,
        feedback: dict[str, Any],
        collateral: dict[str, Any] | None = None,
        file_results: list[Any] | None = None,
        gold_patch_text: Any = _STAGE3_UNSET,
    ) -> None:
        update_kwargs: dict[str, Any] = {"feedback": feedback}
        if collateral is not None:
            update_kwargs["collateral"] = collateral
        if file_results is not None:
            update_kwargs["file_results"] = file_results
        if gold_patch_text is not _STAGE3_UNSET:
            update_kwargs["gold_patch_text"] = gold_patch_text

        self._update_draft_savepoint(run, depth=depth, **update_kwargs)
        run.phase = "breaker_running"
        run.events.append(
            self._make_run_event(
                actor="validator",
                phase="savepoint",
                title="Savepoint rejected",
                message=feedback["message"],
                payload=dict(feedback),
            )
        )
        self.session.commit()
        raise Stage3SavepointRejectedError(
            feedback["message"],
            feedback=feedback,
            collateral=dict(collateral or {}),
        )

    def create_savepoint(
        self,
        repository_id: int,
        run_id: str,
        *,
        milestone_summary: str | None = None,
        rationale: str | None = None,
    ) -> tuple[Stage3Run, dict[str, Any]]:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(run.status or "") != Stage3RunStatus.running.value:
            raise Stage3RunConflictError("savepoint can only be created for an active stage3 run")

        entry_file = run.entry_file
        snapshot = entry_file.snapshot
        workspace_manifest = self._workspace_manifest_for_run(run)
        workspace_dir = Path(workspace_manifest["workspace_path"])
        repo_dir = Path(workspace_manifest["repo_dir"])
        if not repo_dir.exists():
            raise Stage3RunConflictError(f"stage3 repo workspace is missing: {repo_dir}")

        original_p2p_files = [
            _normalize_p2p_file_payload(item)
            for item in list(snapshot.original_p2p_files_json or [])
        ]
        original_p2p_files = [item for item in original_p2p_files if str(item.get("path") or "").strip()]
        if not original_p2p_files:
            raise Stage3RunConflictError("stage3 snapshot is missing original P2P files")
        evaluator_original_p2p_files = [_p2p_file_evaluator_input(item) for item in original_p2p_files]

        draft_depth = self._effective_latest_depth(run) + 1
        normalized_milestone_summary = str(milestone_summary or "").strip()
        normalized_rationale = str(rationale or "").strip()
        run.phase = "savepoint_validation"
        self._clear_draft_savepoint(run)
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="savepoint",
                title="Savepoint evaluation started",
                message=f"Evaluating {len(original_p2p_files)} original P2P file(s) before creating a savepoint",
                payload={
                    "original_p2p_file_count": len(original_p2p_files),
                    "milestone_summary": normalized_milestone_summary,
                    "rationale": normalized_rationale,
                },
            )
        )
        self.session.commit()

        try:
            preflight_patch_text = self._live_gold_patch_text(run, repo_dir=repo_dir)
        except Stage3RunConflictError as exc:
            run.phase = "breaker_running"
            run.error_message = str(exc)
            run.events.append(
                self._make_run_event(
                    actor="validator",
                    phase="savepoint",
                    title="Savepoint patch check failed",
                    message=str(exc),
                    payload={"code": "PATCH_PREFLIGHT_FAILED"},
                )
            )
            self.session.commit()
            raise

        min_removed_code_lines = self._min_removed_code_lines_for_run(run)
        preflight_feedback = _stage3_patch_rejection_feedback(
            preflight_patch_text,
            min_removed_code_lines=min_removed_code_lines,
        )
        if preflight_feedback is not None:
            self._reject_savepoint(
                run,
                depth=draft_depth,
                feedback=preflight_feedback,
                gold_patch_text=preflight_patch_text,
            )

        try:
            evaluation = self._evaluator().evaluate_original_p2p(
                run_id=run.id,
                workspace_dir=workspace_dir,
                repo_dir=repo_dir,
                snapshot_id=str(snapshot.id),
                source_stage2_run_id=str(snapshot.source_stage2_run_id or ""),
                source_commit_sha=str(snapshot.source_commit_sha or ""),
                base_image_id=str(snapshot.base_image or ""),
                dockerfile_text=snapshot.dockerfile_text or "",
                run_script_text=snapshot.run_script_text or "",
                original_p2p_files=evaluator_original_p2p_files,
                emit_event=lambda title, message, payload: self._record_savepoint_evaluation_event(
                    run.id,
                    title=title,
                    message=message,
                    payload=payload,
                ),
            )
            self.session.expire(run, ["events"])
        except (Stage3EvaluationError, Stage2ValidationError) as exc:
            run.phase = "breaker_running"
            run.error_message = str(exc)
            run.events.append(
                self._make_run_event(
                    actor="validator",
                    phase="savepoint",
                    title="Savepoint evaluation failed",
                    message=str(exc),
                    payload={
                        "code": getattr(exc, "code", "EVALUATION_FAILED"),
                        "evidence": dict(getattr(exc, "evidence", {}) or {}),
                    },
                )
            )
            self.session.commit()
            raise Stage3RunConflictError(str(exc)) from exc
        except Exception as exc:
            self.session.rollback()
            run = self.get_repository_run(repository_id, run_id, include_detail=True)
            run.phase = "breaker_running"
            run.error_message = str(exc)
            run.events.append(
                self._make_run_event(
                    actor="validator",
                    phase="savepoint",
                    title="Savepoint evaluation failed",
                    message=str(exc),
                    payload={
                        "code": "EVALUATION_UNEXPECTED_ERROR",
                        "error_type": type(exc).__name__,
                    },
                )
            )
            self.session.commit()
            raise Stage3RunConflictError(str(exc)) from exc

        previous_savepoint = self._latest_effective_savepoint(run)
        previous_pass_rate = (
            float(previous_savepoint.get("entry_pass_rate") or 0.0)
            if previous_savepoint is not None
            else float(entry_file.baseline_pass_rate or 0.0)
        )
        feedback = self._savepoint_feedback(
            run=run,
            entry_file=entry_file,
            evaluation=evaluation.file_results,
            previous_pass_rate=previous_pass_rate,
        )
        collateral = self._collateral_summary(
            evaluation.file_results,
            entry_file_path=entry_file.test_file_path,
        )
        self._update_draft_savepoint(
            run,
            depth=draft_depth,
            feedback=feedback,
            collateral=collateral,
            file_results=evaluation.file_results,
        )
        self.session.commit()
        if not feedback["accepted"]:
            self._reject_savepoint(
                run,
                depth=draft_depth,
                feedback=feedback,
                collateral=collateral,
                file_results=evaluation.file_results,
            )

        depth = draft_depth
        checkpoint: dict[str, Any] = {}
        try:
            self._ensure_git_identity(repo_dir)
            commit_payload = self._commit_stage3_workspace(run, repo_dir=repo_dir)
            patch_text = self._gold_patch_text(run, repo_dir=repo_dir)
            patch_rejection_feedback = _stage3_patch_rejection_feedback(
                patch_text,
                base_feedback=feedback,
                min_removed_code_lines=min_removed_code_lines,
            )
            if patch_rejection_feedback is not None:
                self._reject_savepoint(
                    run,
                    depth=draft_depth,
                    feedback=patch_rejection_feedback,
                    collateral=collateral,
                    file_results=evaluation.file_results,
                    gold_patch_text=patch_text,
                )

            self._update_draft_savepoint(
                run,
                depth=draft_depth,
                gold_patch_text=patch_text,
            )
            self.session.commit()

            checkpoint = self._create_checkpoint_snapshot(run, depth=depth)
            diff_stats = _stage3_diff_stats_from_patch_text(patch_text)
            summary = {
                "git_commit_sha": commit_payload["git_commit_sha"],
                "entry_file_path": entry_file.test_file_path,
                "entry_pass_rate": feedback["entry_pass_rate"],
                "p2p_count": feedback["p2p_count"],
                "f2p_count": feedback["f2p_count"],
                "diff_stats": diff_stats,
                "feedback": dict(feedback),
                "checkpoint_status": "pending",
                "reusable_checkpoint_ready": False,
            }
            if normalized_milestone_summary:
                summary["milestone_summary"] = normalized_milestone_summary
            if normalized_rationale:
                summary["rationale"] = normalized_rationale
            savepoint = Stage3Savepoint(
                run_id=run.id,
                depth=depth,
                entry_pass_rate=feedback["entry_pass_rate"],
                p2p_files_json=list(feedback["p2p_files"]),
                f2p_files_json=list(feedback["f2p_files"]),
                collateral_json=collateral,
                gold_patch_text=patch_text,
                checkpoint_json=checkpoint,
                summary_json=summary,
            )
            savepoint.file_results = [
                Stage3SavepointFileResult(
                    test_file_path=file_result.test_file_path,
                    target_selector=getattr(file_result, "target_selector", None) or file_result.test_file_path,
                    is_entry_file=file_result.test_file_path == entry_file.test_file_path,
                    status=file_result.status,
                    total_tests=file_result.total_tests,
                    passed_tests=file_result.passed_tests,
                    failed_tests=file_result.failed_tests,
                    error_tests=file_result.error_tests,
                    skipped_tests=file_result.skipped_tests,
                    pass_rate=file_result.pass_rate,
                    raw_result_json=file_result.raw_result_json,
                )
                for file_result in evaluation.file_results
            ]
            run.savepoints.append(savepoint)
            run.phase = "breaker_running"
            run.error_message = None
            run.summary_json = {
                **dict(run.summary_json or {}),
                "latest_depth": depth,
                "savepoint_count": self._effective_savepoint_count(run),
                "latest_entry_pass_rate": feedback["entry_pass_rate"],
                "runner_status": "running",
            }
            runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
            runtime_stage3["resume_checkpoint"] = checkpoint
            run.runtime_snapshot_json = {
                **dict(run.runtime_snapshot_json or {}),
                "stage3": runtime_stage3,
            }
            self._persist_savepoint_assets(
                run,
                depth=depth,
                gold_patch_text=patch_text,
                checkpoint=checkpoint,
                evaluation=evaluation.file_results,
                feedback=feedback,
                collateral=collateral,
            )
            self._clear_draft_savepoint(run)
            run.events.append(
                self._make_run_event(
                    actor="system",
                    phase="savepoint",
                    title="Savepoint archived",
                    message=(
                        f"Archived stage3 savepoint depth {depth} with entry-file pass rate "
                        f"{feedback['entry_pass_rate']:.3f}"
                    ),
                    payload={
                        **dict(feedback),
                        "depth": depth,
                        "git_commit_sha": commit_payload["git_commit_sha"],
                        "checkpoint": checkpoint,
                        "checkpoint_status": "pending",
                        "milestone_summary": normalized_milestone_summary,
                        "rationale": normalized_rationale,
                    },
                )
            )
            self.session.flush()
            return self.get_run(run.id, include_detail=True), {
                **dict(feedback),
                "accepted": True,
                "depth": depth,
            }
        except Stage3SavepointRejectedError:
            raise
        except Exception as exc:
            self.session.rollback()
            recovered_run = self.get_repository_run(repository_id, run_id, include_detail=True)
            self._restore_run_after_savepoint_archive_failure(
                recovered_run,
                depth=depth,
                checkpoint=checkpoint,
                error=exc,
            )
            self.session.commit()
            if isinstance(exc, Stage3RunConflictError):
                raise
            raise Stage3RunConflictError(str(exc)) from exc

    def delete_run(self, repository_id: int, run_id: str) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        self.delete_repository_run(repository_id, run_id)
        return run

    def delete_repository_run(self, repository_id: int, run_id: str) -> dict[str, Any]:
        archive = self.repository_run_cleanup_archive(repository_id, run_id)
        self.upsert_cleanup_tombstone(archive, reason="manual_delete")
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        self.session.delete(run)
        self.session.flush()
        return archive

    def delete_snapshot_if_orphaned(self, snapshot_id: str) -> dict[str, Any] | None:
        normalized_snapshot_id = str(snapshot_id or "").strip()
        if not normalized_snapshot_id:
            return None
        snapshot = self.session.get(Stage3CommitSnapshot, normalized_snapshot_id)
        if snapshot is None:
            return None
        if self._snapshot_has_stage3_runs(snapshot):
            return None
        archive = self.prepared_snapshot_cleanup_archive(snapshot)
        self.session.delete(snapshot)
        self.session.flush()
        return archive

    def repository_run_cleanup_archive(self, repository_id: int, run_id: str) -> dict[str, Any]:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if str(run.status or "") in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}:
            raise Stage3RunConflictError(f"cannot delete an active stage3 run: {run_id}")
        return self.prepared_run_cleanup_archive(repository_id=repository_id, run=run)

    def prepared_run_cleanup_archive(self, *, repository_id: int, run: Stage3Run) -> dict[str, Any]:
        return {
            "run_id": run.id,
            "repository_id": repository_id,
            "entry_file_id": run.entry_file_id,
            "workspace_path": run.workspace_path,
            "runtime_path": str(self._run_runtime_dir(run.id)),
            "checkpoint_docker_image_refs": self._owned_checkpoint_docker_image_refs_for_run(run),
        }

    def prepared_snapshot_cleanup_archive(self, snapshot: Stage3CommitSnapshot) -> dict[str, Any]:
        return {
            "snapshot_id": snapshot.id,
            "repository_id": snapshot.repository_id,
            "workspace_path": str(self._snapshot_workspace_dir(snapshot.id)),
        }

    def _prepared_run_cleanup_archive_from_parts(
        self,
        *,
        repository_id: int,
        run_id: str,
        entry_file_id: str | None,
        workspace_path: str | None,
        checkpoint_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "repository_id": repository_id,
            "entry_file_id": entry_file_id,
            "workspace_path": workspace_path,
            "runtime_path": str(self._run_runtime_dir(run_id)),
            "checkpoint_docker_image_refs": self._owned_checkpoint_docker_image_refs_from_payload(
                checkpoint_payload,
                run_id=run_id,
            ),
        }

    def _record_cleanup_tombstone_outside_transaction(
        self,
        archive: dict[str, Any],
        *,
        reason: str,
        cleanup_result: dict[str, Any],
    ) -> None:
        run_id = str(archive.get("run_id") or "").strip()
        if not run_id:
            return
        with Session(bind=self.session.get_bind()) as cleanup_session:
            cleanup_service = Stage3Service(
                cleanup_session,
                workspace_root=self.workspace_root,
                settings=self.settings,
            )
            cleanup_service.upsert_cleanup_tombstone(archive, reason=reason)
            cleanup_service.record_cleanup_tombstone_result(
                run_id,
                cleanup_result=cleanup_result,
            )
            cleanup_session.commit()

    def upsert_cleanup_tombstone(
        self,
        archive: dict[str, Any],
        *,
        reason: str,
    ) -> Stage3CleanupTombstone | None:
        run_id = str(archive.get("run_id") or "").strip()
        if not run_id:
            return None
        tombstone = self.session.get(Stage3CleanupTombstone, run_id)
        if tombstone is None:
            tombstone = Stage3CleanupTombstone(run_id=run_id)
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
                select(Stage3CleanupTombstone)
                .where(Stage3CleanupTombstone.status.in_(["pending", "failed"]))
                .order_by(Stage3CleanupTombstone.created_at.asc())
            )
        )
        archives: list[dict[str, Any]] = []
        for row in rows:
            archive = dict(row.archive_json or {})
            archive.setdefault("run_id", row.run_id)
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
        tombstone = self.session.get(Stage3CleanupTombstone, run_id)
        if tombstone is None:
            return
        failure_payload = dict(cleanup_result or {})
        failed_paths = list(failure_payload.get("failed_paths") or [])
        failed_repo_checkout_paths = list(failure_payload.get("failed_repo_checkout_paths") or [])
        failed_checkpoint_images = list(failure_payload.get("failed_checkpoint_docker_image_refs") or [])
        tombstone.attempt_count = int(tombstone.attempt_count or 0) + 1
        tombstone.last_attempt_at = datetime.now(UTC)
        if failed_paths or failed_repo_checkout_paths or failed_checkpoint_images:
            tombstone.status = "failed"
            tombstone.failure_json = failure_payload
            self.session.add(tombstone)
        else:
            self.session.delete(tombstone)
        self.session.flush()

    def repair_duplicate_active_runs(self, *, return_archives: bool = False) -> int | list[dict[str, Any]]:
        dirty = 0
        archives: list[dict[str, Any]] = []
        duplicate_entry_file_ids = list(
            self.session.scalars(
                select(Stage3Run.entry_file_id)
                .where(Stage3Run.status.in_(_STAGE3_ACTIVE_RUN_STATUSES))
                .group_by(Stage3Run.entry_file_id)
                .having(func.count(Stage3Run.id) > 1)
            )
        )
        if not duplicate_entry_file_ids:
            return archives if return_archives else dirty

        repaired_at = datetime.now(UTC)
        for entry_file_id in duplicate_entry_file_ids:
            active_run_priority = case(
                (Stage3Run.status == Stage3RunStatus.running.value, 0),
                (Stage3Run.status == Stage3RunStatus.queued.value, 1),
                else_=2,
            )
            active_runs = list(
                self.session.scalars(
                    select(Stage3Run)
                    .options(
                        selectinload(Stage3Run.entry_file).selectinload(Stage3EntryFile.snapshot),
                        selectinload(Stage3Run.savepoints),
                    )
                    .where(
                        Stage3Run.entry_file_id == entry_file_id,
                        Stage3Run.status.in_(_STAGE3_ACTIVE_RUN_STATUSES),
                    )
                    .order_by(
                        active_run_priority.asc(),
                        Stage3Run.created_at.desc(),
                        Stage3Run.id.desc(),
                    )
                )
            )
            for stale_run in active_runs[1:]:
                previous_status = stale_run.status
                previous_phase = stale_run.phase
                repository_id = stale_run.entry_file.snapshot.repository_id
                archive = {
                    "run_id": stale_run.id,
                    "repository_id": repository_id,
                    "entry_file_id": stale_run.entry_file_id,
                    "workspace_path": stale_run.workspace_path,
                    "runtime_path": str(self._run_runtime_dir(stale_run.id)),
                    "checkpoint_docker_image_refs": self._owned_checkpoint_docker_image_refs_for_run(stale_run),
                }
                archives.append(archive)
                self.upsert_cleanup_tombstone(archive, reason="duplicate_active_repair")
                stale_run.status = Stage3RunStatus.completed.value
                stale_run.result = Stage3RunResult.failed.value
                stale_run.phase = "completed"
                stale_run.finished_at = repaired_at
                stale_run.error_message = (
                    stale_run.error_message
                    or "stage3 run invalidated while restoring the single-active-run invariant"
                )
                stale_run.summary_json = {
                    **dict(stale_run.summary_json or {}),
                    "runner_status": "failed",
                }
                stale_run.events.append(
                    self._make_run_event(
                        actor="system",
                        phase="failed",
                        title="Stage3 active-run invariant repaired",
                        message=stale_run.error_message,
                        payload={
                            "run_id": stale_run.id,
                            "repository_id": repository_id,
                            "entry_file_id": stale_run.entry_file_id,
                            "previous_status": previous_status,
                            "previous_phase": previous_phase,
                        },
                    )
                )
                dirty += 1
        if dirty:
            self.session.flush()
        return archives if return_archives else dirty

    def recover_interrupted_runs(self, *, return_archives: bool = False) -> int | list[dict[str, Any]]:
        dirty = 0
        archives: list[dict[str, Any]] = []
        rows = list(
            self.session.scalars(
                select(Stage3Run)
                .options(
                    selectinload(Stage3Run.entry_file).selectinload(Stage3EntryFile.snapshot),
                    selectinload(Stage3Run.savepoints),
                )
                .where(Stage3Run.status.in_([Stage3RunStatus.queued.value, Stage3RunStatus.running.value]))
            )
        )
        for row in rows:
            previous_status = str(row.status or "")
            previous_phase = row.phase
            archive = {
                "run_id": row.id,
                "repository_id": row.entry_file.snapshot.repository_id,
                "entry_file_id": row.entry_file_id,
                "workspace_path": row.workspace_path,
                "runtime_path": str(self._run_runtime_dir(row.id)),
                "checkpoint_docker_image_refs": [],
                "cleanup_scope": "recovered_run_assets",
            }
            archives.append(archive)
            self.upsert_cleanup_tombstone(archive, reason="recovered_interrupted_run")
            error_message = (
                row.error_message
                or (
                    "stage3 run interrupted before execution"
                    if previous_status == Stage3RunStatus.queued.value
                    else "stage3 run interrupted before completion"
                )
            )
            row.status = Stage3RunStatus.completed.value
            row.result = Stage3RunResult.failed.value
            row.phase = "completed"
            row.finished_at = datetime.now(UTC)
            row.error_message = error_message
            row.summary_json = {
                **dict(row.summary_json or {}),
                "runner_status": "failed",
            }
            row.events.append(
                self._make_run_event(
                    actor="system",
                    phase="failed",
                    title="Stage3 run recovered as interrupted",
                    message=error_message,
                    payload={
                        "run_id": row.id,
                        "repository_id": archive["repository_id"],
                        "previous_status": previous_status,
                        "previous_phase": previous_phase,
                    },
                )
            )
            dirty += 1
        if dirty:
            self.session.flush()
        return archives if return_archives else dirty

    def serialize_commit_snapshot_summary(self, snapshot: Stage3CommitSnapshot | None) -> dict[str, Any] | None:
        if snapshot is None:
            return None
        summary = dict(snapshot.summary_json or {})
        return {
            "id": snapshot.id,
            "source_stage2_run_id": snapshot.source_stage2_run_id,
            "source_commit_sha": snapshot.source_commit_sha,
            "target_branch": snapshot.target_branch,
            "base_image": snapshot.base_image,
            "entry_file_count": int(summary.get("entry_file_count") or len(snapshot.entry_files or [])),
            "baseline_total_tests": int(summary.get("baseline_total_tests") or 0),
            "baseline_passed_tests": int(summary.get("baseline_passed_tests") or 0),
            "source_created_at": serialize_datetime(snapshot.source_created_at),
            "source_finished_at": serialize_datetime(snapshot.source_finished_at),
            "created_at": serialize_datetime(snapshot.created_at),
            "updated_at": serialize_datetime(snapshot.updated_at),
        }

    def serialize_commit_snapshot_detail(self, snapshot: Stage3CommitSnapshot) -> dict[str, Any]:
        payload = self.serialize_commit_snapshot_summary(snapshot) or {}
        payload.update(
            {
                "planner_guidance": snapshot.planner_guidance,
                "dockerfile_text": snapshot.dockerfile_text,
                "run_script_text": snapshot.run_script_text,
                "original_p2p_files": [
                    _normalize_p2p_file_payload(item)
                    for item in list(snapshot.original_p2p_files_json or [])
                ],
                "summary": dict(snapshot.summary_json or {}),
            }
        )
        return payload

    def serialize_entry_file_summary(
        self,
        entry_file: Stage3EntryFile,
        *,
        run_count: int,
        savepoint_count: int,
        status: str,
        latest_operation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        latest_operation = dict(latest_operation or {})
        return {
            "id": entry_file.id,
            "snapshot_id": entry_file.snapshot_id,
            "test_file_path": entry_file.test_file_path,
            "target_selector": entry_file.target_selector or entry_file.test_file_path,
            "baseline_total_tests": entry_file.baseline_total_tests,
            "baseline_passed_tests": entry_file.baseline_passed_tests,
            "baseline_failed_tests": entry_file.baseline_failed_tests,
            "baseline_error_tests": entry_file.baseline_error_tests,
            "baseline_skipped_tests": entry_file.baseline_skipped_tests,
            "baseline_pass_rate": entry_file.baseline_pass_rate,
            "status": status,
            "run_count": run_count,
            "savepoint_count": savepoint_count,
            "latest_run_id": str(latest_operation.get("run_id") or "") or None,
            "latest_operation_at": serialize_datetime(latest_operation.get("updated_at")),
            "created_at": serialize_datetime(entry_file.created_at),
            "updated_at": serialize_datetime(entry_file.updated_at),
        }

    def serialize_entry_file_detail(
        self,
        entry_file: Stage3EntryFile,
        *,
        run_count: int,
        savepoint_count: int,
        status: str,
        latest_operation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self.serialize_entry_file_summary(
            entry_file,
            run_count=run_count,
            savepoint_count=savepoint_count,
            status=status,
            latest_operation=latest_operation,
        )
        payload["summary"] = dict(entry_file.summary_json or {})
        return payload

    def serialize_run_summary(self, run: Stage3Run) -> dict[str, Any]:
        summary = dict(run.summary_json or {})
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        snapshot = run.entry_file.snapshot if run.entry_file is not None else None
        source_commit_sha = str(
            runtime_stage3.get("source_commit_sha")
            or getattr(snapshot, "source_commit_sha", "")
            or ""
        )
        baseline_commit_sha = str(
            runtime_stage3.get("baseline_commit_sha")
            or source_commit_sha
            or ""
        )
        source_stage2_run_id = str(
            runtime_stage3.get("source_stage2_run_id")
            or getattr(snapshot, "source_stage2_run_id", "")
            or ""
        )
        effective_savepoint_count = self._effective_savepoint_count(run)
        effective_latest_depth = self._effective_latest_depth(run)
        latest_checkpoint = self._latest_resume_checkpoint(run)
        reusable_checkpoint = self._latest_reusable_resume_checkpoint(run)
        run_status = str(run.status or "")
        display_status = self._run_display_status(run)
        usage_metrics = self._serialized_run_usage_metrics(run)
        can_resume = (
            run_status not in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}
            and str(run.result or "") != Stage3RunResult.archived.value
            and bool(reusable_checkpoint)
        )
        return {
            "id": run.id,
            "status": run.status,
            "result": run.result,
            "display_status": display_status,
            "trigger_kind": run.trigger_kind,
            "phase": run.phase,
            "source_stage2_run_id": source_stage2_run_id or None,
            "source_commit_sha": source_commit_sha or None,
            "baseline_commit_sha": baseline_commit_sha or None,
            "summary": {
                **summary,
                "latest_depth": effective_latest_depth,
                "savepoint_count": effective_savepoint_count,
            },
            "error_message": run.error_message,
            "created_at": serialize_datetime(run.created_at),
            "updated_at": serialize_datetime(run.updated_at),
            "started_at": serialize_datetime(run.started_at),
            "finished_at": serialize_datetime(run.finished_at),
            "duration_seconds": usage_metrics["duration_seconds"],
            "token_usage_by_model": dict(usage_metrics["token_usage_by_model"] or {}),
            "is_active": run_status in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value},
            "can_delete": run_status not in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value},
            "can_start": run_status == Stage3RunStatus.pending.value,
            "can_resume": can_resume,
            "can_interrupt": run_status in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value},
            "can_complete": False,
            "can_savepoint": False,
            "can_rerun": run_status
            not in {
                Stage3RunStatus.pending.value,
                Stage3RunStatus.queued.value,
                Stage3RunStatus.running.value,
            },
            "resume_depth": int((reusable_checkpoint or latest_checkpoint).get("depth") or 0)
            if (reusable_checkpoint or latest_checkpoint)
            else 0,
        }

    def _run_display_status(self, run: Stage3Run) -> str:
        status = str(run.status or "")
        if status in {
            Stage3RunStatus.pending.value,
            Stage3RunStatus.queued.value,
            Stage3RunStatus.running.value,
        }:
            return status

        if str(run.result or "") == Stage3RunResult.archived.value:
            return "succeeded"

        error_message = str(run.error_message or "").strip().lower()
        if "interrupted by user" in error_message:
            return "interrupted"
        if bool((run.summary_json or {}).get("schedule_failed")):
            return "schedule_failed"
        if str(run.result or "") == Stage3RunResult.interrupted.value:
            return "interrupted"
        return "failed"

    def serialize_run_detail(self, run: Stage3Run) -> dict[str, Any]:
        payload = self.serialize_run_summary(run)
        effective_savepoints = self._effective_savepoint_payloads(run)
        payload.update(
            {
                "workspace_path": run.workspace_path,
                "runtime_snapshot": self._api_runtime_snapshot(run),
                "workspace_manifest": dict(
                    (dict(run.runtime_snapshot_json or {}).get("stage3") or {}).get("workspace") or {}
                ),
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
                "savepoints": effective_savepoints,
                "draft_savepoint": self._draft_savepoint_payload(run),
            }
        )
        return payload

    def _serialized_llm_completion_archives(self, run: Stage3Run) -> dict[str, dict[str, Any]]:
        runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        return {
            role: summarize_llm_completion_archive(runtime_dir, role)
            for role in STAGE3_LLM_COMPLETION_ARCHIVE_ROLES
        }

    def _eligible_stage2_runs(self, *, repository_ids: list[int] | None = None) -> list[Stage2Run]:
        stmt = (
            select(Stage2Run)
            .options(selectinload(Stage2Run.test_results))
            .where(
                Stage2Run.status == Stage2RunStatus.completed.value,
                Stage2Run.result == Stage2RunResult.passed.value,
                Stage2Run.target_commit_sha.is_not(None),
            )
            .order_by(
                Stage2Run.repository_id.asc(),
                Stage2Run.target_commit_sha.asc(),
                Stage2Run.created_at.desc(),
                Stage2Run.id.desc(),
            )
        )
        if repository_ids:
            stmt = stmt.where(Stage2Run.repository_id.in_(repository_ids))
        return list(self.session.scalars(stmt))

    def _passed_test_results_for_run(self, run: Stage2Run) -> list[Stage2TestResult]:
        return [
            row
            for row in list(run.test_results or [])
            if str(row.status or "") == "passed" and str(row.test_file_path or "").strip()
        ]

    def _stage2_entry_file_test_count_min(self, run: Stage2Run) -> int:
        snapshot = dict(run.runtime_snapshot_json or {})
        hyperparameters = dict(snapshot.get("hyperparameters") or {})
        value = hyperparameters.get("entry_file_test_count_min", -1)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = -1
        return max(parsed, -1)

    def _upsert_snapshot_from_stage2_run(
        self,
        *,
        snapshot: Stage3CommitSnapshot | None,
        run: Stage2Run,
    ) -> bool:
        passed_results = self._passed_test_results_for_run(run)
        if not passed_results:
            return False
        entry_file_test_count_min = self._stage2_entry_file_test_count_min(run)
        filtered_entry_results = [
            row
            for row in passed_results
            if entry_file_test_count_min < 0 or int(row.total_tests or 0) >= entry_file_test_count_min
        ]
        entry_results = filtered_entry_results

        original_p2p_files = [
            _p2p_file_payload(
                row.test_file_path,
                row.target_selector,
                baseline_total_tests=row.total_tests,
                baseline_passed_tests=row.passed_tests,
                baseline_failed_tests=row.failed_tests,
                baseline_error_tests=row.error_tests,
                baseline_skipped_tests=row.skipped_tests,
                baseline_pass_rate=self._baseline_pass_rate(row),
            )
            for row in passed_results
        ]
        materialized_at = serialize_datetime(datetime.now(UTC))
        if snapshot is not None:
            existing_summary = dict(snapshot.summary_json or {})
            materialized_at = str(existing_summary.get("materialized_at") or materialized_at)
        summary = {
            "entry_file_count": len(entry_results),
            "source_entry_file_count": len(passed_results),
            "filtered_out_entry_file_count": max(len(passed_results) - len(entry_results), 0),
            "entry_file_test_count_min": entry_file_test_count_min,
            "baseline_total_tests": sum(int(row.total_tests or 0) for row in entry_results),
            "baseline_passed_tests": sum(int(row.passed_tests or 0) for row in entry_results),
            "baseline_failed_tests": sum(int(row.failed_tests or 0) for row in entry_results),
            "baseline_error_tests": sum(int(row.error_tests or 0) for row in entry_results),
            "baseline_skipped_tests": sum(int(row.skipped_tests or 0) for row in entry_results),
            "materialized_at": materialized_at,
        }
        entry_payloads = [
            {
                "test_file_path": row.test_file_path,
                "target_selector": row.target_selector or row.test_file_path,
                "baseline_total_tests": int(row.total_tests or 0),
                "baseline_passed_tests": int(row.passed_tests or 0),
                "baseline_failed_tests": int(row.failed_tests or 0),
                "baseline_error_tests": int(row.error_tests or 0),
                "baseline_skipped_tests": int(row.skipped_tests or 0),
                "baseline_pass_rate": self._baseline_pass_rate(row),
                "summary_json": {
                    "source_stage2_run_id": run.id,
                    "entry_file_test_count_min": entry_file_test_count_min,
                },
            }
            for row in entry_results
        ]

        if snapshot is None:
            snapshot = Stage3CommitSnapshot(
                repository_id=run.repository_id,
                source_stage2_run_id=run.id,
                source_commit_sha=str(run.target_commit_sha or ""),
                target_branch=run.target_branch,
                base_image=run.base_image,
                planner_guidance=run.planner_guidance,
                dockerfile_text=run.dockerfile_text,
                run_script_text=run.run_script_text,
                original_p2p_files_json=original_p2p_files,
                summary_json=summary,
                source_created_at=run.created_at,
                source_finished_at=run.finished_at,
            )
            snapshot.entry_files = [
                Stage3EntryFile(**payload)
                for payload in entry_payloads
            ]
            self.session.add(snapshot)
            return True

        existing_paths = {
            str(entry_file.test_file_path or ""): entry_file
            for entry_file in list(snapshot.entry_files or [])
        }
        existing_signature = {
            path: (
                entry_file.baseline_total_tests,
                entry_file.baseline_passed_tests,
                entry_file.baseline_failed_tests,
                entry_file.baseline_error_tests,
                entry_file.baseline_skipped_tests,
                entry_file.baseline_pass_rate,
                entry_file.target_selector or entry_file.test_file_path,
            )
            for path, entry_file in existing_paths.items()
        }
        next_signature = {
            str(payload["test_file_path"]): (
                payload["baseline_total_tests"],
                payload["baseline_passed_tests"],
                payload["baseline_failed_tests"],
                payload["baseline_error_tests"],
                payload["baseline_skipped_tests"],
                payload["baseline_pass_rate"],
                payload["target_selector"],
            )
            for payload in entry_payloads
        }
        if (
            snapshot.source_stage2_run_id == run.id
            and snapshot.target_branch == run.target_branch
            and snapshot.base_image == run.base_image
            and (snapshot.planner_guidance or "") == (run.planner_guidance or "")
            and (snapshot.dockerfile_text or "") == (run.dockerfile_text or "")
            and (snapshot.run_script_text or "") == (run.run_script_text or "")
            and list(snapshot.original_p2p_files_json or []) == original_p2p_files
            and dict(snapshot.summary_json or {}) == summary
            and existing_signature == next_signature
        ):
            return False

        if self._snapshot_has_stage3_runs(snapshot):
            return False

        snapshot.source_stage2_run_id = run.id
        snapshot.target_branch = run.target_branch
        snapshot.base_image = run.base_image
        snapshot.planner_guidance = run.planner_guidance
        snapshot.dockerfile_text = run.dockerfile_text
        snapshot.run_script_text = run.run_script_text
        snapshot.original_p2p_files_json = original_p2p_files
        snapshot.summary_json = summary
        snapshot.source_created_at = run.created_at
        snapshot.source_finished_at = run.finished_at
        next_entry_payloads_by_path = {
            str(payload["test_file_path"]): payload
            for payload in entry_payloads
        }
        for path, entry_file in list(existing_paths.items()):
            payload = next_entry_payloads_by_path.pop(path, None)
            if payload is None:
                snapshot.entry_files.remove(entry_file)
                continue
            entry_file.baseline_total_tests = payload["baseline_total_tests"]
            entry_file.baseline_passed_tests = payload["baseline_passed_tests"]
            entry_file.baseline_failed_tests = payload["baseline_failed_tests"]
            entry_file.baseline_error_tests = payload["baseline_error_tests"]
            entry_file.baseline_skipped_tests = payload["baseline_skipped_tests"]
            entry_file.baseline_pass_rate = payload["baseline_pass_rate"]
            entry_file.target_selector = payload["target_selector"]
            entry_file.summary_json = dict(payload["summary_json"] or {})
        for payload in next_entry_payloads_by_path.values():
            snapshot.entry_files.append(Stage3EntryFile(**payload))
        return True

    def _snapshot_has_stage3_runs(self, snapshot: Stage3CommitSnapshot) -> bool:
        count = self.session.scalar(
            select(func.count(Stage3Run.id))
            .join(Stage3EntryFile, Stage3Run.entry_file_id == Stage3EntryFile.id)
            .where(Stage3EntryFile.snapshot_id == snapshot.id)
        )
        return int(count or 0) > 0

    def _run_count_by_entry_file(self, entry_file_ids: list[str]) -> dict[str, int]:
        if not entry_file_ids:
            return {}
        return {
            str(entry_file_id): int(count)
            for entry_file_id, count in self.session.execute(
                select(Stage3Run.entry_file_id, func.count(Stage3Run.id))
                .where(Stage3Run.entry_file_id.in_(entry_file_ids))
                .group_by(Stage3Run.entry_file_id)
            )
        }

    def _savepoint_identity_key(self, payload: dict[str, Any], *, fallback: str) -> str:
        normalized = dict(payload or {})
        savepoint_id = str(normalized.get("id") or "").strip()
        if savepoint_id:
            return f"id:{savepoint_id}"
        summary = dict(normalized.get("summary") or {})
        git_commit_sha = str(normalized.get("git_commit_sha") or summary.get("git_commit_sha") or "").strip()
        if git_commit_sha:
            return f"git:{git_commit_sha}"
        depth = str(normalized.get("depth") or "").strip()
        created_at = str(normalized.get("created_at") or "").strip()
        if depth or created_at:
            return f"depth:{depth}:created:{created_at}"
        return f"fallback:{fallback}"

    def _effective_savepoint_identity_keys(self, run: Stage3Run | None) -> set[str]:
        if run is None:
            return set()
        keys: set[str] = set()
        for savepoint in list(run.savepoints or []):
            keys.add(f"id:{savepoint.id}")
        inherited = list(self._materialized_resume_state(run).get("savepoints") or [])
        for index, payload in enumerate(inherited):
            if isinstance(payload, dict):
                keys.add(self._savepoint_identity_key(payload, fallback=f"{run.id}:inherited:{index}"))
        return keys

    def _effective_savepoint_keys_by_entry_file(self, entry_file_ids: list[str]) -> dict[str, set[str]]:
        result = {str(entry_file_id): set() for entry_file_id in entry_file_ids}
        if not entry_file_ids:
            return result
        runs = list(
            self.session.scalars(
                select(Stage3Run)
                .options(selectinload(Stage3Run.savepoints))
                .where(Stage3Run.entry_file_id.in_(entry_file_ids))
            )
        )
        for run in runs:
            entry_file_id = str(run.entry_file_id or "")
            result.setdefault(entry_file_id, set()).update(self._effective_savepoint_identity_keys(run))
        return result

    def _savepoint_count_by_entry_file(self, entry_file_ids: list[str]) -> dict[str, int]:
        if not entry_file_ids:
            return {}
        return {
            entry_file_id: len(keys)
            for entry_file_id, keys in self._effective_savepoint_keys_by_entry_file(entry_file_ids).items()
        }

    def _entry_file_latest_operation_by_entry_file(self, entry_file_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not entry_file_ids:
            return {}
        latest_by_entry: dict[str, dict[str, Any]] = {}
        for entry_file_id, run_id, updated_at in self.session.execute(
            select(Stage3Run.entry_file_id, Stage3Run.id, Stage3Run.updated_at)
            .where(Stage3Run.entry_file_id.in_(entry_file_ids))
        ):
            entry_key = str(entry_file_id)
            normalized_updated_at = updated_at if isinstance(updated_at, datetime) else None
            current = latest_by_entry.get(entry_key)
            current_updated_at = current.get("updated_at") if current else None
            if (
                current is None
                or current_updated_at is None
                or (
                    normalized_updated_at is not None
                    and (
                        normalized_updated_at > current_updated_at
                        or (
                            normalized_updated_at == current_updated_at
                            and str(run_id or "") > str(current.get("run_id") or "")
                        )
                    )
                )
            ):
                latest_by_entry[entry_key] = {
                    "run_id": str(run_id or ""),
                    "updated_at": normalized_updated_at,
                }
        return latest_by_entry

    def _entry_file_status_by_entry_file(self, entry_file_ids: list[str]) -> dict[str, str]:
        if not entry_file_ids:
            return {}
        latest_run_by_entry: dict[str, dict[str, Any]] = {}
        runs = list(
            self.session.scalars(
                select(Stage3Run)
                .options(selectinload(Stage3Run.savepoints))
                .where(Stage3Run.entry_file_id.in_(entry_file_ids))
            )
        )
        for run in runs:
            entry_key = str(run.entry_file_id or "")
            run_id = str(run.id or "")
            normalized_status = str(run.status or "")
            if normalized_status == Stage3RunStatus.running.value:
                latest_run_by_entry[entry_key] = {"status": "running"}
                continue
            if normalized_status == Stage3RunStatus.queued.value:
                current = latest_run_by_entry.get(entry_key)
                if not current or current.get("status") != "running":
                    latest_run_by_entry[entry_key] = {"status": "queued"}
                continue
            if normalized_status == Stage3RunStatus.pending.value:
                current = latest_run_by_entry.get(entry_key)
                if current and current.get("status") in {"running", "queued"}:
                    continue
                current_updated_at = current.get("updated_at") if current else None
                normalized_updated_at = run.updated_at if isinstance(run.updated_at, datetime) else None
                if (
                    current is None
                    or current_updated_at is None
                    or (
                        normalized_updated_at is not None
                        and (
                            normalized_updated_at > current_updated_at
                            or (
                                normalized_updated_at == current_updated_at
                                and str(run_id or "") > str(current.get("run_id") or "")
                            )
                        )
                    )
                ):
                    latest_run_by_entry[entry_key] = {
                        "status": "pending",
                        "updated_at": run.updated_at if isinstance(run.updated_at, datetime) else None,
                        "run_id": str(run_id or ""),
                    }
                continue
            current = latest_run_by_entry.get(entry_key)
            if current and current.get("status") in {"running", "queued"}:
                continue
            current_updated_at = current.get("updated_at") if current else None
            normalized_updated_at = run.updated_at if isinstance(run.updated_at, datetime) else None
            if (
                current is None
                or current_updated_at is None
                or (
                    normalized_updated_at is not None
                    and (
                        normalized_updated_at > current_updated_at
                        or (
                            normalized_updated_at == current_updated_at
                            and str(run_id or "") > str(current.get("run_id") or "")
                        )
                    )
                )
            ):
                latest_run_by_entry[entry_key] = {
                    "status": (
                        "succeeded"
                        if str(run.result or "") == Stage3RunResult.archived.value
                        and len(self._effective_savepoint_identity_keys(run)) > 0
                        else "failed"
                    ),
                    "updated_at": normalized_updated_at,
                    "run_id": str(run_id or ""),
                }
        return {
            entry_file_id: str(latest_run_by_entry.get(entry_file_id, {}).get("status") or "pending")
            for entry_file_id in entry_file_ids
        }

    def _materialized_resume_state(self, run: Stage3Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        state = dict(runtime_stage3.get("resume_materialized") or {})
        return state if isinstance(state, dict) else {}

    def _build_materialized_resume_state(self, source_run: Stage3Run) -> dict[str, Any]:
        savepoints = [
            self._materialized_resume_savepoint_payload(payload)
            for payload in self._effective_savepoint_payloads(source_run)
        ]
        usage_metrics = self._serialized_run_usage_metrics(source_run)
        return {
            "schema_version": 1,
            "source_run_id": str(source_run.id or ""),
            "latest_depth": self._max_savepoint_depth(savepoints),
            "savepoints": savepoints,
            "duration_seconds": usage_metrics.get("duration_seconds"),
            "token_usage_by_model": dict(usage_metrics.get("token_usage_by_model") or {}),
            "created_at": serialize_datetime(datetime.now(UTC)),
        }

    def _materialized_resume_savepoint_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        materialized = dict(payload or {})
        materialized["checkpoint"] = {}
        materialized["checkpoint_status"] = "service_snapshot_only"
        materialized["reusable_checkpoint_ready"] = False
        materialized["source"] = "inherited"
        summary = dict(materialized.get("summary") or {})
        summary["checkpoint_status"] = "service_snapshot_only"
        summary["reusable_checkpoint_ready"] = False
        materialized["summary"] = summary
        return materialized

    def _materialized_resume_usage_metrics(self, run: Stage3Run | None) -> dict[str, Any]:
        materialized = self._materialized_resume_state(run)
        duration_seconds = materialized.get("duration_seconds")
        if duration_seconds is not None:
            try:
                duration_seconds = max(float(duration_seconds), 0.0)
            except (TypeError, ValueError):
                duration_seconds = None
        token_usage_by_model = aggregate_token_usage_by_model(
            dict(materialized.get("token_usage_by_model") or {}).items()
        )
        return {
            "duration_seconds": duration_seconds,
            "token_usage_by_model": token_usage_by_model,
        }

    def _resume_source_run(
        self,
        run: Stage3Run | None,
        *,
        include_detail: bool = False,
    ) -> Stage3Run | None:
        if run is None:
            return None
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        materialized = self._materialized_resume_state(run)
        source_run_id = str(
            materialized.get("source_run_id")
            or runtime_stage3.get("resume_source_run_id")
            or ""
        ).strip()
        if not source_run_id or source_run_id == str(run.id or ""):
            return None
        try:
            return self.get_run(source_run_id, include_detail=include_detail)
        except ValueError:
            return None

    def _serialized_run_usage_metrics(
        self,
        run: Stage3Run,
        *,
        _seen_run_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        seen_run_ids = set(_seen_run_ids or set())
        run_id = str(run.id or "")
        own_duration_seconds = self._own_run_duration_seconds(run)
        token_usage_by_model = self._own_run_token_usage_by_model(run)
        if run_id in seen_run_ids:
            return {
                "duration_seconds": own_duration_seconds,
                "token_usage_by_model": token_usage_by_model,
            }
        seen_run_ids.add(run_id)
        duration_seconds = own_duration_seconds if own_duration_seconds is not None else None
        inherited_usage = self._materialized_resume_usage_metrics(run)
        inherited_duration_seconds = inherited_usage.get("duration_seconds")
        inherited_token_usage = dict(inherited_usage.get("token_usage_by_model") or {})
        if inherited_duration_seconds is None or not inherited_token_usage:
            source_run = self._resume_source_run(run, include_detail=False)
            if source_run is not None:
                source_usage = self._serialized_run_usage_metrics(
                    source_run,
                    _seen_run_ids=seen_run_ids,
                )
                if inherited_duration_seconds is None:
                    inherited_duration_seconds = source_usage.get("duration_seconds")
                if not inherited_token_usage:
                    inherited_token_usage = dict(source_usage.get("token_usage_by_model") or {})
        if inherited_duration_seconds is not None:
            duration_seconds = float(inherited_duration_seconds) + float(duration_seconds or 0.0)
        token_usage_by_model = aggregate_token_usage_by_model(
            [
                *token_usage_by_model.items(),
                *inherited_token_usage.items(),
            ],
            preferred_models=[self._runtime_token_usage_model(run)],
        )
        return {
            "duration_seconds": duration_seconds,
            "token_usage_by_model": token_usage_by_model,
        }

    def _own_run_duration_seconds(self, run: Stage3Run) -> float | None:
        if not run.started_at:
            return None
        started_at = self._as_utc_datetime(run.started_at)
        end_time = self._as_utc_datetime(run.finished_at) if run.finished_at else datetime.now(UTC)
        return max((end_time - started_at).total_seconds(), 0.0)

    def _as_utc_datetime(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _own_run_token_usage_by_model(self, run: Stage3Run) -> dict[str, int]:
        summary = dict(run.summary_json or {})
        try:
            tokens = max(int(summary.get("breaker_token_usage") or 0), 0)
        except (TypeError, ValueError):
            tokens = 0
        if tokens <= 0:
            return {}
        model = str(summary.get("breaker_model") or "").strip()
        if model.lower() in {"", "unknown", "unknown_model"}:
            runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
            runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
            model = str(((runtime_snapshot.get("breaker") or {}).get("model")) or "").strip()
        return aggregate_token_usage_by_model(
            [(model, tokens)],
            preferred_models=[self._runtime_token_usage_model(run)],
        )

    def _runtime_token_usage_model(self, run: Stage3Run) -> str:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
        return str(((runtime_snapshot.get("breaker") or {}).get("model")) or "").strip()

    def _serialize_stage3_file_result(self, row: Any) -> dict[str, Any]:
        return {
            "id": getattr(row, "id", None),
            "test_file_path": str(getattr(row, "test_file_path", "") or ""),
            "target_selector": _target_selector_for(row),
            "is_entry_file": bool(getattr(row, "is_entry_file", False)),
            "status": str(getattr(row, "status", "") or ""),
            "total_tests": int(getattr(row, "total_tests", 0) or 0),
            "passed_tests": int(getattr(row, "passed_tests", 0) or 0),
            "failed_tests": int(getattr(row, "failed_tests", 0) or 0),
            "error_tests": int(getattr(row, "error_tests", 0) or 0),
            "skipped_tests": int(getattr(row, "skipped_tests", 0) or 0),
            "pass_rate": float(getattr(row, "pass_rate", 0.0) or 0.0),
            "raw_result": dict(getattr(row, "raw_result_json", {}) or {}),
            "created_at": serialize_datetime(getattr(row, "created_at", None)),
        }

    def _draft_savepoint_payload(self, run: Stage3Run | None) -> dict[str, Any] | None:
        if run is None:
            return None
        summary = dict(run.summary_json or {})
        payload = summary.get("draft_savepoint")
        if not isinstance(payload, dict):
            return None
        normalized = dict(payload)
        depth = int(normalized.get("depth") or (self._effective_latest_depth(run) + 1) or 1)
        created_at = str(normalized.get("created_at") or serialize_datetime(run.updated_at or run.created_at))
        updated_at = str(normalized.get("updated_at") or created_at)
        return {
            "depth": depth,
            "label": str(normalized.get("label") or f"深度 {depth} + 临时版本"),
            "gold_patch_text": str(normalized.get("gold_patch_text") or ""),
            "feedback": dict(normalized.get("feedback") or {}),
            "collateral": dict(normalized.get("collateral") or {}),
            "file_results": [
                dict(item)
                for item in list(normalized.get("file_results") or [])
                if isinstance(item, dict)
            ],
            "created_at": created_at,
            "updated_at": updated_at,
            "source": "draft",
        }

    def _persist_draft_savepoint_assets(self, run: Stage3Run, payload: dict[str, Any] | None) -> None:
        manifest = self._workspace_manifest_for_run(run)
        draft_dir = Path(manifest["savepoint_dir"]) / "_draft"
        if not isinstance(payload, dict):
            if draft_dir.exists():
                shutil.rmtree(draft_dir)
            return

        draft_dir.mkdir(parents=True, exist_ok=True)

        def write_text_if_present(path: Path, value: str) -> None:
            if value:
                path.write_text(value, encoding="utf-8")
            else:
                path.unlink(missing_ok=True)

        def write_json_if_present(path: Path, value: Any) -> None:
            if value and (not isinstance(value, dict) or len(value) > 0):
                path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            elif isinstance(value, list) and value:
                path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                path.unlink(missing_ok=True)

        write_text_if_present(draft_dir / "gold.patch", str(payload.get("gold_patch_text") or ""))
        write_json_if_present(draft_dir / "feedback.json", dict(payload.get("feedback") or {}))
        write_json_if_present(draft_dir / "collateral.json", dict(payload.get("collateral") or {}))
        write_json_if_present(draft_dir / "file_results.json", list(payload.get("file_results") or []))

    def _update_draft_savepoint(
        self,
        run: Stage3Run,
        *,
        depth: int,
        gold_patch_text: Any = _STAGE3_UNSET,
        feedback: Any = _STAGE3_UNSET,
        collateral: Any = _STAGE3_UNSET,
        file_results: Any = _STAGE3_UNSET,
    ) -> dict[str, Any]:
        summary = dict(run.summary_json or {})
        existing = self._draft_savepoint_payload(run) or {}
        created_at = str(existing.get("created_at") or serialize_datetime(datetime.now(UTC)))
        payload: dict[str, Any] = {
            "depth": int(depth or 0),
            "label": f"深度 {int(depth or 0)} + 临时版本",
            "gold_patch_text": str(existing.get("gold_patch_text") or ""),
            "feedback": dict(existing.get("feedback") or {}),
            "collateral": dict(existing.get("collateral") or {}),
            "file_results": [
                dict(item)
                for item in list(existing.get("file_results") or [])
                if isinstance(item, dict)
            ],
            "created_at": created_at,
            "updated_at": serialize_datetime(datetime.now(UTC)),
            "source": "draft",
        }
        if gold_patch_text is not _STAGE3_UNSET:
            payload["gold_patch_text"] = str(gold_patch_text or "")
        if feedback is not _STAGE3_UNSET:
            payload["feedback"] = dict(feedback or {})
        if collateral is not _STAGE3_UNSET:
            payload["collateral"] = dict(collateral or {})
        if file_results is not _STAGE3_UNSET:
            payload["file_results"] = [
                self._serialize_stage3_file_result(item)
                for item in list(file_results or [])
            ]
        summary["draft_savepoint"] = payload
        run.summary_json = summary
        self._persist_draft_savepoint_assets(run, payload)
        self.session.flush()
        return payload

    def _clear_draft_savepoint(self, run: Stage3Run) -> None:
        summary = dict(run.summary_json or {})
        if "draft_savepoint" in summary:
            summary.pop("draft_savepoint", None)
            run.summary_json = summary
        self._persist_draft_savepoint_assets(run, None)
        self.session.flush()

    def _restore_run_after_savepoint_archive_failure(
        self,
        run: Stage3Run,
        *,
        depth: int,
        checkpoint: dict[str, Any] | None,
        error: Exception,
    ) -> None:
        cleanup_archive = self._partial_savepoint_cleanup_archive(
            run,
            depth=depth,
            checkpoint=checkpoint,
        )
        cleanup_payload = self._cleanup_partial_savepoint_archive_artifacts(
            cleanup_archive,
        )
        if _cleanup_result_has_failures(cleanup_payload):
            self.upsert_cleanup_tombstone(
                cleanup_archive,
                reason="savepoint_archive_failure",
            )
            self.record_cleanup_tombstone_result(
                run.id,
                cleanup_result=cleanup_payload,
            )
        summary = dict(run.summary_json or {})
        summary.pop("draft_savepoint", None)
        run.summary_json = summary
        run.phase = "breaker_running"
        run.error_message = None
        self._persist_draft_savepoint_assets(run, None)
        run.events.append(
            self._make_run_event(
                actor="system",
                phase="savepoint",
                title="Savepoint archiving failed",
                message=str(error),
                payload={
                    "depth": int(depth or 0),
                    "error_type": type(error).__name__,
                    **cleanup_payload,
                },
            )
        )
        self.session.flush()

    def _partial_savepoint_cleanup_archive(
        self,
        run: Stage3Run,
        *,
        depth: int,
        checkpoint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        manifest = self._workspace_manifest_for_run(run)
        checkpoint_payload = dict(checkpoint or {})
        checkpoint_root = (
            Path(str(checkpoint_payload.get("workspace_snapshot_path") or "")).expanduser().parent
            if checkpoint_payload.get("workspace_snapshot_path")
            else Path(manifest["checkpoint_dir"]) / f"depth-{int(depth or 0):03d}"
        )
        savepoint_root = Path(manifest["savepoint_dir"]) / f"depth-{int(depth or 0):03d}"
        image_ref = str(checkpoint_payload.get("docker_image_ref") or "").strip()
        return {
            "run_id": run.id,
            "repository_id": run.entry_file.snapshot.repository_id,
            "cleanup_scope": "partial_savepoint_archive_assets",
            "checkpoint_paths": [str(checkpoint_root)],
            "savepoint_paths": [str(savepoint_root)],
            "checkpoint_docker_image_refs": [image_ref] if image_ref else [],
        }

    def _cleanup_partial_savepoint_archive_artifacts(
        self,
        cleanup_archive: dict[str, Any],
    ) -> dict[str, list[str]]:
        cleanup_paths = [
            *list(cleanup_archive.get("checkpoint_paths") or []),
            *list(cleanup_archive.get("savepoint_paths") or []),
        ]
        failed_paths = list(
            _remove_paths(
                cleanup_paths,
                root_dir=self.workspace_root,
            )
            or []
        )
        failed_image_refs = list(
            _remove_docker_images(list(cleanup_archive.get("checkpoint_docker_image_refs") or [])) or []
        )
        return {
            "failed_paths": failed_paths,
            "failed_checkpoint_docker_image_refs": failed_image_refs,
        }

    def _serialize_savepoint_payload(self, savepoint: Stage3Savepoint) -> dict[str, Any]:
        summary = dict(savepoint.summary_json or {})
        checkpoint = dict(savepoint.checkpoint_json or {})
        summary["diff_stats"] = _stage3_diff_stats_from_patch_text(savepoint.gold_patch_text)
        can_resume_checkpoint = self._can_resume_checkpoint_payload(checkpoint)
        if can_resume_checkpoint:
            checkpoint_status = "ready"
        else:
            summary_checkpoint_status = str(summary.get("checkpoint_status") or "").strip()
            if summary_checkpoint_status in {"pending", "cleaned", "service_snapshot_only"}:
                checkpoint_status = summary_checkpoint_status
            elif checkpoint:
                checkpoint_status = "unusable"
            else:
                checkpoint_status = "service_snapshot_only"
        if not checkpoint_status:
            checkpoint_status = "service_snapshot_only"
        return {
            "id": savepoint.id,
            "depth": savepoint.depth,
            "entry_pass_rate": savepoint.entry_pass_rate,
            "p2p_files": list(savepoint.p2p_files_json or []),
            "f2p_files": list(savepoint.f2p_files_json or []),
            "collateral": dict(savepoint.collateral_json or {}),
            "gold_patch_text": savepoint.gold_patch_text,
            "checkpoint": checkpoint,
            "checkpoint_status": checkpoint_status,
            "reusable_checkpoint_ready": can_resume_checkpoint,
            "feedback": dict(summary.get("feedback") or {}),
            "summary": summary,
            "file_results": [self._serialize_stage3_file_result(row) for row in list(savepoint.file_results or [])],
            "created_at": serialize_datetime(savepoint.created_at),
            "updated_at": serialize_datetime(savepoint.updated_at),
            "source": "local",
        }

    def _effective_savepoint_payloads(self, run: Stage3Run | None) -> list[dict[str, Any]]:
        if run is None:
            return []
        inherited = list(self._materialized_resume_state(run).get("savepoints") or [])
        payloads = [
            self._materialized_resume_savepoint_payload(item)
            for item in inherited
            if isinstance(item, dict)
        ]
        payloads.extend(self._serialize_savepoint_payload(savepoint) for savepoint in list(run.savepoints or []))
        payloads.sort(key=lambda item: (int(item.get("depth") or 0), str(item.get("created_at") or "")))
        return payloads

    def _max_savepoint_depth(self, savepoints: list[dict[str, Any]]) -> int:
        return max((int(item.get("depth") or 0) for item in savepoints if isinstance(item, dict)), default=0)

    def _effective_latest_depth(self, run: Stage3Run | None) -> int:
        if run is None:
            return 0
        summary_depth = int((dict(run.summary_json or {})).get("latest_depth") or 0)
        return max(summary_depth, self._max_savepoint_depth(self._effective_savepoint_payloads(run)))

    def _effective_savepoint_count(self, run: Stage3Run | None) -> int:
        return len(self._effective_savepoint_identity_keys(run))

    def _latest_effective_savepoint(self, run: Stage3Run | None) -> dict[str, Any] | None:
        payloads = self._effective_savepoint_payloads(run)
        return payloads[-1] if payloads else None

    def _latest_effective_entry_pass_rate(self, run: Stage3Run | None) -> float | None:
        latest_savepoint = self._latest_effective_savepoint(run)
        if latest_savepoint is None:
            return None
        try:
            return float(latest_savepoint.get("entry_pass_rate") or 0.0)
        except (TypeError, ValueError):
            return None

    def _entry_pass_rate_ceiling_for_run(self, run: Stage3Run | None) -> float:
        if run is None:
            return float(self.settings.stage3_entry_pass_rate_ceiling)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
        hyperparameters = dict(runtime_snapshot.get("hyperparameters") or {})
        return _stage3_entry_pass_rate_ceiling(
            hyperparameters.get("entry_pass_rate_ceiling"),
            default=float(self.settings.stage3_entry_pass_rate_ceiling),
        )

    def _min_removed_code_lines_for_run(self, run: Stage3Run | None) -> int:
        if run is None:
            return int(self.settings.stage3_min_removed_code_lines)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_snapshot = extract_stage3_runtime_snapshot(dict(runtime_stage3.get("runtime") or {}))
        hyperparameters = dict(runtime_snapshot.get("hyperparameters") or {})
        return _stage3_min_removed_code_lines(
            hyperparameters.get("min_removed_code_lines"),
            default=int(self.settings.stage3_min_removed_code_lines),
        )

    def _latest_resume_checkpoint(self, run: Stage3Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        checkpoint = dict(runtime_stage3.get("resume_checkpoint") or {})
        if checkpoint:
            return checkpoint
        if run.savepoints:
            checkpoint = dict(run.savepoints[-1].checkpoint_json or {})
            if checkpoint:
                return checkpoint
        return {}

    def _latest_reusable_resume_checkpoint(self, run: Stage3Run | None) -> dict[str, Any]:
        if not self.settings.stage3_enable_checkpoints:
            return {}
        if run is None:
            return {}
        candidates: list[dict[str, Any]] = []
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_checkpoint = dict(runtime_stage3.get("resume_checkpoint") or {})
        if runtime_checkpoint:
            candidates.append(runtime_checkpoint)
        for savepoint in reversed(list(run.savepoints or [])):
            checkpoint = dict(savepoint.checkpoint_json or {})
            if checkpoint:
                candidates.append(checkpoint)
        for checkpoint in candidates:
            if self._can_resume_checkpoint_payload(checkpoint):
                return checkpoint
        return {}

    def _can_resume_checkpoint_payload(self, checkpoint: dict[str, Any] | None) -> bool:
        if not self.settings.stage3_enable_checkpoints:
            return False
        checkpoint = dict(checkpoint or {})
        workspace_snapshot_path = str(checkpoint.get("workspace_snapshot_path") or "").strip()
        conversation_id = str(checkpoint.get("conversation_id") or "").strip()
        openhands = dict(checkpoint.get("openhands") or {})
        conversations_path = str(openhands.get("conversations_path") or "").strip()
        bash_events_dir = str(openhands.get("bash_events_dir") or "").strip()
        docker_commit_status = str(checkpoint.get("docker_commit_status") or "").strip()
        docker_image_ref = str(checkpoint.get("docker_image_ref") or "").strip()
        try:
            conversation_dir_name = uuid.UUID(conversation_id).hex
        except ValueError:
            return False
        if (
            not workspace_snapshot_path
            or not conversation_id
            or not conversations_path
            or not bash_events_dir
            or docker_commit_status != "saved"
            or not docker_image_ref
        ):
            return False
        if not Path(workspace_snapshot_path).expanduser().is_file():
            return False
        conversations_dir = Path(conversations_path).expanduser()
        if not conversations_dir.exists():
            return False
        if not (conversations_dir / conversation_dir_name).exists():
            return False
        if not Path(bash_events_dir).expanduser().exists():
            return False
        return self._docker_image_exists(docker_image_ref)

    def _docker_image_exists(self, image_ref: str) -> bool:
        normalized_ref = str(image_ref or "").strip()
        if not normalized_ref:
            return False
        cached = self._docker_image_exists_cache.get(normalized_ref)
        if cached is not None:
            return cached
        exists = _docker_image_exists(normalized_ref)
        self._docker_image_exists_cache[normalized_ref] = exists
        return exists

    def _workspace_manifest_for_run(self, run: Stage3Run) -> dict[str, Any]:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        manifest = dict(runtime_stage3.get("workspace") or {})
        if manifest.get("workspace_path"):
            return self._normalize_run_workspace_manifest(run, manifest)
        workspace_dir = self._workspace_dir_for_run(run)
        manifest_path = workspace_dir / ".stage3" / "workspace_manifest.json"
        if manifest_path.is_file():
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return self._normalize_run_workspace_manifest(run, payload)
        return self._refresh_workspace_manifest(run)

    def _refresh_workspace_manifest(self, run: Stage3Run) -> dict[str, Any]:
        workspace_dir = self._workspace_dir_for_run(run)
        repo_dir = workspace_dir / "repo"
        metadata_dir = workspace_dir / ".stage3"
        assets_dir = self._assets_dir_for_workspace(workspace_dir)
        checkpoint_dir = metadata_dir / "checkpoints"
        savepoint_dir = metadata_dir / "savepoints"
        for directory in (workspace_dir, repo_dir, metadata_dir, assets_dir, checkpoint_dir, savepoint_dir):
            directory.mkdir(parents=True, exist_ok=True)
        generated_files: list[str] = []
        if assets_dir.exists():
            for path in sorted(assets_dir.rglob("*")):
                if path.is_file():
                    generated_files.append(str(path.relative_to(workspace_dir)))
        manifest = {
            "workspace_path": str(workspace_dir),
            "repo_dir": str(repo_dir),
            "metadata_dir": str(metadata_dir),
            "assets_dir": str(assets_dir),
            "checkpoint_dir": str(checkpoint_dir),
            "savepoint_dir": str(savepoint_dir),
            "generated_files": generated_files + [str((metadata_dir / "workspace_manifest.json").relative_to(workspace_dir))],
            "generated_file_count": len(generated_files) + 1,
            "prepared_at": serialize_datetime(datetime.now(UTC)),
        }
        manifest_path = metadata_dir / "workspace_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["workspace"] = manifest
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        run.workspace_path = str(workspace_dir)
        return manifest

    def _normalize_run_workspace_manifest(self, run: Stage3Run, manifest: dict[str, Any]) -> dict[str, Any]:
        workspace_dir = self._workspace_dir_for_run(run)
        metadata_dir = workspace_dir / ".stage3"
        normalized = {
            **manifest,
            "workspace_path": str(workspace_dir),
            "repo_dir": str(workspace_dir / "repo"),
            "metadata_dir": str(metadata_dir),
            "assets_dir": str(self._assets_dir_for_workspace(workspace_dir)),
            "checkpoint_dir": str(metadata_dir / "checkpoints"),
            "savepoint_dir": str(metadata_dir / "savepoints"),
        }
        run.workspace_path = str(workspace_dir)
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        runtime_stage3 = dict(runtime_snapshot.get("stage3") or {})
        runtime_stage3["workspace"] = normalized
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage3": runtime_stage3,
        }
        return normalized

    def record_event(
        self,
        run_id: str,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ):
        from feature_factory.models import Stage3RunEvent

        row = Stage3RunEvent(
            run_id=run_id,
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload_json=dict(payload or {}),
        )
        self.session.add(row)
        self.session.flush()
        return row

    def _record_savepoint_evaluation_event(
        self,
        run_id: str,
        *,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        row_payload = {
            "run_id": run_id,
            "actor": "validator",
            "phase": "savepoint",
            "title": title,
            "message": message,
            "payload_json": dict(payload or {}),
        }
        try:
            with Session(bind=self.session.get_bind()) as event_session:
                event_session.add(Stage3RunEvent(**row_payload))
                event_session.commit()
        except Exception:
            self.session.add(Stage3RunEvent(**row_payload))

    def mark_run_failed(
        self,
        run_id: str,
        *,
        error_message: str,
        result: str = Stage3RunResult.failed.value,
        phase: str = "failed",
        summary_updates: dict[str, Any] | None = None,
    ) -> Stage3Run:
        run = self.get_run(run_id, include_detail=False)
        run.status = Stage3RunStatus.completed.value
        run.result = result
        run.phase = phase
        run.finished_at = datetime.now(UTC)
        run.error_message = error_message
        if summary_updates:
            run.summary_json = {
                **dict(run.summary_json or {}),
                **dict(summary_updates),
            }
        self.session.flush()
        return run

    def update_run_progress(
        self,
        run_id: str,
        *,
        phase: str | None = None,
        summary_updates: dict[str, Any] | None = None,
    ) -> Stage3Run:
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

    def store_runtime_checkpoint(self, run_id: str, *, checkpoint: dict[str, Any]) -> Stage3Run:
        run = self.get_run(run_id)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["resume_checkpoint"] = dict(checkpoint or {})
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        self.session.flush()
        return run

    def mark_run_completed(
        self,
        run_id: str,
        *,
        result: str = Stage3RunResult.archived.value,
        summary_updates: dict[str, Any] | None = None,
    ) -> Stage3Run:
        run = self.get_run(run_id, include_detail=True)
        effective_savepoint_count = self._effective_savepoint_count(run)
        normalized_result = str(result or Stage3RunResult.archived.value)
        latest_entry_pass_rate = self._latest_effective_entry_pass_rate(run)
        entry_pass_rate_ceiling = self._entry_pass_rate_ceiling_for_run(run)
        merged_summary = {
            **dict(run.summary_json or {}),
            **dict(summary_updates or {}),
            "savepoint_count": effective_savepoint_count,
            "latest_depth": self._effective_latest_depth(run),
            "entry_pass_rate_ceiling": entry_pass_rate_ceiling,
        }
        if latest_entry_pass_rate is not None:
            merged_summary["latest_entry_pass_rate"] = latest_entry_pass_rate
        merged_summary["entry_pass_rate_ceiling_met"] = (
            latest_entry_pass_rate is None or latest_entry_pass_rate <= entry_pass_rate_ceiling
        )
        run.status = Stage3RunStatus.completed.value
        run.finished_at = datetime.now(UTC)
        if (
            normalized_result == Stage3RunResult.archived.value
            and effective_savepoint_count <= 0
        ):
            run.result = Stage3RunResult.failed.value
            run.phase = "failed"
            run.error_message = "stage3 run completed without producing any data"
            merged_summary["runner_status"] = "failed"
            merged_summary["completion_rejected_reason"] = "NO_DATA_PRODUCED"
        else:
            run.result = normalized_result
            run.phase = "completed"
            run.error_message = None
            if (
                normalized_result == Stage3RunResult.archived.value
                and latest_entry_pass_rate is not None
                and latest_entry_pass_rate > entry_pass_rate_ceiling
            ):
                merged_summary["completion_notice"] = "ENTRY_PASS_RATE_THRESHOLD_UNMET"
        run.summary_json = merged_summary
        self.session.flush()
        return run

    def update_run_runtime_snapshot(
        self,
        run_id: str,
        *,
        runtime_snapshot: dict[str, Any],
    ) -> Stage3Run:
        run = self.get_run(run_id, include_detail=False)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = merge_stage3_runtime_snapshot(
            dict(runtime_stage3.get("runtime") or {}),
            runtime_snapshot,
        )
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        self.session.flush()
        return run

    def update_repository_run_runtime_snapshot(
        self,
        repository_id: int,
        run_id: str,
        *,
        runtime_snapshot: dict[str, Any],
    ) -> Stage3Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=False)
        if run.status in {Stage3RunStatus.queued.value, Stage3RunStatus.running.value}:
            raise Stage3RunConflictError(f"cannot modify runtime config for an active stage3 run: {run_id}")
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["runtime"] = merge_stage3_runtime_snapshot(
            dict(runtime_stage3.get("runtime") or {}),
            runtime_snapshot,
        )
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        self.session.flush()
        return run

    def store_savepoint_checkpoint(
        self,
        run_id: str,
        *,
        depth: int,
        checkpoint: dict[str, Any],
    ) -> Stage3Run:
        run = self.get_run(run_id)
        target = next(
            (
                savepoint
                for savepoint in list(run.savepoints or [])
                if int(savepoint.depth or 0) == int(depth or 0)
            ),
            None,
        )
        if target is None:
            raise ValueError(f"stage3 savepoint depth not found for run {run_id}: {depth}")
        cleanup_archive = self._superseded_savepoint_checkpoint_cleanup_archive(
            run,
            replacement_checkpoint=checkpoint,
        )
        for savepoint in list(run.savepoints or []):
            if savepoint.id == target.id:
                continue
            if savepoint.checkpoint_json:
                self._mark_savepoint_checkpoint_cleared(savepoint, status="cleaned")
        target.checkpoint_json = dict(checkpoint or {})
        summary = dict(target.summary_json or {})
        summary["checkpoint_status"] = "ready" if self._can_resume_checkpoint_payload(dict(checkpoint or {})) else "unusable"
        summary["reusable_checkpoint_ready"] = summary["checkpoint_status"] == "ready"
        target.summary_json = summary
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3["resume_checkpoint"] = dict(checkpoint or {})
        runtime_stage3["resume_checkpoint_status"] = summary["checkpoint_status"]
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        if cleanup_archive["checkpoint_paths"] or cleanup_archive["checkpoint_docker_image_refs"]:
            cleanup_payload = self._cleanup_breaker_checkpoint_archive(cleanup_archive)
            if _cleanup_result_has_failures(cleanup_payload):
                run.events.append(
                    self._make_run_event(
                        actor="system",
                        phase="checkpoint",
                        title="Superseded breaker checkpoint cleanup incomplete",
                        message=(
                            "Some older breaker checkpoint assets could not be removed "
                            "after storing a newer checkpoint"
                        ),
                        payload={
                            **cleanup_archive,
                            **cleanup_payload,
                            "retained_depth": int(depth or 0),
                        },
                    )
                )
        self.session.flush()
        return run

    def breaker_checkpoint_cleanup_targets(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id, include_detail=True)
        return {
            "checkpoint_paths": self._breaker_checkpoint_paths_for_run(run),
            "checkpoint_docker_image_refs": self._owned_checkpoint_docker_image_refs_for_run(run),
        }

    def upsert_breaker_checkpoint_cleanup_tombstone(
        self,
        run_id: str,
        *,
        checkpoint_paths: list[str] | None = None,
        checkpoint_docker_image_refs: list[str] | None = None,
        reason: str,
    ) -> Stage3CleanupTombstone | None:
        run = self.get_run(run_id, include_detail=True)
        archive = {
            "run_id": run.id,
            "repository_id": run.entry_file.snapshot.repository_id,
            "cleanup_scope": "breaker_checkpoint_assets",
            "checkpoint_paths": list(checkpoint_paths or []),
            "checkpoint_docker_image_refs": list(checkpoint_docker_image_refs or []),
        }
        return self.upsert_cleanup_tombstone(archive, reason=reason)

    def clear_breaker_checkpoints(self, run_id: str) -> Stage3Run:
        run = self.get_run(run_id, include_detail=True)
        for savepoint in list(run.savepoints or []):
            self._mark_savepoint_checkpoint_cleared(savepoint, status="cleaned")

        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        runtime_stage3 = dict(runtime_snapshot.get("stage3") or {})
        if "resume_checkpoint" in runtime_stage3:
            runtime_stage3["resume_checkpoint"] = {}
        runtime_stage3["resume_checkpoint_status"] = "cleaned"
        materialized = dict(runtime_stage3.get("resume_materialized") or {})
        materialized_savepoints = []
        for payload in list(materialized.get("savepoints") or []):
            if not isinstance(payload, dict):
                continue
            materialized_payload = dict(payload)
            materialized_payload["checkpoint"] = {}
            materialized_payload["checkpoint_status"] = "cleaned"
            materialized_payload["reusable_checkpoint_ready"] = False
            summary = dict(materialized_payload.get("summary") or {})
            summary["checkpoint_status"] = "cleaned"
            summary["reusable_checkpoint_ready"] = False
            materialized_payload["summary"] = summary
            materialized_savepoints.append(materialized_payload)
        if materialized_savepoints or "savepoints" in materialized:
            materialized["savepoints"] = materialized_savepoints
            runtime_stage3["resume_materialized"] = materialized
        run.runtime_snapshot_json = {
            **runtime_snapshot,
            "stage3": runtime_stage3,
        }
        self.session.flush()
        return run

    def _mark_savepoint_checkpoint_cleared(self, savepoint: Stage3Savepoint, *, status: str) -> None:
        if savepoint.checkpoint_json:
            savepoint.checkpoint_json = {}
        summary = dict(savepoint.summary_json or {})
        summary["checkpoint_status"] = str(status or "cleaned")
        summary["reusable_checkpoint_ready"] = False
        savepoint.summary_json = summary

    def _superseded_savepoint_checkpoint_cleanup_archive(
        self,
        run: Stage3Run,
        *,
        replacement_checkpoint: dict[str, Any] | None,
    ) -> dict[str, Any]:
        replacement_identity = _checkpoint_payload_identity(replacement_checkpoint)
        checkpoint_paths: list[str] = []
        checkpoint_image_refs: list[str] = []

        def add_path(path: str | Path) -> None:
            normalized = str(Path(str(path or "")).expanduser()) if str(path or "").strip() else ""
            if normalized and normalized not in checkpoint_paths:
                checkpoint_paths.append(normalized)

        def add_checkpoint(checkpoint_payload: dict[str, Any] | None) -> None:
            payload = dict(checkpoint_payload or {})
            if not payload or _checkpoint_payload_identity(payload) == replacement_identity:
                return
            self._add_checkpoint_cleanup_path(payload, add_path=add_path)
            for image_ref in self._owned_checkpoint_docker_image_refs_from_payload(payload, run_id=run.id):
                if image_ref not in checkpoint_image_refs:
                    checkpoint_image_refs.append(image_ref)

        for savepoint in list(run.savepoints or []):
            add_checkpoint(dict(savepoint.checkpoint_json or {}))
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        add_checkpoint(dict(runtime_stage3.get("resume_checkpoint") or {}))
        return {
            "run_id": run.id,
            "repository_id": run.entry_file.snapshot.repository_id,
            "cleanup_scope": "breaker_checkpoint_assets",
            "checkpoint_paths": checkpoint_paths,
            "checkpoint_docker_image_refs": checkpoint_image_refs,
        }

    def _cleanup_breaker_checkpoint_archive(
        self,
        cleanup_archive: dict[str, Any],
    ) -> dict[str, list[str]]:
        checkpoint_paths = list(cleanup_archive.get("checkpoint_paths") or [])
        checkpoint_image_refs = list(cleanup_archive.get("checkpoint_docker_image_refs") or [])
        failed_paths = list(_remove_paths(checkpoint_paths, root_dir=self.workspace_root) or [])
        failed_checkpoint_docker_image_refs = list(_remove_docker_images(checkpoint_image_refs) or [])
        if checkpoint_image_refs:
            failed_image_set = set(failed_checkpoint_docker_image_refs)
            for image_ref in checkpoint_image_refs:
                if image_ref not in failed_image_set:
                    self._docker_image_exists_cache.pop(str(image_ref or "").strip(), None)
        return {
            "failed_paths": failed_paths,
            "failed_checkpoint_docker_image_refs": failed_checkpoint_docker_image_refs,
        }

    def _repository_cache_dir(self, repository: GitHubRepository) -> Path:
        return self._repository_materializer().repository_cache_dir(repository)

    def _repo_cache_lock(self, cache_dir: Path):
        return self._repository_materializer().repo_cache_lock(cache_dir)

    def _append_run_event(
        self,
        run: Stage3Run,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        run.events.append(
            self._make_run_event(
                actor=actor,
                phase=phase,
                title=title,
                message=message,
                payload=payload,
            )
        )

    def _emit_or_append_run_event(
        self,
        run: Stage3Run,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
        emit_event: Callable[..., None] | None = None,
    ) -> None:
        if emit_event is not None:
            emit_event(
                actor=actor,
                phase=phase,
                title=title,
                message=message,
                payload=dict(payload or {}),
            )
            return
        self._append_run_event(
            run,
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload=payload,
        )

    def _repository_cache_branch(self, repository: GitHubRepository) -> str | None:
        branch = str(repository.default_branch or "").strip()
        return branch or None

    def _git_config_value(self, cache_dir: Path, key: str) -> str | None:
        completed = self._run_command(
            ["git", f"--git-dir={cache_dir}", "config", "--get", key],
            timeout_seconds=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def _try_symbolic_ref(self, cache_dir: Path, revision: str) -> str | None:
        completed = self._run_command(
            ["git", f"--git-dir={cache_dir}", "symbolic-ref", "-q", revision],
            timeout_seconds=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def _try_rev_parse(self, cache_dir: Path, revision: str) -> str | None:
        completed = self._run_command(
            ["git", f"--git-dir={cache_dir}", "rev-parse", "--verify", revision],
            timeout_seconds=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def _repository_cache_rebuild_reason(self, cache_dir: Path, *, default_branch: str | None) -> str | None:
        mirror_flag = self._git_config_value(cache_dir, "remote.origin.mirror")
        if mirror_flag and mirror_flag.lower() == "true":
            return "legacy mirror cache"
        if default_branch:
            head_ref = self._try_symbolic_ref(cache_dir, "HEAD")
            expected_head_ref = f"refs/heads/{default_branch}"
            if head_ref and head_ref != expected_head_ref:
                return f"default branch changed to {default_branch}"
        return None

    def _run_command_with_git_progress(
        self,
        args: list[str],
        *,
        error_message: str,
        run: Stage3Run,
        phase: str | None,
        progress_title: str,
        progress_payload: dict[str, Any],
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        _raise_if_cancel_requested(cancel_requested)
        process = subprocess.Popen(
            args,
            cwd=str(cwd) if cwd is not None else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=0,
        )
        if process.stdout is None:
            raise Stage3RunConflictError(f"{error_message}: failed to capture git output")

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
                raise Stage3RunConflictError("stage3 run interrupted")
            if timeout_seconds > 0 and (time.monotonic() - started_at) > timeout_seconds:
                process.kill()
                process.wait(timeout=5)
                raise Stage3RunConflictError(f"{error_message}: command timed out after {timeout_seconds:.0f}s")
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
            raise Stage3RunConflictError(f"{error_message}: {details}")
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
        run: Stage3Run,
        phase: str | None,
        progress_title: str,
        retry_title: str,
        progress_payload: dict[str, Any],
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        cleanup_after_failed_attempt=None,
        cwd: Path | None = None,
        timeout_seconds: float = 600.0,
    ) -> subprocess.CompletedProcess[str]:
        max_attempts = github_proxy_retry_attempts(args, minimum=_GIT_REMOTE_MAX_ATTEMPTS)
        for attempt in range(1, max_attempts + 1):
            _raise_if_cancel_requested(cancel_requested)
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
            except Stage3RunConflictError as exc:
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
                    _raise_if_cancel_requested(cancel_requested)
                    time.sleep(0.1)
        raise Stage3RunConflictError(error_message)

    def _create_repository_cache(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        default_branch: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        _raise_if_cancel_requested(cancel_requested)
        self._repository_materializer().create_repository_cache(
            run,
            repository=repository,
            cache_dir=cache_dir,
            default_branch=default_branch,
            phase="baseline_prepare",
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def _refresh_repository_cache(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        default_branch: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        _raise_if_cancel_requested(cancel_requested)
        self._repository_materializer().refresh_repository_cache(
            run,
            repository=repository,
            cache_dir=cache_dir,
            default_branch=default_branch,
            phase="baseline_prepare",
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def _sync_repository_cache(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        _raise_if_cancel_requested(cancel_requested)
        self._repository_materializer().sync_repository_cache(
            run,
            repository=repository,
            cache_dir=cache_dir,
            phase="baseline_prepare",
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def _resolve_cached_target_commit(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        requested_commit_sha: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        _raise_if_cancel_requested(cancel_requested)
        return self._repository_materializer().resolve_cached_target_commit(
            run,
            repository=repository,
            cache_dir=cache_dir,
            requested_commit_sha=requested_commit_sha,
            phase="baseline_prepare",
            emit_event=emit_event,
            cancel_requested=cancel_requested,
            try_rev_parse_fn=self._try_rev_parse,
            error_factory=Stage3RunConflictError,
        )

    def _materialize_checkout_from_cache(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        checkout_dir: Path,
        target_commit_sha: str,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        _raise_if_cancel_requested(cancel_requested)
        self._repository_materializer().materialize_checkout_from_cache(
            run,
            repository=repository,
            cache_dir=cache_dir,
            checkout_dir=checkout_dir,
            target_commit_sha=target_commit_sha,
            phase="baseline_prepare",
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )

    def _materialize_repository_checkout(
        self,
        run: Stage3Run,
        *,
        repository: GitHubRepository,
        target_commit_sha: str,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        _raise_if_cancel_requested(cancel_requested)
        target_commit_sha = str(target_commit_sha or "").strip()
        if not target_commit_sha:
            raise Stage3RunConflictError("stage3 checkout is missing the Stage2 source commit sha")
        manifest = self._workspace_manifest_for_run(run)
        repo_dir = Path(manifest["repo_dir"]).expanduser().resolve()
        if repo_dir.exists():
            shutil.rmtree(repo_dir)
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        cache_dir = self._repository_cache_dir(repository)
        with self._repo_cache_lock(cache_dir):
            _raise_if_cancel_requested(cancel_requested)
            self._sync_repository_cache(
                run,
                repository=repository,
                cache_dir=cache_dir,
                emit_event=emit_event,
                cancel_requested=cancel_requested,
            )
            resolved_target_commit_sha = self._resolve_cached_target_commit(
                run,
                repository=repository,
                cache_dir=cache_dir,
                requested_commit_sha=target_commit_sha,
                emit_event=emit_event,
                cancel_requested=cancel_requested,
            )
            self._materialize_checkout_from_cache(
                run,
                repository=repository,
                cache_dir=cache_dir,
                checkout_dir=repo_dir,
                target_commit_sha=resolved_target_commit_sha,
                emit_event=emit_event,
                cancel_requested=cancel_requested,
            )
        _raise_if_cancel_requested(cancel_requested)
        self._ensure_git_identity(repo_dir)
        _raise_if_cancel_requested(cancel_requested)
        resolved_head_commit_sha = self._git_output(repo_dir, ["rev-parse", "HEAD"])
        resolved_target_commit_sha = self._git_output(repo_dir, ["rev-parse", f"{target_commit_sha}^{{commit}}"])
        if resolved_head_commit_sha != resolved_target_commit_sha:
            raise Stage3RunConflictError(
                "stage3 checkout resolved to a different commit than the Stage2 source commit: "
                f"HEAD={resolved_head_commit_sha}, target={resolved_target_commit_sha}"
            )
        baseline_commit_sha = target_commit_sha
        clone_url = self._repository_clone_url(repository)
        refreshed_manifest = self._refresh_workspace_manifest(run)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        runtime_stage3.update(
            {
                "repository_clone_url": clone_url,
                "repository_cache_path": str(cache_dir),
                "target_commit_sha": target_commit_sha,
                "baseline_commit_sha": baseline_commit_sha,
                "resolved_head_commit_sha": resolved_head_commit_sha,
                "workspace": refreshed_manifest,
            }
        )
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage3": runtime_stage3,
        }
        return {
            "clone_url": clone_url,
            "target_commit_sha": target_commit_sha,
            "baseline_commit_sha": baseline_commit_sha,
            "resolved_head_commit_sha": resolved_head_commit_sha,
            "repo_dir": str(repo_dir),
        }

    def _restore_run_from_checkpoint(self, run: Stage3Run, *, checkpoint: dict[str, Any]) -> dict[str, Any]:
        source_snapshot_path = Path(str(checkpoint.get("workspace_snapshot_path") or "")).expanduser()
        if not source_snapshot_path.is_file():
            raise Stage3RunConflictError(f"stage3 resume checkpoint is missing snapshot: {source_snapshot_path}")

        workspace_dir = self._run_workspace_dir(run.id)
        workspace_dir.parent.mkdir(parents=True, exist_ok=True)
        temp_snapshot_path = workspace_dir.parent / f".resume-{run.id}.tar.gz"
        shutil.copy2(source_snapshot_path, temp_snapshot_path)
        try:
            if workspace_dir.exists():
                shutil.rmtree(workspace_dir)
            workspace_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(temp_snapshot_path, "r:gz") as archive:
                _safe_extract_tar(archive, workspace_dir)
            _make_container_writable(workspace_dir, recursive=True)

            refreshed_manifest = self._refresh_workspace_manifest(run)
            repo_dir = Path(refreshed_manifest["repo_dir"])
            runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
            source_commit_sha = str(runtime_stage3.get("source_commit_sha") or "").strip()
            if not source_commit_sha:
                source_commit_sha = str(getattr(run.entry_file.snapshot, "source_commit_sha", "") or "").strip()
            baseline_commit_sha = source_commit_sha or str(checkpoint.get("baseline_commit_sha") or "").strip()
            if not baseline_commit_sha and (repo_dir / ".git").exists():
                baseline_commit_sha = self._git_output(repo_dir, ["rev-parse", "HEAD"])
            resume_checkpoint_root = Path(refreshed_manifest["checkpoint_dir"]) / "resume-current"
            if resume_checkpoint_root.exists():
                shutil.rmtree(resume_checkpoint_root)
            resume_checkpoint_root.mkdir(parents=True, exist_ok=True)
            restored_snapshot_path = resume_checkpoint_root / "workspace_snapshot.tar.gz"
            shutil.copy2(temp_snapshot_path, restored_snapshot_path)
            refreshed_checkpoint = {
                **dict(checkpoint),
                "workspace_snapshot_path": str(restored_snapshot_path),
                "workspace_path": str(workspace_dir),
                "repo_dir": str(repo_dir),
                "baseline_commit_sha": baseline_commit_sha,
                "restored_at": serialize_datetime(datetime.now(UTC)),
            }
            runtime_stage3.update(
                {
                    "baseline_commit_sha": baseline_commit_sha,
                    "workspace": refreshed_manifest,
                    "resume_checkpoint": refreshed_checkpoint,
                }
            )
            run.runtime_snapshot_json = {
                **dict(run.runtime_snapshot_json or {}),
                "stage3": runtime_stage3,
            }
            return {
                "workspace_snapshot_path": str(restored_snapshot_path),
                "baseline_commit_sha": baseline_commit_sha,
                "repo_dir": str(repo_dir),
            }
        finally:
            if temp_snapshot_path.exists():
                temp_snapshot_path.unlink()

    def _create_checkpoint_snapshot(self, run: Stage3Run, *, depth: int) -> dict[str, Any]:
        manifest = self._workspace_manifest_for_run(run)
        workspace_dir = Path(manifest["workspace_path"])
        checkpoint_root = Path(manifest["checkpoint_dir"]) / f"depth-{depth:03d}"
        if checkpoint_root.exists():
            shutil.rmtree(checkpoint_root)
        checkpoint_root.mkdir(parents=True, exist_ok=True)
        snapshot_path = checkpoint_root / "workspace_snapshot.tar.gz"
        self._write_workspace_snapshot(workspace_dir=workspace_dir, destination_path=snapshot_path)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        checkpoint = {
            "run_id": run.id,
            "depth": depth,
            "workspace_snapshot_path": str(snapshot_path),
            "workspace_path": str(workspace_dir),
            "repo_dir": manifest["repo_dir"],
            "baseline_commit_sha": str(runtime_stage3.get("baseline_commit_sha") or ""),
            "created_at": serialize_datetime(datetime.now(UTC)),
        }
        (checkpoint_root / "checkpoint.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return checkpoint

    def _clone_checkpoint_for_resume_run(
        self,
        *,
        source_run: Stage3Run,
        destination_run: Stage3Run,
        checkpoint: dict[str, Any],
    ) -> dict[str, Any]:
        source_snapshot_path = Path(str(checkpoint.get("workspace_snapshot_path") or "")).expanduser()
        if not source_snapshot_path.is_file():
            raise Stage3RunConflictError(f"stage3 resume checkpoint is missing snapshot: {source_snapshot_path}")
        destination_checkpoint_root = self._run_runtime_dir(destination_run.id) / "breaker-checkpoints" / "resume"
        destination_checkpoint_root.mkdir(parents=True, exist_ok=True)
        destination_snapshot_path = destination_checkpoint_root / "workspace_snapshot.tar.gz"
        shutil.copy2(source_snapshot_path, destination_snapshot_path)
        cloned = dict(checkpoint)
        cloned["checkpoint_dir"] = str(destination_checkpoint_root)
        cloned["workspace_snapshot_path"] = str(destination_snapshot_path)
        cloned["run_id"] = destination_run.id
        cloned["cloned_from_run_id"] = source_run.id
        cloned["cloned_for_run_id"] = destination_run.id
        cloned["cloned_at"] = serialize_datetime(datetime.now(UTC))
        cloned["openhands"] = self._clone_checkpoint_openhands_paths(
            checkpoint=checkpoint,
            destination_checkpoint_root=destination_checkpoint_root,
        )
        cloned_image_ref = self._clone_checkpoint_docker_image(
            str(checkpoint.get("docker_image_ref") or ""),
            destination_run_id=destination_run.id,
            depth=int(checkpoint.get("depth") or 0),
        )
        if cloned_image_ref:
            cloned["docker_image_ref"] = cloned_image_ref
            docker_commit = dict(cloned.get("docker_commit") or {})
            docker_commit["docker_image_ref"] = cloned_image_ref
            docker_commit["cloned_from_image_ref"] = str(checkpoint.get("docker_image_ref") or "")
            cloned["docker_commit"] = docker_commit
        return cloned

    def _clone_checkpoint_openhands_paths(
        self,
        *,
        checkpoint: dict[str, Any],
        destination_checkpoint_root: Path,
    ) -> dict[str, Any]:
        source = dict(checkpoint.get("openhands") or {})
        cloned = dict(source)
        path_specs = (
            ("conversations_path", destination_checkpoint_root / "openhands" / "conversations"),
            ("bash_events_dir", destination_checkpoint_root / "openhands" / "bash_events"),
        )
        for key, destination in path_specs:
            source_path_raw = str(source.get(key) or "").strip()
            if not source_path_raw:
                continue
            source_path = Path(source_path_raw).expanduser()
            if not source_path.exists():
                continue
            if destination.exists():
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source_path, destination)
            cloned[key] = str(destination)
        return cloned

    def _clone_checkpoint_docker_image(
        self,
        source_image_ref: str,
        *,
        destination_run_id: str,
        depth: int,
    ) -> str:
        source_image_ref = str(source_image_ref or "").strip()
        if not source_image_ref:
            raise Stage3RunConflictError("resume checkpoint is missing docker_image_ref")
        if not self._docker_image_exists(source_image_ref):
            raise Stage3RunConflictError(
                f"resume checkpoint docker image is unavailable: {source_image_ref}"
            )
        try:
            return _retag_checkpoint_image(
                source_image_ref=source_image_ref,
                destination_run_id=destination_run_id,
                depth=depth,
            )
        except RuntimeError as exc:
            raise Stage3RunConflictError(str(exc)) from exc

    def _write_workspace_snapshot(self, *, workspace_dir: Path, destination_path: Path) -> None:
        def _filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
            name = str(member.name or "").lstrip("./")
            if name.startswith(".stage3/checkpoints") or name.startswith(".stage3/savepoints"):
                return None
            return member

        with tarfile.open(destination_path, "w:gz") as archive:
            archive.add(str(workspace_dir), arcname=".", filter=_filter)

    def _ensure_git_identity(self, repo_dir: Path) -> None:
        self._run_checked_command(
            ["git", "config", "user.name", "FeatureFactory Stage3"],
            cwd=repo_dir,
            timeout_seconds=30.0,
            error_message="failed to configure stage3 git user.name",
        )
        self._run_checked_command(
            ["git", "config", "user.email", "stage3@featurefactory.local"],
            cwd=repo_dir,
            timeout_seconds=30.0,
            error_message="failed to configure stage3 git user.email",
        )

    def _commit_stage3_workspace(self, run: Stage3Run, *, repo_dir: Path) -> dict[str, Any]:
        self._run_checked_command(
            ["git", "add", "-A"],
            cwd=repo_dir,
            timeout_seconds=60.0,
            error_message="failed to stage stage3 workspace changes",
        )
        status_output = self._git_output(repo_dir, ["status", "--porcelain"])
        if status_output.strip():
            self._run_checked_command(
                ["git", "commit", "-m", f"stage3 savepoint for run {run.id}"],
                cwd=repo_dir,
                timeout_seconds=120.0,
                error_message="failed to create stage3 savepoint commit",
            )
        return {
            "git_commit_sha": self._git_output(repo_dir, ["rev-parse", "HEAD"]),
        }

    def _gold_patch_text(self, run: Stage3Run, *, repo_dir: Path) -> str:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        baseline_commit_sha = str(runtime_stage3.get("baseline_commit_sha") or "").strip()
        if not baseline_commit_sha:
            return ""
        completed = self._run_command(
            [
                "git",
                "diff",
                "--binary",
                f"{baseline_commit_sha}..HEAD",
                *stage3_gold_patch_diff_pathspec_args(),
            ],
            cwd=repo_dir,
            timeout_seconds=120.0,
        )
        if completed.returncode != 0:
            raise Stage3RunConflictError(
                completed.stderr.strip() or "failed to compute stage3 gold patch"
            )
        return str(completed.stdout or "")

    def _live_gold_patch_text(self, run: Stage3Run, *, repo_dir: Path) -> str:
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        baseline_commit_sha = str(runtime_stage3.get("baseline_commit_sha") or "").strip()
        if not baseline_commit_sha:
            return ""

        with tempfile.TemporaryDirectory(prefix="stage3-gold-patch-index-") as tmp_dir:
            env = os.environ.copy()
            env["GIT_INDEX_FILE"] = str(Path(tmp_dir) / "index")

            def run_git(args: list[str], *, error_message: str) -> subprocess.CompletedProcess[str]:
                completed = subprocess.run(
                    ["git", *args],
                    cwd=str(repo_dir),
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120.0,
                )
                if completed.returncode != 0:
                    detail = str(completed.stderr or completed.stdout or "").strip()
                    raise Stage3RunConflictError(f"{error_message}: {detail or 'unknown error'}")
                return completed

            run_git(["read-tree", "HEAD"], error_message="failed to prepare stage3 live patch index")
            run_git(["add", "-A"], error_message="failed to stage stage3 live patch changes")
            completed = run_git(
                [
                    "diff",
                    "--cached",
                    "--binary",
                    baseline_commit_sha,
                    *stage3_gold_patch_diff_pathspec_args(),
                ],
                error_message="failed to compute stage3 live gold patch",
            )
            return str(completed.stdout or "")

    def _savepoint_feedback(
        self,
        *,
        run: Stage3Run,
        entry_file: Stage3EntryFile,
        evaluation: list[Any],
        previous_pass_rate: float,
    ) -> dict[str, Any]:
        results_by_path = {row.test_file_path: row for row in evaluation}
        entry_result = results_by_path.get(entry_file.test_file_path)
        p2p_files = [row.test_file_path for row in evaluation if row.status == "passed"]
        f2p_files = [row.test_file_path for row in evaluation if row.status != "passed"]
        if entry_result is None:
            return {
                "accepted": False,
                "code": "ENTRY_RESULT_MISSING",
                "message": (
                    "The original P2P evaluation did not include a result for the entry file, "
                    "so the savepoint cannot be created."
                ),
                "entry_file_path": entry_file.test_file_path,
                "entry_pass_rate": 0.0,
                "previous_entry_pass_rate": previous_pass_rate,
                "entry_status": "missing",
                "p2p_files": p2p_files,
                "f2p_files": f2p_files,
                "p2p_count": len(p2p_files),
                "f2p_count": len(f2p_files),
            }
        entry_status = str(entry_result.status or "").strip() or "unknown"
        entry_pass_rate = float(entry_result.pass_rate or 0.0)
        accepted = (
            entry_status == "failed"
            and int(entry_result.total_tests or 0) > 0
            and int(entry_result.failed_tests or 0) > 0
            and int(entry_result.error_tests or 0) == 0
            and entry_pass_rate < float(previous_pass_rate)
        )
        if accepted:
            code = "SAVEPOINT_ACCEPTED"
            message = (
                f"The entry file {entry_file.test_file_path} pass rate dropped from "
                f"{previous_pass_rate:.3f} to {entry_result.pass_rate:.3f}; "
                "the savepoint is accepted."
            )
        elif entry_status == "passed":
            code = "ENTRY_STILL_PASSED"
            message = (
                f"The entry file {entry_file.test_file_path} still passes in the original "
                "P2P set, so the current savepoint does not satisfy the breakage condition."
            )
        elif entry_status != "failed":
            code = "ENTRY_INVALID_BREAKAGE"
            message = (
                f"The entry file {entry_file.test_file_path} currently has status "
                f"{entry_status}, which indicates a test execution failure, invalid output, "
                "or environment-level damage rather than a functional breakage."
            )
        elif (
            int(entry_result.total_tests or 0) <= 0
            or int(entry_result.failed_tests or 0) <= 0
            or int(entry_result.error_tests or 0) > 0
        ):
            code = "ENTRY_INVALID_BREAKAGE"
            message = (
                f"The entry file {entry_file.test_file_path} no longer passes, but the failure "
                "shape is not a functional failure: at least one test point must be collected, "
                "at least one test point must fail, and there must be no execution, import, "
                "or environment errors."
            )
        else:
            code = "ENTRY_NOT_DEEPER"
            message = (
                f"The entry file {entry_file.test_file_path} is already F2P, but its pass rate "
                f"{entry_result.pass_rate:.3f} is not lower than the previous accepted "
                f"savepoint pass rate {previous_pass_rate:.3f}."
            )
        return {
            "accepted": accepted,
            "code": code,
            "message": message,
            "entry_file_path": entry_file.test_file_path,
            "entry_pass_rate": entry_pass_rate,
            "previous_entry_pass_rate": previous_pass_rate,
            "entry_status": entry_status,
            "entry_total_tests": int(entry_result.total_tests or 0),
            "entry_passed_tests": int(entry_result.passed_tests or 0),
            "entry_failed_tests": int(entry_result.failed_tests or 0),
            "entry_error_tests": int(entry_result.error_tests or 0),
            "entry_skipped_tests": int(entry_result.skipped_tests or 0),
            "p2p_files": p2p_files,
            "f2p_files": f2p_files,
            "p2p_count": len(p2p_files),
            "f2p_count": len(f2p_files),
        }

    def _collateral_summary(
        self,
        evaluation: list[Any],
        *,
        entry_file_path: str,
    ) -> dict[str, Any]:
        degraded_files = []
        for row in evaluation:
            test_file_path = str(row.test_file_path or "")
            if test_file_path == entry_file_path or row.status == "passed":
                continue
            payload = {
                "test_file_path": test_file_path,
                "status": row.status,
                "pass_rate": row.pass_rate,
            }
            target_selector = str(getattr(row, "target_selector", None) or test_file_path)
            if target_selector != test_file_path:
                payload["target_selector"] = target_selector
            degraded_files.append(payload)
        return {
            "non_entry_failed_count": len(degraded_files),
            "non_entry_failed_files": degraded_files,
        }

    def _persist_savepoint_assets(
        self,
        run: Stage3Run,
        *,
        depth: int,
        gold_patch_text: str,
        checkpoint: dict[str, Any],
        evaluation: list[Any],
        feedback: dict[str, Any],
        collateral: dict[str, Any],
    ) -> None:
        manifest = self._workspace_manifest_for_run(run)
        savepoint_dir = Path(manifest["savepoint_dir"]) / f"depth-{depth:03d}"
        if savepoint_dir.exists():
            shutil.rmtree(savepoint_dir)
        savepoint_dir.mkdir(parents=True, exist_ok=True)
        (savepoint_dir / "gold.patch").write_text(gold_patch_text, encoding="utf-8")
        (savepoint_dir / "feedback.json").write_text(
            json.dumps(feedback, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (savepoint_dir / "collateral.json").write_text(
            json.dumps(collateral, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (savepoint_dir / "checkpoint.json").write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (savepoint_dir / "file_results.json").write_text(
            json.dumps(
                [
                    {
                        "test_file_path": row.test_file_path,
                        "target_selector": getattr(row, "target_selector", None) or row.test_file_path,
                        "status": row.status,
                        "total_tests": row.total_tests,
                        "passed_tests": row.passed_tests,
                        "failed_tests": row.failed_tests,
                        "error_tests": row.error_tests,
                        "skipped_tests": row.skipped_tests,
                        "pass_rate": row.pass_rate,
                        "raw_result": row.raw_result_json,
                    }
                    for row in evaluation
                ],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def _repository_clone_url(self, repository: GitHubRepository) -> str:
        return self._repository_materializer().repository_clone_url(repository)

    def _evaluator(self) -> Stage3Evaluator:
        if self.evaluator is None:
            self.evaluator = Stage3Evaluator(self.settings)
        return self.evaluator

    def _run_command(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=str(cwd) if cwd is not None else None,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )

    def _run_checked_command(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = 120.0,
        error_message: str,
    ) -> subprocess.CompletedProcess[str]:
        completed = self._run_command(args, cwd=cwd, timeout_seconds=timeout_seconds)
        if completed.returncode != 0:
            detail = str(completed.stderr or completed.stdout or "").strip()
            raise Stage3RunConflictError(f"{error_message}: {detail or 'unknown error'}")
        return completed

    def _git_output(self, repo_dir: Path, args: list[str]) -> str:
        completed = self._run_checked_command(
            ["git", *args],
            cwd=repo_dir,
            timeout_seconds=60.0,
            error_message=f"failed to run git {' '.join(args)}",
        )
        return str(completed.stdout or "").strip()

    def _baseline_pass_rate(self, row: Stage2TestResult) -> float:
        total_tests = int(row.total_tests or 0)
        if total_tests <= 0:
            return 0.0
        return float(row.passed_tests or 0) / float(total_tests)

    def _prepare_run_workspace(
        self,
        run: Stage3Run,
        *,
        snapshot: Stage3CommitSnapshot,
        entry_file: Stage3EntryFile,
    ) -> dict[str, Any]:
        workspace_dir = self._run_workspace_dir(run.id)
        return self._prepare_snapshot_workspace(
            workspace_dir=workspace_dir,
            snapshot=snapshot,
            entry_file=entry_file,
        )

    def _prepare_snapshot_workspace(
        self,
        *,
        workspace_dir: Path,
        snapshot: Stage3CommitSnapshot,
        entry_file: Stage3EntryFile | None = None,
    ) -> dict[str, Any]:
        workspace_dir = workspace_dir.expanduser().resolve()
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)
        repo_dir = workspace_dir / "repo"
        metadata_dir = workspace_dir / ".stage3"
        assets_dir = workspace_dir / "assets"
        checkpoint_dir = metadata_dir / "checkpoints"
        savepoint_dir = metadata_dir / "savepoints"
        for directory in (workspace_dir, repo_dir, metadata_dir, assets_dir, checkpoint_dir, savepoint_dir):
            directory.mkdir(parents=True, exist_ok=True)

        asset_files: list[Path] = []
        asset_files.extend(
            self._write_workspace_files(
                assets_dir,
                {
                    "planner_guidance.md": snapshot.planner_guidance or "",
                    "Dockerfile": snapshot.dockerfile_text or "",
                    "run_script.sh": snapshot.run_script_text or "",
                },
            )
        )
        original_p2p_path = assets_dir / "original_p2p_files.json"
        original_p2p_path.write_text(
            json.dumps(list(snapshot.original_p2p_files_json or []), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        asset_files.append(original_p2p_path)
        if entry_file is not None:
            entry_file_path = assets_dir / "entry_file.json"
            entry_file_path.write_text(
                json.dumps(
                    {
                        "entry_file_id": entry_file.id,
                        "test_file_path": entry_file.test_file_path,
                        "target_selector": entry_file.target_selector or entry_file.test_file_path,
                        "baseline_total_tests": entry_file.baseline_total_tests,
                        "baseline_passed_tests": entry_file.baseline_passed_tests,
                        "baseline_failed_tests": entry_file.baseline_failed_tests,
                        "baseline_error_tests": entry_file.baseline_error_tests,
                        "baseline_skipped_tests": entry_file.baseline_skipped_tests,
                        "baseline_pass_rate": entry_file.baseline_pass_rate,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            asset_files.append(entry_file_path)
        snapshot_path = assets_dir / "snapshot.json"
        snapshot_path.write_text(
            json.dumps(self.serialize_commit_snapshot_detail(snapshot), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        asset_files.append(snapshot_path)
        manifest = {
            "workspace_path": str(workspace_dir),
            "repo_dir": str(repo_dir),
            "metadata_dir": str(metadata_dir),
            "assets_dir": str(assets_dir),
            "checkpoint_dir": str(checkpoint_dir),
            "savepoint_dir": str(savepoint_dir),
            "generated_files": [str(path.relative_to(workspace_dir)) for path in asset_files],
            "generated_file_count": len(asset_files),
            "prepared_at": serialize_datetime(datetime.now(UTC)),
        }
        manifest_path = metadata_dir / "workspace_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest["generated_files"].append(str(manifest_path.relative_to(workspace_dir)))
        manifest["generated_file_count"] += 1
        return manifest

    def _assets_dir_for_workspace(self, workspace_dir: Path) -> Path:
        return workspace_dir / "assets"

    def _runtime_snapshot_seed(self) -> dict[str, Any]:
        api_key_secret = self.settings.stage3_agent_llm_api_key()
        api_key = api_key_secret.get_secret_value() if api_key_secret else ""
        return {
            "concurrency": {
                "max_concurrent_runs": self.settings.stage3_default_task_max_concurrent_runs
                or self.settings.stage3_max_concurrent_runs,
            },
            "breaker": {
                "model": self.settings.stage3_agent_llm_model() or "",
                "base_url": self.settings.stage3_agent_llm_base_url() or "",
                "api_key": api_key,
                "api_key_preview": f"{api_key[:6]}..." if api_key else "",
                "preset": self.settings.stage3_agent_openhands_preset(),
                "max_iterations": self.settings.stage3_agent_openhands_max_iterations(),
                "timeout_seconds": self.settings.stage3_agent_timeout(),
            },
            "hyperparameters": {
                "build_timeout_seconds": self.settings.stage3_build_timeout_seconds,
                "run_test_timeout_seconds": self.settings.stage3_run_test_timeout_seconds,
                "full_validation_timeout_seconds": self.settings.stage3_full_validation_timeout_seconds,
                "entry_pass_rate_ceiling": self.settings.stage3_entry_pass_rate_ceiling,
                "min_removed_code_lines": self.settings.stage3_min_removed_code_lines,
            },
        }

    def _api_runtime_snapshot(self, run: Stage3Run) -> dict[str, Any]:
        payload = dict(run.runtime_snapshot_json or {})
        stage3 = dict(payload.get("stage3") or {})
        stage3["runtime"] = redact_stage3_runtime_snapshot(
            extract_stage3_runtime_snapshot(dict(stage3.get("runtime") or {}))
        )
        payload["stage3"] = stage3
        return payload

    def _cleanup_run_workspace(self, run: Stage3Run) -> None:
        candidates: list[Path] = []
        if str(run.workspace_path or "").strip():
            candidates.append(Path(str(run.workspace_path)).expanduser())
        candidates.append(self._run_workspace_dir(run.id))
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if not str(resolved):
                continue
            if resolved.exists():
                try:
                    resolved.relative_to(self.workspace_root.resolve())
                except Exception as exc:
                    last_error = exc
                    continue
                try:
                    shutil.rmtree(resolved)
                except Exception as exc:
                    last_error = exc
                    continue
                return
        if last_error is not None:
            raise Stage3RunConflictError(f"failed to cleanup stage3 workspace: {last_error}")

    def _run_workspace_dir(self, run_id: str) -> Path:
        return self.workspace_root / "runs" / run_id

    def _snapshot_workspace_dir(self, snapshot_id: str) -> Path:
        return self.workspace_root / "snapshots" / snapshot_id

    def _run_runtime_dir(self, run_id: str) -> Path:
        return self.workspace_root / "runtime" / run_id

    def _workspace_dir_for_run(self, run: Stage3Run) -> Path:
        raw_workspace_path = str(run.workspace_path or "").strip()
        if raw_workspace_path:
            workspace_path = Path(raw_workspace_path).expanduser()
            if workspace_path.is_absolute():
                return workspace_path.resolve()
        return self._run_workspace_dir(run.id)

    def _cleanup_prepared_run_assets(
        self,
        run_id: str,
        *,
        checkpoint_payload: dict[str, Any] | None = None,
    ) -> dict[str, list[str]]:
        failed_paths = list(
            _remove_paths(
                [str(self._run_workspace_dir(run_id)), str(self._run_runtime_dir(run_id))],
                root_dir=self.workspace_root,
            )
            or []
        )
        image_refs = self._owned_checkpoint_docker_image_refs_from_payload(
            checkpoint_payload,
            run_id=run_id,
        )
        failed_image_refs = list(_remove_docker_images(image_refs) or [])
        return {
            "failed_paths": failed_paths,
            "failed_checkpoint_docker_image_refs": failed_image_refs,
        }

    def _breaker_checkpoint_paths_for_run(self, run: Stage3Run) -> list[str]:
        refs: list[str] = []

        def add_path(path: str | Path) -> None:
            normalized = str(Path(str(path or "")).expanduser()) if str(path or "").strip() else ""
            if normalized and normalized not in refs:
                refs.append(normalized)

        workspace_dir = Path(str(run.workspace_path or self._run_workspace_dir(run.id))).expanduser()
        add_path(workspace_dir / ".stage3" / "checkpoints")
        add_path(self._run_runtime_dir(run.id) / "breaker-checkpoints")

        for checkpoint in self._checkpoint_payloads_for_run(run):
            self._add_checkpoint_cleanup_path(checkpoint, add_path=add_path)
        for event in list(run.events or []):
            payload = dict(event.payload_json or {})
            self._add_checkpoint_cleanup_path(payload, add_path=add_path)
        return refs

    def _add_checkpoint_cleanup_path(self, checkpoint: dict[str, Any], *, add_path) -> None:
        checkpoint_dir_raw = str((checkpoint or {}).get("checkpoint_dir") or "").strip()
        if checkpoint_dir_raw:
            add_path(checkpoint_dir_raw)
            return
        workspace_snapshot_raw = str((checkpoint or {}).get("workspace_snapshot_path") or "").strip()
        if workspace_snapshot_raw:
            add_path(Path(workspace_snapshot_raw).expanduser().parent)

    def _owned_checkpoint_docker_image_refs_for_run(self, run: Stage3Run) -> list[str]:
        refs: list[str] = []

        def add_ref(image_ref: str) -> None:
            normalized_ref = str(image_ref or "").strip()
            if not normalized_ref:
                return
            if not self._checkpoint_image_ref_belongs_to_run(normalized_ref, run_id=run.id):
                return
            if normalized_ref not in refs:
                refs.append(normalized_ref)

        for checkpoint in self._checkpoint_payloads_for_run(run):
            add_ref(str(checkpoint.get("docker_image_ref") or ""))
        for event in list(run.events or []):
            payload = dict(event.payload_json or {})
            add_ref(str(payload.get("docker_image_ref") or ""))
        return refs

    def _owned_checkpoint_docker_image_refs_from_payload(
        self,
        checkpoint_payload: dict[str, Any] | None,
        *,
        run_id: str,
    ) -> list[str]:
        refs: list[str] = []

        def add_ref(image_ref: str) -> None:
            normalized_ref = str(image_ref or "").strip()
            if not normalized_ref:
                return
            if not self._checkpoint_image_ref_belongs_to_run(normalized_ref, run_id=run_id):
                return
            if normalized_ref not in refs:
                refs.append(normalized_ref)

        checkpoint = dict(checkpoint_payload or {})
        add_ref(str(checkpoint.get("docker_image_ref") or ""))
        docker_commit = dict(checkpoint.get("docker_commit") or {})
        add_ref(str(docker_commit.get("docker_image_ref") or ""))
        return refs

    def _checkpoint_image_ref_belongs_to_run(self, image_ref: str, *, run_id: str) -> bool:
        safe_run_id = re.sub(r"[^a-z0-9_.-]+", "-", str(run_id or "").lower()).strip("-")
        return str(image_ref or "").strip().startswith(
            f"feature-factory/stage3-breaker-checkpoint:{safe_run_id}-"
        )

    def _checkpoint_payloads_for_run(self, run: Stage3Run | None) -> list[dict[str, Any]]:
        if run is None:
            return []
        payloads: list[dict[str, Any]] = []
        for savepoint in list(run.savepoints or []):
            checkpoint = dict(savepoint.checkpoint_json or {})
            if checkpoint:
                payloads.append(checkpoint)
        runtime_stage3 = dict((run.runtime_snapshot_json or {}).get("stage3") or {})
        resume_checkpoint = dict(runtime_stage3.get("resume_checkpoint") or {})
        if resume_checkpoint:
            payloads.append(resume_checkpoint)
        return payloads

    def _write_workspace_files(self, root: Path, payloads: dict[str, str]) -> list[Path]:
        created: list[Path] = []
        for relative_name, content in payloads.items():
            path = root / relative_name
            path.write_text(str(content or ""), encoding="utf-8")
            created.append(path)
        return created

    def _make_run_event(
        self,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ):
        from feature_factory.models import Stage3RunEvent

        return Stage3RunEvent(
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload_json=dict(payload or {}),
        )

    def _snapshot_sort_key(self, snapshot: Stage3CommitSnapshot) -> tuple[datetime, datetime, str]:
        return (
            snapshot.source_created_at or snapshot.created_at,
            snapshot.updated_at or snapshot.created_at,
            str(snapshot.id),
        )

    def _repository_row_status(self, stats: dict[str, Any] | None) -> str:
        normalized = dict(stats or {})
        if int(normalized.get("running_count") or 0) > 0:
            return "running"
        if int(normalized.get("queued_count") or 0) > 0:
            return "queued"
        if int(normalized.get("succeeded_count") or 0) > 0:
            return "succeeded"
        if int(normalized.get("failed_count") or 0) > 0:
            return "failed"
        if int(normalized.get("interrupted_count") or 0) > 0:
            return "interrupted"
        if int(normalized.get("pending_count") or 0) > 0 or normalized.get("latest_operation_at") is None:
            return "pending"
        return "failed"