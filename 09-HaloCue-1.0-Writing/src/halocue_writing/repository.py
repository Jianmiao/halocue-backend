from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .errors import DomainError


WRITING_SCHEMA_VERSION = 3


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


class Repository:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_dir = self.data_dir / "artifacts"
        self.release_dir = self.data_dir / "releases"
        self.attachment_dir = self.data_dir / "attachments"
        self.artifact_dir.mkdir(exist_ok=True)
        self.release_dir.mkdir(exist_ok=True)
        self.attachment_dir.mkdir(exist_ok=True)
        self.db_path = self.data_dir / "writing.db"
        self._init_schema()
        self.recover_attempts()

    def connect(self):
        try:
            connection = sqlite3.connect(self.db_path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")
            return connection
        except (sqlite3.DatabaseError, OSError) as exc:
            raise self._database_error(exc) from exc

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self):
        schema = """
        CREATE TABLE IF NOT EXISTS writing_schema_migrations (
          version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS works (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
          version INTEGER NOT NULL, active_writing_pack_version TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS volumes (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          stable_order_key TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
          version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chapters (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          volume_id TEXT NOT NULL REFERENCES volumes(id),
          stable_order_key TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL,
          version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scenes (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          chapter_id TEXT NOT NULL REFERENCES chapters(id), stable_order_key TEXT NOT NULL,
          title TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
          current_revision_id TEXT, contract_json TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS artifacts (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
          current_revision_id TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS revisions (
          id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL REFERENCES artifacts(id),
          parent_revision_id TEXT, ordinal INTEGER NOT NULL, schema_version TEXT NOT NULL,
          content_uri TEXT NOT NULL, content_hash TEXT NOT NULL, provenance_json TEXT NOT NULL,
          created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS proposals (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL DEFAULT 'scene_script',
          scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, base_revision_id TEXT,
          candidate_uri TEXT NOT NULL, candidate_hash TEXT NOT NULL, diff_json TEXT NOT NULL,
          evidence_json TEXT NOT NULL, risk TEXT NOT NULL, status TEXT NOT NULL,
          provider_json TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT
        );
        CREATE TABLE IF NOT EXISTS production_runs (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL, automation_level TEXT NOT NULL, status TEXT NOT NULL,
          pinned_input_refs_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS work_items (
          id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES production_runs(id),
          type TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
          status TEXT NOT NULL, input_refs_json TEXT NOT NULL, output_refs_json TEXT NOT NULL,
          acceptance_json TEXT NOT NULL, attempt_count INTEGER NOT NULL,
          error_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_attempts (
          id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL REFERENCES work_items(id),
          ordinal INTEGER NOT NULL, provider TEXT NOT NULL, request_digest TEXT NOT NULL,
          status TEXT NOT NULL, output_ref TEXT, error_code TEXT,
          started_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_runs (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, instruction TEXT NOT NULL,
          status TEXT NOT NULL, policy_json TEXT NOT NULL, input_snapshot_uri TEXT NOT NULL,
          input_digest TEXT NOT NULL, proposal_id TEXT, failure_json TEXT,
          created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS agent_tool_calls (
          id TEXT PRIMARY KEY, agent_run_id TEXT NOT NULL REFERENCES agent_runs(id),
          ordinal INTEGER NOT NULL, tool_name TEXT NOT NULL, status TEXT NOT NULL,
          input_digest TEXT NOT NULL, output_ref TEXT, error_json TEXT,
          created_at TEXT NOT NULL, finished_at TEXT
        );
        CREATE TABLE IF NOT EXISTS gates (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
          status TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS script_releases (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          display_version TEXT NOT NULL, manifest_uri TEXT NOT NULL, content_uri TEXT NOT NULL,
          content_hash TEXT NOT NULL, source_revision_ids_json TEXT NOT NULL,
          gate_snapshot_ids_json TEXT NOT NULL, writing_pack_version TEXT NOT NULL,
          production_run_id TEXT, released_by TEXT NOT NULL, released_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS decisions (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL, target_id TEXT NOT NULL, decision TEXT NOT NULL,
          note TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_threads (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, title TEXT NOT NULL,
          status TEXT NOT NULL, phase TEXT NOT NULL, permission_mode TEXT NOT NULL,
          version INTEGER NOT NULL, summary_json TEXT NOT NULL,
          archived_message_count INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_messages (
          id TEXT PRIMARY KEY, thread_id TEXT NOT NULL REFERENCES conversation_threads(id),
          ordinal INTEGER NOT NULL, role TEXT NOT NULL, kind TEXT NOT NULL,
          content_json TEXT NOT NULL, status TEXT NOT NULL, provider_json TEXT,
          agent_run_id TEXT, proposal_id TEXT,
          input_tokens INTEGER, output_tokens INTEGER,
          cache_read_tokens INTEGER, cache_write_tokens INTEGER,
          estimated_cost REAL, created_at TEXT NOT NULL, archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS conversation_attachments (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          thread_id TEXT NOT NULL REFERENCES conversation_threads(id),
          message_id TEXT REFERENCES conversation_messages(id),
          filename TEXT NOT NULL, media_type TEXT NOT NULL, content_uri TEXT NOT NULL,
          content_hash TEXT NOT NULL, byte_size INTEGER NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS authorization_policies (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          thread_id TEXT NOT NULL REFERENCES conversation_threads(id),
          scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, mode TEXT NOT NULL,
          allowed_actions_json TEXT NOT NULL, max_turns INTEGER,
          max_cost REAL, expires_at TEXT, status TEXT NOT NULL,
          version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS feedback_reports (
          id TEXT PRIMARY KEY, work_id TEXT REFERENCES works(id),
          category TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL,
          context_json TEXT NOT NULL, status TEXT NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memories (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          kind TEXT NOT NULL, scope_type TEXT NOT NULL, scope_id TEXT NOT NULL,
          content TEXT NOT NULL, source_revision_id TEXT NOT NULL,
          confidence_status TEXT NOT NULL, version INTEGER NOT NULL,
          created_by TEXT NOT NULL, created_at TEXT NOT NULL, last_verified_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reference_files (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          title TEXT NOT NULL, kind TEXT NOT NULL, content_uri TEXT NOT NULL,
          content_hash TEXT NOT NULL, source_label TEXT NOT NULL,
          trust_status TEXT NOT NULL, version INTEGER NOT NULL,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS review_findings (
          id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id),
          scene_id TEXT NOT NULL REFERENCES scenes(id), revision_id TEXT NOT NULL,
          kind TEXT NOT NULL, severity TEXT NOT NULL, status TEXT NOT NULL,
          message TEXT NOT NULL, evidence_json TEXT NOT NULL,
          created_at TEXT NOT NULL, resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_volumes_work ON volumes(work_id, stable_order_key);
        CREATE INDEX IF NOT EXISTS idx_chapters_work ON chapters(work_id, stable_order_key);
        CREATE INDEX IF NOT EXISTS idx_scenes_chapter ON scenes(chapter_id, stable_order_key);
        CREATE INDEX IF NOT EXISTS idx_artifacts_scope ON artifacts(work_id, kind, scope_type, scope_id);
        CREATE INDEX IF NOT EXISTS idx_work_items_run ON work_items(run_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_work ON agent_runs(work_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run ON agent_tool_calls(agent_run_id, ordinal);
        CREATE INDEX IF NOT EXISTS idx_review_findings_scene ON review_findings(scene_id, revision_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_message_order ON conversation_messages(thread_id, ordinal);
        CREATE INDEX IF NOT EXISTS idx_conversation_messages_thread ON conversation_messages(thread_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_conversation_attachments_thread ON conversation_attachments(thread_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_authorization_thread ON authorization_policies(thread_id, status);
        CREATE INDEX IF NOT EXISTS idx_feedback_status ON feedback_reports(status, created_at);
        """
        try:
            with self.connect() as connection:
                current = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if current > WRITING_SCHEMA_VERSION:
                    raise DomainError(
                        "writing_database_version_unsupported",
                        "写作数据库来自更新版本，当前程序无法打开。",
                        status=409,
                        details={
                            "received": current,
                            "supported": WRITING_SCHEMA_VERSION,
                        },
                    )
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS writing_schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                if current < 1:
                    try:
                        connection.executescript(schema)
                        connection.execute(
                            "INSERT OR IGNORE INTO writing_schema_migrations(version, applied_at) VALUES(?, ?)",
                            (1, now()),
                        )
                        connection.execute("PRAGMA user_version = 1")
                    except (sqlite3.DatabaseError, OSError) as exc:
                        raise self._migration_error(1, exc) from exc
                    current = 1
                if current < 2:
                    try:
                        self._migrate_domain_schema(connection)
                        connection.execute(
                            "INSERT OR IGNORE INTO writing_schema_migrations(version, applied_at) VALUES(?, ?)",
                            (2, now()),
                        )
                        connection.execute("PRAGMA user_version = 2")
                    except (sqlite3.DatabaseError, OSError) as exc:
                        raise self._migration_error(2, exc) from exc
                    current = 2
                if current < 3:
                    try:
                        self._migrate_formal_handoff_identity_schema(connection)
                        connection.execute(
                            "INSERT OR IGNORE INTO writing_schema_migrations(version, applied_at) VALUES(?, ?)",
                            (3, now()),
                        )
                        connection.execute("PRAGMA user_version = 3")
                    except (sqlite3.DatabaseError, OSError) as exc:
                        raise self._migration_error(3, exc) from exc
                check = connection.execute("PRAGMA quick_check").fetchone()
                if not check or str(check[0]).casefold() != "ok":
                    raise DomainError(
                        "writing_database_corrupt",
                        "写作数据库完整性检查失败，请从备份恢复或联系支持。",
                        status=500,
                    )
        except DomainError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise self._database_error(exc) from exc

    @staticmethod
    def _database_error(exc: Exception) -> DomainError:
        return DomainError(
            "writing_database_corrupt",
            "写作数据库无法读取，请从备份恢复或联系支持。",
            status=500,
        )

    @staticmethod
    def _migration_error(version: int, exc: Exception) -> DomainError:
        return DomainError(
            "writing_database_migration_failed",
            "写作数据库升级失败，请从备份恢复或联系支持。",
            status=500,
            details={"version": int(version)},
        )

    def schema_version(self) -> int:
        try:
            with self.connect() as connection:
                return int(connection.execute("PRAGMA user_version").fetchone()[0])
        except (sqlite3.DatabaseError, OSError) as exc:
            raise self._database_error(exc) from exc

    def get_formal_handoff_identity(self, release_id: str) -> dict | None:
        """Return the durable formal projection for a writing release."""
        try:
            with self.connect() as connection:
                return self.row(
                    connection.execute(
                        "SELECT * FROM formal_handoff_identities WHERE release_id=?",
                        (release_id,),
                    ).fetchone()
                )
        except (sqlite3.DatabaseError, OSError) as exc:
            raise self._database_error(exc) from exc

    @staticmethod
    def _save_formal_handoff_identity_connection(
        connection,
        *,
        release_id: str,
        formal_release_id: str,
        production_request_id: str,
        formal_work_id: str,
        production_run_id: str | None,
        content_hash: str,
    ) -> dict:
        existing = connection.execute(
            "SELECT * FROM formal_handoff_identities WHERE release_id=?",
            (release_id,),
        ).fetchone()
        expected = {
            "release_id": release_id,
            "formal_release_id": formal_release_id,
            "production_request_id": production_request_id,
            "formal_work_id": formal_work_id,
            "content_hash": content_hash,
        }
        if existing:
            existing = dict(existing)
            for field, value in expected.items():
                if existing[field] != value:
                    raise DomainError(
                        "formal_handoff_identity_conflict",
                        "正式交接身份映射与已冻结记录不一致。",
                        status=409,
                        details={"release_id": release_id, "field": field},
                    )
            if production_run_id and existing["production_run_id"] not in (None, production_run_id):
                raise DomainError(
                    "formal_handoff_identity_conflict",
                    "正式交接已绑定其他制作运行，不能静默覆盖。",
                    status=409,
                    details={"release_id": release_id, "field": "production_run_id"},
                )
            if production_run_id and existing["production_run_id"] is None:
                connection.execute(
                    "UPDATE formal_handoff_identities SET production_run_id=?, updated_at=? WHERE release_id=?",
                    (production_run_id, now(), release_id),
                )
                existing["production_run_id"] = production_run_id
            return existing
        try:
            timestamp = now()
            connection.execute(
                """
                INSERT INTO formal_handoff_identities
                (release_id, formal_release_id, production_request_id, formal_work_id,
                 production_run_id, content_hash, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    release_id,
                    formal_release_id,
                    production_request_id,
                    formal_work_id,
                    production_run_id,
                    content_hash,
                    timestamp,
                    timestamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DomainError(
                "formal_handoff_identity_conflict",
                "正式交接身份映射已被其他版本占用。",
                status=409,
                details={"release_id": release_id},
            ) from exc
        return dict(
            connection.execute(
                "SELECT * FROM formal_handoff_identities WHERE release_id=?",
                (release_id,),
            ).fetchone()
        )

    def save_formal_handoff_identity(
        self,
        *,
        release_id: str,
        formal_release_id: str,
        production_request_id: str,
        formal_work_id: str,
        production_run_id: str | None,
        content_hash: str,
    ) -> dict:
        with self.transaction() as connection:
            return self._save_formal_handoff_identity_connection(
                connection,
                release_id=release_id,
                formal_release_id=formal_release_id,
                production_request_id=production_request_id,
                formal_work_id=formal_work_id,
                production_run_id=production_run_id,
                content_hash=content_hash,
            )

    def _migrate_domain_schema(self, connection):
        """Add durable writing-domain fields without replacing an existing workspace."""
        connection.execute("DROP INDEX IF EXISTS idx_conversation_scope")
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_conversation_scope_lookup ON conversation_threads(work_id, scope_type, scope_id, updated_at)"
        )
        chapter_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(chapters)")
        }
        if "volume_id" not in chapter_columns:
            connection.execute(
                "ALTER TABLE chapters ADD COLUMN volume_id TEXT REFERENCES volumes(id)"
            )
        proposal_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(proposals)")
        }
        if "kind" not in proposal_columns:
            connection.execute(
                "ALTER TABLE proposals ADD COLUMN kind TEXT NOT NULL DEFAULT 'scene_script'"
            )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_chapters_volume ON chapters(volume_id, stable_order_key)"
        )

        timestamp = now()
        for work in connection.execute("SELECT id FROM works").fetchall():
            volume = connection.execute(
                "SELECT id FROM volumes WHERE work_id=? ORDER BY stable_order_key LIMIT 1",
                (work["id"],),
            ).fetchone()
            if not volume:
                volume_id = new_id("volume")
                connection.execute(
                    "INSERT INTO volumes VALUES (?,?,?,?,?,?,?,?)",
                    (
                        volume_id,
                        work["id"],
                        "000001",
                        "第一卷",
                        "active",
                        1,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                volume_id = volume["id"]
            connection.execute(
                "UPDATE chapters SET volume_id=? WHERE work_id=? AND volume_id IS NULL",
                (volume_id, work["id"]),
            )
            thread = connection.execute(
                "SELECT id FROM conversation_threads WHERE work_id=? AND scope_type='work' AND scope_id=?",
                (work["id"], work["id"]),
            ).fetchone()
            if not thread:
                thread_id = new_id("thread")
                connection.execute(
                    "INSERT INTO conversation_threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        thread_id, work["id"], "work", work["id"], "创作主对话",
                        "active", "discuss", "review", 1, "{}", 0, timestamp, timestamp,
                    ),
                )
                connection.execute(
                    "INSERT INTO authorization_policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        new_id("policy"), work["id"], thread_id, "work", work["id"],
                        "review", '["read","discuss"]', None, None, None,
                        "active", 1, timestamp, timestamp,
                    ),
                )

    @staticmethod
    def _migrate_formal_handoff_identity_schema(connection):
        """Persist the cross-domain UUID projection used by formal handoff."""
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS formal_handoff_identities (
              release_id TEXT PRIMARY KEY REFERENCES script_releases(id),
              formal_release_id TEXT NOT NULL UNIQUE,
              production_request_id TEXT NOT NULL UNIQUE,
              formal_work_id TEXT NOT NULL,
              production_run_id TEXT,
              content_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_formal_handoff_run ON formal_handoff_identities(production_run_id)"
        )

    def recover_attempts(self):
        timestamp = now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE job_attempts SET status='abandoned', error_code='process_restarted', finished_at=? WHERE status='started'",
                (timestamp,),
            )
            connection.execute(
                "UPDATE work_items SET status='ready', updated_at=? WHERE status='running'",
                (timestamp,),
            )
            connection.execute(
                "UPDATE production_runs SET status='running', updated_at=? WHERE status='waiting_user' AND id IN (SELECT run_id FROM work_items WHERE status='ready')",
                (timestamp,),
            )

    def atomic_write_bytes(self, relative: str, content: bytes) -> tuple[str, str]:
        target = (self.data_dir / relative).resolve()
        if self.data_dir not in target.parents:
            raise ValueError("path escapes data directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.replace(temporary, target)
        return str(target.relative_to(self.data_dir)).replace("\\", "/"), sha256_bytes(content)

    def atomic_write_text(self, relative_uri: str, content: str) -> tuple[str, str]:
        target = (self.data_dir / relative_uri).resolve()
        if self.data_dir not in target.parents:
            raise ValueError("Artifact path escaped data directory")
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return relative_uri.replace("\\", "/"), sha256_text(content)

    def read_text(self, uri: str) -> str:
        path = (self.data_dir / uri).resolve()
        if self.data_dir not in path.parents:
            raise ValueError("Artifact path escaped data directory")
        return path.read_text(encoding="utf-8")

    @staticmethod
    def rows(rows):
        return [dict(row) for row in rows]

    @staticmethod
    def row(row):
        return dict(row) if row else None
