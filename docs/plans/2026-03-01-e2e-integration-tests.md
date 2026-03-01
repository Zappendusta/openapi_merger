# E2E Integration Tests Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a `tests/e2e/` suite that starts two real in-process HTTP servers (serving static OpenAPI fixture files), boots the merger app via `TestClient`, and asserts the merged result is exactly correct for all supported use cases.

**Architecture:** Two `http.server.HTTPServer` instances (one for JSON, one for YAML) are started in background threads before the test session. The merger's FastAPI app is exercised via `starlette.testclient.TestClient`; it makes real async HTTP calls to those upstream servers during the test. No mocking, no Docker.

**Tech Stack:** pytest, Python stdlib `http.server`, FastAPI `TestClient`, PyYAML, `importlib.reload` (existing pattern)

---

## Use-cases covered

| Use-case | How exercised |
|---|---|
| Basic merge | Paths from both services appear in merged output |
| Discard paths (`discard_paths`) | `/health` and `/internal/*` absent from merged output |
| Rewrite paths (`route_transforms`) | Original prefixes absent, rewritten prefixes present |
| Schema collision avoidance | Shared `Item` schema gets `Users` / `Orders` prefix |
| `$ref` rewriting | `$ref` inside path objects points to prefixed schema name |
| Non-colliding schemas preserved | `User` and `Order` schemas kept as-is |
| YAML upstream | Service B served as `.yaml`; responses appear in merged output |
| JSON upstream | Service A served as `.json`; responses appear in merged output |

---

## Task 1 — Create the `tests/e2e/` package

**Files:**
- Create: `tests/e2e/__init__.py`

**Step 1: Create the empty init file**

```bash
mkdir -p tests/e2e
touch tests/e2e/__init__.py
```

**Step 2: Verify pytest still collects existing tests**

```bash
.venv/bin/pytest --collect-only -q 2>&1 | head -20
```
Expected: existing test names printed, no errors.

**Step 3: Commit**

```bash
git add tests/e2e/__init__.py
git commit -m "test: scaffold tests/e2e package"
```

---

## Task 2 — Create Service A fixture (JSON, many use-cases)

**Files:**
- Create: `tests/e2e/fixtures/service_a.json`

This spec exercises: `discard_paths` (`/health`, `/internal/*`), `route_transforms` (`/users` → `/api/users`), schema collision (`Item` has `qty` field — different from service B), non-colliding schema (`User`), and `$ref` usage.

**Step 1: Create the fixture**

```bash
mkdir -p tests/e2e/fixtures
```

Write `tests/e2e/fixtures/service_a.json`:

```json
{
  "openapi": "3.0.0",
  "info": { "title": "Users API", "version": "1.0" },
  "paths": {
    "/users": {
      "get": {
        "operationId": "listUsers",
        "responses": {
          "200": {
            "description": "List of users",
            "content": {
              "application/json": {
                "schema": { "$ref": "#/components/schemas/User" }
              }
            }
          }
        }
      }
    },
    "/users/{id}": {
      "get": {
        "operationId": "getUser",
        "responses": { "200": { "description": "A user" } }
      }
    },
    "/users/{id}/items": {
      "get": {
        "operationId": "getUserItems",
        "responses": {
          "200": {
            "description": "Items of user",
            "content": {
              "application/json": {
                "schema": { "$ref": "#/components/schemas/Item" }
              }
            }
          }
        }
      }
    },
    "/health": {
      "get": {
        "operationId": "healthA",
        "responses": { "200": { "description": "ok" } }
      }
    },
    "/internal/stats": {
      "get": {
        "operationId": "internalStats",
        "responses": { "200": { "description": "ok" } }
      }
    }
  },
  "components": {
    "schemas": {
      "User": {
        "type": "object",
        "properties": {
          "id": { "type": "integer" },
          "name": { "type": "string" }
        }
      },
      "Item": {
        "type": "object",
        "properties": {
          "label": { "type": "string" },
          "qty": { "type": "integer" }
        }
      }
    }
  }
}
```

**Step 2: Validate it is valid JSON**

```bash
python -c "import json; json.load(open('tests/e2e/fixtures/service_a.json')); print('ok')"
```
Expected: `ok`

**Step 3: Commit**

```bash
git add tests/e2e/fixtures/service_a.json
git commit -m "test(e2e): add service_a.json fixture"
```

