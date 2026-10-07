#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

import yaml


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a Belta candidate run.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Belta project root. Defaults to current working directory.",
    )
    args = parser.parse_args()

    run_dir = args.run_dir.resolve()
    project_root = args.project_root.resolve()
    config = _read_yaml(run_dir / "config.yaml")
    repos = _read_jsonl(run_dir / "repo_discovery" / "repositories.jsonl")
    candidate_config = config.get("candidate_pairs", {})

    offset_ranges = candidate_config.get("offset_ranges") or [
        {"start": 1, "stop": 200, "step": 1}
    ]
    offset_count = sum(
        len(
            range(
                int(offset_range["start"]),
                int(offset_range["stop"]) + 1,
                int(offset_range.get("step", 1)),
            )
        )
        for offset_range in offset_ranges
    )
    offset_summary = "; ".join(
        f"{offset_range['start']}..{offset_range['stop']} "
        f"step {offset_range.get('step', 1)}"
        for offset_range in offset_ranges
    )
    max_pair_attempts = len(repos) * offset_count

    cache_counts = Counter()
    for repo in repos:
        cache_dir = _cache_dir(project_root, repo)
        if cache_dir.exists():
            cache_counts["exists"] += 1
            if _is_bare_git_repo(cache_dir):
                cache_counts["bare_ok"] += 1
            else:
                cache_counts["bad_or_incomplete"] += 1
        else:
            cache_counts["missing"] += 1

    candidate_pairs_dir = run_dir / "candidate_pairs"
    repo_outputs_dir = candidate_pairs_dir / "repo_outputs"
    pair_outputs_dir = candidate_pairs_dir / "pair_outputs"
    repo_results = _read_jsonl(candidate_pairs_dir / "repo_results.jsonl")
    accepted = _read_jsonl(candidate_pairs_dir / "accepted_candidates.jsonl")
    rejected = _read_jsonl(candidate_pairs_dir / "rejected_candidates.jsonl")

    repo_output_dirs = [p for p in repo_outputs_dir.iterdir() if p.is_dir()] if repo_outputs_dir.exists() else []
    pair_output_dirs = [p for p in pair_outputs_dir.iterdir() if p.is_dir()] if pair_outputs_dir.exists() else []
    obsolete_patch_count = sum(1 for p in pair_output_dirs if (p / "obsolete_reinsert.patch").exists())

    print(f"run_dir={run_dir}")
    print(f"repositories={len(repos)}")
    print(f"offset_ranges={offset_summary} ({offset_count} per repo max)")
    print(f"max_pair_attempts={max_pair_attempts}")
    print(
        "cache="
        f"exists:{cache_counts['exists']} "
        f"bare_ok:{cache_counts['bare_ok']} "
        f"bad_or_incomplete:{cache_counts['bad_or_incomplete']} "
        f"missing:{cache_counts['missing']}"
    )

    if not candidate_pairs_dir.exists():
        print("candidate_pairs=not_started")
        return

    print(f"repo_outputs={len(repo_output_dirs)}/{len(repos)}")
    if repo_results:
        status_counts = Counter(row.get("status") for row in repo_results)
        status_code_counts = Counter(row.get("status_code") for row in repo_results)
        print(f"repo_status={dict(status_counts)}")
        print(f"repo_status_code={dict(status_code_counts)}")

    print(f"pair_outputs={len(pair_output_dirs)}")
    print(f"obsolete_reinsert_patches={obsolete_patch_count}")
    print(f"accepted_candidates={len(accepted)}")
    print(f"rejected_candidates={len(rejected)}")

    compile_counts = Counter()
    for row in [*accepted, *rejected]:
        compile_counts[row.get("task_base_compile")] += 1
    if compile_counts:
        print(f"task_base_compile={dict(compile_counts)}")

    reject_counts = Counter(row.get("reject_reason") for row in rejected)
    if reject_counts:
        print(f"reject_reasons={dict(reject_counts)}")

    if accepted:
        by_repo = Counter(row.get("full_name") for row in accepted)
        print("accepted_by_repo_top10=")
        for repo_name, count in by_repo.most_common(10):
            print(f"  {count}\t{repo_name}")


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _cache_dir(project_root: Path, repo: dict[str, Any]) -> Path:
    full_name = str(repo.get("full_name") or "")
    if "/" not in full_name:
        return project_root / "data" / "cache" / "repos" / "unknown.git"
    owner, name = full_name.split("/", 1)
    return project_root / "data" / "cache" / "repos" / owner / f"{name}.git"


def _is_bare_git_repo(path: Path) -> bool:
    try:
        output = subprocess.check_output(
            ["git", f"--git-dir={path}", "rev-parse", "--is-bare-repository"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except subprocess.CalledProcessError:
        return False
    return output.strip() == "true"


if __name__ == "__main__":
    main()
