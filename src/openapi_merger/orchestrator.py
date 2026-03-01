import asyncio
from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.transformer import transform_paths
from openapi_merger.merger import merge_specs


class MergeOrchestrator:
    def __init__(self, service_config: ServiceConfig, sources_config: SourcesConfig):
        self._service = service_config
        self._sources = sources_config
        self._cache: dict | None = None

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            return self._cache
        self._cache = await self._build()
        return self._cache

    async def _build(self) -> dict:
        docs = await asyncio.gather(
            *[fetch_spec(s) for s in self._sources.sources]
        )
        processed = []
        for source, doc in zip(self._sources.sources, docs):
            doc["paths"] = transform_paths(
                doc.get("paths", {}), source.route_transforms
            )
            processed.append((source.name, source.schema_prefix, doc))
        return merge_specs(
            processed,
            title=self._service.info.title,
            version=self._service.info.version,
        )
