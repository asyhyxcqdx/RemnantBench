from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from jinja2 import Environment, TemplateSyntaxError
from jsonschema.validators import validator_for


ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 7
LEGACY_ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 3
OUTPUT_SPEC_ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 4
LOCAL_EXAMPLE_ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 5
COMMON_PROMPT_ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 6
CUSTOM_VALIDATOR_ISSUE_STYLE_CONFIG_SCHEMA_VERSION = 7
ISSUE_STYLE_CONFIG_FILENAME = "issue_styles/config.json"
ISSUE_STYLE_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
UNIVERSAL_ISSUE_STYLE_VALIDATORS = ("schema", "render", "leakage")


class IssueStyleConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PromptAsset:
    asset: str
    text: str
    sha256: str

    def snapshot(self) -> dict[str, str]:
        return {
            "asset": self.asset,
            "text": self.text,
            "sha256": self.sha256,
        }


@dataclass(frozen=True, slots=True)
class IssueStyleOutputSpec:
    fields_schema: PromptAsset
    template: PromptAsset
    validators: tuple[str, ...]
    custom_validator: PromptAsset | None = None

    def snapshot(self) -> dict[str, Any]:
        snapshot = {
            "fields_schema": self.fields_schema.snapshot(),
            "template": self.template.snapshot(),
            "validators": list(self.validators),
        }
        if self.custom_validator is not None:
            snapshot["custom_validator"] = self.custom_validator.snapshot()
        return snapshot


@dataclass(frozen=True, slots=True)
class IssueStyleSpec:
    id: str
    display_name: str
    prompt_variants: tuple[PromptAsset, ...]
    output: IssueStyleOutputSpec
    local_example: PromptAsset
    one_shots: tuple[PromptAsset, ...] = ()

    def selected_prompt(self, *, seed: str) -> PromptAsset:
        digest = hashlib.sha256(f"{seed}\0{self.id}".encode("utf-8")).digest()
        index = int.from_bytes(digest[:8], byteorder="big") % len(self.prompt_variants)
        return self.prompt_variants[index]

    def snapshot(self, *, seed: str) -> dict[str, Any]:
        selected = self.selected_prompt(seed=seed)
        return {
            "id": self.id,
            "display_name": self.display_name,
            "selected_prompt": selected.snapshot(),
            "selection": {
                "algorithm": "sha256_modulo",
                "seed": seed,
                "variant_count": len(self.prompt_variants),
                "selected_index": self.prompt_variants.index(selected),
            },
            "output": self.output.snapshot(),
            "local_example": self.local_example.snapshot(),
            "one_shots": [asset.snapshot() for asset in self.one_shots],
        }


@dataclass(frozen=True, slots=True)
class RuntimeIssueStyleSpec:
    id: str
    display_name: str
    prompt: PromptAsset
    output: IssueStyleOutputSpec
    one_shots: tuple[PromptAsset, ...]
    local_example: PromptAsset | None = None


@dataclass(frozen=True, slots=True)
class IssueStyleCatalog:
    schema_version: int
    enabled_styles: tuple[str, ...]
    styles: dict[str, IssueStyleSpec]
    issuer_prompt: PromptAsset
    sha256: str

    def enabled_specs(self) -> list[IssueStyleSpec]:
        return [self.styles[style_id] for style_id in self.enabled_styles]

    def enabled_contract_sha256(self) -> str:
        """Fingerprint only the assets that can affect the enabled styles."""
        return _sha256_json(
            {
                "schema_version": self.schema_version,
                "issuer_prompt": self.issuer_prompt.snapshot(),
                "enabled_styles": list(self.enabled_styles),
                "styles": [
                    {
                        "id": spec.id,
                        "display_name": spec.display_name,
                        "prompt_variants": [
                            asset.snapshot() for asset in spec.prompt_variants
                        ],
                        "output": spec.output.snapshot(),
                        "local_example": spec.local_example.snapshot(),
                        "one_shots": [asset.snapshot() for asset in spec.one_shots],
                    }
                    for spec in self.enabled_specs()
                ],
            }
        )

    def snapshot(self, *, seed: str) -> dict[str, Any]:
        styles = [spec.snapshot(seed=seed) for spec in self.enabled_specs()]
        snapshot = {
            "schema_version": self.schema_version,
            "catalog_sha256": self.enabled_contract_sha256(),
            "issuer_prompt": self.issuer_prompt.snapshot(),
            "enabled_styles": list(self.enabled_styles),
            "styles": styles,
        }
        snapshot["sha256"] = _sha256_json(snapshot)
        return snapshot


