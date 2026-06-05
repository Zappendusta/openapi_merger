import copy

import pytest
from openapi_merger.merger import (
    rewrite_ref,
    detect_schema_collisions,
    assign_unique_operation_ids,
    merge_specs,
)


# --- rewrite_ref ---

def test_rewrite_ref_top_level():
    doc = {"$ref": "#/components/schemas/Widget"}
    assert rewrite_ref(doc, "Widget", "OrdersWidget") == {
        "$ref": "#/components/schemas/OrdersWidget"
    }


def test_rewrite_ref_nested_dict():
    doc = {"properties": {"item": {"$ref": "#/components/schemas/Widget"}}}
    result = rewrite_ref(doc, "Widget", "OrdersWidget")
    assert result["properties"]["item"]["$ref"] == "#/components/schemas/OrdersWidget"


def test_rewrite_ref_in_list():
    doc = {"oneOf": [
        {"$ref": "#/components/schemas/Widget"},
        {"$ref": "#/components/schemas/Other"},
    ]}
    result = rewrite_ref(doc, "Widget", "OrdersWidget")
    assert result["oneOf"][0]["$ref"] == "#/components/schemas/OrdersWidget"
    assert result["oneOf"][1]["$ref"] == "#/components/schemas/Other"


def test_rewrite_ref_no_match():
    doc = {"$ref": "#/components/schemas/Other"}
    assert rewrite_ref(doc, "Widget", "OrdersWidget") == {"$ref": "#/components/schemas/Other"}


def test_rewrite_ref_non_schema_ref_untouched():
    doc = {"$ref": "#/components/responses/Error"}
    assert rewrite_ref(doc, "Error", "NewError") == {"$ref": "#/components/responses/Error"}


# --- detect_schema_collisions ---

def test_no_collision():
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
        ("b", "B", {"components": {"schemas": {"Bar": {"type": "string"}}}}),
    ]
    assert detect_schema_collisions(sources) == {}


def test_equal_schemas_not_a_collision():
    schema = {"type": "object", "properties": {"id": {"type": "string"}}}
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": schema}}}),
        ("b", "B", {"components": {"schemas": {"Foo": schema}}}),
    ]
    assert detect_schema_collisions(sources) == {}


def test_different_schemas_is_collision():
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
        ("b", "B", {"components": {"schemas": {"Foo": {"type": "string"}}}}),
    ]
    collisions = detect_schema_collisions(sources)
    assert "Foo" in collisions
    assert set(collisions["Foo"]) == {"a", "b"}


def test_collision_only_reports_differing_sources():
    schema = {"type": "object"}
    sources = [
        ("a", "A", {"components": {"schemas": {"Foo": schema}}}),
        ("b", "B", {"components": {"schemas": {"Foo": schema}}}),
        ("c", "C", {"components": {"schemas": {"Foo": {"type": "string"}}}}),
    ]
    collisions = detect_schema_collisions(sources)
    assert "Foo" in collisions  # c differs from a and b


def test_source_with_no_components():
    sources = [
        ("a", "A", {"paths": {}}),
        ("b", "B", {"components": {"schemas": {"Foo": {"type": "object"}}}}),
    ]
    assert detect_schema_collisions(sources) == {}


# --- assign_unique_operation_ids ---

def _op(op_id, summary="s"):
    return {"operationId": op_id, "summary": summary, "responses": {"200": {}}}


def test_assign_op_ids_no_conflicts_unchanged():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("listA")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "listA"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "listB"


