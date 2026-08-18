from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from halocue_production.adapters.base import AdapterRequest
from halocue_production.adapters.drafts import PerformanceDraftStore
from halocue_production.artifacts import ArtifactStore
from halocue_production.contracts import idempotency_key_for_request, validate_contract
from halocue_production.errors import ProductionError
from halocue_production.runtime import RuntimeStore


EXAMPLE_DIR = Path(__file__).resolve().parents[1] / "contracts" / "examples"


def _request(tmp_path: Path) -> tuple[AdapterRequest, PerformanceDraftStore]:
    runtime = RuntimeStore(tmp_path / "runtime.sqlite3")
    artifacts = ArtifactStore(tmp_path / "workspace", runtime)
    payload = json.loads(
        (EXAMPLE_DIR / "production-request-1.1.json").read_text(encoding="utf-8")
    )
    text = "旁白: 开场\n爱丽丝: 你好\n"
    content = text.encode("utf-8")
    content_hash = "sha256:" + hashlib.sha256(content).hexdigest()
    payload["script_release"]["content_hash"] = content_hash
    payload["idempotency_key"] = idempotency_key_for_request(payload)
    artifacts.commit_bytes(
        payload["script_release"]["content_uri"],
        content,
        kind="script-release-content",
        media_type="text/plain; charset=utf-8",
    )
    return AdapterRequest(payload), PerformanceDraftStore(artifacts, runtime)


def test_imported_release_gets_frozen_scene_index_and_valid_draft(tmp_path):
    request, store = _request(tmp_path)

    draft = store.create_imported(request, "storyforge-local")

    assert validate_contract("PerformanceDraft", draft.payload) == draft.payload
    assert draft.payload["provenance"]["created_by"] == "importer"
    assert draft.payload["provenance"]["adapter_id"] == "storyforge-local"
    lines = [
        node["performance_line"]
        for node in draft.payload["scenes"][0]["nodes"]
    ]
    assert all(line["cast_state"] for line in lines)
    assert all(len(line["cast_state"]) == 1 for line in lines)
    assert lines[1]["speaker_id"] == lines[1]["cast_state"][0]["character_id"]


def test_imported_release_retry_reuses_random_frozen_ids(tmp_path):
    request, store = _request(tmp_path)

    first = store.create_imported(request, "storyforge-local")
    second = store.create_imported(request, "storyforge-local")

    assert second.draft_id == first.draft_id
    assert second.revision_id == first.revision_id
    assert second.content_hash == first.content_hash
    assert second.payload == first.payload


def test_update_creates_successor_and_keeps_prior_bytes_immutable(tmp_path):
    request, store = _request(tmp_path)
    original = store.create_imported(request, "storyforge-local")
    original_bytes = store.artifacts.read_artifact_bytes(original.artifact_uri)

    updated = store.update(
        original,
        {"review_status": "pending_review"},
        expected_revision_id=original.revision_id,
    )

    assert updated.draft_id == original.draft_id
    assert updated.revision_id != original.revision_id
    assert updated.content_hash != original.content_hash
    assert updated.review_status == "pending_review"
    assert store.load(original.artifact_uri).content_hash == original.content_hash
    assert store.artifacts.read_artifact_bytes(original.artifact_uri) == original_bytes


def test_update_rejects_stale_revision_without_overwriting_head(tmp_path):
    request, store = _request(tmp_path)
    original = store.create_imported(request, "storyforge-local")
    current = store.update(
        original,
        {"review_status": "pending_review"},
        expected_revision_id=original.revision_id,
    )

    with pytest.raises(ProductionError) as raised:
        store.update(
            original,
            {"review_status": "approved"},
            expected_revision_id=original.revision_id,
        )

    assert raised.value.code == "performance_draft_revision_conflict"
    assert store.current(original.draft_id).revision_id == current.revision_id


def test_update_cannot_pair_old_artifact_with_newer_expected_revision(tmp_path):
    request, store = _request(tmp_path)
    original = store.create_imported(request, "storyforge-local")
    current = store.update(
        original,
        {"review_status": "pending_review"},
        expected_revision_id=original.revision_id,
    )

    with pytest.raises(ProductionError) as raised:
        store.update(
            original,
            {"review_status": "approved"},
            expected_revision_id=current.revision_id,
        )

    assert raised.value.code == "performance_draft_revision_conflict"
    assert store.current(original.draft_id).revision_id == current.revision_id


def test_update_rejects_identity_and_source_mutation(tmp_path):
    request, store = _request(tmp_path)
    original = store.create_imported(request, "storyforge-local")

    for forbidden_patch in (
        {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        {"source": copy.deepcopy(original.payload["source"])},
        {"revision_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
    ):
        with pytest.raises(ProductionError) as raised:
            store.update(original, forbidden_patch)
        assert raised.value.code == "performance_draft_patch_invalid"
