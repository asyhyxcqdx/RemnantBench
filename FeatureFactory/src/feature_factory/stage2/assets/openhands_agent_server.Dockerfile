# syntax=docker/dockerfile:1.7

# FeatureFactory's headless OpenHands agent-server image.
#
# The upstream OpenHands SDK Dockerfile's `source` target includes VSCode Web,
# VNC, Chromium, Docker Engine, GitHub CLI, and ACP CLI packages. Stage2 runs
# planner/worker agents in CLI mode and only needs the REST agent server plus
# terminal/file tools, so this image intentionally keeps the runtime minimal.

ARG AGENT_PYTHON_IMAGE=python:3.13-bookworm
ARG BASE_IMAGE=nikolaik/python-nodejs:python3.13-nodejs22-slim
ARG USERNAME=openhands
ARG UID=10001
ARG GID=10001
ARG PORT=8000

FROM ${AGENT_PYTHON_IMAGE} AS builder
ARG USERNAME
ARG UID
ARG GID
ARG PIP_INDEX_URL=
ARG TORCH_CPU_FIND_LINKS=
ARG UV_INDEX_URL=
ARG UV_PYTHON_INSTALL_MIRROR=
ARG UV_PYTHON_INSTALL_MIRROR_FALLBACKS=
ARG UV_HTTP_TIMEOUT=300
ARG UV_HTTP_RETRIES=8

ENV UV_PROJECT_ENVIRONMENT=/agent-server/.venv
ENV UV_PYTHON_INSTALL_DIR=/agent-server/uv-managed-python

RUN if [ -n "${PIP_INDEX_URL}" ]; then \
      python -m pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" uv==0.11.6; \
    else \
      python -m pip install --no-cache-dir uv==0.11.6; \
    fi

RUN groupadd -g ${GID} ${USERNAME} \
 && useradd -m -u ${UID} -g ${GID} -s /usr/sbin/nologin ${USERNAME} \
 && mkdir -p /agent-server/uv-managed-python \
 && chown -R ${USERNAME}:${USERNAME} /agent-server

USER ${USERNAME}
WORKDIR /agent-server

COPY --chown=${USERNAME}:${USERNAME} pyproject.toml uv.lock README.md LICENSE ./
COPY --chown=${USERNAME}:${USERNAME} openhands-sdk ./openhands-sdk
COPY --chown=${USERNAME}:${USERNAME} openhands-tools ./openhands-tools
COPY --chown=${USERNAME}:${USERNAME} openhands-workspace ./openhands-workspace
COPY --chown=${USERNAME}:${USERNAME} openhands-agent-server ./openhands-agent-server

RUN --mount=type=cache,target=/home/${USERNAME}/.cache,uid=${UID},gid=${GID} \
    export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-300}" UV_REQUEST_TIMEOUT="${UV_HTTP_TIMEOUT:-300}" UV_HTTP_RETRIES="${UV_HTTP_RETRIES:-8}"; \
    mkdir -p "/home/${USERNAME}/.config/uv"; \
    if [ -n "${UV_PYTHON_INSTALL_MIRROR}" ] || [ -n "${UV_INDEX_URL}" ] || [ -n "${PIP_INDEX_URL}" ]; then \
      { \
        if [ -n "${UV_PYTHON_INSTALL_MIRROR}" ]; then \
          printf 'python-install-mirror = "%s"\n' "${UV_PYTHON_INSTALL_MIRROR}"; \
        fi; \
        if [ -n "${UV_INDEX_URL}" ]; then \
          printf '[[index]]\nurl = "%s"\ndefault = true\n' "${UV_INDEX_URL}"; \
        elif [ -n "${PIP_INDEX_URL}" ]; then \
          printf '[[index]]\nurl = "%s"\ndefault = true\n' "${PIP_INDEX_URL}"; \
        fi; \
      } > "/home/${USERNAME}/.config/uv/uv.toml"; \
    else \
      rm -f "/home/${USERNAME}/.config/uv/uv.toml"; \
    fi; \
    if [ -n "${UV_INDEX_URL}" ]; then \
      export UV_DEFAULT_INDEX="${UV_INDEX_URL}" UV_INDEX_URL="${UV_INDEX_URL}"; \
    elif [ -n "${PIP_INDEX_URL}" ]; then \
      export UV_DEFAULT_INDEX="${PIP_INDEX_URL}" UV_INDEX_URL="${PIP_INDEX_URL}"; \
    fi; \
    install_python() { \
      if [ -n "${UV_PYTHON_INSTALL_MIRROR}" ] || [ -n "${UV_PYTHON_INSTALL_MIRROR_FALLBACKS}" ]; then \
        for python_mirror in ${UV_PYTHON_INSTALL_MIRROR} ${UV_PYTHON_INSTALL_MIRROR_FALLBACKS}; do \
          if [ -z "${python_mirror}" ]; then continue; fi; \
          rm -rf "${UV_PYTHON_INSTALL_DIR}/.temp"; \
          if uv python install --no-cache 3.13 --mirror "${python_mirror}"; then return 0; fi; \
        done; \
      fi; \
      rm -rf "${UV_PYTHON_INSTALL_DIR}/.temp"; \
      uv python install --no-cache 3.13; \
    }; \
    install_python && \
    uv venv --python-preference only-managed --python 3.13 .venv && \
    uv sync --frozen --no-editable --managed-python --extra boto3 && \
    readlink -f .venv/bin/python | grep -q '^/agent-server/uv-managed-python/'

