from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from feature_factory.config import Settings
from feature_factory.stage2.validator import (
    CommandResult,
    RunPayloadSchemaError,
    Stage2ValidationInterrupted,
    Stage2Validator,
)


class FakeValidator(Stage2Validator):
    def __init__(self, settings: Settings, *, collect_payload: dict, sample_results: dict[str, dict]) -> None:
        super().__init__(settings)
        self.collect_payload = collect_payload
        self.sample_results = sample_results

    def _build_image(self, *, run_id: str, workspace_dir: Path) -> CommandResult:
        return CommandResult(args=["docker", "build"], returncode=0, stdout="", stderr="")

    def _run_collect(self, image_tag: str, *, cancel_requested=None, timeout_seconds=None) -> CommandResult:  # noqa: ARG002
        return CommandResult(
            args=["docker", "run", "--action", "collect"],
            returncode=0,
            stdout=json.dumps(self.collect_payload),
            stderr="",
        )

    def _run_test_file(self, image_tag: str, test_file_path: str, *, cancel_requested=None, timeout_seconds=None) -> dict:  # noqa: ARG002
        payload = self.sample_results[test_file_path]
        return {
            "test_file_path": test_file_path,
            "command": {
                "command": ["docker", "run", test_file_path],
                "returncode": payload["exit_code"],
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            },
            "payload": payload,
            "schema_error": None,
            "result": payload,
        }

    def _validate_collect_paths_exist(self, image_tag: str, collect_payload: dict, *, cancel_requested=None, timeout_seconds=None):  # noqa: ARG002
        return None


class TimedSampleValidator(FakeValidator):
    def __init__(
        self,
        settings: Settings,
        *,
        collect_payload: dict,
        sample_results: dict[str, dict],
        timed_out_files: set[str],
    ) -> None:
        super().__init__(settings, collect_payload=collect_payload, sample_results=sample_results)
        self.timed_out_files = set(timed_out_files)

    def _run_test_file(self, image_tag: str, test_file_path: str, *, cancel_requested=None, timeout_seconds=None) -> dict:  # noqa: ARG002
        if test_file_path in self.timed_out_files:
            return {
                "test_file_path": test_file_path,
                "command": {
                    "command": ["docker", "run", test_file_path],
                    "returncode": 124,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": True,
                },
                "payload": None,
                "schema_error": None,
                "result": None,
            }
        return super()._run_test_file(
            image_tag,
            test_file_path,
            cancel_requested=cancel_requested,
            timeout_seconds=timeout_seconds,
        )


class NonzeroCollectValidator(FakeValidator):
    def __init__(
        self,
        settings: Settings,
        *,
        collect_payload: dict,
        collect_returncode: int,
        sample_results: dict[str, dict],
    ) -> None:
        super().__init__(settings, collect_payload=collect_payload, sample_results=sample_results)
        self.collect_returncode = collect_returncode

    def _run_collect(self, image_tag: str, *, cancel_requested=None, timeout_seconds=None) -> CommandResult:  # noqa: ARG002
        return CommandResult(
            args=["docker", "run", "--action", "collect"],
            returncode=self.collect_returncode,
            stdout=json.dumps(self.collect_payload),
            stderr="collect command exited non-zero after writing JSON",
        )


class InvalidSampleSchemaValidator(FakeValidator):
    def _run_test_file(self, image_tag: str, test_file_path: str, *, cancel_requested=None, timeout_seconds=None) -> dict:  # noqa: ARG002
        payload = self.sample_results[test_file_path]
        return {
            "test_file_path": test_file_path,
            "command": {
                "command": ["docker", "run", test_file_path],
                "returncode": payload["exit_code"],
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            },
            "payload": payload,
            "schema_error": RunPayloadSchemaError(
                code="RUN_PAYLOAD_STATUS_SUMMARY_MISMATCH",
                message="run payload status conflicts with summary counts",
            ),
            "result": None,
        }


class MissingCollectPathValidator(FakeValidator):
    def _validate_collect_paths_exist(self, image_tag: str, collect_payload: dict, *, cancel_requested=None, timeout_seconds=None):  # noqa: ARG002
        return CommandResult(
            args=["docker", "start", "-a", "collect-path-check"],
            returncode=1,
            stdout="",
            stderr=(
                "missing: unit/lm/test_counter.py\n"
                "collect emitted 1 path(s) that do not exist under /workspace/repo\n"
            ),
        )


