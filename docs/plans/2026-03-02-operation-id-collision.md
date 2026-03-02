# Operation ID Collision Detection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add collision detection and prefix-based resolution for `operationId` values across merged OpenAPI sources, mirroring the existing schema collision logic.

**Architecture:** Introduce `detect_operation_id_collisions` alongside `detect_schema_collisions` in `merger.py`, then apply operationId prefixing in `merge_specs` before the path-merge step. Same semantics as schemas: equal-content duplicates are silently ignored; differing-content duplicates get the source prefix prepended.

**Tech Stack:** Python, pytest (same as rest of project). No new dependencies.

---

### Task 1: Add `HTTP_METHODS` constant and `detect_operation_id_collisions`

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

**Step 1: Write the failing tests**

Add to `tests/test_merger.py` (after the existing `detect_schema_collisions` tests, before `merge_specs` tests):

```python
from openapi_merger.merger import detect_operation_id_collisions


# --- detect_operation_id_collisions ---

def _op(op_id, summary="s"):
    return {"operationId": op_id, "summary": summary, "responses": {"200": {}}}


def test_no_op_id_collision():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("listA")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    assert detect_operation_id_collisions(sources) == {}


def test_equal_op_ids_same_content_not_a_collision():
    op = _op("listFoo")
    sources = [
        ("a", "A", {"paths": {"/a": {"get": op}}}),
        ("b", "B", {"paths": {"/b": {"get": op}}}),
    ]
    assert detect_operation_id_collisions(sources) == {}


def test_different_content_same_op_id_is_collision():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("doThing", summary="from a")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("doThing", summary="from b")}}}),
    ]
    collisions = detect_operation_id_collisions(sources)
    assert "doThing" in collisions
    assert set(collisions["doThing"]) == {"a", "b"}


def test_op_id_collision_three_sources_one_differs():
    op = _op("doThing")
    sources = [
        ("a", "A", {"paths": {"/a": {"get": op}}}),
        ("b", "B", {"paths": {"/b": {"get": op}}}),
        ("c", "C", {"paths": {"/c": {"get": _op("doThing", summary="different")}}}),
    ]
    collisions = detect_operation_id_collisions(sources)
    assert "doThing" in collisions


def test_op_id_collision_multiple_methods():
    sources = [
        ("a", "A", {"paths": {"/a": {
            "get": _op("getItem", summary="a"),
            "post": _op("createItem"),
        }}}),
        ("b", "B", {"paths": {"/b": {
            "get": _op("getItem", summary="b"),
        }}}),
    ]
    collisions = detect_operation_id_collisions(sources)
    assert "getItem" in collisions
    assert "createItem" not in collisions


def test_op_id_no_operation_id_field_ignored():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": {"responses": {"200": {}}}}}}),
        ("b", "B", {"paths": {"/b": {"get": {"responses": {"200": {}}}}}}),
    ]
    assert detect_operation_id_collisions(sources) == {}


def test_op_id_source_with_no_paths():
    sources = [
        ("a", "A", {"components": {}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    assert detect_operation_id_collisions(sources) == {}


def test_non_method_keys_in_path_item_ignored():
    # 'parameters' and 'summary' are valid path-item keys, not operations
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("getA"), "parameters": [], "summary": "x"}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("getB")}}}),
    ]
    assert detect_operation_id_collisions(sources) == {}
```

**Step 2: Run tests to confirm they fail**

```
cd /Users/paulusdettmer/dev/skriptor/openapi_merger
source .venv/bin/activate
pytest tests/test_merger.py -k "op_id" -v
```

Expected: `ImportError: cannot import name 'detect_operation_id_collisions'`

**Step 3: Add `HTTP_METHODS` and `detect_operation_id_collisions` to merger.py**

Add after the `rewrite_ref` function (line 19), before `detect_schema_collisions`:

```python
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def detect_operation_id_collisions(sources: list[Source]) -> dict[str, list[str]]:
    """
    Returns operationId -> [source_names] for IDs that appear in multiple
    sources with different operation content. Equal-content duplicates are not
    collisions (mirrors detect_schema_collisions behaviour).
    """
    op_map: dict[str, list[tuple[str, dict]]] = {}
    for source_name, _prefix, doc in sources:
        for _path, path_item in doc.get("paths", {}).items():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId")
                if op_id:
                    op_map.setdefault(op_id, []).append((source_name, operation))

    collisions: dict[str, list[str]] = {}
    for op_id, entries in op_map.items():
        if len(entries) <= 1:
            continue
        first = entries[0][1]
        if all(e[1] == first for e in entries[1:]):
            continue  # all equal: not a collision
        collisions[op_id] = [e[0] for e in entries]
    return collisions
```