FROM ${BASE_IMAGE} AS agent-server
ARG USERNAME
ARG UID
ARG GID
ARG PORT
ARG OPENHANDS_BUILD_GIT_SHA=unknown
ARG OPENHANDS_BUILD_GIT_REF=unknown
ARG DEBIAN_APT_MIRROR=
ARG DEBIAN_SECURITY_APT_MIRROR=
ARG DEBIAN_APT_MIRROR_BOOTSTRAP=
ARG DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP=
ARG UBUNTU_APT_MIRROR=
ARG UBUNTU_PORTS_APT_MIRROR=
ARG UBUNTU_APT_MIRROR_BOOTSTRAP=
ARG UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP=
ARG PIP_INDEX_URL=
ARG TORCH_CPU_FIND_LINKS=
ARG UV_INDEX_URL=
ARG UV_PYTHON_INSTALL_MIRROR=
ARG UV_PYTHON_INSTALL_MIRROR_FALLBACKS=
ARG UV_HTTP_TIMEOUT=300
ARG UV_HTTP_RETRIES=8
ARG NPM_REGISTRY=
ARG NODE_DIST_MIRROR=
ARG MINICONDA_DIST_MIRROR=

ENV OPENHANDS_BUILD_GIT_SHA=${OPENHANDS_BUILD_GIT_SHA}
ENV OPENHANDS_BUILD_GIT_REF=${OPENHANDS_BUILD_GIT_REF}
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV OH_ENABLE_VNC=false
ENV LOG_JSON=true
ENV FEATURE_FACTORY_OPENHANDS_UMASK=0000
ENV CONDA_DIR=/opt/conda
ENV PATH=/opt/conda/bin:$PATH
LABEL feature_factory.openhands_llm_tls_scope="per-request"

USER root
SHELL ["/bin/bash", "-euo", "pipefail", "-c"]