def _run_payload(status: str, *, exit_code: int, passed: int, failed: int) -> dict:
    return {
        "schema_version": 1,
        "action": "run",
        "status": status,
        "exit_code": exit_code,
        "summary": {
            "collected": passed + failed,
            "passed": passed,
            "failed": failed,
            "errors": 0,
            "skipped": 0,
        },
    }


def test_stage2_validator_returns_structured_schema_feedback(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-schema.db'}")
    validator = Stage2Validator(settings)

    outcome = validator.run_smoke(
        run_id="run-1",
        workspace_dir=tmp_path,
        dockerfile_text="",
        run_script_text="#!/usr/bin/env bash\n",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert outcome.report["feedback"]["retryable"] is True


def test_stage2_validator_runs_target_selector_but_reports_repo_path(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-target-selector.db'}")
    validator = Stage2Validator(settings)
    captured: dict[str, object] = {}

    def fake_run_injected_script_command(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=json.dumps(
                {
                    "action": "run",
                    "status": "passed",
                    "summary": {"collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(validator, "_run_injected_script_command", fake_run_injected_script_command)

    result = validator._run_test_file(  # noqa: SLF001
        "feature-factory/test:image",
        "nltk/test/unit/lm/test_counter.py",
        target_selector="unit/lm/test_counter.py",
    )

    assert "--target-selector unit/lm/test_counter.py" in str(captured["shell_command"])
    assert "--target-selector nltk/test/unit/lm/test_counter.py" not in str(captured["shell_command"])
    assert captured["extra_payload"]["test_file_path"] == "nltk/test/unit/lm/test_counter.py"  # type: ignore[index]
    assert captured["extra_payload"]["target_selector"] == "unit/lm/test_counter.py"  # type: ignore[index]
    assert result["test_file_path"] == "nltk/test/unit/lm/test_counter.py"
    assert result["target_selector"] == "unit/lm/test_counter.py"


def test_stage2_validator_shell_quotes_target_selector(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-target-selector-quote.db'}")
    validator = Stage2Validator(settings)
    captured: dict[str, object] = {}

    def fake_run_injected_script_command(**kwargs):  # noqa: ANN001
        captured.update(kwargs)
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=json.dumps(
                {
                    "action": "run",
                    "status": "passed",
                    "summary": {"collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0},
                }
            ),
            stderr="",
        )

    monkeypatch.setattr(validator, "_run_injected_script_command", fake_run_injected_script_command)

    validator._run_test_file(  # noqa: SLF001
        "feature-factory/test:image",
        "tests/test_safe_name.py",
        target_selector="unit/lm/test_$(touch /tmp/stage2-pwn).py",
    )

    shell_command = str(captured["shell_command"])
    assert "--target-selector 'unit/lm/test_$(touch /tmp/stage2-pwn).py'" in shell_command
    assert "--target-selector unit/lm/test_$(touch /tmp/stage2-pwn).py" not in shell_command


def test_stage2_validator_collect_accepts_relative_target_selector(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-target-selector-schema.db'}")
    validator = Stage2Validator(settings)

    assert validator._validate_collect_payload(  # noqa: SLF001
        {
            "action": "collect",
            "test_files": [
                {
                    "path": "nltk/test/unit/lm/test_counter.py",
                    "target_selector": "unit/lm/test_counter.py",
                }
            ],
        }
    ) is None
    assert "target_selector" in (
        validator._validate_collect_payload(  # noqa: SLF001
            {
                "action": "collect",
                "test_files": [
                    {
                        "path": "nltk/test/unit/lm/test_counter.py",
                        "target_selector": "../unit/lm/test_counter.py",
                    }
                ],
            }
        )
        or ""
    )


def test_stage2_validator_reports_status_summary_mismatch_with_generic_feedback(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-run-mismatch.db'}")
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = Stage2Validator(settings)
    containers: dict[str, str] = {}
    next_container_id = 0

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        nonlocal next_container_id
        command_text = " ".join(args)
        if args[:2] == ["docker", "build"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "create"]:
            next_container_id += 1
            container_id = f"container-{next_container_id}"
            if "--action collect" in command_text:
                containers[container_id] = "collect"
            elif "--target-selector tests/test_demo.py" in command_text:
                containers[container_id] = "run:tests/test_demo.py"
            else:
                raise AssertionError(f"unexpected create command: {args}")
            return CommandResult(args=args, returncode=0, stdout=f"{container_id}\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            container_kind = containers[args[-1]]
            if container_kind == "collect":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "collect",
                            "test_files": [{"path": "tests/test_demo.py"}],
                        }
                    ),
                    stderr="",
                )
            if container_kind == "run:tests/test_demo.py":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "run",
                            "status": "passed",
                            "exit_code": 0,
                            "summary": {
                                "collected": 3,
                                "passed": 0,
                                "failed": 0,
                                "errors": 0,
                                "skipped": 3,
                            },
                        }
                    ),
                    stderr="",
                )
        raise AssertionError(f"unexpected command: {args}")

    validator._run_command = fake_run_command  # type: ignore[method-assign]
    validator._validate_collect_paths_exist = lambda *args, **kwargs: None  # type: ignore[method-assign]  # noqa: ARG005

    outcome = validator.run_smoke(
        run_id="run-status-summary-mismatch",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "RUN_PAYLOAD_STATUS_SUMMARY_MISMATCH"
    assert "status=passed requires collected>0, passed>0, failed=0, errors=0" in outcome.report["feedback"]["message"]
    assert outcome.report["feedback"]["suggested_actions"] == [
        "recompute summary counts from structured test results instead of free-form text",
        "normalize framework-specific outcomes into passed, failed, errors, or skipped",
        "ensure status matches the summary counts for the selected test file",
    ]


def test_stage2_validator_smoke_rejects_collect_paths_missing_under_repo_root(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-collect-paths.db'}")
    validator = MissingCollectPathValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [{"path": "unit/lm/test_counter.py"}],
        },
        sample_results={},
    )

    outcome = validator.run_smoke(
        run_id="run-missing-collect-path",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "TEST_COLLECT_PATHS_NOT_REPOSITORY_RELATIVE"
    assert "repository-root-relative" in outcome.report["feedback"]["message"]
    assert "unit/lm/test_counter.py" in outcome.report["feedback"]["evidence"]["stderr"]


def test_stage2_validator_collect_schema_rejects_unsafe_paths(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-collect-path-schema.db'}")
    validator = Stage2Validator(settings)

    assert (
        validator._validate_collect_payload(  # noqa: SLF001
            {"action": "collect", "test_files": [{"path": "/tests/test_a.py"}]}
        )
        == "collect payload test file paths must be relative to /workspace/repo"
    )
    assert (
        validator._validate_collect_payload(  # noqa: SLF001
            {"action": "collect", "test_files": [{"path": "tests/../test_a.py"}]}
        )
        == "collect payload test file paths must not contain parent-directory traversal"
    )


def test_stage2_validator_rejects_context_repo_copy(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-context-copy.db'}")
    validator = Stage2Validator(settings)

    outcome = validator.run_smoke(
        run_id="run-context-copy",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\nCOPY repo /workspace/repo\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert "local build-context files" in outcome.report["feedback"]["message"]


def test_stage2_validator_rejects_context_run_script_copy(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-context-script-copy.db'}")
    validator = Stage2Validator(settings)

    outcome = validator.run_smoke(
        run_id="run-context-script-copy",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\nCOPY run_script.sh /workspace/run_script.sh\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "ARTIFACT_SCHEMA_INVALID"
    assert "inject run_script.sh" in outcome.report["feedback"]["message"]


def test_stage2_validator_smoke_uses_half_pass_threshold(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-smoke.db'}",
        stage2_quickcheck_sample_size=3,
    )
    validator = FakeValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
                {"path": "tests/test_b.py"},
                {"path": "tests/test_c.py"},
            ],
        },
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=2, failed=0),
            "tests/test_b.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
            "tests/test_c.py": _run_payload("failed", exit_code=1, passed=0, failed=1),
        },
    )

    outcome = validator.run_smoke(
        run_id="run-2",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is True
    assert outcome.report["sample_size"] == 3
    assert outcome.report["passed_files"] == 2
    assert outcome.report["status"] == "passed"


def test_stage2_validator_smoke_uses_collect_json_when_exit_code_is_nonzero(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-collect-nonzero.db'}",
        stage2_quickcheck_sample_size=1,
    )
    validator = NonzeroCollectValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
            ],
        },
        collect_returncode=17,
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        },
    )

    outcome = validator.run_smoke(
        run_id="run-collect-nonzero",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is True
    assert outcome.report["collect"]["returncode"] == 17
    assert outcome.report["collect"]["payload"]["test_files"] == [{"path": "tests/test_a.py"}]


def test_stage2_validator_smoke_calls_collect_hook_after_collect_schema_validation(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-collect-hook.db'}",
        stage2_quickcheck_sample_size=1,
    )
    collect_payload = {
        "schema_version": 1,
        "action": "collect",
        "test_files": [
            {"path": "tests/test_a.py"},
        ],
    }
    validator = FakeValidator(
        settings,
        collect_payload=collect_payload,
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        },
    )

    hook_calls: list[dict] = []
    outcome = validator.run_smoke(
        run_id="run-collect-hook",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
        on_collect_validated=lambda payload: hook_calls.append(dict(payload)),
    )

    assert outcome.passed is True
    assert hook_calls == [collect_payload]


def test_stage2_validator_smoke_limits_collected_p2p_files_before_sampling(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-smoke-p2p-limit.db'}",
        stage2_quickcheck_sample_size=10,
        stage2_p2p_file_count_limit=2,
    )
    collect_payload = {
        "schema_version": 1,
        "action": "collect",
        "test_files": [
            {"path": "tests/test_a.py"},
            {"path": "tests/test_b.py"},
            {"path": "tests/test_c.py"},
        ],
    }
    validator = FakeValidator(
        settings,
        collect_payload=collect_payload,
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
            "tests/test_b.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        },
    )

    hook_calls: list[dict] = []
    outcome = validator.run_smoke(
        run_id="run-smoke-p2p-limit",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
        on_collect_validated=lambda payload: hook_calls.append(dict(payload)),
    )

    assert outcome.passed is True
    assert outcome.report["sample_size"] == 2
    assert sorted(item["test_file_path"] for item in outcome.report["sampled_results"]) == [
        "tests/test_a.py",
        "tests/test_b.py",
    ]
    limited_collect = outcome.report["collect"]["payload"]
    assert [item["path"] for item in limited_collect["test_files"]] == [
        "tests/test_b.py",
        "tests/test_a.py",
    ]
    assert limited_collect["source_test_file_count"] == 3
    assert limited_collect["p2p_file_count_limit"] == 2
    assert limited_collect["p2p_sample_seed"] == "run-smoke-p2p-limit"
    assert limited_collect["truncated_test_file_count"] == 1
    assert hook_calls == [limited_collect]


def test_stage2_validator_smoke_failure_report_is_json_serializable_for_schema_errors(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-smoke-schema-json.db'}",
        stage2_quickcheck_sample_size=1,
    )
    validator = InvalidSampleSchemaValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
            ],
        },
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=0, failed=0),
        },
    )

    outcome = validator.run_smoke(
        run_id="run-smoke-schema-json",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert json.dumps(outcome.report)
    sample_result = outcome.report["feedback"]["evidence"]["sample_result"]
    assert sample_result["schema_error"] == {
        "code": "RUN_PAYLOAD_STATUS_SUMMARY_MISMATCH",
        "message": "run payload status conflicts with summary counts",
    }


def test_stage2_validator_smoke_emits_command_progress_events(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-progress-events.db'}",
        stage2_quickcheck_sample_size=1,
    )
    validator = Stage2Validator(settings)
    containers: dict[str, str] = {}
    next_container_id = 0

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        nonlocal next_container_id
        command_text = " ".join(args)
        if args[:2] == ["docker", "build"]:
            if on_output is not None:
                on_output("stderr", "build step 1\n")
            return CommandResult(args=args, returncode=0, stdout="", stderr="build step 1\n")
        if args[:2] == ["docker", "create"]:
            next_container_id += 1
            container_id = f"container-{next_container_id}"
            if "--action collect" in command_text:
                containers[container_id] = "collect"
            elif "--action run" in command_text:
                containers[container_id] = "run:tests/test_a.py"
            else:
                raise AssertionError(f"unexpected create command: {args}")
            return CommandResult(
                args=args,
                returncode=0,
                stdout=f"{container_id}\n",
                stderr="",
            )
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            container_kind = containers[args[-1]]
            if container_kind == "collect":
                if on_output is not None:
                    on_output("stderr", "collecting tests\n")
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "collect",
                            "test_files": [{"path": "tests/test_a.py"}],
                        }
                    ),
                    stderr="collecting tests\n",
                )
            if container_kind == "run:tests/test_a.py":
                if on_output is not None:
                    on_output("stderr", "running tests/test_a.py\n")
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(_run_payload("passed", exit_code=0, passed=1, failed=0)),
                    stderr="running tests/test_a.py\n",
                )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)
    monkeypatch.setattr(validator, "_validate_collect_paths_exist", lambda *args, **kwargs: None)

    events: list[dict[str, object]] = []
    outcome = validator.run_smoke(
        run_id="run-progress-events",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
        emit_event=lambda title, message, payload: events.append(
            {"title": title, "message": message, "payload": dict(payload or {})}
        ),
        attempt_index=2,
    )

    assert outcome.passed is True
    titles = [str(event["title"]) for event in events]
    assert "Validate build started" in titles
    assert "Validate build output" in titles
    assert "Validate build completed" in titles
    assert "Validate collect command started" in titles
    assert "Validate collect command output" in titles
    assert "Validate collect command completed" in titles
    assert "Validate sample started" in titles
    assert "Validate sample test started" in titles
    assert "Validate sample test output" in titles
    assert "Validate sample test completed" in titles
    build_started = next(event for event in events if event["title"] == "Validate build started")
    build_output = next(event for event in events if event["title"] == "Validate build output")
    assert build_started["payload"]["parent_operation"].startswith("validate_smoke_build_attempt002")
    assert build_output["payload"]["operation"].startswith("validate_smoke_build_attempt002")
    assert build_output["payload"]["tail_lines"] == ["stderr: build step 1"]