def stage4_assets_root() -> Path:
    return Path(__file__).resolve().parent / "assets"


def load_issue_style_catalog(
    *,
    assets_root: Path | None = None,
    config_path: Path | None = None,
    enabled_styles: Sequence[str] | None = None,
) -> IssueStyleCatalog:
    root = (assets_root or stage4_assets_root()).resolve()
    path = (config_path or root / ISSUE_STYLE_CONFIG_FILENAME).resolve()
    _ensure_within_assets(path, root, label="issue style config")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IssueStyleConfigError(f"failed to read issue style config {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise IssueStyleConfigError(f"invalid JSON in issue style config {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IssueStyleConfigError("issue style config must be a JSON object")
    if payload.get("schema_version") != ISSUE_STYLE_CONFIG_SCHEMA_VERSION:
        raise IssueStyleConfigError(
            "unsupported issue style config schema_version: "
            f"{payload.get('schema_version')!r}; expected {ISSUE_STYLE_CONFIG_SCHEMA_VERSION}"
        )

    issuer_prompt = _read_style_asset(
        root,
        root,
        "issuer_prompt.md",
        label="common Stage4 issuer prompt",
    )

    raw_manifests = payload.get("style_manifests")
    if not isinstance(raw_manifests, list) or not raw_manifests:
        raise IssueStyleConfigError("issue style config style_manifests must be a non-empty array")
    styles: dict[str, IssueStyleSpec] = {}
    config_dir = path.parent
    for index, manifest_value in enumerate(raw_manifests, start=1):
        manifest_relative = str(manifest_value or "").strip()
        if not manifest_relative:
            raise IssueStyleConfigError(f"style_manifests[{index}] is empty")
        manifest_path = (config_dir / manifest_relative).resolve()
        _ensure_within_assets(manifest_path, root, label=f"style manifest {index}")
        raw_style = _read_json_object(manifest_path, label=f"style manifest {manifest_relative}")
        if not isinstance(raw_style, dict):
            raise IssueStyleConfigError(f"style manifest {manifest_relative} must be an object")
        style_id = _validated_style_id(raw_style.get("id"), label=f"style manifest {manifest_relative} id")
        if style_id in styles:
            raise IssueStyleConfigError(f"duplicate issue style id: {style_id}")
        display_name = str(raw_style.get("display_name") or "").strip()
        if not display_name:
            raise IssueStyleConfigError(f"issue style {style_id} is missing display_name")
        prompt_assets = raw_style.get("prompt_assets")
        if prompt_assets is None:
            prompt_assets = [raw_style.get("prompt_asset")]
        if not isinstance(prompt_assets, list) or not prompt_assets:
            raise IssueStyleConfigError(f"issue style {style_id} must define at least one prompt asset")
        style_dir = manifest_path.parent
        prompts = tuple(
            _read_style_asset(
                root,
                style_dir,
                value,
                label=f"prompt asset for issue style {style_id}",
            )
            for value in prompt_assets
        )
        output = _read_manifest_output_spec(root, style_dir, raw_style, style_id=style_id)
        one_shot_values = raw_style.get("one_shot_assets") or []
        if not isinstance(one_shot_values, list):
            raise IssueStyleConfigError(f"issue style {style_id} one_shot_assets must be an array")
        one_shots = tuple(
            _read_style_asset(
                root,
                style_dir,
                value,
                label=f"one-shot asset for issue style {style_id}",
            )
            for value in one_shot_values
        )
        local_example = _read_style_asset(
            root,
            style_dir,
            raw_style.get("local_example_asset"),
            label=f"local example asset for issue style {style_id}",
        )
        _parse_local_example(local_example, label=f"local example for issue style {style_id}")
        styles[style_id] = IssueStyleSpec(
            id=style_id,
            display_name=display_name,
            prompt_variants=prompts,
            output=output,
            local_example=local_example,
            one_shots=one_shots,
        )

    raw_enabled = list(enabled_styles) if enabled_styles is not None else payload.get("enabled_styles")
    if not isinstance(raw_enabled, list) or not raw_enabled:
        raise IssueStyleConfigError("enabled issue styles must be a non-empty list")
    enabled = tuple(
        _validated_style_id(value, label=f"enabled_styles[{index}]")
        for index, value in enumerate(raw_enabled, start=1)
    )
    if len(set(enabled)) != len(enabled):
        raise IssueStyleConfigError("issue style config enabled_styles must not contain duplicates")
    unknown = [style_id for style_id in enabled if style_id not in styles]
    if unknown:
        raise IssueStyleConfigError(f"issue style config enabled_styles reference unknown styles: {unknown}")

    catalog_payload = {
        "schema_version": ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        "issuer_prompt": issuer_prompt.snapshot(),
        "enabled_styles": list(enabled),
        "styles": [
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "prompt_variants": [asset.snapshot() for asset in spec.prompt_variants],
                "output": spec.output.snapshot(),
                "local_example": spec.local_example.snapshot(),
                "one_shots": [asset.snapshot() for asset in spec.one_shots],
            }
            for spec in styles.values()
        ],
    }
    return IssueStyleCatalog(
        schema_version=ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        enabled_styles=enabled,
        styles=styles,
        issuer_prompt=issuer_prompt,
        sha256=_sha256_json(catalog_payload),
    )


def runtime_issue_style_specs(stage4: dict[str, Any]) -> list[RuntimeIssueStyleSpec]:
    snapshot = stage4.get("generation_config")
    if not isinstance(snapshot, dict):
        raise IssueStyleConfigError("stage4 runtime is missing generation_config")
    schema_version = snapshot.get("schema_version")
    if schema_version not in {
        LEGACY_ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        OUTPUT_SPEC_ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        LOCAL_EXAMPLE_ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        COMMON_PROMPT_ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
        ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
    }:
        raise IssueStyleConfigError(
            "unsupported runtime generation_config schema_version: "
            f"{schema_version!r}; expected one of "
            f"{LEGACY_ISSUE_STYLE_CONFIG_SCHEMA_VERSION}, "
            f"{OUTPUT_SPEC_ISSUE_STYLE_CONFIG_SCHEMA_VERSION}, "
            f"{LOCAL_EXAMPLE_ISSUE_STYLE_CONFIG_SCHEMA_VERSION}, "
            f"{COMMON_PROMPT_ISSUE_STYLE_CONFIG_SCHEMA_VERSION}, "
            f"{ISSUE_STYLE_CONFIG_SCHEMA_VERSION}"
        )
    expected_sha = str(snapshot.get("sha256") or "").strip()
    fingerprint = dict(snapshot)
    fingerprint.pop("sha256", None)
    if not expected_sha or _sha256_json(fingerprint) != expected_sha:
        raise IssueStyleConfigError("runtime generation_config sha256 mismatch")

    raw_styles = snapshot.get("styles")
    enabled = snapshot.get("enabled_styles")
    if not isinstance(raw_styles, list) or not isinstance(enabled, list):
        raise IssueStyleConfigError("runtime generation_config must contain styles and enabled_styles")
    specs = [
        _runtime_spec(raw, index=index, schema_version=int(schema_version))
        for index, raw in enumerate(raw_styles, start=1)
    ]
    enabled_ids = [_validated_style_id(value, label="runtime enabled style") for value in enabled]
    if len(set(enabled_ids)) != len(enabled_ids):
        raise IssueStyleConfigError("runtime generation_config enabled_styles contains duplicates")
    if [spec.id for spec in specs] != enabled_ids:
        raise IssueStyleConfigError("runtime generation_config style order does not match enabled_styles")
    if not specs:
        raise IssueStyleConfigError("runtime generation_config must contain at least one style")
    return specs


def runtime_issue_style_ids(stage4: dict[str, Any]) -> list[str]:
    return [spec.id for spec in runtime_issue_style_specs(stage4)]


def runtime_generation_spec(stage4: dict[str, Any], style_id: str) -> RuntimeIssueStyleSpec:
    for spec in runtime_issue_style_specs(stage4):
        if spec.id == style_id:
            return spec
    raise IssueStyleConfigError(f"runtime generation_config does not enable style {style_id!r}")


def runtime_issuer_prompt(stage4: dict[str, Any]) -> PromptAsset:
    snapshot = stage4.get("generation_config")
    if not isinstance(snapshot, dict):
        raise IssueStyleConfigError("stage4 runtime is missing generation_config")
    runtime_issue_style_specs(stage4)
    if int(snapshot.get("schema_version") or 0) < COMMON_PROMPT_ISSUE_STYLE_CONFIG_SCHEMA_VERSION:
        raise IssueStyleConfigError(
            "runtime generation_config does not snapshot the common Stage4 issuer prompt"
        )
    return _prompt_asset_from_snapshot(
        snapshot.get("issuer_prompt"),
        label="runtime common Stage4 issuer prompt",
    )


def is_current_generation_config(stage4: dict[str, Any]) -> bool:
    try:
        runtime_issue_style_specs(stage4)
    except IssueStyleConfigError:
        return False
    return dict(stage4.get("generation_config") or {}).get("schema_version") == ISSUE_STYLE_CONFIG_SCHEMA_VERSION


def local_style_submission(spec: RuntimeIssueStyleSpec) -> dict[str, Any]:
    if spec.local_example is None:
        raise IssueStyleConfigError(
            f"runtime generation style {spec.id} does not provide a local example"
        )
    return _parse_local_example(
        spec.local_example,
        label=f"runtime style {spec.id} local example",
    )


def _runtime_spec(raw: Any, *, index: int, schema_version: int) -> RuntimeIssueStyleSpec:
    if not isinstance(raw, dict):
        raise IssueStyleConfigError(f"runtime generation_config styles[{index}] must be an object")
    style_id = _validated_style_id(raw.get("id"), label=f"runtime styles[{index}].id")
    display_name = str(raw.get("display_name") or "").strip()
    if not display_name:
        raise IssueStyleConfigError(f"runtime generation style {style_id} is missing display_name")
    prompt = _prompt_asset_from_snapshot(raw.get("selected_prompt"), label=f"runtime style {style_id} prompt")
    output = (
        _output_spec_from_snapshot(raw.get("output"), style_id=style_id)
        if schema_version >= OUTPUT_SPEC_ISSUE_STYLE_CONFIG_SCHEMA_VERSION
        else _legacy_output_spec()
    )
    raw_one_shots = raw.get("one_shots") or []
    if not isinstance(raw_one_shots, list):
        raise IssueStyleConfigError(f"runtime generation style {style_id} one_shots must be an array")
    one_shots = tuple(
        _prompt_asset_from_snapshot(value, label=f"runtime style {style_id} one-shot {shot_index}")
        for shot_index, value in enumerate(raw_one_shots, start=1)
    )
    local_example = None
    if schema_version >= LOCAL_EXAMPLE_ISSUE_STYLE_CONFIG_SCHEMA_VERSION:
        local_example = _prompt_asset_from_snapshot(
            raw.get("local_example"),
            label=f"runtime style {style_id} local example",
        )
        _parse_local_example(local_example, label=f"runtime style {style_id} local example")
    return RuntimeIssueStyleSpec(
        id=style_id,
        display_name=display_name,
        prompt=prompt,
        output=output,
        one_shots=one_shots,
        local_example=local_example,
    )


def _read_manifest_output_spec(
    root: Path,
    style_dir: Path,
    raw: dict[str, Any],
    *,
    style_id: str,
) -> IssueStyleOutputSpec:
    fields_schema = _read_style_asset(
        root,
        style_dir,
        raw.get("fields_schema_asset"),
        label=f"fields schema asset for issue style {style_id}",
    )
    _parse_fields_schema(fields_schema, label=f"fields schema for issue style {style_id}")
    template = _read_style_asset(
        root,
        style_dir,
        raw.get("task_input_template_asset"),
        label=f"template asset for issue style {style_id}",
    )
    _validate_template(template, label=f"output template for issue style {style_id}")
    custom_validator = _read_style_asset(
        root,
        style_dir,
        raw.get("validator_asset"),
        label=f"custom validator asset for issue style {style_id}",
    )
    _validate_custom_validator_source(
        custom_validator,
        label=f"custom validator for issue style {style_id}",
    )
    return IssueStyleOutputSpec(
        fields_schema=fields_schema,
        template=template,
        validators=(*UNIVERSAL_ISSUE_STYLE_VALIDATORS, "custom"),
        custom_validator=custom_validator,
    )


def _output_spec_from_snapshot(raw: Any, *, style_id: str) -> IssueStyleOutputSpec:
    if not isinstance(raw, dict):
        raise IssueStyleConfigError(f"runtime generation style {style_id} output must be an object")
    fields_schema = _prompt_asset_from_snapshot(
        raw.get("fields_schema"),
        label=f"runtime style {style_id} fields schema",
    )
    _parse_fields_schema(fields_schema, label=f"runtime style {style_id} fields schema")
    template = _prompt_asset_from_snapshot(
        raw.get("template"),
        label=f"runtime style {style_id} template",
    )
    _validate_template(template, label=f"runtime style {style_id} output template")
    validators = _validated_validators(
        raw.get("validators"),
        label=f"runtime style {style_id} validators",
    )
    custom_validator = None
    if raw.get("custom_validator") is not None:
        custom_validator = _prompt_asset_from_snapshot(
            raw.get("custom_validator"),
            label=f"runtime style {style_id} custom validator",
        )
        _validate_custom_validator_source(
            custom_validator,
            label=f"runtime style {style_id} custom validator",
        )
    if "custom" in validators and custom_validator is None:
        raise IssueStyleConfigError(
            f"runtime style {style_id} declares a custom validator without its frozen asset"
        )
    return IssueStyleOutputSpec(
        fields_schema=fields_schema,
        template=template,
        validators=validators,
        custom_validator=custom_validator,
    )


def _validated_validators(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise IssueStyleConfigError(f"{label} must be a non-empty array")
    validators = tuple(str(item or "").strip() for item in value)
    if any(not item for item in validators):
        raise IssueStyleConfigError(f"{label} contains an empty validator")
    if len(set(validators)) != len(validators):
        raise IssueStyleConfigError(f"{label} contains duplicates")
    missing = sorted(set(UNIVERSAL_ISSUE_STYLE_VALIDATORS) - set(validators))
    if missing:
        raise IssueStyleConfigError(f"{label} is missing required validators: {missing}")
    return validators


def _parse_fields_schema(asset: PromptAsset, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(asset.text)
    except json.JSONDecodeError as exc:
        raise IssueStyleConfigError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IssueStyleConfigError(f"{label} must contain a JSON object")
    if payload.get("type") != "object":
        raise IssueStyleConfigError(f"{label} must declare an object schema")
    validator_cls = validator_for(payload)
    try:
        validator_cls.check_schema(payload)
    except Exception as exc:  # noqa: BLE001
        raise IssueStyleConfigError(f"invalid JSON Schema in {label}: {exc}") from exc
    return payload


def _validate_template(asset: PromptAsset, *, label: str) -> None:
    try:
        Environment().parse(asset.text)
    except TemplateSyntaxError as exc:
        raise IssueStyleConfigError(f"invalid Jinja template in {label}: {exc}") from exc


def _validate_custom_validator_source(asset: PromptAsset, *, label: str) -> None:
    try:
        tree = compile(asset.text, asset.asset, "exec", flags=0, dont_inherit=True, optimize=0)
    except SyntaxError as exc:
        raise IssueStyleConfigError(f"invalid Python in {label}: {exc}") from exc
    namespace: dict[str, Any] = {"__name__": "feature_factory_stage4_style_validator_check"}
    try:
        exec(tree, namespace)  # noqa: S102 - style assets are trusted, frozen application code.
    except Exception as exc:  # noqa: BLE001
        raise IssueStyleConfigError(f"failed to load {label}: {exc}") from exc
    if not callable(namespace.get("validate")):
        raise IssueStyleConfigError(f"{label} must define a callable validate function")


def _parse_local_example(asset: PromptAsset, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(asset.text)
    except json.JSONDecodeError as exc:
        raise IssueStyleConfigError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IssueStyleConfigError(f"{label} must contain a JSON object")
    title = str(payload.get("title") or "").strip()
    fields = payload.get("fields")
    if not title:
        raise IssueStyleConfigError(f"{label} must include a non-empty title")
    if not isinstance(fields, dict):
        raise IssueStyleConfigError(f"{label} must include a fields object")
    return {"title": title, "fields": dict(fields)}


def _legacy_output_spec() -> IssueStyleOutputSpec:
    schema_text = json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "additionalProperties": False,
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "minLength": 1},
            },
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    template_text = "{{ fields.content }}"
    return IssueStyleOutputSpec(
        fields_schema=PromptAsset(
            asset="legacy/fields.schema.json",
            text=schema_text,
            sha256=_sha256_text(schema_text),
        ),
        template=PromptAsset(
            asset="legacy/task_input.md.j2",
            text=template_text,
            sha256=_sha256_text(template_text),
        ),
        validators=("schema", "render", "leakage"),
    )


def _read_style_asset(
    root: Path,
    style_dir: Path,
    value: Any,
    *,
    label: str,
) -> PromptAsset:
    relative = str(value or "").strip()
    if not relative:
        raise IssueStyleConfigError(f"{label} is missing")
    path = (style_dir / relative).resolve()
    _ensure_within_assets(path, style_dir, label=label)
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise IssueStyleConfigError(f"failed to read {label} {path}: {exc}") from exc
    if not text:
        raise IssueStyleConfigError(f"{label} is empty: {path}")
    return PromptAsset(asset=path.relative_to(root).as_posix(), text=text, sha256=_sha256_text(text))


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IssueStyleConfigError(f"failed to read {label} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise IssueStyleConfigError(f"invalid JSON in {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise IssueStyleConfigError(f"{label} must contain a JSON object")
    return payload


def _prompt_asset_from_snapshot(raw: Any, *, label: str) -> PromptAsset:
    if not isinstance(raw, dict):
        raise IssueStyleConfigError(f"{label} must be an object")
    asset = str(raw.get("asset") or "").strip()
    text = str(raw.get("text") or "").strip()
    expected_sha = str(raw.get("sha256") or "").strip()
    if not asset or not text or not expected_sha:
        raise IssueStyleConfigError(f"{label} must include asset, text, and sha256")
    if _sha256_text(text) != expected_sha:
        raise IssueStyleConfigError(f"{label} sha256 mismatch")
    return PromptAsset(asset=asset, text=text, sha256=expected_sha)


def _validated_style_id(value: Any, *, label: str) -> str:
    style_id = str(value or "").strip()
    if not ISSUE_STYLE_ID_RE.fullmatch(style_id):
        raise IssueStyleConfigError(
            f"{label} must match {ISSUE_STYLE_ID_RE.pattern!r}; got {style_id!r}"
        )
    return style_id


def _ensure_within_assets(path: Path, root: Path, *, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise IssueStyleConfigError(f"{label} escapes the Stage4 assets directory: {path}") from exc


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_json(value: dict[str, Any]) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))