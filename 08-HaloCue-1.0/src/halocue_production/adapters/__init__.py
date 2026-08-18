"""Formal production-adapter boundary for HaloCue."""

from .base import (
    AdapterBase,
    AdapterRequest,
    AdapterResult,
    BuildBundleRef,
    DraftRef,
    ProductionAdapter,
)
from .registry import AdapterRegistry

__all__ = [
    "AdapterBase",
    "AdapterRequest",
    "AdapterResult",
    "AdapterRegistry",
    "BuildBundleRef",
    "DraftRef",
    "ProductionAdapter",
]