def test_stage2_validator_run_command_reads_json_after_nonzero_exit(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-run-nonzero-command.db'}")
    validator = Stage2Validator(settings)
    run_script_path = tmp_path / "run_script.sh"
    run_script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator._progress_context["run_script_host_path"] = run_script_path  # noqa: SLF001
    containers: dict[str, str] = {}

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        command_text = " ".join(args)
        if args[:2] == ["docker", "create"]:
            assert "&& cat /tmp/run.json" not in command_text
            assert 'if [ -s /tmp/run.json ]; then cat /tmp/run.json; fi; exit "$rc"' in command_text
            containers["run-container"] = "run"
            return CommandResult(args=args, returncode=0, stdout="run-container\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            return CommandResult(
                args=args,
                returncode=1,
                stdout=json.dumps(_run_payload("failed", exit_code=1, passed=1, failed=1)),
                stderr="pytest reported failures",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)

    result = validator._run_test_file("stage2-test-image", "tests/test_a.py")  # noqa: SLF001

    assert result["command"]["returncode"] == 1
    assert result["payload"]["status"] == "failed"
    assert result["schema_error"] is None


def test_stage2_validator_collect_command_reads_json_after_nonzero_exit(monkeypatch, tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-collect-nonzero-command.db'}")
    validator = Stage2Validator(settings)
    run_script_path = tmp_path / "run_script.sh"
    run_script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator._progress_context["run_script_host_path"] = run_script_path  # noqa: SLF001
    collect_payload = {
        "schema_version": 1,
        "action": "collect",
        "test_files": [{"path": "tests/test_a.py"}],
    }
    containers: dict[str, str] = {}

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        command_text = " ".join(args)
        if args[:2] == ["docker", "create"]:
            assert "&& cat /tmp/collect.json" not in command_text
            assert 'if [ -s /tmp/collect.json ]; then cat /tmp/collect.json; fi; exit "$rc"' in command_text
            containers["collect-container"] = "collect"
            return CommandResult(args=args, returncode=0, stdout="collect-container\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            return CommandResult(
                args=args,
                returncode=3,
                stdout=json.dumps(collect_payload),
                stderr="collector reported a partial failure",
            )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)

    result = validator._run_collect("stage2-test-image")  # noqa: SLF001

    assert result.returncode == 3
    assert json.loads(result.stdout) == collect_payload


def test_stage2_validator_smoke_can_pass_with_some_timed_out_samples(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-smoke-timeout-pass.db'}",
        stage2_quickcheck_sample_size=3,
    )
    validator = TimedSampleValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
                {"path": "tests/test_b.py"},
                {"path": "tests/test_c.py"},
            ],
        },
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=2, failed=0),
            "tests/test_b.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        },
        timed_out_files={"tests/test_c.py"},
    )

    outcome = validator.run_smoke(
        run_id="run-timeout-pass",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is True
    assert outcome.report["status"] == "passed"
    assert outcome.report["passed_files"] == 2
    assert outcome.report["timed_out_files"] == 1


def test_stage2_validator_smoke_reports_timeout_only_when_timeouts_prevent_passing(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-smoke-timeout-fail.db'}",
        stage2_quickcheck_sample_size=3,
    )
    validator = TimedSampleValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
                {"path": "tests/test_b.py"},
                {"path": "tests/test_c.py"},
            ],
        },
        sample_results={
            "tests/test_a.py": _run_payload("passed", exit_code=0, passed=2, failed=0),
        },
        timed_out_files={"tests/test_b.py", "tests/test_c.py"},
    )

    outcome = validator.run_smoke(
        run_id="run-timeout-fail",
        workspace_dir=tmp_path,
        dockerfile_text="FROM python:3.11-slim-bookworm\n",
        run_script_text="--action --out",
    )

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "TEST_SAMPLE_TIMEOUT"


