# OpenAPI Merger Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Dockerized FastAPI service that fetches multiple upstream OpenAPI 3.x specs, applies route transforms and schema collision resolution, merges them into a single spec, and exposes it on demand.

**Architecture:** Single FastAPI app; on request (or `?refresh=true`), fetches all upstream specs concurrently via httpx, applies per-source route prefix transforms, resolves schema collisions by prefixing only the colliding schema names, merges paths/schemas/components, caches the result in memory, and serves as JSON or YAML.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, httpx, PyYAML, Pydantic v2, pytest, pytest-asyncio, respx

---

## Project Layout

```
openapi_merger/
├── src/openapi_merger/
│   ├── __init__.py
│   ├── main.py          # FastAPI app, lifespan, endpoints
│   ├── config.py        # Pydantic models, config file loading
│   ├── fetcher.py       # Async upstream HTTP fetch
│   ├── transformer.py   # Route path prefix transforms
│   ├── merger.py        # Collision detection, $ref rewriting, merge
│   └── orchestrator.py  # Cache + pipeline coordinator
├── tests/
│   ├── __init__.py
│   ├── test_config.py
│   ├── test_transformer.py
│   ├── test_fetcher.py
│   ├── test_merger.py
│   └── test_integration.py
├── example/
│   ├── service.yaml
│   └── sources.yaml
├── pyproject.toml
├── Dockerfile
└── .dockerignore
```

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/openapi_merger/__init__.py`
- Create: `src/openapi_merger/main.py`
- Create: `tests/__init__.py`
- Create: `Dockerfile`
- Create: `.dockerignore`

**Step 1: Create stub files**

```bash
touch src/openapi_merger/__init__.py tests/__init__.py
```

**Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "openapi-merger"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "pydantic>=2.6",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "respx>=0.21",
    "httpx>=0.27",
]

[tool.hatch.build.targets.wheel]
packages = ["src/openapi_merger"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

**Step 3: Write stub `src/openapi_merger/main.py`**

```python
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 4: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir -e .
EXPOSE 8080
ENV SERVICE_CONFIG=/config/service.yaml
ENV SOURCES_CONFIG=/config/sources.yaml
CMD ["uvicorn", "openapi_merger.main:app", "--host", "0.0.0.0", "--port", "8080"]
```

**Step 5: Write `.dockerignore`**

```
__pycache__
*.pyc
.pytest_cache
tests/
docs/
.git
```

**Step 6: Install dependencies**

```bash
pip install -e ".[dev]"
```

Expected: packages install without error.

**Step 7: Verify health endpoint**

```bash
uvicorn openapi_merger.main:app --port 8001 &
curl -s http://localhost:8001/health
# expected: {"status":"ok"}
kill %1
```

**Step 8: Commit**

```bash
git init
git add pyproject.toml src/ tests/__init__.py Dockerfile .dockerignore
git commit -m "chore: project scaffold"
```

---

### Task 2: Config models and loading

**Files:**
- Create: `src/openapi_merger/config.py`
- Create: `tests/test_config.py`

**Step 1: Write failing tests**

```python
# tests/test_config.py
import pytest
from pydantic import ValidationError
from openapi_merger.config import (
    ServiceConfig, SourcesConfig,
    load_service_config, load_sources_config,
)


def test_service_config_minimal():
    cfg = ServiceConfig.model_validate({
        "port": 8080,
        "spec_path": "/openapi.json",
        "info": {"title": "Test API", "version": "1.0.0"},
    })
    assert cfg.port == 8080
    assert cfg.auth is None
    assert cfg.spec_path == "/openapi.json"


def test_service_config_with_auth():
    cfg = ServiceConfig.model_validate({
        "port": 8080,
        "spec_path": "/openapi.json",
        "info": {"title": "T", "version": "1"},
        "auth": {"username": "admin", "password": "secret"},
    })
    assert cfg.auth.username == "admin"
    assert cfg.auth.password == "secret"


def test_source_requires_schema_prefix():
    with pytest.raises(ValidationError, match="schema_prefix"):
        SourcesConfig.model_validate({
            "sources": [{"name": "svc", "url": "http://example.com/openapi.json"}]
        })


