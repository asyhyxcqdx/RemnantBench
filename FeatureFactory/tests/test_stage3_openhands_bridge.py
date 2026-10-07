from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel


def _load_stage3_bridge_module_for_test():
    bridge_module_path = (
        Path(__file__).resolve().parents[1]
        / "src/feature_factory/stage3/openhands_bridge.py"
    )
    module_name = "feature_factory_stage3_openhands_bridge_test"

    class _BaseModel(BaseModel):
        pass

    class _Action(_BaseModel):
        pass

    class _Observation(_BaseModel):
        pass

    class _TextContent(_BaseModel):
        text: str = ""

    class _ImageContent(_BaseModel):
        pass

    class _Agent:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            self.args = args
            self.kwargs = kwargs

    class _Conversation:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            self.args = args
            self.kwargs = kwargs

    class _LLM:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            self.args = args
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

    class _Tool:
        pass

    class _ToolDefinition:
        name = "tool"

        def __init_subclass__(cls, **kwargs):  # noqa: ANN003
            super().__init_subclass__(**kwargs)
            if cls.__name__.endswith("Tool"):
                cls.name = cls.__name__[:-4].lower()

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __class_getitem__(cls, item):
            return cls

    class _ToolExecutor:
        def __class_getitem__(cls, item):
            return cls

    def _register_tool(name: str, tool: object) -> None:
        return None

    class _DockerWorkspace:
        pass

    class _ConversationExecutionStatus:
        RUNNING = "running"
        ERROR = "error"
        FINISHED = "finished"
        STOPPED = "stopped"
        STUCK = "stuck"
        PAUSED = "paused"
        IDLE = "idle"

    class _ActionEvent:
        pass

    class _LLMCompletionLogEvent:
        pass

    class _MessageEvent:
        pass

    class _ObservationEvent:
        pass

    fake_openhands = types.ModuleType("openhands")
    fake_sdk = types.ModuleType("openhands.sdk")
    fake_sdk_conversation = types.ModuleType("openhands.sdk.conversation")
    fake_sdk_conversation_state = types.ModuleType("openhands.sdk.conversation.state")
    fake_sdk_event = types.ModuleType("openhands.sdk.event")
    fake_sdk_tool = types.ModuleType("openhands.sdk.tool")
    fake_tools = types.ModuleType("openhands.tools")
    fake_tools_preset = types.ModuleType("openhands.tools.preset")
    fake_tools_default = types.ModuleType("openhands.tools.preset.default")
    fake_tools_gpt5 = types.ModuleType("openhands.tools.preset.gpt5")
    fake_workspace = types.ModuleType("openhands.workspace")

    fake_sdk.Agent = _Agent
    fake_sdk.Conversation = _Conversation
    fake_sdk.LLM = _LLM
    fake_sdk.Action = _Action
    fake_sdk.Observation = _Observation
    fake_sdk.ImageContent = _ImageContent
    fake_sdk.TextContent = _TextContent
    fake_sdk.ToolDefinition = _ToolDefinition
    fake_sdk_conversation_state.ConversationExecutionStatus = (
        _ConversationExecutionStatus
    )
    fake_sdk_event.ActionEvent = _ActionEvent
    fake_sdk_event.LLMCompletionLogEvent = _LLMCompletionLogEvent
    fake_sdk_event.MessageEvent = _MessageEvent
    fake_sdk_event.ObservationEvent = _ObservationEvent
    fake_sdk_tool.Tool = _Tool
    fake_sdk_tool.ToolExecutor = _ToolExecutor
    fake_sdk_tool.register_tool = _register_tool
    fake_tools_default.get_default_condenser = lambda *args, **kwargs: object()  # noqa: ARG005
    fake_tools_default.get_default_tools = lambda *args, **kwargs: []  # noqa: ARG005
    fake_tools_gpt5.get_gpt5_condenser = lambda *args, **kwargs: object()  # noqa: ARG005
    fake_tools_gpt5.get_gpt5_tools = lambda *args, **kwargs: []  # noqa: ARG005
    fake_workspace.DockerWorkspace = _DockerWorkspace

    previous = {
        name: sys.modules.get(name)
        for name in (
            module_name,
            "openhands",
            "openhands.sdk",
            "openhands.sdk.conversation",
            "openhands.sdk.conversation.state",
            "openhands.sdk.event",
            "openhands.sdk.tool",
            "openhands.tools",
            "openhands.tools.preset",
            "openhands.tools.preset.default",
            "openhands.tools.preset.gpt5",
            "openhands.workspace",
        )
    }

    sys.modules["openhands"] = fake_openhands
    sys.modules["openhands.sdk"] = fake_sdk
    sys.modules["openhands.sdk.conversation"] = fake_sdk_conversation
    sys.modules["openhands.sdk.conversation.state"] = fake_sdk_conversation_state
    sys.modules["openhands.sdk.event"] = fake_sdk_event
    sys.modules["openhands.sdk.tool"] = fake_sdk_tool
    sys.modules["openhands.tools"] = fake_tools
    sys.modules["openhands.tools.preset"] = fake_tools_preset
    sys.modules["openhands.tools.preset.default"] = fake_tools_default
    sys.modules["openhands.tools.preset.gpt5"] = fake_tools_gpt5
    sys.modules["openhands.workspace"] = fake_workspace

    try:
        spec = importlib.util.spec_from_file_location(module_name, bridge_module_path)
        if spec is None or spec.loader is None:
            raise AssertionError(
                "failed to load stage3 openhands bridge module for testing"
            )
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


