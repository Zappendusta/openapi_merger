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
