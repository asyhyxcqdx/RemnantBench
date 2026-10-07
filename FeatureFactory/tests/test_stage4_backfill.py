from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from feature_factory.config import Settings
from feature_factory.db import Base, build_engine, build_session_factory, init_db
from feature_factory.models import (
    DataPool,
    DataPoolAsset,
    GitHubRepository,
    Stage2Run,
    Stage2RunResult,
    Stage2RunStatus,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunResult,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4DataPoolBackfillJob,
    Stage4DataPoolBackfillUnit,
    Stage4IssueVariant,
    Stage4Run,
    Stage4RunResult,
    Stage4RunStatus,
    Stage4RuntimeTemplate,
)
from feature_factory.stage4 import backfill as backfill_module
from feature_factory.stage4.backfill import (
    BackfillError,
    _build_parser,
    _inventory_sha256,
    _override_job_concurrency,
    _redacted_runtime,
    clean_all_job,
    materialize_unit,
    preflight,
    select_eligible_assets,
    verify_job,
)
from feature_factory.stage4.issue_styles import load_issue_style_catalog
from feature_factory.stage4.service import Stage4Service


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _seed_complete_asset(session, tmp_path: Path):
    repository = GitHubRepository(
        github_repo_id=123,
        full_name="example/project",
        owner_login="example",
        name="project",
        html_url="https://github.com/example/project",
        api_url="https://api.github.com/repos/example/project",
        primary_language="Python",
        default_branch="main",
        stargazers_count=10,
    )
    session.add(repository)
    session.flush()
    stage2 = Stage2Run(
        id="stage2-complete",
        repository_id=repository.id,
        status=Stage2RunStatus.completed.value,
        result=Stage2RunResult.passed.value,
        target_commit_sha="a" * 40,
        dockerfile_text="FROM python:3.11\n",
        run_script_text="#!/bin/sh\nexit 0\n",
    )
    session.add(stage2)
    snapshot = Stage3CommitSnapshot(
        repository_id=repository.id,
        source_stage2_run_id=stage2.id,
        source_commit_sha="a" * 40,
        dockerfile_text=stage2.dockerfile_text,
        run_script_text=stage2.run_script_text,
        original_p2p_files_json=[],
    )
    session.add(snapshot)
    session.flush()
    entry = Stage3EntryFile(
        snapshot_id=snapshot.id,
        test_file_path="tests/test_feature.py",
        target_selector="tests/test_feature.py",
        baseline_total_tests=1,
        baseline_passed_tests=1,
        baseline_pass_rate=1.0,
    )
    session.add(entry)
    session.flush()
    stage3 = Stage3Run(
        id="stage3-complete",
        entry_file_id=entry.id,
        status=Stage3RunStatus.completed.value,
        result=Stage3RunResult.archived.value,
    )
    session.add(stage3)
    session.flush()
    savepoint = Stage3Savepoint(
        run_id=stage3.id,
        depth=1,
        entry_pass_rate=0.0,
        p2p_files_json=[],
        f2p_files_json=["tests/test_feature.py"],
        gold_patch_text="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
    )
    session.add(savepoint)
    session.flush()
    old_stage4 = Stage4Service(
        session,
        workspace_root=tmp_path / "stage4",
        settings=Settings(_env_file=None, stage4_issue_styles="swe,fb"),
    ).create_run(savepoint.id, enabled_issue_styles=["swe", "fb"])
    old_stage4.status = Stage4RunStatus.completed.value
    old_stage4.result = Stage4RunResult.generated.value
    old_stage4.phase = "completed"

    source_root = tmp_path / "test1"
    source_root.mkdir()
    pool = DataPool(name="test1", root_path=str(source_root), stats_json={})
    session.add(pool)
    session.flush()
    asset_id = "source-asset"
    folder = source_root / "example__project__aaaaaaaaaaaa__tests__test_feature.py__depth-1"
    folder.mkdir()
    manifest = {
        "schema_version": 1,
        "batch_task_id": "original-batch",
        "data_pool_id": pool.id,
        "asset_id": asset_id,
        "repository": {
            "id": repository.id,
            "github_repo_id": repository.github_repo_id,
            "full_name": repository.full_name,
        },
        "commit": snapshot.source_commit_sha,
        "entry_file": entry.test_file_path,
        "target_selector": entry.target_selector,
        "depth": 1,
        "stage2_run_id": stage2.id,
        "stage3_run_id": stage3.id,
        "stage3_savepoint_id": savepoint.id,
        "stage4_run_id": old_stage4.id,
        "metrics": {"entry_pass_rate": 0.0, "issue_count": 3, "hint_count": 3},
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    _write_json(folder / "manifest.json", manifest)
    _write_json(folder / "issues.json", [{"style": "bug_report"}])
    _write_json(folder / "hints.json", [{"strength": "light"}])
    (folder / "dockerfile").write_text(stage2.dockerfile_text, encoding="utf-8")
    (folder / "run_script.sh").write_text(stage2.run_script_text, encoding="utf-8")
    (folder / "gold.patch").write_text(savepoint.gold_patch_text, encoding="utf-8")
    for name, value in (
        ("original_p2p_files.json", []),
        ("savepoint_feedback.json", {}),
        ("savepoint_full_validation.json", []),
        ("hidden_f2p_files.json", []),
        ("p2p_file_snapshots.json", []),
        ("stage2_full_report.json", {}),
    ):
        _write_json(folder / name, value)
    (folder / "payload.bin").write_bytes(b"unchanged payload")
    _write_json(folder / "llm" / "planner" / "completion.json", {"role": "planner"})
    _write_json(folder / "llm" / "issuer" / "completion.json", {"role": "old issuer"})
    asset = DataPoolAsset(
        id=asset_id,
        pool_id=pool.id,
        repository_id=repository.id,
        github_repo_id=repository.github_repo_id,
        repo_full_name=repository.full_name,
        source_commit_sha=snapshot.source_commit_sha,
        language="Python",
        stars=10,
        entry_file_path=entry.test_file_path,
        depth=1,
        stage2_run_id=stage2.id,
        stage3_run_id=stage3.id,
        stage3_savepoint_id=savepoint.id,
        stage4_run_id=old_stage4.id,
        folder_name=folder.name,
        folder_path=str(folder),
        manifest_json=manifest,
    )
    session.add(asset)
    session.commit()
    return pool, asset, savepoint


def _seed_backfill_with_completed_target(session, tmp_path: Path):
    source_pool, source_asset, _ = _seed_complete_asset(session, tmp_path)
    inventory, rejected = select_eligible_assets(session, source_pool.id)
    assert rejected == []

    target_root = tmp_path / "test2"
    target_root.mkdir()
    target_pool = DataPool(name="test2", root_path=str(target_root), stats_json={})
    template = Stage4RuntimeTemplate(
        name="runtime",
        snapshot_json={
            "concurrency": {"max_concurrent_runs": 2},
            "issuer": {"model": "test-model", "api_key": "secret"},
        },
    )
    session.add_all([target_pool, template])
    session.flush()

    target_asset_id = "target-asset"
    target_folder = target_root / source_asset.folder_name
    shutil.copytree(Path(source_asset.folder_path), target_folder)
    target_asset = DataPoolAsset(
        id=target_asset_id,
        pool_id=target_pool.id,
        repository_id=source_asset.repository_id,
        github_repo_id=source_asset.github_repo_id,
        repo_full_name=source_asset.repo_full_name,
        source_commit_sha=source_asset.source_commit_sha,
        language=source_asset.language,
        stars=source_asset.stars,
        entry_file_path=source_asset.entry_file_path,
        depth=source_asset.depth,
        stage2_run_id=source_asset.stage2_run_id,
        stage3_run_id=source_asset.stage3_run_id,
        stage3_savepoint_id=source_asset.stage3_savepoint_id,
        stage4_run_id=source_asset.stage4_run_id,
        folder_name=source_asset.folder_name,
        folder_path=str(target_folder),
        manifest_json=dict(source_asset.manifest_json or {}),
    )
    job = Stage4DataPoolBackfillJob(
        id="backfill-job",
        source_pool_id=source_pool.id,
        target_pool_id=target_pool.id,
        runtime_template_id=template.id,
        runtime_template_name=template.name,
        runtime_snapshot_json=dict(template.snapshot_json or {}),
        styles_json=["swe", "fb"],
        inventory_sha256=_inventory_sha256(inventory),
        eligible_count=1,
        status="interrupted",
        stats_json={
            "total": 1,
            "completed": 1,
            "payload_size_bytes": 123,
            "issue_style_catalog_sha256": "old-catalog-sha256",
        },
        error_message="interrupted",
    )
    session.add_all([target_asset, job])
    session.flush()
    unit = Stage4DataPoolBackfillUnit(
        job_id=job.id,
        position=1,
        source_asset_id=source_asset.id,
        source_stage4_run_id=str(source_asset.stage4_run_id),
        source_savepoint_id=int(source_asset.stage3_savepoint_id),
        github_repo_id=source_asset.github_repo_id,
        entry_file_path=source_asset.entry_file_path,
        depth=source_asset.depth,
        source_folder_name=source_asset.folder_name,
        source_folder_path=source_asset.folder_path,
        status="completed",
        attempt_count=2,
        stage4_run_id=str(source_asset.stage4_run_id),
        target_asset_id=target_asset.id,
        verification_json={"passed": True},
        error_message="old error",
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(unit)
    session.commit()
    return source_pool, source_asset, target_pool, target_asset, job, unit


def test_eligible_inventory_requires_the_complete_matching_chain(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    pool, asset, _ = _seed_complete_asset(session, tmp_path)

    eligible, rejected = select_eligible_assets(session, pool.id)

    assert rejected == []
    assert [item.asset_id for item in eligible] == [asset.id]
    assert len(_inventory_sha256(eligible)) == 64

    asset.source_commit_sha = "b" * 40
    session.commit()
    eligible, rejected = select_eligible_assets(session, pool.id)
    assert eligible == []
    assert rejected == []
    session.close()


def test_preflight_is_read_only_and_uses_the_saved_runtime_template(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        stage4_openhands_sdk_root=Path("software-agent-sdk").resolve(),
    )
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    pool, asset, _ = _seed_complete_asset(session, tmp_path)
    template = Stage4RuntimeTemplate(
        name="saved-runtime",
        snapshot_json={
            "concurrency": {"max_concurrent_runs": 2},
            "issuer": {"model": "saved-model", "api_key": "secret"},
        },
    )
    session.add(template)
    session.commit()
    target_root = tmp_path / "test2"

    result = preflight(
        session,
        settings,
        source_pool_selector=pool.id,
        target_pool_name="test2",
        target_root=target_root,
        runtime_template_selector=template.name,
        styles=["swe", "fb"],
        calculate_size=False,
    )

    assert result["eligible_count"] == 1
    assert result["inventory"][0].asset_id == asset.id
    assert result["runtime_snapshot"]["issuer"]["model"] == "saved-model"
    assert not target_root.exists()
    assert session.query(DataPool).count() == 1
    assert session.query(DataPoolAsset).count() == 1
    session.close()


def test_preflight_excludes_compatible_assets_already_in_the_target_pool(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        stage4_openhands_sdk_root=Path("software-agent-sdk").resolve(),
    )
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    source_pool, source_asset, _ = _seed_complete_asset(session, tmp_path)
    template = Stage4RuntimeTemplate(
        name="saved-runtime",
        snapshot_json={"issuer": {"model": "saved-model", "api_key": "secret"}},
    )
    target_root = tmp_path / "test2"
    target_root.mkdir()
    target_pool = DataPool(name="test2", root_path=str(target_root), stats_json={})
    session.add_all([template, target_pool])
    session.flush()

    target_id = "already-satisfied-target"
    target_folder = target_root / source_asset.folder_name
    shutil.copytree(Path(source_asset.folder_path), target_folder)
    (target_folder / "hints.json").unlink()
    manifest = json.loads((target_folder / "manifest.json").read_text(encoding="utf-8"))
    manifest.update(
        {
            "asset_id": target_id,
            "data_pool_id": target_pool.id,
            "stage4_run_id": source_asset.stage4_run_id,
            "issue_schema_version": 2,
        }
    )
    _write_json(target_folder / "manifest.json", manifest)
    _write_json(target_folder / "issues.json", [{"style": "swe"}, {"style": "fb"}])
    target_asset = DataPoolAsset(
        id=target_id,
        pool_id=target_pool.id,
        repository_id=source_asset.repository_id,
        github_repo_id=source_asset.github_repo_id,
        repo_full_name=source_asset.repo_full_name,
        source_commit_sha=source_asset.source_commit_sha,
        language=source_asset.language,
        stars=source_asset.stars,
        entry_file_path=source_asset.entry_file_path,
        depth=source_asset.depth,
        stage2_run_id=source_asset.stage2_run_id,
        stage3_run_id=source_asset.stage3_run_id,
        stage3_savepoint_id=source_asset.stage3_savepoint_id,
        stage4_run_id=source_asset.stage4_run_id,
        folder_name=source_asset.folder_name,
        folder_path=str(target_folder),
        manifest_json=manifest,
    )
    session.add(target_asset)
    session.commit()

    with pytest.raises(BackfillError, match="all eligible source assets are already satisfied"):
        preflight(
            session,
            settings,
            source_pool_selector=source_pool.id,
            target_pool_name=target_pool.name,
            target_root=target_root,
            runtime_template_selector=template.name,
            styles=["swe", "fb"],
            calculate_size=False,
        )
    session.close()


def test_preflight_limit_freezes_a_deterministic_inventory_prefix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{tmp_path / 'db.sqlite'}",
        stage4_openhands_sdk_root=Path("software-agent-sdk").resolve(),
    )
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    pool, asset, _ = _seed_complete_asset(session, tmp_path)
    template = Stage4RuntimeTemplate(
        name="saved-runtime",
        snapshot_json={
            "concurrency": {"max_concurrent_runs": 2},
            "issuer": {"model": "saved-model", "api_key": "secret"},
        },
    )
    session.add(template)
    session.commit()
    first = select_eligible_assets(session, pool.id)[0][0]
    inventory = [
        first,
        replace(first, asset_id="source-asset-2", source_savepoint_id=2002),
        replace(first, asset_id="source-asset-3", source_savepoint_id=2003),
    ]
    monkeypatch.setattr(
        "feature_factory.stage4.backfill.select_eligible_assets",
        lambda _session, _pool_id: (inventory, []),
    )

    result = preflight(
        session,
        settings,
        source_pool_selector=pool.id,
        target_pool_name="test2-debug",
        target_root=tmp_path / "test2-debug",
        runtime_template_selector=template.name,
        styles=["swe", "fb"],
        limit=2,
        calculate_size=False,
    )

    assert [item.asset_id for item in result["inventory"]] == [
        "source-asset",
        "source-asset-2",
    ]
    assert result["eligible_count"] == 2
    assert result["available_eligible_count"] == 3
    assert result["limited_out_count"] == 1
    assert result["limit"] == 2

    with pytest.raises(BackfillError, match="--limit must be at least 1"):
        preflight(
            session,
            settings,
            source_pool_selector=pool.id,
            target_pool_name="test2-debug",
            target_root=tmp_path / "test2-debug",
            runtime_template_selector=template.name,
            styles=["swe", "fb"],
            limit=0,
            calculate_size=False,
        )
    session.close()


def test_materialize_unit_only_changes_stage4_payload(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    source_pool, source_asset, savepoint = _seed_complete_asset(session, tmp_path)
    source_hash_before = _tree_hash(Path(source_asset.folder_path))

    target_root = tmp_path / "test2"
    target_root.mkdir()
    target_pool = DataPool(name="test2", root_path=str(target_root), stats_json={})
    template = Stage4RuntimeTemplate(
        name="runtime",
        snapshot_json={
            "concurrency": {"max_concurrent_runs": 1},
            "issuer": {"model": "test-model", "api_key": "secret", "api_key_preview": "secret..."},
        },
    )
    session.add_all([target_pool, template])
    session.flush()
    service = Stage4Service(
        session,
        workspace_root=tmp_path / "stage4",
        settings=Settings(_env_file=None, stage4_issue_styles="swe,fb"),
    )
    new_run = service.create_run(
        savepoint.id,
        trigger_kind="data_pool_backfill",
        enabled_issue_styles=["swe", "fb"],
    )
    new_run.status = Stage4RunStatus.completed.value
    new_run.result = Stage4RunResult.generated.value
    new_run.phase = "completed"
    for index, style in enumerate(("swe", "fb"), start=1):
        new_run.issue_variants.append(
            Stage4IssueVariant(
                variant_index=index,
                style=style,
                title=f"{style} title",
                issue_markdown=f"{style} public task",
                issue_json={"style": style},
                quality_json={},
                leakage_check_json={"passed": True},
            )
        )
        runtime_archive = (
            tmp_path
            / "stage4"
            / "runtime"
            / new_run.id
            / "llm-completions"
            / style
            / "completion.json"
        )
        _write_json(runtime_archive, {"role": style})
    job = Stage4DataPoolBackfillJob(
        source_pool_id=source_pool.id,
        target_pool_id=target_pool.id,
        runtime_template_id=template.id,
        runtime_template_name=template.name,
        runtime_snapshot_json=template.snapshot_json,
        styles_json=["swe", "fb"],
        inventory_sha256="0" * 64,
        eligible_count=1,
    )
    session.add(job)
    session.flush()
    unit = Stage4DataPoolBackfillUnit(
        job_id=job.id,
        position=1,
        source_asset_id=source_asset.id,
        source_stage4_run_id=str(source_asset.stage4_run_id),
        source_savepoint_id=savepoint.id,
        github_repo_id=source_asset.github_repo_id,
        entry_file_path=source_asset.entry_file_path,
        depth=source_asset.depth,
        source_folder_name=source_asset.folder_name,
        source_folder_path=source_asset.folder_path,
        status="materializing",
        stage4_run_id=new_run.id,
    )
    session.add(unit)
    session.flush()

    target_asset = materialize_unit(session, job=job, unit=unit)
    session.commit()

    assert _tree_hash(Path(source_asset.folder_path)) == source_hash_before
    target_folder = Path(target_asset.folder_path)
    assert json.loads((target_folder / "manifest.json").read_text())["issue_schema_version"] == 2
    assert [row["style"] for row in json.loads((target_folder / "issues.json").read_text())] == ["swe", "fb"]
    assert not (target_folder / "hints.json").exists()
    assert not (target_folder / "llm" / "issuer").exists()
    assert (target_folder / "llm" / "planner" / "completion.json").read_bytes() == (
        Path(source_asset.folder_path) / "llm" / "planner" / "completion.json"
    ).read_bytes()
    assert unit.verification_json["source_payload_sha256"] == unit.verification_json["target_payload_sha256"]

    catalog = load_issue_style_catalog(enabled_styles=["swe", "fb"])
    reuse_job = Stage4DataPoolBackfillJob(
        source_pool_id=source_pool.id,
        target_pool_id=target_pool.id,
        runtime_template_id=template.id,
        runtime_template_name=template.name,
        runtime_snapshot_json=template.snapshot_json,
        styles_json=["swe", "fb"],
        inventory_sha256="1" * 64,
        eligible_count=1,
        stats_json={"issue_style_catalog_sha256": catalog.enabled_contract_sha256()},
    )
    session.add(reuse_job)
    session.flush()
    reuse_unit = Stage4DataPoolBackfillUnit(
        job_id=reuse_job.id,
        position=1,
        source_asset_id=source_asset.id,
        source_stage4_run_id=str(source_asset.stage4_run_id),
        source_savepoint_id=savepoint.id,
        github_repo_id=source_asset.github_repo_id,
        entry_file_path=source_asset.entry_file_path,
        depth=source_asset.depth,
        source_folder_name=source_asset.folder_name,
        source_folder_path=source_asset.folder_path,
        status="materializing",
        stage4_run_id=new_run.id,
    )
    session.add(reuse_unit)
    session.flush()
    target_hash_before_reuse = _tree_hash(target_folder)

    reused_asset = materialize_unit(session, job=reuse_job, unit=reuse_unit)
    session.commit()

    assert reused_asset.id == target_asset.id
    assert reuse_unit.status == "skipped"
    assert reuse_unit.target_asset_id == target_asset.id
    assert reuse_unit.verification_json["reason"] == "target_grain_already_exists"
    assert _tree_hash(target_folder) == target_hash_before_reuse
    verification = verify_job(session, reuse_job)
    assert verification["passed"] is True
    assert verification["migrated_count"] == 0
    assert verification["already_present_count"] == 1
    with pytest.raises(BackfillError, match="produced outside this backfill job"):
        clean_all_job(session, reuse_job)
    assert Path(target_asset.folder_path).is_dir()
    assert _redacted_runtime(template.snapshot_json)["issuer"] == {
        "model": "test-model",
        "api_key": "<redacted>",
        "api_key_preview": "<redacted>",
    }
    _override_job_concurrency(session, job, 8, settings=settings)
    assert job.runtime_snapshot_json["concurrency"]["max_concurrent_runs"] == 8
    session.close()


def test_clean_all_removes_only_job_targets_and_resets_every_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    source_pool, source_asset, target_pool, target_asset, job, unit = (
        _seed_backfill_with_completed_target(session, tmp_path)
    )
    source_hash_before = _tree_hash(Path(source_asset.folder_path))
    old_stage4_run_id = str(unit.stage4_run_id)
    target_folder = Path(target_asset.folder_path)
    report_root = tmp_path / "reports"
    monkeypatch.setattr("feature_factory.stage4.backfill.REPORT_ROOT", report_root)
    report_dir = report_root / job.id
    _write_json(report_dir / "summary.json", {"status": "interrupted"})
    job.stats_json = {
        **dict(job.stats_json or {}),
        "inventory_limit": 1,
        "available_eligible_count": 3,
    }
    session.commit()

    result = clean_all_job(session, job)

    session.refresh(job)
    session.refresh(unit)
    session.refresh(target_pool)
    current_catalog_sha256 = load_issue_style_catalog(
        enabled_styles=["swe", "fb"]
    ).enabled_contract_sha256()
    assert result["removed_target_asset_count"] == 1
    assert result["reset_unit_count"] == 1
    assert result["previous_issue_style_catalog_sha256"] == "old-catalog-sha256"
    assert result["current_issue_style_catalog_sha256"] == current_catalog_sha256
    assert job.status == "pending"
    assert job.error_message is None
    assert job.lease_owner is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.stats_json["pending"] == 1
    assert job.stats_json["attempts"] == 0
    assert job.stats_json["issue_style_catalog_sha256"] == current_catalog_sha256
    assert len(job.stats_json["clean_all_history"]) == 1
    assert job.stats_json["inventory_limit"] == 1
    assert job.stats_json["available_eligible_count"] == 3
    assert unit.status == "pending"
    assert unit.attempt_count == 0
    assert unit.stage4_run_id is None
    assert unit.target_asset_id is None
    assert unit.verification_json == {}
    assert unit.error_message is None
    assert unit.started_at is None
    assert unit.finished_at is None
    assert session.get(DataPoolAsset, target_asset.id) is None
    assert session.get(Stage4Run, old_stage4_run_id) is not None
    assert not target_folder.exists()
    assert list(Path(target_pool.root_path).iterdir()) == []
    assert target_pool.stats_json["asset_count"] == 0
    assert _tree_hash(Path(source_asset.folder_path)) == source_hash_before
    history_dirs = list((report_dir / "clean-all-history").iterdir())
    assert len(history_dirs) == 1
    assert (history_dirs[0] / "summary.json").is_file()
    assert (history_dirs[0] / "clean-all.json").is_file()
    assert (history_dirs[0] / "units.jsonl").is_file()
    assert source_pool.id != target_pool.id
    session.close()


def test_clean_all_refuses_unknown_target_files_without_mutating_anything(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    _, source_asset, target_pool, target_asset, job, unit = (
        _seed_backfill_with_completed_target(session, tmp_path)
    )
    monkeypatch.setattr("feature_factory.stage4.backfill.REPORT_ROOT", tmp_path / "reports")
    unknown_file = Path(target_pool.root_path) / "do-not-delete.txt"
    unknown_file.write_text("foreign data", encoding="utf-8")
    target_folder = Path(target_asset.folder_path)

    with pytest.raises(BackfillError, match="unknown entries"):
        clean_all_job(session, job)

    session.refresh(job)
    session.refresh(unit)
    assert job.status == "interrupted"
    assert unit.status == "completed"
    assert session.get(DataPoolAsset, target_asset.id) is not None
    assert target_folder.is_dir()
    assert unknown_file.read_text(encoding="utf-8") == "foreign data"
    assert Path(source_asset.folder_path).is_dir()
    session.close()


def test_clean_all_rolls_back_directory_moves_when_the_database_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    _, _, _, target_asset, job, unit = _seed_backfill_with_completed_target(
        session, tmp_path
    )
    target_folder = Path(target_asset.folder_path)
    target_hash_before = _tree_hash(target_folder)

    def fail_refresh(_self, _pool_id):
        raise RuntimeError("forced stats failure")

    monkeypatch.setattr(
        "feature_factory.stage4.backfill.DataPoolService.refresh_pool_stats",
        fail_refresh,
    )
    with pytest.raises(RuntimeError, match="forced stats failure"):
        clean_all_job(session, job)

    session.refresh(job)
    session.refresh(unit)
    assert job.status == "interrupted"
    assert unit.status == "completed"
    assert unit.target_asset_id == target_asset.id
    assert session.get(DataPoolAsset, target_asset.id) is not None
    assert target_folder.is_dir()
    assert _tree_hash(target_folder) == target_hash_before
    session.close()


def test_clean_all_refuses_a_fresh_executor_lease(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    Base.metadata.create_all(engine)
    session = build_session_factory(engine)()
    _, _, _, target_asset, job, unit = _seed_backfill_with_completed_target(
        session, tmp_path
    )
    job.lease_owner = "still-running"
    job.lease_heartbeat_at = datetime.now(UTC)
    session.commit()

    with pytest.raises(BackfillError, match="still running"):
        clean_all_job(session, job)

    assert session.get(DataPoolAsset, target_asset.id) is not None
    assert Path(target_asset.folder_path).is_dir()
    assert unit.status == "completed"
    session.close()


def test_resume_parser_accepts_clean_all() -> None:
    args = _build_parser().parse_args(
        [
            "resume",
            "--job-id",
            "backfill-job",
            "--clean-all",
            "--concurrency",
            "16",
            "--retries",
            "2",
        ]
    )

    assert args.command == "resume"
    assert args.clean_all is True
    assert args.concurrency == 16
    assert args.retries == 2


def test_resume_schema_preflight_accepts_initialized_database(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)
    init_db(engine)

    backfill_module._validate_resume_schema(engine)


def test_resume_schema_preflight_rejects_missing_database_schema(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{tmp_path / 'db.sqlite'}")
    engine = build_engine(settings)

    with pytest.raises(BackfillError, match="missing table stage4_data_pool_backfill_jobs"):
        backfill_module._validate_resume_schema(engine)


def test_resume_session_factory_validates_without_initializing(monkeypatch) -> None:
    engine = object()
    factory = object()
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(backfill_module, "build_engine", lambda _settings: engine)
    monkeypatch.setattr(
        backfill_module,
        "init_db",
        lambda value: calls.append(("initialize", value)),
    )
    monkeypatch.setattr(
        backfill_module,
        "_validate_resume_schema",
        lambda value: calls.append(("validate", value)),
    )
    monkeypatch.setattr(
        backfill_module,
        "build_session_factory",
        lambda value: factory if value is engine else None,
    )

    result = backfill_module._session_factory(
        Settings(_env_file=None),
        initialize=False,
        validate_resume_schema=True,
    )

    assert result is factory
    assert calls == [("validate", engine)]


def test_resume_main_requests_schema_validation_without_initialization(monkeypatch) -> None:
    calls: list[tuple[bool, bool]] = []

    class FakeSession:
        def get(self, _model, _job_id):  # noqa: ANN001
            return Stage4DataPoolBackfillJob(
                id="backfill-job",
                source_pool_id="source-pool",
                target_pool_id="target-pool",
                runtime_template_name="runtime",
                inventory_sha256="inventory",
                eligible_count=1,
                status="partial",
                runtime_snapshot_json={},
                styles_json=["swe"],
            )

        def close(self) -> None:
            pass

    def fake_session_factory(
        _settings,
        *,
        initialize: bool,
        validate_resume_schema: bool = False,
    ):
        calls.append((initialize, validate_resume_schema))
        return FakeSession

    class FakeExecutor:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def run(self) -> int:
            return 0

    monkeypatch.setattr(backfill_module, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(backfill_module, "_session_factory", fake_session_factory)
    monkeypatch.setattr(backfill_module, "_override_job_concurrency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(backfill_module, "BackfillExecutor", FakeExecutor)

    result = backfill_module.main(["resume", "--job-id", "backfill-job"])

    assert result == 0
    assert calls == [(False, True)]


def test_run_parser_accepts_small_inventory_limit() -> None:
    args = _build_parser().parse_args(
        [
            "run",
            "--source-pool",
            "test1",
            "--target-pool",
            "test2-debug",
            "--target-root",
            "/tmp/test2-debug",
            "--runtime-template",
            "runtime",
            "--limit",
            "7",
        ]
    )

    assert args.limit == 7