# syntax=docker/dockerfile:1.7

FROM eclipse-temurin:17-jdk-jammy

RUN --mount=type=cache,id=feature-factory-apt-cache-jammy,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=feature-factory-apt-lists-jammy,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3

WORKDIR /workspace
