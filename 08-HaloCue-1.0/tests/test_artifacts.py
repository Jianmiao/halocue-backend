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
