from __future__ import annotations

from feature_factory.llm_usage import aggregate_token_usage_by_model
from feature_factory.models import Stage2Run
from feature_factory.stage2.service import Stage2Service


def test_token_usage_merges_unique_prefixed_and_bare_model_aliases() -> None:
    assert aggregate_token_usage_by_model(
        [
            ("openai/glm-5-w4a8", 365729),
            ("glm-5-w4a8", 48440),
        ]
    ) == {"openai/glm-5-w4a8": 414169}


def test_token_usage_alias_merge_is_not_openai_specific() -> None:
    assert aggregate_token_usage_by_model(
        [
            ("hosted_vllm/glm-5-w4a8", 10),
            ("glm-5-w4a8", 5),
        ]
    ) == {"hosted_vllm/glm-5-w4a8": 15}


def test_token_usage_keeps_ambiguous_provider_variants_separate() -> None:
    assert aggregate_token_usage_by_model(
        [
            ("openai/gpt-5.4-mini", 10),
            ("azure/gpt-5.4-mini", 5),
            ("gpt-5.4-mini", 2),
        ]
    ) == {
        "azure/gpt-5.4-mini": 5,
        "gpt-5.4-mini": 2,
        "openai/gpt-5.4-mini": 10,
    }


def test_token_usage_prefers_runtime_model_for_safe_alias() -> None:
    assert aggregate_token_usage_by_model(
        [
            ("openai/glm-5-w4a8", 365729),
            ("glm-5-w4a8", 48440),
        ],
        preferred_models=["glm-5-w4a8"],
    ) == {"glm-5-w4a8": 414169}


def test_token_usage_prefers_runtime_provider_model_for_bare_completion_model() -> None:
    assert aggregate_token_usage_by_model(
        [("glm-5-w4a8", 48440)],
        preferred_models=["openai/glm-5-w4a8"],
    ) == {"openai/glm-5-w4a8": 48440}


def test_token_usage_does_not_apply_ambiguous_runtime_preferred_alias() -> None:
    assert aggregate_token_usage_by_model(
        [("gpt-5.4-mini", 100)],
        preferred_models=["openai/gpt-5.4-mini", "azure/gpt-5.4-mini"],
    ) == {"gpt-5.4-mini": 100}


def test_stage2_token_usage_uses_shared_model_alias_aggregation() -> None:
    run = Stage2Run(
        repository_id=1,
        planner_model="openai/glm-5-w4a8",
        planner_token_usage=10,
        worker_model="glm-5-w4a8",
        worker_token_usage=5,
        runtime_snapshot_json={
            "planner": {"model": "glm-5-w4a8"},
            "worker": {"model": "glm-5-w4a8"},
        },
    )

    assert Stage2Service(session=None)._own_run_token_usage_by_model(run) == {"glm-5-w4a8": 15}
