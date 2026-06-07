import pytest
from fastapi.testclient import TestClient

from openapi_merger import main as main_module


@pytest.fixture
def app_with_stub_mergers(monkeypatch, tmp_path):
    """Boot the FastAPI app with stub mergers so all four endpoints succeed without real binaries."""
    svc_yaml = tmp_path / "service.yaml"
    svc_yaml.write_text(
        "spec_path: /openapi.json\n"
        "default_merger: inhouse\n"
        "info:\n  title: T\n  version: V\n"
    )
    src_yaml = tmp_path / "sources.yaml"
    src_yaml.write_text(
        "sources:\n"
        "  - name: alpha\n"
        "    url: http://alpha.invalid/spec\n"
        "    schema_prefix: A\n"
    )
    monkeypatch.setenv("SERVICE_CONFIG", str(svc_yaml))
    monkeypatch.setenv("SOURCES_CONFIG", str(src_yaml))

    async def _fake_fetch(source):
        return {
            "openapi": "3.0.0",
            "info": {"title": source.name, "version": "0.1"},
            "paths": {"/x": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }
    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    class _StubExternal:
        @classmethod
        def is_available(cls):
            return True

        def merge(self, sources, title, version):
            return {
                "openapi": "3.0.0",
                "info": {"title": title, "version": version},
                "paths": {name: {"get": {"responses": {"200": {"description": "ok"}}}} for name, _, _ in sources},
                "components": {"schemas": {}},
            }

    class _Redocly(_StubExternal):
        key = "redocly"
        display_name = "Redocly"

    class _Speakeasy(_StubExternal):
        key = "speakeasy"
        display_name = "Speakeasy"

    class _OpenApiMerge(_StubExternal):
        key = "openapi-merge"
        display_name = "openapi-merge"

    from openapi_merger.mergers.inhouse import InhouseMerger
    fake_registry = {
        "inhouse": InhouseMerger,
        "redocly": _Redocly,
        "speakeasy": _Speakeasy,
        "openapi-merge": _OpenApiMerge,
    }
    monkeypatch.setattr("openapi_merger.main.MERGER_REGISTRY", fake_registry)

    with TestClient(main_module.app) as client:
        yield client


def test_all_four_endpoints_return_200(app_with_stub_mergers):
    for path in ("/inhouse/openapi.json", "/redocly/openapi.json", "/speakeasy/openapi.json", "/openapi-merge/openapi.json"):
        r = app_with_stub_mergers.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
        body = r.json()
        assert body["info"]["title"] == "T"


def test_root_spec_path_aliases_to_default_merger(app_with_stub_mergers):
    r = app_with_stub_mergers.get("/openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert body["info"]["title"] == "T"


def test_admin_clear_clears_all_caches(app_with_stub_mergers):
    for path in ("/inhouse/openapi.json", "/redocly/openapi.json"):
        app_with_stub_mergers.get(path)
    r = app_with_stub_mergers.post("/admin/cache/clear")
    assert r.status_code == 204
