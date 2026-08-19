from __future__ import annotations

import errno
import hashlib
import json

import pytest

from halocue_production.artifacts import ArtifactStore
from halocue_production.errors import ProductionError
from halocue_production.runtime import RuntimeStore


def artifact_store(tmp_path):
    runtime = RuntimeStore(tmp_path / "runtime.sqlite3")
    return ArtifactStore(tmp_path / "workspace", runtime), runtime


def test_commit_bytes_is_hashed_registered_and_restart_safe(tmp_path):
    store, runtime = artifact_store(tmp_path)
    content = b"authorized-fixture\n"
    uri = "workspace://assets/backgrounds/rain.png"

    record = store.commit_bytes(
        uri,
        content,
        kind="background",
        metadata={"display_name": "Rain"},
    )

    assert record.uri == uri
    assert record.relative_path == "assets/backgrounds/rain.png"
    assert record.content_hash == "sha256:" + hashlib.sha256(content).hexdigest()
    assert record.size_bytes == len(content)
    assert record.media_type == "image/png"
    assert runtime.get_workspace_file(uri)["content_hash"] == record.content_hash
    assert store.read_bytes(uri) == content
    assert not list((tmp_path / "workspace").rglob("*.tmp"))
    assert str(tmp_path) not in json.dumps(record.to_dict(), ensure_ascii=False)

    restarted = ArtifactStore(
        tmp_path / "workspace", RuntimeStore(tmp_path / "runtime.sqlite3")
    )
    assert restarted.get(uri).content_hash == record.content_hash


def test_publish_artifact_reuses_registered_workspace_bytes_and_is_restart_safe(tmp_path):
    store, runtime = artifact_store(tmp_path)
    workspace = store.commit_bytes(
        "workspace://builds/11111111-1111-4111-8111-111111111111/bundle.json",
        b"{}",
        kind="build-bundle",
        media_type="application/json",
    )

    published = store.publish_artifact(
        "builds",
        "22222222-2222-4222-8222-222222222222",
        workspace,
    )

    assert published.uri == "artifact://builds/22222222-2222-4222-8222-222222222222"
    assert published.workspace_uri == workspace.uri
    assert published.content_hash == workspace.content_hash
    assert published.attempt_id is None
    assert store.read_artifact_bytes(published.uri) == b"{}"

    restarted = ArtifactStore(
        tmp_path / "workspace", RuntimeStore(tmp_path / "runtime.sqlite3")
    )
    assert restarted.get_artifact(published.uri).content_hash == workspace.content_hash


def test_publish_artifact_rejects_abandoned_attempt_result(tmp_path):
    store, runtime = artifact_store(tmp_path)
    payload = {
        "run_id": "run-000000000001",
        "project": "artifact-attempt-test",
        "release_id": "release-000000000001",
        "draft_token": None,
        "state": "waiting_for_review",
        "current_stage": "review_install",
        "created_at": "2026-08-15T00:00:00+00:00",
        "updated_at": "2026-08-15T00:01:00+00:00",
        "work_items": [
            {
                "key": "compile",
                "label": "compile",
                "state": "pending",
                "progress": 0,
                "detail": "waiting",
            }
        ],
        "source_summary": {"line_count": 1},
    }
    _, work_items = runtime.save_production_run(payload)
    created = runtime.create_attempt(
        job_id="job-000000000001",
        kind="adapter_render",
        legacy_run_id=payload["run_id"],
        work_item_id=work_items["compile"],
        retry_context={},
    )
    assert runtime.start_attempt(created["attempt_id"]) is True
    assert runtime.abandon_active_attempts() == [created["job_id"]]

    workspace = store.commit_bytes(
        "workspace://late-results/preview.json",
        b"late result",
        kind="preview",
        media_type="application/json",
    )
    with pytest.raises(ProductionError) as raised:
        store.publish_artifact(
            "late-results",
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            workspace,
            provenance={"attempt_id": created["attempt_id"]},
        )

    assert raised.value.code == "attempt_result_rejected"
    assert raised.value.status == 409
    assert runtime.list_artifact_refs_for_attempt(created["attempt_id"]) == []


def test_publish_artifact_rejects_conflicting_alias_and_unregistered_workspace(tmp_path):
    store, _ = artifact_store(tmp_path)
    workspace = store.commit_bytes(
        "workspace://builds/first/bundle.json", b"first", kind="build-bundle"
    )
    store.publish_artifact("builds", "44444444-4444-4444-8444-444444444444", workspace)

    with pytest.raises(ProductionError) as raised:
        store.publish_artifact(
            "builds",
            "44444444-4444-4444-8444-444444444444",
            store.commit_bytes(
                "workspace://builds/second/bundle.json",
                b"second",
                kind="build-bundle",
            ),
        )
    assert raised.value.code == "artifact_ref_conflict"

    with pytest.raises(ProductionError) as raised:
        store.publish_artifact(
            "builds",
            "55555555-5555-4555-8555-555555555555",
            type(workspace)(
                uri="workspace://missing/file.json",
                relative_path="missing/file.json",
                kind="build-bundle",
                content_hash=workspace.content_hash,
                size_bytes=workspace.size_bytes,
                media_type=workspace.media_type,
                metadata={},
                created_at=workspace.created_at,
                updated_at=workspace.updated_at,
            ),
        )
    assert raised.value.code == "workspace_file_not_found"


