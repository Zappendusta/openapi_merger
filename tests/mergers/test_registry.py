from openapi_merger.mergers import MERGER_REGISTRY, get_merger


def test_registry_contains_all_four_mergers():
    assert set(MERGER_REGISTRY.keys()) == {"inhouse", "redocly", "speakeasy", "openapi-merge"}


def test_get_merger_returns_instance():
    inhouse = get_merger("inhouse")
    assert inhouse.key == "inhouse"


def test_get_merger_raises_on_unknown_key():
    import pytest
    with pytest.raises(KeyError):
        get_merger("not-a-merger")
