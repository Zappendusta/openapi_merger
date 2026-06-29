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


def test_discard_path_exact_prefix():
    paths = {"/internal/health": {}, "/api/users": {}}
    result = transform_paths(paths, [], discard_paths=["/internal"])
    assert "/internal/health" not in result
    assert "/api/users" in result


def test_discard_multiple_prefixes():
    paths = {"/internal/x": {}, "/debug/y": {}, "/api/z": {}}
    result = transform_paths(paths, [], discard_paths=["/internal", "/debug"])
    assert "/internal/x" not in result
    assert "/debug/y" not in result
    assert "/api/z" in result


def test_discard_empty_list_keeps_all():
    paths = {"/a": {}, "/b": {}}
    result = transform_paths(paths, [], discard_paths=[])
    assert result == {"/a": {}, "/b": {}}


def test_discard_before_transform():
    # /internal is discarded before the /internal→/api transform is considered
    paths = {"/internal/secret": {}, "/other/path": {}}
    result = transform_paths(
        paths,
        [_t("/internal", "/api")],
        discard_paths=["/internal"],
    )
    assert "/internal/secret" not in result
    assert "/api/secret" not in result  # discard wins, transform not applied
    assert "/other/path" in result


def test_discard_no_partial_match():
    # /internal should NOT discard /internalize (path-boundary safe)
    paths = {"/internalize": {}, "/internal/x": {}}
    result = transform_paths(paths, [], discard_paths=["/internal"])
    assert "/internalize" in result
    assert "/internal/x" not in result


def test_discard_exact_path_no_trailing_segment():
    paths = {"/internal": {}, "/api/users": {}}
    result = transform_paths(paths, [], discard_paths=["/internal"])
    assert "/internal" not in result
    assert "/api/users" in result


def test_origin_stamped_on_operations():
    paths = {"/users": {"get": {"summary": "list"}, "post": {"summary": "create"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"
    assert result["/users"]["post"]["x-origin-api"] == "absence api"


def test_origin_only_on_http_methods():
    paths = {
        "/users": {
            "get": {"summary": "list"},
            "parameters": [{"name": "q", "in": "query"}],
            "summary": "Users path",
            "x-internal": True,
        }
    }
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"
    # non-operation keys are left exactly as they were
    assert result["/users"]["parameters"] == [{"name": "q", "in": "query"}]
    assert result["/users"]["summary"] == "Users path"
    assert result["/users"]["x-internal"] is True


def test_origin_none_no_marking():
    paths = {"/users": {"get": {"summary": "list"}}}
    result = transform_paths(paths, [], origin=None)
    assert result == {"/users": {"get": {"summary": "list"}}}


def test_origin_overwrites_existing():
    paths = {"/users": {"get": {"x-origin-api": "stale"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"]["x-origin-api"] == "absence api"


def test_origin_skips_non_dict_operation():
    # malformed operation value must not crash the merge
    paths = {"/users": {"get": "not-a-dict"}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["get"] == "not-a-dict"


def test_origin_uppercase_method_marked():
    paths = {"/users": {"GET": {"summary": "list"}}}
    result = transform_paths(paths, [], origin="absence api")
    assert result["/users"]["GET"]["x-origin-api"] == "absence api"


def test_origin_combined_with_transform_and_discard():
    paths = {
        "/internal/x": {"get": {}},
        "/api/users": {"get": {}},
    }
    result = transform_paths(
        paths,
        [_t("/api", "/api/v2")],
        discard_paths=["/internal"],
        origin="absence api",
    )
    assert "/internal/x" not in result
    assert result["/api/v2/users"]["get"]["x-origin-api"] == "absence api"
