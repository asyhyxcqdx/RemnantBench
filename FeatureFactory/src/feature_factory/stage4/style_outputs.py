from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, UndefinedError
from jsonschema import ValidationError
from jsonschema.validators import validator_for

from feature_factory.stage4.issue_styles import IssueStyleConfigError, RuntimeIssueStyleSpec


STAGE4_TASK_WORKSPACE_ROOT = "/workspace/repo"


@dataclass(slots=True)
class StyleOutputResult:
    rendered_markdown: str = ""
    errors: list[dict[str, str]] = field(default_factory=list)
    missing: dict[str, int] = field(default_factory=dict)
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return not self.errors and bool(self.rendered_markdown.strip())


def validate_and_render_style_output(
    *,
    spec: RuntimeIssueStyleSpec,
    title: str,
    fields: dict[str, Any],
    repository_name: str,
    validation_context: dict[str, Any] | None = None,
) -> StyleOutputResult:
    result = StyleOutputResult(
        validation={
            "validators": list(spec.output.validators),
            "fields_schema_asset": spec.output.fields_schema.asset,
            "fields_schema_sha256": spec.output.fields_schema.sha256,
            "template_asset": spec.output.template.asset,
            "template_sha256": spec.output.template.sha256,
        }
    )
    _validate_schema(spec=spec, fields=fields, result=result)
    if result.errors:
        return result
    _render(
        spec=spec,
        title=title,
        fields=fields,
        repository_name=repository_name,
        result=result,
    )
    if result.rendered_markdown and spec.output.custom_validator is not None:
        _run_custom_validator(
            spec=spec,
            title=title,
            fields=fields,
            rendered_markdown=result.rendered_markdown,
            validation_context=validation_context,
            result=result,
        )
    if result.rendered_markdown:
        result.validation["rendered_sha256"] = hashlib.sha256(
            result.rendered_markdown.encode("utf-8")
        ).hexdigest()
    return result


def _run_custom_validator(
    *,
    spec: RuntimeIssueStyleSpec,
    title: str,
    fields: dict[str, Any],
    rendered_markdown: str,
    validation_context: dict[str, Any] | None,
    result: StyleOutputResult,
) -> None:
    asset = spec.output.custom_validator
    if asset is None:
        return
    namespace: dict[str, Any] = {
        "__name__": f"feature_factory_stage4_style_validator_{spec.id}",
    }
    try:
        source = compile(asset.text, asset.asset, "exec")
        exec(source, namespace)  # noqa: S102 - validator is a trusted, frozen style asset.
        validate = namespace.get("validate")
        if not callable(validate):
            raise TypeError("validator asset does not define callable validate")
        raw_errors = validate(
            title=title,
            fields=json.loads(json.dumps(fields)),
            rendered_markdown=rendered_markdown,
            context=json.loads(json.dumps(validation_context or {})),
        )
    except Exception as exc:  # noqa: BLE001
        raise IssueStyleConfigError(
            f"custom validator for style {spec.id} failed: {exc}"
        ) from exc
    if raw_errors is None:
        raw_errors = []
    if not isinstance(raw_errors, list):
        raise IssueStyleConfigError(
            f"custom validator for style {spec.id} must return a list of error objects"
        )
    for index, raw_error in enumerate(raw_errors, start=1):
        if not isinstance(raw_error, dict):
            raise IssueStyleConfigError(
                f"custom validator for style {spec.id} error {index} must be an object"
            )
        code = str(raw_error.get("code") or "STYLE_VALIDATION_ERROR").strip()
        path = str(raw_error.get("path") or "fields").strip()
        message = str(raw_error.get("message") or "Style-specific validation failed.").strip()
        _append_error(
            result,
            code=code,
            path=path,
            message=message,
            missing=bool(raw_error.get("missing")),
        )
    result.validation["custom_validator"] = asset.snapshot()


def style_fields_schema(spec: RuntimeIssueStyleSpec) -> dict[str, Any]:
    try:
        schema = json.loads(spec.output.fields_schema.text)
    except json.JSONDecodeError as exc:
        raise IssueStyleConfigError(
            f"invalid runtime fields schema for style {spec.id}: {exc}"
        ) from exc
    if not isinstance(schema, dict):
        raise IssueStyleConfigError(f"runtime fields schema for style {spec.id} must be an object")
    return schema


