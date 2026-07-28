from __future__ import annotations
import pathlib
import yaml
from pydantic import BaseModel, Field, field_validator


class AuthConfig(BaseModel):
    username: str
    password: str


class RouteTransform(BaseModel):
    model_config = {"populate_by_name": True}
    from_path: str = Field(alias="from")
    to: str


class SourceConfig(BaseModel):
    name: str
    url: str
    schema_prefix: str
    auth: AuthConfig | None = None
    route_transforms: list[RouteTransform] = []
    discard_paths: list[str] = []


class InfoConfig(BaseModel):
    title: str
    version: str


class ServiceConfig(BaseModel):
    port: int = 8080
    spec_path: str = "/openapi.json"
    default_merger: str = "inhouse"
    cache_ttl_seconds: int = Field(default=600, ge=0)
    auth: AuthConfig | None = None
    info: InfoConfig

    @field_validator("default_merger")
    @classmethod
    def _validate_default_merger(cls, v: str) -> str:
        valid = {"inhouse", "redocly", "speakeasy", "openapi-merge"}
        if v not in valid:
            raise ValueError(f"default_merger must be one of {sorted(valid)}, got '{v}'")
        return v


class SourcesConfig(BaseModel):
    sources: list[SourceConfig]


def _load_yaml(path: str) -> dict:
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with p.open() as f:
        return yaml.safe_load(f)


def load_service_config(path: str) -> ServiceConfig:
    return ServiceConfig.model_validate(_load_yaml(path))


def load_sources_config(path: str) -> SourcesConfig:
    return SourcesConfig.model_validate(_load_yaml(path))
