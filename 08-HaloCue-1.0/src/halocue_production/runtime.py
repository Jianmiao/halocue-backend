from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .errors import ProductionError


RUNTIME_SCHEMA_VERSION = 9
_RUNTIME_NAMESPACE = uuid.UUID("1e07ec7a-bc62-4b64-97fd-d8ec8764dc46")
_ACTIVE_ATTEMPT_STATES = frozenset({"queued", "running", "started"})

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE runtime_schema_migrations (
          version INTEGER PRIMARY KEY,
          applied_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE production_runs (
          id TEXT PRIMARY KEY,
          legacy_run_id TEXT NOT NULL UNIQUE,
          project TEXT NOT NULL,
          release_id TEXT NOT NULL,
          draft_token TEXT,
          status TEXT NOT NULL,
          legacy_state TEXT NOT NULL,
          current_stage TEXT NOT NULL,
          source_summary_json TEXT NOT NULL,
          pending_build_id TEXT,
          last_build_id TEXT,
          last_installed_project TEXT,
          version INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE work_items (
          id TEXT PRIMARY KEY,
          run_id TEXT,
          legacy_key TEXT,
          type TEXT NOT NULL,
          scope_ref TEXT,
          label TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL NOT NULL DEFAULT 0,
          detail TEXT NOT NULL DEFAULT '',
          acceptance_criteria_json TEXT NOT NULL DEFAULT '{}',
          input_refs_json TEXT NOT NULL DEFAULT '[]',
          output_refs_json TEXT NOT NULL DEFAULT '[]',
          attempt_count INTEGER NOT NULL DEFAULT 0,
          lease_owner TEXT,
          lease_expires_at TEXT,
          ordinal INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          UNIQUE(run_id, legacy_key)
        )
        """,
        """
        CREATE TABLE work_item_dependencies (
          work_item_id TEXT NOT NULL,
          dependency_id TEXT NOT NULL,
          PRIMARY KEY(work_item_id, dependency_id),
          FOREIGN KEY(work_item_id) REFERENCES work_items(id),
          FOREIGN KEY(dependency_id) REFERENCES work_items(id)
        )
        """,
        """
        CREATE TABLE job_attempts (
          id TEXT PRIMARY KEY,
          job_id TEXT NOT NULL UNIQUE,
          work_item_id TEXT NOT NULL,
          run_id TEXT,
          legacy_run_id TEXT,
          kind TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          provider TEXT,
          model_or_engine TEXT,
          request_digest TEXT NOT NULL,
          status TEXT NOT NULL,
          created_at TEXT NOT NULL,
          started_at TEXT,
          finished_at TEXT,
          updated_at TEXT NOT NULL,
          result_json TEXT,
          error_json TEXT,
          retry_context_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(work_item_id) REFERENCES work_items(id),
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          UNIQUE(work_item_id, ordinal)
        )
        """,
        "CREATE INDEX idx_work_items_run_status ON work_items(run_id, status)",
        "CREATE INDEX idx_job_attempts_run_status ON job_attempts(run_id, status)",
    ),
    2: (
        "ALTER TABLE job_attempts ADD COLUMN cancellation_requested INTEGER NOT NULL DEFAULT 0",
        """
        CREATE TABLE production_events (
          event_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          run_id TEXT NOT NULL,
          work_item_id TEXT NOT NULL,
          attempt_id TEXT NOT NULL,
          sequence INTEGER NOT NULL,
          timestamp TEXT NOT NULL,
          payload_json TEXT NOT NULL DEFAULT '{}',
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(work_item_id) REFERENCES work_items(id),
          FOREIGN KEY(attempt_id) REFERENCES job_attempts(id),
          UNIQUE(attempt_id, sequence)
        )
        """,
        "CREATE INDEX idx_production_events_run_sequence ON production_events(run_id, timestamp, sequence)",
    ),
    3: (
        """
        CREATE TABLE workspace_files (
          uri TEXT PRIMARY KEY,
          relative_path TEXT NOT NULL UNIQUE,
          kind TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          size_bytes INTEGER NOT NULL,
          media_type TEXT NOT NULL,
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX idx_workspace_files_hash ON workspace_files(content_hash)",
    ),
    4: (
        """
        CREATE TABLE asset_manifests (
          id TEXT PRIMARY KEY,
          run_id TEXT NOT NULL UNIQUE,
          workspace_uri TEXT NOT NULL UNIQUE,
          content_hash TEXT NOT NULL,
          file_hash TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(workspace_uri) REFERENCES workspace_files(uri)
        )
        """,
    ),
    5: (
        "ALTER TABLE asset_manifests RENAME TO asset_manifests_v4",
        """
        CREATE TABLE asset_manifests (
          id TEXT PRIMARY KEY,
          workspace_uri TEXT NOT NULL UNIQUE,
          content_hash TEXT NOT NULL,
          file_hash TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(workspace_uri) REFERENCES workspace_files(uri)
        )
        """,
        """
        INSERT INTO asset_manifests(
          id, workspace_uri, content_hash, file_hash, source_kind, created_at
        )
        SELECT id, workspace_uri, content_hash, file_hash, source_kind, created_at
        FROM asset_manifests_v4
        """,
        """
        CREATE TABLE production_run_asset_manifest_history (
          run_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          manifest_id TEXT NOT NULL,
          predecessor_manifest_id TEXT,
          selection_kind TEXT NOT NULL,
          selected_at TEXT NOT NULL,
          PRIMARY KEY(run_id, ordinal),
          UNIQUE(run_id, manifest_id),
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(manifest_id) REFERENCES asset_manifests(id),
          FOREIGN KEY(predecessor_manifest_id) REFERENCES asset_manifests(id)
        )
        """,
        """
        INSERT INTO production_run_asset_manifest_history(
          run_id, ordinal, manifest_id, predecessor_manifest_id,
          selection_kind, selected_at
        )
        SELECT run_id, 1, id, NULL, 'initial', created_at
        FROM asset_manifests_v4
        """,
        """
        CREATE TABLE production_run_asset_manifest_heads (
          run_id TEXT PRIMARY KEY,
          manifest_id TEXT NOT NULL,
          ordinal INTEGER NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(manifest_id) REFERENCES asset_manifests(id)
        )
        """,
        """
        INSERT INTO production_run_asset_manifest_heads(
          run_id, manifest_id, ordinal, updated_at
        )
        SELECT run_id, id, 1, created_at
        FROM asset_manifests_v4
        """,
        "CREATE INDEX idx_asset_manifest_history_manifest ON production_run_asset_manifest_history(manifest_id)",
        "DROP TABLE asset_manifests_v4",
    ),
    6: (
        """
        CREATE TABLE frozen_script_releases (
          id TEXT PRIMARY KEY,
          schema_version TEXT NOT NULL,
          work_id TEXT NOT NULL,
          display_version TEXT NOT NULL,
          manifest_uri TEXT NOT NULL UNIQUE,
          manifest_file_hash TEXT NOT NULL,
          content_uri TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          canon_revision_id TEXT NOT NULL,
          writing_pack_version TEXT NOT NULL,
          source_revision_ids_json TEXT NOT NULL,
          gate_snapshot_ids_json TEXT NOT NULL,
          released_by TEXT NOT NULL,
          released_at TEXT NOT NULL,
          frozen_at TEXT NOT NULL,
          FOREIGN KEY(manifest_uri) REFERENCES workspace_files(uri),
          FOREIGN KEY(content_uri) REFERENCES workspace_files(uri)
        )
        """,
        """
        CREATE TABLE production_requests (
          id TEXT PRIMARY KEY,
          schema_version TEXT NOT NULL,
          idempotency_key TEXT NOT NULL UNIQUE,
          request_uri TEXT NOT NULL UNIQUE,
          request_file_hash TEXT NOT NULL,
          release_id TEXT NOT NULL UNIQUE,
          run_id TEXT NOT NULL UNIQUE,
          production_display_name TEXT NOT NULL,
          asset_manifest_id TEXT NOT NULL,
          target TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(request_uri) REFERENCES workspace_files(uri),
          FOREIGN KEY(release_id) REFERENCES frozen_script_releases(id),
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(asset_manifest_id) REFERENCES asset_manifests(id)
        )
        """,
        "CREATE INDEX idx_frozen_script_releases_content_hash ON frozen_script_releases(content_hash)",
    ),
    7: (
        """
        CREATE TABLE artifact_refs (
          uri TEXT PRIMARY KEY,
          workspace_uri TEXT NOT NULL,
          kind TEXT NOT NULL,
          content_hash TEXT NOT NULL,
          run_id TEXT,
          work_item_id TEXT,
          attempt_id TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(workspace_uri) REFERENCES workspace_files(uri),
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(work_item_id) REFERENCES work_items(id),
          FOREIGN KEY(attempt_id) REFERENCES job_attempts(id)
        )
        """,
        "CREATE INDEX idx_artifact_refs_workspace ON artifact_refs(workspace_uri)",
        "CREATE INDEX idx_artifact_refs_attempt ON artifact_refs(attempt_id)",
    ),
    8: (
        """
        CREATE TABLE formal_performance_drafts (
          revision_id TEXT PRIMARY KEY,
          draft_id TEXT NOT NULL,
          run_id TEXT,
          request_id TEXT,
          artifact_uri TEXT NOT NULL UNIQUE,
          content_hash TEXT NOT NULL,
          review_status TEXT NOT NULL,
          parent_revision_id TEXT,
          adapter_id TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(run_id) REFERENCES production_runs(id),
          FOREIGN KEY(artifact_uri) REFERENCES artifact_refs(uri),
          FOREIGN KEY(parent_revision_id) REFERENCES formal_performance_drafts(revision_id)
        )
        """,
        "CREATE INDEX idx_formal_performance_drafts_head ON formal_performance_drafts(draft_id, created_at)",
        "CREATE INDEX idx_formal_performance_drafts_request ON formal_performance_drafts(request_id)",
    ),
    9: (
        """
        CREATE TABLE production_run_identity_map (
          legacy_run_id TEXT PRIMARY KEY,
          production_run_id TEXT NOT NULL UNIQUE,
          content_hash TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(production_run_id) REFERENCES production_runs(id)
        )
        """,
        "CREATE INDEX idx_production_run_identity_map_production ON production_run_identity_map(production_run_id)",
    ),
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _json_value(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def production_run_identity_hash(payload: dict[str, Any]) -> str:
    """Hash only the immutable source identity, excluding runtime state."""
    work_items = []
    for item in payload.get("work_items") or []:
        if not isinstance(item, dict):
            continue
        work_items.append(
            {
                "key": str(item.get("key") or ""),
                "label": str(item.get("label") or ""),
            }
        )
    envelope = {
        "project": str(payload.get("project") or ""),
        "release_id": str(payload.get("release_id") or ""),
        "source_summary": payload.get("source_summary") or {},
        "work_items": work_items,
    }
    return "sha256:" + hashlib.sha256(_json(envelope).encode("utf-8")).hexdigest()


def _canonical_uuid(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = uuid.UUID(text)
    except (AttributeError, ValueError):
        return None
    return text if str(parsed) == text else None


def production_run_uuid(legacy_run_id: str) -> str:
    return str(uuid.uuid5(_RUNTIME_NAMESPACE, f"production-run:{legacy_run_id}"))


def legacy_work_item_uuid(production_run_id: str, key: str) -> str:
    return str(uuid.uuid5(_RUNTIME_NAMESPACE, f"work-item:{production_run_id}:{key}"))


def _runtime_status(legacy_state: str) -> str:
    if legacy_state in {"generating_direction", "compiling"}:
        return "running"
    if legacy_state in {"waiting_for_review", "ready_to_compile"}:
        return "waiting_user"
    if legacy_state in {"compiled", "installed"}:
        return "succeeded"
    if legacy_state in {
        "direction_failed",
        "compile_failed",
        "direction_interrupted",
        "compile_interrupted",
    }:
        return "blocked"
    if legacy_state == "cancelled":
        return "cancelled"
    return "queued"


class RuntimeStore:
    def __init__(self, path: Path, *, target_version: int = RUNTIME_SCHEMA_VERSION) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if target_version < 1 or target_version > RUNTIME_SCHEMA_VERSION:
            raise ValueError("unsupported runtime schema target")
        self._migrate(target_version)
        self._identity_map_enabled = self.schema_version() >= 9
        if self._identity_map_enabled:
            self._backfill_production_run_identity_map()
        self._verify()

    @staticmethod
    def _database_error(exc: Exception) -> ProductionError:
        return ProductionError(
            "runtime_database_corrupt",
            "制作运行数据库无法读取，请从备份恢复或联系支持",
            status=500,
        )

    @staticmethod
    def _migration_error(version: int, exc: Exception) -> ProductionError:
        return ProductionError(
            "runtime_database_migration_failed",
            "制作运行数据库升级失败，请从备份恢复或联系支持",
            status=500,
            details={"version": int(version)},
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except (sqlite3.DatabaseError, OSError) as exc:
            raise self._database_error(exc) from exc

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except sqlite3.DatabaseError as exc:
                connection.rollback()
                raise self._database_error(exc) from exc
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self, target_version: int) -> None:
        with self._lock:
            connection = self._connect()
            try:
                current = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if current > RUNTIME_SCHEMA_VERSION:
                    raise ProductionError(
                        "runtime_database_version_unsupported",
                        "制作运行数据库来自更新版本，当前程序无法打开",
                        status=409,
                        details={
                            "received": current,
                            "supported": RUNTIME_SCHEMA_VERSION,
                        },
                    )
                for version in range(current + 1, target_version + 1):
                    try:
                        connection.execute("BEGIN IMMEDIATE")
                        for statement in _MIGRATIONS[version]:
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO runtime_schema_migrations(version, applied_at) VALUES(?, ?)",
                            (version, _now()),
                        )
                        connection.execute(f"PRAGMA user_version={version}")
                        connection.commit()
                    except ProductionError:
                        connection.rollback()
                        raise
                    except Exception as exc:
                        connection.rollback()
                        raise self._migration_error(version, exc) from exc
            except sqlite3.DatabaseError as exc:
                raise self._database_error(exc) from exc
            finally:
                connection.close()

    def _verify(self) -> None:
        connection = self._connect()
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
            if not result or str(result[0]).casefold() != "ok":
                raise ProductionError(
                    "runtime_database_corrupt",
                    "制作运行数据库完整性检查失败",
                    status=500,
                )
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _run_row(
        connection: sqlite3.Connection, identifier: str
    ) -> sqlite3.Row | None:
        column = "id" if _canonical_uuid(identifier) else "legacy_run_id"
        return connection.execute(
            f"SELECT id, legacy_run_id FROM production_runs WHERE {column}=?",
            (str(identifier),),
        ).fetchone()

    def schema_version(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _identity_payload_from_row(
        connection: sqlite3.Connection, row: sqlite3.Row
    ) -> dict[str, Any]:
        work_items = connection.execute(
            "SELECT legacy_key, label FROM work_items WHERE run_id=? ORDER BY ordinal, id",
            (str(row["id"]),),
        ).fetchall()
        return {
            "project": str(row["project"]),
            "release_id": str(row["release_id"]),
            "source_summary": _json_value(row["source_summary_json"], {}),
            "work_items": [
                {"key": str(item["legacy_key"] or ""), "label": str(item["label"] or "")}
                for item in work_items
            ],
        }

    def _backfill_production_run_identity_map(self) -> None:
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT id, legacy_run_id, project, release_id, source_summary_json, created_at, updated_at FROM production_runs"
            ).fetchall()
            for row in rows:
                content_hash = production_run_identity_hash(
                    self._identity_payload_from_row(connection, row)
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO production_run_identity_map(
                      legacy_run_id, production_run_id, content_hash, created_at, updated_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        str(row["legacy_run_id"]),
                        str(row["id"]),
                        content_hash,
                        str(row["created_at"]),
                        str(row["updated_at"]),
                    ),
                )

    @staticmethod
    def _workspace_file_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "uri": str(row["uri"]),
            "relative_path": str(row["relative_path"]),
            "kind": str(row["kind"]),
            "content_hash": str(row["content_hash"]),
            "size_bytes": int(row["size_bytes"]),
            "media_type": str(row["media_type"]),
            "metadata": _json_value(row["metadata_json"], {}),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
        }

    def get_workspace_file(self, uri: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM workspace_files WHERE uri=?", (uri,)
            ).fetchone()
            return self._workspace_file_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def find_workspace_file_by_hash(
        self, content_hash: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM workspace_files WHERE content_hash=?", (content_hash,)
            ).fetchone()
            return self._workspace_file_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def register_workspace_file(
        self,
        *,
        uri: str,
        relative_path: str,
        kind: str,
        content_hash: str,
        size_bytes: int,
        media_type: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        now = _now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM workspace_files WHERE uri=?", (uri,)
            ).fetchone()
            if existing:
                immutable_fields = {
                    "relative_path": relative_path,
                    "kind": kind,
                    "content_hash": content_hash,
                    "size_bytes": int(size_bytes),
                    "media_type": media_type,
                }
                if any(
                    str(existing[field]) != str(value)
                    for field, value in immutable_fields.items()
                ):
                    raise ProductionError(
                        "workspace_file_conflict",
                        "同一工作区 URI 已登记不同内容",
                        status=409,
                        details={"uri": uri, "existing_hash": str(existing["content_hash"])},
                    )
                connection.execute(
                    "UPDATE workspace_files SET metadata_json=?, updated_at=? WHERE uri=?",
                    (_json(metadata), now, uri),
                )
            else:
                duplicate = connection.execute(
                    "SELECT uri FROM workspace_files WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if duplicate:
                    raise ProductionError(
                        "workspace_file_duplicate",
                        "相同内容已在工作区登记",
                        status=409,
                        details={
                            "uri": uri,
                            "existing_uri": str(duplicate["uri"]),
                            "content_hash": content_hash,
                        },
                    )
                try:
                    connection.execute(
                        """
                        INSERT INTO workspace_files(
                          uri, relative_path, kind, content_hash, size_bytes,
                          media_type, metadata_json, created_at, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            uri,
                            relative_path,
                            kind,
                            content_hash,
                            int(size_bytes),
                            media_type,
                            _json(metadata),
                            now,
                            now,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    duplicate = connection.execute(
                        "SELECT uri FROM workspace_files WHERE content_hash=?",
                        (content_hash,),
                    ).fetchone()
                    if duplicate:
                        raise ProductionError(
                            "workspace_file_duplicate",
                            "相同内容已在工作区登记",
                            status=409,
                            details={
                                "uri": uri,
                                "existing_uri": str(duplicate["uri"]),
                                "content_hash": content_hash,
                            },
                        ) from exc
                    raise ProductionError(
                        "workspace_file_conflict",
                        "工作区文件路径已登记到其他 URI",
                        status=409,
                        details={"uri": uri},
                    ) from exc
            row = connection.execute(
                "SELECT * FROM workspace_files WHERE uri=?", (uri,)
            ).fetchone()
            if row is None:
                raise ProductionError(
                    "workspace_file_registration_failed",
                    "工作区文件登记失败",
                    status=500,
                )
            return self._workspace_file_payload(row)

    @staticmethod
    def _artifact_ref_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "uri": str(row["uri"]),
            "workspace_uri": str(row["workspace_uri"]),
            "kind": str(row["kind"]),
            "content_hash": str(row["content_hash"]),
            "run_id": row["run_id"],
            "work_item_id": row["work_item_id"],
            "attempt_id": row["attempt_id"],
            "created_at": str(row["created_at"]),
        }

    def register_artifact_ref(
        self,
        *,
        uri: str,
        workspace_uri: str,
        kind: str,
        content_hash: str,
        run_id: str | None = None,
        work_item_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._transaction() as connection:
            if attempt_id is not None:
                attempt = connection.execute(
                    "SELECT status, cancellation_requested FROM job_attempts WHERE id=?",
                    (attempt_id,),
                ).fetchone()
                if attempt is None:
                    raise ProductionError(
                        "attempt_result_rejected",
                        "Artifact 来源 Attempt 不存在，已拒绝发布结果",
                        status=409,
                        details={"attempt_id": attempt_id},
                    )
                if (
                    str(attempt["status"]) != "running"
                    or bool(attempt["cancellation_requested"])
                ):
                    raise ProductionError(
                        "attempt_result_rejected",
                        "Artifact 来源 Attempt 已不可发布结果",
                        status=409,
                        details={
                            "attempt_id": attempt_id,
                            "state": str(attempt["status"]),
                        },
                    )
            workspace = connection.execute(
                "SELECT content_hash FROM workspace_files WHERE uri=?",
                (workspace_uri,),
            ).fetchone()
            if workspace is None:
                raise ProductionError(
                    "workspace_file_not_found",
                    "artifact 引用的工作区文件尚未登记",
                    status=404,
                    details={"uri": workspace_uri},
                )
            if str(workspace["content_hash"]) != content_hash:
                raise ProductionError(
                    "artifact_hash_mismatch",
                    "artifact 引用哈希与工作区登记不一致",
                    status=409,
                    details={"uri": workspace_uri},
                )
            existing = connection.execute(
                "SELECT * FROM artifact_refs WHERE uri=?", (uri,)
            ).fetchone()
            if existing is not None:
                immutable = {
                    "workspace_uri": workspace_uri,
                    "kind": kind,
                    "content_hash": content_hash,
                    "run_id": run_id,
                    "work_item_id": work_item_id,
                    "attempt_id": attempt_id,
                }
                if any(str(existing[key]) != str(value) for key, value in immutable.items()):
                    raise ProductionError(
                        "artifact_ref_conflict",
                        "同一 artifact URI 已登记不同内容或来源",
                        status=409,
                        details={"uri": uri},
                    )
                return self._artifact_ref_payload(existing)
            try:
                connection.execute(
                    """
                    INSERT INTO artifact_refs(
                      uri, workspace_uri, kind, content_hash,
                      run_id, work_item_id, attempt_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (
                        uri,
                        workspace_uri,
                        kind,
                        content_hash,
                        run_id,
                        work_item_id,
                        attempt_id,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "artifact_ref_conflict",
                    "artifact URI 或关联运行记录已被占用",
                    status=409,
                    details={"uri": uri},
                ) from exc
            row = connection.execute(
                "SELECT * FROM artifact_refs WHERE uri=?", (uri,)
            ).fetchone()
            if row is None:
                raise ProductionError(
                    "artifact_ref_registration_failed",
                    "artifact 引用登记失败",
                    status=500,
                )
            return self._artifact_ref_payload(row)

    def get_artifact_ref(self, uri: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM artifact_refs WHERE uri=?", (uri,)
            ).fetchone()
            return self._artifact_ref_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def list_artifact_refs_for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM artifact_refs WHERE attempt_id=? ORDER BY created_at, uri",
                (attempt_id,),
            ).fetchall()
            return [self._artifact_ref_payload(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _formal_performance_draft_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision_id": str(row["revision_id"]),
            "draft_id": str(row["draft_id"]),
            "run_id": row["run_id"],
            "request_id": row["request_id"],
            "artifact_uri": str(row["artifact_uri"]),
            "content_hash": str(row["content_hash"]),
            "review_status": str(row["review_status"]),
            "parent_revision_id": row["parent_revision_id"],
            "adapter_id": str(row["adapter_id"]),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _formal_performance_draft_head_row(
        connection: sqlite3.Connection, draft_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM formal_performance_drafts
            WHERE draft_id=?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (draft_id,),
        ).fetchone()

    def register_formal_performance_draft(
        self,
        *,
        revision_id: str,
        draft_id: str,
        artifact_uri: str,
        content_hash: str,
        review_status: str,
        adapter_id: str,
        created_at: str,
        run_id: str | None = None,
        request_id: str | None = None,
        parent_revision_id: str | None = None,
    ) -> dict[str, Any]:
        values = {
            "draft_id": draft_id,
            "run_id": run_id,
            "request_id": request_id,
            "artifact_uri": artifact_uri,
            "content_hash": content_hash,
            "review_status": review_status,
            "parent_revision_id": parent_revision_id,
            "adapter_id": adapter_id,
            "created_at": created_at,
        }
        with self._transaction() as connection:
            artifact = connection.execute(
                "SELECT kind FROM artifact_refs WHERE uri=?", (artifact_uri,)
            ).fetchone()
            if artifact is None:
                raise ProductionError(
                    "artifact_ref_not_found",
                    "PerformanceDraft 的 artifact 引用尚未登记",
                    status=404,
                    details={"uri": artifact_uri},
                )
            if str(artifact["kind"]) != "performance-draft":
                raise ProductionError(
                    "performance_draft_artifact_invalid",
                    "artifact 引用不是 PerformanceDraft",
                    status=409,
                    details={"uri": artifact_uri},
                )
            existing = connection.execute(
                "SELECT * FROM formal_performance_drafts WHERE revision_id=?",
                (revision_id,),
            ).fetchone()
            if existing is not None:
                if any(str(existing[key]) != str(value) for key, value in values.items()):
                    raise ProductionError(
                        "performance_draft_revision_identity_conflict",
                        "同一 PerformanceDraft Revision ID 已登记不同内容",
                        status=409,
                        details={"revision_id": revision_id},
                    )
                return self._formal_performance_draft_payload(existing)
            head = self._formal_performance_draft_head_row(connection, draft_id)
            if parent_revision_id is None:
                if head is not None:
                    raise ProductionError(
                        "performance_draft_identity_conflict",
                        "同一 PerformanceDraft ID 已存在不同初始 Revision",
                        status=409,
                        details={
                            "draft_id": draft_id,
                            "current_revision_id": str(head["revision_id"]),
                        },
                    )
            elif head is None or str(head["revision_id"]) != parent_revision_id:
                raise ProductionError(
                    "performance_draft_revision_conflict",
                    "PerformanceDraft 已被其他操作更新",
                    status=409,
                    details={
                        "draft_id": draft_id,
                        "expected_revision_id": parent_revision_id,
                        "current_revision_id": (
                            str(head["revision_id"]) if head is not None else None
                        ),
                    },
                )
            try:
                connection.execute(
                    """
                    INSERT INTO formal_performance_drafts(
                      revision_id, draft_id, run_id, request_id, artifact_uri,
                      content_hash, review_status, parent_revision_id,
                      adapter_id, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        revision_id,
                        draft_id,
                        run_id,
                        request_id,
                        artifact_uri,
                        content_hash,
                        review_status,
                        parent_revision_id,
                        adapter_id,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "performance_draft_revision_conflict",
                    "PerformanceDraft Revision 或 artifact 引用已被占用",
                    status=409,
                    details={
                        "draft_id": draft_id,
                        "revision_id": revision_id,
                    },
                ) from exc
            row = connection.execute(
                "SELECT * FROM formal_performance_drafts WHERE revision_id=?",
                (revision_id,),
            ).fetchone()
            return self._formal_performance_draft_payload(row)

    def get_formal_performance_draft_revision(
        self, revision_id: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM formal_performance_drafts WHERE revision_id=?",
                (revision_id,),
            ).fetchone()
            return self._formal_performance_draft_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def get_formal_performance_draft_by_artifact(
        self, artifact_uri: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM formal_performance_drafts WHERE artifact_uri=?",
                (artifact_uri,),
            ).fetchone()
            return self._formal_performance_draft_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def get_formal_performance_draft_head(
        self, draft_id: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = self._formal_performance_draft_head_row(connection, draft_id)
            return self._formal_performance_draft_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def list_formal_performance_draft_revisions(
        self, draft_id: str
    ) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM formal_performance_drafts
                WHERE draft_id=?
                ORDER BY rowid
                """,
                (draft_id,),
            ).fetchall()
            return [self._formal_performance_draft_payload(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _asset_manifest_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "run_id": str(row["legacy_run_id"]),
            "production_run_id": str(row["production_run_id"]),
            "workspace_uri": str(row["workspace_uri"]),
            "content_hash": str(row["content_hash"]),
            "file_hash": str(row["file_hash"]),
            "source_kind": str(row["source_kind"]),
            "created_at": str(row["created_at"]),
            "revision": int(row["ordinal"]),
            "predecessor_manifest_id": row["predecessor_manifest_id"],
            "selection_kind": str(row["selection_kind"]),
            "selected_at": str(row["selected_at"]),
        }

    @staticmethod
    def _current_asset_manifest_row(
        connection: sqlite3.Connection, production_run_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT manifests.*, runs.id AS production_run_id,
                   runs.legacy_run_id, heads.ordinal,
                   history.predecessor_manifest_id,
                   history.selection_kind, history.selected_at
            FROM production_run_asset_manifest_heads AS heads
            JOIN production_runs AS runs ON runs.id=heads.run_id
            JOIN asset_manifests AS manifests ON manifests.id=heads.manifest_id
            JOIN production_run_asset_manifest_history AS history
              ON history.run_id=heads.run_id AND history.ordinal=heads.ordinal
            WHERE heads.run_id=?
            """,
            (production_run_id,),
        ).fetchone()

    @staticmethod
    def _register_asset_manifest(
        connection: sqlite3.Connection,
        *,
        manifest_id: str,
        workspace_uri: str,
        content_hash: str,
        file_hash: str,
        source_kind: str,
        created_at: str,
    ) -> None:
        workspace_file = connection.execute(
            "SELECT content_hash FROM workspace_files WHERE uri=?",
            (workspace_uri,),
        ).fetchone()
        if workspace_file is None:
            raise ProductionError(
                "workspace_file_not_found",
                "AssetManifest 工作区文件尚未登记",
                status=404,
                details={"uri": workspace_uri},
            )
        if str(workspace_file["content_hash"]) != file_hash:
            raise ProductionError(
                "asset_manifest_file_hash_mismatch",
                "AssetManifest 文件哈希与工作区登记不一致",
                status=409,
                details={"uri": workspace_uri},
            )
        values = {
            "workspace_uri": workspace_uri,
            "content_hash": content_hash,
            "file_hash": file_hash,
            "source_kind": source_kind,
            "created_at": created_at,
        }
        existing = connection.execute(
            "SELECT * FROM asset_manifests WHERE id=?", (manifest_id,)
        ).fetchone()
        if existing is not None:
            if any(str(existing[key]) != str(value) for key, value in values.items()):
                raise ProductionError(
                    "asset_manifest_identity_conflict",
                    "同一 AssetManifest ID 已登记不同内容",
                    status=409,
                    details={"manifest_id": manifest_id},
                )
            return
        uri_owner = connection.execute(
            "SELECT id FROM asset_manifests WHERE workspace_uri=?", (workspace_uri,)
        ).fetchone()
        if uri_owner is not None:
            raise ProductionError(
                "asset_manifest_identity_conflict",
                "AssetManifest 工作区 URI 已属于其他清单",
                status=409,
                details={"manifest_id": manifest_id, "uri": workspace_uri},
            )
        connection.execute(
            """
            INSERT INTO asset_manifests(
              id, workspace_uri, content_hash, file_hash, source_kind, created_at
            ) VALUES(?,?,?,?,?,?)
            """,
            (
                manifest_id,
                workspace_uri,
                content_hash,
                file_hash,
                source_kind,
                created_at,
            ),
        )

    def bind_asset_manifest(
        self,
        *,
        legacy_run_id: str,
        manifest_id: str,
        workspace_uri: str,
        content_hash: str,
        file_hash: str,
        source_kind: str,
        created_at: str,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            run = self._run_row(connection, legacy_run_id)
            if run is None:
                raise ProductionError("run_not_found", "制作任务不存在", status=404)
            production_run_id = str(run["id"])
            current = self._current_asset_manifest_row(connection, production_run_id)
            if current is not None:
                if str(current["id"]) != manifest_id:
                    raise ProductionError(
                        "asset_manifest_conflict",
                        "同一制作任务已冻结不同的 AssetManifest",
                        status=409,
                        details={
                            "run_id": legacy_run_id,
                            "existing_manifest_id": str(current["id"]),
                        },
                    )
                self._register_asset_manifest(
                    connection,
                    manifest_id=manifest_id,
                    workspace_uri=workspace_uri,
                    content_hash=content_hash,
                    file_hash=file_hash,
                    source_kind=source_kind,
                    created_at=created_at,
                )
                return self._asset_manifest_payload(current)
            try:
                self._register_asset_manifest(
                    connection,
                    manifest_id=manifest_id,
                    workspace_uri=workspace_uri,
                    content_hash=content_hash,
                    file_hash=file_hash,
                    source_kind=source_kind,
                    created_at=created_at,
                )
                connection.execute(
                    """
                    INSERT INTO production_run_asset_manifest_history(
                      run_id, ordinal, manifest_id, predecessor_manifest_id,
                      selection_kind, selected_at
                    ) VALUES(?,1,?,NULL,'initial',?)
                    """,
                    (production_run_id, manifest_id, created_at),
                )
                connection.execute(
                    """
                    INSERT INTO production_run_asset_manifest_heads(
                      run_id, manifest_id, ordinal, updated_at
                    ) VALUES(?,?,1,?)
                    """,
                    (production_run_id, manifest_id, created_at),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "asset_manifest_conflict",
                    "AssetManifest 初始绑定与已有记录冲突",
                    status=409,
                    details={"run_id": legacy_run_id, "manifest_id": manifest_id},
                ) from exc
            row = self._current_asset_manifest_row(connection, production_run_id)
            return self._asset_manifest_payload(row)

    def advance_asset_manifest(
        self,
        *,
        legacy_run_id: str,
        expected_manifest_id: str,
        expected_content_hash: str,
        manifest_id: str,
        workspace_uri: str,
        content_hash: str,
        file_hash: str,
        source_kind: str,
        created_at: str,
        selection_kind: str,
    ) -> dict[str, Any]:
        selected_at = _now()
        with self._transaction() as connection:
            run = self._run_row(connection, legacy_run_id)
            if run is None:
                raise ProductionError("run_not_found", "制作任务不存在", status=404)
            production_run_id = str(run["id"])
            current = self._current_asset_manifest_row(connection, production_run_id)
            if current is None:
                raise ProductionError(
                    "asset_manifest_not_found",
                    "制作任务尚未冻结 AssetManifest",
                    status=409,
                    details={"run_id": legacy_run_id},
                )
            if (
                str(current["id"]) != expected_manifest_id
                or str(current["content_hash"]) != expected_content_hash
            ):
                if (
                    str(current["id"]) == manifest_id
                    and str(current["content_hash"]) == content_hash
                ):
                    return self._asset_manifest_payload(current)
                raise ProductionError(
                    "asset_manifest_revision_conflict",
                    "AssetManifest 已被其他操作升级",
                    status=409,
                    details={
                        "run_id": legacy_run_id,
                        "current_manifest_id": str(current["id"]),
                        "current_content_hash": str(current["content_hash"]),
                    },
                )
            self._register_asset_manifest(
                connection,
                manifest_id=manifest_id,
                workspace_uri=workspace_uri,
                content_hash=content_hash,
                file_hash=file_hash,
                source_kind=source_kind,
                created_at=created_at,
            )
            if manifest_id == str(current["id"]):
                return self._asset_manifest_payload(current)
            ordinal = int(current["ordinal"]) + 1
            try:
                connection.execute(
                    """
                    INSERT INTO production_run_asset_manifest_history(
                      run_id, ordinal, manifest_id, predecessor_manifest_id,
                      selection_kind, selected_at
                    ) VALUES(?,?,?,?,?,?)
                    """,
                    (
                        production_run_id,
                        ordinal,
                        manifest_id,
                        str(current["id"]),
                        selection_kind,
                        selected_at,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE production_run_asset_manifest_heads
                    SET manifest_id=?, ordinal=?, updated_at=?
                    WHERE run_id=? AND manifest_id=? AND ordinal=?
                    """,
                    (
                        manifest_id,
                        ordinal,
                        selected_at,
                        production_run_id,
                        expected_manifest_id,
                        int(current["ordinal"]),
                    ),
                )
                if updated.rowcount != 1:
                    raise ProductionError(
                        "asset_manifest_revision_conflict",
                        "AssetManifest 已被其他操作升级",
                        status=409,
                        details={"run_id": legacy_run_id},
                    )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "asset_manifest_revision_conflict",
                    "AssetManifest 后继版本与已有历史冲突",
                    status=409,
                    details={"run_id": legacy_run_id, "manifest_id": manifest_id},
                ) from exc
            row = self._current_asset_manifest_row(connection, production_run_id)
            return self._asset_manifest_payload(row)

    def get_asset_manifest_for_run(
        self, legacy_run_id: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT manifests.*, runs.id AS production_run_id,
                       runs.legacy_run_id, heads.ordinal,
                       history.predecessor_manifest_id,
                       history.selection_kind, history.selected_at
                FROM production_run_asset_manifest_heads AS heads
                JOIN production_runs AS runs ON runs.id=heads.run_id
                JOIN asset_manifests AS manifests ON manifests.id=heads.manifest_id
                JOIN production_run_asset_manifest_history AS history
                  ON history.run_id=heads.run_id AND history.ordinal=heads.ordinal
                WHERE runs.id=(
                  SELECT id FROM production_runs
                  WHERE id=? OR legacy_run_id=?
                )
                """,
                (legacy_run_id, legacy_run_id),
            ).fetchone()
            return self._asset_manifest_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def list_asset_manifests_for_run(
        self, legacy_run_id: str
    ) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT manifests.*, runs.id AS production_run_id,
                       runs.legacy_run_id, history.ordinal,
                       history.predecessor_manifest_id,
                       history.selection_kind, history.selected_at
                FROM production_run_asset_manifest_history AS history
                JOIN production_runs AS runs ON runs.id=history.run_id
                JOIN asset_manifests AS manifests ON manifests.id=history.manifest_id
                WHERE runs.id=(
                  SELECT id FROM production_runs
                  WHERE id=? OR legacy_run_id=?
                )
                ORDER BY history.ordinal
                """,
                (legacy_run_id, legacy_run_id),
            ).fetchall()
            return [self._asset_manifest_payload(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _frozen_script_release_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "schema_version": str(row["schema_version"]),
            "id": str(row["id"]),
            "work_id": str(row["work_id"]),
            "display_version": str(row["display_version"]),
            "manifest_uri": str(row["manifest_uri"]),
            "manifest_file_hash": str(row["manifest_file_hash"]),
            "content_uri": str(row["content_uri"]),
            "content_hash": str(row["content_hash"]),
            "canon_revision_id": str(row["canon_revision_id"]),
            "writing_pack_version": str(row["writing_pack_version"]),
            "source_revision_ids": _json_value(row["source_revision_ids_json"], []),
            "gate_snapshot_ids": _json_value(row["gate_snapshot_ids_json"], []),
            "released_by": str(row["released_by"]),
            "released_at": str(row["released_at"]),
            "frozen_at": str(row["frozen_at"]),
        }

    def register_frozen_script_release(
        self,
        payload: dict[str, Any],
        *,
        manifest_file_hash: str,
    ) -> dict[str, Any]:
        release_id = str(payload["id"])
        manifest_uri = str(payload["manifest_uri"])
        content_uri = str(payload["content_uri"])
        with self._transaction() as connection:
            manifest_file = connection.execute(
                "SELECT content_hash FROM workspace_files WHERE uri=?",
                (manifest_uri,),
            ).fetchone()
            content_file = connection.execute(
                "SELECT content_hash FROM workspace_files WHERE uri=?",
                (content_uri,),
            ).fetchone()
            if manifest_file is None or content_file is None:
                raise ProductionError(
                    "workspace_file_not_found",
                    "ScriptRelease 引用的工作区文件尚未登记",
                    status=404,
                    details={"release_id": release_id},
                )
            if (
                str(manifest_file["content_hash"]) != manifest_file_hash
                or str(content_file["content_hash"]) != str(payload["content_hash"])
            ):
                raise ProductionError(
                    "script_release_artifact_hash_mismatch",
                    "ScriptRelease 引用的工作区文件哈希不一致",
                    status=409,
                    details={"release_id": release_id},
                )
            values = {
                "schema_version": str(payload["schema_version"]),
                "work_id": str(payload["work_id"]),
                "display_version": str(payload["display_version"]),
                "manifest_uri": manifest_uri,
                "manifest_file_hash": manifest_file_hash,
                "content_uri": content_uri,
                "content_hash": str(payload["content_hash"]),
                "canon_revision_id": str(payload["canon_revision_id"]),
                "writing_pack_version": str(payload["writing_pack_version"]),
                "source_revision_ids_json": _json(payload["source_revision_ids"]),
                "gate_snapshot_ids_json": _json(payload["gate_snapshot_ids"]),
                "released_by": str(payload["released_by"]),
                "released_at": str(payload["released_at"]),
            }
            existing = connection.execute(
                "SELECT * FROM frozen_script_releases WHERE id=?", (release_id,)
            ).fetchone()
            if existing is not None:
                if any(str(existing[key]) != str(value) for key, value in values.items()):
                    raise ProductionError(
                        "script_release_identity_conflict",
                        "同一 ScriptRelease ID 已冻结不同内容",
                        status=409,
                        details={"release_id": release_id},
                    )
                return self._frozen_script_release_payload(existing)
            uri_owner = connection.execute(
                "SELECT id FROM frozen_script_releases WHERE manifest_uri=?",
                (manifest_uri,),
            ).fetchone()
            if uri_owner is not None:
                raise ProductionError(
                    "script_release_identity_conflict",
                    "ScriptRelease 清单 URI 已属于其他发布版本",
                    status=409,
                    details={"release_id": release_id},
                )
            try:
                connection.execute(
                    """
                    INSERT INTO frozen_script_releases(
                      id, schema_version, work_id, display_version, manifest_uri,
                      manifest_file_hash, content_uri, content_hash,
                      canon_revision_id, writing_pack_version,
                      source_revision_ids_json, gate_snapshot_ids_json,
                      released_by, released_at, frozen_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        release_id,
                        *values.values(),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "script_release_identity_conflict",
                    "ScriptRelease 身份或工作区引用已被占用",
                    status=409,
                    details={"release_id": release_id},
                ) from exc
            row = connection.execute(
                "SELECT * FROM frozen_script_releases WHERE id=?", (release_id,)
            ).fetchone()
            return self._frozen_script_release_payload(row)

    def get_frozen_script_release(self, release_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM frozen_script_releases WHERE id=?", (release_id,)
            ).fetchone()
            return self._frozen_script_release_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _production_request_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "schema_version": str(row["schema_version"]),
            "idempotency_key": str(row["idempotency_key"]),
            "request_uri": str(row["request_uri"]),
            "request_file_hash": str(row["request_file_hash"]),
            "release_id": str(row["release_id"]),
            "run_id": str(row["legacy_run_id"]),
            "production_run_id": str(row["run_id"]),
            "production_display_name": str(row["production_display_name"]),
            "asset_manifest_id": str(row["asset_manifest_id"]),
            "target": str(row["target"]),
            "created_at": str(row["created_at"]),
        }

    @staticmethod
    def _production_request_row(
        connection: sqlite3.Connection, clause: str, value: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            f"""
            SELECT requests.*, runs.legacy_run_id
            FROM production_requests AS requests
            JOIN production_runs AS runs ON runs.id=requests.run_id
            WHERE requests.{clause}=?
            """,
            (value,),
        ).fetchone()

    def bind_production_request(
        self,
        *,
        request_id: str,
        schema_version: str,
        idempotency_key: str,
        request_uri: str,
        request_file_hash: str,
        release_id: str,
        legacy_run_id: str,
        production_display_name: str,
        asset_manifest_id: str,
        target: str,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            request_file = connection.execute(
                "SELECT content_hash FROM workspace_files WHERE uri=?",
                (request_uri,),
            ).fetchone()
            if request_file is None:
                raise ProductionError(
                    "workspace_file_not_found",
                    "ProductionRequest 工作区文件尚未登记",
                    status=404,
                    details={"request_id": request_id},
                )
            if str(request_file["content_hash"]) != request_file_hash:
                raise ProductionError(
                    "production_request_artifact_hash_mismatch",
                    "ProductionRequest 文件哈希与工作区登记不一致",
                    status=409,
                    details={"request_id": request_id},
                )
            run = self._run_row(connection, legacy_run_id)
            if run is None:
                raise ProductionError("run_not_found", "制作任务不存在", status=404)
            production_run_id = str(run["id"])
            release = connection.execute(
                "SELECT id FROM frozen_script_releases WHERE id=?", (release_id,)
            ).fetchone()
            if release is None:
                raise ProductionError(
                    "script_release_not_frozen",
                    "ScriptRelease 尚未在制作端冻结",
                    status=409,
                    details={"release_id": release_id},
                )
            head = self._current_asset_manifest_row(connection, production_run_id)
            if head is None or str(head["id"]) != asset_manifest_id:
                raise ProductionError(
                    "production_request_asset_manifest_conflict",
                    "ProductionRun 当前素材清单与请求不一致",
                    status=409,
                    details={"request_id": request_id},
                )
            values = {
                "schema_version": schema_version,
                "idempotency_key": idempotency_key,
                "request_uri": request_uri,
                "request_file_hash": request_file_hash,
                "release_id": release_id,
                "run_id": production_run_id,
                "production_display_name": production_display_name,
                "asset_manifest_id": asset_manifest_id,
                "target": target,
            }
            existing = self._production_request_row(connection, "id", request_id)
            if existing is not None:
                if any(str(existing[key]) != str(value) for key, value in values.items()):
                    raise ProductionError(
                        "production_request_identity_conflict",
                        "同一 ProductionRequest ID 已绑定不同输入",
                        status=409,
                        details={"request_id": request_id},
                    )
                return self._production_request_payload(existing)
            release_owner = self._production_request_row(
                connection, "release_id", release_id
            )
            if release_owner is not None:
                raise ProductionError(
                    "production_request_conflict",
                    "该 ScriptRelease 已由另一份 ProductionRequest 创建制作任务",
                    status=409,
                    details={
                        "release_id": release_id,
                        "existing_request_id": str(release_owner["id"]),
                    },
                )
            try:
                connection.execute(
                    """
                    INSERT INTO production_requests(
                      id, schema_version, idempotency_key, request_uri,
                      request_file_hash, release_id, run_id,
                      production_display_name, asset_manifest_id, target, created_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        request_id,
                        *values.values(),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ProductionError(
                    "production_request_conflict",
                    "ProductionRequest 身份或固定输入已被占用",
                    status=409,
                    details={"request_id": request_id, "release_id": release_id},
                ) from exc
            row = self._production_request_row(connection, "id", request_id)
            return self._production_request_payload(row)

    def get_production_request(self, request_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = self._production_request_row(connection, "id", request_id)
            return self._production_request_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def get_production_request_for_release(
        self, release_id: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = self._production_request_row(connection, "release_id", release_id)
            return self._production_request_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def get_production_request_for_run(
        self, legacy_run_id: str
    ) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            run = self._run_row(connection, legacy_run_id)
            if run is None:
                return None
            row = connection.execute(
                """
                SELECT requests.*, runs.legacy_run_id
                FROM production_requests AS requests
                JOIN production_runs AS runs ON runs.id=requests.run_id
                WHERE runs.id=?
                """,
                (str(run["id"]),),
            ).fetchone()
            return self._production_request_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def save_production_run(
        self, payload: dict[str, Any]
    ) -> tuple[str, dict[str, str]]:
        legacy_run_id = str(payload["run_id"])
        requested_id = str(payload.get("production_run_id") or "").strip()
        if requested_id and _canonical_uuid(requested_id) is None:
            raise ProductionError(
                "production_run_identity_invalid",
                "ProductionRun UUID 必须是 canonical UUID",
                status=400,
            )
        now = str(payload.get("updated_at") or _now())
        identity_hash = production_run_identity_hash(payload)
        work_item_ids: dict[str, str] = {}
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM production_runs WHERE legacy_run_id=?",
                (legacy_run_id,),
            ).fetchone()
            identity = None
            if self._identity_map_enabled:
                identity = connection.execute(
                    "SELECT * FROM production_run_identity_map WHERE legacy_run_id=?",
                    (legacy_run_id,),
                ).fetchone()
                if identity is not None and existing is None:
                    raise ProductionError(
                        "runtime_identity_corrupt",
                        "ProductionRun 身份映射缺少对应实体",
                        status=500,
                        details={"legacy_run_id": legacy_run_id},
                    )
            if identity is not None:
                run_id = str(identity["production_run_id"])
                if str(identity["content_hash"]) != identity_hash:
                    raise ProductionError(
                        "production_run_identity_conflict",
                        "同一兼容 Run ID 已绑定不同内容，已拒绝覆盖",
                        status=409,
                        details={
                            "legacy_run_id": legacy_run_id,
                            "production_run_id": run_id,
                        },
                    )
                if requested_id and requested_id != run_id:
                    raise ProductionError(
                        "production_run_identity_conflict",
                        "同一兼容 Run ID 已绑定其他 ProductionRun UUID",
                        status=409,
                        details={
                            "legacy_run_id": legacy_run_id,
                            "production_run_id": run_id,
                            "requested_production_run_id": requested_id,
                        },
                    )
            elif existing is not None:
                run_id = str(existing["id"])
                if self._identity_map_enabled:
                    existing_hash = production_run_identity_hash(
                        self._identity_payload_from_row(connection, existing)
                    )
                    if existing_hash != identity_hash:
                        raise ProductionError(
                            "production_run_identity_conflict",
                            "同一兼容 Run ID 已绑定不同内容，已拒绝覆盖",
                            status=409,
                            details={
                                "legacy_run_id": legacy_run_id,
                                "production_run_id": run_id,
                            },
                        )
                    connection.execute(
                        """
                        INSERT INTO production_run_identity_map(
                          legacy_run_id, production_run_id, content_hash, created_at, updated_at
                        ) VALUES(?,?,?,?,?)
                        """,
                        (
                            legacy_run_id,
                            run_id,
                            identity_hash,
                            str(existing["created_at"]),
                            now,
                        ),
                    )
            else:
                run_id = requested_id or production_run_uuid(legacy_run_id)
                owner = connection.execute(
                    "SELECT legacy_run_id FROM production_runs WHERE id=?",
                    (run_id,),
                ).fetchone()
                if owner is not None and str(owner["legacy_run_id"]) != legacy_run_id:
                    raise ProductionError(
                        "production_run_identity_conflict",
                        "ProductionRun UUID 已绑定其他兼容 Run ID",
                        status=409,
                        details={
                            "legacy_run_id": legacy_run_id,
                            "production_run_id": run_id,
                        },
                    )
            connection.execute(
                """
                INSERT INTO production_runs(
                  id, legacy_run_id, project, release_id, draft_token, status,
                  legacy_state, current_stage, source_summary_json,
                  pending_build_id, last_build_id, last_installed_project,
                  created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  project=excluded.project,
                  release_id=excluded.release_id,
                  draft_token=excluded.draft_token,
                  status=excluded.status,
                  legacy_state=excluded.legacy_state,
                  current_stage=excluded.current_stage,
                  source_summary_json=excluded.source_summary_json,
                  pending_build_id=excluded.pending_build_id,
                  last_build_id=excluded.last_build_id,
                  last_installed_project=excluded.last_installed_project,
                  version=production_runs.version+1,
                  updated_at=excluded.updated_at
                """,
                (
                    run_id,
                    legacy_run_id,
                    str(payload["project"]),
                    str(payload["release_id"]),
                    payload.get("draft_token"),
                    _runtime_status(str(payload["state"])),
                    str(payload["state"]),
                    str(payload["current_stage"]),
                    _json(payload.get("source_summary") or {}),
                    payload.get("pending_build_id"),
                    payload.get("last_build_id"),
                    payload.get("last_installed_project"),
                    str(payload["created_at"]),
                    now,
                ),
            )
            if self._identity_map_enabled and identity is None and existing is None:
                connection.execute(
                    """
                    INSERT INTO production_run_identity_map(
                      legacy_run_id, production_run_id, content_hash, created_at, updated_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        legacy_run_id,
                        run_id,
                        identity_hash,
                        str(payload["created_at"]),
                        now,
                    ),
                )
            for ordinal, item in enumerate(payload.get("work_items") or []):
                key = str(item["key"])
                work_item_id = str(item.get("work_item_id") or "").strip()
                if not work_item_id:
                    work_item_id = legacy_work_item_uuid(run_id, key)
                work_item_ids[key] = work_item_id
                connection.execute(
                    """
                    INSERT INTO work_items(
                      id, run_id, legacy_key, type, label, status, progress,
                      detail, ordinal, created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET
                      label=excluded.label,
                      status=excluded.status,
                      progress=excluded.progress,
                      detail=excluded.detail,
                      ordinal=excluded.ordinal,
                      updated_at=excluded.updated_at
                    """,
                    (
                        work_item_id,
                        run_id,
                        key,
                        key,
                        str(item["label"]),
                        str(item["state"]),
                        float(item["progress"]),
                        str(item["detail"]),
                        ordinal,
                        str(payload["created_at"]),
                        now,
                    ),
                )
        return run_id, work_item_ids

    def _run_payload(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        work_items = connection.execute(
            """
            SELECT id, legacy_key, label, status, progress, detail
            FROM work_items
            WHERE run_id=? AND legacy_key IS NOT NULL
            ORDER BY ordinal, id
            """,
            (row["id"],),
        ).fetchall()
        return {
            "run_id": str(row["legacy_run_id"]),
            "production_run_id": str(row["id"]),
            "project": str(row["project"]),
            "release_id": str(row["release_id"]),
            "draft_token": row["draft_token"],
            "state": str(row["legacy_state"]),
            "runtime_status": str(row["status"]),
            "current_stage": str(row["current_stage"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "work_items": [
                {
                    "key": str(item["legacy_key"]),
                    "label": str(item["label"]),
                    "state": str(item["status"]),
                    "progress": float(item["progress"]),
                    "detail": str(item["detail"]),
                    "work_item_id": str(item["id"]),
                }
                for item in work_items
            ],
            "source_summary": _json_value(row["source_summary_json"], {}),
            "pending_build_id": row["pending_build_id"],
            "last_build_id": row["last_build_id"],
            "last_installed_project": row["last_installed_project"],
        }

    def get_production_run(self, identifier: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            column = "id" if _canonical_uuid(identifier) else "legacy_run_id"
            row = connection.execute(
                f"SELECT * FROM production_runs WHERE {column}=?",
                (str(identifier),),
            ).fetchone()
            return self._run_payload(connection, row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def list_production_runs(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM production_runs ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            return [self._run_payload(connection, row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def resolve_production_run_id(self, legacy_run_id: str | None) -> str | None:
        if not legacy_run_id:
            return None
        connection = self._connect()
        try:
            if self._identity_map_enabled:
                row = connection.execute(
                    "SELECT production_run_id FROM production_run_identity_map WHERE legacy_run_id=?",
                    (legacy_run_id,),
                ).fetchone()
                return str(row["production_run_id"]) if row else None
            row = connection.execute(
                "SELECT id FROM production_runs WHERE legacy_run_id=?",
                (legacy_run_id,),
            ).fetchone()
            return str(row["id"]) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def production_run_id(self, legacy_run_id: str | None) -> str | None:
        """Compatibility alias for callers using the pre-R1 method name."""
        return self.resolve_production_run_id(legacy_run_id)

    @staticmethod
    def request_digest(
        kind: str, legacy_run_id: str | None, retry_context: dict[str, Any]
    ) -> str:
        envelope = {
            "kind": kind,
            "run_id": legacy_run_id,
            "retry_context": retry_context,
        }
        return "sha256:" + hashlib.sha256(_json(envelope).encode("utf-8")).hexdigest()

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        run_id: str | None,
        work_item_id: str,
        attempt_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        if run_id is None:
            return
        row = connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence FROM production_events WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO production_events(
              event_id, kind, run_id, work_item_id, attempt_id, sequence,
              timestamp, payload_json
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                kind,
                run_id,
                work_item_id,
                attempt_id,
                int(row["next_sequence"]),
                _now(),
                _json(payload or {}),
            ),
        )

    def create_attempt(
        self,
        *,
        job_id: str,
        kind: str,
        legacy_run_id: str | None,
        retry_context: dict[str, Any],
        work_item_id: str | None = None,
        provider: str | None = None,
        model_or_engine: str | None = None,
    ) -> dict[str, Any]:
        now = _now()
        with self._transaction() as connection:
            run_id = None
            if legacy_run_id:
                row = self._run_row(connection, legacy_run_id)
                run_id = str(row["id"]) if row else None
            if work_item_id:
                work_item = connection.execute(
                    "SELECT id, run_id, attempt_count FROM work_items WHERE id=?",
                    (work_item_id,),
                ).fetchone()
                if not work_item:
                    raise ProductionError(
                        "work_item_not_found", "重试关联的工作项不存在", status=409
                    )
                if run_id and work_item["run_id"] != run_id:
                    raise ProductionError(
                        "work_item_run_conflict", "工作项不属于当前制作任务", status=409
                    )
                run_id = str(work_item["run_id"]) if work_item["run_id"] else run_id
                ordinal = int(work_item["attempt_count"]) + 1
                connection.execute(
                    "UPDATE work_items SET status='queued', attempt_count=?, updated_at=? WHERE id=?",
                    (ordinal, now, work_item_id),
                )
            else:
                work_item_id = str(uuid.uuid4())
                ordinal = 1
                connection.execute(
                    """
                    INSERT INTO work_items(
                      id, run_id, type, label, status, attempt_count,
                      created_at, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)
                    """,
                    (work_item_id, run_id, kind, kind, "queued", 1, now, now),
                )
            attempt_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO job_attempts(
                  id, job_id, work_item_id, run_id, legacy_run_id, kind,
                  ordinal, provider, model_or_engine, request_digest, status,
                  created_at, updated_at, retry_context_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    attempt_id,
                    job_id,
                    work_item_id,
                    run_id,
                    legacy_run_id,
                    kind,
                    ordinal,
                    provider,
                    model_or_engine,
                    self.request_digest(kind, legacy_run_id, retry_context),
                    "queued",
                    now,
                    now,
                    _json(retry_context),
                ),
            )
            self._append_event(
                connection,
                kind="attempt_queued",
                run_id=run_id,
                work_item_id=work_item_id,
                attempt_id=attempt_id,
            )
        value = self.get_attempt(job_id)
        if value is None:
            raise ProductionError("runtime_write_failed", "任务运行记录写入失败", status=500)
        return value

    def import_legacy_attempt(self, payload: dict[str, Any]) -> None:
        job_id = str(payload.get("job_id") or "")
        if not job_id or self.get_attempt(job_id) is not None:
            return
        state = str(payload.get("state") or "failed")
        recovered_as_abandoned = False
        if state in _ACTIVE_ATTEMPT_STATES or state == "interrupted":
            state = "abandoned"
            recovered_as_abandoned = True
            payload = dict(payload)
            payload["error"] = {
                "code": "attempt_abandoned",
                "message": "服务重启时任务仍在执行，旧 Attempt 已放弃",
            }
        created_at = str(payload.get("created_at") or _now())
        updated_at = (
            _now()
            if recovered_as_abandoned
            else str(payload.get("updated_at") or created_at)
        )
        retry_context = payload.get("retry_context")
        retry_context = retry_context if isinstance(retry_context, dict) else {}
        legacy_run_id = str(payload.get("run_id") or "").strip() or None
        with self._transaction() as connection:
            run_id = None
            if legacy_run_id:
                row = self._run_row(connection, legacy_run_id)
                run_id = str(row["id"]) if row else None
            work_item_id = str(uuid.uuid4())
            attempt_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO work_items(
                  id, run_id, type, label, status, attempt_count,
                  created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    work_item_id,
                    run_id,
                    str(payload.get("kind") or "legacy_job"),
                    str(payload.get("kind") or "legacy_job"),
                    "blocked" if state == "abandoned" else state,
                    1,
                    created_at,
                    updated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO job_attempts(
                  id, job_id, work_item_id, run_id, legacy_run_id, kind,
                  ordinal, request_digest, status, created_at, started_at,
                  finished_at, updated_at, result_json, error_json,
                  retry_context_json, cancellation_requested
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    attempt_id,
                    job_id,
                    work_item_id,
                    run_id,
                    legacy_run_id,
                    str(payload.get("kind") or "legacy_job"),
                    1,
                    self.request_digest(
                        str(payload.get("kind") or "legacy_job"),
                        legacy_run_id,
                        retry_context,
                    ),
                    state,
                    created_at,
                    created_at if state != "queued" else None,
                    updated_at if state not in _ACTIVE_ATTEMPT_STATES else None,
                    updated_at,
                    _json(payload["result"]) if isinstance(payload.get("result"), dict) else None,
                    _json(payload["error"]) if isinstance(payload.get("error"), dict) else None,
                    _json(retry_context),
                    1 if state in {"cancelled", "abandoned"} else 0,
                ),
            )
            if recovered_as_abandoned:
                self._append_event(
                    connection,
                    kind="attempt_abandoned",
                    run_id=run_id,
                    work_item_id=work_item_id,
                    attempt_id=attempt_id,
                )

    def abandon_active_attempts(self) -> list[str]:
        abandoned: list[str] = []
        now = _now()
        with self._transaction() as connection:
            rows = connection.execute(
                "SELECT id, job_id, work_item_id, run_id FROM job_attempts WHERE status IN ('queued','running','started')"
            ).fetchall()
            for row in rows:
                error = {
                    "code": "attempt_abandoned",
                    "message": "服务重启时任务仍在执行，旧 Attempt 已放弃",
                }
                connection.execute(
                    """
                    UPDATE job_attempts
                    SET status='abandoned', cancellation_requested=1,
                        finished_at=?, updated_at=?, error_json=?
                    WHERE id=?
                    """,
                    (now, now, _json(error), row["id"]),
                )
                connection.execute(
                    "UPDATE work_items SET status='blocked', updated_at=? WHERE id=?",
                    (now, row["work_item_id"]),
                )
                self._append_event(
                    connection,
                    kind="attempt_abandoned",
                    run_id=row["run_id"],
                    work_item_id=str(row["work_item_id"]),
                    attempt_id=str(row["id"]),
                )
                abandoned.append(str(row["job_id"]))
        return abandoned

    def start_attempt(self, attempt_id: str) -> bool:
        now = _now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT work_item_id, run_id, status, cancellation_requested FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if not row or row["status"] != "queued" or row["cancellation_requested"]:
                return False
            connection.execute(
                "UPDATE job_attempts SET status='running', started_at=?, updated_at=? WHERE id=?",
                (now, now, attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET status='running', updated_at=? WHERE id=?",
                (now, row["work_item_id"]),
            )
            self._append_event(
                connection,
                kind="operation_started",
                run_id=row["run_id"],
                work_item_id=str(row["work_item_id"]),
                attempt_id=attempt_id,
            )
            return True

    def attempt_accepts_result(self, attempt_id: str) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT status, cancellation_requested FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            return bool(
                row
                and row["status"] == "running"
                and not int(row["cancellation_requested"])
            )
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def succeed_attempt(self, attempt_id: str, result: dict[str, Any]) -> bool:
        now = _now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT work_item_id, run_id, status, cancellation_requested FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if not row or row["status"] != "running" or row["cancellation_requested"]:
                return False
            connection.execute(
                """
                UPDATE job_attempts SET status='succeeded', result_json=?,
                  finished_at=?, updated_at=? WHERE id=?
                """,
                (_json(result), now, now, attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET status='succeeded', progress=100, updated_at=? WHERE id=?",
                (now, row["work_item_id"]),
            )
            self._append_event(
                connection,
                kind="attempt_succeeded",
                run_id=row["run_id"],
                work_item_id=str(row["work_item_id"]),
                attempt_id=attempt_id,
            )
            return True

    def fail_attempt(self, attempt_id: str, error: dict[str, Any]) -> bool:
        now = _now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT work_item_id, run_id, status FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if not row or row["status"] in {"succeeded", "cancelled", "abandoned"}:
                return False
            connection.execute(
                """
                UPDATE job_attempts SET status='failed', error_json=?,
                  finished_at=?, updated_at=? WHERE id=?
                """,
                (_json(error), now, now, attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET status='failed', updated_at=? WHERE id=?",
                (now, row["work_item_id"]),
            )
            self._append_event(
                connection,
                kind="operation_failed",
                run_id=row["run_id"],
                work_item_id=str(row["work_item_id"]),
                attempt_id=attempt_id,
                payload={"code": str(error.get("code") or "job_failed")},
            )
            return True

    def cancel_attempt(self, attempt_id: str) -> bool:
        now = _now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT work_item_id, run_id, status FROM job_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if not row or row["status"] not in _ACTIVE_ATTEMPT_STATES:
                return False
            connection.execute(
                """
                UPDATE job_attempts SET status='cancelled',
                  cancellation_requested=1, error_json=NULL, finished_at=?,
                  updated_at=? WHERE id=?
                """,
                (now, now, attempt_id),
            )
            connection.execute(
                "UPDATE work_items SET status='cancelled', updated_at=? WHERE id=?",
                (now, row["work_item_id"]),
            )
            self._append_event(
                connection,
                kind="operation_cancelled",
                run_id=row["run_id"],
                work_item_id=str(row["work_item_id"]),
                attempt_id=attempt_id,
            )
            return True

    def get_attempt(self, job_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM job_attempts WHERE job_id=?", (job_id,)
            ).fetchone()
            return self._attempt_payload(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    def list_attempts(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM job_attempts ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            return [self._attempt_payload(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()

    @staticmethod
    def _attempt_payload(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "job_id": str(row["job_id"]),
            "kind": str(row["kind"]),
            "state": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "result": _json_value(row["result_json"], None),
            "error": _json_value(row["error_json"], None),
            "run_id": str(row["legacy_run_id"]) if row["legacy_run_id"] else None,
            "retry_context": _json_value(row["retry_context_json"], {}),
            "attempt_id": str(row["id"]),
            "work_item_id": str(row["work_item_id"]),
            "production_run_id": str(row["run_id"]) if row["run_id"] else None,
            "ordinal": int(row["ordinal"]),
            "provider": str(row["provider"]) if row["provider"] else None,
            "model_or_engine": str(row["model_or_engine"]) if row["model_or_engine"] else None,
            "request_digest": str(row["request_digest"]),
            "cancellation_requested": bool(row["cancellation_requested"]),
        }

    def events_for_attempt(self, attempt_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM production_events WHERE attempt_id=? ORDER BY sequence",
                (attempt_id,),
            ).fetchall()
            return [
                {
                    "event_id": str(row["event_id"]),
                    "kind": str(row["kind"]),
                    "run_id": str(row["run_id"]),
                    "work_item_id": str(row["work_item_id"]),
                    "attempt_id": str(row["attempt_id"]),
                    "sequence": int(row["sequence"]),
                    "timestamp": str(row["timestamp"]),
                    "payload": _json_value(row["payload_json"], {}),
                }
                for row in rows
            ]
        except sqlite3.DatabaseError as exc:
            raise self._database_error(exc) from exc
        finally:
            connection.close()
