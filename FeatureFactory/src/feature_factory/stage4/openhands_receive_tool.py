from __future__ import annotations

import hashlib
import json
import os
import stat
import time
import uuid
from copy import deepcopy
from collections.abc import Sequence
from pathlib import Path
from threading import Lock
from typing import Any, ClassVar

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import ToolExecutor, register_tool

RECEIVE_REQUEST_DIR_ENV = "FEATURE_FACTORY_STAGE4_RECEIVE_REQUEST_DIR"
RECEIVE_RESULT_DIR_ENV = "FEATURE_FACTORY_STAGE4_RECEIVE_RESULT_DIR"
DEFAULT_RECEIVE_POLL_INTERVAL_SECONDS = 0.2
RECEIVE_REQUEST_RESULT_SIDECAR_SUFFIX = ".result"
_RECEIVE_ACTION_TYPE_CACHE: dict[str, type["ReceiveAction"]] = {}
_RECEIVE_ACTION_TYPE_CACHE_LOCK = Lock()


class ReceiveAction(Action):
    _tool_fields_schema: ClassVar[dict[str, Any] | None] = None

    title: str = Field(description="Title for the task input.")
    fields: dict[str, Any] = Field(
        description=(
            "Style-specific semantic fields. The host validates these fields against the configured "
            "schema and renders the final Markdown. Private diagnostics, hidden tests, and patch text "
            "are forbidden."
        ),
    )

    @classmethod
    def to_mcp_schema(cls) -> dict[str, Any]:
        schema = super().to_mcp_schema()
        configured = cls._tool_fields_schema
        if configured is None:
            return schema
        properties = schema.setdefault("properties", {})
        original_fields = properties.get("fields")
        fields_schema = deepcopy(configured)
        if isinstance(original_fields, dict) and original_fields.get("description"):
            fields_schema.setdefault("description", original_fields["description"])
        properties["fields"] = fields_schema
        return schema


