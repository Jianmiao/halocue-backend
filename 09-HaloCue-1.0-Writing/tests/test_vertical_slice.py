import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from halocue_writing.errors import DomainError, RevisionConflict
from halocue_writing.service import WritingService
from halocue_writing.workflow_pack import MODE_SOURCES


def build_to_proposal(service: WritingService):
    work = service.create_work({"title": "迟到的线索"})
    result = service.save_brief(
        work["id"],
        {
            "expected_version": work["version"],
            "idea": "凯伊发现旧机器在深夜自行启动",
            "mode": "bond_short",
            "characters": ["爱丽丝", "凯伊"],
        },
    )
    result = service.generate_blueprint(work["id"], {"expected_version": result["work"]["version"]})
    result = service.create_chapter(work["id"], {"expected_version": result["work"]["version"], "title": "第一章"})
    chapter_id = result["chapter_id"]
    result = service.create_scene(
        work["id"],
        chapter_id,
        {
            "expected_version": result["work"]["version"],
            "title": "提示灯",
            "location": "游戏开发部活动室",
            "goal": "确认异常提示灯的来源",
        },
    )
    scene_id = result["scene_id"]
    context = service.assemble_context(work["id"], scene_id)
    assert context["scene_id"] == scene_id
    assert context["readiness"]["fake_provider"] == "ready"
    assert context["readiness"]["real_ba_writing"] == "blocked"
    result = service.generate_scene_candidate(
        work["id"], scene_id, {"expected_version": result["work"]["version"]}
    )
    return work["id"], scene_id, result["proposal_id"], result["work"]


def test_feedback_report_is_persisted_with_page_context(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "反馈验收", "idea": "测试反馈入口"})

    report = service.submit_feedback(
        {
            "work_id": work["id"],
            "category": "usability",
            "summary": "侧栏不知道下一步点哪里",
            "details": "希望突出当前步骤并减少重复入口。",
            "context": {"stage": "structure", "viewport": {"width": 1440, "height": 900}},
        }
    )

    restarted = WritingService(tmp_path)
    with restarted.repo.connect() as connection:
        saved = connection.execute(
            "SELECT * FROM feedback_reports WHERE id=?", (report["id"],)
        ).fetchone()
    assert saved["status"] == "open"
    assert saved["work_id"] == work["id"]
    assert saved["summary"] == "侧栏不知道下一步点哪里"
    assert json.loads(saved["context_json"])["stage"] == "structure"

    with pytest.raises(DomainError) as error:
        service.submit_feedback({"category": "usability", "summary": "", "details": ""})
    assert error.value.code == "validation_error"


def test_real_vertical_slice_persists_and_reloads(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    edited = "旁白: 灯亮了。\n爱丽丝: 先确认电源。\n"
    accepted = service.accept_proposal(
        work_id,
        proposal_id,
        {"expected_version": work["version"], "text": edited},
    )
    reviewed = service.review_scene(work_id, scene_id, {"expected_version": accepted["work"]["version"]})
    release_review = service.review_release(work_id, {"expected_version": reviewed["work"]["version"]})
    assert release_review["status"] == "passed"
    release = service.freeze_release(
        work_id, {"expected_version": release_review["work"]["version"]}
    )

    restarted = WritingService(tmp_path)
    loaded = restarted.get_work(work_id)
    loaded_scene = loaded["chapters"][0]["scenes"][0]
    assert loaded_scene["id"] == scene_id
    assert loaded_scene["current_revision_id"] == accepted["revision_id"]
    frozen = restarted.get_release(release["release_id"])
    assert frozen["text"] == "## 提示灯\n" + edited.rstrip() + "\n"
    assert frozen["manifest"]["scenes"][0]["scene_id"] == scene_id
    assert frozen["content_hash"].startswith("sha256:")


def test_manual_scene_blocks_are_versioned_restart_safe_and_release_compatible(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(
        work_id,
        proposal_id,
        {"expected_version": work["version"], "text": "旁白: 灯亮了。\n爱丽丝: 先确认电源。\n"},
    )
    pending = service.generate_scene_candidate(
        work_id, scene_id, {"expected_version": accepted["work"]["version"]}
    )
    blocks = [
        {"id": "block-opening", "type": "action", "text": "深夜的活动室里，旧显示器先亮了一格。"},
        {"id": "block-aris-01", "type": "dialogue", "speaker": "爱丽丝", "text": "先确认电源。"},
        {"id": "block-kay-01", "type": "dialogue", "speaker": "凯伊", "text": "我去看背面的线路。"},
    ]
    saved = service.save_scene_manuscript(
        work_id,
        scene_id,
        {
            "expected_version": pending["work"]["version"],
            "expected_base_revision_id": accepted["revision_id"],
            "blocks": blocks,
        },
    )
    assert saved["superseded_proposal_ids"] == [pending["proposal_id"]]
    scene = saved["work"]["chapters"][0]["scenes"][0]
    artifact = next(item for item in saved["work"]["artifacts"] if item["kind"] == "scene_script")
    assert scene["current_revision_id"] == saved["revision_id"]
    assert artifact["current_revision"]["schema_version"] == "scene-blocks/1.0"
    assert artifact["current_revision"]["content"]["blocks"] == blocks
    assert artifact["current_revision"]["content"]["text"] == (
        "深夜的活动室里，旧显示器先亮了一格。\n爱丽丝: 先确认电源。\n凯伊: 我去看背面的线路。\n"
    )
    superseded = next(item for item in saved["work"]["proposals"] if item["id"] == pending["proposal_id"])
    assert superseded["status"] == "superseded"

    with pytest.raises(RevisionConflict):
        service.save_scene_manuscript(
            work_id,
            scene_id,
            {"expected_version": pending["work"]["version"], "expected_base_revision_id": saved["revision_id"], "blocks": blocks},
        )
    with pytest.raises(DomainError) as error:
        service.save_scene_manuscript(
            work_id,
            scene_id,
            {
                "expected_version": saved["work"]["version"],
                "expected_base_revision_id": accepted["revision_id"],
                "blocks": blocks,
            },
        )
    assert error.value.code == "manuscript_conflict"

    restarted = WritingService(tmp_path)
    restored = restarted.get_work(work_id)
    restored_artifact = next(item for item in restored["artifacts"] if item["kind"] == "scene_script")
    assert [block["id"] for block in restored_artifact["current_revision"]["content"]["blocks"]] == [
        "block-opening", "block-aris-01", "block-kay-01"
    ]
    reviewed = restarted.review_scene(work_id, scene_id, {"expected_version": restored["version"]})
    release_review = restarted.review_release(work_id, {"expected_version": reviewed["work"]["version"]})
    release = restarted.freeze_release(work_id, {"expected_version": release_review["work"]["version"]})
    assert restarted.get_release(release["release_id"])["text"] == (
        "## 提示灯\n深夜的活动室里，旧显示器先亮了一格。\n爱丽丝: 先确认电源。\n凯伊: 我去看背面的线路。\n"
    )


def test_stale_work_version_is_rejected(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "冲突测试"})
    service.save_brief(
        work["id"],
        {"expected_version": work["version"], "idea": "第一次保存", "mode": "bond_short"},
    )
    with pytest.raises(RevisionConflict) as error:
        service.save_brief(
            work["id"],
            {"expected_version": work["version"], "idea": "过期写入", "mode": "bond_short"},
        )
    assert error.value.details == {"expected_version": 1, "actual_version": 2}


def test_stale_proposal_cannot_overwrite_new_revision(tmp_path):
    service = WritingService(tmp_path)
    work_id, _, first_proposal, work = build_to_proposal(service)
    second = service.generate_scene_candidate(
        work_id,
        work["chapters"][0]["scenes"][0]["id"],
        {"expected_version": work["version"]},
    )
    accepted = service.accept_proposal(
        work_id,
        second["proposal_id"],
        {"expected_version": second["work"]["version"]},
    )
    with pytest.raises(DomainError) as error:
        service.accept_proposal(
            work_id,
            first_proposal,
            {"expected_version": accepted["work"]["version"]},
        )
    assert error.value.code == "proposal_superseded"


def test_scene_rewrite_agent_pins_manuscript_and_stays_proposal_only(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(work_id, proposal_id, {"expected_version": work["version"], "text": "旁白: 灯亮了。\n爱丽丝: 先确认电源。\n"})
    prepared = service.save_character_card(work_id, {"expected_version": accepted["work"]["version"], "card_id": "character-aris", "name": "爱丽丝", "source_refs": ["用户确认"], "voice_anchors": ["先确认眼前的情况。"], "trust_status": "confirmed"})
    configured = service.configure_scene_context(work_id, scene_id, {"expected_version": prepared["work"]["version"], "character_card_ids": ["character-aris"], "world_item_ids": [], "reference_file_ids": []})
    rewritten = service.run_scene_rewrite_agent(work_id, scene_id, {"expected_version": configured["work"]["version"], "instruction": "调整本场节奏，保留停顿。"})
    proposal = next(item for item in rewritten["work"]["proposals"] if item["id"] == rewritten["proposal_id"])
    assert proposal["base_revision_id"] == accepted["revision_id"]
    assert proposal["status"] == "pending"
    current_scene = rewritten["work"]["chapters"][0]["scenes"][0]
    assert current_scene["current_revision_id"] == accepted["revision_id"]
    agent = next(item for item in rewritten["work"]["agent_runs"] if item["id"] == rewritten["agent_run_id"])
    assert agent["policy"]["workflow"] == "scene.draft.rewrite"
    assert [call["tool_name"] for call in agent["tool_calls"]] == ["assemble_scene_context", "validate_runtime_character_cards", "read_pinned_scene_revision", "generate_single_proposal"]
    restored = WritingService(tmp_path).get_work(work_id)
    restored_proposal = next(item for item in restored["proposals"] if item["id"] == rewritten["proposal_id"])
    assert restored_proposal["base_revision_id"] == accepted["revision_id"]


def test_restart_abandons_attempt_and_requeues_work_item(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "恢复测试"})
    with service.repo.transaction() as connection:
        run_id = connection.execute(
            "SELECT id FROM production_runs WHERE work_id=?", (work["id"],)
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO work_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "item-crashed", run_id, "scene.draft.generate", "scene", "scene-x",
                "running", "[]", "[]", "{}", 1, None, "now", "now",
            ),
        )
        connection.execute(
            "INSERT INTO job_attempts VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("attempt-crashed", "item-crashed", 1, "fake", "sha256:x", "started", None, None, "now", None),
        )

    WritingService(tmp_path)
    with sqlite3.connect(tmp_path / "writing.db") as connection:
        attempt = connection.execute(
            "SELECT status, error_code FROM job_attempts WHERE id='attempt-crashed'"
        ).fetchone()
        item = connection.execute(
            "SELECT status FROM work_items WHERE id='item-crashed'"
        ).fetchone()
    assert attempt == ("abandoned", "process_restarted")
    assert item == ("ready",)


