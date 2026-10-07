<p align="center">
  <img src="src/feature_factory/static/feature_factory-logo-readme.png" style="height: 10em" alt="FeatureFactory logo" />
</p>

<p align="center">
  <a href="#quick-start"><img src="https://img.shields.io/badge/Ubuntu-22.04%20%7C%2024.04-E95420.svg" alt="Ubuntu"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Python-3.13%2B-3776AB.svg" alt="Python"></a>
  <a href="#quick-start"><img src="https://img.shields.io/badge/Docker-required-2496ED.svg" alt="Docker"></a>
  <a href="docs/blueprint.md"><img src="https://img.shields.io/badge/Docs-blueprint-6A5ACD.svg" alt="Blueprint"></a>
</p>

---

FeatureFactory is an agent-driven data production pipeline for scalable, end-to-end generation of feature-level coding data. It automatically produces long-horizon, multi-language datasets.

## 🚀 Quick Start

**Prerequisites:**

- [uv](https://docs.astral.sh/uv/)
- [Docker](https://www.docker.com/)

```bash
git clone --recurse-submodules https://github.com/potatoQi/FeatureFactory.git
cd FeatureFactory
git submodule update --init --recursive

uv sync --extra dev
cp .env.example .env

uv run uvicorn feature_factory.server:create_app --factory --host 127.0.0.1 --port 18741
```

To inspect an existing FeatureFactory database without recovering tasks,
cleaning runtime assets, scheduling batch work, or allowing API mutations, run
the server in observer mode:

```bash
FEATURE_FACTORY_SERVER_OBSERVER_MODE=1 \
uv run uvicorn feature_factory.server:create_app --factory --host 127.0.0.1 --port 18742
```