bridge_module = _load_stage3_bridge_module_for_test()
stage2_bridge_module = sys.modules["feature_factory.stage2.openhands_bridge"]


class _DummySaveService:
    def __init__(self, **kwargs) -> None:
        del kwargs

    def request_checkpoint_for_save_observation(self, event=None) -> None:
        del event

    def bind_conversation_ref(self, conversation_ref) -> None:
        del conversation_ref

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None

    def history_payload(self) -> list[dict[str, object]]:
        return []


def test_openhands_action_payload_keeps_reasoning_content() -> None:
    payload = stage2_bridge_module._action_event_payload(  # noqa: SLF001
        {
            "tool_name": "file_editor",
            "summary": "Create Dockerfile",
            "security_risk": "UNKNOWN",
            "reasoning_content": "I need a Dockerfile before validating the app.",
            "action": {"kind": "file_edit", "command": "create"},
        }
    )

    assert payload["reasoning_content"] == (
        "I need a Dockerfile before validating the app."
    )


def test_openhands_message_payload_keeps_reasoning_content() -> None:
    payload = stage2_bridge_module._message_event_payload(  # noqa: SLF001
        {
            "role": "assistant",
            "source": "agent",
            "reasoning_content": "This response can answer without a tool call.",
            "content": "Done.",
        }
    )

    assert (
        payload["reasoning_content"] == "This response can answer without a tool call."
    )


def _stage3_request(tmp_path: Path) -> dict[str, object]:
    workspace_dir = tmp_path / "run"
    repo_dir = workspace_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    return {
        "mode": "breaker",
        "workspace_dir": str(workspace_dir),
        "repo_path": str(repo_dir),
        "repository": {"full_name": "pallets/flask"},
        "stage3": {
            "snapshot_id": "snapshot-123",
            "source_stage2_run_id": "stage2-run-123",
            "source_commit_sha": "feedface1234",
            "base_image": "python:3.11-jammy-builder",
            "runtime": {},
        },
        "resume_checkpoint": {},
        "preset": "gpt5",
        "max_iterations": 5,
    }