def test_source_valid_minimal():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://example.com/openapi.json",
            "schema_prefix": "Svc",
        }]
    })
    assert cfg.sources[0].schema_prefix == "Svc"
    assert cfg.sources[0].auth is None
    assert cfg.sources[0].route_transforms == []


def test_source_with_transforms():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://svc/openapi.json",
            "schema_prefix": "Svc",
            "route_transforms": [{"from": "/api", "to": "/api/svc"}],
        }]
    })
    t = cfg.sources[0].route_transforms[0]
    assert t.from_path == "/api"
    assert t.to == "/api/svc"


def test_load_service_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_service_config("/nonexistent/path.yaml")


def test_load_sources_config_valid(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text("sources:\n  - name: s\n    url: http://x/openapi.json\n    schema_prefix: S\n")
    cfg = load_sources_config(str(f))
    assert cfg.sources[0].name == "s"


def test_load_sources_config_invalid_yaml(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text(": : bad yaml {[")
    with pytest.raises(Exception):
        load_sources_config(str(f))
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_config.py -v
# expected: ImportError (module not found)
```

**Step 3: Implement `src/openapi_merger/config.py`**

```python
from __future__ import annotations
import pathlib
import yaml
from pydantic import BaseModel, Field


class AuthConfig(BaseModel):
    username: str
    password: str


class RouteTransform(BaseModel):
    model_config = {"populate_by_name": True}
    from_path: str = Field(alias="from")
    to: str


class SourceConfig(BaseModel):
    name: str
    url: str
    schema_prefix: str
    auth: AuthConfig | None = None
    route_transforms: list[RouteTransform] = []


class InfoConfig(BaseModel):
    title: str
    version: str


class ServiceConfig(BaseModel):
    port: int = 8080
    spec_path: str = "/openapi.json"
    auth: AuthConfig | None = None
    info: InfoConfig


class SourcesConfig(BaseModel):
    sources: list[SourceConfig]


def _load_yaml(path: str) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with p.open() as f:
        return yaml.safe_load(f)


def load_service_config(path: str) -> ServiceConfig:
    return ServiceConfig.model_validate(_load_yaml(path))


def load_sources_config(path: str) -> SourcesConfig:
    return SourcesConfig.model_validate(_load_yaml(path))
```

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_config.py -v
# expected: all PASS
```

**Step 5: Commit**

```bash
git add src/openapi_merger/config.py tests/test_config.py
git commit -m "feat: config models and loading"
```

---

### Task 3: Route transformer

**Files:**
- Create: `src/openapi_merger/transformer.py`
- Create: `tests/test_transformer.py`

**Step 1: Write failing tests**

```python
# tests/test_transformer.py
from openapi_merger.transformer import transform_paths
from openapi_merger.config import RouteTransform


def _t(from_path, to):
    return RouteTransform.model_validate({"from": from_path, "to": to})


def test_prefix_replaced():
    paths = {"/api/users": {}, "/api/orders": {}}
    result = transform_paths(paths, [_t("/api", "/api/svc")])
    assert "/api/svc/users" in result
    assert "/api/svc/orders" in result
    assert "/api/users" not in result


def test_no_match_unchanged():
    paths = {"/other/path": {}}
    result = transform_paths(paths, [_t("/api", "/api/v2")])
    assert "/other/path" in result


def test_empty_transforms_unchanged():
    paths = {"/a": {}, "/b": {}}
    assert transform_paths(paths, []) == {"/a": {}, "/b": {}}


def test_transforms_applied_sequentially():
    # second transform acts on result of first
    paths = {"/v1/users": {}}
    result = transform_paths(paths, [_t("/v1", "/v2"), _t("/v2", "/v3")])
    assert "/v3/users" in result


def test_path_values_preserved():
    paths = {"/api/users": {"get": {"summary": "list"}}}
    result = transform_paths(paths, [_t("/api", "/api/v2")])
    assert result["/api/v2/users"] == {"get": {"summary": "list"}}


def test_empty_paths():
    assert transform_paths({}, [_t("/api", "/v2")]) == {}
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_transformer.py -v
# expected: ImportError
```

**Step 3: Implement `src/openapi_merger/transformer.py`**

```python
from openapi_merger.config import RouteTransform


def transform_paths(paths: dict, transforms: list[RouteTransform]) -> dict:
    result = {}
    for path, value in paths.items():
        new_path = path
        for t in transforms:
            if new_path.startswith(t.from_path):
                new_path = t.to + new_path[len(t.from_path):]
        result[new_path] = value
    return result
```

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_transformer.py -v
# expected: all PASS
```

**Step 5: Commit**

```bash
git add src/openapi_merger/transformer.py tests/test_transformer.py
git commit -m "feat: route path transformer"
```

---

### Task 4: Upstream fetcher

**Files:**
- Create: `src/openapi_merger/fetcher.py`
- Create: `tests/test_fetcher.py`

**Step 1: Write failing tests**

```python
# tests/test_fetcher.py
import base64
import pytest
import respx
import httpx
import yaml as pyyaml
from openapi_merger.fetcher import fetch_spec
from openapi_merger.config import SourceConfig


def _source(url, auth=None):
    data = {"name": "test", "url": url, "schema_prefix": "Test"}
    if auth:
        data["auth"] = auth
    return SourceConfig.model_validate(data)


_SPEC = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}


@respx.mock
async def test_fetch_json():
    respx.get("http://svc/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC)
    )
    result = await fetch_spec(_source("http://svc/openapi.json"))
    assert result["openapi"] == "3.0.0"


@respx.mock
async def test_fetch_yaml():
    respx.get("http://svc/openapi.yaml").mock(
        return_value=httpx.Response(
            200,
            text=pyyaml.dump(_SPEC),
            headers={"content-type": "text/yaml"},
        )
    )
    result = await fetch_spec(_source("http://svc/openapi.yaml"))
    assert result["openapi"] == "3.0.0"


@respx.mock
async def test_fetch_yaml_by_url_extension():
    respx.get("http://svc/spec.yml").mock(
        return_value=httpx.Response(200, text=pyyaml.dump(_SPEC))
    )
    result = await fetch_spec(_source("http://svc/spec.yml"))
    assert result["info"]["title"] == "T"


@respx.mock
async def test_basic_auth_header_sent():
    route = respx.get("http://svc/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC)
    )
    await fetch_spec(_source("http://svc/openapi.json", auth={"username": "u", "password": "p"}))
    auth_header = route.calls[0].request.headers["authorization"]
    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert auth_header == expected


@respx.mock
async def test_upstream_http_error_raises():
    respx.get("http://svc/openapi.json").mock(return_value=httpx.Response(500))
    with pytest.raises(RuntimeError, match="test"):
        await fetch_spec(_source("http://svc/openapi.json"))


@respx.mock
async def test_network_error_raises():
    respx.get("http://svc/openapi.json").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(RuntimeError, match="test"):
        await fetch_spec(_source("http://svc/openapi.json"))
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_fetcher.py -v
# expected: ImportError
```

**Step 3: Implement `src/openapi_merger/fetcher.py`**

```python
import yaml
import httpx
from openapi_merger.config import SourceConfig


async def fetch_spec(source: SourceConfig) -> dict:
    auth = None
    if source.auth:
        auth = (source.auth.username, source.auth.password)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(source.url, auth=auth)
    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to '{source.name}' at {source.url}: {e}"
        ) from e

    if response.status_code != 200:
        raise RuntimeError(
            f"Upstream '{source.name}' returned HTTP {response.status_code}: {source.url}"
        )

    content_type = response.headers.get("content-type", "")
    if "yaml" in content_type or source.url.endswith((".yaml", ".yml")):
        return yaml.safe_load(response.text)
    return response.json()