def test_stage2_validator_full_validation_enforces_overall_timeout(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-timeout.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=300.0,
        stage2_full_validation_timeout_seconds=30.0,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    class FullTimeoutValidator(FakeValidator):
        def _bounded_timeout(self, *, default_timeout_seconds: float, deadline: float) -> tuple[float | None, bool]:
            if default_timeout_seconds == self.settings.stage2_run_test_timeout_seconds:
                return 0.01, True
            return super()._bounded_timeout(
                default_timeout_seconds=default_timeout_seconds,
                deadline=deadline,
            )

        def _run_test_file(self, image_tag: str, test_file_path: str, *, cancel_requested=None, timeout_seconds=None) -> dict:  # noqa: ARG002
            timed_out = (timeout_seconds or 0.0) <= 0.01
            return {
                "test_file_path": test_file_path,
                "command": {
                    "command": ["docker", "run", test_file_path],
                    "returncode": 124 if timed_out else 0,
                    "stdout": "",
                    "stderr": "",
                    "timed_out": timed_out,
                },
                "payload": None,
                "schema_error": None,
                "result": None,
            }

    validator = FullTimeoutValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
            ],
        },
        sample_results={},
    )

    outcome = validator.run_full(run_id="run-full-timeout", workspace_dir=tmp_path)

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "FULL_VALIDATION_TIMEOUT"


