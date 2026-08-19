from __future__ import annotations

import json
import time

from halocue_production.contracts import idempotency_key_for_request, validate_contract
from halocue_production.service import ProductionService
from test_formal_inputs import formal_request


def _wait_for(service: ProductionService, job_id: str, state: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.jobs.get(job_id)
        if job is not None and job.state == state:
            return job
        time.sleep(0.01)
    return service.jobs.get(job_id)


def test_formal_release_to_storyforge_build_bundle_vertical_slice(settings):
    service = ProductionService(settings)
    request = formal_request(service, text="旁白: 开场\n爱丽丝: 你好\n")
    request["production_policy"]["target"] = "storyforge_preview"
    request["idempotency_key"] = idempotency_key_for_request(request)
    try:
        created = service.create_run(request)
        assert created["production_request"]["version"] == "1.1"

        adapter_request = service._formal_adapter_request(
            request,
        )
        draft = service.formal_drafts.create_imported(
            adapter_request, "storyforge-local"
        )
        approved = service.formal_drafts.update(
            draft,
            {"review_status": "approved"},
        )
        validate_contract("PerformanceDraft", approved.payload)
        assert approved.review_status == "approved"

        status, submitted = service.submit_adapter_operation(
            adapter_request,
            approved,
            operation="render",
            options={"target": "storyforge_preview"},
        )
        assert status == 202
        job = _wait_for(service, submitted["job"]["job_id"], "succeeded")
        assert job is not None and job.state == "succeeded"
        result = job.result or {}
        bundle = result.get("bundle_ref")
        assert isinstance(bundle, dict)
        manifest = validate_contract(
            "BuildBundle",
            json.loads(service.artifacts.read_artifact_bytes(bundle["artifact_uri"]).decode("utf-8")),
        )
        assert manifest["schema_version"] == "1.0"
        assert manifest["target"] == "storyforge_preview"
        assert manifest["performance_draft_id"] == approved.draft_id
        assert manifest["input_hashes"]["performance_draft"] == approved.content_hash
        public = json.dumps(job.to_dict(), ensure_ascii=False)
        assert str(settings.data_dir) not in public
        assert "bundle_dir" not in public
        assert "aap_path" not in public
    finally:
        service.jobs.close()
