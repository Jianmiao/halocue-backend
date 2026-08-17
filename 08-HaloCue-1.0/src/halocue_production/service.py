from __future__ import annotations

import datetime as dt
import io
import mimetypes
import re
import uuid
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .asset_manifests import AssetManifestStore
from .contracts import canonical_json_bytes, contract_content_hash, sha256_bytes
from .config import Settings
from .direction_models import CancellableModelProvider, DirectionModelGateway
from .errors import ProductionError
from .jobs import CancellationToken, JobOutcome, JobRegistry
from .legacy_adapter import Legacy093Adapter
from .models import ProductionRun, ScriptRelease, WorkItem, content_sha256, new_id, utc_now
from .model_settings import DirectionModelSettings
from .repository import ProductionRepository
from .resource_catalog import ResourceCatalog
from .name_baseline import CharacterNameBaseline
from .resource_previews import ResourcePreview
from .settings_store import SettingsStore
from .asset_staging import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_FILES,
    AssetStaging,
)


INVALID_PROJECT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
BUILD_ID = re.compile(r"build-[0-9a-f]{12}")
UPSTREAM_RELEASE_ID = re.compile(r"release-[0-9a-f]{12}")
WORK_ID = re.compile(r"work-[0-9a-f]{12}")
SHA256 = re.compile(r"[0-9a-f]{64}")
SCRIPT_RELEASE_SCHEMA_VERSION = "1.0"
_TASK_ASSET_NAMESPACE = uuid.UUID("40b0223c-3395-4b1b-aeaf-96c9f030c3e7")
_MANIFEST_SUCCESSOR_NAMESPACE = uuid.UUID("0193c639-c212-45f2-9735-317b87b74a35")
DIRECTIVE_COMMANDS = frozenset({
    "bg", "trans", "bgfx", "popup", "bgm", "music", "se", "sound", "place", "wait",
    "enter", "exit", "move", "stage", "auto", "camera", "camera_hold", "fx", "hl",
    "bgshake", "clearst", "hidemenu", "showmenu", "shot", "aronatouch", "st", "stm", "zoom", "raw",
})
RESOURCE_DIRECTIVES = frozenset({"bg", "se", "sound"})
NO_ARGUMENT_DIRECTIVES = frozenset({"auto", "bgshake", "clearst", "hidemenu", "showmenu", "aronatouch"})


