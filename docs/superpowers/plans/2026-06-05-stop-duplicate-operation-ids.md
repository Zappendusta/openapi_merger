# Stop Duplicate operationIds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Guarantee every operation in the merged OpenAPI document has a unique `operationId`, eliminating within-source duplicates, cross-source duplicates, and duplicates introduced by source-prefix collisions.

**Architecture:** Replace the current two-pass approach (`detect_operation_id_collisions` + targeted prefix application in `merge_specs`) with a single-pass walk that mirrors `npx openapi-merge-cli`'s algorithm: maintain a global `seen` set, walk sources in order, and for each operationId conflict apply the source's existing schema/dispute prefix; if that still collides, append numeric suffixes (`_2`, `_3`, ...). Drops the existing "equal-content duplicates are not collisions" exemption because OpenAPI 3.x requires `operationId` to be unique across the document regardless of content equality. All renames are logged via the existing `merge.collision.operation_id` structured warning, with an added `reason` field distinguishing `within_source`, `cross_source`, and `post_prefix`.

**Tech Stack:** Python 3.12, structlog, pytest.

---

## File Structure

- **Modify** `src/openapi_merger/merger.py` — remove `detect_operation_id_collisions`, add `assign_unique_operation_ids`, replace the op-id resolution block in `merge_specs`.
- **Modify** `tests/test_merger.py` — drop the eight `detect_operation_id_collisions` unit tests (function deleted), update three `merge_specs` tests whose expectations change under the new algorithm, add five new `merge_specs` tests covering within-source, post-prefix, multi-source-with-shared-prefix, equal-content-now-dedupes, and numeric-suffix overflow.
- **Modify** `README.md` — update the logging table entry for `merge.collision.operation_id` to reflect the new `reason` field and that all duplicates (not only differing content) are now resolved.

---

## Task 1: Add `assign_unique_operation_ids` with TDD

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Add this block at the top of `tests/test_merger.py` (after the existing imports, before the `rewrite_ref` tests). It replaces the entire `# --- detect_operation_id_collisions ---` section (lines 93–174). Delete those eight tests now; they test a function that is going away.

```python
# Replace the import line
from openapi_merger.merger import (
    rewrite_ref,
    detect_schema_collisions,
    assign_unique_operation_ids,
    merge_specs,
)
```

Then add this new test section in place of the deleted `detect_operation_id_collisions` tests:

