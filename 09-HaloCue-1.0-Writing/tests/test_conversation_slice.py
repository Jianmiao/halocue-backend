import base64

import pytest

from halocue_writing.errors import DomainError
from halocue_writing.providers import FakeWritingProvider
from halocue_writing.service import WritingService


PNG_1X1 = base64.b64encode(
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
).decode("ascii")


class ReasoningProvider(FakeWritingProvider):
    is_simulation = False
    display_name = "Reasoning Test Provider"

    def discuss_work(self, messages: list[dict], work_context: dict) -> dict:
        reply = super().discuss_work(messages, work_context)
        reply["reasoning_summary"] = "先核对当前任务范围，再判断是否需要继续追问。"
        reply["reasoning_content"] = "检查任务合同。\n读取正式上下文。\n决定本轮只继续讨论。"
        return reply


def test_work_supports_multiple_durable_conversations_with_rename_and_archive(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "多会话作品", "idea": "先讨论整体方向。"})

    created = service.create_conversation_thread(
        work["id"],
        {"expected_version": work["version"], "title": "人物关系讨论", "scope_type": "work"},
    )
    assert len(created["work"]["conversation_threads"]) == 2
    new_thread = next(item for item in created["work"]["conversation_threads"] if item["id"] == created["thread_id"])
    assert new_thread["messages"][0]["kind"] == "notice"

    renamed = service.update_conversation_thread(
        work["id"], new_thread["id"],
        {"expected_thread_version": new_thread["version"], "title": "凯伊关系线", "status": "active"},
    )
    renamed_thread = next(item for item in renamed["work"]["conversation_threads"] if item["id"] == new_thread["id"])
    archived = service.update_conversation_thread(
        work["id"], renamed_thread["id"],
        {"expected_thread_version": renamed_thread["version"], "status": "archived"},
    )

    restored = WritingService(tmp_path).get_work(work["id"])
    restored_thread = next(item for item in restored["conversation_threads"] if item["id"] == new_thread["id"])
    assert restored_thread["title"] == "凯伊关系线"
    assert restored_thread["status"] == "archived"
    assert next(item for item in archived["work"]["authorization_policies"] if item["thread_id"] == new_thread["id"])["status"] == "archived"

    reopened = service.update_conversation_thread(
        work["id"], restored_thread["id"],
        {"expected_thread_version": restored_thread["version"], "status": "active"},
    )
    reopened_thread = next(item for item in reopened["work"]["conversation_threads"] if item["id"] == new_thread["id"])
    assert reopened_thread["status"] == "active"
    assert next(item for item in reopened["work"]["authorization_policies"] if item["thread_id"] == new_thread["id"])["status"] == "active"


def test_conversation_image_attachment_is_validated_persisted_and_bound_to_message(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "图片讨论", "idea": "讨论参考图。"})
    thread = work["conversation_threads"][0]

    uploaded = service.create_conversation_attachment(
        work["id"], thread["id"],
        {
            "expected_thread_version": thread["version"],
            "filename": "reference.png",
            "media_type": "image/png",
            "content_base64": PNG_1X1,
        },
    )
    uploaded_thread = next(item for item in uploaded["work"]["conversation_threads"] if item["id"] == thread["id"])
    attachment = uploaded_thread["attachments"][0]
    assert attachment["status"] == "staged"

    sent = service.post_conversation_message(
        work["id"], thread["id"],
        {
            "expected_thread_version": uploaded_thread["version"],
            "text": "这张图作为气氛参考。",
            "attachment_ids": [attachment["id"]],
        },
    )
    sent_thread = next(item for item in sent["work"]["conversation_threads"] if item["id"] == thread["id"])
    assert sent_thread["attachments"][0]["status"] == "attached"
    assert sent_thread["attachments"][0]["message_id"]
    assert sent_thread["messages"][-2]["content"]["attachments"][0]["filename"] == "reference.png"
    assert "不具备视觉理解能力" in sent_thread["messages"][-1]["content"]["text"]

    media_type, content = WritingService(tmp_path).get_conversation_attachment(work["id"], attachment["id"])
    assert media_type == "image/png"
    assert content.startswith(b"\x89PNG")