```

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_fetcher.py -v
# expected: all PASS
```

**Step 5: Commit**

```bash
git add src/openapi_merger/fetcher.py tests/test_fetcher.py
git commit -m "feat: upstream spec fetcher"
```

---

### Task 5: Schema merger (collision detection, $ref rewriting, merge)

**Files:**
- Create: `src/openapi_merger/merger.py`
- Create: `tests/test_merger.py`

This is the core logic. Three functions:
- `rewrite_ref(node, old_name, new_name)` — deep-walks any dict/list/str, rewrites matching `$ref` values
- `detect_schema_collisions(sources)` — finds schema names that appear in multiple sources with different content
- `merge_specs(sources, title, version)` — full pipeline: resolve collisions, merge paths/schemas/components

`sources` throughout is `list[tuple[source_name: str, schema_prefix: str, doc: dict]]`.

**Step 1: Write failing tests**

```python
# tests/test_merger.py
import pytest
from openapi_merger.merger import rewrite_ref, detect_schema_collisions, merge_specs


# --- rewrite_ref ---

def test_rewrite_ref_top_level():
    doc = {"$ref": "#/components/schemas/Widget"}
    assert rewrite_ref(doc, "Widget", "OrdersWidget") == {
        "$ref": "#/components/schemas/OrdersWidget"
    }


def test_rewrite_ref_nested_dict():
    doc = {"properties": {"item": {"$ref": "#/components/schemas/Widget"}}}
    result = rewrite_ref(doc, "Widget", "OrdersWidget")
    assert result["properties"]["item"]["$ref"] == "#/components/schemas/OrdersWidget"


def test_rewrite_ref_in_list():
    doc = {"oneOf": [
        {"$ref": "#/components/schemas/Widget"},
        {"$ref": "#/components/schemas/Other"},
    ]}
    result = rewrite_ref(doc, "Widget", "OrdersWidget")
    assert result["oneOf"][0]["$ref"] == "#/components/schemas/OrdersWidget"
    assert result["oneOf"][1]["$ref"] == "#/components/schemas/Other"


def test_rewrite_ref_no_match():
    doc = {"$ref": "#/components/schemas/Other"}
    assert rewrite_ref(doc, "Widget", "OrdersWidget") == {"$ref": "#/components/schemas/Other"}


def test_rewrite_ref_non_schema_ref_untouched():
    doc = {"$ref": "#/components/responses/Error"}
    assert rewrite_ref(doc, "Error", "NewError") == {"$ref": "#/components/responses/Error"}


# --- detect_schema_collisions ---

def test_no_collision():
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
        ("b", "B", {"components": {"schemas": {"Bar": {"type": "string"}}}}),
    ]
    assert detect_schema_collisions(sources) == {}


def test_equal_schemas_not_a_collision():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": schema}}}),
        ("b", "B", {"components": {"schemas": {"Foo": schema}}}),
    ]
    assert detect_schema_collisions(sources) == {}


def test_different_schemas_is_collision():
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
        ("b", "B", {"components": {"schemas": {"Foo": {"type": "string"}}}}),
    ]
    collisions = detect_schema_collisions(sources)
    assert "Foo" in collisions
    assert set(collisions["Foo"]) == {"a", "b"}


def test_collision_only_reports_differing_sources():
    schema = {"type": "object"}
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": schema}}}),
        ("b", "B", {"components": {"schemas": {"Foo": schema}}}),
        ("c", "C", {"components": {"schemas": {"Foo": {"type": "string"}}}}),
    ]
    collisions = detect_schema_collisions(sources)
    assert "Foo" in collisions  # c differs from a and b


def test_source_with_no_components():
    sources = [
        ("a", "A", {"paths": {}}),
        ("b", "B", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
    ]
    assert detect_schema_collisions(sources) == {}


# --- merge_specs ---

def _doc(paths, schemas, openapi="3.0.0"):
    return {
        "openapi": openapi,
        "info": {"title": "T", "version": "1"},
        "paths": paths,
        "components": {"schemas": schemas},
    }


def test_merge_no_collision():
    sources = [
        ("a", "A", _doc({"/a": {}}, {"Foo": {"type": "object"}})),
        ("b", "B", _doc({"/b": {}}, {"Bar": {"type": "string"}})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert "/a" in merged["paths"]
    assert "/b" in merged["paths"]
    assert "Foo" in merged["components"]["schemas"]
    assert "Bar" in merged["components"]["schemas"]


def test_merge_equal_schemas_deduped():
    schema = {"type": "object"}
    sources = [
        ("a", "A", _doc({"/a": {}}, {"Foo": schema})),
        ("b", "B", _doc({"/b": {}}, {"Foo": schema})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert list(merged["components"]["schemas"].keys()) == ["Foo"]


def test_merge_collision_resolved_with_prefix():
    ref_path = {
        "/a": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Foo"}
                            }
                        }
                    }
                }
            }
        }
    }
    sources = [
        ("a", "A", _doc(ref_path, {"Foo": {"type": "object"}})),
        ("b", "B", _doc({"/b": {}}, {"Foo": {"type": "string"}})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert "AFoo" in merged["components"]["schemas"]
    assert "BFoo" in merged["components"]["schemas"]
    assert "Foo" not in merged["components"]["schemas"]
    # $ref in source a must be rewritten
    ref = (
        merged["paths"]["/a"]["get"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["$ref"]
    )
    assert ref == "#/components/schemas/AFoo"


def test_merge_path_collision_raises():
    sources = [
        ("a", "A", _doc({"/clash": {}}, {})),
        ("b", "B", _doc({"/clash": {}}, {})),
    ]
    with pytest.raises(RuntimeError, match="/clash"):
        merge_specs(sources, title="Merged", version="1.0")


def test_merge_sets_metadata():
    sources = [("a", "A", _doc({"/a": {}}, {}))]
    merged = merge_specs(sources, title="My API", version="2.0")
    assert merged["info"]["title"] == "My API"
    assert merged["info"]["version"] == "2.0"


def test_merge_preserves_openapi_version_from_first_source():
    sources = [
        ("a", "A", _doc({"/a": {}}, {}, openapi="3.1.0")),
        ("b", "B", _doc({"/b": {}}, {}, openapi="3.0.0")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["openapi"] == "3.1.0"
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_merger.py -v
# expected: ImportError
```

