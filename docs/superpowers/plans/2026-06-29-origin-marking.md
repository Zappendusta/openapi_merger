# Per-route Origin Marking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp `x-origin-api: "<source name>"` onto every operation in every merged route so the renderer can group by origin and consumers can filter by it.

**Architecture:** The per-source transform step (`transformer.py`, called from `orchestrator.py`) gains an `origin` parameter and mutates each operation dict in place, adding the `x-origin-api` vendor extension. This happens *before* the merge, so all four engines receive identically-marked input and no merger code changes.

**Tech Stack:** Python 3.12+, FastAPI, pytest (`asyncio_mode = "auto"`), respx for HTTP mocking.

## Global Constraints

- Python 3.12+.
- No new dependencies.
- Vendor extension key is exactly `x-origin-api` (lowercase, hyphenated).
- Marker value is the source's existing `name` field, used verbatim.
- `tags` and all other operation/path-item fields are left untouched.
- Only standard HTTP method keys are treated as operations: `get, put, post, delete, options, head, patch, trace`.
- Commit messages: conventional format, **no** `Co-Authored-By` / Claude / Anthropic / AI trailers.

---

### Task 1: Stamp origin in the transformer and wire it through the orchestrator

**Files:**
- Modify: `src/openapi_merger/transformer.py`
- Modify: `src/openapi_merger/orchestrator.py:54-58`
- Test: `tests/test_transformer.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `RouteTransform` (from `openapi_merger.config`), `SourceConfig.name`.
- Produces:
  - `transform_paths(paths: dict, transforms: list[RouteTransform], discard_paths: list[str] = [], origin: str | None = None) -> dict` — when `origin` is a string, each HTTP-method operation in each kept path gets `operation["x-origin-api"] = origin`. When `origin` is `None`, behavior is unchanged (no marking).
  - `OPERATION_METHODS: frozenset[str]` — module-level constant of the eight lowercase HTTP method names.

- [ ] **Step 1: Write the failing transformer tests**

Add to `tests/test_transformer.py`:

```python
def test_origin_stamped_on_operations():
    paths = {"/users": {"get": {"summary": "list"}, "post": {"summary": "create"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"
    assert result["/users"]["post"]["x-origin-api"] == "absence api"


def test_origin_only_on_http_methods():
    paths = {
        "/users": {
            "get": {"summary": "list"},
            "parameters": [{"name": "q", "in": "query"}],
            "summary": "Users path",
            "x-internal": True,
        }
    }
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"
    # non-operation keys are left exactly as they were
    assert result["/users"]["parameters"] == [{"name": "q", "in": "query"}]
    assert result["/users"]["summary"] == "Users path"
    assert result["/users"]["x-internal"] is True


def test_origin_none_no_marking():
    paths = {"/users": {"get": {"summary": "list"}}}
    result = transform_paths(paths, [], origin=None)
    assert result == {"/users": {"get": {"summary": "list"}}}


def test_origin_overwrites_existing():
    paths = {"/users": {"get": {"x-origin-api": "stale"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"


def test_origin_skips_non_dict_operation():
    # malformed operation value must not crash the merge
    paths = {"/users": {"get": "not-a-dict"}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"] == "not-a-dict"


def test_origin_uppercase_method_marked():
    paths = {"/users": {"GET": {"summary": "list"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["GET"]["x-origin-api"] == "absence api"


def test_origin_combined_with_transform_and_discard():
    paths = {
        "/internal/x": {"get": {}},
        "/api/users": {"get": {}},
    }
    result = transform_paths(
        paths,
        [_t("/api", "/api/v2")],
        discard_paths=["/internal"],
        origin="absence api",
    )
    assert "/internal/x" not in result
    assert result["/api/v2/users"]["get"]["x-origin-api"] == "absence api"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `pytest tests/test_transformer.py -k origin -v`
Expected: FAIL — `transform_paths()` got an unexpected keyword argument `origin`.

- [ ] **Step 3: Implement the origin stamping in `transformer.py`**

Replace the full contents of `src/openapi_merger/transformer.py` with:

```python
from openapi_merger.config import RouteTransform

OPERATION_METHODS = frozenset(
    {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
)


def transform_paths(
    paths: dict,
    transforms: list[RouteTransform],
    discard_paths: list[str] = [],
    origin: str | None = None,
) -> dict:
    result = {}
    for path, value in paths.items():
        if any(path == prefix or path.startswith(prefix.rstrip("/") + "/") for prefix in discard_paths):
            continue
        new_path = path
        for t in transforms:
            if new_path.startswith(t.from_path):
                new_path = t.to + new_path[len(t.from_path):]
        if origin is not None and isinstance(value, dict):
            for method, operation in value.items():
                if method.lower() in OPERATION_METHODS and isinstance(operation, dict):
                    operation["x-origin-api"] = origin
        result[new_path] = value
    return result
```

- [ ] **Step 4: Run the transformer tests to verify they pass**

Run: `pytest tests/test_transformer.py -v`
Expected: PASS — the new origin tests plus all pre-existing transform/discard tests.

- [ ] **Step 5: Wire `source.name` through the orchestrator**

In `src/openapi_merger/orchestrator.py`, update the transform call (currently lines 54-58):

```python
                doc["paths"] = transform_paths(
                    doc.get("paths", {}),
                    source.route_transforms,
                    discard_paths=source.discard_paths,
                    origin=source.name,
                )
```

- [ ] **Step 6: Write the failing orchestrator test**

Add to `tests/test_orchestrator.py`:

```python
@respx.mock
async def test_get_merged_stamps_origin_per_source():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    merged = await o.get_merged()

    assert merged["paths"]["/api/users/users"]["get"]["x-origin-api"] == "users"
    assert merged["paths"]["/api/orders/orders"]["get"]["x-origin-api"] == "orders"
```

- [ ] **Step 7: Run the orchestrator test to verify it passes**

Run: `pytest tests/test_orchestrator.py::test_get_merged_stamps_origin_per_source -v`
Expected: PASS. (`_SPEC_A`/`_SPEC_B` already have `{"get": {}}` operations under `/api/users` and `/api/orders`, and the configured transforms rewrite them to `/api/users/users` and `/api/orders/orders`.)

- [ ] **Step 8: Run the full suite**

Run: `pytest`
Expected: PASS — no regressions.

- [ ] **Step 9: Commit**

```bash
git add src/openapi_merger/transformer.py src/openapi_merger/orchestrator.py tests/test_transformer.py tests/test_orchestrator.py
git commit -m "feat: stamp x-origin-api on each operation by source"
```

---

### Task 2: Verify the marker survives every merge engine

**Files:**
- Test: `tests/e2e/test_pluggable_endpoints.py`

**Interfaces:**
- Consumes: the `app_with_real_mergers` fixture and `_skip_if_missing` helper already defined in `tests/e2e/test_pluggable_endpoints.py`. In that fixture, source `alpha` serves `/users` and source `beta` serves `/orders`, each with a single `get` operation. No route transforms are configured there, so paths stay `/users` and `/orders`.

- [ ] **Step 1: Write the preservation tests**

Add to `tests/e2e/test_pluggable_endpoints.py`:

```python
def test_inhouse_endpoint_marks_origin(app_with_real_mergers):
    body = app_with_real_mergers.get("/inhouse/openapi.json").json()
    assert body["paths"]["/users"]["get"]["x-origin-api"] == "alpha"
    assert body["paths"]["/orders"]["get"]["x-origin-api"] == "beta"


def test_redocly_endpoint_preserves_origin(app_with_real_mergers):
    _skip_if_missing("redocly")
    r = app_with_real_mergers.get("/redocly/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paths"]["/users"]["get"]["x-origin-api"] == "alpha"
    assert body["paths"]["/orders"]["get"]["x-origin-api"] == "beta"


def test_speakeasy_endpoint_preserves_origin(app_with_real_mergers):
    _skip_if_missing("speakeasy")
    r = app_with_real_mergers.get("/speakeasy/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paths"]["/users"]["get"]["x-origin-api"] == "alpha"
    assert body["paths"]["/orders"]["get"]["x-origin-api"] == "beta"


def test_openapi_merge_endpoint_preserves_origin(app_with_real_mergers):
    _skip_if_missing("openapi-merge-cli")
    r = app_with_real_mergers.get("/openapi-merge/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["paths"]["/users"]["get"]["x-origin-api"] == "alpha"
    assert body["paths"]["/orders"]["get"]["x-origin-api"] == "beta"
```

- [ ] **Step 2: Run the inhouse preservation test**

Run: `pytest tests/e2e/test_pluggable_endpoints.py::test_inhouse_endpoint_marks_origin -v`
Expected: PASS.

- [ ] **Step 3: Run the external-engine preservation tests**

Run: `pytest tests/e2e/test_pluggable_endpoints.py -k preserves_origin -v`
Expected: PASS for any installed CLI; SKIP for absent ones (existing `_skip_if_missing` pattern). If an installed engine strips operation-level `x-*`, this is where it surfaces — report it rather than working around it.

- [ ] **Step 4: Run the full suite**

Run: `pytest`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/test_pluggable_endpoints.py
git commit -m "test: verify x-origin-api survives every merge engine"
```

---

## Self-Review

**Spec coverage:**
- Operation-level `x-origin-api`, value = source `name` → Task 1, Steps 1-7.
- Only HTTP-method keys marked; other path-item keys untouched → Task 1, `test_origin_only_on_http_methods`, `OPERATION_METHODS`.
- Overwrite existing marker → Task 1, `test_origin_overwrites_existing`.
- Defensive skip of non-dict values → Task 1, `test_origin_skips_non_dict_operation` + `isinstance` guards.
- Marking before merge / engine-agnostic input → Task 1 orchestrator wiring (before `self._strategy.merge`).
- Unit tests (operations marked, non-ops untouched, value matches, combined with transform/discard) → Task 1.
- Preservation across each available engine → Task 2.

**Placeholder scan:** None — every code and command step is concrete.

**Type consistency:** `transform_paths` signature, `OPERATION_METHODS`, and `x-origin-api` key name are identical across all tasks and the design doc.
