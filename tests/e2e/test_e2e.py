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
    for original in ("/users", "/users/{id}", "/users/{id}/items", "/orders", "/orders/{id}", "/orders/{id}/items"):
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
    assert "/internal/stats" not in paths  # known fixture path from service_a.json
    for path in paths:
        assert not path.startswith("/internal"), (
            f"Path {path!r} should have been discarded (internal prefix)"
        )


# ── schema collision avoidance ────────────────────────────────────────────────

def test_colliding_schema_prefixed_for_users(merger_client):
    """Service A's Item (qty field) -> UsersItem."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "UsersItem" in schemas
    assert "qty" in schemas["UsersItem"].get("properties", {})


def test_colliding_schema_prefixed_for_orders(merger_client):
    """Service B's Item (price field) -> OrdersItem."""
    schemas = _get_spec(merger_client)["components"]["schemas"]
    assert "OrdersItem" in schemas
    assert "price" in schemas["OrdersItem"].get("properties", {})


def test_original_item_schema_absent(merger_client):
    """The bare 'Item' name must not exist -- it should be prefixed in both cases."""
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
    path_item = spec["paths"].get("/api/users/{id}/items", {})
    schema_ref = (
        path_item.get("get", {})
        .get("responses", {}).get("200", {})
        .get("content", {}).get("application/json", {})
        .get("schema", {}).get("$ref")
    )
    assert schema_ref == "#/components/schemas/UsersItem", (
        f"Expected #/components/schemas/UsersItem, got {schema_ref!r}. "
        f"Full path item: {path_item}"
    )


def test_ref_rewritten_in_orders_items_path(merger_client):
    """$ref in /api/orders/{id}/items response rewritten from Item → OrdersItem."""
    spec = _get_spec(merger_client)
    path_item = spec["paths"].get("/api/orders/{id}/items", {})
    schema_ref = (
        path_item.get("get", {})
        .get("responses", {}).get("200", {})
        .get("content", {}).get("application/json", {})
        .get("schema", {}).get("$ref")
    )
    assert schema_ref == "#/components/schemas/OrdersItem", (
        f"Expected #/components/schemas/OrdersItem, got {schema_ref!r}. "
        f"Full path item: {path_item}"
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
    Exactly 6 paths expected after discarding /health (x2) and /internal/stats,
    and rewriting prefixes:
      /api/users, /api/users/{id}, /api/users/{id}/items  (from service A)
      /api/orders, /api/orders/{id}, /api/orders/{id}/items  (from service B)
    """
    spec = _get_spec(merger_client)
    assert len(spec["paths"]) == 6, (
        f"Expected 6 paths, got {len(spec['paths'])}: {sorted(spec['paths'])}"
    )