@pytest.mark.parametrize(
    ("namespace", "artifact_id"),
    [
        ("../builds", "66666666-6666-4666-8666-666666666666"),
        ("builds", "../66666666-6666-4666-8666-666666666666"),
        ("builds", "not-a-uuid"),
    ],
)
def test_publish_artifact_rejects_invalid_alias_identity(tmp_path, namespace, artifact_id):
    store, _ = artifact_store(tmp_path)
    workspace = store.commit_bytes(
        "workspace://builds/valid/bundle.json", b"valid", kind="build-bundle"
    )

    with pytest.raises(ProductionError) as raised:
        store.publish_artifact(namespace, artifact_id, workspace)

    assert raised.value.code == "artifact_uri_invalid"


def test_commit_file_is_idempotent_and_refuses_different_bytes(tmp_path):
    store, _ = artifact_store(tmp_path)
    source = tmp_path / "source.bin"
    original = b"a" * (1024 * 1024 + 17)
    source.write_bytes(original)
    uri = "workspace://inputs/releases/source.bin"

    first = store.commit_file(uri, source, kind="script")
    second = store.commit_file(
        uri, source, kind="script", metadata={"release_id": "fixture-release"}
    )

    assert second.content_hash == first.content_hash
    assert second.metadata == {"release_id": "fixture-release"}
    source.write_bytes(b"different")
    with pytest.raises(ProductionError) as raised:
        store.commit_file(uri, source, kind="script")
    assert raised.value.code == "workspace_file_conflict"
    assert store.read_bytes(uri) == original


def test_unregistered_matching_file_is_recovered_without_overwrite(tmp_path):
    store, runtime = artifact_store(tmp_path)
    uri = "workspace://inputs/releases/recovered.txt"
    physical = tmp_path / "workspace" / "inputs" / "releases" / "recovered.txt"
    physical.parent.mkdir(parents=True)
    physical.write_bytes(b"already durable")

    record = store.commit_bytes(uri, b"already durable", kind="script")

    assert runtime.get_workspace_file(uri)["content_hash"] == record.content_hash
    assert physical.read_bytes() == b"already durable"


def test_duplicate_content_returns_existing_workspace_uri(tmp_path):
    store, _ = artifact_store(tmp_path)
    original_uri = "workspace://assets/backgrounds/shared.png"
    duplicate_uri = "workspace://assets/popups/shared.png"
    store.commit_bytes(original_uri, b"same-content", kind="background")

    with pytest.raises(ProductionError) as raised:
        store.commit_bytes(duplicate_uri, b"same-content", kind="popup")

    assert raised.value.code == "workspace_file_duplicate"
    assert raised.value.details["existing_uri"] == original_uri
    assert not (tmp_path / "workspace" / "assets" / "popups" / "shared.png").exists()


@pytest.mark.parametrize(
    "uri",
    [
        "workspace://assets/../private/file.bin",
        "workspace://assets/%252e%252e/private/file.bin",
        "workspace://assets/C:/Users/creator/file.bin",
        "workspace://assets/folder\\file.bin",
        "file:///tmp/file.bin",
        "C:/Users/creator/file.bin",
    ],
)
def test_workspace_uri_rejects_traversal_and_physical_paths(tmp_path, uri):
    store, _ = artifact_store(tmp_path)

    with pytest.raises(ProductionError) as raised:
        store.commit_bytes(uri, b"test", kind="asset")

    assert raised.value.code == "workspace_uri_invalid"
    assert not any((tmp_path / "workspace").iterdir())


def test_workspace_path_rejects_symlink_escape(tmp_path):
    store, _ = artifact_store(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    namespace = tmp_path / "workspace" / "assets"
    try:
        namespace.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前 Windows 账户不允许创建测试用符号链接")

    with pytest.raises(ProductionError) as raised:
        store.commit_bytes(
            "workspace://assets/private/file.bin", b"test", kind="asset"
        )

    assert raised.value.code == "workspace_path_invalid"
    assert not list(outside.iterdir())


def test_tampered_workspace_file_is_rejected(tmp_path):
    store, _ = artifact_store(tmp_path)
    uri = "workspace://assets/sounds/chime.wav"
    store.commit_bytes(uri, b"original", kind="sound")
    physical = tmp_path / "workspace" / "assets" / "sounds" / "chime.wav"
    physical.write_bytes(b"tampered")

    with pytest.raises(ProductionError) as raised:
        store.get(uri)

    assert raised.value.code == "artifact_hash_mismatch"
    assert str(tmp_path) not in json.dumps(raised.value.details, ensure_ascii=False)


def test_registration_failure_removes_new_file(tmp_path, monkeypatch):
    store, runtime = artifact_store(tmp_path)
    uri = "workspace://artifacts/manifests/frozen.json"

    def fail_registration(**_):
        raise ProductionError(
            "workspace_file_registration_failed", "fixture failure", status=500
        )

    monkeypatch.setattr(runtime, "register_workspace_file", fail_registration)
    with pytest.raises(ProductionError) as raised:
        store.commit_bytes(uri, b"{}", kind="manifest")

    assert raised.value.code == "workspace_file_registration_failed"
    assert not (tmp_path / "workspace" / "artifacts" / "manifests" / "frozen.json").exists()
    assert not list((tmp_path / "workspace").rglob("*.tmp"))


def test_disk_full_returns_stable_error_and_cleans_temporary_file(
    tmp_path, monkeypatch
):
    store, _ = artifact_store(tmp_path)

    def disk_full(_source, _target):
        raise OSError(errno.ENOSPC, "fixture disk full")

    monkeypatch.setattr("halocue_production.artifacts.os.replace", disk_full)
    with pytest.raises(ProductionError) as raised:
        store.commit_bytes(
            "workspace://artifacts/builds/output.bin", b"payload", kind="build"
        )

    assert raised.value.code == "artifact_storage_full"
    assert raised.value.status == 507
    assert not list((tmp_path / "workspace").rglob("*.tmp"))