RUN --mount=type=cache,id=feature-factory-apt-cache-agent-server,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=feature-factory-apt-lists-agent-server,target=/var/lib/apt/lists,sharing=locked \
    set -eux; \
    if [ -d /etc/apt ]; then \
      replace_apt_source() { \
        local from="$1"; \
        local to="$2"; \
        if [ -n "$to" ]; then \
          find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 | \
            xargs -0 -r sed -i -e "s|${from}|${to}|g"; \
        fi; \
      }; \
      debian_apt_bootstrap="${DEBIAN_APT_MIRROR_BOOTSTRAP:-${DEBIAN_APT_MIRROR}}"; \
      debian_security_bootstrap="${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP:-${DEBIAN_SECURITY_APT_MIRROR}}"; \
      ubuntu_apt_bootstrap="${UBUNTU_APT_MIRROR_BOOTSTRAP:-${UBUNTU_APT_MIRROR}}"; \
      ubuntu_ports_bootstrap="${UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP:-${UBUNTU_PORTS_APT_MIRROR}}"; \
      replace_apt_source "http://deb.debian.org/debian-security" "${debian_security_bootstrap}"; \
      replace_apt_source "https://deb.debian.org/debian-security" "${debian_security_bootstrap}"; \
      replace_apt_source "http://security.debian.org/debian-security" "${debian_security_bootstrap}"; \
      replace_apt_source "https://security.debian.org/debian-security" "${debian_security_bootstrap}"; \
      replace_apt_source "http://deb.debian.org/debian" "${debian_apt_bootstrap}"; \
      replace_apt_source "https://deb.debian.org/debian" "${debian_apt_bootstrap}"; \
      replace_apt_source "http://archive.ubuntu.com/ubuntu" "${ubuntu_apt_bootstrap}"; \
      replace_apt_source "https://archive.ubuntu.com/ubuntu" "${ubuntu_apt_bootstrap}"; \
      replace_apt_source "http://security.ubuntu.com/ubuntu" "${ubuntu_apt_bootstrap}"; \
      replace_apt_source "https://security.ubuntu.com/ubuntu" "${ubuntu_apt_bootstrap}"; \
      replace_apt_source "http://ports.ubuntu.com/ubuntu-ports" "${ubuntu_ports_bootstrap}"; \
      replace_apt_source "https://ports.ubuntu.com/ubuntu-ports" "${ubuntu_ports_bootstrap}"; \
      apt-get update; \
      apt-get install -y --no-install-recommends \
        ca-certificates curl wget sudo apt-utils git jq tmux build-essential \
        cmake ninja-build gfortran pkg-config patch rsync unzip xz-utils file \
        libbz2-dev libffi-dev liblapack-dev liblzma-dev libncursesw5-dev \
        libopenblas-dev libreadline-dev libsqlite3-dev libssl-dev libxml2-dev \
        libxmlsec1-dev zlib1g-dev \
        coreutils util-linux procps findutils grep sed \
        apt-transport-https gnupg lsb-release; \
      replace_apt_source "${debian_security_bootstrap}" "${DEBIAN_SECURITY_APT_MIRROR}"; \
      replace_apt_source "${debian_apt_bootstrap}" "${DEBIAN_APT_MIRROR}"; \
      replace_apt_source "${ubuntu_ports_bootstrap}" "${UBUNTU_PORTS_APT_MIRROR}"; \
      replace_apt_source "${ubuntu_apt_bootstrap}" "${UBUNTU_APT_MIRROR}"; \
    fi; \
    if ! getent group "${GID}" >/dev/null; then groupadd -g "${GID}" "${USERNAME}"; fi; \
    if ! id -u "${USERNAME}" >/dev/null 2>&1; then \
      useradd -m -u "${UID}" -g "${GID}" -s /bin/bash "${USERNAME}"; \
    fi; \
    usermod -aG sudo "${USERNAME}" || true; \
    mkdir -p /etc/sudoers.d; \
    echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/90-openhands; \
    chmod 0440 /etc/sudoers.d/90-openhands; \
    mkdir -p /workspace/project; \
    chown -R "${UID}:${GID}" /workspace

RUN --mount=type=cache,id=feature-factory-miniconda-installers,target=/var/cache/feature-factory/miniconda,sharing=locked \
    set -eux; \
    if [ ! -x "${CONDA_DIR}/bin/conda" ]; then \
      case "$(uname -m)" in \
        x86_64) miniconda_arch="x86_64" ;; \
        aarch64|arm64) miniconda_arch="aarch64" ;; \
        *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;; \
      esac; \
      miniconda_base="${MINICONDA_DIST_MIRROR:-https://repo.anaconda.com/miniconda}"; \
      installer_path="/var/cache/feature-factory/miniconda/Miniconda3-latest-Linux-${miniconda_arch}.sh"; \
      installer_url="${miniconda_base}/Miniconda3-latest-Linux-${miniconda_arch}.sh"; \
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
    fi; \
    if [ -n "${MINICONDA_DIST_MIRROR}" ]; then \
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
    fi; \
    "${CONDA_DIR}/bin/conda" config --system --set auto_activate_base false

