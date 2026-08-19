from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

from ..contracts import ContractValidationError, validate_contract
from ..errors import ProductionError
from .base import ProductionAdapter


class AdapterRegistry:
    """Validate and route formal production adapters by stable target."""

    def __init__(self, adapters: Iterable[ProductionAdapter]) -> None:
        self._adapters: dict[str, ProductionAdapter] = {}
        self._capabilities: dict[str, dict[str, Any]] = {}
        self._targets: dict[str, ProductionAdapter] = {}
        self._register_all(adapters)

    def _register_all(self, adapters: Iterable[ProductionAdapter]) -> None:
        for adapter in adapters:
            if not isinstance(adapter, ProductionAdapter):
                raise ProductionError(
                    "adapter_invalid",
                    "适配器未实现完整 ProductionAdapter 协议",
                    status=500,
                )
            try:
                payload = validate_contract("AdapterCapabilities", adapter.capabilities())
            except (ContractValidationError, TypeError, ValueError) as exc:
                contract = getattr(exc, "contract", "AdapterCapabilities")
                path = getattr(exc, "path", "$")
                raise ProductionError(
                    "adapter_capabilities_invalid",
                    "适配器能力合同无效",
                    status=500,
                    details={
                        "adapter_id": str(
                            getattr(adapter, "adapter_id", "unknown-adapter")
                        ),
                        "contract": contract,
                        "path": path,
                        "reason": str(exc),
                    },
                ) from exc
            adapter_id = payload["adapter_id"]
            if adapter_id in self._adapters:
                raise ProductionError(
                    "adapter_id_conflict",
                    "适配器 ID 不能重复",
                    status=409,
                    details={"adapter_id": adapter_id},
                )
            targets = payload["targets"]
            if len(targets) != len(set(targets)):
                raise ProductionError(
                    "adapter_capabilities_invalid",
                    "单个适配器不能重复声明 target",
                    status=500,
                    details={"adapter_id": adapter_id},
                )
            for target in targets:
                if target in self._targets:
                    existing = self._targets[target].capabilities()["adapter_id"]
                    raise ProductionError(
                        "adapter_target_conflict",
                        "同一 target 不能由多个适配器负责",
                        status=409,
                        details={
                            "target": target,
                            "adapter_id": adapter_id,
                            "existing_adapter_id": existing,
                        },
                    )
            self._adapters[adapter_id] = adapter
            self._capabilities[adapter_id] = copy.deepcopy(payload)
            for target in targets:
                self._targets[target] = adapter

    def for_target(self, target: str) -> ProductionAdapter:
        normalized = str(target or "").strip().casefold()
        adapter = self._targets.get(normalized)
        if adapter is None:
            raise ProductionError(
                "adapter_target_unavailable",
                "没有适配器支持请求的 target",
                status=409,
                details={"target": normalized},
            )
        return adapter

    def for_adapter(self, adapter_id: str) -> ProductionAdapter:
        normalized = str(adapter_id or "").strip().casefold()
        adapter = self._adapters.get(normalized)
        if adapter is None:
            raise ProductionError(
                "adapter_not_found",
                "适配器不存在",
                status=404,
                details={"adapter_id": normalized},
            )
        return adapter

    def all_capabilities(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(self._capabilities[key]) for key in sorted(self._capabilities)]

    def __iter__(self):
        return iter(self._adapters.values())

    def __len__(self) -> int:
        return len(self._adapters)


__all__ = ["AdapterRegistry"]
