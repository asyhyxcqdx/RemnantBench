from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from collections.abc import Callable
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from belta.config import BeltaConfig, load_config


@dataclass(frozen=True)
class EnvironmentConstructionResult:
    run_dir: Path
    repo_count: int
    completed_environments: int
    failed_environments: int


@dataclass(frozen=True)
class EnvironmentPrewarmResult:
    run_dir: Path
    platform: str
    mirror_profile: str
    host_environment_ready: bool
    planner_ready: bool
    agent_server_image: str


class EnvironmentPrewarmRequiredError(RuntimeError):
    pass


def run_environment_prewarm(
    run_dir: Path,
    *,
    log_callback: Callable[[str], None] | None = None,
) -> EnvironmentPrewarmResult:
    run_dir = run_dir.resolve()
    config_path = run_dir / "config.yaml"
    if not config_path.exists():
        raise FileNotFoundError(f"run config not found: {config_path}")

    config = load_config(config_path)
    backend = FeatureFactoryStage2Runner(Path.cwd(), config)
    status = backend.prewarm(log_callback=log_callback)
    _require_stage2_prewarm(status, run_dir)
    return _environment_prewarm_result(run_dir, status)


def run_environment_construction(
    run_dir: Path,
    *,
    log_callback: Callable[[str], None] | None = None,
) -> EnvironmentConstructionResult:
    run_dir = run_dir.resolve()
    config_path = run_dir / "config.yaml"
    accepted_path = run_dir / "candidate_pairs" / "accepted_candidates.jsonl"
    if not config_path.exists():
        raise FileNotFoundError(f"run config not found: {config_path}")
    if not accepted_path.exists():
        raise FileNotFoundError(f"accepted candidates not found: {accepted_path}")

    config = load_config(config_path)
    accepted_rows = _read_jsonl(accepted_path)
    repo_inputs = _accepted_repositories(accepted_rows)

    environments_dir = run_dir / "environments"
    environments_dir.mkdir(parents=True, exist_ok=True)

    lock_path = (
        Path.cwd()
        / "data"
        / "runtime"
        / "featurefactory"
        / "stage2"
        / "belta-environment-construction.lock"
    )
    with _exclusive_environment_construction_lock(lock_path):
        backend = FeatureFactoryStage2Runner(Path.cwd(), config)
        _require_stage2_prewarm(
            backend.prewarm(log_callback=log_callback),
            run_dir,
        )
        results = backend.export(repo_inputs, environments_dir)
    _write_jsonl(environments_dir / "environment_results.jsonl", results)

    return EnvironmentConstructionResult(
        run_dir=run_dir,
        repo_count=len(repo_inputs),
        completed_environments=sum(1 for row in results if row.get("status") == "completed"),
        failed_environments=sum(1 for row in results if row.get("status") == "failed"),
    )