def test_stage2_validator_full_validation_rejects_zero_collected_test_files(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-collect-zero.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = FakeValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [],
        },
        sample_results={},
    )

    outcome = validator.run_full(run_id="run-full-collect-zero", workspace_dir=tmp_path)

    assert outcome.passed is False
    assert outcome.report["feedback"]["code"] == "FULL_COLLECT_ZERO"


def test_stage2_validator_full_validation_rejects_run_payload_without_status(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-missing-status.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = Stage2Validator(settings)
    containers: dict[str, str] = {}
    next_container_id = 0

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        nonlocal next_container_id
        command_text = " ".join(args)
        if args[:2] == ["docker", "build"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "create"]:
            next_container_id += 1
            container_id = f"container-{next_container_id}"
            if "--action collect" in command_text:
                containers[container_id] = "collect"
            elif "--action run" in command_text:
                containers[container_id] = "run"
            else:
                raise AssertionError(f"unexpected create command: {args}")
            return CommandResult(args=args, returncode=0, stdout=f"{container_id}\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            container_kind = containers[args[-1]]
            if container_kind == "collect":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "collect",
                            "test_files": [{"path": "tests/test_a.py"}],
                        }
                    ),
                    stderr="",
                )
            if container_kind == "run":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "run",
                            "summary": {
                                "collected": 1,
                                "passed": 1,
                                "failed": 0,
                                "errors": 0,
                                "skipped": 0,
                            },
                        }
                    ),
                    stderr="",
                )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)
    monkeypatch.setattr(validator, "_validate_collect_paths_exist", lambda *args, **kwargs: None)

    outcome = validator.run_full(run_id="run-full-missing-status", workspace_dir=tmp_path)

    assert outcome.passed is False
    assert outcome.report["status"] == "failed"
    assert outcome.report["file_results"][0]["result"]["status"] == "error"
    assert outcome.report["file_results"][0]["result"]["message"] == "run payload is missing status"


