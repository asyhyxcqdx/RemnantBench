from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from feature_factory.config import Settings
from feature_factory.stage4.issue_styles import (
    ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
    IssueStyleConfigError,
    is_current_generation_config,
    local_style_submission,
    load_issue_style_catalog,
    runtime_generation_spec,
    runtime_issuer_prompt,
    runtime_issue_style_ids,
    runtime_issue_style_specs,
)
from feature_factory.stage4.style_outputs import validate_and_render_style_output


def _write_catalog(
    root: Path,
    *,
    enabled_styles: list[str] | None = None,
    styles: list[dict[str, object]] | None = None,
) -> Path:
    (root / "issuer_prompt.md").write_text(
        "Common task-author prompt with {{OUTPUT_INSTRUCTIONS}} and {{PRIVATE_CONTEXT_JSON}}.\n",
        encoding="utf-8",
    )
    style_dir = root / "issue_styles"
    style_dir.mkdir(parents=True, exist_ok=True)
    raw_styles = styles or [
        {
            "id": "swe",
            "display_name": "SWE",
            "prompt_assets": ["swe-a.md", "swe-b.md"],
        },
        {
            "id": "fb",
            "display_name": "Structured PRD",
            "prompt_assets": ["fb.md"],
        },
        {
            "id": "hint",
            "display_name": "Guided issue",
            "prompt_assets": ["hint-light.md", "hint-medium.md", "hint-strong.md"],
        },
    ]
    manifest_assets: list[str] = []
    used_directories: set[str] = set()
    for index, raw_style in enumerate(raw_styles, start=1):
        manifest = dict(raw_style)
        style_id = str(manifest.get("id") or f"style-{index}")
        directory_name = style_id if style_id not in used_directories else f"{style_id}-{index}"
        used_directories.add(directory_name)
        current_dir = style_dir / directory_name
        current_dir.mkdir(parents=True, exist_ok=True)
        prompt_assets = manifest.get("prompt_assets")
        if prompt_assets is None:
            prompt_assets = [manifest.pop("prompt_asset", "prompt.md")]
        manifest["prompt_assets"] = prompt_assets
        manifest.setdefault("fields_schema_asset", "fields.schema.json")
        manifest.setdefault("task_input_template_asset", "task_input.md.j2")
        manifest.setdefault("local_example_asset", "local_example.json")
        manifest.setdefault("validator_asset", "validator.py")
        for prompt_name in prompt_assets:
            prompt_path = (current_dir / str(prompt_name)).resolve()
            if current_dir.resolve() in prompt_path.parents:
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                prompt_path.write_text(f"Write the {style_id} task input.\n", encoding="utf-8")
        (current_dir / "fields.schema.json").write_text(
            json.dumps(
                {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["content"],
                    "properties": {"content": {"type": "string", "minLength": 1}},
                }
            ),
            encoding="utf-8",
        )
        (current_dir / "task_input.md.j2").write_text("{{ fields.content }}\n", encoding="utf-8")
        (current_dir / "local_example.json").write_text(
            json.dumps(
                {
                    "title": f"{style_id} local title",
                    "fields": {"content": f"Distinct local task input for {style_id}."},
                }
            ),
            encoding="utf-8",
        )
        (current_dir / "validator.py").write_text(
            (
                "def validate(*, title, fields, rendered_markdown, context):\n"
                "    return []\n"
            ),
            encoding="utf-8",
        )
        (current_dir / "style.json").write_text(json.dumps(manifest), encoding="utf-8")
        manifest_assets.append(f"{directory_name}/style.json")
    config_path = style_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": ISSUE_STYLE_CONFIG_SCHEMA_VERSION,
                "enabled_styles": enabled_styles or ["swe", "fb", "hint"],
                "style_manifests": manifest_assets,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return config_path


