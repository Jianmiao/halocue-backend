from __future__ import annotations

import json
import uuid

import pytest

from halocue_production.artifacts import ArtifactStore
from halocue_production.asset_manifests import AssetManifestStore
from halocue_production.contracts import contract_content_hash, validate_contract
from halocue_production.errors import ProductionError
from halocue_production.models import ProductionRun
from halocue_production.repository import ProductionRepository
from halocue_production.service import ProductionService

from test_service import configured_resource_settings


def saved_run(repository: ProductionRepository) -> ProductionRun:
    run = ProductionRun(
        run_id="run-000000000001",
        project="manifest-test",
        release_id="release-000000000001",
        draft_token=None,
        state="waiting_for_review",
        current_stage="preflight",
        created_at="2026-08-15T04:05:00+00:00",
        updated_at="2026-08-15T04:05:00+00:00",
    )
    repository.save_run(run)
    return run


def manifest_payload(asset: dict) -> dict:
    payload = {
        "schema_version": "1.0",
        "id": str(uuid.uuid4()),
        "content_hash": "",
        "created_at": "2026-08-15T04:05:00+00:00",
        "assets": [asset],
    }
    payload["content_hash"] = contract_content_hash("AssetManifest", payload)
    return payload


def test_formal_manifest_freeze_is_idempotent_persistent_and_allowlisted(tmp_path):
    repository = ProductionRepository(tmp_path)
    run = saved_run(repository)
    artifacts = ArtifactStore(tmp_path / "workspace", repository.runtime)
    manifests = AssetManifestStore(artifacts, repository.runtime)
    asset_id = str(uuid.uuid4())
    asset_file = artifacts.commit_bytes(
        "workspace://assets/backgrounds/classroom.png",
        b"licensed fixture image",
        kind="background",
        media_type="image/png",
    )
    payload = manifest_payload(
        {
            "asset_id": asset_id,
            "kind": "background",
            "uri": asset_file.uri,
            "content_hash": asset_file.content_hash,
            "display_name": "Classroom",
            "media_type": "image/png",
            "metadata": {"fixture": True},
        }
    )

    with pytest.raises(ProductionError) as compatibility_error:
        manifests.freeze(run, payload, source_kind="compatibility_empty")
    assert compatibility_error.value.code == "asset_manifest_compatibility_not_empty"

    first = manifests.freeze(run, payload, source_kind="production_request")
    repeated = manifests.freeze(run, payload, source_kind="production_request")
    restarted_repository = ProductionRepository(tmp_path)
    restarted = AssetManifestStore(
        ArtifactStore(tmp_path / "workspace", restarted_repository.runtime),
        restarted_repository.runtime,
    )

    assert repeated == first
    assert restarted.describe_for_run(run.run_id) == first
    assert restarted.require_asset(run.run_id, asset_id)["uri"] == asset_file.uri
    with pytest.raises(ProductionError) as raised:
        restarted.require_asset(run.run_id, str(uuid.uuid4()))
    assert raised.value.code == "asset_not_allowlisted"


def test_same_run_rejects_a_different_frozen_manifest(tmp_path):
    repository = ProductionRepository(tmp_path)
    run = saved_run(repository)
    artifacts = ArtifactStore(tmp_path / "workspace", repository.runtime)
    manifests = AssetManifestStore(artifacts, repository.runtime)
    asset_file = artifacts.commit_bytes(
        "workspace://assets/backgrounds/classroom.png",
        b"licensed fixture image",
        kind="background",
        media_type="image/png",
    )
    asset = {
        "asset_id": str(uuid.uuid4()),
        "kind": "background",
        "uri": asset_file.uri,
        "content_hash": asset_file.content_hash,
        "display_name": "Classroom",
        "media_type": "image/png",
        "metadata": {},
    }
    first = manifest_payload(asset)
    manifests.freeze(run, first, source_kind="production_request")
    different = manifest_payload(asset)

    with pytest.raises(ProductionError) as raised:
        manifests.freeze(run, different, source_kind="production_request")

    assert raised.value.code == "asset_manifest_conflict"
    assert raised.value.status == 409


