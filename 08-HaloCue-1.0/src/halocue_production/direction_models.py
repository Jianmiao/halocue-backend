from __future__ import annotations

import importlib
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .errors import ProductionError
from .model_settings import DirectionModelSettings


CancellationProbe = Callable[[], bool]


class CancellableModelProvider:
    """Add persisted cancellation checks without changing the AA provider."""

    def __init__(self, provider: Any, cancellation_probe: CancellationProbe) -> None:
        self._provider = provider
        self._cancellation_probe = cancellation_probe

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    def _check(self) -> None:
        if self._cancellation_probe():
            raise ProductionError(
                "operation_cancelled", "模型调用已取消", status=409
            )

    def _activity(self, downstream=None):
        def callback(event: dict[str, Any]) -> None:
            self._check()
            if downstream is not None:
                downstream(event)
            self._check()

        return callback

    def complete_json(self, static_system, volatile_system, user, schema):
        self._check()
        stream = getattr(self._provider, "complete_json_stream", None)
        if callable(stream):
            result = stream(
                static_system,
                volatile_system,
                user,
                schema,
                on_activity=self._activity(),
            )
        else:
            result = self._provider.complete_json(
                static_system, volatile_system, user, schema
            )
        self._check()
        return result

    def complete_json_stream(
        self,
        static_system,
        volatile_system,
        user,
        schema,
        *,
        on_activity=None,
    ):
        self._check()
        stream = getattr(self._provider, "complete_json_stream", None)
        if callable(stream):
            result = stream(
                static_system,
                volatile_system,
                user,
                schema,
                on_activity=self._activity(on_activity),
            )
        else:
            result = self._provider.complete_json(
                static_system, volatile_system, user, schema
            )
        self._check()
        return result


class DirectionModelGateway:
    """Create a 1.0-owned model connection using the proven transport adapter."""

    def __init__(self, settings: DirectionModelSettings, legacy_root: Path) -> None:
        self.settings = settings
        self.legacy_root = legacy_root

    def provider(self):
        provider_name, provider_settings = self.settings.provider_settings()
        legacy = str(self.legacy_root)
        if legacy not in sys.path:
            sys.path.insert(0, legacy)
        try:
            module = importlib.import_module("llm")
            return module.make_provider_from_settings(provider_name, provider_settings)
        except ProductionError as exc:
            if exc.code == "operation_cancelled":
                raise
            raise ProductionError(
                "model_provider_unavailable",
                "模型提供方不可用，请检查模型配置和网络",
                status=503,
                details={"type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            raise ProductionError(
                "model_provider_unavailable",
                "模型提供方不可用，请检查模型配置和网络",
                status=503,
                details={"type": type(exc).__name__},
            ) from exc

    def test_connection(
        self, cancellation_probe: CancellationProbe | None = None
    ) -> dict[str, Any]:
        provider = self.provider()
        active_provider = (
            CancellableModelProvider(provider, cancellation_probe)
            if cancellation_probe is not None
            else provider
        )
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        started = time.monotonic()
        try:
            result = active_provider.complete_json(
                "You are a connection test. Return JSON only.",
                "",
                'Return exactly {"ok":true}.',
                schema,
            )
        except ProductionError as exc:
            if exc.code == "operation_cancelled":
                raise
            raise ProductionError(
                "model_connection_failed",
                "模型连接测试失败，请检查模型配置和网络",
                status=502,
                details={"type": type(exc).__name__},
            ) from exc
        except Exception as exc:
            raise ProductionError(
                "model_connection_failed",
                "模型连接测试失败，请检查模型配置和网络",
                status=502,
                details={"type": type(exc).__name__},
            ) from exc
        return {
            "ok": True,
            "connection": {
                "provider": str(getattr(provider, "name", "")),
                "model": str(getattr(provider, "model", "")),
                "latency_ms": round((time.monotonic() - started) * 1000),
                "valid": result.get("ok") is True,
                "usage": dict(getattr(provider, "stats", {}) or {}),
            },
        }
