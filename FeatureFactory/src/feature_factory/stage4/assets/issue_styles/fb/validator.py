from __future__ import annotations

import ast
import re
import textwrap
from typing import Any


_IMPLEMENTATION_PROSE_PATTERNS = (
    re.compile(r"\b(?:recursive|recursively|fallback|falling back)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:before|after)\b[^.\n]{0,120}\b"
        r"(?:external|downstream|fallback|repair|parse|parsing)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:local|internal|external|downstream|text[- ]level)\b[^.\n]{0,60}\b"
        r"(?:repair|fix(?:es)?|fallback|strategy|path|pipeline)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:by|while)\s+(?:creating|attaching|setting|sorting|filtering|iterating|"
        r"converting|decoding|encoding|rejecting|routing|calling)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmust\s+handle\b", re.IGNORECASE),
    re.compile(r"\bsuch\s+as\b[^.\n]*(?:,|\band\b)", re.IGNORECASE),
    re.compile(
        r"\b(?:including|for example|e\.g\.)\b[^.\n]{0,160}(?:,|\band\b)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:encoded|decoded)\s+(?:as|in|with|using)\b", re.IGNORECASE),
    re.compile(r"\b(?:range|bounds?)\s*(?:of\s*)?[\[(]", re.IGNORECASE),
    re.compile(r"\b\d+\s*\*\*\s*\d+\b"),
    re.compile(r"(?:,\s*[^,\n]{1,80}){2,}"),
    re.compile(
        r"\b(?:creat|attach|set|populat|assign|register|bind|sort)\w*\b"
        r"[^.\n]*\b(?:factor(?:y|ies)|attribute|helper|branch|handler|collection)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:factor(?:y|ies)|attribute|helper|branch|handler|collection)\w*\b"
        r"[^.\n]*\b(?:initializ|construct|populat|attach|register|assign)\w*\b",
        re.IGNORECASE,
    ),
    re.compile(r"\binternal\b[^.\n]{0,80}\bhelper\w*\b", re.IGNORECASE),
)
_IMPLEMENTATION_DOCSTRING_PATTERNS = (
    re.compile(r"\b(?:repair|recovery)\s+(?:strategy|pipeline|step|path)s?\b", re.IGNORECASE),
    re.compile(
        r"\b(?:third[- ]party|LLM[- ]assisted|advanced|local)\b[^.\n]{0,80}\brepair\w*\b",
        re.IGNORECASE,
    ),
    re.compile(r"修复策略|第三方(?:库)?|LLM\s*辅助修复|本地修复|高级修复|清理\s*markdown|转义未转义"),
)


def validate(
    *,
    title: str,
    fields: dict[str, Any],
    rendered_markdown: str,
    context: dict[str, Any],
) -> list[dict[str, str]]:
    del rendered_markdown, context
    errors: list[dict[str, str]] = []
    if _contains_implementation_prose(title):
        errors.append(
            {
                "code": "FB_IMPLEMENTATION_DETAIL",
                "path": "title",
                "message": (
                    "Keep the title at the feature or interface-goal level. Remove repair-path, "
                    "algorithm, fallback, and private edge-case details."
                ),
            }
        )
    task_statement = str(fields.get("task_statement") or "")
    if _contains_implementation_prose(task_statement):
        errors.append(
            {
                "code": "FB_IMPLEMENTATION_DETAIL",
                "path": "fields.task_statement",
                "message": (
                    "Keep the task statement at the feature-goal level. Remove algorithms, "
                    "repair steps, fallback order, and enumerated private edge cases."
                ),
            }
        )

    for index, interface in enumerate(list(fields.get("interfaces") or [])):
        if not isinstance(interface, dict):
            continue
        description = str(interface.get("description") or "")
        if _contains_implementation_prose(description):
            errors.append(
                {
                    "code": "FB_IMPLEMENTATION_DETAIL",
                    "path": f"fields.interfaces[{index}].description",
                    "message": (
                        "Describe only the interface's identity and public input/output role. "
                        "Remove algorithms, internal steps, branch behavior, and repair strategy."
                    ),
                }
            )
        path = str(interface.get("path") or "")
        code = str(interface.get("code") or "")
        if path.endswith(".py") and _python_skeleton_contains_implementation(code):
            errors.append(
                {
                    "code": "FB_EXECUTABLE_INTERFACE_BODY",
                    "path": f"fields.interfaces[{index}].code",
                    "message": (
                        "The Python interface skeleton contains executable implementation or "
                        "surrounding control flow. Keep declarations, decorators, type information, "
                        "existing concise API documentation, and standalone <your code> placeholders only."
                    ),
                }
            )
        elif path.endswith(".py") and _python_skeleton_contains_implementation_docstring(code):
            errors.append(
                {
                    "code": "FB_IMPLEMENTATION_DETAIL",
                    "path": f"fields.interfaces[{index}].code",
                    "message": (
                        "Keep only concise public API documentation in the declaration skeleton. "
                        "Remove docstrings that enumerate repair strategies, internal workflows, "
                        "or implementation steps."
                    ),
                }
            )
        elif not path.endswith(".py") and _generic_skeleton_contains_implementation(code):
            errors.append(
                {
                    "code": "FB_EXECUTABLE_INTERFACE_BODY",
                    "path": f"fields.interfaces[{index}].code",
                    "message": (
                        "The interface skeleton contains executable statements. Keep only the "
                        "declaration shape and standalone <your code> placeholders."
                    ),
                }
            )
    return errors


def _contains_implementation_prose(value: str) -> bool:
    return any(pattern.search(value) for pattern in _IMPLEMENTATION_PROSE_PATTERNS)


def _python_skeleton_contains_implementation(code: str) -> bool:
    replaced_lines = []
    for line in textwrap.dedent(code).splitlines():
        if line.strip() == "<your code>":
            replaced_lines.append(f"{line[: len(line) - len(line.lstrip())]}pass")
        else:
            replaced_lines.append(line)
    try:
        tree = ast.parse("\n".join(replaced_lines))
    except SyntaxError:
        return True
    return any(not _allowed_declaration(node) for node in tree.body)


def _allowed_declaration(node: ast.stmt) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return _body_is_placeholder_only(node.body)
    if isinstance(node, ast.ClassDef):
        return all(
            _is_docstring(statement)
            or isinstance(statement, ast.Pass)
            or (
                isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef))
                and _body_is_placeholder_only(statement.body)
            )
            for statement in node.body
        )
    return False


def _body_is_placeholder_only(body: list[ast.stmt]) -> bool:
    return bool(body) and all(_is_docstring(node) or isinstance(node, ast.Pass) for node in body)


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(
        node.value.value, str
    )


def _generic_skeleton_contains_implementation(code: str) -> bool:
    for raw_line in code.splitlines():
        line = raw_line.strip()
        if not line or line == "<your code>" or line.startswith(("//", "/*", "*")):
            continue
        if re.match(r"^(?:return|if|for|while|switch|throw)\b", line):
            return True
    return False


def _python_skeleton_contains_implementation_docstring(code: str) -> bool:
    replaced_lines = []
    for line in textwrap.dedent(code).splitlines():
        if line.strip() == "<your code>":
            replaced_lines.append(f"{line[: len(line) - len(line.lstrip())]}pass")
        else:
            replaced_lines.append(line)
    try:
        tree = ast.parse("\n".join(replaced_lines))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        docstring = ast.get_docstring(node, clean=False) or ""
        if any(pattern.search(docstring) for pattern in _IMPLEMENTATION_DOCSTRING_PATTERNS):
            return True
    return False