def _snapshot_sha(payload: dict[str, object]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def test_catalog_snapshots_three_uniform_issue_styles(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    snapshot = catalog.snapshot(seed="run-123")
    stage4 = {"generation_config": snapshot}

    assert catalog.enabled_styles == ("swe", "fb", "hint")
    assert snapshot["schema_version"] == ISSUE_STYLE_CONFIG_SCHEMA_VERSION
    assert snapshot["issuer_prompt"]["asset"] == "issuer_prompt.md"
    assert runtime_issuer_prompt(stage4).text.startswith("Common task-author prompt")
    assert [item["id"] for item in snapshot["styles"]] == ["swe", "fb", "hint"]
    assert runtime_issue_style_ids(stage4) == ["swe", "fb", "hint"]
    assert [spec.id for spec in runtime_issue_style_specs(stage4)] == ["swe", "fb", "hint"]
    fb_spec = runtime_generation_spec(stage4, "fb")
    assert fb_spec.one_shots == ()
    assert fb_spec.output.fields_schema.asset == "issue_styles/fb/fields.schema.json"
    assert fb_spec.output.template.asset == "issue_styles/fb/task_input.md.j2"
    assert fb_spec.output.custom_validator is not None
    assert fb_spec.output.custom_validator.asset == "issue_styles/fb/validator.py"
    assert "custom" in fb_spec.output.validators
    assert fb_spec.local_example is not None
    assert snapshot == catalog.snapshot(seed="run-123")
    assert is_current_generation_config(stage4) is True


@pytest.mark.parametrize(
    ("style_id", "expected"),
    [
        ("swe", {"issue_styles/swe/swe-a.md", "issue_styles/swe/swe-b.md"}),
        (
            "hint",
            {
                "issue_styles/hint/hint-light.md",
                "issue_styles/hint/hint-medium.md",
                "issue_styles/hint/hint-strong.md",
            },
        ),
    ],
)
def test_prompt_pool_selection_varies_deterministically(
    tmp_path: Path,
    style_id: str,
    expected: set[str],
) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    selected = {
        runtime_generation_spec(
            {"generation_config": catalog.snapshot(seed=f"run-{index}")},
            style_id,
        ).prompt.asset
        for index in range(100)
    }
    assert selected == expected


def test_catalog_accepts_new_style_without_python_registration(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(
        assets_root=tmp_path,
        config_path=_write_catalog(
            tmp_path,
            enabled_styles=["support_request"],
            styles=[
                {
                    "id": "support_request",
                    "display_name": "Support request",
                    "prompt_assets": ["prompt.md"],
                }
            ],
        ),
    )
    stage4 = {"generation_config": catalog.snapshot(seed="new-style-run")}
    assert runtime_issue_style_ids(stage4) == ["support_request"]
    spec = runtime_generation_spec(stage4, "support_request")
    submission = local_style_submission(spec)
    result = validate_and_render_style_output(
        spec=spec,
        title=str(submission["title"]),
        fields=dict(submission["fields"]),
        repository_name="example/repository",
    )
    assert result.accepted is True
    assert result.rendered_markdown == "Distinct local task input for support_request."


def test_catalog_enabled_styles_can_be_overridden_by_runtime_configuration(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(
        assets_root=tmp_path,
        config_path=_write_catalog(tmp_path),
        enabled_styles=["fb", "swe"],
    )

    assert catalog.enabled_styles == ("fb", "swe")
    assert [spec.id for spec in catalog.enabled_specs()] == ["fb", "swe"]

    with pytest.raises(IssueStyleConfigError, match="unknown styles"):
        load_issue_style_catalog(
            assets_root=tmp_path,
            config_path=_write_catalog(tmp_path),
            enabled_styles=["missing"],
        )


def test_enabled_contract_fingerprint_ignores_disabled_styles(tmp_path: Path) -> None:
    config_path = _write_catalog(tmp_path, enabled_styles=["swe"])
    original = load_issue_style_catalog(assets_root=tmp_path, config_path=config_path)
    original_fingerprint = original.enabled_contract_sha256()
    original_catalog_sha = original.sha256

    (tmp_path / "issue_styles" / "fb" / "fb.md").write_text(
        "Changed disabled FB prompt.\n",
        encoding="utf-8",
    )
    changed = load_issue_style_catalog(assets_root=tmp_path, config_path=config_path)

    assert changed.sha256 != original_catalog_sha
    assert changed.enabled_contract_sha256() == original_fingerprint
    assert changed.snapshot(seed="same-run")["catalog_sha256"] == original_fingerprint


def test_settings_parse_comma_separated_stage4_issue_styles(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, stage4_issue_styles=" swe, fb ")
    assert settings.stage4_enabled_issue_style_ids() == ["swe", "fb"]

    monkeypatch.setenv("FEATURE_FACTORY_STAGE4_ISSUE_STYLES", "hint,swe")
    assert Settings(_env_file=None).stage4_enabled_issue_style_ids() == ["hint", "swe"]

    with pytest.raises(ValueError, match="empty values"):
        Settings(_env_file=None, stage4_issue_styles="swe,,fb").stage4_enabled_issue_style_ids()
    with pytest.raises(ValueError, match="duplicates"):
        Settings(_env_file=None, stage4_issue_styles="swe,swe").stage4_enabled_issue_style_ids()


def test_builtin_fb_prompt_treats_reference_patch_as_private_evidence() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["fb"])
    stage4 = {"generation_config": catalog.snapshot(seed="fb-interface-boundary")}
    spec = runtime_generation_spec(stage4, "fb")
    schema = json.loads(spec.output.fields_schema.text)

    assert "reference patch are confidential evidence" in spec.prompt.text
    assert "not how the removed implementation worked" in spec.prompt.text
    assert "leave implementation discovery to the coding agent" in spec.prompt.text
    assert len(spec.one_shots) == 1
    assert "without disclosing an implementation strategy" in spec.one_shots[0].text
    assert "complete behavior specification" in schema["properties"]["interfaces"]["items"]["properties"]["description"]["description"]
    assert "maxLength" not in schema["properties"]["task_statement"]
    assert "maxLength" not in schema["properties"]["interfaces"]["items"]["properties"]["description"]


def test_builtin_swe_prompts_keep_internal_diagnosis_out_of_public_reports() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["swe"])
    spec = catalog.styles["swe"]
    schema = json.loads(spec.output.fields_schema.text)

    assert len(spec.prompt_variants) == 6
    for prompt in spec.prompt_variants:
        assert "The reference patch is confidential evidence" in prompt.text
        assert "plausible repository user" in prompt.text
        assert "concrete code-level cause" in prompt.text
    assert "not a source for internal root-cause claims" in schema["properties"]["content"]["description"]
    assert "maxLength" not in schema["properties"]["content"]


def test_builtin_hint_prompts_bound_each_guidance_level_away_from_the_repair() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["hint"])
    spec = catalog.styles["hint"]
    prompts = {Path(prompt.asset).name: prompt.text for prompt in spec.prompt_variants}
    schema = json.loads(spec.output.fields_schema.text)

    assert set(prompts) == {"light.md", "medium.md", "strong.md"}
    for prompt in prompts.values():
        assert "The reference patch is confidential evidence" in prompt
    assert "Do not name internal symbols" in prompts["light.md"]
    assert "subsystem, state transition, data-flow boundary" in prompts["medium.md"]
    assert "must not identify an exact broken condition" in prompts["strong.md"]
    assert "concrete repair operation" in schema["properties"]["content"]["description"]
    assert "maxLength" not in schema["properties"]["content"]


def test_builtin_fb_validator_rejects_algorithm_prose_and_executable_body() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["fb"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-fb")},
        "fb",
    )

    result = validate_and_render_style_output(
        spec=spec,
        title="Add stable identifiers",
        fields={
            "task_statement": "Build identifiers recursively with a fallback for tuples.",
            "interfaces": [
                {
                    "description": "Return the stable identifier for a public value.",
                    "path": "src/identifiers.py",
                    "code": (
                        "def stable_identifier(value: object) -> int:\n"
                        "    <your code>\n"
                        "    return hash(value)"
                    ),
                }
            ],
        },
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert {error["code"] for error in result.errors} == {
        "FB_IMPLEMENTATION_DETAIL",
        "FB_EXECUTABLE_INTERFACE_BODY",
    }
    assert result.validation["custom_validator"]["asset"] == "issue_styles/fb/validator.py"


@pytest.mark.parametrize(
    ("title", "task_statement", "description", "expected_path"),
    [
        (
            "Restore local JSON syntax-repair path",
            "Handle malformed JSON responses.",
            "Return a parseable representation for malformed JSON input.",
            "title",
        ),
        (
            "Handle malformed JSON responses",
            "Fix malformed responses before external repair strategies are attempted.",
            "Return a parseable representation for malformed JSON input.",
            "fields.task_statement",
        ),
        (
            "Support block identifiers",
            "Support valid block identifiers.",
            "Accept bytes encoded in latin-1 and integers in the range [0, 2**256).",
            "fields.interfaces[0].description",
        ),
        (
            "Expose contract events",
            "Expose named contract events.",
            "Populate named event attributes on the collection during initialization.",
            "fields.interfaces[0].description",
        ),
        (
            "Repair malformed JSON",
            "Accept malformed JSON output.",
            "Return repaired JSON text using the parser's internal helper methods.",
            "fields.interfaces[0].description",
        ),
        (
            "Restore contract event factory initialization in BaseContractEvents",
            "Expose named contract events.",
            "Return the contract event collection.",
            "title",
        ),
    ],
)
def test_builtin_fb_validator_rejects_observed_private_strategy_leaks(
    title: str,
    task_statement: str,
    description: str,
    expected_path: str,
) -> None:
    catalog = load_issue_style_catalog(enabled_styles=["fb"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-observed-fb")},
        "fb",
    )

    result = validate_and_render_style_output(
        spec=spec,
        title=title,
        fields={
            "task_statement": task_statement,
            "interfaces": [
                {
                    "description": description,
                    "path": "src/public_api.py",
                    "code": "def public_api(value: object) -> object:\n    <your code>",
                }
            ],
        },
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert any(
        error["code"] == "FB_IMPLEMENTATION_DETAIL" and error["path"] == expected_path
        for error in result.errors
    )


@pytest.mark.parametrize(
    ("style_id", "title", "content", "expected_codes"),
    [
        (
            "swe",
            "Fix parser fallback",
            "The implementation only recognizes the first private branch.",
            {"SWE_INSTRUCTION_TITLE", "SWE_INTERNAL_DIAGNOSIS"},
        ),
        (
            "hint",
            "Parser behavior differs",
            "Replace the comparison operator in the parser branch.",
            {"HINT_DIRECT_REPAIR"},
        ),
    ],
)
def test_builtin_text_style_validators_reject_solution_leakage(
    style_id: str,
    title: str,
    content: str,
    expected_codes: set[str],
) -> None:
    catalog = load_issue_style_catalog(enabled_styles=[style_id])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed=f"validator-{style_id}")},
        style_id,
    )

    result = validate_and_render_style_output(
        spec=spec,
        title=title,
        fields={"content": content},
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert {error["code"] for error in result.errors} == expected_codes


@pytest.mark.parametrize(
    "content",
    [
        (
            "Malformed JSON fails before falling back to an external repair strategy, and the "
            "built-in local fixes are no longer attempted."
        ),
        "Integer block identifiers fail because the routing logic was dropped.",
        (
            "Named events fail because their attributes are no longer being set on the event "
            "collection during contract initialization."
        ),
        "Block identifiers fail because there is a routing issue in how values are matched.",
        "All block identifiers fail because the routing logic is gone.",
        "The predefined block identifier path was lost somewhere in the routing logic.",
        "Everything is rejected by the internal block identifier routing.",
        "Named event attributes appear to have stopped being initialized on the events namespace.",
        "The events namespace is not being populated from the contract ABI.",
        "The parser should recover without an external library or another round-trip.",
    ],
)
def test_builtin_swe_validator_rejects_observed_internal_diagnoses(content: str) -> None:
    catalog = load_issue_style_catalog(enabled_styles=["swe"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-observed-swe")},
        "swe",
    )

    result = validate_and_render_style_output(
        spec=spec,
        title="Public operation raises an error",
        fields={"content": content},
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert {error["code"] for error in result.errors} == {"SWE_INTERNAL_DIAGNOSIS"}


def test_builtin_swe_validator_checks_internal_diagnosis_in_title() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["swe"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-title-swe")},
        "swe",
    )

    result = validate_and_render_style_output(
        spec=spec,
        title="RobustJSONParser no longer repairs malformed output locally",
        fields={"content": "Malformed JSON input now raises JSONParseError."},
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert {error["code"] for error in result.errors} == {"SWE_INTERNAL_DIAGNOSIS"}
    assert result.errors[0]["path"] == "title"


def test_builtin_swe_validator_rejects_patch_private_symbol_and_internal_module() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["swe"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-private-swe")},
        "swe",
    )
    context = {
        "private": {
            "target_selector": (
                "tests/core/block-utils/test_select_method_for_block_identifier.py"
            ),
            "gold_patch_text": (
                "diff --git a/web3/_utils/blocks.py b/web3/_utils/blocks.py\n"
                "--- a/web3/_utils/blocks.py\n"
                "+++ b/web3/_utils/blocks.py\n"
                "@@ -1,3 +1,3 @@\n"
                " def select_method_for_block_identifier(value: object) -> object:\n"
                "-    return None\n"
                "+    return value\n"
            ),
        }
    }

    symbol_result = validate_and_render_style_output(
        spec=spec,
        title="select_method_for_block_identifier rejects latest",
        fields={"content": "The selector rejects a supported block identifier."},
        repository_name="example/repository",
        validation_context=context,
    )
    module_result = validate_and_render_style_output(
        spec=spec,
        title="Latest block lookup fails",
        fields={"content": "The failing call lives in web3._utils.blocks internally."},
        repository_name="example/repository",
        validation_context=context,
    )
    public_result = validate_and_render_style_output(
        spec=spec,
        title="Latest block lookup raises Web3ValueError",
        fields={"content": "Calling w3.eth.get_block(\"latest\") raises Web3ValueError."},
        repository_name="example/repository",
        validation_context=context,
    )

    assert {error["code"] for error in symbol_result.errors} == {
        "SWE_INTERNAL_API_REFERENCE"
    }
    assert {error["code"] for error in module_result.errors} == {
        "SWE_INTERNAL_API_REFERENCE"
    }
    assert public_result.accepted is True


def test_builtin_fb_validator_rejects_strategy_docstring_in_interface_skeleton() -> None:
    catalog = load_issue_style_catalog(enabled_styles=["fb"])
    spec = runtime_generation_spec(
        {"generation_config": catalog.snapshot(seed="validator-docstring-fb")},
        "fb",
    )

    result = validate_and_render_style_output(
        spec=spec,
        title="Enable malformed JSON recovery",
        fields={
            "task_statement": "Allow the parser to accept malformed JSON output.",
            "interfaces": [
                {
                    "description": "Parse JSON text into an object.",
                    "path": "src/parser.py",
                    "code": (
                        "class RobustJSONParser:\n"
                        "    \"\"\"集成多种修复策略，包括本地修复和第三方库。\"\"\"\n"
                        "\n"
                        "    def parse(self, value: str) -> object:\n"
                        "        <your code>"
                    ),
                }
            ],
        },
        repository_name="example/repository",
    )

    assert result.accepted is False
    assert any(
        error["code"] == "FB_IMPLEMENTATION_DETAIL"
        and error["path"] == "fields.interfaces[0].code"
        for error in result.errors
    )


def test_catalog_rejects_duplicate_ids_and_prompt_path_escape(tmp_path: Path) -> None:
    duplicate = [
        {
            "id": "swe",
            "display_name": "SWE",
            "prompt_assets": ["prompt.md"],
        },
        {
            "id": "swe",
            "display_name": "Duplicate",
            "prompt_assets": ["prompt.md"],
        },
    ]
    with pytest.raises(IssueStyleConfigError, match="duplicate issue style id"):
        load_issue_style_catalog(
            assets_root=tmp_path,
            config_path=_write_catalog(tmp_path, enabled_styles=["swe"], styles=duplicate),
        )

    escaping = [{"id": "swe", "display_name": "SWE", "prompt_assets": ["../outside.md"]}]
    with pytest.raises(IssueStyleConfigError, match="escapes"):
        load_issue_style_catalog(
            assets_root=tmp_path,
            config_path=_write_catalog(tmp_path, enabled_styles=["swe"], styles=escaping),
        )


def test_runtime_snapshot_hash_is_verified_and_obsolete_snapshot_is_rejected(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    snapshot = catalog.snapshot(seed="run-123")
    snapshot["styles"][0]["selected_prompt"]["text"] = "tampered"

    with pytest.raises(IssueStyleConfigError, match="generation_config sha256 mismatch"):
        runtime_issue_style_specs({"generation_config": snapshot})

    snapshot = catalog.snapshot(seed="run-issuer-prompt")
    snapshot["issuer_prompt"]["text"] = "tampered common prompt"
    with pytest.raises(IssueStyleConfigError, match="generation_config sha256 mismatch"):
        runtime_issuer_prompt({"generation_config": snapshot})
    with pytest.raises(IssueStyleConfigError, match="missing generation_config"):
        runtime_issue_style_specs({"issue_styles": ["swe", "fb", "hint"]})
    assert is_current_generation_config({"generation_config": {"schema_version": 2}}) is False


def test_runtime_v3_snapshot_uses_legacy_content_output_but_is_not_current(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    snapshot = catalog.snapshot(seed="legacy-run")
    snapshot["schema_version"] = 3
    snapshot.pop("issuer_prompt")
    for style in snapshot["styles"]:
        style.pop("output")
    snapshot.pop("sha256")
    snapshot["sha256"] = _snapshot_sha(snapshot)
    stage4 = {"generation_config": snapshot}

    specs = runtime_issue_style_specs(stage4)

    assert specs[0].output.template.text == "{{ fields.content }}"
    assert is_current_generation_config(stage4) is False


def test_runtime_v4_snapshot_remains_readable_but_is_not_current(tmp_path: Path) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    snapshot = catalog.snapshot(seed="previous-run")
    snapshot["schema_version"] = 4
    snapshot.pop("issuer_prompt")
    for style in snapshot["styles"]:
        style.pop("local_example")
    snapshot.pop("sha256")
    snapshot["sha256"] = _snapshot_sha(snapshot)
    stage4 = {"generation_config": snapshot}

    specs = runtime_issue_style_specs(stage4)

    assert specs[0].output.template.text == "{{ fields.content }}"
    assert specs[0].local_example is None
    assert is_current_generation_config(stage4) is False


def test_runtime_v5_snapshot_remains_readable_but_has_no_frozen_common_prompt(
    tmp_path: Path,
) -> None:
    catalog = load_issue_style_catalog(assets_root=tmp_path, config_path=_write_catalog(tmp_path))
    snapshot = catalog.snapshot(seed="v5-run")
    snapshot["schema_version"] = 5
    snapshot.pop("issuer_prompt")
    snapshot.pop("sha256")
    snapshot["sha256"] = _snapshot_sha(snapshot)
    stage4 = {"generation_config": snapshot}

    specs = runtime_issue_style_specs(stage4)

    assert specs[0].local_example is not None
    assert is_current_generation_config(stage4) is False
    with pytest.raises(IssueStyleConfigError, match="does not snapshot"):
        runtime_issuer_prompt(stage4)


def test_common_stage4_engine_contains_no_builtin_style_contracts() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src/feature_factory/stage4"
    issue_styles_source = (source_root / "issue_styles.py").read_text(encoding="utf-8")
    style_outputs_source = (source_root / "style_outputs.py").read_text(encoding="utf-8")
    backend_source = (source_root / "backend.py").read_text(encoding="utf-8")

    for style_id in ("swe", "fb", "hint"):
        assert f'== "{style_id}"' not in issue_styles_source
        assert f'== "{style_id}"' not in style_outputs_source
        assert f'== "{style_id}"' not in backend_source
    for style_field in ("interfaces", "task_statement", "<your code>"):
        assert style_field not in style_outputs_source
    assert "_issue_variant" not in backend_source


def test_catalog_rejects_invalid_output_schema_template_and_local_example(tmp_path: Path) -> None:
    config_path = _write_catalog(tmp_path, enabled_styles=["swe"])

    (tmp_path / "issue_styles/swe/fields.schema.json").write_text(
        '{"type":"object","properties":{"bad":{"type":"not-a-real-type"}}}',
        encoding="utf-8",
    )
    with pytest.raises(IssueStyleConfigError, match="invalid JSON Schema"):
        load_issue_style_catalog(assets_root=tmp_path, config_path=config_path)

    _write_catalog(tmp_path, enabled_styles=["swe"])
    (tmp_path / "issue_styles/swe/task_input.md.j2").write_text("{% if", encoding="utf-8")
    with pytest.raises(IssueStyleConfigError, match="invalid Jinja template"):
        load_issue_style_catalog(assets_root=tmp_path, config_path=config_path)

    _write_catalog(tmp_path, enabled_styles=["swe"])
    (tmp_path / "issue_styles/swe/local_example.json").write_text(
        '{"title":"missing fields"}',
        encoding="utf-8",
    )
    with pytest.raises(IssueStyleConfigError, match="must include a fields object"):
        load_issue_style_catalog(assets_root=tmp_path, config_path=config_path)