def test_conversation_attachment_rejects_mismatched_image_type(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "错误图片", "idea": "测试附件校验。"})
    thread = work["conversation_threads"][0]

    with pytest.raises(DomainError) as mismatch:
        service.create_conversation_attachment(
            work["id"], thread["id"],
            {
                "expected_thread_version": thread["version"],
                "filename": "fake.png",
                "media_type": "image/png",
                "content_base64": base64.b64encode(b"not an image").decode("ascii"),
            },
        )
    assert mismatch.value.code == "attachment_type_mismatch"


def test_new_work_atomically_creates_volume_chapter_and_work_conversation(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work(
        {
            "idea": "爱丽丝和凯伊发现一台只在深夜回应的旧机器。",
            "world_seed": "blank",
        }
    )

    assert work["title"].startswith("爱丽丝和凯伊")
    assert len(work["volumes"]) == 1
    assert work["volumes"][0]["title"] == "第一卷"
    assert len(work["volumes"][0]["chapters"]) == 1
    assert work["volumes"][0]["chapters"][0]["status"] == "placeholder"
    thread = work["conversation_threads"][0]
    assert thread["scope_type"] == "work"
    assert thread["phase"] == "discuss"
    assert thread["permission_mode"] == "review"
    assert [message["role"] for message in thread["messages"]] == ["user", "assistant"]
    assert thread["messages"][1]["provider"]["is_simulation"] is True
    contract = thread["messages"][1]["content"]["task_contract"]
    assert contract["id"] == "brief.build"
    assert contract["version"] == "1.0.0"
    assert contract["rule_sources"]["common"]
    assert not any(item["kind"] == "brief" for item in work["artifacts"])

    restored = WritingService(tmp_path).get_work(work["id"])
    assert restored["volumes"][0]["chapters"][0]["id"] == work["chapters"][0]["id"]
    assert [message["content"] for message in restored["conversation_threads"][0]["messages"]] == [
        message["content"] for message in thread["messages"]
    ]


def test_conversation_turns_and_permission_changes_are_version_checked(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "权限测试", "idea": "两个人对一条匿名留言产生不同理解。"})
    thread = work["conversation_threads"][0]

    continued = service.post_conversation_message(
        work["id"],
        thread["id"],
        {"expected_thread_version": thread["version"], "text": "不要把留言解释成反派阴谋。"},
    )
    updated_thread = continued["work"]["conversation_threads"][0]
    assert updated_thread["version"] == thread["version"] + 1
    assert len(updated_thread["messages"]) == 4
    reply = updated_thread["messages"][-1]["content"]
    assert "不要把留言解释成反派阴谋" in reply["text"]
    assert reply["ready_to_organize"] is True
    assert [item["tool"] for item in reply["tool_activity"]] == [
        "load_workflow_template",
        "read_work_context",
    ]
    trace = reply["agent_trace"]
    assert trace["schema_version"] == "agent-trace/1.0"
    assert trace["visibility"] == "user_summary"
    assert trace["status"] == "completed"
    assert trace["task_id"] == "brief.build"
    assert trace["reasoning"]["available"] is True
    assert trace["reasoning"]["source"] == "provider"
    assert trace["reasoning"]["is_simulation"] is True
    assert [item["tool"] for item in trace["steps"]] == [
        "load_workflow_template",
        "read_work_context",
    ]
    assert "正式产物" in trace["outcome"]

    restored_reply = WritingService(tmp_path).get_work(work["id"])["conversation_threads"][0]["messages"][-1]["content"]
    assert restored_reply["agent_trace"] == trace

    with pytest.raises(DomainError) as conflict:
        service.post_conversation_message(
            work["id"],
            thread["id"],
            {"expected_thread_version": thread["version"], "text": "这是过期消息。"},
        )
    assert conflict.value.code == "thread_conflict"

    settings = service.update_conversation_settings(
        work["id"],
        thread["id"],
        {
            "expected_thread_version": updated_thread["version"],
            "permission_mode": "managed",
            "phase": "execute",
        },
    )
    restored = WritingService(tmp_path).get_work(work["id"])
    assert settings["work"]["conversation_threads"][0]["permission_mode"] == "managed"
    assert restored["authorization_policies"][0]["allowed_actions"] == [
        "read",
        "discuss",
        "auto_accept_low_risk_writing",
    ]


