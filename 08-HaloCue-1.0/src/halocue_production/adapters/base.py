from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable
from urllib.parse import urlsplit

from ..contracts import ContractValidationError, validate_contract
from ..errors import ProductionError


_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{1,79}")
_URI_NAMESPACE = re.compile(r"[a-z][a-z0-9._-]{0,79}")
_OPERATIONS = frozenset(
    {
        "preflight",
        "create_performance_draft",
        "update_performance_draft",
        "validate",
        "compile_aap",
        "render_preview",
        "export_video",
        "cancel",
        "install_aap",
    }
)


def _copy_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("formal payload must be an object")
    return copy.deepcopy(dict(value))


def _uuid(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a canonical UUID") from exc
    if str(parsed) != text:
        raise ValueError(f"{field_name} must be a canonical UUID")
    return text


def _hash(value: Any, field_name: str) -> str:
    text = str(value or "")
    if not _HASH.fullmatch(text):
        raise ValueError(f"{field_name} must be sha256:<64 lowercase hex>")
    return text


def _uri(value: Any, field_name: str, *, schemes: set[str]) -> str:
    text = str(value or "").strip()
    parsed = urlsplit(text)
    if (
        not text
        or parsed.scheme not in schemes
        or not _URI_NAMESPACE.fullmatch(parsed.netloc.casefold())
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.query
        or parsed.fragment
        or "\\" in text
        or any(part in {"", ".", ".."} for part in parsed.path.split("/")[1:])
    ):
        raise ValueError(f"{field_name} must be a stable workspace/artifact URI")
    return text


def _identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip().casefold()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{field_name} must be a stable identifier")
    return text


@dataclass(frozen=True, init=False)
class AdapterRequest:
    """Validated, read-only view of the formal production inputs."""

    production_request: dict[str, Any]
    script_release: dict[str, Any] | None
    asset_manifest: dict[str, Any] | None
    target: str
    run_id: str | None
    work_item_id: str | None
    attempt_id: str | None
    cancelled: bool

    def __init__(
        self,
        production_request: Mapping[str, Any] | None = None,
        *,
        request: Mapping[str, Any] | None = None,
        script_release: Mapping[str, Any] | None = None,
        asset_manifest: Mapping[str, Any] | None = None,
        target: str | None = None,
        run_id: str | None = None,
        work_item_id: str | None = None,
        attempt_id: str | None = None,
        cancelled: bool = False,
    ) -> None:
        if production_request is not None and request is not None:
            raise TypeError("provide production_request or request, not both")
        raw_request = production_request if production_request is not None else request
        if raw_request is None:
            raise TypeError("production_request is required")
        request_payload = _copy_payload(raw_request)
        try:
            normalized_request = validate_contract("ProductionRequest", request_payload)
        except ContractValidationError as exc:
            raise ValueError(
                f"invalid ProductionRequest at {exc.path}: {exc}"
            ) from exc
        normalized_release = None
        if script_release is not None:
            try:
                normalized_release = validate_contract(
                    "ScriptRelease", _copy_payload(script_release)
                )
            except ContractValidationError as exc:
                raise ValueError(f"invalid ScriptRelease at {exc.path}: {exc}") from exc
        normalized_manifest = None
        if asset_manifest is not None:
            try:
                normalized_manifest = validate_contract(
                    "AssetManifest", _copy_payload(asset_manifest)
                )
            except ContractValidationError as exc:
                raise ValueError(f"invalid AssetManifest at {exc.path}: {exc}") from exc
        policy = normalized_request.get("production_policy", {})
        request_target = policy.get("target") if isinstance(policy, dict) else None
        normalized_target = _identifier(target or request_target, "target")
        if request_target and str(request_target).casefold() != normalized_target:
            raise ValueError("target does not match ProductionRequest production_policy")
        object.__setattr__(self, "production_request", normalized_request)
        object.__setattr__(self, "script_release", normalized_release)
        object.__setattr__(self, "asset_manifest", normalized_manifest)
        object.__setattr__(self, "target", normalized_target)
        object.__setattr__(
            self, "run_id", _uuid(run_id, "run_id") if run_id is not None else None
        )
        object.__setattr__(
            self,
            "work_item_id",
            _uuid(work_item_id, "work_item_id") if work_item_id is not None else None,
        )
        object.__setattr__(
            self,
            "attempt_id",
            _uuid(attempt_id, "attempt_id") if attempt_id is not None else None,
        )
        object.__setattr__(self, "cancelled", bool(cancelled))

    @property
    def request_id(self) -> str:
        return _uuid(self.production_request["request_id"], "request_id")

    @property
    def script_release_id(self) -> str:
        return _uuid(self.production_request["script_release"]["id"], "script_release.id")

    @property
    def asset_manifest_id(self) -> str:
        return _uuid(self.production_request["asset_manifest"]["id"], "asset_manifest.id")

    @property
    def input_hashes(self) -> dict[str, str]:
        release = self.production_request["script_release"]
        manifest = self.production_request["asset_manifest"]
        return {
            "script_release": _hash(release["content_hash"], "script_release.content_hash"),
            "asset_manifest": _hash(manifest["content_hash"], "asset_manifest.content_hash"),
        }

    def to_payload(self) -> dict[str, Any]:
        return copy.deepcopy(self.production_request)


@dataclass(frozen=True)
class DraftRef:
    draft_id: str
    revision_id: str
    artifact_uri: str
    content_hash: str
    review_status: str = "approved"
    adapter_id: str | None = None
    payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "draft_id", _uuid(self.draft_id, "draft_id"))
        object.__setattr__(self, "revision_id", _uuid(self.revision_id, "revision_id"))
        object.__setattr__(
            self,
            "artifact_uri",
            _uri(self.artifact_uri, "artifact_uri", schemes={"workspace", "artifact"}),
        )
        object.__setattr__(self, "content_hash", _hash(self.content_hash, "content_hash"))
        if self.review_status not in {"draft", "pending_review", "approved", "rejected"}:
            raise ValueError("review_status is not supported")
        if self.adapter_id is not None:
            object.__setattr__(self, "adapter_id", _identifier(self.adapter_id, "adapter_id"))
        if self.payload is not None:
            normalized = validate_contract("PerformanceDraft", _copy_payload(self.payload))
            object.__setattr__(self, "payload", normalized)

    @property
    def uri(self) -> str:
        return self.artifact_uri

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "revision_id": self.revision_id,
            "artifact_uri": self.artifact_uri,
            "content_hash": self.content_hash,
            "review_status": self.review_status,
            "adapter_id": self.adapter_id,
        }


