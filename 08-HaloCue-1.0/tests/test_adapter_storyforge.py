from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from halocue_production.adapters.base import AdapterRequest
from halocue_production.adapters.drafts import PerformanceDraftStore
from halocue_production.adapters.storyforge import StoryForgeAdapter, StoryForgeRenderer
from halocue_production.artifacts import ArtifactRef, ArtifactStore
from halocue_production.contracts import (
    contract_content_hash,
    idempotency_key_for_request,
    validate_contract,
)
from halocue_production.errors import ProductionError
from halocue_production.runtime import RuntimeStore


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"


class FakeVideoExporter:
    def __init__(self, artifacts: ArtifactStore):
        self.artifacts = artifacts
        self.calls: list[dict] = []

    def export_video(self, *, request, draft, preview, options, cancelled) -> ArtifactRef:
        self.calls.append({"request": request, "draft": draft, "preview": preview})
        if cancelled():
            raise ProductionError("operation_cancelled", "cancelled", status=409)
        content = b"authorized video fixture"
        workspace = self.artifacts.commit_bytes(
            f"workspace://storyforge-videos/{draft.revision_id}.mp4",
            content,
            kind="video",
            media_type="video/mp4",
        )
        return self.artifacts.publish_artifact(
            "storyforge-videos",
            draft.revision_id,
            workspace,
            provenance={"attempt_id": request.attempt_id},
        )


def _context(tmp_path: Path):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite3")
    artifacts = ArtifactStore(tmp_path / "workspace", runtime)
    request = json.loads(
        (EXAMPLE_DIR / "production-request-1.1.json").read_text(encoding="utf-8")
    )
    text = "旁白: 开场\n爱丽丝: 你好\n"
    content = text.encode("utf-8")
    request["script_release"]["content_hash"] = (
        "sha256:" + hashlib.sha256(content).hexdigest()
    )
    request["production_policy"]["target"] = "storyforge_preview"
    manifest = json.loads(
        (EXAMPLE_DIR / "asset-manifest-1.0.json").read_text(encoding="utf-8")
    )
    manifest["assets"] = []
    manifest["content_hash"] = contract_content_hash("AssetManifest", manifest)
    request["asset_manifest"] = {
        "id": manifest["id"],
        "version": "1.0",
        "content_hash": manifest["content_hash"],
        "uri": "workspace://assets/77777777-7777-4777-8777-777777777777/manifest.json",
    }
    request["idempotency_key"] = idempotency_key_for_request(request)
    artifacts.commit_bytes(
        request["script_release"]["content_uri"],
        content,
        kind="script-release-content",
        media_type="text/plain; charset=utf-8",
    )
    artifacts.commit_bytes(
        request["asset_manifest"]["uri"],
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ),
        kind="asset-manifest",
        media_type="application/json",
    )
    adapter_request = AdapterRequest(request, asset_manifest=manifest)
    drafts = PerformanceDraftStore(artifacts, runtime)
    draft = drafts.create_imported(adapter_request, "storyforge-local")
    return adapter_request, draft, drafts, artifacts


def test_storyforge_preview_is_deterministic_and_does_not_emit_video(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    renderer = StoryForgeRenderer(artifacts)
    adapter = StoryForgeAdapter(renderer, drafts)

    first = adapter.render(request, draft, {"target": "storyforge_preview"})
    second = adapter.render(request, draft, {"target": "storyforge_preview"})

    assert first.artifact_refs == second.artifact_refs
    assert first.artifact_refs[0].startswith("artifact://storyforge-previews/")
    manifest = json.loads(artifacts.read_artifact_bytes(first.artifact_refs[0]))
    assert validate_contract("AdapterCapabilities", adapter.capabilities())
    assert "你好" not in json.dumps(manifest, ensure_ascii=False)
    assert "video" not in {artifacts.get_artifact(ref).kind for ref in first.artifact_refs}


def test_storyforge_receives_only_formal_request_and_draft(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    renderer = StoryForgeRenderer(artifacts)
    adapter = StoryForgeAdapter(renderer, drafts)

    adapter.render(request, draft, {"target": "storyforge_preview"})

    assert renderer.last_request_type is AdapterRequest
    assert renderer.last_draft_type is type(draft)
    assert not hasattr(renderer, "writing_service")


def test_storyforge_video_without_exporter_is_structured_unavailable(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    adapter = StoryForgeAdapter(StoryForgeRenderer(artifacts), drafts)

    with pytest.raises(ProductionError) as raised:
        adapter.render(request, draft, {"target": "storyforge_video"})

    assert raised.value.code == "adapter_capability_unavailable"
    assert raised.value.details["operation"] == "export_video"


def test_storyforge_video_uses_explicit_exporter_and_registered_artifact(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    exporter = FakeVideoExporter(artifacts)
    adapter = StoryForgeAdapter(
        StoryForgeRenderer(artifacts), drafts, video_exporter=exporter
    )
    video_request_payload = json.loads(json.dumps(request.production_request))
    video_request_payload["production_policy"]["target"] = "storyforge_video"
    video_request_payload["idempotency_key"] = idempotency_key_for_request(
        video_request_payload
    )
    request = AdapterRequest(
        video_request_payload,
        asset_manifest=request.asset_manifest,
        target="storyforge_video",
    )

    result = adapter.render(request, draft, {"target": "storyforge_video"})

    assert len(result.artifact_refs) == 1
    assert artifacts.get_artifact(result.artifact_refs[0]).kind == "video"
    assert len(exporter.calls) == 1


def test_storyforge_cancel_prevents_late_preview_publication(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    attempt_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    preview_request_payload = json.loads(json.dumps(request.production_request))
    preview_request_payload["idempotency_key"] = idempotency_key_for_request(
        preview_request_payload
    )
    request = AdapterRequest(
        preview_request_payload,
        asset_manifest=request.asset_manifest,
        target="storyforge_preview",
        attempt_id=attempt_id,
    )
    adapter = StoryForgeAdapter(StoryForgeRenderer(artifacts), drafts)

    assert adapter.cancel(attempt_id).cancelled is True
    with pytest.raises(ProductionError) as raised:
        adapter.render(request, draft, {"target": "storyforge_preview"})

    assert raised.value.code == "adapter_operation_cancelled"
