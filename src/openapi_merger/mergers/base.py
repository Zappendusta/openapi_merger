from __future__ import annotations
from typing import Protocol, runtime_checkable


class MergerNotAvailable(RuntimeError):
    """Raised when a merger's underlying binary is not installed."""


@runtime_checkable
class MergerStrategy(Protocol):
    """Contract for an OpenAPI merger implementation.

    Sources are tuples of (source_name, schema_prefix, pre_transformed_doc).
    The schema_prefix is honored only by InhouseMerger; external mergers ignore it.
    """

    key: str
    display_name: str

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        ...

    @classmethod
    def is_available(cls) -> bool:
        ...
