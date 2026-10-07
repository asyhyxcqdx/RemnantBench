from __future__ import annotations

import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from threading import Semaphore
from typing import Any, Callable, Literal
from urllib.parse import urlsplit

from belta.config import CandidatePairsConfig, load_config


PathClass = Literal["implementation_code", "ignored"]

@dataclass(frozen=True)
class CandidatePairConstructionResult:
    run_dir: Path
    repo_count: int
    accepted_candidates: int
    rejected_candidates: int


@dataclass(frozen=True)
class RepoInput:
    github_repo_id: int
    full_name: str
    clone_url: str
    default_branch: str
    repo_key: str
    cache_dir: Path


@dataclass(frozen=True)
class RepoWorkItem:
    index: int
    repository: dict[str, Any]
    output_key: str
    output_dir: Path


@dataclass(frozen=True)
class DiffFile:
    status: str
    path: str
    path_class: PathClass


@dataclass(frozen=True)
class GitObjectInfo:
    object_id: str
    object_type: str
    size: int


class GitBatchObjectReader:
    def __init__(self, cache_dir: Path) -> None:
        self.cache_dir = cache_dir
        self._check_process: subprocess.Popen[bytes] | None = None
        self._content_process: subprocess.Popen[bytes] | None = None

    def info(self, revision_path: str) -> GitObjectInfo | None:
        process = self._check_process_or_start()
        self._write_query(
            process, revision_path.encode("utf-8", errors="surrogateescape")
        )
        header = self._read_header(process, "git cat-file --batch-check")
        if header.endswith(b" missing"):
            return None
        parts = header.split()
        if len(parts) != 3:
            raise RuntimeError(f"invalid git cat-file --batch-check output: {header!r}")
        try:
            size = int(parts[2])
        except ValueError as exc:
            raise RuntimeError(
                f"invalid git cat-file --batch-check object size: {header!r}"
            ) from exc
        return GitObjectInfo(
            object_id=parts[0].decode("ascii"),
            object_type=parts[1].decode("ascii"),
            size=size,
        )

    def read(self, object_info: GitObjectInfo) -> bytes:
        process = self._content_process_or_start()
        self._write_query(process, object_info.object_id.encode("ascii"))
        header = self._read_header(process, "git cat-file --batch")
        parts = header.split()
        if len(parts) != 3:
            raise RuntimeError(f"invalid git cat-file --batch output: {header!r}")
        object_id = parts[0].decode("ascii")
        object_type = parts[1].decode("ascii")
        try:
            size = int(parts[2])
        except ValueError as exc:
            raise RuntimeError(
                f"invalid git cat-file --batch object size: {header!r}"
            ) from exc
        if (
            object_id != object_info.object_id
            or object_type != object_info.object_type
            or size != object_info.size
        ):
            raise RuntimeError(
                "git cat-file --batch returned an object that did not match "
                f"the requested metadata: {header!r}"
            )
        if process.stdout is None:
            raise RuntimeError("git cat-file --batch stdout is unavailable")
        content = process.stdout.read(size)
        terminator = process.stdout.read(1)
        if len(content) != size or terminator != b"\n":
            raise RuntimeError(
                f"truncated git cat-file --batch content for {object_info.object_id}"
            )
        return content

    def close(self) -> None:
        self._close_process(self._check_process)
        self._close_process(self._content_process)
        self._check_process = None
        self._content_process = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _check_process_or_start(self) -> subprocess.Popen[bytes]:
        if self._check_process is None:
            self._check_process = self._start_process(
                "cat-file",
                "--batch-check=%(objectname) %(objecttype) %(objectsize)",
            )
        return self._check_process

    def _content_process_or_start(self) -> subprocess.Popen[bytes]:
        if self._content_process is None:
            self._content_process = self._start_process("cat-file", "--batch")
        return self._content_process

    def _start_process(self, *args: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            ["git", f"--git-dir={self.cache_dir}", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    @staticmethod
    def _write_query(process: subprocess.Popen[bytes], query: bytes) -> None:
        if process.stdin is None:
            raise RuntimeError("git cat-file stdin is unavailable")
        try:
            process.stdin.write(query + b"\n")
            process.stdin.flush()
        except BrokenPipeError as exc:
            raise RuntimeError("git cat-file process closed its input") from exc

    @staticmethod
    def _read_header(process: subprocess.Popen[bytes], command_name: str) -> bytes:
        if process.stdout is None:
            raise RuntimeError(f"{command_name} stdout is unavailable")
        header = process.stdout.readline()
        if header:
            return header.rstrip(b"\n")
        return_code = process.poll()
        stderr = b""
        if return_code is not None and process.stderr is not None:
            stderr = process.stderr.read()
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"{command_name} stopped unexpectedly" + (f": {detail}" if detail else "")
        )

    @staticmethod
    def _close_process(process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        if process.stdin is not None:
            process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


@dataclass
class RepoAnalysisContext:
    cache_dir: Path
    new_commit: str
    revision_analyses: dict[tuple[str, str], PythonBlobAnalysis | None] = field(
        default_factory=dict
    )
    blob_analyses: dict[str, PythonBlobAnalysis] = field(default_factory=dict)
    _object_reader: GitBatchObjectReader | None = field(
        default=None, init=False, repr=False
    )

    def pair(self, old_commit: str) -> PairAnalysisContext:
        return PairAnalysisContext(repo=self, old_commit=old_commit)

    def analysis(self, commit: str, path: str) -> PythonBlobAnalysis | None:
        key = (commit, path)
        if key in self.revision_analyses:
            return self.revision_analyses[key]

        if "\n" in path:
            source = _read_file_from_git(self.cache_dir, commit, path)
            analysis = _analyze_python_source(source) if source is not None else None
            self.revision_analyses[key] = analysis
            return analysis

        object_info = self._reader().info(f"{commit}:{path}")
        if object_info is None or object_info.object_type != "blob":
            self.revision_analyses[key] = None
            return None
        analysis = self.blob_analyses.get(object_info.object_id)
        if analysis is None:
            analysis = _analyze_python_source(self._reader().read(object_info))
            self.blob_analyses[object_info.object_id] = analysis
        self.revision_analyses[key] = analysis
        return analysis

    def close(self) -> None:
        if self._object_reader is not None:
            self._object_reader.close()
            self._object_reader = None

    def __enter__(self) -> RepoAnalysisContext:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _reader(self) -> GitBatchObjectReader:
        if self._object_reader is None:
            self._object_reader = GitBatchObjectReader(self.cache_dir)
        return self._object_reader


@dataclass
class PairAnalysisContext:
    repo: RepoAnalysisContext
    old_commit: str

    @property
    def new_commit(self) -> str:
        return self.repo.new_commit

    @property
    def cache_dir(self) -> Path:
        return self.repo.cache_dir

    def source(self, commit: str, path: str) -> bytes | None:
        analysis = self._analysis(commit, path)
        return analysis.source if analysis is not None else None

    def is_parseable(self, commit: str, path: str) -> bool:
        analysis = self._analysis(commit, path)
        return analysis is not None and analysis.parseable

    def objects(self, commit: str, path: str) -> list[PythonTarget]:
        analysis = self._analysis(commit, path)
        return list(analysis.python_objects) if analysis is not None else []

    def is_packaging_setup(self, commit: str, path: str) -> bool:
        analysis = self._analysis(commit, path)
        return analysis is not None and analysis.packaging_setup

    def _analysis(self, commit: str, path: str) -> PythonBlobAnalysis | None:
        if commit not in {self.new_commit, self.old_commit}:
            raise ValueError(f"commit is outside pair analysis context: {commit}")
        return self.repo.analysis(commit, path)


@dataclass(frozen=True)
class PythonBlobAnalysis:
    source: bytes
    parseable: bool
    python_objects: tuple[PythonTarget, ...]
    packaging_setup: bool


@dataclass(frozen=True)
class PythonTarget:
    object_kind: Literal["function", "method", "class"]
    qualified_name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class DeletedRange:
    old_start_line: int
    old_end_line: int
    source_text: str
    new_insert_line: int


TEST_DIR_NAMES = {
    "__tests__",
    "expected",
    "fixture",
    "fixtures",
    "golden",
    "snapshot",
    "snapshots",
    "spec",
    "specs",
    "test",
    "testdata",
    "testing",
    "tests",
    "module_testing",
    "unittest",
    "unittests",
    "unit_tests",
}

TEST_ROLE_TOKENS = {
    "fixture",
    "fixtures",
    "test",
    "testing",
    "tests",
    "unittest",
    "unittests",
}

TEST_FILE_PATTERNS = {
    "*_test.py",
    "conftest.py",
    "noxfile.py",
    "pytest.ini",
    "test_*.py",
    "tox.ini",
}

NON_IMPLEMENTATION_ROLE_NAMES = {
    "benchmark",
    "benchmarks",
    "demo",
    "demos",
    "example",
    "examples",
    "sample",
    "samples",
    "tutorial",
    "tutorials",
}

EXCLUDED_DIR_NAMES = {
    ".azure-pipelines",
    ".circleci",
    ".gitlab",
    ".github",
    ".mypy_cache",
    ".nox",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "doc",
    "docs",
    "generated",
    "examples",
    "guide",
    "node_modules",
    "target",
    "vendor",
}

ROOT_EXCLUDED_DIR_NAMES = {
    ".azure-pipelines",
    ".circleci",
    ".gitlab",
    ".github",
    "doc",
    "docs",
    "examples",
    "guide",
}

EXCLUDED_FILE_PATTERNS = {
    ".coveragerc",
    ".gitignore",
    ".pre-commit-config.yaml",
    ".readthedocs.yaml",
    "*.class",
    "*.dll",
    "*.dylib",
    "*.gif",
    "*.jpeg",
    "*.jpg",
    "*.map",
    "*.md",
    "*.min.css",
    "*.min.js",
    "*.o",
    "*.pdf",
    "*.png",
    "*.pyc",
    "*.pyo",
    "*.rst",
    "*.so",
    "*.svg",
    "AUTHORS*",
    "CHANGELOG*",
    "CITATION*",
    "CODE_OF_CONDUCT*",
    "CONTRIBUTORS*",
    "HACKING*",
    "LICENSE*",
    "NOTICE*",
    "README*",
    "RELEASING*",
    "SECURITY*",
}

ROOT_ENVIRONMENT_FILE_PATTERNS = {
    "BUILD",
    "CMakeLists.txt",
    "Makefile",
    "MANIFEST.in",
    "Pipfile",
    "Pipfile.lock",
    "WORKSPACE",
    "build.gradle",
    "build.gradle.kts",
    "bun.lock",
    "bun.lockb",
    "conda*.yaml",
    "conda*.yml",
    "environment.yaml",
    "environment.yml",
    "meson.build",
    "poetry.lock",
    "pyproject.toml",
    "requirements*.txt",
    "setup.cfg",
    "uv.lock",
}

ANYWHERE_ENVIRONMENT_FILE_PATTERNS = {
    "Dockerfile",
    "docker-compose.yaml",
    "docker-compose.yml",
}

HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)

GITHUB_PROXYABLE_HOSTS = {"github.com", "www.github.com"}
GIT_REMOTE_RETRYABLE_ERROR_MARKERS = (
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
GIT_PROXY_RETRYABLE_ERROR_MARKERS = (
    "requested URL returned error: 403",
    "no longer supports git over dumb-http",
)


def run_candidate_pair_construction(run_dir: Path) -> CandidatePairConstructionResult:
    project_root = Path.cwd()
    run_dir = run_dir.resolve()
    config = load_config(run_dir / "config.yaml")
    repositories_path = run_dir / "repo_discovery" / "repositories.jsonl"
    candidate_pairs_dir = run_dir / "candidate_pairs"
    repo_outputs_dir = candidate_pairs_dir / "repo_outputs"
    pair_outputs_dir = candidate_pairs_dir / "pair_outputs"
    candidate_pairs_dir.mkdir(parents=True, exist_ok=True)
    repo_outputs_dir.mkdir(parents=True, exist_ok=True)
    pair_outputs_dir.mkdir(parents=True, exist_ok=True)

    repositories = _read_jsonl(repositories_path)
    work_items = [
        _repo_work_item(
            index=index, repository=repository, repo_outputs_dir=repo_outputs_dir
        )
        for index, repository in enumerate(repositories)
    ]
    work_to_run: list[RepoWorkItem] = []
    for item in work_items:
        if _repo_output_reusable(item.output_dir, config.candidate_pairs):
            continue
        if item.output_dir.exists():
            shutil.rmtree(item.output_dir)
        work_to_run.append(item)

    if work_to_run:
        download_slots = Semaphore(
            config.candidate_pairs.max_concurrent_repo_downloads
        )
        with ThreadPoolExecutor(
            max_workers=config.candidate_pairs.max_concurrent_repos
        ) as executor:
            futures = [
                executor.submit(
                    _process_repo_work_item,
                    item,
                    project_root,
                    config.candidate_pairs,
                    pair_outputs_dir,
                    download_slots,
                )
                for item in work_to_run
            ]
            for future in as_completed(futures):
                future.result()

    repo_results, accepted_candidates, rejected_candidates = _aggregate_repo_outputs(
        work_items
    )
    _cleanup_unused_pair_outputs(
        pair_outputs_dir, accepted_candidates, rejected_candidates
    )

    _write_jsonl(candidate_pairs_dir / "repo_results.jsonl", repo_results)
    _write_jsonl(candidate_pairs_dir / "accepted_candidates.jsonl", accepted_candidates)
    _write_jsonl(candidate_pairs_dir / "rejected_candidates.jsonl", rejected_candidates)

    return CandidatePairConstructionResult(
        run_dir=run_dir,
        repo_count=len(repo_results),
        accepted_candidates=len(accepted_candidates),
        rejected_candidates=len(rejected_candidates),
    )


def _repo_work_item(
    index: int, repository: dict[str, Any], repo_outputs_dir: Path
) -> RepoWorkItem:
    output_key = _repo_output_key(repository, index)
    return RepoWorkItem(
        index=index,
        repository=repository,
        output_key=output_key,
        output_dir=repo_outputs_dir / output_key,
    )


def _repo_output_key(repository: dict[str, Any], index: int) -> str:
    full_name = _non_empty_str(repository.get("full_name"))
    if full_name:
        parts = full_name.split("/", 1)
        if len(parts) == 2 and parts[0] and parts[1]:
            return full_name.replace("/", "__")
    return f"input_{index:06d}"


def _repo_output_reusable(output_dir: Path, config: CandidatePairsConfig) -> bool:
    repo_result_path = output_dir / "repo_result.json"
    accepted_path = output_dir / "accepted_candidates.jsonl"
    rejected_path = output_dir / "rejected_candidates.jsonl"
    if not (
        repo_result_path.exists() and accepted_path.exists() and rejected_path.exists()
    ):
        return False
    try:
        repo_result = _read_json(repo_result_path)
        accepted_rows = _read_jsonl(accepted_path)
        rejected_rows = _read_jsonl(rejected_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    status = repo_result.get("status")
    if status == "skipped":
        return True
    if status != "completed":
        return False
    if not _repo_result_matches_candidate_pair_config(repo_result, config):
        return False
    current_row_fields = {
        "removed_object_targets_count",
        "removed_range_targets_count",
        "implementation_units_count",
        "reinsert_source_lines",
        "task_base_compile",
        "task_base_compile_error",
        "implementation_code_paths",
    }
    if any(
        not current_row_fields.issubset(row) for row in [*accepted_rows, *rejected_rows]
    ):
        return False
    for row in accepted_rows:
        if not _pair_output_reusable(
            output_dir,
            row,
            require_obsolete_reinsert_patch=True,
        ):
            return False
    for row in rejected_rows:
        if not _pair_output_reusable(
            output_dir,
            row,
            require_obsolete_reinsert_patch=False,
        ):
            return False
    return True


def _pair_output_reusable(
    repo_output_dir: Path,
    row: dict[str, Any],
    *,
    require_obsolete_reinsert_patch: bool,
) -> bool:
    pair_id = row.get("pair_id")
    if not isinstance(pair_id, str) or not pair_id:
        return False
    pair_output_dir = repo_output_dir.parent.parent / "pair_outputs" / pair_id
    required_files = [
        "metadata.json",
        "reinsert_targets.jsonl",
        "implementation_units.jsonl",
    ]
    if require_obsolete_reinsert_patch:
        required_files.extend(
            [
                "reinsert_extraction.patch",
                "obsolete_reinsert.patch",
            ]
        )
    return all((pair_output_dir / name).exists() for name in required_files)


def _repo_result_matches_candidate_pair_config(
    repo_result: dict[str, Any],
    config: CandidatePairsConfig,
) -> bool:
    expected = {
        "offset_ranges": [
            offset_range.model_dump() for offset_range in config.offset_ranges
        ],
        "min_implementation_units": config.min_implementation_units,
        "min_reinsert_source_lines": config.min_reinsert_source_lines,
        "max_implementation_units": config.max_implementation_units,
        "max_reinsert_source_lines": config.max_reinsert_source_lines,
    }
    return all(repo_result.get(key) == value for key, value in expected.items())


def _process_repo_work_item(
    item: RepoWorkItem,
    project_root: Path,
    config: CandidatePairsConfig,
    pair_outputs_dir: Path,
    download_slots: Semaphore,
) -> None:
    repo_input = _repo_input(item.repository, project_root)
    if repo_input is None:
        _write_repo_output(
            item.output_dir,
            repo_result=_skipped_repo_result(item.repository, config),
            accepted_candidates=[],
            rejected_candidates=[],
        )
        return

    try:
        repo_result, accepted_candidates, rejected_candidates = (
            _construct_repo_candidate_pairs(
                repo_input,
                config,
                pair_outputs_dir=pair_outputs_dir,
                download_slots=download_slots,
            )
        )
    except RuntimeError as exc:
        repo_result = _failed_repo_result(repo_input, config, error=str(exc))
        accepted_candidates = []
        rejected_candidates = []

    _write_repo_output(
        item.output_dir,
        repo_result=repo_result,
        accepted_candidates=accepted_candidates,
        rejected_candidates=rejected_candidates,
    )


def _construct_repo_candidate_pairs(
    repo_input: RepoInput,
    config: CandidatePairsConfig,
    *,
    pair_outputs_dir: Path,
    download_slots: Semaphore,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    _prepare_repo_cache(repo_input, download_slots)
    commits = _mainline_commits(repo_input.cache_dir, repo_input.default_branch)

    accepted_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []
    accepted_count = 0
    rejected_count = 0
    offsets_examined = 0
    head_commit = commits[0] if commits else None

    if head_commit is not None:
        repo_analysis = RepoAnalysisContext(
            cache_dir=repo_input.cache_dir,
            new_commit=head_commit,
        )
        for offset in config.offset_values():
            if offset >= len(commits):
                break
            offsets_examined += 1
            old_commit = commits[offset]
            new_commit = head_commit
            candidate = _candidate_pair(repo_input, old_commit, new_commit, offset)
            pair_analysis = repo_analysis.pair(old_commit)
            diff_files = _diff_files(
                repo_input.cache_dir,
                old_commit,
                new_commit,
                analysis_context=pair_analysis,
            )
            implementation_code_paths = _implementation_code_paths(diff_files)
            pair_output_dir = pair_outputs_dir / candidate["pair_id"]
            artifact_error: str | None = None
            try:
                artifact_stats = _write_pair_artifacts(
                    pair_output_dir,
                    cache_dir=repo_input.cache_dir,
                    config=config,
                    candidate=candidate,
                    diff_files=diff_files,
                    implementation_code_paths=implementation_code_paths,
                    analysis_context=pair_analysis,
                )
            except RuntimeError as exc:
                artifact_error = str(exc)
                artifact_stats = {
                    **_artifact_stats_from_pair_output(pair_output_dir),
                    "task_base_compile": None,
                    "task_base_compile_error": None,
                }
            reject_reason, reject_detail = _candidate_filter_rejection(
                artifact_stats,
                config,
                artifact_error=artifact_error,
            )
            _write_pair_outcome_metadata(
                pair_output_dir,
                candidate=candidate,
                artifact_stats=artifact_stats,
                implementation_code_paths=implementation_code_paths,
                reject_reason=reject_reason,
                reject_detail=reject_detail,
            )
            if reject_reason is None:
                accepted_count += 1
                accepted_candidates.append(
                    {
                        **candidate,
                        "removed_object_targets_count": artifact_stats[
                            "removed_object_targets_count"
                        ],
                        "removed_range_targets_count": artifact_stats[
                            "removed_range_targets_count"
                        ],
                        "implementation_units_count": artifact_stats[
                            "implementation_units_count"
                        ],
                        "reinsert_source_lines": artifact_stats[
                            "reinsert_source_lines"
                        ],
                        "task_base_compile": artifact_stats["task_base_compile"],
                        "task_base_compile_error": artifact_stats[
                            "task_base_compile_error"
                        ],
                        "implementation_code_paths": implementation_code_paths,
                    }
                )
            else:
                rejected_count += 1
                rejected_candidates.append(
                    {
                        "pair_id": candidate["pair_id"],
                        "repo_key": repo_input.repo_key,
                        "full_name": repo_input.full_name,
                        "github_repo_id": repo_input.github_repo_id,
                        "old_commit": old_commit,
                        "new_commit": new_commit,
                        "offset": offset,
                        "reject_reason": reject_reason,
                        "reject_detail": reject_detail,
                        "removed_object_targets_count": artifact_stats[
                            "removed_object_targets_count"
                        ],
                        "removed_range_targets_count": artifact_stats[
                            "removed_range_targets_count"
                        ],
                        "implementation_units_count": artifact_stats[
                            "implementation_units_count"
                        ],
                        "reinsert_source_lines": artifact_stats[
                            "reinsert_source_lines"
                        ],
                        "task_base_compile": artifact_stats["task_base_compile"],
                        "task_base_compile_error": artifact_stats[
                            "task_base_compile_error"
                        ],
                        "implementation_code_paths": implementation_code_paths,
                    }
                )
        repo_analysis.close()

    repo_result = _completed_repo_result(
        repo_input,
        config,
        head_commit=head_commit,
        offsets_examined=offsets_examined,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
    )
    return repo_result, accepted_candidates, rejected_candidates


def _write_pair_outcome_metadata(
    pair_output_dir: Path,
    *,
    candidate: dict[str, Any],
    artifact_stats: dict[str, Any],
    implementation_code_paths: list[str],
    reject_reason: str | None,
    reject_detail: dict[str, Any] | None,
) -> None:
    metadata_path = pair_output_dir / "metadata.json"
    try:
        metadata = _read_json(metadata_path) if metadata_path.exists() else {}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        metadata = {}
    candidate_status = "accepted" if reject_reason is None else "rejected"
    metadata = {
        **candidate,
        **metadata,
        **artifact_stats,
        "task_base_compile": artifact_stats.get("task_base_compile"),
        "task_base_compile_error": artifact_stats.get("task_base_compile_error"),
        "implementation_code_paths": metadata.get(
            "implementation_code_paths",
            implementation_code_paths,
        ),
        "candidate_status": candidate_status,
        "reject_reason": reject_reason,
        "reject_detail": reject_detail,
    }
    pair_output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(metadata_path, metadata)


def _write_repo_output(
    output_dir: Path,
    *,
    repo_result: dict[str, Any],
    accepted_candidates: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "accepted_candidates.jsonl", accepted_candidates)
    _write_jsonl(output_dir / "rejected_candidates.jsonl", rejected_candidates)
    _write_json(output_dir / "repo_result.json", repo_result)


def _aggregate_repo_outputs(
    work_items: list[RepoWorkItem],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    repo_results: list[dict[str, Any]] = []
    accepted_candidates: list[dict[str, Any]] = []
    rejected_candidates: list[dict[str, Any]] = []
    for item in work_items:
        repo_results.append(_read_json(item.output_dir / "repo_result.json"))
        accepted_candidates.extend(
            _read_jsonl(item.output_dir / "accepted_candidates.jsonl")
        )
        rejected_candidates.extend(
            _read_jsonl(item.output_dir / "rejected_candidates.jsonl")
        )
    return repo_results, accepted_candidates, rejected_candidates


def _cleanup_unused_pair_outputs(
    pair_outputs_dir: Path,
    accepted_candidates: list[dict[str, Any]],
    rejected_candidates: list[dict[str, Any]],
) -> None:
    if not pair_outputs_dir.exists():
        return
    used_pair_dirs: set[str] = set()
    for row in [*accepted_candidates, *rejected_candidates]:
        pair_id = row.get("pair_id")
        if isinstance(pair_id, str) and pair_id:
            used_pair_dirs.add(pair_id)
    for child in pair_outputs_dir.iterdir():
        if child.is_dir() and child.name not in used_pair_dirs:
            shutil.rmtree(child)


def _repo_input(repository: dict[str, Any], project_root: Path) -> RepoInput | None:
    github_repo_id = repository.get("github_repo_id")
    full_name = _non_empty_str(repository.get("full_name"))
    clone_url = _non_empty_str(repository.get("clone_url"))
    default_branch = _non_empty_str(repository.get("default_branch"))
    if (
        type(github_repo_id) is not int
        or not full_name
        or not clone_url
        or not default_branch
    ):
        return None
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    owner, repo = parts
    return RepoInput(
        github_repo_id=github_repo_id,
        full_name=full_name,
        clone_url=clone_url,
        default_branch=default_branch,
        repo_key=full_name.replace("/", "__"),
        cache_dir=project_root / "data" / "cache" / "repos" / owner / f"{repo}.git",
    )


def _prepare_repo_cache(repo: RepoInput, download_slots: Semaphore) -> None:
    if repo.cache_dir.exists():
        if not _repo_cache_has_basic_structure(repo):
            shutil.rmtree(repo.cache_dir, ignore_errors=True)
            with download_slots:
                _clone_repo_cache(repo)
            return
        with download_slots:
            _refresh_repo_cache(repo)
        return

    with download_slots:
        _clone_repo_cache(repo)


def _repo_cache_has_basic_structure(repo: RepoInput) -> bool:
    try:
        output = _run_git(
            [f"--git-dir={repo.cache_dir}", "rev-parse", "--is-bare-repository"]
        )
        if output.strip() != "true":
            return False
        _run_git([f"--git-dir={repo.cache_dir}", "remote", "get-url", "origin"])
    except RuntimeError:
        return False
    return True


def _ensure_default_branch_ref(repo: RepoInput) -> None:
    _run_git(
        [f"--git-dir={repo.cache_dir}", "show-ref", f"refs/heads/{repo.default_branch}"]
    )


def _clone_repo_cache(repo: RepoInput) -> None:
    repo.cache_dir.parent.mkdir(parents=True, exist_ok=True)
    _run_git_remote_with_retry(
        [
            "clone",
            "--bare",
            "--single-branch",
            "--no-tags",
            "--branch",
            repo.default_branch,
            _repo_remote_url(repo),
            str(repo.cache_dir),
        ],
        cleanup_after_failed_attempt=lambda: _remove_incomplete_repo_cache(
            repo.cache_dir
        ),
        operation_label=repo.full_name,
    )
    _set_cache_origin(repo)
    _pin_cache_head(repo)


def _refresh_repo_cache(repo: RepoInput) -> None:
    _run_git_remote_with_retry(
        [
            f"--git-dir={repo.cache_dir}",
            "fetch",
            "--prune",
            "--no-tags",
            _repo_remote_url(repo),
            (
                f"+refs/heads/{repo.default_branch}:"
                f"refs/heads/{repo.default_branch}"
            ),
        ],
        operation_label=repo.full_name,
    )
    _set_cache_origin(repo)
    _ensure_default_branch_ref(repo)
    _pin_cache_head(repo)


def _remove_incomplete_repo_cache(cache_dir: Path) -> None:
    if not cache_dir.exists():
        return
    try:
        shutil.rmtree(cache_dir)
    except OSError as exc:
        raise RuntimeError(
            f"failed to remove incomplete Git cache before clone: {cache_dir}"
        ) from exc


def _set_cache_origin(repo: RepoInput) -> None:
    _run_git(
        [
            f"--git-dir={repo.cache_dir}",
            "remote",
            "set-url",
            "origin",
            _canonical_repo_url(repo),
        ]
    )


def _pin_cache_head(repo: RepoInput) -> None:
    _run_git(
        [
            f"--git-dir={repo.cache_dir}",
            "symbolic-ref",
            "HEAD",
            f"refs/heads/{repo.default_branch}",
        ]
    )


def _canonical_repo_url(repo: RepoInput) -> str:
    return repo.clone_url


def _repo_remote_url(repo: RepoInput) -> str:
    canonical_url = _canonical_repo_url(repo)
    proxy_prefixes = _configured_github_proxy_prefixes()
    if not proxy_prefixes:
        return canonical_url
    proxy_prefix = proxy_prefixes[0]
    if canonical_url.startswith(proxy_prefix):
        return canonical_url
    parsed = urlsplit(canonical_url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return canonical_url
    if (parsed.hostname or "").lower() not in GITHUB_PROXYABLE_HOSTS:
        return canonical_url
    return f"{proxy_prefix}{canonical_url}"


def _configured_github_proxy_prefixes() -> tuple[str, ...]:
    raw_value = os.getenv("BELTA_GITHUB_PROXY_PREFIX", "").strip()
    if not raw_value:
        return ()
    try:
        decoded = json.loads(raw_value)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, list):
        candidates = decoded
    elif isinstance(decoded, str):
        candidates = [decoded]
    else:
        candidates = re.split(r"[,\n]", raw_value)

    prefixes: list[str] = []
    for candidate in candidates:
        normalized = _normalize_github_proxy_prefix(str(candidate or ""))
        if normalized and normalized not in prefixes:
            prefixes.append(normalized)
    return tuple(prefixes)


def _normalize_github_proxy_prefix(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        return ""
    return stripped if stripped.endswith("/") else f"{stripped}/"


def _github_proxy_prefix_for_url(
    url: str,
    *,
    prefixes: tuple[str, ...],
) -> str | None:
    for prefix in sorted(prefixes, key=len, reverse=True):
        if not url.startswith(prefix):
            continue
        upstream = url[len(prefix) :]
        parsed = urlsplit(upstream)
        if (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and (parsed.hostname or "").lower() in GITHUB_PROXYABLE_HOSTS
        ):
            return prefix
    return None


def _rewrite_github_remote_urls(
    args: list[str],
    proxy_prefix: str | None,
    *,
    configured_prefixes: tuple[str, ...],
) -> None:
    for index, value in enumerate(args):
        matched = _github_proxy_prefix_for_url(
            value,
            prefixes=configured_prefixes,
        )
        upstream = value[len(matched) :] if matched is not None else value
        parsed = urlsplit(upstream)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or (parsed.hostname or "").lower() not in GITHUB_PROXYABLE_HOSTS
        ):
            continue
        args[index] = f"{proxy_prefix or ''}{upstream}"


def _proxied_github_prefix(
    args: list[str],
    *,
    configured_prefixes: tuple[str, ...],
) -> str | None:
    return next(
        (
            matched
            for value in args
            if (
                matched := _github_proxy_prefix_for_url(
                    value,
                    prefixes=configured_prefixes,
                )
            )
            is not None
        ),
        None,
    )


def _run_git_remote_with_retry(
    args: list[str],
    *,
    cleanup_after_failed_attempt: Callable[[], None] | None = None,
    operation_label: str | None = None,
) -> str:
    configured_attempts = max(_env_int("BELTA_GIT_REMOTE_MAX_ATTEMPTS", 3), 1)
    retry_delay_seconds = max(
        _env_float("BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS", 3.0), 0.0
    )
    timeout_seconds = max(_env_float("BELTA_GIT_REMOTE_TIMEOUT_SECONDS", 600.0), 0.0)
    proxy_prefixes = _configured_github_proxy_prefixes()
    using_proxy = (
        _proxied_github_prefix(
            args,
            configured_prefixes=proxy_prefixes,
        )
        is not None
    )
    attempt_routes: tuple[str | None, ...] = (
        (*proxy_prefixes, None)
        if using_proxy
        else tuple(None for _ in range(configured_attempts))
    )

    for attempt, proxy_prefix in enumerate(attempt_routes, start=1):
        attempt_args = list(args)
        if using_proxy:
            _rewrite_github_remote_urls(
                attempt_args,
                proxy_prefix,
                configured_prefixes=proxy_prefixes,
            )
        started_at = time.monotonic()
        try:
            result = _run_git(
                attempt_args,
                timeout_seconds=timeout_seconds if timeout_seconds else None,
            )
            _log_git_remote_attempt(
                operation_label,
                proxy_prefix=proxy_prefix,
                attempt=attempt,
                total_attempts=len(attempt_routes),
                status="success",
                elapsed_seconds=time.monotonic() - started_at,
            )
            return result
        except RuntimeError as exc:
            _log_git_remote_attempt(
                operation_label,
                proxy_prefix=proxy_prefix,
                attempt=attempt,
                total_attempts=len(attempt_routes),
                status=_git_remote_failure_status(str(exc)),
                elapsed_seconds=time.monotonic() - started_at,
            )
            if cleanup_after_failed_attempt is not None:
                cleanup_after_failed_attempt()
            retryable = _is_retryable_git_remote_error(str(exc)) or (
                proxy_prefix is not None
                and _is_retryable_git_proxy_error(str(exc))
            )
            if attempt >= len(attempt_routes) or not retryable:
                raise
            if retry_delay_seconds:
                time.sleep(retry_delay_seconds)
    raise RuntimeError(f"git remote command failed: git {' '.join(args)}")


def _log_git_remote_attempt(
    operation_label: str | None,
    *,
    proxy_prefix: str | None,
    attempt: int,
    total_attempts: int,
    status: str,
    elapsed_seconds: float,
) -> None:
    if operation_label is None:
        return
    route = proxy_prefix or "direct"
    print(
        f"git_remote repo={operation_label} route={route} "
        f"attempt={attempt}/{total_attempts} status={status} "
        f"elapsed={elapsed_seconds:.2f}s",
        flush=True,
    )


def _git_remote_failure_status(message: str) -> str:
    lower = message.lower()
    if "timed out" in lower or "operation timed out" in lower:
        return "timeout"
    if "requested url returned error: 403" in lower:
        return "http_403"
    return "network_error" if _is_retryable_git_remote_error(message) else "error"


def _is_retryable_git_remote_error(message: str) -> bool:
    lower = message.lower()
    return any(marker.lower() in lower for marker in GIT_REMOTE_RETRYABLE_ERROR_MARKERS)


def _is_retryable_git_proxy_error(message: str) -> bool:
    lower = message.lower()
    return any(marker.lower() in lower for marker in GIT_PROXY_RETRYABLE_ERROR_MARKERS)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _mainline_commits(cache_dir: Path, default_branch: str) -> list[str]:
    output = _run_git(
        [
            f"--git-dir={cache_dir}",
            "rev-list",
            "--first-parent",
            f"refs/heads/{default_branch}",
        ]
    )
    commits = [line.strip() for line in output.splitlines() if line.strip()]
    if not commits:
        raise RuntimeError(f"no commits found for {default_branch}")
    return commits


def _diff_files(
    cache_dir: Path,
    old_commit: str,
    new_commit: str,
    *,
    analysis_context: PairAnalysisContext | None = None,
) -> list[DiffFile]:
    analysis = analysis_context or RepoAnalysisContext(cache_dir, new_commit).pair(
        old_commit
    )
    statuses = _diff_name_status(cache_dir, old_commit, new_commit)
    return [
        DiffFile(
            status=status,
            path=path,
            path_class=classify_diff_path(
                cache_dir,
                old_commit,
                new_commit,
                path,
                status=status,
                analysis_context=analysis,
            ),
        )
        for status, path in statuses
    ]


def _diff_name_status(
    cache_dir: Path, old_commit: str, new_commit: str
) -> list[tuple[str, str]]:
    output = _run_git_bytes(
        [
            f"--git-dir={cache_dir}",
            "diff",
            "--name-status",
            "-z",
            "--no-renames",
            old_commit,
            new_commit,
        ]
    )
    tokens = _nul_tokens(output)
    rows: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        status = tokens[index]
        path_index = index + 1
        if path_index >= len(tokens):
            raise RuntimeError("invalid git diff --name-status output")
        path = tokens[path_index]
        rows.append((status, path))
        index += 2
    return rows


def classify_diff_path(
    cache_dir: Path,
    old_commit: str,
    new_commit: str,
    path: str,
    *,
    status: str | None = None,
    analysis_context: PairAnalysisContext | None = None,
) -> PathClass:
    if status in {"D", "T"} or not _is_python_implementation_candidate_path(path):
        return "ignored"

    analysis = analysis_context or RepoAnalysisContext(cache_dir, new_commit).pair(
        old_commit
    )
    commits = _commits_for_status(status, old_commit=old_commit, new_commit=new_commit)
    if not commits:
        return "ignored"
    preferred_commit = new_commit if new_commit in commits else old_commit
    preferred_source = analysis.source(preferred_commit, path)
    if preferred_source is None:
        return "ignored"

    name = Path(path).name
    if name == "setup.py" and analysis.is_packaging_setup(preferred_commit, path):
        return "ignored"
    if Path(name).suffix:
        if Path(name).suffix != ".py":
            return "ignored"
    elif not _has_python_shebang(preferred_source):
        return "ignored"
    if all(analysis.is_parseable(commit, path) for commit in commits):
        return "implementation_code"
    return "ignored"


def classify_path(path: str, *, source: bytes | None = None) -> PathClass:
    if not _is_python_implementation_candidate_path(path):
        return "ignored"
    name = Path(path).name
    if source is None:
        return "implementation_code" if Path(name).suffix == ".py" else "ignored"
    analysis = _analyze_python_source(source)
    if name == "setup.py" and analysis.packaging_setup:
        return "ignored"
    suffix = Path(name).suffix
    if suffix == ".py" and analysis.parseable:
        return "implementation_code"
    if not suffix and _has_python_shebang(source) and analysis.parseable:
        return "implementation_code"
    return "ignored"


def _is_python_implementation_candidate_path(path: str) -> bool:
    parts = [part for part in path.split("/") if part]
    name = parts[-1] if parts else path
    is_root_file = len(parts) == 1
    directory_parts = [part.casefold() for part in parts[:-1]]
    stem = Path(name).stem
    normalized_stem = stem.casefold()
    if directory_parts and directory_parts[0] in ROOT_EXCLUDED_DIR_NAMES:
        return False
    if any(_has_test_directory_role(part) for part in parts[:-1]) or (
        _has_test_file_role(name) or _matches_any(name, TEST_FILE_PATTERNS)
    ):
        return False
    if (
        any(part in EXCLUDED_DIR_NAMES for part in directory_parts)
        or any(part in NON_IMPLEMENTATION_ROLE_NAMES for part in directory_parts)
        or normalized_stem in NON_IMPLEMENTATION_ROLE_NAMES
        or _matches_any(
            name,
            EXCLUDED_FILE_PATTERNS,
        )
    ):
        return False
    if is_root_file and _matches_any(name, ROOT_ENVIRONMENT_FILE_PATTERNS):
        return False
    if _matches_any(name, ANYWHERE_ENVIRONMENT_FILE_PATTERNS):
        return False
    suffix = Path(name).suffix
    return suffix == ".py" or not suffix


def _has_test_directory_role(path_part: str) -> bool:
    stem = Path(path_part).stem
    normalized = stem.casefold()
    tokens = _identifier_tokens(stem)
    return (
        normalized in TEST_DIR_NAMES
        or normalized.startswith("test")
        or normalized.endswith("tests")
        or any(token in TEST_ROLE_TOKENS for token in tokens)
    )


def _has_test_file_role(name: str) -> bool:
    stem = Path(name).stem
    normalized = stem.casefold()
    tokens = _identifier_tokens(stem)
    return (
        normalized.startswith("test")
        or normalized.endswith("tests")
        or any(token in TEST_ROLE_TOKENS for token in tokens)
    )


def _identifier_tokens(value: str) -> tuple[str, ...]:
    with_camel_boundaries = re.sub(
        r"(?<=[A-Z])(?=[A-Z][a-z])",
        "_",
        re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value),
    )
    return tuple(
        token.casefold()
        for token in re.split(r"[^A-Za-z0-9]+", with_camel_boundaries)
        if token
    )


def _parse_python_ast(source: str | bytes) -> ast.Module:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        return ast.parse(source)


def _analyze_python_source(source: bytes) -> PythonBlobAnalysis:
    try:
        tree = _parse_python_ast(_decode_source(source))
    except (SyntaxError, ValueError, UnicodeError):
        return PythonBlobAnalysis(
            source=source,
            parseable=False,
            python_objects=(),
            packaging_setup=False,
        )
    return PythonBlobAnalysis(
        source=source,
        parseable=True,
        python_objects=tuple(_python_objects_from_tree(tree)),
        packaging_setup=_is_packaging_setup_tree(tree),
    )


def _commits_for_status(
    status: str | None,
    *,
    old_commit: str,
    new_commit: str,
) -> list[str]:
    if status == "A":
        return [new_commit]
    if status == "D":
        return [old_commit]
    if status == "M" or status is None:
        return [old_commit, new_commit]
    return []


def _has_python_shebang(source: bytes) -> bool:
    first_line = (
        source.splitlines()[0].decode("utf-8", errors="ignore").strip()
        if source
        else ""
    )
    return first_line.startswith("#!") and "python" in first_line


def _read_file_from_git(cache_dir: Path, commit: str, path: str) -> bytes | None:
    try:
        return _run_git_bytes([f"--git-dir={cache_dir}", "show", f"{commit}:{path}"])
    except RuntimeError:
        return None


def _is_packaging_setup_tree(tree: ast.Module) -> bool:
    direct_setup_names: set[str] = set()
    module_aliases: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module in {"setuptools", "distutils.core"}:
                for alias in node.names:
                    if alias.name == "setup":
                        direct_setup_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "setuptools":
                    module_aliases.add(alias.asname or "setuptools")
                elif alias.name == "distutils.core":
                    module_aliases.add(alias.asname or "distutils")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct_setup_names:
            return True
        if _is_setup_attribute_call(func, module_aliases):
            return True
    return False


def _is_parseable_python_source(source: bytes) -> bool:
    return _analyze_python_source(source).parseable


def _is_setup_attribute_call(node: ast.AST, module_aliases: set[str]) -> bool:
    if not isinstance(node, ast.Attribute) or node.attr != "setup":
        return False
    parts = _attribute_parts(node.value)
    if not parts:
        return False
    if parts[0] in module_aliases:
        return True
    return ".".join(parts) in {"setuptools", "distutils.core"}


def _attribute_parts(node: ast.AST) -> list[str] | None:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        base = _attribute_parts(node.value)
        if base is None:
            return None
        return [*base, node.attr]
    return None


def _implementation_code_paths(diff_files: list[DiffFile]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for file in diff_files:
        if file.path_class == "implementation_code" and file.path not in seen:
            paths.append(file.path)
            seen.add(file.path)
    return paths


def _candidate_filter_rejection(
    artifact_stats: dict[str, Any],
    config: CandidatePairsConfig,
    *,
    artifact_error: str | None,
) -> tuple[str | None, dict[str, Any] | None]:
    implementation_units_count = artifact_stats["implementation_units_count"]
    reinsert_source_lines = artifact_stats["reinsert_source_lines"]
    if implementation_units_count < config.min_implementation_units:
        return (
            "not_enough_implementation_units",
            {
                "implementation_units_count": implementation_units_count,
                "min_implementation_units": config.min_implementation_units,
            },
        )
    if reinsert_source_lines < config.min_reinsert_source_lines:
        return (
            "not_enough_reinsert_source_lines",
            {
                "reinsert_source_lines": reinsert_source_lines,
                "min_reinsert_source_lines": config.min_reinsert_source_lines,
            },
        )
    if (
        config.max_implementation_units is not None
        and implementation_units_count > config.max_implementation_units
    ):
        return (
            "too_many_implementation_units",
            {
                "implementation_units_count": implementation_units_count,
                "max_implementation_units": config.max_implementation_units,
            },
        )
    if (
        config.max_reinsert_source_lines is not None
        and reinsert_source_lines > config.max_reinsert_source_lines
    ):
        return (
            "too_many_reinsert_source_lines",
            {
                "reinsert_source_lines": reinsert_source_lines,
                "max_reinsert_source_lines": config.max_reinsert_source_lines,
            },
        )
    if artifact_error is not None:
        return (
            "obsolete_reinsert_failed",
            {
                "error": artifact_error,
            },
        )
    return None, None


def _write_pair_artifacts(
    pair_output_dir: Path,
    *,
    cache_dir: Path,
    config: CandidatePairsConfig,
    candidate: dict[str, Any],
    diff_files: list[DiffFile],
    implementation_code_paths: list[str],
    analysis_context: PairAnalysisContext | None = None,
) -> dict[str, Any]:
    if pair_output_dir.exists():
        shutil.rmtree(pair_output_dir)
    pair_output_dir.mkdir(parents=True, exist_ok=True)
    old_commit = str(candidate["old_commit"])
    new_commit = str(candidate["new_commit"])
    analysis = analysis_context or RepoAnalysisContext(cache_dir, new_commit).pair(
        old_commit
    )
    implementation_files = [
        file for file in diff_files if file.path_class == "implementation_code"
    ]
    modified_implementation_paths = [
        file.path for file in implementation_files if file.status == "M"
    ]
    extraction_patch, extraction_diffs = _combined_reinsert_extraction_diff(
        cache_dir,
        old_commit,
        new_commit,
        modified_implementation_paths,
    )

    reinsert_targets = _extract_reinsert_targets(
        analysis,
        implementation_files,
        extraction_diffs,
    )
    public_targets = _public_reinsert_targets(reinsert_targets)
    implementation_units = _implementation_units(public_targets)
    stats = _reinsert_artifact_stats(public_targets, implementation_units)

    _write_jsonl(pair_output_dir / "reinsert_targets.jsonl", public_targets)
    _write_jsonl(pair_output_dir / "implementation_units.jsonl", implementation_units)

    metadata = {
        **candidate,
        **stats,
        "task_base_compile": None,
        "task_base_compile_error": None,
        "implementation_code_paths": implementation_code_paths,
    }
    _write_json(pair_output_dir / "metadata.json", metadata)

    if _threshold_rejection(stats, config) is not None:
        return {**stats, "task_base_compile": None, "task_base_compile_error": None}

    (pair_output_dir / "reinsert_extraction.patch").write_bytes(extraction_patch)

    obsolete_reinsert_patch, compile_status, compile_error = (
        _generate_obsolete_reinsert_patch(
            cache_dir,
            new_commit,
            reinsert_targets,
        )
    )
    (pair_output_dir / "obsolete_reinsert.patch").write_bytes(obsolete_reinsert_patch)

    metadata = {
        **metadata,
        "task_base_compile": compile_status,
        "task_base_compile_error": compile_error,
    }
    _write_json(pair_output_dir / "metadata.json", metadata)
    return {
        **stats,
        "task_base_compile": compile_status,
        "task_base_compile_error": compile_error,
    }


def _threshold_rejection(
    artifact_stats: dict[str, Any],
    config: CandidatePairsConfig,
) -> str | None:
    if artifact_stats["implementation_units_count"] < config.min_implementation_units:
        return "not_enough_implementation_units"
    if artifact_stats["reinsert_source_lines"] < config.min_reinsert_source_lines:
        return "not_enough_reinsert_source_lines"
    if (
        config.max_implementation_units is not None
        and artifact_stats["implementation_units_count"]
        > config.max_implementation_units
    ):
        return "too_many_implementation_units"
    if (
        config.max_reinsert_source_lines is not None
        and artifact_stats["reinsert_source_lines"] > config.max_reinsert_source_lines
    ):
        return "too_many_reinsert_source_lines"
    return None


def _artifact_stats_from_pair_output(pair_output_dir: Path) -> dict[str, Any]:
    metadata_path = pair_output_dir / "metadata.json"
    if metadata_path.exists():
        try:
            metadata = _read_json(metadata_path)
            return {
                "removed_object_targets_count": int(
                    metadata.get("removed_object_targets_count", 0)
                ),
                "removed_range_targets_count": int(
                    metadata.get("removed_range_targets_count", 0)
                ),
                "implementation_units_count": int(
                    metadata.get("implementation_units_count", 0)
                ),
                "reinsert_source_lines": int(metadata.get("reinsert_source_lines", 0)),
            }
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass
    return {
        "removed_object_targets_count": 0,
        "removed_range_targets_count": 0,
        "implementation_units_count": 0,
        "reinsert_source_lines": 0,
    }


def _extract_reinsert_targets(
    analysis: PairAnalysisContext,
    implementation_files: list[DiffFile],
    extraction_diffs: dict[str, bytes],
) -> list[dict[str, Any]]:
    range_targets_by_key: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
    object_targets_by_key: set[tuple[str, str, str, int, int]] = set()
    reinsert_targets: list[dict[str, Any]] = []
    for diff_file in implementation_files:
        path = diff_file.path
        if diff_file.status != "M":
            continue
        old_source = analysis.source(analysis.old_commit, path)
        if old_source is None:
            continue
        old_objects = analysis.objects(analysis.old_commit, path)
        python_targets = [
            target
            for target in old_objects
            if target.object_kind in {"function", "method"}
        ]
        if not python_targets:
            continue
        new_targets = [
            target
            for target in analysis.objects(analysis.new_commit, path)
            if target.object_kind in {"function", "method"}
        ]
        source_lines = _decode_source(old_source).splitlines(keepends=True)
        for deleted_range in _pure_deleted_ranges_from_diff(
            extraction_diffs.get(path, b"")
        ):
            covered_line_numbers: set[int] = set()
            for object_target in _fully_deleted_objects(old_objects, deleted_range):
                key = (
                    path,
                    object_target.object_kind,
                    object_target.qualified_name,
                    object_target.start_line,
                    object_target.end_line,
                )
                if key in object_targets_by_key:
                    continue
                object_targets_by_key.add(key)
                source_text = "".join(
                    source_lines[object_target.start_line - 1 : object_target.end_line]
                )
                if _is_trivia_only(source_text):
                    continue
                target_record: dict[str, Any] = {
                    "target_type": "removed_object",
                    "path": path,
                    "object_kind": object_target.object_kind,
                    "qualified_name": object_target.qualified_name,
                    "object_start_line": object_target.start_line,
                    "object_end_line": object_target.end_line,
                    "source_text": source_text,
                    "_new_insert_line": deleted_range.new_insert_line,
                }
                if object_target.object_kind == "class":
                    target_record["contained_targets"] = (
                        _contained_implementation_targets(
                            source_text.encode("utf-8", errors="surrogateescape")
                        )
                    )
                reinsert_targets.append(target_record)
                covered_line_numbers.update(
                    range(object_target.start_line, object_target.end_line + 1)
                )
            remaining_ranges = _deleted_ranges_without_lines(
                deleted_range,
                source_lines,
                covered_line_numbers,
            )
            for remaining_range in remaining_ranges:
                for segment_target, segment in _segments_by_target(
                    source_lines,
                    python_targets,
                    remaining_range,
                ):
                    if _is_trivia_only(segment.source_text):
                        continue
                    if not _new_target_context_accepts_range(
                        new_targets,
                        segment_target,
                        segment.new_insert_line,
                    ):
                        continue
                    key = (
                        path,
                        segment_target.object_kind,
                        segment_target.qualified_name,
                        segment_target.start_line,
                        segment_target.end_line,
                    )
                    target_record = range_targets_by_key.get(key)
                    if target_record is None:
                        target_record = {
                            "target_type": "removed_range",
                            "path": path,
                            "object_kind": segment_target.object_kind,
                            "qualified_name": segment_target.qualified_name,
                            "object_start_line": segment_target.start_line,
                            "object_end_line": segment_target.end_line,
                            "removed_ranges": [],
                        }
                        range_targets_by_key[key] = target_record
                        reinsert_targets.append(target_record)
                    target_record["removed_ranges"].append(
                        {
                            "old_start_line": segment.old_start_line,
                            "old_end_line": segment.old_end_line,
                            "source_text": segment.source_text,
                            "_new_insert_line": segment.new_insert_line,
                        }
                    )
    return reinsert_targets


def _fully_deleted_objects(
    objects: list[PythonTarget],
    deleted_range: DeletedRange,
) -> list[PythonTarget]:
    candidates = [
        target
        for target in objects
        if deleted_range.old_start_line <= target.start_line
        and target.end_line <= deleted_range.old_end_line
    ]
    selected: list[PythonTarget] = []
    for candidate in sorted(
        candidates,
        key=lambda target: (target.end_line - target.start_line, -target.start_line),
        reverse=True,
    ):
        if any(
            chosen.start_line <= candidate.start_line
            and candidate.end_line <= chosen.end_line
            for chosen in selected
        ):
            continue
        selected.append(candidate)
    return sorted(selected, key=lambda target: target.start_line)


def _deleted_ranges_without_lines(
    deleted_range: DeletedRange,
    source_lines: list[str],
    excluded_lines: set[int],
) -> list[DeletedRange]:
    if not excluded_lines:
        return [deleted_range]
    ranges: list[DeletedRange] = []
    current: list[tuple[int, str]] = []

    def flush_current() -> None:
        nonlocal current
        if not current:
            return
        ranges.append(
            DeletedRange(
                old_start_line=current[0][0],
                old_end_line=current[-1][0],
                source_text="".join(line for _, line in current),
                new_insert_line=deleted_range.new_insert_line,
            )
        )
        current = []

    for line_number in range(
        deleted_range.old_start_line, deleted_range.old_end_line + 1
    ):
        if line_number in excluded_lines:
            flush_current()
            continue
        line_text = (
            source_lines[line_number - 1] if line_number - 1 < len(source_lines) else ""
        )
        current.append((line_number, line_text))
    flush_current()
    return ranges


def _new_target_context_accepts_range(
    new_targets: list[PythonTarget],
    old_target: PythonTarget,
    new_insert_line: int,
) -> bool:
    matching = [
        target
        for target in new_targets
        if target.object_kind == old_target.object_kind
        and target.qualified_name == old_target.qualified_name
    ]
    if not matching:
        return False
    return any(
        target.start_line <= new_insert_line <= target.end_line for target in matching
    )


def _contained_implementation_targets(
    source: bytes,
    *,
    objects: list[PythonTarget] | None = None,
) -> list[dict[str, Any]]:
    source_lines = _decode_source(source).splitlines(keepends=True)
    contained: list[dict[str, Any]] = []
    targets = objects if objects is not None else _python_objects(source)
    for target in targets:
        if target.object_kind not in {"function", "method"}:
            continue
        contained.append(
            {
                "object_kind": target.object_kind,
                "qualified_name": target.qualified_name,
                "start_line": target.start_line,
                "end_line": target.end_line,
                "source_text": "".join(
                    source_lines[target.start_line - 1 : target.end_line]
                ),
            }
        )
    return contained


def _python_objects(source: bytes) -> list[PythonTarget]:
    return list(_analyze_python_source(source).python_objects)


def _python_objects_from_tree(tree: ast.Module) -> list[PythonTarget]:
    targets: list[PythonTarget] = []

    def visit_body(
        body: list[ast.stmt], scope: list[str], *, parent_is_class: bool
    ) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                if node.end_lineno is None:
                    continue
                qualified_name = ".".join([*scope, node.name])
                targets.append(
                    PythonTarget(
                        object_kind="class",
                        qualified_name=qualified_name,
                        start_line=_decorated_start_line(node),
                        end_line=node.end_lineno,
                    )
                )
                visit_body(node.body, [*scope, node.name], parent_is_class=True)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.end_lineno is None:
                    continue
                qualified_name = ".".join([*scope, node.name])
                targets.append(
                    PythonTarget(
                        object_kind="method" if parent_is_class else "function",
                        qualified_name=qualified_name,
                        start_line=_decorated_start_line(node),
                        end_line=node.end_lineno,
                    )
                )
                visit_body(node.body, [*scope, node.name], parent_is_class=False)

    visit_body(tree.body, [], parent_is_class=False)
    return targets


def _decorated_start_line(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    decorator_lines = [decorator.lineno for decorator in node.decorator_list]
    return min([node.lineno, *decorator_lines])


def _pure_deleted_ranges_for_path(
    cache_dir: Path,
    old_commit: str,
    new_commit: str,
    path: str,
) -> list[DeletedRange]:
    _, diffs_by_path = _combined_reinsert_extraction_diff(
        cache_dir,
        old_commit,
        new_commit,
        [path],
    )
    return _pure_deleted_ranges_from_diff(diffs_by_path.get(path, b""))


def _pure_deleted_ranges_from_diff(output: bytes) -> list[DeletedRange]:
    ranges: list[DeletedRange] = []
    old_line: int | None = None
    new_line: int | None = None
    pending_lines: list[tuple[int, str]] = []
    pending_insert_line: int | None = None
    hunk_ranges: list[DeletedRange] = []
    hunk_has_addition = False

    def flush_pending() -> None:
        nonlocal pending_lines, pending_insert_line
        if not pending_lines or pending_insert_line is None:
            pending_lines = []
            pending_insert_line = None
            return
        hunk_ranges.append(
            DeletedRange(
                old_start_line=pending_lines[0][0],
                old_end_line=pending_lines[-1][0],
                source_text="".join(line for _, line in pending_lines),
                new_insert_line=pending_insert_line,
            )
        )
        pending_lines = []
        pending_insert_line = None

    def flush_hunk() -> None:
        nonlocal hunk_ranges, hunk_has_addition
        flush_pending()
        if hunk_ranges and not hunk_has_addition:
            ranges.extend(hunk_ranges)
        hunk_ranges = []
        hunk_has_addition = False

    for line in _decode_source(output).splitlines(keepends=True):
        hunk_match = HUNK_RE.match(line)
        if hunk_match:
            flush_hunk()
            old_line = int(hunk_match.group("old_start"))
            new_line = int(hunk_match.group("new_start"))
            continue
        if old_line is None or new_line is None:
            continue
        if line.startswith("\\ No newline at end of file"):
            continue
        prefix = line[:1]
        text = line[1:]
        if prefix == "-":
            if pending_insert_line is None:
                pending_insert_line = new_line
            pending_lines.append((old_line, text))
            old_line += 1
        elif prefix == "+":
            hunk_has_addition = True
            flush_pending()
            new_line += 1
        else:
            flush_pending()
            old_line += 1
            new_line += 1
    flush_hunk()
    return ranges


def _combined_reinsert_extraction_diff(
    cache_dir: Path,
    old_commit: str,
    new_commit: str,
    paths: list[str],
) -> tuple[bytes, dict[str, bytes]]:
    if not paths:
        return b"", {}
    output = _run_git_bytes(
        [
            f"--git-dir={cache_dir}",
            "-c",
            "core.quotePath=false",
            "diff",
            "--histogram",
            "--unified=0",
            "--no-renames",
            old_commit,
            new_commit,
            "--",
            *paths,
        ]
    )
    return output, _split_combined_diff_by_path(output, paths)


def _split_combined_diff_by_path(output: bytes, paths: list[str]) -> dict[str, bytes]:
    expected_headers = {_diff_git_header(path): path for path in paths}
    lines = output.splitlines(keepends=True)
    section_starts = [
        index for index, line in enumerate(lines) if line.startswith(b"diff --git ")
    ]
    diffs_by_path: dict[str, bytes] = {}
    for position, start in enumerate(section_starts):
        stop = (
            section_starts[position + 1]
            if position + 1 < len(section_starts)
            else len(lines)
        )
        header = lines[start].rstrip(b"\r\n")
        path = expected_headers.get(header)
        if path is None:
            raise RuntimeError(
                "combined histogram diff contains an unexpected path header: "
                f"{header.decode('utf-8', errors='replace')}"
            )
        if path in diffs_by_path:
            raise RuntimeError(f"combined histogram diff repeats path: {path}")
        diffs_by_path[path] = b"".join(lines[start:stop])
    return {path: diffs_by_path.get(path, b"") for path in paths}


def _diff_git_header(path: str) -> bytes:
    old_path = _git_c_quote_path(f"a/{path}")
    new_path = _git_c_quote_path(f"b/{path}")
    return b"diff --git " + old_path + b" " + new_path


def _git_c_quote_path(path: str) -> bytes:
    raw = path.encode("utf-8", errors="surrogateescape")
    escapes = {
        7: b"\\a",
        8: b"\\b",
        9: b"\\t",
        10: b"\\n",
        11: b"\\v",
        12: b"\\f",
        13: b"\\r",
        34: b'\\"',
        92: b"\\\\",
    }
    if not any(byte in escapes or byte < 32 or byte == 127 for byte in raw):
        return raw
    quoted = bytearray(b'"')
    for byte in raw:
        escaped = escapes.get(byte)
        if escaped is not None:
            quoted.extend(escaped)
        elif byte < 32 or byte == 127:
            quoted.extend(f"\\{byte:03o}".encode("ascii"))
        else:
            quoted.append(byte)
    quoted.extend(b'"')
    return bytes(quoted)


def _segments_by_target(
    source_lines: list[str],
    targets: list[PythonTarget],
    deleted_range: DeletedRange,
) -> list[tuple[PythonTarget, DeletedRange]]:
    segments: list[tuple[PythonTarget, DeletedRange]] = []
    current_target: PythonTarget | None = None
    current_lines: list[tuple[int, str]] = []

    def flush_current() -> None:
        nonlocal current_target, current_lines
        if current_target is None or not current_lines:
            current_target = None
            current_lines = []
            return
        segments.append(
            (
                current_target,
                DeletedRange(
                    old_start_line=current_lines[0][0],
                    old_end_line=current_lines[-1][0],
                    source_text="".join(line for _, line in current_lines),
                    new_insert_line=deleted_range.new_insert_line,
                ),
            )
        )
        current_target = None
        current_lines = []

    for line_number in range(
        deleted_range.old_start_line, deleted_range.old_end_line + 1
    ):
        target = _target_for_line(targets, line_number)
        if target is None:
            flush_current()
            continue
        line_text = (
            source_lines[line_number - 1] if line_number - 1 < len(source_lines) else ""
        )
        if current_target != target:
            flush_current()
            current_target = target
        current_lines.append((line_number, line_text))
    flush_current()
    return segments


def _target_for_line(
    targets: list[PythonTarget], line_number: int
) -> PythonTarget | None:
    candidates = [
        target
        for target in targets
        if target.start_line <= line_number <= target.end_line
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda target: target.end_line - target.start_line)


def _is_trivia_only(source_text: str) -> bool:
    for line in source_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in {'"""', "'''"}:
            continue
        return False
    return True


def _generate_obsolete_reinsert_patch(
    cache_dir: Path,
    new_commit: str,
    reinsert_targets: list[dict[str, Any]],
) -> tuple[bytes, str | None, str | None]:
    if not reinsert_targets:
        return b"", None, None
    with tempfile.TemporaryDirectory() as tmp:
        work_tree = Path(tmp) / "work"
        patch_path = Path(tmp) / "obsolete_reinsert.patch"
        _checkout_commit(
            cache_dir,
            new_commit,
            work_tree,
            paths=_existing_file_reinsert_paths(reinsert_targets),
        )
        _insert_reinsert_targets(work_tree, reinsert_targets)
        patch = b""
        saw_nonempty_patch = False
        for diff_algorithm in ("--histogram", "--patience"):
            candidate_patch = _run_git_work_tree_bytes(
                cache_dir,
                work_tree,
                [
                    "diff",
                    diff_algorithm,
                    "--binary",
                    "--no-renames",
                    new_commit,
                    "--",
                    *_reinsert_target_paths(reinsert_targets),
                ],
            )
            if not candidate_patch:
                continue
            saw_nonempty_patch = True
            if not _patch_has_deleted_content(candidate_patch):
                patch = candidate_patch
                break
        if not patch:
            if saw_nonempty_patch:
                raise RuntimeError(
                    "obsolete_reinsert.patch contains deleted content lines"
                )
            raise RuntimeError("obsolete_reinsert.patch is empty")
        patch_path.write_bytes(patch)
        compile_status, compile_error = _task_base_compile_diagnostic(
            work_tree,
            _related_python_paths(reinsert_targets),
        )
        return patch, compile_status, compile_error


def _checkout_commit(
    cache_dir: Path,
    commit: str,
    work_tree: Path,
    *,
    paths: list[str] | None = None,
) -> None:
    work_tree.mkdir(parents=True, exist_ok=True)
    checkout_paths = paths if paths is not None else ["."]
    if not checkout_paths:
        return
    _run_git_work_tree(
        cache_dir, work_tree, ["checkout", "-f", commit, "--", *checkout_paths]
    )


def _insert_reinsert_targets(
    work_tree: Path, reinsert_targets: list[dict[str, Any]]
) -> None:
    ranges_by_path: dict[str, list[dict[str, Any]]] = {}
    for target in reinsert_targets:
        path = str(target["path"])
        if target["target_type"] == "removed_object":
            ranges_by_path.setdefault(path, []).append(
                {
                    "old_start_line": target["object_start_line"],
                    "old_end_line": target["object_end_line"],
                    "source_text": target["source_text"],
                    "_new_insert_line": target["_new_insert_line"],
                }
            )
        else:
            ranges_by_path.setdefault(path, []).extend(target["removed_ranges"])

    for path, ranges in ranges_by_path.items():
        file_path = work_tree / path
        if not file_path.exists():
            raise RuntimeError(f"target file missing in new commit: {path}")
        lines = file_path.read_bytes().splitlines(keepends=True)
        ordered_ranges = sorted(
            ranges,
            key=lambda item: (
                int(item["_new_insert_line"]),
                int(item["old_start_line"]),
            ),
            reverse=True,
        )
        for removed_range in ordered_ranges:
            insert_index = max(
                0, min(len(lines), int(removed_range["_new_insert_line"]))
            )
            lines[insert_index:insert_index] = _source_text_bytes(
                str(removed_range["source_text"])
            ).splitlines(keepends=True)
        file_path.write_bytes(b"".join(lines))


def _source_text_bytes(source_text: str) -> bytes:
    return source_text.encode("utf-8", errors="surrogateescape")


def _patch_has_deleted_content(patch: bytes) -> bool:
    return any(
        line.startswith(b"-") and not line.startswith(b"--- ")
        for line in patch.splitlines()
    )


def _public_reinsert_targets(
    reinsert_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    public_targets: list[dict[str, Any]] = []
    for target in reinsert_targets:
        if target["target_type"] == "removed_object":
            public_targets.append(
                {
                    key: value
                    for key, value in target.items()
                    if not str(key).startswith("_")
                }
            )
            continue
        public_ranges = []
        for removed_range in target["removed_ranges"]:
            public_ranges.append(
                {
                    key: value
                    for key, value in removed_range.items()
                    if not str(key).startswith("_")
                }
            )
        public_targets.append({**target, "removed_ranges": public_ranges})
    return public_targets


def _implementation_units(
    reinsert_targets: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, int, int]] = set()

    def add_unit(
        source_target_type: str,
        path: str,
        object_kind: str,
        qualified_name: str,
        object_start_line: int,
        object_end_line: int,
    ) -> None:
        key = (
            source_target_type,
            path,
            object_kind,
            qualified_name,
            object_start_line,
            object_end_line,
        )
        if key in seen:
            return
        seen.add(key)
        units.append(
            {
                "source_target_type": source_target_type,
                "path": path,
                "object_kind": object_kind,
                "qualified_name": qualified_name,
                "object_start_line": object_start_line,
                "object_end_line": object_end_line,
            }
        )

    for target in reinsert_targets:
        path = str(target["path"])
        if target["target_type"] == "removed_range":
            add_unit(
                "removed_range",
                path,
                str(target["object_kind"]),
                str(target["qualified_name"]),
                int(target["object_start_line"]),
                int(target["object_end_line"]),
            )
        elif target["target_type"] == "removed_object":
            if target["object_kind"] in {"function", "method"}:
                add_unit(
                    "removed_object",
                    path,
                    str(target["object_kind"]),
                    str(target["qualified_name"]),
                    int(target["object_start_line"]),
                    int(target["object_end_line"]),
                )
            elif target["object_kind"] == "class":
                class_start_line = int(target["object_start_line"])
                for contained in target.get("contained_targets", []):
                    add_unit(
                        "removed_object",
                        path,
                        str(contained["object_kind"]),
                        str(contained["qualified_name"]),
                        class_start_line + int(contained["start_line"]) - 1,
                        class_start_line + int(contained["end_line"]) - 1,
                    )
    return units


def _reinsert_artifact_stats(
    reinsert_targets: list[dict[str, Any]],
    implementation_units: list[dict[str, Any]],
) -> dict[str, int]:
    removed_object_targets_count = 0
    removed_range_targets_count = 0
    reinsert_source_lines = 0
    for target in reinsert_targets:
        if target["target_type"] == "removed_object":
            removed_object_targets_count += 1
            reinsert_source_lines += _source_line_count(str(target["source_text"]))
        elif target["target_type"] == "removed_range":
            removed_range_targets_count += 1
            for removed_range in target["removed_ranges"]:
                reinsert_source_lines += _source_line_count(
                    str(removed_range["source_text"])
                )
    return {
        "removed_object_targets_count": removed_object_targets_count,
        "removed_range_targets_count": removed_range_targets_count,
        "implementation_units_count": len(implementation_units),
        "reinsert_source_lines": reinsert_source_lines,
    }


def _source_line_count(source_text: str) -> int:
    return len(source_text.splitlines())


def _related_python_paths(reinsert_targets: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for target in reinsert_targets:
        path = str(target["path"])
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _reinsert_target_paths(reinsert_targets: list[dict[str, Any]]) -> list[str]:
    return _unique_target_paths(reinsert_targets)


def _existing_file_reinsert_paths(reinsert_targets: list[dict[str, Any]]) -> list[str]:
    return _unique_target_paths(reinsert_targets)


def _unique_target_paths(reinsert_targets: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for target in reinsert_targets:
        path = str(target["path"])
        if path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def _task_base_compile_diagnostic(
    work_tree: Path, paths: list[str]
) -> tuple[str, str | None]:
    for path in paths:
        file_path = work_tree / path
        if not file_path.exists():
            continue
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                compile(file_path.read_bytes(), str(file_path), "exec")
        except (SyntaxError, ValueError, UnicodeDecodeError) as exc:
            return "failed", f"{type(exc).__name__}: {exc}"
    return "passed", None


def _decode_source(source: bytes) -> str:
    return source.decode("utf-8", errors="surrogateescape")


def _candidate_pair(
    repo: RepoInput, old_commit: str, new_commit: str, offset: int
) -> dict[str, Any]:
    return {
        "pair_id": f"{repo.repo_key}__{old_commit[:12]}__{new_commit[:12]}",
        "repo_key": repo.repo_key,
        "full_name": repo.full_name,
        "github_repo_id": repo.github_repo_id,
        "clone_url": repo.clone_url,
        "default_branch": repo.default_branch,
        "old_commit": old_commit,
        "new_commit": new_commit,
        "offset": offset,
    }


def _completed_repo_result(
    repo: RepoInput,
    config: CandidatePairsConfig,
    *,
    head_commit: str | None,
    offsets_examined: int,
    accepted_count: int,
    rejected_count: int,
) -> dict[str, Any]:
    return {
        "repo_key": repo.repo_key,
        "full_name": repo.full_name,
        "github_repo_id": repo.github_repo_id,
        "clone_url": repo.clone_url,
        "default_branch": repo.default_branch,
        "cache_dir": _display_path(repo.cache_dir),
        "head_commit": head_commit,
        "status": "completed",
        "status_code": None,
        "offset_ranges": [
            offset_range.model_dump() for offset_range in config.offset_ranges
        ],
        "min_implementation_units": config.min_implementation_units,
        "min_reinsert_source_lines": config.min_reinsert_source_lines,
        "max_implementation_units": config.max_implementation_units,
        "max_reinsert_source_lines": config.max_reinsert_source_lines,
        "offsets_examined": offsets_examined,
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
    }


def _skipped_repo_result(
    repository: dict[str, Any], config: CandidatePairsConfig
) -> dict[str, Any]:
    return {
        "repo_key": None,
        "full_name": repository.get("full_name"),
        "github_repo_id": repository.get("github_repo_id"),
        "clone_url": repository.get("clone_url"),
        "default_branch": repository.get("default_branch"),
        "cache_dir": None,
        "head_commit": None,
        "status": "skipped",
        "status_code": "missing_required_field",
        "offset_ranges": [
            offset_range.model_dump() for offset_range in config.offset_ranges
        ],
        "min_implementation_units": config.min_implementation_units,
        "min_reinsert_source_lines": config.min_reinsert_source_lines,
        "max_implementation_units": config.max_implementation_units,
        "max_reinsert_source_lines": config.max_reinsert_source_lines,
        "offsets_examined": 0,
        "accepted_count": 0,
        "rejected_count": 0,
    }


def _failed_repo_result(
    repo: RepoInput,
    config: CandidatePairsConfig,
    *,
    error: str,
) -> dict[str, Any]:
    return {
        "repo_key": repo.repo_key,
        "full_name": repo.full_name,
        "github_repo_id": repo.github_repo_id,
        "clone_url": repo.clone_url,
        "default_branch": repo.default_branch,
        "cache_dir": _display_path(repo.cache_dir),
        "head_commit": None,
        "status": "failed",
        "status_code": "git_cache_failed",
        "error": error,
        "offset_ranges": [
            offset_range.model_dump() for offset_range in config.offset_ranges
        ],
        "min_implementation_units": config.min_implementation_units,
        "min_reinsert_source_lines": config.min_reinsert_source_lines,
        "max_implementation_units": config.max_implementation_units,
        "max_reinsert_source_lines": config.max_reinsert_source_lines,
        "offsets_examined": 0,
        "accepted_count": 0,
        "rejected_count": 0,
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            if not isinstance(payload, dict):
                raise ValueError(f"JSONL row must be an object: {path}:{line_number}")
            rows.append(payload)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _run_git(
    args: list[str],
    *,
    timeout_seconds: float | None = None,
) -> str:
    return _run_git_process(
        args,
        timeout_seconds=timeout_seconds,
    ).stdout.decode("utf-8", errors="replace")


def _run_git_bytes(args: list[str]) -> bytes:
    return _run_git_process(args).stdout


def _run_git_work_tree(cache_dir: Path, work_tree: Path, args: list[str]) -> str:
    return _run_git_process(
        [f"--git-dir={cache_dir}", f"--work-tree={work_tree}", *args],
        env=_work_tree_git_env(work_tree),
    ).stdout.decode("utf-8", errors="replace")


def _run_git_work_tree_bytes(
    cache_dir: Path, work_tree: Path, args: list[str]
) -> bytes:
    return _run_git_process(
        [f"--git-dir={cache_dir}", f"--work-tree={work_tree}", *args],
        env=_work_tree_git_env(work_tree),
    ).stdout


def _run_git_process(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            ["git", *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        timeout_text = (
            f"{timeout_seconds:g}" if timeout_seconds is not None else "unknown"
        )
        raise RuntimeError(
            f"git command timed out after {timeout_text} seconds: git {' '.join(args)}"
        ) from exc
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(stderr or f"git command failed: git {' '.join(args)}")
    return result


def _work_tree_git_env(work_tree: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(work_tree.parent / f"{work_tree.name}.index")
    return env


def _nul_tokens(output: bytes) -> list[str]:
    return [
        token.decode("utf-8", errors="surrogateescape")
        for token in output.split(b"\0")
        if token
    ]


def _matches_any(value: str, patterns: set[str]) -> bool:
    return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)


def _non_empty_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
