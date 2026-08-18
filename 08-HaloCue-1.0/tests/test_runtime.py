from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid

import pytest

from halocue_production.errors import ProductionError
from halocue_production.jobs import CancellationToken, JobRegistry
from halocue_production.repository import ProductionRepository
from halocue_production.runtime import RUNTIME_SCHEMA_VERSION, RuntimeStore


def legacy_run_payload() -> dict:
    return {
        "run_id": "run-000000000001",
        "project": "runtime-test",
        "release_id": "release-000000000001",
        "draft_token": "draft-000000000001",
        "state": "waiting_for_review",
        "current_stage": "review_install",
        "created_at": "2026-08-15T00:00:00+00:00",
        "updated_at": "2026-08-15T00:01:00+00:00",
        "work_items": [
            {
                "key": "source",
                "label": "source",
                "state": "done",
                "progress": 100,
                "detail": "ready",
            },
            {
                "key": "compile",
                "label": "compile",
                "state": "pending",
                "progress": 0,
                "detail": "waiting",
            },
        ],
        "source_summary": {"line_count": 1},
        "pending_build_id": None,
        "last_build_id": None,
        "last_installed_project": None,
    }


def test_runtime_store_creates_current_schema(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")

    assert store.schema_version() == RUNTIME_SCHEMA_VERSION
    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "runtime_schema_migrations",
        "production_runs",
        "work_items",
        "job_attempts",
        "production_events",
        "workspace_files",
        "asset_manifests",
        "production_run_asset_manifest_history",
        "production_run_asset_manifest_heads",
        "frozen_script_releases",
        "production_requests",
    } <= tables


def test_runtime_store_upgrades_v6_to_v7_with_immutable_artifact_refs(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    RuntimeStore(path, target_version=6)
    runtime = RuntimeStore(path)
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(artifact_refs)")
        }

    assert runtime.schema_version() == RUNTIME_SCHEMA_VERSION
    assert {
        "uri",
        "workspace_uri",
        "kind",
        "content_hash",
        "run_id",
        "work_item_id",
        "attempt_id",
        "created_at",
    } <= columns


def test_runtime_store_upgrades_v7_to_v8_with_formal_draft_revisions(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    RuntimeStore(path, target_version=7)

    runtime = RuntimeStore(path)
    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(formal_performance_drafts)"
            )
        }

    assert runtime.schema_version() == RUNTIME_SCHEMA_VERSION
    assert {
        "revision_id",
        "draft_id",
        "run_id",
        "request_id",
        "artifact_uri",
        "content_hash",
        "review_status",
        "parent_revision_id",
        "adapter_id",
        "created_at",
    } <= columns


def test_runtime_store_upgrades_v1_to_current(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    RuntimeStore(path, target_version=1)

    upgraded = RuntimeStore(path)

    assert upgraded.schema_version() == RUNTIME_SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        attempt_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(job_attempts)")
        }
        workspace_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(workspace_files)")
        }
    assert "cancellation_requested" in attempt_columns
    assert {"uri", "relative_path", "content_hash", "size_bytes"} <= workspace_columns


