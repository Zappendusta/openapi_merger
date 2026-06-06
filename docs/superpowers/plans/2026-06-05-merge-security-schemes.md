# Merge Security Schemes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry `components.securitySchemes` from every source into the merged OpenAPI document, dedupe equal-content schemes by name, resolve content-differing collisions with the source's `schema_prefix`, rewrite every security-requirement reference that points at a renamed scheme, and merge any document-level `security` arrays.

**Architecture:** Mirror the existing schema-collision pipeline in `merger.py`. Add `detect_security_scheme_collisions(sources)` and `rewrite_security_scheme_name(node, old, new)`. In `merge_specs`, detect security-scheme collisions alongside schema collisions, apply per-source prefix renames, rewrite all security-requirement objects (at document root and on each operation), then dedupe equal-content schemes into `merged_components["securitySchemes"]`. Concatenate document-level `security` arrays with deduplication. Renames are logged via a new `merge.collision.security_scheme` structured warning.

**Tech Stack:** Python 3.12, structlog, pytest.

---

## File Structure

- **Modify** `src/openapi_merger/merger.py` — add `detect_security_scheme_collisions`, `rewrite_security_scheme_name`, integrate both into `merge_specs`, add document-level `security` merging.
- **Modify** `tests/test_merger.py` — add a new test section for security-scheme detection, renaming, requirement rewriting, dedup, collision, and root-level `security` merging.
- **Modify** `README.md` — add `merge.collision.security_scheme` row to the logging table; mention security-schemes carry-through in the merge-behaviour blurb if one exists nearby.

---

## Background — OpenAPI Security Model (for context)

Security in OpenAPI 3.x lives in three places:

1. **`components.securitySchemes`** — a name → scheme definition map (the auth *types*). Example: `BearerAuth`, `ApiKey`, `OAuthFlow`.
2. **Document-level `security`** — a list of *requirement objects*, each a map `{schemeName: [scopes]}`. Default requirement applied to every operation that does not override.
3. **Operation-level `security`** — same shape, overrides the document-level default for a single operation. An empty list `[]` means "explicitly no auth".

Security requirements reference schemes **by name**, not via `$ref`. So renaming a scheme requires walking every requirement object and rewriting matching keys. Schemes are *not* referenced from inside schemas, so this is a separate walker from `rewrite_ref`.

---

## Task 1: Detect security-scheme collisions

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merger.py` after the existing `# --- detect_schema_collisions ---` block (and before `# --- assign_unique_operation_ids ---`):

```python
# --- detect_security_scheme_collisions ---

from openapi_merger.merger import detect_security_scheme_collisions


def _sec(name, scheme):
    return {"components": {"securitySchemes": {name: scheme}}}


def test_sec_no_collision_when_only_one_source_defines_it():
    sources = [
        ("a", "A", _sec("BearerAuth", {"type": "http", "scheme": "bearer"})),
        ("b", "B", {"components": {}}),
    ]
    assert detect_security_scheme_collisions(sources) == {}


def test_sec_no_collision_when_definitions_are_equal():
    scheme = {"type": "http", "scheme": "bearer"}
    sources = [
        ("a", "A", _sec("BearerAuth", scheme)),
        ("b", "B", _sec("BearerAuth", scheme)),
    ]
    assert detect_security_scheme_collisions(sources) == {}


def test_sec_collision_when_definitions_differ():
    sources = [
        ("a", "A", _sec("BearerAuth", {"type": "http", "scheme": "bearer"})),
        ("b", "B", _sec("BearerAuth", {"type": "http", "scheme": "basic"})),
    ]
    collisions = detect_security_scheme_collisions(sources)
    assert "BearerAuth" in collisions
    assert set(collisions["BearerAuth"]) == {"a", "b"}


def test_sec_collision_only_reports_differing_sources():
    scheme = {"type": "http", "scheme": "bearer"}
    sources = [
        ("a", "A", _sec("BearerAuth", scheme)),
        ("b", "B", _sec("BearerAuth", scheme)),
        ("c", "C", _sec("BearerAuth", {"type": "http", "scheme": "basic"})),
    ]
    collisions = detect_security_scheme_collisions(sources)
    assert "BearerAuth" in collisions  # c differs from a and b


def test_sec_source_without_components_ok():
    sources = [
        ("a", "A", {"paths": {}}),
        ("b", "B", _sec("BearerAuth", {"type": "http", "scheme": "bearer"})),
    ]
    assert detect_security_scheme_collisions(sources) == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -k detect_security_scheme_collisions -v`
