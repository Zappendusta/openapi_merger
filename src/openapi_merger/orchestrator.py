import asyncio
import time

import structlog

from openapi_merger.config import ServiceConfig, SourcesConfig
from openapi_merger.fetcher import fetch_spec
from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.transformer import transform_paths

log = structlog.get_logger()


class MergeOrchestrator:
    def __init__(
        self,
        service_config: ServiceConfig,
        sources_config: SourcesConfig,
        strategy: MergerStrategy,
    ):
        self._service = service_config
        self._sources = sources_config
        self._strategy = strategy
        self._cache: dict | None = None

    async def get_merged(self, refresh: bool = False) -> dict:
        if self._cache is not None and not refresh:
            log.info("merge.cache.hit", merger=self._strategy.key)
            return self._cache
        log.info("merge.cache.miss", merger=self._strategy.key, refresh=refresh, cached=self._cache is not None)
        self._cache = await self._build()
        return self._cache

    def clear_cache(self) -> None:
        had_cache = self._cache is not None
        self._cache = None
        log.info("merge.cache.clear", merger=self._strategy.key, had_cache=had_cache)

    async def _build(self) -> dict:
        log.info(
            "merge.build.start",
            merger=self._strategy.key,
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
                    merger=self._strategy.key,
                    source=source.name,
                    paths_in=paths_before,
                    paths_out=paths_after,
                    discarded=paths_before - paths_after,
                    transforms_applied=len(source.route_transforms),
                )
                processed.append((source.name, source.schema_prefix, doc))

            merged = self._strategy.merge(
                processed,
                title=self._service.info.title,
                version=self._service.info.version,
            )
        except Exception as e:
            duration_ms = int((time.perf_counter() - start) * 1000)
            log.error(
                "merge.build.failed",
                merger=self._strategy.key,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        log.info(
            "merge.build.ok",
            merger=self._strategy.key,
            duration_ms=duration_ms,
            paths_count=len(merged.get("paths", {})),
            schemas_count=len(merged.get("components", {}).get("schemas", {})),
        )
        return merged
