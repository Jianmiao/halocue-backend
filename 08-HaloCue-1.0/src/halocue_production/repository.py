from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path

from .errors import ProductionError
from .models import ProductionRun, ScriptRelease
from .runtime import RuntimeStore


class ProductionRepository:
    _RUN_ID = re.compile(r"run-[0-9a-f]{12}")

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.runs_dir = data_dir / "runs"
        self.releases_dir = data_dir / "releases"
        self._lock = threading.RLock()
        self.runtime = RuntimeStore(data_dir / "runtime.sqlite3")
        self._migrate_legacy_runs()

    @staticmethod
    def _atomic_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, path)

    def save_release(self, release: ScriptRelease, text: str) -> None:
        release_dir = self.releases_dir / release.release_id
        with self._lock:
            release_dir.mkdir(parents=True, exist_ok=False)
            (release_dir / "source.txt").write_text(text, encoding="utf-8")
            self._atomic_json(release_dir / "release.json", release.to_dict())

    def save_run(self, run: ProductionRun) -> None:
        with self._lock:
            production_run_id, work_item_ids = self.runtime.save_production_run(
                run.to_dict()
            )
            run.production_run_id = production_run_id
            run.runtime_status = self.runtime.get_production_run(run.run_id)[
                "runtime_status"
            ]
            for item in run.work_items:
                item.work_item_id = work_item_ids[item.key]

    def get_run(self, run_id: str) -> ProductionRun:
        identifier = str(run_id)
        is_legacy = self._RUN_ID.fullmatch(identifier) is not None
        is_canonical = False
        if not is_legacy:
            try:
                parsed = uuid.UUID(identifier)
                is_canonical = str(parsed) == identifier
            except (AttributeError, ValueError):
                is_canonical = False
        if not is_legacy and not is_canonical:
            raise ProductionError("invalid_run_id", "制作任务 ID 无效", status=400)
        value = self.runtime.get_production_run(identifier)
        if value is None:
            raise ProductionError("run_not_found", "制作任务不存在", status=404)
        try:
            return ProductionRun.from_dict(value)
        except (ValueError, TypeError) as exc:
            raise ProductionError(
                "run_corrupted", "制作任务状态损坏", status=500
            ) from exc

    def list_runs(self) -> list[ProductionRun]:
        rows: list[ProductionRun] = []
        for value in self.runtime.list_production_runs():
            try:
                rows.append(ProductionRun.from_dict(value))
            except (ValueError, TypeError):
                continue
        return sorted(rows, key=lambda item: item.updated_at, reverse=True)

    def _migrate_legacy_runs(self) -> None:
        for path in self.runs_dir.glob("run-*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                legacy_run_id = str(payload["run_id"])
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if self.runtime.get_production_run(legacy_run_id) is not None:
                continue
            try:
                self.runtime.save_production_run(payload)
            except (KeyError, TypeError, ValueError):
                continue
