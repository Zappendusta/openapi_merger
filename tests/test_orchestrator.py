import pytest
import respx
import httpx
from openapi_merger.orchestrator import MergeOrchestrator
from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.mergers.inhouse import InhouseMerger


_SVC_CFG = ServiceConfig.model_validate({
    "port": 8080,
    "spec_path": "/openapi.json",
    "info": {"title": "Merged", "version": "1.0"},
})

_SPEC_A = {
    "openapi": "3.0.0",
    "info": {"title": "A", "version": "1"},
    "paths": {"/api/users": {"get": {}}},
    "components": {"schemas": {"User": {"type": "object"}}},
}
_SPEC_B = {
    "openapi": "3.0.0",
    "info": {"title": "B", "version": "1"},
    "paths": {"/api/orders": {"get": {}}},
    "components": {"schemas": {"Order": {"type": "object"}}},
}

_SOURCES_CFG = SourcesConfig.model_validate({
    "sources": [
        {
            "name": "users",
            "url": "http://users/openapi.json",
            "schema_prefix": "Users",
            "route_transforms": [{"from": "/api", "to": "/api/users"}],
        },
        {
            "name": "orders",
            "url": "http://orders/openapi.json",
            "schema_prefix": "Orders",
            "route_transforms": [{"from": "/api", "to": "/api/orders"}],
        },
    ]
})


@respx.mock
async def test_get_merged_fetches_and_merges():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    merged = await o.get_merged()
    assert "/api/users/users" in merged["paths"]
    assert "/api/orders/orders" in merged["paths"]
    assert merged["info"]["title"] == "Merged"


@respx.mock
async def test_second_call_uses_cache():
    route_a = respx.get("http://users/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_A)
    )
    route_b = respx.get("http://orders/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_B)
    )

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    await o.get_merged()
    await o.get_merged()

    assert route_a.call_count == 1
    assert route_b.call_count == 1


@respx.mock
async def test_refresh_bypasses_cache():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    await o.get_merged()
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    await o.get_merged(refresh=True)

    assert respx.calls.call_count == 4


@respx.mock
async def test_upstream_error_propagates():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(500))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    with pytest.raises(RuntimeError, match="users"):
        await o.get_merged()


_SPEC_INTERNAL = {
    "openapi": "3.0.0",
    "info": {"title": "Internal", "version": "1"},
    "paths": {"/internal/secret": {"get": {}}, "/api/users": {"get": {}}},
    "components": {},
}

_SOURCES_DISCARD_CFG = SourcesConfig.model_validate({
    "sources": [
        {
            "name": "internal",
            "url": "http://internal/openapi.json",
            "schema_prefix": "Internal",
            "route_transforms": [],
            "discard_paths": ["/internal"],
        },
    ]
})


@respx.mock
async def test_discard_paths_excluded():
    respx.get("http://internal/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_INTERNAL)
    )

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_DISCARD_CFG, strategy=InhouseMerger())
    merged = await o.get_merged()
    assert "/internal/secret" not in merged["paths"]
    assert "/api/users" in merged["paths"]


@respx.mock
async def test_clear_cache_forces_rebuild():
    respx.get("http://users/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_A)
    )
    respx.get("http://orders/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC_B)
    )
    orch = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())

    await orch.get_merged()
    assert respx.calls.call_count == 2

    orch.clear_cache()
    assert orch._cache is None

    await orch.get_merged()
    assert respx.calls.call_count == 4


def test_clear_cache_is_noop_when_empty():
    orch = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    assert orch._cache is None
    orch.clear_cache()
    assert orch._cache is None


@respx.mock
async def test_get_merged_carries_security_schemes_through():
    spec_with_security = {
        "openapi": "3.0.0",
        "info": {"title": "A", "version": "1"},
        "paths": {
            "/api/users": {
                "get": {
                    "operationId": "getUser",
                    "security": [{"BearerAuth": []}],
                    "responses": {"200": {}},
                }
            }
        },
        "components": {
            "schemas": {"User": {"type": "object"}},
            "securitySchemes": {"BearerAuth": {"type": "http", "scheme": "bearer"}},
        },
    }

    respx.get("http://users/openapi.json").mock(
        return_value=httpx.Response(200, json=spec_with_security)
    )
    # source 'orders' is also configured in _SOURCES_CFG, so it needs a response too
    respx.get("http://orders/openapi.json").mock(
        return_value=httpx.Response(200, json={
            "openapi": "3.0.0",
            "info": {"title": "B", "version": "1"},
            "paths": {"/api/orders": {"get": {}}},
            "components": {"schemas": {"Order": {"type": "object"}}},
        })
    )

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG, strategy=InhouseMerger())
    merged = await o.get_merged()

    assert merged["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
    }
    # The operation-level security reference must survive too
    op = merged["paths"]["/api/users/users"]["get"]
    assert op["security"] == [{"BearerAuth": []}]


from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.config import InfoConfig


class _StubMerger:
    key = "stub"
    display_name = "stub"

    def __init__(self):
        self.calls = []

    def merge(self, sources, title, version):
        self.calls.append((len(sources), title, version))
        return {"openapi": "3.0.0", "info": {"title": title, "version": version}, "paths": {}, "components": {}}

    @classmethod
    def is_available(cls):
        return True


async def test_orchestrator_delegates_to_strategy(monkeypatch):
    async def _fake_fetch(source):
        return {
            "openapi": "3.0.0",
            "info": {"title": source.name, "version": "0.1"},
            "paths": {},
            "components": {},
        }

    monkeypatch.setattr("openapi_merger.orchestrator.fetch_spec", _fake_fetch)

    from openapi_merger.config import SourceConfig
    svc = ServiceConfig(info=InfoConfig(title="T", version="V"))
    srcs = SourcesConfig(sources=[
        SourceConfig(name="s1", url="http://x", schema_prefix="P1"),
    ])
    strategy = _StubMerger()
    orch = MergeOrchestrator(svc, srcs, strategy=strategy)
    result = await orch.get_merged()
    assert result["info"]["title"] == "T"
    assert strategy.calls == [(1, "T", "V")]
