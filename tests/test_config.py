import pytest
from pydantic import ValidationError
from openapi_merger.config import (
    ServiceConfig, SourcesConfig,
    load_service_config, load_sources_config,
)


def test_service_config_minimal():
    cfg = ServiceConfig.model_validate({
        "port": 8080,
        "spec_path": "/openapi.json",
        "info": {"title": "Test API", "version": "1.0.0"},
    })
    assert cfg.port == 8080
    assert cfg.auth is None
    assert cfg.spec_path == "/openapi.json"


def test_service_config_with_auth():
    cfg = ServiceConfig.model_validate({
        "port": 8080,
        "spec_path": "/openapi.json",
        "info": {"title": "T", "version": "1"},
        "auth": {"username": "admin", "password": "secret"},
    })
    assert cfg.auth.username == "admin"
    assert cfg.auth.password == "secret"


def test_source_requires_schema_prefix():
    with pytest.raises(ValidationError, match="schema_prefix"):
        SourcesConfig.model_validate({
            "sources": [{"name": "svc", "url": "http://example.com/openapi.json"}]
        })


def test_source_valid_minimal():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://example.com/openapi.json",
            "schema_prefix": "Svc",
        }]
    })
    assert cfg.sources[0].schema_prefix == "Svc"
    assert cfg.sources[0].auth is None
    assert cfg.sources[0].route_transforms == []


def test_source_with_transforms():
    cfg = SourcesConfig.model_validate({
        "sources": [{
            "name": "svc",
            "url": "http://svc/openapi.json",
            "schema_prefix": "Svc",
            "route_transforms": [{"from": "/api", "to": "/api/svc"}],
        }]
    })
    t = cfg.sources[0].route_transforms[0]
    assert t.from_path == "/api"
    assert t.to == "/api/svc"


def test_load_service_config_file_not_found():
    with pytest.raises(FileNotFoundError):
        load_service_config("/nonexistent/path.yaml")


def test_load_sources_config_valid(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text("sources:\n  - name: s\n    url: http://x/openapi.json\n    schema_prefix: S\n")
    cfg = load_sources_config(str(f))
    assert cfg.sources[0].name == "s"


def test_load_sources_config_invalid_yaml(tmp_path):
    f = tmp_path / "sources.yaml"
    f.write_text(": : bad yaml {[")
    with pytest.raises(Exception):
        load_sources_config(str(f))
