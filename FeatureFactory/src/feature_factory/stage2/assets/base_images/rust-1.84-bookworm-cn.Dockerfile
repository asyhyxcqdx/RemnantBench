# syntax=docker/dockerfile:1.7

FROM rust:1.84-bookworm

ARG DEBIAN_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/debian-security
ARG DEBIAN_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian
ARG DEBIAN_SECURITY_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/debian-security

ENV CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse

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
    && apt-get install -y --no-install-recommends ca-certificates curl git pkg-config python3

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|${DEBIAN_SECURITY_APT_MIRROR_BOOTSTRAP}|${DEBIAN_SECURITY_APT_MIRROR}|g" \
        -e "s|${DEBIAN_APT_MIRROR_BOOTSTRAP}|${DEBIAN_APT_MIRROR}|g"

RUN set -eux; \
    mkdir -p /usr/local/cargo; \
    printf '%s\n' \
      '[source.crates-io]' \
      'replace-with = "rsproxy-sparse"' \
      '[source.rsproxy-sparse]' \
      'registry = "sparse+https://rsproxy.cn/index/"' \
      '[registries.rsproxy]' \
      'index = "https://rsproxy.cn/crates.io-index"' \
      > /usr/local/cargo/config.toml

WORKDIR /workspace
