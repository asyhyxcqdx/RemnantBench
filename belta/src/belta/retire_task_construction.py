from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Event, Lock
from typing import Any, Callable, Iterator

from belta.config import load_config

TASK_INSTRUCTION = """This repository is based on the target version, but remnants of obsolete implementations from an older version have been reintroduced. These remnants may be spread across multiple files, functions, and classes, and may involve independent behaviors.

These obsolete remnants may include, but are not limited to:

- branches, functions, classes, variables, or helper logic that are no longer used;
- old logic that duplicates or conflicts with the target-version implementation;
- old code that can still execute even though the target version no longer needs or expects it;
- historical logic that interferes with the target version\u2019s control flow, data flow, or state management;
- obsolete imports, references, variables, or helper code left behind by the old implementation.

Please use the existing source code, call relationships, and available tests to identify and remove these obsolete implementations, bringing the affected behavior into alignment with the target version while preserving unaffected functionality.

Please follow these principles:

1. Use the target-version implementation already present in the repository as the basis for your work. Avoid redesigning or replacing existing implementations.
2. Remove the identified obsolete implementations and clean up related imports, references, variables, and helper code, taking care to preserve any parts still required by the target version.
3. Preserve code and behavior unrelated to these obsolete implementations, and avoid introducing unrelated changes.
4. Keep the final source changes clear and consistent with the surrounding code style and structure.

Repository and environment notes:

1. The project directory is `/workspace/repo`. Do not search other directories for another copy of the project\u2019s source code.
2. External network access is unavailable in the current environment. Use the tools and dependencies already available, and do not attempt to access the network or install or update dependencies.
3. You may inspect and run the tests already present in the repository, and write additional tests as needed to help validate the changes.
4. Temporary diagnostic files may be placed under `/tmp`. When finished, remove temporary files and diagnostic artifacts, leaving only changes relevant to the task.
"""

FULL_VALIDATION_TIMEOUT_ENV = (
    "FEATURE_FACTORY_STAGE2_FULL_VALIDATION_TIMEOUT_SECONDS"
)
FULL_VALIDATION_TIMEOUT_SECONDS = 1800
REPO_BASELINE_PASSED = "passed"
REPO_BASELINE_FAILED = "failed"
REPO_BASELINE_INFRA_ERROR = "infra_error"
PAIR_PATCH_APPLY_FAILURE_MARKER = "BELTA_PAIR_PATCH_APPLY_FAILED"
PAIR_COMPLETED = "completed"
PAIR_CONSTRUCTION_FAILED = "construction_failed"
PAIR_INFRA_ERROR = "infra_error"
PAIR_MATERIAL_FILES = (
    "metadata.json",
    "reinsert_extraction.patch",
    "reinsert_targets.jsonl",
    "implementation_units.jsonl",
    "obsolete_reinsert.patch",
)
ENVIRONMENT_INPUT_FILES = (
    "manifest.json",
    "Dockerfile",
    "run_script.sh",
    "collect_report.json",
    "full_report.json",
    "test_results.jsonl",
)
RETIRE_OUTPUT_DIRECTORIES = frozenset({"checkpoints", "tasks", "results"})
_ACTIVE_VALIDATION_CONTAINERS: set[str] = set()
_ACTIVE_VALIDATION_CONTAINERS_LOCK = Lock()
_ENVIRONMENT_IMAGE_LOCKS: dict[str, Lock] = {}
_ENVIRONMENT_IMAGE_LOCKS_GUARD = Lock()


@dataclass(frozen=True)
class RetireTaskConstructionResult:
    run_dir: Path
    summary: dict[str, Any]


@dataclass(frozen=True)
class ValidationRunResult:
    test_file: str
    target_selector: str
    status: str
    summary: dict[str, int]
    raw_result: dict[str, Any]
    exit_code: int
    infra_error: str | None = None


@dataclass(frozen=True)
class ValidationSuiteResult:
    results: list[ValidationRunResult]
    error: str | None = None


@dataclass(frozen=True)
class ValidationTimeouts:
    full_validation_timeout_seconds: int = FULL_VALIDATION_TIMEOUT_SECONDS


@dataclass(frozen=True)
class PairConstructionOutcome:
    status: str
    selection_result: str | None = None
    test_summary: dict[str, int] | None = None
    error: str | None = None


class PairConstructionError(RuntimeError):
    pass


class PairInfrastructureError(RuntimeError):
    pass


class ValidationSuiteTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class PairExecutionResult:
    pair_result: dict[str, Any]


@dataclass(frozen=True)
class RepoConstructionOutcome:
    repo_result: dict[str, Any]
    pair_results: list[dict[str, Any]]


@dataclass(frozen=True)
class RepoBaselineWork:
    repo_key: str
    environment_dir: Path
    validation_tests: list[dict[str, Any]]
    validation_timeouts: ValidationTimeouts
    input_fingerprint: str
    test_results_path: Path
    docker_tag: str | None = None


@dataclass(frozen=True)
class RepoBaselineAttemptResult:
    repo_key: str
    status: str
    docker_tag: str | None
    input_fingerprint: str
    test_summary: dict[str, int]
    error: str | None


def run_retire_task_construction(run_dir: Path) -> RetireTaskConstructionResult:
    run_dir = run_dir.resolve()
    config_path = run_dir / "config.yaml"
    accepted_path = run_dir / "candidate_pairs" / "accepted_candidates.jsonl"
    environments_dir = run_dir / "environments"
    if not config_path.exists():
        raise FileNotFoundError(f"run config not found: {config_path}")
    if not accepted_path.exists():
        raise FileNotFoundError(f"accepted candidates not found: {accepted_path}")
    if not environments_dir.exists():
        raise FileNotFoundError(f"environments directory not found: {environments_dir}")

    config = load_config(config_path)
    validation_timeouts = _validation_timeouts_from_environment()
    max_concurrent_repo_baselines = _configured_max_concurrent_repo_baselines(config)
    max_concurrent_pairs = _configured_max_concurrent_pairs(config)

    input_rows = _read_jsonl(accepted_path)
    eligible_rows = _candidates_with_completed_environments(
        environments_dir,
        input_rows,
    )
    accepted_rows = _deduplicate_candidates_by_patch(run_dir, eligible_rows)
    grouped_rows = _group_by_repo(accepted_rows)

    output_root = run_dir / "retire_task_construction"
    _validate_retire_output_layout(output_root)
    checkpoints_root = output_root / "checkpoints"
    pair_checkpoints_dir = checkpoints_root / "pairs"
    baseline_checkpoints_dir = checkpoints_root / "baselines"
    tasks_dir = output_root / "tasks"
    results_dir = output_root / "results"
    baseline_test_results_dir = results_dir / "baseline_test_results"
    pair_test_results_dir = results_dir / "pair_test_results"
    pair_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    baseline_checkpoints_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)
    baseline_test_results_dir.mkdir(parents=True, exist_ok=True)
    pair_test_results_dir.mkdir(parents=True, exist_ok=True)

    accepted_pair_ids = {str(row["pair_id"]) for row in accepted_rows}
    _cleanup_stale_json_checkpoints(pair_checkpoints_dir, accepted_pair_ids)
    _cleanup_stale_tasks(tasks_dir, accepted_pair_ids)

    stop_event = Event()
    try:
        baseline_docker_tags = _run_repo_baseline_phases(
            environments_dir,
            baseline_checkpoints_dir,
            baseline_test_results_dir,
            grouped_rows,
            max_concurrent_repo_baselines=max_concurrent_repo_baselines,
            validation_timeouts=validation_timeouts,
            stop_event=stop_event,
        )
    except BaseException:
        stop_event.set()
        _stop_active_validation_containers()
        raise

    validation_slots = BoundedSemaphore(max_concurrent_pairs)
    repo_outcomes_by_key: dict[str, RepoConstructionOutcome] = {}
    pair_executor = ThreadPoolExecutor(max_workers=max_concurrent_pairs)
    repo_executor = ThreadPoolExecutor(
        max_workers=max(1, min(max_concurrent_pairs, len(grouped_rows)))
    )
    repo_futures: dict[Future[RepoConstructionOutcome], str] = {}
    try:
        for repo_key in sorted(grouped_rows):
            repo_futures[
                repo_executor.submit(
                    _process_repo,
                    run_dir,
                    environments_dir,
                    pair_checkpoints_dir,
                    baseline_checkpoints_dir,
                    baseline_test_results_dir,
                    tasks_dir,
                    pair_test_results_dir,
                    repo_key,
                    grouped_rows[repo_key],
                    pair_executor,
                    validation_slots,
                    stop_event,
                    baseline_docker_tags.get(repo_key),
                    validation_timeouts,
                )
            ] = repo_key
        for future in as_completed(repo_futures):
            repo_key = repo_futures[future]
            repo_outcomes_by_key[repo_key] = future.result()
    except BaseException:
        stop_event.set()
        _stop_active_validation_containers()
        for future in repo_futures:
            future.cancel()
        pair_executor.shutdown(wait=False, cancel_futures=True)
        repo_executor.shutdown(wait=True, cancel_futures=True)
        pair_executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        repo_executor.shutdown(wait=True)
        pair_executor.shutdown(wait=True)

    repo_results = [
        repo_outcomes_by_key[repo_key].repo_result for repo_key in sorted(grouped_rows)
    ]
    pair_checkpoints_by_id = {
        str(row["pair_id"]): row
        for repo_key in sorted(grouped_rows)
        for row in repo_outcomes_by_key[repo_key].pair_results
    }

    current_repo_keys = set(grouped_rows)
    main_task_ids = {
        pair_id
        for pair_id, row in pair_checkpoints_by_id.items()
        if row.get("status") == PAIR_COMPLETED
        and row.get("selection_result") == "main_task"
    }
    _cleanup_stale_json_checkpoints(pair_checkpoints_dir, accepted_pair_ids)
    for path in baseline_checkpoints_dir.glob("*.json"):
        if path.stem not in current_repo_keys:
            path.unlink()
    _cleanup_stale_jsonl_results(baseline_test_results_dir, current_repo_keys)
    _cleanup_stale_jsonl_results(pair_test_results_dir, accepted_pair_ids)
    _cleanup_stale_tasks(tasks_dir, main_task_ids)

    ordered_pair_results = [
        _pair_report_row(pair_checkpoints_by_id[str(row["pair_id"])])
        for row in accepted_rows
        if str(row["pair_id"]) in pair_checkpoints_by_id
    ]
    summary = _retire_summary(repo_results, ordered_pair_results)
    _write_jsonl_atomic(results_dir / "pairs.jsonl", ordered_pair_results)
    _write_jsonl_atomic(results_dir / "repos.jsonl", repo_results)
    _write_json_atomic(results_dir / "summary.json", summary)
    return RetireTaskConstructionResult(
        run_dir=run_dir,
        summary=summary,
    )


