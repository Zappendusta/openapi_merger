from openapi_merger.mergers.inhouse import InhouseMerger


def test_inhouse_merger_matches_merge_specs():
    sources = [
        ("alpha", "Alpha", {
            "openapi": "3.0.0",
            "info": {"title": "A", "version": "0.1"},
            "paths": {"/a": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }),
        ("beta", "Beta", {
            "openapi": "3.0.0",
            "info": {"title": "B", "version": "0.1"},
            "paths": {"/b": {"get": {"responses": {"200": {"description": "ok"}}}}},
            "components": {"schemas": {}},
        }),
    ]
    merger = InhouseMerger()
    out = merger.merge(sources, title="Merged", version="9.9")
    assert out["info"] == {"title": "Merged", "version": "9.9"}
    assert "/a" in out["paths"]
    assert "/b" in out["paths"]


def test_inhouse_merger_is_available():
    assert InhouseMerger.is_available() is True
