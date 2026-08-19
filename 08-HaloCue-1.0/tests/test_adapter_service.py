from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

import pytest

from halocue_production.adapters.base import (
    AdapterBase,
    AdapterRequest,
    AdapterResult,
    DraftRef,
)
from halocue_production.adapters.drafts import PerformanceDraftStore
from halocue_production.adapters.registry import AdapterRegistry
from halocue_production.contracts import contract_content_hash, idempotency_key_for_request
from halocue_production.errors import ProductionError
from halocue_production.legacy_adapter import Legacy093Adapter
from halocue_production.service import ProductionService


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"


class BlockingAdapter(AdapterBase):
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = threading.Event()

    def capabilities(self):
        return {
            "schema_version": "1.0",
            "adapter_api_version": "1.0",
            "adapter_id": "storyforge-test",
            "engine_id": "storyforge",
            "engine_version": "test",
            "capabilities": [
                "preflight",
                "create_performance_draft",
                "update_performance_draft",
                "validate",
                "render_preview",
                "cancel",
            ],
            "supported_script_manifest_versions": ["1.1"],
            "supported_performance_draft_versions": ["1.0"],
            "supported_asset_manifest_versions": ["1.0"],
            "supported_build_bundle_versions": ["1.0"],
            "targets": ["storyforge_preview"],
        }

    def preflight(self, request):
        return AdapterResult()

    def create_performance_draft(self, request, scope=None):
        return AdapterResult()

    def update_performance_draft(self, draft_ref, patch):
        return AdapterResult()

    def validate(self, request, draft_ref):
        return AdapterResult()

    def compile(self, request, draft_ref):
        self.require_capability("compile_aap")

    def render(self, request, draft_ref, options=None):
        self.started.set()
        while not self.release.wait(0.01):
            if self.cancelled.is_set():
                return AdapterResult(cancelled=True)
        if self.cancelled.is_set():
            return AdapterResult(cancelled=True)
        return AdapterResult()

    def cancel(self, attempt_ref):
        self.cancelled.set()
        return AdapterResult(cancelled=True)


class LatePublishingAdapter(BlockingAdapter):
    adapter_id = "storyforge-local"

    def __init__(self, artifacts):
        super().__init__()
        self.artifacts = artifacts
        self.finished = threading.Event()
        self.published_uri = None

    def capabilities(self):
        value = super().capabilities()
        value["adapter_id"] = self.adapter_id
        return value

    def render(self, request, draft_ref, options=None):
        self.started.set()
        self.release.wait(timeout=3)
        try:
            workspace = self.artifacts.commit_bytes(
                f"workspace://late-results/{request.attempt_id}.json",
                b"late adapter result",
                kind="preview",
                media_type="application/json",
            )
            artifact = self.artifacts.publish_artifact(
                "late-results",
                request.attempt_id,
                workspace,
                provenance={"attempt_id": request.attempt_id},
            )
            self.published_uri = artifact.uri
            return AdapterResult(artifact_refs=(artifact.uri,))
        finally:
            self.finished.set()


def _formal_context(service: ProductionService):
    request = json.loads(
        (EXAMPLE_DIR / "production-request-1.1.json").read_text(encoding="utf-8")
    )
    request["production_policy"]["target"] = "storyforge_preview"
    content = "旁白: 开场\n爱丽丝: 你好\n".encode("utf-8")
    request["script_release"]["content_hash"] = (
        "sha256:" + hashlib.sha256(content).hexdigest()
    )
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
    service.artifacts.commit_bytes(
        request["script_release"]["content_uri"],
        content,
        kind="script-release-content",
        media_type="text/plain; charset=utf-8",
    )
    service.artifacts.commit_bytes(
        request["asset_manifest"]["uri"],
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ),
        kind="asset-manifest",
        media_type="application/json",
    )
    release = json.loads(
        (EXAMPLE_DIR / "script-release-1.1.json").read_text(encoding="utf-8")
    )
    release["id"] = request["script_release"]["id"]
    release["manifest_uri"] = request["script_release"]["manifest_uri"]
    release["content_uri"] = request["script_release"]["content_uri"]
    release["content_hash"] = request["script_release"]["content_hash"]
    service.artifacts.commit_bytes(
        release["manifest_uri"],
        json.dumps(release, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        ),
        kind="script-release-manifest",
        media_type="application/json",
    )
    adapter_request = AdapterRequest(request, asset_manifest=manifest)
    draft = service.formal_drafts.create_imported(adapter_request, "storyforge-local")
    return adapter_request, draft