class FeatureFactoryStage2Runner:
    def __init__(self, project_root: Path, config: BeltaConfig) -> None:
        self.project_root = project_root
        self.config = config
        self.featurefactory_root = _resolve_path(project_root, config.featurefactory.root)
        self.postgres_data_dir = _resolve_path(project_root, config.featurefactory.postgres_data_dir)

    def prewarm(
        self,
        *,
        log_callback: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._install_featurefactory_import_path()
        from feature_factory.stage2.image_assets import (
            prewarm_host_sdk_environment,
            prewarm_planner_agent_server_image,
            stage2_sdk_prewarm_status,
        )

        status = dict(stage2_sdk_prewarm_status())
        _log_prewarm_plan(status, log_callback)
        if not bool(dict(status.get("host_environment") or {}).get("prewarmed")):
            prewarm_host_sdk_environment(log_callback=log_callback)
        elif log_callback is not None:
            log_callback("FeatureFactory Stage2 host SDK environment already prewarmed")

        status = dict(stage2_sdk_prewarm_status())
        if not bool(dict(status.get("planner") or {}).get("prewarmed")):
            prewarm_planner_agent_server_image(
                platform_name=str(status.get("platform") or "") or None,
                log_callback=log_callback,
            )
        elif log_callback is not None:
            image_ref = _planner_image_ref(status)
            log_callback(f"FeatureFactory Stage2 planner image already prewarmed: {image_ref}")
        return dict(stage2_sdk_prewarm_status())

    def export(self, repo_inputs: list[dict[str, Any]], environments_dir: Path) -> list[dict[str, Any]]:
        self._install_featurefactory_import_path()

        from feature_factory.config import Settings
        from feature_factory.db import build_engine, build_session_factory, init_db
        from feature_factory.local_postgres import managed_local_postgres
        from feature_factory.models import GitHubRepository, Stage2Run, Stage2RunResult, Stage2RunStatus
        from feature_factory.stage2.runner import Stage2RunRunner
        from feature_factory.stage2.service import Stage2Service

        ff = self.config.featurefactory
        settings = Settings(
            database_url=ff.database_url,
            github_token=SecretStr(self.config.github.token or ""),
            local_postgres_container_name=ff.postgres_container_name,
            local_postgres_data_dir=self.postgres_data_dir,
            stage2_workspace_dir=self.project_root / "data" / "runtime" / "featurefactory" / "stage2",
            stage2_openhands_sdk_root=self.featurefactory_root / "software-agent-sdk",
        )
        postgres_context = (
            managed_local_postgres(settings, enabled=True)
            if ff.auto_manage_postgres
            else nullcontext(None)
        )

        results_by_repo_key: dict[str, dict[str, Any]] = {}
        scheduled_runs: list[dict[str, Any]] = []
        with postgres_context:
            engine = build_engine(settings)
            init_db(engine)
            session_factory = build_session_factory(engine)
            runner = Stage2RunRunner(
                session_factory,
                settings,
                max_workers=settings.stage2_default_task_max_concurrent_runs
                or settings.stage2_max_concurrent_runs,
                max_limit=settings.stage2_max_concurrent_runs_cap,
                app_instance_id="belta",
            )
            try:
                session = session_factory()
                try:
                    recovered_run_ids = _recover_interrupted_belta_stage2_runs(
                        session,
                        service=Stage2Service(session),
                        Stage2Run=Stage2Run,
                        Stage2RunStatus=Stage2RunStatus,
                    )
                    if recovered_run_ids:
                        print(
                            "Belta Environment Construction recovered interrupted "
                            f"FeatureFactory Stage2 runs: {len(recovered_run_ids)}",
                            flush=True,
                        )
                    for repo in repo_inputs:
                        repo_output_dir = environments_dir / str(repo["repo_key"])
                        repo_output_dir.mkdir(parents=True, exist_ok=True)
                        reusable_manifest = _reusable_environment_manifest(repo_output_dir, repo)
                        if reusable_manifest is not None:
                            results_by_repo_key[str(repo["repo_key"])] = reusable_manifest
                            continue
                        try:
                            repository = session.scalar(
                                select(GitHubRepository).where(
                                    GitHubRepository.github_repo_id == int(repo["github_repo_id"])
                                )
                            )
                            if repository is None:
                                result = _failed_result(repo, "ff_repository_not_found")
                                _write_json(repo_output_dir / "manifest.json", result)
                                results_by_repo_key[str(repo["repo_key"])] = result
                                continue

                            service = Stage2Service(session)
                            reusable_result = _export_reusable_stage2_run(
                                service=service,
                                repo=repo,
                                repository=repository,
                                output_dir=repo_output_dir,
                            )
                            if reusable_result is not None:
                                results_by_repo_key[str(repo["repo_key"])] = reusable_result
                                continue

                            active_run = _active_stage2_run(
                                session,
                                Stage2Run=Stage2Run,
                                Stage2RunStatus=Stage2RunStatus,
                                repository_id=repository.id,
                            )
                            if active_run is not None:
                                if str(active_run.target_commit_sha or "") != str(repo["new_commit"]):
                                    result = {
                                        **_failed_result(repo, "stage2_active_run_conflict"),
                                        "ff_repository_id": repository.id,
                                        "ff_stage2_run_id": active_run.id,
                                        "active_target_commit_sha": active_run.target_commit_sha,
                                    }
                                    _write_json(repo_output_dir / "manifest.json", result)
                                    results_by_repo_key[str(repo["repo_key"])] = result
                                    continue
                                run = active_run
                            else:
                                run = service.create_run(
                                    repository.id,
                                    trigger_kind="belta_environment",
                                    target_commit_sha=str(repo["new_commit"]),
                                    allow_existing_successful_commit=True,
                                )
                                session.commit()

                            if run.status == Stage2RunStatus.queued.value:
                                runner.schedule_run(run.id)
                            scheduled_runs.append(
                                {
                                    "repo": repo,
                                    "ff_repository_id": repository.id,
                                    "run_id": run.id,
                                    "output_dir": repo_output_dir,
                                }
                            )
                        except Exception as exc:
                            session.rollback()
                            result = _failed_result(
                                repo,
                                "environment_stage2_schedule_failed",
                                error=f"{type(exc).__name__}: {exc}",
                            )
                            _write_json(repo_output_dir / "manifest.json", result)
                            results_by_repo_key[str(repo["repo_key"])] = result
                finally:
                    session.close()

                for item in scheduled_runs:
                    result = _wait_for_stage2_run_and_export(
                        session_factory,
                        repo=item["repo"],
                        ff_repository_id=int(item["ff_repository_id"]),
                        run_id=str(item["run_id"]),
                        output_dir=Path(item["output_dir"]),
                        Stage2Run=Stage2Run,
                        Stage2RunResult=Stage2RunResult,
                        Stage2RunStatus=Stage2RunStatus,
                    )
                    results_by_repo_key[str(item["repo"]["repo_key"])] = result
            finally:
                runner.shutdown(timeout_seconds=0.0)
        return [results_by_repo_key[str(repo["repo_key"])] for repo in repo_inputs]

    def _install_featurefactory_import_path(self) -> None:
        src = self.featurefactory_root / "src"
        if not src.exists():
            raise FileNotFoundError(f"FeatureFactory src directory not found: {src}")
        value = str(src)
        if value not in sys.path:
            sys.path.insert(0, value)
        os.environ["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"] = str(
            (self.featurefactory_root / "software-agent-sdk").resolve()
        )


def _stage2_prewarm_ready(status: dict[str, Any]) -> bool:
    host = dict(status.get("host_environment") or {})
    planner = dict(status.get("planner") or {})
    return bool(host.get("prewarmed") and planner.get("prewarmed"))


def _require_stage2_prewarm(status: dict[str, Any], run_dir: Path) -> None:
    if _stage2_prewarm_ready(status):
        return
    missing: list[str] = []
    if not bool(dict(status.get("host_environment") or {}).get("prewarmed")):
        missing.append("host_sdk_environment")
    if not bool(dict(status.get("planner") or {}).get("prewarmed")):
        missing.append("planner_agent_server_image")
    command = f"uv run belta candidate prewarm-environment --run-dir {run_dir}"
    raise EnvironmentPrewarmRequiredError(
        "FeatureFactory Stage2 prewarm is not ready "
        f"(missing: {', '.join(missing)}). Run: {command}"
    )


def _planner_image_ref(status: dict[str, Any]) -> str:
    planner = dict(status.get("planner") or {})
    image = dict(planner.get("agent_server_image") or {})
    return str(image.get("image_ref") or "")


def _environment_prewarm_result(
    run_dir: Path,
    status: dict[str, Any],
) -> EnvironmentPrewarmResult:
    return EnvironmentPrewarmResult(
        run_dir=run_dir,
        platform=str(status.get("platform") or ""),
        mirror_profile=str(status.get("mirror_profile") or ""),
        host_environment_ready=bool(
            dict(status.get("host_environment") or {}).get("prewarmed")
        ),
        planner_ready=bool(dict(status.get("planner") or {}).get("prewarmed")),
        agent_server_image=_planner_image_ref(status),
    )


def _log_prewarm_plan(
    status: dict[str, Any],
    log_callback: Callable[[str], None] | None,
) -> None:
    if log_callback is None:
        return
    log_callback(
        "FeatureFactory Stage2 prewarm: "
        f"platform={status.get('platform') or ''} "
        f"mirror_profile={status.get('mirror_profile') or ''} "
        f"planner_image={_planner_image_ref(status)}"
    )


_REQUIRED_ENVIRONMENT_FILES = (
    "manifest.json",
    "planner_guidance.md",
    "Dockerfile",
    "run_script.sh",
    "collect_report.json",
    "full_report.json",
    "test_results.jsonl",
)


def _reusable_environment_manifest(output_dir: Path, repo: dict[str, Any]) -> dict[str, Any] | None:
    manifest_path = output_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if manifest.get("status") != "completed":
        return None
    if str(manifest.get("target_commit_sha") or "") != str(repo.get("new_commit") or ""):
        return None
    for filename in _REQUIRED_ENVIRONMENT_FILES:
        if not (output_dir / filename).exists():
            return None
    if not _test_results_have_current_contract(output_dir / "test_results.jsonl"):
        return None
    return manifest


def _test_results_have_current_contract(path: Path) -> bool:
    found = False
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if not stripped:
                    continue
                row = json.loads(stripped)
                if not isinstance(row, dict):
                    return False
                test_file = str(row.get("test_file") or "").strip()
                target_selector = str(row.get("target_selector") or "").strip()
                if not test_file or not target_selector:
                    return False
                found = True
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return found


def _active_stage2_run(
    session: Any,
    *,
    Stage2Run: Any,
    Stage2RunStatus: Any,
    repository_id: int,
) -> Any | None:
    return session.scalar(
        select(Stage2Run)
        .where(
            Stage2Run.repository_id == repository_id,
            Stage2Run.status.in_([Stage2RunStatus.queued.value, Stage2RunStatus.running.value]),
        )
        .order_by(Stage2Run.created_at.desc(), Stage2Run.id.desc())
        .limit(1)
    )


@contextmanager
def _exclusive_environment_construction_lock(path: Path) -> Any:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(
                "another Belta Environment Construction command is already running"
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _recover_interrupted_belta_stage2_runs(
    session: Any,
    *,
    service: Any,
    Stage2Run: Any,
    Stage2RunStatus: Any,
) -> list[str]:
    rows = list(
        session.scalars(
            select(Stage2Run)
            .where(
                Stage2Run.status.in_(
                    [Stage2RunStatus.queued.value, Stage2RunStatus.running.value]
                ),
                Stage2Run.trigger_kind == "belta_environment",
            )
            .order_by(Stage2Run.created_at.asc(), Stage2Run.id.asc())
        )
    )
    for run in rows:
        service.mark_run_interrupted(
            run.id,
            error_message=(
                "Belta Environment Construction process ended before completion; "
                "the next invocation will retry this repository"
            ),
        )
    if rows:
        session.commit()
    return [str(run.id) for run in rows]


def _export_reusable_stage2_run(
    *,
    service: Any,
    repo: dict[str, Any],
    repository: Any,
    output_dir: Path,
) -> dict[str, Any] | None:
    run = service.successful_run_for_repository_commit(
        repository.id,
        str(repo["new_commit"]),
    )
    if run is None:
        return None
    return _export_stage2_run(repo, repository, run, output_dir)


def _wait_for_stage2_run_and_export(
    session_factory: Any,
    *,
    repo: dict[str, Any],
    ff_repository_id: int,
    run_id: str,
    output_dir: Path,
    Stage2Run: Any,
    Stage2RunResult: Any,
    Stage2RunStatus: Any,
) -> dict[str, Any]:
    while True:
        session = session_factory()
        try:
            run = session.scalar(
                select(Stage2Run)
                .where(Stage2Run.id == run_id)
                .options(
                    selectinload(Stage2Run.repository),
                    selectinload(Stage2Run.test_results),
                    selectinload(Stage2Run.validation_attempts),
                )
                .limit(1)
            )
            if run is None:
                result = {
                    **_failed_result(repo, "stage2_run_disappeared"),
                    "ff_repository_id": ff_repository_id,
                    "ff_stage2_run_id": run_id,
                }
                _write_json(output_dir / "manifest.json", result)
                return result
            if run.status == Stage2RunStatus.completed.value:
                if run.result == Stage2RunResult.passed.value:
                    return _export_stage2_run(repo, run.repository, run, output_dir)
                result = {
                    **_failed_result(
                        repo,
                        "stage2_run_failed",
                        error=str(run.error_message or ""),
                    ),
                    "ff_repository_id": ff_repository_id,
                    "ff_stage2_run_id": run.id,
                    "ff_status": run.status,
                    "ff_result": run.result,
                    "ff_phase": run.phase,
                }
                _write_json(output_dir / "manifest.json", result)
                return result
        finally:
            session.close()
        time.sleep(10.0)


def _export_stage2_run(repo: dict[str, Any], repository: Any, run: Any, output_dir: Path) -> dict[str, Any]:
    latest_attempt = max(list(run.validation_attempts or []), key=lambda item: item.attempt_index, default=None)
    planner_guidance = str(run.planner_guidance or "")
    dockerfile_text = str(run.dockerfile_text or (latest_attempt.dockerfile_text if latest_attempt else "") or "")
    run_script_text = str(run.run_script_text or (latest_attempt.run_script_text if latest_attempt else "") or "")
    collect_report = dict(run.collect_report_json or (latest_attempt.collect_report_json if latest_attempt else {}) or {})
    full_report = dict(run.full_report_json or (latest_attempt.full_report_json if latest_attempt else {}) or {})

    if not dockerfile_text.strip():
        raise RuntimeError("stage2 run has no Dockerfile text")
    if not run_script_text.strip():
        raise RuntimeError("stage2 run has no run_script text")
    if not full_report:
        raise RuntimeError("stage2 run has no full_report")

    test_results = _stage2_test_results(run, full_report=full_report)
    if not test_results:
        test_results = _test_results_from_full_report(full_report)

    _write_text(output_dir / "planner_guidance.md", planner_guidance)
    _write_text(output_dir / "Dockerfile", dockerfile_text)
    _write_text(output_dir / "run_script.sh", run_script_text)
    (output_dir / "run_script.sh").chmod(0o755)
    _write_json(output_dir / "collect_report.json", collect_report)
    _write_json(output_dir / "full_report.json", full_report)
    _write_jsonl(output_dir / "test_results.jsonl", test_results)

    passed_file_count = sum(1 for row in test_results if row.get("status") == "passed")
    manifest = {
        "repo_key": repo["repo_key"],
        "full_name": repo["full_name"],
        "github_repo_id": repo["github_repo_id"],
        "ff_repository_id": repository.id,
        "target_commit_sha": repo["new_commit"],
        "target_branch": repo.get("default_branch"),
        "ff_stage2_run_id": run.id,
        "ff_status": run.status,
        "ff_result": run.result,
        "status": "completed",
        "status_code": None,
        "summary": dict(full_report.get("summary") or run.summary_json or {}),
        "test_file_count": len(test_results),
        "validation_test_universe_file_count": passed_file_count,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def _stage2_test_results(
    run: Any,
    *,
    full_report: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    report_selectors = {
        str(item.get("test_file_path") or ""): str(
            item.get("target_selector") or item.get("test_file_path") or ""
        )
        for item in list((full_report or {}).get("file_results") or [])
        if str(item.get("test_file_path") or "")
    }
    rows: list[dict[str, Any]] = []
    for row in list(run.test_results or []):
        test_file = str(row.test_file_path)
        rows.append(
            {
                "test_file": test_file,
                "target_selector": str(
                    getattr(row, "target_selector", None)
                    or report_selectors.get(test_file)
                    or test_file
                ),
                "status": row.status,
                "total_tests": row.total_tests,
                "passed_tests": row.passed_tests,
                "failed_tests": row.failed_tests,
                "error_tests": row.error_tests,
                "skipped_tests": row.skipped_tests,
                "exit_code": row.exit_code,
                "raw_result": dict(row.raw_result_json or {}),
            }
        )
    return rows


def _test_results_from_full_report(full_report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in list(full_report.get("file_results") or []):
        result = dict(item.get("result") or {})
        summary = dict(result.get("summary") or {})
        test_file = str(item.get("test_file_path") or "")
        rows.append(
            {
                "test_file": test_file,
                "target_selector": str(item.get("target_selector") or test_file),
                "status": str(result.get("status") or "unknown"),
                "total_tests": int(summary.get("collected", 0) or 0),
                "passed_tests": int(summary.get("passed", 0) or 0),
                "failed_tests": int(summary.get("failed", 0) or 0),
                "error_tests": int(summary.get("errors", 0) or 0),
                "skipped_tests": int(summary.get("skipped", 0) or 0),
                "exit_code": int(item.get("returncode", 0) or 0),
                "raw_result": result,
            }
        )
    return rows


def _accepted_repositories(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        repo_key = str(row.get("repo_key") or "").strip()
        if not repo_key:
            raise ValueError("accepted candidate row missing repo_key")
        current = {
            "repo_key": repo_key,
            "full_name": row.get("full_name"),
            "github_repo_id": row.get("github_repo_id"),
            "clone_url": row.get("clone_url"),
            "default_branch": row.get("default_branch"),
            "new_commit": row.get("new_commit"),
        }
        missing = [key for key, value in current.items() if value in (None, "")]
        if missing:
            raise ValueError(f"accepted candidate row missing required fields: {missing}")
        existing = grouped.get(repo_key)
        if existing is None:
            grouped[repo_key] = current
            continue
        if existing["new_commit"] != current["new_commit"]:
            raise ValueError(
                f"repo has multiple new_commit values in accepted candidates: {repo_key}"
            )
    return [grouped[key] for key in sorted(grouped)]


def _failed_result(repo: dict[str, Any], status_code: str, *, error: str | None = None) -> dict[str, Any]:
    result = {
        "repo_key": repo["repo_key"],
        "full_name": repo["full_name"],
        "github_repo_id": repo["github_repo_id"],
        "target_commit_sha": repo["new_commit"],
        "target_branch": repo.get("default_branch"),
        "status": "failed",
        "status_code": status_code,
    }
    if error:
        result["error"] = error
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _resolve_path(project_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return (project_root / path).resolve()
