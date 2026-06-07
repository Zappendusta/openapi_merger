from __future__ import annotations

from openapi_merger.mergers.base import MergerStrategy
from openapi_merger.mergers.inhouse import InhouseMerger
from openapi_merger.mergers.openapi_merge import OpenApiMergeMerger
from openapi_merger.mergers.redocly import RedoclyMerger
from openapi_merger.mergers.speakeasy import SpeakeasyMerger

MERGER_REGISTRY: dict[str, type] = {
    InhouseMerger.key: InhouseMerger,
    RedoclyMerger.key: RedoclyMerger,
    SpeakeasyMerger.key: SpeakeasyMerger,
    OpenApiMergeMerger.key: OpenApiMergeMerger,
}


def get_merger(key: str) -> MergerStrategy:
    cls = MERGER_REGISTRY[key]
    return cls()


__all__ = ["MERGER_REGISTRY", "get_merger", "MergerStrategy"]
