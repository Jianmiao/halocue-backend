from __future__ import annotations

import sys
import threading
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parent
for source_root in (
    PROJECT_ROOT / "src",
    WORKSPACE_ROOT / "09-HaloCue-1.0-Writing" / "src",
    WORKSPACE_ROOT / "08-HaloCue-1.0" / "src",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))

from halocue_integrated.gateway import route_request
from halocue_integrated.server import IntegratedRuntime


def test_route_request_keeps_api_domains_separate():
    assert route_request("/api/v1/works") == ("writing", "/api/v1/works")
    assert route_request("/production/api/v1/health") == ("production", "/api/v1/health")
    assert route_request("/production/app.js") == ("production", "/app.js")
    assert route_request("/api/v1/health", "http://127.0.0.1:8910/production/") == (
        "production",
        "/api/v1/health",
    )


def test_integrated_runtime_serves_both_workbenches_and_apis(tmp_path):
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
    assert 'const productionRoot = productionHost?.shadowRoot;' in embedded_production_js
    assert 'productionRoot.addEventListener("click"' in embedded_production_js
    assert 'document.addEventListener("click"' not in embedded_production_js
    assert 'productionNav.matches(\'.locked-nav,[aria-disabled="true"]\')' in integration_shell_js


def test_script_release_crosses_the_real_writing_production_boundary(tmp_path):
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
        production = runtime.production_service.run_detail(handoff["production_run_id"])
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
    assert origin["release_id"] == frozen["release_id"]
    assert origin["work_id"] == work["id"]
    assert origin["writing_pack_version"] == frozen["manifest"]["writing_pack_version"]
    assert production["run"]["release_id"] != frozen["release_id"]
