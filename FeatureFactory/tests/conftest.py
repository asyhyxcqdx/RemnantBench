from __future__ import annotations

import os


os.environ["FEATURE_FACTORY_DISABLE_DOTENV"] = "1"
# Unit and API tests must not start the production image-prune loop against the
# developer's real Docker daemon. Focused lifecycle tests can enable it explicitly.
os.environ["FEATURE_FACTORY_STAGE3_RUNTIME_IMAGE_AUTO_PRUNE"] = "false"