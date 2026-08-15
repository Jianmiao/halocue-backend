from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import unquote, urlsplit


CONTRACT_VERSION = "1.0"
CONTRACT_NAMES = (
    "ScriptRelease",
    "ProductionRequest",
    "PerformanceDraft",
    "AssetManifest",
    "AdapterCapabilities",
    "BuildBundle",
    "ProductionEvent",
    "ApiError",
)

_HASH = re.compile(r"sha256:[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{1,79}")
_URI_NAMESPACE = re.compile(r"[a-z][a-z0-9._-]{0,79}")
_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{2,79}")
_FORBIDDEN_PRIVATE_KEYS = {
    "api_key",
    "password",
    "secret",
    "private_source",
    "bundle_dir",
    "data_dir",
    "corpus_dir",
    "legacy_root",
    "aap_path",
    "project_dir",
    "save_dir",
    "physical_path",
    "absolute_path",
}


class ContractValidationError(ValueError):
    def __init__(
        self,
        code: str,
        contract: str,
        path: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.contract = contract
        self.path = path
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "contract": self.contract,
            "path": self.path,
            "message": str(self),
            "details": self.details,
        }


def _fail(contract: str, path: str, message: str) -> None:
    raise ContractValidationError("invalid_contract", contract, path, message)


def _object(value: Any, contract: str, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(contract, path, "must be an object")
    return value


def _fields(
    value: dict[str, Any],
    contract: str,
    path: str,
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    optional = optional or set()
    missing = sorted(required - value.keys())
    if missing:
        _fail(contract, path, f"missing required fields: {', '.join(missing)}")
    unknown = sorted(value.keys() - required - optional)
    if unknown:
        _fail(contract, path, f"unknown fields: {', '.join(unknown)}")


def _string(
    value: Any,
    contract: str,
    path: str,
    *,
    minimum: int = 1,
    maximum: int = 4096,
) -> str:
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        _fail(contract, path, f"must be a string with length {minimum}..{maximum}")
    return value


def _nullable_string(
    value: Any, contract: str, path: str, *, maximum: int = 256
) -> str | None:
    if value is None:
        return None
    return _string(value, contract, path, maximum=maximum)


def _enum(value: Any, allowed: set[str], contract: str, path: str) -> str:
    text = _string(value, contract, path, maximum=80)
    if text not in allowed:
        _fail(contract, path, f"must be one of: {', '.join(sorted(allowed))}")
    return text


def _boolean(value: Any, contract: str, path: str) -> bool:
    if not isinstance(value, bool):
        _fail(contract, path, "must be a boolean")
    return value


def _integer(
    value: Any,
    contract: str,
    path: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        _fail(contract, path, f"must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        _fail(contract, path, f"must be an integer <= {maximum}")
    return value


def _number(
    value: Any,
    contract: str,
    path: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(contract, path, "must be a number")
    number = float(value)
    if not minimum <= number <= maximum:
        _fail(contract, path, f"must be between {minimum} and {maximum}")
    return number


def _list(
    value: Any,
    contract: str,
    path: str,
    *,
    minimum: int = 0,
    maximum: int = 10000,
) -> list[Any]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        _fail(contract, path, f"must be an array with {minimum}..{maximum} items")
    return value


def _uuid(value: Any, contract: str, path: str) -> str:
    text = _string(value, contract, path, maximum=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise ContractValidationError(
            "invalid_contract", contract, path, "must be a canonical UUID"
        ) from exc
    if str(parsed) != text:
        _fail(contract, path, "must be a canonical UUID")
    return text


def _identifier(value: Any, contract: str, path: str) -> str:
    text = _string(value, contract, path, maximum=80)
    if not _IDENTIFIER.fullmatch(text):
        _fail(contract, path, "must be a lowercase stable identifier")
    return text


def _hash(value: Any, contract: str, path: str) -> str:
    text = _string(value, contract, path, maximum=71)
    if not _HASH.fullmatch(text):
        _fail(contract, path, "must use sha256:<64 lowercase hex>")
    return text


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize the HaloCue JSON subset used for deterministic content hashes."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonical JSON-safe") from exc
    return text.encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def contract_content_hash(
    contract: str,
    payload: dict[str, Any] | None = None,
    *,
    source_bytes: bytes | None = None,
) -> str:
    """Calculate the content hash for contracts with an explicit hash boundary.

    ScriptRelease hashes the frozen release content bytes. PerformanceDraft and
    AssetManifest hash their canonical envelope without the self-referential
    ``content_hash`` field. Other contracts use Artifact/file hashes instead.
    """
    if contract == "ScriptRelease":
        if source_bytes is None:
            raise ValueError("ScriptRelease requires source_bytes")
        return sha256_bytes(source_bytes)
    if contract in {"PerformanceDraft", "AssetManifest"}:
        if not isinstance(payload, dict):
            raise ValueError(f"{contract} requires a payload")
        body = dict(payload)
        body.pop("content_hash", None)
        return sha256_bytes(canonical_json_bytes(body))
    raise ValueError(f"{contract} does not define an envelope content hash")


def idempotency_key_for_request(payload: dict[str, Any]) -> str:
    """Hash a ProductionRequest envelope without its self-referential key."""
    body = dict(payload)
    body.pop("idempotency_key", None)
    return sha256_bytes(canonical_json_bytes(body))


def _timestamp(value: Any, contract: str, path: str) -> str:
    text = _string(value, contract, path, maximum=40)
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ContractValidationError(
            "invalid_contract", contract, path, "must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        _fail(contract, path, "must include a timezone")
    return text


def _uri(
    value: Any,
    contract: str,
    path: str,
    *,
    schemes: set[str] = frozenset({"workspace", "artifact"}),
) -> str:
    text = _string(value, contract, path, maximum=512)
    decoded = text
    for _ in range(16):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    else:
        _fail(contract, path, "must not contain nested URI encoding")

    parsed = urlsplit(decoded)
    if (
        parsed.scheme not in schemes
        or not _URI_NAMESPACE.fullmatch(parsed.netloc)
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.query
        or parsed.fragment
    ):
        _fail(contract, path, f"must be a {', '.join(sorted(schemes))} URI")
    path_parts = parsed.path.split("/")
    if "\\" in decoded or any(part in {".", ".."} for part in path_parts):
        _fail(contract, path, "must not contain path traversal or system separators")
    if any(re.fullmatch(r"[A-Za-z]:", part) for part in path_parts):
        _fail(contract, path, "must not expose an absolute system path")
    return text


def _unique_uuids(
    value: Any,
    contract: str,
    path: str,
    *,
    minimum: int = 0,
) -> list[str]:
    items = _list(value, contract, path, minimum=minimum)
    result = [_uuid(item, contract, f"{path}[{index}]") for index, item in enumerate(items)]
    if len(result) != len(set(result)):
        _fail(contract, path, "must not contain duplicate IDs")
    return result


def _asset_ref(value: Any, contract: str, path: str, *, nullable: bool = True) -> None:
    if value is None and nullable:
        return
    item = _object(value, contract, path)
    _fields(
        item,
        contract,
        path,
        required={"asset_id", "uri", "content_hash"},
    )
    _uuid(item["asset_id"], contract, f"{path}.asset_id")
    _uri(item["uri"], contract, f"{path}.uri", schemes={"workspace"})
    _hash(item["content_hash"], contract, f"{path}.content_hash")


def _position(value: Any, contract: str, path: str) -> None:
    if value is None:
        return
    item = _object(value, contract, path)
    _fields(item, contract, path, required={"x", "y", "anchor"})
    _number(item["x"], contract, f"{path}.x", minimum=-1.0, maximum=1.0)
    _number(item["y"], contract, f"{path}.y", minimum=-1.0, maximum=1.0)
    _identifier(item["anchor"], contract, f"{path}.anchor")


def _private_data_guard(value: Any, contract: str, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_PRIVATE_KEYS:
                _fail(contract, f"{path}.{key}", "private fields are forbidden")
            _private_data_guard(item, contract, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _private_data_guard(item, contract, f"{path}[{index}]")


def _base(payload: Any, contract: str) -> dict[str, Any]:
    value = _object(payload, contract, "$")
    version = value.get("schema_version")
    if version != CONTRACT_VERSION:
        raise ContractValidationError(
            "unsupported_contract_version",
            contract,
            "$.schema_version",
            f"unsupported {contract} contract version",
            details={"received": version, "supported": [CONTRACT_VERSION]},
        )
    _private_data_guard(value, contract)
    return value


def _script_release(payload: Any) -> None:
    contract = "ScriptRelease"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "id",
            "work_id",
            "display_version",
            "manifest_uri",
            "content_hash",
            "canon_revision_id",
            "writing_pack_version",
            "source_revision_ids",
            "gate_snapshot_ids",
            "released_by",
            "released_at",
        },
    )
    _uuid(value["id"], contract, "$.id")
    _uuid(value["work_id"], contract, "$.work_id")
    _string(value["display_version"], contract, "$.display_version", maximum=40)
    _uri(value["manifest_uri"], contract, "$.manifest_uri", schemes={"workspace"})
    _hash(value["content_hash"], contract, "$.content_hash")
    _uuid(value["canon_revision_id"], contract, "$.canon_revision_id")
    _string(value["writing_pack_version"], contract, "$.writing_pack_version", maximum=120)
    _unique_uuids(value["source_revision_ids"], contract, "$.source_revision_ids", minimum=1)
    _unique_uuids(value["gate_snapshot_ids"], contract, "$.gate_snapshot_ids", minimum=1)
    _string(value["released_by"], contract, "$.released_by", maximum=128)
    _timestamp(value["released_at"], contract, "$.released_at")


def _production_request(payload: Any) -> None:
    contract = "ProductionRequest"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "request_id",
            "script_release",
            "script_manifest_version",
            "asset_manifest",
            "production_policy",
            "idempotency_key",
        },
    )
    _uuid(value["request_id"], contract, "$.request_id")
    release = _object(value["script_release"], contract, "$.script_release")
    _fields(
        release,
        contract,
        "$.script_release",
        required={"id", "display_version", "content_hash", "manifest_uri"},
    )
    _uuid(release["id"], contract, "$.script_release.id")
    _string(release["display_version"], contract, "$.script_release.display_version", maximum=40)
    _hash(release["content_hash"], contract, "$.script_release.content_hash")
    _uri(release["manifest_uri"], contract, "$.script_release.manifest_uri", schemes={"workspace"})
    if value["script_manifest_version"] != CONTRACT_VERSION:
        _fail(contract, "$.script_manifest_version", "unsupported script manifest version")
    manifest = _object(value["asset_manifest"], contract, "$.asset_manifest")
    _fields(
        manifest,
        contract,
        "$.asset_manifest",
        required={"id", "version", "content_hash", "uri"},
    )
    _uuid(manifest["id"], contract, "$.asset_manifest.id")
    if manifest["version"] != CONTRACT_VERSION:
        _fail(contract, "$.asset_manifest.version", "unsupported asset manifest version")
    _hash(manifest["content_hash"], contract, "$.asset_manifest.content_hash")
    _uri(manifest["uri"], contract, "$.asset_manifest.uri", schemes={"workspace"})
    policy = _object(value["production_policy"], contract, "$.production_policy")
    _fields(
        policy,
        contract,
        "$.production_policy",
        required={"asset_reference_mode", "allow_placeholders", "target"},
    )
    _enum(
        policy["asset_reference_mode"],
        {"whitelist_only"},
        contract,
        "$.production_policy.asset_reference_mode",
    )
    _boolean(policy["allow_placeholders"], contract, "$.production_policy.allow_placeholders")
    _identifier(policy["target"], contract, "$.production_policy.target")
    _hash(value["idempotency_key"], contract, "$.idempotency_key")
    if idempotency_key_for_request(value) != value["idempotency_key"]:
        _fail(contract, "$.idempotency_key", "does not match the canonical request envelope")


def _performance_draft(payload: Any) -> None:
    contract = "PerformanceDraft"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "id",
            "revision_id",
            "source",
            "content_hash",
            "provenance",
            "review_status",
            "scenes",
            "created_at",
        },
    )
    _uuid(value["id"], contract, "$.id")
    _uuid(value["revision_id"], contract, "$.revision_id")
    _hash(value["content_hash"], contract, "$.content_hash")
    _enum(
        value["review_status"],
        {"draft", "pending_review", "approved", "rejected"},
        contract,
        "$.review_status",
    )
    _timestamp(value["created_at"], contract, "$.created_at")

    source = _object(value["source"], contract, "$.source")
    _fields(
        source,
        contract,
        "$.source",
        required={"release_id", "release_hash", "scene_revisions"},
    )
    _uuid(source["release_id"], contract, "$.source.release_id")
    _hash(source["release_hash"], contract, "$.source.release_hash")
    scene_revisions = _list(source["scene_revisions"], contract, "$.source.scene_revisions", minimum=1)
    source_scene_ids: set[str] = set()
    for index, raw in enumerate(scene_revisions):
        path = f"$.source.scene_revisions[{index}]"
        item = _object(raw, contract, path)
        _fields(item, contract, path, required={"scene_id", "revision_id", "content_hash"})
        scene_id = _uuid(item["scene_id"], contract, f"{path}.scene_id")
        _uuid(item["revision_id"], contract, f"{path}.revision_id")
        _hash(item["content_hash"], contract, f"{path}.content_hash")
        if scene_id in source_scene_ids:
            _fail(contract, f"{path}.scene_id", "duplicate source Scene ID")
        source_scene_ids.add(scene_id)

    provenance = _object(value["provenance"], contract, "$.provenance")
    _fields(
        provenance,
        contract,
        "$.provenance",
        required={"created_by", "input_hash"},
        optional={"proposal_id", "attempt_id", "adapter_id"},
    )
    created_by = _enum(
        provenance["created_by"],
        {"user", "proposal_acceptance", "importer", "system"},
        contract,
        "$.provenance.created_by",
    )
    _hash(provenance["input_hash"], contract, "$.provenance.input_hash")
    for key in ("proposal_id", "attempt_id"):
        if key in provenance:
            _uuid(provenance[key], contract, f"$.provenance.{key}")
    if "adapter_id" in provenance:
        _identifier(provenance["adapter_id"], contract, "$.provenance.adapter_id")
    if created_by == "proposal_acceptance" and "proposal_id" not in provenance:
        _fail(contract, "$.provenance.proposal_id", "proposal acceptance requires proposal_id")

    scenes = _list(value["scenes"], contract, "$.scenes", minimum=1)
    seen_scene_ids: set[str] = set()
    seen_node_ids: set[str] = set()
    seen_line_ids: set[str] = set()
    seen_choice_group_ids: set[str] = set()
    defined_branches: dict[str, str] = {}
    branch_references: list[tuple[str, str, str]] = []
    for scene_index, raw_scene in enumerate(scenes):
        scene_path = f"$.scenes[{scene_index}]"
        scene = _object(raw_scene, contract, scene_path)
        _fields(scene, contract, scene_path, required={"scene_id", "nodes"})
        scene_id = _uuid(scene["scene_id"], contract, f"{scene_path}.scene_id")
        if scene_id not in source_scene_ids:
            _fail(contract, f"{scene_path}.scene_id", "Scene is not present in source.scene_revisions")
        if scene_id in seen_scene_ids:
            _fail(contract, f"{scene_path}.scene_id", "duplicate Scene ID")
        seen_scene_ids.add(scene_id)
        nodes = _list(scene["nodes"], contract, f"{scene_path}.nodes", minimum=1)
        node_entries: list[tuple[dict[str, Any], str]] = []
        scene_node_ids: set[str] = set()
        for node_index, raw_node in enumerate(nodes):
            node_path = f"{scene_path}.nodes[{node_index}]"
            node = _object(raw_node, contract, node_path)
            _fields(
                node,
                contract,
                node_path,
                required={"node_id", "kind"},
                optional={"performance_line", "choice_group"},
            )
            node_id = _uuid(node["node_id"], contract, f"{node_path}.node_id")
            if node_id in seen_node_ids:
                _fail(contract, f"{node_path}.node_id", "duplicate node ID")
            seen_node_ids.add(node_id)
            scene_node_ids.add(node_id)
            node_entries.append((node, node_path))

        for node, node_path in node_entries:
            kind = _enum(
                node["kind"],
                {"performance_line", "choice_group"},
                contract,
                f"{node_path}.kind",
            )
            if kind == "performance_line":
                if "performance_line" not in node or "choice_group" in node:
                    _fail(contract, node_path, "performance_line node has invalid payload")
                _performance_line(
                    node["performance_line"],
                    contract,
                    f"{node_path}.performance_line",
                    seen_line_ids,
                    branch_references,
                    scene_id,
                )
            else:
                if "choice_group" not in node or "performance_line" in node:
                    _fail(contract, node_path, "choice_group node has invalid payload")
                _choice_group(
                    node["choice_group"],
                    contract,
                    f"{node_path}.choice_group",
                    seen_choice_group_ids,
                    defined_branches,
                    scene_id,
                    scene_node_ids,
                )

    for branch_id, scene_id, path in branch_references:
        defined_scene = defined_branches.get(branch_id)
        if defined_scene is None:
            _fail(contract, path, "branch_id must reference a choice option")
        if defined_scene != scene_id:
            _fail(contract, path, "branch_id must reference a choice option in the same Scene")
    if contract_content_hash(contract, value) != value["content_hash"]:
        _fail(contract, "$.content_hash", "does not match the canonical PerformanceDraft envelope")


def _performance_line(
    raw: Any,
    contract: str,
    path: str,
    seen_line_ids: set[str],
    branch_references: list[tuple[str, str, str]],
    scene_id: str,
) -> None:
    line = _object(raw, contract, path)
    _fields(
        line,
        contract,
        path,
        required={
            "line_id",
            "content_kind",
            "text",
            "location",
            "cast_state",
            "media",
            "extra_instructions",
            "duration_ms",
        },
        optional={"branch_id", "speaker_id", "highlighted_character_id"},
    )
    line_id = _uuid(line["line_id"], contract, f"{path}.line_id")
    if line_id in seen_line_ids:
        _fail(contract, f"{path}.line_id", "duplicate performance line ID")
    seen_line_ids.add(line_id)
    if "branch_id" in line:
        branch_id = _uuid(line["branch_id"], contract, f"{path}.branch_id")
        branch_references.append((branch_id, scene_id, f"{path}.branch_id"))
    _enum(
        line["content_kind"],
        {"dialogue", "narration", "stage_direction"},
        contract,
        f"{path}.content_kind",
    )
    _string(line["text"], contract, f"{path}.text", minimum=0, maximum=20000)
    _string(line["location"], contract, f"{path}.location", maximum=160)
    for key in ("speaker_id", "highlighted_character_id"):
        if key in line:
            _uuid(line[key], contract, f"{path}.{key}")
    cast_state = _list(line["cast_state"], contract, f"{path}.cast_state", maximum=64)
    cast_ids: set[str] = set()
    for index, raw_cast in enumerate(cast_state):
        cast_path = f"{path}.cast_state[{index}]"
        cast = _object(raw_cast, contract, cast_path)
        _fields(
            cast,
            contract,
            cast_path,
            required={
                "character_id",
                "asset_id",
                "face",
                "start_position",
                "end_position",
                "speaking_status",
                "presence_action",
                "action",
                "effect",
                "form_override",
            },
        )
        character_id = _uuid(cast["character_id"], contract, f"{cast_path}.character_id")
        if character_id in cast_ids:
            _fail(contract, f"{cast_path}.character_id", "duplicate character in full cast state")
        cast_ids.add(character_id)
        if cast["asset_id"] is not None:
            _uuid(cast["asset_id"], contract, f"{cast_path}.asset_id")
        _nullable_string(cast["face"], contract, f"{cast_path}.face", maximum=120)
        _position(cast["start_position"], contract, f"{cast_path}.start_position")
        _position(cast["end_position"], contract, f"{cast_path}.end_position")
        _enum(
            cast["speaking_status"],
            {"speaker", "highlighted", "present"},
            contract,
            f"{cast_path}.speaking_status",
        )
        _enum(
            cast["presence_action"],
            {"none", "enter", "exit"},
            contract,
            f"{cast_path}.presence_action",
        )
        for key in ("action", "effect", "form_override"):
            _nullable_string(cast[key], contract, f"{cast_path}.{key}", maximum=160)

    media = _object(line["media"], contract, f"{path}.media")
    _fields(
        media,
        contract,
        f"{path}.media",
        required={
            "background",
            "popup",
            "bgm",
            "voice",
            "sound_effects",
            "background_effect",
            "transition",
        },
    )
    for key in ("background", "popup", "bgm", "voice"):
        _asset_ref(media[key], contract, f"{path}.media.{key}")
    effects = _list(media["sound_effects"], contract, f"{path}.media.sound_effects", maximum=16)
    for index, effect in enumerate(effects):
        _asset_ref(effect, contract, f"{path}.media.sound_effects[{index}]", nullable=False)
    for key in ("background_effect", "transition"):
        _nullable_string(media[key], contract, f"{path}.media.{key}", maximum=160)
    instructions = _list(
        line["extra_instructions"], contract, f"{path}.extra_instructions", maximum=32
    )
    for index, instruction in enumerate(instructions):
        _string(instruction, contract, f"{path}.extra_instructions[{index}]", maximum=500)
    _integer(line["duration_ms"], contract, f"{path}.duration_ms", maximum=3_600_000)


def _choice_group(
    raw: Any,
    contract: str,
    path: str,
    seen_choice_group_ids: set[str],
    defined_branches: dict[str, str],
    scene_id: str,
    scene_node_ids: set[str],
) -> None:
    group = _object(raw, contract, path)
    _fields(group, contract, path, required={"choice_group_id", "prompt", "options"})
    group_id = _uuid(group["choice_group_id"], contract, f"{path}.choice_group_id")
    if group_id in seen_choice_group_ids:
        _fail(contract, f"{path}.choice_group_id", "duplicate choice group ID")
    seen_choice_group_ids.add(group_id)
    _string(group["prompt"], contract, f"{path}.prompt", maximum=1000)
    options = _list(group["options"], contract, f"{path}.options", minimum=2, maximum=16)
    for index, raw_option in enumerate(options):
        option_path = f"{path}.options[{index}]"
        option = _object(raw_option, contract, option_path)
        _fields(option, contract, option_path, required={"branch_id", "label", "target_node_id"})
        branch_id = _uuid(option["branch_id"], contract, f"{option_path}.branch_id")
        if branch_id in defined_branches:
            _fail(contract, f"{option_path}.branch_id", "duplicate branch ID")
        defined_branches[branch_id] = scene_id
        _string(option["label"], contract, f"{option_path}.label", maximum=500)
        target_node_id = _uuid(option["target_node_id"], contract, f"{option_path}.target_node_id")
        if target_node_id not in scene_node_ids:
            _fail(contract, f"{option_path}.target_node_id", "target node must exist in the same Scene")


def _asset_manifest(payload: Any) -> None:
    contract = "AssetManifest"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={"schema_version", "id", "content_hash", "created_at", "assets"},
    )
    _uuid(value["id"], contract, "$.id")
    _hash(value["content_hash"], contract, "$.content_hash")
    _timestamp(value["created_at"], contract, "$.created_at")
    assets = _list(value["assets"], contract, "$.assets", maximum=100000)
    asset_ids: set[str] = set()
    for index, raw_asset in enumerate(assets):
        path = f"$.assets[{index}]"
        asset = _object(raw_asset, contract, path)
        _fields(
            asset,
            contract,
            path,
            required={
                "asset_id",
                "kind",
                "uri",
                "content_hash",
                "display_name",
                "media_type",
                "metadata",
            },
        )
        asset_id = _uuid(asset["asset_id"], contract, f"{path}.asset_id")
        if asset_id in asset_ids:
            _fail(contract, f"{path}.asset_id", "duplicate asset ID")
        asset_ids.add(asset_id)
        _enum(
            asset["kind"],
            {"character", "background", "popup", "bgm", "voice", "sound", "video", "font"},
            contract,
            f"{path}.kind",
        )
        _uri(asset["uri"], contract, f"{path}.uri", schemes={"workspace"})
        _hash(asset["content_hash"], contract, f"{path}.content_hash")
        _string(asset["display_name"], contract, f"{path}.display_name", maximum=200)
        _string(asset["media_type"], contract, f"{path}.media_type", maximum=120)
        metadata = _object(asset["metadata"], contract, f"{path}.metadata")
        try:
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContractValidationError(
                "invalid_contract", contract, f"{path}.metadata", "metadata must be JSON-safe"
            ) from exc
    if contract_content_hash(contract, value) != value["content_hash"]:
        _fail(contract, "$.content_hash", "does not match the canonical AssetManifest envelope")


def _adapter_capabilities(payload: Any) -> None:
    contract = "AdapterCapabilities"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "adapter_api_version",
            "adapter_id",
            "engine_id",
            "engine_version",
            "capabilities",
            "supported_script_manifest_versions",
            "supported_performance_draft_versions",
            "supported_asset_manifest_versions",
            "supported_build_bundle_versions",
            "targets",
        },
    )
    if value["adapter_api_version"] != CONTRACT_VERSION:
        _fail(contract, "$.adapter_api_version", "unsupported adapter API version")
    for key in ("adapter_id", "engine_id"):
        _identifier(value[key], contract, f"$.{key}")
    _string(value["engine_version"], contract, "$.engine_version", maximum=120)
    allowed_capabilities = {
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
    capabilities = _list(value["capabilities"], contract, "$.capabilities")
    for index, capability in enumerate(capabilities):
        _enum(capability, allowed_capabilities, contract, f"$.capabilities[{index}]")
    if len(capabilities) != len(set(capabilities)):
        _fail(contract, "$.capabilities", "must not contain duplicates")
    for key in (
        "supported_script_manifest_versions",
        "supported_performance_draft_versions",
        "supported_asset_manifest_versions",
        "supported_build_bundle_versions",
    ):
        versions = _list(value[key], contract, f"$.{key}")
        for index, version in enumerate(versions):
            if version != CONTRACT_VERSION:
                _fail(contract, f"$.{key}[{index}]", "unsupported declared contract version")
    targets = _list(value["targets"], contract, "$.targets")
    for index, target in enumerate(targets):
        _identifier(target, contract, f"$.targets[{index}]")


def _build_bundle(payload: Any) -> None:
    contract = "BuildBundle"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "id",
            "request_id",
            "performance_draft_id",
            "build_bundle_ref",
            "input_hashes",
            "producer",
            "target",
            "deliverables",
            "warnings",
            "created_at",
        },
    )
    for key in ("id", "request_id", "performance_draft_id"):
        _uuid(value[key], contract, f"$.{key}")
    _uri(value["build_bundle_ref"], contract, "$.build_bundle_ref", schemes={"artifact"})
    hashes = _object(value["input_hashes"], contract, "$.input_hashes")
    _fields(
        hashes,
        contract,
        "$.input_hashes",
        required={"script_release", "performance_draft", "asset_manifest"},
    )
    for key in hashes:
        _hash(hashes[key], contract, f"$.input_hashes.{key}")
    producer = _object(value["producer"], contract, "$.producer")
    _fields(producer, contract, "$.producer", required={"adapter_id", "engine_id", "engine_version"})
    _identifier(producer["adapter_id"], contract, "$.producer.adapter_id")
    _identifier(producer["engine_id"], contract, "$.producer.engine_id")
    _string(producer["engine_version"], contract, "$.producer.engine_version", maximum=120)
    _identifier(value["target"], contract, "$.target")
    deliverables = _list(value["deliverables"], contract, "$.deliverables", minimum=1)
    artifact_ids: set[str] = set()
    for index, raw in enumerate(deliverables):
        path = f"$.deliverables[{index}]"
        item = _object(raw, contract, path)
        _fields(
            item,
            contract,
            path,
            required={"artifact_id", "kind", "uri", "content_hash", "media_type", "size_bytes"},
        )
        artifact_id = _uuid(item["artifact_id"], contract, f"{path}.artifact_id")
        if artifact_id in artifact_ids:
            _fail(contract, f"{path}.artifact_id", "duplicate deliverable Artifact ID")
        artifact_ids.add(artifact_id)
        _enum(item["kind"], {"aap", "video", "preview", "manifest", "log"}, contract, f"{path}.kind")
        _uri(item["uri"], contract, f"{path}.uri")
        _hash(item["content_hash"], contract, f"{path}.content_hash")
        _string(item["media_type"], contract, f"{path}.media_type", maximum=120)
        _integer(item["size_bytes"], contract, f"{path}.size_bytes")
    warnings = _list(value["warnings"], contract, "$.warnings", maximum=1000)
    for index, raw in enumerate(warnings):
        path = f"$.warnings[{index}]"
        item = _object(raw, contract, path)
        _fields(item, contract, path, required={"code", "message", "scope_refs"})
        _ERROR_CODE.fullmatch(_string(item["code"], contract, f"{path}.code", maximum=80)) or _fail(
            contract, f"{path}.code", "must be a stable uppercase code"
        )
        _string(item["message"], contract, f"{path}.message", maximum=500)
        refs = _list(item["scope_refs"], contract, f"{path}.scope_refs", maximum=100)
        for ref_index, ref in enumerate(refs):
            _string(ref, contract, f"{path}.scope_refs[{ref_index}]", maximum=160)
    _timestamp(value["created_at"], contract, "$.created_at")


