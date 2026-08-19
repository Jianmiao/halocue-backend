from __future__ import annotations

import copy
import json

import pytest

from halocue_production.contracts import (
    canonical_json_bytes,
    contract_content_hash,
    idempotency_key_for_request,
)
from halocue_production.errors import ProductionError
from halocue_production.service import ProductionService


RELEASE_ID = "11111111-1111-4111-8111-111111111111"
WORK_ID = "22222222-2222-4222-8222-222222222222"
CANON_REVISION_ID = "33333333-3333-4333-8333-333333333333"
SOURCE_REVISION_ID = "44444444-4444-4444-8444-444444444444"
GATE_ID = "55555555-5555-4555-8555-555555555555"
REQUEST_ID = "66666666-6666-4666-8666-666666666666"
ASSET_MANIFEST_ID = "77777777-7777-4777-8777-777777777777"


def register_asset_manifest(service: ProductionService) -> dict:
    payload = {
        "schema_version": "1.0",
        "id": ASSET_MANIFEST_ID,
        "content_hash": "",
        "created_at": "2026-08-15T04:01:00Z",
        "assets": [],
    }
    payload["content_hash"] = contract_content_hash("AssetManifest", payload)
    uri = f"workspace://assets/{ASSET_MANIFEST_ID}/manifest.json"
    service.artifacts.commit_bytes(
        uri,
        canonical_json_bytes(payload),
        kind="asset-manifest",
        media_type="application/json",
    )
    return {
        "id": payload["id"],
        "version": "1.0",
        "content_hash": payload["content_hash"],
        "uri": uri,
    }


def formal_request(
    service: ProductionService,
    *,
    text: str = "旁白: 正式制作入口\n",
    release_id: str = RELEASE_ID,
    request_id: str = REQUEST_ID,
    asset_manifest: dict | None = None,
    namespace: str = "releases",
) -> dict:
    content_uri = f"workspace://{namespace}/{release_id}/{request_id}/script.txt"
    content = service.artifacts.commit_bytes(
        content_uri,
        text.encode("utf-8"),
        kind="script-release-content",
        media_type="text/plain",
    )
    manifest_uri = f"workspace://{namespace}/{release_id}/{request_id}/manifest.json"
    release = {
        "schema_version": "1.1",
        "id": release_id,
        "work_id": WORK_ID,
        "display_version": "v4",
        "manifest_uri": manifest_uri,
        "content_uri": content_uri,
        "content_hash": content.content_hash,
        "canon_revision_id": CANON_REVISION_ID,
        "writing_pack_version": "ba-writing.productized/1.0.0",
        "source_revision_ids": [SOURCE_REVISION_ID],
        "gate_snapshot_ids": [GATE_ID],
        "released_by": "user:local",
        "released_at": "2026-08-15T04:00:00Z",
    }
    service.artifacts.commit_bytes(
        manifest_uri,
        canonical_json_bytes(release),
        kind="script-release-manifest",
        media_type="application/json",
    )
    manifest_reference = asset_manifest or register_asset_manifest(service)
    request = {
        "schema_version": "1.1",
        "request_id": request_id,
        "production_display_name": "Formal Production v4",
        "script_release": {
            "version": "1.1",
            "id": release["id"],
            "display_version": release["display_version"],
            "content_hash": release["content_hash"],
            "manifest_uri": release["manifest_uri"],
            "content_uri": release["content_uri"],
        },
        "script_manifest_version": "1.1",
        "asset_manifest": manifest_reference,
        "production_policy": {
            "asset_reference_mode": "whitelist_only",
            "allow_placeholders": False,
            "target": "pc_aap",
        },
        "idempotency_key": "",
    }
    request["idempotency_key"] = idempotency_key_for_request(request)
    return request


def test_formal_request_creates_persistent_idempotent_run(settings):
    service = ProductionService(settings)
    request = formal_request(service)

    created = service.create_run(request)

    run_id = created["run"]["run_id"]
    assert created["handoff"] == {
        "kind": "production_request",
        "idempotent": False,
        "request_id": REQUEST_ID,
        "release_id": RELEASE_ID,
    }
    assert created["run"]["release_id"] == RELEASE_ID
    assert created["production_request"]["version"] == "1.1"
    assert created["asset_manifest"] == request["asset_manifest"]
    assert created["asset_policy"] == {
        "mode": "whitelist_only",
        "source": "production_request",
        "asset_count": 0,
        "revision": 1,
    }
    frozen = service.repository.runtime.get_frozen_script_release(RELEASE_ID)
    binding = service.repository.runtime.get_production_request(REQUEST_ID)
    assert frozen["content_hash"] == request["script_release"]["content_hash"]
    assert binding["run_id"] == run_id
    assert str(settings.data_dir) not in json.dumps(created, ensure_ascii=False)

    repeated = service.create_run(copy.deepcopy(request))
    assert repeated["run"]["run_id"] == run_id
    assert repeated["handoff"]["idempotent"] is True
    service.jobs.close()

    restored = ProductionService(settings)
    after_restart = restored.create_run(copy.deepcopy(request))
    assert after_restart["run"]["run_id"] == run_id
    assert after_restart["handoff"]["idempotent"] is True
    restored.jobs.close()


