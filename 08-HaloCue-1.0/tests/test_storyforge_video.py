from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from halocue_production.adapters.storyforge import StoryForgeAdapter, StoryForgeRenderer
from halocue_production.adapters.storyforge.video import FfmpegVideoExporter
from halocue_production.contracts import idempotency_key_for_request
from halocue_production.errors import ProductionError
from test_adapter_storyforge import _context


def _fake_ffmpeg(path: Path) -> Path:
    path.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[-1]).write_bytes(b'video-fixture')\n",
        encoding="utf-8",
    )
    return path


def test_missing_ffmpeg_does_not_advertise_storyforge_video(tmp_path, monkeypatch):
    monkeypatch.setenv("HALOCUE_FFMPEG", str(tmp_path / "missing-ffmpeg.exe"))
    monkeypatch.setenv("PATH", "")

    assert FfmpegVideoExporter.resolve_command() is None


def test_ffmpeg_export_publishes_deterministic_video_artifact_and_cleans_staging(tmp_path, monkeypatch):
    request, draft, drafts, artifacts = _context(tmp_path)
    fake = _fake_ffmpeg(tmp_path / "fake_ffmpeg.py")
    monkeypatch.setenv("HALOCUE_FFMPEG", str(fake))
    exporter = FfmpegVideoExporter(artifacts, staging_root=tmp_path / "staging")
    adapter = StoryForgeAdapter(StoryForgeRenderer(artifacts), drafts, video_exporter=exporter)

    payload = json.loads(json.dumps(request.production_request))
    payload["production_policy"]["target"] = "storyforge_video"
    payload["idempotency_key"] = idempotency_key_for_request(payload)
    request = request.__class__(payload, asset_manifest=request.asset_manifest, target="storyforge_video")
    result = adapter.render(request, draft, {"target": "storyforge_video"})

    assert result.artifact_refs[0] == f"artifact://storyforge-videos/{draft.revision_id}"
    artifact = artifacts.get_artifact(result.artifact_refs[0])
    assert artifact.kind == "video"
    assert artifacts.read_artifact_bytes(artifact.uri) == b"video-fixture"
    assert list((tmp_path / "staging").glob("**/*")) == []


def test_ffmpeg_export_cancellation_does_not_publish_artifact(tmp_path):
    request, draft, drafts, artifacts = _context(tmp_path)
    exporter = FfmpegVideoExporter(
        artifacts,
        staging_root=tmp_path / "staging",
        ffmpeg_command=[sys.executable, "-c", "import time; time.sleep(30)"],
    )

    with pytest.raises(ProductionError) as raised:
        exporter.export_video(
            request=request,
            draft=draft,
            preview=StoryForgeRenderer(artifacts).render_preview(
                request, draft, {"target": "storyforge_preview"}, cancelled=lambda: False
            ),
            options={},
            cancelled=lambda: True,
        )

    assert raised.value.code == "operation_cancelled"
    assert list((tmp_path / "staging").glob("**/*")) == []
