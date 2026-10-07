from __future__ import annotations

import argparse
import json
from pathlib import Path

from belta.candidate_pairs import run_candidate_pair_construction
from belta.environment_construction import (
    EnvironmentPrewarmRequiredError,
    run_environment_construction,
    run_environment_prewarm,
)
from belta.featurefactory_ui import run_featurefactory_ui
from belta.repository_discovery import run_repository_discovery
from belta.retire_task_construction import run_retire_task_construction


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="belta")
    subparsers = parser.add_subparsers(dest="command", required=True)

    candidate = subparsers.add_parser("candidate")
    candidate_subparsers = candidate.add_subparsers(
        dest="candidate_command", required=True
    )

    repos_discovery = candidate_subparsers.add_parser("repos-discovery")
    repos_discovery.add_argument("--config", required=True, type=Path)

    construct_pairs = candidate_subparsers.add_parser("construct-pairs")
    construct_pairs.add_argument("--run-dir", required=True, type=Path)

    construct_environments = candidate_subparsers.add_parser("construct-environments")
    construct_environments.add_argument("--run-dir", required=True, type=Path)

    prewarm_environment = candidate_subparsers.add_parser("prewarm-environment")
    prewarm_environment.add_argument("--run-dir", required=True, type=Path)

    featurefactory_ui = candidate_subparsers.add_parser("featurefactory-ui")
    featurefactory_ui.add_argument("--run-dir", required=True, type=Path)
    featurefactory_ui.add_argument("--host", default="127.0.0.1")
    featurefactory_ui.add_argument("--port", default=18742, type=int)

    construct_retire_tasks = candidate_subparsers.add_parser("construct-retire-tasks")
    construct_retire_tasks.add_argument("--run-dir", required=True, type=Path)

    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "candidate" and args.candidate_command == "repos-discovery":
        result = run_repository_discovery(args.config)
        print(f"run_id={result.run_id}")
        print(f"run_dir={result.run_dir}")
        print(f"ff_job_id={result.ff_job_id}")
        print(f"repository_count={result.repository_count}")
        return
    if args.command == "candidate" and args.candidate_command == "construct-pairs":
        result = run_candidate_pair_construction(args.run_dir)
        print(f"run_dir={result.run_dir}")
        print(f"repo_count={result.repo_count}")
        print(f"accepted_candidates={result.accepted_candidates}")
        print(f"rejected_candidates={result.rejected_candidates}")
        return
    if (
        args.command == "candidate"
        and args.candidate_command == "construct-environments"
    ):
        try:
            result = run_environment_construction(args.run_dir, log_callback=print)
        except EnvironmentPrewarmRequiredError as exc:
            raise SystemExit(str(exc)) from None
        print(f"run_dir={result.run_dir}")
        print(f"repo_count={result.repo_count}")
        print(f"completed_environments={result.completed_environments}")
        print(f"failed_environments={result.failed_environments}")
        return
    if args.command == "candidate" and args.candidate_command == "prewarm-environment":
        result = run_environment_prewarm(args.run_dir, log_callback=print)
        print(f"run_dir={result.run_dir}")
        print(f"platform={result.platform}")
        print(f"mirror_profile={result.mirror_profile}")
        print(f"host_environment_ready={str(result.host_environment_ready).lower()}")
        print(f"planner_ready={str(result.planner_ready).lower()}")
        print(f"agent_server_image={result.agent_server_image}")
        return
    if args.command == "candidate" and args.candidate_command == "featurefactory-ui":
        run_featurefactory_ui(
            args.run_dir,
            host=args.host,
            port=args.port,
        )
        return
    if (
        args.command == "candidate"
        and args.candidate_command == "construct-retire-tasks"
    ):
        result = run_retire_task_construction(args.run_dir)
        print(f"run_dir={result.run_dir}")
        print(f"repos={result.summary['repos']}")
        print(
            "baseline_status_counts="
            + json.dumps(result.summary["baseline_status_counts"], sort_keys=True)
        )
        print(
            "pair_status_counts="
            + json.dumps(result.summary["pair_status_counts"], sort_keys=True)
        )
        print(
            "selection_counts="
            + json.dumps(result.summary["selection_counts"], sort_keys=True)
        )
        print(f"tasks={result.summary['tasks']}")
        return
    raise SystemExit("unsupported command")


if __name__ == "__main__":
    main()
