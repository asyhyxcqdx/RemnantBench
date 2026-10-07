from __future__ import annotations

import json
import re
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only, selectinload

from feature_factory.config import get_settings
from feature_factory.docker_mirrors import (
    env_value,
    github_proxy_retry_attempts,
    refresh_github_proxy_urls,
    rotate_failed_github_proxy_urls,
    temporary_git_remote_url,
)
from feature_factory.llm_usage import aggregate_token_usage_by_model
from feature_factory.models import (
    GitHubRepository,
    Stage2CleanupTombstone,
    Stage2Run,
    Stage2RunEvent,
    Stage2RunResult,
    Stage2RunStatus,
    Stage2TestResult,
    Stage2ValidationAttempt,
)
from feature_factory.stage2.base_images import list_base_images
from feature_factory.stage2.raw_archive import (
    LLM_COMPLETION_ARCHIVE_ROLES,
    legacy_llm_completion_archive_dir,
    llm_completion_archive_dir,
    runtime_dir_from_workspace_path,
    summarize_run_llm_completion_archives,
)
from feature_factory.stage2.resume_paths import (
    rewrite_stage2_json_tree_path_references,
    rewrite_stage2_path_references,
)


class Stage2CommitUnchangedError(RuntimeError):
    pass


class Stage2CommitResolutionError(RuntimeError):
    pass


class Stage2RunDeleteConflictError(RuntimeError):
    pass


class Stage2RunInterruptConflictError(RuntimeError):
    pass


class Stage2RunRuntimeConflictError(RuntimeError):
    pass


class Stage2WorkerRerunConflictError(RuntimeError):
    pass


class Stage2WorkerResumeConflictError(RuntimeError):
    pass


_DEFAULT_GIT_LS_REMOTE_TIMEOUT_SECONDS = 20.0
_GIT_LS_REMOTE_RETRYABLE_ERROR_MARKERS = (
    "SSL_ERROR_SYSCALL",
    "RPC failed",
    "early EOF",
    "unexpected disconnect",
    "unable to update url base from redirection",
    "Failed to connect",
    "Connection reset",
    "Connection refused",
    "Operation timed out",
    "timed out",
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


def serialize_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    else:
        value = value.astimezone(UTC)
    return value.isoformat()


def _remove_paths(paths: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    failed: list[str] = []
    for candidate in paths:
        path = Path(str(candidate or "")).expanduser()
        if not str(path):
            continue
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        resolved_key = str(resolved)
        if resolved_key in seen:
            continue
        seen.add(resolved_key)
        if not resolved.exists():
            continue
        try:
            if resolved.is_dir():
                shutil.rmtree(resolved)
            else:
                resolved.unlink()
        except Exception:
            failed.append(resolved_key)
    return failed


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


def _checkpoint_cleanup_targets_from_payload(checkpoint: dict[str, Any] | None) -> dict[str, list[str]]:
    payload = dict(checkpoint or {})
    checkpoint_paths: list[str] = []
    checkpoint_image_refs: list[str] = []

    def add_path(path: Path | None) -> None:
        if path is None:
            return
        normalized = str(path.expanduser())
        if normalized and normalized not in checkpoint_paths:
            checkpoint_paths.append(normalized)

    checkpoint_dir_raw = str(payload.get("checkpoint_dir") or "").strip()
    if checkpoint_dir_raw:
        checkpoint_dir = Path(checkpoint_dir_raw).expanduser()
        if checkpoint_dir.name == "current" and checkpoint_dir.parent.name == "worker-checkpoint":
            add_path(checkpoint_dir.parent)
        else:
            add_path(checkpoint_dir)
    else:
        workspace_snapshot_raw = str(payload.get("workspace_snapshot_path") or "").strip()
        if workspace_snapshot_raw:
            snapshot_path = Path(workspace_snapshot_raw).expanduser()
            current_dir = snapshot_path.parent
            if current_dir.name == "current" and current_dir.parent.name == "worker-checkpoint":
                add_path(current_dir.parent)
            else:
                add_path(current_dir)

    image_ref = str(payload.get("docker_image_ref") or "").strip()
    if image_ref:
        checkpoint_image_refs.append(image_ref)
    return {
        "checkpoint_paths": checkpoint_paths,
        "checkpoint_docker_image_refs": checkpoint_image_refs,
    }


def _cleanup_resume_checkpoint_assets(checkpoint: dict[str, Any] | None) -> dict[str, list[str]]:
    cleanup = _checkpoint_cleanup_targets_from_payload(checkpoint)
    failed_paths = list(_remove_paths(cleanup.get("checkpoint_paths") or []) or [])
    failed_image_refs = list(
        _remove_docker_images(cleanup.get("checkpoint_docker_image_refs") or []) or []
    )
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_image_refs,
    }


_STAGE2_RUNTIME_SNAPSHOT_SECTION_KEYS = (
    "concurrency",
    "planner",
    "worker",
    "hyperparameters",
    "selection",
)


def extract_stage2_runtime_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    extracted: dict[str, Any] = {}
    for key in _STAGE2_RUNTIME_SNAPSHOT_SECTION_KEYS:
        value = snapshot.get(key)
        if isinstance(value, dict):
            extracted[key] = dict(value)
    return extracted


def merge_stage2_runtime_snapshot(
    existing_snapshot: dict[str, Any] | None,
    updated_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(existing_snapshot or {})
    for key, value in dict(updated_snapshot or {}).items():
        if key in _STAGE2_RUNTIME_SNAPSHOT_SECTION_KEYS and isinstance(value, dict):
            merged[key] = {
                **dict(merged.get(key) or {}),
                **dict(value),
            }
            continue
        merged[key] = value
    return merged


def _stage2_p2p_sample_seed(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    hyperparameters = dict(snapshot.get("hyperparameters") or {})
    return str(hyperparameters.get("p2p_sample_seed") or "").strip()


def _stage2_runtime_snapshot_with_p2p_sample_seed(
    snapshot: dict[str, Any] | None,
    *,
    fallback_seed: str,
) -> dict[str, Any]:
    updated = dict(snapshot or {})
    if _stage2_p2p_sample_seed(updated):
        return updated
    seed = str(fallback_seed or "").strip()
    if not seed:
        return updated
    hyperparameters = dict(updated.get("hyperparameters") or {})
    hyperparameters["p2p_sample_seed"] = seed
    updated["hyperparameters"] = hyperparameters
    return updated


def _stage2_runtime_snapshot_with_inherited_p2p_sample_seed(
    snapshot: dict[str, Any] | None,
    *,
    source_snapshot: dict[str, Any] | None,
    source_run_id: str,
) -> dict[str, Any]:
    inherited_seed = _stage2_p2p_sample_seed(source_snapshot) or str(source_run_id or "").strip()
    return _stage2_runtime_snapshot_with_p2p_sample_seed(
        snapshot,
        fallback_seed=inherited_seed,
    )


def redact_stage2_runtime_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    redacted = dict(snapshot or {})
    redacted.pop("resume_materialized", None)
    for agent_key in ("planner", "worker"):
        agent_snapshot = redacted.get(agent_key)
        if not isinstance(agent_snapshot, dict):
            continue
        agent = dict(agent_snapshot)
        api_key = str(agent.get("api_key") or "")
        api_key_preview = str(agent.get("api_key_preview") or "")
        if api_key and not api_key_preview:
            api_key_preview = f"{api_key[:6]}..."
        agent.pop("api_key", None)
        if api_key_preview:
            agent["api_key_preview"] = api_key_preview
        redacted[agent_key] = agent
    return redacted


def _checkpoint_conversation_directory(checkpoint: dict[str, Any] | None) -> Path | None:
    payload = dict(checkpoint or {})
    conversation_id = str(payload.get("conversation_id") or "").strip()
    conversations_path = str(((payload.get("openhands") or {}).get("conversations_path")) or "").strip()
    if not conversation_id or not conversations_path:
        return None
    try:
        conversation_dir_name = uuid.UUID(conversation_id).hex
    except ValueError:
        return None
    return Path(conversations_path).expanduser() / conversation_dir_name


def _checkpoint_bash_events_directory(checkpoint: dict[str, Any] | None) -> Path | None:
    payload = dict(checkpoint or {})
    bash_events_dir = str(((payload.get("openhands") or {}).get("bash_events_dir")) or "").strip()
    if not bash_events_dir:
        return None
    return Path(bash_events_dir).expanduser()


def _infer_stage2_root_from_run_or_checkpoint(
    *,
    source_run: Stage2Run | None,
    checkpoint: dict[str, Any] | None,
    stage2_workspace_dir: str | Path | None,
) -> Path | None:
    if stage2_workspace_dir is not None:
        return Path(stage2_workspace_dir).expanduser().resolve()

    workspace_path = str(getattr(source_run, "workspace_path", "") or "").strip()
    if workspace_path:
        workspace_dir = Path(workspace_path).expanduser()
        if workspace_dir.name == str(getattr(source_run, "id", "") or "") and workspace_dir.parent.name == "runs":
            return workspace_dir.parent.parent.resolve()

    candidate_paths: list[Path] = []
    checkpoint_dir_raw = str((dict(checkpoint or {})).get("checkpoint_dir") or "").strip()
    if checkpoint_dir_raw:
        candidate_paths.append(Path(checkpoint_dir_raw).expanduser())
    workspace_snapshot_path_raw = str((dict(checkpoint or {})).get("workspace_snapshot_path") or "").strip()
    if workspace_snapshot_path_raw:
        candidate_paths.append(Path(workspace_snapshot_path_raw).expanduser())

    for candidate in candidate_paths:
        for parent in candidate.parents:
            if parent.name == "runtime":
                return parent.parent.resolve()
            if parent.name == "runs":
                return parent.parent.resolve()
    return None


def _checkpoint_image_ref_for_run(*, run_id: str, attempt_index: int) -> str:
    safe_run_id = re.sub(r"[^a-z0-9_.-]+", "-", run_id.lower()).strip("-")
    return f"feature-factory/stage2-worker-checkpoint:{safe_run_id}-attempt-{int(attempt_index or 0):03d}"


def _retag_checkpoint_image(*, source_image_ref: str, destination_run_id: str, attempt_index: int) -> str:
    source = str(source_image_ref or "").strip()
    if not source:
        raise RuntimeError("resume checkpoint is missing docker_image_ref")
    destination = _checkpoint_image_ref_for_run(
        run_id=destination_run_id,
        attempt_index=attempt_index,
    )
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
            f"failed to clone worker checkpoint image {source} -> {destination}: {error}"
        )
    return destination


def _copytree_if_exists(source: Path | None, destination: Path) -> bool:
    if source is None or not source.exists():
        return False
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination)
    return True


def _copy_llm_completion_archives_for_resume_run(
    *,
    source_run: Stage2Run,
    stage2_root: Path,
    destination_run_id: str,
) -> None:
    source_runtime_dir = runtime_dir_from_workspace_path(source_run.workspace_path, run_id=source_run.id)
    if source_runtime_dir is None:
        source_runtime_dir = stage2_root / "runtime" / str(source_run.id)
    source_runtime_dir = source_runtime_dir.expanduser().resolve()
    if not source_runtime_dir.exists():
        return

    destination_runtime_dir = (stage2_root / "runtime" / destination_run_id).expanduser().resolve()
    for role in LLM_COMPLETION_ARCHIVE_ROLES:
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


def _docker_image_exists(image_ref: str) -> bool:
    normalized_ref = str(image_ref or "").strip()
    if not normalized_ref or shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "image", "inspect", normalized_ref],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except Exception:
        return False
    return completed.returncode == 0


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


def _checkpoint_cleanup_root(checkpoint: dict[str, Any] | None) -> Path | None:
    payload = dict(checkpoint or {})
    checkpoint_dir_raw = str(payload.get("checkpoint_dir") or "").strip()
    if checkpoint_dir_raw:
        checkpoint_dir = Path(checkpoint_dir_raw).expanduser()
        if checkpoint_dir.name == "current" and checkpoint_dir.parent.name == "worker-checkpoint":
            return checkpoint_dir.parent
        return checkpoint_dir

    workspace_snapshot_raw = str(payload.get("workspace_snapshot_path") or "").strip()
    if not workspace_snapshot_raw:
        return None
    snapshot_path = Path(workspace_snapshot_raw).expanduser()
    current_dir = snapshot_path.parent
    if current_dir.name == "current" and current_dir.parent.name == "worker-checkpoint":
        return current_dir.parent
    return current_dir