def test_stage2_validator_full_validation_emits_file_progress_messages(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-progress-events.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = Stage2Validator(settings)
    containers: dict[str, str] = {}
    next_container_id = 0

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        nonlocal next_container_id
        command_text = " ".join(args)
        if args[:2] == ["docker", "build"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "create"]:
            next_container_id += 1
            container_id = f"container-{next_container_id}"
            if "--action collect" in command_text:
                containers[container_id] = "collect"
            elif "--target-selector tests/test_a.py" in command_text:
                containers[container_id] = "run:tests/test_a.py"
            elif "--target-selector tests/test_b.py" in command_text:
                containers[container_id] = "run:tests/test_b.py"
            else:
                raise AssertionError(f"unexpected create command: {args}")
            return CommandResult(args=args, returncode=0, stdout=f"{container_id}\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            container_kind = containers[args[-1]]
            if container_kind == "collect":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "collect",
                            "test_files": [
                                {"path": "tests/test_a.py"},
                                {"path": "tests/test_b.py"},
                            ],
                        }
                    ),
                    stderr="",
                )
            if container_kind.startswith("run:"):
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(_run_payload("passed", exit_code=0, passed=2, failed=0)),
                    stderr="",
                )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)
    monkeypatch.setattr(validator, "_validate_collect_paths_exist", lambda *args, **kwargs: None)

    events: list[dict[str, object]] = []
    outcome = validator.run_full(
        run_id="run-full-progress-events",
        workspace_dir=tmp_path,
        emit_event=lambda title, message, payload: events.append(
            {"title": title, "message": message, "payload": dict(payload or {})}
        ),
    )

    assert outcome.passed is True
    started_events = [event for event in events if event["title"] == "Host full test file started"]
    completed_events = [event for event in events if event["title"] == "Host full test file completed"]
    assert len(started_events) == 2
    assert len(completed_events) == 2
    assert started_events[0]["message"] == "第 1/2 个文件：tests/test_a.py"
    assert started_events[1]["message"] == "第 2/2 个文件：tests/test_b.py"
    assert "第 1/2 个文件" in str(completed_events[0]["message"])
    assert "第 2/2 个文件" in str(completed_events[1]["message"])


