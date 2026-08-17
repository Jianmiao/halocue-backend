from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from halocue_production.contracts import (
    CONTRACT_NAMES,
    CONTRACT_VERSIONS,
    ContractValidationError,
    canonical_json_bytes,
    contract_content_hash,
    idempotency_key_for_request,
    validate_contract,
)


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"
COMPATIBILITY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "script_release_handoff_1_0.json"
)
EXAMPLES = {
    "ScriptRelease": "script-release-1.0.json",
    "ProductionRequest": "production-request-1.0.json",
    "PerformanceDraft": "performance-draft-1.0.json",
    "AssetManifest": "asset-manifest-1.0.json",
    "AdapterCapabilities": "adapter-capabilities-1.0.json",
    "BuildBundle": "build-bundle-1.0.json",
    "ProductionEvent": "production-event-1.0.json",
    "ApiError": "api-error-1.0.json",
}
VERSIONED_EXAMPLES = {
    ("ScriptRelease", "1.1"): "script-release-1.1.json",
    ("ProductionRequest", "1.1"): "production-request-1.1.json",
}


def example(contract: str) -> dict:
    return json.loads((EXAMPLE_DIR / EXAMPLES[contract]).read_text(encoding="utf-8"))


def versioned_example(contract: str, version: str) -> dict:
    filename = VERSIONED_EXAMPLES[(contract, version)]
    return json.loads((EXAMPLE_DIR / filename).read_text(encoding="utf-8"))


@pytest.mark.parametrize("contract", CONTRACT_NAMES)
def test_formal_contract_example_round_trips_and_validates(contract):
    payload = example(contract)

    normalized = validate_contract(contract, payload)

    assert normalized == payload
    assert json.loads(json.dumps(normalized, ensure_ascii=False)) == payload
    assert normalized is not payload


@pytest.mark.parametrize(("contract", "version"), VERSIONED_EXAMPLES)
def test_additive_contract_example_round_trips_and_validates(contract, version):
    payload = versioned_example(contract, version)

    normalized = validate_contract(contract, payload)

    assert normalized == payload
    assert normalized is not payload


@pytest.mark.parametrize("contract", CONTRACT_NAMES)
def test_formal_contract_rejects_unknown_version_consistently(contract):
    payload = example(contract)
    payload["schema_version"] = "2.0"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract(contract, payload)

    assert raised.value.code == "unsupported_contract_version"
    assert raised.value.contract == contract
    assert raised.value.path == "$.schema_version"
    assert raised.value.details == {
        "received": "2.0",
        "supported": list(CONTRACT_VERSIONS[contract]),
    }


@pytest.mark.parametrize("contract", CONTRACT_NAMES)
def test_formal_contract_rejects_unknown_top_level_fields(contract):
    payload = example(contract)
    payload["unexpected"] = True

    with pytest.raises(ContractValidationError) as raised:
        validate_contract(contract, payload)

    assert raised.value.code == "invalid_contract"
    assert raised.value.contract == contract
    assert raised.value.path == "$"
    assert "unknown fields: unexpected" in str(raised.value)


@pytest.mark.parametrize(
    ("contract", "required_field"),
    [
        ("ScriptRelease", "id"),
        ("ProductionRequest", "request_id"),
        ("PerformanceDraft", "scenes"),
        ("AssetManifest", "assets"),
        ("AdapterCapabilities", "capabilities"),
        ("BuildBundle", "deliverables"),
        ("ProductionEvent", "attempt_id"),
        ("ApiError", "code"),
    ],
)
def test_formal_contract_rejects_missing_required_fields(contract, required_field):
    payload = example(contract)
    del payload[required_field]

    with pytest.raises(ContractValidationError) as raised:
        validate_contract(contract, payload)

    assert raised.value.code == "invalid_contract"
    assert raised.value.contract == contract
    assert raised.value.path == "$"
    assert required_field in str(raised.value)


def test_formal_contract_rejects_non_uuid_identity():
    payload = example("ScriptRelease")
    payload["id"] = "release-by-title"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", payload)

    assert raised.value.path == "$.id"
    assert "canonical UUID" in str(raised.value)


