from __future__ import annotations

from abc import ABC, abstractmethod
import json
import re
import urllib.error
import urllib.request

from .errors import DomainError


class WritingProvider(ABC):
    kind: str
    display_name: str
    is_simulation: bool

    @abstractmethod
    def generate_blueprint(self, brief: dict, analysis_context: dict | None = None) -> dict: ...

    @abstractmethod
    def generate_scene(self, context: dict) -> str: ...

    @abstractmethod
    def rewrite_scene(self, context: dict, base_text: str, instruction: str) -> str: ...

    @abstractmethod
    def discuss_work(self, messages: list[dict], work_context: dict) -> dict: ...

    @abstractmethod
    def generate_chapter_plan(self, messages: list[dict], chapter_context: dict) -> dict: ...

    def descriptor(self) -> dict:
        return {
            "kind": self.kind,
            "display_name": self.display_name,
            "is_simulation": self.is_simulation,
            "can_call_model": not self.is_simulation,
        }


class FakeWritingProvider(WritingProvider):
    kind = "fake"
    display_name = "本地模拟 Provider"
    is_simulation = True

    def generate_chapter_plan(self, messages: list[dict], chapter_context: dict) -> dict:
        user_notes = [
            str(message.get("text", "")).strip()
            for message in messages
            if message.get("role") == "user" and str(message.get("text", "")).strip()
        ]
        chapter_title = str(chapter_context.get("chapter_title") or "当前章节")
        latest = user_notes[-1] if user_notes else "先明确本章要完成的变化。"
        return {
            "schema_version": "chapter-plan/1.0",
            "title": f"{chapter_title}细纲",
            "chapter_goal": latest,
            "beats": user_notes[-4:] or [latest],
            "continuity_notes": ["承接已确认的全作方向和此前正式正文。"],
            "status": "proposed",
            "simulation_notice": "本地模拟 Provider 只用于验证可替换流程，未调用真实模型。",
        }

    def discuss_work(self, messages: list[dict], work_context: dict) -> dict:
        task_contract = work_context.get("task_contract") or {}
        task_id = task_contract.get("id", "brief.build")
        latest = next(
            (
                str(message.get("text", "")).strip()
                for message in reversed(messages)
                if message.get("role") == "user" and str(message.get("text", "")).strip()
            ),
            "",
        )
        idea = str(work_context.get("idea") or latest).strip()
        lower = latest.lower()
        user_turns = [
            str(message.get("text", "")).strip()
            for message in messages
            if message.get("role") == "user" and str(message.get("text", "")).strip()
        ]

        tool_activity = [
            {"tool": "load_workflow_template", "label": "加载任务契约", "status": "succeeded"},
            {"tool": "read_work_context", "label": "读取作品上下文", "status": "succeeded"},
        ]
        artifact_preview = None

        import re
        char_match = re.search(r"《([^》]+)》.*(?:角色|人物)", latest) or re.search(r"(?:角色|人物).*《([^》]+)》", latest)
        world_match = re.search(r"《([^》]+)》.*(?:地点|世界观|设定)", latest) or re.search(r"(?:地点|世界观|设定).*《([^》]+)》", latest)

        if char_match:
            char_name = char_match.group(1).strip()
            artifact_preview = {
                "kind": "character_card",
                "title": char_name,
                "status": "discussion_draft",
                "content": {
                    "name": char_name,
                    "summary": f"由对话讨论生成的自定义角色卡草稿：{latest}",
                }
            }
            tool_activity.append({"tool": "draft_character_card", "label": "生成角色卡草稿", "status": "succeeded"})
        elif world_match:
            world_name = world_match.group(1).strip()
            artifact_preview = {
                "kind": "world_card",
                "title": world_name,
                "status": "discussion_draft",
                "content": {
                    "name": world_name,
                    "summary": f"由对话讨论生成的世界观设定草稿：{latest}",
                }
            }
            tool_activity.append({"tool": "draft_world_card", "label": "生成世界观草稿", "status": "succeeded"})

        if task_id == "structure.plan":
            text = (
                "故事方向已经确认。现在先讨论卷、章和场景各自要完成的变化，"
                "再由你确认结构；我不会把聊天内容直接改成章节或场景。"
            )
            questions = ["第一卷结束时，人物关系应当发生什么可见变化？", "开场这一章需要先让读者看见哪一个具体问题？"]
            ready = True
        elif task_id == "chapter.plan":
            chapter_title = str(task_contract.get("task_scope", {}).get("chapter_title") or "当前章节")
            text = (
                f"现在只讨论《{chapter_title}》内部的细纲：本章要完成的变化、场景节拍和承接点。"
                "全作方向仍来自作品栏目；这里不会重写 StoryBlueprint，也不会静默建立场景。"
            )
            questions = ["本章结束时，人物或事实必须发生什么变化？", "这一章要承接上一段正文的哪个状态？"]
            ready = True
        elif task_id == "scene.draft.generate":
            text = (
                "现在进入逐场写作。请先打开要处理的场景；我会以该场已固定的上下文提出候选和 Diff，"
                "不会直接覆盖正文或资料库。"
            )
            questions = ["下一场结束时必须发生的变化是什么？", "这场有哪些事实或关系不能被改写？"]
            ready = True
        elif task_id == "release.review":
            text = (
                "所有场景已有已采纳正文。现在的任务是核对连续性、人物一致性和未决伏笔，"
                "Gate 通过后才可以冻结不可变的 ScriptRelease。"
            )
            questions = ["是否有需要作为全篇问题处理的角色或伏笔？"]
            ready = True
        elif any(token in lower for token in ("整理", "形成方案", "生成方案", "定下来")):
            text = "我已经把目前的讨论整理成一份可审查方案。它仍是候选，只有你采纳后才会写入正式 Brief 和故事方向。"
            questions = []
            ready = True
        elif len(user_turns) >= 2 or len(latest) >= 18:
            text = f"你提到了“{latest[:36]}”。结合全作想法，我们可以在开场用一个小事件把人物的处境拉开，再逐步交代原因。"
            questions = ["你希望谁最先意识到异常？", "这次事件要保持完全私密，还是会被更多人察觉？"]
            ready = True
        else:
            text = f"我们先围绕“{idea}”理清故事主线。你希望这篇二创从谁的视角先进入，第一幕最关键的选择是什么？"
            questions = ["主要出场角色是谁？", "故事的基调更偏向日常互动、战斗悬疑还是搞笑闹剧？"]
            ready = False

        res = {
            "text": text,
            "questions": questions,
            "ready_for_proposal": ready,
            "ready_to_organize": ready,
            "reasoning_summary": (
                "先按当前阶段读取作品正式上下文，再判断这轮应继续追问、生成资料讨论草稿，"
                "还是已经足够整理为 Proposal。"
            ),
            "tool_activity": tool_activity,
            "simulation_notice": "当前使用的是本地模拟 Provider；可在设置中接入真实大模型进行智能创作。",
        }
        if artifact_preview:
            res["artifact_preview"] = artifact_preview
        return res

    def generate_blueprint(self, brief: dict, analysis_context: dict | None = None) -> dict:
        idea = brief.get("idea", "未命名的故事想法")
        characters = brief.get("characters") or []
        analysis_context = analysis_context or {}
        runtime_characters = analysis_context.get("runtime_character_cards", [])
        mentioned_cards = [
            card
            for card in runtime_characters
            if card.get("name") in characters or card.get("name") in idea
        ]
        if not characters:
            characters = [card.get("name") for card in mentioned_cards if card.get("name")]
        if not characters:
            if "爱丽丝" in idea or "凯伊" in idea:
                characters = ["爱丽丝", "凯伊"]
            elif "日奈" in idea or "亚子" in idea:
                characters = ["日奈", "亚子"]
            else:
                characters = ["爱丽丝", "凯伊"]
        primary_mode = "bond_short"
        secondary_modes = []
        normalized_idea = idea.lower()
        if any(token in normalized_idea for token in ("战斗", "突入", "任务", "敌人", "防线", "行动", "枪战")):
            primary_mode = "main_battle"
            secondary_modes.append("bond_short")
        elif any(token in normalized_idea for token in ("喜剧", "搞笑", "闹剧", "日常")):
            primary_mode = "long_comedy"
            secondary_modes.append("bond_short")
        elif any(token in normalized_idea for token in ("小说", "内心", "叙述", "阅读")):
            primary_mode = "text_reading"
        if any(token in normalized_idea for token in ("异常", "线索", "调查", "秘密", "谜")) and primary_mode != "bond_short":
            secondary_modes.append("bond_short")
        if not secondary_modes and any(token in normalized_idea for token in ("异常", "线索", "调查", "秘密", "谜")):
            secondary_modes.append("main_battle")
        sensei = "present" if any(token in normalized_idea for token in ("老师", "sensei")) else "absent"
        world = analysis_context.get("world", {"label": "尚未建立世界观基础", "detail": "当前作品没有可供分析的世界观条目。"})
        return {
            "title": f"围绕“{idea[:24]}”的故事方向",
            "premise": idea,
            "theme": "在具体选择中确认彼此，而不是由旁白替人物总结关系。",
            "central_conflict": f"{characters[0]}必须处理眼前的异常，同时避免让真实目的过早暴露。",
            "direction": [
                "先用可见的小问题建立场景压力",
                "让人物的局部目标互相干扰并产生选择",
                "在必要事实成立后停止，不追加主题升华",
            ],
            "characters": characters,
            "mode": primary_mode,
            "status": "proposed",
            "recommendations": {
                "primary_scene_mode": primary_mode,
                "secondary_scene_modes": secondary_modes,
                "character_card_ids": [card["id"] for card in mentioned_cards],
                "sensei_presence": sensei,
                "world_basis": world,
            },
            "simulation_notice": "结构由本地 Fake Provider 生成，仅用于验证工作流。",
        }

    def generate_scene(self, context: dict) -> str:
        contract = context["scene_contract"]
        characters = [card.get("name") for card in context.get("runtime_character_cards", []) if card.get("name")]
        if not characters:
            characters = context["brief"].get("characters") or ["爱丽丝", "凯伊"]
        first = characters[0]
        second = characters[1] if len(characters) > 1 else first
        goal = contract.get("goal") or "确认眼前发生了什么"
        location = contract.get("location") or "活动室"
        return "\n".join(
            [
                f"旁白: {location}里，只剩桌上的提示灯还亮着。",
                f"{first}: 先别碰。它刚才明明没有亮。",
                f"{second}: 我还什么都没做。你是不是已经有结论了？",
                f"{first}: 没有结论。只是如果目标是“{goal}”，现在停下来比较快。",
                f"{second}: 好，那我不碰。你来告诉我第一步看哪里。",
                f"旁白: {first}把手收了回来，提示灯又闪了一次。",
            ]
        ) + "\n"

    def rewrite_scene(self, context: dict, base_text: str, instruction: str) -> str:
        contract = context["scene_contract"]
        cards = context.get("runtime_character_cards", [])
        characters = [card.get("name") for card in cards if card.get("name")]
        first = characters[0] if characters else "角色"
        anchor = next(
            (
                item
                for card in cards
                if card.get("name") == first
                for item in card.get("voice_anchors", [])
                if item
            ),
            "先确认眼前的情况。",
        )
        location = contract.get("location") or "现场"
        lines = [line.rstrip() for line in str(base_text).splitlines() if line.strip()]
        if not lines:
            return self.generate_scene(context)
        lowered = instruction.lower()
        if any(token in lowered for token in ("节奏", "紧凑", "缩短", "收束")):
            revision_line = f"旁白: {location}里，没有人把判断说得太满，下一步已经留在眼前。"
        elif any(token in lowered for token in ("ooc", "人物", "语气", "对白")):
            revision_line = f"{first}: {anchor}"
        else:
            revision_line = f"旁白: {location}里的停顿被保留下来，所有人先回到眼前能确认的事。"
        if lines[-1] != revision_line:
            lines.append(revision_line)
        return "\n".join(lines) + "\n"


