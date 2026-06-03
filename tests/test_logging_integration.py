import asyncio
import importlib
import os
import re
import sys

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from openapi_merger.config import SourceConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.logging_config import configure_logging


@pytest.fixture
def app_with_logs(tmp_path, monkeypatch, capsys):
    service_yaml = tmp_path / "service.yaml"
    service_yaml.write_text(
        "spec_path: /openapi\n"
        "info:\n  title: t\n  version: '1'\n"
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
    assert len(rid) >= 8


def test_client_supplied_request_id_is_echoed(app_with_logs):
    main_mod, capsys = app_with_logs
    with TestClient(main_mod.app) as client:
        resp = client.get("/health", headers={"X-Request-ID": "client-rid-123"})
    assert resp.headers["X-Request-ID"] == "client-rid-123"
    out = capsys.readouterr().out
    assert "request_id=client-rid-123" in out


def test_malicious_request_id_is_sanitized(app_with_logs):
    main_mod, _ = app_with_logs
    with TestClient(main_mod.app) as client:
        resp = client.get(
            "/health",
            headers={"X-Request-ID": "abc def\ninjected-line"},
        )
    # Whitespace and newline stripped; only allowed chars retained
    assert "\n" not in resp.headers["X-Request-ID"]
    assert " " not in resp.headers["X-Request-ID"]
    assert resp.headers["X-Request-ID"].startswith("abc")


@pytest.fixture()
def reset_structlog_to_stdout():
    """Reset structlog to write to real sys.stdout after each fetcher logging test.

    capsys temporarily replaces sys.stdout with a CaptureIO buffer.
    configure_logging() called without a stream captures that buffer reference.
    After the test the buffer is closed, breaking any subsequent test that logs.
    This fixture restores structlog to use the real stdout after the test body.
    """
    yield
    configure_logging(stream=sys.__stdout__)


@respx.mock
def test_fetcher_logs_success(monkeypatch, capsys, reset_structlog_to_stdout):
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
def test_fetcher_logs_failure_then_raises(monkeypatch, capsys, reset_structlog_to_stdout):
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


@respx.mock
def test_orchestrator_logs_cache_miss_then_hit(monkeypatch, capsys, reset_structlog_to_stdout):
    from openapi_merger.config import ServiceConfig, SourcesConfig, InfoConfig
    from openapi_merger.orchestrator import MergeOrchestrator

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(
        return_value=httpx.Response(200, json={"openapi": "3.0.0", "paths": {"/a": {"get": {}}}, "components": {}})
    )
    svc = ServiceConfig(spec_path="/openapi", info=InfoConfig(title="t", version="1"))
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
def test_orchestrator_logs_build_failure(monkeypatch, capsys, reset_structlog_to_stdout):
    from openapi_merger.config import ServiceConfig, SourcesConfig, InfoConfig
    from openapi_merger.orchestrator import MergeOrchestrator

    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("LOG_FORMAT", "logfmt")
    configure_logging()

    respx.get("https://x/api").mock(return_value=httpx.Response(500))
    svc = ServiceConfig(spec_path="/openapi", info=InfoConfig(title="t", version="1"))
    src_cfg = SourcesConfig(sources=[SourceConfig(name="users", url="https://x/api", schema_prefix="U")])
    orch = MergeOrchestrator(svc, src_cfg)

    with pytest.raises(RuntimeError):
        asyncio.run(orch.get_merged())

    out = capsys.readouterr().out
    assert "event=merge.build.failed" in out
    assert "level=error" in out


def test_merger_logs_schema_collision(monkeypatch, capsys, reset_structlog_to_stdout):
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


def test_merger_logs_path_collision_and_raises(monkeypatch, capsys, reset_structlog_to_stdout):
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


@respx.mock
def test_full_request_chain_shares_request_id(tmp_path, monkeypatch, capsys, reset_structlog_to_stdout):
    service_yaml = tmp_path / "service.yaml"
    service_yaml.write_text(
        "spec_path: /openapi\n"
        "info:\n  title: t\n  version: '1'\n"
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