Expected: ImportError — `detect_security_scheme_collisions` not defined.

- [ ] **Step 3: Implement `detect_security_scheme_collisions`**

Add to `src/openapi_merger/merger.py` immediately below `detect_schema_collisions`:

```python
def detect_security_scheme_collisions(sources: list[Source]) -> dict[str, list[str]]:
    """
    Returns scheme_name -> [source_names] for securityScheme names that appear in
    multiple sources with different content. Equal-content duplicates are not
    collisions.
    """
    scheme_map: dict[str, list[tuple[str, dict]]] = {}
    for source_name, _prefix, doc in sources:
        schemes = doc.get("components", {}).get("securitySchemes", {})
        for name, scheme in schemes.items():
            scheme_map.setdefault(name, []).append((source_name, scheme))

    collisions = {}
    for name, entries in scheme_map.items():
        if len(entries) <= 1:
            continue
        first = entries[0][1]
        if all(e[1] == first for e in entries[1:]):
            continue
        collisions[name] = [e[0] for e in entries]
    return collisions
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -k detect_security_scheme_collisions -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: detect security scheme collisions across sources"
```

---

## Task 2: Rewrite security-requirement references when a scheme is renamed

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merger.py` after the security-scheme detection tests added in Task 1:

```python
# --- rewrite_security_scheme_name ---

from openapi_merger.merger import rewrite_security_scheme_name


def test_rewrite_sec_in_document_level_security():
    doc = {"security": [{"BearerAuth": []}, {"ApiKey": ["read"]}]}
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    assert result["security"] == [
        {"AuthApiBearerAuth": []},
        {"ApiKey": ["read"]},
    ]


def test_rewrite_sec_in_operation_security():
    doc = {
        "paths": {
            "/x": {
                "get": {
                    "operationId": "getX",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {}},
                }
            }
        }
    }
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    assert result["paths"]["/x"]["get"]["security"] == [{"AuthApiBearerAuth": []}]


def test_rewrite_sec_renames_components_security_schemes_key():
    doc = {
        "components": {
            "securitySchemes": {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
                "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Key"},
            }
        }
    }
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    schemes = result["components"]["securitySchemes"]
    assert "AuthApiBearerAuth" in schemes
    assert "BearerAuth" not in schemes
    assert schemes["AuthApiBearerAuth"] == {"type": "http", "scheme": "bearer"}
    assert "ApiKey" in schemes


def test_rewrite_sec_leaves_non_matching_keys_alone():
    doc = {"security": [{"ApiKey": []}]}
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    assert result == {"security": [{"ApiKey": []}]}


def test_rewrite_sec_empty_security_list_untouched():
    # An empty list means "explicitly no auth"; must remain empty.
    doc = {"paths": {"/x": {"get": {"security": [], "responses": {"200": {}}}}}}
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    assert result["paths"]["/x"]["get"]["security"] == []


