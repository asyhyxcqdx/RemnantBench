#!/usr/bin/env python3
"""Run a frozen RemnantBench evaluation using named presets and CLI overrides."""

import argparse
import copy
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENTS = ("Full200", "Lite40", "Lite40-file-hints", "Lite40-neutral-repair")


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        required=True,
        help="Preset name, or provider/model for a custom model",
    )
    parser.add_argument("--experiment", choices=EXPERIMENTS, default="Full200")
    parser.add_argument("--env-file", type=Path, default=Path("model.env"))
    parser.add_argument(
        "--base-url", help="Override API URL and the agent network allowlist host"
    )
    parser.add_argument(
        "--provider-model", help="Override the provider/model ID of a named preset"
    )
    parser.add_argument(
        "--reasoning-effort", help="Override the preset's reasoning effort"
    )
    parser.add_argument("--model-class", choices=("litellm", "litellm_response"))
    parser.add_argument(
        "--responses-system-as-instructions",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--step-limit", type=positive_int, default=150)
    parser.add_argument("--concurrency", type=positive_int, default=1)
    parser.add_argument(
        "--request-timeout", type=positive_int, help="API request timeout in seconds"
    )
    parser.add_argument(
        "--task",
        action="append",
        default=[],
        help="Exact task ID or Harbor glob; repeatable",
    )
    parser.add_argument("--dataset-root", type=Path, default=ROOT / "datasets")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "belta-step150")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the effective command without Docker or API calls",
    )
    return parser


def build_command(
    args: argparse.Namespace, root: Path = ROOT
) -> tuple[list[str], Path]:
    presets = json.loads((root / "configs/models.json").read_text())
    spec: dict[str, Any]
    if args.model in presets:
        spec = copy.deepcopy(presets[args.model])
    elif "/" in args.model:
        spec = {
            "model": args.model,
            "mini_config": {},
            "retry_transient_api_errors": True,
        }
    else:
        raise ValueError("Unknown model preset; use a listed preset or provider/model")
    if args.provider_model:
        spec["model"] = args.provider_model
    if "/" not in spec["model"]:
        raise ValueError("Model ID must use provider/model syntax")
    config = spec["mini_config"]
    config.setdefault("agent", {})["step_limit"] = args.step_limit
    model = config.setdefault("model", {})
    options = model.setdefault("model_kwargs", {})
    if args.model_class:
        model["model_class"] = args.model_class
    if args.responses_system_as_instructions is not None:
        spec["responses_system_as_instructions"] = args.responses_system_as_instructions
    if spec.get("responses_system_as_instructions"):
        if args.model_class == "litellm":
            raise ValueError("System instructions transport requires litellm_response")
        model["model_class"] = "litellm_response"
    if args.reasoning_effort:
        if model.get("model_class") == "litellm_response":
            options.pop("reasoning_effort", None)
            options.setdefault("reasoning", {})["effort"] = args.reasoning_effort
        else:
            options.pop("reasoning", None)
            options["reasoning_effort"] = args.reasoning_effort
    if args.request_timeout:
        options["timeout"] = args.request_timeout
    base_url = args.base_url or options.get("api_base")
    if base_url:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("API base URL must be HTTP(S) and contain no credentials")
        options["api_base"] = base_url
    if not options:
        model.pop("model_kwargs", None)
    if not model:
        config.pop("model", None)
    dataset = args.dataset_root.expanduser().resolve() / args.experiment
    if not any(dataset.glob("*/task.toml")):
        raise ValueError(
            f"Dataset is missing: {dataset}. Download and extract the data archive first."
        )
    model_folder = re.sub(r"[^A-Za-z0-9_.-]+", "__", args.model)
    if model_folder in {".", "..", ""}:
        raise ValueError("Invalid model output directory")
    output = args.output_dir.expanduser().resolve() / model_folder / args.experiment
    command = [
        str(root / "harbor/.venv/bin/python"),
        "-m",
        "adapters.belta.run_harbor",
        "--path",
        str(dataset),
        "--env-file",
        str(args.env_file.expanduser().resolve()),
        "--model",
        spec["model"],
        "--n-concurrent",
        str(args.concurrency),
        "--job-name",
        args.experiment,
    ]
    if base_url:
        command.extend(["--base-url", base_url])
    for task in args.task:
        command.extend(["--include-task-name", task])
    command.extend(
        [
            "--",
            "--jobs-dir",
            str(output.parent),
            "--max-retries",
            "0",
            "--ak",
            "config=" + json.dumps(config, separators=(",", ":")),
        ]
    )
    for flag in (
        "retry_transient_api_errors",
        "retry_service_failures",
        "responses_system_as_instructions",
    ):
        if flag in spec:
            command.extend(["--ak", f"{flag}={str(spec[flag]).lower()}"])
    return command, output


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        command, output = build_command(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(shlex.join(command))
        return
    if not Path(command[0]).is_file():
        parser.error(
            "Install the evaluator first: cd harbor && uv sync --frozen --no-dev"
        )
    if not args.env_file.expanduser().is_file():
        parser.error("Create the private --env-file from configs/api.env.example first")
    if output.exists():
        parser.error(
            f"Output exists: {output}. Resume with Harbor jobs resume, or choose another --output-dir."
        )
    environment = os.environ.copy()
    environment["PATH"] = (
        str(ROOT / "harbor/.venv/bin") + os.pathsep + environment.get("PATH", "")
    )
    raise SystemExit(
        subprocess.run(
            command, cwd=ROOT / "harbor", env=environment, check=False
        ).returncode
    )


if __name__ == "__main__":
    main()
