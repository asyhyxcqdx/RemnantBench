- The host appears to be in a China timezone or China mirror mode is enabled.
- The provided base image catalog points to China-mirror Dockerfile variants when available.
- When writing `guidance`, tell the worker to preserve or add China-friendly mirrors for apt, pip, uv, conda, npm, or other package managers when the repository needs network installs.
- When the repository does not already prescribe a mirror, guidance may directly suggest these currently configured values:
  - Docker Hub image proxy: `docker.io/... -> {{DOCKER_IO_MIRROR}}/...`
  - GHCR image proxy: `ghcr.io/... -> {{GHCR_IO_MIRROR}}/...`
  - Ubuntu apt: `UBUNTU_APT_MIRROR={{UBUNTU_APT_MIRROR}}`
  - Ubuntu ports apt: `UBUNTU_PORTS_APT_MIRROR={{UBUNTU_PORTS_APT_MIRROR}}`
  - Debian apt: `DEBIAN_APT_MIRROR={{DEBIAN_APT_MIRROR}}`
  - Debian security apt: `DEBIAN_SECURITY_APT_MIRROR={{DEBIAN_SECURITY_APT_MIRROR}}`
  - pip: `PIP_INDEX_URL={{PIP_INDEX_URL}}`
  - PyTorch CPU wheels: `TORCH_CPU_FIND_LINKS={{TORCH_CPU_FIND_LINKS}}`
  - uv package index: `UV_INDEX_URL={{UV_INDEX_URL}}`
  - uv Python bootstrap mirror: `UV_PYTHON_INSTALL_MIRROR={{UV_PYTHON_INSTALL_MIRROR}}`
  - uv network resilience: `UV_HTTP_TIMEOUT={{UV_HTTP_TIMEOUT}}`, `UV_HTTP_RETRIES={{UV_HTTP_RETRIES}}`
  - Miniconda bootstrap mirror: `MINICONDA_DIST_MIRROR={{MINICONDA_DIST_MIRROR}}`
  - npm registry: `NPM_REGISTRY={{NPM_REGISTRY}}`
  - Node distribution mirror: `NODE_DIST_MIRROR={{NODE_DIST_MIRROR}}`
  - Maven mirror: `MAVEN_MIRROR={{MAVEN_MIRROR}}`
  - RubyGems mirror: `RUBYGEMS_MIRROR={{RUBYGEMS_MIRROR}}`
- FeatureFactory may temporarily rewrite public Docker `FROM` image pulls to those Docker image proxies during host-side builds in China mirror mode; keep final artifact Dockerfiles readable and canonical unless a repository-specific CN-only Dockerfile is explicitly needed.
- For apt on Debian or Ubuntu, guidance may explicitly tell the worker to rewrite `/etc/apt/*.list` or `.sources` entries from upstream hosts to the mirror values above before `apt-get update`; use the Ubuntu ports mirror for `ports.ubuntu.com/ubuntu-ports`.
- For pip, guidance may explicitly tell the worker to export `PIP_INDEX_URL` or write it into `pip.conf`, so ordinary install commands can stay unchanged.
- For CPU-only PyTorch installs, guidance may explicitly tell the worker to keep ordinary dependencies on `PIP_INDEX_URL` and install `torch`, `torchvision`, or `torchaudio` with `--find-links "$TORCH_CPU_FIND_LINKS"` or `--find-links "{{TORCH_CPU_FIND_LINKS}}"`. Do not use the PyTorch wheel directory as the global pip index for ordinary packages.
- For uv, prefer writing a uv config file instead of relying only on environment variables. For root-based Docker builds, write `/root/.config/uv/uv.toml`; for a non-root user, write that user's `~/.config/uv/uv.toml`. A directly usable template is:
  ```toml
  python-install-mirror = "{{UV_PYTHON_INSTALL_MIRROR}}"

  [[index]]
  url = "{{UV_INDEX_URL}}"
  default = true
  ```
- This uv config-file form is especially important for `uv sync --frozen`, because exporting `UV_INDEX_URL` alone may not be enough for locked artifact URL mirror mapping.
- For slow Python bootstrap downloads, guidance may explicitly export `UV_HTTP_TIMEOUT={{UV_HTTP_TIMEOUT}}` and `UV_HTTP_RETRIES={{UV_HTTP_RETRIES}}` before running `uv python install` or `uv sync`.
- For conda-based repositories, guidance may explicitly tell the worker to use the Miniconda mirror above for bootstrap downloads and to rewrite `.condarc` or lockfile URLs to mirror-hosted `anaconda` / `conda-forge` endpoints when the repository uses conda channels.
- For npm-based repositories, guidance may explicitly tell the worker to export `NPM_CONFIG_REGISTRY=$NPM_REGISTRY` or write `registry={{NPM_REGISTRY}}` into `.npmrc`; when Node binaries are downloaded separately, guidance may also set `NODE_DIST_MIRROR` or `NODEJS_ORG_MIRROR`.
- If repository setup downloads public GitHub source, release archives, release assets, raw files, or gists, guidance should tell the worker to use the GitHub proxy prefix directly, such as `{{GITHUB_PROXY_PREFIX}}https://github.com/...` or `{{GITHUB_PROXY_PREFIX}}https://raw.githubusercontent.com/...`.
- That GitHub proxy pattern is suitable for `git clone`, `wget`, or `curl` against public `github.com`, `raw.githubusercontent.com`, `gist.github.com`, or `gist.githubusercontent.com` resources, but it is not a replacement for apt/pip/conda/npm registry mirrors.
- Do not assume SSH-key cloning works through that proxy pattern, and avoid sending tokens or other secrets through third-party proxy URLs unless authenticated HTTPS access is truly unavoidable.
- Prefer deterministic lockfile-driven installs, but avoid guidance that assumes global upstream package registries are reliably reachable from China.
