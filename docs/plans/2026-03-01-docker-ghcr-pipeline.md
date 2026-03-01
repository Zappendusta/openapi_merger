# Docker GHCR Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a GitHub Actions workflow that builds the Docker image on every push to `main`/`master` and publishes it to GitHub Container Registry (ghcr.io).

**Architecture:** A single workflow file in `.github/workflows/` triggers on push to main and on version tags. It uses `docker/build-push-action` with the built-in `GITHUB_TOKEN` for authentication — no extra secrets required. Images are tagged with `latest`, the short SHA, and semver tags when a `v*` tag is pushed.

**Tech Stack:** GitHub Actions, Docker Buildx, ghcr.io (GitHub Container Registry), `docker/metadata-action`, `docker/build-push-action`

---

### Task 1: Create the GitHub Actions workflow directory

**Files:**
- Create: `.github/workflows/docker-publish.yml`

**Step 1: Create directories**

```bash
mkdir -p .github/workflows
```

**Step 2: Verify**

```bash
ls .github/workflows
```
Expected: empty directory (no files yet).

---

### Task 2: Write the workflow file

**Files:**
- Create: `.github/workflows/docker-publish.yml`

**Step 1: Write the file**

```yaml
name: Build & Publish Docker Image

on:
  push:
    branches:
      - main
      - master
    tags:
      - "v*.*.*"
  pull_request:
    branches:
      - main
      - master

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build-and-push:
    name: Build and push image
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GitHub Container Registry
        if: github.event_name != 'pull_request'
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract Docker metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=pr
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=sha,prefix=sha-,format=short
            type=raw,value=latest,enable={{is_default_branch}}

      - name: Build and push Docker image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: ${{ github.event_name != 'pull_request' }}
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

Save this exactly to `.github/workflows/docker-publish.yml`.

**Step 2: Verify the file exists and is valid YAML**

```bash
cat .github/workflows/docker-publish.yml
```

**Step 3: Commit**

```bash
git add .github/workflows/docker-publish.yml
git commit -m "ci: add GitHub Actions workflow to build and push Docker image to GHCR"
```

---

### Task 3: Verify the Dockerfile is compatible

**Files:**
- Read: `Dockerfile`

**Step 1: Check Dockerfile builds locally (optional but recommended)**

```bash
docker build -t openapi-merger:local .
```

Expected: image builds without errors.

**Step 2: Confirm the image runs**

```bash
docker run --rm -e SERVICE_CONFIG=/dev/null -e SOURCES_CONFIG=/dev/null openapi-merger:local --help 2>&1 || true
```

This just confirms the entrypoint is reachable (uvicorn will error without valid configs, that's fine).

---

### Task 4: Push to remote and watch the workflow run

**Step 1: Push the commit**

```bash
git push origin master
```
(or `main` depending on your default branch)

**Step 2: Open the Actions tab**

Navigate to: `https://github.com/<org>/<repo>/actions`

Expected: A new workflow run named "Build & Publish Docker Image" appears and succeeds.

**Step 3: Verify the package is published**

Navigate to: `https://github.com/<org>/<repo>/pkgs/container/<repo>`

Expected: A container package with `latest` and `sha-<short-sha>` tags.

---

### Task 5: (Optional) Enable package visibility

By default GHCR packages inherit repository visibility. If the repo is private, the image is private. If you want the image public:

1. Go to `https://github.com/users/<username>/packages` (or org equivalent)
2. Click the package → Package settings → Change visibility → Public

No code changes needed.

---

## What the tags mean

| Tag | When set | Example |
|-----|----------|---------|
| `latest` | Push to main/master | `ghcr.io/org/repo:latest` |
| `sha-abc1234` | Every push | `ghcr.io/org/repo:sha-abc1234` |
| `master` | Push to master branch | `ghcr.io/org/repo:master` |
| `1.2.3` | Push of tag `v1.2.3` | `ghcr.io/org/repo:1.2.3` |
| `1.2` | Push of tag `v1.2.3` | `ghcr.io/org/repo:1.2` |
| `pr-42` | Pull request #42 (no push, build-only) | — |

## Pull the image

```bash
docker pull ghcr.io/<github-username-or-org>/openapi-merger:latest
```
