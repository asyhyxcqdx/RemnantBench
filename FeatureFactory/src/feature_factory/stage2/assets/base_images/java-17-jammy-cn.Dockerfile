# syntax=docker/dockerfile:1.7

FROM eclipse-temurin:17-jdk-jammy

ARG UBUNTU_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP=http://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports
ARG UBUNTU_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu
ARG UBUNTU_PORTS_APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports
ARG MAVEN_MIRROR=https://maven.aliyun.com/repository/public

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
    && apt-get install -y --no-install-recommends ca-certificates curl git python3

RUN set -eux; \
    find /etc/apt -type f \( -name '*.list' -o -name '*.sources' \) -print0 \
      | xargs -0 -r sed -i \
        -e "s|${UBUNTU_APT_MIRROR_BOOTSTRAP}|${UBUNTU_APT_MIRROR}|g" \
        -e "s|${UBUNTU_PORTS_APT_MIRROR_BOOTSTRAP}|${UBUNTU_PORTS_APT_MIRROR}|g"

RUN set -eux; \
    mkdir -p /root/.m2 /root/.gradle; \
    printf '%s\n' \
      '<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">' \
      '  <mirrors>' \
      '    <mirror>' \
      '      <id>china-public</id>' \
      '      <mirrorOf>*</mirrorOf>' \
      "      <url>${MAVEN_MIRROR}</url>" \
      '    </mirror>' \
      '  </mirrors>' \
      '</settings>' \
      > /root/.m2/settings.xml; \
    printf 'allprojects { repositories { maven { url "%s" }; mavenCentral() } }\n' "${MAVEN_MIRROR}" > /root/.gradle/init.gradle

WORKDIR /workspace
