from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from ..artifacts import ArtifactStore
from ..contracts import (
    ContractValidationError,
    canonical_json_bytes,
    contract_content_hash,
    validate_contract,
)
from ..errors import ProductionError
from ..runtime import RuntimeStore
from .base import AdapterRequest, DraftRef


_SPEAKER_LINE = re.compile(r"^\s*([^:：]{1,80})\s*[:：]\s*(.*)$")
_NARRATORS = frozenset({"旁白", "narrator", "narration"})
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{1,79}")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _canonical_uuid(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError) as exc:
        raise ProductionError(
            "release_scene_index_corrupt",
            "ReleaseSceneIndex 包含无效身份",
            status=500,
            details={"field": field_name},
        ) from exc
    if str(parsed) != text:
        raise ProductionError(
            "release_scene_index_corrupt",
            "ReleaseSceneIndex 包含非 canonical UUID",
            status=500,
            details={"field": field_name},
        )
    return text


class PerformanceDraftStore:
    """Freeze and revise standard PerformanceDraft/1.0 artifacts."""

    def __init__(self, artifacts: ArtifactStore, runtime: RuntimeStore) -> None:
        if artifacts.runtime is not runtime:
            raise ValueError("ArtifactStore and PerformanceDraftStore must share RuntimeStore")
        self.artifacts = artifacts
        self.runtime = runtime

    def create_imported(
        self, request: AdapterRequest, adapter_id: str
    ) -> DraftRef:
        if request.is_cancelled():
            raise ProductionError(
                "adapter_request_cancelled",
                "已取消的适配器请求不能创建 PerformanceDraft",
                status=409,
            )
        normalized_adapter_id = self._adapter_id(adapter_id)
        source_bytes = self._source_bytes(request)
        parsed_lines = self._parse_lines(source_bytes)
        index = self._release_scene_index(request, parsed_lines)
        artifact_uri = f'artifact://performance-drafts/{index["initial_revision_id"]}'
        existing = self.runtime.get_formal_performance_draft_revision(
            index["initial_revision_id"]
        )
        if existing is not None:
            if existing["artifact_uri"] != artifact_uri:
                raise ProductionError(
                    "performance_draft_revision_identity_conflict",
                    "PerformanceDraft 初始 Revision 已绑定不同 artifact",
                    status=409,
                    details={"revision_id": index["initial_revision_id"]},
                )
            return self.load(artifact_uri)

        payload = self._imported_payload(
            request,
            normalized_adapter_id,
            parsed_lines,
            index,
        )
        workspace_uri = (
            f'workspace://performance-drafts/{payload["id"]}/'
            f'{payload["revision_id"]}.json'
        )
        workspace_file = self.artifacts.commit_bytes(
            workspace_uri,
            canonical_json_bytes(payload),
            kind="performance-draft",
            media_type="application/json",
            metadata={
                "contract": "PerformanceDraft/1.0",
                "draft_id": payload["id"],
                "revision_id": payload["revision_id"],
            },
        )
        artifact = self.artifacts.publish_artifact(
            "performance-drafts",
            payload["revision_id"],
            workspace_file,
            provenance={
                "run_id": request.run_id,
                "work_item_id": request.work_item_id,
                "attempt_id": request.attempt_id,
            },
        )
        self.runtime.register_formal_performance_draft(
            revision_id=payload["revision_id"],
            draft_id=payload["id"],
            run_id=request.run_id,
            request_id=request.request_id,
            artifact_uri=artifact.uri,
            content_hash=payload["content_hash"],
            review_status=payload["review_status"],
            parent_revision_id=None,
            adapter_id=normalized_adapter_id,
            created_at=payload["created_at"],
        )
        return self.load(artifact.uri)

    def update(
        self,
        draft_ref: DraftRef,
        patch: Mapping[str, Any],
        *,
        expected_revision_id: str | None = None,
        run_id: str | None = None,
        work_item_id: str | None = None,
        attempt_id: str | None = None,
        cancelled: bool = False,
    ) -> DraftRef:
        if cancelled:
            raise ProductionError(
                "adapter_request_cancelled",
                "已取消的适配器请求不能更新 PerformanceDraft",
                status=409,
            )
        if not isinstance(patch, Mapping) or not patch:
            raise ProductionError(
                "performance_draft_patch_invalid",
                "PerformanceDraft patch 必须是非空对象",
                status=400,
            )
        allowed = {"review_status", "scenes", "provenance"}
        unknown = sorted(set(patch) - allowed)
        if unknown:
            raise ProductionError(
                "performance_draft_patch_invalid",
                "PerformanceDraft patch 不能修改身份或来源",
                status=400,
                details={"fields": unknown},
            )
        persisted = self.load(draft_ref.artifact_uri)
        expected = str(expected_revision_id or draft_ref.revision_id)
        head = self.runtime.get_formal_performance_draft_head(persisted.draft_id)
        if (
            persisted.revision_id != expected
            or head is None
            or head["revision_id"] != expected
        ):
            raise ProductionError(
                "performance_draft_revision_conflict",
                "PerformanceDraft 已被其他操作更新",
                status=409,
                details={
                    "draft_id": persisted.draft_id,
                    "expected_revision_id": expected,
                    "current_revision_id": head["revision_id"] if head else None,
                },
            )
        payload = copy.deepcopy(persisted.payload)
        for key, value in patch.items():
            payload[key] = copy.deepcopy(value)
        payload["revision_id"] = _new_uuid()
        payload["created_at"] = _now()
        payload["content_hash"] = contract_content_hash("PerformanceDraft", payload)
        try:
            payload = validate_contract("PerformanceDraft", payload)
        except ContractValidationError as exc:
            raise ProductionError(
                "performance_draft_patch_invalid",
                "PerformanceDraft patch 不能形成有效的正式草稿",
                status=422,
                details={"path": exc.path, "reason": str(exc)},
            ) from exc

        workspace_file = self.artifacts.commit_bytes(
            (
                f'workspace://performance-drafts/{payload["id"]}/'
                f'{payload["revision_id"]}.json'
            ),
            canonical_json_bytes(payload),
            kind="performance-draft",
            media_type="application/json",
            metadata={
                "contract": "PerformanceDraft/1.0",
                "draft_id": payload["id"],
                "revision_id": payload["revision_id"],
                "parent_revision_id": expected,
            },
        )
        artifact = self.artifacts.publish_artifact(
            "performance-drafts",
            payload["revision_id"],
            workspace_file,
            provenance={
                "run_id": run_id or head["run_id"],
                "work_item_id": work_item_id,
                "attempt_id": attempt_id,
            },
        )
        self.runtime.register_formal_performance_draft(
            revision_id=payload["revision_id"],
            draft_id=payload["id"],
            run_id=run_id or head["run_id"],
            request_id=head["request_id"],
            artifact_uri=artifact.uri,
            content_hash=payload["content_hash"],
            review_status=payload["review_status"],
            parent_revision_id=expected,
            adapter_id=head["adapter_id"],
            created_at=payload["created_at"],
        )
        return self.load(artifact.uri)

    def load(self, artifact_uri: str) -> DraftRef:
        record = self.runtime.get_formal_performance_draft_by_artifact(artifact_uri)
        if record is None:
            raise ProductionError(
                "performance_draft_not_found",
                "PerformanceDraft Revision 不存在",
                status=404,
                details={"artifact_uri": artifact_uri},
            )
        content = self.artifacts.read_artifact_bytes(artifact_uri)
        try:
            raw = json.loads(content.decode("utf-8"))
            payload = validate_contract("PerformanceDraft", raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            details = {"artifact_uri": artifact_uri}
            if isinstance(exc, ContractValidationError):
                details.update({"path": exc.path, "reason": str(exc)})
            raise ProductionError(
                "performance_draft_corrupt",
                "PerformanceDraft artifact 无法读取或合同无效",
                status=500,
                details=details,
            ) from exc
        expected = {
            "id": record["draft_id"],
            "revision_id": record["revision_id"],
            "content_hash": record["content_hash"],
            "review_status": record["review_status"],
        }
        if any(payload[key] != value for key, value in expected.items()):
            raise ProductionError(
                "performance_draft_binding_corrupt",
                "PerformanceDraft artifact 与 SQLite 修订账本不一致",
                status=500,
                details={"artifact_uri": artifact_uri},
            )
        return DraftRef(
            draft_id=payload["id"],
            revision_id=payload["revision_id"],
            artifact_uri=artifact_uri,
            content_hash=payload["content_hash"],
            review_status=payload["review_status"],
            adapter_id=record["adapter_id"],
            payload=payload,
        )

    def current(self, draft_id: str) -> DraftRef:
        record = self.runtime.get_formal_performance_draft_head(draft_id)
        if record is None:
            raise ProductionError(
                "performance_draft_not_found",
                "PerformanceDraft 不存在",
                status=404,
                details={"draft_id": draft_id},
            )
        return self.load(record["artifact_uri"])

    def _source_bytes(self, request: AdapterRequest) -> bytes:
        reference = request.production_request["script_release"]
        content_uri = reference.get("content_uri")
        if not content_uri:
            raise ProductionError(
                "script_release_content_unavailable",
                "ProductionRequest 未提供冻结正文 URI",
                status=409,
                details={"request_id": request.request_id},
            )
        workspace_file = self.artifacts.get(content_uri)
        if workspace_file.content_hash != reference["content_hash"]:
            raise ProductionError(
                "script_release_hash_mismatch",
                "冻结正文哈希与 ProductionRequest 不一致",
                status=409,
                details={"release_id": reference["id"]},
            )
        return self.artifacts.read_bytes(content_uri)

    @staticmethod
    def _parse_lines(source_bytes: bytes) -> list[dict[str, Any]]:
        try:
            text = source_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductionError(
                "script_release_content_invalid",
                "ScriptRelease 正文必须是 UTF-8 文本",
                status=422,
            ) from exc
        parsed: list[dict[str, Any]] = []
        for raw in text.splitlines():
            stripped = raw.strip()
            if not stripped:
                continue
            match = _SPEAKER_LINE.match(stripped)
            if match:
                label = match.group(1).strip()
                content = match.group(2)
                if label.casefold() in _NARRATORS:
                    parsed.append({"kind": "narration", "text": content, "speaker": None})
                else:
                    parsed.append({"kind": "dialogue", "text": content, "speaker": label})
            elif stripped.startswith("@"):
                parsed.append({"kind": "stage_direction", "text": stripped, "speaker": None})
            else:
                parsed.append({"kind": "narration", "text": stripped, "speaker": None})
        if not parsed:
            raise ProductionError(
                "script_release_content_invalid",
                "ScriptRelease 正文没有可制作的演出行",
                status=422,
            )
        return parsed

    def _release_scene_index(
        self, request: AdapterRequest, parsed_lines: list[dict[str, Any]]
    ) -> dict[str, Any]:
        reference = request.production_request["script_release"]
        digest = str(reference["content_hash"]).split(":", 1)[-1]
        uri = f'workspace://release-scene-index/{reference["id"]}/{digest}.json'
        existing = self.runtime.get_workspace_file(uri)
        if existing is not None:
            return self._read_scene_index(uri, reference, parsed_lines)

        speakers: list[str] = []
        for line in parsed_lines:
            speaker = line["speaker"]
            if speaker is not None and speaker not in speakers:
                speakers.append(speaker)
        index = {
            "schema_version": "1.0",
            "release_id": reference["id"],
            "release_hash": reference["content_hash"],
            "draft_id": _new_uuid(),
            "initial_revision_id": _new_uuid(),
            "scene_id": _new_uuid(),
            "scene_revision_id": _new_uuid(),
            "speakers": [
                {"source_label": speaker, "character_id": _new_uuid()}
                for speaker in speakers
            ],
            "lines": [
                {
                    "source_hash": "sha256:"
                    + hashlib.sha256(
                        canonical_json_bytes(line)
                    ).hexdigest(),
                    "node_id": _new_uuid(),
                    "line_id": _new_uuid(),
                }
                for line in parsed_lines
            ],
            "created_at": _now(),
        }
        try:
            self.artifacts.commit_bytes(
                uri,
                canonical_json_bytes(index),
                kind="release-scene-index",
                media_type="application/json",
                metadata={
                    "release_id": reference["id"],
                    "release_hash": reference["content_hash"],
                },
            )
        except ProductionError as exc:
            if exc.code not in {"workspace_file_conflict", "workspace_file_duplicate"}:
                raise
            return self._read_scene_index(uri, reference, parsed_lines)
        return self._validate_scene_index(index, reference, parsed_lines)

    def _read_scene_index(
        self,
        uri: str,
        reference: Mapping[str, Any],
        parsed_lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        try:
            raw = json.loads(self.artifacts.read_bytes(uri).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ProductionError) as exc:
            if isinstance(exc, ProductionError) and exc.code == "artifact_hash_mismatch":
                raise
            raise ProductionError(
                "release_scene_index_corrupt",
                "ReleaseSceneIndex 无法读取",
                status=500,
                details={"uri": uri},
            ) from exc
        return self._validate_scene_index(raw, reference, parsed_lines)

    @staticmethod
    def _validate_scene_index(
        raw: Any,
        reference: Mapping[str, Any],
        parsed_lines: list[dict[str, Any]],
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "release_id",
            "release_hash",
            "draft_id",
            "initial_revision_id",
            "scene_id",
            "scene_revision_id",
            "speakers",
            "lines",
            "created_at",
        }
        if not isinstance(raw, dict) or set(raw) != required:
            raise ProductionError(
                "release_scene_index_corrupt",
                "ReleaseSceneIndex 结构无效",
                status=500,
            )
        if (
            raw["schema_version"] != "1.0"
            or raw["release_id"] != reference["id"]
            or raw["release_hash"] != reference["content_hash"]
            or not isinstance(raw["speakers"], list)
            or not isinstance(raw["lines"], list)
            or len(raw["lines"]) != len(parsed_lines)
        ):
            raise ProductionError(
                "release_scene_index_conflict",
                "ReleaseSceneIndex 与冻结正文不一致",
                status=409,
                details={"release_id": reference["id"]},
            )
        for key in ("draft_id", "initial_revision_id", "scene_id", "scene_revision_id"):
            _canonical_uuid(raw[key], key)
        labels: set[str] = set()
        for index, speaker in enumerate(raw["speakers"]):
            if (
                not isinstance(speaker, dict)
                or set(speaker) != {"source_label", "character_id"}
                or not isinstance(speaker["source_label"], str)
                or speaker["source_label"] in labels
            ):
                raise ProductionError(
                    "release_scene_index_corrupt",
                    "ReleaseSceneIndex speaker binding 无效",
                    status=500,
                )
            labels.add(speaker["source_label"])
            _canonical_uuid(speaker["character_id"], f"speakers[{index}].character_id")
        expected_labels = {line["speaker"] for line in parsed_lines if line["speaker"]}
        if labels != expected_labels:
            raise ProductionError(
                "release_scene_index_conflict",
                "ReleaseSceneIndex speaker binding 与冻结正文不一致",
                status=409,
            )
        for index, (binding, line) in enumerate(zip(raw["lines"], parsed_lines)):
            expected_hash = "sha256:" + hashlib.sha256(canonical_json_bytes(line)).hexdigest()
            if (
                not isinstance(binding, dict)
                or set(binding) != {"source_hash", "node_id", "line_id"}
                or binding["source_hash"] != expected_hash
            ):
                raise ProductionError(
                    "release_scene_index_conflict",
                    "ReleaseSceneIndex 演出行绑定与冻结正文不一致",
                    status=409,
                )
            _canonical_uuid(binding["node_id"], f"lines[{index}].node_id")
            _canonical_uuid(binding["line_id"], f"lines[{index}].line_id")
        return copy.deepcopy(raw)

    @staticmethod
    def _imported_payload(
        request: AdapterRequest,
        adapter_id: str,
        parsed_lines: list[dict[str, Any]],
        index: dict[str, Any],
    ) -> dict[str, Any]:
        speaker_ids = {
            item["source_label"]: item["character_id"] for item in index["speakers"]
        }
        nodes: list[dict[str, Any]] = []
        for line, binding in zip(parsed_lines, index["lines"]):
            speaker_id = speaker_ids.get(line["speaker"])
            performance_line = {
                "line_id": binding["line_id"],
                "content_kind": line["kind"],
                "text": line["text"],
                "location": "未指定",
                "cast_state": [
                    {
                        "character_id": character_id,
                        "asset_id": None,
                        "face": None,
                        "start_position": None,
                        "end_position": None,
                        "speaking_status": (
                            "speaker" if character_id == speaker_id else "present"
                        ),
                        "presence_action": "none",
                        "action": None,
                        "effect": None,
                        "form_override": None,
                    }
                    for character_id in speaker_ids.values()
                ],
                "media": {
                    "background": None,
                    "popup": None,
                    "bgm": None,
                    "voice": None,
                    "sound_effects": [],
                    "background_effect": None,
                    "transition": None,
                },
                "extra_instructions": [],
                "duration_ms": 0,
            }
            if speaker_id is not None:
                performance_line["speaker_id"] = speaker_id
                performance_line["highlighted_character_id"] = speaker_id
            nodes.append(
                {
                    "node_id": binding["node_id"],
                    "kind": "performance_line",
                    "performance_line": performance_line,
                }
            )
        release = request.production_request["script_release"]
        provenance: dict[str, Any] = {
            "created_by": "importer",
            "input_hash": release["content_hash"],
            "adapter_id": adapter_id,
        }
        if request.attempt_id is not None:
            provenance["attempt_id"] = request.attempt_id
        payload = {
            "schema_version": "1.0",
            "id": index["draft_id"],
            "revision_id": index["initial_revision_id"],
            "source": {
                "release_id": release["id"],
                "release_hash": release["content_hash"],
                "scene_revisions": [
                    {
                        "scene_id": index["scene_id"],
                        "revision_id": index["scene_revision_id"],
                        "content_hash": release["content_hash"],
                    }
                ],
            },
            "provenance": provenance,
            "review_status": "draft",
            "scenes": [{"scene_id": index["scene_id"], "nodes": nodes}],
            "created_at": index["created_at"],
        }
        payload["content_hash"] = contract_content_hash("PerformanceDraft", payload)
        try:
            return validate_contract("PerformanceDraft", payload)
        except ContractValidationError as exc:
            raise ProductionError(
                "performance_draft_generation_invalid",
                "导入结果不能形成有效的 PerformanceDraft",
                status=500,
                details={"path": exc.path, "reason": str(exc)},
            ) from exc

    @staticmethod
    def _adapter_id(value: Any) -> str:
        text = str(value or "").strip().casefold()
        if not _IDENTIFIER.fullmatch(text):
            raise ProductionError(
                "adapter_id_invalid",
                "adapter_id 必须是稳定标识",
                status=400,
            )
        return text


StandardDraftStore = PerformanceDraftStore
FormalPerformanceDraftStore = PerformanceDraftStore


__all__ = [
    "FormalPerformanceDraftStore",
    "PerformanceDraftStore",
    "StandardDraftStore",
]
