# openapi-merger

A self-hosted HTTP service that fetches multiple OpenAPI documents, applies route
transformations, merges them into a single unified spec, and exposes the result
over HTTP — with optional Basic Auth and in-memory caching.

## What it does

1. **Fetches** 1–N upstream OpenAPI specs (JSON or YAML) on demand, supporting
   per-source Basic Auth.
2. **Transforms** route prefixes for each source independently
   (e.g. `/api/widgets` → `/api/users/widgets` when `from: /api`, `to: /api/users`).
3. **Merges** all specs into one OpenAPI 3.x document:
   - Duplicate schemas with identical content are silently deduplicated.
   - Colliding schemas (same name, different content) are automatically
     prefixed per source (e.g. `UsersError`, `OrdersError`).
   - Duplicate paths raise a 502 error at request time.
4. **Serves** the merged spec at a configurable path, with optional Basic Auth,
   in JSON or YAML format.

Results are cached in memory. A `?refresh=true` query parameter forces a rebuild.

## Quick start with Docker

The image is published to GitHub Container Registry. Replace `<your-org>` with your GitHub username or organisation (e.g. `paulusdettmer`):

```bash
docker pull ghcr.io/<your-org>/openapi_merger:latest
```

Run with your config directory mounted:

```bash
docker run -p 8080:8080 \
  -v $(pwd)/config:/config \
  ghcr.io/<your-org>/openapi_merger:latest
```

The service expects two files in `/config/`:
- `service.yaml` — server settings
- `sources.yaml` — upstream API sources

## Configuration

### service.yaml

> **Note:** The listening port is set via the uvicorn command (or `-p` in Docker), not in this file.

```yaml
spec_path: /openapi.json   # path where the merged spec is served

info:
  title: My Merged API
  version: 1.0.0

# Optional Basic Auth for the merged spec endpoint
# auth:
#   username: admin
#   password: secret
```

### sources.yaml

```yaml
sources:
  - name: users
    url: http://users-service/openapi.json
    schema_prefix: Users     # prefix applied to schemas on collision
    # auth:                  # optional upstream Basic Auth
    #   username: svc
    #   password: secret
    route_transforms:
      - from: /api
        to: /api/users       # rewrites /api/... → /api/users/...

  - name: orders
    url: http://orders-service/openapi.yaml
    schema_prefix: Orders
    route_transforms:
      - from: /api
        to: /api/orders
```

### Config file locations

| Env variable       | Default                  | Purpose           |
|--------------------|--------------------------|-------------------|
| `SERVICE_CONFIG`   | `/config/service.yaml`   | Server settings   |
| `SOURCES_CONFIG`   | `/config/sources.yaml`   | Source APIs       |

## API

| Method | Path              | Description                                      |
|--------|-------------------|--------------------------------------------------|
| GET    | `<spec_path>`     | Returns the merged OpenAPI spec                  |
| GET    | `/health`         | Health check — returns `{"status": "ok"}`        |

### Query parameters for the spec endpoint

| Parameter  | Values           | Default  | Description                        |
|------------|------------------|----------|------------------------------------|
| `format`   | `json` \| `yaml` | `json`   | Response format                    |
| `refresh`  | `true` \| `false`| `false`  | Force re-fetch and re-merge        |

## Running locally (without Docker)

Requires Python 3.12+.

```bash
pip install -e ".[dev]"

SERVICE_CONFIG=example/service.yaml \
SOURCES_CONFIG=example/sources.yaml \
uvicorn openapi_merger.main:app --port 8080
```

> **Note:** The example configs point to placeholder upstream URLs (`http://users-service/...`). The service will start, but the first request to the spec endpoint will return a 502 until you update `sources.yaml` with real upstream URLs.

## Running tests

```bash
pip install -e ".[dev]"
pytest
```

## Project structure

```
openapi_merger/
├── src/openapi_merger/
│   ├── main.py          # FastAPI app, auth middleware, lifespan wiring
│   ├── config.py        # Pydantic models for service.yaml / sources.yaml
│   ├── fetcher.py       # Async HTTP fetch (httpx), JSON/YAML auto-detect
│   ├── transformer.py   # Route prefix rewriting
│   ├── merger.py        # Schema collision detection, spec merging
│   └── orchestrator.py  # Coordinates fetch → transform → merge, in-memory cache
├── tests/               # pytest suite (unit + integration with respx mocks)
├── example/             # Example service.yaml and sources.yaml
├── Dockerfile           # python:3.12-slim, installs package, runs uvicorn
└── pyproject.toml       # Hatchling build, dependencies, pytest config
```

## How it was built

This project was designed and implemented with [Claude Code](https://claude.ai/code)
(Anthropic's AI coding assistant). The original design intent was captured in
`idea.txt` and then translated into a production-ready service through iterative
AI-assisted development:

- **FastAPI** was chosen for its async-native request handling and automatic
  OpenAPI introspection (disabled here, since the service _is_ an OpenAPI tool).
- **httpx** provides async HTTP with a clean auth API for upstream fetches.
- **Pydantic v2** models validate both config files at startup, failing fast on
  misconfiguration.
- **Hatchling** is the build backend — minimal configuration, PEP 517 compliant.
- **GitHub Actions** builds and pushes the Docker image to GHCR on every push
  to `master` and on version tags, gated behind a `pytest` run.

The core merge logic in `merger.py` handles the tricky case of schema collisions:
identical schemas are silently deduplicated; schemas with the same name but
different content are renamed with a per-source prefix to avoid silent data loss.

## CI / Docker image

GitHub Actions workflow (`.github/workflows/docker-publish.yml`):

1. Runs `pytest` on every push and PR targeting `master`.
2. On push to `master` or a `v*.*.*` tag: builds and pushes to GHCR.

Image tags produced: `latest` (master), `sha-<short>`, semver `vX.Y.Z` / `vX.Y`.