---

## Task 3 — Create Service B fixture (YAML, collision schema, discard `/health`)

**Files:**
- Create: `tests/e2e/fixtures/service_b.yaml`

This spec exercises: YAML parsing, `discard_paths` (`/health`), `route_transforms` (`/orders` → `/api/orders`), schema collision (`Item` has `price` field — different from service A), and non-colliding schema (`Order`).

**Step 1: Write `tests/e2e/fixtures/service_b.yaml`**

```yaml
openapi: "3.0.0"
info:
  title: Orders API
  version: "1.0"
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        "200":
          description: List of orders
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Order"
  /orders/{id}:
    get:
      operationId: getOrder
      responses:
        "200":
          description: An order
  /orders/{id}/items:
    get:
      operationId: getOrderItems
      responses:
        "200":
          description: Items of order
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Item"
  /health:
    get:
      operationId: healthB
      responses:
        "200":
          description: ok
components:
  schemas:
    Order:
      type: object
      properties:
        id:
          type: integer
        total:
          type: number
    Item:
      type: object
      properties:
        label:
          type: string
        price:
          type: number
```

**Step 2: Validate it is valid YAML**

```bash
python -c "import yaml; yaml.safe_load(open('tests/e2e/fixtures/service_b.yaml')); print('ok')"
```
Expected: `ok`

**Step 3: Commit**

```bash
git add tests/e2e/fixtures/service_b.yaml
git commit -m "test(e2e): add service_b.yaml fixture"
```

---

## Task 4 — Write `tests/e2e/conftest.py`

**Files:**
- Create: `tests/e2e/conftest.py`

This file provides two module-scoped pytest fixtures:
1. `upstream_server` — starts a single `HTTPServer` in a background thread serving `tests/e2e/fixtures/`; yields the base URL `http://127.0.0.1:{port}`.
2. `merger_client` — writes `service.yaml` and `sources.yaml` to a temp dir, sets `SERVICE_CONFIG`/`SOURCES_CONFIG` env vars, reloads `openapi_merger.main`, and yields a `TestClient`.

**Step 1: Write the conftest**

```python
# tests/e2e/conftest.py
from __future__ import annotations

import http.server
import importlib
import os
import pathlib
import socket
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def upstream_server():
    """Start a static HTTP server serving tests/e2e/fixtures/."""

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass  # suppress access logs during tests

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def merger_client(upstream_server, tmp_path_factory):
    """Boot the merger FastAPI app pointed at the real upstream server."""
    tmp = tmp_path_factory.mktemp("e2e_config")

    svc_yaml = tmp / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "info:\n"
        "  title: Merged API\n"
        "  version: '1.0'\n"
    )

    sources_dict = {
        "sources": [
            {
                "name": "users",
                "url": f"{upstream_server}/service_a.json",
                "schema_prefix": "Users",
                "discard_paths": ["/health", "/internal"],
                "route_transforms": [{"from": "/users", "to": "/api/users"}],
            },
            {
                "name": "orders",
                "url": f"{upstream_server}/service_b.yaml",
                "schema_prefix": "Orders",
                "discard_paths": ["/health"],
                "route_transforms": [{"from": "/orders", "to": "/api/orders"}],
            },
        ]
    }
    sources_yaml = tmp / "sources.yaml"
    sources_yaml.write_text(yaml.dump(sources_dict))

    prev_svc = os.environ.get("SERVICE_CONFIG")
    prev_src = os.environ.get("SOURCES_CONFIG")
    os.environ["SERVICE_CONFIG"] = str(svc_yaml)
    os.environ["SOURCES_CONFIG"] = str(sources_yaml)

    import openapi_merger.main as m
    importlib.reload(m)

    with TestClient(m.app) as client:
        yield client

    # Restore env
    if prev_svc is None:
        os.environ.pop("SERVICE_CONFIG", None)
    else:
        os.environ["SERVICE_CONFIG"] = prev_svc
    if prev_src is None:
        os.environ.pop("SOURCES_CONFIG", None)
    else:
        os.environ["SOURCES_CONFIG"] = prev_src
```

**Step 2: Check for syntax errors**

```bash
python -c "import ast; ast.parse(open('tests/e2e/conftest.py').read()); print('ok')"
```
Expected: `ok`

**Step 3: Commit**