def test_runtime_store_upgrades_v3_to_current_without_losing_runs(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    legacy = RuntimeStore(path, target_version=3)
    production_run_id, _ = legacy.save_production_run(legacy_run_payload())

    upgraded = RuntimeStore(path)

    assert upgraded.schema_version() == RUNTIME_SCHEMA_VERSION
    assert upgraded.get_production_run("run-000000000001")[
        "production_run_id"
    ] == production_run_id
    with sqlite3.connect(path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(asset_manifests)")
        }
    assert {
        "id",
        "workspace_uri",
        "content_hash",
        "file_hash",
        "source_kind",
        "created_at",
    } <= columns


def test_runtime_store_upgrades_v4_manifest_binding_to_versioned_history(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    legacy = RuntimeStore(path, target_version=4)
    production_run_id, _ = legacy.save_production_run(legacy_run_payload())
    uri = "workspace://assets/77777777-7777-4777-8777-777777777777/manifest.json"
    file_hash = "sha256:" + "a" * 64
    legacy.register_workspace_file(
        uri=uri,
        relative_path="assets/77777777-7777-4777-8777-777777777777/manifest.json",
        kind="asset-manifest",
        content_hash=file_hash,
        size_bytes=10,
        media_type="application/json",
        metadata={},
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO asset_manifests(
              id, run_id, workspace_uri, content_hash, file_hash,
              source_kind, created_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "77777777-7777-4777-8777-777777777777",
                production_run_id,
                uri,
                "sha256:" + "b" * 64,
                file_hash,
                "compatibility_empty",
                "2026-08-15T04:05:00+00:00",
            ),
        )

    upgraded = RuntimeStore(path)
    current = upgraded.get_asset_manifest_for_run("run-000000000001")
    history = upgraded.list_asset_manifests_for_run("run-000000000001")

    assert current["id"] == "77777777-7777-4777-8777-777777777777"
    assert current["revision"] == 1
    assert current["production_run_id"] == production_run_id
    assert history == [current]


def test_runtime_store_upgrades_v5_to_current_with_formal_input_tables(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    legacy = RuntimeStore(path, target_version=5)
    production_run_id, _ = legacy.save_production_run(legacy_run_payload())

    upgraded = RuntimeStore(path)

    assert upgraded.schema_version() == RUNTIME_SCHEMA_VERSION
    assert upgraded.get_production_run("run-000000000001")[
        "production_run_id"
    ] == production_run_id
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        release_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(frozen_script_releases)"
            )
        }
        request_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(production_requests)"
            )
        }
        applied = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM runtime_schema_migrations"
            )
        }

    assert {"frozen_script_releases", "production_requests"} <= tables
    assert {
        "id",
        "manifest_uri",
        "content_uri",
        "content_hash",
        "manifest_file_hash",
    } <= release_columns
    assert {
        "id",
        "idempotency_key",
        "request_uri",
        "request_file_hash",
        "release_id",
        "run_id",
    } <= request_columns
    assert 6 in applied


