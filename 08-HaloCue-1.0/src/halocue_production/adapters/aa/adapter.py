from __future__ import annotations

import copy
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from ...contracts import validate_contract
from ...errors import ProductionError
from ..base import AdapterBase, AdapterRequest, AdapterResult, BuildBundleRef, DraftRef
from ..drafts import PerformanceDraftStore
from .translation import LegacyCompileInput, translate_performance_draft


class BundlePublisher(Protocol):
    def publish_aa(
        self,
        *,
        request: AdapterRequest,
        draft_ref: DraftRef,
        legacy_result: Mapping[str, Any],
        producer: Mapping[str, str],
    ) -> BuildBundleRef: ...


_SAFE_DIAGNOSTIC_SEVERITIES = {"info", "warning", "error"}


class AzureArchiveAdapter(AdapterBase):
    """Anti-corruption wrapper around the local-only Legacy093Adapter."""

    adapter_id = "aa-compat"
    engine_id = "azurearchive"
    engine_version = "0.9.3"

    def __init__(
        self,
        legacy: Any | None,
        drafts: PerformanceDraftStore,
        *,
        bundle_publisher: BundlePublisher | None = None,
    ) -> None:
        self.legacy = legacy
        self.drafts = drafts
        self.bundle_publisher = bundle_publisher
        self._cancelled_attempts: set[str] = set()
        self._active_compiles: dict[str, tuple[str, str]] = {}
        self._lock = threading.RLock()

    def capabilities(self) -> dict[str, Any]:
        capabilities = [
            "create_performance_draft",
            "update_performance_draft",
            "cancel",
        ]
        legacy_caps = self._legacy_capabilities()
        if self.legacy is not None:
            capabilities.extend(["preflight", "validate"])
        if self._compile_available(legacy_caps):
            capabilities.append("compile_aap")
        if self._install_available(legacy_caps):
            capabilities.append("install_aap")
        return {
            "schema_version": "1.0",
            "adapter_api_version": "1.0",
            "adapter_id": self.adapter_id,
            "engine_id": self.engine_id,
            "engine_version": self.engine_version,
            "capabilities": capabilities,
            "supported_script_manifest_versions": ["1.1"],
            "supported_performance_draft_versions": ["1.0"],
            "supported_asset_manifest_versions": ["1.0"],
            "supported_build_bundle_versions": ["1.0"],
            "targets": ["pc_aap"],
        }

    def preflight(self, request: AdapterRequest) -> AdapterResult:
        self.require_capability("preflight", target=request.target)
        try:
            text = self._source_text(request)
            inspect = self.legacy.inspect_script(text)
            diagnostics = []
            if isinstance(inspect, dict):
                line_count = int(inspect.get("line_count") or 0)
                if line_count <= 0:
                    diagnostics.append(
                        {
                            "code": "empty_script",
                            "severity": "error",
                            "message": "AA 预检没有读到可制作的正文。",
                        }
                    )
            return AdapterResult(diagnostics=tuple(diagnostics))
        except Exception as exc:
            raise self._normalize_error(exc, "preflight") from exc

    def create_performance_draft(
        self,
        request: AdapterRequest,
        scope: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        self.require_capability("create_performance_draft", target=request.target)
        try:
            draft = self.drafts.create_imported(request, self.adapter_id)
            return AdapterResult(draft_ref=draft)
        except Exception as exc:
            raise self._normalize_error(exc, "create_performance_draft") from exc

    def update_performance_draft(
        self,
        draft_ref: DraftRef,
        patch: Mapping[str, Any],
    ) -> AdapterResult:
        self.require_capability("update_performance_draft")
        try:
            draft = self.drafts.update(draft_ref, patch)
            return AdapterResult(draft_ref=draft)
        except Exception as exc:
            raise self._normalize_error(exc, "update_performance_draft") from exc

    def validate(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
    ) -> AdapterResult:
        self.require_capability("validate", target=request.target)
        draft = self._load_draft(draft_ref)
        try:
            translated = translate_performance_draft(request, draft)
            legacy_result = self.legacy.validate(self._legacy_draft_token(request, draft))
            diagnostics = list(translated.diagnostics)
            diagnostics.extend(self._diagnostics_from_legacy(legacy_result))
            return AdapterResult(diagnostics=tuple(diagnostics))
        except Exception as exc:
            raise self._normalize_error(exc, "validate") from exc

    def compile(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
    ) -> AdapterResult:
        self.require_capability("compile_aap", target=request.target)
        if request.cancelled or (
            request.attempt_id and request.attempt_id in self._cancelled_attempts
        ):
            raise ProductionError(
                "adapter_operation_cancelled",
                "AA 编译已取消",
                status=409,
                details={"adapter_id": self.adapter_id, "operation": "compile_aap"},
            )
        draft = self._load_draft(draft_ref)
        if draft.review_status != "approved":
            raise ProductionError(
                "performance_draft_review_required",
                "PerformanceDraft 必须先通过审查才能编译",
                status=409,
                details={"revision_id": draft.revision_id},
            )
        translated = translate_performance_draft(request, draft)
        token = ""
        build_id = ""
        attempt = request.attempt_id or str(uuid.uuid4())
        try:
            created = self.legacy.create_performance_draft(
                project=translated.project,
                text=translated.text,
                speakers=list(translated.speakers),
            )
            token = self._private_token(created)
            version = self._private_draft_version(created)
            self.legacy.validate(token)
            build_id = str(self.legacy.create_compile_snapshot(token, version))
            with self._lock:
                self._active_compiles[attempt] = (token, build_id)
            if attempt in self._cancelled_attempts:
                raise ProductionError("operation_cancelled", "AA 编译已取消", status=409)
            legacy_result = self.legacy.execute_compile_cancellable(
                token,
                build_id,
                cancellation_probe=lambda: attempt in self._cancelled_attempts,
            )
            if attempt in self._cancelled_attempts:
                raise ProductionError("operation_cancelled", "AA 编译已取消", status=409)
            publisher = self.bundle_publisher
            if publisher is None:
                raise ProductionError(
                    "adapter_capability_unavailable",
                    "AA 编译结果缺少正式 BuildBundle 发布边界",
                    status=409,
                    details={
                        "adapter_id": self.adapter_id,
                        "target": request.target,
                        "operation": "compile_aap",
                    },
                )
            bundle = publisher.publish_aa(
                request=request,
                draft_ref=draft,
                legacy_result=copy.deepcopy(legacy_result),
                producer={
                    "adapter_id": self.adapter_id,
                    "engine_id": self.engine_id,
                    "engine_version": self.engine_version,
                },
            )
            if not isinstance(bundle, BuildBundleRef):
                raise ProductionError(
                    "build_bundle_reference_invalid",
                    "AA 发布器没有返回标准 BuildBundleRef",
                    status=500,
                )
            return AdapterResult(
                bundle_ref=bundle,
                artifact_refs=(bundle.artifact_uri,),
                warnings=tuple(
                    item["message"] for item in translated.diagnostics
                ),
            )
        except Exception as exc:
            raise self._normalize_error(exc, "compile_aap") from exc
        finally:
            with self._lock:
                self._active_compiles.pop(attempt, None)

    def render(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        self.require_capability("render_preview", target=request.target)
        raise AssertionError("AA render_preview is not advertised")

    def cancel(self, attempt_ref: str) -> AdapterResult:
        attempt = str(attempt_ref or "").strip()
        try:
            uuid.UUID(attempt)
        except (ValueError, AttributeError) as exc:
            raise ProductionError(
                "attempt_reference_invalid",
                "取消引用必须是 canonical Attempt UUID",
                status=400,
            ) from exc
        with self._lock:
            self._cancelled_attempts.add(attempt)
            active = self._active_compiles.get(attempt)
        if active and self.legacy is not None:
            try:
                self.legacy.discard_compile_output(*active)
            except Exception as exc:
                raise self._normalize_error(exc, "cancel") from exc
        return AdapterResult(cancelled=True)

    def _load_draft(self, draft_ref: DraftRef) -> DraftRef:
        try:
            persisted = self.drafts.load(draft_ref.artifact_uri)
        except Exception as exc:
            raise self._normalize_error(exc, "validate") from exc
        if (
            persisted.draft_id != draft_ref.draft_id
            or persisted.revision_id != draft_ref.revision_id
            or persisted.content_hash != draft_ref.content_hash
        ):
            raise ProductionError(
                "performance_draft_reference_mismatch",
                "PerformanceDraft 引用的身份或哈希已变化",
                status=409,
                details={"artifact_uri": draft_ref.artifact_uri},
            )
        return persisted

    def _legacy_draft_token(self, request: AdapterRequest, draft: DraftRef) -> str:
        translated = translate_performance_draft(request, draft)
        created = self.legacy.create_performance_draft(
            project=translated.project,
            text=translated.text,
            speakers=list(translated.speakers),
        )
        return self._private_token(created)

    @staticmethod
    def _private_token(value: Any) -> str:
        try:
            token = str(value["session"]["draft_token"])
        except (KeyError, TypeError) as exc:
            raise ProductionError(
                "aa_adapter_result_invalid",
                "AA 转换器没有返回有效的私有草稿句柄",
                status=500,
            ) from exc
        if not token or len(token) > 200:
            raise ProductionError("aa_adapter_result_invalid", "AA 私有草稿句柄无效", status=500)
        return token

    @staticmethod
    def _private_draft_version(value: Any) -> int:
        try:
            return int(value["session"]["draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionError(
                "aa_adapter_result_invalid",
                "AA 转换器没有返回有效的草稿版本",
                status=500,
            ) from exc

    def _source_text(self, request: AdapterRequest) -> str:
        reference = request.production_request["script_release"]
        content = self.drafts.artifacts.read_bytes(reference["content_uri"])
        workspace = self.drafts.artifacts.get(reference["content_uri"])
        if workspace.content_hash != reference["content_hash"]:
            raise ProductionError(
                "script_release_hash_mismatch",
                "ScriptRelease 正文哈希不一致",
                status=409,
            )
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductionError(
                "script_release_content_invalid",
                "ScriptRelease 正文必须是 UTF-8",
                status=422,
            ) from exc

    def _legacy_capabilities(self) -> dict[str, Any]:
        if self.legacy is None:
            return {}
        try:
            value = self.legacy.capabilities()
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def _compile_available(self, capabilities: Mapping[str, Any]) -> bool:
        compile_caps = capabilities.get("compile")
        return bool(
            self.legacy is not None
            and self.bundle_publisher is not None
            and isinstance(compile_caps, Mapping)
            and compile_caps.get("state") == "available"
        )

    @staticmethod
    def _install_available(capabilities: Mapping[str, Any]) -> bool:
        install_caps = capabilities.get("install")
        return isinstance(install_caps, Mapping) and install_caps.get("state") == "available"

    @staticmethod
    def _diagnostics_from_legacy(value: Any) -> list[dict[str, str]]:
        if not isinstance(value, Mapping):
            return []
        blockers = value.get("blockers")
        if not isinstance(blockers, list):
            return []
        result = []
        for item in blockers[:32]:
            if not isinstance(item, Mapping):
                continue
            severity = "error" if item.get("code") == "blocking_diagnostics" else "warning"
            result.append(
                {
                    "code": str(item.get("code") or "aa_validation_blocker"),
                    "severity": severity,
                    "message": "AA 校验发现需要处理的问题。",
                }
            )
        return result

    def _normalize_error(self, exc: Exception, operation: str) -> ProductionError:
        if isinstance(exc, ProductionError):
            if exc.code in {"operation_cancelled", "adapter_operation_cancelled"}:
                return ProductionError(
                    "adapter_operation_cancelled",
                    "AA 操作已取消",
                    status=409,
                    details={"adapter_id": self.adapter_id, "operation": operation},
                )
            if exc.code in {"compile_not_configured", "legacy_adapter_unavailable"}:
                return ProductionError(
                    "adapter_capability_unavailable",
                    "AA 环境未提供请求的能力",
                    status=409,
                    details={
                        "adapter_id": self.adapter_id,
                        "target": "pc_aap",
                        "operation": operation,
                    },
                )
            if exc.code in {
                "performance_draft_reference_mismatch",
                "performance_draft_review_required",
                "performance_draft_not_found",
            }:
                return exc
            return ProductionError(
                f"aa_{operation}_failed",
                "AA 适配器操作失败",
                status=exc.status,
                details={"adapter_id": self.adapter_id, "operation": operation},
            )
        return ProductionError(
            f"aa_{operation}_failed",
            "AA 适配器操作失败",
            status=500,
            details={"adapter_id": self.adapter_id, "operation": operation},
        )


__all__ = ["AzureArchiveAdapter", "BundlePublisher"]
