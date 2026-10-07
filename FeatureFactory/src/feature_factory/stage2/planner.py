from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


class UnsupportedStage2RepositoryError(RuntimeError):
    pass


PlannerStatus = Literal["ready", "abandoned", "defect"]


@dataclass(frozen=True, slots=True)
class PlannerDecision:
    status: PlannerStatus
    base_image: str | None
    base_image_ref: str | None
    planner_model: str
    planner_token_usage: int
    worker_model: str
    worker_token_usage: int
    guidance: str | None = None
    reason: str | None = None
    upd_dockerfile: str | None = None
    base_image_catalog: list[dict[str, Any]] = field(default_factory=list)
