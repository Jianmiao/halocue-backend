from __future__ import annotations

import json
import hashlib
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager

from halocue_production.app import create_server
from halocue_production.contracts import validate_contract
from halocue_production.service import ProductionService
from test_formal_inputs import formal_request
from test_service import configured_resource_settings
from PIL import Image
from io import BytesIO


@contextmanager
def api(settings):
    service = ProductionService(settings)
    server = create_server(service, "127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        service.jobs.close()
        thread.join(timeout=2)


def request(base: str, path: str, payload=None, method="GET"):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        base + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, dict(response.headers), json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), json.loads(error.read())


def upload(base: str, path: str, filename: str, content: bytes):
    req = urllib.request.Request(
        base + path, data=content, method="POST",
        headers={"Content-Type": "application/octet-stream", "X-HaloCue-Filename": filename},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, dict(response.headers), json.loads(response.read())


def test_http_accepts_idempotent_production_request_1_1(settings):
    staging = ProductionService(settings)
    payload = formal_request(staging)
    staging.jobs.close()

    with api(settings) as base:
        status, _, created = request(
            base, "/api/v1/production-runs", payload, "POST"
        )
        assert status == 201
        assert created["production_request"]["version"] == "1.1"
        assert created["handoff"]["idempotent"] is False
        assert str(settings.data_dir) not in json.dumps(created, ensure_ascii=False)

        status, _, repeated = request(
            base, "/api/v1/production-runs", payload, "POST"
        )
        assert status == 200
        assert repeated["run"]["run_id"] == created["run"]["run_id"]
        assert repeated["handoff"]["idempotent"] is True


def test_http_exposes_formal_production_adapter_capabilities(settings):
    with api(settings) as base:
        status, headers, result = request(base, "/api/v1/production-adapters")

    assert status == 200
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert result["ok"] is True
    assert result["contract"] == "AdapterCapabilities/1.0"
    for capability in result["adapters"]:
        validate_contract("AdapterCapabilities", capability)
    adapters = {item["adapter_id"]: item for item in result["adapters"]}
    assert adapters["aa-compat"]["targets"] == ["pc_aap"]
    assert adapters["storyforge-local"]["targets"] == ["storyforge_preview"]
    assert "compile_aap" not in adapters["storyforge-local"]["capabilities"]
    public = json.dumps(result, ensure_ascii=False)
    assert str(settings.data_dir) not in public
    assert "legacy_root" not in public
    assert "aa_data" not in public
    assert "token" not in public.casefold()


def test_http_maps_formal_request_version_error_to_stable_api_error(settings):
    with api(settings) as base:
        status, _, result = request(
            base,
            "/api/v1/production-runs",
            {"schema_version": "2.0"},
            "POST",
        )

    assert status == 400
    assert result["ok"] is False
    assert result["error"]["code"] == "unsupported_production_request_version"
    assert result["error"]["details"]["path"] == "$.schema_version"
    assert result["error"]["details"]["received"] == "2.0"
    assert "supported" in result["error"]["details"]
    assert "data_dir" not in json.dumps(result, ensure_ascii=False)


def test_http_vertical_slice_and_error_contract(settings):
    with api(settings) as base:
        status, headers, health = request(base, "/api/v1/health")
        assert status == 200
        assert health["api_version"] == "v1"
        assert headers["X-Content-Type-Options"] == "nosniff"

        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {
                "project": "HTTP 测试",
                "source": {"kind": "inline", "text": "爱丽丝: 你好\n"},
            },
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]

        status, _, invalid = request(
            base,
            f"/api/v1/production-runs/{run_id}/cast-bindings",
            {"speaker": "爱丽丝", "mapping": {"kind": "portrait"}, "expected_draft_version": 1},
            "POST",
        )
        assert status == 400
        assert invalid == {
            "ok": False,
            "error": {"code": "cast_id_required", "message": "有立绘角色必须提供 AA 角色 ID", "details": {}},
        }

        status, _, listing = request(base, "/api/v1/production-runs")
        assert status == 200
        assert listing["items"][0]["run_id"] == run_id


def test_http_accepts_canonical_production_run_uuid_as_a_compatibility_alias(settings):
    with api(settings) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {
                "project": "canonical 查询",
                "source": {"kind": "inline", "text": "旁白: 测试\n"},
            },
            "POST",
        )
        assert status == 201
        canonical_id = created["run"]["production_run_id"]
        legacy_id = created["run"]["run_id"]

        status, _, detail = request(
            base, f"/api/v1/production-runs/{canonical_id}"
        )

    assert status == 200
    assert detail["run"]["production_run_id"] == canonical_id
    assert detail["run"]["run_id"] == legacy_id


