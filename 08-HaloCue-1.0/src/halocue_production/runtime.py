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


RUNTIME_SCHEMA_VERSION = 2
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
        self._verify()

    @staticmethod
    def _database_error(exc: Exception) -> ProductionError:
        return ProductionError(
            "runtime_database_corrupt",
            "制作运行数据库无法读取，请从备份恢复或联系支持",
            status=500,
        )

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except sqlite3.DatabaseError as exc:
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
                    connection.execute("BEGIN IMMEDIATE")
                    try:
                        for statement in _MIGRATIONS[version]:
                            connection.execute(statement)
                        connection.execute(
                            "INSERT INTO runtime_schema_migrations(version, applied_at) VALUES(?, ?)",
                            (version, _now()),
                        )
                        connection.execute(f"PRAGMA user_version={version}")
                        connection.commit()
                    except Exception:
                        connection.rollback()
                        raise
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

    def schema_version(self) -> int:
        connection = self._connect()
        try:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        finally:
            connection.close()

    def save_production_run(
        self, payload: dict[str, Any]
    ) -> tuple[str, dict[str, str]]:
        legacy_run_id = str(payload["run_id"])
        requested_id = str(payload.get("production_run_id") or "").strip()
        now = str(payload.get("updated_at") or _now())
        work_item_ids: dict[str, str] = {}
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM production_runs WHERE legacy_run_id=?",
                (legacy_run_id,),
            ).fetchone()
            run_id = str(existing["id"]) if existing else (
                requested_id or production_run_uuid(legacy_run_id)
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

    def get_production_run(self, legacy_run_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM production_runs WHERE legacy_run_id=?",
                (legacy_run_id,),
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

    def production_run_id(self, legacy_run_id: str | None) -> str | None:
        if not legacy_run_id:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT id FROM production_runs WHERE legacy_run_id=?",
                (legacy_run_id,),
            ).fetchone()
            return str(row["id"]) if row else None
        finally:
            connection.close()

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
                row = connection.execute(
                    "SELECT id FROM production_runs WHERE legacy_run_id=?",
                    (legacy_run_id,),
                ).fetchone()
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
                row = connection.execute(
                    "SELECT id FROM production_runs WHERE legacy_run_id=?",
                    (legacy_run_id,),
                ).fetchone()
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
        finally:
            connection.close()

    def list_attempts(self) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM job_attempts ORDER BY updated_at DESC, id DESC"
            ).fetchall()
            return [self._attempt_payload(row) for row in rows]
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
        finally:
            connection.close()
