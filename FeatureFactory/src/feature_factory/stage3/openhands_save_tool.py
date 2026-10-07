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

SAVE_REQUEST_DIR_ENV = "FEATURE_FACTORY_STAGE3_SAVE_REQUEST_DIR"
SAVE_RESULT_DIR_ENV = "FEATURE_FACTORY_STAGE3_SAVE_RESULT_DIR"
DEFAULT_SAVE_POLL_INTERVAL_SECONDS = 0.2
SAVE_REQUEST_RESULT_SIDECAR_SUFFIX = ".result"


class SaveAction(Action):
    milestone_summary: str | None = Field(
        default=None,
        description="Optional one-sentence summary of the current functional breakage milestone.",
    )
    rationale: str | None = Field(
        default=None,
        description="Optional note explaining why this state is worth evaluating as a savepoint.",
    )


class SaveObservation(Observation):
    request_id: str = Field(description="Host save request id for this save tool call.")
    accepted: bool = Field(description="Whether the host accepted and archived this savepoint.")
    depth: int = Field(description="Accepted savepoint depth. Zero when not accepted.")
    pause_for_checkpoint: bool = Field(
        description="Whether the breaker conversation should pause so the host can persist a checkpoint boundary."
    )
    message: str = Field(description="Short human-readable savepoint summary.")
    entry_file_path: str = Field(description="Entry test file path for this run.")
    entry_pass_rate: float = Field(description="Current pass rate of the entry file.")
    previous_entry_pass_rate: float = Field(description="Previous accepted entry-file pass rate.")
    p2p_count: int = Field(description="Number of files still fully passing in the original P2P set.")
    f2p_count: int = Field(description="Number of files no longer fully passing in the original P2P set.")
    feedback: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured savepoint feedback from the host.",
    )
    collateral_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Summary of non-entry collateral signals.",
    )

    @property
    def to_llm_content(self) -> Sequence[TextContent | ImageContent]:
        payload = {
            "feedback": self.feedback,
            "collateral_summary": self.collateral_summary,
        }
        return [TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))]


class SaveExecutor(ToolExecutor[SaveAction, SaveObservation]):
    def __call__(
        self,
        action: SaveAction,
        conversation=None,
    ) -> SaveObservation:
        request_dir = _env_path(SAVE_REQUEST_DIR_ENV)
        result_dir = _env_path(SAVE_RESULT_DIR_ENV)
        request_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)

        request_id = str(uuid.uuid4())
        request_payload = {
            "request_id": request_id,
            "milestone_summary": str(action.milestone_summary or "").strip(),
            "rationale": str(action.rationale or "").strip(),
            "created_at": time.time(),
        }
        request_path = request_dir / f"{request_id}.json"
        result_path = result_dir / f"{request_id}.json"
        request_result_sidecar_path = request_dir / f"{request_id}{SAVE_REQUEST_RESULT_SIDECAR_SUFFIX}"
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
                    return _failed_observation(message="save tool received an invalid host result payload")
                pause_for_checkpoint = bool(payload.get("pause_for_checkpoint", False))
                if pause_for_checkpoint:
                    if conversation is None:
                        raise RuntimeError(
                            "save tool requires a conversation handle so the breaker can be paused "
                            "at the save boundary"
                        )
                    conversation.pause()
                return SaveObservation(
                    request_id=str(payload.get("request_id") or request_id),
                    accepted=bool(payload.get("accepted")),
                    depth=int(payload.get("depth") or 0),
                    pause_for_checkpoint=pause_for_checkpoint,
                    message=str(payload.get("message") or ""),
                    entry_file_path=str(payload.get("entry_file_path") or ""),
                    entry_pass_rate=float(payload.get("entry_pass_rate") or 0.0),
                    previous_entry_pass_rate=float(payload.get("previous_entry_pass_rate") or 0.0),
                    p2p_count=int(payload.get("p2p_count") or 0),
                    f2p_count=int(payload.get("f2p_count") or 0),
                    feedback=dict(payload.get("feedback") or {}),
                    collateral_summary=dict(payload.get("collateral_summary") or {}),
                )
            time.sleep(DEFAULT_SAVE_POLL_INTERVAL_SECONDS)


class SaveTool(ToolDefinition[SaveAction, SaveObservation]):
    @classmethod
    def create(cls, conv_state=None, **params) -> Sequence["SaveTool"]:  # noqa: ARG003
        return [
            cls(
                description=(
                    "Evaluate the current /workspace/repo breakage state against the frozen original P2P set. "
                    "Call this only after the frozen run_script self-check shows useful functional breakage "
                    "for the target entry file. The observation shown to you contains only feedback and "
                    "collateral_summary. If feedback.accepted is true, the host archived the diff as a "
                    "savepoint; otherwise use feedback.message and feedback.code to continue revising."
                ),
                action_type=SaveAction,
                observation_type=SaveObservation,
                executor=SaveExecutor(),
            )
        ]


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


def _failed_observation(*, message: str) -> SaveObservation:
    return SaveObservation(
        request_id="",
        accepted=False,
        depth=0,
        pause_for_checkpoint=False,
        message=message,
        entry_file_path="",
        entry_pass_rate=0.0,
        previous_entry_pass_rate=0.0,
        p2p_count=0,
        f2p_count=0,
        feedback={
            "accepted": False,
            "code": "SAVE_TOOL_RESULT_ERROR",
            "message": message,
        },
        collateral_summary={},
    )


register_tool(SaveTool.name, SaveTool)
