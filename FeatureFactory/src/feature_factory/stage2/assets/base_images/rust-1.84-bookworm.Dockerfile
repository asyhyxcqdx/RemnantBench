# syntax=docker/dockerfile:1.7

FROM rust:1.84-bookworm

RUN --mount=type=cache,id=feature-factory-apt-cache-bookworm,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=feature-factory-apt-lists-bookworm,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git pkg-config python3

WORKDIR /workspace