def test_runtime_store_rejects_newer_schema_version(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version=99")

    with pytest.raises(ProductionError) as raised:
        RuntimeStore(path)

    assert raised.value.code == "runtime_database_version_unsupported"
    assert raised.value.status == 409


def test_runtime_store_rejects_corrupt_database(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    path.write_bytes(b"this is not a sqlite database")

    with pytest.raises(ProductionError) as raised:
        RuntimeStore(path)

    assert raised.value.code == "runtime_database_corrupt"
    assert raised.value.status == 500


def test_repository_imports_legacy_run_json_once(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    path = runs_dir / "run-000000000001.json"
    path.write_text(json.dumps(legacy_run_payload()), encoding="utf-8")

    first = ProductionRepository(tmp_path)
    imported = first.get_run("run-000000000001")
    path.write_text("not-json", encoding="utf-8")
    restored = ProductionRepository(tmp_path).get_run("run-000000000001")

    assert imported.project == "runtime-test"
    assert restored.production_run_id == imported.production_run_id
    assert restored.work_items[0].work_item_id == imported.work_items[0].work_item_id


def test_save_run_preserves_formal_work_item_ids(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    payload = legacy_run_payload()

    production_run_id, first_ids = store.save_production_run(payload)
    payload["work_items"][0]["detail"] = "updated"
    same_run_id, second_ids = store.save_production_run(payload)

    assert uuid.UUID(production_run_id).version == 5
    assert same_run_id == production_run_id
    assert second_ids == first_ids
    assert all(uuid.UUID(value) for value in first_ids.values())


def test_attempt_transitions_emit_canonical_sequenced_events(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    production_run_id, work_items = store.save_production_run(legacy_run_payload())
    created = store.create_attempt(
        job_id="job-000000000001",
        kind="compile",
        legacy_run_id="run-000000000001",
        work_item_id=work_items["compile"],
        retry_context={"expected_draft_version": 3},
    )

    assert store.start_attempt(created["attempt_id"]) is True
    assert store.succeed_attempt(created["attempt_id"], {"build_id": "build-test"}) is True
    events = store.events_for_attempt(created["attempt_id"])

    assert [event["sequence"] for event in events] == [1, 2, 3]
    assert [event["kind"] for event in events] == [
        "attempt_queued",
        "operation_started",
        "attempt_succeeded",
    ]
    assert {event["run_id"] for event in events} == {production_run_id}
    assert {event["work_item_id"] for event in events} == {work_items["compile"]}
    assert {event["attempt_id"] for event in events} == {created["attempt_id"]}
    assert all(event["timestamp"] for event in events)


def test_restart_abandons_active_attempt_without_faking_resume(tmp_path):
    path = tmp_path / "runtime.sqlite3"
    store = RuntimeStore(path)
    _, work_items = store.save_production_run(legacy_run_payload())
    created = store.create_attempt(
        job_id="job-000000000001",
        kind="compile",
        legacy_run_id="run-000000000001",
        work_item_id=work_items["compile"],
        retry_context={"expected_draft_version": 3},
    )
    assert store.start_attempt(created["attempt_id"]) is True

    restarted = RuntimeStore(path)
    assert restarted.abandon_active_attempts() == ["job-000000000001"]
    abandoned = restarted.get_attempt("job-000000000001")

    assert abandoned["state"] == "abandoned"
    assert abandoned["error"]["code"] == "attempt_abandoned"
    assert restarted.attempt_accepts_result(abandoned["attempt_id"]) is False


def test_legacy_active_job_import_keeps_run_link_and_emits_abandonment(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite3")
    production_run_id, _ = store.save_production_run(legacy_run_payload())

    store.import_legacy_attempt(
        {
            "job_id": "job-000000000001",
            "kind": "compile",
            "state": "running",
            "created_at": "2026-08-14T00:00:00+00:00",
            "updated_at": "2026-08-14T00:00:01+00:00",
            "run_id": "run-000000000001",
            "retry_context": {"expected_draft_version": 2},
        }
    )
    imported = store.get_attempt("job-000000000001")
    events = store.events_for_attempt(imported["attempt_id"])

    assert imported["state"] == "abandoned"
    assert imported["production_run_id"] == production_run_id
    assert [event["kind"] for event in events] == ["attempt_abandoned"]


def test_retry_creates_new_attempt_for_same_work_item(tmp_path):
    registry = JobRegistry(tmp_path / "jobs")

    first = registry.submit("compile", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        failed = registry.get(first.job_id)
        if failed and failed.state == "failed":
            break
        time.sleep(0.01)

    retry = registry.submit(
        "compile",
        lambda: {"build_id": "build-retry"},
        work_item_id=failed.work_item_id,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        succeeded = registry.get(retry.job_id)
        if succeeded and succeeded.state == "succeeded":
            break
        time.sleep(0.01)

    assert failed.work_item_id == succeeded.work_item_id
    assert failed.attempt_id != succeeded.attempt_id
    assert failed.ordinal == 1
    assert succeeded.ordinal == 2
    registry.close()


def test_running_cancellation_rejects_late_result(tmp_path):
    registry = JobRegistry(tmp_path / "jobs")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def late_result(token: CancellationToken) -> dict:
        started.set()
        release.wait(timeout=3)
        try:
            token.raise_if_cancelled()
            return {"artifact": "late"}
        finally:
            finished.set()

    submitted = registry.submit("compile", late_result)
    assert started.wait(timeout=2)
    assert registry.cancel(submitted.job_id) is True
    release.set()
    assert finished.wait(timeout=2)

    cancelled = registry.get(submitted.job_id)
    assert cancelled.state == "cancelled"
    assert cancelled.result is None
    assert cancelled.cancellation_requested is True
    registry.close()