def _install_common_bridge_stubs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        bridge_module,
        "get_settings",
        lambda: SimpleNamespace(model_copy=lambda deep=True: SimpleNamespace()),
    )
    monkeypatch.setattr(
        bridge_module,
        "stage3_settings_with_runtime_snapshot",
        lambda settings, runtime_snapshot: SimpleNamespace(
            stage3_build_timeout_seconds=120.0,
            stage3_enable_checkpoints=True,
        ),
    )
    monkeypatch.setattr(
        bridge_module,
        "_build_llm",
        lambda *, events_path: SimpleNamespace(
            log_completions_folder=str(tmp_path / "llm-completions"),
            model="openai/stage3-breaker",
            base_url="https://example.invalid/v1",
        ),
    )
    monkeypatch.setattr(bridge_module, "_build_agent", lambda *, llm, preset: object())
    monkeypatch.setattr(
        bridge_module,
        "_prepare_breaker_support_files",
        lambda *, workspace_dir: {
            "save_request_dir_host": workspace_dir / ".stage3_runtime" / "requests",
            "save_result_dir_host": workspace_dir / ".stage3_runtime" / "results",
            "tool_python_root_container": "/feature_factory_workspace/.feature_factory_openhands_tools",
            "save_request_dir_container": "/feature_factory_workspace/.stage3_runtime/requests",
            "save_result_dir_container": "/feature_factory_workspace/.stage3_runtime/results",
        },
    )
    monkeypatch.setattr(bridge_module, "_Stage3SaveService", _DummySaveService)
    monkeypatch.setattr(
        bridge_module, "_resolve_worker_resume_server_image", lambda **kwargs: None
    )
    monkeypatch.setattr(bridge_module, "_detect_platform", lambda: "linux/amd64")
    monkeypatch.setattr(bridge_module, "_breaker_extra_volumes", lambda *, support: [])
    monkeypatch.setattr(
        bridge_module, "_build_breaker_prompt", lambda **kwargs: "breaker prompt"
    )
    monkeypatch.setattr(
        bridge_module,
        "_prepare_breaker_resume_conversation_state",
        lambda **kwargs: None,
    )


def _read_events(events_path: Path) -> list[dict[str, object]]:
    if not events_path.exists():
        return []
    return [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_stage3_prepare_breaker_support_files_makes_runtime_parents_traversable(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    assets_dir = workspace_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    runtime_root = workspace_dir / bridge_module.FEATURE_FACTORY_STAGE3_RUNTIME_ROOT
    runtime_root.mkdir()
    runtime_root.chmod(0o777)

    support = bridge_module._prepare_breaker_support_files(workspace_dir=workspace_dir)

    save_root = runtime_root / bridge_module.FEATURE_FACTORY_STAGE3_SAVE_DIR
    runtime_mode = stat.S_IMODE(runtime_root.stat().st_mode)
    save_mode = stat.S_IMODE(save_root.stat().st_mode)
    assert runtime_mode & stat.S_IROTH
    assert runtime_mode & stat.S_IXOTH
    assert not runtime_mode & stat.S_IWGRP
    assert not runtime_mode & stat.S_IWOTH
    assert save_mode & stat.S_IROTH
    assert save_mode & stat.S_IXOTH
    assert not save_mode & stat.S_IWGRP
    assert not save_mode & stat.S_IWOTH
    for key in ("save_request_dir_host", "save_result_dir_host"):
        mode = stat.S_IMODE(Path(support[key]).stat().st_mode)
        assert mode & stat.S_IWOTH
        assert mode & stat.S_IXOTH


def test_stage3_runtime_image_failure_does_not_emit_completed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_common_bridge_stubs(monkeypatch, tmp_path)

    def fake_runtime_image_build(**kwargs):
        log_callback = kwargs.get("log_callback")
        cancel_requested = kwargs.get("cancel_requested")
        assert callable(log_callback)
        assert callable(cancel_requested)
        assert cancel_requested() is False
        log_callback("runtime build line 1")
        log_callback("runtime build line 2")
        raise RuntimeError("runtime image build failed")

    monkeypatch.setattr(
        bridge_module,
        "ensure_stage3_breaker_runtime_image_built",
        fake_runtime_image_build,
    )

    events_path = tmp_path / "runtime-image-failure.events.jsonl"
    request = _stage3_request(tmp_path)

    with pytest.raises(RuntimeError, match="runtime image build failed"):
        bridge_module._run_request(request=request, events_path=events_path)

    titles = [str(event.get("title") or "") for event in _read_events(events_path)]
    assert "Stage3 runtime image preparation started" in titles
    assert "Stage3 runtime image preparation completed" not in titles
    assert "Stage3 runtime image build output" in titles


def test_stage3_agent_server_failure_does_not_emit_completed_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_common_bridge_stubs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        bridge_module,
        "ensure_stage3_breaker_runtime_image_built",
        lambda **kwargs: (
            "feature-factory/stage3-breaker-runtime:snapshot-123-linux_amd64"
        ),
    )

    @contextmanager
    def fake_docker_workspace(*args, **kwargs):
        del args
        log_callback = kwargs.get("agent_server_log_callback")
        cancel_requested = kwargs.get("agent_server_cancel_requested")
        assert callable(log_callback)
        assert callable(cancel_requested)
        assert cancel_requested() is False
        log_callback("agent server build line 1")
        log_callback("agent server build line 2")
        raise RuntimeError("agent server image build failed")
        yield

    monkeypatch.setattr(bridge_module, "_docker_workspace", fake_docker_workspace)

    events_path = tmp_path / "agent-server-failure.events.jsonl"
    request = _stage3_request(tmp_path)

    with pytest.raises(RuntimeError, match="agent server image build failed"):
        bridge_module._run_request(request=request, events_path=events_path)

    titles = [str(event.get("title") or "") for event in _read_events(events_path)]
    assert "Breaker agent-server image preparation started" in titles
    assert "Breaker agent-server image preparation completed" not in titles
    assert "Breaker agent-server image build output" in titles


def test_stage3_checkpoint_docker_commit_helper_sanitizes_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(*args, **kwargs):  # noqa: ANN002, ARG001
        return SimpleNamespace(returncode=0, stdout="committed", stderr="")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)
    workspace = SimpleNamespace(_container_id="container-123")

    payload = bridge_module._commit_breaker_container_checkpoint(
        run_id="Run ID/With Spaces",
        checkpoint_key="request-abc/123",
        workspace=workspace,
    )

    assert payload["docker_commit_status"] == "saved"
    assert payload["docker_image_ref"] == (
        "feature-factory/stage3-breaker-checkpoint:run-id-with-spaces-request-abc-123"
    )


