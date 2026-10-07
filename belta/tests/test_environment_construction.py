from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from belta.environment_construction import (
    _accepted_repositories,
    _exclusive_environment_construction_lock,
    _export_reusable_stage2_run,
    _recover_interrupted_belta_stage2_runs,
    _require_stage2_prewarm,
    _reusable_environment_manifest,
    _stage2_test_results,
    _stage2_prewarm_ready,
    _test_results_from_full_report,
    run_environment_construction,
    run_environment_prewarm,
)


class EnvironmentConstructionTests(unittest.TestCase):
    def test_environment_construction_lock_rejects_second_process(self) -> None:
        with TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "environment.lock"
            with _exclusive_environment_construction_lock(lock_path):
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    with _exclusive_environment_construction_lock(lock_path):
                        self.fail("second lock unexpectedly acquired")

    def test_interrupted_belta_stage2_runs_are_recovered_for_retry(self) -> None:
        class Column:
            def in_(self, _: object) -> object:
                return object()

            def __eq__(self, _: object) -> object:
                return object()

            def asc(self) -> object:
                return object()

        class Stage2Run:
            status = Column()
            trigger_kind = Column()
            created_at = Column()
            id = Column()

        class Stage2RunStatus:
            queued = Mock(value="queued")
            running = Mock(value="running")

        query = Mock()
        query.where.return_value = query
        query.order_by.return_value = query
        run = Mock(id="interrupted-run")
        session = Mock()
        session.scalars.return_value = [run]
        service = Mock()

        with patch("belta.environment_construction.select", return_value=query):
            recovered = _recover_interrupted_belta_stage2_runs(
                session,
                service=service,
                Stage2Run=Stage2Run,
                Stage2RunStatus=Stage2RunStatus,
            )

        self.assertEqual(recovered, ["interrupted-run"])
        service.mark_run_interrupted.assert_called_once_with(
            "interrupted-run",
            error_message=(
                "Belta Environment Construction process ended before completion; "
                "the next invocation will retry this repository"
            ),
        )
        session.commit.assert_called_once_with()

    def test_stage2_prewarm_requires_host_and_planner(self) -> None:
        ready = _prewarm_status(host_ready=True, planner_ready=True)
        self.assertTrue(_stage2_prewarm_ready(ready))

        for status in (
            _prewarm_status(host_ready=False, planner_ready=True),
            _prewarm_status(host_ready=True, planner_ready=False),
        ):
            self.assertFalse(_stage2_prewarm_ready(status))
            with self.assertRaisesRegex(RuntimeError, "prewarm-environment"):
                _require_stage2_prewarm(status, Path("/tmp/example-run"))

    def test_explicit_prewarm_uses_featurefactory_runner(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            (run_dir / "config.yaml").write_text("project: {}\n", encoding="utf-8")
            backend = Mock()
            backend.prewarm.return_value = _prewarm_status(
                host_ready=True,
                planner_ready=True,
            )
            logs: list[str] = []
            callback = logs.append

            with (
                patch("belta.environment_construction.load_config", return_value=Mock()),
                patch(
                    "belta.environment_construction.FeatureFactoryStage2Runner",
                    return_value=backend,
                ),
            ):
                result = run_environment_prewarm(run_dir, log_callback=callback)

            backend.prewarm.assert_called_once_with(log_callback=callback)
            self.assertTrue(result.host_environment_ready)
            self.assertTrue(result.planner_ready)
            self.assertEqual(result.agent_server_image, "feature-factory/planner:test")

    def test_environment_construction_attempts_prewarm_before_export(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            _write_minimal_run_input(run_dir)
            backend = Mock()
            backend.prewarm.return_value = _prewarm_status(
                host_ready=True,
                planner_ready=False,
            )
            logs: list[str] = []

            with (
                patch("belta.environment_construction.load_config", return_value=Mock()),
                patch(
                    "belta.environment_construction.FeatureFactoryStage2Runner",
                    return_value=backend,
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "planner_agent_server_image"):
                    run_environment_construction(run_dir, log_callback=logs.append)

            backend.prewarm.assert_called_once_with(log_callback=logs.append)
            backend.export.assert_not_called()

    def test_environment_construction_continues_after_automatic_prewarm(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            repo = _write_minimal_run_input(run_dir)
            backend = Mock()
            backend.prewarm.return_value = _prewarm_status(
                host_ready=True,
                planner_ready=True,
            )
            backend.export.return_value = [
                {
                    "repo_key": repo["repo_key"],
                    "status": "completed",
                    "status_code": None,
                }
            ]

            with (
                patch("belta.environment_construction.load_config", return_value=Mock()),
                patch(
                    "belta.environment_construction.FeatureFactoryStage2Runner",
                    return_value=backend,
                ),
            ):
                result = run_environment_construction(run_dir)

            backend.prewarm.assert_called_once_with(log_callback=None)
            backend.export.assert_called_once()
            self.assertEqual(result.completed_environments, 1)

    def test_environment_construction_prewarms_before_reusing_all_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            repo = _write_minimal_run_input(run_dir)
            output_dir = run_dir / "environments" / str(repo["repo_key"])
            _write_reusable_environment(output_dir, repo)
            manifest = _reusable_environment_manifest(output_dir, repo)
            self.assertIsNotNone(manifest)

            backend = Mock()
            backend.prewarm.return_value = _prewarm_status(
                host_ready=True,
                planner_ready=True,
            )
            backend.export.return_value = [manifest]
            with (
                patch("belta.environment_construction.load_config", return_value=Mock()),
                patch(
                    "belta.environment_construction.FeatureFactoryStage2Runner",
                    return_value=backend,
                ),
            ):
                result = run_environment_construction(run_dir)

            backend.prewarm.assert_called_once_with(log_callback=None)
            backend.export.assert_called_once()
            self.assertEqual(result.completed_environments, 1)

    def test_accepted_repositories_require_single_new_commit_per_repo(self) -> None:
        rows = [
            {
                "repo_key": "example__repo",
                "full_name": "example/repo",
                "github_repo_id": 123,
                "clone_url": "https://github.com/example/repo.git",
                "default_branch": "main",
                "new_commit": "a" * 40,
            },
            {
                "repo_key": "example__repo",
                "full_name": "example/repo",
                "github_repo_id": 123,
                "clone_url": "https://github.com/example/repo.git",
                "default_branch": "main",
                "new_commit": "a" * 40,
            },
        ]

        repos = _accepted_repositories(rows)

        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0]["repo_key"], "example__repo")

        rows[1]["new_commit"] = "b" * 40
        with self.assertRaisesRegex(ValueError, "multiple new_commit"):
            _accepted_repositories(rows)

    def test_full_report_file_results_export_file_level_status(self) -> None:
        full_report = {
            "status": "passed",
            "file_results": [
                {
                    "test_file_path": "Tests/test_ok.py",
                    "target_selector": "test_ok.py",
                    "returncode": 0,
                    "result": {
                        "status": "passed",
                        "summary": {
                            "collected": 2,
                            "passed": 2,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 0,
                        },
                    },
                },
                {
                    "test_file_path": "tests/test_all_skipped.py",
                    "returncode": 0,
                    "result": {
                        "status": "failed",
                        "summary": {
                            "collected": 3,
                            "passed": 0,
                            "failed": 0,
                            "errors": 0,
                            "skipped": 3,
                        },
                    },
                },
            ],
        }

        rows = _test_results_from_full_report(full_report)

        self.assertEqual([row["status"] for row in rows], ["passed", "failed"])
        passed = [row for row in rows if row["status"] == "passed"]
        self.assertEqual(len(passed), 1)
        self.assertEqual(passed[0]["test_file"], "Tests/test_ok.py")
        self.assertEqual(passed[0]["target_selector"], "test_ok.py")

    def test_stage2_rows_recover_selector_from_full_report(self) -> None:
        run = Mock()
        row = Mock()
        row.test_file_path = "Tests/test_Ace.py"
        row.target_selector = None
        row.status = "passed"
        row.total_tests = 1
        row.passed_tests = 1
        row.failed_tests = 0
        row.error_tests = 0
        row.skipped_tests = 0
        row.exit_code = 0
        row.raw_result_json = {"status": "passed"}
        run.test_results = [row]

        rows = _stage2_test_results(
            run,
            full_report={
                "file_results": [
                    {
                        "test_file_path": "Tests/test_Ace.py",
                        "target_selector": "test_Ace.py",
                    }
                ]
            },
        )

        self.assertEqual(rows[0]["test_file"], "Tests/test_Ace.py")
        self.assertEqual(rows[0]["target_selector"], "test_Ace.py")

    def test_reusable_environment_requires_belta_completed_output(self) -> None:
        repo = {"new_commit": "a" * 40}
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            (output_dir / "manifest.json").write_text(
                '{"status": "failed", "target_commit_sha": "' + "a" * 40 + '"}\n',
                encoding="utf-8",
            )

            self.assertIsNone(_reusable_environment_manifest(output_dir, repo))

            (output_dir / "manifest.json").write_text(
                '{"status": "completed", "target_commit_sha": "' + "a" * 40 + '"}\n',
                encoding="utf-8",
            )
            for filename in (
                "planner_guidance.md",
                "Dockerfile",
                "run_script.sh",
                "collect_report.json",
                "full_report.json",
            ):
                (output_dir / filename).write_text("", encoding="utf-8")
            (output_dir / "test_results.jsonl").write_text(
                '{"test_file":"tests/test_api.py",'
                '"target_selector":"tests/test_api.py","status":"passed"}\n',
                encoding="utf-8",
            )

            self.assertIsNotNone(_reusable_environment_manifest(output_dir, repo))

            (output_dir / "test_results.jsonl").write_text(
                '{"test_file":"tests/test_api.py","status":"passed"}\n',
                encoding="utf-8",
            )
            self.assertIsNone(_reusable_environment_manifest(output_dir, repo))

    def test_successful_ff_run_for_exact_commit_is_exported(self) -> None:
        repo = {
            "repo_key": "example__repo",
            "new_commit": "a" * 40,
        }
        repository = Mock(id=123)
        run = Mock(id="stage2-run")
        service = Mock()
        service.successful_run_for_repository_commit.return_value = run
        expected = {"status": "completed", "ff_stage2_run_id": "stage2-run"}

        with patch(
            "belta.environment_construction._export_stage2_run",
            return_value=expected,
        ) as export_run:
            result = _export_reusable_stage2_run(
                service=service,
                repo=repo,
                repository=repository,
                output_dir=Path("/tmp/environment"),
            )

        self.assertEqual(result, expected)
        service.successful_run_for_repository_commit.assert_called_once_with(
            123,
            "a" * 40,
        )
        export_run.assert_called_once_with(
            repo,
            repository,
            run,
            Path("/tmp/environment"),
        )

    def test_missing_successful_ff_run_requires_new_stage2_run(self) -> None:
        service = Mock()
        service.successful_run_for_repository_commit.return_value = None

        result = _export_reusable_stage2_run(
            service=service,
            repo={"new_commit": "b" * 40},
            repository=Mock(id=456),
            output_dir=Path("/tmp/environment"),
        )

        self.assertIsNone(result)
        service.successful_run_for_repository_commit.assert_called_once_with(
            456,
            "b" * 40,
        )


def _prewarm_status(*, host_ready: bool, planner_ready: bool) -> dict[str, object]:
    return {
        "platform": "linux/amd64",
        "mirror_profile": "test",
        "host_environment": {"prewarmed": host_ready},
        "planner": {
            "prewarmed": planner_ready,
            "agent_server_image": {
                "image_ref": "feature-factory/planner:test",
            },
        },
    }


def _write_minimal_run_input(run_dir: Path) -> dict[str, object]:
    (run_dir / "config.yaml").write_text("project: {}\n", encoding="utf-8")
    candidate_dir = run_dir / "candidate_pairs"
    candidate_dir.mkdir(parents=True)
    repo: dict[str, object] = {
        "repo_key": "example__repo",
        "full_name": "example/repo",
        "github_repo_id": 123,
        "clone_url": "https://github.com/example/repo.git",
        "default_branch": "main",
        "new_commit": "a" * 40,
    }
    (candidate_dir / "accepted_candidates.jsonl").write_text(
        json.dumps(repo) + "\n",
        encoding="utf-8",
    )
    return repo


def _write_reusable_environment(output_dir: Path, repo: dict[str, object]) -> None:
    output_dir.mkdir(parents=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(
            {
                "status": "completed",
                "target_commit_sha": repo["new_commit"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    for filename in (
        "planner_guidance.md",
        "Dockerfile",
        "run_script.sh",
        "collect_report.json",
        "full_report.json",
    ):
        (output_dir / filename).write_text("", encoding="utf-8")
    (output_dir / "test_results.jsonl").write_text(
        '{"test_file":"tests/test_api.py",'
        '"target_selector":"tests/test_api.py","status":"passed"}\n',
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
