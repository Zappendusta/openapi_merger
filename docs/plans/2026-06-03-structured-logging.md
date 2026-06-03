# Structured Logging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add structured, kubectl-readable logging to the merger so operators tailing pod logs see when each upstream spec is fetched, when results are served from cache, when fetches fail, and when merges fail — with enough detail to diagnose without being noisy.

**Architecture:** Use `structlog` with a `KeyValueRenderer` (logfmt) so each line is human-readable in raw `kubectl logs` while staying machine-parseable. A single configuration module sets up renderers and processors at app startup. A FastAPI middleware generates a `request_id` per request and binds it into `structlog.contextvars` so every log emitted during that request carries the same `request_id`, letting operators correlate fetch/merge logs back to the originating HTTP call. Each pipeline stage (fetch, transform, merge, cache) emits one summary event per source/stage, not per-path noise.

**Tech Stack:** Python 3.12, FastAPI, structlog, stdlib `logging` (structlog routes through it), uvicorn.

**Log levels — convention used throughout this plan:**
- `info` — normal events (fetch ok, cache hit, build start/end, request done, merge ok)
- `warning` — recoverable anomalies (schema/operationId collisions resolved by prefixing)
- `error` — pipeline failures (fetch failure, merge failure, path collision)
- `debug` — fine-grained detail (per-path transform, full payload sizes) — off by default

**Event naming convention:** dotted lowercase, `<subsystem>.<action>`. Examples: `spec.fetch.start`, `spec.fetch.ok`, `spec.fetch.failed`, `merge.cache.hit`, `merge.build.start`, `merge.build.ok`, `merge.collision.schema`, `request.completed`. Use these exact strings — Task 9's integration test asserts on them.

**File structure:**
- Create `src/openapi_merger/logging_config.py` — structlog setup (named with `_config` suffix to avoid shadowing stdlib `logging`).
- Create `tests/test_logging_config.py` — unit tests for the config module.
- Create `tests/test_logging_integration.py` — end-to-end test asserting expected events fire with expected fields.
- Modify `pyproject.toml` — add `structlog` dependency.
- Modify `src/openapi_merger/main.py` — call setup at startup, add request middleware.
- Modify `src/openapi_merger/fetcher.py` — emit fetch start/ok/failed events.
- Modify `src/openapi_merger/orchestrator.py` — emit cache hit/miss and build start/ok events.
- Modify `src/openapi_merger/transformer.py` — return a summary so orchestrator can log per-source transform counts (transformer stays log-free to keep it pure).
- Modify `src/openapi_merger/merger.py` — emit collision warnings and merge result counts; convert existing `RuntimeError` raises to also log first.

---

### Task 1: Add structlog dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add structlog to dependencies**

Edit `pyproject.toml`. Find the `dependencies = [` block and add `"structlog>=24.1"` as the last entry. Result:

```toml
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "httpx>=0.27",
    "pydantic>=2.6",
    "pyyaml>=6.0",
    "structlog>=24.1",
]
```

- [ ] **Step 2: Reinstall dev environment**

Run: `pip install -e ".[dev]"`
Expected: `Successfully installed ... structlog-24.x`

- [ ] **Step 3: Verify import works**

Run: `python -c "import structlog; print(structlog.__version__)"`
Expected: Prints a version `>= 24.1`.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add structlog dependency"
```

---

### Task 2: Create logging configuration module

**Files:**
- Create: `src/openapi_merger/logging_config.py`
- Create: `tests/test_logging_config.py`

This module configures structlog once. `LOG_LEVEL` env var controls verbosity (default `INFO`). `LOG_FORMAT` env var picks renderer (default `logfmt`, alt `json` for downstream aggregators).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_logging_config.py`:

```python
import io
import logging
import os
import structlog

from openapi_merger.logging_config import configure_logging


def _capture_output(monkeypatch, level="INFO", fmt="logfmt"):
    monkeypatch.setenv("LOG_LEVEL", level)
    monkeypatch.setenv("LOG_FORMAT", fmt)
    buf = io.StringIO()
    configure_logging(stream=buf)
    return buf


def test_logfmt_output_contains_event_and_level(monkeypatch):
    buf = _capture_output(monkeypatch)
    structlog.get_logger().info("spec.fetch.ok", source="users", duration_ms=42)
    out = buf.getvalue()
    assert "event=spec.fetch.ok" in out
    assert "level=info" in out
    assert "source=users" in out
    assert "duration_ms=42" in out


def test_json_output_is_valid_json(monkeypatch):
    import json
    buf = _capture_output(monkeypatch, fmt="json")
    structlog.get_logger().info("merge.cache.hit", refresh=False)
    line = buf.getvalue().strip().splitlines()[-1]
    parsed = json.loads(line)
    assert parsed["event"] == "merge.cache.hit"
    assert parsed["level"] == "info"
    assert parsed["refresh"] is False


def test_log_level_filters_debug_by_default(monkeypatch):
    buf = _capture_output(monkeypatch, level="INFO")
    structlog.get_logger().debug("spec.transform.path", path="/foo")
    assert buf.getvalue() == ""


def test_log_level_debug_emits_debug(monkeypatch):
    buf = _capture_output(monkeypatch, level="DEBUG")
    structlog.get_logger().debug("spec.transform.path", path="/foo")
    assert "event=spec.transform.path" in buf.getvalue()


def test_contextvars_are_merged(monkeypatch):
    buf = _capture_output(monkeypatch)
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="abc-123")
    structlog.get_logger().info("request.completed", status=200)
    structlog.contextvars.clear_contextvars()
    assert "request_id=abc-123" in buf.getvalue()
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/test_logging_config.py -v`
Expected: All five tests fail with `ModuleNotFoundError: openapi_merger.logging_config`.

- [ ] **Step 3: Implement the module**

Create `src/openapi_merger/logging_config.py`:

```python
from __future__ import annotations

import logging
import os
import sys
from typing import IO

import structlog


def configure_logging(stream: IO[str] | None = None) -> None:
    """Configure structlog + stdlib logging.

    Env vars:
        LOG_LEVEL: DEBUG | INFO | WARNING | ERROR (default INFO)
        LOG_FORMAT: logfmt | json (default logfmt)
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = os.getenv("LOG_FORMAT", "logfmt").lower()
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.processors.KeyValueRenderer(
            key_order=["timestamp", "level", "event", "request_id"],
            drop_missing=True,
        )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stdout),
        cache_logger_on_first_use=False,
    )

    # Route uvicorn / fastapi stdlib loggers through the same handler so they
    # also show up in structured form. Uvicorn's own access log stays on its
    # default formatter — we add our own request middleware in Task 4.
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=stream or sys.stdout,
        force=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_logging_config.py -v`
Expected: All five tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/openapi_merger/logging_config.py tests/test_logging_config.py
git commit -m "feat: add structlog configuration module"
```

---

### Task 3: Initialize logging at app startup

**Files:**
- Modify: `src/openapi_merger/main.py` (top of file + inside `lifespan`)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logging_config.py`:

```python
def test_app_startup_logs_loaded_config(monkeypatch, tmp_path, capsys):
    # Minimal service + sources YAML
    service_yaml = tmp_path / "service.yaml"
    service_yaml.write_text(
        "spec_path: /openapi\n"
        "info:\n  title: t\n  version: 1\n"
    )
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text("sources: []\n")

    monkeypatch.setenv("SERVICE_CONFIG", str(service_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(sources_yaml))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")

    import importlib
    import openapi_merger.main as main_mod
    importlib.reload(main_mod)

    from fastapi.testclient import TestClient
    with TestClient(main_mod.app):
        pass

    captured = capsys.readouterr()
    assert "event=app.startup" in captured.out
    assert "sources_count=0" in captured.out
    assert "spec_path=/openapi" in captured.out
```

- [ ] **Step 2: Run it to confirm failure**

Run: `pytest tests/test_logging_config.py::test_app_startup_logs_loaded_config -v`
Expected: FAIL — `event=app.startup` not in output.

- [ ] **Step 3: Wire up logging in main.py**

In `src/openapi_merger/main.py`, add imports near the top (after existing imports):

```python
import structlog

from openapi_merger.logging_config import configure_logging

log = structlog.get_logger()
```