def test_formal_request_rejects_same_release_id_with_different_content(settings):
    service = ProductionService(settings)
    request = formal_request(service)
    service.create_run(request)
    conflicting = formal_request(
        service,
        text="旁白: 被替换的正文\n",
        release_id=RELEASE_ID,
        request_id="88888888-8888-4888-8888-888888888888",
        asset_manifest=request["asset_manifest"],
        namespace="incoming",
    )

    with pytest.raises(ProductionError) as raised:
        service.create_run(conflicting)

    assert raised.value.code == "script_release_identity_conflict"
    assert raised.value.status == 409
    service.jobs.close()


def test_formal_request_rejects_same_request_id_with_different_envelope(settings):
    service = ProductionService(settings)
    request = formal_request(service)
    created = service.create_run(request)
    conflicting = copy.deepcopy(request)
    conflicting["production_display_name"] = "Changed Display Name"
    conflicting["idempotency_key"] = idempotency_key_for_request(conflicting)

    with pytest.raises(ProductionError) as raised:
        service.create_run(conflicting)

    assert raised.value.code == "production_request_identity_conflict"
    assert raised.value.status == 409
    assert len(service.repository.list_runs()) == 1
    assert service.repository.list_runs()[0].run_id == created["run"]["run_id"]
    service.jobs.close()


def test_formal_request_validates_asset_manifest_before_creating_run(settings):
    service = ProductionService(settings)
    request = formal_request(service)
    request["asset_manifest"]["content_hash"] = "sha256:" + "a" * 64
    request["idempotency_key"] = idempotency_key_for_request(request)

    with pytest.raises(ProductionError) as raised:
        service.create_run(request)

    assert raised.value.code == "asset_manifest_reference_mismatch"
    assert service.repository.list_runs() == []
    assert service.repository.runtime.get_frozen_script_release(RELEASE_ID) is None
    service.jobs.close()


def test_formal_request_rejects_tampered_script_artifact_on_retry(settings):
    service = ProductionService(settings)
    request = formal_request(service)
    service.create_run(request)

    content_file = service.artifacts.get(request["script_release"]["content_uri"])
    (service.artifacts.root / content_file.relative_path).write_bytes(
        b"tampered script\n"
    )

    with pytest.raises(ProductionError) as raised:
        service.create_run(copy.deepcopy(request))

    assert raised.value.code == "artifact_hash_mismatch"
    assert raised.value.status == 500
    service.jobs.close()


def test_formal_request_rejects_tampered_request_artifact_on_retry(settings):
    service = ProductionService(settings)
    request = formal_request(service)
    service.create_run(request)
    binding = service.repository.runtime.get_production_request(REQUEST_ID)
    request_file = service.artifacts.get(binding["request_uri"])
    (service.artifacts.root / request_file.relative_path).write_bytes(b"{}")

    with pytest.raises(ProductionError) as raised:
        service.create_run(copy.deepcopy(request))

    assert raised.value.code == "artifact_hash_mismatch"
    assert raised.value.status == 500
    service.jobs.close()


@pytest.mark.parametrize(
    ("version", "code"),
    [
        ("1.0", "production_request_version_not_runnable"),
        ("2.0", "unsupported_production_request_version"),
    ],
)
def test_formal_request_rejects_non_runnable_or_unknown_version(
    settings, version, code
):
    service = ProductionService(settings)
    if version == "1.0":
        request = json.loads(
            (
                settings.project_root
                / "contracts"
                / "examples"
                / "production-request-1.0.json"
            ).read_text(encoding="utf-8")
        )
    else:
        request = {"schema_version": version}

    with pytest.raises(ProductionError) as raised:
        service.create_run(request)

    assert raised.value.code == code
    assert service.repository.list_runs() == []
    service.jobs.close()
