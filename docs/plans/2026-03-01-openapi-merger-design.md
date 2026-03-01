# OpenAPI Merger Service — Design

**Date:** 2026-03-01

## Overview

A Dockerized FastAPI service that fetches multiple upstream OpenAPI 3.x specs, applies per-source route transformations and schema collision resolution, merges them into a single spec, and exposes the result via HTTP.

---

## Architecture

A single FastAPI application in one Docker container. Two config files are loaded at startup:

- **`service.yaml`** — global config: port, route path for the merged spec, optional basic auth for the exposed endpoint, and merged doc metadata (`info` block).
- **`sources.yaml`** — one entry per upstream service.

On the first request to the merged spec endpoint (or when `?refresh=true` is passed), the service fetches all upstream specs concurrently using `httpx` async, processes and merges them, caches the result in memory, and returns it. Subsequent requests hit the in-memory cache.

---

## Configuration Format

### `service.yaml`

```yaml
port: 8080
spec_path: /openapi.json
auth:             # optional basic auth for the exposed endpoint
  username: admin
  password: secret
info:
  title: My Merged API
  version: 1.0.0
```

### `sources.yaml`

```yaml
sources:
  - name: users
    url: https://users-service/openapi.json
    schema_prefix: Users      # required
    auth:                     # optional basic auth to fetch upstream spec
      username: svc
      password: secret
    route_transforms:         # optional, applied in order
      - from: /api
        to: /api/users

  - name: orders
    url: https://orders-service/openapi.yaml
    schema_prefix: Orders
    route_transforms:
      - from: /api
        to: /api/orders
```

**`schema_prefix` is required** for every source. Config validation at startup rejects any source entry missing it.

Route transforms use prefix replacement: any path starting with `from` has that prefix replaced with `to`; the rest of the path is preserved.

---

## Data Flow & Processing Pipeline

For each upstream source (concurrently via `asyncio.gather`):

1. **Fetch** — `httpx.AsyncClient` GET with optional basic auth. Accepts JSON or YAML response, parsed into a dict.
2. **Transform routes** — for each path in `paths`, apply `route_transforms` in order: replace matching `from` prefix with `to`.

Then merge all processed documents:

3. **Collect schemas** — gather all `components/schemas` entries across all sources, tracking provenance.
4. **Resolve collisions** — for each schema name appearing in more than one source:
   - If all copies are deeply equal → deduplicate, keep one.
   - If copies differ → for each contributing source, rename that specific schema to `{schema_prefix}{name}` and rewrite only the `$ref` strings pointing to that schema within that source's document.
5. **Merge `paths`** — union all path entries. Duplicate paths → fail with error.
6. **Merge `components/schemas`** — union all (now collision-free) schema entries.
7. **Merge other `components`** — same union strategy for `responses`, `parameters`, `requestBodies`, etc.
8. **Set top-level metadata** — `info` from `service.yaml`, `openapi` version from first source, `servers` with configured base URL.
9. **Cache** the merged dict in memory.

On request, serialize to JSON (default) or YAML via `?format=yaml`.
Cache is bypassed and rebuilt when `?refresh=true` is passed.

---

## Error Handling

All errors return a JSON body with a descriptive message. The whole merge fails on any error — partial results are never served silently.

| Scenario | HTTP status | Detail |
|---|---|---|
| Upstream fetch fails (network / non-2xx) | 502 | Source name and reason |
| Unparseable spec (bad JSON/YAML / missing OpenAPI fields) | 502 | Source name |
| Path collision (same path in two sources) | 500 | Path and both source names |
| Schema collision after prefix resolution (should not occur) | 500 | Schema name and sources |
| Invalid config at startup | exit non-zero | Clear message to stdout |
| Unknown `?format=` value | 400 | Accepted values listed |

---

## Testing Strategy

- **Unit tests** — each pipeline stage in isolation (route transform, collision detection, $ref rewriting, merge, serialization) using fixture OpenAPI dicts. No HTTP.
- **Integration tests** — `httpx.AsyncClient` with `respx` mocking upstream HTTP calls. Full fetch → transform → merge → serve flow, including cache behavior and `?refresh=true`.
- **Config validation tests** — missing required fields, bad YAML, unknown keys.
- **Error case tests** — upstream 500, path collision, schema collision, unparseable spec.

No Docker-level end-to-end tests in the test suite — keep it fast and portable.