def _configured_max_concurrent_pairs(config: Any) -> int:
    retire_config = getattr(config, "retire_task_construction", None)
    value = getattr(retire_config, "max_concurrent_pairs", None)
    return value if isinstance(value, int) and value >= 1 else 32


def _validation_timeouts_from_environment() -> ValidationTimeouts:
    return ValidationTimeouts(
        full_validation_timeout_seconds=_positive_timeout_from_environment(
            FULL_VALIDATION_TIMEOUT_ENV,
            FULL_VALIDATION_TIMEOUT_SECONDS,
        ),
    )


def _positive_timeout_from_environment(name: str, default: int) -> int:
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _configured_max_concurrent_repo_baselines(config: Any) -> int:
    retire_config = getattr(config, "retire_task_construction", None)
    value = getattr(retire_config, "max_concurrent_repo_baselines", None)
    return value if isinstance(value, int) and value >= 1 else 32


def _run_repo_baseline_phases(
    environments_dir: Path,
    baseline_checkpoints_dir: Path,
    baseline_test_results_dir: Path,
    grouped_rows: dict[str, list[dict[str, Any]]],
    *,
    max_concurrent_repo_baselines: int,
    validation_timeouts: ValidationTimeouts,
    stop_event: Event,
) -> dict[str, str]:
    initial_work: list[RepoBaselineWork] = []
    for repo_key in sorted(grouped_rows):
        baseline_checkpoint_path = baseline_checkpoints_dir / f"{repo_key}.json"
        baseline_test_results_path = baseline_test_results_dir / f"{repo_key}.jsonl"
        environment_dir = environments_dir / repo_key
        environment_fingerprint = (
            _environment_input_fingerprint(environment_dir, validation_timeouts)
            if _load_environment(environment_dir) is not None
            else None
        )
        baseline_status = _read_repo_baseline_status(
            baseline_checkpoint_path,
            input_fingerprint=environment_fingerprint,
            test_results_path=baseline_test_results_path,
        )
        if baseline_status in {REPO_BASELINE_PASSED, REPO_BASELINE_FAILED}:
            continue

        if environment_fingerprint is None:
            baseline_test_results_path.unlink(missing_ok=True)
            _write_json_atomic(
                baseline_checkpoint_path,
                _baseline_checkpoint(
                    repo_key,
                    status=REPO_BASELINE_INFRA_ERROR,
                    input_fingerprint=None,
                    test_summary=_baseline_test_summary([], []),
                    error="completed environment materials are unavailable",
                ),
            )
            continue
        validation_tests = _validation_test_universe(environment_dir)
        if not validation_tests:
            baseline_test_results_path.unlink(missing_ok=True)
            _write_json_atomic(
                baseline_checkpoint_path,
                _baseline_checkpoint(
                    repo_key,
                    status=REPO_BASELINE_INFRA_ERROR,
                    input_fingerprint=environment_fingerprint,
                    test_summary=_baseline_test_summary([], []),
                    error="validation test universe is empty",
                ),
            )
            continue
        initial_work.append(
            RepoBaselineWork(
                repo_key=repo_key,
                environment_dir=environment_dir,
                validation_tests=validation_tests,
                validation_timeouts=validation_timeouts,
                input_fingerprint=environment_fingerprint,
                test_results_path=baseline_test_results_path,
            )
        )

    docker_tags: dict[str, str] = {}

    def record_initial_result(result: RepoBaselineAttemptResult) -> None:
        if result.docker_tag is not None:
            docker_tags[result.repo_key] = result.docker_tag
        _write_json_atomic(
            baseline_checkpoints_dir / f"{result.repo_key}.json",
            _baseline_checkpoint(
                result.repo_key,
                status=result.status,
                input_fingerprint=result.input_fingerprint,
                test_summary=result.test_summary,
                error=result.error,
            ),
        )

    _run_repo_baseline_batch(
        initial_work,
        max_workers=max_concurrent_repo_baselines,
        stop_event=stop_event,
        on_result=record_initial_result,
    )
    return docker_tags


def _run_repo_baseline_batch(
    work_items: list[RepoBaselineWork],
    *,
    max_workers: int,
    stop_event: Event,
    on_result: Callable[[RepoBaselineAttemptResult], None],
) -> None:
    if not work_items:
        return

    executor = ThreadPoolExecutor(max_workers=min(max_workers, len(work_items)))
    futures = {
        executor.submit(_run_repo_baseline_attempt, work, stop_event): work.repo_key
        for work in work_items
    }
    try:
        for future in as_completed(futures):
            on_result(future.result())
    except BaseException:
        stop_event.set()
        _stop_active_validation_containers()
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def _run_repo_baseline_attempt(
    work: RepoBaselineWork,
    stop_event: Event,
) -> RepoBaselineAttemptResult:
    if stop_event.is_set():
        raise InterruptedError("Retire Task Construction interrupted")

    work.test_results_path.unlink(missing_ok=True)
    docker_tag = work.docker_tag
    try:
        if docker_tag is None:
            docker_tag = _build_environment_image(
                work.environment_dir,
                work.repo_key,
            )
        suite_result = _run_validation_tests(
            docker_tag,
            work.environment_dir,
            work.validation_tests,
            test_results_path=work.test_results_path,
            full_validation_timeout_seconds=(
                work.validation_timeouts.full_validation_timeout_seconds
            ),
            stop_event=stop_event,
        )
        _ensure_validation_results_file(work.test_results_path, suite_result.results)
    except RuntimeError as exc:
        return RepoBaselineAttemptResult(
            repo_key=work.repo_key,
            status=REPO_BASELINE_INFRA_ERROR,
            docker_tag=docker_tag,
            input_fingerprint=work.input_fingerprint,
            test_summary=_baseline_test_summary(work.validation_tests, []),
            error=str(exc),
        )
    return RepoBaselineAttemptResult(
        repo_key=work.repo_key,
        status=_new_commit_validation_status(work.validation_tests, suite_result),
        docker_tag=docker_tag,
        input_fingerprint=work.input_fingerprint,
        test_summary=_baseline_test_summary(
            work.validation_tests,
            suite_result.results,
        ),
        error=_validation_suite_error(
            work.validation_tests,
            suite_result,
        ),
    )