def _production_event(payload: Any) -> None:
    contract = "ProductionEvent"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "event_id",
            "kind",
            "run_id",
            "work_item_id",
            "attempt_id",
            "sequence",
            "timestamp",
        },
        optional={"request_id", "progress", "artifact_refs", "message"},
    )
    for key in ("event_id", "run_id", "work_item_id", "attempt_id"):
        _uuid(value[key], contract, f"$.{key}")
    if "request_id" in value:
        _uuid(value["request_id"], contract, "$.request_id")
    kind = _enum(
        value["kind"],
        {
            "operation_started",
            "stage_progress",
            "artifact_created",
            "waiting_user",
            "operation_succeeded",
            "operation_failed",
            "operation_cancelled",
        },
        contract,
        "$.kind",
    )
    _integer(value["sequence"], contract, "$.sequence", minimum=1)
    _timestamp(value["timestamp"], contract, "$.timestamp")
    if "progress" in value:
        progress = _object(value["progress"], contract, "$.progress")
        _fields(progress, contract, "$.progress", required={"percent", "stage"})
        _number(progress["percent"], contract, "$.progress.percent", minimum=0, maximum=100)
        _identifier(progress["stage"], contract, "$.progress.stage")
    refs = value.get("artifact_refs", [])
    refs = _list(refs, contract, "$.artifact_refs", maximum=100)
    for index, ref in enumerate(refs):
        _uri(ref, contract, f"$.artifact_refs[{index}]", schemes={"artifact"})
    if kind in {"artifact_created", "operation_succeeded"} and not refs:
        _fail(contract, "$.artifact_refs", f"{kind} requires a verified Artifact reference")
    if "message" in value:
        _string(value["message"], contract, "$.message", maximum=500)


