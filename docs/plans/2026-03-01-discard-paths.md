# Discard Paths Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Allow each source to declare 0–N path prefixes that are silently dropped before route transforms and merging.

**Architecture:** Add `discard_paths: list[str]` to `SourceConfig`. In `transform_paths`, filter out any path that starts with a discard prefix before applying transforms. Pass `discard_paths` from the orchestrator.

**Tech Stack:** Python 3.12, Pydantic v2, pytest

---

## Context

**Existing files touched:**
- `src/openapi_merger/config.py` — Pydantic models
- `src/openapi_merger/transformer.py` — path rewriting (currently 13 lines)
- `src/openapi_merger/orchestrator.py` — wires fetch → transform → merge
- `tests/test_config.py` — config parsing tests
- `tests/test_transformer.py` — transformer tests
- `example/sources.yaml` — user-facing example
- `README.md` — docs

**Pattern already in use:** `route_transforms` uses `str.startswith` prefix matching, not real regex. `discard_paths` follows the same convention — a path is discarded if it starts with any discard prefix.

**Discard before or after transforms?** Discard first (on original upstream paths), then transform remaining ones. This matches the mental model: "exclude these upstream paths I don't want at all."

**Run tests with:** `pip install -e ".[dev]" && python3 -m pytest -v`

---

### Task 1: Add `discard_paths` to config

**Files:**
- Modify: `src/openapi_merger/config.py:18-23`
- Modify: `tests/test_config.py`

**Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_source_discard_paths_defaults_empty():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://svc/openapi.json",
            "schema_prefix": "Svc",
        }]
    })
    assert cfg.sources[0].discard_paths == []


def test_source_with_discard_paths():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://svc/openapi.json",
            "schema_prefix": "Svc",
            "discard_paths": ["/internal", "/health"],
        }]
    })
    assert cfg.sources[0].discard_paths == ["/internal", "/health"]
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_config.py::test_source_discard_paths_defaults_empty tests/test_config.py::test_source_with_discard_paths -v
```

Expected: FAIL with `ValidationError` or `AttributeError`

**Step 3: Add the field to `SourceConfig`**

In `src/openapi_merger/config.py`, change the `SourceConfig` class:

```python
class SourceConfig(BaseModel):
    name: str
    url: str
    schema_prefix: str
    auth: AuthConfig | None = None
    route_transforms: list[RouteTransform] = []
    discard_paths: list[str] = []
```

**Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_config.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/openapi_merger/config.py tests/test_config.py
git commit -m "feat: add discard_paths field to SourceConfig"
```

---

### Task 2: Implement discard logic in transformer

**Files:**
- Modify: `src/openapi_merger/transformer.py`
- Modify: `tests/test_transformer.py`

**Step 1: Write the failing tests**

Add to `tests/test_transformer.py`:

```python
def test_discard_path_exact_prefix():
    paths = {"/internal/health": {}, "/api/users": {}}
    result = transform_paths(paths, [], discard_paths=["/internal"])
    assert "/internal/health" not in result
    assert "/api/users" in result


def test_discard_multiple_prefixes():
    paths = {"/internal/x": {}, "/debug/y": {}, "/api/z": {}}
    result = transform_paths(paths, [], discard_paths=["/internal", "/debug"])
    assert "/internal/x" not in result
    assert "/debug/y" not in result
    assert "/api/z" in result


def test_discard_empty_list_keeps_all():
    paths = {"/a": {}, "/b": {}}
    result = transform_paths(paths, [], discard_paths=[])
    assert result == {"/a": {}, "/b": {}}


def test_discard_before_transform():
    # /internal is discarded before the /internal→/api transform is considered
    paths = {"/internal/secret": {}, "/other/path": {}}
    result = transform_paths(
        paths,
        [_t("/internal", "/api")],
        discard_paths=["/internal"],
    )
    assert "/internal/secret" not in result
    assert "/api/secret" not in result  # discard wins, transform not applied
    assert "/other/path" in result


def test_discard_no_partial_match():
    # /internal should NOT discard /internalize
    paths = {"/internalize": {}, "/internal/x": {}}
    result = transform_paths(paths, [], discard_paths=["/internal/"])
    assert "/internalize" in result
    assert "/internal/x" not in result
```

**Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_transformer.py::test_discard_path_exact_prefix -v
```

Expected: FAIL with `TypeError` (unexpected keyword argument)

**Step 3: Update `transform_paths` to accept and apply `discard_paths`**

Replace `src/openapi_merger/transformer.py` entirely:

```python
from openapi_merger.config import RouteTransform