**Step 3: Implement `src/openapi_merger/merger.py`**

```python
from __future__ import annotations
import copy

# Type alias for clarity
Source = tuple[str, str, dict]  # (name, schema_prefix, doc)


def rewrite_ref(node, old_name: str, new_name: str):
    """Recursively rewrite $ref values for a specific schema name."""
    old_ref = f"#/components/schemas/{old_name}"
    new_ref = f"#/components/schemas/{new_name}"
    if isinstance(node, dict):
        return {
            k: (new_ref if k == "$ref" and v == old_ref else rewrite_ref(v, old_name, new_name))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [rewrite_ref(item, old_name, new_name) for item in node]
    return node


def detect_schema_collisions(sources: list[Source]) -> dict[str, list[str]]:
    """
    Returns schema_name -> [source_names] for names that appear in multiple
    sources with different content. Equal-content duplicates are not collisions.
    """
    schema_map: dict[str, list[tuple[str, dict]]] = {}
    for source_name, _prefix, doc in sources:
        for name, schema in doc.get("components", {}).get("schemas", {}).items():
            schema_map.setdefault(name, []).append((source_name, schema))

    collisions = {}
    for name, entries in schema_map.items():
        if len(entries) <= 1:
            continue
        first = entries[0][1]
        if all(e[1] == first for e in entries[1:]):
            continue  # all equal: not a collision
        collisions[name] = [e[0] for e in entries]
    return collisions


def merge_specs(sources: list[Source], title: str, version: str) -> dict:
    collisions = detect_schema_collisions(sources)

    # Apply prefix only to colliding schema names (and their $refs) per source
    processed: list[Source] = []
    for source_name, prefix, doc in sources:
        doc = copy.deepcopy(doc)
        colliding = [
            name for name, names in collisions.items() if source_name in names
        ]
        for name in colliding:
            new_name = f"{prefix}{name}"
            schemas = doc.setdefault("components", {}).setdefault("schemas", {})
            if name in schemas:
                schemas[new_name] = schemas.pop(name)
            doc = rewrite_ref(doc, name, new_name)
        processed.append((source_name, prefix, doc))

    # Merge paths — error on duplicates
    merged_paths: dict = {}
    for source_name, _prefix, doc in processed:
        for path, value in doc.get("paths", {}).items():
            if path in merged_paths:
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

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_merger.py -v
# expected: all PASS
```

