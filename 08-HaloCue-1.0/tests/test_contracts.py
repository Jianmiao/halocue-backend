from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from halocue_production.errors import ProductionError
from halocue_production.jobs import JobRecord
from halocue_production.service import ProductionService


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "script_release_handoff_1_0.json"


def script_release_handoff() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_script_release_1_0_fixture_round_trips_and_validates(settings):
    payload = script_release_handoff()
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    assert hashlib.sha256(payload["source"]["text"].encode("utf-8")).hexdigest() == (
        payload["script_release"]["content_hash"]
    )

    service = ProductionService(settings)
    try:
        origin = service._upstream_script_release(
            payload, payload["source"]["text"]
        )
    finally:
        service.jobs.close()

    assert origin == {
        "kind": "halocue_writing",
        "contract_kind": "WritingHandoff/1.0",
        "formal_script_release": False,
        "schema_version": "1.0",
        "release_id": "release-000000000001",
        "display_version": "v1",
        "content_hash": payload["script_release"]["content_hash"],
        "work_id": "work-000000000001",
        "writing_pack_version": "ba-writing.productized/1.0.0",
    }
    assert json.loads(json.dumps(origin, ensure_ascii=False)) == origin


def test_script_release_compatibility_default_is_1_0(settings):
    payload = script_release_handoff()
    del payload["script_release"]["schema_version"]
    service = ProductionService(settings)
    try:
        origin = service._upstream_script_release(
            payload, payload["source"]["text"]
        )
    finally:
        service.jobs.close()

    assert origin["schema_version"] == "1.0"


def test_script_release_rejects_unknown_schema_before_writing_files(settings):
    payload = script_release_handoff()
    payload["script_release"]["schema_version"] = "2.0"
    service = ProductionService(settings)
    try:
        with pytest.raises(ProductionError) as raised:
            service.create_run(payload)
    finally:
        service.jobs.close()

    assert raised.value.code == "unsupported_script_release_version"
    assert raised.value.status == 400
    assert raised.value.details == {"received": "2.0", "supported": ["1.0"]}
    assert service.repository.list_runs() == []
    assert list((settings.data_dir / "releases").iterdir()) == []


def test_script_release_same_id_with_new_hash_is_identity_conflict(settings):
    first_payload = script_release_handoff()
    second_payload = copy.deepcopy(first_payload)
    second_payload["source"]["text"] = "## Scene 01\nNarrator: Changed.\n"
    second_payload["script_release"]["content_hash"] = hashlib.sha256(
        second_payload["source"]["text"].encode("utf-8")
    ).hexdigest()
    service = ProductionService(settings)
    try:
        first = service.create_run(first_payload)
        with pytest.raises(ProductionError) as raised:
            service.create_run(second_payload)
    finally:
        service.jobs.close()

    assert raised.value.code == "script_release_identity_conflict"
    assert raised.value.status == 409
    assert raised.value.details == {
        "release_id": "release-000000000001",
        "run_id": first["run"]["run_id"],
    }
    assert len(service.repository.list_runs()) == 1
    assert len(list((settings.data_dir / "releases").iterdir())) == 1


def test_compile_job_public_contract_hides_physical_bundle_path(tmp_path):
    private_path = str(tmp_path / "drafts" / "draft-1" / "builds" / "build-1")
    record = JobRecord(
        job_id="job-000000000001",
        kind="compile",
        state="succeeded",
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:01+00:00",
        result={
            "run_id": "run-000000000001",
            "build_id": "build-000000000001",
            "bundle": {
                "build_id": "build-000000000001",
                "bundle_dir": private_path,
                "content_revision": 1,
            },
        },
    )

    public = ProductionService._job_public(record.to_dict())

    assert public["result"]["bundle"] == {
        "build_id": "build-000000000001",
        "content_revision": 1,
    }
    assert private_path not in json.dumps(public, ensure_ascii=False)
    assert record.result["bundle"]["bundle_dir"] == private_path


def test_failed_job_public_contract_hides_private_exception_text(tmp_path):
    private_path = str(tmp_path / "private" / "model-response.json")
    record = JobRecord(
        job_id="job-000000000002",
        kind="direction_generation",
        state="failed",
        created_at="2026-08-15T00:00:00+00:00",
        updated_at="2026-08-15T00:00:01+00:00",
        error={"code": "model_output_invalid", "message": f"invalid output: {private_path}"},
    )

    public = ProductionService._job_public(record.to_dict())

    assert public["error"] == {
        "code": "model_output_invalid",
        "message": "AI 安排演出未完成，请检查对应阶段后重试。",
    }
    assert private_path not in json.dumps(public, ensure_ascii=False)
    assert record.error["message"] == f"invalid output: {private_path}"


def test_aa_discovery_error_contract_hides_candidate_paths(settings, tmp_path, monkeypatch):
    private_path = str(tmp_path / "Users" / "creator" / "AAData")
    service = ProductionService(settings)
    monkeypatch.setattr(
        service.adapter,
        "discover_aa_environment",
        lambda _selection: {
            "workspace": {"path": None, "valid": False, "directories": {}},
            "resource_cache": {"available": False, "path": None},
            "candidates": [{"path": private_path, "source": "scan", "valid": False}],
            "issues": [
                {"code": "aa_workspace_incomplete", "message": "AA 工作区不完整", "path": private_path}
            ],
        },
    )
    try:
        with pytest.raises(ProductionError) as raised:
            service.inspect_aa_environment({"adopt": True})
    finally:
        service.jobs.close()

    payload = raised.value.to_payload()
    assert payload["error"]["details"] == {
        "issues": [{"code": "aa_workspace_incomplete", "message": "AA 工作区不完整"}]
    }
    assert private_path not in json.dumps(payload, ensure_ascii=False)
