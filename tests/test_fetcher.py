import base64
import pytest
import respx
import httpx
import yaml as pyyaml
from openapi_merger.fetcher import fetch_spec
from openapi_merger.config import SourceConfig


def _source(url, auth=None):
    data = {"name": "test", "url": url, "schema_prefix": "Test"}
    if auth:
        data["auth"] = auth
    return SourceConfig.model_validate(data)


_SPEC = {"openapi": "3.0.0", "info": {"title": "T", "version": "1"}, "paths": {}}


@respx.mock
async def test_fetch_json():
    respx.get("http://svc/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC)
    )
    result = await fetch_spec(_source("http://svc/openapi.json"))
    assert result["openapi"] == "3.0.0"


@respx.mock
async def test_fetch_yaml():
    respx.get("http://svc/openapi.yaml").mock(
        return_value=httpx.Response(
            200,
            text=pyyaml.dump(_SPEC),
            headers={"content-type": "text/yaml"},
        )
    )
    result = await fetch_spec(_source("http://svc/openapi.yaml"))
    assert result["openapi"] == "3.0.0"


@respx.mock
async def test_fetch_yaml_by_url_extension():
    respx.get("http://svc/spec.yml").mock(
        return_value=httpx.Response(200, text=pyyaml.dump(_SPEC))
    )
    result = await fetch_spec(_source("http://svc/spec.yml"))
    assert result["info"]["title"] == "T"


@respx.mock
async def test_basic_auth_header_sent():
    route = respx.get("http://svc/openapi.json").mock(
        return_value=httpx.Response(200, json=_SPEC)
    )
    await fetch_spec(_source("http://svc/openapi.json", auth={"username": "u", "password": "p"}))
    auth_header = route.calls[0].request.headers["authorization"]
    expected = "Basic " + base64.b64encode(b"u:p").decode()
    assert auth_header == expected


@respx.mock
async def test_upstream_http_error_raises():
    respx.get("http://svc/openapi.json").mock(return_value=httpx.Response(500))
    with pytest.raises(RuntimeError, match="test"):
        await fetch_spec(_source("http://svc/openapi.json"))


@respx.mock
async def test_network_error_raises():
    respx.get("http://svc/openapi.json").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(RuntimeError, match="test"):
        await fetch_spec(_source("http://svc/openapi.json"))