def _process_repo(
    run_dir: Path,
    environments_dir: Path,
    pair_checkpoints_dir: Path,
    baseline_checkpoints_dir: Path,
    baseline_test_results_dir: Path,
    tasks_dir: Path,
    pair_test_results_dir: Path,
    repo_key: str,
    repo_rows: list[dict[str, Any]],
    pair_executor: ThreadPoolExecutor,
    validation_slots: BoundedSemaphore,
    stop_event: Event,
    baseline_docker_tag: str | None,
    validation_timeouts: ValidationTimeouts,
) -> RepoConstructionOutcome:
    if stop_event.is_set():
        raise InterruptedError("Retire Task Construction interrupted")
    environment_dir = environments_dir / repo_key
    baseline_checkpoint_path = baseline_checkpoints_dir / f"{repo_key}.json"
    environment_fingerprint = (
        _environment_input_fingerprint(environment_dir, validation_timeouts)
        if _load_environment(environment_dir) is not None
        else None
    )
    baseline_status = _read_repo_baseline_status(
        baseline_checkpoint_path,
        input_fingerprint=environment_fingerprint,
        test_results_path=baseline_test_results_dir / f"{repo_key}.jsonl",
    )
    if baseline_status != REPO_BASELINE_PASSED:
        effective_status = (
            baseline_status
            if baseline_status in {REPO_BASELINE_FAILED, REPO_BASELINE_INFRA_ERROR}
            else REPO_BASELINE_INFRA_ERROR
        )
        if baseline_status != effective_status:
            _write_json_atomic(
                baseline_checkpoint_path,
                _baseline_checkpoint(
                    repo_key,
                    status=effective_status,
                    input_fingerprint=environment_fingerprint,
                    test_summary=_baseline_test_summary([], []),
                    error="baseline checkpoint is unavailable or invalid",
                ),
            )
        _discard_repo_pair_outputs(
            repo_rows, pair_checkpoints_dir, tasks_dir, pair_test_results_dir
        )
        return RepoConstructionOutcome(
            _repo_report_row(repo_key, effective_status, []),
            [],
        )

    environment = _load_environment(environment_dir)
    if environment is None:
        _write_json_atomic(
            baseline_checkpoint_path,
            _baseline_checkpoint(
                repo_key,
                status=REPO_BASELINE_INFRA_ERROR,
                input_fingerprint=None,
                test_summary=_baseline_test_summary([], []),
                error="completed environment materials are unavailable",
            ),
        )
        _discard_repo_pair_outputs(
            repo_rows, pair_checkpoints_dir, tasks_dir, pair_test_results_dir
        )
        return RepoConstructionOutcome(
            _repo_report_row(repo_key, REPO_BASELINE_INFRA_ERROR, []),
            [],
        )
    validation_tests = _validation_test_universe(environment_dir)
    if not validation_tests:
        _write_json_atomic(
            baseline_checkpoint_path,
            _baseline_checkpoint(
                repo_key,
                status=REPO_BASELINE_INFRA_ERROR,
                input_fingerprint=environment_fingerprint,
                test_summary=_baseline_test_summary([], []),
                error="validation test universe is empty",
            ),
        )
        _discard_repo_pair_outputs(
            repo_rows, pair_checkpoints_dir, tasks_dir, pair_test_results_dir
        )
        return RepoConstructionOutcome(
            _repo_report_row(repo_key, REPO_BASELINE_INFRA_ERROR, []),
            [],
        )

    docker_tag = baseline_docker_tag
    environment_fingerprint = _environment_input_fingerprint(
        environment_dir,
        validation_timeouts,
    )
    pairs_to_process: list[tuple[dict[str, Any], Path, str]] = []
    pair_results_by_id: dict[str, dict[str, Any]] = {}

    for candidate in repo_rows:
        pair_id = str(candidate["pair_id"])
        pair_output_dir = run_dir / "candidate_pairs" / "pair_outputs" / pair_id
        checkpoint_path = pair_checkpoints_dir / f"{pair_id}.json"
        task_dir = tasks_dir / pair_id
        if not _pair_materials_ready(pair_output_dir):
            shutil.rmtree(task_dir, ignore_errors=True)
            (pair_test_results_dir / f"{pair_id}.jsonl").unlink(missing_ok=True)
            pair_result = _construction_failed_pair_result(
                candidate,
                input_fingerprint=None,
            )
            _write_json_atomic(checkpoint_path, pair_result)
            pair_results_by_id[pair_id] = pair_result
            continue

        input_fingerprint = _pair_input_fingerprint(
            candidate,
            pair_output_dir,
            environment_fingerprint,
        )
        reusable_result = _reusable_pair_result(
            checkpoint_path,
            candidate,
            input_fingerprint,
            task_dir,
            pair_test_results_dir / f"{pair_id}.jsonl",
        )
        if reusable_result is None:
            checkpoint_path.unlink(missing_ok=True)
            shutil.rmtree(task_dir, ignore_errors=True)
            pairs_to_process.append((candidate, pair_output_dir, input_fingerprint))
            continue

        pair_results_by_id[pair_id] = reusable_result

    if pairs_to_process and docker_tag is None:
        try:
            with validation_slots:
                docker_tag = _build_environment_image(environment_dir, repo_key)
        except RuntimeError:
            _write_json_atomic(
                baseline_checkpoint_path,
                _baseline_checkpoint(
                    repo_key,
                    status=REPO_BASELINE_INFRA_ERROR,
                    input_fingerprint=environment_fingerprint,
                    test_summary=_baseline_test_summary(validation_tests, []),
                    error="failed to build validation image",
                ),
            )
            _discard_repo_pair_outputs(
                repo_rows, pair_checkpoints_dir, tasks_dir, pair_test_results_dir
            )
            return RepoConstructionOutcome(
                _repo_report_row(repo_key, REPO_BASELINE_INFRA_ERROR, []),
                [],
            )

    pair_futures: dict[Future[PairExecutionResult], str] = {}
    for candidate, pair_output_dir, input_fingerprint in pairs_to_process:
        if stop_event.is_set():
            raise InterruptedError("Retire Task Construction interrupted")
        if docker_tag is None:
            raise RuntimeError("Docker image is unavailable for pair execution")
        pair_id = str(candidate["pair_id"])
        pair_futures[
            pair_executor.submit(
                _execute_pair,
                run_dir,
                tasks_dir,
                pair_checkpoints_dir,
                candidate,
                pair_output_dir,
                environment_dir,
                environment,
                validation_tests,
                docker_tag,
                input_fingerprint,
                pair_test_results_dir / f"{pair_id}.jsonl",
                validation_slots,
                stop_event,
                validation_timeouts,
            )
        ] = pair_id

    for future in as_completed(pair_futures):
        pair_id = pair_futures[future]
        execution = future.result()
        pair_results_by_id[pair_id] = execution.pair_result

    ordered_results = _ordered_repo_pair_results(repo_rows, pair_results_by_id)
    return RepoConstructionOutcome(
        _repo_report_row(repo_key, REPO_BASELINE_PASSED, ordered_results),
        ordered_results,
    )


def _execute_pair(
    run_dir: Path,
    tasks_dir: Path,
    pair_checkpoints_dir: Path,
    candidate: dict[str, Any],
    pair_output_dir: Path,
    environment_dir: Path,
    environment: dict[str, Any],
    validation_tests: list[dict[str, Any]],
    docker_tag: str,
    input_fingerprint: str,
    test_results_path: Path,
    validation_slots: BoundedSemaphore,
    stop_event: Event,
    validation_timeouts: ValidationTimeouts,
) -> PairExecutionResult:
    if stop_event.is_set():
        raise InterruptedError("Retire Task Construction interrupted")
    pair_id = str(candidate["pair_id"])
    checkpoint_path = pair_checkpoints_dir / f"{pair_id}.json"
    task_dir = tasks_dir / pair_id
    try:
        with validation_slots:
            outcome = _construct_pair(
                run_dir,
                tasks_dir,
                candidate,
                pair_output_dir,
                environment_dir,
                environment,
                validation_tests,
                docker_tag,
                test_results_path=test_results_path,
                validation_timeouts=validation_timeouts,
                stop_event=stop_event,
            )
    except RuntimeError as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        outcome = PairConstructionOutcome(
            status=PAIR_CONSTRUCTION_FAILED,
            error=str(exc),
        )
    if stop_event.is_set():
        raise InterruptedError("Retire Task Construction interrupted")
    pair_result = _pair_result_from_outcome(candidate, input_fingerprint, outcome)
    _write_json_atomic(checkpoint_path, pair_result)
    return PairExecutionResult(pair_result)


def _ordered_repo_pair_results(
    repo_rows: list[dict[str, Any]],
    rows_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        rows_by_id[str(row["pair_id"])]
        for row in repo_rows
        if str(row["pair_id"]) in rows_by_id
    ]