def test_http_writing_release_handoff_returns_existing_run_on_retry(settings):
    text = "## 场景 01\n爱丽丝: 我们开始吧。\n"
    payload = {
        "project": "写作交接 · v1",
        "source": {"kind": "inline", "text": text},
        "script_release": {
            "id": "release-000000000001",
            "display_version": "v1",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    }
    with api(settings) as base:
        first_status, _, first = request(base, "/api/v1/production-runs", payload, "POST")
        second_status, _, second = request(base, "/api/v1/production-runs", payload, "POST")
        assert first_status == 201
        assert second_status == 200
        assert second["handoff"]["idempotent"] is True
        assert second["run"]["run_id"] == first["run"]["run_id"]

        conflict_payload = json.loads(json.dumps(payload))
        conflict_text = "## 场景 01\n爱丽丝: 内容已变更。\n"
        conflict_payload["source"]["text"] = conflict_text
        conflict_payload["script_release"]["content_hash"] = hashlib.sha256(
            conflict_text.encode("utf-8")
        ).hexdigest()
        conflict_status, _, conflict = request(
            base, "/api/v1/production-runs", conflict_payload, "POST"
        )
        assert conflict_status == 409
        assert conflict["error"] == {
            "code": "script_release_identity_conflict",
            "message": "同一写作发布版本 ID 已绑定到不同正文，已拒绝交接",
            "details": {
                "release_id": "release-000000000001",
                "run_id": first["run"]["run_id"],
            },
        }


def test_http_writing_release_handoff_rejects_unknown_version(settings):
    text = "## 场景 01\n爱丽丝: 我们开始吧。\n"
    payload = {
        "project": "未知合同版本",
        "source": {"kind": "inline", "text": text},
        "script_release": {
            "schema_version": "2.0",
            "id": "release-000000000001",
            "display_version": "v1",
            "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    }
    with api(settings) as base:
        status, _, result = request(
            base, "/api/v1/production-runs", payload, "POST"
        )

    assert status == 400
    assert result == {
        "ok": False,
        "error": {
            "code": "unsupported_script_release_version",
            "message": "不支持的 ScriptRelease 合同版本",
            "details": {"received": "2.0", "supported": ["1.0"]},
        },
    }


def test_http_serves_production_workbench_assets(settings):
    with api(settings) as base:
        for path, marker, content_type in (
            ("/", "HaloCue", "text/html"),
            ("/app.css", ".app-shell", "text/css"),
            ("/app.js", "direction-generation", "javascript"),
        ):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                body = response.read().decode("utf-8")
                assert response.status == 200
                assert marker in body
                assert content_type in response.headers["Content-Type"]
                assert response.headers["X-Content-Type-Options"] == "nosniff"
                assert "default-src 'self'" in response.headers["Content-Security-Policy"]


def test_http_source_preflight_is_read_only_and_explains_next_action(settings):
    with api(settings) as base:
        status, _, result = request(
            base,
            "/api/v1/script-preflight",
            {"source": {"kind": "inline", "text": "## 场景\n旁白: 测试\n@wait\n"}},
            "POST",
        )
        assert status == 200
        assert result["kind"] == "static_preflight"
        assert result["speakers"][0]["name"] == "旁白"
        assert result["directives"]["issues"][0]["code"] == "missing_directive_argument"
        assert any(action["id"] == "create_run" for action in result["actions"])
        status, _, runs = request(base, "/api/v1/production-runs")
        assert status == 200
        assert runs["items"] == []


def test_http_task_preflight_summary_reads_only_the_frozen_run(settings):
    with api(settings) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {"project": "HTTP 任务初审", "source": {"kind": "inline", "text": "## 场景\n旁白: 测试\n"}},
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        status, _, summary = request(base, f"/api/v1/production-runs/{run_id}/preflight-summary")
        assert status == 200
        assert summary["kind"] == "task_preflight_summary"
        assert summary["speakers"] == [
            {"speaker": "旁白", "count": 1, "sample": "测试", "first_line": 2, "mapping": {"kind": "unset", "name": ""}}
        ]
        assert summary["next_action"]["stage"] == "mapping"


def test_http_ai_preflight_result_is_empty_until_a_real_model_job_finishes(settings):
    with api(settings) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {"project": "HTTP AI 初审", "source": {"kind": "inline", "text": "旁白: 测试\n"}},
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        status, _, result = request(base, f"/api/v1/production-runs/{run_id}/ai-preflights")
        assert status == 200
        assert result == {
            "ok": True, "kind": "ai_preflight_results", "read_only": True,
            "run_id": run_id, "items": [],
        }
        status, _, error = request(base, f"/api/v1/production-runs/{run_id}/ai-preflights", {}, "POST")
        assert status == 409
        assert error["error"]["code"] == "ai_preflight_not_configured"


def test_http_job_retry_action_uses_the_job_route(settings):
    with api(settings) as base:
        status, _, error = request(
            base,
            "/api/v1/jobs/job-000000000000?action=retry",
            {},
            "POST",
        )
        assert status == 404
        assert error["error"]["code"] == "job_not_found"


def test_http_task_asset_upload_validate_register_and_preview(settings, tmp_path):
    configured = configured_resource_settings(settings, tmp_path)
    with api(configured) as base:
        status, _, created = request(
            base, "/api/v1/production-runs",
            {"project": "HTTP 素材", "source": {"kind": "inline", "text": "旁白: 测试\n"}}, "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        image = BytesIO()
        Image.new("RGB", (12, 8), "#447755").save(image, format="PNG")
        status, _, uploaded = upload(base, f"/api/v1/production-runs/{run_id}/assets", "scene.png", image.getvalue())
        assert status == 201
        assert set(uploaded) >= {"ok", "upload_token", "filename", "size"}
        assert "path" not in uploaded

        status, _, validation = request(
            base, f"/api/v1/production-runs/{run_id}/assets/validate",
            {"kind": "background", "upload_token": uploaded["upload_token"]}, "POST",
        )
        assert status == 200
        assert validation["validation"]["ok"] is True
        assert "source" not in validation["validation"]
        status, _, registered = request(
            base, f"/api/v1/production-runs/{run_id}/assets",
            {"kind": "background", "upload_token": uploaded["upload_token"], "expected_draft_version": created["draft"]["draft_version"]}, "PUT",
        )
        assert status == 201
        assert registered["asset"]["key"] == "scene"
        assert "private_source" not in registered["asset"]
        status, _, upgraded = request(
            base,
            f"/api/v1/production-runs/{run_id}/asset-manifests",
            {
                "expected_manifest_id": created["asset_manifest"]["id"],
                "expected_content_hash": created["asset_manifest"]["content_hash"],
                "add_task_asset_ids": [registered["asset"]["asset_id"]],
                "remove_task_asset_ids": [],
            },
            "POST",
        )
        assert status == 201
        assert upgraded["asset_policy"]["revision"] == 2
        assert upgraded["asset_policy"]["asset_count"] == 1
        assert "path" not in json.dumps(upgraded, ensure_ascii=False)
        status, _, history = request(
            base, f"/api/v1/production-runs/{run_id}/asset-manifests"
        )
        assert status == 200
        assert [item["asset_policy"]["revision"] for item in history["items"]] == [1, 2]
        with urllib.request.urlopen(
            base + f"/api/v1/production-runs/{run_id}/resources/backgrounds/scene/preview", timeout=5
        ) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/png"

        status, _, listed = request(base, f"/api/v1/production-runs/{run_id}/resources/backgrounds?q=scene")
        assert status == 200
        assert listed["items"] == [{"key": "scene", "name": "scene", "source": "task_import", "asset_id": registered["asset"]["asset_id"]}]
        status, _, removed = request(
            base,
            f"/api/v1/production-runs/{run_id}/assets/{registered['asset']['asset_id']}",
            {"expected_draft_version": registered["draft"]["draft_version"]},
            "DELETE",
        )
        assert status == 200
        assert removed["asset_manifest_upgrade_required"] is True
        assert removed["draft"]["draft_version"] == registered["draft"]["draft_version"] + 1
        status, _, assets = request(base, f"/api/v1/production-runs/{run_id}/assets")
        assert status == 200
        assert assets["items"] == []


def test_http_rejects_path_shaped_run_identifier(settings):
    with api(settings) as base:
        status, _, result = request(base, "/api/v1/production-runs/..%5Csecret")
        assert status == 400
        assert result["error"]["code"] == "invalid_run_id"


def test_http_card_patch_and_delete_routes(settings):
    with api(settings) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {
                "project": "HTTP 卡片编辑",
                "source": {"kind": "inline", "text": "旁白: 原台词\n"},
            },
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        card_id = created["draft"]["cards"][0]["card_id"]

        status, _, updated = request(
            base,
            f"/api/v1/production-runs/{run_id}/cards/{card_id}",
            {"patch": {"text": "新台词"}, "expected_draft_version": 1},
            "PATCH",
        )
        assert status == 200
        assert updated["draft"]["cards"][0]["current"]["text"] == "新台词"

        status, _, deleted = request(
            base,
            f"/api/v1/production-runs/{run_id}/cards/{card_id}",
            {"expected_draft_version": updated["draft"]["draft_version"]},
            "DELETE",
        )
        assert status == 200
        assert deleted["draft"]["counts"]["total"] == 0


def test_http_structured_directive_patch_rejects_direct_resource_names(settings):
    with api(settings) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {"project": "HTTP 指令", "source": {"kind": "inline", "text": "@wait 100\n"}},
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        card_id = created["draft"]["cards"][0]["card_id"]
        status, _, updated = request(
            base,
            f"/api/v1/production-runs/{run_id}/cards/{card_id}",
            {"patch": {"cmd": "wait", "arg": "250"}, "expected_draft_version": 1},
            "PATCH",
        )
        assert status == 200
        assert updated["draft"]["cards"][0]["current"] == {"cmd": "wait", "arg": "250"}
        assert updated["draft"]["cards"][0]["review_state"] == "pending"
        status, _, rejected = request(
            base,
            f"/api/v1/production-runs/{run_id}/cards/{card_id}",
            {"patch": {"cmd": "se", "arg": "SE_DoorOpen_01"}, "expected_draft_version": updated["draft"]["draft_version"]},
            "PATCH",
        )
        assert status == 409
        assert rejected["error"]["code"] == "directive_requires_resource_picker"


def test_http_resources_and_background_resolution(settings, tmp_path):
    configured = configured_resource_settings(settings, tmp_path)
    with api(configured) as base:
        status, _, resources = request(
            base, "/api/v1/resources/backgrounds?q=rain&limit=10"
        )
        assert status == 200
        assert resources["items"][0]["key"] == "BG_RainyStation"

        status, _, character = request(
            base, "/api/v1/resources/characters/alice-school"
        )
        assert status == 200
        assert len(character["character"]["faces"]) == 2

        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {
                "project": "HTTP 背景处理",
                "source": {
                    "kind": "inline",
                    "text": "# 待生成自定义背景：雨夜车站\n旁白: 测试\n",
                },
            },
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        card = next(
            item
            for item in created["draft"]["cards"]
            if item["kind"] == "background_request"
        )
        status, _, resolved = request(
            base,
            f"/api/v1/production-runs/{run_id}/cards/{card['card_id']}/background-resolution",
            {
                "action": "black",
                "expected_draft_version": created["draft"]["draft_version"],
            },
            "POST",
        )
        assert status == 200
        same = next(
            item
            for item in resolved["draft"]["cards"]
            if item["card_id"] == card["card_id"]
        )
        assert same["current"] == {"cmd": "bg", "arg": "BG_Black"}


def test_http_cg_resources_are_run_frozen(settings, tmp_path):
    aa_data = tmp_path / "aa-data"
    for name in ("projects", "saves", "overrides", "settings"):
        (aa_data / name).mkdir(parents=True)
    popup_dir = aa_data / "overrides" / "popups"
    popup_dir.mkdir()
    (popup_dir / "Event03_CH0070.png").write_bytes(b"test")
    base_settings = configured_resource_settings(settings, tmp_path)
    configured = type(base_settings)(
        project_root=base_settings.project_root, data_dir=base_settings.data_dir,
        legacy_root=base_settings.legacy_root, resource_index=base_settings.resource_index,
        aa_data=aa_data, host="127.0.0.1", port=0,
    )
    with api(configured) as base:
        status, _, created = request(
            base, "/api/v1/production-runs",
            {"project": "HTTP CG", "source": {"kind": "inline", "text": "老师: 测试\\n"}}, "POST"
        )
        assert status == 201
        (popup_dir / "Late_CG.png").write_bytes(b"later")
        status, _, catalog = request(
            base, f"/api/v1/production-runs/{created['run']['run_id']}/resources/cg"
        )
        assert status == 200
        assert catalog["frozen"] is True
        assert [item["key"] for item in catalog["items"]] == ["Event03_CH0070"]


def test_http_resource_usage_is_task_local_and_safe(settings, tmp_path):
    configured = configured_resource_settings(settings, tmp_path)
    with api(configured) as base:
        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {"project": "HTTP 素材使用", "source": {"kind": "inline", "text": "爱丽丝: 测试\n"}},
            "POST",
        )
        assert status == 201
        run_id = created["run"]["run_id"]
        status, _, mapped = request(
            base,
            f"/api/v1/production-runs/{run_id}/cast-bindings",
            {"speaker": "爱丽丝", "mapping": {"kind": "portrait", "id": "alice-school"}, "expected_draft_version": 1},
            "POST",
        )
        assert status == 200
        status, _, usage = request(base, f"/api/v1/production-runs/{run_id}/resource-usage")
        assert status == 200
        assert usage["usage"]["characters:alice-school"][0]["label"] == "角色映射：爱丽丝"
        assert str(configured.data_dir) not in json.dumps(usage)


def test_http_resource_previews_are_allowlisted_and_do_not_expose_paths(settings, tmp_path):
    configured_base = configured_resource_settings(settings, tmp_path)
    configured = type(configured_base)(
        project_root=configured_base.project_root,
        data_dir=configured_base.data_dir,
        legacy_root=tmp_path / "legacy",
        resource_index=configured_base.resource_index,
        aa_data=None,
        host="127.0.0.1",
        port=0,
    )
    preview_root = configured.legacy_root / "out" / "official-previews"
    image = preview_root / "backgrounds" / "rainy.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"\x89PNG\r\n\x1a\npreview")
    (preview_root / "manifest.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "kind": "background",
                        "key": "BG_RainyStation",
                        "path": "backgrounds/rainy.png",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with api(configured) as base:
        with urllib.request.urlopen(
            base + "/api/v1/resources/backgrounds/BG_RainyStation/preview", timeout=5
        ) as response:
            assert response.status == 200
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
            assert response.read().startswith(b"\x89PNG")

        status, _, missing = request(
            base, "/api/v1/resources/backgrounds/BG_Classroom/preview"
        )
        assert status == 404
        assert missing["error"]["code"] == "resource_preview_not_found"
        assert str(preview_root) not in json.dumps(missing)


def test_http_direction_model_settings_are_redacted(settings, monkeypatch):
    monkeypatch.setenv("HALOCUE_HTTP_MODEL_KEY", "http-secret")
    with api(settings) as base:
        status, _, saved = request(
            base,
            "/api/v1/settings/direction-model",
            {
                "provider": "openai",
                "base_url": "https://example.invalid/v1",
                "model": "test-model",
                "api_key_env": "HALOCUE_HTTP_MODEL_KEY",
                "max_tokens": 1024,
            },
            "POST",
        )
        assert status == 200
        assert saved["model"]["configured"] is True
        assert saved["model"]["secret_source"] == "environment"
        assert "http-secret" not in json.dumps(saved)

        status, _, loaded = request(base, "/api/v1/settings/direction-model")
        assert status == 200
        assert loaded == saved
        assert "api_key" not in loaded["model"]


def test_http_file_source_and_aa_environment_routes(settings, tmp_path):
    aa_data = tmp_path / "aa-data"
    for name in ("projects", "saves", "overrides", "settings"):
        (aa_data / name).mkdir(parents=True)
    with api(settings) as base:
        status, _, environment = request(
            base,
            "/api/v1/settings/aa-environment",
            {"selection": str(aa_data), "adopt": True},
            "POST",
        )
        assert status == 200
        assert environment["adopted"] is True
        assert environment["aa_workspace"]["valid"] is True
        assert str(aa_data.resolve()) not in json.dumps(environment, ensure_ascii=False)
        assert environment["environment"]["workspace"]["discovered"] is True

        status, _, created = request(
            base,
            "/api/v1/production-runs",
            {
                "project": "HTTP 文件导入",
                "source": {"kind": "file_upload", "filename": "chapter.md", "text": "旁白: 测试\n"},
            },
            "POST",
        )
        assert status == 201
        assert created["run"]["source_summary"]["source_filename"] == "chapter.md"
