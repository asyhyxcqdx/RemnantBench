from __future__ import annotations

import re
from typing import Any


_DIRECT_REPAIR = re.compile(
    r"\b(?:add|remove|replace|reverse|change|restore)\b[^.\n]*\b"
    r"(?:call|branch|condition|comparison|constant|operator|statement|line)\b",
    re.IGNORECASE,
)


def validate(
    *,
    title: str,
    fields: dict[str, Any],
    rendered_markdown: str,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    del title, rendered_markdown, context
    content = str(fields.get("content") or "")
    if not _DIRECT_REPAIR.search(content):
        return []
    return [
        {
            "code": "HINT_DIRECT_REPAIR",
            "path": "fields.content",
            "message": (
                "Keep the guidance diagnostic. Remove the concrete code operation and describe "
                "the observable invariant or investigation boundary instead."
            ),
        }
    ]