def _api_error(payload: Any) -> None:
    contract = "ApiError"
    value = _base(payload, contract)
    _fields(
        value,
        contract,
        "$",
        required={
            "schema_version",
            "code",
            "category",
            "message",
            "detail_ref",
            "retryability",
            "scope_refs",
            "caused_by_attempt",
        },
    )
    code = _string(value["code"], contract, "$.code", maximum=80)
    if not _ERROR_CODE.fullmatch(code):
        _fail(contract, "$.code", "must be a stable uppercase code")
    _enum(
        value["category"],
        {
            "invalid_input",
            "validation",
            "model_transient",
            "model_output",
            "compiler",
            "environment",
            "cancelled",
            "conflict",
            "internal",
        },
        contract,
        "$.category",
    )
    _string(value["message"], contract, "$.message", maximum=500)
    if value["detail_ref"] is not None:
        _uri(value["detail_ref"], contract, "$.detail_ref", schemes={"artifact"})
    _enum(
        value["retryability"],
        {"never", "after_user_action", "automatic", "new_work_item"},
        contract,
        "$.retryability",
    )
    refs = _list(value["scope_refs"], contract, "$.scope_refs", maximum=100)
    for index, ref in enumerate(refs):
        _string(ref, contract, f"$.scope_refs[{index}]", maximum=160)
    if value["caused_by_attempt"] is not None:
        _uuid(value["caused_by_attempt"], contract, "$.caused_by_attempt")


_VALIDATORS: dict[str, Callable[[Any], None]] = {
    "ScriptRelease": _script_release,
    "ProductionRequest": _production_request,
    "PerformanceDraft": _performance_draft,
    "AssetManifest": _asset_manifest,
    "AdapterCapabilities": _adapter_capabilities,
    "BuildBundle": _build_bundle,
    "ProductionEvent": _production_event,
    "ApiError": _api_error,
}


def validate_contract(contract: str, payload: Any) -> dict[str, Any]:
    validator = _VALIDATORS.get(contract)
    if validator is None:
        raise ContractValidationError(
            "unknown_contract",
            str(contract),
            "$",
            "unknown HaloCue contract",
            details={"supported": list(CONTRACT_NAMES)},
        )
    validator(payload)
    return json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
