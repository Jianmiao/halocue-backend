from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..base import AdapterRequest, DraftRef


@dataclass(frozen=True)
class LegacyCompileInput:
    """Private AA invocation data; never returned from the adapter boundary."""

    project: str
    text: str
    speakers: tuple[str, ...]
    expected_draft_version: int
    diagnostics: tuple[dict[str, str], ...] = ()


def translate_performance_draft(
    request: AdapterRequest, draft_ref: DraftRef
) -> LegacyCompileInput:
    payload = draft_ref.payload or {}
    lines: list[str] = []
    speakers: set[str] = set()
    diagnostics: list[dict[str, str]] = []
    for scene in payload.get("scenes", []) if isinstance(payload, dict) else []:
        for node in scene.get("nodes", []) if isinstance(scene, dict) else []:
            if not isinstance(node, dict):
                continue
            if node.get("kind") == "choice_group":
                diagnostics.append(
                    {
                        "code": "choice_group_requires_manual_translation",
                        "severity": "warning",
                        "message": "选择组需要在 AA 兼容边界人工确认。",
                    }
                )
                continue
            line = node.get("performance_line")
            if not isinstance(line, dict):
                continue
            text = str(line.get("text") or "")
            speaker_id = str(line.get("speaker_id") or "").strip()
            if speaker_id:
                speakers.add(speaker_id)
                lines.append(f"{speaker_id}: {text}")
            elif line.get("content_kind") == "stage_direction":
                lines.append(f"@stage {text}")
            else:
                lines.append(f"旁白: {text}")
    project = str(
        request.production_request.get("production_display_name")
        or request.production_request.get("request_id")
    )
    return LegacyCompileInput(
        project=project[:160],
        text="\n".join(lines) + ("\n" if lines else ""),
        speakers=tuple(sorted(speakers)),
        expected_draft_version=1,
        diagnostics=tuple(diagnostics),
    )


__all__ = ["LegacyCompileInput", "translate_performance_draft"]
