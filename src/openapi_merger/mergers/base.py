from __future__ import annotations
from typing import Protocol, runtime_checkable


class MergerNotAvailable(RuntimeError):
    """Raised by external merger adapters when the underlying binary is absent from PATH."""


@runtime_checkable
class MergerStrategy(Protocol):
    """Contract for an OpenAPI merger implementation.

    Sources are tuples of (source_name, schema_prefix, pre_transformed_doc).
    Each implementation decides how to interpret schema_prefix.
    """

    key: str
    display_name: str

    def merge(self, sources: list[tuple[str, str, dict]], title: str, version: str) -> dict:
        ...

    @classmethod
    def is_available(cls) -> bool:
        ...
