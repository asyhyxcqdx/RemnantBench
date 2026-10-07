from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import types
from pathlib import Path

import pytest
from pydantic import BaseModel

from feature_factory.stage4.issue_styles import IssueStyleConfigError, load_issue_style_catalog


def _load_stage4_bridge_module_for_test():
    bridge_module_path = (
        Path(__file__).resolve().parents[1]
        / "src/feature_factory/stage4/openhands_bridge.py"
    )
    module_name = "feature_factory_stage4_openhands_bridge_test"

    class _BaseModel(BaseModel):
        @classmethod
        def to_mcp_schema(cls):
            return cls.model_json_schema()

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

        def model_copy(self, *, update=None):
            return _LLM(**{**self.kwargs, **dict(update or {})})

    class _Tool:
        def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002
            self.args = args
            self.kwargs = kwargs

    class _ToolDefinition:
        name = "tool"

        def __init_subclass__(cls, **kwargs):  # noqa: ANN003
            super().__init_subclass__(**kwargs)
            if cls.__name__.endswith("Tool"):
                cls.name = cls.__name__[:-4].lower()

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)

        def __class_getitem__(cls, item):
            return cls

    class _ToolExecutor:
        def __class_getitem__(cls, item):
            return cls

    def _register_tool(name: str, tool: object) -> None:
        del name, tool

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

    class _LLMCompletionLogEvent:
        pass

    class _ActionEvent:
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
    fake_sdk_conversation_state.ConversationExecutionStatus = _ConversationExecutionStatus
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
            raise AssertionError("failed to load stage4 openhands bridge module for testing")
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


bridge_module = _load_stage4_bridge_module_for_test()


def _write_receive_request(
    request_dir: Path,
    request_id: str,
    *,
    title: str = "",
    fields: dict[str, object] | None = None,
) -> Path:
    path = request_dir / f"{request_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": request_id,
        "title": title,
        "fields": fields,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return path


def _stage4_source_context() -> dict[str, object]:
    generation_config = load_issue_style_catalog().snapshot(seed="bridge-test-run")
    return {
        "repository": {"full_name": "pallets/flask"},
        "entry_file": {"test_file_path": "tests/test_appctx.py"},
        "savepoint": {"depth": 5, "entry_pass_rate": 0.1333},
        "stage4": {
            "issue_variant_count": 3,
            "issue_styles": ["swe", "fb", "hint"],
            "generation_config": generation_config,
        },
        "private": {
            "gold_patch_text": "diff --git a/src/flask/ctx.py b/src/flask/ctx.py\n+private patch line",
        },
    }