Inside `lifespan`, immediately after `_orchestrator = MergeOrchestrator(...)` and before defining `_get_spec`, add:

```python
    configure_logging()
    log.info(
        "app.startup",
        service_config=svc_path,
        sources_config=src_path,
        spec_path=_service_config.spec_path,
        sources_count=len(sources_config.sources),
        auth_enabled=_service_config.auth is not None,
    )
```

Also add at the very end of `lifespan` (after `yield`):

```python
    log.info("app.shutdown")
```

- [ ] **Step 4: Run the test to verify pass**

Run: `pytest tests/test_logging_config.py::test_app_startup_logs_loaded_config -v`
Expected: PASS.

- [ ] **Step 5: Run full suite to make sure nothing else broke**

Run: `pytest`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/main.py tests/test_logging_config.py
git commit -m "feat: configure logging and log startup event"
```

---

### Task 4: Request middleware with request_id

**Files:**
- Modify: `src/openapi_merger/main.py`
- Create test in: `tests/test_logging_integration.py`

This middleware binds a UUID4 `request_id` into `structlog.contextvars` for the lifetime of each request, so any log emitted during that request inherits it. After the response is produced it logs `request.completed` with method, path, status, and duration. The `request_id` is also returned in the `X-Request-ID` response header.

- [ ] **Step 1: Write the failing test**

Create `tests/test_logging_integration.py`:

```python
import importlib
import io
import os
import re

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def app_with_logs(tmp_path, monkeypatch, capsys):
    service_yaml = tmp_path / "service.yaml"
    service_yaml.write_text(
        "spec_path: /openapi\n"
        "info:\n  title: t\n  version: 1\n"
    )
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text("sources: []\n")
    monkeypatch.setenv("SERVICE_CONFIG", str(service_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(sources_yaml))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")

    import openapi_merger.main as main_mod
    importlib.reload(main_mod)
    return main_mod, capsys


def test_health_request_logs_completed_event(app_with_logs):
    main_mod, capsys = app_with_logs
    with TestClient(main_mod.app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers
    out = capsys.readouterr().out
    assert "event=request.completed" in out
    assert "method=GET" in out
    assert "path=/health" in out
    assert "status=200" in out
    assert re.search(r"duration_ms=\d+", out)


def test_request_id_is_propagated_into_response(app_with_logs):
    main_mod, _ = app_with_logs
    with TestClient(main_mod.app) as client:
        resp = client.get("/health")
    rid = resp.headers["X-Request-ID"]
    assert len(rid) >= 8  # UUID-ish
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_logging_integration.py -v`
Expected: FAIL — `X-Request-ID` missing / event not in output.

- [ ] **Step 3: Add the middleware**

In `src/openapi_merger/main.py`, after `app = FastAPI(...)` add:

```python
import time
import uuid


@app.middleware("http")
async def _request_log_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    start = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.exception(
            "request.failed",
            method=request.method,
            path=request.url.path,
            duration_ms=duration_ms,
        )
        raise
    duration_ms = int((time.perf_counter() - start) * 1000)
    log.info(
        "request.completed",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_logging_integration.py -v`
Expected: Both tests PASS.

- [ ] **Step 5: Confirm full suite still green**

Run: `pytest`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/main.py tests/test_logging_integration.py
git commit -m "feat: add request_id middleware with structured access logs"
```

---

### Task 5: Fetcher events (start / ok / failed)

**Files:**
- Modify: `src/openapi_merger/fetcher.py`

Goal: one `spec.fetch.start` at entry, one `spec.fetch.ok` on success with duration and size, one `spec.fetch.failed` (level=error) on any failure path. The existing `RuntimeError` raises stay — they bubble up to the 502 handler — but they now log first.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_logging_integration.py`:

```python
import httpx
import respx


@respx.mock
def test_fetcher_logs_success(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.config import SourceConfig
    from openapi_merger.fetcher import fetch_spec
    import asyncio

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(
        return_value=httpx.Response(200, json={"openapi": "3.0.0", "paths": {}})
    )
    src = SourceConfig(name="users", url="https://x/api", schema_prefix="U")
    asyncio.run(fetch_spec(src))

    out = capsys.readouterr().out
    assert "event=spec.fetch.start" in out
    assert "event=spec.fetch.ok" in out
    assert "source=users" in out
    assert "status=200" in out
    assert re.search(r"duration_ms=\d+", out)
    assert "format=json" in out


@respx.mock
def test_fetcher_logs_failure_then_raises(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.config import SourceConfig
    from openapi_merger.fetcher import fetch_spec
    import asyncio

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(return_value=httpx.Response(503))
    src = SourceConfig(name="users", url="https://x/api", schema_prefix="U")

    with pytest.raises(RuntimeError):
        asyncio.run(fetch_spec(src))

    out = capsys.readouterr().out
    assert "event=spec.fetch.failed" in out
    assert "level=error" in out
    assert "source=users" in out
    assert "status=503" in out
```

- [ ] **Step 2: Run them to confirm failure**

Run: `pytest tests/test_logging_integration.py -k fetcher -v`
Expected: FAIL — events not emitted.

- [ ] **Step 3: Add logging to fetcher**

Replace the contents of `src/openapi_merger/fetcher.py` with:

```python
import time

import httpx
import structlog
import yaml

from openapi_merger.config import SourceConfig

log = structlog.get_logger()


async def fetch_spec(source: SourceConfig) -> dict:
    auth = None
    if source.auth:
        auth = (source.auth.username, source.auth.password)

    log.info("spec.fetch.start", source=source.name, url=source.url)
    start = time.perf_counter()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(source.url, auth=auth)
    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.error(
            "spec.fetch.failed",
            source=source.name,
            url=source.url,
            reason="connection_error",
            error=str(e),
            duration_ms=duration_ms,
        )
        raise RuntimeError(
            f"Failed to connect to '{source.name}' at {source.url}: {e}"
        ) from e

    duration_ms = int((time.perf_counter() - start) * 1000)

    if response.status_code != 200:
        log.error(
            "spec.fetch.failed",
            source=source.name,
            url=source.url,
            reason="http_error",
            status=response.status_code,
            duration_ms=duration_ms,
        )
        raise RuntimeError(
            f"Upstream '{source.name}' returned HTTP {response.status_code}: {source.url}"
        )

    content_type = response.headers.get("content-type", "")
    is_yaml = "yaml" in content_type or source.url.endswith((".yaml", ".yml"))
    fmt = "yaml" if is_yaml else "json"
    doc = yaml.safe_load(response.text) if is_yaml else response.json()

    log.info(
        "spec.fetch.ok",
        source=source.name,
        status=response.status_code,
        duration_ms=duration_ms,
        size_bytes=len(response.content),
        format=fmt,
    )
    return doc
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_logging_integration.py -k fetcher -v`
Expected: Both PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/fetcher.py tests/test_logging_integration.py
git commit -m "feat: log spec fetch start/ok/failed with timings and sizes"
```

---

### Task 6: Orchestrator cache + build events

**Files:**
- Modify: `src/openapi_merger/orchestrator.py`

Events emitted:
- `merge.cache.hit` (info) when `get_merged` serves cache.
- `merge.cache.miss` (info) when it has to build (also fires on `refresh=true`, with `refresh=true` field).
- `merge.build.start` (info) at top of `_build`.
- `merge.build.ok` (info) at end of `_build` with duration and counts (paths, schemas).
- `merge.build.failed` (error) on exception — re-raise after logging.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_logging_integration.py`:

```python
@respx.mock
def test_orchestrator_logs_cache_miss_then_hit(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.config import ServiceConfig, SourcesConfig, SourceConfig, Info
    from openapi_merger.orchestrator import MergeOrchestrator
    import asyncio

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(
        return_value=httpx.Response(200, json={"openapi": "3.0.0", "paths": {"/a": {"get": {}}}, "components": {}})
    )
    svc = ServiceConfig(spec_path="/openapi", info=Info(title="t", version="1"))
    src_cfg = SourcesConfig(sources=[SourceConfig(name="users", url="https://x/api", schema_prefix="U")])
    orch = MergeOrchestrator(svc, src_cfg)

    asyncio.run(orch.get_merged())
    asyncio.run(orch.get_merged())  # second call → cache hit

    out = capsys.readouterr().out
    assert "event=merge.cache.miss" in out
    assert "event=merge.build.start" in out
    assert "event=merge.build.ok" in out
    assert "event=merge.cache.hit" in out
    assert "paths_count=1" in out


@respx.mock
def test_orchestrator_logs_build_failure(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.config import ServiceConfig, SourcesConfig, SourceConfig, Info
    from openapi_merger.orchestrator import MergeOrchestrator
    import asyncio

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(return_value=httpx.Response(500))
    svc = ServiceConfig(spec_path="/openapi", info=Info(title="t", version="1"))
    src_cfg = SourcesConfig(sources=[SourceConfig(name="users", url="https://x/api", schema_prefix="U")])
    orch = MergeOrchestrator(svc, src_cfg)

    with pytest.raises(RuntimeError):
        asyncio.run(orch.get_merged())

    out = capsys.readouterr().out
    assert "event=merge.build.failed" in out
    assert "level=error" in out
```

- [ ] **Step 2: Run them to confirm failure**

Run: `pytest tests/test_logging_integration.py -k orchestrator -v`
Expected: FAIL — events missing.

- [ ] **Step 3: Add logging to orchestrator**

Replace `src/openapi_merger/orchestrator.py` with:

```python
import asyncio
import time

import structlog

from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.merger import merge_specs
from openapi_merger.transformer import transform_paths

log = structlog.get_logger()


class MergeOrchestrator:
    def __init__(self, service_config: ServiceConfig, sources_config: SourcesConfig):
        self._service = service_config
        self._sources = sources_config
        self._cache: dict | None = None

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            log.info("merge.cache.hit")
            return self._cache
        log.info("merge.cache.miss", refresh=refresh, cached=self._cache is not None)
        self._cache = await self._build()
        return self._cache

    async def _build(self) -> dict:
        log.info(
            "merge.build.start",
            sources_count=len(self._sources.sources),
            source_names=[s.name for s in self._sources.sources],
        )
        start = time.perf_counter()
        try:
            docs = await asyncio.gather(
                *[fetch_spec(s) for s in self._sources.sources]
            )
            processed = []
            for source, doc in zip(self._sources.sources, docs):
                paths_before = len(doc.get("paths", {}))
                doc["paths"] = transform_paths(
                    doc.get("paths", {}),
                    source.route_transforms,
                    discard_paths=source.discard_paths,
                )
                paths_after = len(doc["paths"])
                log.info(
                    "spec.transform.ok",
                    source=source.name,
                    paths_in=paths_before,
                    paths_out=paths_after,
                    discarded=paths_before - paths_after,
                    transforms_applied=len(source.route_transforms),
                )
                processed.append((source.name, source.schema_prefix, doc))

            merged = merge_specs(
                processed,
                title=self._service.info.title,
                version=self._service.info.version,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "merge.build.failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "merge.build.ok",
            duration_ms=duration_ms,
            paths_count=len(merged.get("paths", {})),
            schemas_count=len(merged.get("components", {}).get("schemas", {})),
        )
        return merged
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_logging_integration.py -k orchestrator -v`
Expected: Both PASS.

- [ ] **Step 5: Run full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/orchestrator.py tests/test_logging_integration.py
git commit -m "feat: log cache hits/misses and merge build lifecycle"
```

---

### Task 7: Merger collision warnings

**Files:**
- Modify: `src/openapi_merger/merger.py`

When schema or operationId collisions are detected, log one `merge.collision.schema` / `merge.collision.operation_id` warning per colliding name with the sources involved and the resolved prefix. When a path collision raises `RuntimeError`, log `merge.path_collision` (error) before raising.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_logging_integration.py`:

```python
def test_merger_logs_schema_collision(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.merger import merge_specs

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    a = {
        "openapi": "3.0.0",
        "paths": {"/a": {"get": {}}},
        "components": {"schemas": {"Item": {"type": "object", "properties": {"x": {"type": "integer"}}}}},
    }
    b = {
        "openapi": "3.0.0",
        "paths": {"/b": {"get": {}}},
        "components": {"schemas": {"Item": {"type": "object", "properties": {"x": {"type": "string"}}}}},
    }
    merge_specs([("a", "A", a), ("b", "B", b)], title="t", version="1")

    out = capsys.readouterr().out
    assert "event=merge.collision.schema" in out
    assert "level=warning" in out
    assert "name=Item" in out


def test_merger_logs_path_collision_and_raises(monkeypatch, capsys):
    from openapi_merger.logging_config import configure_logging
    from openapi_merger.merger import merge_specs

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    a = {"openapi": "3.0.0", "paths": {"/dup": {"get": {}}}, "components": {}}
    b = {"openapi": "3.0.0", "paths": {"/dup": {"get": {}}}, "components": {}}
    with pytest.raises(RuntimeError):
        merge_specs([("a", "A", a), ("b", "B", b)], title="t", version="1")

    out = capsys.readouterr().out
    assert "event=merge.path_collision" in out
    assert "level=error" in out
    assert "path=/dup" in out
```

- [ ] **Step 2: Run to confirm failure**

Run: `pytest tests/test_logging_integration.py -k merger -v`
Expected: FAIL.

- [ ] **Step 3: Add logging to merger**

In `src/openapi_merger/merger.py`, add at the top after existing imports:

```python
import structlog

log = structlog.get_logger()
```

Inside `merge_specs`, just after the two `detect_*_collisions` calls, add:

```python
    for name, sources_with_name in collisions.items():
        log.warning(
            "merge.collision.schema",
            name=name,
            sources=sources_with_name,
            resolution="prefix",
        )
    for op_id, sources_with_op in op_collisions.items():
        log.warning(
            "merge.collision.operation_id",
            operation_id=op_id,
            sources=sources_with_op,
            resolution="prefix",
        )
```

Then change the path-collision `raise RuntimeError(...)` block. Find:

```python
            if path in merged_paths:
                raise RuntimeError(
                    f"Path collision: '{path}' found in '{source_name}' and an earlier source"
                )
```

Replace with:

```python
            if path in merged_paths:
                log.error(
                    "merge.path_collision",
                    path=path,
                    source=source_name,
                )
                raise RuntimeError(
                    f"Path collision: '{path}' found in '{source_name}' and an earlier source"
                )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_logging_integration.py -k merger -v`
Expected: Both PASS.

- [ ] **Step 5: Full suite**

Run: `pytest`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/openapi_merger/merger.py tests/test_logging_integration.py
git commit -m "feat: log schema, operationId, and path collisions during merge"
```

---

### Task 8: End-to-end smoke test asserting log shape under a real request

**Files:**
- Modify: `tests/test_logging_integration.py`

Verifies that a single HTTP request through the merged spec endpoint emits the full chain — `request.completed` → `merge.cache.miss` → `merge.build.start` → `spec.fetch.start`/`spec.fetch.ok` → `spec.transform.ok` → `merge.build.ok` — all carrying the same `request_id`.

- [ ] **Step 1: Write the test**

Append to `tests/test_logging_integration.py`:

```python
@respx.mock
def test_full_request_chain_shares_request_id(tmp_path, monkeypatch, capsys):
    service_yaml = tmp_path / "service.yaml"
    service_yaml.write_text(
        "spec_path: /openapi\n"
        "info:\n  title: t\n  version: 1\n"
    )
    sources_yaml = tmp_path / "sources.yaml"
    sources_yaml.write_text(
        "sources:\n"
        "  - name: users\n"
        "    url: https://x/api\n"
        "    schema_prefix: U\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(service_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(sources_yaml))
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")

    respx.get("https://x/api").mock(
        return_value=httpx.Response(
            200,
            json={"openapi": "3.0.0", "paths": {"/a": {"get": {}}}, "components": {}},
        )
    )

    import importlib
    import openapi_merger.main as main_mod
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        resp = client.get("/openapi")
    assert resp.status_code == 200
    rid = resp.headers["X-Request-ID"]

    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if f"request_id={rid}" in l]
    events_seen = {re.search(r"event=(\S+)", l).group(1) for l in lines}
    assert "merge.cache.miss" in events_seen
    assert "merge.build.start" in events_seen
    assert "spec.fetch.start" in events_seen
    assert "spec.fetch.ok" in events_seen
    assert "spec.transform.ok" in events_seen
    assert "merge.build.ok" in events_seen
    assert "request.completed" in events_seen
```

- [ ] **Step 2: Run it**

Run: `pytest tests/test_logging_integration.py::test_full_request_chain_shares_request_id -v`
Expected: PASS (everything is already wired up by prior tasks).

If it fails, the likely cause is that `configure_logging()` re-runs inside `lifespan` and resets the bound contextvars. The middleware re-binds per request so this should not be a problem, but double-check that `clear_contextvars()` only fires inside the middleware, not at startup.

- [ ] **Step 3: Commit**

```bash
git add tests/test_logging_integration.py
git commit -m "test: assert request_id correlates across full merge pipeline"
```

---

### Task 9: README note on logging

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a logging section**

Add a new `## Logging` section to `README.md` (place it after the existing configuration/usage sections — read the file first to find the natural slot). Content:

```markdown
## Logging

The service emits structured logs to stdout. Each pipeline event is one line.

Default format is **logfmt** (`key=value`) — readable in `kubectl logs` and parseable by Loki / ELK alike. Set `LOG_FORMAT=json` for pure JSON.

Set `LOG_LEVEL` to one of `DEBUG`, `INFO` (default), `WARNING`, `ERROR`.

Key events:

| Event                             | Level   | Meaning                                              |
|-----------------------------------|---------|------------------------------------------------------|
| `app.startup`                     | info    | Service started, configs loaded.                     |
| `request.completed`               | info    | HTTP request finished — includes `request_id`, status, duration. |
| `merge.cache.hit`                 | info    | Served from in-process cache.                        |
| `merge.cache.miss`                | info    | Cache empty or `?refresh=true` — rebuilding.          |
| `spec.fetch.start` / `spec.fetch.ok` | info | Upstream OpenAPI fetched.                          |
| `spec.fetch.failed`               | error   | Upstream returned non-200 or connection failed.      |
| `spec.transform.ok`               | info    | Per-source path filter + rewrite summary.            |
| `merge.collision.schema`          | warning | Same schema name with different content across sources — resolved by prefixing. |
| `merge.collision.operation_id`    | warning | Same operationId with different content — resolved by prefixing. |
| `merge.path_collision`            | error   | Duplicate path across sources — request fails 502.   |
| `merge.build.ok` / `merge.build.failed` | info/error | Merge pipeline result.                       |

Every log emitted during an HTTP request carries the same `request_id`, also returned in the `X-Request-ID` response header — copy it from a failed request to grep the pod log.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document structured logging events and env vars"
```

---

## Self-Review Notes

- **Spec coverage check.** User asked for: log on API pull, log on cache hit, log on fetch failure, log on merge failure, structured + not too verbose, kubectl-readable. → `spec.fetch.start/ok/failed` (Task 5), `merge.cache.hit/miss` (Task 6), `merge.build.failed` + `merge.path_collision` (Tasks 6, 7), logfmt + structlog (Tasks 1–2), one summary event per source/stage rather than per-path (no DEBUG logs at INFO level). All covered.
- **Placeholders.** None — every code/test step has full source.
- **Type consistency.** `SourceConfig`, `ServiceConfig`, `SourcesConfig`, `Info` names used in tests match `src/openapi_merger/config.py`. `Info` model exists (used in `_service_config.info.title` in current `main.py`). The `transformer.py` keeps its existing signature — the orchestrator computes pre/post path counts itself; no changes needed in `transformer.py`. Event strings used in tests (`spec.fetch.start`, `merge.cache.hit`, etc.) match the strings emitted in implementation steps exactly.
- **Risk note for executor.** When running the integration tests in sequence inside the same process, structlog's global state persists across tests. The fixture in Task 4 calls `configure_logging()` per test, which is idempotent (it calls `structlog.configure(...)` from scratch each time). If a test sees stale config, ensure `configure_logging()` ran inside that test, not just a prior one.