def _validate_schema(
    *,
    spec: RuntimeIssueStyleSpec,
    fields: dict[str, Any],
    result: StyleOutputResult,
) -> None:
    schema = style_fields_schema(spec)
    validator_cls = validator_for(schema)
    try:
        validator_cls.check_schema(schema)
    except Exception as exc:  # noqa: BLE001
        raise IssueStyleConfigError(f"invalid JSON Schema for style {spec.id}: {exc}") from exc
    validator = validator_cls(schema)
    errors = sorted(
        validator.iter_errors(fields),
        key=lambda item: (tuple(str(part) for part in item.absolute_path), item.message),
    )
    for error in errors:
        _append_schema_error(error=error, result=result)


def _append_schema_error(*, error: ValidationError, result: StyleOutputResult) -> None:
    base_path = _field_path(list(error.absolute_path))
    if error.validator == "required" and isinstance(error.instance, dict):
        missing_keys = [
            str(key)
            for key in list(error.validator_value or [])
            if key not in error.instance
        ]
        for key in missing_keys:
            path = f"{base_path}.{key}" if base_path else f"fields.{key}"
            _append_error(
                result,
                code="MISSING_FIELD",
                path=path,
                message=f"Required style field is missing: {path}.",
                missing=True,
            )
        return
    annotations = error.schema if isinstance(error.schema, dict) else {}
    code = str(annotations.get("x-error-code") or annotations.get("error_code") or "").strip() or {
        "additionalProperties": "UNEXPECTED_FIELD",
        "minItems": "TOO_FEW_ITEMS",
        "minLength": "EMPTY_FIELD",
        "pattern": "INVALID_FIELD",
        "type": "INVALID_FIELD_TYPE",
    }.get(str(error.validator), "INVALID_FIELD")
    message_template = str(
        annotations.get("x-error-message") or annotations.get("error_message") or ""
    ).strip()
    message = (
        message_template.replace("{path}", base_path or "fields").replace(
            "{message}", error.message
        )
        if message_template
        else f"{base_path or 'fields'}: {error.message}"
    )
    _append_error(
        result,
        code=code,
        path=base_path or "fields",
        message=message,
        missing=bool(annotations.get("x-missing"))
        or error.validator in {"minItems", "minLength"},
    )


def _render(
    *,
    spec: RuntimeIssueStyleSpec,
    title: str,
    fields: dict[str, Any],
    repository_name: str,
    result: StyleOutputResult,
) -> None:
    environment = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    try:
        template = environment.from_string(spec.output.template.text)
    except TemplateSyntaxError as exc:
        raise IssueStyleConfigError(
            f"invalid output template for style {spec.id}: {exc}"
        ) from exc
    try:
        rendered = template.render(
            title=title,
            fields=fields,
            repository={"name": repository_name},
            workspace_root=STAGE4_TASK_WORKSPACE_ROOT,
        ).strip()
    except UndefinedError as exc:
        _append_error(
            result,
            code="MISSING_TEMPLATE_FIELD",
            path="fields",
            message=f"The style template could not be filled from the submitted fields: {exc}",
            missing=True,
        )
        return
    except Exception as exc:  # noqa: BLE001
        raise IssueStyleConfigError(f"failed to render output template for style {spec.id}: {exc}") from exc
    if not rendered:
        _append_error(
            result,
            code="EMPTY_RENDERED_OUTPUT",
            path="fields",
            message="The submitted fields rendered an empty task input.",
            missing=True,
        )
        return
    result.rendered_markdown = rendered


def _field_path(parts: list[Any]) -> str:
    path = "fields"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _append_error(
    result: StyleOutputResult,
    *,
    code: str,
    path: str,
    message: str,
    missing: bool = False,
) -> None:
    error = {"code": code, "path": path, "message": message}
    if error not in result.errors:
        result.errors.append(error)
    if missing:
        result.missing[path] = 1