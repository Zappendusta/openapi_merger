from __future__ import annotations

from openapi_merger.merger import merge_specs


class InhouseMerger:
    key = "inhouse"
    display_name = "in-house"

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        return merge_specs(sources, title=title, version=version)

    @classmethod
    def is_available(cls) -> bool:
        return True
