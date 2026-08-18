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
from .drafts import (
    FormalPerformanceDraftStore,
    PerformanceDraftStore,
    StandardDraftStore,
)
from .aa import AzureArchiveAdapter

__all__ = [
    "AdapterBase",
    "AdapterRequest",
    "AdapterResult",
    "AdapterRegistry",
    "BuildBundleRef",
    "DraftRef",
    "ProductionAdapter",
    "FormalPerformanceDraftStore",
    "PerformanceDraftStore",
    "StandardDraftStore",
    "AzureArchiveAdapter",
]
