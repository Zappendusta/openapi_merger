import asyncio
import time

import structlog

from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.merger import merge_specs
from openapi_merger.transformer import transform_paths

log = structlog.get_logger()


class MergeOrchestrator:
    def __init__(self, service_config: ServiceConfig, sources_config: SourcesConfig):
        self._service = service_config
        self._sources = sources_config
        self._cache: dict | None = None

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            log.info("merge.cache.hit")
            return self._cache
        log.info("merge.cache.miss", refresh=refresh, cached=self._cache is not None)
        self._cache = await self._build()
        return self._cache

    async def _build(self) -> dict:
        log.info(
            "merge.build.start",
            sources_count=len(self._sources.sources),
            source_names=[s.name for s in self._sources.sources],
        )
        start = time.perf_counter()
        try:
            docs = await asyncio.gather(
                *[fetch_spec(s) for s in self._sources.sources]
            )
            processed = []
            for source, doc in zip(self._sources.sources, docs):
                paths_before = len(doc.get("paths", {}))
                doc["paths"] = transform_paths(
                    doc.get("paths", {}),
                    source.route_transforms,
                    discard_paths=source.discard_paths,
                )
                paths_after = len(doc["paths"])
                log.info(
                    "spec.transform.ok",
                    source=source.name,
                    paths_in=paths_before,
                    paths_out=paths_after,
                    discarded=paths_before - paths_after,
                    transforms_applied=len(source.route_transforms),
                )
                processed.append((source.name, source.schema_prefix, doc))

            merged = merge_specs(
                processed,
                title=self._service.info.title,
                version=self._service.info.version,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "merge.build.failed",
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "merge.build.ok",
            duration_ms=duration_ms,
            paths_count=len(merged.get("paths", {})),
            schemas_count=len(merged.get("components", {}).get("schemas", {})),
        )
        return merged