def test_rewrite_sec_does_not_touch_unrelated_keys_named_security():
    # A schema property literally named "security" must not have its keys rewritten.
    doc = {
        "components": {
            "schemas": {
                "Config": {
                    "type": "object",
                    "properties": {
                        "security": {"type": "string", "example": "BearerAuth"}
                    },
                }
            }
        }
    }
    result = rewrite_security_scheme_name(doc, "BearerAuth", "AuthApiBearerAuth")
    # The string "BearerAuth" inside an example must be untouched.
    assert (
        result["components"]["schemas"]["Config"]["properties"]["security"]["example"]
        == "BearerAuth"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -k rewrite_security_scheme_name -v`
Expected: ImportError — `rewrite_security_scheme_name` not defined.

- [ ] **Step 3: Implement `rewrite_security_scheme_name`**

Add to `src/openapi_merger/merger.py` immediately above `HTTP_METHODS`:

```python
def rewrite_security_scheme_name(doc: dict, old_name: str, new_name: str) -> dict:
    """Rename a securityScheme from `old_name` to `new_name` everywhere it is
    referenced.

    Security requirements reference schemes **by key name**, not via $ref, so this
    is a structural walker — not a generic name replacer. It touches exactly three
    locations:

      1. ``doc["security"]``                              — list of requirement objects.
      2. ``doc["paths"][p][method]["security"]``          — per-operation requirements.
      3. ``doc["components"]["securitySchemes"][name]``   — the scheme definition itself.

    Anything else (schema properties literally named ``security``, string examples
    containing the scheme name, etc.) is left untouched. The doc is mutated in
    place and also returned for convenience.
    """
    if "security" in doc and isinstance(doc["security"], list):
        doc["security"] = [_rename_requirement(req, old_name, new_name) for req in doc["security"]]

    paths = doc.get("paths")
    if isinstance(paths, dict):
        for _path, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue
            for method, operation in path_item.items():
                if method not in HTTP_METHODS or not isinstance(operation, dict):
                    continue
                if "security" in operation and isinstance(operation["security"], list):
                    operation["security"] = [
                        _rename_requirement(req, old_name, new_name)
                        for req in operation["security"]
                    ]

    schemes = doc.get("components", {}).get("securitySchemes")
    if isinstance(schemes, dict) and old_name in schemes:
        schemes[new_name] = schemes.pop(old_name)

    return doc


def _rename_requirement(req: dict, old_name: str, new_name: str) -> dict:
    if not isinstance(req, dict) or old_name not in req:
        return req
    out = {}
    for k, v in req.items():
        if k == old_name:
            out[new_name] = v
        else:
            out[k] = v
    return out
```

Note: `_rename_requirement` references `HTTP_METHODS` indirectly via the outer function — it does not need the constant itself. `rewrite_security_scheme_name` *does* reference `HTTP_METHODS`, which is already defined just below (line 47 today). Place `rewrite_security_scheme_name` **after** the `HTTP_METHODS` definition. Concretely: define it just below the `HTTP_METHODS` line, above `assign_unique_operation_ids`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -k rewrite_security_scheme_name -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: rewrite security requirement references on scheme rename"
```

---

## Task 3: Merge security schemes (no-collision happy path)

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merger.py` at the end of the file:

```python
# --- merge_specs: securitySchemes ---

def test_merge_security_schemes_from_single_source_carried_through():
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/a": {}},
                "components": {
                    "schemas": {},
                    "securitySchemes": {
                        "BearerAuth": {"type": "http", "scheme": "bearer"}
                    },
                },
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }


