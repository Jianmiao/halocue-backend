from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any, Protocol

from ...artifacts import ArtifactRef
from ...contracts import ContractValidationError, validate_contract
from ...errors import ProductionError
from ..base import AdapterBase, AdapterRequest, AdapterResult, DraftRef
from ..drafts import PerformanceDraftStore
from .renderer import StoryForgeRenderer


class VideoExporter(Protocol):
    def export_video(
        self,
        *,
        request: AdapterRequest,
        draft: DraftRef,
        preview: ArtifactRef,
        options: Mapping[str, Any],
        cancelled,
    ) -> ArtifactRef: ...


class StoryForgeAdapter(AdapterBase):
    adapter_id = "storyforge-local"
    engine_id = "storyforge"
    engine_version = "0.1.0"

    def __init__(
        self,
        renderer: StoryForgeRenderer,
        drafts: PerformanceDraftStore,
        *,
        video_exporter: VideoExporter | None = None,
    ) -> None:
        self.renderer = renderer
        self.drafts = drafts
        self.video_exporter = video_exporter
        self._cancelled_attempts: set[str] = set()

    def capabilities(self) -> dict[str, Any]:
        capabilities = [
            "preflight",
            "create_performance_draft",
            "update_performance_draft",
            "validate",
            "render_preview",
            "cancel",
        ]
        targets = ["storyforge_preview"]
        if self.video_exporter is not None:
            capabilities.append("export_video")
            targets.append("storyforge_video")
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
            "targets": targets,
        }

    def preflight(self, request: AdapterRequest) -> AdapterResult:
        self.require_capability("preflight", target=request.target)
        self._validated(request, None)
        return AdapterResult()

    def create_performance_draft(
        self,
        request: AdapterRequest,
        scope: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        self.require_capability("create_performance_draft", target=request.target)
        try:
            return AdapterResult(
                draft_ref=self.drafts.create_imported(request, self.adapter_id)
            )
        except Exception as exc:
            raise self._normalize_error(exc, "create_performance_draft") from exc

    def update_performance_draft(
        self,
        draft_ref: DraftRef,
        patch: Mapping[str, Any],
    ) -> AdapterResult:
        self.require_capability("update_performance_draft")
        try:
            return AdapterResult(draft_ref=self.drafts.update(draft_ref, patch))
        except Exception as exc:
            raise self._normalize_error(exc, "update_performance_draft") from exc

    def validate(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
    ) -> AdapterResult:
        self.require_capability("validate", target=request.target)
        self._validated(request, draft_ref)
        return AdapterResult()

    def compile(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
    ) -> AdapterResult:
        self.require_capability("compile_aap", target=request.target)
        raise AssertionError("StoryForge compile is not advertised")

    def render(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        target = str((options or {}).get("target") or request.target).strip().casefold()
        if target == "storyforge_video":
            return self.export_video(request, draft_ref, options)
        self.require_capability("render_preview", target=target)
        draft = self._validated(request, draft_ref)
        self._check_cancelled(request)
        try:
            artifact = self.renderer.render_preview(
                request,
                draft,
                options,
                cancelled=lambda: self._is_cancelled(request),
            )
            self._check_cancelled(request)
            return AdapterResult(artifact_refs=(artifact.uri,))
        except Exception as exc:
            raise self._normalize_error(exc, "render_preview") from exc

    def export_video(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterResult:
        self.require_capability("export_video", target="storyforge_video")
        draft = self._validated(request, draft_ref)
        self._check_cancelled(request)
        try:
            preview = self.renderer.render_preview(
                request,
                draft,
                {**dict(options or {}), "target": "storyforge_preview"},
                cancelled=lambda: self._is_cancelled(request),
            )
            self._check_cancelled(request)
            exporter = self.video_exporter
            if exporter is None:
                raise ProductionError(
                    "adapter_capability_unavailable",
                    "StoryForge 未配置视频导出器",
                    status=409,
                    details={
                        "adapter_id": self.adapter_id,
                        "target": "storyforge_video",
                        "operation": "export_video",
                    },
                )
            artifact = exporter.export_video(
                request=request,
                draft=draft,
                preview=preview,
                options=copy.deepcopy(dict(options or {})),
                cancelled=lambda: self._is_cancelled(request),
            )
            self._check_cancelled(request)
            if not isinstance(artifact, ArtifactRef) or artifact.kind != "video":
                raise ProductionError(
                    "video_artifact_invalid",
                    "StoryForge exporter 没有返回已登记的视频 Artifact",
                    status=500,
                )
            verified = self.renderer.artifacts.get_artifact(artifact.uri)
            if verified.kind != "video" or verified.content_hash != artifact.content_hash:
                raise ProductionError(
                    "video_artifact_invalid",
                    "StoryForge 视频 Artifact 哈希校验失败",
                    status=500,
                )
            return AdapterResult(artifact_refs=(artifact.uri,))
        except Exception as exc:
            raise self._normalize_error(exc, "export_video") from exc

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
        self._cancelled_attempts.add(attempt)
        return AdapterResult(cancelled=True)

    def _validated(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef | None,
    ) -> DraftRef | None:
        draft = None
        if draft_ref is not None:
            try:
                draft = self.drafts.load(draft_ref.artifact_uri)
            except Exception as exc:
                raise self._normalize_error(exc, "validate") from exc
            if (
                draft.draft_id != draft_ref.draft_id
                or draft.revision_id != draft_ref.revision_id
                or draft.content_hash != draft_ref.content_hash
            ):
                raise ProductionError(
                    "performance_draft_reference_mismatch",
                    "PerformanceDraft 引用的身份或哈希已变化",
                    status=409,
                )
        manifest = request.asset_manifest
        if manifest is None:
            raise ProductionError(
                "asset_manifest_unavailable",
                "StoryForge 操作需要冻结的 AssetManifest",
                status=409,
            )
        try:
            normalized_manifest = validate_contract("AssetManifest", manifest)
        except ContractValidationError as exc:
            raise ProductionError(
                "asset_manifest_invalid",
                "AssetManifest 不符合正式合同",
                status=422,
                details={"path": exc.path},
            ) from exc
        reference = request.production_request["asset_manifest"]
        if (
            normalized_manifest["id"] != reference["id"]
            or normalized_manifest["content_hash"] != reference["content_hash"]
        ):
            raise ProductionError(
                "asset_manifest_reference_mismatch",
                "AssetManifest 引用与冻结清单不一致",
                status=409,
            )
        if draft is not None:
            self._check_asset_whitelist(draft, normalized_manifest)
        return draft

    @staticmethod
    def _check_asset_whitelist(draft: DraftRef, manifest: Mapping[str, Any]) -> None:
        allowed = {
            str(item["asset_id"]): item
            for item in manifest.get("assets", [])
            if isinstance(item, Mapping)
        }
        references: list[Mapping[str, Any]] = []
        payload = draft.payload or {}
        for scene in payload.get("scenes", []) if isinstance(payload, Mapping) else []:
            for node in scene.get("nodes", []) if isinstance(scene, Mapping) else []:
                line = node.get("performance_line") if isinstance(node, Mapping) else None
                if not isinstance(line, Mapping):
                    continue
                for cast in line.get("cast_state", []) if isinstance(line.get("cast_state"), list) else []:
                    if isinstance(cast, Mapping) and cast.get("asset_id"):
                        references.append(
                            {"asset_id": cast["asset_id"], "content_hash": None, "uri": None}
                        )
                media = line.get("media") if isinstance(line.get("media"), Mapping) else {}
                for value in media.values():
                    if isinstance(value, Mapping) and value.get("asset_id"):
                        references.append(value)
                    elif isinstance(value, list):
                        references.extend(item for item in value if isinstance(item, Mapping))
        for reference in references:
            asset = allowed.get(str(reference["asset_id"]))
            if asset is None:
                raise ProductionError(
                    "asset_not_allowlisted",
                    "PerformanceDraft 引用了不在冻结白名单中的素材",
                    status=409,
                    details={"asset_id": str(reference["asset_id"])},
                )
            for key in ("uri", "content_hash"):
                if reference.get(key) is not None and reference[key] != asset[key]:
                    raise ProductionError(
                        "asset_manifest_reference_mismatch",
                        "PerformanceDraft 素材引用与冻结清单不一致",
                        status=409,
                        details={"asset_id": str(reference["asset_id"])},
                    )

    def _check_cancelled(self, request: AdapterRequest) -> None:
        if self._is_cancelled(request):
            raise ProductionError(
                "adapter_operation_cancelled",
                "StoryForge 操作已取消",
                status=409,
                details={"adapter_id": self.adapter_id},
            )

    def _is_cancelled(self, request: AdapterRequest) -> bool:
        return bool(
            request.is_cancelled()
            or (
                request.attempt_id
                and request.attempt_id in self._cancelled_attempts
            )
        )

    @staticmethod
    def _normalize_error(exc: Exception, operation: str) -> ProductionError:
        if isinstance(exc, ProductionError):
            if exc.code in {"operation_cancelled", "adapter_operation_cancelled"}:
                return ProductionError(
                    "adapter_operation_cancelled",
                    "StoryForge 操作已取消",
                    status=409,
                    details={"adapter_id": "storyforge-local", "operation": operation},
                )
            if exc.code in {
                "performance_draft_reference_mismatch",
                "performance_draft_not_found",
                "asset_manifest_unavailable",
                "asset_manifest_invalid",
                "asset_manifest_reference_mismatch",
                "asset_not_allowlisted",
                "adapter_capability_unavailable",
                "storyforge_render_options_invalid",
                "video_artifact_invalid",
            }:
                return exc
            return ProductionError(
                f"storyforge_{operation}_failed",
                "StoryForge 适配器操作失败",
                status=exc.status,
                details={"adapter_id": "storyforge-local", "operation": operation},
            )
        return ProductionError(
            f"storyforge_{operation}_failed",
            "StoryForge 适配器操作失败",
            status=500,
            details={"adapter_id": "storyforge-local", "operation": operation},
        )


__all__ = ["StoryForgeAdapter", "VideoExporter"]
