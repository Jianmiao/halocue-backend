from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .errors import DomainError
from .service import WritingService


class WritingRequestHandler(BaseHTTPRequestHandler):
    service: WritingService
    static_dir: Path

    def log_message(self, format, *args):
        return

    def _headers(self, status: int, content_type: str, length: int):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'")
        self.end_headers()

    def _json(self, value, status=200, *, content_type="application/json; charset=utf-8"):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _bytes(self, body: bytes, content_type: str, status: int = 200):
        self._headers(status, content_type, len(body))
        self.wfile.write(body)

    def _body(self):
        if self.headers.get("Transfer-Encoding"):
            raise DomainError("unsupported_transfer_encoding", "当前服务只接受带 Content-Length 的请求。")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise DomainError("invalid_content_length", "请求长度无效。") from exc
        if length < 0:
            raise DomainError("invalid_content_length", "请求长度无效。")
        if length > 8_000_000:
            raise DomainError("payload_too_large", "请求内容过大。", status=413)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise DomainError("invalid_json", "请求不是有效 JSON。") from exc

    def _wants_api_error_v1(self) -> bool:
        accept = self.headers.get("Accept", "")
        return "application/vnd.halocue.api-error+json" in accept and (
            "version=1.0" in accept or 'version="1.0"' in accept
        )

    def _parts(self):
        return [item for item in urlparse(self.path).path.split("/") if item]

    def do_GET(self):
        try:
            parts = self._parts()
            if parts == ["api", "v1", "health"]:
                return self._json(self.service.health())
            if parts == ["api", "v1", "capabilities"]:
                return self._json({"ok": True, "data": self.service.capabilities()})
            if parts == ["api", "v1", "official-references", "search"]:
                query = parse_qs(urlparse(self.path).query)
                return self._json({"ok": True, "data": self.service.search_official_references(query.get("q", [""])[0], query.get("limit", [12])[0])})
            if parts == ["api", "v1", "works"]:
                return self._json({"ok": True, "data": self.service.list_works()})
            if parts == ["api", "v1", "settings", "writing-model"]:
                return self._json(self.service.writing_model_settings_public())
            if parts == ["api", "v1", "settings", "preferences"]:
                return self._json(self.service.user_preferences())
            if parts == ["api", "v1", "settings", "diagnostics"]:
                return self._json(self.service.system_diagnostics())
            if len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "attachments" and parts[6] == "content":
                content_type, body = self.service.get_conversation_attachment(parts[3], parts[5])
                return self._bytes(body, content_type)
            if len(parts) == 4 and parts[:3] == ["api", "v1", "works"]:
                return self._json({"ok": True, "data": self.service.get_work(parts[3])})
            if len(parts) == 4 and parts[:3] == ["api", "v1", "releases"]:
                return self._json({"ok": True, "data": self.service.get_release(parts[3])})
            return self._static(urlparse(self.path).path)
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:
            self._error(DomainError("internal_error", "写作服务发生内部错误。", status=500, details={"type": type(exc).__name__}))

    def do_POST(self):
        try:
            parts = self._parts()
            payload = self._body()
            result = None
            if parts == ["api", "v1", "settings", "writing-model"]:
                result = self.service.configure_writing_model(payload)
                return self._json(result)
            if parts == ["api", "v1", "settings", "writing-model", "fetch-models"]:
                result = self.service.fetch_writing_models(payload)
                return self._json({"ok": True, "models": result})
            if parts == ["api", "v1", "settings", "writing-model", "test"]:
                result = self.service.test_writing_model(payload)
                return self._json(result)
            if parts == ["api", "v1", "settings", "preferences"]:
                result = self.service.save_user_preferences(payload)
                return self._json(result)
            if parts == ["api", "v1", "feedback"]:
                result = self.service.submit_feedback(payload)
                return self._json({"ok": True, "data": result}, 201)
            if parts == ["api", "v1", "works"]:
                result = self.service.create_work(payload)
                return self._json({"ok": True, "data": result}, 201)
            if len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads":
                result = self.service.create_conversation_thread(parts[3], payload)
                return self._json({"ok": True, "data": result}, 201)
            if len(parts) == 6 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads":
                result = self.service.update_conversation_thread(parts[3], parts[5], payload)
                return self._json({"ok": True, "data": result})
            if len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads" and parts[6] == "attachments":
                result = self.service.create_conversation_attachment(parts[3], parts[5], payload)
                return self._json({"ok": True, "data": result}, 201)
            if len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "brief":
                result = self.service.save_brief(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "blueprint:generate":
                result = self.service.generate_blueprint(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "blueprint:confirm":
                result = self.service.confirm_blueprint(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "canon":
                result = self.service.save_work_canon(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "character-cards":
                result = self.service.save_character_card(parts[3], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "character-cards" and parts[6] == "archive":
                result = self.service.archive_character_card(parts[3], parts[5], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "world-bible":
                result = self.service.save_world_bible(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "world-bible:starter":
                result = self.service.apply_ba_world_starter(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "reference-files":
                result = self.service.create_reference_file(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "official-references:import":
                result = self.service.import_official_reference(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "volumes":
                result = self.service.create_volume(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "chapters":
                result = self.service.create_chapter(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "structure:reorder":
                result = self.service.reorder_structure(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "writing-target":
                result = self.service.set_writing_target(parts[3], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "chapters" and parts[6] == "scenes":
                result = self.service.create_scene(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "context:assemble":
                result = self.service.assemble_context(parts[3], parts[5])
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "context:configure":
                result = self.service.configure_scene_context(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "contract":
                result = self.service.update_scene_contract(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "manuscript":
                result = self.service.save_scene_manuscript(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "candidate:generate":
                result = self.service.generate_scene_candidate(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "agent:run":
                result = self.service.run_scene_agent(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "agent:rewrite":
                result = self.service.run_scene_rewrite_agent(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "scenes" and parts[6] == "review":
                result = self.service.review_scene(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads" and parts[6] == "messages":
                result = self.service.post_conversation_message(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads" and parts[6] == "settings":
                result = self.service.update_conversation_settings(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads" and parts[6] == "proposal:organize":
                result = self.service.organize_conversation_proposal(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "threads" and parts[6] == "knowledge:propose":
                result = self.service.propose_conversation_knowledge(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "findings" and parts[6] == "resolve":
                result = self.service.resolve_review_finding(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "proposals" and parts[6] == "accept":
                result = self.service.accept_proposal(parts[3], parts[5], payload)
            elif len(parts) == 7 and parts[:3] == ["api", "v1", "works"] and parts[4] == "proposals" and parts[6] == "reject":
                result = self.service.reject_proposal(parts[3], parts[5], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "releases:freeze":
                result = self.service.freeze_release(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "works"] and parts[4] == "release:review":
                result = self.service.review_release(parts[3], payload)
            elif len(parts) == 5 and parts[:3] == ["api", "v1", "releases"] and parts[4] == "handoff":
                result = self.service.handoff_release(parts[3])
            else:
                raise DomainError("route_not_found", "接口不存在。", status=404)
            return self._json({"ok": True, "data": result})
        except DomainError as exc:
            self._error(exc)
        except Exception as exc:
            self._error(DomainError("internal_error", "写作服务发生内部错误。", status=500, details={"type": type(exc).__name__}))

    def _error(self, exc: DomainError):
        if self._wants_api_error_v1():
            self._json(
                exc.to_api_error_payload(),
                exc.status,
                content_type="application/vnd.halocue.api-error+json; version=1.0",
            )
            return
        self._json(exc.to_payload(), exc.status)

    def _static(self, path: str):
        relative = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (self.static_dir / relative).resolve()
        if self.static_dir not in target.parents or not target.is_file():
            raise DomainError("not_found", "页面不存在。", status=404)
        body = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in ("application/javascript", "application/json"):
            content_type += "; charset=utf-8"
        self._headers(200, content_type, len(body))
        self.wfile.write(body)


def make_handler(service: WritingService, static_dir: Path):
    class Handler(WritingRequestHandler):
        pass

    Handler.service = service
    Handler.static_dir = static_dir.resolve()
    return Handler