@dataclass(frozen=True)
class BuildBundleRef:
    bundle_id: str
    artifact_uri: str
    content_hash: str
    target: str
    payload: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", _uuid(self.bundle_id, "bundle_id"))
        object.__setattr__(
            self,
            "artifact_uri",
            _uri(self.artifact_uri, "artifact_uri", schemes={"artifact"}),
        )
        object.__setattr__(self, "content_hash", _hash(self.content_hash, "content_hash"))
        object.__setattr__(self, "target", _identifier(self.target, "target"))
        if self.payload is not None:
            normalized = validate_contract("BuildBundle", _copy_payload(self.payload))
            object.__setattr__(self, "payload", normalized)

    @property
    def uri(self) -> str:
        return self.artifact_uri

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "artifact_uri": self.artifact_uri,
            "content_hash": self.content_hash,
            "target": self.target,
        }


@dataclass(frozen=True)
class AdapterResult:
    """Result envelope containing references only, never a physical path."""

    bundle_ref: BuildBundleRef | None = None
    draft_ref: DraftRef | None = None
    artifact_refs: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    cancelled: bool = False

    def __post_init__(self) -> None:
        refs = tuple(
            _uri(ref, "artifact_ref", schemes={"artifact"}) for ref in self.artifact_refs
        )
        object.__setattr__(self, "artifact_refs", refs)
        object.__setattr__(self, "warnings", tuple(str(item) for item in self.warnings))
        normalized_diagnostics: list[dict[str, Any]] = []
        for item in self.diagnostics:
            if not isinstance(item, Mapping):
                raise ValueError("adapter diagnostics must be objects")
            allowed = {"code", "severity", "message"}
            if set(item) - allowed:
                raise ValueError("adapter diagnostics contain unsupported fields")
            normalized_diagnostics.append(
                {
                    "code": str(item.get("code") or "diagnostic"),
                    "severity": str(item.get("severity") or "info"),
                    "message": str(item.get("message") or ""),
                }
            )
        object.__setattr__(self, "diagnostics", tuple(normalized_diagnostics))
        if self.cancelled and (self.bundle_ref is not None or refs):
            raise ValueError("cancelled adapter results cannot contain artifacts")

    @property
    def artifact_uri(self) -> str | None:
        return self.bundle_ref.artifact_uri if self.bundle_ref is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_ref": self.bundle_ref.to_dict() if self.bundle_ref else None,
            "draft_ref": self.draft_ref.to_dict() if self.draft_ref else None,
            "artifact_refs": list(self.artifact_refs),
            "warnings": list(self.warnings),
            "diagnostics": [dict(item) for item in self.diagnostics],
            "cancelled": self.cancelled,
        }


@runtime_checkable
class ProductionAdapter(Protocol):
    def capabilities(self) -> dict[str, Any]: ...

    def preflight(self, request: AdapterRequest) -> AdapterResult: ...

    def create_performance_draft(
        self, request: AdapterRequest, scope: Mapping[str, Any] | None = None
    ) -> AdapterResult: ...

    def update_performance_draft(
        self, draft_ref: DraftRef, patch: Mapping[str, Any]
    ) -> AdapterResult: ...

    def validate(self, request: AdapterRequest, draft_ref: DraftRef) -> AdapterResult: ...

    def compile(self, request: AdapterRequest, draft_ref: DraftRef) -> AdapterResult: ...

    def render(
        self,
        request: AdapterRequest,
        draft_ref: DraftRef,
        options: Mapping[str, Any] | None = None,
    ) -> AdapterResult: ...

    def cancel(self, attempt_ref: str) -> AdapterResult: ...


class AdapterBase:
    """Small mixin for concrete adapters to enforce capability boundaries."""

    def require_capability(self, operation: str, *, target: str | None = None) -> None:
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown adapter operation: {operation}")
        payload = self.capabilities()
        capabilities = payload.get("capabilities", []) if isinstance(payload, dict) else []
        if operation in capabilities:
            return
        adapter_id = payload.get("adapter_id", "unknown-adapter") if isinstance(payload, dict) else "unknown-adapter"
        targets = payload.get("targets", []) if isinstance(payload, dict) else []
        resolved_target = target or (targets[0] if targets else None)
        raise ProductionError(
            "adapter_capability_unavailable",
            "适配器不支持请求的制作能力",
            status=409,
            details={
                "adapter_id": str(adapter_id),
                "target": resolved_target,
                "operation": operation,
            },
        )


__all__ = [
    "AdapterBase",
    "AdapterRequest",
    "AdapterResult",
    "BuildBundleRef",
    "DraftRef",
    "ProductionAdapter",
]