def _wait_for(service, job_id: str, state: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = service.jobs.get(job_id)
        if job and job.state == state:
            return job
        time.sleep(0.01)
    return service.jobs.get(job_id)


def test_service_keeps_legacy_adapter_and_routes_formal_targets(settings):
    service = ProductionService(settings)

    assert isinstance(service.adapter, Legacy093Adapter)
    assert service.production_adapters.for_target("pc_aap").capabilities()["adapter_id"] == "aa-compat"
    assert service.production_adapters.for_target("storyforge_preview").capabilities()["adapter_id"] == "storyforge-local"
    assert service.adapter_capabilities()["contract"] == "AdapterCapabilities/1.0"
    service.jobs.close()


def test_adapter_job_persists_only_safe_retry_context(settings):
    service = ProductionService(settings)
    request, draft = _formal_context(service)

    status, submitted = service.submit_adapter_operation(
        request,
        draft,
        operation="render",
        options={"target": "storyforge_preview"},
    )
    assert status == 202
    job = _wait_for(service, submitted["job"]["job_id"], "succeeded")

    assert job is not None and job.state == "succeeded"
    assert set(job.retry_context) == {
        "request_id",
        "adapter_id",
        "target",
        "draft_revision_id",
        "input_hashes",
    }
    assert "你好" not in json.dumps(job.retry_context, ensure_ascii=False)
    assert str(service.settings.data_dir) not in json.dumps(job.retry_context)
    service.jobs.close()


def test_cancelled_adapter_job_discards_late_result(settings):
    service = ProductionService(settings)
    request, draft = _formal_context(service)
    blocking = BlockingAdapter()
    service.production_adapters = AdapterRegistry([blocking])

    status, submitted = service.submit_adapter_operation(
        request,
        draft,
        operation="render",
        options={"target": "storyforge_preview"},
    )
    assert status == 202
    assert blocking.started.wait(timeout=2)
    cancelled = service.cancel_job(submitted["job"]["job_id"])
    assert cancelled["job"]["state"] in {"cancelled", "running"}
    job = _wait_for(service, submitted["job"]["job_id"], "cancelled")

    assert blocking.cancelled.is_set()
    assert job is not None and job.state == "cancelled"
    assert job.result is None
    service.jobs.close()


def test_restart_abandons_adapter_attempt_and_retry_isolated_from_late_result(settings):
    service = ProductionService(settings)
    request, draft = _formal_context(service)
    service.create_run(request.to_payload())
    late = LatePublishingAdapter(service.artifacts)
    service.production_adapters = AdapterRegistry([late])

    status, submitted = service.submit_adapter_operation(
        request,
        draft,
        operation="render",
        options={"target": "storyforge_preview"},
    )
    assert status == 202
    old_job = submitted["job"]
    old_attempt_id = old_job["attempt_id"]
    assert late.started.wait(timeout=2)

    service.jobs.close()
    abandoned = service.jobs.get(old_job["job_id"])
    assert abandoned is not None
    assert abandoned.state == "abandoned"

    late.release.set()
    assert late.finished.wait(timeout=3)
    assert service.repository.runtime.list_artifact_refs_for_attempt(old_attempt_id) == []
    assert late.published_uri is None

    restored = ProductionService(settings)
    try:
        retry = restored.retry_job(old_job["job_id"])
        assert retry["job"]["attempt_id"] != old_attempt_id
        retried = _wait_for(restored, retry["job"]["job_id"], "succeeded")
        assert retried is not None
        assert retried.state == "succeeded"
        assert set(retried.retry_context) == {
            "request_id",
            "adapter_id",
            "target",
            "draft_revision_id",
            "input_hashes",
        }
        assert retried.result is not None
        assert retried.result.get("bundle_ref") is not None
        assert restored.repository.runtime.list_artifact_refs_for_attempt(
            retried.attempt_id
        )
        assert restored.jobs.get(old_job["job_id"]).state == "abandoned"
    finally:
        restored.jobs.close()
