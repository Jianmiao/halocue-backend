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
from .storyforge import FfmpegVideoExporter, StoryForgeAdapter, StoryForgeRenderer
from .bundles import BuildBundleAssembler, DeliverableInput

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
    "FfmpegVideoExporter",
    "StoryForgeAdapter",
    "StoryForgeRenderer",
    "BuildBundleAssembler",
    "DeliverableInput",
]
