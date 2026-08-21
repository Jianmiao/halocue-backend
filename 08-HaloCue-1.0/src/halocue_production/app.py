from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .errors import ProductionError
from .service import ProductionService


RUN_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)$")
RUN_PREFLIGHT_SUMMARY_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/preflight-summary$")
RUN_AI_PREFLIGHTS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/ai-preflights$")
RUN_PERFORMANCE_PREVIEW_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/performance-preview$")
RUN_PERFORMANCE_DRAFTS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/performance-drafts$")
RUN_PERFORMANCE_DRAFT_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/performance-drafts/([^/]+)$")
RUN_PERFORMANCE_DRAFT_REVISIONS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/performance-drafts/([^/]+)/revisions$")
RUN_PERFORMANCE_DRAFT_OPERATION_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/performance-drafts/([^/]+)/operations$")
RUN_DIRECTION_PROPOSALS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/direction-proposals$")
RUN_DIRECTION_PROPOSAL_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/direction-proposals/(prop-[0-9A-Za-z-]+)$")
RUN_CG_ADVICE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cg-advice$")
RUN_ACTION_ROUTE = re.compile(
    r"^/api/v1/production-runs/([^/]+)/(cast-bindings|review/approve|validate|compile|install|install-check|direction-generation)$"
)
INSTALL_OPTIONS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/install-options$")
JOB_ROUTE = re.compile(r"^/api/v1/jobs/([^/]+)$")
CARD_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cards/([^/]+)$")
CARDS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cards$")
CARDS_MOVE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cards/move$")
CG_SEGMENTS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cg-segments$")
CG_SEGMENT_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/cg-segments/([^/]+)$")
RUN_RESOURCE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/resources/(characters|backgrounds|cg-backgrounds|sounds|cg)$")
RUN_CHARACTER_RESOURCE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/resources/characters/([^/]+)$")
RUN_RESOURCE_USAGE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/resource-usage$")
RUN_ASSET_MANIFESTS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/asset-manifests$")
RUN_ASSETS_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/assets$")
RUN_ASSET_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/assets/(asset-[0-9a-f]{12})$")
RUN_ASSET_VALIDATE_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/assets/validate$")
RUN_RESOURCE_PREVIEW_ROUTE = re.compile(r"^/api/v1/production-runs/([^/]+)/resources/(characters|backgrounds|cg)/([^/]+)/preview$")
CARD_RESOLUTION_ROUTE = re.compile(
    r"^/api/v1/production-runs/([^/]+)/cards/([^/]+)/(background-resolution|sound-resolution)$"
)
RESOURCE_ROUTE = re.compile(r"^/api/v1/resources/(characters|backgrounds|sounds|cg)$")
CHARACTER_RESOURCE_ROUTE = re.compile(r"^/api/v1/resources/characters/([^/]+)$")
RESOURCE_PREVIEW_ROUTE = re.compile(r"^/api/v1/resources/(characters|backgrounds|cg)/([^/]+)/preview$")