def test_merge_security_schemes_equal_dedup():
    scheme = {"type": "http", "scheme": "bearer"}
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/a": {}},
                "components": {"schemas": {}, "securitySchemes": {"BearerAuth": scheme}},
            },
        ),
        (
            "b",
            "B",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/b": {}},
                "components": {"schemas": {}, "securitySchemes": {"BearerAuth": scheme}},
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert list(merged["components"]["securitySchemes"].keys()) == ["BearerAuth"]


def test_merge_security_schemes_distinct_names_carried_through():
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/a": {}},
                "components": {
                    "schemas": {},
                    "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
                },
            },
        ),
        (
            "b",
            "B",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/b": {}},
                "components": {
                    "schemas": {},
                    "securitySchemes": {
                        "ApiKey": {"type": "apiKey", "in": "header", "name": "X-Key"}
                    },
                },
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    schemes = merged["components"]["securitySchemes"]
    assert "BearerAuth" in schemes
    assert "ApiKey" in schemes
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -k "merge_security_schemes" -v`
Expected: All three fail with `KeyError: 'securitySchemes'` (the current implementation never writes that key).

- [ ] **Step 3: Implement carry-through and dedup**

Edit `src/openapi_merger/merger.py`. Locate the block (currently lines 199–216):

```python
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
```

Replace with:

```python
    # Merge schemas — equal duplicates are silently deduped
    merged_schemas: dict = {}
    for _source_name, _prefix, doc in processed:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            if name not in merged_schemas:
                merged_schemas[name] = schema

    # Merge securitySchemes — equal duplicates are silently deduped.
    # Collisions of differing content were resolved earlier via source-prefix renaming,
    # so any remaining same-name entries are content-equal by construction.
    merged_security_schemes: dict = {}
    for _source_name, _prefix, doc in processed:
        for name, scheme in doc.get("components", {}).get("securitySchemes", {}).items():
            if name not in merged_security_schemes:
                merged_security_schemes[name] = scheme

    # Merge other component sub-objects
    other_component_keys = {
        "responses", "parameters", "requestBodies",
        "headers", "examples", "links", "callbacks",
    }
    merged_components: dict = {"schemas": merged_schemas}
    if merged_security_schemes:
        merged_components["securitySchemes"] = merged_security_schemes
    for _source_name, _prefix, doc in processed:
        for key in other_component_keys:
            items = doc.get("components", {}).get(key, {})
            if items:
                merged_components.setdefault(key, {}).update(items)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -k "merge_security_schemes" -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: carry securitySchemes through merge with equal-content dedup"
```

---

## Task 4: Resolve security-scheme collisions with source prefix and log them

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merger.py`:

```python
def test_merge_security_scheme_collision_resolved_with_prefix():
    source_a = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/a": {
                "get": {
                    "operationId": "getA",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {}},
                }
            }
        },
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }
    source_b = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {
            "/b": {
                "get": {
                    "operationId": "getB",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {}},
                }
            }
        },
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "basic"}},
        },
    }
    merged = merge_specs(
        [("a", "AuthApi", source_a), ("b", "UserApi", source_b)],
        title="T",
        version="1",
    )
    schemes = merged["components"]["securitySchemes"]
    assert "AuthApiBearerAuth" in schemes
    assert "UserApiBearerAuth" in schemes
    assert "BearerAuth" not in schemes
    # Each operation must now reference its own renamed scheme.
    assert merged["paths"]["/a"]["get"]["security"] == [{"AuthApiBearerAuth": []}]
    assert merged["paths"]["/b"]["get"]["security"] == [{"UserApiBearerAuth": []}]


def test_merge_security_scheme_collision_rewrites_document_level_security():
    source_a = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "security": [{"BearerAuth": []}],
        "paths": {"/a": {}},
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }
    source_b = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "security": [{"BearerAuth": []}],
        "paths": {"/b": {}},
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "basic"}},
        },
    }
    merged = merge_specs(
        [("a", "AuthApi", source_a), ("b", "UserApi", source_b)],
        title="T",
        version="1",
    )
    # Both renamed requirements must appear in the merged document-level security list.
    assert {"AuthApiBearerAuth": []} in merged["security"]
    assert {"UserApiBearerAuth": []} in merged["security"]
    assert {"BearerAuth": []} not in merged["security"]


def test_merge_security_scheme_collision_logged(caplog):
    import logging
    caplog.set_level(logging.WARNING)
    source_a = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {"/a": {}},
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }
    source_b = {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {"/b": {}},
        "components": {
            "schemas": {},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "basic"}},
        },
    }
    merge_specs(
        [("a", "AuthApi", source_a), ("b", "UserApi", source_b)],
        title="T",
        version="1",
    )
    assert any("merge.collision.security_scheme" in r.getMessage() for r in caplog.records)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -k "security_scheme_collision" -v`
Expected: All three fail — the collision logic does not yet exist, so the scheme name is not renamed and only one of the two definitions survives the dedup.

- [ ] **Step 3: Implement collision resolution inside `merge_specs`**

Edit `src/openapi_merger/merger.py`. Locate the existing block (currently lines 144–168):

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

    # Apply schema prefix and $ref rewrites per source.
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
```

Replace with:

```python
def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)
    sec_collisions = detect_security_scheme_collisions(sources)

    for name, sources_with_name in collisions.items():
        log.warning(
            "merge.collision.schema",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )

    for name, sources_with_name in sec_collisions.items():
        log.warning(
            "merge.collision.security_scheme",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )

    # Apply schema and securityScheme prefix renames per source.
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

        colliding_schemes = [
            name for name, names in sec_collisions.items() if source_name in names
        ]
        for name in colliding_schemes:
            new_name = f"{prefix}{name}"
            doc = rewrite_security_scheme_name(doc, name, new_name)

        processed.append((source_name, prefix, doc))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -k "security_scheme" -v`
Expected: All security-scheme tests pass (Tasks 1–4 combined: 14 passed).

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: resolve security scheme collisions via source prefix"
```

