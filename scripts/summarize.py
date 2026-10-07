#!/usr/bin/env python3
"""Export completed Harbor trials; preserve missing metrics as unknown."""

import argparse
import csv
import json
import statistics
from pathlib import Path


def read_optional(path: Path) -> dict:
    return json.loads(path.read_text()) if path.is_file() else {}


def cache_total(context: dict, final: dict, mini: dict) -> int | None:
    """Require completeness evidence, including for legacy default-zero totals."""
    reports = [context.get("metadata") or {}, final.get("extra") or {}]
    if any(report.get("cache_usage_complete") is False for report in reports):
        return None
    if any(report.get("cache_usage_complete") is True for report in reports):
        value = context.get("n_cache_tokens")
        return value if value is not None else final.get("total_cached_tokens")

    queries = ((mini.get("info") or {}).get("model_stats") or {}).get("api_calls")
    values = []
    for message in mini.get("messages", []):
        if message.get("role") != "assistant":
            continue
        usage = ((message.get("extra") or {}).get("response") or {}).get("usage") or {}
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        value = details.get("cached_tokens", usage.get("prompt_cache_hit_tokens"))
        if value is None:
            return None
        values.append(value)
    return sum(values) if queries is not None and len(values) == queries else None


def collect(job: Path) -> list[dict]:
    rows = []
    for path in sorted(job.glob("*/result.json")):
        result = json.loads(path.read_text())
        if not result.get("finished_at"):
            continue
        trial = path.parent
        details = read_optional(trial / "verifier/belta_results.json")
        patch = read_optional(trial / "verifier/belta_patch_metrics.json")
        context = result.get("agent_result") or {}
        trajectory = read_optional(trial / "agent/trajectory.json")
        mini = read_optional(trial / "agent/mini-swe-agent.trajectory.json")
        final = trajectory.get("final_metrics") or {}
        info = mini.get("info") or {}
        rewards = (result.get("verifier_result") or {}).get("rewards") or {}
        deletions = details.get("deletions") or patch.get("deletions") or {}
        rows.append(
            {
                "task": result.get("task_name"),
                "trial": trial.name,
                "reward": details.get("reward", rewards.get("reward")),
                "raw_harbor_reward": rewards.get("reward"),
                "score": ((details.get("tests") or {}).get("metrics") or {}).get(
                    "regression_gated_recovery"
                ),
                "patch_similarity": details.get(
                    "patch_code_similarity", patch.get("patch_code_similarity")
                ),
                "coverage": deletions.get("gold_coverage"),
                "agent_queries": (info.get("model_stats") or {}).get("api_calls"),
                "atif_steps": final.get("total_steps"),
                "input_tokens": context.get(
                    "n_input_tokens", final.get("total_prompt_tokens")
                ),
                "output_tokens": context.get(
                    "n_output_tokens", final.get("total_completion_tokens")
                ),
                "cache_tokens": cache_total(context, final, mini),
                "reported_cost_usd": context.get(
                    "cost_usd", final.get("total_cost_usd")
                ),
                "exit_status": info.get("exit_status"),
                "exception": (result.get("exception_info") or {}).get("exception_type"),
            }
        )
    return rows


def aggregate(rows: list[dict]) -> dict:
    summary = {"completed_trials": len(rows)}
    for key in ("reward", "score", "patch_similarity", "coverage", "agent_queries"):
        values = [row[key] for row in rows if row.get(key) is not None]
        summary[key] = {
            "mean": statistics.mean(values) if values else None,
            "reported_trials": len(values),
            "missing_trials": len(rows) - len(values),
        }
    for key in ("input_tokens", "output_tokens", "cache_tokens", "reported_cost_usd"):
        values = [row[key] for row in rows if row[key] is not None]
        summary[key] = {
            "total": sum(values) if values and len(values) == len(rows) else None,
            "reported_subtotal": sum(values),
            "missing_trials": len(rows) - len(values),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "job", type=Path, help="One model/experiment Harbor job directory"
    )
    parser.add_argument("--csv", type=Path)
    args = parser.parse_args()
    if not args.job.is_dir():
        parser.error("Job directory does not exist")
    rows = collect(args.job)
    if args.csv and rows:
        with args.csv.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(aggregate(rows), indent=2))


if __name__ == "__main__":
    main()