def test_formal_contract_rejects_noncanonical_uppercase_uuid():
    payload = example("ScriptRelease")
    payload["id"] = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", payload)

    assert raised.value.path == "$.id"
    assert "canonical UUID" in str(raised.value)


def test_formal_contract_rejects_bare_hash():
    payload = example("ScriptRelease")
    payload["content_hash"] = "a" * 64

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", payload)

    assert raised.value.path == "$.content_hash"
    assert "sha256:<64 lowercase hex>" in str(raised.value)


def test_script_release_1_1_requires_explicit_content_uri():
    payload = versioned_example("ScriptRelease", "1.1")
    del payload["content_uri"]

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", payload)

    assert raised.value.path == "$"
    assert "content_uri" in str(raised.value)


def test_script_release_1_0_remains_frozen_without_content_uri():
    payload = example("ScriptRelease")
    payload["content_uri"] = "workspace://releases/legacy/script.txt"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", payload)

    assert raised.value.path == "$"
    assert "unknown fields: content_uri" in str(raised.value)


def test_production_request_1_1_requires_release_1_1_and_display_name():
    payload = versioned_example("ProductionRequest", "1.1")
    del payload["production_display_name"]

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionRequest", payload)

    assert raised.value.path == "$"
    assert "production_display_name" in str(raised.value)

    payload = versioned_example("ProductionRequest", "1.1")
    payload["script_release"]["version"] = "1.0"
    payload["idempotency_key"] = idempotency_key_for_request(payload)
    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionRequest", payload)
    assert raised.value.path == "$.script_release.version"


def test_formal_contract_rejects_path_traversal_uri():
    payload = example("ProductionRequest")
    payload["asset_manifest"]["uri"] = "workspace://assets/../private/manifest.json"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionRequest", payload)

    assert raised.value.path == "$.asset_manifest.uri"
    assert "path traversal" in str(raised.value)


def test_formal_contract_rejects_encoded_path_traversal_uri():
    payload = example("ProductionRequest")
    payload["asset_manifest"]["uri"] = "workspace://assets/%252e%252e/private/manifest.json"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionRequest", payload)

    assert raised.value.path == "$.asset_manifest.uri"
    assert "path traversal" in str(raised.value)


def test_formal_contract_rejects_disguised_absolute_windows_path():
    payload = example("AssetManifest")
    payload["assets"][0]["uri"] = "workspace://assets/C:/Users/creator/alice.bundle"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("AssetManifest", payload)

    assert raised.value.path == "$.assets[0].uri"
    assert "absolute system path" in str(raised.value)


def test_formal_contract_rejects_private_path_fields_at_any_depth():
    payload = example("AssetManifest")
    payload["assets"][0]["metadata"]["physical_path"] = "C:\\private\\alice.bundle"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("AssetManifest", payload)

    assert raised.value.path == "$.assets[0].metadata.physical_path"
    assert "private fields are forbidden" in str(raised.value)


def test_performance_draft_requires_complete_cast_state_on_every_line():
    payload = example("PerformanceDraft")
    line = payload["scenes"][0]["nodes"][0]["performance_line"]
    del line["cast_state"]

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("PerformanceDraft", payload)

    assert raised.value.path.endswith(".performance_line")
    assert "cast_state" in str(raised.value)


def test_performance_draft_validation_does_not_mutate_payload():
    payload = example("PerformanceDraft")
    original = copy.deepcopy(payload)

    validate_contract("PerformanceDraft", payload)

    assert payload == original


def test_success_event_requires_verified_artifact_reference():
    payload = example("ProductionEvent")
    payload["kind"] = "operation_succeeded"
    payload["artifact_refs"] = []

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionEvent", payload)

    assert raised.value.path == "$.artifact_refs"
    assert "requires a verified Artifact reference" in str(raised.value)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [("category", "network"), ("retryability", "maybe")],
)
def test_api_error_rejects_unknown_decision_values(field, invalid_value):
    payload = example("ApiError")
    payload[field] = invalid_value

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ApiError", payload)

    assert raised.value.path == f"$.{field}"
    assert "must be one of" in str(raised.value)


