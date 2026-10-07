from __future__ import annotations

import os
import subprocess
from pathlib import Path

from belta.config import BeltaConfig, load_config


def run_featurefactory_ui(
    run_dir: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 18742,
) -> None:
    project_root = Path.cwd().resolve()
    run_dir = run_dir.resolve()
    config = load_config(run_dir / "config.yaml")
    featurefactory_root, environment = _featurefactory_ui_environment(
        project_root,
        config,
    )

    print(f"run_dir={run_dir}", flush=True)
    print(f"featurefactory_root={featurefactory_root}", flush=True)
    print("mode=observer", flush=True)
    print(f"url=http://{host}:{port}", flush=True)
    completed = subprocess.run(
        [
            "uv",
            "run",
            "uvicorn",
            "feature_factory.server:create_app",
            "--factory",
            "--host",
            host,
            "--port",
            str(port),
        ],
        cwd=featurefactory_root,
        env=environment,
        check=False,
    )
    if completed.returncode:
        raise SystemExit(completed.returncode)


def _featurefactory_ui_environment(
    project_root: Path,
    config: BeltaConfig,
) -> tuple[Path, dict[str, str]]:
    featurefactory = config.featurefactory
    featurefactory_root = _resolve_path(project_root, featurefactory.root)
    postgres_data_dir = _resolve_path(
        project_root,
        featurefactory.postgres_data_dir,
    )
    environment = dict(os.environ)
    environment.update(
        {
            "FEATURE_FACTORY_DISABLE_DOTENV": "1",
            "FEATURE_FACTORY_SERVER_OBSERVER_MODE": "1",
            "FEATURE_FACTORY_DATABASE_URL": featurefactory.database_url,
            "FEATURE_FACTORY_LOCAL_POSTGRES_CONTAINER_NAME": (
                featurefactory.postgres_container_name
            ),
            "FEATURE_FACTORY_LOCAL_POSTGRES_DATA_DIR": str(postgres_data_dir),
            "FEATURE_FACTORY_STAGE2_WORKSPACE_DIR": str(
                project_root / "data" / "runtime" / "featurefactory" / "stage2"
            ),
            "FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT": str(
                featurefactory_root / "software-agent-sdk"
            ),
            "FEATURE_FACTORY_GITHUB_TOKEN": config.github.token or "",
        }
    )
    return featurefactory_root, environment


def _resolve_path(project_root: Path, path: Path) -> Path:
    return path.resolve() if path.is_absolute() else (project_root / path).resolve()
