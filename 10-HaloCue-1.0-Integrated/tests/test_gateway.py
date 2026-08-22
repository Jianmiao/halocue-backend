from __future__ import annotations

import json
import http.client
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
for source_root in (
    PROJECT_ROOT / "src",
    WORKSPACE_ROOT / "09-HaloCue-1.0-Writing" / "src",
    WORKSPACE_ROOT / "08-HaloCue-1.0" / "src",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from halocue_integrated.gateway import MAX_PROXY_BODY_BYTES, create_gateway, route_request
from halocue_integrated.server import IntegratedRuntime
from halocue_production.contracts import validate_contract
from halocue_writing.errors import DomainError


def _isolate_optional_local_assets(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "HALOCUE_RESOURCE_INDEX", str(tmp_path / "missing-resource-index.json")
    )
    monkeypatch.setenv(
        "HALOCUE_NAME_BASELINE", str(tmp_path / "missing-name-baseline.json")
    )
    monkeypatch.delenv("HALOCUE_AA_DATA", raising=False)


def _freeze_release_for_handoff(writing):
    work = writing.create_work({"title": "正式交接恢复测试"})
    brief = writing.save_brief(
        work["id"],
        {
            "expected_version": work["version"],
            "idea": "验证正式交接在写作进程退出后仍可恢复",
            "mode": "bond_short",
            "characters": ["爱丽丝"],
        },
    )
    blueprint = writing.generate_blueprint(
        work["id"], {"expected_version": brief["work"]["version"]}
    )
    chapter = writing.create_chapter(
        work["id"],
        {"expected_version": blueprint["work"]["version"], "title": "第一章"},
    )
    scene = writing.create_scene(
        work["id"],
        chapter["chapter_id"],
        {
            "expected_version": chapter["work"]["version"],
            "title": "恢复入口",
            "location": "测试工作区",
            "goal": "确认交接恢复",
        },
    )
    candidate = writing.generate_scene_candidate(
        work["id"],
        scene["scene_id"],
        {"expected_version": scene["work"]["version"]},
    )
    accepted = writing.accept_proposal(
        work["id"],
        candidate["proposal_id"],
        {"expected_version": candidate["work"]["version"]},
    )
    scene_review = writing.review_scene(
        work["id"],
        scene["scene_id"],
        {"expected_version": accepted["work"]["version"]},
    )
    release_review = writing.review_release(
        work["id"], {"expected_version": scene_review["work"]["version"]}
    )
    return writing.freeze_release(
        work["id"], {"expected_version": release_review["work"]["version"]}
    )


def test_route_request_keeps_api_domains_separate():
    assert route_request("/api/v1/works") == ("writing", "/api/v1/works")
    assert route_request("/production/api/v1/health") == ("production", "/api/v1/health")
    assert route_request("/production/app.js") == ("production", "/app.js")
    assert route_request("/api/v1/health", "http://127.0.0.1:8910/production/") == (
        "production",
        "/api/v1/health",
    )


def test_integrated_runtime_serves_both_workbenches_and_apis(tmp_path, monkeypatch):
    _isolate_optional_local_assets(monkeypatch, tmp_path)
    runtime = IntegratedRuntime(
        host="127.0.0.1",
        port=0,
        writing_data_dir=tmp_path / "writing",
        production_data_dir=tmp_path / "production",
    )
    runtime.start_upstreams()
    gateway_thread = threading.Thread(target=runtime.gateway.serve_forever, daemon=True)
    gateway_thread.start()
    base = f"http://127.0.0.1:{runtime.port}"
    try:
        with urllib.request.urlopen(base + "/", timeout=5) as response:
            writing_html = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/production/", timeout=5) as response:
            production_html = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/api/v1/health", timeout=5) as response:
            writing_health = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/production/api/v1/health", timeout=5) as response:
            production_health = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/production/app.js", timeout=5) as response:
            production_js = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/production/app-embedded.js", timeout=5) as response:
            embedded_production_js = response.read().decode("utf-8")
        with urllib.request.urlopen(base + "/integration-shell.js", timeout=5) as response:
            integration_shell_js = response.read().decode("utf-8")
    finally:
        runtime.close()
        gateway_thread.join(timeout=3)

    assert "integration-shell.js" in writing_html
    assert "production-embed.js" in writing_html
    assert "production-embed.css" in writing_html
    assert "integration-shell.js" in production_html
    assert '<body class="halocue-integrated-production">' in production_html
    assert 'class="halocue-integrated-production"' not in writing_html
    assert '<script src="integration-shell.js"></script>' in production_html
    assert production_html.index('<script src="app.js"></script>') < production_html.index(
        '<script src="integration-shell.js"></script>'
    )
    assert "halocue-writing" in writing_health
    assert "halocue-production" in production_health
    assert 'const API_ROOT = "/production/api/v1";' in production_js
    assert 'const API_ROOT = "/production/api/v1";' in embedded_production_js
    assert 'id="formalProductionPanel"' in production_html
    assert 'performance-drafts' in production_js
    assert 'expected_revision_id' in production_js
    assert 'storyforge_video' in production_js
    assert 'const productionRoot = productionHost?.shadowRoot;' in embedded_production_js
    assert 'productionRoot.addEventListener("click"' in embedded_production_js
    assert 'document.addEventListener("click"' not in embedded_production_js
    assert 'productionNav.matches(\'.locked-nav,[aria-disabled="true"]\')' in integration_shell_js


def test_script_release_crosses_the_real_writing_production_boundary(
    tmp_path, monkeypatch
):
    _isolate_optional_local_assets(monkeypatch, tmp_path)
    runtime = IntegratedRuntime(
        host="127.0.0.1",
        port=0,
        writing_data_dir=tmp_path / "writing",
        production_data_dir=tmp_path / "production",
    )
    runtime.start_upstreams()
    try:
        writing = runtime.writing_service
        work = writing.create_work({"title": "集成交接测试"})
        brief = writing.save_brief(
            work["id"],
            {
                "expected_version": work["version"],
                "idea": "爱丽丝与凯伊调查深夜启动的旧机器",
                "mode": "bond_short",
                "characters": ["爱丽丝", "凯伊"],
            },
        )
        blueprint = writing.generate_blueprint(
            work["id"], {"expected_version": brief["work"]["version"]}
        )
        chapter = writing.create_chapter(
            work["id"],
            {"expected_version": blueprint["work"]["version"], "title": "第一章"},
        )
        scene = writing.create_scene(
            work["id"],
            chapter["chapter_id"],
            {
                "expected_version": chapter["work"]["version"],
                "title": "提示灯",
                "location": "游戏开发部活动室",
                "goal": "确认异常提示灯的来源",
            },
        )
        candidate = writing.generate_scene_candidate(
            work["id"],
            scene["scene_id"],
            {"expected_version": scene["work"]["version"]},
        )
        accepted = writing.accept_proposal(
            work["id"],
            candidate["proposal_id"],
            {"expected_version": candidate["work"]["version"]},
        )
        scene_review = writing.review_scene(
            work["id"],
            scene["scene_id"],
            {"expected_version": accepted["work"]["version"]},
        )
        release_review = writing.review_release(
            work["id"], {"expected_version": scene_review["work"]["version"]}
        )
        frozen = writing.freeze_release(
            work["id"], {"expected_version": release_review["work"]["version"]}
        )
        handoff = writing.handoff_release(frozen["release_id"])
        repeated = writing.handoff_release(frozen["release_id"])
        identity = writing.repo.get_formal_handoff_identity(frozen["release_id"])
        with pytest.raises(DomainError) as identity_conflict:
            writing.repo.save_formal_handoff_identity(
                release_id=frozen["release_id"],
                formal_release_id=handoff["formal_release_id"],
                production_request_id=handoff["production_request_id"],
                formal_work_id=writing._formal_uuid("work", work["id"]),
                production_run_id=handoff["production_run_id"],
                content_hash="sha256:" + "0" * 64,
            )
        production = runtime.production_service.run_detail(handoff["production_run_id"])
        initial_asset_manifest = runtime.production_service.asset_manifests.payload_for_run(
            handoff["production_run_id"]
        )
        formal_request = runtime.production_service.formal_inputs.load_request(
            handoff["production_request_id"]
        )
    finally:
        runtime.writing_server.shutdown()
        runtime.writing_server.server_close()
        runtime.production_server.shutdown()
        runtime.production_server.server_close()
        runtime.production_service.jobs.close()
        for thread in runtime._threads:
            thread.join(timeout=3)
        runtime.gateway.server_close()

    origin = production["run"]["source_summary"]["upstream_release"]
    assert handoff["contract"] == "ProductionRequest/1.1"
    assert repeated["idempotent"] is True
    assert repeated["formal_release_id"] == handoff["formal_release_id"]
    assert identity["formal_release_id"] == handoff["formal_release_id"]
    assert identity["production_request_id"] == handoff["production_request_id"]
    assert identity["production_run_id"] == handoff["production_run_id"]
    assert identity["content_hash"] == frozen["manifest"]["content_hash"]
    assert identity_conflict.value.code == "formal_handoff_identity_conflict"
    assert initial_asset_manifest["schema_version"] == "1.0"
    assert initial_asset_manifest["assets"] == []
    assert origin["release_id"] == handoff["formal_release_id"]
    assert origin["work_id"] != work["id"]
    assert origin["writing_pack_version"] == frozen["manifest"]["writing_pack_version"]
    assert production["run"]["release_id"] != frozen["release_id"]
    assert formal_request["schema_version"] == "1.1"
    assert formal_request["request_id"] == handoff["production_request_id"]
    assert formal_request["script_release"]["id"] == handoff["formal_release_id"]
    validate_contract("ProductionRequest", formal_request)


def test_formal_handoff_recovers_after_writing_process_exit_before_binding(
    tmp_path, monkeypatch
):
    _isolate_optional_local_assets(monkeypatch, tmp_path)
    writing_data = tmp_path / "writing"
    production_data = tmp_path / "production"
    runtime = IntegratedRuntime(
        host="127.0.0.1",
        port=0,
        writing_data_dir=writing_data,
        production_data_dir=production_data,
    )
    runtime.start_upstreams()
    try:
        frozen = _freeze_release_for_handoff(runtime.writing_service)
        first = runtime.writing_service.handoff_release(frozen["release_id"])
        with runtime.writing_service.repo.transaction() as connection:
            connection.execute(
                "UPDATE script_releases SET production_run_id=NULL WHERE id=?",
                (frozen["release_id"],),
            )
    finally:
        runtime.close()

    restarted = IntegratedRuntime(
        host="127.0.0.1",
        port=0,
        writing_data_dir=writing_data,
        production_data_dir=production_data,
    )
    restarted.start_upstreams()
    try:
        recovered = restarted.writing_service.handoff_release(frozen["release_id"])
        identity = restarted.writing_service.repo.get_formal_handoff_identity(
            frozen["release_id"]
        )
        detail = restarted.production_service.run_detail(first["production_run_id"])
    finally:
        restarted.close()

    assert recovered["idempotent"] is True
    assert recovered["production_run_id"] == first["production_run_id"]
    assert identity["production_run_id"] == first["production_run_id"]
    assert identity["formal_release_id"] == recovered["formal_release_id"]
    assert detail["production_request"]["id"] == recovered["production_request_id"]


def test_integrated_runtime_lifecycle_and_internal_endpoint_redaction(tmp_path, monkeypatch):
    _isolate_optional_local_assets(monkeypatch, tmp_path)
    runtime = IntegratedRuntime(
        host="127.0.0.1",
        port=0,
        writing_data_dir=tmp_path / "writing",
        production_data_dir=tmp_path / "production",
    )
    internal_port = runtime.production_server.server_port
    runtime.start()
    runtime.start_upstreams()
    diagnostics = runtime.writing_service.system_diagnostics()
    try:
        assert diagnostics["production_service"]["url"] == "/production"
        assert str(internal_port) not in json.dumps(diagnostics, ensure_ascii=False)
    finally:
        runtime.close()
        runtime.close()


def test_gateway_errors_keep_legacy_wrapper_and_negotiate_api_error(tmp_path):
    gateway = create_gateway(
        "127.0.0.1",
        0,
        writing_address=("127.0.0.1", 1),
        production_address=("127.0.0.1", 1),
        static_dir=tmp_path,
    )
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{gateway.server_port}"
    try:
        with pytest.raises(urllib.error.HTTPError) as legacy_error:
            urllib.request.urlopen(base + "/api/v1/health", timeout=5)
        legacy = json.loads(legacy_error.value.read().decode("utf-8"))
        assert legacy["ok"] is False
        assert legacy["error"]["code"] == "upstream_unavailable"

        request = urllib.request.Request(
            base + "/api/v1/health",
            headers={"Accept": "application/vnd.halocue.api-error+json; version=1.0"},
        )
        with pytest.raises(urllib.error.HTTPError) as negotiated_error:
            urllib.request.urlopen(request, timeout=5)
        negotiated = json.loads(negotiated_error.value.read().decode("utf-8"))
        assert negotiated["schema_version"] == "1.0"
        assert negotiated["code"] == "UPSTREAM_UNAVAILABLE"
        assert "ok" not in negotiated

        connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
        connection.request(
            "POST",
            "/api/v1/health",
            body=b"",
            headers={"Content-Length": str(MAX_PROXY_BODY_BYTES + 1)},
        )
        oversized = connection.getresponse()
        oversized_payload = json.loads(oversized.read().decode("utf-8"))
        assert oversized.status == 413
        assert oversized_payload["error"]["code"] == "payload_too_large"
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=3)
