# tests/e2e/conftest.py
from __future__ import annotations

import http.server
import importlib
import os
import pathlib
import socket
import threading

import pytest
import yaml
from fastapi.testclient import TestClient

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def upstream_server():
    """Start a static HTTP server serving tests/e2e/fixtures/."""

    class _QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass  # suppress access logs during tests

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(FIXTURES_DIR), **kwargs)

    port = _free_port()
    server = http.server.HTTPServer(("127.0.0.1", port), _QuietHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture(scope="module")
def merger_client(upstream_server, tmp_path_factory):
    """Boot the merger FastAPI app pointed at the real upstream server."""
    tmp = tmp_path_factory.mktemp("e2e_config")

    svc_yaml = tmp / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "info:\n"
        "  title: Merged API\n"
        "  version: '1.0'\n"
    )

    sources_dict = {
        "sources": [
            {
                "name": "users",
                "url": f"{upstream_server}/service_a.json",
                "schema_prefix": "Users",
                "discard_paths": ["/health", "/internal"],
                "route_transforms": [{"from": "/users", "to": "/api/users"}],
            },
            {
                "name": "orders",
                "url": f"{upstream_server}/service_b.yaml",
                "schema_prefix": "Orders",
                "discard_paths": ["/health"],
                "route_transforms": [{"from": "/orders", "to": "/api/orders"}],
            },
        ]
    }
    sources_yaml = tmp / "sources.yaml"
    sources_yaml.write_text(yaml.dump(sources_dict))

    prev_svc = os.environ.get("SERVICE_CONFIG")
    prev_src = os.environ.get("SOURCES_CONFIG")
    os.environ["SERVICE_CONFIG"] = str(svc_yaml)
    os.environ["SOURCES_CONFIG"] = str(sources_yaml)

    import openapi_merger.main as m
    importlib.reload(m)

    with TestClient(m.app) as client:
        yield client

    # Restore env
    if prev_svc is None:
        os.environ.pop("SERVICE_CONFIG", None)
    else:
        os.environ["SERVICE_CONFIG"] = prev_svc
    if prev_src is None:
        os.environ.pop("SOURCES_CONFIG", None)
    else:
        os.environ["SOURCES_CONFIG"] = prev_src
