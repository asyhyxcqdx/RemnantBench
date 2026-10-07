from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from feature_factory.config import Settings
from feature_factory.db import Base, build_engine, build_session_factory, init_db
from feature_factory.models import (
    GitHubRepository,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage3SavepointFileResult,
    Stage4CleanupTombstone,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
)
from feature_factory.openhands_llm import DEPLOYMENT_LLM_MODEL_ENV
from feature_factory.stage2 import image_assets
from feature_factory.stage4.backend import LocalStage4Backend, OpenHandsStage4Backend
from feature_factory.stage4.issue_styles import (
    ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
    load_issue_style_catalog,
)
from feature_factory.stage4.runner import Stage4RunInterruptedError, Stage4RunRunner
from feature_factory.stage4.service import Stage4RunConflictError, Stage4Service


def _stage4_test_bundle(
    *,
    first_issue_markdown: str,
    first_issue_title: str = "Test issue",
) -> dict:
    return {
        "issue_variants": [
            {
                "style": "swe",
                "title": first_issue_title,
                "issue_markdown": first_issue_markdown,
                "issue_json": {},
                "quality_json": {},
            },
            {
                "style": "fb",
                "title": "Structured feature task",
                "issue_markdown": "Implement the documented app context behavior through the existing public API.",
                "issue_json": {},
                "quality_json": {},
            },
            {
                "style": "hint",
                "title": "App context behavior fails near the lifecycle boundary",
                "issue_markdown": (
                    "The public app context behavior no longer matches the expected contract. "
                    "The state transition around entering and leaving the context is the useful boundary to inspect."
                ),
                "issue_json": {},
                "quality_json": {},
            },
        ],
    }


def _git(repo_dir: str | bytes | "os.PathLike[str]" | "os.PathLike[bytes]", *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return str(completed.stdout or "").strip()


def _session_factory(tmp_path, *, issue_styles: str = "swe,fb,hint"):
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
        stage4_issue_styles=issue_styles,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    return settings, build_session_factory(engine)


def test_stage4_agent_bridge_env_marks_bare_custom_endpoint_model(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    sdk_root.mkdir()
    monkeypatch.delenv(DEPLOYMENT_LLM_MODEL_ENV, raising=False)
    monkeypatch.delenv("FEATURE_FACTORY_HUAWEI_LLM_MODEL", raising=False)
    monkeypatch.setattr(image_assets, "resolve_agent_server_build_args", lambda: ("default", {}))
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-bridge-agent-llm-env.db'}",
        _env_file=None,
        stage4_workspace_dir=tmp_path / "stage4",
        stage4_openhands_sdk_root=sdk_root,
        stage4_llm_model="kimi-k2.6-w4a8",
        stage4_llm_base_url="http://10.43.2.168:8073/v1",
        llm_ssl_verify=False,
    )
    backend = OpenHandsStage4Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )

    env = backend._build_agent_bridge_env()

    assert env["LLM_MODEL"] == "kimi-k2.6-w4a8"
    assert env["LLM_BASE_URL"] == "http://10.43.2.168:8073/v1"
    assert env["FEATURE_FACTORY_LLM_SSL_VERIFY"] == "false"
    assert "SSL_VERIFY" not in env
    assert env[DEPLOYMENT_LLM_MODEL_ENV] == "kimi-k2.6-w4a8"


def test_stage4_openhands_backend_runs_enabled_agents_sequentially(monkeypatch, tmp_path) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    repo_dir = tmp_path / "workspace" / "repo"
    sdk_root.mkdir()
    repo_dir.mkdir(parents=True)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-agents.db'}",
        _env_file=None,
        stage4_workspace_dir=tmp_path / "stage4",
        stage4_openhands_sdk_root=sdk_root,
        stage4_llm_model="gpt-5.4-mini",
        stage4_llm_base_url="https://127.0.0.1:5692/v1",
    )
    backend = OpenHandsStage4Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )
    calls: list[tuple[str, str]] = []
    isolated_repos: list[Path] = []

    def fake_bridge_request(*, request, workspace_dir, label, emit_event):  # noqa: ANN001
        del emit_event
        role = request["generation_style_id"]
        calls.append((role, label))
        isolated_repo = backend._runtime_dir_for(workspace_dir) / f"{role}_tool_workspace" / "repo"
        isolated_repo.mkdir(parents=True)
        (isolated_repo / "agent-side-file.txt").write_text("temporary\n", encoding="utf-8")
        isolated_repos.append(isolated_repo)
        full_bundle = _stage4_test_bundle(first_issue_markdown="Natural SWE issue body")
        bundle = {
            "issue_variants": [
                item for item in full_bundle["issue_variants"] if item["style"] == role
            ]
        }
        return {
            "model": "openai/gpt-5.4-mini",
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "llm_completion_archive": {"role": role},
            "result": {"summary": f"{role} complete", "bundle": bundle},
        }

    monkeypatch.setattr(backend, "_run_bridge_request", fake_bridge_request)
    result = backend.generate_issues(
        {
            "repository": {"full_name": "pallets/flask"},
            "stage4": {
                "workspace": {
                    "workspace_path": str(repo_dir.parent),
                    "repo_dir": str(repo_dir),
                },
                "generation_config": load_issue_style_catalog().snapshot(seed="agent-order-test"),
            },
        }
    )

    assert calls == [
        ("swe", "swe-attempt-1"),
        ("fb", "fb-attempt-1"),
        ("hint", "hint-attempt-1"),
    ]
    assert [item["style"] for item in result.bundle["issue_variants"]] == ["swe", "fb", "hint"]
    assert "hint_variants" not in result.bundle
    assert result.token_usage == 45
    assert set(result.llm_completion_archive) == {"swe", "fb", "hint"}
    assert all(not path.exists() for path in isolated_repos)


