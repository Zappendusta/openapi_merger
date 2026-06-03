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
