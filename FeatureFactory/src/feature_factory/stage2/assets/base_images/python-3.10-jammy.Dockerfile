# syntax=docker/dockerfile:1.7

FROM ubuntu:22.04

ARG PYTHON_VERSION=3.10
ARG MINICONDA_DIST_URL=https://repo.anaconda.com/miniconda

ENV DEBIAN_FRONTEND=noninteractive \
    CONDA_DIR=/opt/conda \
    PATH=/opt/conda/bin:$PATH \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN --mount=type=cache,id=feature-factory-apt-cache-jammy,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=feature-factory-apt-lists-jammy,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
      apt-transport-https \
      bash \
      build-essential \
      ca-certificates \
      cmake \
      coreutils \
      curl \
      file \
      findutils \
      gfortran \
      git \
      jq \
      libbz2-dev \
      libffi-dev \
      liblapack-dev \
      liblzma-dev \
      libncursesw5-dev \
      libopenblas-dev \
      libreadline-dev \
      libsqlite3-dev \
      libssl-dev \
      libxml2-dev \
      libxmlsec1-dev \
      ninja-build \
      patch \
      pkg-config \
      procps \
      rsync \
      sed \
      tar \
      unzip \
      wget \
      xz-utils \
      zlib1g-dev

RUN --mount=type=cache,id=feature-factory-miniconda-installers,target=/var/cache/feature-factory/miniconda,sharing=locked \
    --mount=type=cache,id=feature-factory-conda-pkgs,target=/var/cache/feature-factory/conda/pkgs,sharing=locked \
    set -eux; \
    export CONDA_PKGS_DIRS=/var/cache/feature-factory/conda/pkgs; \
    case "$(uname -m)" in \
      x86_64) miniconda_arch="x86_64" ;; \
      aarch64|arm64) miniconda_arch="aarch64" ;; \
      *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
    esac; \
    installer_path="/var/cache/feature-factory/miniconda/Miniconda3-latest-Linux-${miniconda_arch}.sh"; \
    installer_url="${MINICONDA_DIST_URL}/Miniconda3-latest-Linux-${miniconda_arch}.sh"; \
    download_miniconda_installer() { \
      local installer_tmp="${installer_path}.download.$$"; \
      rm -f "${installer_tmp}"; \
      if ! curl -fsSL --retry 5 --retry-delay 2 \
        "${installer_url}" -o "${installer_tmp}"; then \
        rm -f "${installer_tmp}"; \
        return 1; \
      fi; \
      mv -f "${installer_tmp}" "${installer_path}"; \
    }; \
    install_miniconda() { \
      rm -rf "${CONDA_DIR}"; \
      bash "${installer_path}" -b -p "${CONDA_DIR}"; \
    }; \
    if [ ! -s "${installer_path}" ]; then \
      download_miniconda_installer; \
    fi; \
    if ! install_miniconda; then \
      echo "Cached Miniconda installer failed; downloading a fresh copy" >&2; \
      rm -f "${installer_path}"; \
      download_miniconda_installer; \
      if ! install_miniconda; then \
        rm -f "${installer_path}"; \
        exit 1; \
      fi; \
    fi; \
    conda install -y "python=${PYTHON_VERSION}" pip; \
    python -m pip install --no-cache-dir --upgrade pip setuptools wheel uv

WORKDIR /workspace