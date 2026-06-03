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


def test_noisy_stdlib_loggers_are_silenced(monkeypatch):
    import logging as stdlib_logging
    _capture_output(monkeypatch)
    assert stdlib_logging.getLogger("httpx").level == stdlib_logging.WARNING
    assert stdlib_logging.getLogger("httpcore").level == stdlib_logging.WARNING


def test_app_startup_logs_loaded_config(monkeypatch, tmp_path, capsys):
    # Minimal service + sources YAML
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