**Step 5: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_merger.py
git commit -m "feat: schema merger with collision detection and ref rewriting"
```

---

### Task 6: Merge orchestrator

**Files:**
- Create: `src/openapi_merger/orchestrator.py`
- Create: `tests/test_orchestrator.py`

Coordinates: fetch all sources concurrently, apply route transforms, merge, cache result.

**Step 1: Write failing tests**

```python
# tests/test_orchestrator.py
import pytest
import respx
import httpx
from openapi_merger.orchestrator import MergeOrchestrator
from openapi_merger.config import ServiceConfig, SourcesConfig


_SVC_CFG = ServiceConfig.model_validate({
    "port": 8080,
    "spec_path": "/openapi.json",
    "info": {"title": "Merged", "version": "1.0"},
})

_SPEC_A = {
    "openapi": "3.0.0",
    "info": {"title": "A", "version": "1"},
    "paths": {"/api/users": {"get": {}}},
    "components": {"schemas": {"User": {"type": "object"}}},
}
_SPEC_B = {
    "openapi": "3.0.0",
    "info": {"title": "B", "version": "1"},
    "paths": {"/api/orders": {"get": {}}},
    "components": {"schemas": {"Order": {"type": "object"}}},
}

_SOURCES_CFG = SourcesConfig.model_validate({
    "sources": [
        {
            "name": "users",
            "url": "http://users/openapi.json",
            "schema_prefix": "Users",
            "route_transforms": [{"from": "/api", "to": "/api/users"}],
        },
        {
            "name": "orders",
            "url": "http://orders/openapi.json",
            "schema_prefix": "Orders",
            "route_transforms": [{"from": "/api", "to": "/api/orders"}],
        },
    ]
})


