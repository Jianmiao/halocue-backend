from __future__ import annotations

import json
from pathlib import Path

import pytest

from halocue_production.adapters.bundles import (
    BuildBundleAssembler,
    DeliverableInput,
)
from halocue_production.adapters.base import AdapterRequest, DraftRef
from halocue_production.artifacts import ArtifactStore
from halocue_production.contracts import validate_contract
from halocue_production.errors import ProductionError
from halocue_production.runtime import RuntimeStore


IDS = {
    "request": "66666666-6666-4666-8666-666666666666",
    "draft": "88888888-8888-4888-8888-888888888888",
    "deliverable": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
}
HASHES = {
    "script_release": "sha256:" + "1" * 64,
    "performance_draft": "sha256:" + "2" * 64,
    "asset_manifest": "sha256:" + "3" * 64,
}


def _assembler(tmp_path: Path):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite3")
    artifacts = ArtifactStore(tmp_path / "workspace", runtime)
    staging = tmp_path / "staging"
    staging.mkdir()
    return (
        BuildBundleAssembler(artifacts, staging_root=staging),
        staging,
        artifacts,
    )


def _output(staging: Path, *, artifact_id: str = IDS["deliverable"]):
    source = staging / "preview.json"
    source.write_bytes(b'{"preview":true}')
    return DeliverableInput(
        artifact_id=artifact_id,
        kind="preview",
        media_type="application/json",
        source_path=source,
        workspace_uri="workspace://builds/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/preview.json",
    )


def test_build_bundle_registers_every_deliverable_before_final_manifest(tmp_path):
    assembler, staging, artifacts = _assembler(tmp_path)

    bundle = assembler.assemble(
        request_id=IDS["request"],
        performance_draft_id=IDS["draft"],
        input_hashes=HASHES,
        producer={
            "adapter_id": "storyforge-local",
            "engine_id": "storyforge",
            "engine_version": "0.1.0",
        },
        target="storyforge_preview",
        deliverables=[_output(staging)],
        created_at="2026-08-19T00:00:00+00:00",
    )

    manifest = json.loads(artifacts.read_artifact_bytes(bundle.artifact_uri))
    assert validate_contract("BuildBundle", manifest) == manifest
    assert artifacts.get_artifact(manifest["deliverables"][0]["uri"]).kind == "preview"
    assert manifest["deliverables"][0]["uri"].startswith("artifact://")
    assert manifest["build_bundle_ref"] == bundle.artifact_uri


def test_repeating_same_fixed_build_is_idempotent_and_semantically_equal(tmp_path):
    assembler, staging, artifacts = _assembler(tmp_path)
    output = _output(staging)
    kwargs = {
        "request_id": IDS["request"],
        "performance_draft_id": IDS["draft"],
        "input_hashes": HASHES,
        "producer": {
            "adapter_id": "storyforge-local",
            "engine_id": "storyforge",
            "engine_version": "0.1.0",
        },
        "target": "storyforge_preview",
        "deliverables": [output],
        "created_at": "2026-08-19T00:00:00+00:00",
    }

    first = assembler.assemble(**kwargs)
    second = assembler.assemble(**kwargs)

    assert second.bundle_id == first.bundle_id
    assert second.content_hash == first.content_hash
    assert artifacts.read_artifact_bytes(second.artifact_uri) == artifacts.read_artifact_bytes(
        first.artifact_uri
    )


def test_external_path_and_unregistered_source_are_rejected(tmp_path):
    assembler, staging, _ = _assembler(tmp_path)
    external = tmp_path / "outside.bin"
    external.write_bytes(b"outside")

    with pytest.raises(ProductionError) as plain:
        assembler.assemble(
            request_id=IDS["request"],
            performance_draft_id=IDS["draft"],
            input_hashes=HASHES,
            producer={"adapter_id": "storyforge-local", "engine_id": "storyforge", "engine_version": "0.1.0"},
            target="storyforge_preview",
            deliverables=[external],
            created_at="2026-08-19T00:00:00+00:00",
        )
    assert plain.value.code == "build_bundle_output_unregistered"

    with pytest.raises(ProductionError) as outside:
        assembler.assemble(
            request_id=IDS["request"],
            performance_draft_id=IDS["draft"],
            input_hashes=HASHES,
            producer={"adapter_id": "storyforge-local", "engine_id": "storyforge", "engine_version": "0.1.0"},
            target="storyforge_preview",
            deliverables=[
                DeliverableInput(
                    artifact_id=IDS["deliverable"],
                    kind="preview",
                    media_type="application/json",
                    source_path=external,
                    workspace_uri="workspace://builds/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/out.json",
                )
            ],
            created_at="2026-08-19T00:00:00+00:00",
        )
    assert outside.value.code == "build_bundle_source_outside_staging"


def test_cancelled_assembly_never_publishes_bundle_alias(tmp_path):
    assembler, staging, artifacts = _assembler(tmp_path)

    with pytest.raises(ProductionError) as raised:
        assembler.assemble(
            request_id=IDS["request"],
            performance_draft_id=IDS["draft"],
            input_hashes=HASHES,
            producer={"adapter_id": "storyforge-local", "engine_id": "storyforge", "engine_version": "0.1.0"},
            target="storyforge_preview",
            deliverables=[_output(staging)],
            created_at="2026-08-19T00:00:00+00:00",
            cancelled=lambda: True,
        )

    assert raised.value.code == "adapter_operation_cancelled"
    assert not list((tmp_path / "workspace" / "builds").rglob("manifest.json"))


def test_aa_private_output_is_translated_to_public_bundle_reference(tmp_path):
    assembler, staging, artifacts = _assembler(tmp_path)
    request_payload = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "contracts"
            / "examples"
            / "production-request-1.1.json"
        ).read_text(encoding="utf-8")
    )
    request = AdapterRequest(request_payload)
    draft = DraftRef(
        draft_id=IDS["draft"],
        revision_id="99999999-9999-4999-8999-999999999999",
        artifact_uri="artifact://performance-drafts/99999999-9999-4999-8999-999999999999",
        content_hash=HASHES["performance_draft"],
        review_status="approved",
    )
    aap = staging / "private-result.aap"
    aap.write_bytes(b"authorized aap fixture")

    bundle = assembler.publish_aa(
        request=request,
        draft_ref=draft,
        legacy_result={
            "aap_path": str(aap),
            "created_at": "2026-08-19T00:00:00+00:00",
        },
        producer={
            "adapter_id": "aa-compat",
            "engine_id": "azurearchive",
            "engine_version": "0.9.3",
        },
    )

    public = json.dumps(bundle.to_dict(), ensure_ascii=False)
    manifest = json.loads(artifacts.read_artifact_bytes(bundle.artifact_uri))
    assert manifest["deliverables"][0]["kind"] == "aap"
    assert "aap_path" not in public
    assert str(tmp_path) not in public