def test_provider_supplied_reasoning_chain_is_persisted_only_when_available(tmp_path):
    service = WritingService(tmp_path)
    service.provider = ReasoningProvider()
    work = service.create_work({"title": "思考链测试", "idea": "先讨论开场异常。"})

    content = work["conversation_threads"][0]["messages"][-1]["content"]
    reasoning = content["agent_trace"]["reasoning"]
    assert reasoning["available"] is True
    assert reasoning["source"] == "provider"
    assert reasoning["mode"] == "chain"
    assert reasoning["content"] == "检查任务合同。\n读取正式上下文。\n决定本轮只继续讨论。"

    restored = WritingService(tmp_path).get_work(work["id"])
    restored_reasoning = restored["conversation_threads"][0]["messages"][-1]["content"]["agent_trace"]["reasoning"]
    assert restored_reasoning == reasoning


def test_character_card_discussion_returns_a_visible_draft_without_writing_formal_data(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "角色讨论", "idea": "先讨论一个新角色。"})
    thread = work["conversation_threads"][0]

    result = service.post_conversation_message(
        work["id"],
        thread["id"],
        {
            "expected_thread_version": thread["version"],
            "text": "我想创建一个叫《白露》的自定义角色卡。",
        },
    )

    content = result["work"]["conversation_threads"][0]["messages"][-1]["content"]
    assert content["artifact_preview"]["kind"] == "character_card"
    assert content["artifact_preview"]["title"] == "白露"
    assert content["artifact_preview"]["status"] == "discussion_draft"
    assert content["tool_activity"][-1]["tool"] == "draft_character_card"
    assert content["agent_trace"]["outcome"] == "已形成人物卡讨论草稿；正式资料尚未改变。"
    assert not any(item["kind"] == "character_card" for item in result["work"]["artifacts"])


def test_character_discussion_requires_proposal_before_creating_a_versioned_card(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "人物卡维护", "idea": "讨论一个会参与调查的新角色。"})
    thread = work["conversation_threads"][0]
    discussed = service.post_conversation_message(
        work["id"], thread["id"],
        {"expected_thread_version": thread["version"], "text": "创建一个叫《白露》的自定义角色卡，她负责辨认旧机器留下的声音。"},
    )
    current_thread = discussed["work"]["conversation_threads"][0]
    proposed = service.propose_conversation_knowledge(
        work["id"], thread["id"],
        {
            "expected_version": discussed["work"]["version"],
            "expected_thread_version": current_thread["version"],
            "kind": "character_card",
        },
    )
    proposal = next(item for item in proposed["work"]["proposals"] if item["id"] == proposed["proposal_id"])
    assert proposal["kind"] == "character_card"
    assert proposal["candidate"]["content"]["name"] == "白露"
    assert not any(item["kind"] == "character_card" for item in proposed["work"]["artifacts"])

    with pytest.raises(DomainError) as conflict:
        service.accept_proposal(work["id"], proposal["id"], {"expected_version": discussed["work"]["version"]})
    assert conflict.value.code == "revision_conflict"

    accepted = service.accept_proposal(
        work["id"], proposal["id"], {"expected_version": proposed["work"]["version"]}
    )
    card = next(item for item in accepted["work"]["artifacts"] if item["kind"] == "character_card")
    assert card["scope_id"] == accepted["card_id"]
    assert card["current_revision"]["content"]["name"] == "白露"
    assert card["current_revision"]["provenance"]["proposal_id"] == proposal["id"]

    restored = WritingService(tmp_path).get_work(work["id"])
    restored_proposal = next(item for item in restored["proposals"] if item["id"] == proposal["id"])
    assert restored_proposal["status"] == "accepted"
    assert restored_proposal["candidate"]["content"]["name"] == "白露"