@respx.mock
async def test_get_merged_fetches_and_merges():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    merged = await o.get_merged()
    assert "/api/users/users" in merged["paths"]
    assert "/api/orders/orders" in merged["paths"]
    assert merged["info"]["title"] == "Merged"


@respx.mock
async def test_second_call_uses_cache():
    route_a = respx.get("http://users/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_A)
    )
    route_b = respx.get("http://orders/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_B)
    )

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    await o.get_merged()
    await o.get_merged()

    assert route_a.call_count == 1
    assert route_b.call_count == 1


@respx.mock
async def test_refresh_bypasses_cache():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    await o.get_merged()
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    await o.get_merged(refresh=True)

    assert respx.calls.call_count == 4


@respx.mock
async def test_upstream_error_propagates():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(500))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    with pytest.raises(RuntimeError, match="users"):
        await o.get_merged()
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_orchestrator.py -v
# expected: ImportError
```

**Step 3: Implement `src/openapi_merger/orchestrator.py`**

```python
import asyncio
from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.transformer import transform_paths
from openapi_merger.merger import merge_specs


class MergeOrchestrator:
    def __init__(self, service_config: ServiceConfig, sources_config: SourcesConfig):
        self._service = service_config
        self._sources = sources_config
        self._cache: dict | None = None

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            return self._cache
        self._cache = await self._build()
        return self._cache

    async def _build(self) -> dict:
        docs = await asyncio.gather(
            *[fetch_spec(s) for s in self._sources.sources]
        )
        processed = []
        for source, doc in zip(self._sources.sources, docs):
            doc["paths"] = transform_paths(
                doc.get("paths", {}), source.route_transforms
            )
            processed.append((source.name, source.schema_prefix, doc))
        return merge_specs(
            processed,
            title=self._service.info.title,
            version=self._service.info.version,
        )
```

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_orchestrator.py -v
# expected: all PASS
```

**Step 5: Commit**

