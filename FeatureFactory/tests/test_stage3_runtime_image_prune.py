from __future__ import annotations

from datetime import UTC, datetime, timedelta

import feature_factory.server as server_module

from feature_factory.config import Settings
from feature_factory.db import build_engine, build_session_factory, init_db
from feature_factory.models import (
    GitHubRepository,
    Stage3CommitSnapshot,
    Stage3EntryFile,
    Stage3Run,
    Stage3RunStatus,
    Stage3Savepoint,
    Stage4Run,
    Stage4RunStatus,
)


def _make_session(tmp_path):
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage3-runtime-prune.db'}")
    engine = build_engine(settings)
    init_db(engine)
    return settings, build_session_factory(engine)()


def _add_active_stage4_snapshot(session, *, snapshot_id: str) -> None:
    repository = GitHubRepository(
        github_repo_id=987654321,
        full_name="owner/runtime-prune",
        owner_login="owner",
        name="runtime-prune",
        html_url="https://github.com/owner/runtime-prune",
        api_url="https://api.github.com/repos/owner/runtime-prune",
        default_branch="main",
        primary_language="Python",
        stargazers_count=1,
        discovered_at=datetime.now(UTC),
    )
    session.add(repository)
    session.flush()
    snapshot = Stage3CommitSnapshot(
        id=snapshot_id,
        repository_id=repository.id,
        source_stage2_run_id="stage2-active",
        source_commit_sha="abc123",
        target_branch="main",
        base_image="python:3.11-jammy-builder",
        dockerfile_text="FROM python:3.11\n",
        run_script_text="pytest\n",
    )
    session.add(snapshot)
    session.flush()
    entry = Stage3EntryFile(snapshot_id=snapshot.id, test_file_path="tests/test_active.py")
    session.add(entry)
    session.flush()
    stage3_run = Stage3Run(
        entry_file_id=entry.id,
        status=Stage3RunStatus.completed.value,
        trigger_kind="batch",
    )
    session.add(stage3_run)
    session.flush()
    savepoint = Stage3Savepoint(run_id=stage3_run.id, depth=1)
    session.add(savepoint)
    session.flush()
    session.add(
        Stage4Run(
            source_savepoint_id=savepoint.id,
            status=Stage4RunStatus.running.value,
            trigger_kind="batch",
        )
    )
    session.commit()


def _runtime_row(
    image_ref: str,
    *,
    snapshot_id: str,
    last_used_at: datetime | None = None,
    created_at: datetime | None = None,
) -> dict:
    return {
        "image_ref": image_ref,
        "category": "stage3_runtime_image",
        "labels": {server_module.FEATURE_FACTORY_STAGE3_SNAPSHOT_ID_LABEL: snapshot_id},
        "last_used_at": last_used_at.isoformat() if last_used_at is not None else None,
        "created_at": created_at.isoformat() if created_at is not None else None,
    }


def test_stage3_runtime_image_prune_plan_selects_expired_runtime_and_wrapper(tmp_path) -> None:
    settings, session = _make_session(tmp_path)
    settings.stage3_runtime_image_retention_days = 7
    now = datetime(2026, 6, 2, 12, 0, 0, tzinfo=UTC)
    expired_ref = "feature-factory/stage3-breaker-runtime:expired-linux_amd64"
    fresh_ref = "feature-factory/stage3-breaker-runtime:fresh-linux_amd64"
    active_ref = "feature-factory/stage3-breaker-runtime:active-linux_amd64"
    active_snapshot_id = "active-snapshot"
    _add_active_stage4_snapshot(session, snapshot_id=active_snapshot_id)
    status_payload = {
        "rows": [
            _runtime_row(expired_ref, snapshot_id="expired-snapshot", last_used_at=now - timedelta(days=8)),
            _runtime_row(fresh_ref, snapshot_id="fresh-snapshot", last_used_at=now - timedelta(days=2)),
            _runtime_row(active_ref, snapshot_id=active_snapshot_id, last_used_at=now - timedelta(days=30)),
            {
                "image_ref": "feature-factory/openhands-agent-server:expired-wrapper",
                "category": "openhands_stage3_breaker_image",
                "labels": {
                    server_module.FEATURE_FACTORY_STAGE2_AGENT_SERVER_BASE_IMAGE_REF_LABEL: expired_ref,
                },
            },
        ]
    }
    try:
        plan = server_module._stage3_runtime_image_prune_plan(
            session,
            settings,
            status_payload=status_payload,
            now=now,
        )
    finally:
        session.close()

    assert plan["runtime_refs"] == [expired_ref]
    assert plan["delete_refs"] == [
        "feature-factory/openhands-agent-server:expired-wrapper",
        expired_ref,
    ]
    assert {"image_ref": fresh_ref, "reason": "within_retention", "snapshot_id": "fresh-snapshot"} in plan["skipped"]
    assert {"image_ref": active_ref, "reason": "active_snapshot", "snapshot_id": active_snapshot_id} in plan["skipped"]


def test_prune_stage3_runtime_images_once_deletes_plan_refs_and_skips_running_containers(
    monkeypatch,
    tmp_path,
) -> None:
    settings, session = _make_session(tmp_path)
    deleted: list[str] = []
    delete_refs = [
        "feature-factory/openhands-agent-server:expired-wrapper",
        "feature-factory/stage3-breaker-runtime:expired-linux_amd64",
        "feature-factory/stage3-breaker-runtime:running-linux_amd64",
    ]

    monkeypatch.setattr(
        server_module,
        "_stage3_runtime_image_prune_plan",
        lambda _session, _settings: {"cutoff": datetime(2026, 5, 26, tzinfo=UTC), "delete_refs": delete_refs},
    )
    monkeypatch.setattr(
        server_module,
        "_docker_image_has_running_containers",
        lambda image_ref: image_ref.endswith(":running-linux_amd64"),
    )
    monkeypatch.setattr(server_module, "_delete_local_docker_image", lambda image_ref: deleted.append(image_ref))
    try:
        result = server_module._prune_stage3_runtime_images_once(session, settings)
    finally:
        session.close()

    assert deleted == delete_refs[:2]
    assert result["deleted"] == delete_refs[:2]
    assert result["skipped_running_containers"] == [delete_refs[2]]
    assert result["failed"] == []