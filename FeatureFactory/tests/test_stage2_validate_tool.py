from __future__ import annotations

import importlib.util
import json
import stat
import sys
import threading
import time
import types
from contextlib import contextmanager
from pathlib import Path

from pydantic import BaseModel


@contextmanager
def _stub_openhands_validate_tool_dependencies():
    tool_module_path = (
        Path(__file__).resolve().parents[1]
        / "src/feature_factory/stage2/openhands_validate_tool.py"
    )
    module_name = "feature_factory_stage2_openhands_validate_tool_test"

    class _Action(BaseModel):
        pass

    class _Observation(BaseModel):
        pass

    class _TextContent(BaseModel):
        text: str

    class _ImageContent(BaseModel):
        pass

    class _ToolDefinition:
        name = "validate"

        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __class_getitem__(cls, item):
            return cls

    class _ToolExecutor:
        def __class_getitem__(cls, item):
            return cls

    registered_tools: list[tuple[str, object]] = []

    def _register_tool(name: str, tool: object) -> None:
        registered_tools.append((name, tool))

    fake_openhands = types.ModuleType("openhands")
    fake_sdk = types.ModuleType("openhands.sdk")
    fake_tool = types.ModuleType("openhands.sdk.tool")
    fake_sdk.Action = _Action
    fake_sdk.ImageContent = _ImageContent
    fake_sdk.Observation = _Observation
    fake_sdk.TextContent = _TextContent
    fake_sdk.ToolDefinition = _ToolDefinition
    fake_tool.ToolExecutor = _ToolExecutor
    fake_tool.register_tool = _register_tool

    previous = {
        name: sys.modules.get(name)
        for name in (
            module_name,
            "openhands",
            "openhands.sdk",
            "openhands.sdk.tool",
        )
    }
    sys.modules["openhands"] = fake_openhands
    sys.modules["openhands.sdk"] = fake_sdk
    sys.modules["openhands.sdk.tool"] = fake_tool

    try:
        spec = importlib.util.spec_from_file_location(module_name, tool_module_path)
        if spec is None or spec.loader is None:
            raise AssertionError("failed to load openhands_validate_tool.py for testing")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        yield module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _write_validate_result_when_request_arrives(
    *,
    request_dir: Path,
    result_dir: Path,
    payload: dict[str, object],
    use_request_sidecar: bool = False,
    request_sidecar_suffix: str = ".result",
) -> threading.Thread:
    def _target() -> None:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            request_files = sorted(request_dir.glob("*.json"))
            if request_files:
                request_id = request_files[0].stem
                output_path = (
                    request_dir / f"{request_id}{request_sidecar_suffix}"
                    if use_request_sidecar
                    else result_dir / f"{request_id}.json"
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                return
            time.sleep(0.01)
        raise AssertionError("validate request file was not created in time")

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    return thread


def test_validate_executor_pauses_conversation_before_returning(monkeypatch, tmp_path) -> None:
    with _stub_openhands_validate_tool_dependencies() as module:
        request_dir = tmp_path / "requests"
        result_dir = tmp_path / "results"
        monkeypatch.setenv(module.VALIDATE_REQUEST_DIR_ENV, str(request_dir))
        monkeypatch.setenv(module.VALIDATE_RESULT_DIR_ENV, str(result_dir))

        writer = _write_validate_result_when_request_arrives(
            request_dir=request_dir,
            result_dir=result_dir,
            payload={
                "result": "retry",
                "phase": "smoke",
                "attempt_index": 2,
                "message": "retry with validator feedback",
                "feedback": {"code": "SMOKE_SAMPLE_BELOW_THRESHOLD"},
                "collect_summary": {"test_file_count": 10},
                "smoke_summary": {"status": "failed"},
                "full_summary": {},
            },
        )

        class FakeConversation:
            def __init__(self) -> None:
                self.pause_calls = 0

            def pause(self) -> None:
                self.pause_calls += 1

        conversation = FakeConversation()
        executor = module.ValidateExecutor()
        action = module.ValidateAction(
            dockerfile_path="/workspace/Dockerfile",
            run_script_path="/workspace/run_script.sh",
        )

        observation = executor(action, conversation=conversation)
        writer.join(timeout=1)

        assert conversation.pause_calls == 1
        assert observation.result == "retry"
        assert observation.phase == "smoke"
        assert observation.attempt_index == 2


def test_validate_executor_skips_pause_when_checkpoint_is_not_requested(monkeypatch, tmp_path) -> None:
    with _stub_openhands_validate_tool_dependencies() as module:
        request_dir = tmp_path / "requests"
        result_dir = tmp_path / "results"
        monkeypatch.setenv(module.VALIDATE_REQUEST_DIR_ENV, str(request_dir))
        monkeypatch.setenv(module.VALIDATE_RESULT_DIR_ENV, str(result_dir))

        writer = _write_validate_result_when_request_arrives(
            request_dir=request_dir,
            result_dir=result_dir,
            payload={
                "result": "retry",
                "phase": "schema",
                "attempt_index": 1,
                "message": "fix the requested paths",
                "feedback": {"code": "VALIDATE_ARTIFACT_READ_FAILED"},
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            },
        )

        class FakeConversation:
            def __init__(self) -> None:
                self.pause_calls = 0

            def pause(self) -> None:
                self.pause_calls += 1

        conversation = FakeConversation()
        executor = module.ValidateExecutor()
        action = module.ValidateAction(
            dockerfile_path="/workspace/Dockerfile",
            run_script_path="/workspace/run_script.sh",
        )

        observation = executor(action, conversation=conversation)
        writer.join(timeout=1)

        assert conversation.pause_calls == 0
        assert observation.result == "retry"
        assert observation.phase == "schema"
        assert observation.attempt_index == 1


def test_validate_executor_makes_request_and_artifacts_cross_user_readable(monkeypatch, tmp_path) -> None:
    with _stub_openhands_validate_tool_dependencies() as module:
        request_dir = tmp_path / "requests"
        result_dir = tmp_path / "results"
        dockerfile_path = tmp_path / "Dockerfile"
        run_script_path = tmp_path / "run_script.sh"
        dockerfile_path.write_text("FROM scratch\n", encoding="utf-8")
        run_script_path.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        dockerfile_path.chmod(0o600)
        run_script_path.chmod(0o600)
        monkeypatch.setenv(module.VALIDATE_REQUEST_DIR_ENV, str(request_dir))
        monkeypatch.setenv(module.VALIDATE_RESULT_DIR_ENV, str(result_dir))

        writer = _write_validate_result_when_request_arrives(
            request_dir=request_dir,
            result_dir=result_dir,
            payload={
                "result": "retry",
                "phase": "schema",
                "attempt_index": 1,
                "message": "feedback",
                "feedback": {},
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            },
        )

        executor = module.ValidateExecutor()
        action = module.ValidateAction(
            dockerfile_path=str(dockerfile_path),
            run_script_path=str(run_script_path),
        )

        observation = executor(action, conversation=None)
        writer.join(timeout=1)

        assert observation.result == "retry"
        request_files = sorted(request_dir.glob("*.json"))
        assert len(request_files) == 1
        for path in (dockerfile_path, run_script_path, request_files[0]):
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode & stat.S_IROTH
            assert not mode & stat.S_IWOTH


def test_validate_executor_reads_request_sidecar_result_when_primary_result_is_missing(
    monkeypatch,
    tmp_path,
) -> None:
    with _stub_openhands_validate_tool_dependencies() as module:
        request_dir = tmp_path / "requests"
        result_dir = tmp_path / "results"
        monkeypatch.setenv(module.VALIDATE_REQUEST_DIR_ENV, str(request_dir))
        monkeypatch.setenv(module.VALIDATE_RESULT_DIR_ENV, str(result_dir))

        writer = _write_validate_result_when_request_arrives(
            request_dir=request_dir,
            result_dir=result_dir,
            payload={
                "result": "failed",
                "phase": "tool",
                "attempt_index": 3,
                "message": "host wrote a fallback result sidecar",
                "feedback": {"code": "VALIDATE_TOOL_HOST_FAILURE_HANDLER_ERROR"},
                "pause_for_checkpoint": False,
                "collect_summary": {},
                "smoke_summary": {},
                "full_summary": {},
            },
            use_request_sidecar=True,
            request_sidecar_suffix=module.VALIDATE_REQUEST_RESULT_SIDECAR_SUFFIX,
        )

        class FakeConversation:
            def __init__(self) -> None:
                self.pause_calls = 0

            def pause(self) -> None:
                self.pause_calls += 1

        executor = module.ValidateExecutor()
        action = module.ValidateAction(
            dockerfile_path="/workspace/Dockerfile",
            run_script_path="/workspace/run_script.sh",
        )

        observation = executor(action, conversation=FakeConversation())
        writer.join(timeout=1)

        assert observation.result == "failed"
        assert observation.phase == "tool"
        assert observation.attempt_index == 3


def test_validate_executor_fails_when_pause_cannot_be_requested(monkeypatch, tmp_path) -> None:
    with _stub_openhands_validate_tool_dependencies() as module:
        request_dir = tmp_path / "requests"
        result_dir = tmp_path / "results"
        monkeypatch.setenv(module.VALIDATE_REQUEST_DIR_ENV, str(request_dir))
        monkeypatch.setenv(module.VALIDATE_RESULT_DIR_ENV, str(result_dir))

        writer = _write_validate_result_when_request_arrives(
            request_dir=request_dir,
            result_dir=result_dir,
            payload={
                "result": "smoke_passed",
                "phase": "smoke",
                "attempt_index": 1,
                "message": "smoke passed",
                "feedback": {},
                "collect_summary": {},
                "smoke_summary": {"status": "passed"},
                "full_summary": {},
            },
        )

        class FailingConversation:
            def pause(self) -> None:
                raise RuntimeError("pause failed")

        executor = module.ValidateExecutor()
        action = module.ValidateAction(
            dockerfile_path="/workspace/Dockerfile",
            run_script_path="/workspace/run_script.sh",
        )

        try:
            executor(action, conversation=FailingConversation())
        except RuntimeError as exc:
            assert str(exc) == "pause failed"
        else:
            raise AssertionError("expected validate executor to fail when pause request fails")
        finally:
            writer.join(timeout=1)
