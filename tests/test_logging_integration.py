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
