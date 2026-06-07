# syntax=docker/dockerfile:1.6

# Stage 1: download Speakeasy binary.
FROM debian:bookworm-slim AS speakeasy
ARG SPEAKEASY_VERSION=1.605.0
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) SE_ARCH=linux_amd64 ;; \
      arm64) SE_ARCH=linux_arm64 ;; \
      *) echo "unsupported arch ${TARGETARCH}"; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/speakeasy-api/speakeasy/releases/download/v${SPEAKEASY_VERSION}/speakeasy_${SE_ARCH}.zip" -o /tmp/speakeasy.zip; \
    apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*; \
    unzip /tmp/speakeasy.zip -d /tmp/speakeasy; \
    install -m 0755 /tmp/speakeasy/speakeasy /usr/local/bin/speakeasy; \
    /usr/local/bin/speakeasy --version

# Stage 2: runtime image.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

ARG REDOCLY_VERSION=1.34.5
ARG OPENAPI_MERGE_CLI_VERSION=1.3.2

# Install Node.js (for redocly + openapi-merge-cli) and the two CLIs.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
 && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && npm install -g "@redocly/cli@${REDOCLY_VERSION}" "openapi-merge-cli@${OPENAPI_MERGE_CLI_VERSION}" \
 && apt-get purge -y --auto-remove curl gnupg \
 && rm -rf /var/lib/apt/lists/* /root/.npm

# Speakeasy binary from stage 1.
COPY --from=speakeasy /usr/local/bin/speakeasy /usr/local/bin/speakeasy

# Verify all three binaries are on PATH.
RUN redocly --version && openapi-merge-cli --version && speakeasy --version

COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .

ARG BUILD_GIT_SHA=unknown
ARG BUILD_GIT_BRANCH=unknown
ARG BUILD_TIME=unknown
ENV BUILD_GIT_SHA=${BUILD_GIT_SHA}
ENV BUILD_GIT_BRANCH=${BUILD_GIT_BRANCH}
ENV BUILD_TIME=${BUILD_TIME}

EXPOSE 8080
ENV SERVICE_CONFIG=/config/service.yaml
ENV SOURCES_CONFIG=/config/sources.yaml
CMD ["uvicorn", "openapi_merger.main:app", "--host", "0.0.0.0", "--port", "8080"]
