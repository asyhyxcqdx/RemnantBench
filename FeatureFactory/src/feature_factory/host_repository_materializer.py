from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from feature_factory.config import Settings
from feature_factory.docker_mirrors import temporary_git_remote_url
from feature_factory.models import GitHubRepository

_HOST_REPO_CACHE_LOCKS_GUARD = Lock()
_HOST_REPO_CACHE_LOCKS: dict[str, Lock] = {}

RunEventEmitter = Callable[..., None]
RunCheckedCommand = Callable[..., subprocess.CompletedProcess[str]]
RunProgressCommand = Callable[..., subprocess.CompletedProcess[str]]
CancelRequested = Callable[[], bool]
CancelCheck = Callable[[CancelRequested | None], None]
ErrorFactory = Callable[[str], Exception]
ResolveHeadCommit = Callable[[GitHubRepository], str | None]
TemporaryGitRemoteUrl = Callable[[str], str]
TryRevParse = Callable[[Path, str], str | None]


def _raise_if_cancel_requested(cancel_requested: CancelRequested | None) -> None:
    if cancel_requested is not None and cancel_requested():
        raise RuntimeError("repository materialization interrupted by user")


@dataclass(frozen=True, slots=True)
class MaterializedRepositoryCheckout:
    source_kind: str
    clone_source: str
    checkout_dir: str
    target_commit_sha: str
    resolved_head_commit_sha: str
    cache_path: str | None = None


