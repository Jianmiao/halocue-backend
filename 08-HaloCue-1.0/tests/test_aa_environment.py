from __future__ import annotations

import hashlib
import json
from pathlib import Path

from halocue_production.aa_environment import AaEnvironmentPreflight
from halocue_production.config import Settings


def _settings(tmp_path: Path, *, aa_data: Path | None, resource_index: Path | None) -> Settings:
    return Settings(
        project_root=tmp_path,
        data_dir=tmp_path / "data",
        legacy_root=tmp_path / "legacy",
        resource_index=resource_index,
        aa_data=aa_data,
        host="127.0.0.1",
        port=0,
    )


def _workspace(tmp_path: Path) -> Path:
    path = tmp_path / "aa-data"
    for name in ("projects", "saves", "overrides", "settings"):
        (path / name).mkdir(parents=True)
    return path


def test_preflight_reports_missing_workspace_without_physical_path(tmp_path):
    settings = _settings(tmp_path, aa_data=None, resource_index=None)

    result = AaEnvironmentPreflight(settings).run()

    assert result["kind"] == "aa_workspace_preflight"
    assert result["status"] == "not_ready"
    assert result["workspace"]["configured"] is False
    assert "aa_workspace_not_configured" in {item["code"] for item in result["issues"]}
    serialized = json.dumps(result, ensure_ascii=False)
    assert str(tmp_path) not in serialized
    assert "projects" in result["workspace"]["required_directories"]


def test_preflight_verifies_resource_index_sidecar_hash(tmp_path):
    workspace = _workspace(tmp_path)
    resource_index = tmp_path / "aa-resources.json"
    content = b'{"characters": []}\n'
    resource_index.write_bytes(content)
    (tmp_path / "aa-resources.json.sha256").write_text(
        hashlib.sha256(content).hexdigest(), encoding="ascii"
    )
    settings = _settings(tmp_path, aa_data=workspace, resource_index=resource_index)

    result = AaEnvironmentPreflight(settings).run()

    assert result["status"] == "ready"
    assert result["workspace"]["valid"] is True
    assert result["resource_index"] == {
        "available": True,
        "hash_status": "verified",
    }
    assert result["capabilities"] == {"compile": True, "install": True}


def test_preflight_reports_resource_index_hash_mismatch_and_never_exposes_private_data(tmp_path):
    workspace = _workspace(tmp_path)
    resource_index = tmp_path / "aa-resources.json"
    resource_index.write_text('{"private_asset": "secret"}', encoding="utf-8")
    (tmp_path / "aa-resources.json.sha256").write_text("0" * 64, encoding="ascii")
    settings = _settings(tmp_path, aa_data=workspace, resource_index=resource_index)

    result = AaEnvironmentPreflight(settings).run()

    assert result["status"] == "not_ready"
    assert result["resource_index"]["hash_status"] == "mismatch"
    assert "resource_index_hash_mismatch" in {item["code"] for item in result["issues"]}
    serialized = json.dumps(result, ensure_ascii=False)
    assert "private_asset" not in serialized
    assert "secret" not in serialized
    assert str(resource_index) not in serialized