def _cleanup_cloned_worker_checkpoint_assets(checkpoint: dict[str, Any] | None) -> dict[str, list[str]]:
    failed_paths: list[str] = []
    cleanup_root = _checkpoint_cleanup_root(checkpoint)
    if cleanup_root is not None and cleanup_root.exists():
        try:
            if cleanup_root.is_dir():
                shutil.rmtree(cleanup_root)
            else:
                cleanup_root.unlink()
        except Exception:
            failed_paths.append(str(cleanup_root))

    failed_image_refs = _remove_docker_images(
        [str((dict(checkpoint or {})).get("docker_image_ref") or "").strip()]
    )
    return {
        "failed_paths": failed_paths,
        "failed_checkpoint_docker_image_refs": failed_image_refs,
    }


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(path)


_OPENHANDS_CONVERSATION_STATE_FILENAMES = ("base_state.json", "meta.json")
_OPENHANDS_REDACTED_SECRET = "**********"


def _worker_llm_patch_from_runtime_snapshot(
    runtime_snapshot: dict[str, Any] | None,
    *,
    completion_archive_dir: Path | None = None,
) -> dict[str, Any]:
    worker_snapshot = dict((runtime_snapshot or {}).get("worker") or {})
    llm_patch: dict[str, Any] = {}
    for key in ("model", "base_url"):
        value = str(worker_snapshot.get(key) or "").strip()
        if value:
            llm_patch[key] = value
    if "api_key" in worker_snapshot:
        api_key = str(worker_snapshot.get("api_key") or "")
        llm_patch["api_key"] = "" if api_key == _OPENHANDS_REDACTED_SECRET else api_key
    if completion_archive_dir is not None:
        llm_patch["log_completions"] = True
        llm_patch["log_completions_folder"] = str(completion_archive_dir)
    return llm_patch


def _patch_openhands_agent_llm(agent: dict[str, Any], llm_patch: dict[str, Any]) -> bool:
    changed = False
    llm = agent.get("llm")
    if isinstance(llm, dict):
        for key, value in llm_patch.items():
            if llm.get(key) != value:
                llm[key] = value
                changed = True
    condenser = agent.get("condenser")
    if isinstance(condenser, dict):
        condenser_llm = condenser.get("llm")
        if isinstance(condenser_llm, dict):
            for key, value in llm_patch.items():
                if condenser_llm.get(key) != value:
                    condenser_llm[key] = value
                    changed = True
    return changed


def _patch_openhands_conversation_llm_state(
    conversation_dir: Path,
    *,
    runtime_snapshot: dict[str, Any] | None,
    completion_archive_dir: Path | None = None,
) -> list[str]:
    llm_patch = _worker_llm_patch_from_runtime_snapshot(
        runtime_snapshot,
        completion_archive_dir=completion_archive_dir,
    )
    if not llm_patch:
        return []

    patched: list[str] = []
    for filename in _OPENHANDS_CONVERSATION_STATE_FILENAMES:
        state_path = conversation_dir / filename
        if not state_path.exists():
            continue
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            continue
        agent = payload.get("agent")
        if not isinstance(agent, dict):
            continue
        if _patch_openhands_agent_llm(agent, llm_patch):
            _atomic_write_json(state_path, payload)
            patched.append(filename)
    return patched


_OPENHANDS_BRIDGE_FAILURE_RE = re.compile(r"^OpenHands (?P<label>.+?) failed:\s*")


def _read_existing_text_file(path_value: Any) -> str:
    path_text = str(path_value or "").strip()
    if not path_text:
        return ""
    try:
        path = Path(path_text).expanduser()
        if not path.exists() or not path.is_file():
            return ""
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _expanded_stage2_error_message(run: "Stage2Run") -> str | None:
    error_message = str(run.error_message or "")
    if not error_message:
        return None
    match = _OPENHANDS_BRIDGE_FAILURE_RE.match(error_message)
    if match is None or not error_message.endswith("..."):
        return error_message

    label = match.group("label")
    for event in reversed(list(run.events or [])):
        payload = dict(event.payload_json or {})
        if not str(payload.get("operation") or "").startswith("openhands_bridge_"):
            continue
        details = _read_existing_text_file(payload.get("stderr_log")) or _read_existing_text_file(
            payload.get("stdout_log")
        )
        if details:
            return f"OpenHands {label} failed: {details}"
    return error_message


