import shutil
import pytest
from fastapi.testclient import TestClient

from openapi_merger import main as main_module


_SPEC_A = {
    "openapi": "3.0.0",
    "info": {"title": "A", "version": "0.1"},
    "paths": {"/users": {"get": {"operationId": "listUsers", "responses": {"200": {"description": "ok"}}}}},
    "components": {"schemas": {"User": {"type": "object", "properties": {"id": {"type": "string"}}}}},
}
_SPEC_B = {
    "openapi": "3.0.0",
    "info": {"title": "B", "version": "0.1"},
    "paths": {"/orders": {"get": {"operationId": "listOrders", "responses": {"200": {"description": "ok"}}}}},
    "components": {"schemas": {"Order": {"type": "object", "properties": {"id": {"type": "string"}}}}},
}


@pytest.fixture
def app_with_real_mergers(monkeypatch, tmp_path):
    svc_yaml = tmp_path / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "default_merger: inhouse\n"
        "info:\n  title: Merged\n  version: 1.0.0\n"
    )
    src_yaml = tmp_path / "sources.yaml"
    src_yaml.write_text(
        "sources:\n"
        "  - name: alpha\n    url: http://alpha.invalid\n    schema_prefix: A\n"
        "  - name: beta\n    url: http://beta.invalid\n    schema_prefix: B\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(svc_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(src_yaml))

    async def _fake_fetch(source):
        return _SPEC_A if source.name == "alpha" else _SPEC_B
    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    with TestClient(main_module.app) as client:
        yield client


def _skip_if_missing(binary: str) -> None:
    if shutil.which(binary) is None:
        pytest.skip(f"{binary} binary not installed; skipping e2e test")


def test_inhouse_endpoint_real(app_with_real_mergers):
    r = app_with_real_mergers.get("/inhouse/openapi.json")
    assert r.status_code == 200
    assert "/users" in r.json()["paths"]
    assert "/orders" in r.json()["paths"]


def test_redocly_endpoint_real(app_with_real_mergers):
    _skip_if_missing("redocly")
    r = app_with_real_mergers.get("/redocly/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]


def test_speakeasy_endpoint_real(app_with_real_mergers):
    _skip_if_missing("speakeasy")
    r = app_with_real_mergers.get("/speakeasy/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]


def test_openapi_merge_endpoint_real(app_with_real_mergers):
    _skip_if_missing("openapi-merge-cli")
    r = app_with_real_mergers.get("/openapi-merge/openapi.json")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["info"] == {"title": "Merged", "version": "1.0.0"}
    assert "/users" in body["paths"]
    assert "/orders" in body["paths"]