def test_world_discussion_proposal_is_recoverable_and_updates_world_bible_only_after_acceptance(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "世界观维护", "idea": "调查一座停用校舍。"})
    thread = work["conversation_threads"][0]
    discussed = service.post_conversation_message(
        work["id"], thread["id"],
        {"expected_thread_version": thread["version"], "text": "请创建《静默校舍》的地点设定：只有午夜后旧广播才会工作。"},
    )
    draft = discussed["work"]["conversation_threads"][0]["messages"][-1]["content"]["artifact_preview"]
    assert draft["title"] == "静默校舍"
    current_thread = discussed["work"]["conversation_threads"][0]
    proposed = service.propose_conversation_knowledge(
        work["id"], thread["id"],
        {
            "expected_version": discussed["work"]["version"],
            "expected_thread_version": current_thread["version"],
            "kind": "world_card",
        },
    )
    proposal = next(item for item in proposed["work"]["proposals"] if item["id"] == proposed["proposal_id"])
    assert proposal["kind"] == "world_entity"
    assert proposal["candidate"]["content"]["name"] == "静默校舍"
    assert not any(item["kind"] == "world_bible" for item in proposed["work"]["artifacts"])

    with pytest.raises(DomainError) as waiting:
        service.propose_conversation_knowledge(
            work["id"], thread["id"],
            {
                "expected_version": proposed["work"]["version"],
                "expected_thread_version": proposed["work"]["conversation_threads"][0]["version"],
                "kind": "world_card",
            },
        )
    assert waiting.value.code == "proposal_waiting_user"

    accepted = service.accept_proposal(
        work["id"], proposal["id"], {"expected_version": proposed["work"]["version"]}
    )
    bible = next(item for item in accepted["work"]["artifacts"] if item["kind"] == "world_bible")
    assert [item["name"] for item in bible["current_revision"]["content"]["entities"]] == ["静默校舍"]
    assert bible["current_revision"]["provenance"]["proposal_id"] == proposal["id"]


def test_discussion_becomes_auditable_proposal_before_formal_artifacts(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "讨论后成案", "idea": "凯伊发现爱丽丝隐瞒了一段日志。"})
    thread = work["conversation_threads"][0]
    discussed = service.post_conversation_message(
        work["id"],
        thread["id"],
        {
            "expected_thread_version": thread["version"],
            "text": "重点是两个人如何重新确认信任，不要立刻揭示日志来源。",
        },
    )
    current_thread = discussed["work"]["conversation_threads"][0]
    proposed = service.organize_conversation_proposal(
        work["id"],
        thread["id"],
        {
            "expected_version": discussed["work"]["version"],
            "expected_thread_version": current_thread["version"],
        },
    )

    proposal = next(item for item in proposed["work"]["proposals"] if item["id"] == proposed["proposal_id"])
    assert proposal["kind"] == "brief_blueprint"
    assert proposal["status"] == "pending"
    assert proposal["candidate"]["brief"]["idea"] == "凯伊发现爱丽丝隐瞒了一段日志。"
    assert not any(item["kind"] in {"brief", "story_blueprint"} for item in proposed["work"]["artifacts"])

    accepted = service.accept_proposal(
        work["id"], proposal["id"], {"expected_version": proposed["work"]["version"]}
    )
    brief = next(item for item in accepted["work"]["artifacts"] if item["kind"] == "brief")
    blueprint = next(item for item in accepted["work"]["artifacts"] if item["kind"] == "story_blueprint")
    assert brief["current_revision"]["content"]["status"] == "confirmed"
    assert blueprint["current_revision"]["content"]["status"] == "accepted"

    restored = WritingService(tmp_path).get_work(work["id"])
    assert next(item for item in restored["proposals"] if item["id"] == proposal["id"])["status"] == "accepted"
    assert restored["conversation_threads"][0]["messages"][-1]["proposal_id"] == proposal["id"]


