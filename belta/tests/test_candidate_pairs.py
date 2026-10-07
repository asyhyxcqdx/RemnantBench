from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Event, Lock, Semaphore
from unittest.mock import patch

import belta.candidate_pairs as candidate_pairs
from belta.candidate_pairs import DiffFile, RepoInput, classify_diff_path, classify_path
from belta.config import CandidateOffsetRangeConfig, CandidatePairsConfig


class CandidatePairsConfigTests(unittest.TestCase):
    def test_config_uses_reinsert_thresholds(self) -> None:
        config = CandidatePairsConfig()

        self.assertEqual(config.max_concurrent_repos, 4)
        self.assertEqual(config.max_concurrent_repo_downloads, 4)
        self.assertEqual(config.min_implementation_units, 1)
        self.assertEqual(config.min_reinsert_source_lines, 5)
        self.assertIsNone(config.max_implementation_units)
        self.assertIsNone(config.max_reinsert_source_lines)

    def test_offset_ranges_form_one_ordered_scan_plan(self) -> None:
        config = CandidatePairsConfig(
            offset_ranges=[
                CandidateOffsetRangeConfig(start=1, stop=3, step=1),
                CandidateOffsetRangeConfig(start=5, stop=9, step=2),
            ]
        )

        self.assertEqual(config.offset_values(), (1, 2, 3, 5, 7, 9))

    def test_offset_ranges_reject_overlapping_or_out_of_order_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            CandidatePairsConfig(
                offset_ranges=[
                    CandidateOffsetRangeConfig(start=1, stop=5, step=1),
                    CandidateOffsetRangeConfig(start=5, stop=10, step=1),
                ]
            )

    def test_reinsert_thresholds_reject_over_large_pairs(self) -> None:
        config = CandidatePairsConfig(
            max_implementation_units=200,
            max_reinsert_source_lines=3000,
        )

        reason, detail = candidate_pairs._candidate_filter_rejection(
            {
                "implementation_units_count": 201,
                "reinsert_source_lines": 3000,
            },
            config,
            artifact_error=None,
        )

        self.assertEqual(reason, "too_many_implementation_units")
        self.assertEqual(
            detail,
            {
                "implementation_units_count": 201,
                "max_implementation_units": 200,
            },
        )

        reason, detail = candidate_pairs._candidate_filter_rejection(
            {
                "implementation_units_count": 200,
                "reinsert_source_lines": 3001,
            },
            config,
            artifact_error=None,
        )

        self.assertEqual(reason, "too_many_reinsert_source_lines")
        self.assertEqual(
            detail,
            {
                "reinsert_source_lines": 3001,
                "max_reinsert_source_lines": 3000,
            },
        )