def test_stage4_openhands_backend_keeps_cancel_latched_between_styles(
    monkeypatch,
    tmp_path,
) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    repo_dir = tmp_path / "run-between-styles" / "repo"
    sdk_root.mkdir()
    repo_dir.mkdir(parents=True)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-cancel-between-styles.db'}",
        _env_file=None,
        stage4_workspace_dir=tmp_path / "stage4",
        stage4_openhands_sdk_root=sdk_root,
        stage4_llm_model="gpt-5.4-mini",
        stage4_llm_base_url="https://127.0.0.1:5692/v1",
    )
    backend = OpenHandsStage4Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )
    launched_styles: list[str] = []

    class CompletedBridgeProcess:
        returncode = 0
        pid = 12345

        def __init__(self, *, run_id: str) -> None:
            self.run_id = run_id
            self.cancel_recorded = False

        def poll(self) -> int:
            if not self.cancel_recorded:
                self.cancel_recorded = True
                with backend._bridge_process_lock:
                    backend._bridge_cancel_requests.add(self.run_id)
            return 0

    def fake_popen(command, **kwargs):  # noqa: ANN001
        del kwargs
        request_path = Path(command[command.index("--request") + 1])
        result_path = Path(command[command.index("--result") + 1])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        style = str(request["generation_style_id"])
        launched_styles.append(style)
        bundle = {
            "issue_variants": [
                item
                for item in _stage4_test_bundle(first_issue_markdown="Natural SWE issue body")[
                    "issue_variants"
                ]
                if item["style"] == style
            ]
        }
        result_path.write_text(
            json.dumps(
                {
                    "model": "openai/gpt-5.4-mini",
                    "token_usage": {"total_tokens": 15},
                    "llm_completion_archive": {"role": style},
                    "result": {"summary": f"{style} complete", "bundle": bundle},
                }
            ),
            encoding="utf-8",
        )
        return CompletedBridgeProcess(run_id=repo_dir.parent.name)

    monkeypatch.setattr("feature_factory.stage4.backend._repo_head_commit", lambda repo_path: "deadbeef")
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(RuntimeError, match="swe-attempt-1 interrupted by user"):
        backend.generate_issues(
            {
                "repository": {"full_name": "pallets/flask"},
                "stage4": {
                    "workspace": {
                        "workspace_path": str(repo_dir.parent),
                        "repo_dir": str(repo_dir),
                    },
                    "generation_config": load_issue_style_catalog().snapshot(
                        seed="cancel-between-styles-test"
                    ),
                },
            }
        )

    assert launched_styles == ["swe"]
    assert not backend._bridge_cancel_requested(repo_dir.parent.name)


def test_stage4_runner_honors_cancel_latched_before_per_run_backend_registration(
    tmp_path,
) -> None:
    settings, factory = _session_factory(tmp_path)
    runner = Stage4RunRunner(factory, settings, max_workers=1, max_limit=1)

    class RecordingBackend:
        def __init__(self) -> None:
            self.generate_called = False

        def generate_issues(self, source_context, *, emit_event=None):  # noqa: ANN001
            del source_context, emit_event
            self.generate_called = True
            raise AssertionError("cancelled run must not start generation")

        def cancel_run(self, run_id: str) -> bool:
            del run_id
            return True

    backend = RecordingBackend()
    runner.backend = backend
    run = Stage4Run(id="cancel-before-backend-registration", runtime_snapshot_json={})
    try:
        with runner._lock:
            runner._cancel_requested_run_ids.add(run.id)

        with pytest.raises(Stage4RunInterruptedError, match="interrupted by user"):
            runner._execute_run(run, {})

        assert backend.generate_called is False
        assert runner._run_backends[run.id] is backend
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_stage4_runner_interrupts_queued_future_without_locking_its_done_callback(
    monkeypatch,
    tmp_path,
) -> None:
    settings, factory = _session_factory(tmp_path)
    runner = Stage4RunRunner(factory, settings, max_workers=1, max_limit=1)
    run_id = "queued-cancel"
    cancelled_backend_runs: list[str] = []

    class RecordingBackend:
        def cancel_run(self, target_run_id: str) -> bool:
            cancelled_backend_runs.append(target_run_id)
            return True

    runner.backend = RecordingBackend()
    monkeypatch.setattr(runner, "_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "_complete_interrupted_run", lambda target_run_id: None)
    future: Future[None] = Future()
    future.add_done_callback(
        lambda completed, target=run_id: runner._cleanup_future(target, completed)
    )
    with runner._lock:
        runner._futures[run_id] = future
    try:
        assert runner.interrupt_run(run_id) is True
        assert future.cancelled() is True
        assert cancelled_backend_runs == [run_id]
        assert run_id not in runner._futures
    finally:
        runner.shutdown(timeout_seconds=0.0)


def test_stage4_runner_generated_commit_and_interrupt_are_mutually_exclusive(
    monkeypatch,
    tmp_path,
) -> None:
    settings, factory = _session_factory(tmp_path)
    runner = Stage4RunRunner(factory, settings, max_workers=1, max_limit=1)
    run_id = "commit-boundary"
    completed_runs: list[str] = []

    class RecordingService:
        def complete_run_with_bundle(self, target_run_id: str, **kwargs) -> None:
            del kwargs
            completed_runs.append(target_run_id)

    monkeypatch.setattr(
        runner,
        "_with_service",
        lambda callback: callback(RecordingService()),
    )
    result = SimpleNamespace(
        bundle={"issue_variants": []},
        model="test-model",
        token_usage=0,
        token_usage_details={},
    )
    future: Future[None] = Future()
    future.set_running_or_notify_cancel()
    try:
        with runner._lock:
            runner._cancel_requested_run_ids.add(run_id)
        with pytest.raises(Stage4RunInterruptedError, match="interrupted by user"):
            runner._commit_generated_result(run_id, result)
        assert completed_runs == []

        with runner._lock:
            runner._cancel_requested_run_ids.discard(run_id)
            runner._futures[run_id] = future
        runner._commit_generated_result(run_id, result)

        assert completed_runs == [run_id]
        assert runner.interrupt_run(run_id) is False
        assert run_id not in runner._cancel_requested_run_ids
    finally:
        future.set_result(None)
        runner.shutdown(timeout_seconds=0.0)


def test_stage4_openhands_backend_parent_cleans_isolated_repo_after_bridge_failure(
    monkeypatch,
    tmp_path,
) -> None:
    sdk_root = tmp_path / "software-agent-sdk"
    repo_dir = tmp_path / "workspace" / "repo"
    sdk_root.mkdir()
    repo_dir.mkdir(parents=True)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-agent-failure.db'}",
        _env_file=None,
        stage4_workspace_dir=tmp_path / "stage4",
        stage4_openhands_sdk_root=sdk_root,
        stage4_llm_model="gpt-5.4-mini",
        stage4_llm_base_url="https://127.0.0.1:5692/v1",
    )
    backend = OpenHandsStage4Backend(
        settings,
        selection_detail="ready",
        ready=True,
        readiness_message="ready",
    )
    isolated_repo = backend._runtime_dir_for(repo_dir.parent) / "swe_tool_workspace" / "repo"

    def failing_bridge_request(*, request, workspace_dir, label, emit_event):  # noqa: ANN001
        del request, workspace_dir, label, emit_event
        isolated_repo.mkdir(parents=True)
        (isolated_repo / "agent-side-file.txt").write_text("temporary\n", encoding="utf-8")
        raise RuntimeError("simulated hard bridge failure")

    monkeypatch.setattr(backend, "_run_bridge_request", failing_bridge_request)

    with pytest.raises(RuntimeError, match="simulated hard bridge failure"):
        backend.generate_issues(
            {
                "repository": {"full_name": "pallets/flask"},
                "stage4": {
                    "workspace": {
                        "workspace_path": str(repo_dir.parent),
                        "repo_dir": str(repo_dir),
                    },
                    "generation_config": load_issue_style_catalog(
                        enabled_styles=["swe"]
                    ).snapshot(seed="agent-failure-cleanup-test"),
                },
            }
        )

    assert not isolated_repo.exists()