def test_conversation_task_contract_changes_with_the_server_validated_stage(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "阶段合同", "idea": "两位学生在夜间校舍里寻找一段失落的录音。"})
    thread = work["conversation_threads"][0]
    proposal_result = service.organize_conversation_proposal(
        work["id"], thread["id"], {"expected_version": work["version"], "expected_thread_version": thread["version"]}
    )
    proposal = next(item for item in proposal_result["work"]["proposals"] if item["id"] == proposal_result["proposal_id"])
    accepted = service.accept_proposal(work["id"], proposal["id"], {"expected_version": proposal_result["work"]["version"]})
    current_thread = accepted["work"]["conversation_threads"][0]

    continued = service.post_conversation_message(
        work["id"],
        current_thread["id"],
        {"expected_thread_version": current_thread["version"], "text": "第一卷希望先从一段日常互动开始。"},
    )
    contract = continued["work"]["conversation_threads"][0]["messages"][-1]["content"]["task_contract"]
    assert contract["id"] == "structure.plan"
    assert contract["pack"]
    assert contract["execution"] == "proposal_then_confirm"


def test_writing_target_and_chapter_plan_are_durable_and_scoped(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "章节范围", "idea": "两位学生在夜间校舍寻找录音。"})
    card = service.save_character_card(work["id"], {"expected_version": work["version"], "card_id": "character-a", "name": "学生甲", "source_type": "custom", "trust_status": "confirmed", "source_refs": ["用户设定"]})
    brief = service.save_brief(work["id"], {"expected_version": card["work"]["version"], "idea": "两位学生在夜间校舍寻找录音。", "intent_only": True})
    blueprint = service.generate_blueprint(work["id"], {"expected_version": brief["work"]["version"]})
    confirmed = service.confirm_blueprint(work["id"], {"expected_version": blueprint["work"]["version"], "mode": "bond_short", "character_card_ids": ["character-a"], "sensei_presence": "auto"})
    chapter = service.create_chapter(work["id"], {"expected_version": confirmed["work"]["version"], "title": "夜间调查"})
    target = service.set_writing_target(work["id"], {"expected_version": chapter["work"]["version"], "chapter_id": chapter["chapter_id"]})
    restored = WritingService(tmp_path).get_work(work["id"])
    target_artifact = next(item for item in restored["artifacts"] if item["kind"] == "writing_target")
    assert target_artifact["current_revision"]["content"]["chapter_id"] == chapter["chapter_id"]

    thread = restored["conversation_threads"][0]
    continued = service.post_conversation_message(
        work["id"], thread["id"],
        {"expected_thread_version": thread["version"], "text": "这一章先建立两人的信任，再找到录音。", "task_scope": {"surface": "chapter", "chapter_id": chapter["chapter_id"]}},
    )
    contract = continued["work"]["conversation_threads"][0]["messages"][-1]["content"]["task_contract"]
    assert contract["id"] == "chapter.plan"
    assert contract["task_scope"]["chapter_id"] == chapter["chapter_id"]

    proposal_result = service.organize_conversation_proposal(
        work["id"], thread["id"],
        {"expected_version": continued["work"]["version"], "expected_thread_version": continued["work"]["conversation_threads"][0]["version"], "task_scope": {"surface": "chapter", "chapter_id": chapter["chapter_id"]}},
    )
    proposal = next(item for item in proposal_result["work"]["proposals"] if item["id"] == proposal_result["proposal_id"])
    assert proposal["kind"] == "chapter_plan"
    accepted = service.accept_proposal(work["id"], proposal["id"], {"expected_version": proposal_result["work"]["version"]})
    plan = next(item for item in accepted["work"]["artifacts"] if item["kind"] == "chapter_plan")
    assert plan["scope_type"] == "chapter"
    assert plan["scope_id"] == chapter["chapter_id"]
    assert plan["current_revision"]["content"]["status"] == "accepted"
    assert not any(item["kind"] == "story_blueprint" and item["scope_type"] == "chapter" for item in accepted["work"]["artifacts"])