class PathClassificationTests(unittest.TestCase):
    def test_non_implementation_paths_are_ignored(self) -> None:
        self.assertEqual(classify_path("report/src/App.vue"), "ignored")
        self.assertEqual(classify_path("templates/error.html"), "ignored")
        self.assertEqual(classify_path("src/pkg/defaults.json"), "ignored")
        self.assertEqual(classify_path("pyproject.toml"), "ignored")
        self.assertEqual(classify_path("docker/Dockerfile"), "ignored")
        self.assertEqual(classify_path("tests/fixtures/requirements.txt"), "ignored")
        self.assertEqual(classify_path("docs/specs/test_api.py"), "ignored")

    def test_test_roles_are_filtered_before_source_analysis(self) -> None:
        pytest_source = b"import pytest\n\n\ndef test_download():\n    assert True\n"
        runtime_test_named_source = (
            b"from agents.elegantrl_models import DRLAgent\n\n"
            b"def test(drl_lib, env, model_name, **kwargs):\n"
            b"    return DRLAgent.DRL_prediction(model_name=model_name, environment=env)\n"
        )

        self.assertEqual(
            classify_path("web3/_utils/module_testing/eth_module.py"),
            "ignored",
        )
        self.assertEqual(
            classify_path("unit_tests/test_provider.py"),
            "ignored",
        )
        self.assertEqual(classify_path("Tests/helpers.py"), "ignored")
        self.assertEqual(classify_path("cms/test_utils/helpers.py"), "ignored")
        self.assertEqual(classify_path("var/spack/test_repos/package.py"), "ignored")
        self.assertEqual(classify_path("beartype_test/helpers.py"), "ignored")
        self.assertEqual(classify_path("SCons/ActionTests.py"), "ignored")
        self.assertEqual(classify_path("hscommon/testutil.py"), "ignored")
        self.assertEqual(classify_path("starlette/testclient.py"), "ignored")
        self.assertEqual(
            classify_path("test.py", source=pytest_source),
            "ignored",
        )
        self.assertEqual(
            classify_path("test.py", source=runtime_test_named_source),
            "ignored",
        )
        self.assertEqual(
            classify_path(
                "sklearn/model_selection/train_test_split.py",
                source=b"def split():\n    pass\n",
            ),
            "ignored",
        )

    def test_test_substrings_without_role_boundaries_are_preserved(self) -> None:
        source = b"def run():\n    pass\n"

        self.assertEqual(
            classify_path(
                "meta/env_market_impact/backtest_config.py",
                source=source,
            ),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("edgar/proxy/contest.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("src/azure/latest/commands.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("litestar/routing.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("networkx/shortest_paths/astar.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("sympy/ntheory/primetest.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("mypy/stubtest.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("lib/spack/spack/spec.py", source=source),
            "implementation_code",
        )
        self.assertEqual(
            classify_path("src/werkzeug/security.py", source=source),
            "implementation_code",
        )

    def test_examples_and_samples_are_filtered_as_exact_roles(self) -> None:
        source = b"def run():\n    pass\n"

        self.assertEqual(classify_path("samples/train.py", source=source), "ignored")
        self.assertEqual(classify_path("src/examples.py", source=source), "ignored")
        self.assertEqual(
            classify_path("src/audio/sample_rate.py", source=source),
            "implementation_code",
        )

    def test_setup_py_uses_packaging_ast_detection(self) -> None:
        packaging_source = b"from setuptools import setup\n\nsetup(name='demo')\n"
        normal_source = b"def setup_logging():\n    return None\n"

        self.assertEqual(
            classify_path("pkg/setup.py", source=packaging_source),
            "ignored",
        )
        self.assertEqual(
            classify_path("sanic/logging/setup.py", source=normal_source),
            "implementation_code",
        )


class PythonSourceWarningTests(unittest.TestCase):
    def test_ast_analysis_suppresses_invalid_escape_syntax_warning(self) -> None:
        source = b'pattern = "\\s+"\n'

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            parseable = candidate_pairs._is_parseable_python_source(source)

        self.assertTrue(parseable)
        self.assertFalse(any(item.category is SyntaxWarning for item in caught))

    def test_compile_diagnostic_suppresses_warning_but_keeps_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work_tree = Path(tmp)
            source_path = work_tree / "warning.py"
            source_path.write_bytes(b'pattern = "\\s+"\n')

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                status, error = candidate_pairs._task_base_compile_diagnostic(
                    work_tree,
                    ["warning.py"],
                )

            self.assertEqual((status, error), ("passed", None))
            self.assertFalse(any(item.category is SyntaxWarning for item in caught))

            source_path.write_text("def broken(:\n", encoding="utf-8")
            status, error = candidate_pairs._task_base_compile_diagnostic(
                work_tree,
                ["warning.py"],
            )

            self.assertEqual(status, "failed")
            self.assertIn("SyntaxError", error or "")


class GitBackedPathClassificationTests(unittest.TestCase):
    def test_obvious_ignored_paths_do_not_read_git_sources(self) -> None:
        with patch.object(
            candidate_pairs,
            "_read_file_from_git",
            side_effect=AssertionError("ignored path should not read source"),
        ):
            for path in (
                "docs/guide.rst",
                "tests/test_app.py",
                ".github/workflows/ci.yml",
                "pyproject.toml",
                "src/frontend.ts",
            ):
                self.assertEqual(
                    classify_diff_path(
                        Path("/tmp/example.git"),
                        "a" * 40,
                        "b" * 40,
                        path,
                        status="M",
                    ),
                    "ignored",
                )

    def test_type_changed_python_path_is_ignored_without_reading_source(self) -> None:
        with patch.object(
            candidate_pairs,
            "_read_file_from_git",
            side_effect=AssertionError("type-changed path should not read source"),
        ):
            self.assertEqual(
                classify_diff_path(
                    Path("/tmp/example.git"),
                    "a" * 40,
                    "b" * 40,
                    "src/app.py",
                    status="T",
                ),
                "ignored",
            )

    def test_python_source_requires_ast_parse_success_for_modified_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._git(repo, "init")
            self._git(repo, "config", "user.email", "test@example.com")
            self._git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "app.py"
            source.parent.mkdir()
            source.write_text("def run():\n    return 1\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "old")
            old_commit = self._git(repo, "rev-parse", "HEAD").strip()

            source.write_text("def run(:\n    return 2\n", encoding="utf-8")
            self._git(repo, "add", ".")
            self._git(repo, "commit", "-m", "new")
            new_commit = self._git(repo, "rev-parse", "HEAD").strip()

            self.assertEqual(
                classify_diff_path(repo / ".git", old_commit, new_commit, "src/app.py"),
                "ignored",
            )

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=repo,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return result.stdout


class PairArtifactTests(unittest.TestCase):
    def test_repo_analysis_reuses_blob_source_and_ast_across_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_repo = Path(tmp) / "repo"
            git_repo.mkdir()
            _git(git_repo, "init")
            _git(git_repo, "config", "user.email", "test@example.com")
            _git(git_repo, "config", "user.name", "Test User")

            source = git_repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text("def run():\n    return 1\n", encoding="utf-8")
            (git_repo / "marker.txt").write_text("one\n", encoding="utf-8")
            _git(git_repo, "add", ".")
            _git(git_repo, "commit", "-m", "old-one")
            old_one = _git(git_repo, "rev-parse", "HEAD").strip()

            (git_repo / "marker.txt").write_text("two\n", encoding="utf-8")
            _git(git_repo, "add", ".")
            _git(git_repo, "commit", "-m", "old-two")
            old_two = _git(git_repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    return 2\n", encoding="utf-8")
            _git(git_repo, "add", ".")
            _git(git_repo, "commit", "-m", "new")
            new_commit = _git(git_repo, "rev-parse", "HEAD").strip()

            repo = candidate_pairs.RepoAnalysisContext(
                cache_dir=git_repo / ".git",
                new_commit=new_commit,
            )
            reader = repo._reader()
            first_pair = repo.pair(old_one)
            second_pair = repo.pair(old_two)
            with (
                patch.object(reader, "read", wraps=reader.read) as read_mock,
                patch.object(
                    candidate_pairs,
                    "_parse_python_ast",
                    wraps=candidate_pairs._parse_python_ast,
                ) as parse_mock,
            ):
                self.assertEqual(
                    first_pair.source(new_commit, "src/runner.py"),
                    b"def run():\n    return 2\n",
                )
                self.assertEqual(
                    second_pair.source(new_commit, "src/runner.py"),
                    b"def run():\n    return 2\n",
                )
                self.assertEqual(
                    first_pair.source(old_one, "src/runner.py"),
                    b"def run():\n    return 1\n",
                )
                self.assertEqual(
                    second_pair.source(old_two, "src/runner.py"),
                    b"def run():\n    return 1\n",
                )
                self.assertTrue(first_pair.is_parseable(old_one, "src/runner.py"))
                self.assertEqual(
                    first_pair.objects(old_one, "src/runner.py")[0].qualified_name,
                    "run",
                )
                self.assertFalse(
                    first_pair.is_packaging_setup(old_one, "src/runner.py")
                )

            repo.close()
            self.assertEqual(read_mock.call_count, 2)
            self.assertEqual(parse_mock.call_count, 2)
            self.assertEqual(len(repo.blob_analyses), 2)

    def test_git_batch_reader_handles_missing_and_unicode_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            git_repo = Path(tmp) / "repo"
            git_repo.mkdir()
            _git(git_repo, "init")
            _git(git_repo, "config", "user.email", "test@example.com")
            _git(git_repo, "config", "user.name", "Test User")
            source_path = "src/na\u00efve file.py"
            source = git_repo / source_path
            source.parent.mkdir()
            source.write_text("def run():\n    return 1\n", encoding="utf-8")
            _git(git_repo, "add", ".")
            _git(git_repo, "commit", "-m", "source")
            commit = _git(git_repo, "rev-parse", "HEAD").strip()

            with candidate_pairs.RepoAnalysisContext(
                cache_dir=git_repo / ".git",
                new_commit=commit,
            ) as repo:
                pair = repo.pair(commit)
                self.assertEqual(
                    pair.source(commit, source_path),
                    b"def run():\n    return 1\n",
                )
                self.assertIsNone(pair.source(commit, "src/missing.py"))

    def test_implementation_units_use_ast_object_identity(self) -> None:
        units = candidate_pairs._implementation_units(
            [
                {
                    "target_type": "removed_object",
                    "path": "src/flask/app.py",
                    "object_kind": "method",
                    "qualified_name": "Flask.debug",
                    "object_start_line": 753,
                    "object_end_line": 764,
                },
                {
                    "target_type": "removed_object",
                    "path": "src/flask/app.py",
                    "object_kind": "method",
                    "qualified_name": "Flask.debug",
                    "object_start_line": 766,
                    "object_end_line": 771,
                },
                {
                    "target_type": "removed_range",
                    "path": "src/runner.py",
                    "object_kind": "method",
                    "qualified_name": "Runner.run",
                    "object_start_line": 20,
                    "object_end_line": 55,
                    "removed_ranges": [
                        {
                            "old_start_line": 31,
                            "old_end_line": 31,
                            "source_text": "        old_one()\n",
                        },
                        {
                            "old_start_line": 40,
                            "old_end_line": 40,
                            "source_text": "        old_two()\n",
                        },
                    ],
                },
            ]
        )

        self.assertEqual(len(units), 3)
        self.assertEqual(
            [(unit["qualified_name"], unit["object_start_line"]) for unit in units],
            [
                ("Flask.debug", 753),
                ("Flask.debug", 766),
                ("Runner.run", 20),
            ],
        )

    def test_repo_output_reuse_requires_matching_candidate_pair_thresholds(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "repo_outputs" / "example__repo"
            output_dir.mkdir(parents=True)
            (output_dir / "repo_result.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "offset_ranges": [{"start": 1, "stop": 200, "step": 1}],
                        "min_implementation_units": 10,
                        "min_reinsert_source_lines": 500,
                        "max_implementation_units": 200,
                        "max_reinsert_source_lines": 3000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "accepted_candidates.jsonl").write_text("", encoding="utf-8")
            (output_dir / "rejected_candidates.jsonl").write_text("", encoding="utf-8")

            reusable = candidate_pairs._repo_output_reusable(
                output_dir,
                CandidatePairsConfig(
                    offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                    min_implementation_units=10,
                    min_reinsert_source_lines=500,
                    max_implementation_units=200,
                    max_reinsert_source_lines=3000,
                ),
            )
            not_reusable = candidate_pairs._repo_output_reusable(
                output_dir,
                CandidatePairsConfig(
                    offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                    min_implementation_units=10,
                    min_reinsert_source_lines=500,
                    max_implementation_units=100,
                    max_reinsert_source_lines=3000,
                ),
            )
            scheduling_change_reusable = candidate_pairs._repo_output_reusable(
                output_dir,
                CandidatePairsConfig(
                    offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                    max_concurrent_repos=64,
                    max_concurrent_repo_downloads=16,
                    min_implementation_units=10,
                    min_reinsert_source_lines=500,
                    max_implementation_units=200,
                    max_reinsert_source_lines=3000,
                ),
            )

            self.assertTrue(reusable)
            self.assertFalse(not_reusable)
            self.assertTrue(scheduling_change_reusable)

    def test_repo_output_reuse_rejects_rows_missing_current_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            output_dir = run_dir / "candidate_pairs" / "repo_outputs" / "example__repo"
            output_dir.mkdir(parents=True)
            pair_output_dir = (
                run_dir / "candidate_pairs" / "pair_outputs" / "example__repo__old__new"
            )
            pair_output_dir.mkdir(parents=True)
            for name in (
                "reinsert_targets.jsonl",
                "implementation_units.jsonl",
                "obsolete_reinsert.patch",
            ):
                (pair_output_dir / name).write_text("", encoding="utf-8")
            (output_dir / "repo_result.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "offset_ranges": [{"start": 1, "stop": 200, "step": 1}],
                        "min_implementation_units": 10,
                        "min_reinsert_source_lines": 500,
                        "max_implementation_units": 200,
                        "max_reinsert_source_lines": 3000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "accepted_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "pair_id": "example__repo__old__new",
                        "removed_object_targets_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "rejected_candidates.jsonl").write_text("", encoding="utf-8")

            self.assertFalse(
                candidate_pairs._repo_output_reusable(
                    output_dir,
                    CandidatePairsConfig(
                        offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                        min_implementation_units=10,
                        min_reinsert_source_lines=500,
                        max_implementation_units=200,
                        max_reinsert_source_lines=3000,
                    ),
                )
            )

    def test_repo_output_reuse_requires_rejected_pair_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            output_dir = run_dir / "candidate_pairs" / "repo_outputs" / "example__repo"
            output_dir.mkdir(parents=True)
            (output_dir / "repo_result.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "offset_ranges": [{"start": 1, "stop": 200, "step": 1}],
                        "min_implementation_units": 10,
                        "min_reinsert_source_lines": 500,
                        "max_implementation_units": 200,
                        "max_reinsert_source_lines": 3000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "accepted_candidates.jsonl").write_text("", encoding="utf-8")
            (output_dir / "rejected_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "pair_id": "example__repo__old__new",
                        "removed_object_targets_count": 0,
                        "removed_range_targets_count": 1,
                        "implementation_units_count": 1,
                        "reinsert_source_lines": 20,
                        "task_base_compile": None,
                        "task_base_compile_error": None,
                        "implementation_code_paths": ["src/runner.py"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertFalse(
                candidate_pairs._repo_output_reusable(
                    output_dir,
                    CandidatePairsConfig(
                        offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                        min_implementation_units=10,
                        min_reinsert_source_lines=500,
                        max_implementation_units=200,
                        max_reinsert_source_lines=3000,
                    ),
                )
            )

    def test_repo_output_reuse_accepts_rejected_pair_with_light_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            output_dir = run_dir / "candidate_pairs" / "repo_outputs" / "example__repo"
            output_dir.mkdir(parents=True)
            pair_output_dir = (
                run_dir / "candidate_pairs" / "pair_outputs" / "example__repo__old__new"
            )
            pair_output_dir.mkdir(parents=True)
            for name in (
                "metadata.json",
                "reinsert_targets.jsonl",
                "implementation_units.jsonl",
            ):
                (pair_output_dir / name).write_text("", encoding="utf-8")
            (output_dir / "repo_result.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "offset_ranges": [{"start": 1, "stop": 200, "step": 1}],
                        "min_implementation_units": 10,
                        "min_reinsert_source_lines": 500,
                        "max_implementation_units": 200,
                        "max_reinsert_source_lines": 3000,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (output_dir / "accepted_candidates.jsonl").write_text("", encoding="utf-8")
            (output_dir / "rejected_candidates.jsonl").write_text(
                json.dumps(
                    {
                        "pair_id": "example__repo__old__new",
                        "removed_object_targets_count": 0,
                        "removed_range_targets_count": 1,
                        "implementation_units_count": 1,
                        "reinsert_source_lines": 20,
                        "task_base_compile": None,
                        "task_base_compile_error": None,
                        "implementation_code_paths": ["src/runner.py"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            self.assertTrue(
                candidate_pairs._repo_output_reusable(
                    output_dir,
                    CandidatePairsConfig(
                        offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=200, step=1)],
                        min_implementation_units=10,
                        min_reinsert_source_lines=500,
                        max_implementation_units=200,
                        max_reinsert_source_lines=3000,
                    ),
                )
            )

    def test_cleanup_unused_pair_outputs_removes_stale_pair_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pair_outputs_dir = Path(tmp) / "pair_outputs"
            keep_dir = pair_outputs_dir / "keep_pair"
            stale_dir = pair_outputs_dir / "stale_pair"
            keep_dir.mkdir(parents=True)
            stale_dir.mkdir(parents=True)

            cleanup = getattr(candidate_pairs, "_cleanup_unused_pair_outputs", None)
            self.assertIsNotNone(cleanup, "_cleanup_unused_pair_outputs should exist")
            cleanup(
                pair_outputs_dir,
                [
                    {
                        "pair_id": "keep_pair",
                    }
                ],
                [],
            )

            self.assertTrue(keep_dir.exists())
            self.assertFalse(stale_dir.exists())

    def test_pair_artifacts_capture_reinsert_targets_and_units(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text(
                "\n".join(
                    [
                        "class Runner:",
                        "    def run(self):",
                        "        keep = self.current()",
                        "        legacy_result = self._legacy_run()",
                        "        if legacy_result is not None:",
                        "            return legacy_result",
                        "        return keep",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text(
                "\n".join(
                    [
                        "class Runner:",
                        "    def run(self):",
                        "        keep = self.current()",
                        "        return keep",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            pair_dir.mkdir()
            (pair_dir / "removal_targets.jsonl").write_text("{}\n", encoding="utf-8")
            writer = getattr(candidate_pairs, "_write_pair_artifacts", None)
            self.assertIsNotNone(writer, "_write_pair_artifacts should exist")
            result = writer(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/runner.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/runner.py"],
            )

            self.assertEqual(result["removed_range_targets_count"], 1)
            self.assertEqual(result["removed_object_targets_count"], 0)
            self.assertEqual(result["implementation_units_count"], 1)
            self.assertEqual(result["reinsert_source_lines"], 3)
            for name in (
                "metadata.json",
                "reinsert_extraction.patch",
                "reinsert_targets.jsonl",
                "implementation_units.jsonl",
                "obsolete_reinsert.patch",
            ):
                self.assertTrue((pair_dir / name).exists(), name)
            self.assertFalse((pair_dir / "old_to_new.patch").exists())
            self.assertFalse((pair_dir / "implementation_code.patch").exists())
            self.assertFalse((pair_dir / "removal_targets.jsonl").exists())

            metadata = json.loads(
                (pair_dir / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["implementation_units_count"], 1)
            self.assertEqual(metadata["removed_object_targets_count"], 0)
            self.assertEqual(metadata["removed_range_targets_count"], 1)
            self.assertEqual(metadata["reinsert_source_lines"], 3)
            self.assertNotIn("audit_patches_materialized", metadata)
            self.assertEqual(metadata["implementation_code_paths"], ["src/runner.py"])

            targets = _read_jsonl(pair_dir / "reinsert_targets.jsonl")
            self.assertEqual(
                targets,
                [
                    {
                        "target_type": "removed_range",
                        "path": "src/runner.py",
                        "object_kind": "method",
                        "qualified_name": "Runner.run",
                        "object_start_line": 2,
                        "object_end_line": 7,
                        "removed_ranges": [
                            {
                                "old_start_line": 4,
                                "old_end_line": 6,
                                "source_text": (
                                    "        legacy_result = self._legacy_run()\n"
                                    "        if legacy_result is not None:\n"
                                    "            return legacy_result\n"
                                ),
                            }
                        ],
                    }
                ],
            )
            units = _read_jsonl(pair_dir / "implementation_units.jsonl")
            self.assertEqual(
                units,
                [
                    {
                        "source_target_type": "removed_range",
                        "path": "src/runner.py",
                        "object_kind": "method",
                        "qualified_name": "Runner.run",
                        "object_start_line": 2,
                        "object_end_line": 7,
                    }
                ],
            )

            patch_text = (pair_dir / "obsolete_reinsert.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("legacy_result = self._legacy_run()", patch_text)
            extraction_patch_text = (pair_dir / "reinsert_extraction.patch").read_text(
                encoding="utf-8"
            )
            self.assertTrue(extraction_patch_text.startswith("diff --git "))
            self.assertIn("legacy_result = self._legacy_run()", extraction_patch_text)

            checkout = Path(tmp) / "checkout"
            checkout.mkdir()
            _git_with_work_tree(
                repo / ".git", checkout, "checkout", "-f", new_commit, "--", "."
            )
            _git_apply(checkout, pair_dir / "obsolete_reinsert.patch")
            self.assertIn(
                "legacy_result = self._legacy_run()",
                (checkout / "src" / "runner.py").read_text(encoding="utf-8"),
            )

    def test_pair_artifacts_record_compile_failure_for_repeated_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "app.py"
            source.parent.mkdir()
            source.write_text(
                "\n".join(
                    [
                        "def create(static_folder, static_url_path):",
                        "    return build(",
                        "        static_folder=static_folder,",
                        "        static_url_path=static_url_path,",
                        "    )",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text(
                "\n".join(
                    [
                        "def create(static_folder, static_url_path):",
                        "    return build(",
                        "        static_url_path=static_url_path,",
                        "        static_folder=static_folder,",
                        "    )",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/app.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/app.py"],
            )

            self.assertEqual(result["task_base_compile"], "failed")
            self.assertIn(
                "keyword argument repeated", result["task_base_compile_error"]
            )
            metadata = json.loads(
                (pair_dir / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["task_base_compile"], "failed")
            self.assertIn(
                "keyword argument repeated", metadata["task_base_compile_error"]
            )

    def test_complete_deleted_class_creates_removed_object_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "resources.py"
            source.parent.mkdir()
            source.write_text(
                "\n".join(
                    [
                        "class CalendarHandler:",
                        "    def get(self):",
                        "        self.finish()",
                        "",
                        "",
                        "class NotFoundHandler:",
                        "    def get(self, *args, **kwargs):",
                        "        raise RuntimeError('not found')",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text(
                "\n".join(
                    [
                        "class CalendarHandler:",
                        "    def get(self):",
                        "        self.finish()",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/resources.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/resources.py"],
            )

            self.assertEqual(result["removed_object_targets_count"], 1)
            self.assertEqual(result["removed_range_targets_count"], 0)
            self.assertEqual(result["implementation_units_count"], 1)
            targets = _read_jsonl(pair_dir / "reinsert_targets.jsonl")
            self.assertEqual(len(targets), 1)
            self.assertEqual(targets[0]["target_type"], "removed_object")
            self.assertEqual(targets[0]["object_kind"], "class")
            self.assertEqual(targets[0]["qualified_name"], "NotFoundHandler")
            self.assertIn("class NotFoundHandler:", targets[0]["source_text"])
            self.assertIn("def get", targets[0]["source_text"])

            units = _read_jsonl(pair_dir / "implementation_units.jsonl")
            self.assertEqual(
                units,
                [
                    {
                        "source_target_type": "removed_object",
                        "path": "src/resources.py",
                        "object_kind": "method",
                        "qualified_name": "NotFoundHandler.get",
                        "object_start_line": 7,
                        "object_end_line": 8,
                    }
                ],
            )

            patch_text = (pair_dir / "obsolete_reinsert.patch").read_text(
                encoding="utf-8"
            )
            self.assertIn("+class NotFoundHandler:", patch_text)

    def test_replacement_hunks_do_not_create_reinsert_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "compat.py"
            source.parent.mkdir()
            source.write_text(
                "\n".join(
                    [
                        "def tob(s):",
                        "    if isinstance(s, unicode):",
                        "        return s.encode('utf8')",
                        "    return s",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text(
                "\n".join(
                    [
                        "def tob(s):",
                        "    if isinstance(s, str):",
                        "        return s.encode('utf8')",
                        "    return s",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/compat.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/compat.py"],
            )

            self.assertEqual(result["removed_range_targets_count"], 0)
            self.assertEqual(result["implementation_units_count"], 0)
            self.assertEqual(_read_jsonl(pair_dir / "reinsert_targets.jsonl"), [])
            self.assertEqual(_read_jsonl(pair_dir / "implementation_units.jsonl"), [])
            self.assertFalse((pair_dir / "obsolete_reinsert.patch").exists())

    def test_pure_deleted_ranges_use_histogram_diff(self) -> None:
        calls: list[list[str]] = []

        def fake_git_bytes(args: list[str]) -> bytes:
            calls.append(args)
            return b""

        original = candidate_pairs._run_git_bytes
        candidate_pairs._run_git_bytes = fake_git_bytes
        try:
            candidate_pairs._pure_deleted_ranges_for_path(
                Path("/tmp/cache.git"),
                "oldsha",
                "newsha",
                "src/app.py",
            )
        finally:
            candidate_pairs._run_git_bytes = original

        self.assertEqual(len(calls), 1)
        self.assertIn("--histogram", calls[0])
        self.assertIn("--unified=0", calls[0])
        self.assertLess(calls[0].index("--histogram"), calls[0].index("--unified=0"))

    def test_combined_histogram_diff_runs_once_and_splits_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            paths = ["src/a b.py", 'src/a"b.py', "src/a\tb.py", "src/中.py"]
            for index, path in enumerate(paths):
                file_path = repo / path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(
                    f"def run_{index}():\n    old_{index}()\n    keep_{index}()\n",
                    encoding="utf-8",
                )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            for index, path in enumerate(paths):
                (repo / path).write_text(
                    f"def run_{index}():\n    keep_{index}()\n",
                    encoding="utf-8",
                )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            with patch.object(
                candidate_pairs,
                "_run_git_bytes",
                wraps=candidate_pairs._run_git_bytes,
            ) as git_mock:
                combined, by_path = candidate_pairs._combined_reinsert_extraction_diff(
                    repo / ".git",
                    old_commit,
                    new_commit,
                    paths,
                )

            self.assertEqual(git_mock.call_count, 1)
            self.assertEqual(list(by_path), paths)
            self.assertEqual(combined.count(b"diff --git "), len(paths))
            for index, path in enumerate(paths):
                ranges = candidate_pairs._pure_deleted_ranges_from_diff(by_path[path])
                self.assertEqual(len(ranges), 1)
                self.assertEqual(ranges[0].source_text, f"    old_{index}()\n")
                other_index = (index + 1) % len(paths)
                self.assertNotIn(f"old_{other_index}()", by_path[path].decode("utf-8"))

    def test_removed_python_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "legacy_runner.py"
            source.parent.mkdir()
            (repo / "README.md").write_text("demo\n", encoding="utf-8")
            source.write_text(
                "\n".join(
                    [
                        "def legacy_run():",
                        "    return 'legacy'",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.unlink()
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            with patch.object(candidate_pairs, "_read_file_from_git") as read_mock:
                diff_files = candidate_pairs._diff_files(
                    repo / ".git",
                    old_commit,
                    new_commit,
                )
            read_mock.assert_not_called()
            self.assertEqual(
                diff_files,
                [
                    DiffFile(
                        status="D",
                        path="src/legacy_runner.py",
                        path_class="ignored",
                    )
                ],
            )

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=diff_files,
                implementation_code_paths=[],
            )

            self.assertEqual(result["removed_object_targets_count"], 0)
            self.assertEqual(result["removed_range_targets_count"], 0)
            self.assertEqual(result["implementation_units_count"], 0)
            self.assertEqual(result["reinsert_source_lines"], 0)
            self.assertEqual(_read_jsonl(pair_dir / "reinsert_targets.jsonl"), [])
            self.assertEqual(_read_jsonl(pair_dir / "implementation_units.jsonl"), [])
            self.assertFalse((pair_dir / "obsolete_reinsert.patch").exists())

    def test_obsolete_reinsert_patch_preserves_crlf_without_whole_file_rewrite(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_bytes(b"def run():\r\n    old_step()\r\n    keep()\r\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_bytes(b"def run():\r\n    keep()\r\n")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1, min_reinsert_source_lines=1
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/runner.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/runner.py"],
            )

            self.assertEqual(result["removed_range_targets_count"], 1)
            patch_lines = (
                (pair_dir / "obsolete_reinsert.patch")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            real_minus_lines = [
                line
                for line in patch_lines
                if line.startswith("-") and not line.startswith("---")
            ]
            self.assertEqual(real_minus_lines, [])
            self.assertTrue(any(line == "+    old_step()" for line in patch_lines))

    def test_obsolete_reinsert_patch_prefers_histogram_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(candidate_pairs, "_run_git_work_tree"),
                patch.object(candidate_pairs, "_insert_reinsert_targets"),
                patch.object(
                    candidate_pairs,
                    "_run_git_work_tree_bytes",
                    return_value=b"diff --git a/legacy.py b/legacy.py\n",
                ) as run_diff,
            ):
                candidate_pairs._generate_obsolete_reinsert_patch(
                    Path(tmp) / "cache.git",
                    "new-commit",
                    [
                        {
                            "target_type": "removed_range",
                            "path": "legacy.py",
                            "object_kind": "function",
                            "qualified_name": "legacy",
                            "removed_ranges": [],
                        }
                    ],
                )

            diff_args = run_diff.call_args.args[2]
            self.assertIn("--histogram", diff_args)
            self.assertLess(diff_args.index("--histogram"), diff_args.index("--binary"))

    def test_obsolete_reinsert_patch_falls_back_to_add_only_diff(self) -> None:
        deletion_patch = (
            b"diff --git a/legacy.py b/legacy.py\n"
            b"--- a/legacy.py\n"
            b"+++ b/legacy.py\n"
            b"@@ -1 +1 @@\n"
            b"-\n"
            b"+    old_step()\n"
        )
        add_only_patch = (
            b"diff --git a/legacy.py b/legacy.py\n"
            b"--- a/legacy.py\n"
            b"+++ b/legacy.py\n"
            b"@@ -1,0 +2 @@\n"
            b"+    old_step()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.object(candidate_pairs, "_run_git_work_tree"),
                patch.object(candidate_pairs, "_insert_reinsert_targets"),
                patch.object(
                    candidate_pairs,
                    "_run_git_work_tree_bytes",
                    side_effect=[deletion_patch, add_only_patch],
                ) as run_diff,
            ):
                generated, _, _ = candidate_pairs._generate_obsolete_reinsert_patch(
                    Path(tmp) / "cache.git",
                    "new-commit",
                    [
                        {
                            "target_type": "removed_range",
                            "path": "legacy.py",
                            "object_kind": "function",
                            "qualified_name": "legacy",
                            "removed_ranges": [],
                        }
                    ],
                )

            self.assertEqual(generated, add_only_patch)
            self.assertEqual(run_diff.call_count, 2)
            self.assertIn("--histogram", run_diff.call_args_list[0].args[2])
            self.assertIn("--patience", run_diff.call_args_list[1].args[2])

    def test_pair_artifacts_skip_patch_generation_when_thresholds_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text("def run():\n    old()\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            with patch.object(
                candidate_pairs,
                "_combined_reinsert_extraction_diff",
                wraps=candidate_pairs._combined_reinsert_extraction_diff,
            ) as diff_mock:
                result = candidate_pairs._write_pair_artifacts(
                    pair_dir,
                    cache_dir=repo / ".git",
                    config=CandidatePairsConfig(
                        min_implementation_units=10,
                        min_reinsert_source_lines=500,
                    ),
                    candidate={
                        "pair_id": "example__repo__old__new",
                        "repo_key": "example__repo",
                        "old_commit": old_commit,
                        "new_commit": new_commit,
                        "offset": 1,
                    },
                    diff_files=[
                        DiffFile(
                            status="M",
                            path="src/runner.py",
                            path_class="implementation_code",
                        )
                    ],
                    implementation_code_paths=["src/runner.py"],
                )

            self.assertEqual(result["implementation_units_count"], 1)
            self.assertEqual(diff_mock.call_count, 1)
            self.assertEqual(result["reinsert_source_lines"], 1)
            self.assertTrue((pair_dir / "reinsert_targets.jsonl").exists())
            self.assertTrue((pair_dir / "implementation_units.jsonl").exists())
            for name in (
                "old_to_new.patch",
                "reinsert_extraction.patch",
                "obsolete_reinsert.patch",
            ):
                self.assertFalse((pair_dir / name).exists(), name)
            metadata = json.loads(
                (pair_dir / "metadata.json").read_text(encoding="utf-8")
            )
            self.assertNotIn("audit_patches_materialized", metadata)

    def test_pair_artifacts_skip_patch_generation_when_max_thresholds_fail(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text("def run():\n    old()\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            pair_dir = Path(tmp) / "pair_output"
            result = candidate_pairs._write_pair_artifacts(
                pair_dir,
                cache_dir=repo / ".git",
                config=CandidatePairsConfig(
                    min_implementation_units=1,
                    min_reinsert_source_lines=1,
                    max_implementation_units=0,
                    max_reinsert_source_lines=3000,
                ),
                candidate={
                    "pair_id": "example__repo__old__new",
                    "repo_key": "example__repo",
                    "old_commit": old_commit,
                    "new_commit": new_commit,
                    "offset": 1,
                },
                diff_files=[
                    DiffFile(
                        status="M",
                        path="src/runner.py",
                        path_class="implementation_code",
                    )
                ],
                implementation_code_paths=["src/runner.py"],
            )

            self.assertEqual(result["implementation_units_count"], 1)
            self.assertEqual(result["reinsert_source_lines"], 1)
            self.assertTrue((pair_dir / "reinsert_targets.jsonl").exists())
            self.assertTrue((pair_dir / "implementation_units.jsonl").exists())
            for name in (
                "old_to_new.patch",
                "reinsert_extraction.patch",
                "obsolete_reinsert.patch",
            ):
                self.assertFalse((pair_dir / name).exists(), name)

    def test_patch_generation_does_not_checkout_second_verify_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text("def run():\n    old()\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            checkout_count = 0
            original_checkout = candidate_pairs._checkout_commit

            def counting_checkout(
                cache_dir: Path,
                commit: str,
                work_tree: Path,
                *,
                paths: list[str] | None = None,
            ) -> None:
                nonlocal checkout_count
                checkout_count += 1
                original_checkout(cache_dir, commit, work_tree, paths=paths)

            candidate_pairs._checkout_commit = counting_checkout
            try:
                candidate_pairs._write_pair_artifacts(
                    Path(tmp) / "pair_output",
                    cache_dir=repo / ".git",
                    config=CandidatePairsConfig(
                        min_implementation_units=1, min_reinsert_source_lines=1
                    ),
                    candidate={
                        "pair_id": "example__repo__old__new",
                        "repo_key": "example__repo",
                        "old_commit": old_commit,
                        "new_commit": new_commit,
                        "offset": 1,
                    },
                    diff_files=[
                        DiffFile(
                            status="M",
                            path="src/runner.py",
                            path_class="implementation_code",
                        )
                    ],
                    implementation_code_paths=["src/runner.py"],
                )
            finally:
                candidate_pairs._checkout_commit = original_checkout

            self.assertEqual(checkout_count, 1)

    def test_patch_generation_checks_out_only_existing_reinsert_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")

            source = repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text("def run():\n    old()\n    keep()\n", encoding="utf-8")
            (repo / "unrelated.py").write_text(
                "def untouched():\n    return 1\n", encoding="utf-8"
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "old")
            old_commit = _git(repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    keep()\n", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "new")
            new_commit = _git(repo, "rev-parse", "HEAD").strip()

            checkout_paths: list[list[str] | None] = []
            original_checkout = candidate_pairs._checkout_commit

            def counting_checkout(
                cache_dir: Path,
                commit: str,
                work_tree: Path,
                paths: list[str] | None = None,
            ) -> None:
                checkout_paths.append(paths)
                if paths is None:
                    original_checkout(cache_dir, commit, work_tree)
                else:
                    original_checkout(cache_dir, commit, work_tree, paths=paths)

            candidate_pairs._checkout_commit = counting_checkout
            try:
                candidate_pairs._write_pair_artifacts(
                    Path(tmp) / "pair_output",
                    cache_dir=repo / ".git",
                    config=CandidatePairsConfig(
                        min_implementation_units=1, min_reinsert_source_lines=1
                    ),
                    candidate={
                        "pair_id": "example__repo__old__new",
                        "repo_key": "example__repo",
                        "old_commit": old_commit,
                        "new_commit": new_commit,
                        "offset": 1,
                    },
                    diff_files=[
                        DiffFile(
                            status="M",
                            path="src/runner.py",
                            path_class="implementation_code",
                        )
                    ],
                    implementation_code_paths=["src/runner.py", "unrelated.py"],
                )
            finally:
                candidate_pairs._checkout_commit = original_checkout

            self.assertEqual(checkout_paths, [["src/runner.py"]])

    def test_prepare_repo_cache_refreshes_valid_local_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            repo_input.cache_dir.mkdir()

            with (
                patch.object(
                    candidate_pairs,
                    "_repo_cache_has_basic_structure",
                    return_value=True,
                ),
                patch.object(
                    candidate_pairs, "_refresh_repo_cache"
                ) as refresh_cache,
                patch.object(candidate_pairs, "_clone_repo_cache") as clone_cache,
            ):
                candidate_pairs._prepare_repo_cache(repo_input, Semaphore(1))

            refresh_cache.assert_called_once_with(repo_input)
            clone_cache.assert_not_called()

    def test_prepare_repo_cache_preserves_valid_cache_when_refresh_fails(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            repo_input.cache_dir.mkdir()
            (repo_input.cache_dir / "stale").write_text("stale", encoding="utf-8")

            with (
                patch.object(
                    candidate_pairs,
                    "_repo_cache_has_basic_structure",
                    return_value=True,
                ),
                patch.object(
                    candidate_pairs,
                    "_refresh_repo_cache",
                    side_effect=RuntimeError("fetch failed"),
                ),
                patch.object(candidate_pairs, "_clone_repo_cache") as clone_cache,
                self.assertRaisesRegex(RuntimeError, "fetch failed"),
            ):
                candidate_pairs._prepare_repo_cache(repo_input, Semaphore(1))

            self.assertTrue(repo_input.cache_dir.exists())
            self.assertTrue((repo_input.cache_dir / "stale").exists())
            clone_cache.assert_not_called()

    def test_refresh_repo_cache_updates_only_the_configured_default_branch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )

            with (
                patch.object(
                    candidate_pairs,
                    "_run_git_remote_with_retry",
                ) as remote_command,
                patch.object(candidate_pairs, "_set_cache_origin") as set_origin,
                patch.object(
                    candidate_pairs,
                    "_ensure_default_branch_ref",
                ) as ensure_branch,
                patch.object(candidate_pairs, "_pin_cache_head") as pin_head,
                _temporary_env(
                    BELTA_GITHUB_PROXY_PREFIX="https://ghfast.top/"
                ),
            ):
                candidate_pairs._refresh_repo_cache(repo_input)

            remote_command.assert_called_once_with(
                [
                    f"--git-dir={repo_input.cache_dir}",
                    "fetch",
                    "--prune",
                    "--no-tags",
                    "https://ghfast.top/https://github.com/owner/demo.git",
                    "+refs/heads/main:refs/heads/main",
                ],
                operation_label="owner/demo",
            )
            set_origin.assert_called_once_with(repo_input)
            ensure_branch.assert_called_once_with(repo_input)
            pin_head.assert_called_once_with(repo_input)

    def test_prepare_repo_cache_fetches_new_commits_into_existing_bare_cache(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_repo = root / "source"
            source_repo.mkdir()
            _git(source_repo, "init", "-b", "main")
            _git(source_repo, "config", "user.email", "test@example.com")
            _git(source_repo, "config", "user.name", "Test User")
            source_file = source_repo / "source.py"
            source_file.write_text("value = 1\n", encoding="utf-8")
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "initial")

            cache_dir = root / "cache.git"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    "--single-branch",
                    "--no-tags",
                    "--branch",
                    "main",
                    str(source_repo),
                    str(cache_dir),
                ],
                check=True,
                capture_output=True,
            )

            source_file.write_text("value = 2\n", encoding="utf-8")
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "new head")
            expected_head = _git(source_repo, "rev-parse", "HEAD").strip()

            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url=str(source_repo),
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=cache_dir,
            )
            candidate_pairs._prepare_repo_cache(repo_input, Semaphore(1))

            actual_head = _git(
                root,
                f"--git-dir={cache_dir}",
                "rev-parse",
                "refs/heads/main",
            ).strip()
            self.assertEqual(actual_head, expected_head)

    def test_prepare_repo_cache_limits_concurrent_clones(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repos = [
                RepoInput(
                    github_repo_id=index,
                    full_name=f"owner/demo-{index}",
                    clone_url=f"https://github.com/owner/demo-{index}.git",
                    default_branch="main",
                    repo_key=f"owner__demo-{index}",
                    cache_dir=Path(tmp) / f"demo-{index}.git",
                )
                for index in range(4)
            ]
            download_slots = Semaphore(2)
            state_lock = Lock()
            two_started = Event()
            release_clones = Event()
            active_clones = 0
            max_active_clones = 0

            def fake_clone(_repo: RepoInput) -> None:
                nonlocal active_clones, max_active_clones
                with state_lock:
                    active_clones += 1
                    max_active_clones = max(max_active_clones, active_clones)
                    if active_clones == 2:
                        two_started.set()
                release_clones.wait(timeout=2)
                with state_lock:
                    active_clones -= 1

            with (
                patch.object(
                    candidate_pairs,
                    "_clone_repo_cache",
                    side_effect=fake_clone,
                ),
                ThreadPoolExecutor(max_workers=4) as executor,
            ):
                futures = [
                    executor.submit(
                        candidate_pairs._prepare_repo_cache,
                        repo,
                        download_slots,
                    )
                    for repo in repos
                ]
                try:
                    self.assertTrue(two_started.wait(timeout=1))
                    with state_lock:
                        self.assertEqual(active_clones, 2)
                finally:
                    release_clones.set()
                for future in futures:
                    future.result(timeout=2)

            self.assertEqual(max_active_clones, 2)

    def test_prepare_repo_cache_releases_download_slot_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            download_slots = Semaphore(1)

            with (
                patch.object(
                    candidate_pairs,
                    "_clone_repo_cache",
                    side_effect=RuntimeError("clone failed"),
                ),
                self.assertRaisesRegex(RuntimeError, "clone failed"),
            ):
                candidate_pairs._prepare_repo_cache(repo_input, download_slots)

            self.assertTrue(download_slots.acquire(blocking=False))
            download_slots.release()

    def test_clone_repo_cache_uses_proxy_then_resets_origin_and_head(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            calls: list[list[str]] = []
            original_run_git = candidate_pairs._run_git
            candidate_pairs._run_git = lambda args, **_kwargs: (
                calls.append(list(args)) or ""
            )
            try:
                with _temporary_env(BELTA_GITHUB_PROXY_PREFIX="https://ghfast.top/"):
                    candidate_pairs._clone_repo_cache(repo_input)
            finally:
                candidate_pairs._run_git = original_run_git

            self.assertEqual(
                calls,
                [
                    [
                        "clone",
                        "--bare",
                        "--single-branch",
                        "--no-tags",
                        "--branch",
                        "main",
                        "https://ghfast.top/https://github.com/owner/demo.git",
                        str(repo_input.cache_dir),
                    ],
                    [
                        f"--git-dir={repo_input.cache_dir}",
                        "remote",
                        "set-url",
                        "origin",
                        "https://github.com/owner/demo.git",
                    ],
                    [
                        f"--git-dir={repo_input.cache_dir}",
                        "symbolic-ref",
                        "HEAD",
                        "refs/heads/main",
                    ],
                ],
            )

    def test_git_remote_uses_configured_proxies_then_falls_back_to_direct(
        self,
    ) -> None:
        repo_input = RepoInput(
            github_repo_id=1,
            full_name="owner/demo",
            clone_url="https://github.com/owner/demo.git",
            default_branch="main",
            repo_key="owner__demo",
            cache_dir=Path("/tmp/demo.git"),
        )
        attempted_urls: list[str] = []
        original_run_git = candidate_pairs._run_git

        def flaky_run_git(
            args: list[str],
            *,
            timeout_seconds: float | None = None,
        ) -> str:
            self.assertEqual(timeout_seconds, 15.0)
            attempted_urls.append(args[-2])
            if len(attempted_urls) == 1:
                raise RuntimeError("The requested URL returned error: 403")
            if len(attempted_urls) == 2:
                raise RuntimeError("git clone failed: command timed out after 15s")
            return ""

        candidate_pairs._run_git = flaky_run_git
        try:
            with _temporary_env(
                BELTA_GITHUB_PROXY_PREFIX=(
                    '["https://proxy-one.test/","https://proxy-two.test/"]'
                ),
                BELTA_GIT_REMOTE_MAX_ATTEMPTS="2",
                BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                BELTA_GIT_REMOTE_TIMEOUT_SECONDS="15",
            ):
                args = [
                    "clone",
                    "--bare",
                    candidate_pairs._repo_remote_url(repo_input),
                    str(repo_input.cache_dir),
                ]
                candidate_pairs._run_git_remote_with_retry(args)
        finally:
            candidate_pairs._run_git = original_run_git

        self.assertEqual(
            attempted_urls,
            [
                "https://proxy-one.test/https://github.com/owner/demo.git",
                "https://proxy-two.test/https://github.com/owner/demo.git",
                "https://github.com/owner/demo.git",
            ],
        )

    def test_concurrent_git_remote_retries_use_independent_proxy_order(
        self,
    ) -> None:
        attempted_urls: dict[str, list[str]] = {
            "repo-one.git": [],
            "repo-two.git": [],
        }
        attempts_lock = Lock()
        original_run_git = candidate_pairs._run_git

        def flaky_run_git(
            args: list[str],
            *,
            timeout_seconds: float | None = None,
        ) -> str:
            self.assertEqual(timeout_seconds, 15.0)
            destination = Path(args[-1]).name
            remote_url = args[-2]
            with attempts_lock:
                attempted_urls[destination].append(remote_url)
            if remote_url.startswith("https://github.com/"):
                return ""
            raise RuntimeError("git clone failed: RPC failed; early EOF")

        candidate_pairs._run_git = flaky_run_git
        try:
            with _temporary_env(
                BELTA_GITHUB_PROXY_PREFIX=(
                    '["https://proxy-one.test/","https://proxy-two.test/"]'
                ),
                BELTA_GIT_REMOTE_MAX_ATTEMPTS="9",
                BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                BELTA_GIT_REMOTE_TIMEOUT_SECONDS="15",
            ):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            candidate_pairs._run_git_remote_with_retry,
                            [
                                "clone",
                                (
                                    "https://proxy-one.test/"
                                    f"https://github.com/owner/{repo_name}"
                                ),
                                f"/tmp/{repo_name}",
                            ],
                        )
                        for repo_name in attempted_urls
                    ]
                    for future in futures:
                        future.result(timeout=2)
        finally:
            candidate_pairs._run_git = original_run_git

        expected_routes = {
            repo_name: [
                f"https://proxy-one.test/https://github.com/owner/{repo_name}",
                f"https://proxy-two.test/https://github.com/owner/{repo_name}",
                f"https://github.com/owner/{repo_name}",
            ]
            for repo_name in attempted_urls
        }
        self.assertEqual(attempted_urls, expected_routes)

    def test_direct_github_403_is_not_retried(self) -> None:
        calls = 0
        original_run_git = candidate_pairs._run_git

        def failing_run_git(
            args: list[str],
            *,
            timeout_seconds: float | None = None,
        ) -> str:
            nonlocal calls
            del args, timeout_seconds
            calls += 1
            raise RuntimeError("The requested URL returned error: 403")

        candidate_pairs._run_git = failing_run_git
        try:
            with (
                _temporary_env(
                    BELTA_GITHUB_PROXY_PREFIX="",
                    BELTA_GIT_REMOTE_MAX_ATTEMPTS="3",
                    BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                ),
                self.assertRaisesRegex(RuntimeError, "403"),
            ):
                candidate_pairs._run_git_remote_with_retry(
                    ["clone", "https://github.com/owner/demo.git"]
                )
        finally:
            candidate_pairs._run_git = original_run_git

        self.assertEqual(calls, 1)

    def test_git_remote_retry_retries_retryable_network_errors(self) -> None:
        calls: list[tuple[list[str], float | None]] = []
        original_run_git = candidate_pairs._run_git

        def flaky_run_git(
            args: list[str],
            *,
            timeout_seconds: float | None = None,
        ) -> str:
            calls.append((list(args), timeout_seconds))
            if len(calls) == 1:
                raise RuntimeError("git clone failed: RPC failed; early EOF")
            return ""

        candidate_pairs._run_git = flaky_run_git
        try:
            with _temporary_env(
                BELTA_GIT_REMOTE_MAX_ATTEMPTS="2",
                BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                BELTA_GIT_REMOTE_TIMEOUT_SECONDS="15",
            ):
                candidate_pairs._run_git_remote_with_retry(["clone", "remote"])
        finally:
            candidate_pairs._run_git = original_run_git

        self.assertEqual(
            calls,
            [
                (["clone", "remote"], 15.0),
                (["clone", "remote"], 15.0),
            ],
        )

    def test_clone_repo_cache_removes_partial_destination_after_failed_attempt(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            clone_attempts = 0
            original_run_git = candidate_pairs._run_git

            def flaky_run_git(
                args: list[str],
                *,
                timeout_seconds: float | None = None,
            ) -> str:
                nonlocal clone_attempts
                if args[0] != "clone":
                    return ""
                clone_attempts += 1
                self.assertEqual(timeout_seconds, 15.0)
                self.assertFalse(repo_input.cache_dir.exists())
                repo_input.cache_dir.mkdir()
                if clone_attempts == 1:
                    (repo_input.cache_dir / "partial").write_text(
                        "partial",
                        encoding="utf-8",
                    )
                    raise RuntimeError("git command timed out after 15 seconds")
                return ""

            candidate_pairs._run_git = flaky_run_git
            try:
                with _temporary_env(
                    BELTA_GIT_REMOTE_MAX_ATTEMPTS="2",
                    BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                    BELTA_GIT_REMOTE_TIMEOUT_SECONDS="15",
                ):
                    candidate_pairs._clone_repo_cache(repo_input)
            finally:
                candidate_pairs._run_git = original_run_git

            self.assertEqual(clone_attempts, 2)
            self.assertTrue(repo_input.cache_dir.exists())
            self.assertFalse((repo_input.cache_dir / "partial").exists())

    def test_clone_repo_cache_removes_partial_destination_after_final_failure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="owner/demo",
                clone_url="https://github.com/owner/demo.git",
                default_branch="main",
                repo_key="owner__demo",
                cache_dir=Path(tmp) / "demo.git",
            )
            clone_attempts = 0
            original_run_git = candidate_pairs._run_git

            def failing_run_git(
                args: list[str],
                *,
                timeout_seconds: float | None = None,
            ) -> str:
                nonlocal clone_attempts
                self.assertEqual(args[0], "clone")
                clone_attempts += 1
                self.assertEqual(timeout_seconds, 15.0)
                self.assertFalse(repo_input.cache_dir.exists())
                repo_input.cache_dir.mkdir()
                (repo_input.cache_dir / "partial").write_text(
                    "partial",
                    encoding="utf-8",
                )
                raise RuntimeError("git command timed out after 15 seconds")

            candidate_pairs._run_git = failing_run_git
            try:
                with (
                    _temporary_env(
                        BELTA_GIT_REMOTE_MAX_ATTEMPTS="2",
                        BELTA_GIT_REMOTE_RETRY_DELAY_SECONDS="0",
                        BELTA_GIT_REMOTE_TIMEOUT_SECONDS="15",
                    ),
                    self.assertRaisesRegex(RuntimeError, "timed out"),
                ):
                    candidate_pairs._clone_repo_cache(repo_input)
            finally:
                candidate_pairs._run_git = original_run_git

            self.assertEqual(clone_attempts, 2)
            self.assertFalse(repo_input.cache_dir.exists())

    def test_failed_repo_result_records_git_error(self) -> None:
        repo_input = RepoInput(
            github_repo_id=1,
            full_name="owner/demo",
            clone_url="https://github.com/owner/demo.git",
            default_branch="main",
            repo_key="owner__demo",
            cache_dir=Path("/tmp/demo.git"),
        )

        result = candidate_pairs._failed_repo_result(
            repo_input,
            CandidatePairsConfig(),
            error="fatal: connection reset",
        )

        self.assertEqual(result["status_code"], "git_cache_failed")
        self.assertEqual(result["error"], "fatal: connection reset")

    def test_construct_repo_candidate_pairs_uses_pair_outputs_and_reinsert_filtering(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_repo = tmp_path / "source"
            source_repo.mkdir()
            _git(source_repo, "init", "-b", "main")
            _git(source_repo, "config", "user.email", "test@example.com")
            _git(source_repo, "config", "user.name", "Test User")

            source = source_repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text(
                "\n".join(
                    [
                        "def run():",
                        "    keep = current()",
                        "    old_step = legacy()",
                        "    if old_step:",
                        "        return old_step",
                        "    return keep",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "old")
            old_commit = _git(source_repo, "rev-parse", "HEAD").strip()

            source.write_text(
                "\n".join(
                    [
                        "def run():",
                        "    keep = current()",
                        "    return keep",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "new")
            new_commit = _git(source_repo, "rev-parse", "HEAD").strip()

            cache_dir = tmp_path / "cache.git"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    "--single-branch",
                    "--branch",
                    "main",
                    str(source_repo),
                    str(cache_dir),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            pair_outputs_dir = tmp_path / "candidate_pairs" / "pair_outputs"
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="example/repo",
                clone_url=str(source_repo),
                default_branch="main",
                repo_key="example__repo",
                cache_dir=cache_dir,
            )
            config = CandidatePairsConfig(
                offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=1, step=1)],
                min_implementation_units=1,
                min_reinsert_source_lines=1,
            )

            try:
                repo_result, accepted, rejected = (
                    candidate_pairs._construct_repo_candidate_pairs(
                        repo_input,
                        config,
                        pair_outputs_dir=pair_outputs_dir,
                        download_slots=Semaphore(1),
                    )
                )
            except TypeError as exc:
                self.fail(
                    f"_construct_repo_candidate_pairs should accept pair_outputs_dir: {exc}"
                )

            pair_id = f"example__repo__{old_commit[:12]}__{new_commit[:12]}"
            self.assertEqual(repo_result["accepted_count"], 1)
            self.assertEqual(repo_result["rejected_count"], 0)
            self.assertEqual(rejected, [])
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0]["pair_id"], pair_id)
            self.assertNotIn("pair_output_dir", accepted[0])
            self.assertNotIn("obsolete_reinsert_patch", accepted[0])
            self.assertEqual(accepted[0]["removed_range_targets_count"], 1)
            self.assertEqual(accepted[0]["implementation_units_count"], 1)
            self.assertEqual(accepted[0]["reinsert_source_lines"], 3)
            self.assertNotIn("diff_stats", accepted[0])
            self.assertTrue((pair_outputs_dir / pair_id / "metadata.json").exists())
            self.assertTrue(
                (pair_outputs_dir / pair_id / "obsolete_reinsert.patch").exists()
            )
            metadata = json.loads(
                (pair_outputs_dir / pair_id / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(metadata["candidate_status"], "accepted")
            self.assertIsNone(metadata["reject_reason"])
            self.assertIsNone(metadata["reject_detail"])
            self.assertNotIn("obsolete_reinsert_patch", metadata)

    def test_construct_repo_candidate_pairs_writes_rejected_pair_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_repo = tmp_path / "source"
            source_repo.mkdir()
            _git(source_repo, "init", "-b", "main")
            _git(source_repo, "config", "user.email", "test@example.com")
            _git(source_repo, "config", "user.name", "Test User")

            source = source_repo / "src" / "runner.py"
            source.parent.mkdir()
            source.write_text(
                "def run():\n    old_step()\n    return current()\n",
                encoding="utf-8",
            )
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "old")
            old_commit = _git(source_repo, "rev-parse", "HEAD").strip()

            source.write_text("def run():\n    return current()\n", encoding="utf-8")
            _git(source_repo, "add", ".")
            _git(source_repo, "commit", "-m", "new")
            new_commit = _git(source_repo, "rev-parse", "HEAD").strip()

            cache_dir = tmp_path / "cache.git"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--bare",
                    "--single-branch",
                    "--branch",
                    "main",
                    str(source_repo),
                    str(cache_dir),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            pair_outputs_dir = tmp_path / "candidate_pairs" / "pair_outputs"
            repo_input = RepoInput(
                github_repo_id=1,
                full_name="example/repo",
                clone_url=str(source_repo),
                default_branch="main",
                repo_key="example__repo",
                cache_dir=cache_dir,
            )
            config = CandidatePairsConfig(
                offset_ranges=[CandidateOffsetRangeConfig(start=1, stop=1, step=1)],
                min_implementation_units=10,
                min_reinsert_source_lines=1,
            )

            repo_result, accepted, rejected = (
                candidate_pairs._construct_repo_candidate_pairs(
                    repo_input,
                    config,
                    pair_outputs_dir=pair_outputs_dir,
                    download_slots=Semaphore(1),
                )
            )

            pair_id = f"example__repo__{old_commit[:12]}__{new_commit[:12]}"
            self.assertEqual(repo_result["accepted_count"], 0)
            self.assertEqual(repo_result["rejected_count"], 1)
            self.assertEqual(accepted, [])
            self.assertEqual(
                rejected[0]["reject_reason"], "not_enough_implementation_units"
            )
            metadata = json.loads(
                (pair_outputs_dir / pair_id / "metadata.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(metadata["candidate_status"], "rejected")
            self.assertEqual(
                metadata["reject_reason"], "not_enough_implementation_units"
            )
            self.assertEqual(
                metadata["reject_detail"],
                {
                    "implementation_units_count": 1,
                    "min_implementation_units": 10,
                },
            )
            self.assertNotIn("obsolete_reinsert_patch", metadata)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _git_with_work_tree(git_dir: Path, work_tree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", f"--git-dir={git_dir}", f"--work-tree={work_tree}", *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.stdout


def _git_apply(work_tree: Path, patch_path: Path) -> None:
    subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=work_tree,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                self_check = isinstance(payload, dict)
                if not self_check:
                    raise AssertionError("JSONL row must be an object")
                rows.append(payload)
    return rows


@contextmanager
def _temporary_env(**values: str):
    previous = {name: os.environ.get(name) for name in values}
    try:
        for name, value in values.items():
            os.environ[name] = value
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    unittest.main()
