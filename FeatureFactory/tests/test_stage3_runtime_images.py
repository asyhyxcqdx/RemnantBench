from __future__ import annotations

import subprocess
import stat
from pathlib import Path

import pytest

from feature_factory.config import Settings
from feature_factory.stage3.backend import Stage3BackendEvent, _BridgeTimeoutBudget
from feature_factory.stage3.bridge_events import (
    STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
    STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
)
from feature_factory.stage3.evaluator import (
    CommandResult,
    Stage3EvaluationError,
    Stage3EvaluationResult,
    Stage3Evaluator,
)
from feature_factory.stage3.runtime_images import (
    Stage3DockerBuildResult,
    build_stage3_workspace_image,
    ensure_stage3_breaker_runtime_image_built,
    stage3_breaker_runtime_image_labels,
    stage3_breaker_runtime_image_ref,
    stage3_workspace_dockerfile_path,
)
import feature_factory.stage3.evaluator as stage3_evaluator_module
import feature_factory.stage3.runtime_images as stage3_runtime_images_module


def _prepare_stage3_workspace(tmp_path: Path) -> Path:
    workspace_dir = tmp_path / "workspace"
    assets_dir = workspace_dir / "assets"
    repo_dir = workspace_dir / "repo"
    assets_dir.mkdir(parents=True, exist_ok=True)
    repo_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / "Dockerfile").write_text("FROM python:3.13\n", encoding="utf-8")
    (assets_dir / "run_script.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("print('hello')\n", encoding="utf-8")
    return workspace_dir


def test_build_stage3_workspace_image_uses_frozen_asset_dockerfile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "0")
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured["command"] = list(command)
        captured["kwargs"] = dict(kwargs)
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(stage3_runtime_images_module.subprocess, "run", fake_run)

    result = build_stage3_workspace_image(
        image_ref="feature-factory/stage3-test:run-1",
        workspace_dir=workspace_dir,
        timeout_seconds=123.0,
        platform_name="linux/arm64",
        labels={"com.feature-factory.stage3.kind": "test"},
    )

    assert result.returncode == 0
    command = captured["command"]
    assert command[:3] == ["docker", "build", "--progress=plain"]
    assert "--platform" in command
    assert command[command.index("--platform") + 1] == "linux/arm64"
    assert "--file" in command
    assert command[command.index("--file") + 1] == str(stage3_workspace_dockerfile_path(workspace_dir=workspace_dir))
    assert "--tag" in command
    assert command[command.index("--tag") + 1] == "feature-factory/stage3-test:run-1"
    assert command[-1] == str(workspace_dir.resolve())
    assert captured["kwargs"]["cwd"] == str(workspace_dir.resolve())
    assert captured["kwargs"]["env"]["DOCKER_BUILDKIT"] == "1"
    assert captured["kwargs"]["timeout"] == 123.0


