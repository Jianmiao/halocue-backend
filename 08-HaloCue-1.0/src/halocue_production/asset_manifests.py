from __future__ import annotations

import json
import threading
import uuid
from typing import Any

from .artifacts import ArtifactStore
from .contracts import (
    ContractValidationError,
    canonical_json_bytes,
    contract_content_hash,
    validate_contract,
)
from .errors import ProductionError
from .models import ProductionRun
from .runtime import RuntimeStore


_MANIFEST_NAMESPACE = uuid.UUID("63e78d19-fdac-4ac1-9804-da72f134a7a0")
_SOURCE_KINDS = frozenset(
    {"compatibility_empty", "production_request", "task_asset_upgrade"}
)


class AssetManifestStore:
    """Freeze and verify the asset allowlist bound to a ProductionRun."""

    def __init__(self, artifacts: ArtifactStore, runtime: RuntimeStore) -> None:
        self.artifacts = artifacts
        self.runtime = runtime
        self._lock = threading.RLock()

    @staticmethod
    def _reference(binding: dict[str, Any]) -> dict[str, str]:
        return {
            "id": str(binding["id"]),
            "version": "1.0",
            "content_hash": str(binding["content_hash"]),
            "uri": str(binding["workspace_uri"]),
        }

    @staticmethod
    def _manifest_id(production_run_id: str) -> str:
        try:
            run_uuid = uuid.UUID(production_run_id)
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProductionError(
                "production_run_identity_invalid",
                "制作任务缺少稳定身份",
                status=500,
            ) from exc
        return str(uuid.uuid5(_MANIFEST_NAMESPACE, str(run_uuid)))

    def ensure_compatibility_manifest(self, run: ProductionRun) -> dict[str, Any]:
        existing = self.runtime.get_asset_manifest_for_run(run.run_id)
        if existing is not None:
            payload = self._load(existing)
            return self._description(existing, payload)
        if not run.production_run_id:
            raise ProductionError(
                "production_run_identity_invalid",
                "制作任务缺少稳定身份",
                status=500,
            )
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "id": self._manifest_id(run.production_run_id),
            "content_hash": "",
            "created_at": run.created_at,
            "assets": [],
        }
        payload["content_hash"] = contract_content_hash("AssetManifest", payload)
        return self.freeze(run, payload, source_kind="compatibility_empty")

    def freeze(
        self,
        run: ProductionRun,
        payload: dict[str, Any],
        *,
        source_kind: str,
    ) -> dict[str, Any]:
        with self._lock:
            return self._freeze(run, payload, source_kind=source_kind)

    def _freeze(
        self,
        run: ProductionRun,
        payload: dict[str, Any],
        *,
        source_kind: str,
    ) -> dict[str, Any]:
        persisted_run = self.runtime.get_production_run(run.run_id)
        if (
            persisted_run is None
            or not run.production_run_id
            or persisted_run["production_run_id"] != run.production_run_id
        ):
            raise ProductionError(
                "production_run_identity_invalid",
                "只能为已持久化的制作任务冻结 AssetManifest",
                status=409,
                details={"run_id": run.run_id},
            )
        if source_kind not in _SOURCE_KINDS:
            raise ProductionError(
                "asset_manifest_source_invalid",
                "AssetManifest 冻结来源无效",
                status=400,
            )
        if source_kind == "compatibility_empty" and payload.get("assets") != []:
            raise ProductionError(
                "asset_manifest_compatibility_not_empty",
                "兼容流程不得把 AA 资源冒充为正式 AssetManifest",
                status=409,
            )
        try:
            normalized = validate_contract("AssetManifest", payload)
        except ContractValidationError as exc:
            raise ProductionError(
                "asset_manifest_invalid",
                "AssetManifest 不符合 1.0 合同",
                status=422,
                details={"path": exc.path, "reason": str(exc)},
            ) from exc
        existing = self.runtime.get_asset_manifest_for_run(run.run_id)
        if existing is not None:
            if (
                existing["id"] != normalized["id"]
                or existing["content_hash"] != normalized["content_hash"]
                or existing["source_kind"] != source_kind
            ):
                raise ProductionError(
                    "asset_manifest_conflict",
                    "同一制作任务已冻结不同的 AssetManifest",
                    status=409,
                    details={
                        "run_id": run.run_id,
                        "existing_manifest_id": existing["id"],
                    },
                )
            current = self._load(existing)
            return self._description(existing, current)
        self._verify_allowlisted_assets(normalized)
        uri, artifact = self._commit_manifest(run, normalized, source_kind)
        binding = self.runtime.bind_asset_manifest(
            legacy_run_id=run.run_id,
            manifest_id=normalized["id"],
            workspace_uri=uri,
            content_hash=normalized["content_hash"],
            file_hash=artifact.content_hash,
            source_kind=source_kind,
            created_at=normalized["created_at"],
        )
        return self._description(binding, normalized)

    def advance(
        self,
        run: ProductionRun,
        payload: dict[str, Any],
        *,
        expected_manifest_id: str,
        expected_content_hash: str,
        source_kind: str,
        selection_kind: str,
    ) -> dict[str, Any]:
        with self._lock:
            persisted_run = self.runtime.get_production_run(run.run_id)
            if (
                persisted_run is None
                or not run.production_run_id
                or persisted_run["production_run_id"] != run.production_run_id
            ):
                raise ProductionError(
                    "production_run_identity_invalid",
                    "只能为已持久化的制作任务升级 AssetManifest",
                    status=409,
                    details={"run_id": run.run_id},
                )
            if source_kind not in _SOURCE_KINDS:
                raise ProductionError(
                    "asset_manifest_source_invalid",
                    "AssetManifest 冻结来源无效",
                    status=400,
                )
            try:
                normalized = validate_contract("AssetManifest", payload)
            except ContractValidationError as exc:
                raise ProductionError(
                    "asset_manifest_invalid",
                    "AssetManifest 不符合 1.0 合同",
                    status=422,
                    details={"path": exc.path, "reason": str(exc)},
                ) from exc
            current_binding = self.runtime.get_asset_manifest_for_run(run.run_id)
            if current_binding is None:
                raise ProductionError(
                    "asset_manifest_not_found",
                    "制作任务尚未冻结 AssetManifest",
                    status=409,
                    details={"run_id": run.run_id},
                )
            if (
                current_binding["id"] != expected_manifest_id
                or current_binding["content_hash"] != expected_content_hash
            ):
                if (
                    current_binding["id"] == normalized["id"]
                    and current_binding["content_hash"] == normalized["content_hash"]
                ):
                    current = self._load(current_binding)
                    return self._description(current_binding, current)
                raise ProductionError(
                    "asset_manifest_revision_conflict",
                    "AssetManifest 已被其他操作升级",
                    status=409,
                    details={
                        "run_id": run.run_id,
                        "current_manifest_id": current_binding["id"],
                        "current_content_hash": current_binding["content_hash"],
                    },
                )
            self._verify_allowlisted_assets(normalized)
            uri, artifact = self._commit_manifest(run, normalized, source_kind)
            binding = self.runtime.advance_asset_manifest(
                legacy_run_id=run.run_id,
                expected_manifest_id=expected_manifest_id,
                expected_content_hash=expected_content_hash,
                manifest_id=normalized["id"],
                workspace_uri=uri,
                content_hash=normalized["content_hash"],
                file_hash=artifact.content_hash,
                source_kind=source_kind,
                created_at=normalized["created_at"],
                selection_kind=selection_kind,
            )
            return self._description(binding, normalized)

    def freeze_reference(
        self,
        run: ProductionRun,
        reference: dict[str, Any],
        *,
        source_kind: str = "production_request",
    ) -> dict[str, Any]:
        """Bind an already registered formal AssetManifest to a ProductionRun."""
        with self._lock:
            if source_kind not in _SOURCE_KINDS:
                raise ProductionError(
                    "asset_manifest_source_invalid",
                    "AssetManifest 冻结来源无效",
                    status=400,
                )
            normalized, artifact = self._reference_payload(reference)
            binding = self.runtime.bind_asset_manifest(
                legacy_run_id=run.run_id,
                manifest_id=normalized["id"],
                workspace_uri=artifact.uri,
                content_hash=normalized["content_hash"],
                file_hash=artifact.content_hash,
                source_kind=source_kind,
                created_at=normalized["created_at"],
            )
            return self._description(binding, normalized)

    def validate_reference(self, reference: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            normalized, _ = self._reference_payload(reference)
            return normalized

    def _reference_payload(
        self, reference: dict[str, Any]
    ) -> tuple[dict[str, Any], Any]:
        if reference.get("version") != "1.0":
            raise ProductionError(
                "unsupported_asset_manifest_version",
                "正式制作入口只接受 AssetManifest/1.0",
                status=400,
            )
        artifact = self.artifacts.get(str(reference.get("uri") or ""))
        content = self.artifacts.read_bytes(artifact.uri)
        try:
            raw = json.loads(content.decode("utf-8"))
            normalized = validate_contract("AssetManifest", raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            raise ProductionError(
                "asset_manifest_invalid",
                "AssetManifest 引用的工作区文件不符合 1.0 合同",
                status=422,
            ) from exc
        expected = {
            "id": str(reference.get("id") or ""),
            "content_hash": str(reference.get("content_hash") or ""),
        }
        if any(normalized[key] != value for key, value in expected.items()):
            raise ProductionError(
                "asset_manifest_reference_mismatch",
                "ProductionRequest 中的素材清单引用与文件内容不一致",
                status=409,
                details={"manifest_id": expected["id"]},
            )
        self._verify_allowlisted_assets(normalized)
        return normalized, artifact

    def _commit_manifest(
        self,
        run: ProductionRun,
        payload: dict[str, Any],
        source_kind: str,
    ) -> tuple[str, Any]:
        uri = f'workspace://assets/{payload["id"]}/manifest.json'
        try:
            artifact = self.artifacts.commit_bytes(
                uri,
                canonical_json_bytes(payload),
                kind="asset-manifest",
                media_type="application/json",
                metadata={
                    "contract": "AssetManifest/1.0",
                    "source_kind": source_kind,
                    "run_id": run.run_id,
                },
            )
        except ProductionError as exc:
            if exc.code != "workspace_file_conflict":
                raise
            raise ProductionError(
                "asset_manifest_conflict",
                "AssetManifest 工作区 URI 已存在不同内容",
                status=409,
                details={"run_id": run.run_id, "manifest_id": payload["id"]},
            ) from exc
        return uri, artifact

    def describe_for_run(self, legacy_run_id: str) -> dict[str, Any]:
        binding = self.runtime.get_asset_manifest_for_run(legacy_run_id)
        if binding is None:
            raise ProductionError(
                "asset_manifest_not_found",
                "制作任务尚未冻结 AssetManifest",
                status=500,
                details={"run_id": legacy_run_id},
            )
        payload = self._load(binding)
        return self._description(binding, payload)

    def payload_for_run(self, legacy_run_id: str) -> dict[str, Any]:
        binding = self.runtime.get_asset_manifest_for_run(legacy_run_id)
        if binding is None:
            raise ProductionError(
                "asset_manifest_not_found",
                "制作任务尚未冻结 AssetManifest",
                status=500,
                details={"run_id": legacy_run_id},
            )
        return self._load(binding)

    def history_for_run(self, legacy_run_id: str) -> list[dict[str, Any]]:
        values = []
        for binding in self.runtime.list_asset_manifests_for_run(legacy_run_id):
            payload = self._load(binding)
            description = self._description(binding, payload)
            values.append(
                {
                    **description,
                    "predecessor_manifest_id": binding["predecessor_manifest_id"],
                    "selection_kind": binding["selection_kind"],
                    "selected_at": binding["selected_at"],
                }
            )
        return values

    def require_asset(self, legacy_run_id: str, asset_id: str) -> dict[str, Any]:
        binding = self.runtime.get_asset_manifest_for_run(legacy_run_id)
        if binding is None:
            raise ProductionError(
                "asset_manifest_not_found",
                "制作任务尚未冻结 AssetManifest",
                status=500,
                details={"run_id": legacy_run_id},
            )
        payload = self._load(binding)
        for asset in payload["assets"]:
            if asset["asset_id"] == asset_id:
                return dict(asset)
        raise ProductionError(
            "asset_not_allowlisted",
            "素材不在该制作任务冻结的白名单中",
            status=409,
            details={"run_id": legacy_run_id, "asset_id": asset_id},
        )

    def _verify_allowlisted_assets(self, payload: dict[str, Any]) -> None:
        for asset in payload["assets"]:
            record = self.artifacts.get(asset["uri"])
            if record.content_hash != asset["content_hash"]:
                raise ProductionError(
                    "asset_manifest_asset_hash_mismatch",
                    "AssetManifest 中的素材哈希与工作区登记不一致",
                    status=409,
                    details={
                        "asset_id": asset["asset_id"],
                        "uri": asset["uri"],
                    },
                )

    def _load(self, binding: dict[str, Any]) -> dict[str, Any]:
        artifact = self.artifacts.get(binding["workspace_uri"])
        if artifact.content_hash != binding["file_hash"]:
            raise ProductionError(
                "asset_manifest_file_hash_mismatch",
                "AssetManifest 文件哈希与冻结记录不一致",
                status=500,
                details={"uri": binding["workspace_uri"]},
            )
        content = self.artifacts.read_bytes(binding["workspace_uri"])
        try:
            payload = json.loads(content.decode("utf-8"))
            normalized = validate_contract("AssetManifest", payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            raise ProductionError(
                "asset_manifest_corrupt",
                "冻结的 AssetManifest 无法读取或不符合 1.0 合同",
                status=500,
                details={"uri": binding["workspace_uri"]},
            ) from exc
        if (
            normalized["id"] != binding["id"]
            or normalized["content_hash"] != binding["content_hash"]
            or normalized["created_at"] != binding["created_at"]
        ):
            raise ProductionError(
                "asset_manifest_binding_corrupt",
                "AssetManifest 内容与制作任务冻结记录不一致",
                status=500,
                details={"uri": binding["workspace_uri"]},
            )
        self._verify_allowlisted_assets(normalized)
        return normalized

    def _description(
        self, binding: dict[str, Any], payload: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "reference": self._reference(binding),
            "policy": {
                "mode": "whitelist_only",
                "source": binding["source_kind"],
                "asset_count": len(payload["assets"]),
                "revision": int(binding["revision"]),
            },
        }