def transform_paths(
    paths: dict,
    transforms: list[RouteTransform],
    discard_paths: list[str] = [],
) -> dict:
    result = {}
    for path, value in paths.items():
        if any(path.startswith(prefix) for prefix in discard_paths):
            continue
        new_path = path
        for t in transforms:
            if new_path.startswith(t.from_path):
                new_path = t.to + new_path[len(t.from_path):]
        result[new_path] = value
    return result
```

**Step 4: Run all transformer tests**

```bash
python3 -m pytest tests/test_transformer.py -v
```

Expected: all PASS

**Step 5: Commit**

```bash
git add src/openapi_merger/transformer.py tests/test_transformer.py
git commit -m "feat: filter discard_paths before applying route transforms"
```

---

### Task 3: Wire `discard_paths` through the orchestrator

**Files:**
- Modify: `src/openapi_merger/orchestrator.py:26`
- Modify: `tests/test_orchestrator.py`

**Step 1: Read the current orchestrator test to understand mock setup**

Check `tests/test_orchestrator.py` to understand how `respx` mocks are used.

**Step 2: Write a failing test**

In `tests/test_orchestrator.py`, add a test that verifies discarded paths are absent from the merged output. Look at the existing test structure; a minimal addition like:

```python
@pytest.mark.asyncio
async def test_discard_paths_excluded(service_cfg, tmp_path):
    """Paths matching discard_paths are absent from the merged spec."""
    sources_cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://svc/openapi.json",
            "schema_prefix": "Svc",
            "discard_paths": ["/internal"],
        }]
    })
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {"/internal/secret": {}, "/api/users": {}},
        "components": {"schemas": {}},
    }
    with respx.mock:
        respx.get("http://svc/openapi.json").respond(json=spec)
        orch = MergeOrchestrator(service_cfg, sources_cfg)
        merged = await orch.get_merged()
    assert "/internal/secret" not in merged["paths"]
    assert "/api/users" in merged["paths"]
```

Adapt fixture names (`service_cfg`, `respx.mock`) to match what's already in the file.

**Step 3: Run the test to verify it fails**

```bash
python3 -m pytest tests/test_orchestrator.py::test_discard_paths_excluded -v
```

Expected: FAIL (path is not filtered)

**Step 4: Update the orchestrator call**

In `src/openapi_merger/orchestrator.py`, change line 26–28:

```python
            doc["paths"] = transform_paths(
                doc.get("paths", {}),
                source.route_transforms,
                discard_paths=source.discard_paths,
            )
```

**Step 5: Run all tests**

```bash
python3 -m pytest -v
```

Expected: all PASS

**Step 6: Commit**

```bash
git add src/openapi_merger/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: pass discard_paths from source config through orchestrator"
```

---

### Task 4: Update example and README docs

**Files:**
- Modify: `example/sources.yaml`
- Modify: `README.md`

**Step 1: Update `example/sources.yaml`**

Add a commented-out `discard_paths` block to the first source:

```yaml
sources:
  - name: users
    url: http://users-service/openapi.json
    schema_prefix: Users
    # auth:
    #   username: svc
    #   password: secret
    # discard_paths:          # drop these upstream paths entirely
    #   - /internal
    #   - /health
    route_transforms:
      - from: /api
        to: /api/users

  - name: orders
    url: http://orders-service/openapi.yaml
    schema_prefix: Orders
    route_transforms:
      - from: /api
        to: /api/orders
```

**Step 2: Update README.md `sources.yaml` section**

In the `sources.yaml` example block, add `discard_paths` documentation:

```yaml
sources:
  - name: users
    url: http://users-service/openapi.json
    schema_prefix: Users     # prefix applied to schemas on collision
    # auth:                  # optional upstream Basic Auth
    #   username: svc
    #   password: secret
    discard_paths:           # drop these upstream paths before merging
      - /internal            # any path starting with /internal/ is excluded
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

Also update the prose description (item 2 under "What it does") to mention discarding:

```markdown
2. **Transforms** route prefixes for each source independently, and optionally
   **discards** unwanted upstream paths by prefix
   (e.g. `discard_paths: [/internal]` drops all paths starting with `/internal`).
```

**Step 3: Run full test suite one final time**

```bash
python3 -m pytest -v
```

Expected: all PASS

**Step 4: Commit**

```bash
git add example/sources.yaml README.md
git commit -m "docs: document discard_paths in example config and README"
```
