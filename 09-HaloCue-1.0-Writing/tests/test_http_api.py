import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from halocue_writing.app import make_handler
from halocue_writing.errors import DomainError
from halocue_writing.repository import Repository
from halocue_writing.service import WritingService


def request(url, method="GET", body=None, headers=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request_headers = {"Content-Type": "application/json"}
    request_headers.update(headers or {})
    req = urllib.request.Request(url, data=data, method=method, headers=request_headers)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_http_contract_and_static_workspace(tmp_path):
    service = WritingService(tmp_path / "data")
    static = Path(__file__).resolve().parents[1] / "web"
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service, static))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/") as response:
            page = response.read().decode("utf-8")
            assert response.status == 200
            assert "HaloCue 写作工作台" in page
        status, health = request(base + "/api/v1/health")
        assert status == 200
        assert "data_dir" not in health
        status, capabilities = request(base + "/api/v1/capabilities")
        assert status == 200
        assert "corpus_dir" not in capabilities["data"]["official_references"]
        status, diagnostics = request(base + "/api/v1/settings/diagnostics")
        assert status == 200
        assert "data_dir" not in diagnostics["writing_service"]
        assert str((tmp_path / "data").resolve()) not in json.dumps(
            {"health": health, "capabilities": capabilities, "diagnostics": diagnostics},
            ensure_ascii=False,
        )
        status, created = request(base + "/api/v1/works", "POST", {"title": "HTTP 作品"})
        assert status == 201
        assert created["ok"] is True
        work = created["data"]
        status, conflict = request(
            base + f"/api/v1/works/{work['id']}/brief",
            "POST",
            {"expected_version": 0, "idea": "过期请求", "mode": "bond_short"},
        )
        assert status == 409
        assert conflict == {
            "ok": False,
            "error": {
                "code": "revision_conflict",
                "message": "内容已在其他位置更新，请刷新后重试。",
                "details": {"expected_version": 0, "actual_version": 1},
            },
        }
        status, negotiated = request(
            base + f"/api/v1/works/{work['id']}/brief",
            "POST",
            {"expected_version": 0, "idea": "过期请求", "mode": "bond_short"},
            headers={
                "Accept": "application/vnd.halocue.api-error+json; version=1.0"
            },
        )
        assert status == 409
        assert negotiated["schema_version"] == "1.0"
        assert negotiated["code"] == "REVISION_CONFLICT"
        assert "ok" not in negotiated
        status, bad_search = request(base + "/api/v1/official-references/search?q=x")
        assert status == 400
        assert bad_search["error"]["code"] == "validation_error"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_writing_schema_version_migration_restart_and_corruption(tmp_path):
    data_dir = tmp_path / "writing"
    repository = Repository(data_dir)
    assert repository.schema_version() == 2
    with sqlite3.connect(repository.db_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT version FROM writing_schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,)]
        connection.execute("PRAGMA user_version = 1")

    restarted = Repository(data_dir)
    assert restarted.schema_version() == 2

    with sqlite3.connect(restarted.db_path) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(DomainError) as unsupported:
        Repository(data_dir)
    assert unsupported.value.code == "writing_database_version_unsupported"

    corrupt_path = tmp_path / "corrupt" / "writing.db"
    corrupt_path.parent.mkdir()
    corrupt_path.write_bytes(b"not a sqlite database")
    with pytest.raises(DomainError) as corrupt:
        Repository(corrupt_path.parent)
    assert corrupt.value.code == "writing_database_corrupt"
