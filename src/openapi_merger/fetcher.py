import time

import httpx
import structlog
import yaml

from openapi_merger.config import SourceConfig

log = structlog.get_logger()


async def fetch_spec(source: SourceConfig) -> dict:
    auth = None
    if source.auth:
        auth = (source.auth.username, source.auth.password)

    log.info("spec.fetch.start", source=source.name, url=source.url)
    start = time.perf_counter()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(source.url, auth=auth)
    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        log.error(
            "spec.fetch.failed",
            source=source.name,
            url=source.url,
            reason="connection_error",
            error=str(e),
            duration_ms=duration_ms,
        )
        raise RuntimeError(
            f"Failed to connect to '{source.name}' at {source.url}: {e}"
        ) from e

    duration_ms = int((time.perf_counter() - start) * 1000)

    if response.status_code != 200:
        log.error(
            "spec.fetch.failed",
            source=source.name,
            url=source.url,
            reason="http_error",
            status=response.status_code,
            duration_ms=duration_ms,
        )
        raise RuntimeError(
            f"Upstream '{source.name}' returned HTTP {response.status_code}: {source.url}"
        )

    content_type = response.headers.get("content-type", "")
    is_yaml = "yaml" in content_type or source.url.endswith((".yaml", ".yml"))
    fmt = "yaml" if is_yaml else "json"
    doc = yaml.safe_load(response.text) if is_yaml else response.json()

    log.info(
        "spec.fetch.ok",
        source=source.name,
        status=response.status_code,
        duration_ms=duration_ms,
        size_bytes=len(response.content),
        format=fmt,
    )
    return doc