class ProductionHandler(BaseHTTPRequestHandler):
    service: ProductionService
    server_version = "HaloCueProduction/1.0"
    ui_root = Path(__file__).resolve().parents[2] / "ui"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _headers(self, status: int, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self._development_cors()
        self.end_headers()

    def _development_cors(self) -> None:
        """Permit the repository's static local preview and no other origins."""
        origin = self.headers.get("Origin")
        if origin in {"http://127.0.0.1:8891", "http://localhost:8891"}:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status, len(body))
        self.wfile.write(body)

    def _wants_api_error_v1(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "application/vnd.halocue.api-error+json" in accept and (
            "version=1.0" in accept or "version=\"1.0\"" in accept
        )

    def _send_asset(self, path: Path) -> None:
        if not path.is_file() or not path.is_relative_to(self.ui_root):
            raise ProductionError("route_not_found", "页面资源不存在", status=404)
        body = path.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type",
            mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self._development_cors()
        self.end_headers()
        self.wfile.write(body)

    def _send_preview(self, path: Path, media_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "private, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self._development_cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict[str, Any]:
        if self.headers.get("Transfer-Encoding"):
            raise ProductionError(
                "unsupported_transfer_encoding",
                "当前服务只接受带 Content-Length 的请求",
            )
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ProductionError("invalid_content_length", "请求长度无效") from exc
        if length < 0:
            raise ProductionError("invalid_content_length", "请求长度无效")
        if length > 6 * 1024 * 1024:
            raise ProductionError("request_too_large", "请求不能超过 6 MiB", status=413)
        try:
            value = json.loads(self.rfile.read(length) or b"{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProductionError("invalid_json", "请求体不是合法 JSON") from exc
        if not isinstance(value, dict):
            raise ProductionError("invalid_json_object", "请求体必须是 JSON 对象")
        return value

    def _upload(self) -> tuple[str, bytes]:
        if self.headers.get("Transfer-Encoding"):
            raise ProductionError(
                "unsupported_transfer_encoding",
                "当前服务只接受带 Content-Length 的请求",
            )
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise ProductionError("invalid_content_length", "请求长度无效") from exc
        if length <= 0:
            raise ProductionError("upload_empty", "上传文件不能为空")
        if length > 65 * 1024 * 1024:
            raise ProductionError("upload_too_large", "单个素材不能超过 64 MiB", status=413)
        filename = unquote(self.headers.get("X-HaloCue-Filename") or "")
        if not filename:
            raise ProductionError("upload_name_required", "上传时必须提供文件名")
        return filename, self.rfile.read(length)

    def _dispatch(self, method: str) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        if method == "GET" and path == "/api/v1/health":
            return 200, self.service.health()
        if method == "GET" and path == "/api/v1/capabilities":
            return 200, {"ok": True, "capabilities": self.service.capabilities()}
        if method == "GET" and path == "/api/v1/production-adapters":
            return 200, self.service.adapter_capabilities()
        if path == "/api/v1/settings/direction-model":
            if method == "GET":
                return 200, self.service.direction_model_settings_public()
            if method == "POST":
                return 200, self.service.configure_direction_model(self._body())
        if path == "/api/v1/settings/direction-model/fetch-models" and method == "POST":
            return 200, {"ok": True, "models": self.service.fetch_direction_models(self._body())}
        if path == "/api/v1/settings/direction-model/test" and method == "POST":
            return self.service.test_direction_model()
        match = RESOURCE_ROUTE.fullmatch(path)
        if match and method == "GET":
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["80"])[0])
            except ValueError as exc:
                raise ProductionError("invalid_pagination", "分页参数必须是整数") from exc
            return 200, self.service.list_resources(
                match.group(1),
                query=query.get("q", [""])[0],
                offset=offset,
                limit=limit,
            )
        match = CHARACTER_RESOURCE_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.character_resource(match.group(1))
        if path == "/api/v1/settings/aa-workspace":
            if method == "GET":
                return 200, self.service.aa_workspace_settings()
            if method == "POST":
                return 200, self.service.configure_aa_workspace(self._body())
        if path == "/api/v1/settings/aa-environment":
            if method == "GET":
                return 200, self.service.inspect_aa_environment()
            if method == "POST":
                return 200, self.service.inspect_aa_environment(self._body())
        if path == "/api/v1/production-runs":
            if method == "GET":
                return 200, self.service.list_runs()
            if method == "POST":
                result = self.service.create_run(self._body())
                status = 200 if result.get("handoff", {}).get("idempotent") else 201
                return status, result
        if path == "/api/v1/script-preflight" and method == "POST":
            return 200, self.service.preflight_source(self._body())
        match = RUN_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.run_detail(match.group(1))
        match = RUN_PREFLIGHT_SUMMARY_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.task_preflight_summary(match.group(1))
        match = RUN_AI_PREFLIGHTS_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                return 200, self.service.ai_preflights(match.group(1))
            if method == "POST":
                return self.service.start_ai_preflight(match.group(1))
        match = RUN_PERFORMANCE_PREVIEW_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.performance_preview(match.group(1))
        match = RUN_PERFORMANCE_DRAFTS_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                return 200, self.service.formal_performance_draft_revisions(match.group(1))
            if method == "POST":
                return 201, self.service.create_formal_performance_draft(
                    match.group(1), self._body()
                )
        match = RUN_PERFORMANCE_DRAFT_REVISIONS_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.formal_performance_draft_revisions(match.group(1))
        match = RUN_PERFORMANCE_DRAFT_OPERATION_ROUTE.fullmatch(path)
        if match and method == "POST":
            payload = self._body()
            current = self.service.formal_performance_draft(match.group(1))
            if current["performance_draft"]["draft_id"] != match.group(2):
                raise ProductionError(
                    "performance_draft_not_found",
                    "PerformanceDraft 不属于当前制作任务。",
                    status=404,
                )
            payload["revision_id"] = payload.get("revision_id") or current[
                "performance_draft"
            ]["revision_id"]
            return self.service.submit_formal_performance_operation(
                match.group(1), payload
            )
        match = RUN_PERFORMANCE_DRAFT_ROUTE.fullmatch(path)
        if match:
            run_id, draft_id = match.groups()
            if method == "GET":
                current = self.service.formal_performance_draft(run_id)
                if current["performance_draft"]["draft_id"] != draft_id:
                    raise ProductionError(
                        "performance_draft_not_found",
                        "PerformanceDraft 不属于当前制作任务。",
                        status=404,
                        details={"draft_id": draft_id, "run_id": run_id},
                    )
                return 200, current
            if method == "PATCH":
                payload = self._body()
                return 200, self.service.update_formal_performance_draft(
                    run_id, payload, draft_id
                )
        match = RUN_PERFORMANCE_DRAFT_ROUTE.fullmatch(path)
        if match and method == "POST":
            run_id, draft_id = match.groups()
            payload = self._body()
            if payload.get("action") == "review":
                current = self.service.formal_performance_draft(run_id)
                if current["performance_draft"]["draft_id"] != draft_id:
                    raise ProductionError(
                        "performance_draft_not_found",
                        "PerformanceDraft 不属于当前制作任务。",
                        status=404,
                    )
                payload["revision_id"] = payload.get("revision_id") or current[
                    "performance_draft"
                ]["revision_id"]
                result = self.service.review_formal_performance_draft(run_id, payload)
                return 200, result
            if payload.get("action") == "validate":
                current = self.service.formal_performance_draft(run_id)
                if current["performance_draft"]["draft_id"] != draft_id:
                    raise ProductionError(
                        "performance_draft_not_found",
                        "PerformanceDraft 不属于当前制作任务。",
                        status=404,
                    )
                payload["revision_id"] = payload.get("revision_id") or current[
                    "performance_draft"
                ]["revision_id"]
                return self.service.submit_formal_performance_validation(run_id, payload)
        match = RUN_DIRECTION_PROPOSALS_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.direction_proposals(match.group(1))
        match = RUN_DIRECTION_PROPOSAL_ROUTE.fullmatch(path)
        if match and method == "POST":
            return 200, self.service.decide_direction_proposal(match.group(1), match.group(2), self._body())
        match = RUN_CG_ADVICE_ROUTE.fullmatch(path)
        if match and method == "POST":
            return self.service.request_cg_advice(match.group(1), self._body())
        match = RUN_RESOURCE_ROUTE.fullmatch(path)
        if match and method == "GET":
            try:
                offset = int(query.get("offset", ["0"])[0])
                limit = int(query.get("limit", ["80"])[0])
            except ValueError as exc:
                raise ProductionError("invalid_pagination", "分页参数必须是整数") from exc
            return 200, self.service.list_run_resources(
                match.group(1), match.group(2), query=query.get("q", [""])[0], offset=offset, limit=limit
            )
        match = RUN_CHARACTER_RESOURCE_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.run_character_resource(match.group(1), match.group(2))
        match = RUN_RESOURCE_USAGE_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.resource_usage(match.group(1))
        match = RUN_ASSET_MANIFESTS_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                return 200, self.service.asset_manifest_history(match.group(1))
            if method == "POST":
                return 201, self.service.upgrade_asset_manifest(
                    match.group(1), self._body()
                )
        match = RUN_ASSETS_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                return 200, self.service.task_assets(match.group(1))
            if method == "POST":
                filename, content = self._upload()
                return 201, self.service.upload_asset(filename=filename, content=content)
        match = RUN_ASSET_VALIDATE_ROUTE.fullmatch(path)
        if match and method == "POST":
            return 200, self.service.validate_task_asset(match.group(1), self._body())
        match = RUN_ASSETS_ROUTE.fullmatch(path)
        if match and method == "PUT":
            return 201, self.service.register_task_asset(match.group(1), self._body())
        match = RUN_ASSET_ROUTE.fullmatch(path)
        if match and method == "DELETE":
            return 200, self.service.remove_task_asset(match.group(1), match.group(2), self._body())
        match = INSTALL_OPTIONS_ROUTE.fullmatch(path)
        if match and method == "GET":
            return 200, self.service.install_options(
                match.group(1), query.get("build_id", [None])[0]
            )
        match = JOB_ROUTE.fullmatch(path)
        if match:
            if method == "GET":
                return 200, self.service.job_detail(match.group(1))
            if method == "POST" and query.get("action", [""])[0] == "cancel":
                return 200, self.service.cancel_job(match.group(1))
            if method == "POST" and query.get("action", [""])[0] == "retry":
                return 202, self.service.retry_job(match.group(1))
        if path == "/api/v1/jobs" and method == "GET":
            return 200, self.service.list_jobs()
        match = CARDS_MOVE_ROUTE.fullmatch(path)
        if match and method == "POST":
            return 200, self.service.move_card(match.group(1), self._body())
        match = CG_SEGMENTS_ROUTE.fullmatch(path)
        if match and method == "POST":
            return 201, self.service.create_cg_segment(match.group(1), self._body())
        match = CG_SEGMENT_ROUTE.fullmatch(path)
        if match and method == "DELETE":
            return 200, self.service.delete_cg_segment(
                match.group(1), match.group(2), self._body()
            )
        match = CARDS_ROUTE.fullmatch(path)
        if match and method == "POST":
            return 201, self.service.insert_card(match.group(1), self._body())
        match = CARD_RESOLUTION_ROUTE.fullmatch(path)
        if match and method == "POST":
            run_id, card_id, action = match.groups()
            if action == "background-resolution":
                return 200, self.service.resolve_background_request(
                    run_id, card_id, self._body()
                )
            return 200, self.service.resolve_sound_request(
                run_id, card_id, self._body()
            )
        match = CARD_ROUTE.fullmatch(path)
        if match and method == "PATCH":
            return 200, self.service.update_card(match.group(1), match.group(2), self._body())
        if match and method == "DELETE":
            return 200, self.service.delete_card(match.group(1), match.group(2), self._body())
        match = RUN_ACTION_ROUTE.fullmatch(path)
        if match and method == "POST":
            run_id, action = match.groups()
            payload = self._body()
            if action == "cast-bindings":
                return 200, self.service.update_cast(run_id, payload)
            if action == "review/approve":
                return 200, self.service.approve_review(run_id, payload)
            if action == "validate":
                return 200, self.service.validate(run_id)
            if action == "compile":
                return self.service.compile(run_id, payload)
            if action == "install":
                return 200, self.service.install(run_id, payload)
            if action == "install-check":
                return 200, self.service.check_install(run_id, payload)
            if action == "direction-generation":
                return self.service.generate_direction(run_id, payload)
        raise ProductionError("route_not_found", "接口不存在", status=404)

    def _handle(self, method: str) -> None:
        try:
            path = unquote(urlparse(self.path).path)
            if method == "GET" and path in {"/", "/index.html"}:
                self._send_asset(self.ui_root / "index.html")
                return
            if method == "GET" and path in {"/app.css", "/previews.css", "/preflight.css", "/cg-responsive.css", "/workspace-migration.css", "/app.js"}:
                self._send_asset(self.ui_root / path.lstrip("/"))
                return
            match = RESOURCE_PREVIEW_ROUTE.fullmatch(path)
            if method == "GET" and match:
                preview = self.service.resource_preview(match.group(1), match.group(2))
                self._send_preview(preview.path, preview.media_type)
                return
            match = RUN_RESOURCE_PREVIEW_ROUTE.fullmatch(path)
            if method == "GET" and match:
                preview = self.service.run_resource_preview(*match.groups())
                self._send_preview(preview.path, preview.media_type)
                return
            status, payload = self._dispatch(method)
        except ProductionError as exc:
            self._send(
                exc.status,
                exc.to_api_error_payload()
                if self._wants_api_error_v1()
                else exc.to_payload(),
            )
        except Exception as exc:
            error = ProductionError(
                "internal_error", "服务器处理失败", status=500, details={"type": type(exc).__name__}
            )
            self._send(
                500,
                error.to_api_error_payload()
                if self._wants_api_error_v1()
                else error.to_payload(),
            )
        else:
            self._send(status, payload)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PATCH(self) -> None:
        self._handle("PATCH")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")


def create_server(service: ProductionService, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundProductionHandler", (ProductionHandler,), {"service": service})
    return ThreadingHTTPServer((host, port), handler)