def test_stage4_prepare_issuer_support_files_makes_runtime_parents_traversable(
    tmp_path: Path,
) -> None:
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    assets_dir = workspace_dir / "assets"
    assets_dir.mkdir(parents=True)
    (assets_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    repo_dir = workspace_dir / "repo"
    repo_dir.mkdir()
    (repo_dir / "source.txt").write_text("source\n", encoding="utf-8")
    tool_workspace_host = runtime_dir / "swe_tool_workspace"
    runtime_root = tool_workspace_host / bridge_module.FEATURE_FACTORY_STAGE4_RUNTIME_ROOT
    runtime_root.mkdir(parents=True)
    runtime_root.chmod(0o777)

    support = bridge_module._prepare_issuer_support_files(
        workspace_dir=workspace_dir,
        runtime_dir=runtime_dir,
        role="swe",
    )

    receive_root = runtime_root / bridge_module.FEATURE_FACTORY_STAGE4_RECEIVE_DIR
    runtime_mode = stat.S_IMODE(runtime_root.stat().st_mode)
    receive_mode = stat.S_IMODE(receive_root.stat().st_mode)
    assert runtime_mode & stat.S_IROTH
    assert runtime_mode & stat.S_IXOTH
    assert not runtime_mode & stat.S_IWGRP
    assert not runtime_mode & stat.S_IWOTH
    assert receive_mode & stat.S_IROTH
    assert receive_mode & stat.S_IXOTH
    assert not receive_mode & stat.S_IWGRP
    assert not receive_mode & stat.S_IWOTH
    for key in ("receive_request_dir_host", "receive_result_dir_host"):
        mode = stat.S_IMODE(Path(support[key]).stat().st_mode)
        assert mode & stat.S_IWOTH
        assert mode & stat.S_IXOTH
    isolated_repo_dir = Path(support["repo_host"])
    assert isolated_repo_dir != repo_dir
    assert (isolated_repo_dir / "source.txt").read_text(encoding="utf-8") == "source\n"


def test_stage4_issuer_support_files_isolate_and_clean_each_style_repo(tmp_path: Path) -> None:
    workspace_dir = tmp_path / "workspace"
    runtime_dir = tmp_path / "runtime"
    repo_dir = workspace_dir / "repo"
    assets_dir = workspace_dir / "assets"
    repo_dir.mkdir(parents=True)
    assets_dir.mkdir()
    (repo_dir / "tracked.txt").write_text("original\n", encoding="utf-8")
    (assets_dir / "run_script.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="simulated conversation failure"):
        with bridge_module._issuer_support_files(  # noqa: SLF001
            workspace_dir=workspace_dir,
            runtime_dir=runtime_dir,
            role="swe",
        ) as support:
            isolated_repo_dir = Path(support["repo_host"])
            assert isolated_repo_dir != repo_dir
            (isolated_repo_dir / "tracked.txt").write_text("changed by swe\n", encoding="utf-8")
            (isolated_repo_dir / "generated.txt").write_text("temporary\n", encoding="utf-8")
            raise RuntimeError("simulated conversation failure")

    assert (repo_dir / "tracked.txt").read_text(encoding="utf-8") == "original\n"
    assert not (repo_dir / "generated.txt").exists()
    assert not isolated_repo_dir.exists()


def test_stage4_receive_service_rejects_incomplete_then_accepts_one_task_input(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="hint",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        fields={"content": "The public behavior does not match the expected contract."},
    )
    assert service.process_pending_requests() is True
    payload_1 = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload_1["complete"] is False
    assert payload_1["feedback"]["status"] == "incomplete"
    assert payload_1["feedback"]["missing"] == {"title": 1}
    assert payload_1["feedback"]["errors"][0]["code"] == "MISSING_TITLE"

    _write_receive_request(
        request_dir,
        "call-2",
        title="Public app-context behavior regressed",
        fields={
            "content": (
                "The public app-context workflow no longer matches its documented behavior. "
                "The transition between entering the context and exposing the current state is "
                "the useful boundary to inspect."
            )
        },
    )
    assert service.process_pending_requests() is True
    payload_2 = json.loads((result_dir / "call-2.json").read_text(encoding="utf-8"))
    assert payload_2["complete"] is True
    assert payload_2["feedback"]["status"] == "complete"
    bundle = service.completed_bundle()
    assert bundle is not None
    assert [item["style"] for item in bundle["issue_variants"]] == ["hint"]
    assert "hint_variants" not in bundle
    assert bundle["issue_variants"][0]["issue_json"]["fields"]["content"].startswith(
        "The public app-context workflow"
    )


def test_stage4_receive_result_payloads_are_container_readable_under_strict_umask(
    tmp_path: Path,
) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    request_dir.mkdir()
    result_dir.mkdir()
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    old_umask = os.umask(0o077)
    try:
        service._write_result_payload(  # noqa: SLF001
            "request-primary",
            {"request_id": "request-primary", "complete": False},
        )
        (result_dir / "request-fallback.json").mkdir()
        service._write_result_payload(  # noqa: SLF001
            "request-fallback",
            {"request_id": "request-fallback", "complete": False},
            allow_request_sidecar_fallback=True,
        )
    finally:
        os.umask(old_umask)

    for path in (
        result_dir / "request-primary.json",
        request_dir / "request-fallback.result",
    ):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & stat.S_IROTH
        assert not mode & stat.S_IWOTH


def test_stage4_receive_service_rejects_private_diagnostic_references(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        title="Leaky app-context issue",
        fields={"content": "Run /workspace/run_script.sh against tests/test_appctx.py to see the failure."},
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    errors = payload["feedback"]["errors"]
    assert [item["code"] for item in errors] == ["LEAKAGE_DETECTED"]
    assert "private_runner_reference" in errors[0]["message"]
    assert "private_target_test_file" in errors[0]["message"]


def test_stage4_receive_service_allows_generic_diagnostic_language(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        title="Entry selection behaves inconsistently",
        fields={
            "content": (
                "The entry file selected through the public API is sometimes ignored. "
                "The repository documents run_script.sh, while tests/test_appctx.pyx is "
                "a separate public example rather than a private test identifier."
            )
        },
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["complete"] is True
    assert payload["feedback"]["errors"] == []


def test_stage4_receive_service_assigns_configured_style_on_host(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        title="app context contract regressed",
        fields={"content": "The public app-context behavior no longer matches the expected contract."},
    )
    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["feedback"]["accepted"] is True
    assert service.completed_bundle()["issue_variants"][0]["style"] == "swe"


def test_stage4_receive_service_emits_raw_result_payload_in_event(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    events_path = tmp_path / "events.jsonl"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=events_path,
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        title="app context contract regressed",
        fields={"content": "The public app-context behavior no longer matches the expected contract."},
    )
    assert service.process_pending_requests() is True
    result_payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    event_rows = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    processed_event = next(row for row in event_rows if row.get("title") == "Receive tool call processed")
    assert processed_event["payload"]["result_payload"] == result_payload


def test_stage4_issue_prompts_are_role_local_and_use_single_output_tool() -> None:
    swe_prompt = bridge_module._build_issuer_prompt(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
    )
    fb_prompt = bridge_module._build_issuer_prompt(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
    )
    guided_prompt = bridge_module._build_issuer_prompt(
        source_context=_stage4_source_context(),
        generation_style_id="hint",
    )
    assert "receive" in swe_prompt
    assert "Stage4" not in swe_prompt
    assert "two top-level arrays" not in swe_prompt
    assert "`issues`" not in swe_prompt
    assert "`hints`" not in swe_prompt
    assert swe_prompt.startswith("You are the task author.")
    assert "coding agent" in swe_prompt
    assert "A developer who receives" not in swe_prompt
    assert "Write one public issue" not in swe_prompt
    assert "one `title` and one `fields` object" in swe_prompt
    assert "Do not assemble the final Markdown" in swe_prompt
    assert "Do not make every style uniformly clear" in swe_prompt
    assert "enough truthful, public information" not in swe_prompt
    assert "Example task input in the requested structure" in fb_prompt
    assert "reference patch are confidential evidence" in fb_prompt
    assert "without disclosing an implementation strategy" in fb_prompt
    assert '"reference_patch"' in fb_prompt
    assert "private patch line" in fb_prompt
    assert '"$schema"' not in fb_prompt
    assert '"task_statement"' in fb_prompt
    assert '"interfaces"' in fb_prompt
    assert "receive tool's parameter schema" in fb_prompt
    assert "The host, not you, assembles the final Markdown" in fb_prompt
    assert "FeatureBench" not in fb_prompt
    assert "/testbed" not in fb_prompt
    assert "agent_code" not in fb_prompt
    assert "bare hint" in guided_prompt
    assert "/workspace/run_script.sh" in swe_prompt
    assert "issuer_result.json" not in swe_prompt


def test_stage4_receive_tool_dynamically_exposes_each_style_field_structure() -> None:
    source_context = _stage4_source_context()

    def receive_schema(style_id: str) -> dict[str, object]:
        generation_spec = bridge_module.runtime_generation_spec(
            source_context["stage4"],
            style_id,
        )
        full_schema = bridge_module.style_fields_schema(generation_spec)
        tool = bridge_module.ReceiveTool.create(fields_schema=full_schema)[0]
        return tool.action_type.to_mcp_schema()

    fb_schema = receive_schema("fb")
    swe_schema = receive_schema("swe")
    hint_schema = receive_schema("hint")

    fb_fields = fb_schema["properties"]["fields"]
    assert fb_fields["required"] == ["task_statement", "interfaces"]
    assert set(fb_fields["properties"]) == {"task_statement", "interfaces"}
    interface_item = fb_fields["properties"]["interfaces"]["items"]
    assert interface_item["required"] == ["description", "path", "code"]
    assert set(interface_item["properties"]) == {"description", "path", "code"}
    description_schema = interface_item["properties"]["description"]
    path_schema = interface_item["properties"]["path"]
    code_schema = interface_item["properties"]["code"]
    assert "public purpose and input/output role" in description_schema["description"]
    assert "not a complete behavior specification" in description_schema["description"]
    assert "repository-relative POSIX file path" in path_schema["description"]
    assert "single-line" in path_schema["description"]
    assert "parent or current-directory traversal" in path_schema["description"]
    assert "Markdown backticks" in path_schema["description"]
    assert "literal '<your code>'" in code_schema["description"]
    assert "Markdown fences" in code_schema["description"]
    assert "repository file" in code_schema["description"]
    assert "imports" not in code_schema["description"]
    assert "Python" not in code_schema["description"]
    assert "allOf" not in code_schema
    assert "x-error-code" not in json.dumps(fb_fields)

    for schema in (swe_schema, hint_schema):
        style_fields = schema["properties"]["fields"]
        assert style_fields["required"] == ["content"]
        assert set(style_fields["properties"]) == {"content"}
    assert "public symptoms and workflows" in swe_schema["properties"]["fields"]["properties"]["content"]["description"]
    assert "configured level of diagnostic guidance" in hint_schema["properties"]["fields"]["properties"]["content"]["description"]


def test_stage4_agent_passes_current_style_schema_to_receive_tool() -> None:
    source_context = _stage4_source_context()
    generation_spec = bridge_module.runtime_generation_spec(source_context["stage4"], "fb")
    full_schema = bridge_module.style_fields_schema(generation_spec)

    agent = bridge_module._build_agent(
        llm=bridge_module.LLM(model="test-model"),
        preset="gpt5",
        fields_schema=full_schema,
    )

    receive_spec = agent.kwargs["tools"][-1]
    assert receive_spec.kwargs["name"] == "receive"
    assert receive_spec.kwargs["params"] == {"fields_schema": full_schema}


def test_stage4_receive_service_does_not_accept_agent_selected_style(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="swe",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    path = _write_receive_request(
        request_dir,
        "call-1",
        title="Public behavior regressed",
        fields={"content": "The public workflow no longer matches its documented behavior."},
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["style"] = "bug_report"
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert service.process_pending_requests() is True
    assert service.completed_bundle()["issue_variants"][0]["style"] == "swe"


def test_stage4_receive_service_rejects_legacy_snapshot_styles(tmp_path: Path) -> None:
    source_context = _stage4_source_context()
    source_context["stage4"] = {
        "issue_variant_count": 3,
        "issue_styles": ["bug_report", "reproduction_report", "maintainer_task"],
        "hint_strengths": ["light", "medium", "strong"],
    }
    with pytest.raises(IssueStyleConfigError, match="missing generation_config"):
        bridge_module._Stage4ReceiveService(
            source_context=source_context,
            generation_style_id="swe",
            events_path=tmp_path / "events.jsonl",
            request_dir=tmp_path / "requests",
            result_dir=tmp_path / "results",
        )


def test_stage4_fb_receive_validates_fields_and_renders_fixed_template(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )

    _write_receive_request(
        request_dir,
        "call-1",
        title="Add application-context inspection helpers",
        fields={"task_statement": "Add public helpers for inspecting application context state."},
    )
    assert service.process_pending_requests() is True
    incomplete = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert incomplete["feedback"]["status"] == "incomplete"
    assert incomplete["feedback"]["missing"] == {"fields.interfaces": 1}
    assert incomplete["feedback"]["errors"][0]["code"] == "MISSING_FIELD"

    fields = {
        "task_statement": (
            "Add public application-context inspection helpers while preserving the existing "
            "context lifecycle and error behavior."
        ),
        "interfaces": [
            {
                "description": (
                    "Return the public state for a named application context while preserving "
                    "the established lifecycle and error behavior."
                ),
                "path": "src/flask/ctx.py",
                "code": (
                    "def inspect_app_context(name: str) -> dict[str, object]:\n"
                    "    \"\"\"Return the public state associated with an application context.\"\"\"\n"
                    "    <your code>"
                ),
            },
            {
                "description": (
                    "Expose application-context inspection through the application API and return "
                    "the same public state representation."
                ),
                "path": "src/flask/app.py",
                "code": (
                    "class AppContextInspector:\n"
                    "    \"\"\"Expose application-context state through the application API.\"\"\"\n"
                    "    <your code>"
                ),
            },
        ],
    }
    _write_receive_request(
        request_dir,
        "call-2",
        title="Add application-context inspection helpers",
        fields=fields,
    )
    assert service.process_pending_requests() is True
    complete = json.loads((result_dir / "call-2.json").read_text(encoding="utf-8"))
    assert complete["feedback"]["status"] == "complete"

    variant = service.completed_bundle()["issue_variants"][0]
    markdown = variant["issue_markdown"]
    assert markdown.startswith("## Task\n")
    assert "## Interface Descriptions" in markdown
    assert "### Interface Description 1" in markdown
    assert "### Interface Description 2" in markdown
    assert "<public interface declaration or signature>" in markdown
    assert "Path: `/workspace/repo/<repository-relative-path>`" in markdown
    assert "Return the public state for a named application context" in markdown
    assert "Expose application-context inspection through the application API" in markdown
    assert markdown.count("Return the public state for a named application context") == 1
    assert "Path: `/workspace/repo/src/flask/ctx.py`" in markdown
    assert "Path: `/workspace/repo/src/flask/app.py`" in markdown
    assert "/testbed" not in markdown
    assert "agent_code" not in markdown
    assert "FeatureBench" not in markdown
    assert variant["issue_json"]["fields"] == fields
    assert variant["quality_json"]["style_validation"]["rendered_sha256"]


def test_stage4_fb_receive_reports_path_and_interface_errors(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    _write_receive_request(
        request_dir,
        "call-1",
        title="Add context inspection",
        fields={
            "task_statement": "Add a context inspection interface.",
            "interfaces": [
                {
                    "description": "Return the observable state of the active context.",
                    "path": "/testbed/src/flask/ctx.py",
                    "code": "def inspect_context() -> dict:\n    pass",
                }
            ],
        },
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["feedback"]["status"] == "incomplete"
    assert {item["code"] for item in payload["feedback"]["errors"]} == {
        "INVALID_REPO_PATH",
        "MISSING_IMPLEMENTATION_PLACEHOLDER",
    }


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "src/client.ts\n",
        "src/client.ts\rINJECT",
        "src/cli`ent.ts",
        "src/\x01client.ts",
        "src/client.ts\u2028INJECT",
    ],
)
def test_stage4_fb_receive_rejects_markdown_unsafe_paths(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    _write_receive_request(
        request_dir,
        "call-1",
        title="Add a public client",
        fields={
            "task_statement": "Add a public client operation.",
            "interfaces": [
                {
                    "description": "Run the public operation and return its observable result.",
                    "path": unsafe_path,
                    "code": "export function run(): Result {\n  <your code>\n}",
                }
            ],
        },
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["feedback"]["status"] == "incomplete"
    assert {item["code"] for item in payload["feedback"]["errors"]} == {
        "INVALID_REPO_PATH",
    }


def test_stage4_fb_receive_requires_description_and_real_placeholder_line(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    _write_receive_request(
        request_dir,
        "call-1",
        title="Add a public client",
        fields={
            "task_statement": "Add a public client operation.",
            "interfaces": [
                {
                    "path": "src/client.ts",
                    "code": "export function run(): Result {\n  <your code>\n}",
                }
            ],
        },
    )

    assert service.process_pending_requests() is True
    missing_description = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert missing_description["feedback"]["status"] == "incomplete"
    assert missing_description["feedback"]["missing"] == {
        "fields.interfaces[0].description": 1,
    }

    _write_receive_request(
        request_dir,
        "call-2",
        title="Add a public client",
        fields={
            "task_statement": "Add a public client operation.",
            "interfaces": [
                {
                    "description": "Run the public client operation and return its observable result.",
                    "path": "src/client.ts",
                    "code": "export function run(): Result {\n  // <your code>\n}",
                }
            ],
        },
    )

    assert service.process_pending_requests() is True
    fake_placeholder = json.loads((result_dir / "call-2.json").read_text(encoding="utf-8"))
    assert fake_placeholder["feedback"]["status"] == "incomplete"
    assert fake_placeholder["feedback"]["errors"][0]["code"] == "MISSING_IMPLEMENTATION_PLACEHOLDER"


def test_stage4_fb_receive_allows_multiple_interfaces_in_the_same_file(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    _write_receive_request(
        request_dir,
        "call-1",
        title="Add application helpers",
        fields={
            "task_statement": "Add two public helpers to the application module.",
            "interfaces": [
                {
                    "description": "Create and return a configured application instance.",
                    "path": "src/flask/app.py",
                    "code": "def create_app() -> object:\n    <your code>",
                },
                {
                    "description": "Represent an application through its public lifecycle API.",
                    "path": "src/flask/app.py",
                    "code": "class Application:\n    <your code>",
                },
            ],
        },
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["feedback"]["status"] == "complete"
    markdown = service.completed_bundle()["issue_variants"][0]["issue_markdown"]
    assert markdown.count("Path: `/workspace/repo/src/flask/app.py`") >= 2


def test_stage4_fb_receive_accepts_non_python_interface_declarations(tmp_path: Path) -> None:
    request_dir = tmp_path / "requests"
    result_dir = tmp_path / "results"
    service = bridge_module._Stage4ReceiveService(
        source_context=_stage4_source_context(),
        generation_style_id="fb",
        events_path=tmp_path / "events.jsonl",
        request_dir=request_dir,
        result_dir=result_dir,
    )
    fields = {
        "task_statement": "Add a public client interface for asynchronous feature execution.",
        "interfaces": [
            {
                "description": (
                    "Provide an asynchronous client operation that accepts a FeatureInput and "
                    "resolves to the corresponding FeatureResult."
                ),
                "path": "src/client.ts",
                "code": (
                    "export interface FeatureClient {\n"
                    "  run(input: FeatureInput): Promise<FeatureResult>;\n"
                    "  <your code>\n"
                    "}"
                ),
            }
        ],
    }
    _write_receive_request(
        request_dir,
        "call-1",
        title="Add an asynchronous feature client",
        fields=fields,
    )

    assert service.process_pending_requests() is True
    payload = json.loads((result_dir / "call-1.json").read_text(encoding="utf-8"))
    assert payload["feedback"]["status"] == "complete"
    markdown = service.completed_bundle()["issue_variants"][0]["issue_markdown"]
    assert "Path: `/workspace/repo/src/client.ts`" in markdown
    assert "export interface FeatureClient" in markdown
    assert "```python" not in markdown


def test_stage4_issuer_prompt_context_keeps_target_selector_private() -> None:
    source_context = _stage4_source_context()
    source_context["entry_file"] = {
        "test_file_path": "nltk/test/unit/lm/test_counter.py",
        "target_selector": "unit/lm/test_counter.py",
    }
    source_context["savepoint"] = {
        "depth": 5,
        "entry_pass_rate": 0.25,
        "file_results": [
            {
                "test_file_path": "nltk/test/unit/lm/test_counter.py",
                "target_selector": "unit/lm/test_counter.py",
                "is_entry_file": True,
                "status": "failed",
                "total_tests": 4,
                "passed_tests": 1,
                "failed_tests": 3,
                "error_tests": 0,
                "skipped_tests": 0,
                "pass_rate": 0.25,
            }
        ],
    }

    prompt_context = bridge_module._issuer_prompt_context(
        source_context=source_context,
        generation_style_id="swe",
    )

    assert "target_selector" not in prompt_context["broken_behavior"]["entry_result"]
    assert prompt_context["private"]["target_selector"] == "unit/lm/test_counter.py"


def test_stage4_issuer_prompt_context_hides_infrastructure_patch_noise() -> None:
    source_context = _stage4_source_context()
    source_context["private"] = {
        "gold_patch_text": (
            "diff --git a/src/flask/ctx.py b/src/flask/ctx.py\n"
            "--- a/src/flask/ctx.py\n"
            "+++ b/src/flask/ctx.py\n"
            "@@ -1 +1 @@\n"
            "-old behavior\n"
            "+broken behavior\n"
            "diff --git a/uv.lock b/uv.lock\n"
            "--- a/uv.lock\n"
            "+++ b/uv.lock\n"
            "@@ -1 +1 @@\n"
            "-https://pypi.org/simple\n"
            "+https://mirrors.example/simple\n"
        )
    }

    prompt_context = bridge_module._issuer_prompt_context(
        source_context=source_context,
        generation_style_id="swe",
    )

    reference_patch = prompt_context["private"]["reference_patch"]
    assert "src/flask/ctx.py" in reference_patch
    assert "+broken behavior" in reference_patch
    assert "uv.lock" not in reference_patch
    assert "mirrors.example" not in reference_patch


def test_stage4_issuer_prompt_context_truncates_reference_patch_middle() -> None:
    source_context = _stage4_source_context()
    source_context["private"] = {
        "gold_patch_text": "HEAD\n" + ("a" * 30_000) + ("b" * 30_000) + "\nTAIL",
    }

    prompt_context = bridge_module._issuer_prompt_context(
        source_context=source_context,
        generation_style_id="swe",
    )

    reference_patch = prompt_context["private"]["reference_patch"]
    assert len(reference_patch) == bridge_module.STAGE4_REFERENCE_PATCH_MAX_CHARS
    assert reference_patch.startswith("HEAD\n")
    assert reference_patch.endswith("\nTAIL")
    assert reference_patch.count(bridge_module.STAGE4_REFERENCE_PATCH_TRUNCATION_MARKER) == 1
    assert "a" * 100 in reference_patch
    assert "b" * 100 in reference_patch