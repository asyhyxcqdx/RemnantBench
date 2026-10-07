# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim

ARG DEBIAN_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG NPM_REGISTRY=https://registry.npmmirror.com

ENV NPM_CONFIG_REGISTRY=${NPM_REGISTRY} \
    COREPACK_NPM_REGISTRY=${NPM_REGISTRY}

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|http://deb.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|http://security.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|http://deb.debian.org/debian|${DEBIAN_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://deb.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://security.debian.org/debian-security|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|g" \
        -e "s|https://deb.debian.org/debian|${DEBIAN_APT_MIRROR_BOOTSTRAP}|g"

RUN --mount=type=cache,id=feature-factory-apt-cache-bookworm,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=feature-factory-apt-lists-bookworm,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 make g++

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|${DEBIAN_APT_MIRROR_BOOTSTRAP}|${DEBIAN_APT_MIRROR}|g"

RUN set -eux; \
    printf 'registry=%s\n' "${NPM_REGISTRY}" > /root/.npmrc; \
    corepack enable

WORKDIR /workspace
