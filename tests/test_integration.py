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
