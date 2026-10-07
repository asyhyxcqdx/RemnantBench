from __future__ import annotations

import hashlib
import inspect
import json
import os
import queue
import random
import re
import signal
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from feature_factory.config import Settings
from feature_factory.docker_mirrors import (
    dockerfile_with_mirrored_from_images,
    should_use_china_docker_mirrors,
)


class Stage2ValidationError(RuntimeError):
    pass


class Stage2ValidationInterrupted(Stage2ValidationError):
    pass


_LOCAL_CONTEXT_TRANSFER_RE = re.compile(r"^\s*(?:COPY|ADD)\b(?![^\n]*\b--from=).*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True, slots=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    passed: bool
    report: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Stage2ValidationConfig:
    stage2_quickcheck_sample_size: int
    stage2_collect_timeout_seconds: float
    stage2_run_test_timeout_seconds: float
    stage2_build_timeout_seconds: float
    stage2_full_validation_timeout_seconds: float
    stage2_docker_image_prefix: str
    stage2_p2p_file_count_limit: int | None = None
    stage2_p2p_sample_seed: str | None = None


ValidationEventCallback = Callable[[str, str, dict[str, Any]], None]


@dataclass(frozen=True, slots=True)
class RunPayloadSchemaError:
    code: str
    message: str


def _json_safe_validation_value(value: Any) -> Any:
    if isinstance(value, RunPayloadSchemaError):
        return {
            "code": str(value.code or ""),
            "message": str(value.message or ""),
        }
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _json_safe_validation_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_safe_validation_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe_validation_value(item) for item in value]
    return value


def _collect_test_file_path(item: dict[str, Any]) -> str:
    return str(item.get("path") or "").strip()


def _collect_target_selector(item: dict[str, Any]) -> str:
    path = _collect_test_file_path(item)
    return str(item.get("target_selector") or path).strip()


def _validate_relative_file_path_value(value: str, *, label: str, root_label: str) -> str | None:
    if not value.strip():
        return f"collect payload contains an empty {label}"
    if "\x00" in value or "\n" in value or "\r" in value:
        return f"collect payload {label}s must be single-line strings"
    posix_path = PurePosixPath(value)
    if posix_path.is_absolute():
        return f"collect payload {label}s must be relative to {root_label}"
    if any(part == ".." for part in posix_path.parts):
        return f"collect payload {label}s must not contain parent-directory traversal"
    return None


def validate_artifact_schema(*, dockerfile_text: str | None, run_script_text: str | None) -> None:
    if not dockerfile_text or "FROM " not in dockerfile_text:
        raise Stage2ValidationError("Dockerfile is missing or does not declare a base image")
    if _LOCAL_CONTEXT_TRANSFER_RE.search(dockerfile_text):
        raise Stage2ValidationError(
            "Dockerfile must not rely on local build-context files; fetch the repository "
            "inside the image and let the host inject run_script.sh at /workspace/run_script.sh"
        )
    if not run_script_text or "--action" not in run_script_text or "--out" not in run_script_text:
        raise Stage2ValidationError("run_script.sh is missing required CLI flags")