def test_assign_op_ids_cross_source_uses_prefix():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("doThing", summary="from a")}}}),
        ("b", "B", {"paths": {"/b": {"get": _op("doThing", summary="from b")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "BdoThing"
    assert renames == [
        {"source": "b", "path": "/b", "method": "get", "old": "doThing", "new": "BdoThing", "reason": "cross_source"},
    ]


def test_assign_op_ids_equal_content_still_deduped():
    op = _op("doThing")
    sources = [
        ("a", "A", {"paths": {"/a": {"get": copy.deepcopy(op)}}}),
        ("b", "B", {"paths": {"/b": {"get": copy.deepcopy(op)}}}),
    ]
    assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "BdoThing"


def test_assign_op_ids_within_source_uses_prefix():
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("getItem", summary="first")},
            "/y": {"get": _op("getItem", summary="second")},
        }}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "getItem"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "AgetItem"
    assert len(renames) == 1
    assert renames[0]["reason"] == "within_source"


def test_assign_op_ids_post_prefix_uses_numeric_suffix():
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("foo", summary="a-foo")},
            "/y": {"get": _op("Bfoo", summary="a-Bfoo")},
        }}),
        ("b", "B", {"paths": {"/z": {"get": _op("foo", summary="b-foo")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "foo"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "Bfoo"
    assert sources[1][2]["paths"]["/z"]["get"]["operationId"] == "foo_2"
    assert any(r["reason"] == "post_prefix" and r["new"] == "foo_2" for r in renames)


def test_assign_op_ids_within_source_chain_uses_numeric_after_prefix():
    sources = [
        ("a", "A", {"paths": {
            "/x": {"get": _op("bar", summary="1")},
            "/y": {"get": _op("Abar", summary="2")},
            "/z": {"get": _op("bar", summary="3")},
        }}),
    ]
    assign_unique_operation_ids(sources)
    assert sources[0][2]["paths"]["/x"]["get"]["operationId"] == "bar"
    assert sources[0][2]["paths"]["/y"]["get"]["operationId"] == "Abar"
    assert sources[0][2]["paths"]["/z"]["get"]["operationId"] == "bar_2"


def test_assign_op_ids_missing_field_ignored():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": {"responses": {"200": {}}}}}}),
        ("b", "B", {"paths": {"/b": {"get": {"responses": {"200": {}}}}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert "operationId" not in sources[0][2]["paths"]["/a"]["get"]
    assert "operationId" not in sources[1][2]["paths"]["/b"]["get"]


def test_assign_op_ids_non_method_keys_ignored():
    sources = [
        ("a", "A", {"paths": {"/a": {"get": _op("getA"), "parameters": [], "summary": "x"}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[0][2]["paths"]["/a"]["get"]["operationId"] == "getA"


def test_assign_op_ids_no_paths_ok():
    sources = [
        ("a", "A", {"components": {}}),
        ("b", "B", {"paths": {"/b": {"get": _op("listB")}}}),
    ]
    renames = assign_unique_operation_ids(sources)
    assert renames == []
    assert sources[1][2]["paths"]["/b"]["get"]["operationId"] == "listB"


def test_assign_op_ids_numeric_suffix_overflow_raises():
    paths = {"/p0": {"get": _op("foo", summary="first")}}
    for i in range(2, 1001):
        paths[f"/p{i}"] = {"get": _op(f"foo_{i}", summary=str(i))}
    paths["/dup"] = {"get": _op("foo", summary="trigger overflow")}
    sources = [("a", "", {"paths": paths})]
    with pytest.raises(RuntimeError, match="could not assign unique operationId"):
        assign_unique_operation_ids(sources)


# --- merge_specs ---

def _doc(paths, schemas, openapi="3.0.0"):
    return {
        "openapi": openapi,
        "info": {"title": "T", "version": "1"},
        "paths": paths,
        "components": {"schemas": schemas},
    }


def test_merge_no_collision():
    sources = [
        ("a", "A", _doc({"/a": {}}, {"Foo": {"type": "object"}})),
        ("b", "B", _doc({"/b": {}}, {"Bar": {"type": "string"}})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert "/a" in merged["paths"]
    assert "/b" in merged["paths"]
    assert "Foo" in merged["components"]["schemas"]
    assert "Bar" in merged["components"]["schemas"]


def test_merge_equal_schemas_deduped():
    schema = {"type": "object"}
    sources = [
        ("a", "A", _doc({"/a": {}}, {"Foo": schema})),
        ("b", "B", _doc({"/b": {}}, {"Foo": schema})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert list(merged["components"]["schemas"].keys()) == ["Foo"]


def test_merge_collision_resolved_with_prefix():
    ref_path = {
        "/a": {
            "get": {
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Foo"}
                            }
                        }
                    }
                }
            }
        }
    }
    sources = [
        ("a", "A", _doc(ref_path, {"Foo": {"type": "object"}})),
        ("b", "B", _doc({"/b": {}}, {"Foo": {"type": "string"}})),
    ]
    merged = merge_specs(sources, title="Merged", version="1.0")
    assert "AFoo" in merged["components"]["schemas"]
    assert "BFoo" in merged["components"]["schemas"]
    assert "Foo" not in merged["components"]["schemas"]
    # $ref in source a must be rewritten
    ref = (
        merged["paths"]["/a"]["get"]["responses"]["200"]
        ["content"]["application/json"]["schema"]["$ref"]
    )
    assert ref == "#/components/schemas/AFoo"


def test_merge_path_collision_raises():
    sources = [
        ("a", "A", _doc({"/clash": {}}, {})),
        ("b", "B", _doc({"/clash": {}}, {})),
    ]
    with pytest.raises(RuntimeError, match="/clash"):
        merge_specs(sources, title="Merged", version="1.0")


def test_merge_sets_metadata():
    sources = [("a", "A", _doc({"/a": {}}, {}))]
    merged = merge_specs(sources, title="My API", version="2.0")
    assert merged["info"]["title"] == "My API"
    assert merged["info"]["version"] == "2.0"


def test_merge_preserves_openapi_version_from_first_source():
    sources = [
        ("a", "A", _doc({"/a": {}}, {}, openapi="3.1.0")),
        ("b", "B", _doc({"/b": {}}, {}, openapi="3.0.0")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["openapi"] == "3.1.0"


def _doc_with_op(path, method, op_id, summary="s", schemas=None):
    return {
        "openapi": "3.0.0",
        "info": {"title": "T", "version": "1"},
        "paths": {path: {method: {"operationId": op_id, "summary": summary, "responses": {"200": {}}}}},
        "components": {"schemas": schemas or {}},
    }


def test_merge_no_op_id_collision():
    sources = [
        ("a", "A", _doc_with_op("/a", "get", "listA")),
        ("b", "B", _doc_with_op("/b", "get", "listB")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "listA"
    assert merged["paths"]["/b"]["get"]["operationId"] == "listB"


def test_merge_op_id_collision_resolved_with_prefix():
    sources = [
        ("a", "A", _doc_with_op("/a", "get", "doThing", summary="from a")),
        ("b", "B", _doc_with_op("/b", "get", "doThing", summary="from b")),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BdoThing"


def test_merge_equal_op_ids_now_deduped():
    op_def = {"operationId": "doThing", "summary": "same", "responses": {"200": {}}}
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/a": {"get": copy.deepcopy(op_def)}},
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/b": {"get": copy.deepcopy(op_def)}},
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "doThing"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BdoThing"


def test_merge_op_id_collision_multiple_methods():
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/a": {
                "get": {"operationId": "getItem", "summary": "a", "responses": {"200": {}}},
                "post": {"operationId": "createItem", "responses": {"200": {}}},
            }},
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {"/b": {
                "get": {"operationId": "getItem", "summary": "b", "responses": {"200": {}}},
            }},
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/a"]["get"]["operationId"] == "getItem"
    assert merged["paths"]["/b"]["get"]["operationId"] == "BgetItem"
    assert merged["paths"]["/a"]["post"]["operationId"] == "createItem"


def test_merge_within_source_op_id_collision_resolved():
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {"get": {"operationId": "getItem", "summary": "1", "responses": {"200": {}}}},
                "/y": {"get": {"operationId": "getItem", "summary": "2", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    assert merged["paths"]["/x"]["get"]["operationId"] == "getItem"
    assert merged["paths"]["/y"]["get"]["operationId"] == "AgetItem"


def test_merge_all_op_ids_unique_in_output():
    sources = [
        ("a", "A", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/x": {"get": {"operationId": "dup", "summary": "ax", "responses": {"200": {}}}},
                "/y": {"get": {"operationId": "dup", "summary": "ay", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
        ("b", "B", {
            "openapi": "3.0.0", "info": {"title": "T", "version": "1"},
            "paths": {
                "/z": {"get": {"operationId": "dup", "summary": "bz", "responses": {"200": {}}}},
            },
            "components": {"schemas": {}},
        }),
    ]
    merged = merge_specs(sources, title="T", version="1")
    op_ids = [
        op["operationId"]
        for path_item in merged["paths"].values()
        for method, op in path_item.items()
        if method in {"get", "put", "post", "delete", "options", "head", "patch", "trace"}
        and isinstance(op, dict)
        and "operationId" in op
    ]
    assert len(op_ids) == len(set(op_ids)), f"duplicate operationIds in merged output: {op_ids}"