def _seed_stage3_savepoint(session) -> Stage3Savepoint:
    repository = GitHubRepository(
        github_repo_id=1001,
        full_name="pallets/flask",
        owner_login="pallets",
        name="flask",
        html_url="https://github.com/pallets/flask",
        api_url="https://api.github.com/repos/pallets/flask",
        description="test repo",
        primary_language="Python",
        default_branch="main",
        license_key="bsd-3-clause",
        stargazers_count=71000,
        created_at_github=datetime(2020, 1, 1, tzinfo=UTC),
        pushed_at_github=datetime(2020, 1, 2, tzinfo=UTC),
        discovered_at=datetime(2020, 1, 3, tzinfo=UTC),
    )
    session.add(repository)
    session.flush()
    snapshot = Stage3CommitSnapshot(
        repository_id=repository.id,
        source_stage2_run_id="stage2-run",
        source_commit_sha="2ac89881234567890abcdef",
        target_branch="main",
        base_image="python:3.11-jammy-builder",
        dockerfile_text="FROM python:3.11",
        run_script_text="#!/usr/bin/env bash\nexit 0\n",
        original_p2p_files_json=["tests/test_basic.py"],
        summary_json={},
        source_created_at=datetime(2020, 1, 4, tzinfo=UTC),
        source_finished_at=datetime(2020, 1, 5, tzinfo=UTC),
    )
    session.add(snapshot)
    session.flush()
    entry_file = Stage3EntryFile(
        snapshot_id=snapshot.id,
        test_file_path="tests/test_appctx.py",
        baseline_total_tests=15,
        baseline_passed_tests=15,
        baseline_failed_tests=0,
        baseline_error_tests=0,
        baseline_skipped_tests=0,
        baseline_pass_rate=1.0,
        summary_json={},
    )
    session.add(entry_file)
    session.flush()
    stage3_run = Stage3Run(
        entry_file_id=entry_file.id,
        status=Stage3RunStatus.completed.value,
        result=Stage3RunResult.archived.value,
        trigger_kind="manual",
        phase="completed",
        runtime_snapshot_json={
            "stage3": {
                "workspace": {},
            }
        },
        summary_json={},
    )
    session.add(stage3_run)
    session.flush()
    savepoint = Stage3Savepoint(
        run_id=stage3_run.id,
        depth=2,
        entry_pass_rate=0.4,
        p2p_files_json=["tests/test_basic.py"],
        f2p_files_json=["tests/test_appctx.py"],
        collateral_json={"non_entry_failed_count": 0},
        gold_patch_text=(
            "diff --git a/src/flask/ctx.py b/src/flask/ctx.py\n"
            "--- a/src/flask/ctx.py\n"
            "+++ b/src/flask/ctx.py\n"
            "@@ -1 +1 @@\n"
            "-old behavior line with enough text to be considered private\n"
            "+new broken line with enough text to be considered private\n"
        ),
        checkpoint_json={},
        summary_json={
            "milestone_summary": "Application context URL generation no longer follows its public contract.",
            "rationale": "The broken behavior is local to app context URL generation.",
            "feedback": {
                "entry_total_tests": 15,
                "entry_failed_tests": 9,
            },
        },
    )
    session.add(savepoint)
    session.flush()
    session.add(
        Stage3SavepointFileResult(
            savepoint_id=savepoint.id,
            test_file_path="tests/test_appctx.py",
            is_entry_file=True,
            status="failed",
            total_tests=15,
            passed_tests=6,
            failed_tests=9,
            error_tests=0,
            skipped_tests=0,
            pass_rate=0.4,
            raw_result_json={},
        )
    )
    session.commit()
    return savepoint


