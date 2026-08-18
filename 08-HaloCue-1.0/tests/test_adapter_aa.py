from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from halocue_production.adapters.aa import AzureArchiveAdapter
from halocue_production.adapters.base import AdapterRequest, BuildBundleRef
from halocue_production.adapters.drafts import PerformanceDraftStore
from halocue_production.artifacts import ArtifactStore
from halocue_production.contracts import idempotency_key_for_request, validate_contract
from halocue_production.errors import ProductionError
from halocue_production.runtime import RuntimeStore


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"


class FakeLegacyAdapter:
    def __init__(self, tmp_path: Path, *, compile_available: bool = True):
        self.tmp_path = tmp_path
        self.compile_available = compile_available
        self.calls: list[tuple] = []
        self.discarded: list[tuple[str, str]] = []

    def capabilities(self) -> dict:
        state = "available" if self.compile_available else "not_configured"
        return {
            "legacy_adapter": {"state": "available", "version": "0.9.3"},
            "compile": {"state": state},
            "install": {"state": "available"},
        }

    def inspect_script(self, text: str) -> dict:
        self.calls.append(("inspect_script", text))
        return {"line_count": len(text.splitlines()), "speakers": []}

    def create_performance_draft(self, **kwargs) -> dict:
        self.calls.append(("create_performance_draft", kwargs))
        return {
            "session": {
                "draft_token": "draft-private-token",
                "draft_version": 7,
            }
        }

    def validate(self, token: str) -> dict:
        self.calls.append(("validate", token))
        return {"ok": True, "review_ready": True, "blockers": []}

    def create_compile_snapshot(self, token: str, expected: int) -> str:
        self.calls.append(("create_compile_snapshot", token, expected))
        return "build-private-id"

    def execute_compile_cancellable(
        self, token: str, build_id: str, *, cancellation_probe
    ) -> dict:
        self.calls.append(("execute_compile", token, build_id))
        if cancellation_probe():
            raise ProductionError("operation_cancelled", "cancelled", status=409)
        return {
            "bundle_dir": str(self.tmp_path / "private-aa-bundle"),
            "aap_path": str(self.tmp_path / "private-aa-bundle" / "story.aap"),
            "build_id": build_id,
        }

    def discard_compile_output(self, token: str, build_id: str) -> None:
        self.discarded.append((token, build_id))


class FakeBundlePublisher:
    def __init__(self):
        self.calls: list[dict] = []

    def publish_aa(self, **kwargs) -> BuildBundleRef:
        self.calls.append(kwargs)
        return BuildBundleRef(
            bundle_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            artifact_uri="artifact://build-bundles/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            content_hash="sha256:" + "a" * 64,
            target="pc_aap",
        )


def _context(tmp_path: Path, *, approved: bool = True):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite3")
    artifacts = ArtifactStore(tmp_path / "workspace", runtime)
    payload = json.loads(
        (EXAMPLE_DIR / "production-request-1.1.json").read_text(encoding="utf-8")
    )
    text = "旁白: 开场\n爱丽丝: 你好\n"
    content = text.encode("utf-8")
    payload["script_release"]["content_hash"] = (
        "sha256:" + hashlib.sha256(content).hexdigest()
    )
    payload["idempotency_key"] = idempotency_key_for_request(payload)
    artifacts.commit_bytes(
        payload["script_release"]["content_uri"],
        content,
        kind="script-release-content",
        media_type="text/plain; charset=utf-8",
    )
    request = AdapterRequest(payload)
    drafts = PerformanceDraftStore(artifacts, runtime)
    draft = drafts.create_imported(request, "aa-compat")
    if approved:
        draft = drafts.update(draft, {"review_status": "approved"})
    return request, draft, drafts


def test_aa_capabilities_are_formal_and_conditional(tmp_path):
    request, draft, drafts = _context(tmp_path)
    publisher = FakeBundlePublisher()
    available = AzureArchiveAdapter(
        FakeLegacyAdapter(tmp_path), drafts, bundle_publisher=publisher
    )
    unavailable = AzureArchiveAdapter(
        FakeLegacyAdapter(tmp_path, compile_available=False),
        drafts,
        bundle_publisher=publisher,
    )

    capabilities = validate_contract("AdapterCapabilities", available.capabilities())
    assert "compile_aap" in capabilities["capabilities"]
    assert "install_aap" in capabilities["capabilities"]
    assert "compile_aap" not in unavailable.capabilities()["capabilities"]
    assert "render_preview" not in capabilities["capabilities"]


def test_aa_compile_publishes_only_standard_bundle_reference(tmp_path):
    request, draft, drafts = _context(tmp_path)
    legacy = FakeLegacyAdapter(tmp_path)
    publisher = FakeBundlePublisher()
    adapter = AzureArchiveAdapter(legacy, drafts, bundle_publisher=publisher)

    result = adapter.compile(request, draft)
    public = result.to_dict()

    assert result.bundle_ref is not None
    assert result.bundle_ref.target == "pc_aap"
    assert "draft_token" not in json.dumps(public)
    assert "bundle_dir" not in json.dumps(public)
    assert "aap_path" not in json.dumps(public)
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["legacy_result"]["bundle_dir"].endswith(
        "private-aa-bundle"
    )
    assert not any(call[0] == "install" for call in legacy.calls)


def test_aa_compile_unavailable_creates_no_bundle(tmp_path):
    request, draft, drafts = _context(tmp_path)
    publisher = FakeBundlePublisher()
    adapter = AzureArchiveAdapter(
        FakeLegacyAdapter(tmp_path, compile_available=False),
        drafts,
        bundle_publisher=publisher,
    )

    with pytest.raises(ProductionError) as raised:
        adapter.compile(request, draft)

    assert raised.value.code == "adapter_capability_unavailable"
    assert raised.value.details["operation"] == "compile_aap"
    assert publisher.calls == []


def test_aa_rejects_mismatched_draft_hash_before_legacy_call(tmp_path):
    request, draft, drafts = _context(tmp_path)
    legacy = FakeLegacyAdapter(tmp_path)
    adapter = AzureArchiveAdapter(
        legacy, drafts, bundle_publisher=FakeBundlePublisher()
    )
    mismatched = replace(draft, content_hash="sha256:" + "f" * 64)

    with pytest.raises(ProductionError) as raised:
        adapter.validate(request, mismatched)

    assert raised.value.code == "performance_draft_reference_mismatch"
    assert legacy.calls == []


def test_aa_cancel_prevents_compile_publication(tmp_path):
    request, draft, drafts = _context(tmp_path)
    attempt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    request = AdapterRequest(request.production_request, attempt_id=attempt_id)
    publisher = FakeBundlePublisher()
    adapter = AzureArchiveAdapter(
        FakeLegacyAdapter(tmp_path), drafts, bundle_publisher=publisher
    )

    cancelled = adapter.cancel(attempt_id)
    with pytest.raises(ProductionError) as raised:
        adapter.compile(request, draft)

    assert cancelled.cancelled is True
    assert raised.value.code == "adapter_operation_cancelled"
    assert publisher.calls == []


def test_aa_render_is_explicitly_unavailable(tmp_path):
    request, draft, drafts = _context(tmp_path)
    adapter = AzureArchiveAdapter(
        FakeLegacyAdapter(tmp_path), drafts, bundle_publisher=FakeBundlePublisher()
    )

    with pytest.raises(ProductionError) as raised:
        adapter.render(request, draft, {"target": "storyforge_preview"})

    assert raised.value.code == "adapter_capability_unavailable"
    assert raised.value.details["operation"] == "render_preview"