def test_unknown_contract_name_is_rejected():
    with pytest.raises(ContractValidationError) as raised:
        validate_contract("Draft", {})

    assert raised.value.code == "unknown_contract"
    assert raised.value.details == {"supported": list(CONTRACT_NAMES)}


def test_canonical_json_hash_vectors_are_stable():
    assert canonical_json_bytes({"z": 1, "a": "爱丽丝"}) == (
        '{"a":"爱丽丝","z":1}'.encode("utf-8")
    )
    assert contract_content_hash("ScriptRelease", source_bytes=b"hello\n") == (
        "sha256:5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
    )


def test_formal_script_release_hash_matches_compatibility_source_bytes():
    formal = example("ScriptRelease")
    compatibility = json.loads(COMPATIBILITY_FIXTURE.read_text(encoding="utf-8"))
    source_bytes = compatibility["source"]["text"].encode("utf-8")

    assert contract_content_hash("ScriptRelease", source_bytes=source_bytes) == (
        formal["content_hash"]
    )
    assert compatibility["script_release"]["content_hash"] == formal[
        "content_hash"
    ].removeprefix("sha256:")


@pytest.mark.parametrize("contract", ["PerformanceDraft", "AssetManifest"])
def test_envelope_hash_vectors_match_examples(contract):
    payload = example(contract)

    assert contract_content_hash(contract, payload) == payload["content_hash"]


def test_production_request_idempotency_vector_matches_example():
    payload = example("ProductionRequest")

    assert idempotency_key_for_request(payload) == payload["idempotency_key"]


def test_production_request_1_1_idempotency_vector_matches_example():
    payload = versioned_example("ProductionRequest", "1.1")

    assert idempotency_key_for_request(payload) == payload["idempotency_key"]


def test_production_request_rejects_stale_idempotency_key():
    payload = example("ProductionRequest")
    payload["production_policy"]["target"] = "storyforge_video"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ProductionRequest", payload)

    assert raised.value.path == "$.idempotency_key"
    assert "canonical request envelope" in str(raised.value)


@pytest.mark.parametrize("contract", ["PerformanceDraft", "AssetManifest"])
def test_envelope_contract_rejects_stale_content_hash(contract):
    payload = example(contract)
    if contract == "PerformanceDraft":
        payload["scenes"][0]["nodes"][0]["performance_line"]["text"] = "changed"
    else:
        payload["assets"][0]["display_name"] = "changed"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract(contract, payload)

    assert raised.value.path == "$.content_hash"
    assert "canonical" in str(raised.value)


def test_performance_draft_rejects_undefined_branch_reference():
    payload = example("PerformanceDraft")
    payload["scenes"][0]["nodes"][0]["performance_line"]["branch_id"] = (
        "89898989-8989-4989-8989-898989898989"
    )

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("PerformanceDraft", payload)

    assert raised.value.path.endswith(".branch_id")
    assert "must reference a choice option" in str(raised.value)


def test_performance_draft_rejects_target_node_outside_scene():
    payload = example("PerformanceDraft")
    choice = payload["scenes"][0]["nodes"][1]["choice_group"]
    choice["options"][0]["target_node_id"] = "90909090-9090-4090-8090-909090909090"

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("PerformanceDraft", payload)

    assert raised.value.path.endswith(".target_node_id")
    assert "same Scene" in str(raised.value)


def test_performance_draft_rejects_duplicate_branch_definition():
    payload = example("PerformanceDraft")
    choice = payload["scenes"][0]["nodes"][1]["choice_group"]
    choice["options"][1]["branch_id"] = choice["options"][0]["branch_id"]

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("PerformanceDraft", payload)

    assert raised.value.path.endswith(".branch_id")
    assert "duplicate branch ID" in str(raised.value)


def test_compatibility_handoff_is_not_promoted_to_formal_script_release():
    compatibility = json.loads(COMPATIBILITY_FIXTURE.read_text(encoding="utf-8"))

    with pytest.raises(ContractValidationError) as raised:
        validate_contract("ScriptRelease", compatibility["script_release"])

    assert raised.value.contract == "ScriptRelease"
    assert raised.value.code == "invalid_contract"
    assert "missing required fields" in str(raised.value)
