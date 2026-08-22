from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...artifacts import ArtifactRef, ArtifactStore
from ...errors import ProductionError
from ..base import AdapterRequest, DraftRef


class FfmpegVideoExporter:
    """Optional local FFmpeg exporter with task-local deterministic frame input."""

    def __init__(
        self,
        artifacts: ArtifactStore,
        *,
        staging_root: Path | None = None,
        ffmpeg_command: Sequence[str] | None = None,
    ) -> None:
        self.artifacts = artifacts
        self.staging_root = Path(staging_root or artifacts.root / ".storyforge-video-staging").resolve()
        self.ffmpeg_command = list(ffmpeg_command or self.resolve_command() or [])
        if not self.ffmpeg_command:
            raise ProductionError(
                "video_export_unavailable",
                "未找到本机 FFmpeg，StoryForge 视频导出不可用。",
                status=409,
            )

    @classmethod
    def resolve_command(cls) -> list[str] | None:
        configured = os.getenv("HALOCUE_FFMPEG", "").strip()
        if configured:
            path = Path(configured.strip('"'))
            if not path.is_file():
                return None
            if path.suffix.casefold() == ".py":
                return [sys.executable, str(path)]
            return [str(path)]
        discovered = shutil.which("ffmpeg")
        return [discovered] if discovered else None

    @classmethod
    def available(cls) -> bool:
        return cls.resolve_command() is not None

    def export_video(
        self,
        *,
        request: AdapterRequest,
        draft: DraftRef,
        preview: ArtifactRef,
        options: Mapping[str, Any],
        cancelled,
    ) -> ArtifactRef:
        self._check_cancelled(cancelled)
        preview_bytes = self.artifacts.read_artifact_bytes(preview.uri)
        self.staging_root.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix="storyforge-video-", dir=self.staging_root))
        try:
            frames = self._write_frames(stage, preview_bytes, draft, cancelled, options)
            output = stage / "output.mp4"
            command = [
                *self.ffmpeg_command,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-framerate",
                "30",
                "-i",
                str(frames),
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                str(output),
            ]
            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                )
            except OSError as exc:
                raise ProductionError(
                    "video_export_failed",
                    "FFmpeg 视频导出进程无法启动。",
                    status=502,
                ) from exc
            self._wait(process, cancelled)
            if process.returncode != 0:
                raise ProductionError(
                    "video_export_failed",
                    "FFmpeg 视频导出失败。",
                    status=502,
                    details={"returncode": int(process.returncode or 0)},
                )
            self._check_cancelled(cancelled)
            if not output.is_file() or output.stat().st_size <= 0:
                raise ProductionError(
                    "video_export_output_missing",
                    "FFmpeg 没有生成有效视频文件。",
                    status=502,
                )
            workspace = self.artifacts.commit_file(
                f"workspace://storyforge-videos/{draft.draft_id}/{draft.revision_id}.mp4",
                output,
                kind="video",
                media_type="video/mp4",
            )
            self._check_cancelled(cancelled)
            return self.artifacts.publish_artifact(
                "storyforge-videos",
                draft.revision_id,
                workspace,
                provenance={
                    "run_id": request.run_id,
                    "work_item_id": request.work_item_id,
                    "attempt_id": request.attempt_id,
                },
            )
        finally:
            shutil.rmtree(stage, ignore_errors=True)

    @staticmethod
    def _write_frames(
        stage: Path,
        preview_bytes: bytes,
        draft: DraftRef,
        cancelled,
        options: Mapping[str, Any],
    ) -> Path:
        width = int(options.get("width") or 320)
        height = int(options.get("height") or 180)
        if not 64 <= width <= 1920 or not 64 <= height <= 1080:
            raise ProductionError("video_export_options_invalid", "视频输出尺寸无效。", status=400)
        try:
            manifest = json.loads(preview_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            manifest = {}
        count = 0
        for scene in manifest.get("scenes", []) if isinstance(manifest, dict) else []:
            if isinstance(scene, dict):
                count += len(scene.get("nodes", [])) if isinstance(scene.get("nodes"), list) else 0
        count = max(1, min(count, 300))
        digest = hashlib.sha256(preview_bytes + draft.content_hash.encode("ascii")).digest()
        pattern = stage / "frame-%06d.ppm"
        pixel_count = width * height
        for index in range(count):
            if cancelled():
                raise ProductionError("operation_cancelled", "StoryForge 视频导出已取消。", status=409)
            color = bytes((digest[(index * 3 + offset) % len(digest)] for offset in range(3)))
            pixels = color * pixel_count
            (stage / f"frame-{index + 1:06d}.ppm").write_bytes(
                f"P6\n{width} {height}\n255\n".encode("ascii") + pixels
            )
        return pattern

    @staticmethod
    def _wait(process: subprocess.Popen, cancelled) -> None:
        while process.poll() is None:
            if cancelled():
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
                raise ProductionError("operation_cancelled", "StoryForge 视频导出已取消。", status=409)
            time.sleep(0.02)

    @staticmethod
    def _check_cancelled(cancelled) -> None:
        if cancelled():
            raise ProductionError("operation_cancelled", "StoryForge 视频导出已取消。", status=409)


__all__ = ["FfmpegVideoExporter"]