def test_stage3_save_observation_without_pause_request_does_not_arm_checkpoint(
    tmp_path: Path,
) -> None:
    service = bridge_module._Stage3SaveService(  # noqa: SLF001
        run_id="stage3-run-1",
        workspace_dir=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        events_path=tmp_path / "events.jsonl",
        request_dir=tmp_path / "requests",
        result_dir=tmp_path / "results",
    )
    event = bridge_module.ObservationEvent()
    event.tool_name = bridge_module.SaveTool.name
    event.observation = SimpleNamespace(
        pause_for_checkpoint=False,
        accepted=False,
        depth=0,
        request_id="request-123",
    )

    checkpoint_index = service.request_checkpoint_for_save_observation(event=event)

    assert checkpoint_index == 0
    assert service.pending_checkpoint_payload() == {}


def test_stage3_save_result_payloads_are_container_readable_under_strict_umask(
    tmp_path: Path,
) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    request_dir.mkdir()
    result_dir.mkdir()
    service = bridge_module._Stage3SaveService(  # noqa: SLF001
        run_id="stage3-run-1",
        workspace_dir=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    old_umask = os.umask(0o077)
    try:
        primary_path = service._write_result_payload(  # noqa: SLF001
            "request-primary",
            {"request_id": "request-primary", "accepted": False},
        )
        (result_dir / "request-fallback.json").mkdir()
        fallback_path = service._write_result_payload(  # noqa: SLF001
            "request-fallback",
            {"request_id": "request-fallback", "accepted": False},
            allow_request_sidecar_fallback=True,
        )
    finally:
        os.umask(old_umask)

    for path in (primary_path, fallback_path):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IROTH
        assert not mode & stat.S_IWOTH


def test_stage3_save_observation_dict_payload_arms_checkpoint(
    tmp_path: Path,
) -> None:
    service = bridge_module._Stage3SaveService(  # noqa: SLF001
        run_id="stage3-run-1",
        workspace_dir=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        events_path=tmp_path / "events.jsonl",
        request_dir=tmp_path / "requests",
        result_dir=tmp_path / "results",
    )
    event = bridge_module.ObservationEvent()
    event.tool_name = bridge_module.SaveTool.name
    event.observation = {
        "pause_for_checkpoint": True,
        "accepted": True,
        "depth": 2,
        "request_id": "request-456",
    }

    checkpoint_index = service.request_checkpoint_for_save_observation(event=event)

    assert checkpoint_index == 1
    assert service.pending_checkpoint_payload()["checkpoint_key"] == "depth-002"
    assert service.pending_checkpoint_payload()["accepted"] is True
    assert service.pending_checkpoint_payload()["depth"] == 2


def test_stage3_save_observation_checkpoint_disabled_does_not_arm_checkpoint(
    tmp_path: Path,
) -> None:
    service = bridge_module._Stage3SaveService(  # noqa: SLF001
        run_id="stage3-run-1",
        workspace_dir=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        events_path=tmp_path / "events.jsonl",
        request_dir=tmp_path / "requests",
        result_dir=tmp_path / "results",
        checkpoint_enabled=False,
    )
    event = bridge_module.ObservationEvent()
    event.tool_name = bridge_module.SaveTool.name
    event.observation = {
        "pause_for_checkpoint": True,
        "accepted": True,
        "depth": 2,
        "request_id": "request-456",
    }

    checkpoint_index = service.request_checkpoint_for_save_observation(event=event)

    assert checkpoint_index == 0
    assert service.pending_checkpoint_payload() == {}


def test_stage3_recover_pending_checkpoint_from_reconciled_events(
    tmp_path: Path,
) -> None:
    service = bridge_module._Stage3SaveService(  # noqa: SLF001
        run_id="stage3-run-1",
        workspace_dir=tmp_path / "workspace",
        runtime_dir=tmp_path / "runtime",
        events_path=tmp_path / "events.jsonl",
        request_dir=tmp_path / "requests",
        result_dir=tmp_path / "results",
    )
    event = bridge_module.ObservationEvent()
    event.tool_name = bridge_module.SaveTool.name
    event.observation = {
        "pause_for_checkpoint": True,
        "accepted": True,
        "depth": 3,
        "request_id": "request-789",
    }

    class _Events(list):
        def reconcile(self) -> int:
            if not self:
                self.append(event)
                return 1
            return 0

    conversation = SimpleNamespace(state=SimpleNamespace(events=_Events()))

    recovered = (
        bridge_module._recover_pending_stage3_save_checkpoint_from_conversation_events(  # noqa: SLF001
            conversation=conversation,
            save_service=service,
        )
    )

    assert recovered is True
    assert service.pending_checkpoint_payload()["checkpoint_key"] == "depth-003"


def test_stage3_breaker_prompt_includes_entry_pass_rate_ceiling() -> None:
    prompt = bridge_module._build_breaker_prompt(  # noqa: SLF001
        request={
            "repository": {"full_name": "owner/repo"},
        },
        stage3_payload={
            "entry_file_path": "tests/test_target.py",
            "original_p2p_files": ["tests/test_target.py"],
            "entry_pass_rate_ceiling": 0.25,
            "min_removed_code_lines": 12,
        },
    )

    assert "0.250 (25.0%)" in prompt
    assert "remove at least 12 lines of substantive implementation code" in prompt
    assert "not sufficient by itself" in prompt
    assert "milestone, not an automatic finish signal" in prompt
    assert "Crossing the ceiling is not, by itself, a reason to stop." in prompt
    assert "Do not comment out existing working implementation code." in prompt
    assert "The archived gold patch must not contain answer breadcrumbs." in prompt


def test_stage3_bridge_patch_extraction_excludes_generated_metadata(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="diff --git a/app.py b/app.py\n")

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    patch_text = bridge_module._extract_breaker_repo_patch(  # noqa: SLF001
        workspace=SimpleNamespace(_container_id="stage3-container"),
        baseline_commit_sha="abc123",
    )

    script = str(captured["command"][-1])
    assert patch_text == "diff --git a/app.py b/app.py\n"
    assert ":(exclude,glob)*.egg-info" in script
    assert ":(exclude,glob)**/*.egg-info/**" in script
    assert ":(exclude,glob)*.dist-info" in script
    assert ":(exclude,glob)**/*.dist-info/**" in script
    assert "__pycache__" in script
    assert "uv.lock" not in script
    assert "GIT_ALTERNATE_OBJECT_DIRECTORIES" in script
    assert bridge_module.FEATURE_FACTORY_STAGE3_HOST_REPO_DIR in script
    assert 'git init --bare -q "$tmp_root/git"' in script


def _run_test_git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        capture_output=True,
        text=True,
        check=True,
    )