```python
# --- assign_unique_operation_ids ---

def _op(op_id, summary="s"):
    return {"operationId": op_id, "summary": summary, "responses": {"200": {}}}


def test_assign_op_ids_no_conflicts_unchanged():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("listA")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "listA"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "listB"


def test_assign_op_ids_cross_source_uses_prefix():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("doThing", summary="from a")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("doThing", summary="from b")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "BdoThing"
    assert renames == [
        {"source": "b", "path": "/b", "method": "get", "old": "doThing", "new": "BdoThing", "reason": "cross_source"},
    ]


def test_assign_op_ids_equal_content_still_deduped():
    # Was previously exempted. OpenAPI requires unique operationIds regardless of content.
    op = _op("doThing")
    sources = [
        ("a", "A", {"paths": {"/a": {"get": copy.deepcopy(op)}}}),
        ("b", "B", {"paths": {"/b": {"get": copy.deepcopy(op)}}}),
    ]
    assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "BdoThing"


def test_assign_op_ids_within_source_uses_prefix():
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("getItem", summary="first")},
            "/y": {"get": _op("getItem", summary="second")},
        }}),
    ]
    renames = assign_unique_operation_ids(sources)
    # Path iteration order is insertion order (Python 3.7+).
    # First occurrence keeps the id; second gets prefixed.
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "getItem"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "AgetItem"
    assert len(renames) == 1
    assert renames[0]["reason"] == "within_source"


def test_assign_op_ids_post_prefix_uses_numeric_suffix():
    # Source A has both 'foo' and 'Bfoo'. Source B has 'foo'.
    # Source B's 'foo' conflicts -> tries 'Bfoo' (taken) -> falls back to 'foo_2'.
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("foo", summary="a-foo")},
            "/y": {"get": _op("Bfoo", summary="a-Bfoo")},
        }}),
        ("b", "B", {"paths": {"/z": {"get": _op("foo", summary="b-foo")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "foo"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "Bfoo"
    assert sources[1][2]["paths"]["/z"]["get"]["operationId"] == "foo_2"
    assert any(r["reason"] == "post_prefix" and r["new"] == "foo_2" for r in renames)


def test_assign_op_ids_within_source_chain_uses_numeric_after_prefix():
    # Same source has 'bar', 'Abar', and a third 'bar'. Third -> 'Abar' taken -> 'bar_2'.
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("bar", summary="1")},
            "/y": {"get": _op("Abar", summary="2")},
            "/z": {"get": _op("bar", summary="3")},
        }}),
    ]
    assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "bar"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "Abar"
    assert sources[0][2]["paths"]["/z"]["get"]["operationId"] == "bar_2"


def test_assign_op_ids_missing_field_ignored():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": {"responses": {"200": {}}}}}}),
        ("b", "B", {"paths": {"/b": {"get": {"responses": {"200": {}}}}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    # No operationId added either way.
    assert "operationId" not in sources[0][2]["paths"]["/a"]["get"]
    assert "operationId" not in sources[1][2]["paths"]["/b"]["get"]


def test_assign_op_ids_non_method_keys_ignored():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("getA"), "parameters": [], "summary": "x"}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "getA"


def test_assign_op_ids_no_paths_ok():
    sources = [
        ("a", "A", {"components": {}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "listB"


def test_assign_op_ids_numeric_suffix_overflow_raises():
    # If 1000 attempts can't find a unique id, raise. Construct a worst case.
    base = "foo"
    paths = {f"/p{i}": {"get": _op(base if i == 0 else f"{base}_{i}", summary=str(i))} for i in range(1001)}
    sources = [("a", "", paths_to_doc(paths))]
    with pytest.raises(RuntimeError, match="could not assign unique operationId"):
        assign_unique_operation_ids(sources)


def paths_to_doc(paths):
    return {"paths": paths}
```

