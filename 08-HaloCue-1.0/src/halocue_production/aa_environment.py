from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Any, Mapping

from .config import Settings


AA_WORKSPACE_DIRECTORIES = ("projects", "saves", "overrides", "settings")
_SHA256 = re.compile(r"^(?:sha256:)?([0-9a-f]{64})$", re.IGNORECASE)


class AaEnvironmentPreflight:
    """Read-only, sanitized checks for the local-only AA integration boundary."""

    def __init__(
        self,
        settings: Settings,
        *,
        environment: Mapping[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        self.environment = environment or {}
        discovered_workspace = self.environment.get("workspace")
        discovered_workspace = discovered_workspace if isinstance(discovered_workspace, Mapping) else {}
        discovered_cache = self.environment.get("resource_cache")
        discovered_cache = discovered_cache if isinstance(discovered_cache, Mapping) else {}
        self._workspace_path = self.settings.aa_data or self._private_path(discovered_workspace.get("path"))
        self._resource_index_path = self.settings.resource_index or self._private_path(discovered_cache.get("path"))

    def run(self) -> dict[str, Any]:
        issues: list[dict[str, str]] = []
        workspace = self._workspace(issues)
        resource_index = self._resource_index(issues)
        executable_available = bool(self.environment.get("executable"))
        install_root_available = bool(self.environment.get("install_root"))
        workspace_valid = bool(workspace["valid"])
        resource_usable = resource_index["available"] and resource_index["hash_status"] != "mismatch"
        configured_workspace_valid = bool(workspace.get("configured") and workspace_valid)
        ready = configured_workspace_valid and resource_usable
        return {
            "ok": True,
            "kind": "aa_workspace_preflight",
            "schema_version": "1.0",
            "local_only": True,
            "status": "ready" if ready else "not_ready",
            "workspace": workspace,
            "resource_index": resource_index,
            "executable_available": executable_available,
            "install_root_available": install_root_available,
            "capabilities": {
                "compile": bool(ready),
                "install": configured_workspace_valid,
            },
            "issues": issues,
        }

    def _workspace(self, issues: list[dict[str, str]]) -> dict[str, Any]:
        path = self._workspace_path
        directories = {
            name: bool(path and (path / name).is_dir())
            for name in AA_WORKSPACE_DIRECTORIES
        }
        configured = bool(self.settings.aa_data)
        discovered = bool(path) and not configured
        valid = configured and bool(path and path.is_dir()) and all(directories.values())
        if not configured:
            issues.append({"code": "aa_workspace_not_configured", "message": "尚未配置 AA data 工作区。"})
        elif not path.is_dir():
            issues.append({"code": "aa_workspace_not_found", "message": "配置的 AA data 工作区不存在。"})
        elif not valid:
            issues.append({"code": "aa_workspace_missing_directories", "message": "AA data 工作区缺少必要目录。"})
        elif discovered:
            issues.append({"code": "aa_workspace_selection_required", "message": "已发现 AA 工作区，但尚未采用到当前 HaloCue 设置。"})
        return {
            "configured": configured,
            "discovered": discovered,
            "valid": bool(valid),
            "required_directories": dict(directories),
        }

    def _resource_index(self, issues: list[dict[str, str]]) -> dict[str, Any]:
        path = self._resource_index_path
        if not path or not path.is_file():
            issues.append({"code": "resource_index_missing", "message": "AA 素材索引不可用。"})
            return {"available": False, "hash_status": "missing"}
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            issues.append({"code": "resource_index_unreadable", "message": "AA 素材索引无法读取。"})
            return {"available": False, "hash_status": "unreadable"}
        expected = self._expected_hash(path)
        if expected is None:
            return {"available": True, "hash_status": "unverified"}
        if digest != expected:
            issues.append({"code": "resource_index_hash_mismatch", "message": "AA 素材索引哈希校验失败。"})
            return {"available": True, "hash_status": "mismatch"}
        return {"available": True, "hash_status": "verified"}

    @staticmethod
    def _expected_hash(path: Path) -> str | None:
        raw = os.getenv("HALOCUE_RESOURCE_INDEX_SHA256", "").strip()
        if not raw:
            sidecar = path.with_name(path.name + ".sha256")
            try:
                raw = sidecar.read_text(encoding="ascii").strip().split()[0]
            except (OSError, IndexError):
                return None
        match = _SHA256.fullmatch(raw)
        return match.group(1).lower() if match else None

    @staticmethod
    def _private_path(value: object) -> Path | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            return Path(raw).expanduser().resolve()
        except (OSError, RuntimeError, ValueError):
            return None


__all__ = ["AA_WORKSPACE_DIRECTORIES", "AaEnvironmentPreflight"]
