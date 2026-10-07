from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from feature_factory.models import Stage3Run
from feature_factory.stage3.service import Stage3Service


def _git(repo_dir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return str(completed.stdout or "").strip()


def test_stage3_gold_patch_excludes_generated_python_metadata(tmp_path: Path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git is not available")

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _git(repo_dir, "init", "-q")
    _git(repo_dir, "config", "user.email", "stage3@example.test")
    _git(repo_dir, "config", "user.name", "Stage3 Test")
    (repo_dir / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo_dir / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    _git(repo_dir, "add", "app.py", "uv.lock")
    _git(repo_dir, "commit", "-q", "-m", "baseline")
    baseline_commit_sha = _git(repo_dir, "rev-parse", "HEAD")

    (repo_dir / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (repo_dir / "uv.lock").write_text("version = 2\n", encoding="utf-8")
    (repo_dir / "pkg.egg-info").mkdir()
    (repo_dir / "pkg.egg-info" / "PKG-INFO").write_text("Name: pkg\n", encoding="utf-8")
    (repo_dir / "nested" / "pkg.dist-info").mkdir(parents=True)
    (repo_dir / "nested" / "pkg.dist-info" / "METADATA").write_text("Name: pkg\n", encoding="utf-8")
    (repo_dir / "__pycache__").mkdir()
    (repo_dir / "__pycache__" / "app.cpython-313.pyc").write_bytes(b"pyc")

    run = Stage3Run(
        id="stage3-test-run",
        runtime_snapshot_json={"stage3": {"baseline_commit_sha": baseline_commit_sha}},
    )
    service = Stage3Service(session=None, workspace_root=tmp_path / "stage3")  # type: ignore[arg-type]

    live_patch = service._live_gold_patch_text(run, repo_dir=repo_dir)
    assert "diff --git a/app.py b/app.py" in live_patch
    assert "diff --git a/uv.lock b/uv.lock" in live_patch
    assert "egg-info" not in live_patch
    assert "dist-info" not in live_patch
    assert "__pycache__" not in live_patch

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-q", "-m", "broken")
    committed_patch = service._gold_patch_text(run, repo_dir=repo_dir)
    assert "diff --git a/app.py b/app.py" in committed_patch
    assert "diff --git a/uv.lock b/uv.lock" in committed_patch
    assert "egg-info" not in committed_patch
    assert "dist-info" not in committed_patch
    assert "__pycache__" not in committed_patch