class ProductionService:
    def __init__(self, settings: Settings) -> None:
        settings.prepare()
        self.settings = settings
        self.repository = ProductionRepository(settings.data_dir)
        self.artifacts = ArtifactStore(
            settings.data_dir / "workspace", self.repository.runtime
        )
        self.asset_manifests = AssetManifestStore(
            self.artifacts, self.repository.runtime
        )
        for existing_run in self.repository.list_runs():
            self.asset_manifests.ensure_compatibility_manifest(existing_run)
        self.settings_store = SettingsStore(settings.data_dir)
        persisted = self.settings_store.load()
        if settings.aa_data is None and persisted.get("aa_data"):
            try:
                configured_aa = self.settings_store.validate_aa_workspace(
                    persisted["aa_data"]
                )
            except ProductionError:
                configured_aa = None
            if configured_aa:
                self.settings = replace(settings, aa_data=configured_aa)
        self.adapter = Legacy093Adapter(self.settings)
        self.name_baseline = CharacterNameBaseline(self.settings.name_baseline)
        self.resources = ResourceCatalog(
            self.settings.resource_index,
            self.settings.aa_data,
            self.settings.legacy_root,
            self.name_baseline,
        )
        self.direction_model_settings = DirectionModelSettings(settings.data_dir)
        self.direction_models = DirectionModelGateway(
            self.direction_model_settings, settings.legacy_root
        )
        self.jobs = JobRegistry(settings.data_dir / "jobs", runtime=self.repository.runtime)
        self.asset_staging = AssetStaging(settings.data_dir / "uploads")
        self._cleanup_abandoned_compile_outputs()
        self._recover_interrupted_runs()

    def _cleanup_abandoned_compile_outputs(self) -> None:
        for job in self.jobs.list():
            if job.kind != "compile" or job.state != "abandoned" or not job.run_id:
                continue
            build_id = str(job.retry_context.get("build_id") or "")
            if not BUILD_ID.fullmatch(build_id):
                continue
            try:
                run = self.repository.get_run(job.run_id)
                self.adapter.discard_compile_output(str(run.draft_token), build_id)
            except (OSError, ProductionError):
                continue

    def _recover_interrupted_runs(self) -> None:
        for run in self.repository.list_runs():
            recovery_state = {
                "compiling": "compile_interrupted",
                "generating_direction": "direction_interrupted",
            }.get(run.state)
            if not recovery_state:
                continue
            run.state = recovery_state
            run.pending_build_id = None
            run.updated_at = utc_now()
            self.repository.save_run(run)

    @staticmethod
    def _project_name(value: Any) -> str:
        name = str(value or "").strip()
        if not name:
            raise ProductionError("project_required", "AA 工程名称不能为空")
        if len(name) > 80 or INVALID_PROJECT.search(name) or name.endswith((".", " ")):
            raise ProductionError("invalid_project_name", "AA 工程名称包含 Windows 不允许的字符")
        return name

    @staticmethod
    def _source_text(payload: dict[str, Any]) -> tuple[str, str, str | None]:
        source = payload.get("source")
        if not isinstance(source, dict) or source.get("kind") not in {"inline", "file_upload"}:
            raise ProductionError(
                "unsupported_source",
                "当前版本只接受直接输入或本地文本文件",
                details={"supported": ["inline", "file_upload"]},
            )
        text = source.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ProductionError("source_empty", "剧本文本不能为空")
        if len(text.encode("utf-8")) > 5 * 1024 * 1024:
            raise ProductionError("source_too_large", "剧本文本不能超过 5 MiB", status=413)
        kind = str(source.get("kind"))
        filename: str | None = None
        if kind == "file_upload":
            filename = Path(str(source.get("filename") or "")).name.strip()
            if not filename or Path(filename).suffix.casefold() not in {".txt", ".md", ".markdown"}:
                raise ProductionError(
                    "source_file_type_unsupported",
                    "剧本文件必须是 TXT、MD 或 Markdown",
                    details={"allowed": [".txt", ".md", ".markdown"]},
                )
        return text.replace("\x00", ""), kind, filename

    def health(self) -> dict[str, Any]:
        return {
            "ok": True,
            "service": "halocue-production",
            "version": "1.0.0a1",
            "api_version": "v1",
            "capabilities": self.capabilities(),
        }

    def capabilities(self) -> dict[str, Any]:
        capabilities = self.adapter.capabilities()
        model = self.direction_model_settings.public()["model"]
        capabilities["ai_preflight"] = {
            "state": "available" if model["configured"] else "not_configured",
            "reason": None if model["configured"] else "model_provider_not_configured",
        }
        if model["configured"]:
            capabilities["generation_modes"]["ai_direction"] = {
                "state": "available",
                "provider": model.get("provider"),
                "model": model.get("model"),
            }
        capabilities["custom_assets"] = {
            "state": "available",
            "flow": ["upload", "validate", "register_to_task", "compile", "install"],
            "kinds": ["background", "sound", "character", "cg"],
        }
        capabilities["script_release_handoff"] = {
            "state": "available",
            "schema_version": "1.0",
            "contract_kind": "WritingHandoff/1.0",
            "formal_contract": "ScriptRelease/1.0",
            "formal_contract_state": "not_connected",
            "identity_fields": ["id", "display_version", "content_hash"],
            "content_hash": "sha256",
            "idempotent": True,
        }
        return capabilities

    @staticmethod
    def _upstream_script_release(
        payload: dict[str, Any], text: str
    ) -> dict[str, Any] | None:
        value = payload.get("script_release")
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ProductionError(
                "invalid_script_release", "script_release 必须是对象"
            )
        schema_version = str(
            value.get("schema_version") or SCRIPT_RELEASE_SCHEMA_VERSION
        ).strip()
        if schema_version != SCRIPT_RELEASE_SCHEMA_VERSION:
            raise ProductionError(
                "unsupported_script_release_version",
                "不支持的 ScriptRelease 合同版本",
                details={
                    "received": schema_version,
                    "supported": [SCRIPT_RELEASE_SCHEMA_VERSION],
                },
            )
        release_id = str(value.get("id") or "").strip()
        display_version = str(value.get("display_version") or "").strip()
        declared_hash = str(value.get("content_hash") or "").strip().casefold()
        if not UPSTREAM_RELEASE_ID.fullmatch(release_id):
            raise ProductionError(
                "invalid_script_release", "写作发布版本 ID 无效"
            )
        if not display_version or len(display_version) > 40:
            raise ProductionError(
                "invalid_script_release", "写作发布版本号无效"
            )
        if not SHA256.fullmatch(declared_hash):
            raise ProductionError(
                "invalid_script_release", "写作发布版本内容哈希无效"
            )
        actual_hash = content_sha256(text)
        if declared_hash != actual_hash:
            raise ProductionError(
                "script_release_hash_mismatch",
                "写作发布版本的正文与内容哈希不一致，已拒绝交接",
                status=409,
                details={"release_id": release_id},
            )

        origin = {
            "kind": "halocue_writing",
            "contract_kind": "WritingHandoff/1.0",
            "formal_script_release": False,
            "schema_version": schema_version,
            "release_id": release_id,
            "display_version": display_version,
            "content_hash": declared_hash,
        }
        work_id = str(value.get("work_id") or "").strip()
        if work_id:
            if not WORK_ID.fullmatch(work_id):
                raise ProductionError(
                    "invalid_script_release", "写作作品 ID 无效"
                )
            origin["work_id"] = work_id
        writing_pack_version = str(value.get("writing_pack_version") or "").strip()
        if writing_pack_version:
            if len(writing_pack_version) > 120:
                raise ProductionError(
                    "invalid_script_release", "WritingPack 版本号过长"
                )
            origin["writing_pack_version"] = writing_pack_version
        return origin

    def direction_model_settings_public(self) -> dict[str, Any]:
        return self.direction_model_settings.public()

    def request_cg_advice(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        work_item_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if not self.direction_model_settings.public()["model"]["configured"]:
            raise ProductionError(
                "cg_advice_not_configured", "获取 AI 制作意见前，请先配置演出模型", status=409
            )
        run = self._run(run_id)
        expected = self._expected_version(payload)
        detail = self.adapter.draft_detail(str(run.draft_token))
        if detail["draft_version"] != expected:
            raise ProductionError("revision_conflict", "草稿版本已经变化", status=409)
        start_card_id = str(payload.get("start_card_id") or "").strip()
        end_card_id = str(payload.get("end_card_id") or "").strip()
        provider = self.direction_models.provider()

        def work(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            result = self.adapter.execute_cg_advice(
                token=str(run.draft_token),
                provider=CancellableModelProvider(provider, token.is_cancelled),
                start_card_id=start_card_id, end_card_id=end_card_id,
            )
            token.raise_if_cancelled()
            return result

        job = self.jobs.submit(
            "cg_advice",
            work,
            run_id=run_id,
            retry_context={
                "expected_draft_version": expected,
                "start_card_id": start_card_id,
                "end_card_id": end_card_id,
            },
            work_item_id=work_item_id,
            provider=str(self.direction_model_settings.public()["model"].get("provider") or "") or None,
            model_or_engine=str(self.direction_model_settings.public()["model"].get("model") or "") or None,
        )
        return 202, {"ok": True, "job": self._job_public(job.to_dict())}

    def configure_direction_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.direction_model_settings.save(payload)

    def fetch_direction_models(self, payload: dict[str, Any] | None = None) -> list[str]:
        return self.direction_model_settings.fetch_models(payload)

    def test_direction_model(self, *, work_item_id: str | None = None) -> tuple[int, dict[str, Any]]:
        def work(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            result = self.direction_models.test_connection(token.is_cancelled)
            token.raise_if_cancelled()
            return result

        model = self.direction_model_settings.public()["model"]
        job = self.jobs.submit(
            "model_connection_test",
            work,
            retry_context={},
            work_item_id=work_item_id,
            provider=str(model.get("provider") or "") or None,
            model_or_engine=str(model.get("model") or "") or None,
        )
        return 202, {"ok": True, "job": self._job_public(job.to_dict())}

    def generate_direction(
        self, run_id: str, payload: dict[str, Any], *, work_item_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        run = self._run(run_id)
        if run.source_summary.get("generation_mode") != "ai_direction":
            raise ProductionError(
                "direction_mode_not_selected",
                "该制作任务不是 AI 安排演出模式",
                status=409,
            )
        expected = self._expected_version(payload)
        detail = self.adapter.draft_detail(str(run.draft_token))
        if detail["draft_version"] != expected:
            raise ProductionError("revision_conflict", "草稿版本已经变化", status=409)
        actor_errors = [
            issue
            for issue in detail["diagnostics"]
            if str(issue.get("code") or "").startswith("actor.")
            and issue.get("severity") == "error"
        ]
        if actor_errors:
            raise ProductionError(
                "cast_mapping_required",
                "AI 安排演出前必须完成角色映射",
                status=409,
                details={"count": len(actor_errors)},
            )
        story_type = str(payload.get("story_type") or "auto").strip()
        if story_type not in {"auto", "main", "event", "bond"}:
            raise ProductionError("invalid_story_type", "剧情类型无效")
        generation_id = new_id("direction")
        provider = self.direction_models.provider()
        run.state = "generating_direction"
        run.current_stage = "generation"
        run.updated_at = utc_now()
        self.repository.save_run(run)

        def commit_success() -> None:
            latest = self._run(run_id)
            latest.state = "waiting_for_review"
            latest.current_stage = "review_install"
            latest.updated_at = utc_now()
            self.repository.save_run(latest)

        def commit_failure(_exc: Exception) -> None:
            latest = self._run(run_id)
            latest.state = "direction_failed"
            latest.updated_at = utc_now()
            self.repository.save_run(latest)

        def work(token: CancellationToken) -> JobOutcome:
            token.raise_if_cancelled()
            result = self.adapter.execute_direction_generation(
                token=str(run.draft_token),
                generation_id=generation_id,
                provider=CancellableModelProvider(provider, token.is_cancelled),
                expected_draft_version=expected,
                story_type=story_type,
            )
            token.raise_if_cancelled()
            return JobOutcome({"run_id": run_id, **result}, commit_success)

        job = self.jobs.submit(
            "direction_generation",
            work,
            run_id=run_id,
            retry_context={"expected_draft_version": expected, "story_type": story_type},
            work_item_id=work_item_id,
            provider=str(self.direction_model_settings.public()["model"].get("provider") or "") or None,
            model_or_engine=str(self.direction_model_settings.public()["model"].get("model") or "") or None,
            on_failure=commit_failure,
        )
        return 202, {
            "ok": True,
            "job": self._job_public(job.to_dict()),
            "generation_id": generation_id,
        }

    def aa_workspace_settings(self) -> dict[str, Any]:
        path = self.settings.aa_data
        valid = bool(
            path
            and path.is_dir()
            and all(
                (path / name).is_dir()
                for name in ("projects", "saves", "overrides", "settings")
            )
        )
        return {
            "ok": True,
            "aa_workspace": {
                "configured": bool(path),
                "valid": valid,
            },
            "capabilities": self.capabilities(),
        }

    @staticmethod
    def _aa_environment_public(environment: dict[str, Any]) -> dict[str, Any]:
        workspace = environment.get("workspace")
        workspace = workspace if isinstance(workspace, dict) else {}
        directories = workspace.get("directories")
        directories = directories if isinstance(directories, dict) else {}
        resource_cache = environment.get("resource_cache")
        resource_cache = resource_cache if isinstance(resource_cache, dict) else {}
        candidates = environment.get("candidates")
        candidates = candidates if isinstance(candidates, list) else []
        issues = environment.get("issues")
        issues = issues if isinstance(issues, list) else []
        recent_projects = environment.get("recent_projects")
        recent_projects = recent_projects if isinstance(recent_projects, list) else []
        return {
            "workspace": {
                "discovered": bool(workspace.get("path")),
                "valid": bool(workspace.get("valid")),
                "directories": {
                    name: bool(directories.get(name))
                    for name in ("projects", "saves", "overrides", "settings")
                },
            },
            "resource_cache": {"available": bool(resource_cache.get("available"))},
            "executable_available": bool(environment.get("executable")),
            "install_root_available": bool(environment.get("install_root")),
            "recent_projects": [Path(str(item)).name for item in recent_projects[:12]],
            "requires_selection": bool(environment.get("requires_selection")),
            "candidate_count": len(candidates),
            "issues": [
                {
                    "code": str(item.get("code") or ""),
                    "message": str(item.get("message") or ""),
                }
                for item in issues
                if isinstance(item, dict)
            ],
        }

    def inspect_aa_environment(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        selection = str(payload.get("selection") or "").strip() or None
        environment = self.adapter.discover_aa_environment(selection)
        adopted = False
        if payload.get("adopt") is True:
            workspace = environment.get("workspace") or {}
            if not workspace.get("path"):
                public_environment = self._aa_environment_public(environment)
                raise ProductionError(
                    "aa_workspace_not_discovered",
                    "没有检测到可采用的 AA 工作区",
                    status=409,
                    details={"issues": public_environment["issues"]},
                )
            self.configure_aa_workspace({"path": workspace["path"]})
            adopted = True
            environment = self.adapter.discover_aa_environment(str(workspace["path"]))
        return {
            "ok": True,
            "environment": self._aa_environment_public(environment),
            "adopted": adopted,
            "aa_workspace": self.aa_workspace_settings()["aa_workspace"],
            "capabilities": self.capabilities(),
        }

    def configure_aa_workspace(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = self.settings_store.validate_aa_workspace(payload.get("path"))
        current = self.settings_store.load()
        current["aa_data"] = str(path)
        self.settings_store.save(current)
        self.settings = replace(self.settings, aa_data=path)
        self.adapter.settings = self.settings
        self.resources = ResourceCatalog(
            self.settings.resource_index,
            self.settings.aa_data,
            self.settings.legacy_root,
            self.name_baseline,
        )
        return self.aa_workspace_settings()

    def list_resources(
        self, kind: str, *, query: str = "", offset: int = 0, limit: int = 80
    ) -> dict[str, Any]:
        return self.resources.list(kind, query=query, offset=offset, limit=limit)

    def character_resource(self, identifier: str) -> dict[str, Any]:
        return self.resources.character_detail(identifier)

    def resource_preview(self, kind: str, key: str) -> ResourcePreview:
        preview = self.resources.preview(kind, key)
        if preview is None:
            raise ProductionError("resource_preview_not_found", "该资源没有可用的本地预览", status=404)
        return preview

    def list_run_resources(
        self, run_id: str, kind: str, *, query: str = "", offset: int = 0, limit: int = 80
    ) -> dict[str, Any]:
        run = self._run(run_id)
        return self.adapter.list_draft_resources(
            str(run.draft_token), kind, query=query, offset=offset, limit=limit
        )

    def run_character_resource(self, run_id: str, identifier: str) -> dict[str, Any]:
        run = self._run(run_id)
        return self.adapter.draft_character_detail(str(run.draft_token), identifier)

    def upload_asset(self, *, filename: str, content: bytes) -> dict[str, Any]:
        return self.asset_staging.upload(filename=filename, content=content)

    @staticmethod
    def _asset_kind(value: Any) -> str:
        kind = str(value or "").strip().casefold()
        if kind not in {"background", "sound", "character", "cg"}:
            raise ProductionError(
                "invalid_asset_kind", "素材类型必须是背景、音效或角色骨骼",
                details={"allowed": ["background", "sound", "character", "cg"]},
            )
        return kind

    def validate_task_asset(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._run(run_id)
        kind = self._asset_kind(payload.get("kind"))
        token = str(payload.get("upload_token") or "").strip()
        source = self.asset_staging.source_for(token, kind)
        result = self.adapter.validate_task_asset(
            source=source, kind=kind, identifier=str(payload.get("identifier") or "").strip()
        )
        return {"ok": True, "upload_token": token, "validation": result}

    def register_task_asset(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        kind = self._asset_kind(payload.get("kind"))
        token = str(payload.get("upload_token") or "").strip()
        source = self.asset_staging.source_for(token, kind)
        labels = payload.get("labels") or {}
        if not isinstance(labels, dict):
            raise ProductionError("invalid_asset_labels", "素材标签必须是对象")
        result = self.adapter.register_task_asset(
            token=str(run.draft_token),
            source=source,
            kind=kind,
            identifier=str(payload.get("identifier") or "").strip(),
            display_name=str(payload.get("display_name") or "").strip(),
            nickname=str(payload.get("nickname") or "").strip(),
            labels=labels,
            expected_draft_version=self._expected_version(payload),
        )
        if result.get("status") == "rejected":
            raise ProductionError(
                "asset_validation_failed", "素材没有通过检查，尚未登记",
                status=422, details={"issues": result.get("issues", [])},
            )
        if result.get("status") == "registered":
            run.state = "waiting_for_review"
            run.updated_at = utc_now()
            self.repository.save_run(run)
            detail = self.run_detail(run_id)
            result["run"] = detail["run"]
            result["draft"] = detail["draft"]
            result["gates"] = detail["gates"]
            result["asset_manifest"] = detail["asset_manifest"]
            result["asset_policy"] = detail["asset_policy"]
            result["asset_manifest_upgrade_required"] = True
        return result

    def task_assets(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        manifest = self.asset_manifests.payload_for_run(run_id)
        allowlisted = {
            str(asset.get("metadata", {}).get("task_asset_id") or "")
            for asset in manifest["assets"]
            if isinstance(asset.get("metadata"), dict)
        }
        items = [
            {**item, "allowlisted": item["asset_id"] in allowlisted}
            for item in self.adapter.list_task_assets(str(run.draft_token))
        ]
        return {"ok": True, "run_id": run_id, "items": items}

    @staticmethod
    def _task_asset_ids(payload: dict[str, Any], field: str) -> list[str]:
        value = payload.get(field, [])
        if not isinstance(value, list) or len(value) > 100:
            raise ProductionError(
                "asset_manifest_change_invalid",
                f"{field} 必须是不超过 100 项的数组",
            )
        values = [str(item or "").strip() for item in value]
        if any(not re.fullmatch(r"asset-[0-9a-f]{12}", item) for item in values):
            raise ProductionError(
                "asset_manifest_change_invalid", f"{field} 包含无效素材 ID"
            )
        if len(values) != len(set(values)):
            raise ProductionError(
                "asset_manifest_change_invalid", f"{field} 不得包含重复素材 ID"
            )
        return values

    @staticmethod
    def _deterministic_bundle(source: Path) -> bytes:
        files: list[tuple[str, Path]] = []
        total = 0
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise ProductionError(
                    "task_asset_corrupt", "角色素材包含不安全的符号链接", status=500
                )
            if not path.is_file():
                continue
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(source.resolve())
                size = resolved.stat().st_size
            except (OSError, ValueError) as exc:
                raise ProductionError(
                    "task_asset_corrupt", "角色素材内容无法安全读取", status=500
                ) from exc
            total += size
            files.append((path.relative_to(source).as_posix(), resolved))
        if (
            not files
            or len(files) > MAX_ARCHIVE_FILES
            or total > MAX_ARCHIVE_BYTES
        ):
            raise ProductionError(
                "task_asset_corrupt", "角色素材包内容为空或超出安全限制", status=500
            )
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            for relative, path in files:
                info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        return output.getvalue()

    @staticmethod
    def _duplicate_workspace_file(error: ProductionError, artifacts: ArtifactStore):
        if error.code != "workspace_file_duplicate":
            raise error
        existing_uri = str(error.details.get("existing_uri") or "")
        if not existing_uri:
            raise error
        return artifacts.get(existing_uri)

    def _freeze_task_asset(
        self, run: ProductionRun, task_asset_id: str
    ) -> dict[str, Any]:
        if not run.production_run_id:
            raise ProductionError(
                "production_run_identity_invalid", "制作任务缺少稳定身份", status=500
            )
        record, source = self.adapter.task_asset_record(
            str(run.draft_token), task_asset_id
        )
        asset_id = str(
            uuid.uuid5(
                _TASK_ASSET_NAMESPACE,
                f"{run.production_run_id}:{task_asset_id}",
            )
        )
        kind = str(record.get("kind") or "")
        contract_kind = {
            "background": "background",
            "cg": "popup",
            "sound": "sound",
            "character": "character",
        }.get(kind)
        if contract_kind is None:
            raise ProductionError(
                "invalid_asset_kind", "任务素材类型无法写入 AssetManifest"
            )
        metadata = {
            "task_asset_id": task_asset_id,
            "engine_key": str(record.get("key") or ""),
        }
        if kind == "character":
            uri = f"workspace://assets/items/{asset_id}/bundle.zip"
            try:
                artifact = self.artifacts.commit_bytes(
                    uri,
                    self._deterministic_bundle(source),
                    kind=contract_kind,
                    media_type="application/zip",
                    metadata=metadata,
                )
            except ProductionError as exc:
                artifact = self._duplicate_workspace_file(exc, self.artifacts)
        else:
            suffix = source.suffix.casefold()
            uri = f"workspace://assets/items/{asset_id}/content{suffix}"
            media_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
            try:
                artifact = self.artifacts.commit_file(
                    uri,
                    source,
                    kind=contract_kind,
                    media_type=media_type,
                    metadata=metadata,
                )
            except ProductionError as exc:
                artifact = self._duplicate_workspace_file(exc, self.artifacts)
        return {
            "asset_id": asset_id,
            "kind": contract_kind,
            "uri": artifact.uri,
            "content_hash": artifact.content_hash,
            "display_name": str(record.get("display_name") or record.get("key") or task_asset_id),
            "media_type": artifact.media_type,
            "metadata": metadata,
        }

    @staticmethod
    def _successor_manifest(
        run: ProductionRun,
        current: dict[str, Any],
        assets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ordered = sorted(assets, key=lambda item: item["asset_id"])
        digest = sha256_bytes(canonical_json_bytes(ordered))
        manifest_id = str(
            uuid.uuid5(
                _MANIFEST_SUCCESSOR_NAMESPACE,
                f'{run.production_run_id}:{current["id"]}:{digest}',
            )
        )
        created_at = dt.datetime.fromisoformat(
            str(current["created_at"]).replace("Z", "+00:00")
        ) + dt.timedelta(microseconds=1)
        payload: dict[str, Any] = {
            "schema_version": "1.0",
            "id": manifest_id,
            "content_hash": "",
            "created_at": created_at.isoformat(),
            "assets": ordered,
        }
        payload["content_hash"] = contract_content_hash("AssetManifest", payload)
        return payload

    def upgrade_asset_manifest(
        self, run_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        unexpected = sorted(
            set(payload)
            - {
                "expected_manifest_id",
                "expected_content_hash",
                "add_task_asset_ids",
                "remove_task_asset_ids",
            }
        )
        if unexpected:
            raise ProductionError(
                "asset_manifest_change_invalid",
                "素材清单升级请求包含未知字段",
                details={"fields": unexpected},
            )
        expected_id = str(payload.get("expected_manifest_id") or "").strip()
        expected_hash = str(payload.get("expected_content_hash") or "").strip()
        try:
            if str(uuid.UUID(expected_id)) != expected_id:
                raise ValueError
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProductionError(
                "asset_manifest_reference_invalid", "expected_manifest_id 必须是规范 UUID"
            ) from exc
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_hash):
            raise ProductionError(
                "asset_manifest_reference_invalid",
                "expected_content_hash 必须是规范 SHA-256",
            )
        additions = self._task_asset_ids(payload, "add_task_asset_ids")
        removals = self._task_asset_ids(payload, "remove_task_asset_ids")
        if not additions and not removals:
            raise ProductionError(
                "asset_manifest_change_empty", "素材清单升级必须包含添加或移除项"
            )
        overlap = sorted(set(additions) & set(removals))
        if overlap:
            raise ProductionError(
                "asset_manifest_change_invalid",
                "同一素材不能同时添加和移除",
                details={"asset_ids": overlap},
            )
        run = self._run(run_id)
        current_description = self.asset_manifests.describe_for_run(run_id)
        current = self.asset_manifests.payload_for_run(run_id)
        by_task_id = {
            str(asset.get("metadata", {}).get("task_asset_id") or ""): asset
            for asset in current["assets"]
            if isinstance(asset.get("metadata"), dict)
        }
        already_applied = all(item in by_task_id for item in additions) and all(
            item not in by_task_id for item in removals
        )
        if current["id"] != expected_id or current["content_hash"] != expected_hash:
            if already_applied:
                return {
                    "ok": True,
                    "run_id": run_id,
                    "asset_manifest": current_description["reference"],
                    "asset_policy": current_description["policy"],
                    "idempotent": True,
                    "added_task_asset_ids": additions,
                    "removed_task_asset_ids": removals,
                }
            raise ProductionError(
                "asset_manifest_revision_conflict",
                "AssetManifest 已被其他操作升级",
                status=409,
                details={
                    "run_id": run_id,
                    "current_manifest_id": current["id"],
                    "current_content_hash": current["content_hash"],
                },
            )
        next_assets = [
            asset
            for asset in current["assets"]
            if str(asset.get("metadata", {}).get("task_asset_id") or "")
            not in removals
        ]
        existing_ids = {asset["metadata"].get("task_asset_id") for asset in next_assets}
        for task_asset_id in additions:
            if task_asset_id not in existing_ids:
                next_assets.append(self._freeze_task_asset(run, task_asset_id))
        if next_assets == current["assets"]:
            return {
                "ok": True,
                "run_id": run_id,
                "asset_manifest": current_description["reference"],
                "asset_policy": current_description["policy"],
                "idempotent": True,
                "added_task_asset_ids": additions,
                "removed_task_asset_ids": removals,
            }
        successor = self._successor_manifest(run, current, next_assets)
        description = self.asset_manifests.advance(
            run,
            successor,
            expected_manifest_id=expected_id,
            expected_content_hash=expected_hash,
            source_kind="task_asset_upgrade",
            selection_kind="user_asset_upgrade",
        )
        return {
            "ok": True,
            "run_id": run_id,
            "asset_manifest": description["reference"],
            "asset_policy": description["policy"],
            "idempotent": False,
            "previous_manifest_id": current["id"],
            "added_task_asset_ids": additions,
            "removed_task_asset_ids": removals,
        }

    def asset_manifest_history(self, run_id: str) -> dict[str, Any]:
        self._run(run_id)
        history = self.asset_manifests.history_for_run(run_id)
        return {
            "ok": True,
            "run_id": run_id,
            "items": [
                {
                    "asset_manifest": item["reference"],
                    "asset_policy": item["policy"],
                    "predecessor_manifest_id": item["predecessor_manifest_id"],
                    "selection_kind": item["selection_kind"],
                    "selected_at": item["selected_at"],
                }
                for item in history
            ],
        }

    def _require_task_assets_allowlisted(self, run: ProductionRun) -> None:
        manifest = self.asset_manifests.payload_for_run(run.run_id)
        allowed = {
            str(asset.get("metadata", {}).get("task_asset_id") or "")
            for asset in manifest["assets"]
            if isinstance(asset.get("metadata"), dict)
        }
        missing = sorted(
            item["asset_id"]
            for item in self.adapter.list_task_assets(str(run.draft_token))
            if item["asset_id"] not in allowed
        )
        if missing:
            raise ProductionError(
                "asset_reference_not_allowed",
                "自定义素材尚未进入当前任务的冻结白名单",
                status=409,
                details={"run_id": run.run_id, "task_asset_ids": missing},
            )

    def remove_task_asset(self, run_id: str, asset_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        current_manifest = self.asset_manifests.payload_for_run(run_id)
        was_allowlisted = any(
            asset.get("metadata", {}).get("task_asset_id") == asset_id
            for asset in current_manifest["assets"]
            if isinstance(asset.get("metadata"), dict)
        )
        self.adapter.remove_task_asset(
            token=str(run.draft_token), asset_id=asset_id,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        result = self.run_detail(run_id)
        result["asset_manifest_upgrade_required"] = was_allowlisted
        result["removed_task_asset_id"] = asset_id
        return result

    def run_resource_preview(self, run_id: str, kind: str, key: str) -> ResourcePreview:
        run = self._run(run_id)
        custom = self.adapter.task_asset_preview(str(run.draft_token), kind, key)
        if custom:
            return ResourcePreview(path=custom[0], media_type=custom[1])
        return self.resource_preview(kind, key)

    def resource_usage(self, run_id: str) -> dict[str, Any]:
        """Return safe, task-local usage locations for registered resources."""
        run = self._run(run_id)
        detail = self.adapter.draft_detail(str(run.draft_token))
        usage: dict[str, list[dict[str, Any]]] = {}

        def add(kind: str, key: str, card: dict[str, Any], label: str) -> None:
            normalized = str(key or "").strip()
            if not normalized:
                return
            usage.setdefault(f"{kind}:{normalized}", []).append(
                {"card_id": card.get("card_id"), "line_no": card.get("line_no"), "label": label}
            )

        for card in detail.get("cards", []):
            if not isinstance(card, dict):
                continue
            current = card.get("current") if isinstance(card.get("current"), dict) else {}
            cmd = str(current.get("cmd") or "").casefold()
            if cmd == "bg":
                add("backgrounds", str(current.get("arg") or ""), card, "背景")
            elif cmd in {"se", "sound"}:
                add("sounds", str(current.get("arg") or ""), card, "音效")
            cg = card.get("cg") if isinstance(card.get("cg"), dict) else None
            if cg:
                add("backgrounds", str(cg.get("background_key") or ""), card, str(cg.get("label") or "CG 背景"))

        cast = detail.get("cast") if isinstance(detail.get("cast"), dict) else {}
        cast_map = cast.get("cast") if isinstance(cast.get("cast"), dict) else {}
        for speaker, mapping in cast_map.items():
            if isinstance(mapping, dict) and mapping.get("kind") == "portrait":
                add("characters", str(mapping.get("id") or ""), {"card_id": None, "line_no": None}, f"角色映射：{speaker}")
        return {"ok": True, "run_id": run_id, "usage": usage}

    def create_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        generation_mode = str(payload.get("generation_mode") or "format_only").strip()
        if generation_mode not in {"format_only", "ai_direction"}:
            raise ProductionError(
                "invalid_generation_mode",
                "不支持的草稿生成模式",
                details={"allowed": ["format_only", "ai_direction"]},
            )
        if generation_mode == "ai_direction":
            if not self.direction_model_settings.public()["model"]["configured"]:
                raise ProductionError(
                    "direction_generation_not_configured",
                    "AI 安排演出需要先配置 1.0 模型 Provider",
                    status=409,
                )
        project = self._project_name(payload.get("project"))
        text, source_kind, source_filename = self._source_text(payload)
        upstream_release = self._upstream_script_release(payload, text)
        if upstream_release:
            existing = self._run_for_upstream_release(upstream_release)
            if existing:
                result = self.run_detail(existing.run_id)
                result["handoff"] = {
                    "kind": "script_release",
                    "idempotent": True,
                    "upstream_release": upstream_release,
                }
                return result
        release = ScriptRelease.create(project, text, source_kind)
        self.repository.save_release(release, text)

        summary = self.adapter.inspect_script(text)
        summary["source_kind"] = source_kind
        if source_filename:
            summary["source_filename"] = source_filename
        if upstream_release:
            summary["upstream_release"] = upstream_release
        draft = self.adapter.create_performance_draft(
            project=project,
            text=text,
            speakers=summary["speakers"],
            cg_keys=self.resources.cg_keys(),
        )
        now = utc_now()
        run = ProductionRun(
            run_id=new_id("run"),
            project=project,
            release_id=release.release_id,
            draft_token=draft["session"]["draft_token"],
            state="waiting_for_review",
            current_stage="preflight",
            created_at=now,
            updated_at=now,
            source_summary=summary,
            work_items=[
                WorkItem("workspace", "建立剧情工作区", "succeeded", 100, "剧本发布版本已冻结"),
                WorkItem("structure", "识别格式与场景结构", "succeeded", 100, f'{summary["scene_count"]} 个场景'),
                WorkItem("clues", "提取角色、指令与素材线索", "succeeded", 100, f'{len(summary["speakers"])} 个说话角色'),
                WorkItem("preflight", "建立初审与演出草稿", "succeeded", 100, "等待角色映射与逐卡审查"),
            ],
        )
        run.source_summary["generation_mode"] = generation_mode
        self.repository.save_run(run)
        self.asset_manifests.ensure_compatibility_manifest(run)
        result = self.run_detail(run.run_id)
        if upstream_release:
            result["handoff"] = {
                "kind": "script_release",
                "idempotent": False,
                "upstream_release": upstream_release,
            }
        return result

    def _run_for_upstream_release(
        self, upstream_release: dict[str, Any]
    ) -> ProductionRun | None:
        release_id = upstream_release["release_id"]
        content_hash = upstream_release["content_hash"]
        for run in self.repository.list_runs():
            origin = run.source_summary.get("upstream_release")
            if not isinstance(origin, dict) or origin.get("release_id") != release_id:
                continue
            if origin.get("content_hash") != content_hash:
                raise ProductionError(
                    "script_release_identity_conflict",
                    "同一写作发布版本 ID 已绑定到不同正文，已拒绝交接",
                    status=409,
                    details={"release_id": release_id, "run_id": run.run_id},
                )
            return run
        return None

    def preflight_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Inspect source text without creating a release, draft, or background job."""
        text, _, _ = self._source_text(payload)
        return self.adapter.preflight_script(
            text,
            commands=set(DIRECTIVE_COMMANDS),
            no_argument_commands=set(NO_ARGUMENT_DIRECTIVES),
        )

    def start_ai_preflight(
        self, run_id: str, *, work_item_id: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Submit a source-only AI review without changing any production state."""
        run = self._run(run_id)
        if not self.direction_model_settings.public()["model"]["configured"]:
            raise ProductionError(
                "ai_preflight_not_configured",
                "运行 AI 初审前需要先在设置中配置演出模型",
                status=409,
            )
        preflight_id = new_id("preflight")
        provider = self.direction_models.provider()

        def work(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            result = self.adapter.execute_ai_preflight(
                token=str(run.draft_token),
                preflight_id=preflight_id,
                provider=CancellableModelProvider(provider, token.is_cancelled),
            )
            token.raise_if_cancelled()
            return {
                "run_id": run_id,
                "preflight_id": preflight_id,
                "scene_count": len(result["analysis"]["scenes"]),
                "ambiguity_count": len(result["analysis"]["ambiguities"]),
            }

        model = self.direction_model_settings.public()["model"]
        job = self.jobs.submit(
            "ai_preflight",
            work,
            run_id=run_id,
            retry_context={},
            work_item_id=work_item_id,
            provider=str(model.get("provider") or "") or None,
            model_or_engine=str(model.get("model") or "") or None,
        )
        return 202, {"ok": True, "job": self._job_public(job.to_dict()), "preflight_id": preflight_id}

    def ai_preflights(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        result = self.adapter.ai_preflights(str(run.draft_token))
        return {"run_id": run_id, **result}

    def list_runs(self) -> dict[str, Any]:
        return {"ok": True, "items": [item.to_dict() for item in self.repository.list_runs()]}

    def _run(self, run_id: str) -> ProductionRun:
        return self.repository.get_run(run_id)

    def run_detail(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        draft = self.adapter.draft_detail(str(run.draft_token)) if run.draft_token else None
        gates = self._gates(run, draft)
        manifest = self.asset_manifests.describe_for_run(run_id)
        return {
            "ok": True,
            "run": run.to_dict(),
            "gates": gates,
            "draft": draft,
            "asset_manifest": manifest["reference"],
            "asset_policy": manifest["policy"],
        }

    def performance_preview(self, run_id: str) -> dict[str, Any]:
        """Build a read-only, task-local representation for the draft preview.

        This deliberately describes the current PerformanceDraft rather than a
        finished AA render. The client receives stable card IDs and safe resource
        keys only, then requests allowlisted previews through the existing routes.
        """
        run = self._run(run_id)
        detail = self.adapter.draft_detail(str(run.draft_token))
        cast_data = detail.get("cast") if isinstance(detail.get("cast"), dict) else {}
        cast = cast_data.get("cast") if isinstance(cast_data.get("cast"), dict) else {}
        background = str(cast_data.get("default_bg") or "BG_Black")
        frames: list[dict[str, Any]] = []
        for index, card in enumerate(detail.get("cards") or []):
            if not isinstance(card, dict):
                continue
            current = card.get("current") if isinstance(card.get("current"), dict) else {}
            kind = str(card.get("kind") or "")
            command = str(current.get("cmd") or "").casefold() if kind == "dir" else ""
            if command == "bg" and str(current.get("arg") or "").strip():
                background = str(current["arg"]).strip()
            speaker = str(current.get("who") or "").strip()
            mapping = cast.get(speaker) if isinstance(cast.get(speaker), dict) else {"kind": "unset"}
            cg = card.get("cg") if isinstance(card.get("cg"), dict) else None
            annotations = [
                {"kind": label, "value": str(current.get(key) or "").strip()}
                for key, label in (("face", "表情"), ("emo", "情绪"), ("act", "动作"), ("fx", "画面效果"))
                if str(current.get(key) or "").strip()
            ]
            if cg:
                background = str(cg.get("background_key") or background)
                presentation = "cg"
                title = str(cg.get("label") or "CG 段落")
                text = str(current.get("text") or "") if kind == "line" else ""
            elif kind == "line":
                presentation = "dialogue"
                title = speaker if mapping.get("kind") != "narrator" else "旁白"
                text = str(current.get("text") or "")
            elif kind == "scene":
                presentation = "scene"
                title = "场景切换"
                text = str(current.get("title") or "")
            elif kind == "background_request":
                presentation = "request"
                title = "待处理背景"
                text = str(current.get("description") or card.get("raw") or "")
            elif kind == "dir":
                presentation = "direction"
                title = f"@{command or '指令'}"
                text = str(current.get("arg") or card.get("raw") or "")
            else:
                presentation = "note"
                title = kind or "文本"
                text = str(current.get("text") or current.get("title") or card.get("raw") or "")
            frames.append(
                {
                    "index": index,
                    "card_id": str(card.get("card_id") or ""),
                    "line_no": card.get("line_no"),
                    "card_kind": kind,
                    "presentation": presentation,
                    "background_key": background,
                    "cg": (
                        {"background_key": str(cg.get("background_key") or ""), "label": str(cg.get("label") or "CG 段落")}
                        if cg else None
                    ),
                    "speaker": {
                        "name": speaker,
                        "mapping_kind": str(mapping.get("kind") or "unset"),
                        "character_id": str(mapping.get("id") or ""),
                    },
                    "title": title,
                    "text": text,
                    "annotations": annotations,
                    "review_state": str(card.get("review_state") or "pending"),
                }
            )
        return {
            "ok": True,
            "kind": "draft_performance_preview",
            "read_only": True,
            "run_id": run_id,
            "draft_version": detail.get("draft_version"),
            "frames": frames,
        }

    def direction_proposals(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        audit = self.adapter.direction_proposals(str(run.draft_token))
        return {
            "ok": True,
            "kind": "direction_proposal_audit",
            "read_only": True,
            "run_id": run_id,
            "generation_mode": run.source_summary.get("generation_mode"),
            "draft_version": self.adapter.draft_detail(str(run.draft_token)).get("draft_version"),
            **audit,
        }

    def decide_direction_proposal(self, run_id: str, proposal_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        self.adapter.decide_direction_proposal(
            token=str(run.draft_token),
            proposal_id=proposal_id,
            action=str(payload.get("action") or "").strip(),
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)


    def task_preflight_summary(self, run_id: str) -> dict[str, Any]:
        """Explain task-local production decisions without invoking an AI provider."""
        run = self._run(run_id)
        detail = self.adapter.draft_detail(str(run.draft_token))
        cast_data = detail.get("cast") if isinstance(detail.get("cast"), dict) else {}
        cast = cast_data.get("cast") if isinstance(cast_data.get("cast"), dict) else {}
        details = run.source_summary.get("speaker_details")
        if not isinstance(details, list):
            details = [{"speaker": name, "count": 0, "sample": "", "first_line": None} for name in run.source_summary.get("speakers", [])]
        speakers = []
        missing = 0
        for item in details:
            if not isinstance(item, dict):
                continue
            name = str(item.get("speaker") or "").strip()
            if not name:
                continue
            mapping = cast.get(name) if isinstance(cast.get(name), dict) else {"kind": "unset"}
            kind = str(mapping.get("kind") or "unset")
            if kind == "unset":
                missing += 1
            speakers.append(
                {
                    "speaker": name,
                    "count": int(item.get("count") or 0),
                    "sample": str(item.get("sample") or ""),
                    "first_line": item.get("first_line"),
                    "mapping": {
                        "kind": kind,
                        "name": str(mapping.get("name") or mapping.get("display_name") or mapping.get("id") or ""),
                    },
                }
            )
        requests = []
        for card in detail.get("cards", []):
            if not isinstance(card, dict) or card.get("kind") not in {"background_request", "sound_request"}:
                continue
            current = card.get("current") if isinstance(card.get("current"), dict) else {}
            requests.append(
                {
                    "card_id": str(card.get("card_id") or ""),
                    "line_no": card.get("line_no"),
                    "kind": str(card.get("kind") or ""),
                    "description": str(current.get("description") or current.get("text") or card.get("raw") or "").strip()[:100],
                    "state": str(card.get("review_state") or "pending"),
                }
            )
        diagnostics = [
            {
                "severity": str(item.get("severity") or "warning"),
                "code": str(item.get("code") or "diagnostic"),
                "message": str(item.get("message") or "需要检查的项目"),
                "line_no": item.get("line_no"),
                "card_id": item.get("card_id"),
            }
            for item in detail.get("diagnostics", []) if isinstance(item, dict)
        ]
        diagnostics.sort(key=lambda item: (0 if item["severity"] == "error" else 1, item["line_no"] or 0, item["code"]))
        if missing:
            next_action = {"stage": "mapping", "label": f"先处理 {missing} 位未映射说话者", "detail": "每位说话者都要明确使用立绘、旁白或无立绘角色，才能安全进入后续演出制作。"}
        elif requests:
            next_action = {"stage": "review", "label": f"处理 {len(requests)} 项素材请求", "detail": "在审查器内从当前任务的冻结素材清单选择背景或音效。"}
        elif detail["counts"].get("pending"):
            next_action = {"stage": "review", "label": f"审查 {detail['counts']['pending']} 张待确认卡片", "detail": "逐卡确认台词、演出和场景后，系统才会开放编译。"}
        else:
            next_action = {"stage": "review", "label": "进入编译前检查", "detail": "草稿已无待审卡片；请在审查页运行检查并确认编译门禁。"}
        return {
            "ok": True,
            "kind": "task_preflight_summary",
            "source": "frozen_draft",
            "speakers": speakers,
            "scenes": run.source_summary.get("scenes") if isinstance(run.source_summary.get("scenes"), list) else [],
            "requests": requests,
            "diagnostics": diagnostics,
            "counts": detail.get("counts") or {},
            "next_action": next_action,
        }

    def _gates(
        self, run: ProductionRun, draft: dict[str, Any] | None
    ) -> dict[str, Any]:
        caps = self.adapter.capabilities()
        if not draft:
            return {
                "preflight": {"passed": False, "blockers": ["draft_missing"]},
                "compile": {"passed": False, "blockers": ["draft_missing"]},
                "install": {"passed": False, "blockers": ["build_missing"]},
            }
        blockers = []
        if draft["counts"]["blocking_errors"]:
            blockers.append("blocking_diagnostics")
        if draft["counts"]["pending"]:
            blockers.append("pending_review")
        if caps["compile"]["state"] != "available":
            blockers.append("compile_not_configured")
        return {
            "preflight": {
                "passed": draft["counts"]["blocking_errors"] == 0,
                "blockers": ["blocking_diagnostics"] if draft["counts"]["blocking_errors"] else [],
            },
            "compile": {"passed": not blockers, "blockers": blockers},
            "install": {
                "passed": bool(run.last_build_id)
                and caps["install"]["state"] == "available",
                "blockers": ([] if run.last_build_id else ["build_missing"])
                + (
                    ["aa_workspace_not_configured"]
                    if caps["install"]["state"] != "available"
                    else []
                ),
            },
        }

    def update_cast(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        speaker = str(payload.get("speaker") or "").strip()
        mapping = payload.get("mapping")
        if not speaker or not isinstance(mapping, dict):
            raise ProductionError("invalid_cast_binding", "speaker 和 mapping 为必填项")
        if str(mapping.get("kind") or "") == "portrait":
            identifier = str(mapping.get("id") or "").strip()
            if identifier and not self.adapter.draft_resource_contains(
                str(run.draft_token), "characters", identifier
            ):
                raise ProductionError(
                    "character_not_found",
                    "所选角色不在该草稿冻结的资源索引中",
                    status=404,
                )
            if identifier:
                character = self.adapter.draft_character_detail(
                    str(run.draft_token), identifier
                )["character"]
                mapping = dict(mapping)
                # The task snapshot owns display names. Ignore stale client labels.
                mapping["name"] = str(character.get("name") or identifier)
        try:
            expected = int(payload["expected_draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionError("expected_version_required", "必须提供 expected_draft_version") from exc
        self.adapter.update_cast_binding(
            token=str(run.draft_token),
            speaker=speaker,
            mapping=mapping,
            expected_draft_version=expected,
        )
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def approve_review(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        card_ids = payload.get("card_ids")
        if card_ids is not None and not isinstance(card_ids, list):
            raise ProductionError("invalid_card_ids", "card_ids 必须是数组或 null")
        try:
            expected = int(payload["expected_draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionError("expected_version_required", "必须提供 expected_draft_version") from exc
        draft = self.adapter.approve_cards(
            token=str(run.draft_token), card_ids=card_ids, expected_draft_version=expected
        )
        run.state = "ready_to_compile" if draft["review_ready"] else "waiting_for_review"
        run.current_stage = "review_install"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    @staticmethod
    def _expected_version(payload: dict[str, Any]) -> int:
        try:
            return int(payload["expected_draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionError(
                "expected_version_required", "必须提供 expected_draft_version"
            ) from exc

    def update_card(
        self, run_id: str, card_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._run(run_id)
        patch = payload.get("patch")
        if not isinstance(patch, dict) or not patch:
            raise ProductionError("card_patch_required", "patch 必须是非空对象")
        detail = self.adapter.draft_detail(str(run.draft_token))
        card = next((item for item in detail["cards"] if item["card_id"] == card_id), None)
        if not card:
            raise ProductionError("card_not_found", "卡片不存在", status=404)
        patch = self._validated_card_patch(card, patch)
        if str(card.get("kind") or "") == "line":
            self._validate_line_performance(run, detail, card, patch)
        self.adapter.update_card(
            token=str(run.draft_token),
            card_id=card_id,
            patch=patch,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def _validate_line_performance(
        self, run: ProductionRun, detail: dict[str, Any], card: dict[str, Any], patch: dict[str, Any]
    ) -> None:
        """Keep line-level face choices within the speaker's frozen portrait mapping."""
        current = card.get("current") if isinstance(card.get("current"), dict) else {}
        face = str(patch.get("face", current.get("face") or "")).strip()
        if not face:
            return
        speaker = str(patch.get("who") or current.get("who") or "").strip()
        cast_data = detail.get("cast") if isinstance(detail.get("cast"), dict) else {}
        cast = cast_data.get("cast") if isinstance(cast_data.get("cast"), dict) else {}
        mapping = cast.get(speaker) if isinstance(cast.get(speaker), dict) else {}
        if mapping.get("kind") != "portrait":
            raise ProductionError(
                "face_requires_portrait_mapping",
                "只有已映射立绘的说话者才能设置表情；请先在角色映射中选择骨骼角色",
                status=409,
            )
        identifier = str(mapping.get("id") or "").strip()
        character = self.adapter.draft_character_detail(str(run.draft_token), identifier)["character"]
        choices = {
            str(value).strip()
            for item in character.get("faces", [])
            if isinstance(item, dict)
            for value in (item.get("id"), item.get("raw"), item.get("label"))
            if str(value or "").strip()
        }
        if face not in choices:
            raise ProductionError(
                "face_not_available_for_character",
                "所选表情不属于当前说话者映射的冻结角色素材",
                status=409,
                details={"speaker": speaker, "character": identifier},
            )

    @staticmethod
    def _validated_card_patch(card: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "line": {"who", "text", "face", "emo", "act", "fx"},
            "dir": {"cmd", "arg"}, "scene": {"title"}, "title": {"title"}, "meta": {"text"},
        }.get(str(card.get("kind") or ""))
        if allowed is None:
            raise ProductionError("card_not_editable", "这类卡片需要通过专用操作处理，不能直接编辑", status=409)
        unexpected = sorted(set(patch) - allowed)
        if unexpected:
            raise ProductionError("card_patch_field_not_allowed", "该卡片不支持修改这些字段", details={"fields": unexpected, "allowed": sorted(allowed)})
        normalized = {key: str(value) for key, value in patch.items()}
        kind = str(card.get("kind") or "")
        if kind == "dir":
            current = card.get("current") if isinstance(card.get("current"), dict) else {}
            command = normalized.get("cmd", str(current.get("cmd") or "")).strip().casefold()
            argument = normalized.get("arg", str(current.get("arg") or "")).strip()
            if command not in DIRECTIVE_COMMANDS:
                raise ProductionError("directive_not_supported", "请选择已支持的 AA 演出指令，或使用“原样 AA 指令”", details={"command": command, "allowed": sorted(DIRECTIVE_COMMANDS)})
            if command in RESOURCE_DIRECTIVES:
                raise ProductionError("directive_requires_resource_picker", "背景和音效必须从当前任务的素材选择器中选取，不能直接输入名称", status=409, details={"command": command})
            if command in NO_ARGUMENT_DIRECTIVES:
                argument = ""
            elif not argument:
                raise ProductionError("directive_argument_required", f"@{command} 需要填写参数")
            if command == "wait" and not argument.isdigit():
                raise ProductionError("directive_argument_invalid", "@wait 的参数必须是毫秒整数")
            if command == "move":
                parts = argument.split()
                if len(parts) < 2 or parts[1] not in {"1", "2", "3", "4", "5"}:
                    raise ProductionError("directive_argument_invalid", "@move 请填写“角色名 位置”，位置为 1 到 5")
            if command == "stage" and (not argument or any(not re.fullmatch(r".+@[1-5]", slot) for slot in argument.split())):
                raise ProductionError("directive_argument_invalid", "@stage 请填写“角色@位置”，位置为 1 到 5")
            return {"cmd": command, "arg": argument}
        if kind in {"line", "meta"} and "text" in normalized and not normalized["text"].strip():
            raise ProductionError("card_text_required", "文本内容不能为空")
        if kind in {"scene", "title"} and "title" in normalized and not normalized["title"].strip():
            raise ProductionError("card_title_required", "标题不能为空")
        return normalized

    def insert_card(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        kind = str(payload.get("kind") or "").strip()
        fields = payload.get("fields")
        if kind not in {"line", "dir", "scene", "meta"}:
            raise ProductionError(
                "invalid_card_kind",
                "不支持的卡片类型",
                details={"allowed": ["line", "dir", "scene", "meta"]},
            )
        if not isinstance(fields, dict):
            raise ProductionError("card_fields_required", "fields 必须是对象")
        self.adapter.insert_card(
            token=str(run.draft_token),
            after_card_id=(str(payload["after_card_id"]) if payload.get("after_card_id") else None),
            kind=kind,
            fields=fields,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def create_cg_segment(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        background_key = str(payload.get("background_key") or "").strip()
        if not self.adapter.draft_cg_background_contains(str(run.draft_token), background_key):
            raise ProductionError(
                "cg_background_not_found",
                "所选素材不是当前任务可用的自定义背景或官方 CG",
                status=404,
            )
        self.adapter.create_cg_segment(
            token=str(run.draft_token),
            start_card_id=str(payload.get("start_card_id") or ""),
            end_card_id=str(payload.get("end_card_id") or ""),
            background_key=background_key,
            label=str(payload.get("label") or ""),
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def delete_cg_segment(
        self, run_id: str, segment_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._run(run_id)
        self.adapter.delete_cg_segment(
            token=str(run.draft_token),
            segment_id=segment_id,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def move_card(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        card_id = str(payload.get("card_id") or "").strip()
        if not card_id:
            raise ProductionError("card_id_required", "card_id 为必填项")
        before = str(payload.get("before_card_id") or "").strip() or None
        self.adapter.move_card(
            token=str(run.draft_token),
            card_id=card_id,
            before_card_id=before,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def delete_card(
        self, run_id: str, card_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._run(run_id)
        self.adapter.delete_card(
            token=str(run.draft_token),
            card_id=card_id,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def resolve_background_request(
        self, run_id: str, card_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._run(run_id)
        action = str(payload.get("action") or "select").strip()
        if action not in {"select", "black"}:
            raise ProductionError(
                "invalid_background_resolution",
                "背景请求只能选择背景或使用黑屏",
            )
        background_key = (
            "BG_Black" if action == "black" else str(payload.get("background_key") or "").strip()
        )
        if not background_key:
            raise ProductionError("background_key_required", "必须选择一个背景")
        if not self.adapter.draft_resource_contains(
            str(run.draft_token), "backgrounds", background_key
        ):
            raise ProductionError("background_not_found", "所选背景不在资源索引中", status=404)
        self.adapter.resolve_background(
            token=str(run.draft_token),
            card_id=card_id,
            background_key=background_key,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def resolve_sound_request(
        self, run_id: str, card_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        run = self._run(run_id)
        action = str(payload.get("action") or "select").strip()
        if action not in {"select", "remove"}:
            raise ProductionError(
                "invalid_sound_resolution",
                "音效请求只能选择已登记音效或移除声音指令",
            )
        sound_key = str(payload.get("sound_key") or "").strip() or None
        if action == "select":
            if not sound_key:
                raise ProductionError("sound_key_required", "必须选择一个音效")
            if not self.adapter.draft_resource_contains(
                str(run.draft_token), "sounds", sound_key
            ):
                raise ProductionError("sound_not_found", "所选音效不在资源索引中", status=404)
        self.adapter.resolve_sound(
            token=str(run.draft_token),
            card_id=card_id,
            action=action,
            sound_key=sound_key,
            expected_draft_version=self._expected_version(payload),
        )
        run.state = "waiting_for_review"
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return self.run_detail(run_id)

    def validate(self, run_id: str) -> dict[str, Any]:
        run = self._run(run_id)
        self._require_task_assets_allowlisted(run)
        return self.adapter.validate(str(run.draft_token))

    def compile(
        self,
        run_id: str,
        payload: dict[str, Any],
        *,
        work_item_id: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        run = self._run(run_id)
        self._require_task_assets_allowlisted(run)
        try:
            expected = int(payload["expected_draft_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductionError("expected_version_required", "必须提供 expected_draft_version") from exc
        build_id = self.adapter.create_compile_snapshot(str(run.draft_token), expected)
        run.state = "compiling"
        run.current_stage = "review_install"
        run.pending_build_id = build_id
        run.updated_at = utc_now()
        self.repository.save_run(run)

        def commit_success() -> None:
            latest = self._run(run_id)
            latest.state = "compiled"
            latest.pending_build_id = None
            latest.last_build_id = build_id
            latest.updated_at = utc_now()
            self.repository.save_run(latest)

        def commit_failure(_exc: Exception) -> None:
            latest = self._run(run_id)
            latest.state = "compile_failed"
            latest.pending_build_id = None
            latest.updated_at = utc_now()
            self.repository.save_run(latest)

        def work(token: CancellationToken) -> JobOutcome:
            token.raise_if_cancelled()
            try:
                result = self.adapter.execute_compile_cancellable(
                    str(run.draft_token),
                    build_id,
                    cancellation_probe=token.is_cancelled,
                )
            finally:
                if token.is_cancelled():
                    self.adapter.discard_compile_output(
                        str(run.draft_token), build_id
                    )
            token.raise_if_cancelled()
            return JobOutcome(
                {"run_id": run_id, "build_id": build_id, "bundle": result},
                commit_success,
            )

        job = self.jobs.submit(
            "compile",
            work,
            run_id=run_id,
            retry_context={
                "expected_draft_version": expected,
                "build_id": build_id,
            },
            work_item_id=work_item_id,
            model_or_engine="azurearchive-0.9.3",
            on_failure=commit_failure,
            on_cancel=lambda: self.adapter.discard_compile_output(
                str(run.draft_token), build_id
            ),
        )
        return 202, {"ok": True, "job": self._job_public(job.to_dict()), "build_id": build_id}

    def job_detail(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise ProductionError("job_not_found", "后台任务不存在", status=404)
        return {"ok": True, "job": self._job_public(job.to_dict())}

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        job = self.jobs.get(job_id)
        if not job:
            raise ProductionError("job_not_found", "后台任务不存在", status=404)
        if not self.jobs.cancel(job_id):
            raise ProductionError(
                "job_not_cancellable",
                "只能取消排队中或运行中的任务",
                status=409,
                details={"state": job.state},
            )
        if job.run_id and job.kind in {"compile", "direction_generation"}:
            run = self._run(job.run_id)
            run.state = "cancelled"
            if job.kind == "compile":
                run.pending_build_id = None
            run.updated_at = utc_now()
            self.repository.save_run(run)
        cancelled = self.jobs.get(job_id)
        return {"ok": True, "job": self._job_public((cancelled or job).to_dict())}

    def retry_job(self, job_id: str) -> dict[str, Any]:
        """Resubmit a failed stage using only persisted, non-sensitive inputs."""
        job = self.jobs.get(job_id)
        if not job:
            raise ProductionError("job_not_found", "后台任务不存在", status=404)
        if job.state not in {"failed", "abandoned", "interrupted"}:
            raise ProductionError(
                "job_not_retryable",
                "只能重新提交已失败或服务重启后放弃的任务",
                status=409,
                details={"state": job.state},
            )

        context = job.retry_context if isinstance(job.retry_context, dict) else {}
        kind = job.kind
        run_id = job.run_id
        if kind == "model_connection_test":
            _, response = self.test_direction_model(work_item_id=job.work_item_id)
        elif kind == "ai_preflight" and run_id:
            _, response = self.start_ai_preflight(run_id, work_item_id=job.work_item_id)
        elif kind in {"cg_advice", "direction_generation", "compile"} and run_id:
            if "expected_draft_version" not in context:
                raise ProductionError(
                    "job_retry_unavailable",
                    "旧任务没有保存可恢复的草稿版本，无法安全重试",
                    status=409,
                    details={"kind": kind},
                )
            try:
                expected = int(context["expected_draft_version"])
            except (TypeError, ValueError) as exc:
                raise ProductionError(
                    "job_retry_unavailable",
                    "任务的草稿版本信息无效，无法安全重试",
                    status=409,
                    details={"kind": kind},
                ) from exc
            payload: dict[str, Any] = {"expected_draft_version": expected}
            if kind == "cg_advice":
                if not {"start_card_id", "end_card_id"}.issubset(context):
                    raise ProductionError(
                        "job_retry_unavailable",
                        "旧 CG 咨询任务缺少卡片范围，无法安全重试",
                        status=409,
                        details={"kind": kind},
                    )
                payload.update(
                    start_card_id=str(context.get("start_card_id") or ""),
                    end_card_id=str(context.get("end_card_id") or ""),
                )
                _, response = self.request_cg_advice(
                    run_id, payload, work_item_id=job.work_item_id
                )
            elif kind == "direction_generation":
                payload["story_type"] = str(context.get("story_type") or "auto")
                _, response = self.generate_direction(
                    run_id, payload, work_item_id=job.work_item_id
                )
            else:
                _, response = self.compile(
                    run_id, payload, work_item_id=job.work_item_id
                )
        else:
            raise ProductionError(
                "job_retry_unavailable",
                "该任务类型或关联任务信息不足，无法安全重试",
                status=409,
                details={"kind": kind},
            )

        return {"ok": True, "retried_from": job_id, "job": response["job"]}

    def list_jobs(self) -> dict[str, Any]:
        return {"ok": True, "items": [self._job_public(job.to_dict()) for job in self.jobs.list()]}

    @staticmethod
    def _job_public(job: dict[str, Any]) -> dict[str, Any]:
        kind = str(job.get("kind") or "")
        state = str(job.get("state") or "")
        retry_context = job.get("retry_context") if isinstance(job.get("retry_context"), dict) else {}
        retryable = state in {"failed", "abandoned", "interrupted"} and (
            kind == "model_connection_test"
            or (kind == "ai_preflight" and bool(job.get("run_id")))
            or (
                kind in {"cg_advice", "direction_generation", "compile"}
                and bool(job.get("run_id"))
                and "expected_draft_version" in retry_context
                and (
                    kind != "cg_advice"
                    or {"start_card_id", "end_card_id"}.issubset(retry_context)
                )
            )
        )
        label = {
            "compile": "编译 AA 工程",
            "direction_generation": "AI 安排演出",
            "ai_preflight": "AI 初审（只读建议）",
            "model_connection_test": "测试演出模型连接",
            "cg_advice": "生成 CG 制作意见",
        }.get(kind, kind or "后台任务")
        if state == "succeeded":
            next_action = {"label": "已完成", "detail": "结果已写回关联任务。", "stage": None}
        elif state == "cancelled":
            next_action = {"label": "已取消", "detail": "已阻止该次任务提交结果。", "stage": None}
        elif state in {"failed", "abandoned", "interrupted"}:
            stage = "mapping" if kind == "ai_preflight" else "review" if kind in {"compile", "direction_generation"} else None
            next_action = {
                "label": "查看失败原因并回到任务处理",
                "detail": "修正问题后，从对应步骤重新提交；系统不会自动覆盖现有草稿。",
                "stage": stage,
            }
        else:
            next_action = {"label": "正在执行", "detail": "完成后任务状态会自动更新。", "stage": None}
        public = {key: value for key, value in job.items() if key != "retry_context"}
        error = public.get("error")
        if isinstance(error, dict):
            public["error"] = {
                "code": str(error.get("code") or "job_failed"),
                "message": f"{label}未完成，请检查对应阶段后重试。",
            }
        result = public.get("result")
        if kind == "compile" and isinstance(result, dict):
            public["result"] = dict(result)
            bundle = result.get("bundle")
            if isinstance(bundle, dict):
                public_bundle = dict(bundle)
                public_bundle.pop("bundle_dir", None)
                public["result"]["bundle"] = public_bundle
        return {
            **public,
            "retryable": retryable,
            "retry_label": "重试此阶段" if retryable else None,
            "label": label,
            "next_action": next_action,
        }

    def install(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run = self._run(run_id)
        build_id = str(payload.get("build_id") or run.last_build_id or "").strip()
        if not build_id:
            raise ProductionError("build_required", "安装前必须先完成编译", status=409)
        if not BUILD_ID.fullmatch(build_id):
            raise ProductionError("invalid_build_id", "构建 ID 无效")
        if run.state != "compiled" or build_id != run.last_build_id:
            raise ProductionError(
                "build_not_installable",
                "只能安装当前制作任务最近一次成功完成的构建",
                status=409,
                details={"state": run.state, "last_build_id": run.last_build_id},
            )
        result = self.adapter.install(
            token=str(run.draft_token),
            build_id=build_id,
            category=str(payload.get("category") or ""),
            story_name=(str(payload["story_name"]) if payload.get("story_name") else None),
        )
        run.state = "installed"
        run.last_installed_project = str(result.get("project") or run.project)
        run.updated_at = utc_now()
        self.repository.save_run(run)
        return {
            "ok": True,
            "run": run.to_dict(),
            "install": {
                "ok": bool(result.get("ok", True)),
                "project": str(result.get("project") or ""),
                "source_project": str(result.get("source_project") or ""),
                "installed_build_id": str(result.get("installed_build_id") or build_id),
            },
        }

    def _installable_build(self, run_id: str, build_id: str | None = None) -> tuple[ProductionRun, str]:
        run = self._run(run_id)
        selected = str(build_id or run.last_build_id or "").strip()
        if not selected:
            raise ProductionError("build_required", "必须先完成编译", status=409)
        if not BUILD_ID.fullmatch(selected):
            raise ProductionError("invalid_build_id", "构建 ID 无效")
        if selected != run.last_build_id or run.state not in {"compiled", "installed"}:
            raise ProductionError(
                "build_not_installable",
                "只能查看当前制作任务最近一次成功构建的安装信息",
                status=409,
            )
        return run, selected

    def install_options(self, run_id: str, build_id: str | None = None) -> dict[str, Any]:
        run, selected = self._installable_build(run_id, build_id)
        return self.adapter.install_options(token=str(run.draft_token), build_id=selected)

    def check_install(self, run_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        run, selected = self._installable_build(
            run_id, str(payload.get("build_id") or "") or None
        )
        category = str(payload.get("category") or "")
        story_name = (
            str(payload["story_name"])
            if payload.get("story_name") is not None
            else None
        )

        def work(token: CancellationToken) -> dict[str, Any]:
            token.raise_if_cancelled()
            result = self.adapter.check_install_target(
                token=str(run.draft_token),
                build_id=selected,
                category=category,
                story_name=story_name,
            )
            token.raise_if_cancelled()
            return result

        _, result = self.jobs.run_sync(
            "install_preflight",
            work,
            run_id=run_id,
            retry_context={
                "build_id": selected,
                "category": category,
                "story_name": story_name,
            },
            model_or_engine="azurearchive-0.9.3",
        )
        return result
