from __future__ import annotations

import json
from typing import Any

from .artifacts import ArtifactStore, WorkspaceFile
from .contracts import (
    ContractValidationError,
    canonical_json_bytes,
    validate_contract,
)
from .errors import ProductionError
from .models import ProductionRun
from .runtime import RuntimeStore


RUNNABLE_PRODUCTION_REQUEST_VERSION = "1.1"
FORMAL_SCRIPT_RELEASE_VERSION = "1.1"
MAX_SCRIPT_BYTES = 5 * 1024 * 1024


class FormalProductionInputs:
    """Validate and persist formal cross-domain production inputs."""

    def __init__(self, artifacts: ArtifactStore, runtime: RuntimeStore) -> None:
        self.artifacts = artifacts
        self.runtime = runtime

    @staticmethod
    def validate_request(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            normalized = validate_contract("ProductionRequest", payload)
        except ContractValidationError as exc:
            code = (
                "unsupported_production_request_version"
                if exc.code == "unsupported_contract_version"
                else "production_request_invalid"
            )
            raise ProductionError(
                code,
                "ProductionRequest 不符合受支持的正式合同",
                status=400 if exc.code == "unsupported_contract_version" else 422,
                details={
                    "path": exc.path,
                    "reason": str(exc),
                    **exc.details,
                },
            ) from exc
        if normalized["schema_version"] != RUNNABLE_PRODUCTION_REQUEST_VERSION:
            raise ProductionError(
                "production_request_version_not_runnable",
                "ProductionRequest/1.0 缺少正文 URI，不能创建正式制作任务",
                status=409,
                details={
                    "received": normalized["schema_version"],
                    "runnable": [RUNNABLE_PRODUCTION_REQUEST_VERSION],
                },
            )
        return normalized

    def existing_request(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        reference = payload["script_release"]
        frozen = self.runtime.get_frozen_script_release(reference["id"])
        if frozen is not None and frozen["content_hash"] != reference["content_hash"]:
            raise ProductionError(
                "script_release_identity_conflict",
                "同一 ScriptRelease ID 已冻结不同正文",
                status=409,
                details={"release_id": reference["id"]},
            )
        existing = self.runtime.get_production_request(payload["request_id"])
        if existing is not None:
            if existing["idempotency_key"] != payload["idempotency_key"]:
                raise ProductionError(
                    "production_request_identity_conflict",
                    "同一 ProductionRequest ID 对应不同请求内容",
                    status=409,
                    details={"request_id": payload["request_id"]},
                )
            return existing
        release_owner = self.runtime.get_production_request_for_release(reference["id"])
        if release_owner is not None:
            raise ProductionError(
                "production_request_conflict",
                "该 ScriptRelease 已由另一份 ProductionRequest 创建制作任务",
                status=409,
                details={
                    "release_id": reference["id"],
                    "existing_request_id": release_owner["id"],
                },
            )
        return None

    def freeze_script_release(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        reference = request["script_release"]
        manifest_artifact = self.artifacts.get(reference["manifest_uri"])
        manifest_bytes = self.artifacts.read_bytes(reference["manifest_uri"])
        try:
            raw = json.loads(manifest_bytes.decode("utf-8"))
            release = validate_contract("ScriptRelease", raw)
        except (UnicodeDecodeError, json.JSONDecodeError, ContractValidationError) as exc:
            if isinstance(exc, ContractValidationError) and exc.code == "unsupported_contract_version":
                code = "unsupported_script_release_version"
                status = 400
            else:
                code = "script_release_invalid"
                status = 422
            details = {"release_id": reference["id"]}
            if isinstance(exc, ContractValidationError):
                details.update({"path": exc.path, "reason": str(exc)})
            raise ProductionError(
                code,
                "ScriptRelease 清单无效或版本不受支持",
                status=status,
                details=details,
            ) from exc
        if release["schema_version"] != FORMAL_SCRIPT_RELEASE_VERSION:
            raise ProductionError(
                "unsupported_script_release_version",
                "正式制作入口只接受 ScriptRelease/1.1",
                status=400,
                details={
                    "received": release["schema_version"],
                    "supported": [FORMAL_SCRIPT_RELEASE_VERSION],
                },
            )
        expected = {
            "schema_version": reference["version"],
            "id": reference["id"],
            "display_version": reference["display_version"],
            "manifest_uri": reference["manifest_uri"],
            "content_uri": reference["content_uri"],
            "content_hash": reference["content_hash"],
        }
        if any(release[key] != value for key, value in expected.items()):
            raise ProductionError(
                "script_release_reference_mismatch",
                "ProductionRequest 中的 ScriptRelease 引用与冻结清单不一致",
                status=409,
                details={"release_id": reference["id"]},
            )
        content_artifact = self.artifacts.get(release["content_uri"])
        if content_artifact.content_hash != release["content_hash"]:
            raise ProductionError(
                "script_release_hash_mismatch",
                "ScriptRelease 正文哈希与清单不一致",
                status=409,
                details={"release_id": release["id"]},
            )
        content = self.artifacts.read_bytes(release["content_uri"])
        if len(content) > MAX_SCRIPT_BYTES:
            raise ProductionError(
                "source_too_large", "剧本文本不能超过 5 MiB", status=413
            )
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProductionError(
                "script_release_content_invalid",
                "ScriptRelease 正文必须是 UTF-8 文本",
                status=422,
                details={"release_id": release["id"]},
            ) from exc
        if not text.strip() or "\x00" in text:
            raise ProductionError(
                "script_release_content_invalid",
                "ScriptRelease 正文为空或包含无效字符",
                status=422,
                details={"release_id": release["id"]},
            )
        frozen = self.runtime.register_frozen_script_release(
            release,
            manifest_file_hash=manifest_artifact.content_hash,
        )
        return frozen, text

    def bind_request(
        self,
        payload: dict[str, Any],
        run: ProductionRun,
    ) -> dict[str, Any]:
        uri = f'workspace://requests/{payload["request_id"]}/request.json'
        try:
            artifact = self.artifacts.commit_bytes(
                uri,
                canonical_json_bytes(payload),
                kind="production-request",
                media_type="application/json",
                metadata={
                    "contract": "ProductionRequest/1.1",
                    "request_id": payload["request_id"],
                    "release_id": payload["script_release"]["id"],
                },
            )
        except ProductionError as exc:
            artifact = self._existing_duplicate(exc)
        binding = self.runtime.bind_production_request(
            request_id=payload["request_id"],
            schema_version=payload["schema_version"],
            idempotency_key=payload["idempotency_key"],
            request_uri=artifact.uri,
            request_file_hash=artifact.content_hash,
            release_id=payload["script_release"]["id"],
            legacy_run_id=run.run_id,
            production_display_name=payload["production_display_name"],
            asset_manifest_id=payload["asset_manifest"]["id"],
            target=payload["production_policy"]["target"],
        )
        return self._description(binding)

    def describe_for_run(self, legacy_run_id: str) -> dict[str, Any] | None:
        binding = self.runtime.get_production_request_for_run(legacy_run_id)
        if binding is None:
            return None
        self._verify_binding(binding)
        return self._description(binding)

    def _verify_binding(self, binding: dict[str, Any]) -> None:
        artifact = self.artifacts.get(binding["request_uri"])
        if artifact.content_hash != binding["request_file_hash"]:
            raise ProductionError(
                "production_request_artifact_hash_mismatch",
                "ProductionRequest 文件哈希与冻结记录不一致",
                status=500,
                details={"request_id": binding["id"]},
            )
        content = self.artifacts.read_bytes(binding["request_uri"])
        try:
            raw = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionError(
                "production_request_corrupt",
                "冻结的 ProductionRequest 无法读取",
                status=500,
                details={"request_id": binding["id"]},
            ) from exc
        request = self.validate_request(raw)
        expected = {
            "request_id": binding["id"],
            "schema_version": binding["schema_version"],
            "idempotency_key": binding["idempotency_key"],
            "production_display_name": binding["production_display_name"],
        }
        if any(request[key] != value for key, value in expected.items()) or any(
            (
                request["script_release"]["id"] != binding["release_id"],
                request["asset_manifest"]["id"] != binding["asset_manifest_id"],
                request["production_policy"]["target"] != binding["target"],
            )
        ):
            raise ProductionError(
                "production_request_binding_corrupt",
                "ProductionRequest 内容与制作任务冻结记录不一致",
                status=500,
                details={"request_id": binding["id"]},
            )
        self.freeze_script_release(request)

    def _existing_duplicate(self, error: ProductionError) -> WorkspaceFile:
        if error.code != "workspace_file_duplicate":
            raise error
        existing_uri = str(error.details.get("existing_uri") or "")
        if not existing_uri:
            raise error
        return self.artifacts.get(existing_uri)

    @staticmethod
    def _description(binding: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": binding["id"],
            "version": binding["schema_version"],
            "idempotency_key": binding["idempotency_key"],
            "release_id": binding["release_id"],
            "asset_manifest_id": binding["asset_manifest_id"],
            "target": binding["target"],
        }