class Stage2Validator:
    def __init__(self, settings: Settings | Stage2ValidationConfig) -> None:
        self.settings = settings
        self._progress_context: dict[str, Any] = {}

    def run_smoke(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        dockerfile_text: str,
        run_script_text: str,
        on_collect_validated: Callable[[dict[str, Any]], None] | None = None,
        emit_event: ValidationEventCallback | None = None,
        attempt_index: int | None = None,
    ) -> ValidationOutcome:
        image_tag = self._image_tag(run_id)
        with self._validation_progress_scope(
            emit_event=emit_event,
            validator="smoke",
            attempt_index=attempt_index,
            run_id=run_id,
            image_tag=image_tag,
        ):
            try:
                validate_artifact_schema(dockerfile_text=dockerfile_text, run_script_text=run_script_text)
                run_script_host_path = self._resolve_run_script_host_path(
                    workspace_dir=workspace_dir,
                    run_script_text=run_script_text,
                )
            except Stage2ValidationError as exc:
                return ValidationOutcome(
                    passed=False,
                    report=self._failure_report(
                        validator="smoke",
                        phase="schema",
                        code="ARTIFACT_SCHEMA_INVALID",
                        message=str(exc),
                        evidence={},
                        suggested_actions=[
                            "ensure Dockerfile declares a base image with FROM",
                            "ensure run_script.sh accepts --action and --out",
                        ],
                        image_tag=image_tag,
                    ),
                )
            with self._validation_progress_scope(run_script_host_path=run_script_host_path):
                build_result = self._build_image(run_id=run_id, workspace_dir=workspace_dir)
                if build_result.returncode != 0:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="smoke",
                            phase="build",
                            code="DOCKER_BUILD_FAILED",
                            message="docker build failed during smoke validation",
                            evidence=self._command_evidence(build_result),
                            suggested_actions=[
                                "inspect Dockerfile install steps",
                                "inspect missing system packages or Python dependencies",
                                "check whether the selected base image is compatible with the repo runtime",
                            ],
                            image_tag=image_tag,
                        ),
                    )

                collect_result = self._run_collect(image_tag)
                collect_payload = self._maybe_load_json_from_stdout(collect_result)
                if collect_payload is None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="smoke",
                            phase="collect",
                            code="TEST_COLLECT_INVALID_PAYLOAD",
                            message="run_script.sh collect did not emit valid JSON",
                            evidence=self._command_evidence(collect_result),
                            suggested_actions=[
                                "ensure run_script.sh always prints JSON to stdout",
                                "ensure collect writes the schema fields expected by validator",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                collect_error = self._validate_collect_payload(collect_payload)
                if collect_error is not None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="smoke",
                            phase="collect",
                            code="TEST_COLLECT_INVALID_SCHEMA",
                            message=collect_error,
                            evidence={
                                **self._command_evidence(collect_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "ensure collect payload contains action=collect and a valid test_files list",
                                "ensure each test file entry includes a path string",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                path_check_result = self._validate_collect_paths_exist(
                    image_tag,
                    collect_payload,
                    timeout_seconds=self.settings.stage2_collect_timeout_seconds,
                )
                if path_check_result is not None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="smoke",
                            phase="collect",
                            code="TEST_COLLECT_PATHS_NOT_REPOSITORY_RELATIVE",
                            message=(
                                "collect emitted one or more test file paths that do not exist under "
                                "/workspace/repo; collect paths must be repository-root-relative file paths"
                            ),
                            evidence={
                                **self._command_evidence(path_check_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "emit test_files[].path values relative to /workspace/repo",
                                "if tests run from a subdirectory, convert the repo-root path inside run_script.sh before invoking the test framework",
                                "do not emit test-root-relative selectors when the real file path includes an additional repository subdirectory prefix",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                collect_payload = self._collect_payload_with_p2p_limit(collect_payload, run_id=run_id)
                if on_collect_validated is not None:
                    on_collect_validated(dict(collect_payload))

                test_files = list(collect_payload.get("test_files") or [])
                if not test_files:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="smoke",
                            phase="collect",
                            code="TEST_COLLECT_ZERO",
                            message="test collection returned zero test files",
                            evidence={
                                **self._command_evidence(collect_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "inspect custom test paths",
                                "check whether editable install or PYTHONPATH is missing",
                                "check whether the repository uses a different test framework",
                            ],
                            image_tag=image_tag,
                        ),
                    )

                sample_size = min(len(test_files), self.settings.stage2_quickcheck_sample_size)
                sample = random.Random(run_id).sample(test_files, k=sample_size)
                self._emit_validation_event(
                    title="Validate sample started",
                    message=f"Running {sample_size} sampled unit test files",
                    payload={
                        "validator": "smoke",
                        "stage": "sample",
                        "attempt_index": attempt_index,
                        "sample_size": sample_size,
                    },
                )
                sampled_results = []
                for index, item in enumerate(sample, start=1):
                    test_file_path = _collect_test_file_path(item)
                    target_selector = _collect_target_selector(item)
                    with self._validation_progress_scope(
                        current_test_file=test_file_path,
                        current_target_selector=target_selector,
                        current_test_index=index,
                        current_test_total=sample_size,
                    ):
                        sampled_results.append(
                            self._run_collected_test_file(
                                image_tag,
                                test_file_path=test_file_path,
                                target_selector=target_selector,
                            )
                        )

            invalid_payload_sample = next(
                (
                    item
                    for item in sampled_results
                    if not item["command"]["timed_out"]
                    and (item["payload"] is None or item["schema_error"] is not None)
                ),
                None,
            )
            if invalid_payload_sample is not None:
                return ValidationOutcome(
                    passed=False,
                    report=self._failure_report(
                        validator="smoke",
                        phase="sample",
                        code=str(
                            (
                                (invalid_payload_sample.get("schema_error") or {}).get("code")
                                if isinstance(invalid_payload_sample.get("schema_error"), dict)
                                else getattr(invalid_payload_sample.get("schema_error"), "code", "")
                            )
                            or "TEST_SAMPLE_INVALID_PAYLOAD"
                        ),
                        message=str(
                            (
                                (invalid_payload_sample.get("schema_error") or {}).get("message")
                                if isinstance(invalid_payload_sample.get("schema_error"), dict)
                                else getattr(invalid_payload_sample.get("schema_error"), "message", "")
                            )
                            or "sample test execution did not emit valid JSON"
                        ),
                        evidence={
                            "collect": {
                                "command": collect_result.args,
                                "returncode": collect_result.returncode,
                                "payload": collect_payload,
                            },
                            "sample_result": invalid_payload_sample,
                        },
                        suggested_actions=[
                            "recompute summary counts from structured test results instead of free-form text",
                            "normalize framework-specific outcomes into passed, failed, errors, or skipped",
                            "ensure status matches the summary counts for the selected test file",
                        ],
                        image_tag=image_tag,
                    ),
                )

            timed_out_samples = [
                item for item in sampled_results if bool((item.get("command") or {}).get("timed_out"))
            ]
            passed_files = sum(1 for item in sampled_results if (item["result"] or {}).get("status") == "passed")
            smoke_passed = sample_size > 0 and passed_files * 2 >= sample_size
            report = {
                "schema_version": 1,
                "validator": "smoke",
                "status": "passed" if smoke_passed else "failed",
                "phase": "sample",
                "image_tag": image_tag,
                "collect": {
                    "command": collect_result.args,
                    "returncode": collect_result.returncode,
                    "payload": collect_payload,
                },
                "sample_size": sample_size,
                "passed_files": passed_files,
                "timed_out_files": len(timed_out_samples),
                "sampled_results": sampled_results,
            }
            if not smoke_passed:
                if timed_out_samples:
                    report["feedback"] = {
                        "status": "failed",
                        "code": "TEST_SAMPLE_TIMEOUT",
                        "message": "one or more sampled test files timed out and fewer than half of sampled test files passed",
                        "evidence": {
                            "sample_size": sample_size,
                            "passed_files": passed_files,
                            "timed_out_files": len(timed_out_samples),
                            "timed_out_samples": timed_out_samples,
                            "sampled_results": sampled_results,
                        },
                        "suggested_actions": [
                            "inspect run_script.sh run action and emitted JSON schema",
                            "check test command prefix and environment variables",
                            "check whether tests hang because required services or env vars are missing",
                        ],
                        "retryable": True,
                    }
                else:
                    report["feedback"] = {
                        "status": "failed",
                        "code": "SMOKE_SAMPLE_BELOW_THRESHOLD",
                        "message": "fewer than half of sampled test files passed",
                        "evidence": {
                            "sample_size": sample_size,
                            "passed_files": passed_files,
                            "sampled_results": sampled_results,
                        },
                        "suggested_actions": [
                            "adjust dependency installation strategy",
                            "retry with explicit PYTHONPATH or alternate package install mode",
                            "inspect failing sample tests for missing runtime dependencies",
                        ],
                        "retryable": True,
                    }
            return ValidationOutcome(passed=smoke_passed, report=report)

    def run_full(
        self,
        *,
        run_id: str,
        workspace_dir: Path,
        cancel_requested: Callable[[], bool] | None = None,
        emit_event: ValidationEventCallback | None = None,
    ) -> ValidationOutcome:
        if shutil.which("docker") is None:
            raise Stage2ValidationError("docker is required for stage2 validation")
        image_tag = self._image_tag(run_id)
        with self._validation_progress_scope(
            emit_event=emit_event,
            validator="full",
            run_id=run_id,
            image_tag=image_tag,
        ):
            run_script_host_path = self._resolve_run_script_host_path(workspace_dir=workspace_dir)
            with self._validation_progress_scope(run_script_host_path=run_script_host_path):
                if not self._image_exists(image_tag):
                    build_result = self._build_image(run_id=run_id, workspace_dir=workspace_dir)
                    if build_result.returncode != 0:
                        return ValidationOutcome(
                            passed=False,
                            report=self._failure_report(
                                validator="full",
                                phase="build",
                                code="FULL_DOCKER_BUILD_FAILED",
                                message="docker build failed before full validation could start",
                                evidence=self._command_evidence(build_result),
                                suggested_actions=[
                                    "inspect Dockerfile install steps",
                                    "inspect missing system packages or Python dependencies",
                                    "check whether the selected base image is compatible with the repo runtime",
                                ],
                                image_tag=image_tag,
                            ),
                        )
                deadline = time.monotonic() + self.settings.stage2_full_validation_timeout_seconds

                collect_timeout_seconds, collect_hit_full_timeout = self._bounded_timeout(
                    default_timeout_seconds=self.settings.stage2_collect_timeout_seconds,
                    deadline=deadline,
                )
                if collect_timeout_seconds is None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="full",
                            code="FULL_VALIDATION_TIMEOUT",
                            message="full validation exceeded the configured overall timeout",
                            evidence={
                                "timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                            },
                            suggested_actions=[
                                "increase the full validation timeout when the repository legitimately has many test files",
                                "reduce collected test scope only when that matches the repository's intended unit-test surface",
                            ],
                            image_tag=image_tag,
                        ),
                    )

                collect_result = self._run_collect(
                    image_tag,
                    cancel_requested=cancel_requested,
                    timeout_seconds=collect_timeout_seconds,
                )
                if collect_result.timed_out and collect_hit_full_timeout:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="full",
                            code="FULL_VALIDATION_TIMEOUT",
                            message="full validation exceeded the configured overall timeout while collecting tests",
                            evidence={
                                **self._command_evidence(collect_result),
                                "timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                            },
                            suggested_actions=[
                                "increase the full validation timeout when the repository legitimately has many test files",
                                "inspect whether collect work can be made more deterministic and faster",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                collect_payload = self._maybe_load_json_from_stdout(collect_result)
                if collect_payload is None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="collect",
                            code="FULL_COLLECT_FAILED",
                            message="full validation could not collect test files from the built image",
                            evidence={
                                **self._command_evidence(collect_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "re-run smoke validation before full validation",
                                "inspect whether collect results are stable across fresh containers",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                collect_error = self._validate_collect_payload(collect_payload)
                if collect_error is not None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="collect",
                            code="FULL_COLLECT_INVALID_SCHEMA",
                            message=collect_error,
                            evidence={
                                **self._command_evidence(collect_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "ensure collect payload stays stable between smoke and full validation",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                path_timeout_seconds, path_hit_full_timeout = self._bounded_timeout(
                    default_timeout_seconds=self.settings.stage2_collect_timeout_seconds,
                    deadline=deadline,
                )
                if path_timeout_seconds is None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="full",
                            code="FULL_VALIDATION_TIMEOUT",
                            message="full validation exceeded the configured overall timeout before checking collected paths",
                            evidence={
                                "timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                            },
                            suggested_actions=[
                                "increase the full validation timeout when the repository legitimately has many test files",
                                "inspect whether collect work can be made more deterministic and faster",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                path_check_result = self._validate_collect_paths_exist(
                    image_tag,
                    collect_payload,
                    cancel_requested=cancel_requested,
                    timeout_seconds=path_timeout_seconds,
                )
                if path_check_result is not None:
                    if path_check_result.timed_out and path_hit_full_timeout:
                        return ValidationOutcome(
                            passed=False,
                            report=self._failure_report(
                                validator="full",
                                phase="full",
                                code="FULL_VALIDATION_TIMEOUT",
                                message="full validation exceeded the configured overall timeout while checking collected paths",
                                evidence={
                                    **self._command_evidence(path_check_result),
                                    "timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                                },
                                suggested_actions=[
                                    "increase the full validation timeout when the repository legitimately has many test files",
                                    "inspect whether collect work can be made more deterministic and faster",
                                ],
                                image_tag=image_tag,
                            ),
                        )
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="collect",
                            code="FULL_COLLECT_PATHS_NOT_REPOSITORY_RELATIVE",
                            message=(
                                "full validation found collected test file paths that do not exist under "
                                "/workspace/repo; collect paths must remain repository-root-relative"
                            ),
                            evidence={
                                **self._command_evidence(path_check_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "re-run smoke validation before full validation",
                                "ensure collect paths are repository-root-relative file paths",
                                "if tests run from a subdirectory, convert the repo-root path inside run_script.sh before invoking the test framework",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                collect_payload = self._collect_payload_with_p2p_limit(collect_payload, run_id=run_id)
                if not list(collect_payload.get("test_files") or []):
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="collect",
                            code="FULL_COLLECT_ZERO",
                            message="full validation collected zero test files",
                            evidence={
                                **self._command_evidence(collect_result),
                                "payload": collect_payload,
                            },
                            suggested_actions=[
                                "re-run smoke validation before full validation",
                                "inspect whether collect is filtering out the intended unit-test surface",
                            ],
                            image_tag=image_tag,
                        ),
                    )

                file_results = []
                total_files = len(list(collect_payload.get("test_files") or []))
                for index, item in enumerate(collect_payload["test_files"], start=1):
                    test_file_path = _collect_test_file_path(item)
                    target_selector = _collect_target_selector(item)
                    with self._validation_progress_scope(
                        current_test_file=test_file_path,
                        current_target_selector=target_selector,
                        current_test_index=index,
                        current_test_total=total_files,
                    ):
                        file_results.append(
                            self._run_test_file_with_full_timeout(
                                image_tag=image_tag,
                                test_file_path=test_file_path,
                                target_selector=target_selector,
                                deadline=deadline,
                                cancel_requested=cancel_requested,
                            )
                        )
                timeout_result = next((item for item in file_results if bool(item.get("full_timeout"))), None)
                if timeout_result is not None:
                    return ValidationOutcome(
                        passed=False,
                        report=self._failure_report(
                            validator="full",
                            phase="full",
                            code="FULL_VALIDATION_TIMEOUT",
                            message="full validation exceeded the configured overall timeout while running collected test files",
                            evidence={
                                "timeout_seconds": self.settings.stage2_full_validation_timeout_seconds,
                                "timed_out_test_file": timeout_result.get("test_file_path"),
                                "command": dict(timeout_result.get("command") or {}),
                            },
                            suggested_actions=[
                                "increase the full validation timeout when the repository legitimately has many test files",
                                "inspect whether the slow test file can be made faster or isolated by the repository's own test tooling",
                            ],
                            image_tag=image_tag,
                        ),
                    )
                normalized_results = [self._normalize_file_result(item) for item in file_results]
                passed_files = sum(1 for item in normalized_results if item["result"]["status"] == "passed")
                total_files = len(normalized_results)
                total_tests = sum(int(item["result"]["summary"]["collected"]) for item in normalized_results)
                passed_tests = sum(int(item["result"]["summary"]["passed"]) for item in normalized_results)
                failed_tests = sum(int(item["result"]["summary"]["failed"]) for item in normalized_results)
                error_tests = sum(int(item["result"]["summary"]["errors"]) for item in normalized_results)
                skipped_tests = sum(int(item["result"]["summary"]["skipped"]) for item in normalized_results)
                validation_passed = passed_files > 0
                return ValidationOutcome(
                    passed=validation_passed,
                    report={
                        "schema_version": 1,
                        "validator": "full",
                        "status": "passed" if validation_passed else "failed",
                        "phase": "full",
                        "image_tag": image_tag,
                        "collect": {
                            "command": collect_result.args,
                            "returncode": collect_result.returncode,
                            "payload": collect_payload,
                        },
                        "summary": {
                            "total_files": total_files,
                            "passed_files": passed_files,
                            "failed_files": total_files - passed_files,
                            "total_tests": total_tests,
                            "passed_tests": passed_tests,
                            "failed_tests": failed_tests,
                            "error_tests": error_tests,
                            "skipped_tests": skipped_tests,
                        },
                        "file_results": normalized_results,
                    },
                )

    def _build_image(self, *, run_id: str, workspace_dir: Path) -> CommandResult:
        if shutil.which("docker") is None:
            raise Stage2ValidationError("docker is required for stage2 validation")
        image_tag = self._image_tag(run_id)
        dockerfile_path = workspace_dir / "Dockerfile"
        with dockerfile_with_mirrored_from_images(
            dockerfile_path,
            enabled=should_use_china_docker_mirrors(),
            log_callback=lambda line: self._emit_validation_event(
                title="Validate build output",
                message=line,
                payload={"stage": "build", "image_tag": image_tag},
            ),
        ) as build_dockerfile_path:
            command = [
                "docker",
                "build",
                "--progress=plain",
                "--tag",
                image_tag,
                "--file",
                str(build_dockerfile_path),
                str(workspace_dir),
            ]
            return self._run_command_with_progress_events(
                command,
                timeout_seconds=self.settings.stage2_build_timeout_seconds,
                stage="build",
                start_title="Validate build started",
                output_title="Validate build output",
                completed_title="Validate build completed",
                start_message=f"Building validator image {image_tag}",
                completed_label="validate build",
                extra_payload={
                    "image_tag": image_tag,
                },
            )

    def _image_exists(self, image_tag: str) -> bool:
        if shutil.which("docker") is None:
            raise Stage2ValidationError("docker is required for stage2 validation")
        completed = subprocess.run(
            ["docker", "image", "inspect", image_tag],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0

    def _run_collect(
        self,
        image_tag: str,
        *,
        cancel_requested: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult:
        validator_kind = str(self._progress_context.get("validator") or "smoke")
        stage_label = "Host full collect command" if validator_kind == "full" else "Validate collect command"
        return self._run_injected_script_command(
            image_tag=image_tag,
            shell_command=(
                "/workspace/run_script.sh --action collect --out /tmp/collect.json; "
                'rc=$?; if [ -s /tmp/collect.json ]; then cat /tmp/collect.json; fi; exit "$rc"'
            ),
            timeout_seconds=timeout_seconds or self.settings.stage2_collect_timeout_seconds,
            stage="collect",
            start_title=f"{stage_label} started",
            output_title=f"{stage_label} output",
            completed_title=f"{stage_label} completed",
            start_message=f"Running collect in image {image_tag}",
            completed_label=f"{stage_label.lower()} command",
            cancel_requested=cancel_requested,
            extra_payload={
                "image_tag": image_tag,
            },
        )

    def _run_test_file(
        self,
        image_tag: str,
        test_file_path: str,
        *,
        target_selector: str | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        resolved_target_selector = str(target_selector or test_file_path or "").strip()
        current_test_index = self._progress_context.get("current_test_index")
        current_test_total = self._progress_context.get("current_test_total")
        validator_kind = str(self._progress_context.get("validator") or "smoke")
        title_prefix = "Host full test file" if validator_kind == "full" else "Validate sample test"
        progress_label = ""
        if validator_kind == "full" and current_test_index is not None and current_test_total is not None:
            progress_label = f"第 {int(current_test_index)}/{int(current_test_total)} 个文件"
        elif current_test_index is not None and current_test_total is not None:
            progress_label = f"sample {int(current_test_index)}/{int(current_test_total)}"

        if progress_label:
            start_message = f"{progress_label}：{test_file_path}"
            completed_label = f"{title_prefix.lower()} {progress_label}"
        else:
            start_message = f"Running {test_file_path}"
            completed_label = f"{title_prefix.lower()} command"

        result = self._run_injected_script_command(
            image_tag=image_tag,
            shell_command=(
                "/workspace/run_script.sh "
                f"--action run --target-selector {shlex.quote(resolved_target_selector)} --out /tmp/run.json "
                '; rc=$?; if [ -s /tmp/run.json ]; then cat /tmp/run.json; fi; exit "$rc"'
            ),
            timeout_seconds=timeout_seconds or self.settings.stage2_run_test_timeout_seconds,
            stage="run",
            start_title=f"{title_prefix} started",
            output_title=f"{title_prefix} output",
            completed_title=f"{title_prefix} completed",
            start_message=start_message,
            completed_label=completed_label,
            cancel_requested=cancel_requested,
            extra_payload={
                "image_tag": image_tag,
                "test_file_path": test_file_path,
                "target_selector": resolved_target_selector,
                "test_file_index": current_test_index,
                "test_file_total": current_test_total,
            },
        )
        payload = self._maybe_load_json_from_stdout(result)
        schema_error = None if payload is None else self._validate_run_payload(payload)
        return {
            "test_file_path": test_file_path,
            "target_selector": resolved_target_selector,
            "command": self._command_evidence(result),
            "payload": payload,
            "schema_error": schema_error,
            "result": payload if schema_error is None else None,
        }

    def _run_collected_test_file(
        self,
        image_tag: str,
        *,
        test_file_path: str,
        target_selector: str,
        cancel_requested: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        parameters = inspect.signature(self._run_test_file).parameters
        if "target_selector" in parameters:
            result = self._run_test_file(
                image_tag,
                test_file_path,
                target_selector=target_selector,
                cancel_requested=cancel_requested,
                timeout_seconds=timeout_seconds,
            )
        else:
            result = self._run_test_file(
                image_tag,
                test_file_path,
                cancel_requested=cancel_requested,
                timeout_seconds=timeout_seconds,
            )
        result.setdefault("test_file_path", test_file_path)
        result.setdefault("target_selector", target_selector or test_file_path)
        return result

    def _validate_collect_paths_exist(
        self,
        image_tag: str,
        collect_payload: dict[str, Any],
        *,
        cancel_requested: Callable[[], bool] | None = None,
        timeout_seconds: float | None = None,
    ) -> CommandResult | None:
        test_files = list(collect_payload.get("test_files") or [])
        paths = [str(item.get("path") or "") for item in test_files if isinstance(item, dict)]
        if not paths:
            return None
        timeout = timeout_seconds or self.settings.stage2_collect_timeout_seconds
        script = r"""
if [ ! -d /workspace/repo ]; then
  echo "/workspace/repo is missing from validator image" >&2
  exit 1
fi
missing=0
shown=0
while IFS= read -r path; do
  if [ -z "$path" ]; then
    continue
  fi
  if [ ! -f "/workspace/repo/$path" ]; then
    missing=$((missing + 1))
    if [ "$shown" -lt 50 ]; then
      printf 'missing: %s\n' "$path" >&2
      shown=$((shown + 1))
    fi
  fi
done < /tmp/feature_factory_collect_paths.txt
if [ "$missing" -ne 0 ]; then
  printf 'collect emitted %s path(s) that do not exist under /workspace/repo\n' "$missing" >&2
  exit 1
fi
""".strip()

        with tempfile.TemporaryDirectory(prefix="feature-factory-stage2-paths-") as temp_dir:
            paths_file = Path(temp_dir) / "collect_paths.txt"
            paths_file.write_text("\n".join(paths) + "\n", encoding="utf-8")
            container_id = ""
            try:
                create_command = [
                    "docker",
                    "create",
                    "--rm",
                    image_tag,
                    "sh",
                    "-lc",
                    script,
                ]
                create_result = self._run_command(
                    create_command,
                    timeout_seconds=min(timeout, 30.0),
                    cancel_requested=cancel_requested,
                )
                if create_result.returncode != 0 or create_result.timed_out:
                    return create_result
                container_id = self._extract_container_id(create_result.stdout)
                if not container_id:
                    return CommandResult(
                        args=create_command,
                        returncode=1,
                        stdout=create_result.stdout,
                        stderr="docker create did not return a container id",
                        timed_out=False,
                    )
                copy_result = self._run_command(
                    [
                        "docker",
                        "cp",
                        str(paths_file),
                        f"{container_id}:/tmp/feature_factory_collect_paths.txt",
                    ],
                    timeout_seconds=min(timeout, 30.0),
                    cancel_requested=cancel_requested,
                )
                if copy_result.returncode != 0 or copy_result.timed_out:
                    return copy_result
                result = self._run_command(
                    ["docker", "start", "-a", container_id],
                    timeout_seconds=timeout,
                    cancel_requested=cancel_requested,
                )
                if result.returncode != 0 or result.timed_out:
                    return result
                return None
            finally:
                if container_id:
                    self._force_remove_container(container_id)

    def _run_test_file_with_full_timeout(
        self,
        *,
        image_tag: str,
        test_file_path: str,
        target_selector: str | None = None,
        deadline: float,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        resolved_target_selector = str(target_selector or test_file_path or "").strip()
        timeout_seconds, hit_full_timeout = self._bounded_timeout(
            default_timeout_seconds=self.settings.stage2_run_test_timeout_seconds,
            deadline=deadline,
        )
        if timeout_seconds is None:
            return {
                "test_file_path": test_file_path,
                "target_selector": resolved_target_selector,
                "command": {
                    "command": [],
                    "returncode": 124,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": True,
                },
                "payload": None,
                "schema_error": "full validation exceeded the configured overall timeout",
                "result": None,
                "full_timeout": True,
            }
        result = self._run_collected_test_file(
            image_tag,
            test_file_path=test_file_path,
            target_selector=resolved_target_selector,
            cancel_requested=cancel_requested,
            timeout_seconds=timeout_seconds,
        )
        result["full_timeout"] = bool(result["command"]["timed_out"] and hit_full_timeout)
        return result

    def _bounded_timeout(
        self,
        *,
        default_timeout_seconds: float,
        deadline: float,
    ) -> tuple[float | None, bool]:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            return None, True
        bounded = min(default_timeout_seconds, remaining)
        return bounded, bounded < default_timeout_seconds

    @contextmanager
    def _validation_progress_scope(self, **updates: Any):
        previous = dict(self._progress_context)
        self._progress_context.update(updates)
        try:
            yield
        finally:
            self._progress_context = previous

    def _emit_validation_event(
        self,
        *,
        title: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        emit_event = self._progress_context.get("emit_event")
        if emit_event is None:
            return
        emit_event(title, message, dict(payload or {}))

    def _current_progress_payload(self, *, stage: str, extra_payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "validator": self._progress_context.get("validator"),
            "stage": stage,
            "attempt_index": self._progress_context.get("attempt_index"),
            "image_tag": self._progress_context.get("image_tag"),
            "test_file_path": self._progress_context.get("current_test_file"),
            "target_selector": self._progress_context.get("current_target_selector"),
            "test_file_index": self._progress_context.get("current_test_index"),
            "test_file_total": self._progress_context.get("current_test_total"),
        }
        if extra_payload:
            payload.update(extra_payload)
        return {key: value for key, value in payload.items() if value not in (None, "")}

    def _progress_operation(self, *, stage: str) -> str:
        validator_kind = str(self._progress_context.get("validator") or "validator")
        attempt_index = self._progress_context.get("attempt_index")
        current_test_file = str(self._progress_context.get("current_test_file") or "").strip()
        parts = ["validate", validator_kind, stage]
        if attempt_index is not None and int(attempt_index or 0) > 0:
            parts.append(f"attempt{int(attempt_index):03d}")
        if current_test_file:
            parts.append(hashlib.sha1(current_test_file.encode("utf-8")).hexdigest()[:10])
        return "_".join(parts)

    def _make_command_output_callback(
        self,
        *,
        title: str,
        operation: str,
        base_payload: dict[str, Any],
    ) -> tuple[Callable[[str, str], None], Callable[[], None]]:
        pending_lines: list[str] = []
        last_emit_at = 0.0

        def flush(*, force: bool) -> None:
            nonlocal last_emit_at, pending_lines
            if not pending_lines:
                return
            if not force and len(pending_lines) < 4 and (time.monotonic() - last_emit_at) < 0.35:
                return
            self._emit_validation_event(
                title=title,
                message=pending_lines[-1],
                payload={
                    **base_payload,
                    "operation": operation,
                    "tail_lines": list(pending_lines),
                },
            )
            pending_lines = []
            last_emit_at = time.monotonic()

        def on_output(stream_name: str, chunk: str) -> None:
            nonlocal pending_lines
            normalized = str(chunk or "").replace("\r", "\n")
            for line in normalized.split("\n"):
                if not line:
                    continue
                pending_lines.append(f"{stream_name}: {line}")
                if len(pending_lines) > 12:
                    pending_lines = pending_lines[-12:]
            flush(force=False)

        return on_output, lambda: flush(force=True)

    def _run_command_with_progress_events(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        stage: str,
        start_title: str,
        output_title: str,
        completed_title: str,
        start_message: str,
        completed_label: str,
        cancel_requested: Callable[[], bool] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> CommandResult:
        base_payload = self._current_progress_payload(stage=stage, extra_payload=extra_payload)
        operation = self._progress_operation(stage=stage)
        self._emit_validation_event(
            title=start_title,
            message=start_message,
            payload={
                **base_payload,
                "parent_operation": operation,
                "command": list(args),
            },
        )
        on_output, flush_output = self._make_command_output_callback(
            title=output_title,
            operation=operation,
            base_payload=base_payload,
        )
        try:
            result = self._run_command(
                args,
                timeout_seconds=timeout_seconds,
                cancel_requested=cancel_requested,
                on_output=on_output,
            )
        except Stage2ValidationInterrupted:
            flush_output()
            self._emit_validation_event(
                title=completed_title,
                message=f"{completed_label} interrupted",
                payload={
                    **base_payload,
                    "operation": operation,
                    "returncode": 130,
                    "timed_out": False,
                    "interrupted": True,
                },
            )
            raise
        flush_output()
        completion_message = (
            f"{completed_label} timed out"
            if result.timed_out
            else f"{completed_label} completed with exit code {result.returncode}"
        )
        self._emit_validation_event(
            title=completed_title,
            message=completion_message,
            payload={
                **base_payload,
                "operation": operation,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
            },
        )
        return result

    def _run_injected_script_command(
        self,
        *,
        image_tag: str,
        shell_command: str,
        timeout_seconds: float,
        stage: str,
        start_title: str,
        output_title: str,
        completed_title: str,
        start_message: str,
        completed_label: str,
        cancel_requested: Callable[[], bool] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> CommandResult:
        run_script_host_path = self._progress_context.get("run_script_host_path")
        if not run_script_host_path:
            raise Stage2ValidationError("run_script.sh host path is unavailable for validator injection")
        run_script_host_path = Path(str(run_script_host_path))
        if not run_script_host_path.is_file():
            raise Stage2ValidationError(
                f"run_script.sh host path does not exist for validator injection: {run_script_host_path}"
            )
        base_payload = self._current_progress_payload(stage=stage, extra_payload=extra_payload)
        operation = self._progress_operation(stage=stage)
        self._emit_validation_event(
            title=start_title,
            message=start_message,
            payload={
                **base_payload,
                "parent_operation": operation,
                "command": ["sh", "-lc", shell_command],
                "injected_run_script_path": "/workspace/run_script.sh",
            },
        )
        on_output, flush_output = self._make_command_output_callback(
            title=output_title,
            operation=operation,
            base_payload=base_payload,
        )
        container_id = ""
        result: CommandResult | None = None
        try:
            create_command = [
                "docker",
                "create",
                "--rm",
                image_tag,
                "sh",
                "-lc",
                f"chmod +x /workspace/run_script.sh && {shell_command}",
            ]
            create_result = self._run_command(
                create_command,
                timeout_seconds=min(timeout_seconds, 30.0),
                cancel_requested=cancel_requested,
            )
            if create_result.returncode != 0 or create_result.timed_out:
                result = create_result
            else:
                container_id = self._extract_container_id(create_result.stdout)
                if not container_id:
                    result = CommandResult(
                        args=create_command,
                        returncode=1,
                        stdout=create_result.stdout,
                        stderr="docker create did not return a container id",
                        timed_out=False,
                    )
                else:
                    copy_command = [
                        "docker",
                        "cp",
                        str(run_script_host_path),
                        f"{container_id}:/workspace/run_script.sh",
                    ]
                    copy_result = self._run_command(
                        copy_command,
                        timeout_seconds=min(timeout_seconds, 30.0),
                        cancel_requested=cancel_requested,
                    )
                    if copy_result.returncode != 0 or copy_result.timed_out:
                        result = copy_result
                    else:
                        result = self._run_command(
                            ["docker", "start", "-a", container_id],
                            timeout_seconds=timeout_seconds,
                            cancel_requested=cancel_requested,
                            on_output=on_output,
                        )
        except Stage2ValidationInterrupted:
            flush_output()
            self._emit_validation_event(
                title=completed_title,
                message=f"{completed_label} interrupted",
                payload={
                    **base_payload,
                    "operation": operation,
                    "returncode": 130,
                    "timed_out": False,
                    "interrupted": True,
                },
            )
            raise
        finally:
            flush_output()
            if container_id:
                self._force_remove_container(container_id)

        if result is None:
            result = CommandResult(args=[], returncode=1, stdout="", stderr="validator command did not run")
        completion_message = (
            f"{completed_label} timed out"
            if result.timed_out
            else f"{completed_label} completed with exit code {result.returncode}"
        )
        self._emit_validation_event(
            title=completed_title,
            message=completion_message,
            payload={
                **base_payload,
                "operation": operation,
                "returncode": result.returncode,
                "timed_out": result.timed_out,
            },
        )
        return result

    def _run_command(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool] | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        if cancel_requested is None and on_output is None:
            return self._run_command_with_timeout(args, timeout_seconds=timeout_seconds)
        return self._run_command_streaming(
            args,
            timeout_seconds=timeout_seconds,
            cancel_requested=cancel_requested,
            on_output=on_output,
        )

    def _run_command_streaming(
        self,
        args: list[str],
        *,
        timeout_seconds: float,
        cancel_requested: Callable[[], bool] | None = None,
        on_output: Callable[[str, str], None] | None = None,
    ) -> CommandResult:
        process = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []
        output_queue: queue.Queue[tuple[str, str, bool]] = queue.Queue()

        def _reader(stream_name: str, stream, collector: list[str]) -> None:
            try:
                while True:
                    chunk = stream.readline()
                    if chunk == "":
                        break
                    collector.append(chunk)
                    output_queue.put((stream_name, chunk, False))
            finally:
                try:
                    stream.close()
                except Exception:
                    pass
                output_queue.put((stream_name, "", True))

        if process.stdout is None or process.stderr is None:
            raise Stage2ValidationError("stage2 validator failed to open command pipes")

        reader_threads = [
            threading.Thread(
                target=_reader,
                args=("stdout", process.stdout, stdout_chunks),
                daemon=True,
            ),
            threading.Thread(
                target=_reader,
                args=("stderr", process.stderr, stderr_chunks),
                daemon=True,
            ),
        ]
        for thread in reader_threads:
            thread.start()

        deadline = time.monotonic() + timeout_seconds
        completed_streams = 0
        timed_out = False
        try:
            while completed_streams < len(reader_threads) or process.poll() is None:
                if cancel_requested is not None and cancel_requested():
                    self._terminate_process(process)
                    raise Stage2ValidationInterrupted("stage2 validation interrupted by user")

                remaining = max(deadline - time.monotonic(), 0.0)
                if remaining <= 0.0:
                    self._terminate_process(process)
                    timed_out = True
                    break

                try:
                    stream_name, chunk, stream_done = output_queue.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue
                if stream_done:
                    completed_streams += 1
                    continue
                if on_output is not None:
                    on_output(stream_name, chunk)
        finally:
            for thread in reader_threads:
                thread.join(timeout=1)

        if timed_out:
            return CommandResult(
                args=args,
                returncode=124,
                stdout="".join(stdout_chunks),
                stderr="".join(stderr_chunks),
                timed_out=True,
            )
        return CommandResult(
            args=args,
            returncode=int(process.wait(timeout=5) or 0),
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            timed_out=False,
        )

    def _run_command_with_timeout(self, args: list[str], *, timeout_seconds: float) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
            return CommandResult(
                args=args,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                args=args,
                returncode=124,
                stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "") if isinstance(exc.stderr, str) else "",
                timed_out=True,
            )

    def _resolve_run_script_host_path(
        self,
        *,
        workspace_dir: Path,
        run_script_text: str | None = None,
    ) -> Path:
        if run_script_text is None:
            host_path = workspace_dir / "run_script.sh"
            if not host_path.is_file():
                raise Stage2ValidationError("run_script.sh artifact is missing from the workspace")
        else:
            runtime_dir = workspace_dir / ".stage2_validator_runtime"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            host_path = runtime_dir / "run_script.sh"
            host_path.write_text(run_script_text, encoding="utf-8")
        host_path.chmod(0o755)
        return host_path

    def _extract_container_id(self, raw_stdout: str) -> str:
        lines = [line.strip() for line in str(raw_stdout or "").splitlines() if line.strip()]
        return lines[-1] if lines else ""

    def _force_remove_container(self, container_id: str) -> None:
        try:
            subprocess.run(
                ["docker", "rm", "-f", container_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=10,
            )
        except Exception:
            pass

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGINT)
            else:
                process.send_signal(signal.SIGINT)
            process.wait(timeout=5)
        except (subprocess.TimeoutExpired, ProcessLookupError):
            if process.poll() is None:
                if hasattr(os, "killpg"):
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=5)

    def _maybe_load_json_from_stdout(self, result: CommandResult) -> dict[str, Any] | None:
        raw = result.stdout.strip()
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _p2p_file_count_limit(self) -> int | None:
        value = getattr(self.settings, "stage2_p2p_file_count_limit", None)
        try:
            limit = int(value)
        except (TypeError, ValueError):
            return None
        return limit if limit > 0 else None

    def _collect_payload_with_p2p_limit(self, payload: dict[str, Any], *, run_id: str) -> dict[str, Any]:
        limit = self._p2p_file_count_limit()
        if limit is None:
            return payload
        test_files = list(payload.get("test_files") or [])
        sample_population = sorted(
            test_files,
            key=lambda item: (
                str((item or {}).get("path") or ""),
                json.dumps(item, ensure_ascii=False, sort_keys=True, default=str),
            ),
        )
        sample_seed = self._p2p_sample_seed(run_id)
        limited_test_files = (
            random.Random(f"{sample_seed}:p2p_file_count_limit").sample(sample_population, k=limit)
            if len(sample_population) > limit
            else sample_population
        )
        return {
            **payload,
            "test_files": limited_test_files,
            "source_test_file_count": len(test_files),
            "p2p_file_count_limit": limit,
            "p2p_sample_seed": sample_seed,
            "truncated_test_file_count": max(len(test_files) - len(limited_test_files), 0),
        }

    def _p2p_sample_seed(self, run_id: str) -> str:
        value = str(getattr(self.settings, "stage2_p2p_sample_seed", "") or "").strip()
        return value or run_id

    def _validate_collect_payload(self, payload: dict[str, Any]) -> str | None:
        if payload.get("action") != "collect":
            return "collect payload has wrong action"
        test_files = payload.get("test_files")
        if not isinstance(test_files, list):
            return "collect payload is missing test_files"
        for item in test_files:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                return "collect payload contains invalid test file entries"
            path_error = _validate_relative_file_path_value(
                _collect_test_file_path(item),
                label="test file path",
                root_label="/workspace/repo",
            )
            if path_error is not None:
                return path_error
            if "target_selector" in item and not isinstance(item.get("target_selector"), str):
                return "collect payload contains invalid target_selector values"
            selector_error = _validate_relative_file_path_value(
                _collect_target_selector(item),
                label="target_selector",
                root_label="the test runner working directory",
            )
            if selector_error is not None:
                return selector_error
        return None

    def _validate_run_payload(self, payload: dict[str, Any]) -> RunPayloadSchemaError | None:
        if payload.get("action") != "run":
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_WRONG_ACTION",
                message="run payload has wrong action",
            )
        if not isinstance(payload.get("status"), str) or not str(payload.get("status") or "").strip():
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_MISSING_STATUS",
                message="run payload is missing status",
            )
        summary = payload.get("summary")
        if not isinstance(summary, dict):
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_MISSING_SUMMARY",
                message="run payload is missing summary",
            )
        for key in ("collected", "passed", "failed", "errors", "skipped"):
            if key not in summary or not isinstance(summary.get(key), int):
                return RunPayloadSchemaError(
                    code="RUN_PAYLOAD_SUMMARY_MISSING_FIELD",
                    message=f"run payload summary is missing integer field {key}",
                )
        collected = int(summary.get("collected") or 0)
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        errors = int(summary.get("errors") or 0)
        skipped = int(summary.get("skipped") or 0)
        if min(collected, passed, failed, errors, skipped) < 0:
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_SUMMARY_NEGATIVE_VALUE",
                message="run payload summary counts must be non-negative integers",
            )
        if collected != passed + failed + errors + skipped:
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_SUMMARY_TOTAL_MISMATCH",
                message=(
                    "run payload summary must satisfy "
                    "collected = passed + failed + errors + skipped"
                ),
            )
        status = str(payload.get("status") or "").strip()
        expected_status = (
            "passed"
            if collected > 0 and passed > 0 and failed == 0 and errors == 0
            else "failed"
        )
        if status != expected_status:
            return RunPayloadSchemaError(
                code="RUN_PAYLOAD_STATUS_SUMMARY_MISMATCH",
                message=(
                    "run payload status conflicts with summary counts: "
                    "status=passed requires collected>0, passed>0, failed=0, errors=0; "
                    "all other valid summaries must use status=failed"
                ),
            )
        return None

    def _command_evidence(self, result: CommandResult) -> dict[str, Any]:
        return {
            "command": result.args,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "timed_out": result.timed_out,
        }

    def _failure_report(
        self,
        *,
        validator: str,
        phase: str,
        code: str,
        message: str,
        evidence: dict[str, Any],
        suggested_actions: list[str],
        image_tag: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "validator": validator,
            "status": "failed",
            "phase": phase,
            "image_tag": image_tag,
            "feedback": {
                "status": "failed",
                "code": code,
                "message": message,
                "evidence": _json_safe_validation_value(evidence),
                "suggested_actions": suggested_actions,
                "retryable": validator == "smoke",
            },
        }

    def _normalize_file_result(self, item: dict[str, Any]) -> dict[str, Any]:
        if bool(item.get("full_timeout")):
            return {
                "test_file_path": item["test_file_path"],
                "target_selector": item.get("target_selector") or item["test_file_path"],
                "command": item["command"]["command"],
                "returncode": item["command"]["returncode"],
                "timed_out": item["command"]["timed_out"],
                "result": {
                    "schema_version": 1,
                    "action": "run",
                    "status": "error",
                    "summary": {
                        "collected": 0,
                        "passed": 0,
                        "failed": 0,
                        "errors": 1,
                        "skipped": 0,
                    },
                    "stdout": item["command"]["stdout"],
                    "stderr": item["command"]["stderr"],
                    "message": "full validation exceeded the configured overall timeout",
                },
            }
        if item["payload"] is not None and item["schema_error"] is None:
            return {
                "test_file_path": item["test_file_path"],
                "target_selector": item.get("target_selector") or item["test_file_path"],
                "command": item["command"]["command"],
                "returncode": item["command"]["returncode"],
                "timed_out": item["command"]["timed_out"],
                "result": item["payload"],
            }
        result_status = "error"
        if item["command"]["timed_out"]:
            message = "test file execution timed out"
        else:
            schema_error = item.get("schema_error")
            message = str(
                getattr(schema_error, "message", "")
                or "test file execution did not emit valid JSON"
            )
        return {
            "test_file_path": item["test_file_path"],
            "target_selector": item.get("target_selector") or item["test_file_path"],
            "command": item["command"]["command"],
            "returncode": item["command"]["returncode"],
            "timed_out": item["command"]["timed_out"],
            "result": {
                "schema_version": 1,
                "action": "run",
                "status": result_status,
                "summary": {
                    "collected": 0,
                    "passed": 0,
                    "failed": 0,
                    "errors": 1,
                    "skipped": 0,
                },
                "stdout": item["command"]["stdout"],
                "stderr": item["command"]["stderr"],
                "message": message,
            },
        }

    def _image_tag(self, run_id: str) -> str:
        return f"{self.settings.stage2_docker_image_prefix}:{run_id}"
