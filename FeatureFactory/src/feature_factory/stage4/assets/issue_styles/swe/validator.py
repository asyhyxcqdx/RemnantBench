from __future__ import annotations

import re
from typing import Any


_INSTRUCTION_TITLE = re.compile(
    r"^(?:fix|restore|implement|add|remove|replace|change|refactor)\b",
    re.IGNORECASE,
)
_INTERNAL_DIAGNOSIS = (
    re.compile(r"\b(?:only recognizes|no longer calls|does not call|fails to call)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:the|this)\s+(?:internal\s+)?(?:implementation|branch|initializer|helper)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:branch|helper|initializer|implementation)\b[^.\n]*\b(?:removed|deleted|replaced)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:before|after)\b[^.\n]{0,120}\b(?:fallback|falling back|external|repair)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:local|built[- ]in|internal|external)\b[^.\n]{0,60}\b"
        r"(?:repair|fix(?:es)?|fallback|strategy|path)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:routing|classification|selection)\b[^.\n]{0,120}\b"
        r"(?:dropped|broken|stopped|missing|removed|gone)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:routing|classification|selection)\s+(?:issue|problem|failure)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bhow\b[^.\n]{0,120}\b(?:matched|classified|routed|selected)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:set|populate|assign|register|attach)\w*\b[^.\n]{0,120}\b"
        r"(?:initializ|constructor|attribute|collection)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:attribute|namespace|factory|collection)\w*\b[^.\n]{0,120}\b"
        r"(?:initializ|construct|populat)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\binternal\b[^.\n]{0,80}\b(?:routing|classification|selection)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\brepair\w*\b[^.\n]{0,80}\blocal(?:ly)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:routing|classification|selection)\s+logic\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bwithout\b[^.\n]{0,100}\b(?:external|third[- ]party|round[- ]trip)\b",
        re.IGNORECASE,
    ),
)
_INTERNAL_MODULE_REFERENCE = re.compile(
    r"(?:^|[./])_[A-Za-z_]\w*(?:[./]|$)|\binternally\b",
    re.IGNORECASE | re.MULTILINE,
)
_DECLARATION = re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_]\w*)\b")
_TEST_FILE_SYMBOL = re.compile(r"(?:^|/)test_([A-Za-z_]\w*)\.py(?:$|[#:])")


def validate(
    *,
    title: str,
    fields: dict[str, Any],
    rendered_markdown: str,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    del rendered_markdown
    errors: list[dict[str, str]] = []
    if _INSTRUCTION_TITLE.search(title):
        errors.append(
            {
                "code": "SWE_INSTRUCTION_TITLE",
                "path": "title",
                "message": "Use a symptom-focused issue title, not an instruction to implement or fix code.",
            }
        )
    content = str(fields.get("content") or "")
    diagnosis_text = f"{title}\n{content}"
    if any(pattern.search(diagnosis_text) for pattern in _INTERNAL_DIAGNOSIS):
        errors.append(
            {
                "code": "SWE_INTERNAL_DIAGNOSIS",
                "path": (
                    "title"
                    if any(pattern.search(title) for pattern in _INTERNAL_DIAGNOSIS)
                    else "fields.content"
                ),
                "message": (
                    "Report observable behavior only. Remove claims about missing internal helpers, "
                    "branches, calls, initializers, or implementation changes."
                ),
            }
        )
    combined = diagnosis_text
    private_symbols = _private_symbols(context)
    mentions_private_symbol = any(
        re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", combined)
        for symbol in private_symbols
    )
    if _INTERNAL_MODULE_REFERENCE.search(combined) or mentions_private_symbol:
        errors.append(
            {
                "code": "SWE_INTERNAL_API_REFERENCE",
                "path": (
                    "title"
                    if _INTERNAL_MODULE_REFERENCE.search(title)
                    or any(
                        re.search(rf"(?<!\w){re.escape(symbol)}(?!\w)", title)
                        for symbol in private_symbols
                    )
                    else "fields.content"
                ),
                "message": (
                    "Describe the symptom through the affected public API. Do not name a "
                    "patch-private selector, helper, internal module, or internal call path."
                ),
            }
        )
    return errors


def _private_symbols(context: dict[str, Any]) -> set[str]:
    private = context.get("private")
    if not isinstance(private, dict):
        return set()
    symbols = _symbols_from_internal_patch_files(str(private.get("gold_patch_text") or ""))
    for key, value in private.items():
        if not isinstance(value, str) or not any(part in key for part in ("target", "test_file")):
            continue
        for match in _TEST_FILE_SYMBOL.finditer(value):
            symbols.add(match.group(1))
    return {symbol for symbol in symbols if len(symbol) >= 4}


def _symbols_from_internal_patch_files(patch: str) -> set[str]:
    symbols: set[str] = set()
    internal_file = False
    for raw_line in patch.splitlines():
        if raw_line.startswith("diff --git "):
            internal_file = False
            continue
        if raw_line.startswith("+++ "):
            path = raw_line[4:].strip()
            if path.startswith("b/"):
                path = path[2:]
            segments = path.split("/")
            internal_file = any(
                segment.startswith("_") and segment not in {"__init__.py"}
                for segment in segments
            )
            continue
        if not internal_file or raw_line.startswith(("--- ", "@@")):
            continue
        line = raw_line[1:] if raw_line[:1] in {"+", "-", " "} else raw_line
        match = _DECLARATION.match(line)
        if match:
            symbols.add(match.group(1))
    return symbols