def test_build_stage3_workspace_image_uses_cn_mirrored_build_dockerfile(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FEATURE_FACTORY_DOCKER_USE_CHINA_MIRRORS", "1")
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):  # noqa: ANN001
        dockerfile_arg = Path(command[command.index("--file") + 1])
        captured["command"] = list(command)
        captured["dockerfile_text"] = dockerfile_arg.read_text(encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(stage3_runtime_images_module.subprocess, "run", fake_run)

    build_stage3_workspace_image(
        image_ref="feature-factory/stage3-test:run-1",
        workspace_dir=workspace_dir,
        timeout_seconds=123.0,
    )

    assert captured["dockerfile_text"] == "FROM docker.1ms.run/library/python:3.13\n"
    assert (
        stage3_workspace_dockerfile_path(workspace_dir=workspace_dir).read_text(encoding="utf-8")
        == "FROM python:3.13\n"
    )


def test_stage3_evaluator_runs_frozen_run_script_as_executable(monkeypatch, tmp_path) -> None:
    captured_commands: list[list[str]] = []
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        captured_commands.append(list(command))
        captured_kwargs.append(dict(kwargs))
        if command[:2] == ["docker", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-1\n", stderr="")
        if command[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    evaluator = Stage3Evaluator(Settings())
    result = evaluator._run_test_file(  # noqa: SLF001
        image_tag="feature-factory/stage3-test:run-script",
        repo_dir=tmp_path / "repo",
        run_script_host_path=tmp_path / "run_script.sh",
        test_file_path="tests/test_target.py",
        timeout_seconds=12.0,
    )

    assert result.returncode == 0
    create_command = captured_commands[0]
    assert create_command[:2] == ["docker", "create"]
    assert "--name" in create_command
    shell_script = create_command[-1]
    assert "/workspace/run_script.sh --action run" in shell_script
    assert "/bin/sh /workspace/run_script.sh" not in shell_script
    assert (
        "rc=$?; chmod -R a+rwX /workspace/repo >/dev/null 2>&1 || true; "
        "if [ -s /tmp/run.json ]; then cat /tmp/run.json; fi; exit \"$rc\""
    ) in shell_script
    assert captured_commands[1][0:3] == ["docker", "start", "-a"]
    assert captured_commands[2][0:4] == ["docker", "exec", "--user", "root"]
    assert captured_commands[3][0:3] == ["docker", "rm", "-f"]
    assert captured_kwargs[0]["timeout"] == 12.0
    assert 0 < captured_kwargs[1]["timeout"] <= 12.0


def test_stage3_evaluator_shell_quotes_target_selector(monkeypatch, tmp_path) -> None:
    captured_commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        captured_commands.append(list(command))
        if command[:2] == ["docker", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-quoted\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    evaluator = Stage3Evaluator(Settings())
    evaluator._run_test_file(  # noqa: SLF001
        image_tag="feature-factory/stage3-test:quoted",
        repo_dir=tmp_path / "repo",
        run_script_host_path=tmp_path / "run_script.sh",
        test_file_path="tests/test_$(touch /tmp/stage3-pwn).py",
        timeout_seconds=12.0,
    )

    shell_script = captured_commands[0][-1]
    assert "--target-selector 'tests/test_$(touch /tmp/stage3-pwn).py'" in shell_script
    assert '--target-selector "tests/test_$(touch /tmp/stage3-pwn).py"' not in shell_script


def test_stage3_evaluator_uses_target_selector_separately_from_repo_path(monkeypatch, tmp_path) -> None:
    captured_commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        captured_commands.append(list(command))
        if command[:2] == ["docker", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-selector\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="{}\n", stderr="")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    evaluator = Stage3Evaluator(Settings())
    evaluator._run_test_file(  # noqa: SLF001
        image_tag="feature-factory/stage3-test:selector",
        repo_dir=tmp_path / "repo",
        run_script_host_path=tmp_path / "run_script.sh",
        test_file_path="nltk/test/unit/lm/test_counter.py",
        target_selector="unit/lm/test_counter.py",
        timeout_seconds=12.0,
    )

    shell_script = captured_commands[0][-1]
    assert "--target-selector unit/lm/test_counter.py" in shell_script
    assert "--target-selector nltk/test/unit/lm/test_counter.py" not in shell_script


def test_stage3_evaluator_removes_container_after_timeout(monkeypatch, tmp_path) -> None:
    captured_commands: list[list[str]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        captured_commands.append(list(command))
        if command[:2] == ["docker", "create"]:
            return subprocess.CompletedProcess(command, 0, stdout="container-timeout\n", stderr="")
        if command[:3] == ["docker", "start", "-a"]:
            raise subprocess.TimeoutExpired(command, timeout=12.0, output="partial", stderr="slow")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    evaluator = Stage3Evaluator(Settings())
    result = evaluator._run_test_file(  # noqa: SLF001
        image_tag="feature-factory/stage3-test:timeout",
        repo_dir=tmp_path / "repo",
        run_script_host_path=tmp_path / "run_script.sh",
        test_file_path="tests/test_timeout.py",
        timeout_seconds=12.0,
    )

    assert result.timed_out is True
    assert any(command[:4] == ["docker", "exec", "--user", "root"] for command in captured_commands)
    assert any(command[:3] == ["docker", "rm", "-f"] for command in captured_commands)


def test_stage3_timeout_budget_exempts_host_setup_work() -> None:
    clock_value = 100.0

    def clock() -> float:
        return clock_value

    budget = _BridgeTimeoutBudget(timeout_seconds=10.0, clock=clock)
    budget.observe_event(
        Stage3BackendEvent(
            actor="system",
            phase="breaker_agent",
            title="host setup started",
            message="host setup started",
            payload={
                "operation": STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
                "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_STARTED,
                "request_id": "stage3-runtime-image",
            },
        )
    )
    clock_value = 115.0
    assert not budget.expired()

    budget.observe_event(
        Stage3BackendEvent(
            actor="system",
            phase="breaker_agent",
            title="host setup completed",
            message="host setup completed",
            payload={
                "operation": STAGE3_HOST_SETUP_TIMEOUT_EXEMPT_OPERATION,
                "status": STAGE3_SAVE_TIMEOUT_EXEMPT_STATUS_COMPLETED,
                "request_id": "stage3-runtime-image",
                "duration_seconds": 15.0,
            },
        )
    )
    assert budget.deadline() == 125.0


def test_stage3_breaker_runtime_image_reuses_cached_snapshot_image(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    image_ref = stage3_breaker_runtime_image_ref(
        snapshot_id="snapshot-123",
        platform_name="linux/amd64",
    )
    expected_labels = stage3_breaker_runtime_image_labels(
        workspace_dir=workspace_dir,
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        platform_name="linux/amd64",
    )
    monkeypatch.setattr(
        stage3_runtime_images_module,
        "inspect_docker_image",
        lambda reference: {"present": reference == image_ref, "labels": expected_labels},
    )

    def fail_run(*args, **kwargs):  # noqa: ANN001
        raise AssertionError("docker build should not run when the cached runtime image fingerprint matches")

    monkeypatch.setattr(stage3_runtime_images_module.subprocess, "run", fail_run)
    events: list[tuple[str, str, dict[str, object]]] = []

    resolved = ensure_stage3_breaker_runtime_image_built(
        workspace_dir=workspace_dir,
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        platform_name="linux/amd64",
        timeout_seconds=600.0,
        emit_event=lambda title, message, payload: events.append((title, message, dict(payload))),
    )

    assert resolved == image_ref
    assert events[-1][0] == "Stage3 runtime image ready"
    assert events[-1][2]["reused"] is True


def test_stage3_breaker_runtime_image_build_removes_replaced_stale_image(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    image_ref = stage3_breaker_runtime_image_ref(
        snapshot_id="snapshot-123",
        platform_name="linux/amd64",
    )
    removed: list[tuple[str, str]] = []

    monkeypatch.setattr(
        stage3_runtime_images_module,
        "inspect_docker_image",
        lambda reference: {
            "present": reference == image_ref,
            "image_id": "sha256:old-runtime",
            "labels": {},
        },
    )
    monkeypatch.setattr(
        stage3_runtime_images_module,
        "build_stage3_workspace_image",
        lambda **kwargs: Stage3DockerBuildResult(
            image_ref=kwargs["image_ref"],
            command=["docker", "build"],
            returncode=0,
            stdout="built",
            stderr="",
            timed_out=False,
        ),
    )
    monkeypatch.setattr(
        stage3_runtime_images_module,
        "cleanup_replaced_docker_image",
        lambda **kwargs: removed.append((kwargs["previous_image_id"], kwargs["current_image_ref"])) or [],
    )

    resolved = ensure_stage3_breaker_runtime_image_built(
        workspace_dir=workspace_dir,
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        platform_name="linux/amd64",
        timeout_seconds=600.0,
    )

    assert resolved == image_ref
    assert removed == [("sha256:old-runtime", image_ref)]


def test_stage3_evaluator_build_delegates_to_workspace_image_helper(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    captured: dict[str, object] = {"events": []}

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)

    def fake_ensure_stage3_breaker_runtime_image_built(
        *,
        workspace_dir,
        snapshot_id,
        source_stage2_run_id,
        source_commit_sha,
        base_image_id,
        platform_name,
        timeout_seconds,
        emit_event=None,
    ):  # noqa: ANN001
        captured["workspace_dir"] = workspace_dir
        captured["snapshot_id"] = snapshot_id
        captured["source_stage2_run_id"] = source_stage2_run_id
        captured["source_commit_sha"] = source_commit_sha
        captured["base_image_id"] = base_image_id
        captured["platform_name"] = platform_name
        captured["timeout_seconds"] = timeout_seconds
        if emit_event is not None:
            emit_event("runtime image ready", "reused", {"reused": True})
        return "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64"

    monkeypatch.setattr(
        stage3_evaluator_module,
        "ensure_stage3_breaker_runtime_image_built",
        fake_ensure_stage3_breaker_runtime_image_built,
    )
    monkeypatch.setattr(stage3_evaluator_module, "detect_stage3_platform", lambda: "linux/amd64")

    def fake_run_test_file(
        self,
        *,
        image_tag,
        repo_dir,
        run_script_host_path,
        test_file_path,
        timeout_seconds=None,
        **_kwargs,
    ):  # noqa: ANN001
        captured["image_tag"] = image_tag
        captured["repo_dir"] = repo_dir
        captured["run_script_host_path"] = run_script_host_path
        captured.setdefault("test_file_paths", []).append(test_file_path)
        captured.setdefault("per_file_timeouts", []).append(timeout_seconds)
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=(
                '{"action":"run","status":"passed","summary":{"collected":4,"passed":4,'
                '"failed":0,"errors":0,"skipped":0}}\n'
            ),
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(Stage3Evaluator, "_run_test_file", fake_run_test_file)
    evaluator = Stage3Evaluator(Settings())

    result = evaluator.evaluate_original_p2p(
        run_id="run-123",
        workspace_dir=workspace_dir,
        repo_dir=workspace_dir / "repo",
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        dockerfile_text="FROM python:3.13\n",
        run_script_text=(
            "#!/usr/bin/env bash\n"
            'echo "$@"\n'
            "# supports --action and --out\n"
        ),
        original_p2p_files=["tests/test_target.py"],
        emit_event=lambda title, message, payload: captured["events"].append((title, message, dict(payload))),
    )

    assert captured["workspace_dir"] == workspace_dir
    assert captured["snapshot_id"] == "snapshot-123"
    assert captured["source_stage2_run_id"] == "stage2-run-1"
    assert captured["source_commit_sha"] == "abc123"
    assert captured["base_image_id"] == "python:3.13"
    assert captured["timeout_seconds"] == Settings().stage3_build_timeout_seconds
    assert captured["platform_name"] == "linux/amd64"
    assert captured["image_tag"] == "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64"
    assert captured["repo_dir"] != workspace_dir / "repo"
    assert Path(captured["repo_dir"]).name == "repo"
    assert captured["run_script_host_path"].parent == workspace_dir / ".stage2_validator_runtime"
    assert captured["run_script_host_path"].name == "run_script.sh"
    assert captured["test_file_paths"] == ["tests/test_target.py"]
    assert len(captured["per_file_timeouts"]) == 1
    assert captured["per_file_timeouts"][0] == Settings().stage3_run_test_timeout_seconds
    assert isinstance(result, Stage3EvaluationResult)
    assert result.image_tag == "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64"
    assert [row.test_file_path for row in result.file_results] == ["tests/test_target.py"]
    assert captured["events"][0][0] == "runtime image ready"


def test_stage3_evaluator_uses_disposable_repo_per_original_p2p_file(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    live_repo_dir = workspace_dir / "repo"
    (live_repo_dir / "tests").mkdir()
    (live_repo_dir / "tests" / "test_one.py").write_text("def test_one(): pass\n", encoding="utf-8")
    (live_repo_dir / "tests" / "test_two.py").write_text("def test_two(): pass\n", encoding="utf-8")

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(
        stage3_evaluator_module,
        "ensure_stage3_breaker_runtime_image_built",
        lambda **kwargs: "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64",
    )
    monkeypatch.setattr(stage3_evaluator_module, "detect_stage3_platform", lambda: "linux/amd64")

    seen_repo_dirs: list[Path] = []
    saw_previous_contamination: list[bool] = []

    def fake_run_test_file(
        self,
        *,
        image_tag,
        repo_dir,
        run_script_host_path,
        test_file_path,
        timeout_seconds,
        **_kwargs,
    ):  # noqa: ANN001
        del self, image_tag, run_script_host_path, test_file_path, timeout_seconds
        repo_dir = Path(repo_dir)
        seen_repo_dirs.append(repo_dir)
        saw_previous_contamination.append((repo_dir / "container-side-contamination.txt").exists())
        assert (repo_dir / "app.py").exists()
        (repo_dir / "container-side-contamination.txt").write_text("mutated by test container\n", encoding="utf-8")
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=(
                '{"action":"run","status":"passed","summary":{"collected":1,"passed":1,'
                '"failed":0,"errors":0,"skipped":0}}\n'
            ),
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(Stage3Evaluator, "_run_test_file", fake_run_test_file)

    result = Stage3Evaluator(Settings()).evaluate_original_p2p(
        run_id="run-123",
        workspace_dir=workspace_dir,
        repo_dir=live_repo_dir,
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        dockerfile_text="FROM python:3.13\n",
        run_script_text="#!/usr/bin/env bash\n# supports --action and --out\n",
        original_p2p_files=["tests/test_one.py", "tests/test_two.py"],
    )

    assert [row.test_file_path for row in result.file_results] == ["tests/test_one.py", "tests/test_two.py"]
    assert len(seen_repo_dirs) == 2
    assert seen_repo_dirs[0] != seen_repo_dirs[1]
    assert live_repo_dir not in seen_repo_dirs
    assert saw_previous_contamination == [False, False]
    assert not (live_repo_dir / "container-side-contamination.txt").exists()
    assert not any(repo_dir.exists() for repo_dir in seen_repo_dirs)


def test_stage3_evaluator_copy_repo_tree_prefers_platform_cow_copy(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "app.py").write_text("print('hello')\n", encoding="utf-8")
    commands: list[list[str]] = []

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/bin/cp" if name == "cp" else None)
    monkeypatch.setattr(stage3_evaluator_module.sys, "platform", "darwin")

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        commands.append(list(command))
        destination.mkdir(parents=True)
        (destination / "app.py").write_text((source / "app.py").read_text(encoding="utf-8"), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    Stage3Evaluator(Settings())._copy_repo_tree(source, destination)  # noqa: SLF001

    assert commands == [["cp", "-cR", str(source.resolve()), str(destination.resolve())]]
    assert (destination / "app.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_stage3_evaluator_copy_repo_tree_falls_back_after_failed_cow_copy(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    (source / "app.py").write_text("print('hello')\n", encoding="utf-8")

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/bin/cp" if name == "cp" else None)
    monkeypatch.setattr(stage3_evaluator_module.sys, "platform", "darwin")

    def fake_run(command, **kwargs):  # noqa: ANN001
        del kwargs
        destination.mkdir(parents=True)
        (destination / "partial").write_text("partial clone artifact\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="clone failed")

    monkeypatch.setattr(stage3_evaluator_module.subprocess, "run", fake_run)

    Stage3Evaluator(Settings())._copy_repo_tree(source, destination)  # noqa: SLF001

    assert (destination / "app.py").read_text(encoding="utf-8") == "print('hello')\n"
    assert not (destination / "partial").exists()


def test_stage3_evaluator_copy_repo_tree_makes_destination_container_writable(
    tmp_path,
    monkeypatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    nested = source / "pkg"
    nested.mkdir(parents=True)
    (nested / "app.py").write_text("print('hello')\n", encoding="utf-8")
    source.chmod(0o700)
    nested.chmod(0o700)
    (nested / "app.py").chmod(0o600)

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: None)

    Stage3Evaluator(Settings())._copy_repo_tree(source, destination)  # noqa: SLF001

    assert stat.S_IMODE(destination.stat().st_mode) & 0o777 == 0o777
    assert stat.S_IMODE((destination / "pkg").stat().st_mode) & 0o777 == 0o777
    assert stat.S_IMODE((destination / "pkg" / "app.py").stat().st_mode) & 0o666 == 0o666


def test_stage3_evaluator_uses_overall_full_validation_deadline(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    settings = Settings(stage3_full_validation_timeout_seconds=30.0)
    evaluator = Stage3Evaluator(settings)

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(
        stage3_evaluator_module,
        "ensure_stage3_breaker_runtime_image_built",
        lambda **kwargs: "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64",
    )
    monkeypatch.setattr(stage3_evaluator_module, "detect_stage3_platform", lambda: "linux/amd64")

    monotonic_values = iter([10.0, 10.0, 41.0])
    monkeypatch.setattr(stage3_evaluator_module.time, "monotonic", lambda: next(monotonic_values))

    seen: dict[str, object] = {}

    def fake_run_test_file(
        self,
        *,
        image_tag,
        repo_dir,
        run_script_host_path,
        test_file_path,
        timeout_seconds,
        **_kwargs,
    ):  # noqa: ANN001
        seen["timeout_seconds"] = timeout_seconds
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=(
                '{"action":"run","status":"passed","summary":{"collected":2,"passed":2,'
                '"failed":0,"errors":0,"skipped":0}}\n'
            ),
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(Stage3Evaluator, "_run_test_file", fake_run_test_file)

    with pytest.raises(Stage3EvaluationError) as exc_info:
        evaluator.evaluate_original_p2p(
            run_id="run-123",
            workspace_dir=workspace_dir,
            repo_dir=workspace_dir / "repo",
            snapshot_id="snapshot-123",
            source_stage2_run_id="stage2-run-1",
            source_commit_sha="abc123",
            base_image_id="python:3.13",
            dockerfile_text="FROM python:3.13\n",
            run_script_text="#!/usr/bin/env bash\n# supports --action and --out\n",
            original_p2p_files=["tests/test_one.py", "tests/test_two.py"],
        )

    assert seen["timeout_seconds"] == 30.0
    assert exc_info.value.code == "FULL_VALIDATION_TIMEOUT"
    assert exc_info.value.evidence == {
        "timeout_seconds": 30.0,
        "completed_files": 1,
        "total_files": 2,
        "next_test_file_path": "tests/test_two.py",
    }


def test_stage3_evaluator_caps_each_file_by_run_test_timeout(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    settings = Settings(
        stage3_run_test_timeout_seconds=12.0,
        stage3_full_validation_timeout_seconds=600.0,
    )
    evaluator = Stage3Evaluator(settings)

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(
        stage3_evaluator_module,
        "ensure_stage3_breaker_runtime_image_built",
        lambda **kwargs: "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64",
    )
    monkeypatch.setattr(stage3_evaluator_module, "detect_stage3_platform", lambda: "linux/amd64")

    seen: dict[str, object] = {}

    def fake_run_test_file(
        self,
        *,
        image_tag,
        repo_dir,
        run_script_host_path,
        test_file_path,
        timeout_seconds,
        **_kwargs,
    ):  # noqa: ANN001
        del self, image_tag, repo_dir, run_script_host_path, test_file_path
        seen["timeout_seconds"] = timeout_seconds
        return CommandResult(
            args=["docker", "run"],
            returncode=0,
            stdout=(
                '{"action":"run","status":"passed","summary":{"collected":2,"passed":2,'
                '"failed":0,"errors":0,"skipped":0}}\n'
            ),
            stderr="",
            timed_out=False,
        )

    monkeypatch.setattr(Stage3Evaluator, "_run_test_file", fake_run_test_file)

    result = evaluator.evaluate_original_p2p(
        run_id="run-123",
        workspace_dir=workspace_dir,
        repo_dir=workspace_dir / "repo",
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        dockerfile_text="FROM python:3.13\n",
        run_script_text="#!/usr/bin/env bash\n# supports --action and --out\n",
        original_p2p_files=["tests/test_one.py"],
    )

    assert [row.test_file_path for row in result.file_results] == ["tests/test_one.py"]
    assert seen["timeout_seconds"] == 12.0


def test_stage3_evaluator_single_file_timeout_becomes_error_result(tmp_path, monkeypatch) -> None:
    workspace_dir = _prepare_stage3_workspace(tmp_path)
    settings = Settings(
        stage3_run_test_timeout_seconds=12.0,
        stage3_full_validation_timeout_seconds=600.0,
    )
    evaluator = Stage3Evaluator(settings)

    monkeypatch.setattr(stage3_evaluator_module.shutil, "which", lambda name: "/usr/bin/docker" if name == "docker" else None)
    monkeypatch.setattr(
        stage3_evaluator_module,
        "ensure_stage3_breaker_runtime_image_built",
        lambda **kwargs: "feature-factory/stage3-breaker-runtime:snapshot-123-linux-amd64",
    )
    monkeypatch.setattr(stage3_evaluator_module, "detect_stage3_platform", lambda: "linux/amd64")

    def fake_run_test_file(
        self,
        *,
        image_tag,
        repo_dir,
        run_script_host_path,
        test_file_path,
        timeout_seconds,
        **_kwargs,
    ):  # noqa: ANN001
        del self, image_tag, repo_dir, run_script_host_path, test_file_path
        assert timeout_seconds == 12.0
        return CommandResult(
            args=["docker", "run"],
            returncode=124,
            stdout="",
            stderr="test timed out",
            timed_out=True,
        )

    monkeypatch.setattr(Stage3Evaluator, "_run_test_file", fake_run_test_file)

    result = evaluator.evaluate_original_p2p(
        run_id="run-123",
        workspace_dir=workspace_dir,
        repo_dir=workspace_dir / "repo",
        snapshot_id="snapshot-123",
        source_stage2_run_id="stage2-run-1",
        source_commit_sha="abc123",
        base_image_id="python:3.13",
        dockerfile_text="FROM python:3.13\n",
        run_script_text="#!/usr/bin/env bash\n# supports --action and --out\n",
        original_p2p_files=["tests/test_one.py"],
    )

    assert result.file_results[0].status == "error"
    assert result.file_results[0].error_tests == 1
    assert result.file_results[0].raw_result_json["message"] == "test file execution timed out"