def test_stage2_validator_full_validation_passes_when_at_least_one_file_passes(monkeypatch, tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-partial-pass.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = Stage2Validator(settings)
    containers: dict[str, str] = {}
    next_container_id = 0

    def fake_run_command(args, *, timeout_seconds, cancel_requested=None, on_output=None):  # noqa: ARG001
        nonlocal next_container_id
        command_text = " ".join(args)
        if args[:2] == ["docker", "build"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "create"]:
            next_container_id += 1
            container_id = f"container-{next_container_id}"
            if "--action collect" in command_text:
                containers[container_id] = "collect"
            elif "--target-selector tests/test_a.py" in command_text:
                containers[container_id] = "run:tests/test_a.py"
            elif "--target-selector tests/test_b.py" in command_text:
                containers[container_id] = "run:tests/test_b.py"
            else:
                raise AssertionError(f"unexpected create command: {args}")
            return CommandResult(args=args, returncode=0, stdout=f"{container_id}\n", stderr="")
        if args[:2] == ["docker", "cp"]:
            return CommandResult(args=args, returncode=0, stdout="", stderr="")
        if args[:2] == ["docker", "start"]:
            container_kind = containers[args[-1]]
            if container_kind == "collect":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "schema_version": 1,
                            "action": "collect",
                            "test_files": [
                                {"path": "tests/test_a.py"},
                                {"path": "tests/test_b.py"},
                            ],
                        }
                    ),
                    stderr="",
                )
            if container_kind == "run:tests/test_a.py":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(_run_payload("passed", exit_code=0, passed=2, failed=0)),
                    stderr="",
                )
            if container_kind == "run:tests/test_b.py":
                return CommandResult(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(_run_payload("failed", exit_code=1, passed=1, failed=1)),
                    stderr="",
                )
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(validator, "_run_command", fake_run_command)
    monkeypatch.setattr(validator, "_validate_collect_paths_exist", lambda *args, **kwargs: None)

    outcome = validator.run_full(run_id="run-full-partial-pass", workspace_dir=tmp_path)

    assert outcome.passed is True
    assert outcome.report["status"] == "passed"
    assert outcome.report["summary"]["total_files"] == 2
    assert outcome.report["summary"]["passed_files"] == 1
    assert outcome.report["summary"]["failed_files"] == 1


