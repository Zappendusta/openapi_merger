from openapi_merger.transformer import transform_paths
from openapi_merger.config import RouteTransform


def _t(from_path, to):
    return RouteTransform.model_validate({"from": from_path, "to": to})


def test_prefix_replaced():
    paths = {"/api/users": {}, "/api/orders": {}}
    result = transform_paths(paths, [_t("/api", "/api/svc")])
    assert "/api/svc/users" in result
    assert "/api/svc/orders" in result
    assert "/api/users" not in result


def test_no_match_unchanged():
    paths = {"/other/path": {}}
    result = transform_paths(paths, [_t("/api", "/api/v2")])
    assert "/other/path" in result


def test_empty_transforms_unchanged():
    paths = {"/a": {}, "/b": {}}
    assert transform_paths(paths, []) == {"/a": {}, "/b": {}}


def test_transforms_applied_sequentially():
    # second transform acts on result of first
    paths = {"/v1/users": {}}
    result = transform_paths(paths, [_t("/v1", "/v2"), _t("/v2", "/v3")])
    assert "/v3/users" in result


def test_path_values_preserved():
    paths = {"/api/users": {"get": {"summary": "list"}}}
    result = transform_paths(paths, [_t("/api", "/api/v2")])
    assert result["/api/v2/users"] == {"get": {"summary": "list"}}


def test_empty_paths():
    assert transform_paths({}, [_t("/api", "/v2")]) == {}
