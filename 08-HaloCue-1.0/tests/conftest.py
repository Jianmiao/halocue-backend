from __future__ import annotations

import os
from pathlib import Path

import pytest

from halocue_production.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    workspace_root = project_root.parent
    legacy_root = Path(
        os.environ.get("HALOCUE_LEGACY_ROOT")
        or workspace_root / "01-完整程序" / "aa"
    ).resolve()
    value = Settings(
        project_root=project_root,
        data_dir=tmp_path / "data",
        legacy_root=legacy_root,
        resource_index=None,
        aa_data=None,
        host="127.0.0.1",
        port=0,
    )
    value.prepare()
    return value

