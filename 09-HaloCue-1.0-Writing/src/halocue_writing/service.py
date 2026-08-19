from __future__ import annotations

import base64
import binascii
import difflib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .errors import DomainError, NotFound, RevisionConflict
from .ba_world_starter import BA_WORLD_STARTER_SOURCE, BA_WORLD_STARTER_VERSION, starter_bible
from .model_settings import UserPreferencesStore, WritingModelSettings
from .official_reference_catalog import OfficialReferenceCatalog
from .providers import FakeWritingProvider, make_writing_provider
from .repository import Repository, canonical_json, new_id, now, sha256_text
from .workflow_pack import COMMON_RULES, MODE_SOURCES, PACK_VERSION, describe_pack, template_contract


PRODUCTION_HANDOFF_TIMEOUT_SECONDS = 30


class WritingService:
    def __init__(
        self,
        data_dir: Path,
        production_url: str = "http://127.0.0.1:8892",
        official_corpus_dir: Path | None = None,
        public_production_url: str | None = None,
    ):
        self.repo = Repository(data_dir)
        self.model_settings = WritingModelSettings(data_dir)
        self.preferences = UserPreferencesStore(data_dir)
        self.provider = make_writing_provider(self.model_settings)
        self.production_url = production_url.rstrip("/")
        self.public_production_url = (
            (public_production_url or self.production_url).rstrip("/")
        )
        configured_corpus = official_corpus_dir or os.environ.get("HALOCUE_BA_CORPUS_DIR")
        if configured_corpus:
            corpus_dir = Path(configured_corpus)
        else:
            corpus_dir = Path(__file__).resolve().parents[3] / "05-官方演出语料库" / "records"
        self.official_references = OfficialReferenceCatalog(corpus_dir)

    def health(self):
        return {
            "ok": True,
            "service": "halocue-writing",
            "version": "0.1.0",
            "provider": self.provider.descriptor(),
        }

    def capabilities(self):
        return {
            "api_version": "1.0",
            "capabilities": [
                "works",
                "brief_revisions",
                "story_blueprint",
                "scene_context",
                "proposal_diff",
                "script_release",
                "production_handoff",
                "work_canon",
                "character_cards",
                "world_bible",
                "ba_world_starter",
                "reference_files",
                "official_reference_catalog",
                "review_findings",
                "agent_runs",
                "volumes",
                "conversation_threads",
                "conversation_attachments",
                "authorization_policies",
                "feedback_reports",
                "provider_reasoning_trace",
            ],
            "writing_pack": describe_pack(),
            "providers": [self.provider.descriptor()],
            "official_references": self.official_references.descriptor(),
        }

    def search_official_references(self, query: str, limit: int = 12):
        bounded = max(1, min(int(limit or 12), 30))
        return {
            "catalog": self.official_references.descriptor(),
            "query": str(query).strip(),
            "items": self.official_references.search(query, bounded),
        }

    def list_works(self):
        with self.repo.connect() as connection:
            return self.repo.rows(connection.execute("SELECT * FROM works ORDER BY updated_at DESC"))

    def submit_feedback(self, payload: dict):
        category = str(payload.get("category", "usability")).strip()
        if category not in {"bug", "usability", "suggestion"}:
            raise DomainError("validation_error", "反馈类型无效。", details={"field": "category"})
        summary = str(payload.get("summary", "")).strip()
        details = str(payload.get("details", "")).strip()
        if not summary or not details:
            raise DomainError(
                "validation_error",
                "请填写问题概述和详细说明。",
                details={"fields": ["summary", "details"]},
            )
        if len(summary) > 120 or len(details) > 4000:
            raise DomainError(
                "validation_error",
                "反馈内容过长。",
                details={"summary_max": 120, "details_max": 4000},
            )
        work_id = str(payload.get("work_id", "")).strip() or None
        context = payload.get("context") if isinstance(payload.get("context"), dict) else {}
        timestamp = now()
        report_id = new_id("feedback")
        with self.repo.transaction() as connection:
            if work_id and not connection.execute("SELECT 1 FROM works WHERE id=?", (work_id,)).fetchone():
                raise NotFound("work", work_id)
            connection.execute(
                "INSERT INTO feedback_reports VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    report_id,
                    work_id,
                    category,
                    summary,
                    details,
                    canonical_json(context),
                    "open",
                    timestamp,
                    timestamp,
                ),
            )
        return {
            "id": report_id,
            "status": "open",
            "stored_locally": True,
            "created_at": timestamp,
        }

    def _append_conversation_message(
        self, connection, thread_id: str, role: str, kind: str, content: dict,
        *, provider: dict | None = None, proposal_id: str | None = None,
    ) -> str:
        ordinal = connection.execute(
            "SELECT COALESCE(MAX(ordinal),0)+1 FROM conversation_messages WHERE thread_id=?",
            (thread_id,),
        ).fetchone()[0]
        message_id = new_id("message")
        connection.execute(
            "INSERT INTO conversation_messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                message_id, thread_id, ordinal, role, kind, canonical_json(content),
                "complete", canonical_json(provider) if provider else None,
                None, proposal_id, None, None, None, None, None, now(), None,
            ),
        )
        return message_id

    def _conversation_task_contract(self, connection, work_id: str, requested_scope: dict | None = None) -> dict:
        """Resolve the active director task from persisted work state.

        The browser never selects a writing template.  A work conversation is
        continuous, while each provider turn receives only the template that
        the current, server-validated workflow stage permits.
        """
        contents: dict[str, dict] = {}
        for artifact in connection.execute(
            "SELECT kind,current_revision_id FROM artifacts WHERE work_id=? AND kind IN ('brief','story_blueprint')",
            (work_id,),
        ).fetchall():
            if artifact["current_revision_id"]:
                revision = connection.execute(
                    "SELECT content_uri FROM revisions WHERE id=?", (artifact["current_revision_id"],)
                ).fetchone()
                if revision:
                    contents[artifact["kind"]] = json.loads(self.repo.read_text(revision["content_uri"]))

        brief = contents.get("brief")
        blueprint = contents.get("story_blueprint")
        scene_count = connection.execute("SELECT COUNT(*) FROM scenes WHERE work_id=?", (work_id,)).fetchone()[0]
        drafted_count = connection.execute(
            "SELECT COUNT(*) FROM scenes WHERE work_id=? AND current_revision_id IS NOT NULL", (work_id,)
        ).fetchone()[0]
        pending_count = connection.execute(
            "SELECT COUNT(*) FROM proposals WHERE work_id=? AND status='pending'", (work_id,)
        ).fetchone()[0]

        requested_scope = requested_scope if isinstance(requested_scope, dict) else {}
        surface = str(requested_scope.get("surface", "auto"))
        chapter = None
        if surface == "chapter":
            chapter_id = str(requested_scope.get("chapter_id", "")).strip()
            if not chapter_id:
                target_artifact = connection.execute(
                    "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='writing_target'",
                    (work_id,),
                ).fetchone()
                if target_artifact and target_artifact["current_revision_id"]:
                    target_revision = connection.execute(
                        "SELECT content_uri FROM revisions WHERE id=?", (target_artifact["current_revision_id"],)
                    ).fetchone()
                    if target_revision:
                        chapter_id = str(json.loads(self.repo.read_text(target_revision["content_uri"])).get("chapter_id", ""))
            chapter = connection.execute(
                "SELECT id,title FROM chapters WHERE id=? AND work_id=?", (chapter_id, work_id)
            ).fetchone()
            if not chapter:
                raise DomainError("invalid_writing_target", "当前写作章节不存在，请重新选择。", status=409)

        if surface == "work" and brief and blueprint and blueprint.get("status") == "accepted":
            template_id = "blueprint.generate"
            task = "在作品栏目维护全作方向、人物关系和世界观边界；任何调整都先形成新的 StoryBlueprint Proposal。"
        elif chapter and brief and blueprint and blueprint.get("status") == "accepted":
            template_id = "chapter.plan"
            task = f"只规划《{chapter['title']}》内部的章节目标、承接点和场景节拍，不重写全作 StoryBlueprint。"
        elif not brief:
            template_id = "brief.build"
            task = "理解这句想法，提出需要讨论的方向，不写入任何正式设定。"
        elif not blueprint or blueprint.get("status") != "accepted" or brief.get("status") != "confirmed":
            template_id = "blueprint.generate"
            task = "围绕当前想法讨论、比较并形成可审查的故事方向 Proposal。"
        elif not scene_count:
            template_id = "structure.plan"
            task = "基于已确认的故事方向，讨论卷、章与场景的稳定结构；结构变更需经用户确认。"
        elif drafted_count < scene_count:
            template_id = "scene.draft.generate"
            task = "协助确定下一场的目标与修改约束；具体正文只能通过该场的 Proposal / Diff 提交。"
        else:
            template_id = "release.review"
            task = "协助全篇审查、确认未决事项，并在 Gate 通过后准备冻结 ScriptRelease。"

        contract = template_contract(template_id)
        selected_modes = [mode for mode in (brief or {}).get("story_modes", []) if mode in MODE_SOURCES]
        if not selected_modes and (brief or {}).get("mode") in MODE_SOURCES:
            selected_modes = [brief["mode"]]
        contract.update(
            {
                "task": task,
                "workflow_state": {
                    "scene_count": scene_count,
                    "drafted_scene_count": drafted_count,
                    "pending_proposal_count": pending_count,
                },
                "task_scope": {
                    "surface": "chapter" if chapter else ("work" if surface == "work" else "auto"),
                    "chapter_id": chapter["id"] if chapter else None,
                    "chapter_title": chapter["title"] if chapter else None,
                },
                "rule_sources": {
                    "common": COMMON_RULES,
                    "modes": {mode: MODE_SOURCES[mode] for mode in selected_modes},
                },
                "write_boundary": "正式 Brief、资料库事实和正文只能由对应 Proposal 采纳后写入。",
            }
        )
        return contract

    def create_work(self, payload: dict):
        idea = str(payload.get("idea", "")).strip()
        title = str(payload.get("title", "")).strip() or idea[:24]
        if not title:
            raise DomainError("validation_error", "请写下一句故事想法或作品名称。", details={"fields": ["idea", "title"]})
        world_seed = str(payload.get("world_seed", "blank")).strip() or "blank"
        if world_seed not in {"blank", "ba_starter"}:
            raise DomainError("validation_error", "世界观底稿类型无效。", details={"field": "world_seed"})
        permission_mode = str(payload.get("permission_mode", "review")).strip() or "review"
        if permission_mode not in {"review", "managed"}:
            raise DomainError("validation_error", "Agent 授权模式无效。", details={"field": "permission_mode"})
        work_id = new_id("work")
        volume_id = new_id("volume")
        chapter_id = new_id("chapter")
        thread_id = new_id("thread")
        timestamp = now()
        with self.repo.transaction() as connection:
            connection.execute(
                "INSERT INTO works VALUES (?,?,?,?,?,?,?)",
                (work_id, title, "active", 1, PACK_VERSION, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO volumes VALUES (?,?,?,?,?,?,?,?)",
                (volume_id, work_id, "000001", "第一卷", "active", 1, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO chapters (id,work_id,volume_id,stable_order_key,title,status,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (chapter_id, work_id, volume_id, "000001", "第一章", "placeholder", 1, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO conversation_threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, work_id, "work", work_id, "创作主对话", "active", "discuss", permission_mode, 1, "{}", 0, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO authorization_policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    new_id("policy"), work_id, thread_id, "work", work_id, permission_mode,
                    canonical_json(["read", "discuss"] if permission_mode == "review" else ["read", "discuss", "auto_accept_low_risk_writing"]),
                    12 if permission_mode == "managed" else None,
                    None, None, "active", 1, timestamp, timestamp,
                ),
            )
            run_id = new_id("run")
            connection.execute(
                "INSERT INTO production_runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, work_id, "creation", permission_mode, "planned", "[]", timestamp, timestamp),
            )
            if idea:
                self._append_conversation_message(connection, thread_id, "user", "text", {"text": idea})
                task_contract = self._conversation_task_contract(connection, work_id)
                reply = self.provider.discuss_work(
                    [{"role": "user", "text": idea}],
                    {"work_id": work_id, "idea": idea, "task_contract": task_contract},
                )
                reply = self._finalize_agent_reply(task_contract, reply)
                self._append_conversation_message(
                    connection, thread_id, "assistant", "discussion", reply,
                    provider=self.provider.descriptor(),
                )
            if world_seed == "ba_starter":
                artifact = self._artifact(connection, work_id, "world_bible", "work", work_id)
                self._add_revision(
                    connection,
                    artifact,
                    starter_bible(),
                    "user",
                    {
                        "workflow": "world.starter.apply",
                        "pack": PACK_VERSION,
                        "starter_version": BA_WORLD_STARTER_VERSION,
                        "source": BA_WORLD_STARTER_SOURCE,
                        "disclosure": "这是待核对的产品起始架构，不是自动确认的 BA 原作事实。",
                    },
                )
        return self.get_work(work_id)

    def get_work(self, work_id: str):
        with self.repo.connect() as connection:
            work = self.repo.row(connection.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone())
            if not work:
                raise NotFound("work", work_id)
            volumes = self.repo.rows(connection.execute("SELECT * FROM volumes WHERE work_id=? ORDER BY stable_order_key", (work_id,)))
            chapters = self.repo.rows(connection.execute("SELECT * FROM chapters WHERE work_id=? ORDER BY stable_order_key", (work_id,)))
            for chapter in chapters:
                chapter["scenes"] = self.repo.rows(connection.execute("SELECT * FROM scenes WHERE chapter_id=? ORDER BY stable_order_key", (chapter["id"],)))
                for scene in chapter["scenes"]:
                    scene["contract"] = json.loads(scene.pop("contract_json"))
            for volume in volumes:
                volume["chapters"] = [chapter for chapter in chapters if chapter.get("volume_id") == volume["id"]]
            artifacts = self.repo.rows(connection.execute("SELECT * FROM artifacts WHERE work_id=?", (work_id,)))
            for artifact in artifacts:
                if artifact["current_revision_id"]:
                    revision = self.repo.row(connection.execute("SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)).fetchone())
                    revision["content"] = json.loads(self.repo.read_text(revision["content_uri"]))
                    if artifact["kind"] == "scene_script" and "blocks" not in revision["content"]:
                        revision["content"] = self._scene_content_from_text(
                            revision["content"].get("text", ""), revision["id"]
                        )
                    revision["provenance"] = json.loads(revision.pop("provenance_json"))
                    artifact["current_revision"] = revision
                history = self.repo.rows(connection.execute(
                    "SELECT id, parent_revision_id, ordinal, schema_version, content_hash, created_by, created_at, content_uri, provenance_json FROM revisions WHERE artifact_id=? ORDER BY ordinal DESC",
                    (artifact["id"],),
                ))
                for item in history:
                    item["content"] = json.loads(self.repo.read_text(item.pop("content_uri")))
                    if artifact["kind"] == "scene_script" and "blocks" not in item["content"]:
                        item["content"] = self._scene_content_from_text(
                            item["content"].get("text", ""), item["id"]
                        )
                    item["provenance"] = json.loads(item.pop("provenance_json"))
                artifact["revisions"] = history
            work["volumes"] = volumes
            work["chapters"] = chapters
            work["artifacts"] = artifacts
            work["proposals"] = self.repo.rows(connection.execute("SELECT * FROM proposals WHERE work_id=? ORDER BY created_at DESC", (work_id,)))
            for proposal in work["proposals"]:
                candidate_text = self.repo.read_text(proposal["candidate_uri"])
                structured_kinds = {"brief_blueprint", "chapter_plan", "character_card", "world_entity"}
                proposal["candidate"] = json.loads(candidate_text) if proposal.get("kind") in structured_kinds else candidate_text
                proposal["diff"] = json.loads(proposal.pop("diff_json"))
                proposal["evidence"] = json.loads(proposal.pop("evidence_json"))
                proposal["provider"] = json.loads(proposal.pop("provider_json"))
            work["releases"] = self.repo.rows(connection.execute("SELECT * FROM script_releases WHERE work_id=? ORDER BY released_at DESC", (work_id,)))
            work["runs"] = self.repo.rows(connection.execute("SELECT * FROM production_runs WHERE work_id=? ORDER BY created_at DESC", (work_id,)))
            for run in work["runs"]:
                run["work_items"] = self.repo.rows(connection.execute("SELECT * FROM work_items WHERE run_id=? ORDER BY created_at", (run["id"],)))
            work["agent_runs"] = self.repo.rows(connection.execute("SELECT * FROM agent_runs WHERE work_id=? ORDER BY created_at DESC", (work_id,)))
            for agent_run in work["agent_runs"]:
                agent_run["policy"] = json.loads(agent_run.pop("policy_json"))
                agent_run["failure"] = json.loads(agent_run.pop("failure_json")) if agent_run.get("failure_json") else None
                agent_run["tool_calls"] = self.repo.rows(connection.execute("SELECT * FROM agent_tool_calls WHERE agent_run_id=? ORDER BY ordinal", (agent_run["id"],)))
                for call in agent_run["tool_calls"]:
                    call["error"] = json.loads(call.pop("error_json")) if call.get("error_json") else None
            work["conversation_threads"] = self.repo.rows(connection.execute(
                "SELECT * FROM conversation_threads WHERE work_id=? ORDER BY updated_at DESC", (work_id,)
            ))
            for thread in work["conversation_threads"]:
                thread["summary"] = json.loads(thread.pop("summary_json"))
                thread["messages"] = self.repo.rows(connection.execute(
                    "SELECT * FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread["id"],)
                ))
                for message in thread["messages"]:
                    message["content"] = json.loads(message.pop("content_json"))
                    message["provider"] = json.loads(message.pop("provider_json")) if message.get("provider_json") else None
                thread["attachments"] = self.repo.rows(connection.execute(
                    "SELECT id,message_id,filename,media_type,content_hash,byte_size,status,created_at FROM conversation_attachments WHERE thread_id=? ORDER BY created_at",
                    (thread["id"],),
                ))
                for attachment in thread["attachments"]:
                    attachment["content_url"] = f"/api/v1/works/{work_id}/attachments/{attachment['id']}/content"
            work["authorization_policies"] = self.repo.rows(connection.execute(
                "SELECT * FROM authorization_policies WHERE work_id=? ORDER BY updated_at DESC", (work_id,)
            ))
            for policy in work["authorization_policies"]:
                policy["allowed_actions"] = json.loads(policy.pop("allowed_actions_json"))
            work["reference_files"] = self.repo.rows(connection.execute("SELECT id,title,kind,content_uri,content_hash,source_label,trust_status,version,created_at,updated_at FROM reference_files WHERE work_id=? ORDER BY updated_at DESC", (work_id,)))
            for reference in work["reference_files"]:
                reference["preview"] = self.repo.read_text(reference.pop("content_uri"))[:1800]
            work["gates"] = self.repo.rows(connection.execute("SELECT * FROM gates WHERE work_id=? ORDER BY created_at DESC", (work_id,)))
            for gate in work["gates"]:
                gate["snapshot"] = json.loads(gate.pop("result_json"))
            work["review_findings"] = self.repo.rows(connection.execute("SELECT * FROM review_findings WHERE work_id=? ORDER BY created_at DESC", (work_id,)))
            for finding in work["review_findings"]:
                finding["evidence"] = json.loads(finding.pop("evidence_json"))
            return work

    def _check_work_version(self, connection, work_id: str, expected_version: int):
        row = connection.execute("SELECT version FROM works WHERE id=?", (work_id,)).fetchone()
        if not row:
            raise NotFound("work", work_id)
        if row["version"] != expected_version:
            raise RevisionConflict(expected_version, row["version"])
        return row["version"]

    def _bump_work(self, connection, work_id: str, version: int):
        connection.execute("UPDATE works SET version=?, updated_at=? WHERE id=?", (version + 1, now(), work_id))

    def _check_thread_version(self, connection, work_id: str, thread_id: str, expected: int):
        thread = connection.execute(
            "SELECT * FROM conversation_threads WHERE id=? AND work_id=?", (thread_id, work_id)
        ).fetchone()
        if not thread:
            raise NotFound("conversation_thread", thread_id)
        if thread["version"] != expected:
            raise DomainError(
                "thread_conflict", "对话已在其他位置更新，请刷新后重试。", status=409,
                details={"expected_version": expected, "actual_version": thread["version"]},
            )
        return thread

    def create_conversation_thread(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        scope_type = str(payload.get("scope_type", "work")).strip() or "work"
        scope_id = str(payload.get("scope_id", work_id)).strip() or work_id
        title = str(payload.get("title", "新对话")).strip() or "新对话"
        permission_mode = str(payload.get("permission_mode", "review")).strip() or "review"
        if scope_type not in {"work", "chapter"}:
            raise DomainError("validation_error", "对话作用域无效。", details={"field": "scope_type"})
        if permission_mode not in {"review", "managed"}:
            raise DomainError("validation_error", "Agent 授权模式无效。", details={"field": "permission_mode"})
        if len(title) > 80:
            raise DomainError("validation_error", "对话名称不能超过 80 个字符。", details={"field": "title"})
        thread_id = new_id("thread")
        timestamp = now()
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            if scope_type == "work":
                scope_id = work_id
            elif not connection.execute(
                "SELECT 1 FROM chapters WHERE id=? AND work_id=?", (scope_id, work_id)
            ).fetchone():
                raise NotFound("chapter", scope_id)
            connection.execute(
                "INSERT INTO conversation_threads VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (thread_id, work_id, scope_type, scope_id, title, "active", "discuss", permission_mode, 1, "{}", 0, timestamp, timestamp),
            )
            allowed = ["read", "discuss"]
            if permission_mode == "managed":
                allowed.append("auto_accept_low_risk_writing")
            connection.execute(
                "INSERT INTO authorization_policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("policy"), work_id, thread_id, scope_type, scope_id, permission_mode,
                 canonical_json(allowed), 12 if permission_mode == "managed" else None,
                 None, None, "active", 1, timestamp, timestamp),
            )
            self._append_conversation_message(
                connection, thread_id, "assistant", "notice",
                {"text": "新的讨论已经建立。我会读取当前作品的正式上下文，但不会把其他对话当作已经确认的事实。"},
            )
            self._bump_work(connection, work_id, version)
        return {"thread_id": thread_id, "work": self.get_work(work_id)}

    def update_conversation_thread(self, work_id: str, thread_id: str, payload: dict):
        expected = int(payload.get("expected_thread_version", -1))
        with self.repo.transaction() as connection:
            thread = self._check_thread_version(connection, work_id, thread_id, expected)
            title = str(payload.get("title", thread["title"])).strip() or thread["title"]
            status = str(payload.get("status", thread["status"])).strip() or thread["status"]
            if len(title) > 80:
                raise DomainError("validation_error", "对话名称不能超过 80 个字符。", details={"field": "title"})
            if status not in {"active", "archived"}:
                raise DomainError("validation_error", "对话状态无效。", details={"field": "status"})
            timestamp = now()
            connection.execute(
                "UPDATE conversation_threads SET title=?,status=?,version=version+1,updated_at=? WHERE id=?",
                (title, status, timestamp, thread_id),
            )
            connection.execute(
                "UPDATE authorization_policies SET status=?,version=version+1,updated_at=? WHERE thread_id=?",
                ("active" if status == "active" else "archived", timestamp, thread_id),
            )
        return {"thread_id": thread_id, "work": self.get_work(work_id)}

    @staticmethod
    def _validate_image_signature(media_type: str, content: bytes) -> bool:
        if media_type == "image/png":
            return content.startswith(b"\x89PNG\r\n\x1a\n")
        if media_type == "image/jpeg":
            return content.startswith(b"\xff\xd8\xff")
        if media_type == "image/gif":
            return content.startswith((b"GIF87a", b"GIF89a"))
        if media_type == "image/webp":
            return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
        return False

    def create_conversation_attachment(self, work_id: str, thread_id: str, payload: dict):
        expected = int(payload.get("expected_thread_version", -1))
        filename = Path(str(payload.get("filename", "image"))).name[:120] or "image"
        media_type = str(payload.get("media_type", "")).strip().lower()
        encoded = str(payload.get("content_base64", ""))
        allowed = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}
        if media_type not in allowed:
            raise DomainError("unsupported_attachment_type", "仅支持 PNG、JPEG、WebP 或 GIF 图片。", status=415)
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise DomainError("invalid_attachment", "图片内容不是有效的 Base64 数据。") from exc
        if not content or len(content) > 5_000_000:
            raise DomainError("attachment_too_large", "单张图片必须小于 5 MB。", status=413)
        if not self._validate_image_signature(media_type, content):
            raise DomainError("attachment_type_mismatch", "图片内容与声明格式不一致。", status=415)
        attachment_id = new_id("attachment")
        with self.repo.transaction() as connection:
            self._check_thread_version(connection, work_id, thread_id, expected)
            uri, digest = self.repo.atomic_write_bytes(
                f"attachments/{work_id}/{attachment_id}{allowed[media_type]}", content
            )
            timestamp = now()
            connection.execute(
                "INSERT INTO conversation_attachments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (attachment_id, work_id, thread_id, None, filename, media_type, uri, digest, len(content), "staged", timestamp),
            )
            connection.execute(
                "UPDATE conversation_threads SET version=version+1,updated_at=? WHERE id=?",
                (timestamp, thread_id),
            )
        return {"attachment_id": attachment_id, "work": self.get_work(work_id)}

    def get_conversation_attachment(self, work_id: str, attachment_id: str):
        with self.repo.connect() as connection:
            row = connection.execute(
                "SELECT media_type,content_uri FROM conversation_attachments WHERE id=? AND work_id=?",
                (attachment_id, work_id),
            ).fetchone()
        if not row:
            raise NotFound("conversation_attachment", attachment_id)
        path = (self.repo.data_dir / row["content_uri"]).resolve()
        if self.repo.data_dir not in path.parents or not path.is_file():
            raise NotFound("conversation_attachment", attachment_id)
        return row["media_type"], path.read_bytes()

    def post_conversation_message(self, work_id: str, thread_id: str, payload: dict):
        expected = int(payload.get("expected_thread_version", -1))
        text = str(payload.get("text", "")).strip()
        if not text:
            raise DomainError("validation_error", "消息不能为空。", details={"field": "text"})
        attachment_ids = payload.get("attachment_ids", [])
        if not isinstance(attachment_ids, list) or len(attachment_ids) > 4:
            raise DomainError("validation_error", "每条消息最多附带 4 张图片。", details={"field": "attachment_ids"})
        attachment_ids = [str(item).strip() for item in attachment_ids if str(item).strip()]
        with self.repo.transaction() as connection:
            thread = self._check_thread_version(connection, work_id, thread_id, expected)
            attachments = []
            for attachment_id in attachment_ids:
                attachment = connection.execute(
                    "SELECT id,filename,media_type,byte_size,status FROM conversation_attachments WHERE id=? AND work_id=? AND thread_id=?",
                    (attachment_id, work_id, thread_id),
                ).fetchone()
                if not attachment or attachment["status"] != "staged":
                    raise DomainError("invalid_attachment", "图片附件不存在、已使用或不属于当前对话。", status=409, details={"id": attachment_id})
                attachments.append(dict(attachment))
            user_message_id = self._append_conversation_message(
                connection, thread_id, "user", "text", {"text": text, "attachments": attachments}
            )
            if attachment_ids:
                connection.executemany(
                    "UPDATE conversation_attachments SET message_id=?,status='attached' WHERE id=?",
                    [(user_message_id, attachment_id) for attachment_id in attachment_ids],
                )
            history = []
            for row in connection.execute(
                "SELECT role,content_json FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread_id,)
            ).fetchall():
                content = json.loads(row["content_json"])
                history.append({"role": row["role"], "text": content.get("text", "")})
            first_idea = next((item["text"] for item in history if item["role"] == "user"), text)
            task_contract = self._conversation_task_contract(connection, work_id, payload.get("task_scope"))
            reply = self.provider.discuss_work(
                history, {"work_id": work_id, "idea": first_idea, "task_contract": task_contract, "attachments": attachments}
            )
            if attachments:
                reply["text"] += " 图片已随本轮保存；当前 Fake Provider 不具备视觉理解能力，因此没有声称读取图片内容。"
                reply.setdefault("tool_activity", []).append({
                    "tool": "store_conversation_attachments", "label": "保存对话图片",
                    "status": "succeeded", "output": f"{len(attachments)} 张（未进行视觉分析）",
                })
            reply = self._finalize_agent_reply(task_contract, reply)
            assistant_message_id = self._append_conversation_message(
                connection, thread_id, "assistant", "discussion", reply,
                provider=self.provider.descriptor(),
            )
            timestamp = now()
            connection.execute(
                "UPDATE conversation_threads SET version=version+1,updated_at=? WHERE id=?",
                (timestamp, thread_id),
            )
        return {
            "thread_id": thread_id,
            "assistant_message_id": assistant_message_id,
            "simulation": self.provider.is_simulation,
            "work": self.get_work(work_id),
        }

    def _finalize_agent_reply(self, task_contract: dict, reply: dict) -> dict:
        """Attach a durable, user-facing execution trace without storing hidden chain-of-thought."""
        reply = dict(reply or {})
        reply["task_contract"] = task_contract
        activity = reply.get("tool_activity")
        if not isinstance(activity, list) or not activity:
            activity = [
                {"tool": "load_workflow_template", "label": "加载任务契约", "status": "succeeded"},
                {"tool": "read_work_context", "label": "读取作品上下文", "status": "succeeded"},
            ]

        normalized_activity = []
        allowed_statuses = {"queued", "running", "succeeded", "failed", "waiting_user"}
        for item in activity[:12]:
            if not isinstance(item, dict):
                continue
            tool = str(item.get("tool") or "agent_step").strip()[:80]
            label = str(item.get("label") or tool).strip()[:120]
            status = str(item.get("status") or "succeeded").strip()
            output = str(item.get("output") or "").strip()[:240]
            normalized_activity.append(
                {
                    "tool": tool,
                    "label": label,
                    "status": status if status in allowed_statuses else "succeeded",
                    "output": output,
                }
            )
        reply["tool_activity"] = normalized_activity

        task_id = str(task_contract.get("id") or "brief.build")
        default_summaries = {
            "brief.build": "先确认作品想法与关键不确定项，再决定是否需要人物、世界观或方向草稿。",
            "blueprint.generate": "结合当前讨论与正式资料，判断是否已经足够形成全作方向候选。",
            "structure.plan": "以已确认的全作方向为边界，检查卷、章与场景结构需要怎样推进。",
            "chapter.plan": "只处理当前章节的目标、节拍与承接点，不改写全作方向。",
            "scene.draft.generate": "读取当前场景边界后，只提出候选或 Diff，不直接覆盖正文。",
            "release.review": "核对连续性、人物一致性与未决伏笔，再决定是否允许冻结发布。",
        }
        provider_reasoning = str(reply.get("reasoning_summary") or "").strip()
        provider_reasoning_content = str(reply.get("reasoning_content") or "").strip()[:12000]
        summary = str(provider_reasoning or default_summaries.get(task_id) or task_contract.get("task") or "确认当前任务范围并选择下一步。")
        summary = " ".join(summary.split())[:300]
        preview = reply.get("artifact_preview") if isinstance(reply.get("artifact_preview"), dict) else None
        if preview:
            kind_label = "人物卡" if preview.get("kind") == "character_card" else "世界观卡"
            outcome = f"已形成{kind_label}讨论草稿；正式资料尚未改变。"
        elif reply.get("ready_for_proposal") or reply.get("ready_to_organize"):
            outcome = "现有讨论已经可以整理为 Proposal；是否写入正式产物仍由用户决定。"
        else:
            outcome = "继续讨论并补齐关键约束；本轮没有写入正式产物。"
        reply["agent_trace"] = {
            "schema_version": "agent-trace/1.0",
            "visibility": "user_summary",
            "status": "completed",
            "task_id": task_id,
            "task": str(task_contract.get("task") or "继续当前创作任务")[:240],
            "scope": task_contract.get("task_scope") or {},
            "summary": summary,
            "reasoning": {
                "available": bool(provider_reasoning or provider_reasoning_content),
                "source": "provider" if provider_reasoning or provider_reasoning_content else "system",
                "is_simulation": bool(self.provider.is_simulation) if provider_reasoning or provider_reasoning_content else False,
                "mode": "chain" if provider_reasoning_content else "summary",
                "summary": summary,
                "content": provider_reasoning_content,
            },
            "steps": normalized_activity,
            "outcome": outcome,
        }
        return reply

    def update_conversation_settings(self, work_id: str, thread_id: str, payload: dict):
        expected = int(payload.get("expected_thread_version", -1))
        permission_mode = str(payload.get("permission_mode", "review")).strip()
        phase = str(payload.get("phase", "discuss")).strip()
        if permission_mode not in {"review", "managed"}:
            raise DomainError("validation_error", "Agent 授权模式无效。", details={"field": "permission_mode"})
        if phase not in {"discuss", "execute"}:
            raise DomainError("validation_error", "Agent 状态无效。", details={"field": "phase"})
        with self.repo.transaction() as connection:
            self._check_thread_version(connection, work_id, thread_id, expected)
            timestamp = now()
            connection.execute(
                "UPDATE conversation_threads SET permission_mode=?,phase=?,version=version+1,updated_at=? WHERE id=?",
                (permission_mode, phase, timestamp, thread_id),
            )
            actions = ["read", "discuss"]
            if permission_mode == "managed":
                actions.append("auto_accept_low_risk_writing")
            connection.execute(
                "UPDATE authorization_policies SET mode=?,allowed_actions_json=?,max_turns=?,version=version+1,updated_at=? WHERE thread_id=? AND status='active'",
                (permission_mode, canonical_json(actions), 12 if permission_mode == "managed" else None, timestamp, thread_id),
            )
        return {"thread_id": thread_id, "work": self.get_work(work_id)}

    def propose_conversation_knowledge(self, work_id: str, thread_id: str, payload: dict):
        """Turn an Agent discussion draft into an auditable knowledge Proposal."""
        expected_work = int(payload.get("expected_version", -1))
        expected_thread = int(payload.get("expected_thread_version", -1))
        requested_kind = str(payload.get("kind", "")).strip()
        if requested_kind not in {"character_card", "world_card"}:
            raise DomainError("validation_error", "资料候选类型无效。", details={"field": "kind"})
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected_work)
            self._check_thread_version(connection, work_id, thread_id, expected_thread)
            pending_kind = "character_card" if requested_kind == "character_card" else "world_entity"
            if connection.execute(
                "SELECT 1 FROM proposals WHERE work_id=? AND kind=? AND status='pending'",
                (work_id, pending_kind),
            ).fetchone():
                raise DomainError("proposal_waiting_user", "已有同类资料候选等待决定。", status=409)
            rows = connection.execute(
                "SELECT id,role,content_json FROM conversation_messages WHERE thread_id=? ORDER BY ordinal",
                (thread_id,),
            ).fetchall()
            messages = [(row, json.loads(row["content_json"])) for row in rows]
            preview = next(
                (
                    content.get("artifact_preview")
                    for row, content in reversed(messages)
                    if row["role"] == "assistant"
                    and isinstance(content.get("artifact_preview"), dict)
                    and content["artifact_preview"].get("kind") == requested_kind
                ),
                None,
            )
            if not preview:
                raise DomainError("knowledge_draft_required", "请先和 Agent 讨论出一张资料草稿。", status=409)
            title = str(payload.get("title") or preview.get("title") or "").strip()
            if not title or title in {"待命名角色", "待命名世界观", "世界观设定草稿"}:
                raise DomainError("knowledge_name_required", "请先在对话中明确资料名称。", status=409)
            user_notes = [
                str(content.get("text", "")).strip()
                for row, content in messages
                if row["role"] == "user" and str(content.get("text", "")).strip()
            ]
            source_message_ids = [row["id"] for row, _ in messages]
            scope_id = new_id("character" if requested_kind == "character_card" else "world-card")
            base_revision_id = None
            if requested_kind == "character_card":
                content = {
                    "name": title,
                    "canonical_name": title,
                    "aliases": [],
                    "source_type": "custom",
                    "role": user_notes[-1] if user_notes else str(preview.get("summary", "")),
                    "voice_anchors": [],
                    "knowledge_boundary": "",
                    "ooc_constraints": [],
                    "relationships": [],
                    "source_refs": [f"作品主对话 {thread_id}"],
                    "trust_status": "confirmed",
                }
            else:
                world_artifact = connection.execute(
                    "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='world_bible'",
                    (work_id,),
                ).fetchone()
                base_revision_id = world_artifact["current_revision_id"] if world_artifact else None
                content = {
                    "id": scope_id,
                    "name": title,
                    "kind": "custom",
                    "summary": user_notes[-1] if user_notes else str(preview.get("summary", "")),
                    "aliases": [],
                    "source": f"作品主对话 {thread_id}",
                    "source_type": "custom",
                    "confidence_status": "confirmed",
                    "scope": "work",
                    "participants": [],
                    "related_world_ids": [],
                    "status": "active",
                }
            candidate = {
                "schema_version": "conversation-knowledge-proposal/1.0",
                "kind": requested_kind,
                "scope_id": scope_id,
                "base_revision_id": base_revision_id,
                "content": content,
                "source_thread_id": thread_id,
                "source_message_ids": source_message_ids,
            }
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(
                f"artifacts/proposals/{proposal_id}.json",
                json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
            )
            timestamp = now()
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id, work_id, pending_kind,
                    "character" if requested_kind == "character_card" else "work", scope_id,
                    base_revision_id, candidate_uri, candidate_hash,
                    canonical_json({"format": "knowledge-fields/1.0", "changes": [{"field": "名称", "after": title}, {"field": "讨论依据", "after": content.get("role") or content.get("summary", "")}]}),
                    canonical_json(source_message_ids), "medium", "pending",
                    canonical_json(self.provider.descriptor()), timestamp, None,
                ),
            )
            proposal_preview = {
                **preview,
                "title": title,
                "status": "proposal",
                "summary": "Agent 已把讨论整理成资料候选。采纳后才会建立正式修订。",
            }
            self._append_conversation_message(
                connection, thread_id, "assistant", "proposal",
                {
                    "text": f"我已把“{title}”整理成可审查的{'人物卡' if requested_kind == 'character_card' else '世界观卡'}候选。",
                    "artifact_preview": proposal_preview,
                    "proposal_id": proposal_id,
                    "tool_activity": [{"tool": "create_knowledge_proposal", "label": "创建资料 Proposal", "status": "succeeded", "output": proposal_id}],
                },
                provider=self.provider.descriptor(), proposal_id=proposal_id,
            )
            connection.execute(
                "UPDATE conversation_threads SET phase='execute',version=version+1,updated_at=? WHERE id=?",
                (timestamp, thread_id),
            )
            self._bump_work(connection, work_id, version)
        return {"proposal_id": proposal_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def organize_conversation_proposal(self, work_id: str, thread_id: str, payload: dict):
        requested_scope = payload.get("task_scope") if isinstance(payload.get("task_scope"), dict) else {}
        if requested_scope.get("surface") == "chapter":
            return self._organize_chapter_plan_proposal(work_id, thread_id, payload, requested_scope)
        expected_work = int(payload.get("expected_version", -1))
        expected_thread = int(payload.get("expected_thread_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected_work)
            thread = self._check_thread_version(connection, work_id, thread_id, expected_thread)
            if thread["scope_type"] != "work" or thread["scope_id"] != work_id:
                raise DomainError("invalid_thread_scope", "只有作品主对话可以整理整体故事方案。", status=409)
            if connection.execute(
                "SELECT 1 FROM proposals WHERE work_id=? AND kind='brief_blueprint' AND status='pending'", (work_id,)
            ).fetchone():
                raise DomainError("proposal_waiting_user", "已有故事方案等待决定，请先采纳或退回。", status=409)
            messages = []
            for row in connection.execute(
                "SELECT role,content_json FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread_id,)
            ).fetchall():
                content = json.loads(row["content_json"])
                messages.append({"role": row["role"], "text": content.get("text", "")})
            user_messages = [item["text"] for item in messages if item["role"] == "user" and item["text"]]
            if not user_messages:
                raise DomainError("discussion_required", "请先和创作导演讨论一句故事想法。", status=409)
            idea = user_messages[0]
            discussion_notes = user_messages[1:]
            brief = {
                "idea": idea,
                "mode": "pending_analysis",
                "story_modes": [],
                "characters": [],
                "character_card_ids": [],
                "target_length": "pending_analysis",
                "constraints": "\n".join(discussion_notes),
                "has_sensei": False,
                "sensei_decision": "pending_analysis",
                "status": "proposed",
            }
            analysis_context = {
                "character_cards": self._analysis_character_cards(connection, work_id),
                "world": self._analysis_world_summary(connection, work_id),
            }
            blueprint = self.provider.generate_blueprint(brief, analysis_context)
            blueprint["status"] = "proposed"
            current_brief = connection.execute(
                "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='brief'", (work_id,)
            ).fetchone()
            current_blueprint = connection.execute(
                "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='story_blueprint'", (work_id,)
            ).fetchone()
            candidate = {
                "schema_version": "brief-blueprint-proposal/1.0",
                "brief": brief,
                "story_blueprint": blueprint,
                "base_brief_revision_id": current_brief["current_revision_id"] if current_brief else None,
                "base_blueprint_revision_id": current_blueprint["current_revision_id"] if current_blueprint else None,
                "source_thread_id": thread_id,
                "source_message_ids": [
                    row["id"] for row in connection.execute(
                        "SELECT id FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread_id,)
                    ).fetchall()
                ],
            }
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(
                f"artifacts/proposals/{proposal_id}.json",
                json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
            )
            diff = {
                "format": "proposal-fields/1.0",
                "changes": [
                    {"field": "写作想法", "after": idea},
                    {"field": "故事前提", "after": blueprint.get("premise", "")},
                    {"field": "核心冲突", "after": blueprint.get("central_conflict", "")},
                    {"field": "讨论补充", "after": "\n".join(discussion_notes)},
                ],
            }
            timestamp = now()
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id, work_id, "brief_blueprint", "work", work_id,
                    candidate["base_blueprint_revision_id"], candidate_uri, candidate_hash,
                    canonical_json(diff), canonical_json(candidate["source_message_ids"]),
                    "medium", "pending", canonical_json(self.provider.descriptor()), timestamp, None,
                ),
            )
            self._append_conversation_message(
                connection, thread_id, "assistant", "proposal",
                {"text": "我已整理成可审查的写作想法与故事方向，采纳前不会写入正式产物。", "proposal_id": proposal_id},
                provider=self.provider.descriptor(), proposal_id=proposal_id,
            )
            connection.execute(
                "UPDATE conversation_threads SET phase='execute',version=version+1,updated_at=? WHERE id=?",
                (timestamp, thread_id),
            )
            self._bump_work(connection, work_id, version)
        return {"proposal_id": proposal_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def _organize_chapter_plan_proposal(self, work_id: str, thread_id: str, payload: dict, scope: dict):
        expected_work = int(payload.get("expected_version", -1))
        expected_thread = int(payload.get("expected_thread_version", -1))
        chapter_id = str(scope.get("chapter_id", "")).strip()
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected_work)
            thread = self._check_thread_version(connection, work_id, thread_id, expected_thread)
            chapter = connection.execute(
                "SELECT id,title FROM chapters WHERE id=? AND work_id=?", (chapter_id, work_id)
            ).fetchone()
            if not chapter:
                raise DomainError("invalid_writing_target", "请先选择一章，再整理章内细纲。", status=409)
            if connection.execute(
                "SELECT 1 FROM proposals WHERE work_id=? AND kind='chapter_plan' AND scope_id=? AND status='pending'",
                (work_id, chapter_id),
            ).fetchone():
                raise DomainError("proposal_waiting_user", "本章已有细纲候选等待决定，请先采纳或退回。", status=409)
            messages = []
            for row in connection.execute(
                "SELECT role,content_json FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread_id,)
            ).fetchall():
                content = json.loads(row["content_json"])
                messages.append({"role": row["role"], "text": content.get("text", "")})
            if not any(item["role"] == "user" and item["text"] for item in messages):
                raise DomainError("discussion_required", "请先和 Agent 讨论本章要完成的变化。", status=409)
            target_revision = connection.execute(
                "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='writing_target'", (work_id,)
            ).fetchone()
            blueprint_revision = connection.execute(
                "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='story_blueprint'", (work_id,)
            ).fetchone()
            chapter_context = {
                "chapter_id": chapter_id,
                "chapter_title": chapter["title"],
                "story_blueprint_revision_id": blueprint_revision["current_revision_id"] if blueprint_revision else None,
                "writing_target_revision_id": target_revision["current_revision_id"] if target_revision else None,
            }
            candidate_plan = self.provider.generate_chapter_plan(messages, chapter_context)
            candidate_plan["status"] = "proposed"
            candidate = {
                "schema_version": "chapter-plan-proposal/1.0",
                "chapter_id": chapter_id,
                "chapter_title": chapter["title"],
                "chapter_plan": candidate_plan,
                "base_revision_id": connection.execute(
                    "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='chapter_plan' AND scope_id=?",
                    (work_id, chapter_id),
                ).fetchone()[0] if connection.execute(
                    "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='chapter_plan' AND scope_id=?",
                    (work_id, chapter_id),
                ).fetchone() else None,
                "source_thread_id": thread_id,
                "source_message_ids": [
                    row["id"] for row in connection.execute(
                        "SELECT id FROM conversation_messages WHERE thread_id=? ORDER BY ordinal", (thread_id,)
                    ).fetchall()
                ],
            }
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(
                f"artifacts/proposals/{proposal_id}.json",
                json.dumps(candidate, ensure_ascii=False, indent=2) + "\n",
            )
            diff = {
                "format": "chapter-plan-fields/1.0",
                "changes": [{"field": "章内细纲", "after": candidate_plan.get("chapter_goal", "")}],
            }
            timestamp = now()
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    proposal_id, work_id, "chapter_plan", "chapter", chapter_id,
                    candidate["base_revision_id"], candidate_uri, candidate_hash,
                    canonical_json(diff), canonical_json(candidate["source_message_ids"]),
                    "medium", "pending", canonical_json(self.provider.descriptor()), timestamp, None,
                ),
            )
            self._append_conversation_message(
                connection, thread_id, "assistant", "proposal",
                {"text": f"我已整理《{chapter['title']}》的章内细纲候选。它不会替换全作方向，先由你审查。", "proposal_id": proposal_id},
                provider=self.provider.descriptor(), proposal_id=proposal_id,
            )
            connection.execute(
                "UPDATE conversation_threads SET phase='execute',version=version+1,updated_at=? WHERE id=?",
                (timestamp, thread_id),
            )
            self._bump_work(connection, work_id, version)
        return {"proposal_id": proposal_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def _artifact(self, connection, work_id: str, kind: str, scope_type: str, scope_id: str):
        row = connection.execute(
            "SELECT * FROM artifacts WHERE work_id=? AND kind=? AND scope_type=? AND scope_id=?",
            (work_id, kind, scope_type, scope_id),
        ).fetchone()
        if row:
            return dict(row)
        artifact_id = new_id("artifact")
        connection.execute(
            "INSERT INTO artifacts VALUES (?,?,?,?,?,?,?)",
            (artifact_id, work_id, kind, scope_type, scope_id, None, now()),
        )
        return {"id": artifact_id, "current_revision_id": None}

    def _add_revision(
        self,
        connection,
        artifact: dict,
        content,
        created_by: str,
        provenance: dict,
        schema_version: str = "1.0",
    ):
        revision_id = new_id("revision")
        count = connection.execute("SELECT COUNT(*) FROM revisions WHERE artifact_id=?", (artifact["id"],)).fetchone()[0]
        text = canonical_json(content) + "\n"
        uri, digest = self.repo.atomic_write_text(f"artifacts/{artifact['id']}/{revision_id}.json", text)
        connection.execute(
            "INSERT INTO revisions VALUES (?,?,?,?,?,?,?,?,?,?)",
            (revision_id, artifact["id"], artifact.get("current_revision_id"), count + 1, schema_version, uri, digest, canonical_json(provenance), created_by, now()),
        )
        connection.execute("UPDATE artifacts SET current_revision_id=? WHERE id=?", (revision_id, artifact["id"]))
        return revision_id

    @staticmethod
    def _scene_blocks_from_text(text: str, namespace: str = "") -> list[dict]:
        blocks = []
        for index, raw_line in enumerate(str(text).splitlines()):
            line = raw_line.strip()
            if not line:
                continue
            ascii_divider = line.find(":")
            chinese_divider = line.find("：")
            dividers = [value for value in (ascii_divider, chinese_divider) if value >= 0]
            divider = min(dividers) if dividers else -1
            digest = sha256_text(f"{namespace}:{index}:{line}").split(":", 1)[1][:12]
            block_id = f"block-{digest}"
            if divider > 0 and line[:divider].strip():
                blocks.append({
                    "id": block_id,
                    "type": "dialogue",
                    "speaker": line[:divider].strip(),
                    "text": line[divider + 1:].strip(),
                })
            else:
                blocks.append({"id": block_id, "type": "action", "text": line})
        return blocks

    @staticmethod
    def _scene_text_from_blocks(blocks: list[dict]) -> str:
        lines = []
        for block in blocks:
            if block["type"] == "dialogue":
                lines.append(f"{block['speaker']}: {block['text']}")
            else:
                lines.append(block["text"])
        return "\n".join(lines) + ("\n" if lines else "")

    def _normalize_scene_blocks(self, blocks) -> list[dict]:
        if not isinstance(blocks, list) or not blocks:
            raise DomainError("validation_error", "正文至少需要一个对白或动作块。", details={"field": "blocks"})
        normalized = []
        seen_ids = set()
        for index, block in enumerate(blocks):
            if not isinstance(block, dict):
                raise DomainError("validation_error", "正文块格式无效。", details={"index": index})
            block_id = str(block.get("id", "")).strip()
            block_type = str(block.get("type", "")).strip()
            text = str(block.get("text", "")).strip()
            suffix = block_id[6:] if block_id.startswith("block-") else ""
            if (
                not suffix
                or len(block_id) > 96
                or not suffix.isascii()
                or any(not (char.isalnum() or char in "_-") for char in suffix)
            ):
                raise DomainError("validation_error", "正文块需要稳定且有效的 ID。", details={"index": index, "field": "id"})
            if block_id in seen_ids:
                raise DomainError("validation_error", "正文块 ID 不能重复。", details={"index": index, "id": block_id})
            if block_type not in {"action", "dialogue"}:
                raise DomainError("validation_error", "正文块类型只能是 action 或 dialogue。", details={"index": index})
            if not text:
                raise DomainError("validation_error", "正文块内容不能为空。", details={"index": index, "field": "text"})
            item = {"id": block_id, "type": block_type, "text": text}
            if block_type == "dialogue":
                speaker = str(block.get("speaker", "")).strip()
                if not speaker:
                    raise DomainError("validation_error", "对白块必须填写说话人。", details={"index": index, "field": "speaker"})
                item["speaker"] = speaker
            normalized.append(item)
            seen_ids.add(block_id)
        return normalized

    def _scene_content_from_text(self, text: str, namespace: str = "") -> dict:
        blocks = self._scene_blocks_from_text(text, namespace)
        return {"schema_version": "scene-blocks/1.0", "blocks": blocks, "text": str(text)}

    def _analysis_character_cards(self, connection, work_id: str) -> list[dict]:
        cards = []
        rows = connection.execute(
            "SELECT * FROM artifacts WHERE work_id=? AND kind='character_card' AND current_revision_id IS NOT NULL",
            (work_id,),
        ).fetchall()
        for row in rows:
            revision = connection.execute("SELECT * FROM revisions WHERE id=?", (row["current_revision_id"],)).fetchone()
            content = json.loads(self.repo.read_text(revision["content_uri"]))
            if content.get("status", "active") == "archived":
                continue
            cards.append({
                "id": row["scope_id"],
                "name": content.get("name", ""),
                "canonical_name": content.get("canonical_name", ""),
                "aliases": content.get("aliases", []),
                "source_type": content.get("source_type", "custom"),
                "trust_status": content.get("trust_status", "open"),
            })
        return cards

    def _analysis_world_summary(self, connection, work_id: str) -> dict:
        artifact = connection.execute(
            "SELECT * FROM artifacts WHERE work_id=? AND kind='world_bible'", (work_id,)
        ).fetchone()
        if not artifact or not artifact["current_revision_id"]:
            return {
                "label": "尚未建立世界观基础",
                "detail": "当前作品没有世界观条目；可以先分析想法，确认方向后再建立原创设定。",
                "source_type": "blank",
                "total_items": 0,
                "confirmed_items": 0,
            }
        revision = connection.execute("SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)).fetchone()
        bible = json.loads(self.repo.read_text(revision["content_uri"]))
        entries = [
            item for collection in ("entities", "rules", "timeline")
            for item in bible.get(collection, [])
            if item.get("status", "active") != "archived"
        ]
        source_type = bible.get("source_type", "custom")
        if source_type == "ba_starter":
            label = "BA 起始架构"
        elif source_type == "mixed":
            label = "BA 起始架构 + 本作自定义设定"
        else:
            label = "本作自定义世界观"
        return {
            "label": label,
            "detail": f"当前资料库有 {len(entries)} 项设定，其中 {sum(item.get('confidence_status') == 'confirmed' for item in entries)} 项已确认。未确认条目不会被当作既定事实。",
            "source_type": source_type,
            "total_items": len(entries),
            "confirmed_items": sum(item.get("confidence_status") == "confirmed" for item in entries),
            "revision_id": revision["id"],
        }

    def save_brief(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        intent_only = bool(payload.get("intent_only", False))
        mode = "pending_analysis" if intent_only else payload.get("mode", "bond_short")
        if not intent_only and mode not in MODE_SOURCES:
            raise DomainError("validation_error", "未知写作模式。", details={"mode": mode})
        idea = str(payload.get("idea", "")).strip()
        if not idea:
            raise DomainError("validation_error", "一句想法不能为空。", details={"field": "idea"})
        brief = {
            "idea": idea,
            "mode": mode,
            "characters": [str(x).strip() for x in payload.get("characters", []) if str(x).strip()],
            "character_card_ids": [str(x).strip() for x in payload.get("character_card_ids", []) if str(x).strip()],
            "target_length": payload.get("target_length", "short"),
            "constraints": str(payload.get("constraints", "")).strip(),
            "has_sensei": bool(payload.get("has_sensei", False)),
            "sensei_decision": "manual" if not intent_only else "pending_analysis",
            "status": "analysis_pending" if intent_only else "confirmed",
        }
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            artifact = self._artifact(connection, work_id, "brief", "work", work_id)
            revision_id = self._add_revision(connection, artifact, brief, "user", {
                "workflow": "brief.intent" if intent_only else "brief.build",
                "pack": PACK_VERSION,
            })
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def generate_blueprint(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            brief_artifact = connection.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='brief'", (work_id,)).fetchone()
            if not brief_artifact or not brief_artifact["current_revision_id"]:
                raise DomainError("brief_required", "请先保存写作想法。", status=409)
            brief_revision = connection.execute("SELECT * FROM revisions WHERE id=?", (brief_artifact["current_revision_id"],)).fetchone()
            brief = json.loads(self.repo.read_text(brief_revision["content_uri"]))
            analysis_context = {
                "character_cards": self._analysis_character_cards(connection, work_id),
                "world": self._analysis_world_summary(connection, work_id),
            }
            blueprint = self.provider.generate_blueprint(brief, analysis_context)
            feedback = str(payload.get("feedback", "")).strip()
            if feedback:
                blueprint["feedback"] = feedback
            # Older direct API consumers already submit a fully formed Brief.
            # The product UI submits an intent-only Brief and must confirm this proposal.
            blueprint["status"] = "proposed" if brief.get("status") == "analysis_pending" or feedback else "accepted"
            artifact = self._artifact(connection, work_id, "story_blueprint", "work", work_id)
            revision_id = self._add_revision(connection, artifact, blueprint, "agent", {
                "workflow": "blueprint.generate", "pack": PACK_VERSION,
                "provider": self.provider.descriptor(), "input_revisions": [brief_revision["id"]],
                "feedback": feedback or None,
            })
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def confirm_blueprint(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        mode = str(payload.get("mode", "")).strip()
        if mode not in MODE_SOURCES:
            raise DomainError("validation_error", "请选择本场起草规则包。", details={"field": "mode"})
        requested_ids = [str(value).strip() for value in payload.get("character_card_ids", []) if str(value).strip()]
        if not requested_ids:
            raise DomainError("validation_error", "请从人物库选择至少一张人物卡。", details={"field": "character_card_ids"})
        sensei_decision = str(payload.get("sensei_presence", "auto")).strip()
        if sensei_decision not in {"auto", "present", "absent"}:
            raise DomainError("validation_error", "老师出场选择无效。", details={"field": "sensei_presence"})
        feedback = str(payload.get("feedback", "")).strip()
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            brief_artifact = connection.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='brief'", (work_id,)).fetchone()
            blueprint_artifact = connection.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='story_blueprint'", (work_id,)).fetchone()
            if not brief_artifact or not brief_artifact["current_revision_id"] or not blueprint_artifact or not blueprint_artifact["current_revision_id"]:
                raise DomainError("blueprint_required", "请先分析写作想法。", status=409)
            brief_revision = connection.execute("SELECT * FROM revisions WHERE id=?", (brief_artifact["current_revision_id"],)).fetchone()
            blueprint_revision = connection.execute("SELECT * FROM revisions WHERE id=?", (blueprint_artifact["current_revision_id"],)).fetchone()
            brief = json.loads(self.repo.read_text(brief_revision["content_uri"]))
            blueprint = json.loads(self.repo.read_text(blueprint_revision["content_uri"]))
            if blueprint.get("status", "accepted") != "proposed":
                raise DomainError("blueprint_not_pending", "当前故事方向没有等待确认的候选。", status=409)
            available_cards = {card["id"]: card for card in self._analysis_character_cards(connection, work_id)}
            invalid_ids = [card_id for card_id in requested_ids if card_id not in available_cards]
            if invalid_ids:
                raise DomainError("validation_error", "选择的人物卡不属于当前作品。", details={"character_card_ids": invalid_ids})
            recommendations = blueprint.get("recommendations", {})
            has_sensei = (
                recommendations.get("sensei_presence") == "present"
                if sensei_decision == "auto"
                else sensei_decision == "present"
            )
            secondary = [
                item for item in recommendations.get("secondary_scene_modes", [])
                if item in MODE_SOURCES and item != mode
            ]
            confirmed_brief = {
                **brief,
                "mode": mode,
                "story_modes": [mode, *secondary],
                "characters": [available_cards[card_id]["name"] for card_id in requested_ids],
                "character_card_ids": requested_ids,
                "has_sensei": has_sensei,
                "sensei_decision": sensei_decision,
                "status": "confirmed",
                "constraints": feedback or brief.get("constraints", ""),
            }
            confirmed_brief_revision = self._add_revision(connection, dict(brief_artifact), confirmed_brief, "user", {
                "workflow": "brief.confirm", "pack": PACK_VERSION,
                "blueprint_revision_id": blueprint_revision["id"],
                "character_card_ids": requested_ids,
            })
            accepted_blueprint = {
                **blueprint,
                "status": "accepted",
                "decision": {
                    "mode": mode,
                    "character_card_ids": requested_ids,
                    "sensei_presence": sensei_decision,
                    "feedback": feedback,
                    "brief_revision_id": confirmed_brief_revision,
                },
            }
            accepted_blueprint_revision = self._add_revision(connection, dict(blueprint_artifact), accepted_blueprint, "user", {
                "workflow": "blueprint.confirm", "pack": PACK_VERSION,
                "input_revisions": [brief_revision["id"], blueprint_revision["id"]],
            })
            self._bump_work(connection, work_id, version)
        return {
            "brief_revision_id": confirmed_brief_revision,
            "blueprint_revision_id": accepted_blueprint_revision,
            "work": self.get_work(work_id),
        }

    def save_work_canon(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        facts = payload.get("facts", [])
        if not isinstance(facts, list):
            raise DomainError("validation_error", "事实清单必须是数组。", details={"field": "facts"})
        normalized = []
        for index, fact in enumerate(facts):
            text = str(fact.get("text", "")).strip() if isinstance(fact, dict) else ""
            source = str(fact.get("source", "")).strip() if isinstance(fact, dict) else ""
            confidence = str(fact.get("confidence_status", "confirmed")).strip() if isinstance(fact, dict) else "confirmed"
            if not text:
                raise DomainError("validation_error", "每条事实都需要内容。", details={"index": index})
            if not source:
                raise DomainError("validation_error", "每条事实都需要来源。", details={"index": index})
            if confidence not in {"confirmed", "inferred", "open", "conflict", "retired"}:
                raise DomainError("validation_error", "事实可信状态无效。", details={"index": index})
            scope = str(fact.get("scope", "work")).strip() or "work"
            if scope not in {"work", "chapter", "scene"}:
                raise DomainError("validation_error", "事实作用域无效。", details={"index": index})
            status = str(fact.get("status", "active")).strip() or "active"
            if status not in {"active", "archived"}:
                raise DomainError("validation_error", "事实状态无效。", details={"index": index})
            normalized.append({
                "id": str(fact.get("id", "")).strip() or new_id("fact"),
                "text": text,
                "source": source,
                "confidence_status": confidence,
                "scope": scope,
                "status": status,
            })
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            artifact = self._artifact(connection, work_id, "work_canon", "work", work_id)
            revision_id = self._add_revision(connection, artifact, {"facts": normalized}, "user", {"workflow": "canon.assemble", "pack": PACK_VERSION, "source_type": "user_confirmed"})
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def save_character_card(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        name = str(payload.get("name", "")).strip()
        if not name:
            raise DomainError("validation_error", "角色名称不能为空。", details={"field": "name"})
        source_refs = [str(item).strip() for item in payload.get("source_refs", []) if str(item).strip()]
        if not source_refs:
            raise DomainError("validation_error", "人物卡至少需要一条来源。", details={"field": "source_refs"})
        source_type = str(payload.get("source_type", "custom")).strip()
        if source_type not in {"official_reference", "custom"}:
            raise DomainError("validation_error", "人物卡来源类型无效。", details={"field": "source_type"})
        trust_status = str(payload.get("trust_status", "confirmed")).strip() or "confirmed"
        if trust_status not in {"confirmed", "inferred", "open", "unverified", "conflict"}:
            raise DomainError("validation_error", "人物卡采用状态无效。", details={"field": "trust_status"})
        card = {
            "name": name,
            "canonical_name": str(payload.get("canonical_name", name)).strip() or name,
            "aliases": [str(item).strip() for item in payload.get("aliases", []) if str(item).strip()],
            "source_type": source_type,
            "role": str(payload.get("role", "")).strip(),
            "voice_anchors": [str(item).strip() for item in payload.get("voice_anchors", []) if str(item).strip()],
            "knowledge_boundary": str(payload.get("knowledge_boundary", "")).strip(),
            "ooc_constraints": [str(item).strip() for item in payload.get("ooc_constraints", []) if str(item).strip()],
            "relationships": [
                {
                    "target": str(item.get("target", "")).strip(),
                    "kind": str(item.get("kind", "关系待定")).strip(),
                    "summary": str(item.get("summary", "")).strip(),
                    "status": str(item.get("status", "confirmed")).strip(),
                }
                for item in payload.get("relationships", [])
                if isinstance(item, dict) and str(item.get("target", "")).strip()
            ],
            "source_refs": source_refs,
            "trust_status": trust_status,
        }
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            # Card identity remains stable even when the display name changes.
            card_id = str(payload.get("card_id", "")).strip() or new_id("character")
            artifact = self._artifact(connection, work_id, "character_card", "character", card_id)
            revision_id = self._add_revision(connection, artifact, card, "user", {"workflow": "character.prepare", "pack": PACK_VERSION, "source_refs": source_refs})
            self._bump_work(connection, work_id, version)
        return {"card_id": card_id, "revision_id": revision_id, "work": self.get_work(work_id)}

    def archive_character_card(self, work_id: str, card_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            artifact = connection.execute(
                "SELECT * FROM artifacts WHERE work_id=? AND kind='character_card' AND scope_type='character' AND scope_id=?",
                (work_id, card_id),
            ).fetchone()
            if not artifact or not artifact["current_revision_id"]:
                raise NotFound("character_card", card_id)
            revision = connection.execute("SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)).fetchone()
            card = json.loads(self.repo.read_text(revision["content_uri"]))
            if card.get("status") == "archived":
                raise DomainError("already_archived", "人物卡已经归档。", status=409)
            card["status"] = "archived"
            revision_id = self._add_revision(connection, dict(artifact), card, "user", {
                "workflow": "character.archive", "pack": PACK_VERSION, "source_revision_id": revision["id"],
            })
            self._bump_work(connection, work_id, version)
        return {"card_id": card_id, "revision_id": revision_id, "work": self.get_work(work_id)}

    def _merge_world_source_type(self, source_types: list[str]) -> str:
        values = set(source_types)
        if "ba_starter" in values and len(values) == 1:
            return "ba_starter"
        if len(values) > 1 or "mixed" in values:
            return "mixed"
        return next(iter(values), "custom")

    def save_world_bible(self, work_id: str, payload: dict):
        """Save world rules and timeline as a distinct versioned artifact, never as chat text."""
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip() or "作品世界观"
        source_type = str(payload.get("source_type", "custom")).strip()
        if source_type not in {"official_reference", "custom", "mixed", "ba_starter"}:
            raise DomainError("validation_error", "世界观来源类型无效。", details={"field": "source_type"})

        def normalized_items(items, kind):
            if not isinstance(items, list):
                raise DomainError("validation_error", f"{kind}必须是数组。")
            result = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise DomainError("validation_error", f"{kind}条目无效。", details={"index": index})
                text = str(item.get("text", item.get("label", ""))).strip()
                source = str(item.get("source", "")).strip()
                if not text or not source:
                    raise DomainError("validation_error", f"每条{kind}都需要内容和来源。", details={"index": index})
                status = str(item.get("confidence_status", "confirmed")).strip()
                if status not in {"confirmed", "inferred", "open", "conflict", "retired"}:
                    raise DomainError("validation_error", "可信状态无效。", details={"index": index})
                scope = str(item.get("scope", "work")).strip() or "work"
                if scope not in {"work", "chapter", "scene"}:
                    raise DomainError("validation_error", f"{kind}作用域无效。", details={"index": index})
                item_status = str(item.get("status", "active")).strip() or "active"
                if item_status not in {"active", "archived"}:
                    raise DomainError("validation_error", f"{kind}状态无效。", details={"index": index})
                result.append({
                    "id": str(item.get("id", "")).strip() or new_id("world" if kind == "世界规则" else "event"),
                    "text": text,
                    "category": str(item.get("category", "general")).strip() or "general",
                    "source": source,
                    "confidence_status": status,
                    "scope": scope,
                    "participants": [str(value).strip() for value in item.get("participants", []) if str(value).strip()],
                    "status": item_status,
                })
            return result

        rules = normalized_items(payload.get("rules", []), "世界规则")
        timeline = normalized_items(payload.get("timeline", []), "时间线事件")
        def normalized_entities(items):
            if not isinstance(items, list):
                raise DomainError("validation_error", "世界观卡必须是数组。", details={"field": "entities"})
            result = []
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    raise DomainError("validation_error", "世界观卡条目无效。", details={"index": index})
                name = str(item.get("name", "")).strip()
                source = str(item.get("source", "")).strip()
                if not name or not source:
                    raise DomainError("validation_error", "每张世界观卡都需要名称和来源。", details={"index": index})
                confidence = str(item.get("confidence_status", "confirmed")).strip()
                if confidence not in {"confirmed", "inferred", "open", "conflict", "retired"}:
                    raise DomainError("validation_error", "世界观卡可信状态无效。", details={"index": index})
                entity_source_type = str(item.get("source_type", source_type)).strip()
                if entity_source_type not in {"official_reference", "custom", "mixed", "ba_starter"}:
                    raise DomainError("validation_error", "世界观卡来源类型无效。", details={"index": index})
                entity_status = str(item.get("status", "active")).strip() or "active"
                if entity_status not in {"active", "archived"}:
                    raise DomainError("validation_error", "世界观卡状态无效。", details={"index": index})
                entity_scope = str(item.get("scope", "work")).strip() or "work"
                if entity_scope not in {"work", "chapter", "scene"}:
                    raise DomainError("validation_error", "世界观卡作用域无效。", details={"index": index})
                entity_kind = str(item.get("kind", "custom")).strip() or "custom"
                if entity_kind not in {"place", "academy", "organization", "object", "technology", "custom"}:
                    raise DomainError("validation_error", "世界观卡类型无效。", details={"index": index})
                result.append({
                    "id": str(item.get("id", "")).strip() or new_id("world-card"),
                    "name": name,
                    "kind": entity_kind,
                    "summary": str(item.get("summary", "")).strip(),
                    "aliases": [str(value).strip() for value in item.get("aliases", []) if str(value).strip()],
                    "source": source,
                    "source_type": entity_source_type,
                    "confidence_status": confidence,
                    "scope": entity_scope,
                    "participants": [str(value).strip() for value in item.get("participants", []) if str(value).strip()],
                    "related_world_ids": [
                        str(value).strip()
                        for value in item.get("related_world_ids", [])
                        if str(value).strip()
                    ],
                    "status": entity_status,
                })
            return result

        entities = normalized_entities(payload.get("entities", []))
        entity_ids = {item["id"] for item in entities}
        for index, entity in enumerate(entities):
            related = list(dict.fromkeys(entity["related_world_ids"]))
            invalid = [item_id for item_id in related if item_id not in entity_ids or item_id == entity["id"]]
            if invalid:
                raise DomainError(
                    "validation_error",
                    "世界观卡关联必须指向当前作品中的其他世界观卡。",
                    details={"index": index, "field": "related_world_ids", "ids": invalid},
                )
            entity["related_world_ids"] = related
        effective_source_type = self._merge_world_source_type(
            [source_type, *(item["source_type"] for item in entities)]
        )
        bible = {"title": title, "source_type": effective_source_type, "entities": entities, "rules": rules, "timeline": timeline}
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            artifact = self._artifact(connection, work_id, "world_bible", "work", work_id)
            revision_id = self._add_revision(connection, artifact, bible, "user", {
                "workflow": "world.assemble", "pack": PACK_VERSION, "source_type": effective_source_type,
            })
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def apply_ba_world_starter(self, work_id: str, payload: dict):
        """Create a work-owned, editable BA setting starter at an explicit user action.

        The template is intentionally stored as open knowledge.  It must be
        reviewed, sourced and confirmed before scene assembly can use it.
        """
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            artifact = self._artifact(connection, work_id, "world_bible", "work", work_id)
            starter = starter_bible()
            if artifact.get("current_revision_id"):
                current_revision = connection.execute(
                    "SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)
                ).fetchone()
                current = json.loads(self.repo.read_text(current_revision["content_uri"]))
                existing_ids = {
                    item.get("id")
                    for collection in ("entities", "rules", "timeline")
                    for item in current.get(collection, [])
                }
                starter_ids = {item["id"] for item in starter["entities"]}
                if existing_ids & starter_ids:
                    raise DomainError(
                        "world_starter_already_applied",
                        "BA 世界观起始架构已在当前作品中。请直接修订这些卡片。",
                        status=409,
                    )
                bible = {
                    "title": current.get("title") or starter["title"],
                    "source_type": "mixed" if current.get("entities") or current.get("rules") or current.get("timeline") else "ba_starter",
                    "entities": [*current.get("entities", []), *starter["entities"]],
                    "rules": current.get("rules", []),
                    "timeline": current.get("timeline", []),
                }
                provenance_source_revision = current_revision["id"]
            else:
                bible = starter
                provenance_source_revision = None
            revision_id = self._add_revision(
                connection,
                artifact,
                bible,
                "user",
                {
                    "workflow": "world.starter.apply",
                    "pack": PACK_VERSION,
                    "starter_version": BA_WORLD_STARTER_VERSION,
                    "source": BA_WORLD_STARTER_SOURCE,
                    "source_revision_id": provenance_source_revision,
                    "disclosure": "这是待核对的产品起始架构，不是自动确认的 BA 原作事实。",
                },
            )
            self._bump_work(connection, work_id, version)
        return {
            "revision_id": revision_id,
            "starter_version": BA_WORLD_STARTER_VERSION,
            "disclosure": "BA 世界观起始架构已复制到本作品；全部条目均为待核对，尚不会进入 Agent。",
            "work": self.get_work(work_id),
        }

    def create_reference_file(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip()
        content = str(payload.get("content", "")).strip()
        source_label = str(payload.get("source_label", "")).strip()
        if not title or not content or not source_label:
            raise DomainError("validation_error", "资料名称、内容和来源都不能为空。")
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            ref_id = new_id("reference")
            uri, digest = self.repo.atomic_write_text(f"references/{ref_id}.md", content + "\n")
            timestamp = now()
            connection.execute("INSERT INTO reference_files VALUES (?,?,?,?,?,?,?,?,?,?,?)", (ref_id, work_id, title, payload.get("kind", "note"), uri, digest, source_label, payload.get("trust_status", "unverified"), 1, timestamp, timestamp))
            self._bump_work(connection, work_id, version)
        return {"reference_file_id": ref_id, "work": self.get_work(work_id)}

    def import_official_reference(self, work_id: str, payload: dict):
        """Copy one selected corpus excerpt into the work-owned evidence library."""
        expected = int(payload.get("expected_version", -1))
        item = self.official_references.get(payload.get("record_uid", ""))
        title = str(payload.get("title", "")).strip() or " / ".join(
            value for value in (item.get("character_name"), item.get("story_title")) if value
        ) or item["record_uid"]
        ref_id = new_id("reference")
        content = self.official_references.render_import_excerpt(item)
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            uri, digest = self.repo.atomic_write_text(f"references/{ref_id}.md", content)
            timestamp = now()
            connection.execute(
                "INSERT INTO reference_files VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ref_id,
                    work_id,
                    title,
                    "official_excerpt",
                    uri,
                    digest,
                    f"official-corpus:{item['record_uid']}",
                    "official_reference",
                    1,
                    timestamp,
                    timestamp,
                ),
            )
            self._bump_work(connection, work_id, version)
        return {"reference_file_id": ref_id, "record_uid": item["record_uid"], "work": self.get_work(work_id)}

    def review_scene(self, work_id: str, scene_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute("SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            if not scene["current_revision_id"]:
                raise DomainError("review_blocked", "当前场景还没有已采纳正文。", status=409)
            revision = connection.execute("SELECT * FROM revisions WHERE id=?", (scene["current_revision_id"],)).fetchone()
            text = json.loads(self.repo.read_text(revision["content_uri"])).get("text", "")
            connection.execute("UPDATE review_findings SET status='superseded', resolved_at=? WHERE scene_id=? AND revision_id=? AND status='open'", (now(), scene_id, revision["id"]))
            findings = []
            meta_terms = ["作者", "读者", "观众", "这一话", "第1章", "第一周目", "第二周目", "按设定", "这是故事"]
            for term in meta_terms:
                if term in text:
                    findings.append(("meta_boundary", "blocking", f"正文包含元叙事词“{term}”。", {"term": term}))
            contract = json.loads(scene["contract_json"])
            for term in contract.get("forbidden_reveals", []):
                if str(term).strip() and str(term).strip() in text:
                    findings.append(("forbidden_reveal", "blocking", f"正文出现本场禁止揭示项“{term}”。", {"term": term}))
            cards = connection.execute("SELECT a.current_revision_id FROM artifacts a WHERE a.work_id=? AND a.kind='character_card'", (work_id,)).fetchall()
            card_names = set()
            for card in cards:
                if card["current_revision_id"]:
                    card_revision = connection.execute("SELECT content_uri FROM revisions WHERE id=?", (card["current_revision_id"],)).fetchone()
                    card_content = json.loads(self.repo.read_text(card_revision["content_uri"]))
                    if card_content.get("status", "active") != "archived" and card_content.get("trust_status", "confirmed") == "confirmed":
                        card_names.add(card_content.get("name"))
            speakers = {line.split(":", 1)[0].strip() for line in text.splitlines() if ":" in line}
            missing_cards = sorted(speaker for speaker in speakers if speaker not in {"旁白", "老师"} and speaker not in card_names)
            if missing_cards:
                findings.append(("character_card_missing", "warning", "以下说话者没有可追溯人物卡：" + "、".join(missing_cards), {"speakers": missing_cards}))
            created = []
            for kind, severity, message, evidence in findings:
                finding_id = new_id("finding")
                connection.execute("INSERT INTO review_findings VALUES (?,?,?,?,?,?,?,?,?,?,?)", (finding_id, work_id, scene_id, revision["id"], kind, severity, "open", message, canonical_json(evidence), now(), None))
                created.append({"id": finding_id, "kind": kind, "severity": severity, "message": message, "evidence": evidence})
            gate_id = new_id("gate")
            blockers = [item for item in created if item["severity"] == "blocking"]
            connection.execute("INSERT INTO gates VALUES (?,?,?,?,?,?,?,?)", (gate_id, work_id, "scene.review", "scene", scene_id, "blocked" if blockers else "passed", canonical_json({"revision_id": revision["id"], "finding_ids": [item["id"] for item in created], "blocker_count": len(blockers)}), now()))
            self._bump_work(connection, work_id, version)
        return {"gate_id": gate_id, "findings": created, "work": self.get_work(work_id)}

    def review_release(self, work_id: str, payload: dict):
        """Create a release.review Gate for exactly the current scene revisions."""
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scenes = connection.execute(
                "SELECT s.id,s.current_revision_id FROM scenes s JOIN chapters c ON c.id=s.chapter_id WHERE s.work_id=? ORDER BY c.stable_order_key,s.stable_order_key",
                (work_id,),
            ).fetchall()
            missing = [scene["id"] for scene in scenes if not scene["current_revision_id"]]
            current_revision_ids = [scene["current_revision_id"] for scene in scenes if scene["current_revision_id"]]
            reviewed_revision_ids = set()
            scene_gates = connection.execute(
                "SELECT result_json FROM gates WHERE work_id=? AND kind='scene.review'",
                (work_id,),
            ).fetchall()
            for gate in scene_gates:
                revision_id = json.loads(gate["result_json"]).get("revision_id")
                if revision_id:
                    reviewed_revision_ids.add(revision_id)
            unreviewed = [revision_id for revision_id in current_revision_ids if revision_id not in reviewed_revision_ids]
            blocking_rows = []
            if current_revision_ids:
                placeholders = ",".join("?" for _ in current_revision_ids)
                blocking_rows = connection.execute(
                    f"SELECT id,scene_id,message FROM review_findings WHERE revision_id IN ({placeholders}) AND severity='blocking' AND status='open' ORDER BY created_at",
                    current_revision_ids,
                ).fetchall()
            gate_id = new_id("gate")
            gate_status = "passed" if scenes and not missing and not unreviewed and not blocking_rows else "blocked"
            snapshot = {
                "checked_scene_count": len(scenes),
                "no_scenes": not bool(scenes),
                "scene_revision_ids": current_revision_ids,
                "missing_scene_ids": missing,
                "unreviewed_revision_ids": unreviewed,
                "blocking_finding_ids": [row["id"] for row in blocking_rows],
            }
            connection.execute(
                "INSERT INTO gates VALUES (?,?,?,?,?,?,?,?)",
                (gate_id, work_id, "release.review", "work", work_id, gate_status, canonical_json(snapshot), now()),
            )
            self._bump_work(connection, work_id, version)
        return {"gate_id": gate_id, "status": gate_status, "snapshot": snapshot, "work": self.get_work(work_id)}

    def resolve_review_finding(self, work_id: str, finding_id: str, payload: dict):
        """Record a human decision for a finding; never silently removes audit evidence."""
        expected = int(payload.get("expected_version", -1))
        note = str(payload.get("note", "")).strip()
        if not note:
            raise DomainError("validation_error", "处理审查发现时必须说明理由。", details={"field": "note"})
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            finding = connection.execute(
                "SELECT * FROM review_findings WHERE id=? AND work_id=?", (finding_id, work_id)
            ).fetchone()
            if not finding:
                raise NotFound("review_finding", finding_id)
            if finding["status"] != "open":
                raise DomainError("finding_not_open", "该审查发现已经处理或被新审查替代。", status=409)
            connection.execute(
                "UPDATE review_findings SET status='resolved', resolved_at=? WHERE id=?", (now(), finding_id)
            )
            connection.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                (new_id("decision"), work_id, "review_finding", finding_id, "resolved", note, now()),
            )
            self._bump_work(connection, work_id, version)
        return {"finding_id": finding_id, "work": self.get_work(work_id)}

    def create_chapter(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip() or "第一章"
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            blueprint_artifact = connection.execute(
                "SELECT * FROM artifacts WHERE work_id=? AND kind='story_blueprint'", (work_id,)
            ).fetchone()
            if not blueprint_artifact or not blueprint_artifact["current_revision_id"]:
                raise DomainError("blueprint_required", "请先分析并确认故事方向。", status=409)
            blueprint_revision = connection.execute(
                "SELECT * FROM revisions WHERE id=?", (blueprint_artifact["current_revision_id"],)
            ).fetchone()
            blueprint = json.loads(self.repo.read_text(blueprint_revision["content_uri"]))
            if blueprint.get("status", "accepted") != "accepted":
                raise DomainError("blueprint_unconfirmed", "请先确认故事方向候选，再建立章节。", status=409)
            requested_volume_id = str(payload.get("volume_id", "")).strip()
            if requested_volume_id:
                volume = connection.execute(
                    "SELECT id FROM volumes WHERE id=? AND work_id=?", (requested_volume_id, work_id)
                ).fetchone()
            else:
                volume = connection.execute(
                    "SELECT id FROM volumes WHERE work_id=? ORDER BY stable_order_key LIMIT 1", (work_id,)
                ).fetchone()
            if not volume:
                raise DomainError("volume_required", "请先建立一个卷。", status=409)
            placeholder = connection.execute(
                "SELECT id FROM chapters WHERE work_id=? AND volume_id=? AND status='placeholder' ORDER BY stable_order_key LIMIT 1",
                (work_id, volume["id"]),
            ).fetchone()
            timestamp = now()
            if placeholder:
                chapter_id = placeholder["id"]
                connection.execute(
                    "UPDATE chapters SET title=?,status='planned',version=version+1,updated_at=? WHERE id=?",
                    (title, timestamp, chapter_id),
                )
            else:
                count = connection.execute(
                    "SELECT COUNT(*) FROM chapters WHERE volume_id=?", (volume["id"],)
                ).fetchone()[0]
                chapter_id = new_id("chapter")
                connection.execute(
                    "INSERT INTO chapters (id,work_id,volume_id,stable_order_key,title,status,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (chapter_id, work_id, volume["id"], f"{count + 1:06d}", title, "planned", 1, timestamp, timestamp),
                )
            self._bump_work(connection, work_id, version)
        return {"chapter_id": chapter_id, "work": self.get_work(work_id)}

    def set_writing_target(self, work_id: str, payload: dict):
        """Persist the chapter the Writing surface is currently responsible for."""
        expected = int(payload.get("expected_version", -1))
        chapter_id = str(payload.get("chapter_id", "")).strip()
        anchor_scene_id = str(payload.get("anchor_scene_id", "")).strip() or None
        if not chapter_id:
            raise DomainError("validation_error", "请选择当前要写作的章节。", details={"field": "chapter_id"})
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            chapter = connection.execute(
                "SELECT id,title FROM chapters WHERE id=? AND work_id=?", (chapter_id, work_id)
            ).fetchone()
            if not chapter:
                raise DomainError("invalid_writing_target", "当前写作章节不存在。", status=409)
            if anchor_scene_id:
                scene = connection.execute(
                    "SELECT id FROM scenes WHERE id=? AND chapter_id=? AND work_id=?",
                    (anchor_scene_id, chapter_id, work_id),
                ).fetchone()
                if not scene:
                    raise DomainError("invalid_writing_target", "承接场景不属于当前章节。", status=409)
            artifact = self._artifact(connection, work_id, "writing_target", "work", work_id)
            revision_id = self._add_revision(
                connection,
                artifact,
                {
                    "schema_version": "writing-target/1.0",
                    "surface": "chapter",
                    "chapter_id": chapter_id,
                    "chapter_title": chapter["title"],
                    "anchor_scene_id": anchor_scene_id,
                    "status": "active",
                },
                "user",
                {"workflow": "writing.target.select", "pack": PACK_VERSION, "chapter_id": chapter_id, "anchor_scene_id": anchor_scene_id},
            )
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def create_volume(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip() or "未命名卷"
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            count = connection.execute(
                "SELECT COUNT(*) FROM volumes WHERE work_id=?", (work_id,)
            ).fetchone()[0]
            volume_id = new_id("volume")
            chapter_id = new_id("chapter")
            timestamp = now()
            connection.execute(
                "INSERT INTO volumes VALUES (?,?,?,?,?,?,?,?)",
                (volume_id, work_id, f"{count + 1:06d}", title, "active", 1, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO chapters (id,work_id,volume_id,stable_order_key,title,status,version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (chapter_id, work_id, volume_id, "000001", "第一章", "placeholder", 1, timestamp, timestamp),
            )
            self._bump_work(connection, work_id, version)
        return {"volume_id": volume_id, "chapter_id": chapter_id, "work": self.get_work(work_id)}

    def create_scene(self, work_id: str, chapter_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip() or "未命名场景"
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            chapter = connection.execute("SELECT id FROM chapters WHERE id=? AND work_id=?", (chapter_id, work_id)).fetchone()
            if not chapter:
                raise NotFound("chapter", chapter_id)
            brief_artifact = connection.execute(
                "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='brief'", (work_id,)
            ).fetchone()
            brief_mode = "bond_short"
            if brief_artifact and brief_artifact["current_revision_id"]:
                brief_revision = connection.execute(
                    "SELECT content_uri FROM revisions WHERE id=?", (brief_artifact["current_revision_id"],)
                ).fetchone()
                saved_brief = json.loads(self.repo.read_text(brief_revision["content_uri"]))
                if saved_brief.get("mode") in MODE_SOURCES:
                    brief_mode = saved_brief["mode"]
            writing_mode = str(payload.get("writing_mode") or brief_mode).strip()
            if writing_mode not in MODE_SOURCES:
                raise DomainError("validation_error", "本场起草规则包无效。", details={"field": "writing_mode", "mode": writing_mode})
            contract = {
                "location": str(payload.get("location", "")).strip(),
                "goal": str(payload.get("goal", "")).strip(),
                "known_facts": payload.get("known_facts", []),
                "forbidden_reveals": payload.get("forbidden_reveals", []),
                "stop_boundary": str(payload.get("stop_boundary", "必要事实成立后停止")).strip(),
                # A Work may mix directions. A single provider call may not.
                "writing_mode": writing_mode,
            }
            count = connection.execute("SELECT COUNT(*) FROM scenes WHERE chapter_id=?", (chapter_id,)).fetchone()[0]
            scene_id = new_id("scene")
            timestamp = now()
            connection.execute("INSERT INTO scenes VALUES (?,?,?,?,?,?,?,?,?,?,?)", (scene_id, work_id, chapter_id, f"{count + 1:06d}", title, "planned", 1, None, canonical_json(contract), timestamp, timestamp))
            self._bump_work(connection, work_id, version)
        return {"scene_id": scene_id, "work": self.get_work(work_id)}

    def reorder_structure(self, work_id: str, payload: dict):
        """Persist chapter order and scene placement without changing scene identity.

        Structure is part of a release's meaning, so the operation is versioned
        at the Work level. It deliberately leaves SceneContract, manuscript
        Revisions and Proposal contents alone: only parent chapter and order are
        updated. A release review snapshot becomes stale naturally because it
        records scene revision IDs in structural order.
        """
        expected = int(payload.get("expected_version", -1))
        chapter_ids = payload.get("chapter_ids")
        placements = payload.get("scene_placements")
        if not isinstance(chapter_ids, list) or not isinstance(placements, list):
            raise DomainError(
                "validation_error",
                "章节顺序和场景安排必须以数组提交。",
                details={"fields": ["chapter_ids", "scene_placements"]},
            )
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            chapters = connection.execute(
                "SELECT id,stable_order_key FROM chapters WHERE work_id=? ORDER BY stable_order_key",
                (work_id,),
            ).fetchall()
            scenes = connection.execute(
                "SELECT id,chapter_id,stable_order_key FROM scenes WHERE work_id=?",
                (work_id,),
            ).fetchall()
            current_chapter_ids = {chapter["id"] for chapter in chapters}
            requested_chapter_ids = [str(value).strip() for value in chapter_ids]
            if len(requested_chapter_ids) != len(chapters) or set(requested_chapter_ids) != current_chapter_ids:
                raise DomainError(
                    "invalid_structure_order",
                    "章节顺序必须恰好包含当前作品的全部章节。",
                    details={"expected_chapter_ids": sorted(current_chapter_ids)},
                )
            if len(set(requested_chapter_ids)) != len(requested_chapter_ids):
                raise DomainError("invalid_structure_order", "章节顺序不能包含重复章节。")

            requested_scene_ids = []
            grouped = {chapter_id: [] for chapter_id in requested_chapter_ids}
            for index, placement in enumerate(placements):
                if not isinstance(placement, dict):
                    raise DomainError("validation_error", "场景安排条目无效。", details={"index": index})
                scene_id = str(placement.get("scene_id", "")).strip()
                chapter_id = str(placement.get("chapter_id", "")).strip()
                if not scene_id or not chapter_id:
                    raise DomainError("validation_error", "场景安排需要场景和目标章节。", details={"index": index})
                if chapter_id not in grouped:
                    raise DomainError(
                        "invalid_structure_order",
                        "场景不能移动到当前作品以外的章节。",
                        details={"index": index, "chapter_id": chapter_id},
                    )
                requested_scene_ids.append(scene_id)
                grouped[chapter_id].append(scene_id)

            current_scene_ids = {scene["id"] for scene in scenes}
            if len(requested_scene_ids) != len(scenes) or set(requested_scene_ids) != current_scene_ids:
                raise DomainError(
                    "invalid_structure_order",
                    "场景安排必须恰好包含当前作品的全部场景。",
                    details={"expected_scene_ids": sorted(current_scene_ids)},
                )
            if len(set(requested_scene_ids)) != len(requested_scene_ids):
                raise DomainError("invalid_structure_order", "场景安排不能包含重复场景。")

            current_chapter_order = [chapter["id"] for chapter in chapters]
            current_scene_state = {
                scene["id"]: (scene["chapter_id"], scene["stable_order_key"])
                for scene in scenes
            }
            changed = current_chapter_order != requested_chapter_ids
            for chapter_id, scene_ids in grouped.items():
                for index, scene_id in enumerate(scene_ids, start=1):
                    if current_scene_state[scene_id] != (chapter_id, f"{index:06d}"):
                        changed = True

            if not changed:
                return {"changed": False, "work": self.get_work(work_id)}

            timestamp = now()
            for index, chapter_id in enumerate(requested_chapter_ids, start=1):
                connection.execute(
                    "UPDATE chapters SET stable_order_key=?, version=version+1, updated_at=? WHERE id=?",
                    (f"{index:06d}", timestamp, chapter_id),
                )
            for chapter_id, scene_ids in grouped.items():
                for index, scene_id in enumerate(scene_ids, start=1):
                    connection.execute(
                        "UPDATE scenes SET chapter_id=?, stable_order_key=?, version=version+1, updated_at=? WHERE id=?",
                        (chapter_id, f"{index:06d}", timestamp, scene_id),
                    )
            connection.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                (
                    new_id("decision"),
                    work_id,
                    "structure",
                    work_id,
                    "reordered",
                    "用户调整了章节或场景顺序；正文修订保持不变，需重新运行全篇审查。",
                    timestamp,
                ),
            )
            self._bump_work(connection, work_id, version)
        return {"changed": True, "work": self.get_work(work_id)}

    @staticmethod
    def _contract_lines(payload: dict, field: str) -> list[str]:
        values = payload.get(field, [])
        if not isinstance(values, list):
            raise DomainError("validation_error", "场景契约中的列表必须是数组。", details={"field": field})
        return [item for item in (str(value).strip() for value in values) if item]

    def update_scene_contract(self, work_id: str, scene_id: str, payload: dict):
        """Revise one Scene's generative boundary without editing manuscript text."""
        expected = int(payload.get("expected_version", -1))
        title = str(payload.get("title", "")).strip()
        location = str(payload.get("location", "")).strip()
        goal = str(payload.get("goal", "")).strip()
        known_facts = self._contract_lines(payload, "known_facts")
        forbidden_reveals = self._contract_lines(payload, "forbidden_reveals")
        stop_boundary = str(payload.get("stop_boundary", "")).strip()
        requested_mode = str(payload.get("writing_mode", "")).strip()
        if not title:
            raise DomainError("validation_error", "场景标题不能为空。", details={"field": "title"})
        if not goal:
            raise DomainError("validation_error", "场景目标不能为空。", details={"field": "goal"})
        if not stop_boundary:
            raise DomainError("validation_error", "停止边界不能为空。", details={"field": "stop_boundary"})
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute("SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            contract = json.loads(scene["contract_json"])
            writing_mode = requested_mode or contract.get("writing_mode")
            if not writing_mode:
                brief_artifact = connection.execute(
                    "SELECT current_revision_id FROM artifacts WHERE work_id=? AND kind='brief'", (work_id,)
                ).fetchone()
                if brief_artifact and brief_artifact["current_revision_id"]:
                    brief_revision = connection.execute(
                        "SELECT content_uri FROM revisions WHERE id=?", (brief_artifact["current_revision_id"],)
                    ).fetchone()
                    writing_mode = json.loads(self.repo.read_text(brief_revision["content_uri"])).get("mode")
            if writing_mode not in MODE_SOURCES:
                raise DomainError("validation_error", "本场起草规则包无效。", details={"field": "writing_mode", "mode": writing_mode})
            contract.update({
                "location": location,
                "goal": goal,
                "known_facts": known_facts,
                "forbidden_reveals": forbidden_reveals,
                "stop_boundary": stop_boundary,
                "writing_mode": writing_mode,
            })
            timestamp = now()
            pending = connection.execute(
                "SELECT id FROM proposals WHERE work_id=? AND scope_type='scene' AND scope_id=? AND status='pending'",
                (work_id, scene_id),
            ).fetchall()
            if pending:
                connection.execute(
                    "UPDATE proposals SET status='superseded', decided_at=? WHERE work_id=? AND scope_type='scene' AND scope_id=? AND status='pending'",
                    (timestamp, work_id, scene_id),
                )
                for proposal in pending:
                    connection.execute(
                        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                        (new_id("decision"), work_id, "proposal", proposal["id"], "superseded", "场景契约已更新，候选不再适用。", timestamp),
                    )
            connection.execute(
                "UPDATE scenes SET title=?, contract_json=?, version=version+1, updated_at=? WHERE id=?",
                (title, canonical_json(contract), timestamp, scene_id),
            )
            self._bump_work(connection, work_id, version)
        return {"scene_id": scene_id, "superseded_proposal_ids": [row["id"] for row in pending], "work": self.get_work(work_id)}

    @staticmethod
    def _context_selection_ids(payload: dict, field: str) -> list[str]:
        values = payload.get(field, [])
        if not isinstance(values, list):
            raise DomainError("validation_error", "场景上下文选择必须是数组。", details={"field": field})
        result = []
        seen = set()
        for value in values:
            item_id = str(value).strip()
            if not item_id:
                raise DomainError("validation_error", "场景上下文不能包含空白 ID。", details={"field": field})
            if item_id in seen:
                raise DomainError("validation_error", "场景上下文不能重复选择同一条资料。", details={"field": field, "id": item_id})
            seen.add(item_id)
            result.append(item_id)
        return result

    def configure_scene_context(self, work_id: str, scene_id: str, payload: dict):
        """Persist the exact work-owned inputs a scene is allowed to assemble.

        The selection does not change manuscript text or a pending Proposal. It
        only constrains future context snapshots and therefore stays auditable
        in the Scene contract itself.
        """
        expected = int(payload.get("expected_version", -1))
        character_card_ids = self._context_selection_ids(payload, "character_card_ids")
        world_item_ids = self._context_selection_ids(payload, "world_item_ids")
        reference_file_ids = self._context_selection_ids(payload, "reference_file_ids")
        if not character_card_ids:
            raise DomainError(
                "validation_error",
                "本场上下文至少需要选择一张已确认的人物卡。",
                details={"field": "character_card_ids"},
            )
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute(
                "SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)
            ).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)

            cards = {}
            for artifact in connection.execute(
                "SELECT * FROM artifacts WHERE work_id=? AND kind='character_card'", (work_id,)
            ).fetchall():
                if not artifact["current_revision_id"]:
                    continue
                revision = connection.execute(
                    "SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)
                ).fetchone()
                cards[artifact["scope_id"]] = json.loads(self.repo.read_text(revision["content_uri"]))
            for card_id in character_card_ids:
                card = cards.get(card_id)
                if not card:
                    raise DomainError("invalid_context_selection", "选择的人物卡不属于当前作品。", details={"field": "character_card_ids", "id": card_id})
                if card.get("status", "active") != "active" or card.get("trust_status") != "confirmed":
                    raise DomainError(
                        "invalid_context_selection",
                        "本场只能选择已确认且未归档的人物卡。",
                        details={"field": "character_card_ids", "id": card_id},
                    )

            world_items = {}
            world_artifact = connection.execute(
                "SELECT * FROM artifacts WHERE work_id=? AND kind='world_bible'", (work_id,)
            ).fetchone()
            if world_artifact and world_artifact["current_revision_id"]:
                world_revision = connection.execute(
                    "SELECT * FROM revisions WHERE id=?", (world_artifact["current_revision_id"],)
                ).fetchone()
                world = json.loads(self.repo.read_text(world_revision["content_uri"]))
                for collection in ("entities", "rules", "timeline"):
                    for item in world.get(collection, []):
                        world_items[item.get("id")] = item
            for item_id in world_item_ids:
                item = world_items.get(item_id)
                if not item:
                    raise DomainError("invalid_context_selection", "选择的世界观条目不属于当前作品。", details={"field": "world_item_ids", "id": item_id})
                if item.get("status", "active") != "active" or item.get("confidence_status") != "confirmed":
                    raise DomainError(
                        "invalid_context_selection",
                        "本场只能选择已确认且未归档的世界观条目。",
                        details={"field": "world_item_ids", "id": item_id},
                    )

            reference_rows = connection.execute(
                "SELECT id FROM reference_files WHERE work_id=?", (work_id,)
            ).fetchall()
            reference_ids = {row["id"] for row in reference_rows}
            for reference_id in reference_file_ids:
                if reference_id not in reference_ids:
                    raise DomainError("invalid_context_selection", "选择的证据资料不属于当前作品。", details={"field": "reference_file_ids", "id": reference_id})

            contract = json.loads(scene["contract_json"])
            contract["context_selection"] = {
                "mode": "explicit",
                "character_card_ids": character_card_ids,
                "world_item_ids": world_item_ids,
                "reference_file_ids": reference_file_ids,
            }
            connection.execute(
                "UPDATE scenes SET contract_json=?, version=version+1, updated_at=? WHERE id=?",
                (canonical_json(contract), now(), scene_id),
            )
            self._bump_work(connection, work_id, version)
        return {
            "scene_id": scene_id,
            "context_selection": contract["context_selection"],
            "work": self.get_work(work_id),
        }

    def assemble_context(self, work_id: str, scene_id: str):
        with self.repo.connect() as connection:
            scene = connection.execute("SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            scene_contract = json.loads(scene["contract_json"])
            selection = scene_contract.get("context_selection") or {"mode": "legacy"}
            explicit_selection = selection.get("mode") == "explicit"
            artifacts = connection.execute("SELECT * FROM artifacts WHERE work_id=? AND kind IN ('brief','story_blueprint','work_canon','world_bible')", (work_id,)).fetchall()
            values = {}
            revision_refs = []
            for artifact in artifacts:
                if artifact["current_revision_id"]:
                    revision = connection.execute("SELECT * FROM revisions WHERE id=?", (artifact["current_revision_id"],)).fetchone()
                    values[artifact["kind"]] = json.loads(self.repo.read_text(revision["content_uri"]))
                    revision_refs.append(revision["id"])
            if "brief" not in values or "story_blueprint" not in values:
                raise DomainError("context_incomplete", "请先保存写作想法并建立故事方向。", status=409)
            if values["brief"].get("status", "confirmed") != "confirmed" or values["story_blueprint"].get("status", "accepted") != "accepted":
                raise DomainError("context_incomplete", "请先确认故事方向候选，再装配场景上下文。", status=409)
            scene_mode = scene_contract.get("writing_mode") or values["brief"].get("mode")
            if scene_mode not in MODE_SOURCES:
                raise DomainError(
                    "context_incomplete",
                    "本场尚未确定可用的起草规则包。",
                    status=409,
                    details={"field": "writing_mode", "mode": scene_mode},
                )
            reference_rows = connection.execute(
                "SELECT id,title,kind,content_uri,content_hash,source_label,trust_status,version FROM reference_files WHERE work_id=? ORDER BY updated_at DESC",
                (work_id,),
            ).fetchall()
            selected_reference_ids = set(selection.get("reference_file_ids", [])) if explicit_selection else None
            reference_files = []
            for reference in reference_rows:
                if selected_reference_ids is not None and reference["id"] not in selected_reference_ids:
                    continue
                reference_files.append({
                    "id": reference["id"],
                    "title": reference["title"],
                    "kind": reference["kind"],
                    "source_label": reference["source_label"],
                    "trust_status": reference["trust_status"],
                    "version": reference["version"],
                    "content_hash": reference["content_hash"],
                    "content": self.repo.read_text(reference["content_uri"]),
                })
            brief_characters = values["brief"].get("characters", [])
            card_rows = connection.execute("SELECT * FROM artifacts WHERE work_id=? AND kind='character_card'", (work_id,)).fetchall()
            cards = {}
            cards_by_name = {}
            unverified_cards = {}
            for card_artifact in card_rows:
                if card_artifact["current_revision_id"]:
                    card_revision = connection.execute("SELECT * FROM revisions WHERE id=?", (card_artifact["current_revision_id"],)).fetchone()
                    card_content = json.loads(self.repo.read_text(card_revision["content_uri"]))
                    if card_content.get("status", "active") != "archived":
                        card_name = card_content.get("name")
                        if card_content.get("trust_status", "confirmed") == "confirmed":
                            card = {"revision_id": card_revision["id"], "content": card_content}
                            cards[card_artifact["scope_id"]] = card
                            cards_by_name[card_name] = card
                        else:
                            unverified_cards[card_name] = card_content.get("trust_status", "open")
            selected_card_ids = selection.get("character_card_ids", []) if explicit_selection else []
            selected_cards = []
            if explicit_selection:
                for card_id in selected_card_ids:
                    selected = cards.get(card_id)
                    if selected:
                        selected_cards.append((card_id, selected))
            else:
                selected_cards = [
                    (card_id, card)
                    for card_id, card in cards.items()
                    if card["content"].get("name") in brief_characters
                ]
            missing_cards = []
            if explicit_selection:
                missing_cards = [card_id for card_id in selected_card_ids if card_id not in cards]
            else:
                missing_cards = [name for name in brief_characters if name not in cards_by_name]
            runtime_cards = []
            for _, card in selected_cards:
                runtime_cards.append({
                    "name": card["content"].get("name"),
                    "source_revision_id": card["revision_id"],
                    "voice_anchors": card["content"].get("voice_anchors", []),
                    "knowledge_boundary": card["content"].get("knowledge_boundary", ""),
                    "ooc_constraints": card["content"].get("ooc_constraints", []),
                    "source_refs": card["content"].get("source_refs", []),
                })
                revision_refs.append(card["revision_id"])
            work_canon = values.get("work_canon")
            if work_canon:
                # Draft, inferred, conflicted, and archived memories are visible in the library,
                # but may not be asserted as facts in a new scene prompt.
                work_canon = {
                    **work_canon,
                    "facts": [
                        fact for fact in work_canon.get("facts", [])
                        if fact.get("status", "active") != "archived" and fact.get("confidence_status") == "confirmed"
                    ],
                }
            world_bible = values.get("world_bible")
            unverified_world_items = []
            if world_bible:
                for collection in ("entities", "rules", "timeline"):
                    for item in world_bible.get(collection, []):
                        if item.get("status", "active") != "archived" and item.get("confidence_status") != "confirmed":
                            unverified_world_items.append({
                                "id": item.get("id"),
                                "kind": collection,
                                "label": item.get("name") or item.get("text"),
                                "confidence_status": item.get("confidence_status", "open"),
                            })
                # Draft and archived world knowledge remains in immutable history, but is not
                # asserted as established setting in a new scene prompt.
                world_bible = {
                    **world_bible,
                    "entities": [
                        item for item in world_bible.get("entities", [])
                        if item.get("status", "active") != "archived" and item.get("confidence_status") == "confirmed"
                    ],
                    "rules": [
                        item for item in world_bible.get("rules", [])
                        if item.get("status", "active") != "archived" and item.get("confidence_status") == "confirmed"
                    ],
                    "timeline": [
                        item for item in world_bible.get("timeline", [])
                        if item.get("status", "active") != "archived" and item.get("confidence_status") == "confirmed"
                    ],
                }
                if explicit_selection:
                    selected_world_ids = set(selection.get("world_item_ids", []))
                    world_bible = {
                        **world_bible,
                        "entities": [item for item in world_bible["entities"] if item.get("id") in selected_world_ids],
                        "rules": [item for item in world_bible["rules"] if item.get("id") in selected_world_ids],
                        "timeline": [item for item in world_bible["timeline"] if item.get("id") in selected_world_ids],
                    }
            context = {
                "scene_id": scene_id,
                "scene_contract": scene_contract,
                "context_selection": selection,
                "brief": values["brief"],
                "story_blueprint": values["story_blueprint"],
                "work_canon": work_canon,
                "world_bible": world_bible,
                "reference_files": reference_files,
                "reference_file_refs": [
                    f"reference:{item['id']}@v{item['version']}:{item['content_hash']}" for item in reference_files
                ],
                "rules": {
                    "pack_version": PACK_VERSION,
                    "common": ["agents/writer.md", "knowledge/写作内核.md", "knowledge/人味对话机制.md"],
                    "mode_key": scene_mode,
                    "mode": MODE_SOURCES[scene_mode],
                    "sensei": "knowledge/老师在场规则.md" if values["brief"].get("has_sensei") else None,
                    "evidence_contract": "资料文件是可追溯证据，不会自动升级为 WorkCanon；只有已确认且未归档的 WorkCanon 条目可以被表述为确定事实。",
                },
                "runtime_character_cards": runtime_cards,
                "source_revision_ids": revision_refs + [item["content_hash"] for item in reference_files],
                "readiness": {
                    "fake_provider": "ready",
                    "real_ba_writing": "blocked" if missing_cards or not runtime_cards else "ready_for_provider",
                    "missing_runtime_character_cards": missing_cards,
                    "unverified_character_cards": {
                        key: unverified_cards[key]
                        for key in missing_cards if key in unverified_cards
                    },
                    "unverified_world_items": unverified_world_items,
                    "reason": (
                        "本场没有选择已确认的人物卡。" if explicit_selection and not runtime_cards
                        else "缺少经来源校验的运行时人物卡：" + "、".join(missing_cards) if missing_cards
                        else "人物卡已就绪；仍需配置真实模型 Provider 才会执行模型调用。"
                    ),
                },
            }
            return context

    def generate_scene_candidate(self, work_id: str, scene_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        context = self.assemble_context(work_id, scene_id)
        context_digest = sha256_text(canonical_json(context))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute("SELECT * FROM scenes WHERE id=?", (scene_id,)).fetchone()
            run = connection.execute("SELECT * FROM production_runs WHERE work_id=? AND kind='creation' ORDER BY created_at LIMIT 1", (work_id,)).fetchone()
            work_item_id = new_id("item")
            timestamp = now()
            connection.execute(
                "INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (work_item_id, run["id"], "scene.draft.generate", "scene", scene_id, "running", canonical_json(context["source_revision_ids"]), "[]", canonical_json({"proposal_only": True}), 1, None, timestamp, timestamp),
            )
            attempt_id = new_id("attempt")
            connection.execute("INSERT INTO job_attempts VALUES (?,?,?,?,?,?,?,?,?,?)", (attempt_id, work_item_id, 1, self.provider.kind, context_digest, "started", None, None, timestamp, None))
            candidate = self.provider.generate_scene(context)
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(f"artifacts/proposals/{proposal_id}.txt", candidate)
            base_text = ""
            if scene["current_revision_id"]:
                base_revision = connection.execute("SELECT content_uri FROM revisions WHERE id=?", (scene["current_revision_id"],)).fetchone()
                base_content = json.loads(self.repo.read_text(base_revision["content_uri"]))
                base_text = base_content.get("text", "")
            diff = list(difflib.unified_diff(base_text.splitlines(), candidate.splitlines(), fromfile="当前稿件", tofile="模拟候选", lineterm=""))
            provider_json = canonical_json(self.provider.descriptor())
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, work_id, "scene_script", "scene", scene_id, scene["current_revision_id"], candidate_uri, candidate_hash, canonical_json(diff), canonical_json(context["source_revision_ids"]), "medium", "pending", provider_json, timestamp, None),
            )
            connection.execute("UPDATE job_attempts SET status='succeeded', output_ref=?, finished_at=? WHERE id=?", (proposal_id, now(), attempt_id))
            connection.execute("UPDATE work_items SET status='waiting_user', output_refs_json=?, updated_at=? WHERE id=?", (canonical_json([proposal_id]), now(), work_item_id))
            connection.execute("UPDATE production_runs SET status='waiting_user', updated_at=? WHERE id=?", (now(), run["id"]))
            self._bump_work(connection, work_id, version)
        return {"proposal_id": proposal_id, "simulation": True, "work": self.get_work(work_id)}

    def run_scene_agent(self, work_id: str, scene_id: str, payload: dict):
        """Run one constrained ba-writing Agent turn and return a Proposal, never a direct edit."""
        expected = int(payload.get("expected_version", -1))
        instruction = str(payload.get("instruction", "")).strip()
        if not instruction:
            raise DomainError("validation_error", "请说明希望 Agent 对当前场景做什么。", details={"field": "instruction"})
        context = self.assemble_context(work_id, scene_id)
        if context["readiness"]["real_ba_writing"] != "ready_for_provider":
            raise DomainError(
                "agent_blocked",
                "BA 写作 Agent 缺少本场运行时人物卡，不能降级生成。",
                status=409,
                details={"missing_runtime_character_cards": context["readiness"]["missing_runtime_character_cards"]},
            )
        scene_contract = context["scene_contract"]
        policy = {
            "workflow": "scene.draft.generate",
            "pack_version": PACK_VERSION,
            "mode_source": context["rules"]["mode"],
            "tool_allowlist": ["assemble_scene_context", "validate_runtime_character_cards", "generate_single_proposal"],
            "tool_denied": ["read_previous_script", "write_scene_revision", "mutate_work_canon", "mutate_character_card", "internet_search"],
            "write_policy": "one_candidate_zero_edit_proposal_only",
            "skill_contract": {
                "single_mode": True,
                "runtime_cards_only": True,
                "has_sensei": bool(context["brief"].get("has_sensei")),
                "output_mode": "official_script",
            },
        }
        snapshot = {
            "instruction": instruction,
            "scene_id": scene_id,
            "scene_contract": scene_contract,
            "brief": context["brief"],
            "work_canon": context["work_canon"],
            "world_bible": context["world_bible"],
            "runtime_character_cards": context["runtime_character_cards"],
            "reference_files": context["reference_files"],
            "reference_file_refs": context["reference_file_refs"],
            "source_revision_ids": context["source_revision_ids"],
            "rules": context["rules"],
            "policy": policy,
        }
        snapshot_text = json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n"
        run_id = new_id("agent")
        snapshot_uri, digest = self.repo.atomic_write_text(f"agent-runs/{run_id}/input.json", snapshot_text)
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute("SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            if scene["current_revision_id"]:
                raise DomainError(
                    "agent_scope_blocked",
                    "首次 BA 场景 Agent 只处理尚无正文的场景；已有正文请走后续的受控复写 Proposal 工作流。",
                    status=409,
                )
            if connection.execute("SELECT 1 FROM proposals WHERE work_id=? AND scope_id=? AND status='pending'", (work_id, scene_id)).fetchone():
                raise DomainError("agent_waiting_user", "当前场景已有待决定的 Proposal，请先采纳或退回。", status=409)
            timestamp = now()
            connection.execute(
                "INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, work_id, "scene", scene_id, instruction, "running", canonical_json(policy), snapshot_uri, digest, None, None, timestamp, None),
            )
            tool_call_id = new_id("tool")
            connection.execute(
                "INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (tool_call_id, run_id, 1, "assemble_scene_context", "succeeded", digest, snapshot_uri, None, timestamp, now()),
            )
            card_call_id = new_id("tool")
            connection.execute(
                "INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (card_call_id, run_id, 2, "validate_runtime_character_cards", "succeeded", sha256_text(canonical_json(context["runtime_character_cards"])), None, None, timestamp, now()),
            )
            work_item_id = new_id("item")
            run = connection.execute("SELECT * FROM production_runs WHERE work_id=? AND kind='creation' ORDER BY created_at LIMIT 1", (work_id,)).fetchone()
            connection.execute(
                "INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (work_item_id, run["id"], "agent.scene.draft.generate", "scene", scene_id, "running", canonical_json(context["source_revision_ids"]), "[]", canonical_json({"proposal_only": True, "agent_run_id": run_id}), 1, None, timestamp, timestamp),
            )
            attempt_id = new_id("attempt")
            connection.execute("INSERT INTO job_attempts VALUES (?,?,?,?,?,?,?,?,?,?)", (attempt_id, work_item_id, 1, self.provider.kind, digest, "started", None, None, timestamp, None))
            try:
                candidate = self.provider.generate_scene(context)
            except Exception as exc:
                error = {"code": "provider_failed", "type": type(exc).__name__}
                connection.execute("UPDATE agent_runs SET status='failed', failure_json=?, finished_at=? WHERE id=?", (canonical_json(error), now(), run_id))
                connection.execute("UPDATE job_attempts SET status='failed', error_code='provider_failed', finished_at=? WHERE id=?", (now(), attempt_id))
                connection.execute("UPDATE work_items SET status='failed', error_json=?, updated_at=? WHERE id=?", (canonical_json(error), now(), work_item_id))
                self._bump_work(connection, work_id, version)
                raise DomainError("agent_failed", "写作 Agent 未能完成本次运行。", status=502, details=error) from exc
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(f"artifacts/proposals/{proposal_id}.txt", candidate)
            diff = list(difflib.unified_diff([], candidate.splitlines(), fromfile="空白正文", tofile="Agent 候选", lineterm=""))
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, work_id, "scene_script", "scene", scene_id, None, candidate_uri, candidate_hash, canonical_json(diff), canonical_json(context["source_revision_ids"]), "medium", "pending", canonical_json(self.provider.descriptor()), now(), None),
            )
            proposal_call_id = new_id("tool")
            connection.execute(
                "INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)",
                (proposal_call_id, run_id, 3, "generate_single_proposal", "succeeded", digest, proposal_id, None, timestamp, now()),
            )
            connection.execute("UPDATE agent_runs SET status='waiting_user', proposal_id=?, finished_at=? WHERE id=?", (proposal_id, now(), run_id))
            connection.execute("UPDATE job_attempts SET status='succeeded', output_ref=?, finished_at=? WHERE id=?", (proposal_id, now(), attempt_id))
            connection.execute("UPDATE work_items SET status='waiting_user', output_refs_json=?, updated_at=? WHERE id=?", (canonical_json([proposal_id]), now(), work_item_id))
            connection.execute("UPDATE production_runs SET status='waiting_user', updated_at=? WHERE id=?", (now(), run["id"]))
            self._bump_work(connection, work_id, version)
        return {"agent_run_id": run_id, "proposal_id": proposal_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def run_scene_rewrite_agent(self, work_id: str, scene_id: str, payload: dict):
        """Create a full-scene rewrite Proposal from a pinned accepted revision.

        The provider receives the accepted manuscript as a fixed input and can
        only return a candidate. Acceptance is still the only way to create a
        new ScriptRevision.
        """
        expected = int(payload.get("expected_version", -1))
        instruction = str(payload.get("instruction", "")).strip()
        if not instruction:
            raise DomainError("validation_error", "请说明希望如何调整当前正文。", details={"field": "instruction"})
        context = self.assemble_context(work_id, scene_id)
        if context["readiness"]["real_ba_writing"] != "ready_for_provider":
            raise DomainError(
                "agent_blocked",
                "BA 写作 Agent 缺少本场运行时人物卡，不能降级改写正文。",
                status=409,
                details={"missing_runtime_character_cards": context["readiness"]["missing_runtime_character_cards"]},
            )

        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute("SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            if not scene["current_revision_id"]:
                raise DomainError("rewrite_requires_manuscript", "当前场景还没有已采纳正文，请先生成第一份候选。", status=409)
            if connection.execute("SELECT 1 FROM proposals WHERE work_id=? AND scope_id=? AND status='pending'", (work_id, scene_id)).fetchone():
                raise DomainError("agent_waiting_user", "当前场景已有待决定的 Proposal，请先采纳或退回。", status=409)

            base_revision = connection.execute("SELECT * FROM revisions WHERE id=?", (scene["current_revision_id"],)).fetchone()
            base_text = json.loads(self.repo.read_text(base_revision["content_uri"])).get("text", "")
            policy = {
                "workflow": "scene.draft.rewrite",
                "pack_version": PACK_VERSION,
                "mode_source": context["rules"]["mode"],
                "tool_allowlist": ["assemble_scene_context", "validate_runtime_character_cards", "read_pinned_scene_revision", "generate_single_proposal"],
                "tool_denied": ["write_scene_revision", "mutate_work_canon", "mutate_character_card", "internet_search"],
                "write_policy": "one_full_scene_candidate_zero_edit_proposal_only",
                "skill_contract": {"single_mode": True, "runtime_cards_only": True, "base_revision_pinned": True, "output_mode": "official_script"},
            }
            snapshot = {
                "instruction": instruction,
                "scene_id": scene_id,
                "base_revision_id": base_revision["id"],
                "base_text": base_text,
                "scene_contract": context["scene_contract"],
                "brief": context["brief"],
                "work_canon": context["work_canon"],
                "world_bible": context["world_bible"],
                "runtime_character_cards": context["runtime_character_cards"],
                "reference_files": context["reference_files"],
                "reference_file_refs": context["reference_file_refs"],
                "source_revision_ids": context["source_revision_ids"],
                "rules": context["rules"],
                "policy": policy,
            }
            run_id = new_id("agent")
            snapshot_uri, digest = self.repo.atomic_write_text(f"agent-runs/{run_id}/input.json", json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n")
            timestamp = now()
            connection.execute("INSERT INTO agent_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, work_id, "scene", scene_id, instruction, "running", canonical_json(policy), snapshot_uri, digest, None, None, timestamp, None))
            context_call_id = new_id("tool")
            connection.execute("INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)", (context_call_id, run_id, 1, "assemble_scene_context", "succeeded", digest, snapshot_uri, None, timestamp, now()))
            card_call_id = new_id("tool")
            connection.execute("INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)", (card_call_id, run_id, 2, "validate_runtime_character_cards", "succeeded", sha256_text(canonical_json(context["runtime_character_cards"])), None, None, timestamp, now()))
            revision_call_id = new_id("tool")
            connection.execute("INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)", (revision_call_id, run_id, 3, "read_pinned_scene_revision", "succeeded", base_revision["content_hash"], base_revision["id"], None, timestamp, now()))
            run = connection.execute("SELECT * FROM production_runs WHERE work_id=? AND kind='creation' ORDER BY created_at LIMIT 1", (work_id,)).fetchone()
            work_item_id = new_id("item")
            input_refs = [*context["source_revision_ids"], base_revision["id"]]
            connection.execute("INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (work_item_id, run["id"], "agent.scene.draft.rewrite", "scene", scene_id, "running", canonical_json(input_refs), "[]", canonical_json({"proposal_only": True, "agent_run_id": run_id, "base_revision_id": base_revision["id"]}), 1, None, timestamp, timestamp))
            attempt_id = new_id("attempt")
            connection.execute("INSERT INTO job_attempts VALUES (?,?,?,?,?,?,?,?,?,?)", (attempt_id, work_item_id, 1, self.provider.kind, digest, "started", None, None, timestamp, None))
            try:
                candidate = self.provider.rewrite_scene(context, base_text, instruction)
            except Exception as exc:
                error = {"code": "provider_failed", "type": type(exc).__name__}
                connection.execute("UPDATE agent_runs SET status='failed', failure_json=?, finished_at=? WHERE id=?", (canonical_json(error), now(), run_id))
                connection.execute("UPDATE job_attempts SET status='failed', error_code='provider_failed', finished_at=? WHERE id=?", (now(), attempt_id))
                connection.execute("UPDATE work_items SET status='failed', error_json=?, updated_at=? WHERE id=?", (canonical_json(error), now(), work_item_id))
                self._bump_work(connection, work_id, version)
                raise DomainError("agent_failed", "写作 Agent 未能完成本次改写。", status=502, details=error) from exc
            proposal_id = new_id("proposal")
            candidate_uri, candidate_hash = self.repo.atomic_write_text(f"artifacts/proposals/{proposal_id}.txt", candidate)
            diff = list(difflib.unified_diff(base_text.splitlines(), candidate.splitlines(), fromfile="当前正文", tofile="Agent 改写候选", lineterm=""))
            evidence = [*context["source_revision_ids"], base_revision["id"]]
            connection.execute(
                "INSERT INTO proposals (id,work_id,kind,scope_type,scope_id,base_revision_id,candidate_uri,candidate_hash,diff_json,evidence_json,risk,status,provider_json,created_at,decided_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (proposal_id, work_id, "scene_script", "scene", scene_id, base_revision["id"], candidate_uri, candidate_hash, canonical_json(diff), canonical_json(evidence), "medium", "pending", canonical_json(self.provider.descriptor()), now(), None),
            )
            proposal_call_id = new_id("tool")
            connection.execute("INSERT INTO agent_tool_calls VALUES (?,?,?,?,?,?,?,?,?,?)", (proposal_call_id, run_id, 4, "generate_single_proposal", "succeeded", digest, proposal_id, None, timestamp, now()))
            connection.execute("UPDATE agent_runs SET status='waiting_user', proposal_id=?, finished_at=? WHERE id=?", (proposal_id, now(), run_id))
            connection.execute("UPDATE job_attempts SET status='succeeded', output_ref=?, finished_at=? WHERE id=?", (proposal_id, now(), attempt_id))
            connection.execute("UPDATE work_items SET status='waiting_user', output_refs_json=?, updated_at=? WHERE id=?", (canonical_json([proposal_id]), now(), work_item_id))
            connection.execute("UPDATE production_runs SET status='waiting_user', updated_at=? WHERE id=?", (now(), run["id"]))
            self._bump_work(connection, work_id, version)
        return {"agent_run_id": run_id, "proposal_id": proposal_id, "simulation": self.provider.is_simulation, "work": self.get_work(work_id)}

    def accept_proposal(self, work_id: str, proposal_id: str, payload: dict):
        with self.repo.connect() as connection:
            proposal = connection.execute(
                "SELECT kind FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)
            ).fetchone()
        if not proposal:
            raise NotFound("proposal", proposal_id)
        if proposal["kind"] == "brief_blueprint":
            return self._accept_brief_blueprint_proposal(work_id, proposal_id, payload)
        if proposal["kind"] == "chapter_plan":
            return self._accept_chapter_plan_proposal(work_id, proposal_id, payload)
        if proposal["kind"] in {"character_card", "world_entity"}:
            return self._accept_knowledge_proposal(work_id, proposal_id, payload)

        expected = int(payload.get("expected_version", -1))
        selected_text = payload.get("text")
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            proposal = connection.execute("SELECT * FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)).fetchone()
            if proposal["status"] != "pending":
                raise DomainError("proposal_not_pending", "候选方案已经处理。", status=409)
            scene = connection.execute("SELECT * FROM scenes WHERE id=?", (proposal["scope_id"],)).fetchone()
            if not scene:
                raise NotFound("scene", proposal["scope_id"])
            if scene["current_revision_id"] != proposal["base_revision_id"]:
                connection.execute("UPDATE proposals SET status='superseded', decided_at=? WHERE id=?", (now(), proposal_id))
                raise DomainError("proposal_superseded", "当前正文已经变化，请重新生成差异。", status=409)
            text = str(selected_text) if selected_text is not None else self.repo.read_text(proposal["candidate_uri"])
            artifact = self._artifact(connection, work_id, "scene_script", "scene", scene["id"])
            if artifact.get("current_revision_id") != scene["current_revision_id"]:
                artifact["current_revision_id"] = scene["current_revision_id"]
            revision_id = self._add_revision(connection, artifact, self._scene_content_from_text(text), "user", {
                "workflow": "scene.review", "proposal_id": proposal_id, "pack": PACK_VERSION,
                "provider": json.loads(proposal["provider_json"]), "partial_accept": selected_text is not None,
            }, schema_version="scene-blocks/1.0")
            connection.execute("UPDATE scenes SET current_revision_id=?, status='review', version=version+1, updated_at=? WHERE id=?", (revision_id, now(), scene["id"]))
            connection.execute("UPDATE proposals SET status='accepted', decided_at=? WHERE id=?", (now(), proposal_id))
            connection.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (new_id("decision"), work_id, "proposal", proposal_id, "accepted", str(payload.get("note", "")), now()))
            connection.execute("UPDATE work_items SET status='succeeded', updated_at=? WHERE output_refs_json LIKE ?", (now(), f'%{proposal_id}%'))
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def _accept_knowledge_proposal(self, work_id: str, proposal_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)
            ).fetchone()
            if not proposal or proposal["kind"] not in {"character_card", "world_entity"}:
                raise NotFound("proposal", proposal_id)
            if proposal["status"] != "pending":
                raise DomainError("proposal_not_pending", "资料候选已经处理。", status=409)
            candidate = json.loads(self.repo.read_text(proposal["candidate_uri"]))
            if proposal["kind"] == "character_card":
                artifact = self._artifact(connection, work_id, "character_card", "character", candidate["scope_id"])
                if artifact.get("current_revision_id") != candidate.get("base_revision_id"):
                    connection.execute("UPDATE proposals SET status='superseded',decided_at=? WHERE id=?", (now(), proposal_id))
                    raise DomainError("proposal_superseded", "人物卡已经变化，请重新整理。", status=409)
                revision_id = self._add_revision(
                    connection, artifact, candidate["content"], "user",
                    {"workflow": "character.from_conversation", "pack": PACK_VERSION, "proposal_id": proposal_id, "thread_id": candidate["source_thread_id"]},
                )
                result_key = "card_id"
            else:
                artifact = self._artifact(connection, work_id, "world_bible", "work", work_id)
                if artifact.get("current_revision_id") != candidate.get("base_revision_id"):
                    connection.execute("UPDATE proposals SET status='superseded',decided_at=? WHERE id=?", (now(), proposal_id))
                    raise DomainError("proposal_superseded", "世界观已经变化，请重新整理。", status=409)
                if artifact.get("current_revision_id"):
                    revision = connection.execute("SELECT content_uri FROM revisions WHERE id=?", (artifact["current_revision_id"],)).fetchone()
                    bible = json.loads(self.repo.read_text(revision["content_uri"]))
                else:
                    bible = {"title": "作品世界观", "source_type": "custom", "entities": [], "rules": [], "timeline": []}
                bible = {**bible, "entities": [*(bible.get("entities") or []), candidate["content"]]}
                bible["source_type"] = self._merge_world_source_type([bible.get("source_type", "custom"), "custom"])
                revision_id = self._add_revision(
                    connection, artifact, bible, "user",
                    {"workflow": "world.from_conversation", "pack": PACK_VERSION, "proposal_id": proposal_id, "thread_id": candidate["source_thread_id"]},
                )
                result_key = "world_id"
            connection.execute("UPDATE proposals SET status='accepted',decided_at=? WHERE id=?", (now(), proposal_id))
            connection.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                (new_id("decision"), work_id, "proposal", proposal_id, "accepted", str(payload.get("note", "")), now()),
            )
            self._bump_work(connection, work_id, version)
        return {result_key: candidate["scope_id"], "revision_id": revision_id, "work": self.get_work(work_id)}

    def _accept_brief_blueprint_proposal(self, work_id: str, proposal_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)
            ).fetchone()
            if not proposal or proposal["kind"] != "brief_blueprint":
                raise NotFound("proposal", proposal_id)
            if proposal["status"] != "pending":
                raise DomainError("proposal_not_pending", "候选方案已经处理。", status=409)
            candidate = json.loads(self.repo.read_text(proposal["candidate_uri"]))
            brief_artifact = self._artifact(connection, work_id, "brief", "work", work_id)
            blueprint_artifact = self._artifact(connection, work_id, "story_blueprint", "work", work_id)
            if (
                brief_artifact.get("current_revision_id") != candidate.get("base_brief_revision_id")
                or blueprint_artifact.get("current_revision_id") != candidate.get("base_blueprint_revision_id")
            ):
                connection.execute("UPDATE proposals SET status='superseded',decided_at=? WHERE id=?", (now(), proposal_id))
                raise DomainError("proposal_superseded", "正式故事方案已经变化，请基于最新版本重新整理。", status=409)
            brief_revision_id = self._add_revision(
                connection, brief_artifact, {**candidate["brief"], "status": "confirmed"}, "user",
                {"workflow": "brief.from_conversation", "pack": PACK_VERSION, "proposal_id": proposal_id, "thread_id": candidate["source_thread_id"]},
            )
            blueprint_revision_id = self._add_revision(
                connection, blueprint_artifact,
                {**candidate["story_blueprint"], "status": "accepted", "decision": {"proposal_id": proposal_id, "brief_revision_id": brief_revision_id}},
                "user",
                {"workflow": "blueprint.from_conversation", "pack": PACK_VERSION, "proposal_id": proposal_id, "thread_id": candidate["source_thread_id"]},
            )
            connection.execute("UPDATE proposals SET status='accepted',decided_at=? WHERE id=?", (now(), proposal_id))
            connection.execute(
                "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                (new_id("decision"), work_id, "proposal", proposal_id, "accepted", str(payload.get("note", "")), now()),
            )
            self._bump_work(connection, work_id, version)
        return {
            "revision_id": blueprint_revision_id,
            "brief_revision_id": brief_revision_id,
            "blueprint_revision_id": blueprint_revision_id,
            "work": self.get_work(work_id),
        }

    def _accept_chapter_plan_proposal(self, work_id: str, proposal_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            proposal = connection.execute(
                "SELECT * FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)
            ).fetchone()
            if not proposal or proposal["kind"] != "chapter_plan":
                raise NotFound("proposal", proposal_id)
            if proposal["status"] != "pending":
                raise DomainError("proposal_not_pending", "候选方案已经处理。", status=409)
            candidate = json.loads(self.repo.read_text(proposal["candidate_uri"]))
            artifact = self._artifact(connection, work_id, "chapter_plan", "chapter", candidate["chapter_id"])
            if artifact.get("current_revision_id") != candidate.get("base_revision_id"):
                connection.execute("UPDATE proposals SET status='superseded',decided_at=? WHERE id=?", (now(), proposal_id))
                raise DomainError("proposal_superseded", "本章细纲已经变化，请基于最新讨论重新整理。", status=409)
            revision_id = self._add_revision(
                connection, artifact, {**candidate["chapter_plan"], "status": "accepted"}, "user",
                {"workflow": "chapter.plan", "pack": PACK_VERSION, "proposal_id": proposal_id, "thread_id": candidate["source_thread_id"], "chapter_id": candidate["chapter_id"]},
            )
            connection.execute("UPDATE proposals SET status='accepted',decided_at=? WHERE id=?", (now(), proposal_id))
            connection.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (new_id("decision"), work_id, "proposal", proposal_id, "accepted", str(payload.get("note", "")), now()))
            self._bump_work(connection, work_id, version)
        return {"revision_id": revision_id, "work": self.get_work(work_id)}

    def save_scene_manuscript(self, work_id: str, scene_id: str, payload: dict):
        """Create a manuscript Revision from user-edited stable SceneBlocks."""
        expected = int(payload.get("expected_version", -1))
        expected_base = payload.get("expected_base_revision_id") or None
        blocks = self._normalize_scene_blocks(payload.get("blocks"))
        text = self._scene_text_from_blocks(blocks)
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scene = connection.execute(
                "SELECT * FROM scenes WHERE id=? AND work_id=?", (scene_id, work_id)
            ).fetchone()
            if not scene:
                raise NotFound("scene", scene_id)
            if scene["current_revision_id"] != expected_base:
                raise DomainError(
                    "manuscript_conflict",
                    "正文已经产生新修订，请重新载入后再保存。",
                    status=409,
                    details={
                        "expected_base_revision_id": expected_base,
                        "actual_revision_id": scene["current_revision_id"],
                    },
                )
            artifact = self._artifact(connection, work_id, "scene_script", "scene", scene_id)
            if artifact.get("current_revision_id") != scene["current_revision_id"]:
                artifact["current_revision_id"] = scene["current_revision_id"]
            content = {"schema_version": "scene-blocks/1.0", "blocks": blocks, "text": text}
            revision_id = self._add_revision(
                connection,
                artifact,
                content,
                "user",
                {
                    "workflow": "scene.manuscript.edit",
                    "pack": PACK_VERSION,
                    "base_revision_id": expected_base,
                    "editor": "scene-blocks",
                },
                schema_version="scene-blocks/1.0",
            )
            timestamp = now()
            pending = connection.execute(
                "SELECT id FROM proposals WHERE work_id=? AND scope_type='scene' AND scope_id=? AND status='pending'",
                (work_id, scene_id),
            ).fetchall()
            if pending:
                connection.execute(
                    "UPDATE proposals SET status='superseded', decided_at=? WHERE work_id=? AND scope_type='scene' AND scope_id=? AND status='pending'",
                    (timestamp, work_id, scene_id),
                )
                for proposal in pending:
                    connection.execute(
                        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
                        (new_id("decision"), work_id, "proposal", proposal["id"], "superseded", "用户保存了新的正文修订，旧候选不再适用。", timestamp),
                    )
            connection.execute(
                "UPDATE scenes SET current_revision_id=?, status='draft', version=version+1, updated_at=? WHERE id=?",
                (revision_id, timestamp, scene_id),
            )
            self._bump_work(connection, work_id, version)
        return {
            "revision_id": revision_id,
            "superseded_proposal_ids": [row["id"] for row in pending],
            "work": self.get_work(work_id),
        }

    def reject_proposal(self, work_id: str, proposal_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            proposal = connection.execute("SELECT * FROM proposals WHERE id=? AND work_id=?", (proposal_id, work_id)).fetchone()
            if not proposal:
                raise NotFound("proposal", proposal_id)
            if proposal["status"] != "pending":
                raise DomainError("proposal_not_pending", "候选方案已经处理。", status=409)
            connection.execute("UPDATE proposals SET status='rejected', decided_at=? WHERE id=?", (now(), proposal_id))
            connection.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?)", (new_id("decision"), work_id, "proposal", proposal_id, "rejected", str(payload.get("note", "")), now()))
            self._bump_work(connection, work_id, version)
        return {"work": self.get_work(work_id)}

    def freeze_release(self, work_id: str, payload: dict):
        expected = int(payload.get("expected_version", -1))
        with self.repo.transaction() as connection:
            version = self._check_work_version(connection, work_id, expected)
            scenes = connection.execute("SELECT s.* FROM scenes s JOIN chapters c ON c.id=s.chapter_id WHERE s.work_id=? ORDER BY c.stable_order_key,s.stable_order_key", (work_id,)).fetchall()
            if not scenes:
                raise DomainError("release_blocked", "作品还没有场景。", status=409)
            missing = [scene["id"] for scene in scenes if not scene["current_revision_id"]]
            if missing:
                raise DomainError("release_blocked", "所有场景都需要有已采纳正文。", status=409, details={"scene_ids": missing})
            source_ids = [scene["current_revision_id"] for scene in scenes]
            placeholders = ",".join("?" for _ in source_ids)
            blocking_rows = connection.execute(
                f"SELECT id FROM review_findings WHERE revision_id IN ({placeholders}) AND severity='blocking' AND status='open' ORDER BY created_at",
                source_ids,
            ).fetchall()
            if blocking_rows:
                raise DomainError(
                    "release_blocked",
                    "发布前审查仍有未处理的阻塞项。",
                    status=409,
                    details={"finding_ids": [row["id"] for row in blocking_rows]},
                )
            latest_gate = connection.execute(
                "SELECT * FROM gates WHERE work_id=? AND kind='release.review' ORDER BY created_at DESC LIMIT 1",
                (work_id,),
            ).fetchone()
            if not latest_gate:
                raise DomainError("release_blocked", "请先运行全篇审查。", status=409, details={"reason": "release_review_missing"})
            gate_snapshot = json.loads(latest_gate["result_json"])
            if latest_gate["status"] != "passed" or gate_snapshot.get("scene_revision_ids") != source_ids:
                raise DomainError(
                    "release_blocked",
                    "全篇审查尚未通过，或正文修订已在审查后变更。",
                    status=409,
                    details={"gate_id": latest_gate["id"], "reason": "release_review_not_current"},
                )
            release_id = new_id("release")
            chunks = []
            manifest_scenes = []
            for scene in scenes:
                revision = connection.execute("SELECT * FROM revisions WHERE id=?", (scene["current_revision_id"],)).fetchone()
                content = json.loads(self.repo.read_text(revision["content_uri"]))
                chunks.append(f"## {scene['title']}\n{content['text'].rstrip()}\n")
                manifest_scenes.append({"scene_id": scene["id"], "revision_id": revision["id"], "title": scene["title"], "content_hash": revision["content_hash"]})
            release_text = "\n".join(chunks)
            content_uri, content_hash = self.repo.atomic_write_text(f"releases/{release_id}/script.txt", release_text)
            number = connection.execute("SELECT COUNT(*) FROM script_releases WHERE work_id=?", (work_id,)).fetchone()[0] + 1
            manifest = {
                "schema_version": "1.0", "release_id": release_id, "work_id": work_id,
                "display_version": f"v{number}", "content_hash": content_hash,
                "writing_pack_version": PACK_VERSION, "scenes": manifest_scenes,
                "gate_snapshot_ids": [latest_gate["id"]], "released_at": now(),
            }
            manifest_uri, _ = self.repo.atomic_write_text(f"releases/{release_id}/manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            connection.execute(
                "INSERT INTO script_releases VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (release_id, work_id, f"v{number}", manifest_uri, content_uri, content_hash, canonical_json(source_ids), canonical_json([latest_gate["id"]]), PACK_VERSION, None, "user", manifest["released_at"]),
            )
            connection.execute("UPDATE scenes SET status='released', updated_at=? WHERE work_id=?", (now(), work_id))
            self._bump_work(connection, work_id, version)
        return {"release_id": release_id, "manifest": manifest, "work": self.get_work(work_id)}

    def handoff_release(self, release_id: str):
        with self.repo.connect() as connection:
            release = connection.execute("SELECT * FROM script_releases WHERE id=?", (release_id,)).fetchone()
            if not release:
                raise NotFound("script_release", release_id)
            if release["production_run_id"]:
                return {"release_id": release_id, "production_run_id": release["production_run_id"], "idempotent": True}
            work = connection.execute("SELECT title FROM works WHERE id=?", (release["work_id"],)).fetchone()
        project_name = f"{work['title']} · {release['display_version']}"
        contract_hash = release["content_hash"].removeprefix("sha256:")
        existing_run_id = self._find_production_run(release_id, contract_hash)
        if existing_run_id:
            with self.repo.transaction() as connection:
                connection.execute("UPDATE script_releases SET production_run_id=? WHERE id=?", (existing_run_id, release_id))
            return {"release_id": release_id, "production_run_id": existing_run_id, "idempotent": True, "recovered": True}
        body = canonical_json({
            "project": project_name,
            "generation_mode": "format_only",
            "source": {"kind": "inline", "text": self.repo.read_text(release["content_uri"])},
            "script_release": {
                "schema_version": "1.0",
                "id": release_id,
                "work_id": release["work_id"],
                "display_version": release["display_version"],
                "content_hash": contract_hash,
                "writing_pack_version": release["writing_pack_version"],
            },
        }).encode("utf-8")
        request = urllib.request.Request(self.production_url + "/api/v1/production-runs", data=body, headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(
                request, timeout=PRODUCTION_HANDOFF_TIMEOUT_SECONDS
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                upstream = json.loads(exc.read().decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                upstream = {}
            error = upstream.get("error", {})
            raise DomainError(
                str(error.get("code") or "production_rejected"),
                str(error.get("message") or "AA 制作后端拒绝了这份发布版本。"),
                status=exc.code,
                details={
                    "upstream_code": str(error.get("code") or "production_rejected"),
                    "url": self.public_production_url,
                },
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise DomainError(
                "production_unavailable",
                "AA 制作后端当前不可用，发布版本仍安全保留。",
                status=503,
                details={"url": self.public_production_url},
            ) from exc
        run_id = (
            result.get("run_id")
            or result.get("id")
            or result.get("run", {}).get("run_id")
            or result.get("production_run", {}).get("id")
        )
        if not run_id:
            raise DomainError(
                "production_contract_error",
                "制作后端未返回 ProductionRun ID。",
                status=502,
                details={"response_keys": sorted(result) if isinstance(result, dict) else []},
            )
        with self.repo.transaction() as connection:
            connection.execute("UPDATE script_releases SET production_run_id=? WHERE id=?", (run_id, release_id))
        return {"release_id": release_id, "production_run_id": run_id, "response": result}

    def _find_production_run(self, release_id: str, content_hash: str):
        request = urllib.request.Request(self.production_url + "/api/v1/production-runs", method="GET")
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError):
            return None
        for item in result.get("items", []):
            origin = item.get("source_summary", {}).get("upstream_release", {})
            if (
                origin.get("release_id") == release_id
                and origin.get("content_hash") == content_hash
                and item.get("run_id")
            ):
                return item["run_id"]
        return None

    def get_release(self, release_id: str):
        with self.repo.connect() as connection:
            release = self.repo.row(connection.execute("SELECT * FROM script_releases WHERE id=?", (release_id,)).fetchone())
            if not release:
                raise NotFound("script_release", release_id)
            release["manifest"] = json.loads(self.repo.read_text(release["manifest_uri"]))
            release["text"] = self.repo.read_text(release["content_uri"])
            return release

    def writing_model_settings_public(self) -> dict:
        return self.model_settings.public()

    def configure_writing_model(self, payload: dict) -> dict:
        result = self.model_settings.save(payload)
        self.provider = make_writing_provider(self.model_settings)
        return result

    def fetch_writing_models(self, payload: dict | None = None) -> list[str]:
        return self.model_settings.fetch_models(payload)

    def test_writing_model(self, payload: dict | None = None) -> dict:
        return self.model_settings.test_connection(payload)

    def user_preferences(self) -> dict:
        return {"ok": True, "preferences": self.preferences.load()}

    def save_user_preferences(self, payload: dict) -> dict:
        return {"ok": True, "preferences": self.preferences.save(payload)}

    def system_diagnostics(self) -> dict:
        prod_health = False
        try:
            req = urllib.request.Request(self.production_url + "/api/v1/health")
            with urllib.request.urlopen(req, timeout=3) as resp:
                prod_health = (resp.status == 200)
        except Exception:
            pass

        corpus_count = 0
        if self.official_references.available:
            try:
                corpus_count = len(list(self.official_references.corpus_dir.glob("*.json")))
            except Exception:
                pass

        return {
            "ok": True,
            "writing_service": {
                "status": "online",
                "model_configured": self.model_settings.public()["model"]["configured"],
                "dpapi_available": os.name == "nt",
            },
            "production_service": {
                "status": "online" if prod_health else "offline",
                "url": self.public_production_url,
            },
            "corpus_status": {
                "available": self.official_references.available,
                "count": corpus_count,
            },
        }
