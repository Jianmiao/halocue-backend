from __future__ import annotations

PACK_VERSION = "ba-writing.productized/1.0.0"
RULE_SOURCE = "ba-writing"

MODE_SOURCES = {
    "main_battle": "knowledge/modes/主线与战斗.md",
    "long_comedy": "knowledge/modes/长篇喜剧.md",
    "bond_short": "knowledge/modes/羁绊短场景.md",
    "text_reading": "knowledge/modes/小说化阅读.md",
}

COMMON_RULES = [
    "agents/writer.md",
    "knowledge/写作内核.md",
    "knowledge/人味对话机制.md",
]

TEMPLATES = [
    {
        "id": "brief.build",
        "version": "1.0.0",
        "execution": "user_confirmed",
        "inputs": ["idea", "mode", "characters", "target_length", "constraints"],
        "outputs": ["brief_revision"],
        "checks": ["idea_present", "single_mode_selected"],
    },
    {
        "id": "canon.assemble",
        "version": "1.0.0",
        "execution": "proposal_then_confirm",
        "inputs": ["brief_revision", "official_evidence_refs", "user_facts"],
        "outputs": ["work_canon_proposal"],
        "checks": ["every_fact_has_source", "confidence_status_present"],
    },
    {
        "id": "character.prepare",
        "version": "1.0.0",
        "execution": "automatic_then_confirm_missing",
        "inputs": ["scene_contract_revision", "character_card_revisions"],
        "outputs": ["runtime_character_cards"],
        "checks": ["voice_evidence_scoped", "ooc_constraints_present", "sensei_is_special_role"],
    },
    {
        "id": "blueprint.generate",
        "version": "1.0.0",
        "execution": "proposal_then_confirm",
        "inputs": ["brief_revision", "canon_revision_optional"],
        "outputs": ["story_blueprint_proposal"],
        "checks": ["conflict_present", "direction_has_scope", "no_unconfirmed_fact_promoted"],
    },
    {
        "id": "structure.plan",
        "version": "1.0.0",
        "execution": "proposal_then_confirm",
        "inputs": ["story_blueprint_revision", "volume_chapter_scene_tree"],
        "outputs": ["structure_proposal"],
        "checks": ["stable_ids", "chapter_scope_present", "scene_goal_present"],
    },
    {
        "id": "chapter.plan",
        "version": "1.0.0",
        "execution": "proposal_then_confirm",
        "inputs": ["story_blueprint_revision", "writing_target_revision", "chapter_tree", "prior_scene_revisions_optional"],
        "outputs": ["chapter_plan_proposal"],
        "checks": ["chapter_scope_pinned", "chapter_goal_present", "beats_ordered", "no_global_blueprint_replacement"],
    },
    {
        "id": "scene.context.assemble",
        "version": "1.0.0",
        "execution": "automatic",
        "inputs": ["scene_contract_revision", "brief_revision", "blueprint_revision", "canon_revision_optional", "runtime_character_cards"],
        "outputs": ["scene_context_snapshot"],
        "checks": ["one_mode_only", "stable_scene_id", "sources_pinned", "character_cards_complete"],
    },
    {
        "id": "scene.draft.generate",
        "version": "1.0.0",
        "execution": "automatic_proposal_only",
        "inputs": ["scene_context_snapshot", "provider_config"],
        "outputs": ["script_candidate", "proposal", "job_attempt"],
        "checks": ["context_ready", "provider_disclosed", "no_direct_writeback"],
    },
    {
        "id": "scene.draft.rewrite",
        "version": "1.0.0",
        "execution": "automatic_proposal_only",
        "inputs": ["scene_context_snapshot", "pinned_scene_revision", "rewrite_instruction", "provider_config"],
        "outputs": ["script_candidate", "proposal", "job_attempt"],
        "checks": ["base_revision_pinned", "context_ready", "provider_disclosed", "no_direct_writeback"],
    },
    {
        "id": "scene.review",
        "version": "1.0.0",
        "execution": "automatic_findings_user_decides",
        "inputs": ["script_candidate", "scene_contract_revision", "runtime_character_cards"],
        "outputs": ["review_findings", "gate_snapshot"],
        "checks": ["continuity", "character_voice", "ba_style", "format", "stop_boundary"],
    },
    {
        "id": "continuity.review",
        "version": "1.0.0",
        "execution": "automatic_findings_user_decides",
        "inputs": ["script_revisions", "confirmed_memories", "open_threads"],
        "outputs": ["review_findings"],
        "checks": ["knowledge_order", "location_state", "relationship_state", "foreshadowing"],
    },
    {
        "id": "release.review",
        "version": "1.0.0",
        "execution": "automatic_gate_then_user_freeze",
        "inputs": ["scene_revision_ids", "canon_revision_optional"],
        "outputs": ["gate_snapshot", "script_release"],
        "checks": ["all_scenes_have_text", "no_blocking_findings", "sources_are_current", "release_is_immutable"],
    },
]


def template_contract(template_id: str) -> dict:
    """Return the versioned runtime contract for one workflow step."""
    for template in TEMPLATES:
        if template["id"] == template_id:
            return {**template, "pack": PACK_VERSION, "rule_source": RULE_SOURCE}
    raise KeyError(template_id)


def describe_pack():
    return {
        "id": "ba-writing",
        "version": PACK_VERSION,
        "rule_source": RULE_SOURCE,
        "runtime_contract": {
            "common_rules": COMMON_RULES,
            "mode_sources": MODE_SOURCES,
            "requires_runtime_character_cards": True,
            "sensei_uses_special_contract": True,
            "agent_writes_through_proposal_only": True,
        },
        "templates": TEMPLATES,
    }
