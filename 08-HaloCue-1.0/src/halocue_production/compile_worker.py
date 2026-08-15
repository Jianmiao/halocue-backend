from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import ProductionError
from .legacy_adapter import Legacy093Adapter


def _optional_path(value: str) -> Path | None:
    return Path(value) if value else None


def _write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    os.replace(temporary, path)


def _process_is_running(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32
    open_process = kernel32.OpenProcess
    open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    open_process.restype = ctypes.c_void_p
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_exit_code.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000, False, process_id)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not get_exit_code(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == 259
    finally:
        close_handle(handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--resource-index", default="")
    parser.add_argument("--aa-data", default="")
    parser.add_argument("--name-baseline", default="")
    parser.add_argument("--token", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result_path = Path(args.result)
    settings = Settings(
        project_root=Path(args.project_root),
        data_dir=Path(args.data_dir),
        legacy_root=Path(args.legacy_root),
        resource_index=_optional_path(args.resource_index),
        aa_data=_optional_path(args.aa_data),
        name_baseline=_optional_path(args.name_baseline),
        host="127.0.0.1",
        port=0,
    )
    try:
        settings.prepare()
        if not _process_is_running(args.parent_pid):
            raise ProductionError(
                "attempt_abandoned", "父进程已退出，编译任务已放弃", status=409
            )
        adapter = Legacy093Adapter(settings)
        result = adapter.execute_compile(args.token, args.build_id)
        if not _process_is_running(args.parent_pid):
            adapter.discard_compile_output(args.token, args.build_id)
            raise ProductionError(
                "attempt_abandoned", "父进程已退出，编译结果已清理", status=409
            )
        payload = {"ok": True, "result": result}
    except ProductionError as exc:
        payload = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "status": exc.status,
                "details": exc.details,
            },
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "error": {
                "code": "compile_worker_failed",
                "message": "AA 编译子进程失败",
                "status": 500,
                "details": {"type": type(exc).__name__},
            },
        }
    _write_result(result_path, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
