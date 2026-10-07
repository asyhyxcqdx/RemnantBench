# syntax=docker/dockerfile:1.7

FROM ubuntu:22.04

ARG PYTHON_VERSION=3.10
ARG UBUNTU_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports
ARG UBUNTU_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG UBUNTU_PORTS_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports
ARG MINICONDA_DIST_URL=https://mirrors.tuna.tsinghua.edu.cn/anaconda/miniconda
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG TORCH_CPU_FIND_LINKS=https://mirrors.aliyun.com/pytorch-wheels/cpu
ARG UV_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pypi/web/simple/
ARG UV_PYTHON_INSTALL_MIRROR=https://ghfast.top/https://github.com/astral-sh/python-build-standalone/releases/download

ENV DEBIAN_FRONTEND=noninteractive \
    CONDA_DIR=/opt/conda \
    PATH=/opt/conda/bin:$PATH \
    PIP_INDEX_URL=${PIP_INDEX_URL} \
    TORCH_CPU_FIND_LINKS=${TORCH_CPU_FIND_LINKS} \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_INDEX_URL=${UV_INDEX_URL} \
    UV_DEFAULT_INDEX=${UV_INDEX_URL} \
    UV_PYTHON_INSTALL_MIRROR=${UV_PYTHON_INSTALL_MIRROR} \
    PYTHONDONTWRITEBYTECODE=1

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|http://security.ubuntu.com/ubuntu|${UBUNTU_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://archive.ubuntu.com/ubuntu|${UBUNTU_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://security.ubuntu.com/ubuntu|${UBUNTU_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|http://ports.ubuntu.com/ubuntu-ports|${UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://ports.ubuntu.com/ubuntu-ports|${UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP}|g"

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

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|${UBUNTU_APT_MIRROR_BOOTSTRAP}|${UBUNTU_APT_MIRROR}|g" \
        -e "s|${UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP}|${UBUNTU_PORTS_APT_MIRROR}|g"

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
    printf '%s\n' \
      'channels:' \
      '  - defaults' \
      'show_channel_urls: true' \
      'default_channels:' \
      '  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/main' \
      '  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/r' \
      '  - https://mirrors.tuna.tsinghua.edu.cn/anaconda/pkgs/msys2' \
      'custom_channels:' \
      '  conda-forge: https://mirrors.tuna.tsinghua.edu.cn/anaconda/cloud' \
      > "${CONDA_DIR}/.condarc"; \
    conda install -y "python=${PYTHON_VERSION}" pip; \
    mkdir -p /root/.config/uv; \
    printf 'python-install-mirror = "%s"\n[[index]]\nurl = "%s"\ndefault = true\n' \
      "${UV_PYTHON_INSTALL_MIRROR}" "${UV_INDEX_URL}" > /root/.config/uv/uv.toml; \
    python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" --upgrade pip setuptools wheel uv

WORKDIR /workspace