```bash
git add src/openapi_merger/orchestrator.py tests/test_orchestrator.py
git commit -m "feat: merge orchestrator with in-memory cache"
```

---

### Task 7: FastAPI app with auth, format, and refresh

**Files:**
- Modify: `src/openapi_merger/main.py`
- Create: `tests/test_integration.py`

Config file paths come from env vars `SERVICE_CONFIG` (default `/config/service.yaml`) and `SOURCES_CONFIG` (default `/config/sources.yaml`). The spec endpoint path and optional basic auth come from `service.yaml`.

**Step 1: Write failing integration tests**

```python
# tests/test_integration.py
import os
import pytest
import respx
import httpx
import yaml as pyyaml
from fastapi.testclient import TestClient


_SPEC_A = {
    "openapi": "3.0.0",
    "info": {"title": "A", "version": "1"},
    "paths": {"/users": {"get": {}}},
    "components": {"schemas": {"User": {"type": "object"}}},
}
_SPEC_B = {
    "openapi": "3.0.0",
    "info": {"title": "B", "version": "1"},
    "paths": {"/orders": {"get": {}}},
    "components": {"schemas": {}},
}


@pytest.fixture
def config_files(tmp_path):
    svc = tmp_path / "service.yaml"
    svc.write_text(
        "port: 8080\n"
        "spec_path: /openapi.json\n"
        "info:\n  title: Merged\n  version: '1.0'\n"
    )
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n"
        "  - name: users\n"
        "    url: http://users/openapi.json\n"
        "    schema_prefix: Users\n"
        "  - name: orders\n"
        "    url: http://orders/openapi.json\n"
        "    schema_prefix: Orders\n"
    )
    return str(svc), str(sources)


@pytest.fixture
def client(config_files, monkeypatch):
    svc_path, sources_path = config_files
    monkeypatch.setenv("SERVICE_CONFIG", svc_path)
    monkeypatch.setenv("SOURCES_CONFIG", sources_path)
    # Re-import app after env vars are set
    import importlib
    import openapi_merger.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        yield c


@respx.mock
def test_health(client):
    assert client.get("/health").status_code == 200


@respx.mock
def test_get_spec_json(client):
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    r = client.get("/openapi.json")
    assert r.status_code == 200
    data = r.json()
    assert "/users" in data["paths"]
    assert data["info"]["title"] == "Merged"


@respx.mock
def test_get_spec_yaml(client):
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    r = client.get("/openapi.json?format=yaml")
    assert r.status_code == 200
    data = pyyaml.safe_load(r.text)
    assert "/users" in data["paths"]


@respx.mock
def test_get_spec_invalid_format(client):
    r = client.get("/openapi.json?format=xml")
    assert r.status_code == 400


@respx.mock
def test_refresh_param(client):
    route_a = respx.get("http://users/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_A)
    )
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    client.get("/openapi.json")
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    client.get("/openapi.json?refresh=true")
    assert respx.calls.call_count == 4


@respx.mock
def test_upstream_error_returns_502(client):
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(500))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    r = client.get("/openapi.json")
    assert r.status_code == 502


@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    svc = tmp_path / "service.yaml"
    svc.write_text(
        "port: 8080\n"
        "spec_path: /openapi.json\n"
        "info:\n  title: Merged\n  version: '1.0'\n"
        "auth:\n  username: admin\n  password: secret\n"
    )
    sources = tmp_path / "sources.yaml"
    sources.write_text(
        "sources:\n"
        "  - name: svc\n    url: http://svc/openapi.json\n    schema_prefix: Svc\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(svc))
    monkeypatch.setenv("SOURCES_CONFIG", str(sources))
    import importlib
    import openapi_merger.main as m
    importlib.reload(m)
    with TestClient(m.app) as c:
        yield c


@respx.mock
def test_auth_required_without_credentials(auth_client):
    r = auth_client.get("/openapi.json")
    assert r.status_code == 401


@respx.mock
def test_auth_passes_with_correct_credentials(auth_client):
    respx.get("http://svc/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_A)
    )
    r = auth_client.get("/openapi.json", auth=("admin", "secret"))
    assert r.status_code == 200


@respx.mock
def test_auth_fails_with_wrong_credentials(auth_client):
    r = auth_client.get("/openapi.json", auth=("admin", "wrong"))
    assert r.status_code == 401
```

