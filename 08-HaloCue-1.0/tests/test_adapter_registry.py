from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from halocue_production.adapters.base import (
    AdapterBase,
    AdapterRequest,
    AdapterResult,
    BuildBundleRef,
    DraftRef,
)
from halocue_production.adapters.registry import AdapterRegistry
from halocue_production.errors import ProductionError


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"


def _capabilities(adapter_id: str, target: str, capabilities: list[str]) -> dict:
    return {
        "schema_version": "1.0",
        "adapter_api_version": "1.0",
        "adapter_id": adapter_id,
        "engine_id": "test-engine",
        "engine_version": "1.0.0",
        "capabilities": capabilities,
        "supported_script_manifest_versions": ["1.0"],
        "supported_performance_draft_versions": ["1.0"],
        "supported_asset_manifest_versions": ["1.0"],
        "supported_build_bundle_versions": ["1.0"],
        "targets": [target],
    }


class StubAdapter(AdapterBase):
    def __init__(self, payload: dict):
        self._payload = copy.deepcopy(payload)

    def capabilities(self) -> dict:
        return copy.deepcopy(self._payload)

    def preflight(self, request: AdapterRequest) -> AdapterResult:
        self.require_capability("preflight")
        return AdapterResult()

    def create_performance_draft(
        self, request: AdapterRequest, scope: dict | None = None
    ) -> AdapterResult:
        self.require_capability("create_performance_draft")
        return AdapterResult()

    def update_performance_draft(
        self, draft_ref: DraftRef, patch: dict
    ) -> AdapterResult:
        self.require_capability("update_performance_draft")
        return AdapterResult()

    def validate(
        self, request: AdapterRequest, draft_ref: DraftRef
    ) -> AdapterResult:
        self.require_capability("validate")
        return AdapterResult()

    def compile(
        self, request: AdapterRequest, draft_ref: DraftRef
    ) -> AdapterResult:
        self.require_capability("compile_aap")
        return AdapterResult()

    def render(
        self, request: AdapterRequest, draft_ref: DraftRef, options: dict | None = None
    ) -> AdapterResult:
        self.require_capability("render_preview")
        return AdapterResult()

    def cancel(self, attempt_ref: str) -> AdapterResult:
        self.require_capability("cancel")
        return AdapterResult(cancelled=True)


def _adapter(adapter_id: str, target: str) -> StubAdapter:
    return StubAdapter(
        _capabilities(
            adapter_id,
            target,
            ["preflight", "create_performance_draft", "update_performance_draft", "validate", "cancel"],
        )
    )


def _formal_request() -> dict:
    return json.loads(
        (EXAMPLE_DIR / "production-request-1.1.json").read_text(encoding="utf-8")
    )


def test_adapter_request_and_refs_keep_formal_identity_and_stable_uris():
    request = AdapterRequest(_formal_request())
    assert request.request_id == "66666666-6666-4666-8666-666666666666"
    assert request.target == "pc_aap"
    assert request.input_hashes["script_release"].startswith("sha256:")

    draft = DraftRef(
        draft_id="88888888-8888-4888-8888-888888888888",
        revision_id="99999999-9999-4999-8999-999999999999",
        artifact_uri="artifact://drafts/88888888-8888-4888-8888-888888888888",
        content_hash="sha256:" + "8" * 64,
    )
    bundle = BuildBundleRef(
        bundle_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        artifact_uri="artifact://bundles/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        content_hash="sha256:" + "a" * 64,
        target=request.target,
    )
    result = AdapterResult(bundle_ref=bundle, artifact_refs=(draft.uri,))

    assert result.artifact_uri == bundle.uri
    assert result.cancelled is False
    with pytest.raises(AttributeError):
        request.target = "other"


def test_registry_maps_invalid_capability_payload_to_structured_error():
    invalid = _capabilities("bad-adapter", "pc_aap", ["render_video"])
    with pytest.raises(ProductionError) as raised:
        AdapterRegistry([StubAdapter(invalid)])

    assert raised.value.code == "adapter_capabilities_invalid"
    assert raised.value.details["path"] == "$.capabilities[0]"


def test_registry_routes_unique_targets():
    aa = _adapter("aa-compat", "pc_aap")
    storyforge = _adapter("storyforge-local", "storyforge_preview")
    registry = AdapterRegistry([aa, storyforge])

    assert registry.for_target("pc_aap").capabilities()["adapter_id"] == "aa-compat"
    assert (
        registry.for_target("storyforge_preview").capabilities()["adapter_id"]
        == "storyforge-local"
    )
    assert [item["adapter_id"] for item in registry.all_capabilities()] == [
        "aa-compat",
        "storyforge-local",
    ]


def test_registry_rejects_duplicate_adapter_id():
    with pytest.raises(ProductionError) as raised:
        AdapterRegistry([_adapter("same-id", "pc_aap"), _adapter("same-id", "storyforge_preview")])

    assert raised.value.code == "adapter_id_conflict"


def test_registry_rejects_ambiguous_target():
    with pytest.raises(ProductionError) as raised:
        AdapterRegistry([_adapter("first", "pc_aap"), _adapter("second", "pc_aap")])

    assert raised.value.code == "adapter_target_conflict"
    assert raised.value.details["target"] == "pc_aap"


def test_missing_capability_has_stable_error_and_no_artifact():
    adapter = _adapter("storyforge-local", "storyforge_preview")
    request = object.__new__(AdapterRequest)

    with pytest.raises(ProductionError) as raised:
        adapter.compile(request, object())

    assert raised.value.code == "adapter_capability_unavailable"
    assert raised.value.details == {
        "adapter_id": "storyforge-local",
        "target": "storyforge_preview",
        "operation": "compile_aap",
    }


def test_capabilities_are_returned_as_copies():
    adapter = _adapter("copy-test", "pc_aap")
    registry = AdapterRegistry([adapter])
    payload = registry.all_capabilities()[0]
    payload["capabilities"].clear()

    assert "preflight" in registry.for_target("pc_aap").capabilities()["capabilities"]


def test_capability_fixture_files_are_json_objects():
    for filename in (
        "adapter-capabilities-aa-1.0.json",
        "adapter-capabilities-storyforge-1.0.json",
    ):
        payload = json.loads((EXAMPLE_DIR / filename).read_text(encoding="utf-8"))
        assert payload["schema_version"] == "1.0"