Also update the import line in `test_merger.py` to include the new function (top of file):

```python
from openapi_merger.merger import (
    rewrite_ref,
    detect_schema_collisions,
    detect_operation_id_collisions,
    merge_specs,
)
```

**Step 4: Run tests to confirm they pass**

```
pytest tests/test_merger.py -k "op_id" -v
```

Expected: all 8 new tests PASS.

**Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: add detect_operation_id_collisions"
```

---

### Task 2: Apply operation ID prefixing in `merge_specs`

**Files:**
- Modify: `src/openapi_merger/merger.py:43-59` (the `merge_specs` preprocessing loop)
- Test: `tests/test_merger.py`

**Step 1: Write the failing integration tests**

Add to `tests/test_merger.py` (after the existing `merge_specs` tests):

```python
def _doc_with_op(path, method, op_id, summary="s", schemas=None):
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {path: {method: {"operationId": op_id, "summary": summary, "responses": {"200": {}}}}},
        "components": {"schemas": schemas or {}},
    }


def test_merge_no_op_id_collision():
    sources = [
        ("a", "A", _doc_with_op("/a", "get", "listA")),
        ("b", "B", _doc_with_op("/b", "get", "listB")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "listA"
    assert merged["paths"]["/b"]["get"]["operationId"] == "listB"


def test_merge_op_id_collision_resolved_with_prefix():
    sources = [
        ("a", "A", _doc_with_op("/a", "get", "doThing", summary="from a")),
        ("b", "B", _doc_with_op("/b", "get", "doThing", summary="from b")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "AdoThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BdoThing"


def test_merge_equal_op_ids_not_prefixed():
    op_def = {"operationId": "doThing", "summary": "same", "responses": {"200": {}}}
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/a": {"get": op_def}},
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/b": {"get": op_def}},
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "doThing"


def test_merge_op_id_collision_multiple_methods():
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/a": {
                "get": {"operationId": "getItem", "summary": "a", "responses": {"200": {}}},
                "post": {"operationId": "createItem", "responses": {"200": {}}},
            }},
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/b": {
                "get": {"operationId": "getItem", "summary": "b", "responses": {"200": {}}},
            }},
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "AgetItem"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BgetItem"
    assert merged["paths"]["/a"]["post"]["operationId"] == "createItem"  # no collision
```

**Step 2: Run tests to confirm they fail**

```
pytest tests/test_merger.py -k "merge_op_id or merge_no_op_id or merge_equal_op" -v
```

Expected: `test_merge_op_id_collision_resolved_with_prefix` FAILS (operationId not prefixed yet).

**Step 3: Apply operationId prefixing in `merge_specs`**

In `merger.py`, modify the `merge_specs` function. Replace the preprocessing loop (lines 44-59) with:

```python
def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)
    op_collisions = detect_operation_id_collisions(sources)

    # Apply prefix only to colliding schema names (and their $refs) per source,
    # and to colliding operationIds per source.
    processed: list[Source] = []
    for source_name, prefix, doc in sources:
        doc = copy.deepcopy(doc)

        # --- Schema collision resolution (unchanged) ---
        colliding_schemas = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding_schemas:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            doc = rewrite_ref(doc, name, new_name)

        # --- Operation ID collision resolution ---
        colliding_op_ids = {
            op_id for op_id, names in op_collisions.items() if source_name in names
        }
        for path_item in doc.get("paths", {}).values():
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if isinstance(operation, dict) and operation.get("operationId") in colliding_op_ids:
                    operation["operationId"] = f"{prefix}{operation['operationId']}"

        processed.append((source_name, prefix, doc))
```

Leave the rest of `merge_specs` (path merge, schema merge, component merge, return) unchanged.

**Step 4: Run all merger tests**

```
pytest tests/test_merger.py -v
```

Expected: all tests PASS (no regressions).

**Step 5: Run full test suite**

```
pytest -v
```

Expected: all tests PASS.

**Step 6: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: resolve operationId collisions with source prefix"
```

---

## Summary

Two tasks, two commits:
1. `detect_operation_id_collisions` function + tests
2. Wire it into `merge_specs` + integration tests

The approach mirrors schema collision detection exactly: same operationId with equal content → not a collision; same operationId with differing content → prefix both with their source prefix.