def test_stage4_local_generation_persists_configured_issue_styles(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        assert runtime_stage4["issue_styles"] == ["swe", "fb", "hint"]
        assert runtime_stage4["issue_variant_count"] == 3
        assert runtime_stage4["generation_config"]["schema_version"] == ISSUE_STYLE_CONFIG_SCHEMA_VERSION
        assert runtime_stage4["generation_config"]["enabled_styles"] == ["swe", "fb", "hint"]
        assert [item["id"] for item in runtime_stage4["generation_config"]["styles"]] == [
            "swe",
            "fb",
            "hint",
        ]
        run = service.queue_run(run.id)
        run = service.start_run(run.id)
        source_context = service.source_context_for_run(run.id)
        assert source_context["stage4"]["issue_styles"] == ["swe", "fb", "hint"]
        assert source_context["stage4"]["generation_config"] == runtime_stage4["generation_config"]
        result = LocalStage4Backend(settings).generate_issues(source_context)
        completed = service.complete_run_with_bundle(
            run.id,
            bundle=result.bundle,
            model="openai/gpt-5.4",
            token_usage=12345,
            token_usage_details={"total_tokens": 12345},
        )
        session.commit()

        detail = service.serialize_run_detail(completed)

        assert completed.status == Stage4RunStatus.completed.value
        assert completed.result == "generated"
        assert [item["style"] for item in detail["issue_variants"]] == [
            "swe",
            "fb",
            "hint",
        ]
        assert "hint_variants" not in detail
        assert all("public_output_boundary" not in item["quality"] for item in detail["issue_variants"])
        assert detail["source"]["savepoint"]["diff_stats"]["lines_changed"] == 2
        assert detail["source"]["savepoint"]["gold_patch_text"] == savepoint.gold_patch_text
        assert detail["token_usage_by_model"] == {"openai/gpt-5.4": 12345}
        fb_issue = detail["issue_variants"][1]["issue_markdown"]
        assert "## Task" in fb_issue
        assert "## Interface Descriptions" in fb_issue
        assert "/workspace/repo" in fb_issue
        assert "/testbed" not in fb_issue
        assert "agent_code" not in fb_issue
    finally:
        session.close()


def test_stage4_run_uses_issue_styles_selected_by_settings(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path, issue_styles="swe,fb")
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)

        run = service.create_run(savepoint.id)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})

        assert runtime_stage4["issue_styles"] == ["swe", "fb"]
        assert runtime_stage4["issue_variant_count"] == 2
        assert runtime_stage4["generation_config"]["enabled_styles"] == ["swe", "fb"]
        assert [item["id"] for item in runtime_stage4["generation_config"]["styles"]] == ["swe", "fb"]
    finally:
        session.close()


