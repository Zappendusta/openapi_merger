import yaml
import httpx
from openapi_merger.config import SourceConfig


async def fetch_spec(source: SourceConfig) -> dict:
    auth = None
    if source.auth:
        auth = (source.auth.username, source.auth.password)
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(source.url, auth=auth)
    except Exception as e:
        raise RuntimeError(
            f"Failed to connect to '{source.name}' at {source.url}: {e}"
        ) from e

    if response.status_code != 200:
        raise RuntimeError(
            f"Upstream '{source.name}' returned HTTP {response.status_code}: {source.url}"
        )

    content_type = response.headers.get("content-type", "")
    if "yaml" in content_type or source.url.endswith((".yaml", ".yml")):
        return yaml.safe_load(response.text)
    return response.json()