def _create_test_git_baseline(tmp_path: Path) -> tuple[Path, str]:
    repo_dir = tmp_path / "baseline"
    repo_dir.mkdir()
    _run_test_git(repo_dir, "init", "-q")
    _run_test_git(repo_dir, "config", "user.name", "Stage3 Test")
    _run_test_git(repo_dir, "config", "user.email", "stage3-test@example.com")
    (repo_dir / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo_dir / "old.txt").write_text("remove me\n", encoding="utf-8")
    (repo_dir / "binary.bin").write_bytes(b"\x00baseline\xff")
    executable = repo_dir / "run.sh"
    executable.write_text("#!/bin/sh\necho baseline\n", encoding="utf-8")
    executable.chmod(0o644)
    _run_test_git(repo_dir, "add", "-A")
    _run_test_git(repo_dir, "commit", "-q", "-m", "baseline")
    baseline_commit_sha = _run_test_git(repo_dir, "rev-parse", "HEAD").stdout.strip()
    return repo_dir, baseline_commit_sha


def _apply_test_breaker_changes(repo_dir: Path) -> None:
    (repo_dir / "app.py").write_text("def value():\n    return 2\n", encoding="utf-8")
    (repo_dir / "old.txt").unlink()
    (repo_dir / "new.txt").write_text("new file\n", encoding="utf-8")
    (repo_dir / "binary.bin").write_bytes(b"\x00changed\xfe")
    (repo_dir / "run.sh").chmod(0o755)