def test_stage4_obsolete_pending_run_is_rebuilt_before_queue(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        runtime_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        runtime_stage4.pop("generation_config", None)
        runtime_stage4["issue_styles"] = [
            "bug_report",
            "reproduction_report",
            "maintainer_task",
        ]
        runtime_stage4["issue_variant_count"] = 3
        runtime_stage4["hint_strengths"] = ["light", "medium", "strong"]
        runtime_stage4["hint_variant_count"] = 3
        run.issue_variant_count = 3
        run.hint_variant_count = 3
        run.summary_json = {
            **dict(run.summary_json or {}),
            "hint_variant_count": 3,
        }
        run.runtime_snapshot_json = {
            **dict(run.runtime_snapshot_json or {}),
            "stage4": runtime_stage4,
        }
        session.flush()

        run = service.queue_run(run.id)
        rebuilt_stage4 = dict((run.runtime_snapshot_json or {}).get("stage4") or {})
        assert rebuilt_stage4["generation_config"]["schema_version"] == ISSUE_STYLE_CONFIG_SCHEMA_VERSION
        assert rebuilt_stage4["issue_styles"] == ["swe", "fb", "hint"]
        assert "hint_strengths" not in rebuilt_stage4
        assert "hint_variant_count" not in rebuilt_stage4
        assert run.issue_variant_count == 3
        assert run.hint_variant_count == 0
        assert "hint_variant_count" not in run.summary_json
        assert run.summary_json["semantic_rebuild_count"] == 1
        assert any("rebuilt with current generation semantics" in event.title for event in run.events)
        run = service.start_run(run.id)
        source_context = service.source_context_for_run(run.id)
        result = LocalStage4Backend(settings).generate_issues(source_context)
        completed = service.complete_run_with_bundle(run.id, bundle=result.bundle)

        assert [row.style for row in completed.issue_variants] == [
            "swe",
            "fb",
            "hint",
        ]
    finally:
        session.close()


def test_stage4_obsolete_completed_run_can_be_recreated_with_current_semantics(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        old_run = service.create_run(savepoint.id)
        old_stage4 = dict((old_run.runtime_snapshot_json or {}).get("stage4") or {})
        old_stage4.pop("generation_config", None)
        old_stage4["issue_styles"] = ["bug_report", "reproduction_report", "maintainer_task"]
        old_run.runtime_snapshot_json = {
            **dict(old_run.runtime_snapshot_json or {}),
            "stage4": old_stage4,
        }
        old_run.status = Stage4RunStatus.completed.value
        old_run.result = Stage4RunResult.failed.value
        session.flush()

        recreated = service.create_run(savepoint.id)
        recreated_stage4 = dict((recreated.runtime_snapshot_json or {}).get("stage4") or {})

        assert recreated.id != old_run.id
        assert recreated_stage4["generation_config"]["schema_version"] == ISSUE_STYLE_CONFIG_SCHEMA_VERSION
        assert recreated_stage4["issue_styles"] == ["swe", "fb", "hint"]
        assert "hint_strengths" not in recreated_stage4
    finally:
        session.close()


def test_stage4_runtime_update_preserves_generation_config_snapshot(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        before = dict((run.runtime_snapshot_json or {}).get("stage4") or {})

        updated = service.update_run_runtime_snapshot(
            run.id,
            runtime_snapshot={"issuer": {"model": "new-model"}},
        )
        after = dict((updated.runtime_snapshot_json or {}).get("stage4") or {})

        assert after["issue_styles"] == before["issue_styles"] == ["swe", "fb", "hint"]
        assert after["generation_config"] == before["generation_config"]
        assert after["runtime"]["issuer"]["model"] == "new-model"
    finally:
        session.close()


def test_stage4_token_usage_prefers_runtime_model_alias(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        run = service.queue_run(run.id)
        run = service.start_run(run.id)
        source_context = service.source_context_for_run(run.id)
        result = LocalStage4Backend(settings).generate_issues(source_context)
        completed = service.complete_run_with_bundle(
            run.id,
            bundle=result.bundle,
            model="openai/glm-5-w4a8",
            token_usage=12345,
            token_usage_details={"total_tokens": 12345},
        )
        runtime_stage4 = dict((completed.runtime_snapshot_json or {}).get("stage4") or {})
        runtime = dict(runtime_stage4.get("runtime") or {})
        runtime["issuer"] = {
            **dict(runtime.get("issuer") or {}),
            "model": "glm-5-w4a8",
        }
        completed.runtime_snapshot_json = {
            **dict(completed.runtime_snapshot_json or {}),
            "stage4": {
                **runtime_stage4,
                "runtime": runtime,
            },
        }
        session.commit()

        detail = service.serialize_run_detail(completed)

        assert detail["token_usage_by_model"] == {"glm-5-w4a8": 12345}
    finally:
        session.close()


def test_stage4_leakage_check_rejects_patch_text(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        run = service.queue_run(run.id)
        run = service.start_run(run.id)

        with pytest.raises(Stage4RunConflictError, match="leaks private patch or diagnostic details"):
            service.complete_run_with_bundle(
                run.id,
                bundle=_stage4_test_bundle(
                    first_issue_title="Leaky issue",
                    first_issue_markdown=(
                        "diff --git a/src/flask/ctx.py b/src/flask/ctx.py\n"
                        "--- a/src/flask/ctx.py\n"
                        "+++ b/src/flask/ctx.py\n"
                        "@@ -1 +1 @@\n"
                        "-old behavior\n"
                        "+new behavior"
                    ),
                ),
            )
    finally:
        session.close()


def test_stage4_leakage_check_rejects_private_diagnostics(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        run = service.queue_run(run.id)
        run = service.start_run(run.id)

        with pytest.raises(Stage4RunConflictError, match="private_.*test_file"):
            service.complete_run_with_bundle(
                run.id,
                bundle=_stage4_test_bundle(
                    first_issue_title="Leaky diagnostic issue",
                    first_issue_markdown="Run /workspace/run_script.sh on tests/test_appctx.py.",
                ),
            )
    finally:
        session.close()


def test_stage4_leakage_check_allows_generic_diagnostic_language(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.start_run(service.queue_run(service.create_run(savepoint.id).id).id)

        completed = service.complete_run_with_bundle(
            run.id,
            bundle=_stage4_test_bundle(
                first_issue_title="Entry selection behaves inconsistently",
                first_issue_markdown=(
                    "The entry file selected by the public API is sometimes ignored. "
                    "This is visible without naming a target test file or an entry test file. "
                    "The repository also documents run_script.sh, and its git apply parser should "
                    "continue accepting ordinary input. A similarly named path such as "
                    "tests/test_appctx.pyx is public documentation, not a private test identifier."
                ),
            ),
        )

        leakage = dict(completed.issue_variants[0].leakage_check_json or {})
        assert leakage["passed"] is True
        assert leakage["reasons"] == []
    finally:
        session.close()


def test_stage4_leakage_check_warns_but_accepts_gold_patch_line_overlap(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.start_run(service.queue_run(service.create_run(savepoint.id).id).id)

        completed = service.complete_run_with_bundle(
            run.id,
            bundle=_stage4_test_bundle(
                first_issue_title="Document the observed behavior",
                first_issue_markdown="new broken line with enough text to be considered private",
            ),
        )

        leakage = dict(completed.issue_variants[0].leakage_check_json or {})
        assert leakage["passed"] is True
        assert leakage["reasons"] == []
        assert leakage["warnings"] == ["gold_patch_line_overlap"]
    finally:
        session.close()


def test_stage4_serializes_llm_completion_archives(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        runtime_dir = settings.stage4_workspace_dir / "runtime" / run.id / "llm-completions" / "swe"
        runtime_dir.mkdir(parents=True, exist_ok=True)
        (runtime_dir / "swe.json").write_text('{"ok": true}\n', encoding="utf-8")

        detail = service.serialize_run_detail(service.get_run(run.id, include_detail=True))

        archive = detail["llm_completion_archives"]["swe"]
        assert archive["file_count"] == 1
        assert archive["files"][0]["path"] == "llm-completions/swe/swe.json"
        assert set(detail["llm_completion_archives"]) == {"swe", "fb", "hint"}
    finally:
        session.close()


def test_stage4_run_detail_includes_same_savepoint_run_history(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        first_run = service.create_run(savepoint.id)
        first_run.status = Stage4RunStatus.completed.value
        first_run.result = Stage4RunResult.failed.value
        session.flush()
        second_run = service.create_run(savepoint.id)
        session.commit()

        detail = service.serialize_run_detail(service.get_run(second_run.id, include_detail=True))

        assert [item["id"] for item in detail["run_history"]] == [second_run.id, first_run.id]
        assert detail["run_history"][0]["issue_variant_count"] == 3
        assert "hint_variant_count" not in detail["run_history"][0]
    finally:
        session.close()


def test_stage4_list_methods_do_not_truncate_by_default(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)

        for depth in range(3, 208):
            session.add(
                Stage3Savepoint(
                    run_id=savepoint.run_id,
                    depth=depth,
                    entry_pass_rate=max(0.0, 1.0 - depth / 500.0),
                    p2p_files_json=["tests/test_basic.py"],
                    f2p_files_json=["tests/test_appctx.py"],
                    collateral_json={"non_entry_failed_count": 0},
                    gold_patch_text=savepoint.gold_patch_text,
                    checkpoint_json={},
                    summary_json={},
                )
            )
        session.flush()

        session.add_all(
            [
                Stage4Run(
                    source_savepoint_id=savepoint.id,
                    status=Stage4RunStatus.completed.value,
                    result=Stage4RunResult.failed.value,
                    phase="failed",
                    issue_variant_count=3,
                    hint_variant_count=0,
                    runtime_snapshot_json={},
                    summary_json={},
                )
                for _ in range(205)
            ]
        )
        session.commit()

        source_payload = service.list_source_savepoints()
        run_payload = service.list_runs()

        assert source_payload["total"] == 206
        assert len(source_payload["sources"]) == 206
        assert run_payload["total"] == 205
        assert len(run_payload["runs"]) == 205
    finally:
        session.close()


def test_stage4_create_run_rejects_existing_active_run(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        active_run = service.create_run(savepoint.id)

        with pytest.raises(Stage4RunConflictError, match=active_run.id):
            service.create_run(savepoint.id)
    finally:
        session.close()


def test_stage4_run_summary_does_not_expose_start_action_for_pending_run(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)

        pending_summary = service.serialize_run_summary(run)
        assert pending_summary["status"] == Stage4RunStatus.pending.value
        assert "can_start" not in pending_summary
        assert pending_summary["can_interrupt"] is False
        assert pending_summary["can_delete"] is True

        queued = service.queue_run(run.id)
        queued_summary = service.serialize_run_summary(queued)
        assert queued_summary["status"] == Stage4RunStatus.queued.value
        assert "can_start" not in queued_summary
        assert queued_summary["can_interrupt"] is True
        assert queued_summary["can_delete"] is False
    finally:
        session.close()


def test_stage4_init_db_repairs_duplicate_active_runs_and_enforces_single_active_run(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-active-run-invariant.db'}",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    factory = build_session_factory(engine)

    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        first_run = service.create_run(savepoint.id)
        duplicated_run = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.pending.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="manual",
            phase="created",
            workspace_path=str(settings.stage4_workspace_dir / "runs" / "duplicate"),
            runtime_snapshot_json={"stage4": {"source_savepoint_id": savepoint.id}},
            summary_json={"runner_status": "created"},
        )
        session.add(duplicated_run)
        session.commit()
        duplicated_run_id = duplicated_run.id
    finally:
        session.close()

    init_db(engine)

    session = factory()
    try:
        rows = list(
            session.scalars(
                select(Stage4Run)
                .where(Stage4Run.source_savepoint_id == savepoint.id)
                .order_by(Stage4Run.created_at.asc(), Stage4Run.id.asc())
            )
        )
        active_rows = [
            row
            for row in rows
            if row.status in {
                Stage4RunStatus.pending.value,
                Stage4RunStatus.queued.value,
                Stage4RunStatus.running.value,
            }
        ]
        assert len(active_rows) == 1
        stale_rows = [row for row in rows if row.id != active_rows[0].id]
        assert len(stale_rows) == 1
        assert stale_rows[0].status == Stage4RunStatus.completed.value
        assert stale_rows[0].result == Stage4RunResult.failed.value
        assert stale_rows[0].phase == "failed"
        assert stale_rows[0].error_message == (
            "stage4 run invalidated while restoring the single-active-run invariant"
        )
        tombstone = session.get(Stage4CleanupTombstone, stale_rows[0].id)
        assert tombstone is not None
        assert tombstone.reason == "duplicate_active_repair"

        blocked_run = Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.pending.value,
            result=Stage4RunResult.unknown.value,
            trigger_kind="manual",
            phase="created",
            workspace_path=str(settings.stage4_workspace_dir / "runs" / "blocked"),
            runtime_snapshot_json={"stage4": {"source_savepoint_id": savepoint.id}},
            summary_json={"runner_status": "created"},
        )
        session.add(blocked_run)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        assert {row.id for row in rows} == {first_run.id, duplicated_run_id}
    finally:
        session.close()


def test_stage4_mixed_repo_group_status_is_generated(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        session.add(
            Stage3Savepoint(
                run_id=savepoint.run_id,
                depth=3,
                entry_pass_rate=0.2,
                p2p_files_json=["tests/test_basic.py"],
                f2p_files_json=["tests/test_appctx.py"],
                collateral_json={"non_entry_failed_count": 0},
                gold_patch_text=savepoint.gold_patch_text,
                checkpoint_json={},
                summary_json={},
            )
        )
        session.flush()
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        generated_run = service.create_run(savepoint.id)
        generated_run.status = Stage4RunStatus.completed.value
        generated_run.result = Stage4RunResult.generated.value
        session.commit()

        payload = service.list_source_groups(
            page=1,
            page_size=20,
            sort_by="latest_operation_at",
            sort_order="desc",
        )

        assert payload["groups"][0]["status"] == "generated"
    finally:
        session.close()


def test_stage4_interrupted_repo_group_status_stays_interrupted(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        interrupted_run = service.create_run(savepoint.id)
        interrupted_run.status = Stage4RunStatus.completed.value
        interrupted_run.result = Stage4RunResult.interrupted.value
        interrupted_run.phase = "interrupted"
        session.commit()

        list_payload = service.list_source_groups(
            page=1,
            page_size=20,
            sort_by="latest_operation_at",
            sort_order="desc",
        )
        assert list_payload["groups"][0]["status"] == "interrupted"

        filtered_payload = service.list_source_groups(
            page=1,
            page_size=20,
            sort_by="latest_operation_at",
            sort_order="desc",
            statuses=["interrupted"],
        )
        assert [group["status"] for group in filtered_payload["groups"]] == ["interrupted"]

        failed_filtered_payload = service.list_source_groups(
            page=1,
            page_size=20,
            sort_by="latest_operation_at",
            sort_order="desc",
            statuses=["failed"],
        )
        assert failed_filtered_payload["groups"] == []

        detail_payload = service.get_source_group_detail(
            int(savepoint.run.entry_file.snapshot.repository_id),
            str(savepoint.run.entry_file.snapshot_id),
            sort_by="latest_operation_at",
            sort_order="desc",
        )
        assert detail_payload["group"]["status"] == "interrupted"
    finally:
        session.close()


def test_stage4_source_group_detail_filters_and_sorts_by_test_count(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        snapshot = savepoint.run.entry_file.snapshot
        small_entry = Stage3EntryFile(
            snapshot_id=snapshot.id,
            test_file_path="tests/test_small.py",
            baseline_total_tests=5,
            baseline_passed_tests=5,
            baseline_failed_tests=0,
            baseline_error_tests=0,
            baseline_skipped_tests=0,
            baseline_pass_rate=1.0,
            summary_json={},
        )
        session.add(small_entry)
        session.flush()
        small_run = Stage3Run(
            entry_file_id=small_entry.id,
            status=Stage3RunStatus.completed.value,
            result=Stage3RunResult.archived.value,
            trigger_kind="manual",
            phase="completed",
            runtime_snapshot_json={"stage3": {"workspace": {}}},
            summary_json={},
        )
        session.add(small_run)
        session.flush()
        session.add(
            Stage3Savepoint(
                run_id=small_run.id,
                depth=1,
                entry_pass_rate=0.8,
                p2p_files_json=["tests/test_basic.py"],
                f2p_files_json=[],
                collateral_json={"non_entry_failed_count": 0},
                gold_patch_text=savepoint.gold_patch_text,
                checkpoint_json={},
                summary_json={},
            )
        )
        session.commit()

        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        sorted_payload = service.get_source_group_detail(
            int(snapshot.repository_id),
            str(snapshot.id),
            sort_by="test_count",
            sort_order="asc",
        )
        assert [source["entry_file"]["test_file_path"] for source in sorted_payload["sources"]] == [
            "tests/test_small.py",
            "tests/test_appctx.py",
        ]

        filtered_payload = service.get_source_group_detail(
            int(snapshot.repository_id),
            str(snapshot.id),
            sort_by="test_count",
            sort_order="asc",
            test_count_min=10,
            test_count_max=20,
        )
        assert [source["entry_file"]["test_file_path"] for source in filtered_payload["sources"]] == [
            "tests/test_appctx.py",
        ]
        assert filtered_payload["filters"]["test_count_min"] == 10
        assert filtered_payload["filters"]["test_count_max"] == 20
    finally:
        session.close()


def test_stage4_delete_run_upserts_cleanup_tombstone(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        run = service.create_run(savepoint.id)
        run.status = Stage4RunStatus.completed.value
        run.result = Stage4RunResult.generated.value
        run.workspace_path = str(settings.stage4_workspace_dir / "runs" / run.id)
        session.flush()

        archive = service.delete_run(run.id)

        tombstone = session.get(Stage4CleanupTombstone, run.id)
        assert tombstone is not None
        assert tombstone.reason == "manual_delete"
        assert archive["runtime_path"] == str(settings.stage4_workspace_dir / "runtime" / run.id)
    finally:
        session.close()


def test_stage4_cleanup_run_archive_accepts_relative_workspace_root(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        run_id = "11111111-1111-4111-8111-111111111111"
        workspace_dir = Path("stage4") / "runs" / run_id
        runtime_dir = Path("stage4") / "runtime" / run_id
        workspace_dir.mkdir(parents=True)
        runtime_dir.mkdir(parents=True)
        (workspace_dir / "asset.txt").write_text("workspace", encoding="utf-8")
        (runtime_dir / "event.jsonl").write_text("runtime", encoding="utf-8")

        service = Stage4Service(session, workspace_root=Path("stage4"), settings=settings)
        result = service.cleanup_run_archive(
            {
                "run_id": run_id,
                "workspace_path": str(workspace_dir),
                "runtime_path": str(runtime_dir),
            }
        )

        assert result == {"failed_paths": []}
        assert not workspace_dir.exists()
        assert not runtime_dir.exists()
    finally:
        session.close()


def test_stage4_recover_interrupted_runs_marks_interrupted_and_records_tombstone(tmp_path) -> None:
    settings, factory = _session_factory(tmp_path)
    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        queued_run = service.create_run(savepoint.id)
        queued_run.status = Stage4RunStatus.queued.value
        queued_run.workspace_path = str(settings.stage4_workspace_dir / "runs" / queued_run.id)
        session.flush()

        archives = service.recover_interrupted_runs(return_archives=True)

        assert len(archives) == 1
        refreshed = service.get_run(queued_run.id, include_detail=True)
        assert refreshed.status == Stage4RunStatus.completed.value
        assert refreshed.result == Stage4RunResult.interrupted.value
        assert refreshed.phase == "interrupted"
        assert refreshed.error_message == "stage4 run interrupted before execution"
        assert refreshed.summary_json["runner_status"] == "interrupted"
        tombstone = session.get(Stage4CleanupTombstone, queued_run.id)
        assert tombstone is not None
        assert tombstone.reason == "recovered_interrupted_run"
    finally:
        session.close()


def test_stage4_runtime_seed_uses_default_task_concurrency(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-settings.db'}",
        stage4_max_concurrent_runs=24,
        stage4_default_task_max_concurrent_runs=7,
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    session_factory = build_session_factory(engine)
    session = session_factory()
    try:
        service = Stage4Service(session, workspace_root=tmp_path / "stage4", settings=settings)
        assert service._runtime_snapshot_seed()["concurrency"]["max_concurrent_runs"] == 7
    finally:
        session.close()
        engine.dispose()


def test_stage4_prepare_run_workspace_prefers_shared_cache(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage4-materialize.db'}",
        stage2_workspace_dir=tmp_path / "stage2",
        stage4_workspace_dir=tmp_path / "stage4",
    )
    engine = build_engine(settings)
    Base.metadata.create_all(bind=engine)
    factory = build_session_factory(engine)

    source_repo_dir = tmp_path / "source-repo"
    source_repo_dir.mkdir(parents=True)
    _git(source_repo_dir, "init")
    _git(source_repo_dir, "checkout", "-b", "main")
    _git(source_repo_dir, "config", "user.name", "Stage4 Test")
    _git(source_repo_dir, "config", "user.email", "stage4-test@example.com")
    (source_repo_dir / "app.py").write_text("baseline\n", encoding="utf-8")
    _git(source_repo_dir, "add", "app.py")
    _git(source_repo_dir, "commit", "-m", "baseline")
    baseline_commit_sha = _git(source_repo_dir, "rev-parse", "HEAD")

    cache_dir = settings.stage2_workspace_dir / "cache" / "repos" / "pallets" / "flask.git"
    cache_dir.parent.mkdir(parents=True, exist_ok=True)
    _git(tmp_path, "clone", "--bare", str(source_repo_dir), str(cache_dir))

    stage3_repo_dir = tmp_path / "stage3-local-repo"
    _git(tmp_path, "clone", str(source_repo_dir), str(stage3_repo_dir))

    session = factory()
    try:
        savepoint = _seed_stage3_savepoint(session)
        stage3_run = savepoint.run
        repository = savepoint.run.entry_file.snapshot.repository
        snapshot = savepoint.run.entry_file.snapshot
        repository.html_url = str(source_repo_dir)
        repository.default_branch = "main"
        snapshot.source_commit_sha = baseline_commit_sha
        stage3_run.workspace_path = str(tmp_path / "stage3-run")
        stage3_run.runtime_snapshot_json = {
            "stage3": {
                "workspace": {
                    "repo_dir": str(stage3_repo_dir),
                }
            }
        }
        savepoint.gold_patch_text = (
            "diff --git a/app.py b/app.py\n"
            "--- a/app.py\n"
            "+++ b/app.py\n"
            "@@ -1 +1 @@\n"
            "-baseline\n"
            "+broken\n"
        )
        session.flush()

        service = Stage4Service(session, workspace_root=settings.stage4_workspace_dir, settings=settings)
        original_run_checked_command = service._run_checked_command
        observed_git_commands: list[list[str]] = []

        def tracking_run_checked_command(
            args: list[str],
            *,
            timeout_seconds: float,
            error_message: str,
        ):
            observed_git_commands.append(list(args))
            return original_run_checked_command(
                args,
                timeout_seconds=timeout_seconds,
                error_message=error_message,
            )

        service._run_checked_command = tracking_run_checked_command
        run = service.create_run(savepoint.id)
        service.queue_run(run.id)
        service.start_run(run.id)
        service.prepare_run_workspace(run.id)
        refreshed = service.get_run(run.id, include_detail=True)
        runtime_stage4 = dict((refreshed.runtime_snapshot_json or {}).get("stage4") or {})
        assert runtime_stage4["repository_source_kind"] == "shared_cache"
        assert Path(runtime_stage4["repository_clone_source"]) == cache_dir.resolve()
        assert Path(runtime_stage4["repository_cache_path"]) == cache_dir.resolve()
        broken_repo_dir = Path(runtime_stage4["broken_repo_dir"])
        assert (broken_repo_dir / "app.py").read_text(encoding="utf-8") == "broken\n"
        assert not any("safe.directory" in " ".join(args) for args in observed_git_commands)
    finally:
        session.close()