Add `import copy` to the test file imports if not already present (it isn't at the top — check line 1).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -v -k "assign_op_ids"`
Expected: All tests in the new section fail with `ImportError: cannot import name 'assign_unique_operation_ids'`.

- [ ] **Step 3: Implement `assign_unique_operation_ids` in merger.py**

In `src/openapi_merger/merger.py`, replace the existing `detect_operation_id_collisions` function (lines 29–55) with this new function:

```python
def assign_unique_operation_ids(sources: list[Source]) -> list[dict]:
    """
    Walk all operations across all sources in order and ensure every operationId
    is globally unique. Mutates source docs in place.

    Resolution strategy per conflict:
      1. Try the source's prefix: `{prefix}{op_id}`
      2. Still taken (or prefix is empty): append `_2`, `_3`, ... up to `_1000`.

    Returns a list of rename records: {source, path, method, old, new, reason}
    where reason is "within_source", "cross_source", or "post_prefix".
    """
    seen: set[str] = {}  # placeholder, replaced below
    seen = set()
    # Track which source each seen id originated from so we can classify renames.
    origin: dict[str, str] = {}
    renames: list[dict] = []

    for source_name, prefix, doc in sources:
        for path, path_item in doc.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                if op_id not in seen:
                    seen.add(op_id)
                    origin[op_id] = source_name
                    continue

                # Conflict. Classify the reason for the rename.
                if origin[op_id] == source_name:
                    reason = "within_source"
                else:
                    reason = "cross_source"

                # Step 1: try source prefix.
                candidate = f"{prefix}{op_id}" if prefix else op_id
                if not prefix or candidate in seen:
                    # Step 2: numeric suffix fallback.
                    base = candidate if (prefix and candidate not in seen) else op_id
                    # If prefix was empty or its candidate was taken, fall back to suffix on op_id.
                    found = False
                    for n in range(2, 1001):
                        suffixed = f"{op_id}_{n}"
                        if suffixed not in seen:
                            candidate = suffixed
                            found = True
                            # If we tried the prefix and it was taken, reason becomes post_prefix.
                            if prefix and f"{prefix}{op_id}" in seen:
                                reason = "post_prefix"
                            break
                    if not found:
                        raise RuntimeError(
                            f"could not assign unique operationId for '{op_id}' in source '{source_name}' "
                            f"after 1000 attempts"
                        )

                operation["operationId"] = candidate
                seen.add(candidate)
                origin[candidate] = source_name
                renames.append({
                    "source": source_name,
                    "path": path,
                    "method": method,
                    "old": op_id,
                    "new": candidate,
                    "reason": reason,
                })

    return renames
```

The dead `seen: set[str] = {}` placeholder line is a typo guard — delete it; the real `seen = set()` is the next line. Final clean version:

```python
def assign_unique_operation_ids(sources: list[Source]) -> list[dict]:
    """
    Walk all operations across all sources in order and ensure every operationId
    is globally unique. Mutates source docs in place.

    Resolution strategy per conflict:
      1. Try the source's prefix: `{prefix}{op_id}`
      2. Still taken (or prefix is empty): append `_2`, `_3`, ... up to `_1000`.

    Returns a list of rename records: {source, path, method, old, new, reason}
    where reason is "within_source", "cross_source", or "post_prefix".
    """
    seen: set[str] = set()
    origin: dict[str, str] = {}
    renames: list[dict] = []

    for source_name, prefix, doc in sources:
        for path, path_item in doc.get("paths", {}).items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in HTTP_METHODS:
                    continue
                if not isinstance(operation, dict):
                    continue
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                if op_id not in seen:
                    seen.add(op_id)
                    origin[op_id] = source_name
                    continue

                reason = "within_source" if origin[op_id] == source_name else "cross_source"

                candidate: str | None = None
                prefixed = f"{prefix}{op_id}" if prefix else None
                if prefixed and prefixed not in seen:
                    candidate = prefixed
                else:
                    if prefixed is not None:
                        reason = "post_prefix"
                    for n in range(2, 1001):
                        suffixed = f"{op_id}_{n}"
                        if suffixed not in seen:
                            candidate = suffixed
                            break
                    if candidate is None:
                        raise RuntimeError(
                            f"could not assign unique operationId for '{op_id}' in source '{source_name}' "
                            f"after 1000 attempts"
                        )

                operation["operationId"] = candidate
                seen.add(candidate)
                origin[candidate] = source_name
                renames.append({
                    "source": source_name,
                    "path": path,
                    "method": method,
                    "old": op_id,
                    "new": candidate,
                    "reason": reason,
                })

    return renames
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -v -k "assign_op_ids"`
Expected: All ten `assign_op_ids_*` tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: add assign_unique_operation_ids resolver"
```

---

## Task 2: Wire `assign_unique_operation_ids` into `merge_specs`

**Files:**
- Modify: `src/openapi_merger/merger.py:79-126`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Update the affected `merge_specs` tests to match new behavior**

Three existing tests have expectations that change. Update them in `tests/test_merger.py`:

Replace `test_merge_op_id_collision_resolved_with_prefix` (lines 286–293) with:

```python
def test_merge_op_id_collision_resolved_with_prefix():
    sources = [
        ("a", "A", _doc_with_op("/a", "get", "doThing", summary="from a")),
        ("b", "B", _doc_with_op("/b", "get", "doThing", summary="from b")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    # First source keeps the id; second gets prefixed.
    assert merged["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BdoThing"
```

Replace `test_merge_equal_op_ids_not_prefixed` (lines 296–312) with:

```python
def test_merge_equal_op_ids_now_deduped():
    # Was previously exempted (equal content = not a collision). OpenAPI 3.x
    # requires unique operationIds regardless of content, so we now always dedupe.
    op_def = {"operationId": "doThing", "summary": "same", "responses": {"200": {}}}
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/a": {"get": copy.deepcopy(op_def)}},
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/b": {"get": copy.deepcopy(op_def)}},
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BdoThing"
```

Replace `test_merge_op_id_collision_multiple_methods` (lines 315–336) — the assertion for source `a` changes because it's no longer prefixed:

```python
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
    assert merged["paths"]["/a"]["get"]["operationId"] == "getItem"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BgetItem"
    assert merged["paths"]["/a"]["post"]["operationId"] == "createItem"
```

Also add `import copy` to the top of `tests/test_merger.py` if not already present.

Add a new integration test for within-source collisions resolved via `merge_specs`:

```python
def test_merge_within_source_op_id_collision_resolved():
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {"get": {"operationId": "getItem", "summary": "1", "responses": {"200": {}}}},
                "/y": {"get": {"operationId": "getItem", "summary": "2", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/x"]["get"]["operationId"] == "getItem"
    assert merged["paths"]["/y"]["get"]["operationId"] == "AgetItem"


def test_merge_all_op_ids_unique_in_output():
    # Property-style check: across the merged paths, every operationId is unique.
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {"get": {"operationId": "dup", "summary": "ax", "responses": {"200": {}}}},
                "/y": {"get": {"operationId": "dup", "summary": "ay", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/z": {"get": {"operationId": "dup", "summary": "bz", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    op_ids = [
        op["operationId"]
        for path_item in merged["paths"].values()
        for method, op in path_item.items()
        if method in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
        and isinstance(op, dict)
        and "operationId" in op
    ]
    assert len(op_ids) == len(set(op_ids)), f"duplicate operationIds in merged output: {op_ids}"
```

- [ ] **Step 2: Run tests to verify the updated ones fail and the new ones fail**

Run: `pytest tests/test_merger.py -v -k "merge_op_id or within_source_op_id or all_op_ids_unique or equal_op_ids"`
Expected: The updated and new tests fail because `merge_specs` still uses the old detection+prefix logic.

- [ ] **Step 3: Rewrite the operationId block inside `merge_specs`**

In `src/openapi_merger/merger.py`, replace lines 79–126 of the current `merge_specs` (the block that calls `detect_operation_id_collisions`, logs warnings, deep-copies, and applies prefix to colliding ids). The new structure:

```python
def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)

    for name, sources_with_name in collisions.items():
        log.warning(
            "merge.collision.schema",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )

    # Deep-copy sources up front so we never mutate caller-owned docs.
    working_sources: list[Source] = [
        (source_name, prefix, copy.deepcopy(doc)) for source_name, prefix, doc in sources
    ]

    # Apply schema prefixing (unchanged semantics).
    for source_name, prefix, doc in working_sources:
        colliding_schemas = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding_schemas:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            # rewrite_ref returns a new structure; rebind via index lookup.
        # rewrite_ref is functional — rebuild doc after each rename.
        # (See loop body below — we use a small helper to apply all renames at once.)

    # Schema $ref rewriting needs to operate on the doc as a value; rebuild the list.
    processed: list[Source] = []
    for source_name, prefix, doc in working_sources:
        colliding_schemas = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding_schemas:
            new_name = f"{prefix}{name}"
            doc = rewrite_ref(doc, name, new_name)
        processed.append((source_name, prefix, doc))

    # Resolve operationIds across the full set, mutating in place.
    renames = assign_unique_operation_ids(processed)
    for r in renames:
        log.warning(
            "merge.collision.operation_id",
            operation_id=r["old"],
            new_operation_id=r["new"],
            source=r["source"],
            path=r["path"],
            method=r["method"],
            reason=r["reason"],
            resolution="prefix" if r["reason"] in {"within_source", "cross_source"} else "numeric_suffix",
        )

    # ... rest of merge_specs (path/schema/component merge) unchanged below.
```

The block above has two passes — the first does the schema name swap, the second rewrites `$ref`. Rewriting `$ref` returns a new tree, so we rebuild the list. The cleaner final version that fits the existing code shape:

```python
def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)

    for name, sources_with_name in collisions.items():
        log.warning(
            "merge.collision.schema",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )

    # Apply schema prefix and $ref rewrites per source (semantics unchanged from before).
    processed: list[Source] = []
    for source_name, prefix, doc in sources:
        doc = copy.deepcopy(doc)
        colliding_schemas = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding_schemas:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            doc = rewrite_ref(doc, name, new_name)
        processed.append((source_name, prefix, doc))

    # Resolve operationIds globally across the full processed set, mutating in place.
    renames = assign_unique_operation_ids(processed)
    for r in renames:
        log.warning(
            "merge.collision.operation_id",
            operation_id=r["old"],
            new_operation_id=r["new"],
            source=r["source"],
            path=r["path"],
            method=r["method"],
            reason=r["reason"],
            resolution="prefix" if r["reason"] in {"within_source", "cross_source"} else "numeric_suffix",
        )

    # Merge paths — error on duplicates
    merged_paths: dict = {}
    for source_name, _prefix, doc in processed:
        for path, value in doc.get("paths", {}).items():
            if path in merged_paths:
                log.error(
                    "merge.path_collision",
                    path=path,
                    source=source_name,
                )
                raise RuntimeError(
                    f"Path collision: '{path}' found in '{source_name}' and an earlier source"
                )
            merged_paths[path] = value

    # Merge schemas — equal duplicates are silently deduped
    merged_schemas: dict = {}
    for _source_name, _prefix, doc in processed:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            if name not in merged_schemas:
                merged_schemas[name] = schema

    # Merge other component sub-objects
    other_component_keys = {
        "responses", "parameters", "requestBodies",
        "headers", "examples", "links", "callbacks",
    }
    merged_components: dict = {"schemas": merged_schemas}
    for _source_name, _prefix, doc in processed:
        for key in other_component_keys:
            items = doc.get("components", {}).get(key, {})
            if items:
                merged_components.setdefault(key, {}).update(items)

    openapi_version = next(
        (doc.get("openapi", "3.0.0") for _, _, doc in processed), "3.0.0"
    )

    return {
        "openapi": openapi_version,
        "info": {"title": title, "version": version},
        "paths": merged_paths,
        "components": merged_components,
    }
```

Note: `detect_operation_id_collisions` is no longer referenced anywhere. Delete the old function (the one currently at lines 29–55) — Task 1's diff already removed it, so this step is just confirming.

- [ ] **Step 4: Run the full merger test suite**

Run: `pytest tests/test_merger.py -v`
Expected: All tests pass — original `rewrite_ref` and `detect_schema_collisions` tests, the new `assign_op_ids_*` tests from Task 1, the updated `merge_op_id_*` tests, and the new `merge_within_source_op_id_collision_resolved` + `merge_all_op_ids_unique_in_output` tests.

- [ ] **Step 5: Run the full test suite to check nothing else regressed**

Run: `pytest -v`
Expected: All tests pass. If `tests/test_integration.py`, `tests/test_orchestrator.py`, `tests/test_logging_integration.py`, or `tests/e2e/*` reference `detect_operation_id_collisions` or assert specific operationId values after merge, they need fixing. Check now:

```bash
grep -rn "detect_operation_id_collisions\|operationId" tests/
```

If any test outside `tests/test_merger.py` asserts on operationId values that change under the new algorithm (the "first occurrence keeps id, later ones get prefixed" behavior), update its expected value. From the earlier survey, only `tests/test_merger.py` references `detect_operation_id_collisions`, so the grep should be clean of that symbol after Task 1; the `operationId` grep will list test fixtures — verify none of their assertions depended on the old prefix-everything behavior.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: globally enforce unique operationIds in merged specs"
```

---

## Task 3: Update README logging documentation

**Files:**
- Modify: `README.md:149`

- [ ] **Step 1: Read the current logging table**

Run: `grep -n "merge.collision" README.md`
Expected output: lines 148 and 149 show the two collision rows.

- [ ] **Step 2: Update the `merge.collision.operation_id` row**

Replace the existing line 149 in `README.md`:

```
| `merge.collision.operation_id`    | warning | Same operationId with different content — resolved by prefixing. |
```

With:

```
| `merge.collision.operation_id`    | warning | Duplicate operationId — resolved by source prefix or numeric suffix. `reason` field: `within_source`, `cross_source`, or `post_prefix`. |
```

- [ ] **Step 3: Verify the change**

Run: `grep -n "merge.collision.operation_id" README.md`
Expected: one line, showing the new wording with `reason` field reference.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: document new operationId dedupe reasons"
```

---

## Task 4: Final verification

**Files:** none modified.

- [ ] **Step 1: Run the entire test suite**

Run: `pytest -v`
Expected: 100% pass. Capture the count for the PR description.

- [ ] **Step 2: Confirm no stale references to the removed function**

Run: `grep -rn "detect_operation_id_collisions" src/ tests/`
Expected: no output. (The `docs/plans/2026-03-02-operation-id-collision.md` historical plan still mentions it — that's a frozen artifact, leave it.)

- [ ] **Step 3: Manually inspect a merged-spec scenario end-to-end**

Run: `python -c "
import copy
from openapi_merger.merger import merge_specs
op = lambda i, s='x': {'operationId': i, 'summary': s, 'responses': {'200': {}}}
sources = [
    ('a', 'A', {'openapi': '3.0.0', 'info': {'title':'T','version':'1'},
        'paths': {'/x': {'get': op('dup')}, '/y': {'get': op('dup', 'y')}},
        'components': {'schemas': {}}}),
    ('b', 'B', {'openapi': '3.0.0', 'info': {'title':'T','version':'1'},
        'paths': {'/z': {'get': op('dup', 'z')}},
        'components': {'schemas': {}}}),
]
merged = merge_specs(sources, 'T', '1')
import json; print(json.dumps({p: list(m.keys()) for p, m in merged['paths'].items()}, indent=2))
print('op ids:', [m['get']['operationId'] for p, m in merged['paths'].items()])
"`
Expected output:
```
{
  "/x": ["get"],
  "/y": ["get"],
  "/z": ["get"]
}
op ids: ['dup', 'Adup', 'Bdup']
```

- [ ] **Step 4: No commit needed — verification only.**

---

## Self-Review Notes

- **Spec coverage:**
  - Within-source duplicates → Task 1 (`test_assign_op_ids_within_source_uses_prefix`, `test_assign_op_ids_within_source_chain_uses_numeric_after_prefix`) + Task 2 (`test_merge_within_source_op_id_collision_resolved`).
  - Post-prefix duplicates → Task 1 (`test_assign_op_ids_post_prefix_uses_numeric_suffix`) + Task 2 (`test_merge_all_op_ids_unique_in_output`).
  - openapi-merge-cli algorithm parity (global seen set, prefix-then-numeric) → Task 1 implementation.
  - Documentation freshness → Task 3.
- **Placeholder scan:** No `TBD`, no "implement later", every code block contains full code, every command shows expected output.
- **Type consistency:** `assign_unique_operation_ids` signature matches across Task 1 (definition), Task 2 (call site), and tests (import + invocation). Rename record keys (`source`, `path`, `method`, `old`, `new`, `reason`) are consistent between the implementation and `merge_specs`'s logging.
- **Symbol cleanup:** `detect_operation_id_collisions` removed from `merger.py` in Task 1, dropped from the test import list in Task 1, no remaining references after Task 2 (verified in Task 4 step 2).