def _run_breaker_git_script(
    script: str,
    *,
    baseline_commit_sha: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-lc", script],
        env={**os.environ, "BASELINE_COMMIT": baseline_commit_sha},
        capture_output=True,
        text=True,
        check=True,
    )


def test_stage3_bridge_patch_extraction_uses_read_only_host_objects(tmp_path: Path) -> None:
    baseline_repo, baseline_commit_sha = _create_test_git_baseline(tmp_path)
    live_repo = tmp_path / "live-tarball"
    reference_repo = tmp_path / "reference-clone"
    shutil.copytree(
        baseline_repo,
        live_repo,
        ignore=shutil.ignore_patterns(".git"),
        symlinks=True,
    )
    shutil.copytree(baseline_repo, reference_repo, symlinks=True)
    _apply_test_breaker_changes(live_repo)
    _apply_test_breaker_changes(reference_repo)

    _run_test_git(reference_repo, "add", "-A")
    expected_patch = _run_test_git(
        reference_repo,
        "diff",
        "--binary",
        "--cached",
        baseline_commit_sha,
        *bridge_module.stage3_gold_patch_diff_pathspec_args(),
    ).stdout
    host_status_before = _run_test_git(baseline_repo, "status", "--porcelain=v1").stdout
    host_objects_before = {
        path.relative_to(baseline_repo / ".git" / "objects")
        for path in (baseline_repo / ".git" / "objects").rglob("*")
        if path.is_file()
    }

    completed = _run_breaker_git_script(
        bridge_module._breaker_repo_patch_script(  # noqa: SLF001
            repo_dir=str(live_repo),
            baseline_repo_dir=str(baseline_repo),
        ),
        baseline_commit_sha=baseline_commit_sha,
    )
    healthy_repo_completed = _run_breaker_git_script(
        bridge_module._breaker_repo_patch_script(  # noqa: SLF001
            repo_dir=str(reference_repo),
            baseline_repo_dir=str(tmp_path / "missing-host-repo"),
        ),
        baseline_commit_sha=baseline_commit_sha,
    )

    host_objects_after = {
        path.relative_to(baseline_repo / ".git" / "objects")
        for path in (baseline_repo / ".git" / "objects").rglob("*")
        if path.is_file()
    }
    assert completed.stdout == expected_patch
    assert healthy_repo_completed.stdout == expected_patch
    assert not (live_repo / ".git").exists()
    assert _run_test_git(baseline_repo, "status", "--porcelain=v1").stdout == host_status_before
    assert host_objects_after == host_objects_before


