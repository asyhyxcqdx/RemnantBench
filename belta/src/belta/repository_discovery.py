from __future__ import annotations

import json
import re
import shutil
import sys
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select

from belta.config import BeltaConfig, RepoDiscoveryConfig, load_config


@dataclass(frozen=True)
class RepositoryDiscoveryResult:
    run_id: str
    run_dir: Path
    ff_job_id: str
    repository_count: int


def run_repository_discovery(config_path: Path) -> RepositoryDiscoveryResult:
    project_root = Path.cwd()
    config_path = config_path.resolve()
    config = load_config(config_path)
    run_id = _generate_run_id(config.project.run_name)
    run_dir = project_root / "data" / "runs" / run_id
    if run_dir.exists():
        raise FileExistsError(f"run directory already exists: {run_dir}")

    repo_discovery_dir = run_dir / "repo_discovery"
    repo_discovery_dir.mkdir(parents=True)
    shutil.copyfile(config_path, run_dir / "config.yaml")

    backend = FeatureFactoryStage1Backend(project_root, config)
    ff_job_id, status, repositories = backend.run()
    if status != "completed":
        _write_json(
            repo_discovery_dir / "ff_job.json",
            {
                "ff_job_id": ff_job_id,
                "status": status,
                "repository_count": len(repositories),
            },
        )
        raise RuntimeError(f"FeatureFactory Stage1 job did not complete: status={status}")

    _write_json(
        repo_discovery_dir / "ff_job.json",
        {
            "ff_job_id": ff_job_id,
            "status": status,
            "repository_count": len(repositories),
        },
    )
    _write_jsonl(repo_discovery_dir / "repositories.jsonl", repositories)
    return RepositoryDiscoveryResult(
        run_id=run_id,
        run_dir=run_dir,
        ff_job_id=ff_job_id,
        repository_count=len(repositories),
    )


class FeatureFactoryStage1Backend:
    def __init__(self, project_root: Path, config: BeltaConfig) -> None:
        self.project_root = project_root
        self.config = config
        self.featurefactory_root = _resolve_path(project_root, config.featurefactory.root)
        self.postgres_data_dir = _resolve_path(project_root, config.featurefactory.postgres_data_dir)

    def run(self) -> tuple[str, str, list[dict[str, Any]]]:
        self._install_featurefactory_import_path()

        from feature_factory.admin_runner import Stage1JobRunner
        from feature_factory.config import Settings
        from feature_factory.db import build_engine, build_session_factory, init_db
        from feature_factory.local_postgres import managed_local_postgres
        from feature_factory.models import CrawlJob, GitHubRepository, RepoDiscovery
        from feature_factory.stage1.github_client import GitHubSearchClient
        from feature_factory.stage1.schemas import CrawlFilters
        from feature_factory.stage1.service import CrawlService
        from feature_factory.stage1.token_scheduler import GitHubTokenScheduler

        ff = self.config.featurefactory
        settings = Settings(
            database_url=ff.database_url,
            github_token=SecretStr(self.config.github.token or ""),
            local_postgres_container_name=ff.postgres_container_name,
            local_postgres_data_dir=self.postgres_data_dir,
        )
        postgres_context = (
            managed_local_postgres(settings, enabled=True)
            if ff.auto_manage_postgres
            else nullcontext(None)
        )

        with postgres_context:
            engine = build_engine(settings)
            init_db(engine)
            session_factory = build_session_factory(engine)
            token_scheduler = GitHubTokenScheduler(settings)

            session = session_factory()
            client = GitHubSearchClient(settings, token_scheduler=token_scheduler)
            try:
                service = CrawlService(session, settings, client)
                filters = CrawlFilters.model_validate(_crawl_filters_payload(self.config.repo_discovery))
                job = service.create_job(
                    self.config.repo_discovery.name,
                    filters,
                    max_concurrent_partitions=self.config.repo_discovery.max_concurrent_partitions,
                )
                session.commit()
                ff_job_id = job.id
            finally:
                client.close()
                session.close()

            runner = Stage1JobRunner(
                session_factory,
                settings,
                max_workers=1,
                max_limit=max(1, settings.stage1_max_concurrent_jobs_cap),
                token_scheduler=token_scheduler,
            )
            try:
                runner.run_job(
                    ff_job_id,
                    max_concurrent_partitions=self.config.repo_discovery.max_concurrent_partitions,
                )
            finally:
                runner.shutdown(timeout_seconds=0.0)

            export_session = session_factory()
            try:
                job_row = export_session.get(CrawlJob, ff_job_id)
                if job_row is None:
                    raise RuntimeError(f"FeatureFactory crawl job disappeared: {ff_job_id}")
                rows = list(
                    export_session.execute(
                        select(GitHubRepository, RepoDiscovery)
                        .join(RepoDiscovery, RepoDiscovery.repository_id == GitHubRepository.id)
                        .where(RepoDiscovery.job_id == ff_job_id)
                        .order_by(GitHubRepository.full_name.asc())
                    )
                )
                repositories = [
                    _repository_record(repository, discovery)
                    for repository, discovery in rows
                ]
                return ff_job_id, str(job_row.status), repositories
            finally:
                export_session.close()

    def _install_featurefactory_import_path(self) -> None:
        src = self.featurefactory_root / "src"
        if not src.exists():
            raise FileNotFoundError(f"FeatureFactory src directory not found: {src}")
        value = str(src)
        if value not in sys.path:
            sys.path.insert(0, value)


