from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..artifacts import ArtifactRef, ArtifactStore, WorkspaceFile
from ..contracts import (
    ContractValidationError,
    canonical_json_bytes,
    sha256_bytes,
    validate_contract,
)
from ..errors import ProductionError
from .base import AdapterRequest, BuildBundleRef, DraftRef


_BUNDLE_NAMESPACE = uuid.UUID("9924c499-1661-4811-aae3-2f2b6bb430f7")
_DELIVERABLE_NAMESPACE = uuid.UUID("37fc81da-b09b-4a2d-a2dd-35de7a3dfeb1")
_DELIVERABLE_KINDS = frozenset({"aap", "video", "preview", "manifest", "log"})


@dataclass(frozen=True)
class DeliverableInput:
    kind: str
    media_type: str
    source_path: Path
    workspace_uri: str
    artifact_id: str | None = None


@dataclass(frozen=True)
class _RegisteredDeliverable:
    artifact_id: str
    artifact: ArtifactRef
    workspace: WorkspaceFile

    def contract_payload(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "kind": self.artifact.kind,
            "uri": self.artifact.uri,
            "content_hash": self.artifact.content_hash,
            "media_type": self.workspace.media_type,
            "size_bytes": self.workspace.size_bytes,
        }


class BuildBundleAssembler:
    """Publish verified deliverables before the immutable BuildBundle manifest."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        *,
        staging_root: Path | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.staging_root = (
            Path(staging_root).expanduser().resolve() if staging_root is not None else None
        )

    def assemble(
        self,
        *,
        request_id: str,
        performance_draft_id: str,
        input_hashes: Mapping[str, str],
        producer: Mapping[str, str],
        target: str,
        deliverables: Sequence[DeliverableInput | ArtifactRef | Any],
        created_at: str,
        warnings: Sequence[Mapping[str, Any]] = (),
        run_id: str | None = None,
        work_item_id: str | None = None,
        attempt_id: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> BuildBundleRef:
        cancellation_probe = cancelled or (lambda: False)
        self._raise_if_cancelled(cancellation_probe)
        if not deliverables:
            raise ProductionError(
                "build_bundle_deliverables_required",
                "BuildBundle 至少需要一个已登记交付物",
                status=400,
            )
        registered: list[_RegisteredDeliverable] = []
        for raw in deliverables:
            self._raise_if_cancelled(cancellation_probe)
            registered.append(
                self._register_deliverable(
                    raw,
                    run_id=run_id,
                    work_item_id=work_item_id,
                    attempt_id=attempt_id,
                )
            )
        deliverable_payloads = [item.contract_payload() for item in registered]
        identity = {
            "request_id": str(request_id),
            "performance_draft_id": str(performance_draft_id),
            "input_hashes": dict(input_hashes),
            "producer": dict(producer),
            "target": str(target),
            "deliverables": deliverable_payloads,
            "warnings": [dict(item) for item in warnings],
            "created_at": str(created_at),
        }
        identity_hash = sha256_bytes(canonical_json_bytes(identity))
        bundle_id = str(uuid.uuid5(_BUNDLE_NAMESPACE, identity_hash))
        artifact_uri = f"artifact://build-bundles/{bundle_id}"
        existing = self.artifacts.runtime.get_artifact_ref(artifact_uri)
        if existing is not None:
            return self._load_existing(artifact_uri, bundle_id, target)
        payload = {
            "schema_version": "1.0",
            "id": bundle_id,
            "request_id": str(request_id),
            "performance_draft_id": str(performance_draft_id),
            "build_bundle_ref": artifact_uri,
            "input_hashes": dict(input_hashes),
            "producer": dict(producer),
            "target": str(target),
            "deliverables": deliverable_payloads,
            "warnings": [dict(item) for item in warnings],
            "created_at": str(created_at),
        }
        try:
            payload = validate_contract("BuildBundle", payload)
        except ContractValidationError as exc:
            raise ProductionError(
                "build_bundle_invalid",
                "BuildBundle 不符合正式合同",
                status=422,
                details={"path": exc.path, "reason": str(exc)},
            ) from exc
        self._raise_if_cancelled(cancellation_probe)
        manifest = self.artifacts.commit_bytes(
            f"workspace://build-bundles/{bundle_id}/manifest.json",
            canonical_json_bytes(payload),
            kind="manifest",
            media_type="application/json",
            metadata={"contract": "BuildBundle/1.0", "bundle_id": bundle_id},
        )
        self._raise_if_cancelled(cancellation_probe)
        artifact = self.artifacts.publish_artifact(
            "build-bundles",
            bundle_id,
            manifest,
            provenance={
                "run_id": run_id,
                "work_item_id": work_item_id,
                "attempt_id": attempt_id,
            },
        )
        return BuildBundleRef(
            bundle_id=bundle_id,
            artifact_uri=artifact.uri,
            content_hash=artifact.content_hash,
            target=str(target),
            payload=payload,
        )

    def publish_aa(
        self,
        *,
        request: AdapterRequest,
        draft_ref: DraftRef,
        legacy_result: Mapping[str, Any],
        producer: Mapping[str, str],
        cancelled: Callable[[], bool] | None = None,
    ) -> BuildBundleRef:
        raw_path = legacy_result.get("aap_path")
        if raw_path is None:
            raise ProductionError(
                "aa_compile_output_invalid",
                "AA 编译结果没有可登记的 .aap 交付物",
                status=500,
            )
        source = Path(str(raw_path))
        file_hash = self._hash_source(source)
        artifact_id = str(uuid.uuid5(_DELIVERABLE_NAMESPACE, f"aap:{file_hash}"))
        cancellation_probe = cancelled or request.is_cancelled
        return self.assemble(
            request_id=request.request_id,
            performance_draft_id=draft_ref.draft_id,
            input_hashes={
                "script_release": request.input_hashes["script_release"],
                "performance_draft": draft_ref.content_hash,
                "asset_manifest": request.input_hashes["asset_manifest"],
            },
            producer=producer,
            target="pc_aap",
            deliverables=[
                DeliverableInput(
                    artifact_id=artifact_id,
                    kind="aap",
                    media_type="application/octet-stream",
                    source_path=source,
                    workspace_uri=(
                        f"workspace://build-deliverables/{artifact_id}/project.aap"
                    ),
                )
            ],
            created_at=str(legacy_result.get("created_at") or self._stable_created_at(source)),
            run_id=request.run_id,
            work_item_id=request.work_item_id,
            attempt_id=request.attempt_id,
            cancelled=cancellation_probe,
        )

    def _register_deliverable(
        self,
        raw: DeliverableInput | ArtifactRef | Any,
        *,
        run_id: str | None,
        work_item_id: str | None,
        attempt_id: str | None,
    ) -> _RegisteredDeliverable:
        if isinstance(raw, ArtifactRef):
            artifact = self.artifacts.get_artifact(raw.uri)
            workspace = self.artifacts.get(artifact.workspace_uri)
            artifact_id = self._artifact_id_from_uri(artifact.uri)
            self._validate_kind(artifact.kind)
            return _RegisteredDeliverable(artifact_id, artifact, workspace)
        if not isinstance(raw, DeliverableInput):
            raise ProductionError(
                "build_bundle_output_unregistered",
                "BuildBundle 交付物必须是已登记 Artifact 或显式 staging 输入",
                status=400,
            )
        kind = self._validate_kind(raw.kind)
        source = self._staged_source(raw.source_path)
        workspace = self.artifacts.commit_file(
            raw.workspace_uri,
            source,
            kind=kind,
            media_type=raw.media_type,
            metadata={"bundle_deliverable": True},
        )
        artifact_id = raw.artifact_id or str(
            uuid.uuid5(_DELIVERABLE_NAMESPACE, f"{kind}:{workspace.content_hash}")
        )
        self._canonical_uuid(artifact_id, "artifact_id")
        artifact = self.artifacts.publish_artifact(
            "build-deliverables",
            artifact_id,
            workspace,
            provenance={
                "run_id": run_id,
                "work_item_id": work_item_id,
                "attempt_id": attempt_id,
            },
        )
        if artifact.content_hash != workspace.content_hash:
            raise ProductionError(
                "build_bundle_deliverable_hash_mismatch",
                "交付物 Artifact 哈希与工作区文件不一致",
                status=500,
            )
        return _RegisteredDeliverable(artifact_id, artifact, workspace)

    def _load_existing(
        self, artifact_uri: str, bundle_id: str, target: str
    ) -> BuildBundleRef:
        artifact = self.artifacts.get_artifact(artifact_uri)
        try:
            raw = json.loads(self.artifacts.read_artifact_bytes(artifact_uri).decode("utf-8"))
            payload = validate_contract("BuildBundle", raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            raise ProductionError(
                "build_bundle_corrupt",
                "已登记 BuildBundle manifest 无法读取",
                status=500,
                details={"artifact_uri": artifact_uri},
            ) from exc
        if payload["id"] != bundle_id or payload["target"] != target:
            raise ProductionError(
                "build_bundle_identity_conflict",
                "已登记 BuildBundle 与固定输入不一致",
                status=409,
            )
        return BuildBundleRef(
            bundle_id=bundle_id,
            artifact_uri=artifact_uri,
            content_hash=artifact.content_hash,
            target=target,
            payload=payload,
        )

    def _staged_source(self, source: Path) -> Path:
        if self.staging_root is None:
            raise ProductionError(
                "build_bundle_source_unregistered",
                "未配置允许的构建 staging 根目录",
                status=409,
            )
        candidate = Path(source)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self.staging_root)
        except (OSError, ValueError) as exc:
            raise ProductionError(
                "build_bundle_source_outside_staging",
                "构建交付物越过允许的 staging 目录",
                status=400,
            ) from exc
        if candidate.is_symlink() or not resolved.is_file():
            raise ProductionError(
                "build_bundle_source_outside_staging",
                "构建交付物不是安全的普通文件",
                status=400,
            )
        return resolved

    @staticmethod
    def _validate_kind(value: Any) -> str:
        kind = str(value or "").strip().casefold()
        if kind not in _DELIVERABLE_KINDS:
            raise ProductionError(
                "build_bundle_deliverable_kind_invalid",
                "BuildBundle 交付物类型无效",
                status=400,
            )
        return kind

    @staticmethod
    def _artifact_id_from_uri(uri: str) -> str:
        parts = urlsplit(uri).path.split("/")
        artifact_id = parts[-1] if parts else ""
        BuildBundleAssembler._canonical_uuid(artifact_id, "artifact_id")
        return artifact_id

    @staticmethod
    def _canonical_uuid(value: Any, field_name: str) -> str:
        text = str(value or "").strip()
        try:
            parsed = uuid.UUID(text)
        except (AttributeError, ValueError) as exc:
            raise ProductionError(
                "build_bundle_identity_invalid",
                f"{field_name} 必须是 canonical UUID",
                status=400,
            ) from exc
        if str(parsed) != text:
            raise ProductionError(
                "build_bundle_identity_invalid",
                f"{field_name} 必须是 canonical UUID",
                status=400,
            )
        return text

    @staticmethod
    def _raise_if_cancelled(probe: Callable[[], bool]) -> None:
        if probe():
            raise ProductionError(
                "adapter_operation_cancelled",
                "BuildBundle 发布已取消",
                status=409,
            )

    @staticmethod
    def _hash_source(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ProductionError(
                "artifact_source_unreadable",
                "构建输出无法读取",
                status=422,
            ) from exc
        return "sha256:" + digest.hexdigest()

    @staticmethod
    def _stable_created_at(path: Path) -> str:
        try:
            timestamp = path.stat().st_mtime
        except OSError as exc:
            raise ProductionError(
                "artifact_source_unreadable",
                "构建输出无法读取",
                status=422,
            ) from exc
        import datetime as dt

        return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()


__all__ = ["BuildBundleAssembler", "DeliverableInput"]