class LLMWritingProvider(WritingProvider):
    """Real LLM Provider supporting OpenAI-compatible and Anthropic protocols."""

    kind = "llm"
    is_simulation = False

    def __init__(self, credentials: dict):
        self.credentials = credentials
        self.provider_type = credentials.get("provider", "openai")
        self.base_url = credentials.get("base_url", "")
        self.model = credentials.get("model", "gpt-4o")
        self.api_key = credentials.get("api_key", "")
        self.max_tokens = int(credentials.get("max_tokens", 8192))
        self.timeout = int(credentials.get("timeout", 120))
        self.display_name = f"{self.model} ({self.provider_type})"
        self.fallback = FakeWritingProvider()
        self._last_reasoning_content = ""

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        self._last_reasoning_content = ""
        if self.provider_type == "anthropic":
            endpoint = f"{self.base_url or 'https://api.anthropic.com/v1'}/messages"
            req_data = {
                "model": self.model,
                "max_tokens": min(self.max_tokens, 4096),
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            req_bytes = json.dumps(req_data).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_bytes, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("anthropic-version", "2023-06-01")
            req.add_header("x-api-key", self.api_key)
        else:
            endpoint = f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"
            req_data = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
            req_bytes = json.dumps(req_data).encode("utf-8")
            req = urllib.request.Request(endpoint, data=req_bytes, method="POST")
            req.add_header("Content-Type", "application/json")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")

        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if self.provider_type == "anthropic":
                content_blocks = data.get("content", [])
                reasoning_blocks = [
                    str(block.get("thinking") or block.get("text") or "").strip()
                    for block in content_blocks
                    if block.get("type") in {"thinking", "reasoning"}
                ]
                self._last_reasoning_content = "\n\n".join(item for item in reasoning_blocks if item)
                text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
                return text
            else:
                choices = data.get("choices", [])
                if choices:
                    message = choices[0].get("message", {})
                    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
                    if isinstance(reasoning, (dict, list)):
                        reasoning = json.dumps(reasoning, ensure_ascii=False)
                    self._last_reasoning_content = str(reasoning).strip()
                    return message.get("content", "")
                return ""

    def _provider_failure(self, operation: str, exc: Exception | None = None):
        details = {"operation": operation, "provider": self.provider_type, "model": self.model}
        if exc is not None:
            details["reason"] = str(exc)
        error = DomainError(
            "writing_provider_failed",
            f"模型未能完成{operation}，本次没有回退为模拟结果。",
            status=502,
            details=details,
        )
        if exc is not None:
            raise error from exc
        raise error

    def generate_chapter_plan(self, messages: list[dict], chapter_context: dict) -> dict:
        try:
            system_prompt = (
                "你是一位专业的《蔚蓝档案》二创剧本创作导演。请根据讨论记录与章节目标，制定章节细纲。\n"
                "必须以纯 JSON 返回，格式：\n"
                "{\n"
                '  "schema_version": "chapter-plan/1.0",\n'
                '  "title": "章节名称",\n'
                '  "chapter_goal": "核心目标",\n'
                '  "beats": ["场景1节拍", "场景2节拍", ...],\n'
                '  "continuity_notes": ["承接说明..."]\n'
                "}"
            )
            chat_history = "\n".join(f"{m.get('role')}: {m.get('text', '')}" for m in messages)
            user_prompt = f"章节上下文: {json.dumps(chapter_context, ensure_ascii=False)}\n讨论历史:\n{chat_history}"
            raw = self._call_llm(system_prompt, user_prompt)
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group(0))
                data["status"] = "proposed"
                return data
        except DomainError:
            raise
        except Exception as exc:
            self._provider_failure("章节细纲生成", exc)
        self._provider_failure("章节细纲生成")

    def discuss_work(self, messages: list[dict], work_context: dict) -> dict:
        try:
            system_prompt = (
                "你是一位专业的《蔚蓝档案》二创剧本创作导演。你的任务是协助作者讨论并理清故事方向与人物弧光。\n"
                "语气温和、敏锐、富有二创文学素养。请回复 JSON：\n"
                "{\n"
                '  "text": "对作者想法的提炼分析与推进建议",\n'
                '  "questions": ["1-2个引导性问题"],\n'
                '  "reasoning_summary": "一句面向作者的判断依据摘要，不输出隐藏推理过程",\n'
                '  "ready_for_proposal": true/false\n'
                "}"
            )
            user_prompt = f"作品上下文: {json.dumps(work_context, ensure_ascii=False)}\n历史消息:\n" + "\n".join(
                f"{m.get('role')}: {m.get('text', '')}" for m in messages[-8:]
            )
            raw = self._call_llm(system_prompt, user_prompt)
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group(0))
                if self._last_reasoning_content:
                    data["reasoning_content"] = self._last_reasoning_content[:12000]
                return data
        except DomainError:
            raise
        except Exception as exc:
            self._provider_failure("作品讨论", exc)
        self._provider_failure("作品讨论")

    def generate_blueprint(self, brief: dict, analysis_context: dict | None = None) -> dict:
        try:
            system_prompt = (
                "你是一位专业的《蔚蓝档案》二创剧本规划专家。请根据创意简报生成结构化 StoryBlueprint。\n"
                "必须返回纯 JSON，包含 title, premise, theme, central_conflict, direction (数组), characters (数组), mode。\n"
            )
            user_prompt = f"Brief: {json.dumps(brief, ensure_ascii=False)}\nContext: {json.dumps(analysis_context or {}, ensure_ascii=False)}"
            raw = self._call_llm(system_prompt, user_prompt)
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                data = json.loads(match.group(0))
                data["status"] = "proposed"
                return data
        except DomainError:
            raise
        except Exception as exc:
            self._provider_failure("故事方向生成", exc)
        self._provider_failure("故事方向生成")

    def generate_scene(self, context: dict) -> str:
        try:
            system_prompt = (
                "你是一位高水平的《蔚蓝档案》视觉小说/剧本作家。严格输出剧本格式，每行格式为：\n"
                "角色名: 台词\n"
                "或 旁白: 场景/动作描写\n"
                "保持二创角色性格鲜明，台词精炼有韵味，单句不超过40字，避免空洞说教。"
            )
            user_prompt = f"场景写作上下文: {json.dumps(context, ensure_ascii=False)}"
            raw = self._call_llm(system_prompt, user_prompt)
            cleaned = "\n".join(line.strip() for line in raw.splitlines() if line.strip() and ":" in line or "：" in line)
            if cleaned:
                return cleaned + "\n"
        except DomainError:
            raise
        except Exception as exc:
            self._provider_failure("场景起草", exc)
        self._provider_failure("场景起草")

    def rewrite_scene(self, context: dict, base_text: str, instruction: str) -> str:
        try:
            system_prompt = (
                "你是一位高水平的《蔚蓝档案》剧本精修专家。根据作者的修改意见重写或局部调整剧本文本。\n"
                "每行格式保持：\n"
                "角色名: 台词\n"
                "或 旁白: 场景/动作描写\n"
            )
            user_prompt = f"基础剧本文本:\n{base_text}\n\n修改指示:\n{instruction}\n\n上下文:\n{json.dumps(context, ensure_ascii=False)}"
            raw = self._call_llm(system_prompt, user_prompt)
            cleaned = "\n".join(line.strip() for line in raw.splitlines() if line.strip() and ":" in line or "：" in line)
            if cleaned:
                return cleaned + "\n"
        except DomainError:
            raise
        except Exception as exc:
            self._provider_failure("场景改写", exc)
        self._provider_failure("场景改写")


def make_writing_provider(settings_or_credentials) -> WritingProvider:
    if hasattr(settings_or_credentials, "get_credentials"):
        creds = settings_or_credentials.get_credentials()
        pub = settings_or_credentials.public()["model"]
    elif isinstance(settings_or_credentials, dict):
        creds = settings_or_credentials
        pub = creds
    else:
        return FakeWritingProvider()

    if pub.get("configured") and creds.get("model"):
        return LLMWritingProvider(creds)
    return FakeWritingProvider()
