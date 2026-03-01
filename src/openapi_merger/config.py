from __future__ import annotations
import pathlib
import yaml
from pydantic import BaseModel, Field


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
    auth: AuthConfig | None = None
    info: InfoConfig


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