RUN set -eux; \
    mkdir -p "/home/${USERNAME}/.config/uv" "/home/${USERNAME}/.config/pip"; \
    if [ -n "${UV_HTTP_TIMEOUT}" ] || [ -n "${UV_HTTP_RETRIES}" ]; then \
      { \
        if [ -n "${UV_HTTP_TIMEOUT}" ]; then \
          printf 'export UV_HTTP_TIMEOUT="%s"\n' "${UV_HTTP_TIMEOUT}"; \
          printf 'export UV_REQUEST_TIMEOUT="%s"\n' "${UV_HTTP_TIMEOUT}"; \
        fi; \
        if [ -n "${UV_HTTP_RETRIES}" ]; then \
          printf 'export UV_HTTP_RETRIES="%s"\n' "${UV_HTTP_RETRIES}"; \
        fi; \
      } > /etc/profile.d/feature-factory-uv-network.sh; \
      chmod 0644 /etc/profile.d/feature-factory-uv-network.sh; \
    fi; \
    if [ -n "${UV_PYTHON_INSTALL_MIRROR}" ] || [ -n "${UV_INDEX_URL}" ]; then \
      { \
        if [ -n "${UV_PYTHON_INSTALL_MIRROR}" ]; then \
          printf 'python-install-mirror = "%s"\n' "${UV_PYTHON_INSTALL_MIRROR}"; \
        fi; \
        if [ -n "${UV_INDEX_URL}" ]; then \
          printf '[[index]]\nurl = "%s"\ndefault = true\n' "${UV_INDEX_URL}"; \
        fi; \
      } > "/home/${USERNAME}/.config/uv/uv.toml"; \
    fi; \
    if [ -n "${PIP_INDEX_URL}" ]; then \
      printf '[global]\nindex-url = %s\n' "${PIP_INDEX_URL}" > "/home/${USERNAME}/.config/pip/pip.conf"; \
    fi; \
    if [ -n "${NPM_REGISTRY}" ]; then \
      printf 'registry=%s\n' "${NPM_REGISTRY}" > "/home/${USERNAME}/.npmrc"; \
    fi; \
    if [ -n "${NPM_REGISTRY}" ] || [ -n "${NODE_DIST_MIRROR}" ] || [ -n "${MINICONDA_DIST_MIRROR}" ] || [ -n "${TORCH_CPU_FIND_LINKS}" ]; then \
      { \
        if [ -n "${TORCH_CPU_FIND_LINKS}" ]; then \
          printf 'export TORCH_CPU_FIND_LINKS="%s"\n' "${TORCH_CPU_FIND_LINKS}"; \
        fi; \
        if [ -n "${NPM_REGISTRY}" ]; then \
          printf 'export NPM_CONFIG_REGISTRY="%s"\n' "${NPM_REGISTRY}"; \
        fi; \
        if [ -n "${NODE_DIST_MIRROR}" ]; then \
          printf 'export NODE_DIST_MIRROR="%s"\n' "${NODE_DIST_MIRROR}"; \
          printf 'export NODEJS_ORG_MIRROR="%s"\n' "${NODE_DIST_MIRROR}"; \
          printf 'export NVM_NODEJS_ORG_MIRROR="%s"\n' "${NODE_DIST_MIRROR}"; \
        fi; \
        if [ -n "${MINICONDA_DIST_MIRROR}" ]; then \
          printf 'export MINICONDA_DIST_MIRROR="%s"\n' "${MINICONDA_DIST_MIRROR}"; \
        fi; \
      } > /etc/profile.d/feature-factory-mirrors.sh; \
      chmod 0644 /etc/profile.d/feature-factory-mirrors.sh; \
    fi; \
    chown -R "${UID}:${GID}" "/home/${USERNAME}/.config" "/home/${USERNAME}/.npmrc" 2>/dev/null || true

COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv
COPY --from=builder /usr/local/bin/uvx /usr/local/bin/uvx
COPY --chown=${UID}:${GID} --from=builder /agent-server /agent-server
RUN printf '%s\n' \
      '#!/usr/bin/env bash' \
      'umask "${FEATURE_FACTORY_OPENHANDS_UMASK:-0000}"' \
      'exec /agent-server/.venv/bin/python -m openhands.agent_server "$@"' \
      > /usr/local/bin/feature-factory-openhands-agent-server \
    && chmod 0755 /usr/local/bin/feature-factory-openhands-agent-server

USER ${USERNAME}
WORKDIR /
EXPOSE ${PORT}
ENTRYPOINT ["/usr/local/bin/feature-factory-openhands-agent-server"]