def test_manifest_rejects_unregistered_or_hash_mismatched_assets(tmp_path):
    repository = ProductionRepository(tmp_path)
    run = saved_run(repository)
    artifacts = ArtifactStore(tmp_path / "workspace", repository.runtime)
    manifests = AssetManifestStore(artifacts, repository.runtime)
    missing = manifest_payload(
        {
            "asset_id": str(uuid.uuid4()),
            "kind": "background",
            "uri": "workspace://assets/backgrounds/missing.png",
            "content_hash": "sha256:" + "a" * 64,
            "display_name": "Missing",
            "media_type": "image/png",
            "metadata": {},
        }
    )

    with pytest.raises(ProductionError) as missing_error:
        manifests.freeze(run, missing, source_kind="production_request")
    assert missing_error.value.code == "workspace_file_not_found"

    artifact = artifacts.commit_bytes(
        "workspace://assets/backgrounds/known.png",
        b"known",
        kind="background",
        media_type="image/png",
    )
    mismatched = manifest_payload(
        {
            "asset_id": str(uuid.uuid4()),
            "kind": "background",
            "uri": artifact.uri,
            "content_hash": "sha256:" + "b" * 64,
            "display_name": "Known",
            "media_type": "image/png",
            "metadata": {},
        }
    )
    with pytest.raises(ProductionError) as mismatch_error:
        manifests.freeze(run, mismatched, source_kind="production_request")
    assert mismatch_error.value.code == "asset_manifest_asset_hash_mismatch"


def test_compatibility_run_freezes_empty_manifest_without_aa_resource_data(
    settings, tmp_path
):
    configured = configured_resource_settings(settings, tmp_path)
    service = ProductionService(configured)

    created = service.create_run(
        {
            "project": "asset-manifest-compatibility",
            "source": {"kind": "inline", "text": "爱丽丝: 测试。\n"},
        }
    )
    reference = created["asset_manifest"]
    manifest = json.loads(service.artifacts.read_bytes(reference["uri"]))
    binding = service.repository.runtime.get_asset_manifest_for_run(
        created["run"]["run_id"]
    )

    assert validate_contract("AssetManifest", manifest) == manifest
    assert manifest["assets"] == []
    assert created["asset_policy"] == {
        "mode": "whitelist_only",
        "source": "compatibility_empty",
        "asset_count": 0,
    }
    assert reference["content_hash"] == manifest["content_hash"]
    assert binding["file_hash"] == service.artifacts.get(reference["uri"]).content_hash
    assert binding["content_hash"] != binding["file_hash"]
    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "alice-school" not in serialized
    assert str(configured.resource_index) not in serialized
    public_response = json.dumps(
        {
            "asset_manifest": reference,
            "asset_policy": created["asset_policy"],
        },
        ensure_ascii=False,
    )
    assert str(configured.data_dir) not in public_response
    assert str(configured.resource_index) not in public_response

    service.jobs.close()
    restored = ProductionService(configured)
    assert restored.run_detail(created["run"]["run_id"])["asset_manifest"] == reference
    restored.jobs.close()


def test_tampered_manifest_file_is_rejected(settings):
    service = ProductionService(settings)
    created = service.create_run(
        {
            "project": "asset-manifest-tamper",
            "source": {"kind": "inline", "text": "旁白: 测试。\n"},
        }
    )
    reference = created["asset_manifest"]
    record = service.artifacts.get(reference["uri"])
    physical = service.artifacts.root.joinpath(*record.relative_path.split("/"))
    physical.write_bytes(b"tampered")

    with pytest.raises(ProductionError) as raised:
        service.run_detail(created["run"]["run_id"])

    assert raised.value.code == "artifact_hash_mismatch"
    service.jobs.close()