class ReceiveObservation(Observation):
    request_id: str = Field(description="Host receive request id for this tool call.")
    complete: bool = Field(description="Whether the host accepted the task input.")
    final_failure: bool = Field(
        description=(
            "Whether the host has ended the run because receive-tool submission can no longer continue."
        )
    )
    message: str = Field(description="Short human-readable status summary.")
    feedback: dict[str, Any] = Field(
        default_factory=dict,
        description="Minimal structured feedback for the task author.",
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        return [TextContent(text=json.dumps(self.feedback, ensure_ascii=False, indent=2))]


class ReceiveExecutor(ToolExecutor[ReceiveAction, ReceiveObservation]):
    def __call__(
        self,
        action: ReceiveAction,
        conversation=None,
    ) -> ReceiveObservation:
        request_dir = _env_path(RECEIVE_REQUEST_DIR_ENV)
        result_dir = _env_path(RECEIVE_RESULT_DIR_ENV)
        request_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        request_id = str(uuid.uuid4())
        request_payload = {
            "request_id": request_id,
            "title": action.title,
            "fields": action.fields,
            "created_at": time.time(),
        }
        request_path = request_dir / f"{request_id}.json"
        result_path = result_dir / f"{request_id}.json"
        request_result_sidecar_path = request_dir / f"{request_id}{RECEIVE_REQUEST_RESULT_SIDECAR_SUFFIX}"
        temp_request_path = request_dir / f".{request_id}.tmp"
        temp_request_path.write_text(
            json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_request_path.replace(request_path)
        _make_cross_user_readable_file(request_path)

        while True:
            result_candidate = (
                result_path
                if result_path.exists()
                else request_result_sidecar_path if request_result_sidecar_path.exists() else None
            )
            if result_candidate is not None:
                try:
                    payload = json.loads(result_candidate.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    return _failed_observation(message="receive tool received an invalid host result payload")
                if bool(payload.get("pause_for_terminal")):
                    if conversation is None:
                        raise RuntimeError(
                            "receive tool requires a conversation handle so the task author can be paused "
                            "at the terminal receive boundary"
                        )
                    conversation.pause()
                return ReceiveObservation(
                    request_id=str(payload.get("request_id") or request_id),
                    complete=bool(payload.get("complete")),
                    final_failure=bool(payload.get("final_failure")),
                    message=str(payload.get("message") or ""),
                    feedback=dict(payload.get("feedback") or {}),
                )
            time.sleep(DEFAULT_RECEIVE_POLL_INTERVAL_SECONDS)


class ReceiveTool(ToolDefinition[ReceiveAction, ReceiveObservation]):
    @classmethod
    def create(
        cls,
        conv_state=None,
        *,
        fields_schema: dict[str, Any] | None = None,
        **params,
    ) -> Sequence["ReceiveTool"]:  # noqa: ARG003
        action_type = (
            _configured_receive_action_type(fields_schema)
            if fields_schema is not None
            else ReceiveAction
        )
        return [
            cls(
                description=(
                    "Submit one task-input title and its style-specific semantic fields. "
                    "The host checks the configured field schema, renders the style template, "
                    "and checks private-information leakage. "
                    'If feedback.status is "incomplete", correct the task input and submit it again. '
                    'If feedback.status is "complete", stop. '
                    'If feedback.status is "fatal", the run cannot continue.'
                ),
                action_type=action_type,
                observation_type=ReceiveObservation,
                executor=ReceiveExecutor(),
            )
        ]


def tool_fields_schema(fields_schema: dict[str, Any]) -> dict[str, Any]:
    """Project a style schema to the provider-safe structure shown by the tool."""
    if not isinstance(fields_schema, dict) or fields_schema.get("type") != "object":
        raise ValueError("receive fields_schema must be an object JSON Schema")
    return _project_schema_node(fields_schema)


def _project_schema_node(schema: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {}
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        projected["type"] = schema_type
    description = schema.get("description")
    if isinstance(description, str) and description.strip():
        projected["description"] = description.strip()
    enum = schema.get("enum")
    if isinstance(enum, list):
        projected["enum"] = list(enum)
    if schema_type == "object":
        raw_properties = schema.get("properties")
        if isinstance(raw_properties, dict):
            projected["properties"] = {
                str(name): _project_schema_node(value)
                for name, value in raw_properties.items()
                if isinstance(value, dict)
            }
            raw_required = schema.get("required")
            if isinstance(raw_required, list):
                known = set(projected["properties"])
                required = [str(name) for name in raw_required if str(name) in known]
                if required:
                    projected["required"] = required
    elif schema_type == "array" and isinstance(schema.get("items"), dict):
        projected["items"] = _project_schema_node(schema["items"])
    return projected


def _configured_receive_action_type(
    fields_schema: dict[str, Any],
) -> type[ReceiveAction]:
    projected = tool_fields_schema(fields_schema)
    fingerprint = hashlib.sha256(
        json.dumps(projected, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    with _RECEIVE_ACTION_TYPE_CACHE_LOCK:
        cached = _RECEIVE_ACTION_TYPE_CACHE.get(fingerprint)
        if cached is not None:
            return cached
        action_type = type(
            f"Receive{fingerprint[:12]}Action",
            (ReceiveAction,),
            {
                "__module__": __name__,
                "_tool_fields_schema": projected,
            },
        )
        _RECEIVE_ACTION_TYPE_CACHE[fingerprint] = action_type
        return action_type


def _env_path(name: str) -> Path:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is not configured")
    return Path(value)


def _make_cross_user_readable_file(path: Path) -> None:
    try:
        current_mode = stat.S_IMODE(path.stat().st_mode)
        desired_mode = current_mode | stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
        if desired_mode != current_mode:
            path.chmod(desired_mode)
    except OSError:
        pass


def _failed_observation(*, message: str) -> ReceiveObservation:
    return ReceiveObservation(
        request_id="",
        complete=False,
        final_failure=True,
        message=message,
        feedback={
            "status": "fatal",
            "accepted": False,
            "errors": [],
            "missing": {"fields": 1},
        },
    )


register_tool(ReceiveTool.name, ReceiveTool)