import pytest
import respx
import httpx
from openapi_merger.orchestrator import MergeOrchestrator
from openapi_merger.config import ServiceConfig, SourcesConfig


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

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
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

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    await o.get_merged()
    await o.get_merged()

    assert route_a.call_count == 1
    assert route_b.call_count == 1


@respx.mock
async def test_refresh_bypasses_cache():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    await o.get_merged()
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_A))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))
    await o.get_merged(refresh=True)

    assert respx.calls.call_count == 4


@respx.mock
async def test_upstream_error_propagates():
    respx.get("http://users/openapi.json").mock(return_value=httpx.Response(500))
    respx.get("http://orders/openapi.json").mock(return_value=httpx.Response(200, json=_SPEC_B))

    o = MergeOrchestrator(_SVC_CFG, _SOURCES_CFG)
    with pytest.raises(RuntimeError, match="users"):
        await o.get_merged()