---

## Task 5: Merge document-level `security` arrays

**Files:**
- Modify: `src/openapi_merger/merger.py`
- Test: `tests/test_merger.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_merger.py`:

```python
# --- merge_specs: document-level security ---

def test_merge_document_security_from_single_source_carried_through():
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "security": [{"BearerAuth": []}],
                "paths": {"/a": {}},
                "components": {
                    "schemas": {},
                    "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
                },
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["security"] == [{"BearerAuth": []}]


def test_merge_document_security_concatenated_and_deduped():
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "security": [{"BearerAuth": []}, {"ApiKey": ["read"]}],
                "paths": {"/a": {}},
                "components": {"schemas": {}},
            },
        ),
        (
            "b",
            "B",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "security": [{"ApiKey": ["read"]}, {"OAuth": ["scope1"]}],
                "paths": {"/b": {}},
                "components": {"schemas": {}},
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    # Source order preserved; duplicates removed; first occurrence wins.
    assert merged["security"] == [
        {"BearerAuth": []},
        {"ApiKey": ["read"]},
        {"OAuth": ["scope1"]},
    ]


def test_merge_document_security_absent_when_no_source_defines_it():
    sources = [
        (
            "a",
            "A",
            {
                "openapi": "3.0.0",
                "info": {"title": "T", "version": "1"},
                "paths": {"/a": {}},
                "components": {"schemas": {}},
            },
        ),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert "security" not in merged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_merger.py -k "document_security" -v`
Expected: 3 failures — `merged["security"]` is missing because `merge_specs` does not currently emit a top-level `security` key.

- [ ] **Step 3: Implement document-level security merging**

Edit `src/openapi_merger/merger.py`. Locate the final `return` block (currently lines 218–227):

```python
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

Replace with:

```python
    openapi_version = next(
        (doc.get("openapi", "3.0.0") for _, _, doc in processed), "3.0.0"
    )

    # Merge document-level `security` lists across sources, preserving order and
    # removing exact-duplicate requirement objects. Operation-level security is
    # already carried through paths.
    merged_security: list[dict] = []
    for _source_name, _prefix, doc in processed:
        for requirement in doc.get("security", []) or []:
            if requirement not in merged_security:
                merged_security.append(requirement)

    result: dict = {
        "openapi": openapi_version,
        "info": {"title": title, "version": version},
        "paths": merged_paths,
        "components": merged_components,
    }
    if merged_security:
        result["security"] = merged_security
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_merger.py -k "document_security" -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full merger test suite**