def test_stage2_validator_full_validation_limits_collected_p2p_files(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-full-p2p-limit.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
        stage2_p2p_file_count_limit=2,
    )
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    validator = FakeValidator(
        settings,
        collect_payload={
            "schema_version": 1,
            "action": "collect",
            "test_files": [
                {"path": "tests/test_a.py"},
                {"path": "tests/test_b.py"},
                {"path": "tests/test_c.py"},
            ],
        },
        sample_results={
            "tests/test_b.py": _run_payload("passed", exit_code=0, passed=3, failed=0),
            "tests/test_c.py": _run_payload("passed", exit_code=0, passed=2, failed=0),
        },
    )

    outcome = validator.run_full(run_id="run-full-p2p-limit", workspace_dir=tmp_path)

    assert outcome.passed is True
    assert outcome.report["summary"]["total_files"] == 2
    assert outcome.report["summary"]["passed_files"] == 2
    assert [item["test_file_path"] for item in outcome.report["file_results"]] == [
        "tests/test_c.py",
        "tests/test_b.py",
    ]
    limited_collect = outcome.report["collect"]["payload"]
    assert [item["path"] for item in limited_collect["test_files"]] == [
        "tests/test_c.py",
        "tests/test_b.py",
    ]
    assert limited_collect["source_test_file_count"] == 3
    assert limited_collect["p2p_file_count_limit"] == 2
    assert limited_collect["p2p_sample_seed"] == "run-full-p2p-limit"
    assert limited_collect["truncated_test_file_count"] == 1


def test_stage2_validator_p2p_limit_uses_runtime_sample_seed_across_run_ids(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'validator-p2p-sample-seed.db'}",
        stage2_collect_timeout_seconds=120.0,
        stage2_run_test_timeout_seconds=120.0,
        stage2_full_validation_timeout_seconds=600.0,
        stage2_p2p_file_count_limit=3,
        stage2_p2p_sample_seed="fixed-p2p-seed",
    )
    collect_payload = {
        "schema_version": 1,
        "action": "collect",
        "test_files": [
            {"path": "tests/test_e.py"},
            {"path": "tests/test_c.py"},
            {"path": "tests/test_a.py"},
            {"path": "tests/test_d.py"},
            {"path": "tests/test_b.py"},
        ],
    }
    sample_results = {
        "tests/test_a.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        "tests/test_b.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
        "tests/test_d.py": _run_payload("passed", exit_code=0, passed=1, failed=0),
    }
    expected_paths = [
        "tests/test_a.py",
        "tests/test_d.py",
        "tests/test_b.py",
    ]
    (tmp_path / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    first = FakeValidator(settings, collect_payload=collect_payload, sample_results=sample_results)
    second = FakeValidator(settings, collect_payload=collect_payload, sample_results=sample_results)

    first_outcome = first.run_full(run_id="original-run-id", workspace_dir=tmp_path)
    second_outcome = second.run_full(run_id="resume-run-id", workspace_dir=tmp_path)

    assert [item["path"] for item in first_outcome.report["collect"]["payload"]["test_files"]] == expected_paths
    assert [item["path"] for item in second_outcome.report["collect"]["payload"]["test_files"]] == expected_paths
    assert first_outcome.report["collect"]["payload"]["p2p_sample_seed"] == "fixed-p2p-seed"
    assert second_outcome.report["collect"]["payload"]["p2p_sample_seed"] == "fixed-p2p-seed"


def test_stage2_validator_can_interrupt_running_command(tmp_path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'validator-interrupt.db'}")
    validator = Stage2Validator(settings)
    started_at = time.monotonic()

    def cancel_requested() -> bool:
        return time.monotonic() - started_at >= 0.2

    try:
        validator._run_command(  # noqa: SLF001
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout_seconds=30.0,
            cancel_requested=cancel_requested,
        )
    except Stage2ValidationInterrupted as exc:
        assert str(exc) == "stage2 validation interrupted by user"
    else:
        raise AssertionError("expected Stage2ValidationInterrupted")
