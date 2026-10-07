from __future__ import annotations

from typing import Any


def validation_failure_message(
    *,
    final_validation: dict[str, Any],
    description: str,
    unvalidated_description: str | None = None,
) -> str:
    full_report = dict(final_validation.get("full_report") or {})
    smoke_report = dict(final_validation.get("smoke_report") or {})
    direct_feedback = dict(final_validation.get("feedback") or {})
    full_summary = dict(full_report.get("summary") or {})
    full_feedback = dict(full_report.get("feedback") or {})
    smoke_feedback = dict(smoke_report.get("feedback") or {})
    if full_feedback.get("message"):
        return str(full_feedback["message"])
    if smoke_feedback.get("message"):
        return str(smoke_feedback["message"])
    if direct_feedback.get("message"):
        return str(direct_feedback["message"])
    direct_message = str(final_validation.get("message") or "").strip()
    if direct_message and str(final_validation.get("status") or "") != "unvalidated":
        return direct_message
    if full_summary:
        return (
            "full validation failed: "
            f"{int(full_summary.get('passed_files') or 0)}/"
            f"{int(full_summary.get('total_files') or 0)} files passed"
        )
    if str(final_validation.get("status") or "") == "unvalidated":
        return str(unvalidated_description or "worker finished without a successful validate tool call")
    if str(final_validation.get("status") or "") == "smoke_passed":
        return "smoke validation passed, but host full validation did not complete"
    return str(description or "worker validation failed")