**Step 2: Run to verify they fail**

```bash
pytest tests/test_integration.py -v
# expected: failures (no routes registered yet)
```

**Step 3: Implement full `src/openapi_merger/main.py`**

```python
import os
import secrets
from contextlib import asynccontextmanager

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from openapi_merger.config import load_service_config, load_sources_config, ServiceConfig
from openapi_merger.orchestrator import MergeOrchestrator

_security = HTTPBasic(auto_error=False)

_service_config: ServiceConfig | None = None
_orchestrator: MergeOrchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _service_config, _orchestrator
    svc_path = os.getenv("SERVICE_CONFIG", "/config/service.yaml")
    src_path = os.getenv("SOURCES_CONFIG", "/config/sources.yaml")
    _service_config = load_service_config(svc_path)
    sources_config = load_sources_config(src_path)
    _orchestrator = MergeOrchestrator(_service_config, sources_config)

    async def _get_spec(
        format: str = Query("json"),
        refresh: bool = Query(False),
        credentials: HTTPBasicCredentials | None = Depends(_security),
    ):
        if _service_config.auth:
            if credentials is None:
                raise HTTPException(
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic"},
                )
            valid = secrets.compare_digest(
                credentials.username, _service_config.auth.username
            ) and secrets.compare_digest(
                credentials.password, _service_config.auth.password
            )
            if not valid:
                raise HTTPException(status_code=401)

        if format not in ("json", "yaml"):
            raise HTTPException(
                status_code=400,
                detail=f"Unknown format '{format}'. Use 'json' or 'yaml'.",
            )

        try:
            merged = await _orchestrator.get_merged(refresh=refresh)
        except RuntimeError as e:
            raise HTTPException(status_code=502, detail=str(e))

        if format == "yaml":
            return Response(
                content=yaml.dump(merged, allow_unicode=True),
                media_type="text/yaml",
            )
        return merged

    app.add_api_route(
        _service_config.spec_path,
        _get_spec,
        methods=["GET"],
    )
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok"}
```

**Step 4: Run to verify tests pass**

```bash
pytest tests/test_integration.py -v
# expected: all PASS
```

**Step 5: Run full test suite**

```bash
pytest -v
# expected: all PASS
```

**Step 6: Commit**

```bash
git add src/openapi_merger/main.py tests/test_integration.py
git commit -m "feat: FastAPI app with auth, format, and refresh"
```

---

### Task 8: Example configs and Dockerfile verification

**Files:**
- Create: `example/service.yaml`
- Create: `example/sources.yaml`
- Verify: `Dockerfile`

**Step 1: Write example configs**

```yaml
# example/service.yaml
port: 8080
spec_path: /openapi.json
info:
  title: My Merged API
  version: 1.0.0
# auth:
#   username: admin
#   password: secret
```

```yaml
# example/sources.yaml
sources:
  - name: users
    url: http://users-service/openapi.json
    schema_prefix: Users
    # auth:
    #   username: svc
    #   password: secret
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

**Step 2: Build Docker image**

```bash
docker build -t openapi-merger:local .
# expected: build succeeds
```

**Step 3: Verify image starts (no config → exits with error, not a crash)**

```bash
docker run --rm openapi-merger:local 2>&1 | head -5
# expected: FileNotFoundError or similar for missing /config/service.yaml
```

**Step 4: Run image with example config**

```bash
docker run --rm \
  -v $(pwd)/example:/config:ro \
  -p 8080:8080 \
  openapi-merger:local &
sleep 2
curl -s http://localhost:8080/health
# expected: {"status":"ok"}
# (full merge will fail since example URLs don't exist — that's fine)
kill %1
```

**Step 5: Commit**

```bash
git add example/
git commit -m "docs: add example configuration files"
```

---

## Running the full test suite

```bash
pytest -v
```

All tasks complete when all tests pass.