def test_stage3_breaker_git_baseline_repairs_missing_and_unrelated_git(tmp_path: Path) -> None:
    baseline_repo, baseline_commit_sha = _create_test_git_baseline(tmp_path)
    live_repo = tmp_path / "live-tarball"
    shutil.copytree(
        baseline_repo,
        live_repo,
        ignore=shutil.ignore_patterns(".git"),
        symlinks=True,
    )
    script = bridge_module._breaker_repo_git_baseline_script(  # noqa: SLF001
        repo_dir=str(live_repo),
        baseline_repo_dir=str(baseline_repo),
    )

    first = _run_breaker_git_script(script, baseline_commit_sha=baseline_commit_sha)

    assert "__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\trepaired\t" in first.stdout
    assert _run_test_git(live_repo, "rev-parse", "HEAD").stdout.strip() == baseline_commit_sha
    assert _run_test_git(live_repo, "status", "--porcelain=v1").stdout == ""
    assert (live_repo / ".git" / "objects" / "info" / "alternates").read_text(
        encoding="utf-8"
    ).strip() == str(baseline_repo / ".git" / "objects")

    _apply_test_breaker_changes(live_repo)
    shutil.rmtree(live_repo / ".git")
    _run_test_git(live_repo, "init", "-q")
    _run_test_git(live_repo, "config", "user.name", "Agent")
    _run_test_git(live_repo, "config", "user.email", "agent@example.com")
    _run_test_git(live_repo, "add", "-A")
    _run_test_git(live_repo, "commit", "-q", "-m", "unrelated agent history")

    repaired = _run_breaker_git_script(script, baseline_commit_sha=baseline_commit_sha)

    assert "__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\trepaired\t" in repaired.stdout
    assert _run_test_git(live_repo, "rev-parse", "HEAD").stdout.strip() == baseline_commit_sha
    assert "return 2" in (live_repo / "app.py").read_text(encoding="utf-8")
    assert "app.py" in _run_test_git(live_repo, "status", "--porcelain=v1").stdout

    existing = _run_breaker_git_script(script, baseline_commit_sha=baseline_commit_sha)

    assert "__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\texisting\t" in existing.stdout
    assert "return 2" in (live_repo / "app.py").read_text(encoding="utf-8")
    assert _run_test_git(baseline_repo, "status", "--porcelain=v1").stdout == ""


def test_stage3_ensure_breaker_git_baseline_parses_repair_status(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):  # noqa: ANN001, ANN202
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="__FEATURE_FACTORY_STAGE3_GIT_BASELINE__\trepaired\tabc123\n",
        )

    monkeypatch.setattr(bridge_module.subprocess, "run", fake_run)

    payload = bridge_module._ensure_breaker_repo_git_baseline(  # noqa: SLF001
        workspace=SimpleNamespace(_container_id="stage3-container"),
        baseline_commit_sha="abc123",
    )

    assert payload == {
        "status": "ready",
        "repair_status": "repaired",
        "baseline_commit_sha": "abc123",
        "container_id": "stage3-container",
    }
    assert "BASELINE_COMMIT=abc123" in captured["command"]


def test_stage3_breaker_finish_threshold_check_records_unmet_ceiling(
    tmp_path: Path,
) -> None:
    events_path = tmp_path / "events.jsonl"

    met = bridge_module._record_breaker_entry_pass_rate_ceiling_result(  # noqa: SLF001
        savepoint_history=[
            {"accepted": True, "depth": 1, "entry_pass_rate": 0.8},
            {"accepted": True, "depth": 2, "entry_pass_rate": 0.6},
        ],
        entry_pass_rate_ceiling=0.5,
        events_path=events_path,
    )

    assert met is False
    events = _read_events(events_path)
    assert events[-1]["title"] == "Breaker finish threshold check"
    assert events[-1]["payload"]["threshold_met"] is False
    assert events[-1]["payload"]["entry_pass_rate_ceiling"] == 0.5
    assert events[-1]["payload"]["latest_entry_pass_rate"] == 0.6