class HostRepositoryMaterializer:
    def __init__(
        self,
        settings: Settings,
        *,
        emit_or_append_run_event: RunEventEmitter,
        run_checked_command: RunCheckedCommand,
        run_command_with_git_progress: RunProgressCommand,
        run_git_remote_command_with_progress: RunProgressCommand,
        raise_if_cancel_requested: CancelCheck | None = None,
        temporary_git_remote_url_fn: TemporaryGitRemoteUrl | None = None,
        prefer_repository_html_url: bool = False,
    ) -> None:
        self.settings = settings
        self._emit_or_append_run_event = emit_or_append_run_event
        self._run_checked_command = run_checked_command
        self._run_command_with_git_progress = run_command_with_git_progress
        self._run_git_remote_command_with_progress = run_git_remote_command_with_progress
        self._raise_if_cancel_requested = raise_if_cancel_requested or _raise_if_cancel_requested
        self._temporary_git_remote_url = temporary_git_remote_url_fn or temporary_git_remote_url
        self._prefer_repository_html_url = bool(prefer_repository_html_url)

    def _run_checked_command_cancelable(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        error_message: str,
        cancel_requested: CancelRequested | None = None,
    ) -> subprocess.CompletedProcess[str]:
        self._raise_if_cancel_requested(cancel_requested)
        completed = self._run_checked_command(
            args,
            timeout_seconds=timeout_seconds,
            error_message=error_message,
        )
        self._raise_if_cancel_requested(cancel_requested)
        return completed

    def repository_cache_dir(self, repository: GitHubRepository) -> Path:
        owner = repository.owner_login or "_owner"
        name = repository.name or "repo"
        return (
            Path(self.settings.stage2_workspace_dir).expanduser()
            / "cache"
            / "repos"
            / owner
            / f"{name}.git"
        ).resolve()

    def repo_cache_lock(self, cache_dir: Path) -> Lock:
        cache_key = str(cache_dir.resolve())
        with _HOST_REPO_CACHE_LOCKS_GUARD:
            return _HOST_REPO_CACHE_LOCKS.setdefault(cache_key, Lock())

    def repository_clone_url(self, repository: GitHubRepository) -> str:
        html_url = str(repository.html_url or "").strip()
        if html_url.startswith(("file://", "/", "git@", "ssh://")):
            return html_url
        if html_url and self._prefer_repository_html_url:
            return self._temporary_git_remote_url(html_url)
        if repository.full_name:
            return self._temporary_git_remote_url(f"https://github.com/{repository.full_name}.git")
        return self._temporary_git_remote_url(html_url) if html_url else html_url

    def repository_cache_branch(self, repository: GitHubRepository) -> str | None:
        branch = str(repository.default_branch or "").strip()
        return branch or None

    def git_config_value(self, cache_dir: Path, key: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={cache_dir}", "config", "--get", key],
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def try_symbolic_ref(self, cache_dir: Path, revision: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={cache_dir}", "symbolic-ref", "-q", revision],
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def try_rev_parse(self, git_dir: Path, revision: str) -> str | None:
        completed = subprocess.run(
            ["git", f"--git-dir={git_dir}", "rev-parse", "--verify", revision],
            capture_output=True,
            text=True,
            check=False,
            timeout=30.0,
        )
        if completed.returncode != 0:
            return None
        value = str(completed.stdout or "").strip()
        return value or None

    def repository_cache_rebuild_reason(self, cache_dir: Path, *, default_branch: str | None) -> str | None:
        mirror_flag = self.git_config_value(cache_dir, "remote.origin.mirror")
        if mirror_flag and mirror_flag.lower() == "true":
            return "legacy mirror cache"
        if default_branch:
            head_ref = self.try_symbolic_ref(cache_dir, "HEAD")
            expected_head_ref = f"refs/heads/{default_branch}"
            if head_ref and head_ref != expected_head_ref:
                return f"default branch changed to {default_branch}"
        return None

    def create_repository_cache(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        default_branch: str | None,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> None:
        self._raise_if_cancel_requested(cancel_requested)
        remote_url = self.repository_clone_url(repository)
        args = ["git", "clone", "--progress", "--bare", "--single-branch", "--no-tags"]
        if default_branch:
            args.extend(["--branch", default_branch])
        args.extend([remote_url, str(cache_dir)])
        self._run_git_remote_command_with_progress(
            args,
            error_message=f"failed to create repository cache for {repository.full_name}",
            run=run,
            phase=phase,
            progress_title="Repository cache clone progress",
            retry_title="Repository cache clone retrying",
            progress_payload={
                "operation": "cache_clone",
                "repository_full_name": repository.full_name,
                "cache_path": str(cache_dir),
                "default_branch": default_branch,
            },
            emit_event=emit_event,
            cancel_requested=cancel_requested,
            cleanup_after_failed_attempt=lambda: shutil.rmtree(cache_dir, ignore_errors=True),
            timeout_seconds=600.0,
        )
        if default_branch:
            self._run_checked_command_cancelable(
                ["git", f"--git-dir={cache_dir}", "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"],
                timeout_seconds=30.0,
                error_message=f"failed to pin cache HEAD for {repository.full_name}",
                cancel_requested=cancel_requested,
            )

    def refresh_repository_cache(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        default_branch: str | None,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> None:
        self._raise_if_cancel_requested(cancel_requested)
        self._run_checked_command_cancelable(
            ["git", f"--git-dir={cache_dir}", "remote", "set-url", "origin", repository.html_url],
            timeout_seconds=30.0,
            error_message=f"failed to update repository cache origin for {repository.full_name}",
            cancel_requested=cancel_requested,
        )
        fetch_remote = self.repository_clone_url(repository)
        fetch_args = ["git", f"--git-dir={cache_dir}", "fetch", "--progress", "--prune", "--no-tags"]
        fetch_args.append(fetch_remote if fetch_remote and fetch_remote != repository.html_url else "origin")
        if default_branch:
            fetch_args.append(f"+refs/heads/{default_branch}:refs/heads/{default_branch}")
        self._run_git_remote_command_with_progress(
            fetch_args,
            error_message=f"failed to refresh repository cache for {repository.full_name}",
            run=run,
            phase=phase,
            progress_title="Repository cache refresh progress",
            retry_title="Repository cache refresh retrying",
            progress_payload={
                "operation": "cache_refresh",
                "repository_full_name": repository.full_name,
                "cache_path": str(cache_dir),
                "default_branch": default_branch,
            },
            emit_event=emit_event,
            cancel_requested=cancel_requested,
            timeout_seconds=600.0,
        )
        if default_branch:
            self._run_checked_command_cancelable(
                ["git", f"--git-dir={cache_dir}", "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"],
                timeout_seconds=30.0,
                error_message=f"failed to update cache HEAD for {repository.full_name}",
                cancel_requested=cancel_requested,
            )

    def sync_repository_cache(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> None:
        self._raise_if_cancel_requested(cancel_requested)
        default_branch = self.repository_cache_branch(repository)
        branch_label = default_branch or "remote HEAD"
        cache_preexisted = cache_dir.exists()
        cache_dir.parent.mkdir(parents=True, exist_ok=True)
        if cache_preexisted:
            rebuild_reason = self.repository_cache_rebuild_reason(cache_dir, default_branch=default_branch)
            if rebuild_reason is not None:
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title="Repository cache rebuild started",
                    message=(
                        f"Discarding the existing shared cache for {repository.full_name} "
                        f"and rebuilding it in lightweight mode ({rebuild_reason})"
                    ),
                    payload={
                        "cache_path": str(cache_dir),
                        "default_branch": default_branch,
                        "reason": rebuild_reason,
                    },
                    emit_event=emit_event,
                )
                shutil.rmtree(cache_dir)
            else:
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title="Repository cache hit",
                    message=f"Found existing shared cache for {repository.full_name}",
                    payload={"cache_path": str(cache_dir), "default_branch": default_branch},
                    emit_event=emit_event,
                )
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title="Repository cache refresh started",
                    message=f"Refreshing cached default branch {branch_label} for {repository.full_name} from origin",
                    payload={"cache_path": str(cache_dir), "default_branch": default_branch},
                    emit_event=emit_event,
                )
                self.refresh_repository_cache(
                    run,
                    repository=repository,
                    cache_dir=cache_dir,
                    default_branch=default_branch,
                    phase=phase,
                    emit_event=emit_event,
                    cancel_requested=cancel_requested,
                )
                self._raise_if_cancel_requested(cancel_requested)
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title="Repository cache synchronized",
                    message=f"Shared cache for {repository.full_name} is up to date",
                    payload={"cache_path": str(cache_dir), "default_branch": default_branch},
                    emit_event=emit_event,
                )
                return
        if not cache_preexisted:
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase=phase,
                title="Repository cache miss",
                message=f"No shared cache exists yet for {repository.full_name}",
                payload={"cache_path": str(cache_dir), "default_branch": default_branch},
                emit_event=emit_event,
            )
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Repository cache clone started",
            message=f"Cloning cached default branch {branch_label} for {repository.full_name}",
            payload={
                "cache_path": str(cache_dir),
                "repository_url": repository.html_url,
                "default_branch": default_branch,
            },
            emit_event=emit_event,
        )
        self.create_repository_cache(
            run,
            repository=repository,
            cache_dir=cache_dir,
            default_branch=default_branch,
            phase=phase,
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )
        self._raise_if_cancel_requested(cancel_requested)
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Repository cache synchronized",
            message=f"Shared cache for {repository.full_name} was created successfully",
            payload={"cache_path": str(cache_dir), "default_branch": default_branch},
            emit_event=emit_event,
        )

    def resolve_cached_target_commit(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        requested_commit_sha: str | None,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
        resolve_head_commit: ResolveHeadCommit | None = None,
        try_rev_parse_fn: TryRevParse | None = None,
        error_factory: ErrorFactory = RuntimeError,
    ) -> str:
        self._raise_if_cancel_requested(cancel_requested)
        try_rev_parse = try_rev_parse_fn or self.try_rev_parse
        requested_commit_sha = str(requested_commit_sha or "").strip()
        if requested_commit_sha:
            verified = try_rev_parse(cache_dir, f"{requested_commit_sha}^{{commit}}")
            if verified:
                return verified
            fetch_remote = self.repository_clone_url(repository)
            self._emit_or_append_run_event(
                run,
                actor="system",
                phase=phase,
                title="Requested commit fetch started",
                message=(
                    f"Requested commit {requested_commit_sha} is not in the shared cache yet; fetching it explicitly"
                ),
                payload={"cache_path": str(cache_dir), "requested_commit_sha": requested_commit_sha},
                emit_event=emit_event,
            )
            self._run_git_remote_command_with_progress(
                [
                    "git",
                    f"--git-dir={cache_dir}",
                    "fetch",
                    "--progress",
                    "--no-tags",
                    fetch_remote if fetch_remote and fetch_remote != repository.html_url else "origin",
                    requested_commit_sha,
                ],
                error_message=(
                    f"failed to fetch requested commit {requested_commit_sha} for {repository.full_name} into cache"
                ),
                run=run,
                phase=phase,
                progress_title="Requested commit fetch progress",
                retry_title="Requested commit fetch retrying",
                progress_payload={
                    "operation": "requested_commit_fetch",
                    "repository_full_name": repository.full_name,
                    "cache_path": str(cache_dir),
                    "requested_commit_sha": requested_commit_sha,
                },
                emit_event=emit_event,
                cancel_requested=cancel_requested,
                timeout_seconds=600.0,
            )
            self._raise_if_cancel_requested(cancel_requested)
            verified = try_rev_parse(cache_dir, f"{requested_commit_sha}^{{commit}}")
            if verified:
                self._emit_or_append_run_event(
                    run,
                    actor="system",
                    phase=phase,
                    title="Requested commit fetched",
                    message=f"Fetched requested commit {requested_commit_sha} into the shared cache",
                    payload={"cache_path": str(cache_dir), "requested_commit_sha": requested_commit_sha},
                    emit_event=emit_event,
                )
                return verified
            raise error_factory(
                f"requested commit {requested_commit_sha} is not available in repository cache for {repository.full_name}"
            )

        branch = repository.default_branch or "HEAD"
        if branch != "HEAD":
            branch_head = try_rev_parse(cache_dir, f"refs/heads/{branch}")
            if branch_head:
                return branch_head
        head_commit = try_rev_parse(cache_dir, "HEAD")
        if head_commit:
            return head_commit
        if resolve_head_commit is not None:
            resolved = str(resolve_head_commit(repository) or "").strip()
            if resolved:
                return resolved
        raise error_factory(
            f"failed to resolve target commit from repository cache for {repository.full_name}"
        )

    def materialize_checkout_from_cache(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        cache_dir: Path,
        checkout_dir: Path,
        target_commit_sha: str,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> MaterializedRepositoryCheckout:
        self._raise_if_cancel_requested(cancel_requested)
        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Repository checkout materializing",
            message=f"Materializing {repository.full_name} from the shared cache into the run workspace",
            payload={
                "cache_path": str(cache_dir),
                "checkout_path": str(checkout_dir),
            },
            emit_event=emit_event,
        )
        self._run_command_with_git_progress(
            [
                "git",
                "clone",
                "--progress",
                "--no-checkout",
                str(cache_dir),
                str(checkout_dir),
            ],
            error_message=f"failed to materialize checkout for {repository.full_name}",
            run=run,
            phase=phase,
            progress_title="Repository checkout materialization progress",
            progress_payload={
                "operation": "checkout_materialize",
                "repository_full_name": repository.full_name,
                "cache_path": str(cache_dir),
                "checkout_path": str(checkout_dir),
            },
            emit_event=emit_event,
            cancel_requested=cancel_requested,
            timeout_seconds=600.0,
        )
        self._restore_origin_remote(checkout_dir, repository, cancel_requested=cancel_requested)
        self._checkout_target_commit(
            run,
            repository=repository,
            checkout_dir=checkout_dir,
            target_commit_sha=target_commit_sha,
            phase=phase,
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )
        resolved_head_commit_sha = self._run_checked_command_cancelable(
            ["git", "-C", str(checkout_dir), "rev-parse", "HEAD"],
            timeout_seconds=30.0,
            error_message=f"failed to resolve HEAD for {repository.full_name}",
            cancel_requested=cancel_requested,
        ).stdout.strip()
        return MaterializedRepositoryCheckout(
            source_kind="shared_cache",
            clone_source=str(cache_dir),
            checkout_dir=str(checkout_dir),
            target_commit_sha=target_commit_sha,
            resolved_head_commit_sha=resolved_head_commit_sha,
            cache_path=str(cache_dir),
        )

    def materialize_checkout_from_clone_source(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        clone_source: str,
        checkout_dir: Path,
        target_commit_sha: str,
        phase: str | None,
        source_kind: str,
        source_label: str,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> MaterializedRepositoryCheckout:
        self._raise_if_cancel_requested(cancel_requested)
        if checkout_dir.exists():
            shutil.rmtree(checkout_dir)
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Repository checkout materializing",
            message=f"Materializing {repository.full_name} from {source_label} into the run workspace",
            payload={
                "clone_source": str(clone_source),
                "checkout_path": str(checkout_dir),
                "source_kind": source_kind,
            },
            emit_event=emit_event,
        )
        clone_args = ["git", "clone", "--progress", str(clone_source), str(checkout_dir)]
        if Path(str(clone_source)).exists():
            self._run_command_with_git_progress(
                clone_args,
                error_message=f"failed to clone repository for {repository.full_name}",
                run=run,
                phase=phase,
                progress_title="Repository clone progress",
                progress_payload={
                    "operation": "checkout_clone",
                    "repository_full_name": repository.full_name,
                    "clone_source": str(clone_source),
                    "checkout_path": str(checkout_dir),
                    "source_kind": source_kind,
                },
                emit_event=emit_event,
                cancel_requested=cancel_requested,
                timeout_seconds=600.0,
            )
        else:
            self._run_git_remote_command_with_progress(
                clone_args,
                error_message=f"failed to clone repository for {repository.full_name}",
                run=run,
                phase=phase,
                progress_title="Repository clone progress",
                retry_title="Repository clone retrying",
                progress_payload={
                    "operation": "checkout_clone",
                    "repository_full_name": repository.full_name,
                    "clone_source": str(clone_source),
                    "checkout_path": str(checkout_dir),
                    "source_kind": source_kind,
                },
                emit_event=emit_event,
                cancel_requested=cancel_requested,
                cleanup_after_failed_attempt=lambda: shutil.rmtree(checkout_dir, ignore_errors=True),
                timeout_seconds=600.0,
            )
        self._restore_origin_remote(checkout_dir, repository, cancel_requested=cancel_requested)
        self._checkout_target_commit(
            run,
            repository=repository,
            checkout_dir=checkout_dir,
            target_commit_sha=target_commit_sha,
            phase=phase,
            emit_event=emit_event,
            cancel_requested=cancel_requested,
        )
        resolved_head_commit_sha = self._run_checked_command_cancelable(
            ["git", "-C", str(checkout_dir), "rev-parse", "HEAD"],
            timeout_seconds=30.0,
            error_message=f"failed to resolve HEAD for {repository.full_name}",
            cancel_requested=cancel_requested,
        ).stdout.strip()
        return MaterializedRepositoryCheckout(
            source_kind=source_kind,
            clone_source=str(clone_source),
            checkout_dir=str(checkout_dir),
            target_commit_sha=target_commit_sha,
            resolved_head_commit_sha=resolved_head_commit_sha,
        )

    def _restore_origin_remote(
        self,
        checkout_dir: Path,
        repository: GitHubRepository,
        *,
        cancel_requested: CancelRequested | None = None,
    ) -> None:
        html_url = str(repository.html_url or "").strip()
        if not html_url:
            return
        self._run_checked_command_cancelable(
            ["git", "-C", str(checkout_dir), "remote", "set-url", "origin", html_url],
            timeout_seconds=30.0,
            error_message=f"failed to restore origin remote for {repository.full_name}",
            cancel_requested=cancel_requested,
        )

    def _checkout_target_commit(
        self,
        run: Any,
        *,
        repository: GitHubRepository,
        checkout_dir: Path,
        target_commit_sha: str,
        phase: str | None,
        emit_event: Callable[..., None] | None = None,
        cancel_requested: CancelRequested | None = None,
    ) -> None:
        self._raise_if_cancel_requested(cancel_requested)
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Target commit checkout started",
            message=f"Checking out target commit {target_commit_sha} in the run workspace",
            payload={"checkout_path": str(checkout_dir), "target_commit_sha": target_commit_sha},
            emit_event=emit_event,
        )
        self._raise_if_cancel_requested(cancel_requested)
        checkout_args = ["git"]
        html_url = str(repository.html_url or "").strip()
        if html_url.startswith(("https://github.com/", "https://www.github.com/")):
            proxied_url = self._temporary_git_remote_url(html_url).rstrip("/")
            lfs_repo_url = (
                proxied_url if proxied_url.endswith(".git") else f"{proxied_url}.git"
            )
            checkout_args.extend(["-c", f"lfs.url={lfs_repo_url}/info/lfs"])
        checkout_args.extend(
            [
                "-C",
                str(checkout_dir),
                "checkout",
                "--force",
                "--detach",
                target_commit_sha,
            ]
        )
        self._run_git_remote_command_with_progress(
            checkout_args,
            error_message=(
                f"failed to checkout target commit {target_commit_sha} "
                f"for {repository.full_name}"
            ),
            run=run,
            phase=phase,
            progress_title="Target commit checkout progress",
            retry_title="Target commit checkout retrying",
            progress_payload={
                "operation": "checkout_target_commit",
                "repository_full_name": repository.full_name,
                "checkout_path": str(checkout_dir),
                "target_commit_sha": target_commit_sha,
            },
            emit_event=emit_event,
            cancel_requested=cancel_requested,
            timeout_seconds=120.0,
        )
        self._raise_if_cancel_requested(cancel_requested)
        self._emit_or_append_run_event(
            run,
            actor="system",
            phase=phase,
            title="Target commit checked out",
            message=f"Target commit {target_commit_sha} is checked out in the run workspace",
            payload={"checkout_path": str(checkout_dir), "target_commit_sha": target_commit_sha},
            emit_event=emit_event,
        )
