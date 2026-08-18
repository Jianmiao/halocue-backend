from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

from ...artifacts import ArtifactRef, ArtifactStore
from ...contracts import canonical_json_bytes, sha256_bytes
from ...errors import ProductionError
from ..base import AdapterRequest, DraftRef


class StoryForgeRenderer:
    """Deterministic local preview renderer; it never pretends to create video."""

    renderer_id = "storyforge-local"
    renderer_version = "0.1.0"

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        self.last_request_type: type | None = None
        self.last_draft_type: type | None = None

    def render_preview(
        self,
        request: AdapterRequest,
        draft: DraftRef,
        options: Mapping[str, Any] | None = None,
        *,
        cancelled,
    ) -> ArtifactRef:
        self.last_request_type = type(request)
        self.last_draft_type = type(draft)
        if cancelled():
            raise ProductionError("operation_cancelled", "StoryForge 预览已取消", status=409)
        safe_options = self._options(options)
        payload = {
            "schema_version": "1.0",
            "kind": "storyforge_preview_manifest",
            "renderer_id": self.renderer_id,
            "renderer_version": self.renderer_version,
            "request_id": request.request_id,
            "draft_id": draft.draft_id,
            "revision_id": draft.revision_id,
            "draft_hash": draft.content_hash,
            "target": request.target,
            "options": safe_options,
            "scenes": self._scene_summary(draft),
        }
        content = canonical_json_bytes(payload)
        content_hash = sha256_bytes(content)
        artifact_uri = f"artifact://storyforge-previews/{draft.revision_id}"
        existing = self.artifacts.runtime.get_artifact_ref(artifact_uri)
        if existing is not None:
            return self.artifacts.get_artifact(artifact_uri)
        workspace = self.artifacts.commit_bytes(
            f"workspace://storyforge-previews/{draft.draft_id}/{draft.revision_id}.json",
            content,
            kind="preview",
            media_type="application/json",
            metadata={
                "contract": "StoryForgePreviewManifest/1.0",
                "draft_id": draft.draft_id,
                "revision_id": draft.revision_id,
            },
        )
        if cancelled():
            raise ProductionError("operation_cancelled", "StoryForge 预览已取消", status=409)
        artifact = self.artifacts.publish_artifact(
            "storyforge-previews",
            draft.revision_id,
            workspace,
            provenance={
                "run_id": request.run_id,
                "work_item_id": request.work_item_id,
                "attempt_id": request.attempt_id,
            },
        )
        if artifact.content_hash != content_hash:
            raise ProductionError(
                "preview_artifact_hash_mismatch",
                "StoryForge 预览 manifest 哈希校验失败",
                status=500,
            )
        return artifact

    @staticmethod
    def _scene_summary(draft: DraftRef) -> list[dict[str, Any]]:
        payload = draft.payload or {}
        scenes: list[dict[str, Any]] = []
        for scene in payload.get("scenes", []) if isinstance(payload, dict) else []:
            if not isinstance(scene, dict):
                continue
            nodes: list[dict[str, Any]] = []
            for node in scene.get("nodes", []) if isinstance(scene.get("nodes"), list) else []:
                if not isinstance(node, dict):
                    continue
                item = {"node_id": node.get("node_id"), "kind": node.get("kind")}
                line = node.get("performance_line")
                if isinstance(line, dict):
                    item["line_id"] = line.get("line_id")
                    item["content_kind"] = line.get("content_kind")
                    item["duration_ms"] = line.get("duration_ms")
                    item["cast_ids"] = sorted(
                        str(cast.get("character_id"))
                        for cast in line.get("cast_state", [])
                        if isinstance(cast, dict) and cast.get("character_id")
                    )
                nodes.append(item)
            scenes.append({"scene_id": scene.get("scene_id"), "nodes": nodes})
        return copy.deepcopy(scenes)

    @staticmethod
    def _options(options: Mapping[str, Any] | None) -> dict[str, Any]:
        raw = dict(options or {})
        allowed = {"target", "width", "height", "format", "quality"}
        if set(raw) - allowed:
            raise ProductionError(
                "storyforge_render_options_invalid",
                "StoryForge 渲染选项包含不支持的字段",
                status=400,
            )
        result: dict[str, Any] = {}
        for key in allowed:
            if key not in raw:
                continue
            value = raw[key]
            if key in {"width", "height"}:
                if not isinstance(value, int) or not 64 <= value <= 16384:
                    raise ProductionError(
                        "storyforge_render_options_invalid",
                        "StoryForge 输出尺寸无效",
                        status=400,
                    )
                result[key] = value
            elif key == "quality":
                if not isinstance(value, int) or not 1 <= value <= 100:
                    raise ProductionError(
                        "storyforge_render_options_invalid",
                        "StoryForge 输出质量无效",
                        status=400,
                    )
                result[key] = value
            else:
                text = str(value or "").strip()
                if len(text) > 80 or ":" in text or "\\" in text or "/" in text:
                    raise ProductionError(
                        "storyforge_render_options_invalid",
                        "StoryForge 渲染选项包含不安全文本",
                        status=400,
                    )
                result[key] = text
        return result


__all__ = ["StoryForgeRenderer"]
