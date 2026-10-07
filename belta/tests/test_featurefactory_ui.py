from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from belta.featurefactory_ui import _featurefactory_ui_environment


class FeatureFactoryUiTests(unittest.TestCase):
    def test_environment_uses_the_run_featurefactory_database_and_workspace(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "belta"
            root.mkdir()
            config = SimpleNamespace(
                featurefactory=SimpleNamespace(
                    root=Path("../FeatureFactory"),
                    database_url=(
                        "postgresql+psycopg://belta_ff:belta_ff@"
                        "127.0.0.1:55433/belta_ff"
                    ),
                    postgres_container_name="belta-ff-postgres",
                    postgres_data_dir=Path("data/cache/featurefactory/postgres"),
                ),
                github=SimpleNamespace(token="github-token"),
            )

            with patch.dict(os.environ, {}, clear=True):
                featurefactory_root, environment = _featurefactory_ui_environment(
                    root,
                    config,
                )

            self.assertEqual(featurefactory_root, root.parent / "FeatureFactory")
            self.assertEqual(
                environment["FEATURE_FACTORY_DATABASE_URL"],
                config.featurefactory.database_url,
            )
            self.assertEqual(
                environment["FEATURE_FACTORY_LOCAL_POSTGRES_CONTAINER_NAME"],
                "belta-ff-postgres",
            )
            self.assertEqual(
                environment["FEATURE_FACTORY_LOCAL_POSTGRES_DATA_DIR"],
                str(root / "data" / "cache" / "featurefactory" / "postgres"),
            )
            self.assertEqual(
                environment["FEATURE_FACTORY_STAGE2_WORKSPACE_DIR"],
                str(root / "data" / "runtime" / "featurefactory" / "stage2"),
            )
            self.assertEqual(
                environment["FEATURE_FACTORY_STAGE2_OPENHANDS_SDK_ROOT"],
                str(root.parent / "FeatureFactory" / "software-agent-sdk"),
            )
            self.assertEqual(
                environment["FEATURE_FACTORY_GITHUB_TOKEN"],
                "github-token",
            )
            self.assertEqual(environment["FEATURE_FACTORY_DISABLE_DOTENV"], "1")
            self.assertEqual(
                environment["FEATURE_FACTORY_SERVER_OBSERVER_MODE"],
                "1",
            )


if __name__ == "__main__":
    unittest.main()