```bash
git add tests/e2e/conftest.py
git commit -m "test(e2e): add conftest with real upstream HTTP server fixture"
```

---

## Task 5 — Write the E2E test file

**Files:**
- Create: `tests/e2e/test_e2e.py`

**Step 1: Write the test file**

```python
# tests/e2e/test_e2e.py
"""
End-to-end integration tests.

Two real HTTP servers serve OpenAPI fixture files.
The merger app runs via TestClient and makes real HTTP calls to those servers.
No mocking.

Use-cases exercised:
  - basic merge (paths from both services in output)
  - discard_paths (/health and /internal/* stripped before merge)
  - route_transforms (/users -> /api/users, /orders -> /api/orders)
  - schema collision avoidance (Item -> UsersItem / OrdersItem)
  - $ref rewriting (refs updated to match prefixed schema names)
  - non-colliding schemas preserved (User, Order)
  - YAML upstream (service_b.yaml parsed correctly)
  - JSON upstream (service_a.json parsed correctly)
"""

import pytest


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_spec(client) -> dict:
    """Fetch the merged spec and return parsed JSON. Caches in client session."""
    r = client.get("/openapi.json")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    return r.json()


# ── health ────────────────────────────────────────────────────────────────────

def test_health_endpoint(merger_client):
    r = merger_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


# ── basic merge ───────────────────────────────────────────────────────────────

def test_merged_spec_has_openapi_field(merger_client):
    spec = _get_spec(merger_client)
    assert spec.get("openapi") == "3.0.0"


def test_merged_info_title(merger_client):
    spec = _get_spec(merger_client)
    assert spec["info"]["title"] == "Merged API"


# ── route_transforms ─────────────────────────────────────────────────────────

def test_users_paths_rewritten(merger_client):
    """Original /users prefix replaced by /api/users."""
    spec = _get_spec(merger_client)
    paths = spec["paths"]
    assert "/api/users" in paths
    assert "/api/users/{id}" in paths
    assert "/api/users/{id}/items" in paths


def test_orders_paths_rewritten(merger_client):
    """Original /orders prefix replaced by /api/orders."""
    spec = _get_spec(merger_client)
    paths = spec["paths"]
    assert "/api/orders" in paths
    assert "/api/orders/{id}" in paths
    assert "/api/orders/{id}/items" in paths


def test_original_prefixes_absent(merger_client):
    """Pre-transform paths must not appear in the merged output."""
    spec = _get_spec(merger_client)
    paths = spec["paths"]
    for original in ("/users", "/users/{id}", "/orders", "/orders/{id}"):
        assert original not in paths, f"Original path {original!r} should have been rewritten"


# ── discard_paths ─────────────────────────────────────────────────────────────

def test_health_paths_discarded(merger_client):
    """/health was in both services; both should be absent from merged output."""
    spec = _get_spec(merger_client)
    paths = spec["paths"]
    assert "/health" not in paths


def test_internal_paths_discarded(merger_client):
    """/internal/* paths from service A must be absent (prefix discard)."""
    spec = _get_spec(merger_client)
    paths = spec["paths"]
    for path in paths:
        assert not path.startswith("/internal"), (
            f"Path {path!r} should have been discarded (internal prefix)"
        )


# ── schema collision avoidance ────────────────────────────────────────────────

def test_colliding_schema_prefixed_for_users(merger_client):
    """Service A's Item (qty field) → UsersItem."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "UsersItem" in schemas
    assert "qty" in schemas["UsersItem"].get("properties", {})


def test_colliding_schema_prefixed_for_orders(merger_client):
    """Service B's Item (price field) → OrdersItem."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "OrdersItem" in schemas
    assert "price" in schemas["OrdersItem"].get("properties", {})


def test_original_item_schema_absent(merger_client):
    """The bare 'Item' name must not exist — it should be prefixed in both cases."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "Item" not in schemas, (
        "Schema 'Item' should have been prefixed due to collision; found it unprefixed"
    )


# ── non-colliding schemas preserved ──────────────────────────────────────────

def test_user_schema_preserved(merger_client):
    """User schema from service A has no collision; kept as-is."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "User" in schemas
    assert schemas["User"]["properties"]["name"]["type"] == "string"


def test_order_schema_preserved(merger_client):
    """Order schema from service B has no collision; kept as-is."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "Order" in schemas
    assert schemas["Order"]["properties"]["total"]["type"] == "number"


# ── $ref rewriting ────────────────────────────────────────────────────────────

def test_ref_rewritten_in_users_items_path(merger_client):
    """$ref in /api/users/{id}/items response rewritten from Item → UsersItem."""
    spec = _get_spec(merger_client)
    schema_ref = (
        spec["paths"]["/api/users/{id}/items"]["get"]
        ["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )
    assert schema_ref == "#/components/schemas/UsersItem", (
        f"Expected #/components/schemas/UsersItem, got {schema_ref!r}"
    )


def test_ref_rewritten_in_orders_items_path(merger_client):
    """$ref in /api/orders/{id}/items response rewritten from Item → OrdersItem."""
    spec = _get_spec(merger_client)
    schema_ref = (
        spec["paths"]["/api/orders/{id}/items"]["get"]
        ["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
    )
    assert schema_ref == "#/components/schemas/OrdersItem", (
        f"Expected #/components/schemas/OrdersItem, got {schema_ref!r}"
    )


# ── YAML upstream ─────────────────────────────────────────────────────────────

def test_yaml_upstream_paths_present(merger_client):
    """service_b.yaml was fetched and parsed; its paths appear in the merge."""
    spec = _get_spec(merger_client)
    # /api/orders comes exclusively from service_b.yaml
    assert "/api/orders" in spec["paths"]


def test_yaml_upstream_schema_present(merger_client):
    """Order schema comes exclusively from service_b.yaml."""
    assert "Order" in _get_spec(merger_client)["components"]["schemas"]


# ── JSON upstream ─────────────────────────────────────────────────────────────

def test_json_upstream_paths_present(merger_client):
    """service_a.json was fetched and parsed; its paths appear in the merge."""
    spec = _get_spec(merger_client)
    assert "/api/users" in spec["paths"]


def test_json_upstream_schema_present(merger_client):
    """User schema comes exclusively from service_a.json."""
    assert "User" in _get_spec(merger_client)["components"]["schemas"]


# ── refresh param ─────────────────────────────────────────────────────────────

def test_refresh_returns_valid_spec(merger_client):
    """?refresh=true bypasses cache and re-fetches upstreams."""
    r = merger_client.get("/openapi.json?refresh=true")
    assert r.status_code == 200
    spec = r.json()
    assert "/api/users" in spec["paths"]
    assert "/api/orders" in spec["paths"]


# ── total path count sanity check ─────────────────────────────────────────────

def test_total_path_count(merger_client):
    """
    Exactly 6 paths expected after discarding /health (×2) and /internal/stats,
    and rewriting prefixes:
      /api/users, /api/users/{id}, /api/users/{id}/items  (from service A)
      /api/orders, /api/orders/{id}, /api/orders/{id}/items  (from service B)
    """
    spec = _get_spec(merger_client)
    assert len(spec["paths"]) == 6, (
        f"Expected 6 paths, got {len(spec['paths'])}: {sorted(spec['paths'])}"
    )
```

**Step 2: Check for syntax errors**

```bash
python -c "import ast; ast.parse(open('tests/e2e/test_e2e.py').read()); print('ok')"
```
Expected: `ok`

**Step 3: Run the new tests (they should all PASS)**

```bash
.venv/bin/pytest tests/e2e/ -v
```

Expected output — all 20 tests pass, e.g.:
```
tests/e2e/test_e2e.py::test_health_endpoint PASSED
tests/e2e/test_e2e.py::test_merged_spec_has_openapi_field PASSED
...
tests/e2e/test_e2e.py::test_total_path_count PASSED
20 passed in X.XXs
```

**Step 4: Run the full test suite to make sure nothing is broken**

```bash
.venv/bin/pytest -v
```

Expected: all tests pass (existing + new e2e).

**Step 5: Commit**

```bash
git add tests/e2e/test_e2e.py
git commit -m "test(e2e): add full end-to-end integration tests covering all use-cases"
```

---

## Summary

After this plan is executed:

- `tests/e2e/` contains a self-contained E2E suite
- Two real in-process HTTP servers serve static OpenAPI fixtures
- The merger app is exercised end-to-end with real HTTP I/O
- All use-cases are covered with one assertion per concern
- Existing unit/integration tests remain untouched
