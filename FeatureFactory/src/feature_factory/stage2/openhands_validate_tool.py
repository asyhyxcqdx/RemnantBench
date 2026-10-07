from __future__ import annotations

import json
import os
import stat
import time
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import Field

from openhands.sdk import Action, ImageContent, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import ToolExecutor, register_tool

VALIDATE_REQUEST_DIR_ENV = "FEATURE_FACTORY_STAGE2_VALIDATE_REQUEST_DIR"
VALIDATE_RESULT_DIR_ENV = "FEATURE_FACTORY_STAGE2_VALIDATE_RESULT_DIR"
DEFAULT_VALIDATE_POLL_INTERVAL_SECONDS = 0.2
VALIDATE_REQUEST_RESULT_SIDECAR_SUFFIX = ".result"


class ValidateAction(Action):
    dockerfile_path: str = Field(
        description="Absolute container path to the Dockerfile that should be validated."
    )
    run_script_path: str = Field(
        description="Absolute container path to the run_script.sh file that should be validated."
    )


class ValidateObservation(Observation):
    result: str = Field(description='Validation result: "smoke_passed", "retry", or "failed".')
    phase: str = Field(description="Validation phase that produced the current result.")
    attempt_index: int = Field(description="1-based validation attempt index.")
    message: str = Field(description="Short human-readable validation summary.")
    feedback: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured feedback describing what should be fixed next.",
    )
    collect_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact collect summary for the current validation call.",
    )
    smoke_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact smoke summary for the current validation call.",
    )
    full_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Compact full-validation summary for the current validation call.",
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        payload = {
            "result": self.result,
            "phase": self.phase,
            "attempt_index": self.attempt_index,
            "message": self.message,
            "feedback": self.feedback,
            "collect_summary": self.collect_summary,
            "smoke_summary": self.smoke_summary,
            "full_summary": self.full_summary,
        }
        return [TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))]


class ValidateExecutor(ToolExecutor[ValidateAction, ValidateObservation]):
    def __call__(
        self,
        action: ValidateAction,
        conversation=None,
    ) -> ValidateObservation:
        request_dir = _env_path(VALIDATE_REQUEST_DIR_ENV)
        result_dir = _env_path(VALIDATE_RESULT_DIR_ENV)
        request_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        request_id = str(uuid.uuid4())
        request_payload = {
            "request_id": request_id,
            "dockerfile_path": action.dockerfile_path,
            "run_script_path": action.run_script_path,
            "created_at": time.time(),
        }
        request_path = request_dir / f"{request_id}.json"
        result_path = result_dir / f"{request_id}.json"
        request_result_sidecar_path = request_dir / f"{request_id}{VALIDATE_REQUEST_RESULT_SIDECAR_SUFFIX}"
        temp_request_path = request_dir / f".{request_id}.tmp"
        _make_cross_user_readable_file(Path(action.dockerfile_path))
        _make_cross_user_readable_file(Path(action.run_script_path))
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
                    return _failed_observation(
                        attempt_index=0,
                        phase="tool",
                        message="validate tool received an invalid host result payload",
                    )
                pause_for_checkpoint = bool(payload.get("pause_for_checkpoint", True))
                if pause_for_checkpoint:
                    if conversation is None:
                        raise RuntimeError(
                            "validate tool requires a conversation handle so the worker can be paused "
                            "at the validate boundary"
                        )
                    conversation.pause()
                return ValidateObservation(
                    result=str(payload.get("result") or "failed"),
                    phase=str(payload.get("phase") or "tool"),
                    attempt_index=int(payload.get("attempt_index") or 0),
                    message=str(payload.get("message") or ""),
                    feedback=dict(payload.get("feedback") or {}),
                    collect_summary=dict(payload.get("collect_summary") or {}),
                    smoke_summary=dict(payload.get("smoke_summary") or {}),
                    full_summary=dict(payload.get("full_summary") or {}),
                )
            time.sleep(DEFAULT_VALIDATE_POLL_INTERVAL_SECONDS)


class ValidateTool(ToolDefinition[ValidateAction, ValidateObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["ValidateTool"]:  # noqa: ARG003
        checkpoint_enabled = _env_bool("FEATURE_FACTORY_STAGE2_ENABLE_CHECKPOINTS", default=True)
        completion_sentence = (
            'If the tool returns `result = "smoke_passed"` after smoke passes, the host '
            "will checkpoint the worker, end the agent run, and continue full validation "
            "outside the agent time budget."
            if checkpoint_enabled
            else "When checkpointing is disabled, the tool runs full validation directly "
            "after smoke passes and returns the final validation result without creating "
            "a worker resume checkpoint."
        )
        return [
            cls(
                description=(
                    "Validate the current Dockerfile and run_script.sh environment artifacts. "
                    "Call this after you believe the environment is ready. The tool runs schema "
                    f"checks, build, collect, and smoke validation. {completion_sentence}"
                ),
                action_type=ValidateAction,
                observation_type=ValidateObservation,
                executor=ValidateExecutor(),
            )
        ]


def _env_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off"}


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


def _failed_observation(*, attempt_index: int, phase: str, message: str) -> ValidateObservation:
    return ValidateObservation(
        result="failed",
        phase=phase,
        attempt_index=attempt_index,
        message=message,
        feedback={},
        collect_summary={},
        smoke_summary={},
        full_summary={},
    )


register_tool(ValidateTool.name, ValidateTool)
