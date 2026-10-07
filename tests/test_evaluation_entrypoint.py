import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = load_script("evaluate")
summarize = load_script("summarize")
images = load_script("images")


@pytest.fixture
def release(tmp_path):
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/models.json").write_bytes(
        (ROOT / "configs/models.json").read_bytes()
    )
    (tmp_path / "datasets/Full200/example").mkdir(parents=True)
    (tmp_path / "datasets/Full200/example/task.toml").write_text("")
    return tmp_path


def args(root, *extra):
    return evaluate.build_parser().parse_args(
        [
            "--dataset-root",
            str(root / "datasets"),
            "--output-dir",
            str(root / "results"),
            *extra,
        ]
    )


def command_config(command):
    return json.loads(
        next(
            value.removeprefix("config=")
            for value in command
            if value.startswith("config=")
        )
    )


@pytest.mark.parametrize(
    "model", list(json.loads((ROOT / "configs/models.json").read_text()))
)
def test_all_presets_preserve_transport_and_use_150_queries(release, model):
    command, output = evaluate.build_command(args(release, "--model", model), release)
    spec = json.loads((release / "configs/models.json").read_text())[model]
    assert command_config(command) == spec["mini_config"]
    assert command_config(command)["agent"]["step_limit"] == 150
    assert "config_file=" not in " ".join(command)
    assert "retry_transient_api_errors=true" in command
    assert ("retry_service_failures=true" in command) == (model == "GLM-5.3")
    assert command[command.index("--max-retries") + 1] == "0"
    assert output == release / "results" / model / "Full200"


def test_cli_endpoint_and_effort_overrides_are_sent_to_both_layers(release):
    command, _ = evaluate.build_command(
        args(
            release,
            "--model",
            "GPT-6.1-Sol",
            "--base-url",
            "https://custom.example/v1",
            "--step-limit",
            "12",
            "--reasoning-effort",
            "low",
            "--concurrency",
            "3",
            "--task",
            "example",
        ),
        release,
    )
    config = command_config(command)
    assert config["model"]["model_kwargs"]["api_base"] == "https://custom.example/v1"
    assert command[command.index("--base-url") + 1] == "https://custom.example/v1"
    assert config["model"]["model_kwargs"]["reasoning"] == {"effort": "low"}
    assert config["agent"]["step_limit"] == 12
    assert command.index("--include-task-name") < command.index("--")
    assert command[command.index("--n-concurrent") + 1] == "3"


def test_custom_chat_model_is_supported_without_preset(release):
    command, _ = evaluate.build_command(
        args(
            release,
            "--model",
            "openai/custom-model",
            "--base-url",
            "http://models.example:8000/v1",
            "--reasoning-effort",
            "high",
        ),
        release,
    )
    assert (
        command_config(command)["model"]["model_kwargs"]["reasoning_effort"] == "high"
    )
    assert "responses_system_as_instructions=true" not in command


@pytest.mark.parametrize(
    "url", ["file:///tmp/socket", "https://user:secret@example.com/v1", "example.com"]
)
def test_invalid_endpoints_are_rejected_before_running(release, url):
    with pytest.raises(ValueError, match="HTTP"):
        evaluate.build_command(
            args(release, "--model", "GPT-6.1-Sol", "--base-url", url), release
        )


def test_dry_run_never_calls_docker_or_reads_a_key(release, monkeypatch, capsys):
    monkeypatch.setattr(evaluate, "ROOT", release)
    # build_command's default root is intentionally explicit here for an isolated test.
    real_build = evaluate.build_command
    monkeypatch.setattr(
        evaluate, "build_command", lambda value: real_build(value, release)
    )
    monkeypatch.setattr("sys.argv", ["evaluate.py", "--model", "GLM-5.3", "--dry-run"])
    with patch.object(evaluate.subprocess, "run") as run:
        evaluate.main()
    run.assert_not_called()
    assert "retry_service_failures=true" in capsys.readouterr().out


def test_report_never_turns_partial_cache_into_a_complete_total():
    rows = [
        {
            key: 1
            for key in (
                "reward",
                "score",
                "coverage",
                "agent_queries",
                "input_tokens",
                "output_tokens",
                "cache_tokens",
                "reported_cost_usd",
            )
        }
        for _ in range(2)
    ]
    rows[1]["cache_tokens"] = None
    report = summarize.aggregate(rows)
    assert report["cache_tokens"] == {
        "total": None,
        "reported_subtotal": 1,
        "missing_trials": 1,
    }
    assert report["input_tokens"]["total"] == 2


def test_missing_score_is_reported_with_its_denominator():
    rows = [
        {
            key: None
            for key in (
                "reward",
                "score",
                "coverage",
                "agent_queries",
                "input_tokens",
                "output_tokens",
                "cache_tokens",
                "reported_cost_usd",
            )
        }
    ]
    assert summarize.aggregate(rows)["score"] == {
        "mean": None,
        "reported_trials": 0,
        "missing_trials": 1,
    }


def test_collection_uses_test_recovery_and_preserves_missing_reward(tmp_path):
    trial = tmp_path / "trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({
        "finished_at": "2026-10-08T00:00:00", "verifier_result": None,
        "agent_result": {"n_cache_tokens": 0},
        "exception_info": {"exception_type": "VerifierTimeoutError"},
    }))
    (trial / "verifier/belta_results.json").write_text(json.dumps({
        "reward": 0, "patch_code_similarity": 0.8,
        "tests": {"metrics": {"regression_gated_recovery": 0.25}},
        "deletions": {"gold_coverage": 0.5},
    }))
    row, = summarize.collect(tmp_path)
    assert row["score"] == 0.25
    assert row["patch_similarity"] == 0.8
    assert row["coverage"] == 0.5
    assert row["reward"] is None
    assert row["cache_tokens"] is None
    assert row["exception"] == "VerifierTimeoutError"


@pytest.mark.parametrize("cached, expected", [(None, None), (0, 0), (12, 12)])
def test_legacy_cache_requires_usage_for_every_call(cached, expected):
    mini = {"info": {"model_stats": {"api_calls": 1}}, "messages": [
        {"role": "assistant", "extra": {"response": {"usage": {
            "prompt_tokens_details": {"cached_tokens": cached},
        }}}},
    ]}
    assert summarize.cache_total({"n_cache_tokens": 0}, {}, mini) == expected
    mini["info"]["model_stats"]["api_calls"] = 2
    assert summarize.cache_total({"n_cache_tokens": 0}, {}, mini) is None


def test_partial_cache_metadata_overrides_a_legacy_total():
    assert summarize.cache_total(
        {"n_cache_tokens": 123}, {"extra": {"cache_usage_complete": False}}, {}
    ) is None


def test_corrupt_image_download_is_rejected_before_docker(tmp_path):
    (tmp_path / "environment").mkdir()
    (tmp_path / "part").write_bytes(b"bad")
    (tmp_path / "environment/image-archive.json").write_text(
        json.dumps({"parts": [{"file": "part", "bytes": 3, "sha256": "0" * 64}]})
    )
    with pytest.raises(ValueError, match="checksum"):
        images.verify_parts(tmp_path, tmp_path)