def _crawl_filters_payload(discovery: RepoDiscoveryConfig) -> dict[str, Any]:
    if discovery.target_repositories:
        return {
            "target_repositories": discovery.target_repositories,
            "languages": [],
            "licenses": [],
            "keywords": [],
            "created_after": "1970-01-01T00:00:00Z",
            "created_before": None,
            "pushed_after": None,
            "pushed_before": None,
            "stars_min": None,
            "stars_max": None,
            "exclude_forks": False,
            "exclude_archived": False,
            "public_only": True,
            "sort": discovery.sort,
            "order": discovery.order,
        }
    return {
        "target_repositories": [],
        "languages": discovery.languages,
        "licenses": discovery.licenses,
        "keywords": discovery.keywords,
        "created_after": discovery.created_after,
        "created_before": discovery.created_before,
        "pushed_after": discovery.pushed_after,
        "pushed_before": discovery.pushed_before,
        "stars_min": discovery.stars_min,
        "stars_max": discovery.stars_max,
        "exclude_forks": discovery.exclude_forks,
        "exclude_archived": discovery.exclude_archived,
        "public_only": discovery.public_only,
        "sort": discovery.sort,
        "order": discovery.order,
    }


def _repository_record(repository: Any, discovery: Any) -> dict[str, Any]:
    raw_payload = dict(repository.raw_payload or {})
    html_url = str(repository.html_url or raw_payload.get("html_url") or "")
    clone_url = str(raw_payload.get("clone_url") or (f"{html_url}.git" if html_url else ""))
    return {
        "github_repo_id": repository.github_repo_id,
        "full_name": repository.full_name,
        "html_url": html_url,
        "clone_url": clone_url,
        "default_branch": repository.default_branch,
        "language": repository.primary_language,
        "license": repository.license_key,
        "stars": repository.stargazers_count,
        "forks": repository.forks_count,
        "pushed_at": _format_datetime(repository.pushed_at_github),
        "discovered_at": _format_datetime(discovery.discovered_at),
    }


def _generate_run_id(run_name: str) -> str:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"candidate_{timestamp}_{_slugify(run_name)}"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    normalized = normalized.strip("-._")
    if not normalized:
        raise ValueError("project.run_name must contain at least one slug-safe character")
    return normalized


def _resolve_path(project_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path
    return (project_root / path).resolve()


def _format_datetime(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