class Stage2Service:
    def __init__(self, session: Session) -> None:
        self.session = session
        self._docker_image_exists_cache: dict[str, bool] = {}

    def list_repository_runs(self, repository_id: int, *, include_detail: bool = False) -> list[Stage2Run]:
        statement = (
            select(Stage2Run)
            .where(Stage2Run.repository_id == repository_id)
            .order_by(Stage2Run.created_at.desc())
        )
        if include_detail:
            statement = statement.options(
                selectinload(Stage2Run.events),
                selectinload(Stage2Run.test_results),
                selectinload(Stage2Run.validation_attempts),
            )
        else:
            statement = statement.options(
                load_only(
                    Stage2Run.id,
                    Stage2Run.status,
                    Stage2Run.result,
                    Stage2Run.phase,
                    Stage2Run.trigger_kind,
                    Stage2Run.target_branch,
                    Stage2Run.target_commit_sha,
                    Stage2Run.base_image,
                    Stage2Run.created_at,
                    Stage2Run.updated_at,
                    Stage2Run.started_at,
                    Stage2Run.finished_at,
                )
            )
        return list(self.session.scalars(statement))

    def get_repository(self, repository_id: int) -> GitHubRepository:
        repository = self.session.get(GitHubRepository, repository_id)
        if repository is None:
            raise ValueError(f"repository not found: {repository_id}")
        return repository

    def get_run(self, run_id: str, *, include_detail: bool = True) -> Stage2Run:
        options = ()
        if include_detail:
            options = (
                selectinload(Stage2Run.events),
                selectinload(Stage2Run.test_results),
                selectinload(Stage2Run.validation_attempts),
            )
        run = self.session.get(
            Stage2Run,
            run_id,
            options=options,
        )
        if run is None:
            raise ValueError(f"stage2 run not found: {run_id}")
        return run

    def get_repository_run(self, repository_id: int, run_id: str, *, include_detail: bool = True) -> Stage2Run:
        statement = select(Stage2Run).where(
            Stage2Run.id == run_id,
            Stage2Run.repository_id == repository_id,
        )
        if include_detail:
            statement = statement.options(
                selectinload(Stage2Run.events),
                selectinload(Stage2Run.test_results),
                selectinload(Stage2Run.validation_attempts),
            )
        run = self.session.scalar(statement.limit(1))
        if run is None:
            raise ValueError(f"stage2 run not found: {run_id}")
        return run

    def delete_repository_run(self, repository_id: int, run_id: str) -> dict[str, Any]:
        archive = self.repository_run_cleanup_archive(repository_id, run_id)
        self.upsert_cleanup_tombstone(archive, reason="manual_delete")
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        self.session.delete(run)
        self.session.flush()
        return archive

    def upsert_cleanup_tombstone(
        self,
        archive: dict[str, Any],
        *,
        reason: str,
    ) -> Stage2CleanupTombstone | None:
        run_id = str(archive.get("run_id") or "").strip()
        if not run_id:
            return None
        tombstone = self.session.get(Stage2CleanupTombstone, run_id)
        if tombstone is None:
            tombstone = Stage2CleanupTombstone(run_id=run_id)
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
                select(Stage2CleanupTombstone)
                .where(Stage2CleanupTombstone.status.in_(["pending", "failed"]))
                .order_by(Stage2CleanupTombstone.created_at.asc())
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
        tombstone = self.session.get(Stage2CleanupTombstone, run_id)
        if tombstone is None:
            return
        failure_payload = dict(cleanup_result or {})
        failed_paths = list(failure_payload.get("failed_paths") or [])
        failed_repo_checkout_paths = list(failure_payload.get("failed_repo_checkout_paths") or [])
        failed_checkpoint_images = list(
            failure_payload.get("failed_checkpoint_docker_image_refs") or []
        )
        failed_validator_images = list(failure_payload.get("failed_validator_image_refs") or [])
        tombstone.attempt_count = int(tombstone.attempt_count or 0) + 1
        tombstone.last_attempt_at = datetime.now(UTC)
        if (
            failed_paths
            or failed_repo_checkout_paths
            or failed_checkpoint_images
            or failed_validator_images
        ):
            tombstone.status = "failed"
            tombstone.failure_json = failure_payload
            self.session.add(tombstone)
        else:
            self.session.delete(tombstone)
        self.session.flush()

    def repository_run_cleanup_archive(self, repository_id: int, run_id: str) -> dict[str, Any]:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        if run.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value}:
            raise Stage2RunDeleteConflictError(f"cannot delete an active stage2 run: {run_id}")
        return {
            "run_id": run.id,
            "repository_id": run.repository_id,
            "workspace_path": run.workspace_path,
            "checkpoint_docker_image_refs": self._checkpoint_docker_image_refs_for_run(run),
            "validator_image_refs": self._validator_image_refs_for_run(run),
        }

    def create_run(
        self,
        repository_id: int,
        *,
        trigger_kind: str = "manual",
        commit_resolver=None,
        target_commit_sha: str | None = None,
        allow_existing_successful_commit: bool = False,
        source_run_id: str | None = None,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> Stage2Run:
        repository = self.get_repository(repository_id)
        target_branch = repository.default_branch or "HEAD"
        if target_commit_sha is None and commit_resolver is not None:
            target_commit_sha = commit_resolver(repository)
        target_commit_sha = str(target_commit_sha or "").strip()
        if not target_commit_sha:
            raise Stage2CommitResolutionError(
                f"failed to resolve remote default-branch commit for {repository.full_name}"
            )
        active_run = self.active_run_for_repository(repository_id)
        if active_run is not None:
            raise Stage2CommitUnchangedError(
                f"repository {repository.full_name} already has an active stage2 run"
            )

        existing_successful_run = self.successful_run_for_repository_commit(
            repository_id,
            target_commit_sha,
        )
        if (
            not allow_existing_successful_commit
            and existing_successful_run is not None
        ):
            raise Stage2CommitUnchangedError(
                f"repository {repository.full_name} is already successfully built for commit {target_commit_sha}"
            )

        run = Stage2Run(
            repository_id=repository.id,
            status=Stage2RunStatus.queued.value,
            result=Stage2RunResult.unknown.value,
            trigger_kind=trigger_kind,
            phase="queued",
            target_branch=target_branch,
            target_commit_sha=target_commit_sha,
            runtime_snapshot_json=dict(runtime_snapshot or {}),
        )
        self.session.add(run)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise Stage2CommitUnchangedError(
                f"repository {repository.full_name} already has an active stage2 run"
            ) from exc
        run.runtime_snapshot_json = _stage2_runtime_snapshot_with_p2p_sample_seed(
            run.runtime_snapshot_json,
            fallback_seed=run.id,
        )
        self.record_event(
            run.id,
            actor="system",
            phase="queued",
            title="Run queued",
            message=f"Stage2 run created for {repository.full_name}",
            payload={
                "repository_id": repository.id,
                "repository_full_name": repository.full_name,
                "target_branch": target_branch,
                "target_commit_sha": target_commit_sha,
                "source_run_id": source_run_id,
            },
        )
        self.session.flush()
        return run

    def create_run_from_existing_commit(
        self,
        repository_id: int,
        source_run_id: str,
        *,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> Stage2Run:
        source_run = self.get_repository_run(repository_id, source_run_id, include_detail=False)
        target_commit_sha = str(source_run.target_commit_sha or "").strip()
        if not target_commit_sha:
            raise Stage2CommitUnchangedError(
                f"stage2 run {source_run_id} does not have a target commit to rerun"
            )
        source_runtime_snapshot = extract_stage2_runtime_snapshot(
            dict(source_run.runtime_snapshot_json or {})
        )
        runtime_snapshot = _stage2_runtime_snapshot_with_inherited_p2p_sample_seed(
            runtime_snapshot,
            source_snapshot=source_runtime_snapshot,
            source_run_id=source_run_id,
        )
        return self.create_run(
            repository_id,
            trigger_kind="rerun_commit",
            target_commit_sha=target_commit_sha,
            allow_existing_successful_commit=True,
            source_run_id=source_run_id,
            runtime_snapshot=runtime_snapshot,
        )

    def create_run_from_existing_worker_plan(
        self,
        repository_id: int,
        source_run_id: str,
        *,
        runtime_snapshot: dict[str, Any] | None = None,
    ) -> Stage2Run:
        source_run = self.get_repository_run(repository_id, source_run_id, include_detail=True)
        if not self.can_rerun_worker_for_run(source_run):
            raise Stage2WorkerRerunConflictError(
                f"stage2 run {source_run_id} does not have a reusable ready planner result for worker rerun"
            )
        target_commit_sha = str(source_run.target_commit_sha or "").strip()
        source_runtime_snapshot = extract_stage2_runtime_snapshot(
            dict(source_run.runtime_snapshot_json or {})
        )
        selection_snapshot = self._selection_snapshot_for_run(source_run)
        if selection_snapshot:
            source_runtime_snapshot = merge_stage2_runtime_snapshot(
                source_runtime_snapshot,
                {"selection": selection_snapshot},
            )
        source_runtime_snapshot = _stage2_runtime_snapshot_with_p2p_sample_seed(
            source_runtime_snapshot,
            fallback_seed=source_run_id,
        )
        runtime_snapshot = _stage2_runtime_snapshot_with_inherited_p2p_sample_seed(
            runtime_snapshot if runtime_snapshot is not None else source_runtime_snapshot,
            source_snapshot=source_runtime_snapshot,
            source_run_id=source_run_id,
        )
        materialized_usage = self._build_materialized_inherited_usage_state(
            source_run,
            source_kind="rerun_worker",
        )
        rerun_runtime_snapshot = merge_stage2_runtime_snapshot(
            runtime_snapshot,
            {
                "resume_materialized": materialized_usage,
                "resume_source_run_id": source_run_id,
            },
        )
        run = self.create_run(
            repository_id,
            trigger_kind="rerun_worker",
            target_commit_sha=target_commit_sha,
            allow_existing_successful_commit=True,
            source_run_id=source_run_id,
            runtime_snapshot=rerun_runtime_snapshot,
        )
        run.base_image = source_run.base_image
        run.planner_guidance = source_run.planner_guidance
        run.planner_model = source_run.planner_model
        run.planner_token_usage = 0
        stage2_root = _infer_stage2_root_from_run_or_checkpoint(
            source_run=source_run,
            checkpoint=None,
            stage2_workspace_dir=None,
        )
        if stage2_root is not None:
            run.workspace_path = str((Path(stage2_root) / "runs" / run.id).resolve())
            _copy_llm_completion_archives_for_resume_run(
                source_run=source_run,
                stage2_root=stage2_root,
                destination_run_id=run.id,
            )
        self.record_event(
            run.id,
            actor="system",
            phase="queued",
            title="Worker rerun prepared",
            message="Prepared a worker-only rerun by reusing the previous ready planner result",
            payload={
                "run_id": run.id,
                "repository_id": run.repository_id,
                "source_run_id": source_run_id,
                "target_commit_sha": target_commit_sha,
                "base_image": source_run.base_image,
                "planner_model": source_run.planner_model,
            },
        )
        self.session.flush()
        return run

    def create_run_from_worker_checkpoint(
        self,
        repository_id: int,
        source_run_id: str,
        *,
        stage2_workspace_dir: str | Path | None = None,
    ) -> Stage2Run:
        source_run = self.get_repository_run(repository_id, source_run_id, include_detail=True)
        resume_kind = self.resume_kind_for_run(source_run)
        if resume_kind is None:
            raise Stage2WorkerResumeConflictError(
                f"stage2 run {source_run_id} does not have a reusable worker checkpoint"
            )
        resume_attempt = self.latest_resume_checkpoint_attempt(source_run)
        resume_attempt_index = int(resume_attempt.attempt_index or 0) if resume_attempt is not None else 0
        checkpoint = dict(resume_attempt.checkpoint_json or {}) if resume_attempt is not None else {}
        if resume_kind == "worker" and not checkpoint:
            checkpoint = self._runtime_snapshot_resume_checkpoint(source_run)
            resume_attempt_index = max(
                int(checkpoint.get("attempt_index") or 0),
                int((source_run.runtime_snapshot_json or {}).get("resume_source_attempt_index") or 0),
            )
        if resume_attempt_index <= 0 or not checkpoint:
            raise Stage2WorkerResumeConflictError(
                f"stage2 run {source_run_id} does not have a reusable worker checkpoint"
            )
        checkpoint.setdefault("attempt_index", resume_attempt_index)
        target_commit_sha = str(source_run.target_commit_sha or "").strip()
        source_runtime_snapshot = extract_stage2_runtime_snapshot(
            dict(source_run.runtime_snapshot_json or {})
        )
        selection_snapshot = self._selection_snapshot_for_run(source_run)
        if selection_snapshot:
            source_runtime_snapshot = merge_stage2_runtime_snapshot(
                source_runtime_snapshot,
                {"selection": selection_snapshot},
            )
        source_runtime_snapshot = _stage2_runtime_snapshot_with_p2p_sample_seed(
            source_runtime_snapshot,
            fallback_seed=source_run_id,
        )
        run = self.create_run(
            repository_id,
            trigger_kind="resume_full_validation" if resume_kind == "full_validation" else "resume_worker",
            target_commit_sha=target_commit_sha,
            allow_existing_successful_commit=True,
            source_run_id=source_run_id,
            runtime_snapshot=source_runtime_snapshot,
        )
        run.base_image = source_run.base_image
        run.planner_guidance = source_run.planner_guidance
        run.planner_model = source_run.planner_model
        run.planner_token_usage = 0
        run.worker_model = source_run.worker_model
        cloned_checkpoint: dict[str, Any] = {}
        materialized_resume = self._build_materialized_resume_state(
            source_run,
            source_attempt_index=resume_attempt_index,
        )
        try:
            cloned_checkpoint = self._clone_checkpoint_for_resume_run(
                checkpoint=checkpoint,
                source_run=source_run,
                destination_run=run,
                stage2_workspace_dir=stage2_workspace_dir,
                clone_docker_image=resume_kind == "worker",
                runtime_snapshot=source_runtime_snapshot,
            )
            workspace_dir = str(cloned_checkpoint.get("workspace_dir") or "").strip()
            if workspace_dir:
                run.workspace_path = workspace_dir
            if resume_kind == "full_validation":
                if resume_attempt is None:
                    raise Stage2WorkerResumeConflictError(
                        f"stage2 run {source_run_id} does not have a reusable full-validation checkpoint"
                    )
                run.dockerfile_text = resume_attempt.dockerfile_text or source_run.dockerfile_text
                run.run_script_text = resume_attempt.run_script_text or source_run.run_script_text
                run.collect_report_json = dict(resume_attempt.collect_report_json or {})
                run.smoke_report_json = dict(resume_attempt.smoke_report_json or {})
                run.full_report_json = {}
                run.summary_json = {
                    **dict(run.summary_json or {}),
                    "latest_attempt_index": resume_attempt_index,
                }
                self.session.add(
                    Stage2ValidationAttempt(
                        run=run,
                        attempt_index=resume_attempt_index,
                        dockerfile_text=resume_attempt.dockerfile_text,
                        run_script_text=resume_attempt.run_script_text,
                        collect_report_json=dict(resume_attempt.collect_report_json or {}),
                        smoke_report_json=dict(resume_attempt.smoke_report_json or {}),
                        full_report_json={},
                        checkpoint_json=dict(cloned_checkpoint),
                        version_finalized=True,
                        validator_status="smoke_completed",
                        error_message=resume_attempt.error_message,
                    )
                )
            run.runtime_snapshot_json = merge_stage2_runtime_snapshot(
                dict(run.runtime_snapshot_json or {}),
                {
                    **source_runtime_snapshot,
                    "resume_kind": resume_kind,
                    "resume_checkpoint": cloned_checkpoint,
                    "resume_materialized": materialized_resume,
                    "resume_source_run_id": source_run_id,
                    "resume_source_attempt_index": resume_attempt_index,
                },
            )
            self.record_event(
                run.id,
                actor="system",
                phase="queued",
                title=(
                    "Full validation resume prepared"
                    if resume_kind == "full_validation"
                    else "Worker resume prepared"
                ),
                message=(
                    "Prepared a full-validation resume run from the saved smoke-passed checkpoint"
                    if resume_kind == "full_validation"
                    else "Prepared a worker resume run from the latest saved validation checkpoint"
                ),
                payload={
                    "run_id": run.id,
                    "repository_id": run.repository_id,
                    "source_run_id": source_run_id,
                    "source_attempt_index": resume_attempt_index,
                    "target_commit_sha": target_commit_sha,
                    "base_image": source_run.base_image,
                    "resume_kind": resume_kind,
                },
            )
            self.session.flush()
        except Exception:
            if cloned_checkpoint:
                _cleanup_resume_checkpoint_assets(cloned_checkpoint)
            raise
        return run

    def record_terminal_validator_image_cleanup_failure(
        self,
        run_id: str,
        *,
        image_refs: list[str],
        failed_image_refs: list[str],
    ) -> None:
        run = self.get_run(run_id, include_detail=False)
        normalized_image_refs: list[str] = []
        for image_ref in image_refs:
            normalized = str(image_ref or "").strip()
            if normalized and normalized not in normalized_image_refs:
                normalized_image_refs.append(normalized)
        normalized_failed_refs: list[str] = []
        for image_ref in failed_image_refs:
            normalized = str(image_ref or "").strip()
            if normalized and normalized not in normalized_failed_refs:
                normalized_failed_refs.append(normalized)
        if not normalized_failed_refs:
            return
        self.upsert_cleanup_tombstone(
            {
                "run_id": run.id,
                "repository_id": run.repository_id,
                "workspace_path": "",
                "checkpoint_docker_image_refs": [],
                "validator_image_refs": normalized_failed_refs,
                "cleanup_paths": False,
            },
            reason="terminal_validator_image_cleanup",
        )
        self.record_event(
            run.id,
            actor="system",
            phase="cleanup",
            title="Validator image cleanup incomplete",
            message="Some validator images could not be removed after the run reached a terminal state",
            payload={
                "validator_image_refs": normalized_image_refs,
                "failed_validator_image_refs": normalized_failed_refs,
            },
        )
        self.record_cleanup_tombstone_result(
            run.id,
            cleanup_result={
                "failed_paths": [],
                "failed_checkpoint_docker_image_refs": [],
                "failed_validator_image_refs": normalized_failed_refs,
            },
        )
        self.session.flush()

    def request_run_interrupt(self, repository_id: int, run_id: str) -> Stage2Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=False)
        if run.status not in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value}:
            raise Stage2RunInterruptConflictError(f"cannot interrupt an inactive stage2 run: {run_id}")
        self.record_event(
            run.id,
            actor="system",
            phase=run.phase or "interrupt",
            title="Stage2 interrupt requested",
            message="User requested to interrupt this stage2 run",
            payload={"run_id": run.id, "repository_id": run.repository_id},
        )
        self.session.flush()
        return run

    def mark_run_interrupted(
        self,
        run_id: str,
        *,
        error_message: str = "stage2 run interrupted by user",
    ) -> Stage2Run:
        run = self.get_run(run_id, include_detail=False)
        run.status = Stage2RunStatus.completed.value
        run.result = Stage2RunResult.failed.value
        run.phase = "completed"
        run.finished_at = datetime.now(UTC)
        run.error_message = error_message
        self.record_event(
            run.id,
            actor="system",
            phase="failed",
            title="Stage2 run interrupted",
            message=error_message,
            payload={"run_id": run.id, "repository_id": run.repository_id},
        )
        self.session.flush()
        return run

    def mark_run_schedule_failed(
        self,
        run_id: str,
        *,
        error_message: str,
    ) -> Stage2Run:
        run = self.get_run(run_id, include_detail=False)
        run.status = Stage2RunStatus.completed.value
        run.result = Stage2RunResult.failed.value
        run.phase = "completed"
        run.finished_at = datetime.now(UTC)
        run.error_message = error_message
        run.summary_json = {
            **dict(run.summary_json or {}),
            "schedule_failed": True,
        }
        self.record_event(
            run.id,
            actor="system",
            phase="failed",
            title="Stage2 run scheduling failed",
            message=error_message,
            payload={"run_id": run.id, "repository_id": run.repository_id},
        )
        self.session.flush()
        return run

    def latest_run_for_repository(self, repository_id: int) -> Stage2Run | None:
        return self.session.scalar(
            select(Stage2Run)
            .where(Stage2Run.repository_id == repository_id)
            .order_by(Stage2Run.created_at.desc())
            .limit(1)
        )

    def active_run_for_repository(self, repository_id: int) -> Stage2Run | None:
        return self.session.scalar(
            select(Stage2Run)
            .where(
                Stage2Run.repository_id == repository_id,
                Stage2Run.status.in_([Stage2RunStatus.queued.value, Stage2RunStatus.running.value]),
            )
            .order_by(Stage2Run.created_at.desc())
            .limit(1)
        )

    def latest_successful_run_for_repository(self, repository_id: int) -> Stage2Run | None:
        return self.session.scalar(
            select(Stage2Run)
            .where(
                Stage2Run.repository_id == repository_id,
                Stage2Run.status == Stage2RunStatus.completed.value,
                Stage2Run.result == Stage2RunResult.passed.value,
            )
            .order_by(Stage2Run.created_at.desc())
            .limit(1)
        )

    def successful_run_for_repository_commit(
        self,
        repository_id: int,
        target_commit_sha: str,
    ) -> Stage2Run | None:
        target_commit_sha = str(target_commit_sha or "").strip()
        if not target_commit_sha:
            return None
        return self.session.scalar(
            select(Stage2Run)
            .where(
                Stage2Run.repository_id == repository_id,
                Stage2Run.status == Stage2RunStatus.completed.value,
                Stage2Run.result == Stage2RunResult.passed.value,
                Stage2Run.target_commit_sha == target_commit_sha,
            )
            .order_by(Stage2Run.created_at.desc())
            .limit(1)
        )

    def mark_run_started(self, run_id: str, *, phase: str) -> Stage2Run:
        run = self.get_run(run_id)
        run.status = Stage2RunStatus.running.value
        run.phase = phase
        run.started_at = run.started_at or datetime.now(UTC)
        run.finished_at = None
        run.error_message = None
        self.session.flush()
        return run

    def update_run_plan(
        self,
        run_id: str,
        *,
        phase: str,
        target_commit_sha: str | None = None,
        base_image: str | None = None,
        base_image_ref: str | None = None,
        selected_base_image: dict[str, Any] | None = None,
        planner_model: str | None = None,
        planner_token_usage: int | None = None,
        worker_model: str | None = None,
        worker_token_usage: int | None = None,
        planner_guidance: str | None = None,
        workspace_path: str | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.phase = phase
        if target_commit_sha is not None:
            run.target_commit_sha = target_commit_sha
        if base_image is not None:
            run.base_image = base_image
        selection_updates: dict[str, Any] = {}
        if base_image_ref is not None:
            normalized_base_image_ref = str(base_image_ref or "").strip()
            if normalized_base_image_ref:
                selection_updates["base_image_ref"] = normalized_base_image_ref
        if isinstance(selected_base_image, dict) and selected_base_image:
            selection_updates["selected_base_image"] = dict(selected_base_image)
            if "base_image_ref" not in selection_updates:
                normalized_selected_ref = str(selected_base_image.get("image_ref") or "").strip()
                if normalized_selected_ref:
                    selection_updates["base_image_ref"] = normalized_selected_ref
        if selection_updates:
            run.runtime_snapshot_json = merge_stage2_runtime_snapshot(
                dict(run.runtime_snapshot_json or {}),
                {"selection": selection_updates},
            )
        if planner_model is not None:
            run.planner_model = planner_model
        if planner_token_usage is not None:
            run.planner_token_usage = planner_token_usage
        if worker_model is not None:
            run.worker_model = worker_model
        if worker_token_usage is not None:
            run.worker_token_usage = worker_token_usage
        if planner_guidance is not None:
            run.planner_guidance = planner_guidance
        if workspace_path is not None:
            run.workspace_path = workspace_path
        self.session.flush()
        return run

    def update_run_runtime_snapshot(
        self,
        run_id: str,
        *,
        runtime_snapshot: dict[str, Any],
    ) -> Stage2Run:
        run = self.get_run(run_id, include_detail=False)
        run.runtime_snapshot_json = merge_stage2_runtime_snapshot(
            dict(run.runtime_snapshot_json or {}),
            runtime_snapshot,
        )
        self.session.flush()
        return run

    def update_repository_run_runtime_snapshot(
        self,
        repository_id: int,
        run_id: str,
        *,
        runtime_snapshot: dict[str, Any],
    ) -> Stage2Run:
        run = self.get_repository_run(repository_id, run_id, include_detail=False)
        if run.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value}:
            raise Stage2RunRuntimeConflictError(f"cannot modify runtime config for an active stage2 run: {run_id}")
        run.runtime_snapshot_json = merge_stage2_runtime_snapshot(
            dict(run.runtime_snapshot_json or {}),
            runtime_snapshot,
        )
        self.session.flush()
        return run

    def store_artifacts(
        self,
        run_id: str,
        *,
        dockerfile_text: str,
        run_script_text: str,
        attempt_index: int | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.dockerfile_text = dockerfile_text
        run.run_script_text = run_script_text
        if attempt_index is None:
            self._create_validation_attempt(
                run,
                dockerfile_text=dockerfile_text,
                run_script_text=run_script_text,
                validator_status="artifacts_ready",
            )
        else:
            attempt = self._get_or_create_validation_attempt_by_index(run, attempt_index)
            attempt.dockerfile_text = dockerfile_text
            attempt.run_script_text = run_script_text
            attempt.validator_status = "artifacts_ready"
            run.summary_json = {
                **dict(run.summary_json or {}),
                "latest_attempt_index": attempt.attempt_index,
            }
        self.session.flush()
        return run

    def store_collect_report(
        self,
        run_id: str,
        *,
        report: dict[str, Any],
        attempt_index: int | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.collect_report_json = report
        attempt = (
            self._get_or_create_latest_validation_attempt(run)
            if attempt_index is None
            else self._get_or_create_validation_attempt_by_index(run, attempt_index)
        )
        attempt.collect_report_json = report
        attempt.validator_status = "collect_completed"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "latest_attempt_index": attempt.attempt_index,
        }
        self.session.flush()
        return run

    def store_smoke_report(
        self,
        run_id: str,
        *,
        report: dict[str, Any],
        attempt_index: int | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.phase = "validator_smoke"
        run.smoke_report_json = report
        attempt = (
            self._get_or_create_latest_validation_attempt(run)
            if attempt_index is None
            else self._get_or_create_validation_attempt_by_index(run, attempt_index)
        )
        attempt.smoke_report_json = report
        attempt.validator_status = "smoke_completed"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "latest_attempt_index": attempt.attempt_index,
        }
        self.session.flush()
        return run

    def store_full_report(
        self,
        run_id: str,
        *,
        report: dict[str, Any],
        attempt_index: int | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.phase = "validator_full"
        run.full_report_json = report
        run.summary_json = dict(report.get("summary") or {})
        attempt = (
            self._get_or_create_latest_validation_attempt(run)
            if attempt_index is None
            else self._get_or_create_validation_attempt_by_index(run, attempt_index)
        )
        attempt.full_report_json = report
        attempt.validator_status = "full_completed"
        run.summary_json = {
            **dict(run.summary_json or {}),
            "latest_attempt_index": attempt.attempt_index,
        }
        self._replace_test_results(run, report.get("file_results") or [])
        self.session.flush()
        return run

    def store_validation_checkpoint(
        self,
        run_id: str,
        *,
        attempt_index: int,
        checkpoint: dict[str, Any],
    ) -> Stage2Run:
        run = self.get_run(run_id)
        for existing_attempt in run.validation_attempts:
            if existing_attempt.attempt_index != attempt_index and existing_attempt.checkpoint_json:
                existing_attempt.checkpoint_json = {}
        attempt = self._get_or_create_validation_attempt_by_index(run, attempt_index)
        attempt.checkpoint_json = dict(checkpoint or {})
        attempt.version_finalized = True
        run.summary_json = {
            **dict(run.summary_json or {}),
            "latest_attempt_index": attempt.attempt_index,
        }
        self.session.flush()
        return run

    def mark_run_completed(
        self,
        run_id: str,
        *,
        passed: bool | None = None,
        result: str | None = None,
        error_message: str | None = None,
    ) -> Stage2Run:
        run = self.get_run(run_id)
        run.status = Stage2RunStatus.completed.value
        if result is not None:
            run.result = result
        elif passed is not None:
            run.result = Stage2RunResult.passed.value if passed else Stage2RunResult.failed.value
        run.phase = "completed"
        run.finished_at = datetime.now(UTC)
        run.error_message = error_message
        self.session.flush()
        return run

    def validator_image_refs(self, run_id: str) -> list[str]:
        run = self.get_run(run_id)
        return self._validator_image_refs_for_run(run)

    def worker_checkpoint_cleanup_targets(self, run_id: str) -> dict[str, Any]:
        run = self.get_run(run_id)
        return {
            "checkpoint_paths": self._worker_checkpoint_paths_for_run(run),
            "checkpoint_docker_image_refs": self._checkpoint_docker_image_refs_for_run(run),
        }

    def clear_worker_checkpoints(self, run_id: str) -> Stage2Run:
        run = self.get_run(run_id)
        for attempt in run.validation_attempts:
            if attempt.checkpoint_json:
                attempt.checkpoint_json = {}
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        if "resume_checkpoint" in runtime_snapshot:
            runtime_snapshot["resume_checkpoint"] = {}
            run.runtime_snapshot_json = runtime_snapshot
        self.session.flush()
        return run

    def release_worker_checkpoints(self, run_id: str) -> dict[str, Any]:
        cleanup = self.worker_checkpoint_cleanup_targets(run_id)
        self.clear_worker_checkpoints(run_id)
        return cleanup

    def record_event(
        self,
        run_id: str,
        *,
        actor: str,
        phase: str | None,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> Stage2RunEvent:
        row = Stage2RunEvent(
            run_id=run_id,
            actor=actor,
            phase=phase,
            title=title,
            message=message,
            payload_json=payload or {},
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_repository_stage2_rows(
        self,
        repositories: list[GitHubRepository],
        *,
        running_repository_ids: set[int] | None = None,
    ) -> list[dict[str, Any]]:
        repository_ids = [repository.id for repository in repositories]
        if not repository_ids:
            return []

        run_rows = list(
            self.session.scalars(
                select(Stage2Run)
                .where(Stage2Run.repository_id.in_(repository_ids))
                .order_by(Stage2Run.repository_id.asc(), Stage2Run.created_at.desc())
            )
        )
        latest_by_repo: dict[int, Stage2Run] = {}
        history_count_by_repo: dict[int, int] = {}
        for row in run_rows:
            history_count_by_repo[row.repository_id] = history_count_by_repo.get(row.repository_id, 0) + 1
            latest_by_repo.setdefault(row.repository_id, row)

        running_repository_ids = running_repository_ids or set()
        payloads: list[dict[str, Any]] = []
        for repository in repositories:
            latest_run = latest_by_repo.get(repository.id)
            status = self._repository_stage2_status(
                latest_run,
                is_running=repository.id in running_repository_ids,
            )
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
                    "stage2": {
                        "status": status,
                        "history_count": history_count_by_repo.get(repository.id, 0),
                        "latest_run": self.serialize_run_summary(latest_run) if latest_run is not None else None,
                        "can_run": status == "pending",
                        "can_rerun": status in {"succeeded", "abandoned", "defect", "failed"},
                    },
                }
            )
        return payloads

    def build_status_filter_clause(
        self,
        statuses: list[str],
        *,
        running_repository_ids: set[int] | None = None,
    ) -> tuple[Any, Any]:
        latest_runs = self._latest_run_subquery()
        normalized_statuses = {status for status in statuses if status}
        terminal_completed = Stage2RunStatus.completed.value
        successful_terminal_results = {
            Stage2RunResult.passed.value,
            Stage2RunResult.abandoned.value,
            Stage2RunResult.defect.value,
        }
        clauses: list[Any] = []
        if "pending" in normalized_statuses:
            clauses.append(latest_runs.c.repository_id.is_(None))
        if "queued" in normalized_statuses:
            clauses.append(latest_runs.c.status == Stage2RunStatus.queued.value)
        if "running" in normalized_statuses:
            running_clause = latest_runs.c.status == Stage2RunStatus.running.value
            if running_repository_ids:
                running_clause = or_(running_clause, GitHubRepository.id.in_(running_repository_ids))
            clauses.append(running_clause)
        if "succeeded" in normalized_statuses:
            clauses.append(
                and_(
                    latest_runs.c.status == terminal_completed,
                    latest_runs.c.result == Stage2RunResult.passed.value,
                )
            )
        if "abandoned" in normalized_statuses:
            clauses.append(
                and_(
                    latest_runs.c.status == terminal_completed,
                    latest_runs.c.result == Stage2RunResult.abandoned.value,
                )
            )
        if "defect" in normalized_statuses:
            clauses.append(
                and_(
                    latest_runs.c.status == terminal_completed,
                    latest_runs.c.result == Stage2RunResult.defect.value,
                )
            )
        if "failed" in normalized_statuses:
            clauses.append(
                and_(
                    latest_runs.c.status == terminal_completed,
                    ~latest_runs.c.result.in_(successful_terminal_results),
                )
            )
        return latest_runs, or_(*clauses) if clauses else None

    def latest_run_listing_subquery(self) -> Any:
        return self._latest_run_subquery()

    def _repository_stage2_status(self, latest_run: Stage2Run | None, *, is_running: bool) -> str:
        if latest_run is None:
            return "pending"
        if is_running or latest_run.status == Stage2RunStatus.running.value:
            return "running"
        if latest_run.status == Stage2RunStatus.queued.value:
            return "queued"
        if latest_run.result == Stage2RunResult.passed.value:
            return "succeeded"
        if latest_run.result == Stage2RunResult.abandoned.value:
            return "abandoned"
        if latest_run.result == Stage2RunResult.defect.value:
            return "defect"
        return "failed"

    def serialize_run_summary(self, run: Stage2Run | None) -> dict[str, Any] | None:
        if run is None:
            return None
        display_status = self._run_display_status(run)
        resume_kind = self.resume_kind_for_run(run)
        checkpoint_resume_enabled = bool(get_settings().stage2_enable_checkpoints)
        summary = self._serialized_run_summary_metrics(run)
        usage_metrics = self._serialized_run_usage_metrics(run)
        duration_seconds = usage_metrics["duration_seconds"]
        return {
            "id": run.id,
            "status": run.status,
            "result": run.result,
            "display_status": display_status,
            "display_result": display_status if run.status == Stage2RunStatus.completed.value else run.status,
            "phase": run.phase,
            "trigger_kind": run.trigger_kind,
            "target_branch": run.target_branch,
            "target_commit_sha": run.target_commit_sha,
            "base_image": run.base_image,
            "planner_model": run.planner_model,
            "planner_token_usage": run.planner_token_usage,
            "worker_model": run.worker_model,
            "worker_token_usage": run.worker_token_usage,
            "token_usage_by_model": dict(usage_metrics["token_usage_by_model"] or {}),
            "created_at": serialize_datetime(run.created_at),
            "updated_at": serialize_datetime(run.updated_at),
            "started_at": serialize_datetime(run.started_at),
            "finished_at": serialize_datetime(run.finished_at),
            "duration_seconds": duration_seconds,
            "is_running": run.status == Stage2RunStatus.running.value,
            "is_active": run.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value},
            "can_rerun_worker": checkpoint_resume_enabled and self.can_rerun_worker_for_run(run),
            "can_resume_worker": resume_kind is not None,
            "resume_kind": resume_kind,
            "summary": summary,
            "error_message": _expanded_stage2_error_message(run),
        }

    def serialize_run_card_summary(self, run: Stage2Run | None) -> dict[str, Any] | None:
        if run is None:
            return None
        display_status = self._run_display_status(run)
        resume_kind = self.resume_kind_for_run(run)
        checkpoint_resume_enabled = bool(get_settings().stage2_enable_checkpoints)
        summary = self._serialized_run_summary_metrics(run)
        usage_metrics = self._serialized_run_usage_metrics(run)
        duration_seconds = usage_metrics["duration_seconds"]
        return {
            "id": run.id,
            "status": run.status,
            "result": run.result,
            "display_status": display_status,
            "display_result": display_status if run.status == Stage2RunStatus.completed.value else run.status,
            "phase": run.phase,
            "trigger_kind": run.trigger_kind,
            "target_branch": run.target_branch,
            "target_commit_sha": run.target_commit_sha,
            "base_image": run.base_image,
            "created_at": serialize_datetime(run.created_at),
            "updated_at": serialize_datetime(run.updated_at),
            "started_at": serialize_datetime(run.started_at),
            "finished_at": serialize_datetime(run.finished_at),
            "duration_seconds": duration_seconds,
            "token_usage_by_model": dict(usage_metrics["token_usage_by_model"] or {}),
            "is_running": run.status == Stage2RunStatus.running.value,
            "is_active": run.status in {Stage2RunStatus.queued.value, Stage2RunStatus.running.value},
            "can_rerun_worker": checkpoint_resume_enabled and self.can_rerun_worker_for_run(run),
            "can_resume_worker": resume_kind is not None,
            "resume_kind": resume_kind,
            "summary": summary,
        }

    def serialize_run_detail(self, run: Stage2Run) -> dict[str, Any]:
        payload = self.serialize_run_summary(run) or {}
        payload.update(
            {
                "planner_guidance": run.planner_guidance,
                "planner_upd_dockerfile": self._planner_upd_dockerfile(run),
                "dockerfile_text": run.dockerfile_text,
                "run_script_text": run.run_script_text,
                "workspace_path": run.workspace_path,
                "runtime_snapshot": redact_stage2_runtime_snapshot(dict(run.runtime_snapshot_json or {})),
                "collect_report": dict(run.collect_report_json or {}),
                "smoke_report": dict(run.smoke_report_json or {}),
                "full_report": dict(run.full_report_json or {}),
                "validation_attempts": self._serialized_validation_attempts(run),
                "draft_validation_attempt": self._serialized_draft_validation_attempt(run),
                "llm_completion_archives": summarize_run_llm_completion_archives(
                    workspace_path=run.workspace_path,
                    run_id=run.id,
                ),
                "events": [
                    {
                        "id": row.id,
                        "actor": row.actor,
                        "phase": row.phase,
                        "title": row.title,
                        "message": row.message,
                        "payload": dict(row.payload_json or {}),
                        "created_at": serialize_datetime(row.created_at),
                    }
                    for row in run.events
                ],
                "test_results": [
                    {
                        "test_file_path": row.test_file_path,
                        "target_selector": row.target_selector or row.test_file_path,
                        "status": row.status,
                        "total_tests": row.total_tests,
                        "passed_tests": row.passed_tests,
                        "failed_tests": row.failed_tests,
                        "error_tests": row.error_tests,
                        "skipped_tests": row.skipped_tests,
                        "exit_code": row.exit_code,
                        "raw_result": dict(row.raw_result_json or {}),
                    }
                    for row in run.test_results
                ],
            }
        )
        return payload

    def _planner_upd_dockerfile(self, run: Stage2Run) -> str:
        for event in reversed(list(run.events or [])):
            if str(event.title or "") != "Planner reported base image defect":
                continue
            payload = dict(event.payload_json or {})
            upd_dockerfile = str(payload.get("upd_dockerfile") or "")
            if upd_dockerfile.strip():
                return upd_dockerfile
        return ""

    def serialize_validation_attempt(self, attempt: Stage2ValidationAttempt) -> dict[str, Any]:
        return {
            "id": attempt.id,
            "attempt_index": attempt.attempt_index,
            "dockerfile_text": attempt.dockerfile_text,
            "run_script_text": attempt.run_script_text,
            "collect_report": dict(attempt.collect_report_json or {}),
            "smoke_report": dict(attempt.smoke_report_json or {}),
            "full_report": dict(attempt.full_report_json or {}),
            "checkpoint": dict(attempt.checkpoint_json or {}),
            "version_finalized": self._is_finalized_validation_attempt(attempt),
            "validator_status": attempt.validator_status,
            "error_message": attempt.error_message,
            "created_at": serialize_datetime(attempt.created_at),
            "updated_at": serialize_datetime(attempt.updated_at),
        }

    def _serialized_run_summary_metrics(self, run: Stage2Run) -> dict[str, Any]:
        summary = dict(run.summary_json or {})
        latest_attempt_index = self._effective_latest_attempt_index(run)
        if latest_attempt_index > 0:
            summary["latest_attempt_index"] = latest_attempt_index
        return summary

    def _build_materialized_resume_state(
        self,
        source_run: Stage2Run,
        *,
        source_attempt_index: int,
    ) -> dict[str, Any]:
        normalized_attempt_index = max(int(source_attempt_index or 0), 0)
        if normalized_attempt_index <= 0:
            return {}
        attempts: list[dict[str, Any]] = []
        for payload in self._serialized_validation_attempts(source_run):
            attempt_index = int(payload.get("attempt_index") or 0)
            if attempt_index <= 0 or attempt_index > normalized_attempt_index:
                continue
            materialized_payload = dict(payload)
            materialized_payload["checkpoint"] = {}
            attempts.append(materialized_payload)
        usage_metrics = self._serialized_run_usage_metrics(source_run)
        duration_seconds = usage_metrics.get("duration_seconds")
        token_usage_by_model = dict(usage_metrics.get("token_usage_by_model") or {})
        return {
            "schema_version": 1,
            "source_run_id": str(source_run.id or ""),
            "source_attempt_index": normalized_attempt_index,
            "latest_attempt_index": normalized_attempt_index,
            "duration_seconds": duration_seconds,
            "token_usage_by_model": token_usage_by_model,
            "validation_attempts": attempts,
            "created_at": serialize_datetime(datetime.now(UTC)),
        }

    def _build_materialized_inherited_usage_state(
        self,
        source_run: Stage2Run,
        *,
        source_kind: str,
    ) -> dict[str, Any]:
        usage_metrics = self._serialized_run_usage_metrics(source_run)
        return {
            "schema_version": 1,
            "source_kind": source_kind,
            "source_run_id": str(source_run.id or ""),
            "duration_seconds": usage_metrics.get("duration_seconds"),
            "token_usage_by_model": dict(usage_metrics.get("token_usage_by_model") or {}),
            "validation_attempts": [],
            "created_at": serialize_datetime(datetime.now(UTC)),
        }

    def _materialized_resume_state(self, run: Stage2Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        payload = runtime_snapshot.get("resume_materialized")
        return dict(payload) if isinstance(payload, dict) else {}

    def _materialized_resume_validation_attempts(self, run: Stage2Run | None) -> list[dict[str, Any]]:
        materialized = self._materialized_resume_state(run)
        payloads = materialized.get("validation_attempts")
        if not isinstance(payloads, list):
            return []
        attempts: list[dict[str, Any]] = []
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            attempt_index = int(payload.get("attempt_index") or 0)
            if attempt_index <= 0:
                continue
            materialized_payload = dict(payload)
            materialized_payload["checkpoint"] = {}
            attempts.append(materialized_payload)
        attempts.sort(key=lambda payload: int(payload.get("attempt_index") or 0))
        return attempts

    def _materialized_resume_usage_metrics(self, run: Stage2Run | None) -> dict[str, Any]:
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

    def _serialized_run_usage_metrics(
        self,
        run: Stage2Run,
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
            source_run, _ = self._resume_source_context(run, include_detail=False)
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
            preferred_models=self._runtime_token_usage_models(run),
        )

        return {
            "duration_seconds": duration_seconds,
            "token_usage_by_model": token_usage_by_model,
        }

    def _own_run_duration_seconds(self, run: Stage2Run) -> float | None:
        if not run.started_at:
            return None
        end_time = run.finished_at or datetime.now(UTC)
        return max((end_time - run.started_at).total_seconds(), 0.0)

    def _own_run_token_usage_by_model(self, run: Stage2Run) -> dict[str, int]:
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        entries = [
            (
                str(
                    run.planner_model
                    or ((runtime_snapshot.get("planner") or {}).get("model"))
                    or ""
                ).strip(),
                int(run.planner_token_usage or 0),
            ),
            (
                str(
                    run.worker_model
                    or ((runtime_snapshot.get("worker") or {}).get("model"))
                    or ""
                ).strip(),
                int(run.worker_token_usage or 0),
            ),
        ]
        return aggregate_token_usage_by_model(
            entries,
            preferred_models=self._runtime_token_usage_models(run),
        )

    def _runtime_token_usage_models(self, run: Stage2Run) -> list[str]:
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        return [
            str(((runtime_snapshot.get("planner") or {}).get("model")) or "").strip(),
            str(((runtime_snapshot.get("worker") or {}).get("model")) or "").strip(),
        ]

    def _effective_latest_attempt_index(self, run: Stage2Run) -> int:
        summary_attempt_index = int((run.summary_json or {}).get("latest_attempt_index") or 0)
        owned_attempt_index = max(
            (
                int(attempt.attempt_index or 0)
                for attempt in list(run.validation_attempts or [])
                if self._is_finalized_validation_attempt(attempt)
            ),
            default=0,
        )
        materialized_attempt_index = int(
            self._materialized_resume_state(run).get("latest_attempt_index") or 0
        )
        resume_attempt_index = int(
            (dict(run.runtime_snapshot_json or {})).get("resume_source_attempt_index") or 0
        )
        return max(summary_attempt_index, owned_attempt_index, materialized_attempt_index, resume_attempt_index)

    def _resume_source_context(
        self,
        run: Stage2Run,
        *,
        include_detail: bool = False,
    ) -> tuple[Stage2Run | None, int]:
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        source_run_id = str(runtime_snapshot.get("resume_source_run_id") or "").strip()
        source_attempt_index = int(runtime_snapshot.get("resume_source_attempt_index") or 0)
        if not source_run_id or source_attempt_index <= 0 or source_run_id == str(run.id):
            return None, 0
        try:
            source_run = self.get_run(source_run_id, include_detail=include_detail)
        except ValueError:
            return None, 0
        return source_run, source_attempt_index

    def repository_summary_payload(
        self,
        repository_id: int,
        *,
        running_repository_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        repository = self.get_repository(repository_id)
        runs = self.list_repository_runs(repository_id, include_detail=False)
        repo_row = self.list_repository_stage2_rows(
            [repository],
            running_repository_ids=running_repository_ids,
        )[0]
        return {
            "repository": repo_row,
            "runs": [self.serialize_run_card_summary(run) for run in runs],
        }

    def repository_detail_payload(
        self,
        repository_id: int,
        *,
        selected_run_id: str | None = None,
        running_repository_ids: set[int] | None = None,
    ) -> dict[str, Any]:
        payload = self.repository_summary_payload(
            repository_id,
            running_repository_ids=running_repository_ids,
        )
        runs = payload["runs"]
        selected_run_summary = next(
            (run for run in runs if str(run["id"]) == str(selected_run_id)),
            None,
        )
        if selected_run_summary is None and runs:
            selected_run_summary = runs[0]
        payload["selected_run"] = (
            self.serialize_run_detail(
                self.get_repository_run(
                    repository_id,
                    selected_run_summary["id"],
                    include_detail=True,
                )
            )
            if selected_run_summary is not None
            else None
        )
        return payload

    def run_detail_payload(self, repository_id: int, run_id: str) -> dict[str, Any]:
        run = self.get_repository_run(repository_id, run_id, include_detail=True)
        return {"run": self.serialize_run_detail(run)}

    def _serialized_validation_attempts(
        self,
        run: Stage2Run,
        *,
        _seen_run_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        seen_run_ids = set(_seen_run_ids or set())
        if str(run.id) in seen_run_ids:
            return []
        seen_run_ids.add(str(run.id))

        attempts_by_index: dict[int, dict[str, Any]] = {}
        source_run, source_attempt_index = self._resume_source_context(run, include_detail=True)
        resume_source_attempt_index = int(
            (dict(run.runtime_snapshot_json or {})).get("resume_source_attempt_index")
            or source_attempt_index
            or 0
        )
        inherited_attempts = self._materialized_resume_validation_attempts(run)
        if not inherited_attempts and source_run is not None and resume_source_attempt_index > 0:
            inherited_attempts = self._serialized_validation_attempts(
                source_run,
                _seen_run_ids=seen_run_ids,
            )
        resume_checkpoint = dict((run.runtime_snapshot_json or {}).get("resume_checkpoint") or {})
        for payload in inherited_attempts:
            attempt_index = int(payload.get("attempt_index") or 0)
            if attempt_index <= 0:
                continue
            inherited_payload = dict(payload)
            inherited_payload["checkpoint"] = (
                dict(resume_checkpoint)
                if resume_source_attempt_index > 0
                and attempt_index == resume_source_attempt_index
                and resume_checkpoint
                else {}
            )
            attempts_by_index[attempt_index] = inherited_payload

        for attempt in list(run.validation_attempts or []):
            if not self._is_finalized_validation_attempt(attempt):
                continue
            attempt_index = int(attempt.attempt_index or 0)
            if attempt_index <= 0:
                continue
            attempts_by_index[attempt_index] = self.serialize_validation_attempt(attempt)

        return [attempts_by_index[index] for index in sorted(attempts_by_index)]

    def _serialized_draft_validation_attempt(self, run: Stage2Run) -> dict[str, Any] | None:
        draft_attempts = [
            attempt
            for attempt in list(run.validation_attempts or [])
            if not self._is_finalized_validation_attempt(attempt)
            and self._attempt_has_any_assets(
                dockerfile_text=attempt.dockerfile_text,
                run_script_text=attempt.run_script_text,
                collect_report=attempt.collect_report_json,
                smoke_report=attempt.smoke_report_json,
                full_report=attempt.full_report_json,
            )
        ]
        if draft_attempts:
            draft_attempts.sort(
                key=lambda attempt: (attempt.attempt_index, attempt.updated_at or attempt.created_at)
            )
            attempt = draft_attempts[-1]
            payload = self.serialize_validation_attempt(attempt)
            payload["label"] = "临时文件"
            return payload

        if self._attempt_has_any_assets(
            dockerfile_text=run.dockerfile_text,
            run_script_text=run.run_script_text,
            collect_report=run.collect_report_json,
            smoke_report=run.smoke_report_json,
            full_report=run.full_report_json,
        ):
            finalized_attempts = [
                attempt
                for attempt in list(run.validation_attempts or [])
                if self._is_finalized_validation_attempt(attempt)
            ]
            latest_finalized = finalized_attempts[-1] if finalized_attempts else None
            if latest_finalized is None or self._run_assets_differ_from_attempt(run, latest_finalized):
                return {
                    "id": None,
                    "attempt_index": None,
                    "label": "临时文件",
                    "dockerfile_text": run.dockerfile_text,
                    "run_script_text": run.run_script_text,
                    "collect_report": dict(run.collect_report_json or {}),
                    "smoke_report": dict(run.smoke_report_json or {}),
                    "full_report": dict(run.full_report_json or {}),
                    "checkpoint": {},
                    "version_finalized": False,
                    "validator_status": None,
                    "error_message": None,
                    "created_at": serialize_datetime(run.created_at),
                    "updated_at": serialize_datetime(run.updated_at),
                }
        return None

    def _is_finalized_validation_attempt(self, attempt: Stage2ValidationAttempt) -> bool:
        if bool(attempt.version_finalized):
            return True
        if dict(attempt.checkpoint_json or {}):
            return True
        if dict(attempt.full_report_json or {}):
            return True
        return False

    def _attempt_has_any_assets(
        self,
        *,
        dockerfile_text: str | None,
        run_script_text: str | None,
        collect_report: dict[str, Any] | None,
        smoke_report: dict[str, Any] | None,
        full_report: dict[str, Any] | None,
    ) -> bool:
        return bool(
            str(dockerfile_text or "").strip()
            or str(run_script_text or "").strip()
            or dict(collect_report or {})
            or dict(smoke_report or {})
            or dict(full_report or {})
        )

    def _run_assets_differ_from_attempt(
        self,
        run: Stage2Run,
        attempt: Stage2ValidationAttempt,
    ) -> bool:
        return any(
            [
                str(run.dockerfile_text or "") != str(attempt.dockerfile_text or ""),
                str(run.run_script_text or "") != str(attempt.run_script_text or ""),
                dict(run.collect_report_json or {}) != dict(attempt.collect_report_json or {}),
                dict(run.smoke_report_json or {}) != dict(attempt.smoke_report_json or {}),
                dict(run.full_report_json or {}) != dict(attempt.full_report_json or {}),
            ]
        )

    def _create_validation_attempt(
        self,
        run: Stage2Run,
        *,
        dockerfile_text: str | None = None,
        run_script_text: str | None = None,
        validator_status: str | None = None,
    ) -> Stage2ValidationAttempt:
        latest_index = max((attempt.attempt_index for attempt in run.validation_attempts), default=0)
        attempt = Stage2ValidationAttempt(
            run=run,
            attempt_index=latest_index + 1,
            dockerfile_text=dockerfile_text,
            run_script_text=run_script_text,
            validator_status=validator_status,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def _get_or_create_latest_validation_attempt(self, run: Stage2Run) -> Stage2ValidationAttempt:
        if run.validation_attempts:
            return max(run.validation_attempts, key=lambda attempt: attempt.attempt_index)
        return self._create_validation_attempt(
            run,
            dockerfile_text=run.dockerfile_text,
            run_script_text=run.run_script_text,
        )

    def _get_or_create_validation_attempt_by_index(
        self,
        run: Stage2Run,
        attempt_index: int,
    ) -> Stage2ValidationAttempt:
        target_index = max(int(attempt_index or 0), 1)
        for attempt in run.validation_attempts:
            if attempt.attempt_index == target_index:
                return attempt
        attempt = Stage2ValidationAttempt(
            run=run,
            attempt_index=target_index,
            dockerfile_text=run.dockerfile_text,
            run_script_text=run.run_script_text,
        )
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def _terminal_stage2_status(self, run: Stage2Run) -> str:
        if run.result == Stage2RunResult.passed.value:
            return "succeeded"
        if run.result == Stage2RunResult.abandoned.value:
            return "abandoned"
        if run.result == Stage2RunResult.defect.value:
            return "defect"
        return self._failed_display_status(run)

    def _run_display_status(self, run: Stage2Run) -> str:
        if run.status == Stage2RunStatus.queued.value:
            return "queued"
        if run.status == Stage2RunStatus.running.value:
            return "running"
        return self._terminal_stage2_status(run)

    def _failed_display_status(self, run: Stage2Run) -> str:
        error_message = str(run.error_message or "").strip().lower()
        if "interrupted by user" in error_message:
            return "interrupted"

        if bool((run.summary_json or {}).get("schedule_failed")):
            return "schedule_failed"

        if self._is_full_validation_resume_pending(run):
            return "full_test_error"

        latest_attempt_index = self._latest_failure_attempt_index(run)
        if latest_attempt_index > 0:
            return f"attempt{latest_attempt_index}_failed"

        resume_baseline_attempt_index = self._resume_baseline_attempt_index(run)
        if resume_baseline_attempt_index > 0:
            return f"worker_failed_before_attempt{resume_baseline_attempt_index + 1}"

        if str(run.planner_guidance or "").strip() or str(run.base_image or "").strip():
            return "worker_failed"
        if "interrupted before" in error_message:
            return "interrupted"
        return "planner_failed"

    def _latest_failure_attempt_index(self, run: Stage2Run) -> int:
        attempts = [
            attempt
            for attempt in list(run.validation_attempts or [])
            if self._is_finalized_validation_attempt(attempt)
        ]
        if attempts:
            return max(int(attempt.attempt_index or 0) for attempt in attempts)
        return 0

    def _resume_baseline_attempt_index(self, run: Stage2Run | None) -> int:
        if run is None:
            return 0
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        materialized_attempt_index = int(
            self._materialized_resume_state(run).get("latest_attempt_index") or 0
        )
        runtime_attempt_index = int(runtime_snapshot.get("resume_source_attempt_index") or 0)
        return max(materialized_attempt_index, runtime_attempt_index)

    def can_rerun_worker_for_run(self, run: Stage2Run | None) -> bool:
        if run is None:
            return False
        if run.status != Stage2RunStatus.completed.value:
            return False
        if run.result == Stage2RunResult.passed.value:
            return False
        target_commit_sha = str(run.target_commit_sha or "").strip()
        planner_guidance = str(run.planner_guidance or "").strip()
        base_image = str(run.base_image or "").strip()
        return bool(target_commit_sha and planner_guidance and base_image)

    def can_resume_worker_for_run(self, run: Stage2Run | None) -> bool:
        return self.resume_kind_for_run(run) is not None

    def latest_resume_checkpoint_attempt(self, run: Stage2Run | None) -> Stage2ValidationAttempt | None:
        resume_kind = self.resume_kind_for_run(run)
        if resume_kind == "full_validation":
            return self._latest_full_validation_resume_attempt(run)
        return self._latest_worker_resume_attempt(run)

    def resume_kind_for_run(self, run: Stage2Run | None) -> str | None:
        if run is None:
            return None
        if not get_settings().stage2_enable_checkpoints:
            return None
        if not self.can_rerun_worker_for_run(run):
            return None
        if self._is_full_validation_resume_pending(run):
            return "full_validation"
        if (
            self._latest_worker_resume_attempt(run) is not None
            or self._can_resume_worker_checkpoint_payload(self._runtime_snapshot_resume_checkpoint(run))
        ):
            return "worker"
        return None

    def _is_full_validation_resume_pending(self, run: Stage2Run | None) -> bool:
        if run is None:
            return False
        if run.status != Stage2RunStatus.completed.value:
            return False
        if run.result == Stage2RunResult.passed.value:
            return False
        if "interrupted by user" in str(run.error_message or "").strip().lower():
            return False
        attempt = self._latest_full_validation_resume_attempt(run)
        if attempt is None:
            return False
        if dict(attempt.full_report_json or {}):
            return False
        if dict(run.full_report_json or {}):
            return False
        return True

    def _latest_full_validation_resume_attempt(self, run: Stage2Run | None) -> Stage2ValidationAttempt | None:
        if run is None:
            return None
        attempts = [
            attempt
            for attempt in run.validation_attempts
            if self._can_resume_full_validation_attempt(attempt)
        ]
        if not attempts:
            return None
        attempts.sort(key=lambda attempt: (attempt.attempt_index, attempt.updated_at or attempt.created_at))
        return attempts[-1]

    def _can_resume_full_validation_attempt(self, attempt: Stage2ValidationAttempt) -> bool:
        checkpoint = dict(attempt.checkpoint_json or {})
        validation = dict(checkpoint.get("validation") or {})
        workspace_snapshot_path = str(checkpoint.get("workspace_snapshot_path") or "").strip()
        if str(validation.get("result") or "").strip() != "smoke_passed":
            return False
        if not workspace_snapshot_path:
            return False
        return Path(workspace_snapshot_path).exists()

    def _latest_worker_resume_attempt(self, run: Stage2Run | None) -> Stage2ValidationAttempt | None:
        if run is None:
            return None
        attempts = [
            attempt
            for attempt in run.validation_attempts
            if self._can_resume_worker_attempt(attempt)
        ]
        if not attempts:
            return None
        attempts.sort(key=lambda attempt: (attempt.attempt_index, attempt.updated_at or attempt.created_at))
        return attempts[-1]

    def _can_resume_worker_attempt(self, attempt: Stage2ValidationAttempt) -> bool:
        checkpoint = dict(attempt.checkpoint_json or {})
        return self._can_resume_worker_checkpoint_payload(checkpoint)

    def _runtime_snapshot_resume_checkpoint(self, run: Stage2Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        checkpoint = dict(runtime_snapshot.get("resume_checkpoint") or {})
        attempt_index = max(
            int(checkpoint.get("attempt_index") or 0),
            int(runtime_snapshot.get("resume_source_attempt_index") or 0),
        )
        if attempt_index > 0:
            checkpoint["attempt_index"] = attempt_index
        return checkpoint

    def _can_resume_worker_checkpoint_payload(self, checkpoint: dict[str, Any] | None) -> bool:
        checkpoint = dict(checkpoint or {})
        workspace_snapshot_path = str(checkpoint.get("workspace_snapshot_path") or "").strip()
        conversation_id = str(checkpoint.get("conversation_id") or "").strip()
        conversations_path = str(((checkpoint.get("openhands") or {}).get("conversations_path")) or "").strip()
        bash_events_dir = str(((checkpoint.get("openhands") or {}).get("bash_events_dir")) or "").strip()
        docker_commit_status = str(checkpoint.get("docker_commit_status") or "").strip()
        docker_image_ref = str(checkpoint.get("docker_image_ref") or "").strip()
        try:
            conversation_dir_name = uuid.UUID(conversation_id).hex
        except ValueError:
            return False
        conversation_dir = Path(conversations_path) / conversation_dir_name
        if (
            not workspace_snapshot_path
            or not conversation_id
            or not conversations_path
            or not bash_events_dir
            or docker_commit_status != "saved"
            or not docker_image_ref
        ):
            return False
        if not Path(workspace_snapshot_path).exists():
            return False
        if not Path(conversations_path).exists():
            return False
        if not conversation_dir.exists():
            return False
        if not Path(bash_events_dir).exists():
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

    def _selection_snapshot_for_run(self, run: Stage2Run | None) -> dict[str, Any]:
        if run is None:
            return {}
        runtime_snapshot = dict(run.runtime_snapshot_json or {})
        existing_selection = runtime_snapshot.get("selection")
        if isinstance(existing_selection, dict) and existing_selection:
            return dict(existing_selection)

        base_image_ref = str(runtime_snapshot.get("base_image_ref") or "").strip()
        if not base_image_ref:
            for event in reversed(list(run.events or [])):
                payload = dict(event.payload_json or {})
                base_image_ref = str(payload.get("base_image_ref") or "").strip()
                if base_image_ref:
                    break

        selected_base_image = self._catalog_base_image_payload(
            base_image_id=str(run.base_image or "").strip(),
            base_image_ref=base_image_ref,
        )
        selection_snapshot: dict[str, Any] = {}
        if base_image_ref:
            selection_snapshot["base_image_ref"] = base_image_ref
        if selected_base_image:
            selection_snapshot["selected_base_image"] = selected_base_image
            selection_snapshot.setdefault(
                "base_image_ref",
                str(selected_base_image.get("image_ref") or "").strip(),
            )
        return selection_snapshot

    def _catalog_base_image_payload(
        self,
        *,
        base_image_id: str,
        base_image_ref: str,
    ) -> dict[str, Any]:
        normalized_id = str(base_image_id or "").strip()
        normalized_ref = str(base_image_ref or "").strip()
        for candidate in list_base_images():
            payload = candidate.to_payload()
            candidate_id = str(payload.get("image_id") or "").strip()
            candidate_ref = str(payload.get("image_ref") or "").strip()
            if normalized_ref and candidate_ref == normalized_ref:
                return dict(payload)
            if normalized_id and candidate_id == normalized_id:
                return dict(payload)
        return {}

    def _checkpoint_docker_image_refs_for_run(self, run: Stage2Run) -> list[str]:
        refs: list[str] = []

        def add_from_checkpoint(checkpoint: dict[str, Any] | None) -> None:
            image_ref = str((dict(checkpoint or {})).get("docker_image_ref") or "").strip()
            if image_ref and image_ref not in refs:
                refs.append(image_ref)

        add_from_checkpoint(dict((run.runtime_snapshot_json or {}).get("resume_checkpoint") or {}))
        for attempt in run.validation_attempts:
            add_from_checkpoint(dict(attempt.checkpoint_json or {}))
        return refs

    def _validator_image_refs_for_run(self, run: Stage2Run) -> list[str]:
        refs: list[str] = []

        def add_from_report(report: dict[str, Any] | None) -> None:
            image_ref = str((dict(report or {})).get("image_tag") or "").strip()
            if image_ref and image_ref not in refs:
                refs.append(image_ref)

        add_from_report(dict(run.smoke_report_json or {}))
        add_from_report(dict(run.full_report_json or {}))
        for attempt in run.validation_attempts:
            add_from_report(dict(attempt.smoke_report_json or {}))
            add_from_report(dict(attempt.full_report_json or {}))
        return refs

    def _worker_checkpoint_paths_for_run(self, run: Stage2Run) -> list[str]:
        refs: list[str] = []

        def add_path(path: Path | None) -> None:
            if path is None:
                return
            normalized = str(path.expanduser())
            if normalized and normalized not in refs:
                refs.append(normalized)

        runtime_dir = runtime_dir_from_workspace_path(run.workspace_path, run_id=run.id)
        if runtime_dir is not None:
            add_path(runtime_dir / "worker-checkpoint")

        def add_from_checkpoint(checkpoint: dict[str, Any] | None) -> None:
            payload = dict(checkpoint or {})
            checkpoint_dir_raw = str(payload.get("checkpoint_dir") or "").strip()
            if checkpoint_dir_raw:
                checkpoint_dir = Path(checkpoint_dir_raw).expanduser()
                if checkpoint_dir.name == "current" and checkpoint_dir.parent.name == "worker-checkpoint":
                    add_path(checkpoint_dir.parent)
                else:
                    add_path(checkpoint_dir)
                return
            workspace_snapshot_raw = str(payload.get("workspace_snapshot_path") or "").strip()
            if workspace_snapshot_raw:
                snapshot_path = Path(workspace_snapshot_raw).expanduser()
                current_dir = snapshot_path.parent
                if current_dir.name == "current" and current_dir.parent.name == "worker-checkpoint":
                    add_path(current_dir.parent)

        add_from_checkpoint(dict((run.runtime_snapshot_json or {}).get("resume_checkpoint") or {}))
        for attempt in run.validation_attempts:
            add_from_checkpoint(dict(attempt.checkpoint_json or {}))
        return refs

    def _clone_checkpoint_for_resume_run(
        self,
        *,
        checkpoint: dict[str, Any],
        source_run: Stage2Run,
        destination_run: Stage2Run,
        stage2_workspace_dir: str | Path | None,
        clone_docker_image: bool,
        runtime_snapshot: dict[str, Any] | None,
    ) -> dict[str, Any]:
        source_checkpoint = dict(checkpoint or {})
        if not source_checkpoint:
            return {}

        stage2_root = _infer_stage2_root_from_run_or_checkpoint(
            source_run=source_run,
            checkpoint=source_checkpoint,
            stage2_workspace_dir=stage2_workspace_dir,
        )
        if stage2_root is None:
            raise RuntimeError(
                "unable to determine the stage2 workspace root while cloning the worker checkpoint"
            )

        destination_runtime_dir = Path(stage2_root) / "runtime" / destination_run.id
        destination_workspace_dir = Path(stage2_root) / "runs" / destination_run.id
        checkpoint_root = destination_runtime_dir / "worker-checkpoint"
        current_dir = checkpoint_root / "current"
        temp_dir = checkpoint_root / f".tmp-{uuid.uuid4().hex}"
        cloned_image_ref = ""
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)

            workspace_snapshot_source = Path(
                str(source_checkpoint.get("workspace_snapshot_path") or "")
            ).expanduser()
            if not workspace_snapshot_source.exists():
                raise RuntimeError(
                    f"resume checkpoint workspace snapshot is missing: {workspace_snapshot_source}"
                )
            workspace_snapshot_destination = temp_dir / "workspace_snapshot.tar.gz"
            shutil.copy2(workspace_snapshot_source, workspace_snapshot_destination)

            source_openhands = dict(source_checkpoint.get("openhands") or {})
            destination_openhands = {
                **source_openhands,
                "conversations_path": "",
                "bash_events_dir": "",
            }
            conversation_id = str(source_checkpoint.get("conversation_id") or "").strip()
            if conversation_id:
                source_conversation_dir = _checkpoint_conversation_directory(source_checkpoint)
                conversation_dir_name = uuid.UUID(conversation_id).hex
                destination_conversations_path = temp_dir / "openhands" / "conversations"
                destination_conversation_dir = destination_conversations_path / conversation_dir_name
                if _copytree_if_exists(source_conversation_dir, destination_conversation_dir):
                    _patch_openhands_conversation_llm_state(
                        destination_conversation_dir,
                        runtime_snapshot=runtime_snapshot,
                        completion_archive_dir=llm_completion_archive_dir(destination_runtime_dir, "worker"),
                    )
                    rewrite_stage2_json_tree_path_references(
                        destination_conversation_dir,
                        stage2_root=Path(stage2_root),
                        destination_run_id=destination_run.id,
                    )
                    destination_openhands["conversations_path"] = str(
                        current_dir / "openhands" / "conversations"
                    )
            source_bash_events_dir = _checkpoint_bash_events_directory(source_checkpoint)
            destination_bash_events_dir = temp_dir / "openhands" / "bash_events"
            if _copytree_if_exists(source_bash_events_dir, destination_bash_events_dir):
                rewrite_stage2_json_tree_path_references(
                    destination_bash_events_dir,
                    stage2_root=Path(stage2_root),
                    destination_run_id=destination_run.id,
                )
                destination_openhands["bash_events_dir"] = str(current_dir / "openhands" / "bash_events")

            cloned_checkpoint = dict(source_checkpoint)
            cloned_checkpoint, _ = rewrite_stage2_path_references(
                cloned_checkpoint,
                stage2_root=Path(stage2_root),
                destination_run_id=destination_run.id,
            )
            cloned_checkpoint["run_id"] = destination_run.id
            cloned_checkpoint["checkpoint_dir"] = str(current_dir)
            cloned_checkpoint["workspace_snapshot_path"] = str(current_dir / "workspace_snapshot.tar.gz")
            cloned_checkpoint["workspace_dir"] = str(destination_workspace_dir)
            cloned_checkpoint["openhands"] = destination_openhands
            cloned_checkpoint["cloned_from_run_id"] = source_run.id
            cloned_checkpoint["cloned_for_run_id"] = destination_run.id
            cloned_checkpoint["cloned_at"] = serialize_datetime(datetime.now(UTC))
            workspace_snapshot_payload = dict(cloned_checkpoint.get("workspace_snapshot") or {})
            if workspace_snapshot_payload:
                workspace_snapshot_payload["path"] = str(current_dir / "workspace_snapshot.tar.gz")
                cloned_checkpoint["workspace_snapshot"] = workspace_snapshot_payload

            source_image_ref = str(source_checkpoint.get("docker_image_ref") or "").strip()
            if clone_docker_image:
                cloned_image_ref = _retag_checkpoint_image(
                    source_image_ref=source_image_ref,
                    destination_run_id=destination_run.id,
                    attempt_index=int(source_checkpoint.get("attempt_index") or 0),
                )
                cloned_checkpoint["docker_image_ref"] = cloned_image_ref
                cloned_checkpoint["docker_commit_status"] = "saved"
                docker_commit = dict(source_checkpoint.get("docker_commit") or {})
                docker_commit["docker_image_ref"] = cloned_image_ref
                cloned_checkpoint["docker_commit"] = docker_commit
            else:
                cloned_checkpoint["docker_image_ref"] = ""
                cloned_checkpoint["docker_commit_status"] = "not_required"
                docker_commit = dict(source_checkpoint.get("docker_commit") or {})
                if docker_commit:
                    docker_commit["docker_image_ref"] = ""
                    docker_commit["docker_commit_status"] = "not_required"
                    cloned_checkpoint["docker_commit"] = docker_commit

            _atomic_write_json(temp_dir / "checkpoint.json", cloned_checkpoint)
            if current_dir.exists():
                shutil.rmtree(current_dir)
            current_dir.parent.mkdir(parents=True, exist_ok=True)
            temp_dir.replace(current_dir)
            _copy_llm_completion_archives_for_resume_run(
                source_run=source_run,
                stage2_root=Path(stage2_root),
                destination_run_id=destination_run.id,
            )
            return cloned_checkpoint
        except Exception:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
            if current_dir.exists():
                shutil.rmtree(current_dir.parent, ignore_errors=True)
            if cloned_image_ref:
                try:
                    subprocess.run(
                        ["docker", "image", "rm", "-f", cloned_image_ref],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=60,
                    )
                except Exception:
                    pass
            raise

    def _latest_run_subquery(self) -> Any:
        ranked_runs = (
            select(
                Stage2Run.repository_id.label("repository_id"),
                Stage2Run.status.label("status"),
                Stage2Run.result.label("result"),
                Stage2Run.updated_at.label("latest_operation_at"),
                func.row_number().over(
                    partition_by=Stage2Run.repository_id,
                    order_by=(Stage2Run.created_at.desc(), Stage2Run.id.desc()),
                ).label("row_number"),
            )
            .subquery()
        )
        return (
            select(
                ranked_runs.c.repository_id,
                ranked_runs.c.status,
                ranked_runs.c.result,
                ranked_runs.c.latest_operation_at,
            )
            .where(ranked_runs.c.row_number == 1)
            .subquery()
        )

    def repair_duplicate_active_runs(self, *, return_archives: bool = False) -> int | list[dict[str, Any]]:
        dirty = 0
        archives: list[dict[str, Any]] = []
        duplicate_repository_ids = list(
            self.session.scalars(
                select(Stage2Run.repository_id)
                .where(
                    Stage2Run.status.in_(
                        [Stage2RunStatus.queued.value, Stage2RunStatus.running.value]
                    )
                )
                .group_by(Stage2Run.repository_id)
                .having(func.count(Stage2Run.id) > 1)
            )
        )
        if not duplicate_repository_ids:
            return archives if return_archives else dirty

        repaired_at = datetime.now(UTC)
        for repository_id in duplicate_repository_ids:
            active_run_priority = case(
                (Stage2Run.status == Stage2RunStatus.running.value, 0),
                else_=1,
            )
            active_runs = list(
                self.session.scalars(
                    select(Stage2Run)
                    .where(
                        Stage2Run.repository_id == repository_id,
                        Stage2Run.status.in_(
                            [Stage2RunStatus.queued.value, Stage2RunStatus.running.value]
                        ),
                    )
                    .order_by(
                        active_run_priority.asc(),
                        Stage2Run.created_at.desc(),
                        Stage2Run.id.desc(),
                    )
                )
            )
            for stale_run in active_runs[1:]:
                previous_status = stale_run.status
                previous_phase = stale_run.phase
                archives.append(
                    {
                        "run_id": stale_run.id,
                        "repository_id": stale_run.repository_id,
                        "workspace_path": stale_run.workspace_path,
                        "checkpoint_docker_image_refs": self._checkpoint_docker_image_refs_for_run(stale_run),
                        "validator_image_refs": self._validator_image_refs_for_run(stale_run),
                    }
                )
                self.upsert_cleanup_tombstone(
                    archives[-1],
                    reason="duplicate_active_repair",
                )
                stale_run.status = Stage2RunStatus.completed.value
                stale_run.result = Stage2RunResult.failed.value
                stale_run.phase = "completed"
                stale_run.finished_at = repaired_at
                stale_run.error_message = (
                    stale_run.error_message
                    or "stage2 run invalidated while restoring the single-active-run invariant"
                )
                self.session.add(
                    Stage2RunEvent(
                        run_id=stale_run.id,
                        actor="system",
                        phase="failed",
                        title="Stage2 active-run invariant repaired",
                        message=stale_run.error_message,
                        payload_json={
                            "run_id": stale_run.id,
                            "repository_id": stale_run.repository_id,
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
                select(Stage2Run).where(Stage2Run.status.in_([Stage2RunStatus.queued.value, Stage2RunStatus.running.value]))
            )
        )
        for row in rows:
            previous_status = row.status
            previous_phase = row.phase
            archives.append(
                {
                    "run_id": row.id,
                    "repository_id": row.repository_id,
                    "workspace_path": row.workspace_path,
                    "checkpoint_docker_image_refs": self._checkpoint_docker_image_refs_for_run(row),
                    "validator_image_refs": self._validator_image_refs_for_run(row),
                    "cleanup_scope": "recovered_run_assets",
                }
            )
            self.upsert_cleanup_tombstone(
                archives[-1],
                reason="recovered_interrupted_run",
            )
            error_message = (
                row.error_message
                or (
                    "stage2 run interrupted before execution"
                    if previous_status == Stage2RunStatus.queued.value
                    else "stage2 run interrupted before completion"
                )
            )
            row.status = Stage2RunStatus.completed.value
            row.result = Stage2RunResult.failed.value
            row.phase = "completed"
            row.finished_at = datetime.now(UTC)
            row.error_message = error_message
            self.session.add(
                Stage2RunEvent(
                    run_id=row.id,
                    actor="system",
                    phase="failed",
                    title="Stage2 run recovered as interrupted",
                    message=error_message,
                    payload_json={
                        "run_id": row.id,
                        "repository_id": row.repository_id,
                        "previous_status": previous_status,
                        "previous_phase": previous_phase,
                    },
                )
            )
            dirty += 1
        if dirty:
            self.session.flush()
        return archives if return_archives else dirty

    def _replace_test_results(self, run: Stage2Run, file_results: list[dict[str, Any]]) -> None:
        run.test_results.clear()
        for item in file_results:
            result = item.get("result") or {}
            summary = result.get("summary") or {}
            run.test_results.append(
                Stage2TestResult(
                    test_file_path=item.get("test_file_path", ""),
                    target_selector=item.get("target_selector") or item.get("test_file_path", ""),
                    status=str(result.get("status") or "unknown"),
                    total_tests=int(summary.get("collected", 0) or 0),
                    passed_tests=int(summary.get("passed", 0) or 0),
                    failed_tests=int(summary.get("failed", 0) or 0),
                    error_tests=int(summary.get("errors", 0) or 0),
                    skipped_tests=int(summary.get("skipped", 0) or 0),
                    exit_code=int(item.get("returncode", 0) or 0),
                    raw_result_json=result,
                )
            )
        self.session.flush()


def resolve_repo_head_commit(repository: GitHubRepository) -> str | None:
    branch = repository.default_branch or "HEAD"
    remote_url_args = [temporary_git_remote_url(repository.html_url)]
    timeout_seconds = _git_ls_remote_timeout_seconds()
    max_attempts = github_proxy_retry_attempts(remote_url_args, minimum=1)
    for attempt in range(1, max_attempts + 1):
        refresh_github_proxy_urls(remote_url_args)
        remote_url = remote_url_args[0]
        command = ["git", "ls-remote", "--heads", remote_url, branch]
        completed = _run_git_ls_remote(command, timeout_seconds=timeout_seconds)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.split()[0]
        if _is_retryable_git_ls_remote_failure(completed):
            failed_proxy, next_proxy = rotate_failed_github_proxy_urls(remote_url_args)
            if failed_proxy is not None and next_proxy != failed_proxy:
                if attempt < max_attempts:
                    continue
                return None

        head_fallback = _run_git_ls_remote(
            ["git", "ls-remote", remote_url, "HEAD"],
            timeout_seconds=timeout_seconds,
        )
        if head_fallback.returncode == 0 and head_fallback.stdout.strip():
            return head_fallback.stdout.split()[0]
        if _is_retryable_git_ls_remote_failure(head_fallback):
            failed_proxy, next_proxy = rotate_failed_github_proxy_urls(remote_url_args)
            if (
                failed_proxy is not None
                and next_proxy != failed_proxy
                and attempt < max_attempts
            ):
                continue
        return None
    return None


def _is_retryable_git_ls_remote_failure(completed: subprocess.CompletedProcess[str]) -> bool:
    if completed.returncode == 124:
        return True
    details = f"{completed.stderr or ''}\n{completed.stdout or ''}".lower()
    return any(marker.lower() in details for marker in _GIT_LS_REMOTE_RETRYABLE_ERROR_MARKERS)


def _run_git_ls_remote(command: list[str], *, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            command,
            124,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "git ls-remote timed out",
        )


def _git_ls_remote_timeout_seconds() -> float:
    raw_value = (
        env_value("FEATURE_FACTORY_STAGE2_GIT_REMOTE_TIMEOUT_SECONDS")
        or env_value("FEATURE_FACTORY_GIT_REMOTE_TIMEOUT_SECONDS")
        or ""
    ).strip()
    if not raw_value:
        return _DEFAULT_GIT_LS_REMOTE_TIMEOUT_SECONDS
    try:
        return max(1.0, float(raw_value))
    except ValueError:
        return _DEFAULT_GIT_LS_REMOTE_TIMEOUT_SECONDS