Run: `pytest tests/test_merger.py -v`
Expected: All tests pass (existing + new).

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: merge document-level security arrays across sources"
```

---

## Task 6: End-to-end test through the orchestrator

**Files:**
- Test: `tests/test_orchestrator.py`

- [ ] **Step 1: Inspect the existing orchestrator test patterns**

Run: `grep -n "def test_" tests/test_orchestrator.py`
Expected: Existing tests use `MergeOrchestrator` with mocked `fetch_spec`. Follow the same pattern.

- [ ] **Step 2: Write the failing end-to-end test**

Append to `tests/test_orchestrator.py` (preserving existing imports and fixtures):

```python
def test_orchestrator_carries_security_schemes_through(monkeypatch):
    """Smoke test: a source declaring securitySchemes appears in the merged spec."""
    from openapi_merger.config import ServiceConfig, SourcesConfig, SourceConfig, ServiceInfo
    from openapi_merger.orchestrator import MergeOrchestrator
    import openapi_merger.orchestrator as orch_mod

    spec_a = {
        "openapi": "3.0.0",
        "info": {"title": "A", "version": "1"},
        "paths": {
            "/a": {
                "get": {
                    "operationId": "getA",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {}},
                }
            }
        },
        "components": {
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }

    async def fake_fetch(source):
        return spec_a

    monkeypatch.setattr(orch_mod, "fetch_spec", fake_fetch)

    service = ServiceConfig(info=ServiceInfo(title="Merged", version="1.0"))
    sources = SourcesConfig(
        sources=[
            SourceConfig(name="a", url="http://example.invalid/a", schema_prefix="A"),
        ]
    )
    orch = MergeOrchestrator(service, sources)

    import asyncio
    merged = asyncio.run(orch.get_merged())

    assert "BearerAuth" in merged["components"]["securitySchemes"]
    assert merged["paths"]["/a"]["get"]["security"] == [{"BearerAuth": []}]
```

If `SourceConfig` / `ServiceInfo` / fixture shapes differ from the above, mirror whatever pattern `test_orchestrator.py` already uses for constructing configs — the goal is one orchestrator-level pass-through test, not novel fixture work. Read the existing fixtures/imports at the top of `test_orchestrator.py` and adapt accordingly.

- [ ] **Step 3: Run the test**

Run: `pytest tests/test_orchestrator.py::test_orchestrator_carries_security_schemes_through -v`
Expected: PASS (Task 3 already wired the carry-through; this test confirms the orchestrator does not strip it).

- [ ] **Step 4: Commit**

```bash
git add tests/test_orchestrator.py
git commit -m "test: verify securitySchemes survive the orchestrator merge pipeline"
```

---

## Task 7: Document the new behaviour

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a log-event row**

Open `README.md`. Locate the existing log-event table (the block containing `merge.collision.schema` near line 151). Add a new row immediately after the `merge.collision.schema` row:

```markdown
| `merge.collision.security_scheme` | warning | Same securityScheme name with different content across sources — resolved by prefixing. |
```

- [ ] **Step 2: Mention security in the merge-behaviour description (if a description block exists nearby)**

Search `README.md` for any existing summary of what `components` keys are carried through. If such a sentence exists and lists schemas / responses / parameters etc., add `securitySchemes` to that list. If no such sentence exists, **do not invent one** — the table row above is sufficient.

Run: `grep -n "securitySchemes\|components\." README.md`
Expected: After edit, at least one match for `securitySchemes` (the new table row). If no prose existed before, that is fine.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document merge.collision.security_scheme log event"
```

---

## Self-Review Notes

- **Spec coverage:** the user asked for two things — (1) stop dropping security schemes, (2) deduplicate them. Task 3 covers (1) and equal-content dedup. Tasks 1, 2, 4 cover collision dedup via prefix renaming. Task 5 covers document-level `security` (closely-related, would otherwise be a follow-up bug). Tasks 6, 7 cover integration test + docs.
- **Type/name consistency:** functions are named consistently — `detect_security_scheme_collisions`, `rewrite_security_scheme_name`, `_rename_requirement`. Log event is `merge.collision.security_scheme` (matches the existing `merge.collision.schema` / `merge.collision.operation_id` pattern). Source tuple shape `(name, prefix, doc)` matches the existing `Source` alias.
- **No placeholders:** every step has concrete code, exact commands, expected output.
- **Risk:** `rewrite_security_scheme_name` walks structurally rather than via name-string replacement, so schema properties named "security" (test in Task 2 Step 1) and string examples containing the scheme name are left untouched. That keeps the rewriter from corrupting unrelated data.