def _construct_pair(
    run_dir: Path,
    tasks_dir: Path,
    candidate: dict[str, Any],
    pair_output_dir: Path,
    environment_dir: Path,
    environment: dict[str, Any],
    validation_tests: list[dict[str, Any]],
    docker_tag: str,
    *,
    test_results_path: Path | None = None,
    validation_timeouts: ValidationTimeouts | None = None,
    stop_event: Event | None = None,
) -> PairConstructionOutcome:
    validation_timeouts = validation_timeouts or ValidationTimeouts()
    pair_id = str(candidate["pair_id"])
    task_dir = tasks_dir / pair_id
    if task_dir.exists():
        shutil.rmtree(task_dir)
    try:
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Retire Task Construction interrupted")
        suite_result = _run_validation_tests(
            docker_tag,
            environment_dir,
            validation_tests,
            patch_path=pair_output_dir / "obsolete_reinsert.patch",
            test_results_path=test_results_path,
            full_validation_timeout_seconds=(
                validation_timeouts.full_validation_timeout_seconds
            ),
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            raise InterruptedError("Retire Task Construction interrupted")
    except PairConstructionError as exc:
        return PairConstructionOutcome(
            status=PAIR_CONSTRUCTION_FAILED,
            test_summary=_test_summary(validation_tests, []),
            error=str(exc),
        )
    except PairInfrastructureError as exc:
        return PairConstructionOutcome(
            status=PAIR_INFRA_ERROR,
            test_summary=_test_summary(validation_tests, []),
            error=str(exc),
        )

    task_base_results = suite_result.results
    validation_error = _validation_suite_error(validation_tests, suite_result)
    if validation_error is not None:
        return PairConstructionOutcome(
            status=PAIR_INFRA_ERROR,
            test_summary=_test_summary(validation_tests, task_base_results),
            error=validation_error,
        )
    f2p_results = [result for result in task_base_results if result.status == "failed"]
    if not f2p_results:
        return PairConstructionOutcome(
            status=PAIR_COMPLETED,
            selection_result="no_f2p",
            test_summary=_test_summary(validation_tests, task_base_results),
        )

    with tempfile.TemporaryDirectory(
        prefix="belta-retire-task-", ignore_cleanup_errors=True
    ) as tmp:
        tmp_root = Path(tmp)
        task_base_full = tmp_root / "task_base_full"
        cache_dir = _repo_cache_dir(Path.cwd(), candidate)
        _checkout_commit(cache_dir, str(candidate["new_commit"]), task_base_full)
        _apply_patch(task_base_full, pair_output_dir / "obsolete_reinsert.patch")

        task_dir.mkdir(parents=True, exist_ok=True)
        private_materials_dir = task_dir / "private_materials"
        eval_assets_dir = task_dir / "eval_assets"

        _copy_private_materials(pair_output_dir, private_materials_dir)
        gold_cleanup_patch = _gold_cleanup_patch(
            cache_dir,
            task_base_full,
            str(candidate["new_commit"]),
        )
        (private_materials_dir / "gold_cleanup.patch").write_bytes(gold_cleanup_patch)

        snapshot_rows = _snapshot_validation_tests(
            task_base_full, eval_assets_dir, validation_tests
        )

        _write_text(task_dir / "task_instruction.md", TASK_INSTRUCTION)

        _copy_environment_assets(environment_dir, eval_assets_dir)
        _write_jsonl(eval_assets_dir / "validation_test_snapshots.jsonl", snapshot_rows)

        test_summary = _test_summary(validation_tests, task_base_results)

        manifest = _task_manifest(
            run_dir,
            task_dir,
            candidate,
            environment,
            validation_tests,
            test_summary,
            validation_timeouts,
        )
        _write_json(task_dir / "manifest.json", manifest)
        return PairConstructionOutcome(
            status=PAIR_COMPLETED,
            selection_result="main_task",
            test_summary=test_summary,
        )


def _group_by_repo(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        repo_key = str(row.get("repo_key") or "")
        if repo_key:
            grouped.setdefault(repo_key, []).append(row)
    for repo_rows in grouped.values():
        repo_rows.sort(key=lambda row: int(row.get("offset", 0) or 0))
    return grouped


def _candidates_with_completed_environments(
    environments_dir: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    eligible_repo_keys: dict[str, bool] = {}
    result: list[dict[str, Any]] = []
    for row in rows:
        repo_key = str(row.get("repo_key") or "")
        if not repo_key:
            continue
        eligible = eligible_repo_keys.get(repo_key)
        if eligible is None:
            eligible = _load_environment(environments_dir / repo_key) is not None
            eligible_repo_keys[repo_key] = eligible
        if eligible:
            result.append(row)
    return result


def _deduplicate_candidates_by_patch(
    run_dir: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    best_index_by_key: dict[tuple[str, str, str, int], int] = {}
    for index, row in enumerate(rows):
        pair_id = str(row.get("pair_id") or "")
        pair_output_dir = run_dir / "candidate_pairs" / "pair_outputs" / pair_id
        patch_path = pair_output_dir / "obsolete_reinsert.patch"
        try:
            patch_bytes = patch_path.read_bytes()
        except OSError:
            key = ("missing", pair_id, "", index)
        else:
            key = (
                str(row.get("repo_key") or ""),
                str(row.get("new_commit") or ""),
                hashlib.sha256(patch_bytes).hexdigest(),
                len(patch_bytes),
            )

        previous_index = best_index_by_key.get(key)
        if previous_index is None:
            best_index_by_key[key] = index
            continue
        previous = rows[previous_index]
        current_order = (int(row.get("offset", 0) or 0), pair_id)
        previous_order = (
            int(previous.get("offset", 0) or 0),
            str(previous.get("pair_id") or ""),
        )
        if current_order < previous_order:
            best_index_by_key[key] = index

    selected_indices = set(best_index_by_key.values())
    return [row for index, row in enumerate(rows) if index in selected_indices]


def _environment_input_fingerprint(
    environment_dir: Path,
    validation_timeouts: ValidationTimeouts | None = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(TASK_INSTRUCTION.encode("utf-8"))
    for filename in ENVIRONMENT_INPUT_FILES:
        _update_fingerprint_with_file(digest, filename, environment_dir / filename)
    if validation_timeouts is not None:
        digest.update(
            json.dumps(
                {
                    "full_validation_timeout_seconds": (
                        validation_timeouts.full_validation_timeout_seconds
                    ),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("ascii")
        )
    return digest.hexdigest()


def _pair_input_fingerprint(
    candidate: dict[str, Any],
    pair_output_dir: Path,
    environment_fingerprint: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(environment_fingerprint.encode("ascii"))
    digest.update(
        json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    for filename in PAIR_MATERIAL_FILES:
        _update_fingerprint_with_file(digest, filename, pair_output_dir / filename)
    return digest.hexdigest()


def _update_fingerprint_with_file(digest: Any, label: str, path: Path) -> None:
    content = path.read_bytes()
    digest.update(label.encode("utf-8"))
    digest.update(b"\0")
    digest.update(str(len(content)).encode("ascii"))
    digest.update(b"\0")
    digest.update(content)


def _reusable_pair_result(
    checkpoint_path: Path,
    candidate: dict[str, Any],
    input_fingerprint: str,
    task_dir: Path,
    test_results_path: Path,
) -> dict[str, Any] | None:
    if not checkpoint_path.is_file():
        return None
    try:
        result = _read_json(checkpoint_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if result.get("status") != PAIR_COMPLETED:
        return None
    for key in ("pair_id", "repo_key", "old_commit", "new_commit"):
        if str(result.get(key) or "") != str(candidate.get(key) or ""):
            return None
    if result.get("input_fingerprint") != input_fingerprint:
        return None
    try:
        test_summary = _required_test_summary(result)
    except (KeyError, TypeError, ValueError):
        return None
    if not _completed_test_results_ready(test_results_path, test_summary):
        return None
    selection_result = result.get("selection_result")
    if selection_result == "no_f2p":
        return result
    if selection_result == "main_task" and _task_ready(task_dir):
        return result
    return None


def _required_test_summary(result: dict[str, Any]) -> dict[str, int]:
    summary = result.get("test_summary")
    if not isinstance(summary, dict):
        raise ValueError("completed pair checkpoint is missing test_summary")
    required = (
        "new_commit_total_files",
        "new_commit_passed_files",
        "task_base_total_files",
        "task_base_passed_files",
        "task_base_failed_files",
        "task_base_error_files",
    )
    return {key: int(summary[key]) for key in required}


def _completed_test_results_ready(
    path: Path,
    test_summary: dict[str, int],
) -> bool:
    statuses = _completed_validation_result_statuses(path)
    if statuses is None:
        return False
    if len(statuses) != test_summary["task_base_total_files"]:
        return False
    return (
        statuses.count("passed") == test_summary["task_base_passed_files"]
        and statuses.count("failed") == test_summary["task_base_failed_files"]
        and statuses.count("error") == 0
        and test_summary["task_base_error_files"] == 0
    )


def _completed_validation_result_statuses(path: Path) -> list[str] | None:
    try:
        rows = _read_jsonl(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    required_fields = {
        "test_file",
        "target_selector",
        "status",
        "total_tests",
        "passed_tests",
        "failed_tests",
        "error_tests",
        "skipped_tests",
        "exit_code",
        "raw_result",
    }
    statuses: list[str] = []
    for row in rows:
        if not required_fields.issubset(row):
            return None
        if not isinstance(row["test_file"], str) or not row["test_file"]:
            return None
        if not isinstance(row["target_selector"], str) or not row["target_selector"]:
            return None
        status = str(row["status"])
        if status not in {"passed", "failed"}:
            return None
        if not isinstance(row["exit_code"], int) or not isinstance(
            row["raw_result"], dict
        ):
            return None
        if _runner_payload_error(row["raw_result"]) is not None:
            return None
        raw_status = str(row["raw_result"]["status"]).strip()
        raw_summary = _normalize_summary(row["raw_result"]["summary"])
        outer_counts = {
            "collected": row["total_tests"],
            "passed": row["passed_tests"],
            "failed": row["failed_tests"],
            "errors": row["error_tests"],
            "skipped": row["skipped_tests"],
        }
        if any(
            not isinstance(value, int) or value < 0
            for value in outer_counts.values()
        ):
            return None
        if status != raw_status or outer_counts != raw_summary:
            return None
        statuses.append(status)
    return statuses


def _task_ready(task_dir: Path) -> bool:
    required_files = (
        "manifest.json",
        "task_instruction.md",
        "eval_assets/Dockerfile",
        "eval_assets/run_script.sh",
        "eval_assets/validation_test_snapshots.jsonl",
        "private_materials/pair_metadata.json",
        "private_materials/reinsert_extraction.patch",
        "private_materials/reinsert_targets.jsonl",
        "private_materials/implementation_units.jsonl",
        "private_materials/obsolete_reinsert.patch",
        "private_materials/gold_cleanup.patch",
    )
    if any(not (task_dir / rel_path).is_file() for rel_path in required_files):
        return False
    snapshots_dir = task_dir / "eval_assets" / "validation_test_snapshots"
    if not snapshots_dir.is_dir():
        return False
    try:
        snapshot_rows = _read_jsonl(
            task_dir / "eval_assets" / "validation_test_snapshots.jsonl"
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return all(
        isinstance(row.get("storage_path"), str)
        and (task_dir / "eval_assets" / str(row["storage_path"])).is_file()
        for row in snapshot_rows
    )


def _pair_result_from_outcome(
    candidate: dict[str, Any],
    input_fingerprint: str,
    outcome: PairConstructionOutcome,
) -> dict[str, Any]:
    if outcome.status not in {
        PAIR_COMPLETED,
        PAIR_CONSTRUCTION_FAILED,
        PAIR_INFRA_ERROR,
    }:
        raise ValueError(f"invalid pair outcome status: {outcome.status}")
    row = _pair_result_base(candidate, input_fingerprint)
    row["status"] = outcome.status
    row["test_summary"] = outcome.test_summary
    row["error"] = outcome.error
    if outcome.status != PAIR_COMPLETED:
        if outcome.error is None:
            raise ValueError(f"{outcome.status} pair outcome is missing error")
        return row
    if outcome.selection_result not in {"main_task", "no_f2p"}:
        raise ValueError("completed pair outcome is missing selection_result")
    if outcome.test_summary is None:
        raise ValueError("completed pair outcome is missing test_summary")
    if outcome.error is not None:
        raise ValueError("completed pair outcome must not contain an error")
    row["selection_result"] = outcome.selection_result
    return row


def _construction_failed_pair_result(
    candidate: dict[str, Any],
    *,
    input_fingerprint: str | None,
) -> dict[str, Any]:
    row = _pair_result_base(candidate, input_fingerprint)
    row["status"] = PAIR_CONSTRUCTION_FAILED
    row["test_summary"] = None
    row["error"] = "required Pair materials are missing"
    return row


def _pair_result_base(
    candidate: dict[str, Any], input_fingerprint: str | None
) -> dict[str, Any]:
    row = {
        "pair_id": candidate["pair_id"],
        "repo_key": candidate["repo_key"],
        "full_name": candidate.get("full_name"),
        "old_commit": candidate["old_commit"],
        "new_commit": candidate["new_commit"],
        "offset": candidate.get("offset"),
    }
    if input_fingerprint is not None:
        row["input_fingerprint"] = input_fingerprint
    return row


def _read_repo_baseline_status(
    path: Path,
    *,
    input_fingerprint: str | None,
    test_results_path: Path,
) -> str | None:
    if not path.is_file():
        return None
    try:
        checkpoint = _read_json(path)
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if checkpoint.get("input_fingerprint") != input_fingerprint:
        return None
    status = str(checkpoint.get("status") or "")
    if status in {
        REPO_BASELINE_PASSED,
        REPO_BASELINE_FAILED,
    }:
        summary = checkpoint.get("test_summary")
        if not isinstance(summary, dict):
            return None
        if _baseline_test_results_ready(test_results_path, summary, status=status):
            return status
        return None
    if status == REPO_BASELINE_INFRA_ERROR:
        return status
    return None


def _baseline_test_results_ready(
    path: Path,
    summary: dict[str, Any],
    *,
    status: str,
) -> bool:
    try:
        counts = {
            "total": int(summary["new_commit_total_files"]),
            "passed": int(summary["new_commit_passed_files"]),
            "failed": int(summary["new_commit_failed_files"]),
            "error": int(summary["new_commit_error_files"]),
        }
    except (KeyError, TypeError, ValueError):
        return False
    if min(counts.values()) < 0 or counts["total"] != sum(
        counts[key] for key in ("passed", "failed", "error")
    ):
        return False
    if status == REPO_BASELINE_PASSED and (
        counts["passed"] != counts["total"]
        or counts["failed"] != 0
        or counts["error"] != 0
    ):
        return False
    if status == REPO_BASELINE_FAILED and (
        counts["failed"] == 0 or counts["error"] != 0
    ):
        return False
    statuses = _completed_validation_result_statuses(path)
    return statuses is not None and (
        len(statuses) == counts["total"]
        and statuses.count("passed") == counts["passed"]
        and statuses.count("failed") == counts["failed"]
        and statuses.count("error") == counts["error"]
    )


def _baseline_checkpoint(
    repo_key: str,
    *,
    status: str,
    input_fingerprint: str | None,
    test_summary: dict[str, int],
    error: str | None,
) -> dict[str, Any]:
    if status not in {
        REPO_BASELINE_PASSED,
        REPO_BASELINE_FAILED,
        REPO_BASELINE_INFRA_ERROR,
    }:
        raise ValueError(f"invalid baseline status: {status}")
    return {
        "repo_key": repo_key,
        "status": status,
        "input_fingerprint": input_fingerprint,
        "test_summary": test_summary,
        "error": error,
    }


def _new_commit_validation_status(
    validation_tests: list[dict[str, Any]],
    suite_result: ValidationSuiteResult,
) -> str:
    results = suite_result.results
    if suite_result.error is not None:
        return REPO_BASELINE_INFRA_ERROR
    expected_paths = [str(row["test_file"]) for row in validation_tests]
    results_by_path = {result.test_file: result for result in results}
    if len(results) != len(expected_paths):
        return REPO_BASELINE_INFRA_ERROR
    ordered_results: list[ValidationRunResult] = []
    for path in expected_paths:
        result = results_by_path.get(path)
        if (
            result is None
            or result.infra_error is not None
            or result.status == "error"
        ):
            return REPO_BASELINE_INFRA_ERROR
        ordered_results.append(result)
    if all(result.status == "passed" for result in ordered_results):
        return REPO_BASELINE_PASSED
    return REPO_BASELINE_FAILED


def _discard_repo_pair_outputs(
    repo_rows: list[dict[str, Any]],
    pair_checkpoints_dir: Path,
    tasks_dir: Path,
    pair_test_results_dir: Path,
) -> None:
    for candidate in repo_rows:
        pair_id = str(candidate["pair_id"])
        (pair_checkpoints_dir / f"{pair_id}.json").unlink(missing_ok=True)
        (pair_test_results_dir / f"{pair_id}.jsonl").unlink(missing_ok=True)
        shutil.rmtree(tasks_dir / pair_id, ignore_errors=True)


def _repo_report_row(
    repo_key: str,
    baseline_status: str,
    pair_results: list[dict[str, Any]],
) -> dict[str, Any]:
    pair_status_counts = {
        PAIR_COMPLETED: 0,
        PAIR_CONSTRUCTION_FAILED: 0,
        PAIR_INFRA_ERROR: 0,
    }
    selection_counts = {"main_task": 0, "no_f2p": 0}
    for row in pair_results:
        status = str(row.get("status") or "")
        if status in pair_status_counts:
            pair_status_counts[status] += 1
        selection_result = str(row.get("selection_result") or "")
        if selection_result in selection_counts:
            selection_counts[selection_result] += 1
    return {
        "repo_key": repo_key,
        "baseline_status": baseline_status,
        "pair_status_counts": pair_status_counts,
        "selection_counts": selection_counts,
        "tasks": selection_counts["main_task"],
    }


def _pair_report_row(checkpoint: dict[str, Any]) -> dict[str, Any]:
    row = {
        "pair_id": checkpoint["pair_id"],
        "repo_key": checkpoint["repo_key"],
        "status": checkpoint["status"],
        "test_summary": checkpoint.get("test_summary"),
        "error": checkpoint.get("error"),
    }
    if checkpoint.get("status") == PAIR_COMPLETED:
        row["selection_result"] = checkpoint["selection_result"]
        row["test_summary"] = _required_test_summary(checkpoint)
    return row


def _retire_summary(
    repo_results: list[dict[str, Any]],
    pair_results: list[dict[str, Any]],
) -> dict[str, Any]:
    baseline_status_counts = {
        REPO_BASELINE_PASSED: 0,
        REPO_BASELINE_FAILED: 0,
        REPO_BASELINE_INFRA_ERROR: 0,
    }
    for row in repo_results:
        status = str(row.get("baseline_status") or "")
        if status in baseline_status_counts:
            baseline_status_counts[status] += 1

    pair_status_counts = {
        PAIR_COMPLETED: 0,
        PAIR_CONSTRUCTION_FAILED: 0,
        PAIR_INFRA_ERROR: 0,
    }
    selection_counts = {"main_task": 0, "no_f2p": 0}
    for row in pair_results:
        status = str(row.get("status") or "")
        if status in pair_status_counts:
            pair_status_counts[status] += 1
        selection_result = str(row.get("selection_result") or "")
        if selection_result in selection_counts:
            selection_counts[selection_result] += 1

    return {
        "repos": len(repo_results),
        "baseline_status_counts": baseline_status_counts,
        "pair_status_counts": pair_status_counts,
        "selection_counts": selection_counts,
        "tasks": selection_counts["main_task"],
    }


def _cleanup_stale_json_checkpoints(directory: Path, expected_stems: set[str]) -> None:
    for path in directory.glob("*.json"):
        if path.stem not in expected_stems:
            path.unlink()


def _cleanup_stale_jsonl_results(directory: Path, expected_stems: set[str]) -> None:
    for path in directory.glob("*.jsonl"):
        if path.stem not in expected_stems:
            path.unlink()


def _cleanup_stale_tasks(tasks_dir: Path, expected_names: set[str]) -> None:
    for path in tasks_dir.iterdir():
        if path.name in expected_names:
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _validate_retire_output_layout(output_root: Path) -> None:
    if not output_root.exists():
        return
    if not output_root.is_dir():
        raise RuntimeError(f"Retire output path is not a directory: {output_root}")
    unexpected = sorted(
        path.name
        for path in output_root.iterdir()
        if path.name not in RETIRE_OUTPUT_DIRECTORIES or not path.is_dir()
    )
    if unexpected:
        names = ", ".join(unexpected)
        raise RuntimeError(
            "Retire output directory does not use the current layout; remove it "
            f"before rerunning: {output_root} (unexpected: {names})"
        )


def _load_environment(environment_dir: Path) -> dict[str, Any] | None:
    manifest_path = environment_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    manifest = _read_json(manifest_path)
    if manifest.get("status") != "completed":
        return None
    required = (
        "Dockerfile",
        "run_script.sh",
        "collect_report.json",
        "full_report.json",
        "test_results.jsonl",
    )
    if any(not (environment_dir / filename).exists() for filename in required):
        return None
    return manifest


def _validation_test_universe(environment_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _read_jsonl(environment_dir / "test_results.jsonl"):
        test_file = str(row.get("test_file") or "").strip()
        if not test_file or row.get("status") != "passed":
            continue
        target_selector = str(row.get("target_selector") or test_file).strip()
        _validate_runner_selector(target_selector)
        rows.append(
            {
                "test_file": test_file,
                "target_selector": target_selector,
                "status": "passed",
                "total_tests": int(row.get("total_tests", 0) or 0),
                "passed_tests": int(row.get("passed_tests", 0) or 0),
                "failed_tests": int(row.get("failed_tests", 0) or 0),
                "error_tests": int(row.get("error_tests", 0) or 0),
                "skipped_tests": int(row.get("skipped_tests", 0) or 0),
                "exit_code": int(row.get("exit_code", 0) or 0),
            }
        )
    return rows


def _pair_materials_ready(pair_output_dir: Path) -> bool:
    return all(
        (pair_output_dir / filename).is_file() for filename in PAIR_MATERIAL_FILES
    )


def _repo_cache_dir(project_root: Path, candidate: dict[str, Any]) -> Path:
    full_name = str(candidate["full_name"])
    owner, repo = full_name.split("/", 1)
    return project_root / "data" / "cache" / "repos" / owner / f"{repo}.git"


def _checkout_commit(cache_dir: Path, commit: str, work_tree: Path) -> None:
    work_tree.mkdir(parents=True, exist_ok=True)
    env = _work_tree_git_env(work_tree)
    index_path = Path(env["GIT_INDEX_FILE"])
    try:
        _run_git_process(
            ["git", f"--git-dir={cache_dir}", "read-tree", commit],
            env=env,
        )
        _run_git_process(
            [
                "git",
                f"--git-dir={cache_dir}",
                f"--work-tree={work_tree}",
                "checkout-index",
                "--all",
                "--force",
            ],
            env=env,
        )
    finally:
        index_path.unlink(missing_ok=True)


def _apply_patch(work_tree: Path, patch_path: Path) -> None:
    _run_process(
        [
            "git",
            "apply",
            "--binary",
            "--whitespace=nowarn",
            str(patch_path),
        ],
        env=_work_tree_git_env(work_tree),
        cwd=work_tree,
    )


def _gold_cleanup_patch(cache_dir: Path, work_tree: Path, new_commit: str) -> bytes:
    env = _work_tree_git_env(work_tree)
    _run_git_process(
        [
            "git",
            f"--git-dir={cache_dir}",
            f"--work-tree={work_tree}",
            "read-tree",
            new_commit,
        ],
        env=env,
    )
    cleanup_patch = _run_git_process(
        [
            "git",
            f"--git-dir={cache_dir}",
            f"--work-tree={work_tree}",
            "diff",
            "--minimal",
            "--binary",
            "--no-renames",
            "-R",
            new_commit,
            "--",
            ".",
        ],
        env=env,
    ).stdout
    if _patch_has_added_content(cleanup_patch):
        raise RuntimeError("gold_cleanup.patch contains added content lines")
    return cleanup_patch


def _patch_has_added_content(patch: bytes) -> bool:
    return any(
        line.startswith(b"+") and not line.startswith(b"+++ ")
        for line in patch.splitlines()
    )


def _build_environment_image(environment_dir: Path, repo_key: str) -> str:
    dockerfile = environment_dir / "Dockerfile"
    digest = _environment_build_context_digest(environment_dir)
    tag = f"belta-retire-base-{_slug(repo_key)}-{digest[:16]}"
    if _docker_image_exists(tag):
        return tag
    with _environment_image_build_lock(tag):
        if _docker_image_exists(tag):
            return tag
        _run_process(
            [
                "docker",
                "build",
                "--label",
                "org.belta.managed=true",
                "--label",
                "org.belta.stage=retire-base",
                "--label",
                f"org.belta.build-context-sha256={digest}",
                "-t",
                tag,
                "-f",
                str(dockerfile),
                str(environment_dir),
            ]
        )
    return tag


def _environment_build_context_digest(environment_dir: Path) -> str:
    digest = hashlib.sha256()
    platform_value = os.environ.get(
        "DOCKER_DEFAULT_PLATFORM",
        f"{platform.system().lower()}/{platform.machine().lower()}",
    )
    digest.update(platform_value.encode("utf-8"))
    digest.update(b"\0")
    for path in sorted(environment_dir.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(environment_dir).as_posix()
        stat_result = path.lstat()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat_result.st_mode).encode("ascii"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(os.readlink(path).encode("utf-8"))
        elif path.is_file():
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _docker_image_exists(tag: str) -> bool:
    completed = subprocess.run(
        ["docker", "image", "inspect", tag],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return completed.returncode == 0


@contextmanager
def _environment_image_build_lock(tag: str) -> Iterator[None]:
    with _ENVIRONMENT_IMAGE_LOCKS_GUARD:
        thread_lock = _ENVIRONMENT_IMAGE_LOCKS.setdefault(tag, Lock())
    with thread_lock:
        lock_dir = Path(tempfile.gettempdir()) / "belta-retire-image-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{hashlib.sha256(tag.encode()).hexdigest()}.lock"
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _run_validation_tests(
    docker_tag: str,
    environment_dir: Path,
    validation_tests: list[dict[str, Any]],
    *,
    patch_path: Path | None = None,
    test_results_path: Path | None = None,
    full_validation_timeout_seconds: int = FULL_VALIDATION_TIMEOUT_SECONDS,
    stop_event: Event | None = None,
) -> ValidationSuiteResult:
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("Retire Task Construction interrupted")
    if test_results_path is not None:
        test_results_path.unlink(missing_ok=True)
    container_name = f"belta-validation-{os.getpid()}-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(
        prefix="belta-test-out-", ignore_cleanup_errors=True
    ) as tmp:
        output_dir = Path(tmp)
        output_dir.chmod(0o777)
        start_command = [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--init",
            "--name",
            container_name,
        ]
        start_command.extend(
            [
                "-v",
                f"{(environment_dir / 'run_script.sh').resolve()}:/workspace/run_script.sh:ro",
                "-v",
                f"{output_dir.resolve()}:/workspace/belta_out",
                docker_tag,
                "bash",
                "-lc",
                "while :; do sleep 3600; done",
            ]
        )
        try:
            started = subprocess.run(
                start_command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            error = f"failed to start validation container: {type(exc).__name__}: {exc}"
            _write_validation_results(test_results_path, [])
            return ValidationSuiteResult([], error=error)
        if started.returncode != 0:
            error = _command_error_message(
                "failed to start validation container",
                stdout=started.stdout or "",
                stderr=started.stderr or "",
            )
            _write_validation_results(test_results_path, [])
            return ValidationSuiteResult([], error=error)

        _register_validation_container(container_name)
        try:
            if patch_path is not None:
                _apply_patch_in_validation_container(container_name, patch_path)
            results: list[ValidationRunResult] = []
            suite_error: str | None = None
            deadline = time.monotonic() + full_validation_timeout_seconds
            for index, row in enumerate(validation_tests):
                if stop_event is not None and stop_event.is_set():
                    raise InterruptedError("Retire Task Construction interrupted")
                test_file = str(row["test_file"])
                target_selector = str(row.get("target_selector") or test_file)
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    suite_error = (
                        "validation suite timed out after "
                        f"{full_validation_timeout_seconds} seconds"
                    )
                    break
                try:
                    result = _run_validation_test_in_container(
                        container_name,
                        test_file,
                        target_selector,
                        output_dir / f"result-{index:06d}.json",
                        remaining_suite_seconds=remaining_seconds,
                    )
                except ValidationSuiteTimeout:
                    suite_error = (
                        "validation suite timed out after "
                        f"{full_validation_timeout_seconds} seconds while running "
                        f"{test_file}"
                    )
                    break
                except PairInfrastructureError as exc:
                    suite_error = str(exc)
                    break
                results.append(result)
            _write_validation_results(test_results_path, results)
            return ValidationSuiteResult(results, error=suite_error)
        finally:
            _remove_validation_container(container_name)
            _unregister_validation_container(container_name)


def _ensure_validation_results_file(
    path: Path,
    results: list[ValidationRunResult],
) -> None:
    if not path.is_file():
        _write_validation_results(path, results)


def _apply_patch_in_validation_container(
    container_name: str,
    patch_path: Path,
) -> None:
    try:
        patch_content = patch_path.read_bytes()
    except OSError as exc:
        raise PairConstructionError(
            f"failed to read Pair patch: {type(exc).__name__}: {exc}"
        ) from exc
    command = [
        "docker",
        "exec",
        "--interactive",
        container_name,
        "bash",
        "-lc",
        (
            "cd /workspace/repo && "
            "if ! git apply --binary --whitespace=nowarn -; then "
            f"echo {PAIR_PATCH_APPLY_FAILURE_MARKER} >&2; exit 86; fi"
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            input=patch_content,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        raise PairInfrastructureError(
            f"failed to execute Pair patch command: {type(exc).__name__}: {exc}"
        ) from exc
    if completed.returncode == 0:
        return
    output = (completed.stdout or b"").decode("utf-8", errors="replace").strip()
    if completed.returncode == 86 or PAIR_PATCH_APPLY_FAILURE_MARKER in output:
        raise PairConstructionError(output or "Pair patch could not be applied")
    raise PairInfrastructureError(output or "failed to execute Pair patch command")


def _run_validation_test_in_container(
    container_name: str,
    test_file: str,
    target_selector: str,
    result_path: Path,
    *,
    remaining_suite_seconds: float,
) -> ValidationRunResult:
    result_name = result_path.name
    result_path.unlink(missing_ok=True)
    command = [
        "docker",
        "exec",
        "-e",
        f"BELTA_TEST_FILE={test_file}",
        "-e",
        f"BELTA_TARGET_SELECTOR={target_selector}",
        "-e",
        f"BELTA_RESULT_FILE={result_name}",
        container_name,
        "bash",
        "-lc",
        (
            "set +e; /workspace/run_script.sh --action run "
            '--target-selector "$BELTA_TARGET_SELECTOR" '
            '--out "/workspace/belta_out/$BELTA_RESULT_FILE"; '
            'rc=$?; chmod a+r "/workspace/belta_out/$BELTA_RESULT_FILE" '
            '2>/dev/null || true; exit "$rc"'
        ),
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=remaining_suite_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValidationSuiteTimeout from exc
    except OSError as exc:
        raise PairInfrastructureError(
            f"failed to execute run_script: {type(exc).__name__}: {exc}"
        ) from exc
    if not result_path.exists():
        return _validation_infra_result(
            test_file,
            target_selector,
            "run_script did not write result JSON",
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    try:
        payload = _read_json(result_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _validation_infra_result(
            test_file,
            target_selector,
            f"invalid result JSON: {type(exc).__name__}: {exc}",
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    payload_error = _runner_payload_error(payload)
    if payload_error is not None:
        return _validation_infra_result(
            test_file,
            target_selector,
            payload_error,
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )
    status = str(payload["status"]).strip()
    return ValidationRunResult(
        test_file=test_file,
        target_selector=target_selector,
        status=status,
        summary=_normalize_summary(payload.get("summary")),
        raw_result=payload,
        exit_code=completed.returncode,
    )


def _validation_infra_result(
    test_file: str,
    target_selector: str,
    error: str,
    *,
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
) -> ValidationRunResult:
    summary = {"collected": 0, "passed": 0, "failed": 0, "errors": 1, "skipped": 0}
    return ValidationRunResult(
        test_file=test_file,
        target_selector=target_selector,
        status="error",
        summary=summary,
        raw_result={
            "action": "run",
            "status": "error",
            "summary": summary,
            "stdout": stdout,
            "stderr": stderr,
            "message": error,
        },
        exit_code=exit_code,
        infra_error=error,
    )


def _command_error_message(default: str, *, stdout: str, stderr: str) -> str:
    details = stderr.strip() or stdout.strip()
    return f"{default}: {details}" if details else default


def _remove_validation_container(container_name: str) -> None:
    subprocess.run(
        ["docker", "rm", "--force", container_name],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _register_validation_container(container_name: str) -> None:
    with _ACTIVE_VALIDATION_CONTAINERS_LOCK:
        _ACTIVE_VALIDATION_CONTAINERS.add(container_name)


def _unregister_validation_container(container_name: str) -> None:
    with _ACTIVE_VALIDATION_CONTAINERS_LOCK:
        _ACTIVE_VALIDATION_CONTAINERS.discard(container_name)


def _stop_active_validation_containers() -> None:
    with _ACTIVE_VALIDATION_CONTAINERS_LOCK:
        container_names = tuple(_ACTIVE_VALIDATION_CONTAINERS)
    for container_name in container_names:
        _remove_validation_container(container_name)


def _normalize_summary(value: Any) -> dict[str, int]:
    source = value if isinstance(value, dict) else {}
    return {
        "collected": int(source.get("collected", 0) or 0),
        "passed": int(source.get("passed", 0) or 0),
        "failed": int(source.get("failed", 0) or 0),
        "errors": int(source.get("errors", 0) or 0),
        "skipped": int(source.get("skipped", 0) or 0),
    }


def _runner_payload_error(
    payload: object,
) -> str | None:
    if not isinstance(payload, dict):
        return "run result JSON must be an object"
    if payload.get("action") != "run":
        return "run result JSON is missing action=run"
    status = payload.get("status")
    if not isinstance(status, str) or status.strip() not in {"passed", "failed"}:
        return "run result JSON status must be passed or failed"
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        return "run result JSON is missing summary"
    keys = ("collected", "passed", "failed", "errors", "skipped")
    if any(
        isinstance(summary.get(key), bool) or not isinstance(summary.get(key), int)
        for key in keys
    ):
        return "run result JSON summary fields must be integers"
    normalized = {key: int(summary[key]) for key in keys}
    if min(normalized.values()) < 0:
        return "run result JSON summary fields must be non-negative"
    if status.strip() == "passed":
        if (
            normalized["collected"] <= 0
            or normalized["passed"] <= 0
            or normalized["failed"] != 0
            or normalized["errors"] != 0
            or normalized["collected"]
            != sum(
                normalized[key]
                for key in ("passed", "failed", "errors", "skipped")
            )
        ):
            return "run result JSON passed status conflicts with summary counts"
    return None


def _validation_result_row(result: ValidationRunResult) -> dict[str, Any]:
    return {
        "test_file": result.test_file,
        "target_selector": result.target_selector,
        "status": result.status,
        "total_tests": result.summary["collected"],
        "passed_tests": result.summary["passed"],
        "failed_tests": result.summary["failed"],
        "error_tests": result.summary["errors"],
        "skipped_tests": result.summary["skipped"],
        "exit_code": result.exit_code,
        "raw_result": result.raw_result,
    }


def _write_validation_results(
    path: Path | None,
    results: list[ValidationRunResult],
) -> None:
    if path is None:
        return
    _write_jsonl_atomic(path, [_validation_result_row(result) for result in results])


def _copy_private_materials(pair_output_dir: Path, private_materials_dir: Path) -> None:
    private_materials_dir.mkdir(parents=True, exist_ok=True)
    copies = {
        "metadata.json": "pair_metadata.json",
        "reinsert_extraction.patch": "reinsert_extraction.patch",
        "reinsert_targets.jsonl": "reinsert_targets.jsonl",
        "implementation_units.jsonl": "implementation_units.jsonl",
        "obsolete_reinsert.patch": "obsolete_reinsert.patch",
    }
    for source_name, target_name in copies.items():
        shutil.copy2(pair_output_dir / source_name, private_materials_dir / target_name)


def _copy_environment_assets(environment_dir: Path, eval_assets_dir: Path) -> None:
    eval_assets_dir.mkdir(parents=True, exist_ok=True)
    copies = {
        "Dockerfile": "Dockerfile",
        "run_script.sh": "run_script.sh",
    }
    for source_name, target_name in copies.items():
        shutil.copy2(environment_dir / source_name, eval_assets_dir / target_name)
    (eval_assets_dir / "run_script.sh").chmod(0o755)


def _snapshot_validation_tests(
    task_base_full: Path,
    eval_assets_dir: Path,
    validation_tests: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    snapshots_dir = eval_assets_dir / "validation_test_snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(validation_tests, start=1):
        rel_path = str(row["test_file"])
        target_selector = str(row.get("target_selector") or rel_path)
        source_path = _safe_repo_path(task_base_full, rel_path)
        if not source_path.is_file():
            raise RuntimeError(
                f"validation test file missing from task_base_full: {rel_path}"
            )
        content = source_path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        suffix = Path(rel_path).suffix
        storage_name = f"{index:04d}-{digest[:16]}{suffix}"
        storage_path = snapshots_dir / storage_name
        storage_path.write_bytes(content)
        snapshot = {
            "path": rel_path,
            "storage_path": f"validation_test_snapshots/{storage_name}",
            "sha256": digest,
        }
        if target_selector != rel_path:
            snapshot["target_selector"] = target_selector
        rows.append(snapshot)
    return rows


def _safe_repo_path(repo_dir: Path, rel_path: str) -> Path:
    path = Path(rel_path)
    if path.is_absolute() or "\x00" in rel_path or ".." in path.parts:
        raise RuntimeError(f"unsafe repo-relative path: {rel_path!r}")
    resolved = (repo_dir / path).resolve()
    try:
        resolved.relative_to(repo_dir.resolve())
    except ValueError as exc:
        raise RuntimeError(f"unsafe repo-relative path: {rel_path!r}") from exc
    return resolved


def _validate_runner_selector(value: str) -> None:
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        raise RuntimeError(f"unsafe runner target selector: {value!r}")


def _test_summary(
    validation_tests: list[dict[str, Any]],
    task_base_results: list[ValidationRunResult],
) -> dict[str, int]:
    passed = sum(1 for result in task_base_results if result.status == "passed")
    failed = sum(1 for result in task_base_results if result.status == "failed")
    errors = sum(1 for result in task_base_results if result.status == "error")
    return {
        "new_commit_total_files": len(validation_tests),
        "new_commit_passed_files": len(validation_tests),
        "task_base_total_files": len(validation_tests),
        "task_base_passed_files": passed,
        "task_base_failed_files": failed,
        "task_base_error_files": errors,
    }


def _baseline_test_summary(
    validation_tests: list[dict[str, Any]],
    results: list[ValidationRunResult],
) -> dict[str, int]:
    return {
        "new_commit_total_files": len(validation_tests),
        "new_commit_passed_files": sum(
            1 for result in results if result.status == "passed"
        ),
        "new_commit_failed_files": sum(
            1 for result in results if result.status == "failed"
        ),
        "new_commit_error_files": sum(
            1 for result in results if result.status == "error"
        ),
    }


def _validation_suite_error(
    validation_tests: list[dict[str, Any]],
    suite_result: ValidationSuiteResult,
) -> str | None:
    if suite_result.error is not None:
        return suite_result.error
    error_results = [
        result
        for result in suite_result.results
        if result.status == "error" or result.infra_error is not None
    ]
    if error_results:
        first = error_results[0]
        first_message = first.infra_error or "validation execution error"
        return (
            f"{len(error_results)} validation test file(s) ended with execution errors; "
            f"first: {first.test_file}: {first_message}"
        )
    if len(suite_result.results) != len(validation_tests):
        return (
            "validation suite ended before all planned test files ran: "
            f"attempted={len(suite_result.results)} planned={len(validation_tests)}"
        )
    return None


def _task_manifest(
    run_dir: Path,
    task_dir: Path,
    candidate: dict[str, Any],
    environment: dict[str, Any],
    validation_tests: list[dict[str, Any]],
    test_summary: dict[str, int],
    validation_timeouts: ValidationTimeouts,
) -> dict[str, Any]:
    pair_id = str(candidate["pair_id"])
    repo_key = str(candidate["repo_key"])
    return {
        "pair_id": pair_id,
        "repo": {
            "repo_key": repo_key,
            "full_name": candidate["full_name"],
            "github_repo_id": candidate["github_repo_id"],
            "clone_url": candidate["clone_url"],
            "default_branch": candidate["default_branch"],
        },
        "commits": {
            "old_commit": candidate["old_commit"],
            "new_commit": candidate["new_commit"],
        },
        "candidate_stats": _candidate_stats(candidate),
        "task_base_full": {
            "base_commit": candidate["new_commit"],
            "construction": "apply private_materials/obsolete_reinsert.patch to new_commit",
            "patch": "private_materials/obsolete_reinsert.patch",
            "agent_visible": False,
        },
        "private_materials": {
            "source_pair_output_dir": _display_path(
                run_dir / "candidate_pairs" / "pair_outputs" / pair_id,
                relative_to=run_dir,
            ),
            "pair_metadata": "private_materials/pair_metadata.json",
            "reinsert_extraction_patch": "private_materials/reinsert_extraction.patch",
            "reinsert_targets": "private_materials/reinsert_targets.jsonl",
            "implementation_units": "private_materials/implementation_units.jsonl",
            "obsolete_reinsert_patch": "private_materials/obsolete_reinsert.patch",
            "gold_cleanup_patch": "private_materials/gold_cleanup.patch",
        },
        "eval_assets": {
            "dockerfile": "eval_assets/Dockerfile",
            "run_script": "eval_assets/run_script.sh",
            "validation_test_snapshots": "eval_assets/validation_test_snapshots.jsonl",
            "validation_test_snapshots_dir": "eval_assets/validation_test_snapshots",
            "ff_stage2_run_id": environment.get("ff_stage2_run_id"),
            "validation_test_universe_file_count": len(validation_tests),
        },
        "validation": {
            "full_validation_timeout_seconds": (
                validation_timeouts.full_validation_timeout_seconds
            ),
        },
        "selection": {
            "selection_result": "main_task",
            "reason": (
                "Environment Construction passed validation_test_universe; "
                "task_base_full failed at least one file"
            ),
        },
        "test_summary": test_summary,
        "paths": {
            "task_dir": _display_path(task_dir, relative_to=run_dir),
            "manifest": "manifest.json",
            "task_instruction": "task_instruction.md",
        },
    }


def _candidate_stats(candidate: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "removed_object_targets_count",
        "removed_range_targets_count",
        "implementation_units_count",
        "reinsert_source_lines",
        "task_base_compile",
        "task_base_compile_error",
    )
    return {key: candidate.get(key) for key in keys}


def _display_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(value: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")


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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_jsonl_atomic(path: Path, rows: list[dict[str, Any]]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    _write_text_atomic(path, content)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON file must be an object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_atomic(path, content)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_git_process(
    args: list[str], *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    return _run_process(args, env=env)


def _run_process(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        stdout = result.stdout.decode("utf-8", errors="replace").strip()
        detail = "\n".join(part for part in (stderr, stdout) if part)
        if not detail:
            detail = f"command failed: {' '.join(args)}"
        raise RuntimeError(detail)
    return result


def _work_tree_git_env(work_tree: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(work_tree.parent / f"{work_tree.name}.index")
    return env