def test_release_files_do_not_change_after_new_draft(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(
        work_id, proposal_id, {"expected_version": work["version"]}
    )
    reviewed = service.review_scene(work_id, scene_id, {"expected_version": accepted["work"]["version"]})
    release_review = service.review_release(work_id, {"expected_version": reviewed["work"]["version"]})
    release = service.freeze_release(
        work_id, {"expected_version": release_review["work"]["version"]}
    )
    original = service.get_release(release["release_id"])
    next_candidate = service.generate_scene_candidate(
        work_id, scene_id, {"expected_version": release["work"]["version"]}
    )
    service.accept_proposal(
        work_id,
        next_candidate["proposal_id"],
        {"expected_version": next_candidate["work"]["version"], "text": "凯伊: 新版本。\n"},
    )
    unchanged = service.get_release(release["release_id"])
    assert unchanged["content_hash"] == original["content_hash"]
    assert unchanged["text"] == original["text"]


def test_workflow_pack_has_versioned_structured_steps(tmp_path):
    service = WritingService(tmp_path)
    pack = service.capabilities()["writing_pack"]
    ids = {item["id"] for item in pack["templates"]}
    assert {"brief.build", "scene.context.assemble", "scene.draft.generate", "release.review"} <= ids
    assert all(item["version"] and item["inputs"] and item["outputs"] for item in pack["templates"])
    assert pack["runtime_contract"]["agent_writes_through_proposal_only"] is True


def test_handoff_accepts_nested_run_response_and_is_idempotent(tmp_path):
    service = WritingService(tmp_path)
    work_id, _, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(work_id, proposal_id, {"expected_version": work["version"]})
    reviewed = service.review_scene(work_id, work["chapters"][0]["scenes"][0]["id"], {"expected_version": accepted["work"]["version"]})
    release_review = service.review_release(work_id, {"expected_version": reviewed["work"]["version"]})
    release = service.freeze_release(work_id, {"expected_version": release_review["work"]["version"]})

    class ProductionHandler(BaseHTTPRequestHandler):
        posts = 0
        posted_payloads = []

        def log_message(self, *_):
            pass

        def do_GET(self):
            body = json.dumps({"ok": True, "items": []}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            type(self).posts += 1
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            type(self).posted_payloads.append(payload)
            body = json.dumps({"ok": True, "run": {"run_id": "run-nested"}}).encode()
            self.send_response(201); self.end_headers(); self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), ProductionHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    service.production_url = f"http://127.0.0.1:{server.server_port}"
    try:
        first = service.handoff_release(release["release_id"])
        second = service.handoff_release(release["release_id"])
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert first["production_run_id"] == "run-nested"
    assert second["idempotent"] is True
    assert ProductionHandler.posts == 1
    submitted = ProductionHandler.posted_payloads[0]
    assert submitted["generation_mode"] == "format_only"
    assert submitted["script_release"] == {
        "schema_version": "1.0",
        "id": release["release_id"],
        "work_id": work_id,
        "display_version": "v1",
        "content_hash": release["manifest"]["content_hash"].removeprefix("sha256:"),
        "writing_pack_version": release["manifest"]["writing_pack_version"],
    }


def test_references_are_durable_and_make_runtime_cards_provider_ready(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    canon = service.save_work_canon(
        work_id,
        {
            "expected_version": work["version"],
            "facts": [{"text": "旧机器没有接通外部电源", "source": "用户确认", "confidence_status": "confirmed"}],
        },
    )
    alice = service.save_character_card(
        work_id,
        {
            "expected_version": canon["work"]["version"],
            "name": "爱丽丝",
            "voice_anchors": ["先确认眼前的情况。"],
            "ooc_constraints": ["不替其他人猜测动机"],
            "source_refs": ["用户确认"],
        },
    )
    kay = service.save_character_card(
        work_id,
        {
            "expected_version": alice["work"]["version"],
            "name": "凯伊",
            "voice_anchors": ["让我先看看日志。"],
            "ooc_constraints": ["不无端泄露未知事实"],
            "source_refs": ["用户确认"],
        },
    )
    stored = service.create_reference_file(
        work_id,
        {
            "expected_version": kay["work"]["version"],
            "title": "活动室观察笔记",
            "source_label": "用户导入",
            "content": "提示灯在零点后闪烁。",
        },
    )
    context = service.assemble_context(work_id, scene_id)
    assert context["readiness"]["real_ba_writing"] == "ready_for_provider"
    assert {card["name"] for card in context["runtime_character_cards"]} == {"爱丽丝", "凯伊"}
    assert len(context["source_revision_ids"]) >= 3
    assert context["reference_files"][0]["id"] == stored["reference_file_id"]
    assert context["reference_files"][0]["content"].startswith("提示灯")
    assert context["reference_file_refs"][0].startswith("reference:")

    restarted = WritingService(tmp_path).get_work(work_id)
    assert restarted["artifacts"]
    assert len(restarted["reference_files"]) == 1
    assert restarted["reference_files"][0]["content_hash"].startswith("sha256:")
    assert stored["reference_file_id"] == restarted["reference_files"][0]["id"]


def test_creative_bible_keeps_world_and_character_sources_across_restart(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    original = service.save_character_card(
        work_id,
        {
            "expected_version": work["version"],
            "card_id": "character-aris",
            "name": "爱丽丝",
            "canonical_name": "天童爱丽丝",
            "source_type": "official_reference",
            "voice_anchors": ["把眼前的异常当作任务确认。"],
            "relationships": [{"target": "凯伊", "kind": "队友", "summary": "共同调查异常。"}],
            "source_refs": ["官方剧情索引"],
        },
    )
    custom = service.save_character_card(
        work_id,
        {
            "expected_version": original["work"]["version"],
            "card_id": "character-kei",
            "name": "凯伊",
            "source_type": "custom",
            "voice_anchors": ["先确认日志。"],
            "source_refs": ["用户确认"],
        },
    )
    first_bible = service.save_world_bible(
        work_id,
        {
            "expected_version": custom["work"]["version"],
            "title": "本作世界观",
            "source_type": "mixed",
            "rules": [{"text": "旧游戏机只在零点后接收匿名指令。", "category": "技术", "source": "用户确认", "confidence_status": "confirmed"}],
            "timeline": [{"text": "异常提示灯第一次亮起。", "category": "当前剧情", "source": "第一章设定", "confidence_status": "confirmed"}],
        },
    )
    updated_bible = service.save_world_bible(
        work_id,
        {
            "expected_version": first_bible["work"]["version"],
            "title": "本作世界观",
            "source_type": "mixed",
            "rules": [{"text": "旧游戏机只在零点后接收匿名指令。", "category": "技术", "source": "用户确认", "confidence_status": "confirmed"}],
            "timeline": [
                {"text": "异常提示灯第一次亮起。", "category": "当前剧情", "source": "第一章设定", "confidence_status": "confirmed"},
                {"text": "匿名发件人身份本卷不公开。", "category": "伏笔", "source": "用户确认", "confidence_status": "confirmed"},
            ],
        },
    )
    restarted = WritingService(tmp_path)
    loaded = restarted.get_work(work_id)
    cards = {
        artifact["scope_id"]: artifact["current_revision"]["content"]
        for artifact in loaded["artifacts"]
        if artifact["kind"] == "character_card"
    }
    bible = next(artifact["current_revision"]["content"] for artifact in loaded["artifacts"] if artifact["kind"] == "world_bible")
    context = restarted.assemble_context(work_id, scene_id)
    assert cards["character-aris"]["source_type"] == "official_reference"
    assert cards["character-aris"]["relationships"][0]["target"] == "凯伊"
    assert cards["character-kei"]["source_type"] == "custom"
    assert bible["source_type"] == "mixed"
    assert len(bible["timeline"]) == 2
    assert updated_bible["revision_id"] == next(artifact["current_revision_id"] for artifact in loaded["artifacts"] if artifact["kind"] == "world_bible")
    assert context["world_bible"]["rules"][0]["text"].startswith("旧游戏机")


def test_character_card_history_and_archive_are_versioned_and_restart_safe(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "人物资料库验证"})
    work_id = work["id"]
    created = service.save_character_card(
        work_id,
        {
            "expected_version": work["version"],
            "card_id": "character-original",
            "name": "原创角色",
            "source_type": "custom",
            "voice_anchors": ["先确认现场。"],
            "source_refs": ["用户确认"],
        },
    )
    revised = service.save_character_card(
        work_id,
        {
            "expected_version": created["work"]["version"],
            "card_id": "character-original",
            "name": "原创角色",
            "source_type": "custom",
            "voice_anchors": ["先确认现场，再报告判断。"],
            "source_refs": ["用户确认"],
        },
    )
    archived = service.archive_character_card(
        work_id,
        "character-original",
        {"expected_version": revised["work"]["version"]},
    )
    loaded = WritingService(tmp_path).get_work(work_id)
    card = next(item for item in loaded["artifacts"] if item["kind"] == "character_card")
    assert card["scope_id"] == "character-original"
    assert [revision["ordinal"] for revision in card["revisions"]] == [3, 2, 1]
    assert card["current_revision"]["content"]["status"] == "archived"
    assert archived["revision_id"] == card["current_revision_id"]


def test_world_cards_keep_stable_identity_history_and_archive_out_of_context(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    created = service.save_world_bible(
        work_id,
        {
            "expected_version": work["version"],
            "title": "本作世界观",
            "source_type": "mixed",
            "entities": [{
                "id": "world-card-schaale",
                "name": "夏莱临时指挥室",
                "kind": "place",
                "summary": "本作中用于汇总异常记录的临时据点。",
                "aliases": ["临时指挥室"],
                "source": "official-corpus:scenario_7:42; 用户确认",
                "source_type": "mixed",
                "confidence_status": "confirmed",
                "participants": ["爱丽丝"],
            }],
        },
    )
    revised = service.save_world_bible(
        work_id,
        {
            "expected_version": created["work"]["version"],
            "title": "本作世界观",
            "source_type": "mixed",
            "entities": [{
                "id": "world-card-schaale",
                "name": "夏莱临时指挥室",
                "kind": "place",
                "summary": "本作中汇总异常记录并安排调查的临时据点。",
                "aliases": ["临时指挥室"],
                "source": "official-corpus:scenario_7:42; 用户确认",
                "source_type": "mixed",
                "confidence_status": "confirmed",
                "participants": ["爱丽丝", "凯伊"],
            }],
        },
    )
    archived = service.save_world_bible(
        work_id,
        {
            "expected_version": revised["work"]["version"],
            "title": "本作世界观",
            "source_type": "mixed",
            "entities": [{
                "id": "world-card-schaale",
                "name": "夏莱临时指挥室",
                "kind": "place",
                "summary": "本作中汇总异常记录并安排调查的临时据点。",
                "aliases": ["临时指挥室"],
                "source": "official-corpus:scenario_7:42; 用户确认",
                "source_type": "mixed",
                "confidence_status": "confirmed",
                "participants": ["爱丽丝", "凯伊"],
                "status": "archived",
            }],
        },
    )
    restarted = WritingService(tmp_path)
    loaded = restarted.get_work(work_id)
    artifact = next(item for item in loaded["artifacts"] if item["kind"] == "world_bible")
    entity = artifact["current_revision"]["content"]["entities"][0]
    context = restarted.assemble_context(work_id, scene_id)
    assert entity["id"] == "world-card-schaale"
    assert entity["status"] == "archived"
    assert [revision["ordinal"] for revision in artifact["revisions"]] == [3, 2, 1]
    assert archived["revision_id"] == artifact["current_revision_id"]
    assert context["world_bible"]["entities"] == []


def test_official_reference_search_and_import_is_work_owned_and_restart_safe(tmp_path):
    corpus = tmp_path / "official-corpus"
    corpus.mkdir()
    record = {
        "record_uid": "scenario_7:42",
        "source_file": "ScenarioScriptExcel_7.json",
        "source_row_index": 42,
        "primary_story_membership": {
            "category": "main_story",
            "character_name": "爱丽丝",
            "title": "前往夏莱",
        },
        "speakers": ["爱丽丝", "老师"],
        "text": {"zh_cn": "爱丽丝: 先确认夏莱的门锁。", "localization_status": "official_zh"},
    }
    (corpus / "scenario_7.jsonl").write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    service = WritingService(tmp_path / "data", official_corpus_dir=corpus)
    work = service.create_work({"title": "原作资料导入"})

    search = service.search_official_references("夏莱")
    assert search["catalog"]["available"] is True
    assert search["items"][0]["record_uid"] == "scenario_7:42"
    assert search["items"][0]["zh_cn"].startswith("爱丽丝")

    imported = service.import_official_reference(
        work["id"],
        {"expected_version": work["version"], "record_uid": "scenario_7:42"},
    )
    loaded = WritingService(tmp_path / "data", official_corpus_dir=corpus).get_work(work["id"])
    reference = loaded["reference_files"][0]
    assert reference["id"] == imported["reference_file_id"]
    assert reference["source_label"] == "official-corpus:scenario_7:42"
    assert reference["trust_status"] == "official_reference"
    assert (tmp_path / "data" / "references" / f"{reference['id']}.md").read_text(encoding="utf-8").find("不是自动确认") >= 0
    assert (corpus / "scenario_7.jsonl").read_text(encoding="utf-8") == json.dumps(record, ensure_ascii=False) + "\n"


def test_official_catalog_permission_error_is_reported_as_unavailable(tmp_path, monkeypatch):
    """Optional evidence must not make the writing service unavailable."""
    corpus = tmp_path / "official-corpus"
    corpus.mkdir()
    service = WritingService(tmp_path / "data", official_corpus_dir=corpus)

    def denied(_path):
        raise PermissionError("corpus access changed")

    monkeypatch.setattr(type(corpus), "is_dir", denied)

    capabilities = service.capabilities()
    assert capabilities["official_references"]["available"] is False


def test_blocking_scene_review_prevents_release(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(
        work_id,
        proposal_id,
        {"expected_version": work["version"], "text": "旁白: 作者已经安排好了这一幕。\n"},
    )
    review = service.review_scene(
        work_id, scene_id, {"expected_version": accepted["work"]["version"]}
    )
    assert any(item["kind"] == "meta_boundary" and item["severity"] == "blocking" for item in review["findings"])
    with pytest.raises(DomainError) as error:
        service.freeze_release(work_id, {"expected_version": review["work"]["version"]})
    assert error.value.code == "release_blocked"
    assert error.value.details["finding_ids"]


def test_release_review_must_cover_current_scene_revisions(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(work_id, proposal_id, {"expected_version": work["version"]})
    reviewed = service.review_scene(work_id, scene_id, {"expected_version": accepted["work"]["version"]})
    release_review = service.review_release(work_id, {"expected_version": reviewed["work"]["version"]})
    assert release_review["status"] == "passed"
    next_candidate = service.generate_scene_candidate(work_id, scene_id, {"expected_version": release_review["work"]["version"]})
    accepted_next = service.accept_proposal(work_id, next_candidate["proposal_id"], {"expected_version": next_candidate["work"]["version"]})
    with pytest.raises(DomainError) as error:
        service.freeze_release(work_id, {"expected_version": accepted_next["work"]["version"]})
    assert error.value.code == "release_blocked"
    assert error.value.details["reason"] == "release_review_not_current"


def test_release_review_accepts_clean_scene_review_without_findings(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(
        work_id,
        proposal_id,
        {"expected_version": work["version"], "text": "旁白: 灯光在桌面上安静下来。\n"},
    )
    scene_review = service.review_scene(work_id, scene_id, {"expected_version": accepted["work"]["version"]})
    assert scene_review["findings"] == []
    release_review = service.review_release(work_id, {"expected_version": scene_review["work"]["version"]})
    assert release_review["status"] == "passed"


def test_empty_work_cannot_pass_release_review(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "空作品审查"})
    review = service.review_release(work["id"], {"expected_version": work["version"]})
    assert review["status"] == "blocked"
    assert review["snapshot"]["no_scenes"] is True


def test_resolving_finding_requires_a_reason_and_is_audited(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    accepted = service.accept_proposal(
        work_id, proposal_id, {"expected_version": work["version"], "text": "旁白: 作者走进活动室。\n"}
    )
    review = service.review_scene(work_id, scene_id, {"expected_version": accepted["work"]["version"]})
    finding_id = review["findings"][0]["id"]
    with pytest.raises(DomainError) as error:
        service.resolve_review_finding(work_id, finding_id, {"expected_version": review["work"]["version"], "note": ""})
    assert error.value.code == "validation_error"
    resolved = service.resolve_review_finding(
        work_id, finding_id, {"expected_version": review["work"]["version"], "note": "已在下一稿中安排修改"}
    )
    assert resolved["work"]["version"] == review["work"]["version"] + 1
    with service.repo.connect() as connection:
        finding = connection.execute("SELECT status FROM review_findings WHERE id=?", (finding_id,)).fetchone()
        decision = connection.execute("SELECT decision,note FROM decisions WHERE target_id=?", (finding_id,)).fetchone()
    assert finding["status"] == "resolved"
    assert tuple(decision) == ("resolved", "已在下一稿中安排修改")


def test_release_scene_order_follows_chapter_order_not_identifier(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "顺序测试"})
    result = service.save_brief(work["id"], {"expected_version": work["version"], "idea": "测试", "mode": "bond_short"})
    result = service.generate_blueprint(work["id"], {"expected_version": result["work"]["version"]})
    first = service.create_chapter(work["id"], {"expected_version": result["work"]["version"], "title": "第一章"})
    second = service.create_chapter(work["id"], {"expected_version": first["work"]["version"], "title": "第二章"})
    one = service.create_scene(work["id"], first["chapter_id"], {"expected_version": second["work"]["version"], "title": "先发生"})
    two = service.create_scene(work["id"], second["chapter_id"], {"expected_version": one["work"]["version"], "title": "后发生"})
    for scene_id, text, version in [(one["scene_id"], "旁白: 一。\n", two["work"]["version"]), (two["scene_id"], "旁白: 二。\n", None)]:
        current = service.get_work(work["id"])
        with service.repo.transaction() as connection:
            artifact = service._artifact(connection, work["id"], "scene_script", "scene", scene_id)
            revision_id = service._add_revision(connection, artifact, {"text": text}, "user", {"test": True})
            connection.execute("UPDATE scenes SET current_revision_id=?, status='review' WHERE id=?", (revision_id, scene_id))
            service._bump_work(connection, work["id"], current["version"])
        reviewed = service.review_scene(work["id"], scene_id, {"expected_version": service.get_work(work["id"])["version"]})
    release_review = service.review_release(work["id"], {"expected_version": reviewed["work"]["version"]})
    release = service.freeze_release(work["id"], {"expected_version": release_review["work"]["version"]})
    assert [scene["title"] for scene in release["manifest"]["scenes"]] == ["先发生", "后发生"]


def test_structure_reorder_keeps_scene_identity_manuscript_and_requires_current_release_review(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "章节调整"})
    brief = service.save_brief(
        work["id"], {"expected_version": work["version"], "idea": "调整故事结构", "mode": "bond_short"}
    )
    blueprint = service.generate_blueprint(
        work["id"], {"expected_version": brief["work"]["version"]}
    )
    first = service.create_chapter(work["id"], {"expected_version": blueprint["work"]["version"], "title": "第一章"})
    second = service.create_chapter(work["id"], {"expected_version": first["work"]["version"], "title": "第二章"})
    one = service.create_scene(work["id"], first["chapter_id"], {"expected_version": second["work"]["version"], "title": "先发生", "goal": "建立线索"})
    two = service.create_scene(work["id"], second["chapter_id"], {"expected_version": one["work"]["version"], "title": "后发生", "goal": "确认线索"})
    first_candidate = service.generate_scene_candidate(work["id"], one["scene_id"], {"expected_version": two["work"]["version"]})
    first_accepted = service.accept_proposal(work["id"], first_candidate["proposal_id"], {"expected_version": first_candidate["work"]["version"], "text": "旁白: 先发生。\n"})
    second_candidate = service.generate_scene_candidate(work["id"], two["scene_id"], {"expected_version": first_accepted["work"]["version"]})
    second_accepted = service.accept_proposal(work["id"], second_candidate["proposal_id"], {"expected_version": second_candidate["work"]["version"], "text": "旁白: 后发生。\n"})
    reviewed_one = service.review_scene(work["id"], one["scene_id"], {"expected_version": second_accepted["work"]["version"]})
    reviewed_two = service.review_scene(work["id"], two["scene_id"], {"expected_version": reviewed_one["work"]["version"]})
    release_review = service.review_release(work["id"], {"expected_version": reviewed_two["work"]["version"]})

    reordered = service.reorder_structure(
        work["id"],
        {
            "expected_version": release_review["work"]["version"],
            "chapter_ids": [second["chapter_id"], first["chapter_id"]],
            "scene_placements": [
                {"scene_id": two["scene_id"], "chapter_id": second["chapter_id"]},
                {"scene_id": one["scene_id"], "chapter_id": first["chapter_id"]},
            ],
        },
    )
    assert reordered["changed"] is True
    restored = WritingService(tmp_path).get_work(work["id"])
    assert [chapter["id"] for chapter in restored["chapters"]] == [second["chapter_id"], first["chapter_id"]]
    ordered_scenes = [scene for chapter in restored["chapters"] for scene in chapter["scenes"]]
    assert [scene["id"] for scene in ordered_scenes] == [two["scene_id"], one["scene_id"]]
    assert [scene["current_revision_id"] for scene in ordered_scenes] == [second_accepted["revision_id"], first_accepted["revision_id"]]

    with pytest.raises(DomainError) as error:
        service.freeze_release(work["id"], {"expected_version": reordered["work"]["version"]})
    assert error.value.code == "release_blocked"
    assert error.value.details["reason"] == "release_review_not_current"


def test_structure_reorder_rejects_missing_and_external_ids(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "结构校验"})
    brief = service.save_brief(
        work["id"],
        {"expected_version": work["version"], "idea": "整理一个小事件", "mode": "bond_short"},
    )
    blueprint = service.generate_blueprint(work["id"], {"expected_version": brief["work"]["version"]})
    chapter = service.create_chapter(work["id"], {"expected_version": blueprint["work"]["version"], "title": "第一章"})
    scene = service.create_scene(work["id"], chapter["chapter_id"], {"expected_version": chapter["work"]["version"], "title": "场景", "goal": "有变化"})
    with pytest.raises(DomainError) as missing:
        service.reorder_structure(work["id"], {"expected_version": scene["work"]["version"], "chapter_ids": [], "scene_placements": []})
    assert missing.value.code == "invalid_structure_order"
    with pytest.raises(DomainError) as external:
        service.reorder_structure(
            work["id"],
            {
                "expected_version": scene["work"]["version"],
                "chapter_ids": [chapter["chapter_id"]],
                "scene_placements": [{"scene_id": scene["scene_id"], "chapter_id": "chapter-outside"}],
            },
        )
    assert external.value.code == "invalid_structure_order"


def test_intent_proposal_confirmation_and_scene_mode_are_durable(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "混合作品", "world_seed": "ba_starter"})
    card = service.save_character_card(
        work["id"],
        {
            "expected_version": work["version"],
            "card_id": "character-aris",
            "name": "爱丽丝",
            "source_type": "official_reference",
            "trust_status": "confirmed",
            "source_refs": ["用户核对"],
        },
    )
    intent = service.save_brief(
        work["id"],
        {
            "expected_version": card["work"]["version"],
            "idea": "爱丽丝与凯伊先查线索，随后进入行动。",
            "intent_only": True,
        },
    )
    assert next(item for item in intent["work"]["artifacts"] if item["kind"] == "brief")["current_revision"]["content"]["status"] == "analysis_pending"
    proposed = service.generate_blueprint(work["id"], {"expected_version": intent["work"]["version"]})
    proposal = next(item for item in proposed["work"]["artifacts"] if item["kind"] == "story_blueprint")["current_revision"]["content"]
    assert proposal["status"] == "proposed"
    with pytest.raises(DomainError) as blocked:
        service.create_chapter(work["id"], {"expected_version": proposed["work"]["version"], "title": "第一章"})
    assert blocked.value.code == "blueprint_unconfirmed"

    confirmed = service.confirm_blueprint(
        work["id"],
        {
            "expected_version": proposed["work"]["version"],
            "mode": "bond_short",
            "character_card_ids": ["character-aris"],
            "sensei_presence": "auto",
        },
    )
    chapter = service.create_chapter(work["id"], {"expected_version": confirmed["work"]["version"], "title": "第一章"})
    scene = service.create_scene(
        work["id"],
        chapter["chapter_id"],
        {
            "expected_version": chapter["work"]["version"],
            "title": "前半场",
            "goal": "先确认线索",
            "writing_mode": "long_comedy",
        },
    )
    context = service.assemble_context(work["id"], scene["scene_id"])
    assert context["scene_contract"]["writing_mode"] == "long_comedy"
    assert context["rules"]["mode_key"] == "long_comedy"
    assert context["rules"]["mode"] == MODE_SOURCES["long_comedy"]

    restarted = WritingService(tmp_path)
    restored = restarted.get_work(work["id"])
    assert restored["chapters"][0]["scenes"][0]["contract"]["writing_mode"] == "long_comedy"
    with pytest.raises(DomainError) as invalid:
        restarted.update_scene_contract(
            work["id"],
            scene["scene_id"],
            {
                "expected_version": restored["version"],
                "title": "前半场",
                "goal": "先确认线索",
                "stop_boundary": "线索确认后停止",
                "writing_mode": "whole_work_everything",
            },
        )
    assert invalid.value.code == "validation_error"


def test_ba_agent_requires_runtime_character_cards(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    with pytest.raises(DomainError) as error:
        service.run_scene_agent(
            work_id, scene_id, {"expected_version": work["version"], "instruction": "起草本场"}
        )
    assert error.value.code == "agent_blocked"
    with service.repo.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == 0


def test_ba_agent_creates_audited_single_proposal(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, initial_proposal, work = build_to_proposal(service)
    rejected = service.reject_proposal(work_id, initial_proposal, {"expected_version": work["version"], "note": "改由 BA Agent 起草"})
    alice = service.save_character_card(
        work_id,
            {"expected_version": rejected["work"]["version"], "name": "爱丽丝", "voice_anchors": ["先确认。"], "source_refs": ["测试来源"]},
    )
    kay = service.save_character_card(
        work_id,
        {"expected_version": alice["work"]["version"], "name": "凯伊", "voice_anchors": ["我先看日志。"], "source_refs": ["测试来源"]},
    )
    result = service.run_scene_agent(
        work_id, scene_id, {"expected_version": kay["work"]["version"], "instruction": "让两人先处理眼前的提示灯。"}
    )
    assert result["simulation"] is True
    run = next(item for item in result["work"]["agent_runs"] if item["id"] == result["agent_run_id"])
    assert run["status"] == "waiting_user"
    assert run["proposal_id"] == result["proposal_id"]
    assert run["policy"]["write_policy"] == "one_candidate_zero_edit_proposal_only"
    assert [call["tool_name"] for call in run["tool_calls"]] == ["assemble_scene_context", "validate_runtime_character_cards", "generate_single_proposal"]
    with pytest.raises(DomainError) as error:
        service.run_scene_agent(
            work_id, scene_id, {"expected_version": result["work"]["version"], "instruction": "再写一份"}
        )
    assert error.value.code == "agent_waiting_user"


def test_explicit_scene_context_selection_is_durable_and_limits_runtime_inputs(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    rejected = service.reject_proposal(work_id, proposal_id, {"expected_version": work["version"], "note": "准备指定本场资料"})
    first = service.save_character_card(
        work_id,
        {"expected_version": rejected["work"]["version"], "card_id": "character-aris", "name": "爱丽丝", "source_refs": ["用户确认"]},
    )
    second = service.save_character_card(
        work_id,
        {"expected_version": first["work"]["version"], "card_id": "character-kei", "name": "凯伊", "source_refs": ["用户确认"]},
    )
    world = service.save_world_bible(
        work_id,
        {
            "expected_version": second["work"]["version"],
            "title": "场景世界观",
            "source_type": "custom",
            "entities": [
                {"id": "world-room", "name": "活动室", "kind": "place", "source": "用户确认", "confidence_status": "confirmed"},
                {"id": "world-lab", "name": "实验室", "kind": "place", "source": "用户确认", "confidence_status": "confirmed"},
            ],
        },
    )
    first_ref = service.create_reference_file(
        work_id,
        {"expected_version": world["work"]["version"], "title": "活动室笔记", "source_label": "用户导入", "content": "活动室只在夜间开放。"},
    )
    second_ref = service.create_reference_file(
        work_id,
        {"expected_version": first_ref["work"]["version"], "title": "实验室笔记", "source_label": "用户导入", "content": "实验室不在本场。"},
    )
    configured = service.configure_scene_context(
        work_id,
        scene_id,
        {
            "expected_version": second_ref["work"]["version"],
            "character_card_ids": ["character-aris"],
            "world_item_ids": ["world-room"],
            "reference_file_ids": [first_ref["reference_file_id"]],
        },
    )
    context = service.assemble_context(work_id, scene_id)
    assert context["context_selection"]["mode"] == "explicit"
    assert [item["name"] for item in context["runtime_character_cards"]] == ["爱丽丝"]
    assert [item["name"] for item in context["world_bible"]["entities"]] == ["活动室"]
    assert [item["title"] for item in context["reference_files"]] == ["活动室笔记"]

    restarted = WritingService(tmp_path)
    restored = restarted.assemble_context(work_id, scene_id)
    assert restored["context_selection"] == configured["context_selection"]
    candidate = restarted.generate_scene_candidate(
        work_id, scene_id, {"expected_version": configured["work"]["version"]}
    )
    proposal = next(item for item in candidate["work"]["proposals"] if item["id"] == candidate["proposal_id"])
    assert "凯伊:" not in proposal["candidate"]
    assert "老师:" not in proposal["candidate"]

    rejected_candidate = restarted.reject_proposal(
        work_id,
        candidate["proposal_id"],
        {"expected_version": candidate["work"]["version"], "note": "转为 Agent 输入快照检查"},
    )
    agent_ready = restarted.run_scene_agent(
        work_id,
        scene_id,
        {"expected_version": rejected_candidate["work"]["version"], "instruction": "检查本场范围"},
    )
    snapshot_path = tmp_path / "agent-runs" / agent_ready["agent_run_id"] / "input.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert [card["name"] for card in snapshot["runtime_character_cards"]] == ["爱丽丝"]
    assert [item["name"] for item in snapshot["world_bible"]["entities"]] == ["活动室"]
    assert [item["title"] for item in snapshot["reference_files"]] == ["活动室笔记"]


def test_scene_context_selection_rejects_unconfirmed_or_stale_inputs(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    open_card = service.save_character_card(
        work_id,
        {"expected_version": work["version"], "card_id": "character-open", "name": "爱丽丝", "source_refs": ["待核对"], "trust_status": "open"},
    )
    with pytest.raises(DomainError) as unconfirmed:
        service.configure_scene_context(
            work_id,
            scene_id,
            {"expected_version": open_card["work"]["version"], "character_card_ids": ["character-open"], "world_item_ids": [], "reference_file_ids": []},
        )
    assert unconfirmed.value.code == "invalid_context_selection"

    confirmed = service.save_character_card(
        work_id,
        {"expected_version": open_card["work"]["version"], "card_id": "character-open", "name": "爱丽丝", "source_refs": ["用户确认"], "trust_status": "confirmed"},
    )
    with pytest.raises(RevisionConflict):
        service.configure_scene_context(
            work_id,
            scene_id,
            {"expected_version": open_card["work"]["version"], "character_card_ids": ["character-open"], "world_item_ids": [], "reference_file_ids": []},
        )
    configured = service.configure_scene_context(
        work_id,
        scene_id,
        {"expected_version": confirmed["work"]["version"], "character_card_ids": ["character-open"], "world_item_ids": [], "reference_file_ids": []},
    )
    assert configured["work"]["version"] == confirmed["work"]["version"] + 1


def test_scene_contract_is_durable_invalidates_pending_proposal_and_keeps_context_selection(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, proposal_id, work = build_to_proposal(service)
    rejected = service.reject_proposal(work_id, proposal_id, {"expected_version": work["version"], "note": "先固定资料"})
    card = service.save_character_card(
        work_id,
        {"expected_version": rejected["work"]["version"], "card_id": "character-aris", "name": "爱丽丝", "source_refs": ["用户确认"]},
    )
    configured = service.configure_scene_context(
        work_id,
        scene_id,
        {"expected_version": card["work"]["version"], "character_card_ids": ["character-aris"], "world_item_ids": [], "reference_file_ids": []},
    )
    candidate = service.generate_scene_candidate(work_id, scene_id, {"expected_version": configured["work"]["version"]})
    updated = service.update_scene_contract(
        work_id,
        scene_id,
        {
            "expected_version": candidate["work"]["version"],
            "title": "校订后的场景",
            "location": "夏莱临时指挥室",
            "goal": "确认机器并非普通故障。",
            "known_facts": ["机器没有接通外部电源。"],
            "forbidden_reveals": ["匿名发件人的身份。"],
            "stop_boundary": "确认异常来源后停止。",
        },
    )
    assert candidate["proposal_id"] in updated["superseded_proposal_ids"]
    scene = next(scene for scene in updated["work"]["chapters"][0]["scenes"] if scene["id"] == scene_id)
    assert scene["title"] == "校订后的场景"
    assert scene["contract"]["context_selection"]["character_card_ids"] == ["character-aris"]
    assert scene["contract"]["forbidden_reveals"] == ["匿名发件人的身份。"]
    superseded = next(item for item in updated["work"]["proposals"] if item["id"] == candidate["proposal_id"])
    assert superseded["status"] == "superseded"
    with pytest.raises(DomainError) as error:
        service.accept_proposal(work_id, candidate["proposal_id"], {"expected_version": updated["work"]["version"]})
    assert error.value.code == "proposal_not_pending"
    context = WritingService(tmp_path).assemble_context(work_id, scene_id)
    assert context["scene_contract"]["location"] == "夏莱临时指挥室"
    assert context["scene_contract"]["known_facts"] == ["机器没有接通外部电源。"]


def test_scene_contract_rejects_stale_work_version(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    service.create_reference_file(
        work_id,
        {"expected_version": work["version"], "title": "变更", "source_label": "用户", "content": "内容"},
    )
    with pytest.raises(RevisionConflict):
        service.update_scene_contract(
            work_id,
            scene_id,
            {"expected_version": work["version"], "title": "过期", "goal": "目标", "stop_boundary": "停止"},
        )


def test_open_character_card_is_durable_but_cannot_unlock_agent_context(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    draft = service.save_character_card(
        work_id,
        {
            "expected_version": work["version"],
            "card_id": "character-aris",
            "name": "爱丽丝",
            "source_type": "official_reference",
            "trust_status": "open",
            "source_refs": ["official-corpus:scenario_7:42"],
        },
    )
    context = service.assemble_context(work_id, scene_id)
    assert "爱丽丝" in context["readiness"]["missing_runtime_character_cards"]
    assert context["readiness"]["unverified_character_cards"] == {"爱丽丝": "open"}
    assert context["runtime_character_cards"] == []

    confirmed = service.save_character_card(
        work_id,
        {
            "expected_version": draft["work"]["version"],
            "card_id": "character-aris",
            "name": "爱丽丝",
            "source_type": "official_reference",
            "trust_status": "confirmed",
            "voice_anchors": ["先确认眼前的情况。"],
            "source_refs": ["official-corpus:scenario_7:42", "用户核对"],
        },
    )
    restarted = WritingService(tmp_path)
    context = restarted.assemble_context(work_id, scene_id)
    card_artifact = next(item for item in confirmed["work"]["artifacts"] if item["scope_id"] == "character-aris")
    assert card_artifact["scope_id"] == "character-aris"
    assert [revision["ordinal"] for revision in card_artifact["revisions"]] == [2, 1]
    assert [card["name"] for card in context["runtime_character_cards"]] == ["爱丽丝"]


def test_work_canon_identity_history_and_context_trust_survive_restart(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    created = service.save_work_canon(
        work_id,
        {
            "expected_version": work["version"],
            "facts": [
                {"id": "fact-power", "text": "旧机器没有接通电源。", "source": "用户确认", "confidence_status": "confirmed", "scope": "work"},
                {"id": "fact-sender", "text": "发件人可能来自夏莱。", "source": "剧情推断", "confidence_status": "inferred", "scope": "chapter"},
            ],
        },
    )
    revised = service.save_work_canon(
        work_id,
        {
            "expected_version": created["work"]["version"],
            "facts": [
                {"id": "fact-power", "text": "旧机器仍未接通外部电源。", "source": "用户确认", "confidence_status": "confirmed", "scope": "work"},
                {"id": "fact-sender", "text": "发件人可能来自夏莱。", "source": "剧情推断", "confidence_status": "inferred", "scope": "chapter"},
            ],
        },
    )
    service.save_work_canon(
        work_id,
        {
            "expected_version": revised["work"]["version"],
            "facts": [
                {"id": "fact-power", "text": "旧机器仍未接通外部电源。", "source": "用户确认", "confidence_status": "confirmed", "scope": "work", "status": "archived"},
                {"id": "fact-sender", "text": "发件人可能来自夏莱。", "source": "剧情推断", "confidence_status": "inferred", "scope": "chapter"},
            ],
        },
    )
    restarted = WritingService(tmp_path)
    loaded = restarted.get_work(work_id)
    artifact = next(item for item in loaded["artifacts"] if item["kind"] == "work_canon")
    context = restarted.assemble_context(work_id, scene_id)
    assert [revision["ordinal"] for revision in artifact["revisions"]] == [3, 2, 1]
    assert {fact["id"] for fact in artifact["current_revision"]["content"]["facts"]} == {"fact-power", "fact-sender"}
    assert context["work_canon"]["facts"] == []


def test_unconfirmed_world_card_stays_in_library_but_out_of_scene_context(tmp_path):
    service = WritingService(tmp_path)
    work_id, scene_id, _, work = build_to_proposal(service)
    saved = service.save_world_bible(
        work_id,
        {
            "expected_version": work["version"],
            "title": "本作世界观",
            "source_type": "official_reference",
            "entities": [{
                "id": "world-card-schaale",
                "name": "夏莱",
                "kind": "organization",
                "source": "official-corpus:scenario_7:42",
                "source_type": "official_reference",
                "confidence_status": "open",
            }],
        },
    )
    context = WritingService(tmp_path).assemble_context(work_id, scene_id)
    artifact = next(item for item in saved["work"]["artifacts"] if item["kind"] == "world_bible")
    assert artifact["current_revision"]["content"]["entities"][0]["id"] == "world-card-schaale"
    assert context["world_bible"]["entities"] == []
    assert context["readiness"]["unverified_world_items"][0]["id"] == "world-card-schaale"


def test_ba_world_starter_is_work_owned_open_and_restart_safe(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "世界观起始架构"})

    applied = service.apply_ba_world_starter(
        work["id"], {"expected_version": work["version"]}
    )
    bible = next(
        item for item in applied["work"]["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    assert bible["source_type"] == "ba_starter"
    assert {
        "ba-starter-kivotos",
        "ba-starter-schale",
        "ba-starter-general-student-council",
        "ba-starter-academy-network",
    }.issubset({item["id"] for item in bible["entities"]})
    assert len(bible["entities"]) >= 10
    assert {item["confidence_status"] for item in bible["entities"]} == {"open"}

    restarted = WritingService(tmp_path)
    loaded = restarted.get_work(work["id"])
    saved = next(item for item in loaded["artifacts"] if item["kind"] == "world_bible")
    assert saved["current_revision"]["content"] == bible

    with pytest.raises(DomainError) as error:
        restarted.apply_ba_world_starter(
            work["id"], {"expected_version": loaded["version"]}
        )
    assert error.value.code == "world_starter_already_applied"


def test_ba_world_starter_merges_into_existing_custom_world_without_overwrite(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "混合世界观"})
    custom = service.save_world_bible(
        work["id"],
        {
            "expected_version": work["version"],
            "title": "我的自定义世界",
            "source_type": "custom",
            "entities": [{
                "id": "world-custom-lab",
                "name": "地下实验室",
                "kind": "place",
                "summary": "本作原创地点。",
                "source": "用户确认",
                "source_type": "custom",
                "confidence_status": "confirmed",
            }],
        },
    )
    applied = service.apply_ba_world_starter(
        work["id"], {"expected_version": custom["work"]["version"]}
    )
    bible = next(
        item for item in applied["work"]["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    assert bible["title"] == "我的自定义世界"
    assert bible["source_type"] == "mixed"
    assert [item["id"] for item in bible["entities"]][:1] == ["world-custom-lab"]
    assert len(bible["entities"]) >= 11

    revised = service.save_world_bible(
        work["id"],
        {
            "expected_version": applied["work"]["version"],
            **bible,
            "entities": [
                {**item, "confidence_status": "confirmed"}
                if item["id"] == "ba-starter-kivotos" else item
                for item in bible["entities"]
            ],
        },
    )
    updated = next(
        item for item in revised["work"]["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    assert updated["source_type"] == "mixed"


def test_work_can_start_with_a_versioned_ba_world_library(tmp_path):
    service = WritingService(tmp_path)

    work = service.create_work({"title": "从 BA 底稿开始", "world_seed": "ba_starter"})
    bible = next(
        item for item in work["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]

    assert bible["source_type"] == "ba_starter"
    assert len(bible["entities"]) >= 10
    assert {item["confidence_status"] for item in bible["entities"]} == {"open"}
    assert all(item["status"] == "active" for item in bible["entities"])
    assert all(item["source_type"] == "ba_starter" for item in bible["entities"])

    restarted = WritingService(tmp_path)
    restored = next(
        item for item in restarted.get_work(work["id"])["artifacts"]
        if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    assert restored == bible


def test_ba_starter_and_custom_world_remain_distinguishable_after_a_card_revision(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "混合底稿", "world_seed": "ba_starter"})
    bible = next(
        item for item in work["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]

    updated = service.save_world_bible(
        work["id"],
        {
            "expected_version": work["version"],
            **bible,
            "entities": [
                *bible["entities"],
                {
                    "id": "world-custom-clubroom",
                    "name": "旧社团活动室",
                    "kind": "place",
                    "summary": "本作原创场景地点。",
                    "source": "用户确认",
                    "source_type": "custom",
                    "confidence_status": "confirmed",
                },
            ],
        },
    )
    saved = next(
        item for item in updated["work"]["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]

    assert saved["source_type"] == "mixed"
    assert next(item for item in saved["entities"] if item["id"] == "ba-starter-kivotos")["source_type"] == "ba_starter"
    assert next(item for item in saved["entities"] if item["id"] == "world-custom-clubroom")["source_type"] == "custom"


def test_ba_starter_card_can_be_confirmed_without_losing_its_provenance(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "确认 BA 底稿", "world_seed": "ba_starter"})
    bible = next(
        item for item in work["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    updated_entities = [
        {**item, "confidence_status": "confirmed"}
        if item["id"] == "ba-starter-kivotos" else item
        for item in bible["entities"]
    ]

    saved = service.save_world_bible(
        work["id"],
        {
            "expected_version": work["version"],
            **bible,
            "entities": updated_entities,
        },
    )
    persisted = next(
        item for item in saved["work"]["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    kivotos = next(item for item in persisted["entities"] if item["id"] == "ba-starter-kivotos")

    assert persisted["source_type"] == "ba_starter"
    assert kivotos["confidence_status"] == "confirmed"
    assert kivotos["source_type"] == "ba_starter"
    assert WritingService(tmp_path).get_work(work["id"])["version"] == saved["work"]["version"]


def test_world_card_links_are_versioned_validated_and_restored(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "世界关系", "world_seed": "ba_starter"})
    bible = next(
        item for item in work["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]

    linked = service.save_world_bible(
        work["id"],
        {
            "expected_version": work["version"],
            **bible,
            "entities": [
                {
                    **item,
                    "related_world_ids": ["ba-starter-schale"],
                }
                if item["id"] == "ba-starter-kivotos" else item
                for item in bible["entities"]
            ],
        },
    )
    restarted = WritingService(tmp_path).get_work(work["id"])
    restored = next(
        item for item in restarted["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["content"]
    kivotos = next(item for item in restored["entities"] if item["id"] == "ba-starter-kivotos")
    assert kivotos["related_world_ids"] == ["ba-starter-schale"]
    assert linked["revision_id"] != next(
        item for item in work["artifacts"] if item["kind"] == "world_bible"
    )["current_revision"]["id"]

    with pytest.raises(DomainError) as error:
        service.save_world_bible(
            work["id"],
            {
                "expected_version": linked["work"]["version"],
                **restored,
                "entities": [
                    {**item, "related_world_ids": ["world-missing"]}
                    if item["id"] == "ba-starter-kivotos" else item
                    for item in restored["entities"]
                ],
            },
        )
    assert error.value.details["field"] == "related_world_ids"


def test_library_rejects_invalid_trust_scope_and_status(tmp_path):
    service = WritingService(tmp_path)
    work = service.create_work({"title": "资料校验"})
    with pytest.raises(DomainError) as character_error:
        service.save_character_card(work["id"], {"expected_version": work["version"], "name": "角色", "source_refs": ["用户"], "trust_status": "trusted"})
    assert character_error.value.details == {"field": "trust_status"}
    with pytest.raises(DomainError) as canon_error:
        service.save_work_canon(work["id"], {"expected_version": work["version"], "facts": [{"text": "事实", "source": "用户", "scope": "global"}]})
    assert canon_error.value.code == "validation_error"
    with pytest.raises(DomainError) as world_error:
        service.save_world_bible(work["id"], {"expected_version": work["version"], "rules": [{"text": "规则", "source": "用户", "status": "deleted"}]})
    assert world_error.value.code == "validation_error"
