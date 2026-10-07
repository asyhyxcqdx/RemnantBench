from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from time import sleep
from types import SimpleNamespace
from unittest.mock import patch

import belta.retire_task_construction as retire
from belta.retire_task_construction import (
    PAIR_PATCH_APPLY_FAILURE_MARKER,
    TASK_INSTRUCTION,
    PairConstructionError,
    PairConstructionOutcome,
    PairInfrastructureError,
    ValidationRunResult,
    ValidationSuiteResult,
    ValidationSuiteTimeout,
    ValidationTimeouts,
    _apply_patch_in_validation_container,
    _build_environment_image,
    _candidates_with_completed_environments,
    _checkout_commit,
    _construct_pair,
    _deduplicate_candidates_by_patch,
    _gold_cleanup_patch,
    _group_by_repo,
    _run_validation_tests,
    _snapshot_validation_tests,
    _task_manifest,
    _validate_retire_output_layout,
    _validation_test_universe,
    _validation_timeouts_from_environment,
    run_retire_task_construction,
)


class RetireTaskConstructionTests(unittest.TestCase):
    def test_task_instruction_matches_the_canonical_prompt(self) -> None:
        self.assertEqual(
            TASK_INSTRUCTION,
            (
                "This repository is based on the target version, but remnants of obsolete implementations from an older version have been reintroduced. These remnants may be spread across multiple files, functions, and classes, and may involve independent behaviors.\n"
                "\n"
                "These obsolete remnants may include, but are not limited to:\n"
                "\n"
                "- branches, functions, classes, variables, or helper logic that are no longer used;\n"
                "- old logic that duplicates or conflicts with the target-version implementation;\n"
                "- old code that can still execute even though the target version no longer needs or expects it;\n"
                "- historical logic that interferes with the target version\u2019s control flow, data flow, or state management;\n"
                "- obsolete imports, references, variables, or helper code left behind by the old implementation.\n"
                "\n"
                "Please use the existing source code, call relationships, and available tests to identify and remove these obsolete implementations, bringing the affected behavior into alignment with the target version while preserving unaffected functionality.\n"
                "\n"
                "Please follow these principles:\n"
                "\n"
                "1. Use the target-version implementation already present in the repository as the basis for your work. Avoid redesigning or replacing existing implementations.\n"
                "2. Remove the identified obsolete implementations and clean up related imports, references, variables, and helper code, taking care to preserve any parts still required by the target version.\n"
                "3. Preserve code and behavior unrelated to these obsolete implementations, and avoid introducing unrelated changes.\n"
                "4. Keep the final source changes clear and consistent with the surrounding code style and structure.\n"
                "\n"
                "Repository and environment notes:\n"
                "\n"
                "1. The project directory is `/workspace/repo`. Do not search other directories for another copy of the project\u2019s source code.\n"
                "2. External network access is unavailable in the current environment. Use the tools and dependencies already available, and do not attempt to access the network or install or update dependencies.\n"
                "3. You may inspect and run the tests already present in the repository, and write additional tests as needed to help validate the changes.\n"
                "4. Temporary diagnostic files may be placed under `/tmp`. When finished, remove temporary files and diagnostic artifacts, leaving only changes relevant to the task.\n"
            ),
        )

    def test_runner_payload_accepts_failed_file_with_inconsistent_summary(self) -> None:
        payload = {
            "action": "run",
            "status": "failed",
            "summary": {
                "collected": 7,
                "passed": 0,
                "failed": 0,
                "errors": 77,
                "skipped": 0,
            },
        }

        self.assertIsNone(retire._runner_payload_error(payload))

    def test_runner_payload_requires_json_object(self) -> None:
        self.assertEqual(
            retire._runner_payload_error([]),
            "run result JSON must be an object",
        )

    def test_runner_payload_requires_strict_passed_summary(self) -> None:
        payload = {
            "action": "run",
            "status": "passed",
            "summary": {
                "collected": 5,
                "passed": 5,
                "failed": 0,
                "errors": 2,
                "skipped": 0,
            },
        }

        self.assertEqual(
            retire._runner_payload_error(payload),
            "run result JSON passed status conflicts with summary counts",
        )

    def test_runner_payload_accepts_nonzero_exit_code_for_passed_status(self) -> None:
        payload = {
            "action": "run",
            "status": "passed",
            "summary": {
                "collected": 5,
                "passed": 5,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            },
        }

        self.assertIsNone(retire._runner_payload_error(payload))

    def test_runner_payload_accepts_zero_count_failed_file(self) -> None:
        payload = {
            "action": "run",
            "status": "failed",
            "summary": {
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            },
            "log_preview": "SyntaxError: invalid syntax",
        }

        self.assertIsNone(retire._runner_payload_error(payload))

    def test_runner_payload_rejects_unknown_status(self) -> None:
        payload = {
            "action": "run",
            "status": "error",
            "summary": {
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "skipped": 0,
            },
        }

        self.assertEqual(
            retire._runner_payload_error(payload),
            "run result JSON status must be passed or failed",
        )

    def test_runner_payload_requires_nonnegative_integer_counts(self) -> None:
        base_payload = {
            "action": "run",
            "status": "failed",
            "summary": {
                "collected": 1,
                "passed": 0,
                "failed": 1,
                "errors": 0,
                "skipped": 0,
            },
        }
        for value, expected in (
            ("1", "run result JSON summary fields must be integers"),
            (True, "run result JSON summary fields must be integers"),
            (-1, "run result JSON summary fields must be non-negative"),
        ):
            with self.subTest(value=value):
                payload = json.loads(json.dumps(base_payload))
                payload["summary"]["errors"] = value
                self.assertEqual(
                    retire._runner_payload_error(payload),
                    expected,
                )

    def test_task_manifest_freezes_validation_timeouts(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            manifest = _task_manifest(
                run_dir,
                run_dir / "retire_task_construction" / "tasks" / "pair-1",
                _candidate("pair-1", offset=1),
                {"ff_stage2_run_id": "stage2-run"},
                [{"test_file": "tests/test_api.py"}],
                {
                    "new_commit_total_files": 1,
                    "new_commit_passed_files": 1,
                    "task_base_total_files": 1,
                    "task_base_passed_files": 0,
                    "task_base_failed_files": 1,
                },
                ValidationTimeouts(
                    full_validation_timeout_seconds=1801,
                ),
            )

        self.assertEqual(
            manifest["validation"],
            {
                "full_validation_timeout_seconds": 1801,
            },
        )

    def test_environment_fingerprint_includes_validation_timeouts(self) -> None:
        with TemporaryDirectory() as tmp:
            environment_dir = Path(tmp)
            for filename in retire.ENVIRONMENT_INPUT_FILES:
                (environment_dir / filename).write_text(filename, encoding="utf-8")

            first = retire._environment_input_fingerprint(
                environment_dir,
                ValidationTimeouts(
                    full_validation_timeout_seconds=1800,
                ),
            )
            second = retire._environment_input_fingerprint(
                environment_dir,
                ValidationTimeouts(
                    full_validation_timeout_seconds=1801,
                ),
            )

        self.assertNotEqual(first, second)

    def test_gold_cleanup_patch_uses_minimal_diff(self) -> None:
        with TemporaryDirectory() as tmp:
            with patch(
                "belta.retire_task_construction._run_git_process",
                side_effect=[
                    subprocess.CompletedProcess([], 0, b"", b""),
                    subprocess.CompletedProcess([], 0, b"cleanup patch", b""),
                ],
            ) as run_git:
                patch_bytes = _gold_cleanup_patch(
                    Path(tmp) / "cache.git",
                    Path(tmp) / "work-tree",
                    "new-commit",
                )

            diff_args = run_git.call_args_list[-1].args[0]
            self.assertEqual(patch_bytes, b"cleanup patch")
            self.assertIn("--minimal", diff_args)
            self.assertLess(diff_args.index("--minimal"), diff_args.index("--binary"))

    def test_gold_cleanup_patch_rejects_added_content(self) -> None:
        with (
            TemporaryDirectory() as tmp,
            patch(
                "belta.retire_task_construction._run_git_process",
                side_effect=[
                    subprocess.CompletedProcess([], 0, b"", b""),
                    subprocess.CompletedProcess(
                        [],
                        0,
                        (
                            b"diff --git a/app.py b/app.py\n"
                            b"--- a/app.py\n"
                            b"+++ b/app.py\n"
                            b"@@ -1,0 +1 @@\n"
                            b"+unexpected_addition()\n"
                        ),
                        b"",
                    ),
                ],
            ),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"gold_cleanup\.patch contains added content lines",
            ):
                _gold_cleanup_patch(
                    Path(tmp) / "cache.git",
                    Path(tmp) / "work-tree",
                    "new-commit",
                )

    def test_gold_cleanup_patch_only_contains_task_base_changes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            subprocess.run(["git", "init", "-q", source], check=True)
            subprocess.run(
                ["git", "-C", source, "config", "user.name", "Belta Test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", source, "config", "user.email", "belta@example.com"],
                check=True,
            )
            (source / "stable.txt").write_text("stable\n", encoding="utf-8")
            (source / "app.py").write_text("value = 'new'\n", encoding="utf-8")
            subprocess.run(["git", "-C", source, "add", "."], check=True)
            subprocess.run(["git", "-C", source, "commit", "-qm", "new"], check=True)
            new_commit = subprocess.check_output(
                ["git", "-C", source, "rev-parse", "HEAD"],
                text=True,
            ).strip()

            cache = root / "cache.git"
            subprocess.run(["git", "clone", "-q", "--bare", source, cache], check=True)
            task_base = root / "task-base"
            _checkout_commit(cache, new_commit, task_base)
            (task_base / "app.py").write_text(
                "legacy = True\nvalue = 'new'\n",
                encoding="utf-8",
            )

            cleanup_patch = _gold_cleanup_patch(cache, task_base, new_commit)

            self.assertIn(b"app.py", cleanup_patch)
            self.assertIn(b"-legacy = True", cleanup_patch)
            self.assertFalse(
                any(
                    line.startswith(b"+") and not line.startswith(b"+++ ")
                    for line in cleanup_patch.splitlines()
                )
            )
            self.assertNotIn(b"stable.txt", cleanup_patch)
            patch_path = root / "gold_cleanup.patch"
            patch_path.write_bytes(cleanup_patch)
            subprocess.run(
                ["git", "-C", task_base, "apply", "--check", patch_path],
                check=True,
            )

    def test_validation_test_universe_uses_file_level_passed_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            env_dir = Path(tmp)
            (env_dir / "test_results.jsonl").write_text(
                (
                    '{"test_file":"Tests/test_ok.py","target_selector":"test_ok.py",'
                    '"status":"passed","total_tests":2,'
                    '"passed_tests":2,"failed_tests":0,"error_tests":0,'
                    '"skipped_tests":0,"exit_code":7}\n'
                    '{"test_file":"tests/test_bad.py","status":"failed","total_tests":1,'
                    '"passed_tests":0,"failed_tests":1,"error_tests":0,'
                    '"skipped_tests":0,"exit_code":1}\n'
                    '{"test_file":"","status":"passed"}\n'
                ),
                encoding="utf-8",
            )

            rows = _validation_test_universe(env_dir)

            self.assertEqual([row["test_file"] for row in rows], ["Tests/test_ok.py"])
            self.assertEqual(rows[0]["target_selector"], "test_ok.py")
            self.assertEqual(rows[0]["status"], "passed")
            self.assertEqual(rows[0]["total_tests"], 2)

    def test_snapshot_validation_entry_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            eval_assets = root / "eval_assets"
            (repo / "tests" / "fixtures").mkdir(parents=True)
            (repo / "tests" / "test_api.py").write_text(
                "def test_api(): pass\n", encoding="utf-8"
            )
            (repo / "tests" / "fixtures" / "data.json").write_text(
                "{}\n", encoding="utf-8"
            )

            snapshots = _snapshot_validation_tests(
                repo,
                eval_assets,
                [{"test_file": "tests/test_api.py"}],
            )

            self.assertEqual(len(snapshots), 1)
            self.assertEqual(
                set(snapshots[0]),
                {"path", "storage_path", "sha256"},
            )
            self.assertEqual(snapshots[0]["path"], "tests/test_api.py")
            self.assertTrue((eval_assets / snapshots[0]["storage_path"]).is_file())

    def test_snapshot_preserves_distinct_target_selector(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            eval_assets = root / "eval_assets"
            (repo / "Tests").mkdir(parents=True)
            (repo / "Tests" / "test_Ace.py").write_text(
                "def test_ace(): pass\n", encoding="utf-8"
            )

            snapshots = _snapshot_validation_tests(
                repo,
                eval_assets,
                [
                    {
                        "test_file": "Tests/test_Ace.py",
                        "target_selector": "test_Ace.py",
                    }
                ],
            )

            self.assertEqual(snapshots[0]["path"], "Tests/test_Ace.py")
            self.assertEqual(snapshots[0]["target_selector"], "test_Ace.py")

    def test_snapshot_accepts_runner_relative_target_selector(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "repo"
            eval_assets = root / "eval_assets"
            (repo / "unit" / "lm").mkdir(parents=True)
            (repo / "unit" / "lm" / "test_counter.py").write_text(
                "def test_counter(): pass\n", encoding="utf-8"
            )

            snapshots = _snapshot_validation_tests(
                repo,
                eval_assets,
                [
                    {
                        "test_file": "unit/lm/test_counter.py",
                        "target_selector": "../unit/lm/test_counter.py",
                    }
                ],
            )

            self.assertEqual(
                snapshots[0]["target_selector"],
                "../unit/lm/test_counter.py",
            )

    def test_group_by_repo_preserves_repo_groups_and_sorts_offsets(self) -> None:
        rows = [
            {"repo_key": "b", "offset": 20, "pair_id": "b20"},
            {"repo_key": "a", "offset": 2, "pair_id": "a2"},
            {"repo_key": "a", "offset": 1, "pair_id": "a1"},
        ]

        grouped = _group_by_repo(rows)

        self.assertEqual([row["pair_id"] for row in grouped["a"]], ["a1", "a2"])
        self.assertEqual([row["pair_id"] for row in grouped["b"]], ["b20"])

    def test_only_candidates_with_completed_environments_enter_retire(self) -> None:
        with TemporaryDirectory() as tmp:
            environments = Path(tmp)
            completed = environments / "owner__completed"
            failed = environments / "owner__failed"
            completed.mkdir()
            failed.mkdir()
            (completed / "manifest.json").write_text(
                '{"status":"completed"}\n', encoding="utf-8"
            )
            (failed / "manifest.json").write_text(
                '{"status":"failed"}\n', encoding="utf-8"
            )
            for filename in (
                "Dockerfile",
                "run_script.sh",
                "collect_report.json",
                "full_report.json",
                "test_results.jsonl",
            ):
                (completed / filename).write_text("", encoding="utf-8")

            rows = [
                {"repo_key": "owner__completed", "pair_id": "kept"},
                {"repo_key": "owner__failed", "pair_id": "dropped"},
            ]

            eligible = _candidates_with_completed_environments(environments, rows)

            self.assertEqual([row["pair_id"] for row in eligible], ["kept"])

    def test_environment_image_reuses_stable_content_tag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "run-a" / "environment"
            second = root / "run-b" / "environment"
            for environment in (first, second):
                environment.mkdir(parents=True)
                (environment / "Dockerfile").write_text(
                    "FROM python:3.13\n", encoding="utf-8"
                )
                (environment / "run_script.sh").write_text(
                    "#!/bin/sh\n", encoding="utf-8"
                )

            with (
                patch(
                    "belta.retire_task_construction._docker_image_exists",
                    side_effect=[False, False, True],
                ),
                patch("belta.retire_task_construction._run_process") as build,
            ):
                first_tag = _build_environment_image(first, "owner__repo")
                second_tag = _build_environment_image(second, "owner__repo")

            self.assertEqual(first_tag, second_tag)
            build.assert_called_once()

    def test_validation_tests_use_one_container_and_continue_after_failure(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            commands: list[list[str]] = []
            output_dir: Path | None = None

            def fake_run(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                nonlocal output_dir
                commands.append(command)
                if command[:3] == ["docker", "run", "--detach"]:
                    output_mount = next(
                        value
                        for value in command
                        if value.endswith(":/workspace/belta_out")
                    )
                    output_dir = Path(output_mount.split(":", 1)[0])
                    return subprocess.CompletedProcess(
                        command, 0, stdout="container-id\n"
                    )
                if command[:2] == ["docker", "exec"]:
                    assert output_dir is not None
                    test_file = next(
                        value.removeprefix("BELTA_TEST_FILE=")
                        for value in command
                        if value.startswith("BELTA_TEST_FILE=")
                    )
                    result_name = next(
                        value.removeprefix("BELTA_RESULT_FILE=")
                        for value in command
                        if value.startswith("BELTA_RESULT_FILE=")
                    )
                    failed = test_file.endswith("test_a.py")
                    (output_dir / result_name).write_text(
                        json.dumps(
                            {
                                "action": "run",
                                "status": "failed" if failed else "passed",
                                "summary": {
                                    "collected": 1,
                                    "passed": 0 if failed else 1,
                                    "failed": 1 if failed else 0,
                                    "errors": 0,
                                    "skipped": 0,
                                },
                            }
                        ),
                        encoding="utf-8",
                    )
                    return subprocess.CompletedProcess(
                        command, 1 if failed else 0, stdout=""
                    )
                if command[:3] == ["docker", "rm", "--force"]:
                    return subprocess.CompletedProcess(command, 0, stdout="")
                raise AssertionError(f"unexpected command: {command}")

            with patch(
                "belta.retire_task_construction.subprocess.run", side_effect=fake_run
            ):
                suite_result = _run_validation_tests(
                    "pair-image",
                    environment_dir,
                    [
                        {
                            "test_file": "Tests/test_a.py",
                            "target_selector": "test_a.py",
                        },
                        {"test_file": "tests/test_b.py"},
                    ],
                )

            self.assertEqual(
                [result.test_file for result in suite_result.results],
                ["Tests/test_a.py", "tests/test_b.py"],
            )
            self.assertEqual(
                [result.status for result in suite_result.results], ["failed", "passed"]
            )
            self.assertEqual(
                sum(command[:2] == ["docker", "run"] for command in commands), 1
            )
            self.assertEqual(
                sum(command[:2] == ["docker", "exec"] for command in commands), 2
            )
            self.assertEqual(
                sum(command[:2] == ["docker", "rm"] for command in commands), 1
            )
            run_command = next(
                command for command in commands if command[:2] == ["docker", "run"]
            )
            self.assertFalse(
                any(value.endswith(":/workspace/repo") for value in run_command)
            )
            self.assertNotIn("HOME=/tmp", run_command)
            self.assertNotIn("-e", run_command)
            first_exec = next(
                command for command in commands if command[:2] == ["docker", "exec"]
            )
            self.assertIn("BELTA_TEST_FILE=Tests/test_a.py", first_exec)
            self.assertIn("BELTA_TARGET_SELECTOR=test_a.py", first_exec)

    def test_validation_tests_record_missing_result_as_infra_error(self) -> None:
        with TemporaryDirectory() as tmp:
            environment_dir = Path(tmp) / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )

            def fake_run(
                command: list[str], **_: object
            ) -> subprocess.CompletedProcess[str]:
                if command[:3] == ["docker", "run", "--detach"]:
                    return subprocess.CompletedProcess(
                        command, 0, stdout="container-id\n"
                    )
                if command[:2] == ["docker", "exec"]:
                    return subprocess.CompletedProcess(
                        command,
                        2,
                        stdout="runner stdout",
                        stderr="runner stderr",
                    )
                return subprocess.CompletedProcess(command, 0, stdout="")

            with patch(
                "belta.retire_task_construction.subprocess.run", side_effect=fake_run
            ):
                suite_result = _run_validation_tests(
                    "pair-image",
                    environment_dir,
                    [{"test_file": "tests/test_api.py"}],
                )

            result = suite_result.results[0]
            self.assertEqual(result.status, "error")
            self.assertEqual(result.exit_code, 2)
            self.assertEqual(result.infra_error, "run_script did not write result JSON")
            self.assertEqual(result.raw_result["stdout"], "runner stdout")
            self.assertEqual(result.raw_result["stderr"], "runner stderr")

    def test_running_file_timeout_ends_the_validation_suite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text(
                "#!/bin/sh\n", encoding="utf-8"
            )
            test_results_path = root / "pair.jsonl"

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="container-id\n",
                    stderr="",
                )

            with (
                patch(
                    "belta.retire_task_construction.subprocess.run",
                    side_effect=fake_run,
                ),
                patch(
                    "belta.retire_task_construction._run_validation_test_in_container",
                    side_effect=ValidationSuiteTimeout,
                ),
                patch(
                    "belta.retire_task_construction.time.monotonic",
                    side_effect=[0.0, 0.0],
                ),
            ):
                suite_result = _run_validation_tests(
                    "pair-image",
                    environment_dir,
                    [{"test_file": "tests/test_slow.py"}],
                    test_results_path=test_results_path,
                    full_validation_timeout_seconds=10,
                )

            self.assertEqual(suite_result.results, [])
            self.assertEqual(
                suite_result.error,
                "validation suite timed out after 10 seconds while running "
                "tests/test_slow.py",
            )
            self.assertEqual(test_results_path.read_text(encoding="utf-8"), "")

    def test_validation_process_uses_only_the_remaining_suite_time(self) -> None:
        with TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            with patch(
                "belta.retire_task_construction.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    cmd=["docker", "exec"],
                    timeout=12.5,
                ),
            ) as run:
                with self.assertRaises(ValidationSuiteTimeout):
                    retire._run_validation_test_in_container(
                        "validation-container",
                        "tests/test_slow.py",
                        "tests/test_slow.py",
                        result_path,
                        remaining_suite_seconds=12.5,
                    )

        self.assertEqual(run.call_args.kwargs["timeout"], 12.5)
        self.assertNotIn("timeout ", run.call_args.args[0][-1])

    def test_valid_runner_error_count_is_a_failed_file_not_infrastructure(self) -> None:
        result = ValidationRunResult(
            test_file="tests/test_setup.py",
            target_selector="tests/test_setup.py",
            status="failed",
            summary={
                "collected": 1,
                "passed": 0,
                "failed": 0,
                "errors": 1,
                "skipped": 0,
            },
            raw_result={"action": "run", "status": "failed"},
            exit_code=1,
        )
        validation_tests = [{"test_file": "tests/test_setup.py"}]

        summary = retire._test_summary(validation_tests, [result])
        suite_error = retire._validation_suite_error(
            validation_tests,
            _validation_suite(result),
        )

        self.assertEqual(summary["task_base_failed_files"], 1)
        self.assertEqual(summary["task_base_error_files"], 0)
        self.assertIsNone(suite_error)

    def test_baseline_checkpoint_requires_matching_detailed_results(self) -> None:
        with TemporaryDirectory() as tmp:
            checkpoint_path = (
                Path(tmp)
                / "retire_task_construction"
                / "checkpoints"
                / "baselines"
                / "repo.json"
            )
            _write_baseline_checkpoint(checkpoint_path, "passed")
            results_path = (
                checkpoint_path.parents[2]
                / "results"
                / "baseline_test_results"
                / "repo.jsonl"
            )

            self.assertEqual(
                retire._read_repo_baseline_status(
                    checkpoint_path,
                    input_fingerprint="environment-fingerprint",
                    test_results_path=results_path,
                ),
                "passed",
            )
            results_path.unlink()
            self.assertIsNone(
                retire._read_repo_baseline_status(
                    checkpoint_path,
                    input_fingerprint="environment-fingerprint",
                    test_results_path=results_path,
                )
            )

    def test_suite_timeout_does_not_create_results_for_unstarted_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            test_results_path = root / "pair.jsonl"

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="container-id\n",
                    stderr="",
                )

            with (
                patch(
                    "belta.retire_task_construction.subprocess.run",
                    side_effect=fake_run,
                ),
                patch(
                    "belta.retire_task_construction._run_validation_test_in_container",
                    return_value=_passed_validation_result(),
                ) as run_one,
                patch(
                    "belta.retire_task_construction.time.monotonic",
                    side_effect=[0.0, 0.0, 11.0],
                ),
            ):
                suite_result = _run_validation_tests(
                    "pair-image",
                    environment_dir,
                    [
                        {"test_file": "tests/test_a.py"},
                        {"test_file": "tests/test_b.py"},
                    ],
                    test_results_path=test_results_path,
                    full_validation_timeout_seconds=10,
                )

            self.assertEqual(run_one.call_count, 1)
            self.assertEqual(
                run_one.call_args.kwargs["remaining_suite_seconds"],
                10.0,
            )
            self.assertEqual(len(suite_result.results), 1)
            self.assertEqual(
                suite_result.error,
                "validation suite timed out after 10 seconds",
            )
            rows = _read_jsonl(test_results_path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "passed")

    def test_container_start_failure_writes_no_per_file_results(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            test_results_path = root / "pair.jsonl"

            with patch(
                "belta.retire_task_construction.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [],
                    125,
                    stdout="",
                    stderr="docker daemon unavailable",
                ),
            ):
                suite_result = _run_validation_tests(
                    "pair-image",
                    environment_dir,
                    [{"test_file": "tests/test_api.py"}],
                    test_results_path=test_results_path,
                )

            self.assertEqual(suite_result.results, [])
            self.assertIn("docker daemon unavailable", suite_result.error or "")
            self.assertTrue(test_results_path.is_file())
            self.assertEqual(test_results_path.read_text(encoding="utf-8"), "")

    def test_interruption_publishes_no_test_results(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            environment_dir = root / "environment"
            environment_dir.mkdir()
            (environment_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            test_results_path = root / "pair.jsonl"

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    stdout="container-id\n",
                    stderr="",
                )

            with (
                patch(
                    "belta.retire_task_construction.subprocess.run",
                    side_effect=fake_run,
                ),
                patch(
                    "belta.retire_task_construction._run_validation_test_in_container",
                    side_effect=[
                        _passed_validation_result(),
                        InterruptedError("stopped"),
                    ],
                ),
            ):
                with self.assertRaises(InterruptedError):
                    _run_validation_tests(
                        "pair-image",
                        environment_dir,
                        [
                            {"test_file": "tests/test_a.py"},
                            {"test_file": "tests/test_b.py"},
                        ],
                        test_results_path=test_results_path,
                    )

            self.assertFalse(test_results_path.exists())
            self.assertFalse(
                test_results_path.with_name(f".{test_results_path.name}.partial").exists()
            )

    def test_validation_timeouts_use_loaded_environment_contract(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "FEATURE_FACTORY_STAGE2_FULL_VALIDATION_TIMEOUT_SECONDS": "1801",
            },
        ):
            timeouts = _validation_timeouts_from_environment()

        self.assertEqual(timeouts.full_validation_timeout_seconds, 1801)

    def test_pair_patch_is_streamed_into_running_container(self) -> None:
        with TemporaryDirectory() as tmp:
            patch_path = Path(tmp) / "obsolete_reinsert.patch"
            patch_path.write_text("private patch content\n", encoding="utf-8")
            with patch(
                "belta.retire_task_construction.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, stdout=b""),
            ) as run:
                _apply_patch_in_validation_container(
                    "validation-container",
                    patch_path,
                )

            command = run.call_args.args[0]
            self.assertEqual(command[:3], ["docker", "exec", "--interactive"])
            self.assertIn("validation-container", command)
            self.assertIn("git apply --binary --whitespace=nowarn -", command[-1])
            self.assertNotIn("private patch content", " ".join(command))
            self.assertEqual(run.call_args.kwargs["input"], b"private patch content\n")

    def test_runtime_pair_patch_classifies_apply_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            patch_path = Path(tmp) / "obsolete_reinsert.patch"
            patch_path.write_text("private patch content\n", encoding="utf-8")
            with patch(
                "belta.retire_task_construction.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 86, stdout=PAIR_PATCH_APPLY_FAILURE_MARKER.encode()
                ),
            ):
                with self.assertRaises(PairConstructionError):
                    _apply_patch_in_validation_container(
                        "validation-container",
                        patch_path,
                    )

    def test_runtime_pair_patch_classifies_docker_failure_as_infra(self) -> None:
        with TemporaryDirectory() as tmp:
            patch_path = Path(tmp) / "obsolete_reinsert.patch"
            patch_path.write_text("private patch content\n", encoding="utf-8")
            with patch(
                "belta.retire_task_construction.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [], 125, stdout=b"docker daemon unavailable"
                ),
            ):
                with self.assertRaises(PairInfrastructureError):
                    _apply_patch_in_validation_container(
                        "validation-container",
                        patch_path,
                    )

    def test_checkout_commit_reads_bare_cache_without_moving_head(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            cache = root / "cache.git"
            source.mkdir()
            subprocess.run(["git", "init", "-q", str(source)], check=True)
            subprocess.run(
                ["git", "-C", str(source), "config", "user.email", "test@example.com"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(source), "config", "user.name", "Test User"],
                check=True,
            )
            (source / ".gitattributes").write_text(
                "ignored.txt export-ignore\nversion.py export-subst\n",
                encoding="utf-8",
            )
            (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
            (source / "ignored.txt").write_text(
                "tracked but excluded from archives\n",
                encoding="utf-8",
            )
            (source / "version.py").write_text(
                'COMMIT = "$Format:%H$"\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "-C", str(source), "add", "."], check=True)
            subprocess.run(
                ["git", "-C", str(source), "commit", "-qm", "initial"], check=True
            )
            commit = subprocess.check_output(
                ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
            ).strip()
            subprocess.run(
                ["git", "clone", "-q", "--bare", str(source), str(cache)], check=True
            )
            head_before = (cache / "HEAD").read_text(encoding="utf-8")

            _checkout_commit(cache, commit, root / "work-a")
            _checkout_commit(cache, commit, root / "work-b")

            self.assertEqual((root / "work-a" / "module.py").read_text(), "VALUE = 1\n")
            self.assertEqual((root / "work-b" / "module.py").read_text(), "VALUE = 1\n")
            self.assertEqual(
                (root / "work-a" / "ignored.txt").read_text(),
                "tracked but excluded from archives\n",
            )
            self.assertEqual(
                (root / "work-a" / "version.py").read_text(),
                'COMMIT = "$Format:%H$"\n',
            )
            self.assertFalse((root / "work-a.index").exists())
            self.assertFalse((root / "work-b.index").exists())
            self.assertEqual((cache / "HEAD").read_text(encoding="utf-8"), head_before)

    def test_run_processes_all_repo_candidates_and_keeps_multiple_main_tasks(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "config.yaml").write_text("project: {}\n", encoding="utf-8")
            candidate_dir = run_dir / "candidate_pairs"
            candidate_dir.mkdir()
            rows = [
                _candidate("pair-1", offset=1),
                _candidate("pair-2", offset=2),
                _candidate("pair-3", offset=3),
            ]
            (candidate_dir / "accepted_candidates.jsonl").write_text(
                "".join(f"{json.dumps(row)}\n" for row in rows),
                encoding="utf-8",
            )
            (run_dir / "environments").mkdir()

            outcomes = [
                _main_task_outcome("pair-1"),
                _main_task_outcome("pair-2"),
                _no_f2p_outcome(),
            ]
            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=outcomes,
                ) as construct_task,
            ):
                result = run_retire_task_construction(run_dir)

            self.assertEqual(construct_task.call_count, 3)
            self.assertEqual(build_image.call_count, 1)
            self.assertEqual(result.summary["tasks"], 2)
            self.assertEqual(result.summary["pair_status_counts"]["completed"], 3)
            self.assertEqual(
                result.summary["pair_status_counts"]["construction_failed"], 0
            )
            pair_results = _read_jsonl(
                run_dir / "retire_task_construction" / "results" / "pairs.jsonl"
            )
            self.assertEqual(
                [row["selection_result"] for row in pair_results],
                ["main_task", "main_task", "no_f2p"],
            )
            repo_results = _read_jsonl(
                run_dir / "retire_task_construction" / "results" / "repos.jsonl"
            )
            self.assertEqual(
                repo_results,
                [
                    {
                        "repo_key": "repo",
                        "baseline_status": "passed",
                        "pair_status_counts": {
                            "completed": 3,
                            "construction_failed": 0,
                            "infra_error": 0,
                        },
                        "selection_counts": {"main_task": 2, "no_f2p": 1},
                        "tasks": 2,
                    }
                ],
            )
            summary = json.loads(
                (
                    run_dir / "retire_task_construction" / "results" / "summary.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(summary, result.summary)

    def test_run_deduplicates_identical_repo_patches_at_the_smallest_offset(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            rows = [
                _candidate("pair-high", offset=10),
                _candidate("pair-low", offset=2),
                _candidate("pair-other", offset=3),
            ]
            run_dir = _minimal_retire_run(Path(tmp), rows)
            pair_outputs_dir = run_dir / "candidate_pairs" / "pair_outputs"
            for pair_id, content in (
                ("pair-high", b"+same residue\n"),
                ("pair-low", b"+same residue\n"),
                ("pair-other", b"+different residue\n"),
            ):
                pair_dir = pair_outputs_dir / pair_id
                pair_dir.mkdir(parents=True)
                (pair_dir / "obsolete_reinsert.patch").write_bytes(content)

            output_root = run_dir / "retire_task_construction"
            pair_results_dir = output_root / "checkpoints" / "pairs"
            stale_task_dir = output_root / "tasks" / "pair-high"
            pair_results_dir.mkdir(parents=True)
            stale_task_dir.mkdir(parents=True)
            (pair_results_dir / "pair-high.json").write_text(
                '{"pair_id": "pair-high"}\n',
                encoding="utf-8",
            )
            (stale_task_dir / "stale").write_text("stale\n", encoding="utf-8")
            baseline_dir = output_root / "checkpoints" / "baselines"
            baseline_dir.mkdir(parents=True)
            _write_baseline_checkpoint(baseline_dir / "repo.json", "passed")

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(max_concurrent_pairs=1)
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config", return_value=config
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    return_value=_no_f2p_outcome(),
                ) as construct_task,
            ):
                result = run_retire_task_construction(run_dir)

            processed_pair_ids = [
                call.args[2]["pair_id"] for call in construct_task.call_args_list
            ]
            self.assertEqual(processed_pair_ids, ["pair-low", "pair-other"])
            self.assertEqual(result.summary["pair_status_counts"]["completed"], 2)
            self.assertFalse((pair_results_dir / "pair-high.json").exists())
            self.assertFalse(stale_task_dir.exists())
            pair_results = _read_jsonl(output_root / "results" / "pairs.jsonl")
            self.assertEqual(
                [row["pair_id"] for row in pair_results],
                ["pair-low", "pair-other"],
            )

    def test_retire_output_layout_only_accepts_current_directories(self) -> None:
        with TemporaryDirectory() as tmp:
            output_root = Path(tmp) / "retire_task_construction"
            output_root.mkdir()
            for name in ("checkpoints", "tasks", "results"):
                (output_root / name).mkdir()

            _validate_retire_output_layout(output_root)

            (output_root / "unexpected").mkdir()
            with self.assertRaisesRegex(
                RuntimeError,
                r"does not use the current layout.*unexpected",
            ):
                _validate_retire_output_layout(output_root)

    def test_patch_deduplication_does_not_merge_missing_materials(self) -> None:
        with TemporaryDirectory() as tmp:
            rows = [
                _candidate("pair-1", offset=1),
                _candidate("pair-2", offset=2),
            ]
            selected = _deduplicate_candidates_by_patch(Path(tmp), rows)

            self.assertEqual(selected, rows)

    def test_pair_pool_runs_concurrently_and_keeps_candidate_order(self) -> None:
        with TemporaryDirectory() as tmp:
            rows = [_candidate(f"pair-{index}", offset=index) for index in range(1, 4)]
            run_dir = _minimal_retire_run(Path(tmp), rows)
            baseline_dir = (
                run_dir / "retire_task_construction" / "checkpoints" / "baselines"
            )
            baseline_dir.mkdir(parents=True)
            _write_baseline_checkpoint(baseline_dir / "repo.json", "passed")
            barrier = Barrier(2)
            lock = Lock()
            active = 0
            max_active = 0

            def construct_pair(*args: object, **_: object) -> PairConstructionOutcome:
                nonlocal active, max_active
                candidate = args[2]
                assert isinstance(candidate, dict)
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                if candidate["pair_id"] in {"pair-1", "pair-2"}:
                    barrier.wait(timeout=5)
                sleep(0.02 if candidate["pair_id"] == "pair-1" else 0.01)
                with lock:
                    active -= 1
                return _no_f2p_outcome()

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(
                    max_concurrent_repo_baselines=2,
                    max_concurrent_pairs=2,
                )
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config", return_value=config
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=construct_pair,
                ),
            ):
                result = run_retire_task_construction(run_dir)

            pair_results = _read_jsonl(
                run_dir / "retire_task_construction" / "results" / "pairs.jsonl"
            )
            self.assertEqual(result.summary["pair_status_counts"]["completed"], 3)
            self.assertEqual(max_active, 2)
            self.assertEqual(
                [row["pair_id"] for row in pair_results],
                ["pair-1", "pair-2", "pair-3"],
            )

    def test_repo_baselines_run_concurrently(self) -> None:
        with TemporaryDirectory() as tmp:
            first = _candidate("pair-a", offset=1)
            first.update({"repo_key": "repo-a", "full_name": "owner/repo-a"})
            second = _candidate("pair-b", offset=1)
            second.update({"repo_key": "repo-b", "full_name": "owner/repo-b"})
            run_dir = _minimal_retire_run(Path(tmp), [first, second])
            barrier = Barrier(2)
            lock = Lock()
            active = 0
            max_active = 0

            def run_validation(*_: object, **__: object) -> ValidationSuiteResult:
                nonlocal active, max_active
                with lock:
                    active += 1
                    max_active = max(max_active, active)
                barrier.wait(timeout=5)
                with lock:
                    active -= 1
                return _validation_suite(_passed_validation_result())

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(max_concurrent_pairs=2)
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config", return_value=config
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    side_effect=lambda _, repo_key: f"image-{repo_key}",
                ),
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    side_effect=run_validation,
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    return_value=_no_f2p_outcome(),
                ),
            ):
                result = run_retire_task_construction(run_dir)

            self.assertEqual(result.summary["repos"], 2)
            self.assertEqual(max_active, 2)
            self.assertEqual(
                _read_baseline_status(
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "baselines"
                    / "repo-a.json"
                ),
                "passed",
            )
            self.assertEqual(
                _read_baseline_status(
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "baselines"
                    / "repo-b.json"
                ),
                "passed",
            )

    def test_pair_phase_skips_repo_after_baseline_infra_error(self) -> None:
        with TemporaryDirectory() as tmp:
            first = _candidate("pair-a", offset=1)
            first.update({"repo_key": "repo-a", "full_name": "owner/repo-a"})
            second = _candidate("pair-b", offset=1)
            second.update({"repo_key": "repo-b", "full_name": "owner/repo-b"})
            run_dir = _minimal_retire_run(Path(tmp), [first, second])
            attempts: dict[str, int] = {}
            constructed_pairs: list[str] = []

            def run_validation(
                docker_tag: str, *_: object, **__: object
            ) -> ValidationSuiteResult:
                attempts[docker_tag] = attempts.get(docker_tag, 0) + 1
                if docker_tag == "image-repo-a":
                    return _validation_suite(_infra_validation_result())
                return _validation_suite(_passed_validation_result())

            def construct_pair(
                *args: object, **__: object
            ) -> PairConstructionOutcome:
                candidate = args[2]
                assert isinstance(candidate, dict)
                constructed_pairs.append(str(candidate["pair_id"]))
                return _no_f2p_outcome()

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(
                    max_concurrent_repo_baselines=2,
                    max_concurrent_pairs=2,
                )
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config",
                    return_value=config,
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    side_effect=lambda _, repo_key: f"image-{repo_key}",
                ),
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    side_effect=run_validation,
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=construct_pair,
                ) as construct_pair_mock,
            ):
                result = run_retire_task_construction(run_dir)

            baseline_dir = (
                run_dir / "retire_task_construction" / "checkpoints" / "baselines"
            )
            self.assertEqual(attempts, {"image-repo-a": 1, "image-repo-b": 1})
            self.assertEqual(constructed_pairs, ["pair-b"])
            self.assertEqual(construct_pair_mock.call_count, 1)
            self.assertEqual(result.summary["pair_status_counts"]["completed"], 1)
            self.assertEqual(
                result.summary["baseline_status_counts"],
                {"passed": 1, "failed": 0, "infra_error": 1},
            )
            self.assertEqual(
                _read_baseline_status(baseline_dir / "repo-a.json"),
                "infra_error",
            )
            self.assertEqual(
                _read_baseline_status(baseline_dir / "repo-b.json"),
                "passed",
            )

    def test_completed_baseline_checkpoint_survives_later_interruption(self) -> None:
        with TemporaryDirectory() as tmp:
            first = _candidate("pair-a", offset=1)
            first.update({"repo_key": "repo-a", "full_name": "owner/repo-a"})
            second = _candidate("pair-b", offset=1)
            second.update({"repo_key": "repo-b", "full_name": "owner/repo-b"})
            run_dir = _minimal_retire_run(Path(tmp), [first, second])
            first_status_written = Event()

            def run_validation(
                docker_tag: str, *_: object, **__: object
            ) -> ValidationSuiteResult:
                if docker_tag == "image-repo-a":
                    return _validation_suite(_passed_validation_result())
                first_status_written.wait(timeout=5)
                raise InterruptedError("stopped")

            def write_checkpoint(path: Path, payload: dict[str, object]) -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(payload), encoding="utf-8")
                if path.name == "repo-a.json":
                    first_status_written.set()

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(
                    max_concurrent_repo_baselines=2,
                    max_concurrent_pairs=1,
                )
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config",
                    return_value=config,
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    side_effect=lambda _, repo_key: f"image-{repo_key}",
                ),
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    side_effect=run_validation,
                ),
                patch(
                    "belta.retire_task_construction._write_json_atomic",
                    side_effect=write_checkpoint,
                ),
            ):
                with self.assertRaises(InterruptedError):
                    run_retire_task_construction(run_dir)

            baseline_dir = (
                run_dir / "retire_task_construction" / "checkpoints" / "baselines"
            )
            self.assertEqual(
                _read_baseline_status(baseline_dir / "repo-a.json"),
                "passed",
            )
            self.assertFalse((baseline_dir / "repo-b.json").exists())

    def test_completed_pair_checkpoint_is_reused_without_docker_build(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _minimal_retire_run(Path(tmp), [_candidate("pair-1", offset=1)])

            def construct_pair_with_results(
                *_: object, **kwargs: object
            ) -> PairConstructionOutcome:
                test_results_path = kwargs["test_results_path"]
                assert isinstance(test_results_path, Path)
                _write_pair_test_results(test_results_path, ["passed"])
                return _no_f2p_outcome()

            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    return_value="pair-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ) as run_validation,
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=construct_pair_with_results,
                ) as construct_pair,
            ):
                first = run_retire_task_construction(run_dir)
                second = run_retire_task_construction(run_dir)

            self.assertEqual(first.summary["tasks"], 0)
            self.assertEqual(second.summary["tasks"], 0)
            self.assertEqual(build_image.call_count, 1)
            self.assertEqual(run_validation.call_count, 1)
            self.assertEqual(construct_pair.call_count, 1)
            self.assertEqual(
                _read_baseline_status(
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "baselines"
                    / "repo.json"
                ),
                "passed",
            )
            checkpoint = json.loads(
                (
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "pairs"
                    / "pair-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["status"], "completed")
            self.assertEqual(checkpoint["selection_result"], "no_f2p")

    def test_docker_build_infra_error_is_checkpointed_without_pairs(self) -> None:
        with TemporaryDirectory() as tmp:
            rows = [_candidate(f"pair-{index}", offset=index) for index in range(1, 4)]
            run_dir = _minimal_retire_run(Path(tmp), rows)
            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    side_effect=RuntimeError("docker build failed"),
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    return_value=_no_f2p_outcome(),
                ) as construct_pair,
            ):
                result = run_retire_task_construction(run_dir)

            self.assertEqual(build_image.call_count, 1)
            self.assertEqual(construct_pair.call_count, 0)
            self.assertEqual(result.summary["tasks"], 0)
            self.assertEqual(result.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(
                _read_baseline_status(
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "baselines"
                    / "repo.json"
                ),
                "infra_error",
            )
            repo_result = _read_jsonl(
                run_dir / "retire_task_construction" / "results" / "repos.jsonl"
            )[0]
            self.assertEqual(repo_result["baseline_status"], "infra_error")
            self.assertEqual(repo_result["selection_counts"]["no_f2p"], 0)

    def test_new_commit_shared_validation_failure_discards_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            rows = [_candidate("pair-1", offset=1), _candidate("pair-2", offset=2)]
            run_dir = _minimal_retire_run(Path(tmp), rows)
            checkpoint_dir = (
                run_dir / "retire_task_construction" / "checkpoints" / "pairs"
            )
            checkpoint_dir.mkdir(parents=True)
            (checkpoint_dir / "pair-1.json").write_text(
                json.dumps(
                    {
                        "pair_id": "pair-1",
                        "repo_key": "repo",
                        "full_name": "owner/repo",
                        "old_commit": "old-pair-1",
                        "new_commit": "new-commit",
                        "offset": 1,
                        "input_fingerprint": "pair-fingerprint-pair-1",
                        "status": "completed",
                        "selection_result": "no_f2p",
                        "test_summary": _test_summary(failed=0),
                    }
                ),
                encoding="utf-8",
            )
            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ) as pair_materials_ready,
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"pair-fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ),
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_failed_validation_result()),
                ) as run_validation,
                patch(
                    "belta.retire_task_construction._construct_pair"
                ) as construct_pair,
            ):
                first = run_retire_task_construction(run_dir)
                second = run_retire_task_construction(run_dir)

            construct_pair.assert_not_called()
            pair_materials_ready.assert_not_called()
            self.assertEqual(run_validation.call_count, 1)
            self.assertEqual(first.summary["tasks"], 0)
            self.assertEqual(second.summary["baseline_status_counts"]["failed"], 1)
            self.assertEqual(second.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(
                second.summary["pair_status_counts"]["construction_failed"], 0
            )
            self.assertEqual(
                _read_baseline_status(
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "baselines"
                    / "repo.json"
                ),
                "failed",
            )
            repo_result = _read_jsonl(
                run_dir / "retire_task_construction" / "results" / "repos.jsonl"
            )[0]
            self.assertEqual(
                repo_result,
                {
                    "repo_key": "repo",
                    "baseline_status": "failed",
                    "pair_status_counts": {
                        "completed": 0,
                        "construction_failed": 0,
                        "infra_error": 0,
                    },
                    "selection_counts": {"main_task": 0, "no_f2p": 0},
                    "tasks": 0,
                },
            )
            self.assertFalse(
                any(
                    (
                        run_dir / "retire_task_construction" / "checkpoints" / "pairs"
                    ).iterdir()
                )
            )
            self.assertEqual(
                (
                    run_dir / "retire_task_construction" / "results" / "pairs.jsonl"
                ).read_text(encoding="utf-8"),
                "",
            )

    def test_baseline_infra_error_is_retried_on_next_run(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _minimal_retire_run(Path(tmp), [_candidate("pair-1", offset=1)])
            baseline_path = (
                run_dir
                / "retire_task_construction"
                / "checkpoints"
                / "baselines"
                / "repo.json"
            )
            validation_attempt = 0

            def run_validation(*_: object, **__: object) -> ValidationSuiteResult:
                nonlocal validation_attempt
                validation_attempt += 1
                if validation_attempt == 1:
                    return _validation_suite(_infra_validation_result())
                self.assertEqual(
                    _read_baseline_status(baseline_path),
                    "infra_error",
                )
                return _validation_suite(_passed_validation_result())

            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    return_value="pair-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    side_effect=run_validation,
                ) as run_validation,
                patch(
                    "belta.retire_task_construction._construct_pair",
                    return_value=_no_f2p_outcome(),
                ) as construct_pair,
            ):
                first = run_retire_task_construction(run_dir)
                self.assertEqual(
                    _read_baseline_status(baseline_path),
                    "infra_error",
                )
                second = run_retire_task_construction(run_dir)

            self.assertEqual(first.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(second.summary["pair_status_counts"]["completed"], 1)
            self.assertEqual(build_image.call_count, 2)
            self.assertEqual(run_validation.call_count, 2)
            self.assertEqual(construct_pair.call_count, 1)
            self.assertEqual(_read_baseline_status(baseline_path), "passed")

    def test_manual_resume_retries_baseline_and_pair_infra_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            first_candidate = _candidate("pair-a", offset=1)
            first_candidate.update(
                {"repo_key": "repo-a", "full_name": "owner/repo-a"}
            )
            second_candidate = _candidate("pair-b", offset=1)
            second_candidate.update(
                {"repo_key": "repo-b", "full_name": "owner/repo-b"}
            )
            run_dir = _minimal_retire_run(
                Path(tmp),
                [first_candidate, second_candidate],
            )
            validation_attempts: dict[str, int] = {}
            pair_attempts: dict[str, int] = {}

            def run_validation(
                docker_tag: str, *_: object, **__: object
            ) -> ValidationSuiteResult:
                validation_attempts[docker_tag] = (
                    validation_attempts.get(docker_tag, 0) + 1
                )
                if (
                    docker_tag == "image-repo-a"
                    and validation_attempts[docker_tag] == 1
                ):
                    return _validation_suite(_infra_validation_result())
                return _validation_suite(_passed_validation_result())

            def construct_pair(
                *args: object, **__: object
            ) -> PairConstructionOutcome:
                candidate = args[2]
                assert isinstance(candidate, dict)
                pair_id = str(candidate["pair_id"])
                pair_attempts[pair_id] = pair_attempts.get(pair_id, 0) + 1
                if pair_id == "pair-b" and pair_attempts[pair_id] == 1:
                    return _infra_outcome()
                return _no_f2p_outcome()

            config = SimpleNamespace(
                retire_task_construction=SimpleNamespace(
                    max_concurrent_repo_baselines=2,
                    max_concurrent_pairs=2,
                )
            )
            with (
                patch(
                    "belta.retire_task_construction.load_config",
                    return_value=config,
                ),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    side_effect=lambda candidate, *_: (
                        f"fingerprint-{candidate['pair_id']}"
                    ),
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    side_effect=lambda _, repo_key: f"image-{repo_key}",
                ),
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    side_effect=run_validation,
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=construct_pair,
                ),
            ):
                first = run_retire_task_construction(run_dir)
                second = run_retire_task_construction(run_dir)

            self.assertEqual(first.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(first.summary["pair_status_counts"]["infra_error"], 1)
            self.assertEqual(second.summary["pair_status_counts"]["completed"], 2)
            self.assertEqual(second.summary["pair_status_counts"]["infra_error"], 0)
            self.assertEqual(
                validation_attempts,
                {"image-repo-a": 2, "image-repo-b": 1},
            )
            self.assertEqual(pair_attempts, {"pair-a": 1, "pair-b": 2})

    def test_construction_failed_pair_checkpoint_is_retried(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _minimal_retire_run(Path(tmp), [_candidate("pair-1", offset=1)])
            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    return_value="pair-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    return_value=_construction_failed_outcome(),
                ) as construct_pair,
            ):
                first = run_retire_task_construction(run_dir)
                second = run_retire_task_construction(run_dir)

            self.assertEqual(
                first.summary["pair_status_counts"]["construction_failed"], 1
            )
            self.assertEqual(second.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(
                second.summary["pair_status_counts"]["construction_failed"], 1
            )
            self.assertEqual(build_image.call_count, 2)
            self.assertEqual(construct_pair.call_count, 2)
            checkpoint = json.loads(
                (
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "pairs"
                    / "pair-1.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["status"], "construction_failed")
            self.assertNotIn("selection_result", checkpoint)
            self.assertNotIn("status_code", checkpoint)

    def test_infra_error_pair_checkpoint_is_retried(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = _minimal_retire_run(Path(tmp), [_candidate("pair-1", offset=1)])
            with (
                patch("belta.retire_task_construction.load_config"),
                patch(
                    "belta.retire_task_construction._load_environment",
                    return_value={"status": "completed"},
                ),
                patch(
                    "belta.retire_task_construction._validation_test_universe",
                    return_value=[{"test_file": "tests/test_api.py"}],
                ),
                patch(
                    "belta.retire_task_construction._pair_materials_ready",
                    return_value=True,
                ),
                patch(
                    "belta.retire_task_construction._environment_input_fingerprint",
                    return_value="environment-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._pair_input_fingerprint",
                    return_value="pair-fingerprint",
                ),
                patch(
                    "belta.retire_task_construction._build_environment_image",
                    return_value="image",
                ) as build_image,
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ),
                patch(
                    "belta.retire_task_construction._construct_pair",
                    side_effect=[_infra_outcome(), _no_f2p_outcome()],
                ) as construct_pair,
            ):
                first = run_retire_task_construction(run_dir)
                checkpoint_path = (
                    run_dir
                    / "retire_task_construction"
                    / "checkpoints"
                    / "pairs"
                    / "pair-1.json"
                )
                first_checkpoint = json.loads(
                    checkpoint_path.read_text(encoding="utf-8")
                )
                second = run_retire_task_construction(run_dir)

            self.assertEqual(first.summary["pair_status_counts"]["completed"], 0)
            self.assertEqual(
                first.summary["pair_status_counts"]["construction_failed"], 0
            )
            self.assertEqual(first_checkpoint["status"], "infra_error")
            self.assertNotIn("selection_result", first_checkpoint)
            self.assertNotIn("status_code", first_checkpoint)
            self.assertEqual(second.summary["pair_status_counts"]["completed"], 1)
            self.assertEqual(build_image.call_count, 2)
            self.assertEqual(construct_pair.call_count, 2)
            final_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(final_checkpoint["status"], "completed")
            self.assertEqual(final_checkpoint["selection_result"], "no_f2p")

    def test_no_f2p_pair_does_not_materialize_host_task_base(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks = root / "tasks"

            with (
                patch(
                    "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_passed_validation_result()),
                ) as run_validation,
                patch(
                    "belta.retire_task_construction._checkout_commit"
                ) as checkout_commit,
            ):
                outcome = _construct_pair(
                    root,
                    tasks,
                    _candidate("pair-A", offset=1),
                    root / "pair-A",
                    root / "environment",
                    {"status": "completed"},
                    [{"test_file": "tests/test_api.py"}],
                    "base-image",
                )

            self.assertEqual(outcome.selection_result, "no_f2p")
            checkout_commit.assert_not_called()
            self.assertEqual(
                run_validation.call_args.kwargs["patch_path"],
                root / "pair-A" / "obsolete_reinsert.patch",
            )
            self.assertEqual(run_validation.call_args.args[0], "base-image")

    def test_pair_checkpoint_with_skipped_only_failed_status_is_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "pair.jsonl"
            row = _test_result_row(1, "failed")
            row.update(
                total_tests=1,
                failed_tests=0,
                skipped_tests=1,
            )
            row["raw_result"] = {
                "action": "run",
                "status": "failed",
                "summary": {
                    "collected": 1,
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 1,
                },
            }
            results_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            reusable = retire._completed_test_results_ready(
                results_path,
                {
                    "task_base_total_files": 1,
                    "task_base_passed_files": 0,
                    "task_base_failed_files": 1,
                    "task_base_error_files": 0,
                },
            )

        self.assertTrue(reusable)

    def test_pair_with_skipped_only_failed_file_is_main_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = {
                "collected": 10,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 10,
            }
            result = ValidationRunResult(
                test_file="tests/test_api.py",
                target_selector="tests/test_api.py",
                status="failed",
                summary=summary,
                raw_result={"action": "run", "status": "failed", "summary": summary},
                exit_code=0,
            )
            outcome = _construct_pair_with_result(root, result)

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.selection_result, "main_task")
        self.assertEqual(outcome.test_summary["task_base_failed_files"], 1)
        self.assertIsNone(outcome.error)

    def test_pair_checkpoint_with_zero_collected_failed_status_is_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "pair.jsonl"
            row = _test_result_row(1, "failed")
            row.update(
                total_tests=0,
                failed_tests=0,
                skipped_tests=0,
            )
            row["raw_result"] = {
                "action": "run",
                "status": "failed",
                "summary": {
                    "collected": 0,
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "skipped": 0,
                },
            }
            results_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            reusable = retire._completed_test_results_ready(
                results_path,
                {
                    "task_base_total_files": 1,
                    "task_base_passed_files": 0,
                    "task_base_failed_files": 1,
                    "task_base_error_files": 0,
                },
            )

        self.assertTrue(reusable)

    def test_pair_with_zero_collected_failed_file_is_main_task(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = {
                "collected": 0,
                "passed": 0,
                "failed": 0,
                "errors": 0,
                "skipped": 0,
            }
            result = ValidationRunResult(
                test_file="tests/test_api.py",
                target_selector="tests/test_api.py",
                status="failed",
                summary=summary,
                raw_result={"action": "run", "status": "failed", "summary": summary},
                exit_code=5,
            )
            outcome = _construct_pair_with_result(root, result)

        self.assertEqual(outcome.status, "completed")
        self.assertEqual(outcome.selection_result, "main_task")
        self.assertEqual(outcome.test_summary["task_base_failed_files"], 1)
        self.assertIsNone(outcome.error)

    def test_pair_checkpoint_with_collection_error_is_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "pair.jsonl"
            row = _test_result_row(1, "failed")
            row.update(
                total_tests=1,
                failed_tests=0,
                error_tests=1,
            )
            row["raw_result"] = {
                "action": "run",
                "status": "failed",
                "summary": {
                    "collected": 1,
                    "passed": 0,
                    "failed": 0,
                    "errors": 1,
                    "skipped": 0,
                },
            }
            results_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            reusable = retire._completed_test_results_ready(
                results_path,
                {
                    "task_base_total_files": 1,
                    "task_base_passed_files": 0,
                    "task_base_failed_files": 1,
                    "task_base_error_files": 0,
                },
            )

        self.assertTrue(reusable)

    def test_pair_checkpoint_with_inconsistent_summary_is_not_reused(self) -> None:
        with TemporaryDirectory() as tmp:
            results_path = Path(tmp) / "pair.jsonl"
            row = _test_result_row(1, "failed")
            row["raw_result"]["summary"]["errors"] = 2
            results_path.write_text(json.dumps(row) + "\n", encoding="utf-8")

            reusable = retire._completed_test_results_ready(
                results_path,
                {
                    "task_base_total_files": 1,
                    "task_base_passed_files": 0,
                    "task_base_failed_files": 1,
                    "task_base_error_files": 0,
                },
            )

        self.assertFalse(reusable)

    def test_pair_test_infrastructure_error_is_not_a_test_failure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch(
                "belta.retire_task_construction._run_validation_tests",
                    return_value=_validation_suite(_infra_validation_result()),
            ):
                outcome = _construct_pair(
                    root,
                    root / "tasks",
                    _candidate("pair-A", offset=1),
                    root / "pair-A",
                    root / "environment",
                    {"status": "completed"},
                    [{"test_file": "tests/test_api.py"}],
                    "base-image",
                )

            self.assertEqual(outcome.status, "infra_error")
            self.assertIsNone(outcome.selection_result)


def _write_baseline_checkpoint(
    path: Path,
    status: str,
    *,
    repo_key: str | None = None,
    input_fingerprint: str = "environment-fingerprint",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    passed = int(status == "passed")
    failed = int(status == "failed")
    path.write_text(
        json.dumps(
            {
                "repo_key": repo_key or path.stem,
                "status": status,
                "input_fingerprint": input_fingerprint,
                "test_summary": {
                    "new_commit_total_files": passed + failed,
                    "new_commit_passed_files": passed,
                    "new_commit_failed_files": failed,
                    "new_commit_error_files": 0,
                },
                "error": None,
            }
        ),
        encoding="utf-8",
    )
    if status in {"passed", "failed"}:
        results_path = (
            path.parents[2]
            / "results"
            / "baseline_test_results"
            / f"{path.stem}.jsonl"
        )
        _write_test_results(results_path, [status])


def _read_baseline_status(path: Path) -> str:
    return str(json.loads(path.read_text(encoding="utf-8"))["status"])


def _write_pair_test_results(path: Path, statuses: list[str]) -> None:
    _write_test_results(path, statuses)


def _write_test_results(path: Path, statuses: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(_test_result_row(index, status))
            + "\n"
            for index, status in enumerate(statuses, start=1)
        ),
        encoding="utf-8",
    )


def _test_result_row(index: int, status: str) -> dict[str, object]:
    passed = int(status == "passed")
    failed = int(status == "failed")
    errors = int(status == "error")
    collected = passed + failed
    summary = {
        "collected": collected,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "skipped": 0,
    }
    return {
        "test_file": f"tests/test_{index}.py",
        "target_selector": f"tests/test_{index}.py",
        "status": status,
        "total_tests": collected,
        "passed_tests": passed,
        "failed_tests": failed,
        "error_tests": errors,
        "skipped_tests": 0,
        "exit_code": 0 if status == "passed" else (1 if status == "failed" else 124),
        "raw_result": {
            "action": "run",
            "status": status,
            "summary": summary,
        },
    }


def _candidate(pair_id: str, *, offset: int) -> dict[str, object]:
    return {
        "pair_id": pair_id,
        "repo_key": "repo",
        "full_name": "owner/repo",
        "github_repo_id": 1,
        "clone_url": "https://github.com/owner/repo.git",
        "default_branch": "main",
        "old_commit": f"old-{pair_id}",
        "new_commit": "new-commit",
        "offset": offset,
    }


def _test_summary(*, failed: int) -> dict[str, int]:
    return {
        "new_commit_total_files": 1,
        "new_commit_passed_files": 1,
        "task_base_total_files": 1,
        "task_base_passed_files": 1 - failed,
        "task_base_failed_files": failed,
        "task_base_error_files": 0,
    }


def _main_task_outcome(pair_id: str) -> PairConstructionOutcome:
    return PairConstructionOutcome(
        status="completed",
        selection_result="main_task",
        test_summary=_test_summary(failed=1),
    )


def _no_f2p_outcome() -> PairConstructionOutcome:
    return PairConstructionOutcome(
        status="completed",
        selection_result="no_f2p",
        test_summary=_test_summary(failed=0),
    )


def _construction_failed_outcome() -> PairConstructionOutcome:
    return PairConstructionOutcome(
        status="construction_failed",
        error="construction failed",
    )


def _infra_outcome() -> PairConstructionOutcome:
    return PairConstructionOutcome(
        status="infra_error",
        test_summary=_test_summary(failed=0),
        error="validation infrastructure failed",
    )


def _passed_validation_result() -> ValidationRunResult:
    summary = {
        "collected": 1,
        "passed": 1,
        "failed": 0,
        "errors": 0,
        "skipped": 0,
    }
    return ValidationRunResult(
        test_file="tests/test_api.py",
        target_selector="tests/test_api.py",
        status="passed",
        summary=summary,
        raw_result={"action": "run", "status": "passed", "summary": summary},
        exit_code=0,
    )


def _failed_validation_result() -> ValidationRunResult:
    summary = {
        "collected": 1,
        "passed": 0,
        "failed": 1,
        "errors": 0,
        "skipped": 0,
    }
    return ValidationRunResult(
        test_file="tests/test_api.py",
        target_selector="tests/test_api.py",
        status="failed",
        summary=summary,
        raw_result={"action": "run", "status": "failed", "summary": summary},
        exit_code=1,
    )


def _infra_validation_result() -> ValidationRunResult:
    return ValidationRunResult(
        test_file="tests/test_api.py",
        target_selector="tests/test_api.py",
        status="error",
        summary={"collected": 0, "passed": 0, "failed": 0, "errors": 1, "skipped": 0},
        raw_result={"action": "run", "status": "error"},
        exit_code=1,
        infra_error="validation container stopped",
    )


def _validation_suite(
    *results: ValidationRunResult,
    error: str | None = None,
) -> ValidationSuiteResult:
    return ValidationSuiteResult(list(results), error=error)


def _construct_pair_with_result(
    root: Path,
    result: ValidationRunResult,
) -> PairConstructionOutcome:
    def make_directory(_source: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)

    with (
        patch(
            "belta.retire_task_construction._run_validation_tests",
            return_value=_validation_suite(result),
        ),
        patch("belta.retire_task_construction._repo_cache_dir", return_value=root),
        patch("belta.retire_task_construction._checkout_commit"),
        patch("belta.retire_task_construction._apply_patch"),
        patch(
            "belta.retire_task_construction._copy_private_materials",
            side_effect=make_directory,
        ),
        patch(
            "belta.retire_task_construction._gold_cleanup_patch",
            return_value=b"gold patch\n",
        ),
        patch(
            "belta.retire_task_construction._snapshot_validation_tests",
            return_value=[],
        ),
        patch(
            "belta.retire_task_construction._copy_environment_assets",
            side_effect=make_directory,
        ),
        patch(
            "belta.retire_task_construction._task_manifest",
            return_value={"pair_id": "pair-A"},
        ),
    ):
        return _construct_pair(
            root,
            root / "tasks",
            _candidate("pair-A", offset=1),
            root / "pair-A",
            root / "environment",
            {"status": "completed"},
            [{"test_file": "tests/test_api.py"}],
            "base-image",
        )


def _minimal_retire_run(root: Path, rows: list[dict[str, object]]) -> Path:
    (root / "config.yaml").write_text("project: {}\n", encoding="utf-8")
    candidate_dir = root / "candidate_pairs"
    candidate_dir.mkdir()
    (candidate_dir / "accepted_candidates.jsonl").write_text(
        "".join(f"{json.dumps(row)}\n" for row in rows),
        encoding="utf-8",
    )
    (root / "environments").mkdir()
    return root


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


if __name__ == "__main__":
    unittest.main()
