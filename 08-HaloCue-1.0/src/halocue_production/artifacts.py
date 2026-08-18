from __future__ import annotations

import errno
import hashlib
import json
import mimetypes
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable
from urllib.parse import unquote, urlsplit

from .errors import ProductionError
from .runtime import RuntimeStore


_URI_NAMESPACE = re.compile(r"[a-z][a-z0-9._-]{0,79}")
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class WorkspaceFile:
    uri: str
    relative_path: str
    kind: str
    content_hash: str
    size_bytes: int
    media_type: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "WorkspaceFile":
        metadata = value.get("metadata")
        return cls(
            uri=str(value["uri"]),
            relative_path=str(value["relative_path"]),
            kind=str(value["kind"]),
            content_hash=str(value["content_hash"]),
            size_bytes=int(value["size_bytes"]),
            media_type=str(value["media_type"]),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            created_at=str(value["created_at"]),
            updated_at=str(value["updated_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "relative_path": self.relative_path,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "size_bytes": self.size_bytes,
            "media_type": self.media_type,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ArtifactRef:
    uri: str
    workspace_uri: str
    kind: str
    content_hash: str
    run_id: str | None
    work_item_id: str | None
    attempt_id: str | None
    created_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ArtifactRef":
        return cls(
            uri=str(value["uri"]),
            workspace_uri=str(value["workspace_uri"]),
            kind=str(value["kind"]),
            content_hash=str(value["content_hash"]),
            run_id=value.get("run_id"),
            work_item_id=value.get("work_item_id"),
            attempt_id=value.get("attempt_id"),
            created_at=str(value["created_at"]),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uri": self.uri,
            "workspace_uri": self.workspace_uri,
            "kind": self.kind,
            "content_hash": self.content_hash,
            "run_id": self.run_id,
            "work_item_id": self.work_item_id,
            "attempt_id": self.attempt_id,
            "created_at": self.created_at,
        }


class ArtifactStore:
    """Commit workspace files without exposing physical paths as domain data."""

    def __init__(self, root: Path, runtime: RuntimeStore) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = runtime
        self._lock = threading.RLock()

    def commit_bytes(
        self,
        uri: str,
        content: bytes,
        *,
        kind: str,
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceFile:
        if not isinstance(content, bytes):
            raise ProductionError(
                "artifact_content_invalid", "工作区文件内容必须是字节", status=400
            )
        digest = hashlib.sha256(content).hexdigest()

        def copy_to(target: BinaryIO) -> None:
            for offset in range(0, len(content), _CHUNK_SIZE):
                target.write(content[offset : offset + _CHUNK_SIZE])

        return self._commit(
            uri,
            kind=kind,
            media_type=media_type,
            metadata=metadata,
            size_bytes=len(content),
            content_hash=f"sha256:{digest}",
            copy_to=copy_to,
        )

    def commit_file(
        self,
        uri: str,
        source: Path,
        *,
        kind: str,
        media_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WorkspaceFile:
        source_path = Path(source)
        if not source_path.is_file():
            raise ProductionError(
                "artifact_source_not_found",
                "工作区文件来源不存在或不是文件",
                status=404,
            )
        try:
            digest, size_bytes = self._hash_file(source_path)
        except OSError as exc:
            raise ProductionError(
                "artifact_source_unreadable", "工作区文件来源无法读取", status=422
            ) from exc

        def copy_to(target: BinaryIO) -> None:
            with source_path.open("rb") as source_file:
                while True:
                    chunk = source_file.read(_CHUNK_SIZE)
                    if not chunk:
                        break
                    target.write(chunk)

        return self._commit(
            uri,
            kind=kind,
            media_type=media_type,
            metadata=metadata,
            size_bytes=size_bytes,
            content_hash=digest,
            copy_to=copy_to,
        )

    def get(self, uri: str) -> WorkspaceFile:
        normalized_uri, relative_path, target = self._location(uri)
        record = self.runtime.get_workspace_file(normalized_uri)
        if record is None:
            raise ProductionError(
                "workspace_file_not_found",
                "工作区文件尚未登记",
                status=404,
                details={"uri": normalized_uri},
            )
        if record["relative_path"] != relative_path:
            raise ProductionError(
                "workspace_file_metadata_corrupt",
                "工作区文件登记路径无效",
                status=500,
                details={"uri": normalized_uri},
            )
        self._verify_target(target, record)
        return WorkspaceFile.from_dict(record)

    def read_bytes(self, uri: str) -> bytes:
        normalized_uri, relative_path, target = self._location(uri)
        record = self.runtime.get_workspace_file(normalized_uri)
        if record is None:
            raise ProductionError(
                "workspace_file_not_found",
                "工作区文件尚未登记",
                status=404,
                details={"uri": normalized_uri},
            )
        if record["relative_path"] != relative_path:
            raise ProductionError(
                "workspace_file_metadata_corrupt",
                "工作区文件登记路径无效",
                status=500,
                details={"uri": normalized_uri},
            )
        if target.is_symlink():
            raise ProductionError(
                "workspace_file_missing", "工作区文件内容不存在", status=500
            )
        try:
            content = target.read_bytes()
        except OSError as exc:
            raise ProductionError(
                "workspace_file_missing", "工作区文件内容不存在", status=500
            ) from exc
        actual_hash = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if len(content) != int(record["size_bytes"]) or actual_hash != record["content_hash"]:
            raise ProductionError(
                "artifact_hash_mismatch",
                "工作区文件哈希与登记不一致",
                status=500,
                details={"uri": normalized_uri, "expected_hash": record["content_hash"]},
            )
        return content

    def publish_artifact(
        self,
        namespace: str,
        artifact_id: str,
        workspace_file: WorkspaceFile,
        *,
        provenance: dict[str, str | None] | None = None,
    ) -> ArtifactRef:
        normalized_namespace = self._artifact_namespace(namespace)
        normalized_id = self._artifact_id(artifact_id)
        verified_file = self.get(workspace_file.uri)
        provenance = {} if provenance is None else dict(provenance)
        allowed = {"run_id", "work_item_id", "attempt_id"}
        if set(provenance) - allowed:
            raise ProductionError(
                "artifact_provenance_invalid",
                "artifact 来源元数据包含不支持的字段",
                status=400,
            )
        refs = {
            key: self._optional_uuid(provenance.get(key))
            for key in allowed
        }
        uri = f"artifact://{normalized_namespace}/{normalized_id}"
        record = self.runtime.register_artifact_ref(
            uri=uri,
            workspace_uri=verified_file.uri,
            kind=verified_file.kind,
            content_hash=verified_file.content_hash,
            run_id=refs["run_id"],
            work_item_id=refs["work_item_id"],
            attempt_id=refs["attempt_id"],
        )
        return ArtifactRef.from_dict(record)

    def get_artifact(self, uri: str) -> ArtifactRef:
        normalized_uri = self._artifact_uri(uri)
        record = self.runtime.get_artifact_ref(normalized_uri)
        if record is None:
            raise ProductionError(
                "artifact_ref_not_found",
                "artifact 引用尚未登记",
                status=404,
                details={"uri": normalized_uri},
            )
        workspace = self.get(str(record["workspace_uri"]))
        if workspace.content_hash != str(record["content_hash"]):
            raise ProductionError(
                "artifact_hash_mismatch",
                "artifact 引用哈希与工作区文件不一致",
                status=500,
                details={"uri": normalized_uri},
            )
        return ArtifactRef.from_dict(record)

    def read_artifact_bytes(self, uri: str) -> bytes:
        artifact = self.get_artifact(uri)
        return self.read_bytes(artifact.workspace_uri)

    def _commit(
        self,
        uri: str,
        *,
        kind: str,
        media_type: str | None,
        metadata: dict[str, Any] | None,
        size_bytes: int,
        content_hash: str,
        copy_to: Callable[[BinaryIO], None],
    ) -> WorkspaceFile:
        normalized_uri, relative_path, target = self._location(uri)
        normalized_kind = self._identifier(kind, "invalid_artifact_kind")
        normalized_media_type = self._media_type(media_type, relative_path)
        normalized_metadata = self._metadata(metadata)
        with self._lock:
            existing = self.runtime.get_workspace_file(normalized_uri)
            self._check_existing_record(
                existing,
                normalized_uri=normalized_uri,
                relative_path=relative_path,
                kind=normalized_kind,
                content_hash=content_hash,
                size_bytes=size_bytes,
                media_type=normalized_media_type,
            )
            duplicate = self.runtime.find_workspace_file_by_hash(content_hash)
            if duplicate is not None and duplicate["uri"] != normalized_uri:
                raise ProductionError(
                    "workspace_file_duplicate",
                    "相同内容已在工作区登记",
                    status=409,
                    details={
                        "uri": normalized_uri,
                        "existing_uri": duplicate["uri"],
                        "content_hash": content_hash,
                    },
                )
            created_target = False
            if target.exists() or target.is_symlink():
                if target.is_symlink() or not target.is_file():
                    raise ProductionError(
                        "workspace_file_target_invalid",
                        "工作区文件目标不是安全的普通文件",
                        status=409,
                        details={"uri": normalized_uri},
                    )
                try:
                    actual_hash, actual_size = self._hash_file(target)
                except OSError as exc:
                    raise ProductionError(
                        "workspace_file_unreadable",
                        "工作区文件内容无法读取",
                        status=500,
                        details={"uri": normalized_uri},
                    ) from exc
                if actual_hash != content_hash or actual_size != size_bytes:
                    raise ProductionError(
                        "workspace_file_conflict",
                        "同一工作区 URI 已存在不同内容",
                        status=409,
                        details={"uri": normalized_uri, "existing_hash": actual_hash},
                    )
            else:
                temporary: Path | None = None
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
                    with temporary.open("xb") as handle:
                        copy_to(handle)
                        handle.flush()
                        os.fsync(handle.fileno())
                    actual_hash, actual_size = self._hash_file(temporary)
                    if actual_hash != content_hash or actual_size != size_bytes:
                        raise ProductionError(
                            "artifact_source_changed",
                            "工作区文件来源在提交过程中发生变化",
                            status=409,
                            details={"uri": normalized_uri},
                        )
                    os.replace(temporary, target)
                    created_target = True
                except ProductionError:
                    raise
                except OSError as exc:
                    raise self._write_error(exc) from exc
                finally:
                    if temporary is not None:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
            try:
                record = self.runtime.register_workspace_file(
                    uri=normalized_uri,
                    relative_path=relative_path,
                    kind=normalized_kind,
                    content_hash=content_hash,
                    size_bytes=size_bytes,
                    media_type=normalized_media_type,
                    metadata=normalized_metadata,
                )
            except Exception:
                if created_target:
                    try:
                        if target.is_file() and not target.is_symlink():
                            actual_hash, actual_size = self._hash_file(target)
                            if actual_hash == content_hash and actual_size == size_bytes:
                                target.unlink()
                    except OSError:
                        pass
                raise
            return WorkspaceFile.from_dict(record)

    def _location(self, uri: str) -> tuple[str, str, Path]:
        text = str(uri or "").strip()
        if not text or len(text) > 512:
            self._invalid_uri()
        decoded = text
        for _ in range(16):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        else:
            self._invalid_uri()
        parsed = urlsplit(decoded)
        namespace = parsed.netloc.casefold()
        if (
            parsed.scheme != "workspace"
            or not _URI_NAMESPACE.fullmatch(namespace)
            or not parsed.path.startswith("/")
            or parsed.path == "/"
            or parsed.query
            or parsed.fragment
            or "\\" in decoded
        ):
            self._invalid_uri()
        parts = parsed.path.split("/")[1:]
        if any(
            not part
            or part in {".", ".."}
            or ":" in part
            or "\\" in part
            or any(ord(character) < 32 for character in part)
            for part in parts
        ):
            self._invalid_uri()
        normalized_uri = f"workspace://{namespace}/{'/'.join(parts)}"
        relative_path = "/".join((namespace, *parts))
        target = self._safe_target(relative_path)
        return normalized_uri, relative_path, target

    @staticmethod
    def _artifact_namespace(value: Any) -> str:
        text = str(value or "").strip().casefold()
        if not _IDENTIFIER.fullmatch(text):
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 命名空间无效",
                status=400,
            )
        return text

    @staticmethod
    def _artifact_id(value: Any) -> str:
        text = str(value or "").strip()
        try:
            parsed = uuid.UUID(text)
        except (ValueError, AttributeError) as exc:
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 必须使用 canonical UUID",
                status=400,
            ) from exc
        if str(parsed) != text:
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 必须使用 canonical UUID",
                status=400,
            )
        return text

    @classmethod
    def _artifact_uri(cls, uri: Any) -> str:
        text = str(uri or "").strip()
        if not text or len(text) > 512:
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 无效",
                status=400,
            )
        decoded = text
        for _ in range(16):
            next_value = unquote(decoded)
            if next_value == decoded:
                break
            decoded = next_value
        else:
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 不得包含多重编码",
                status=400,
            )
        parsed = urlsplit(decoded)
        parts = parsed.path.split("/")[1:]
        if (
            parsed.scheme != "artifact"
            or parsed.query
            or parsed.fragment
            or "\\" in decoded
            or len(parts) != 1
        ):
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 无效或包含路径穿越",
                status=400,
            )
        namespace = cls._artifact_namespace(parsed.netloc)
        if any(part in {"", ".", ".."} for part in parts):
            raise ProductionError(
                "artifact_uri_invalid",
                "artifact URI 无效或包含路径穿越",
                status=400,
            )
        artifact_id = cls._artifact_id(parts[0])
        return f"artifact://{namespace}/{artifact_id}"

    @staticmethod
    def _optional_uuid(value: Any) -> str | None:
        if value is None or str(value).strip() == "":
            return None
        text = str(value).strip()
        try:
            parsed = uuid.UUID(text)
        except (ValueError, AttributeError) as exc:
            raise ProductionError(
                "artifact_provenance_invalid",
                "artifact 来源 ID 必须是 canonical UUID",
                status=400,
            ) from exc
        if str(parsed) != text:
            raise ProductionError(
                "artifact_provenance_invalid",
                "artifact 来源 ID 必须是 canonical UUID",
                status=400,
            )
        return text

    def _safe_target(self, relative_path: str) -> Path:
        target = self.root.joinpath(*relative_path.split("/"))
        try:
            resolved = target.resolve(strict=False)
            resolved.relative_to(self.root)
        except (OSError, ValueError) as exc:
            raise ProductionError(
                "workspace_path_invalid", "工作区路径越过受保护目录", status=400
            ) from exc
        current = target.parent
        while current != self.root:
            if current.is_symlink():
                raise ProductionError(
                    "workspace_path_invalid", "工作区路径包含不安全的符号链接", status=400
                )
            current = current.parent
        return target

    @staticmethod
    def _invalid_uri() -> None:
        raise ProductionError(
            "workspace_uri_invalid",
            "工作区 URI 无效或包含路径穿越",
            status=400,
        )

    @staticmethod
    def _write_error(exc: OSError) -> ProductionError:
        if exc.errno == errno.ENOSPC:
            return ProductionError(
                "artifact_storage_full", "工作区磁盘空间不足", status=507
            )
        return ProductionError(
            "artifact_write_failed", "工作区文件写入失败", status=500
        )

    @staticmethod
    def _identifier(value: Any, code: str) -> str:
        text = str(value or "").strip().casefold()
        if not _IDENTIFIER.fullmatch(text):
            raise ProductionError(code, "工作区文件类型标识无效", status=400)
        return text

    @staticmethod
    def _media_type(value: str | None, relative_path: str) -> str:
        text = str(value or "").strip()
        if not text:
            text = mimetypes.guess_type(relative_path)[0] or "application/octet-stream"
        if len(text) > 120 or "\r" in text or "\n" in text:
            raise ProductionError(
                "artifact_media_type_invalid", "工作区文件媒体类型无效", status=400
            )
        return text

    @staticmethod
    def _metadata(value: dict[str, Any] | None) -> dict[str, Any]:
        metadata = {} if value is None else value
        if not isinstance(metadata, dict):
            raise ProductionError("artifact_metadata_invalid", "工作区文件元数据必须是对象", status=400)
        try:
            json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ProductionError(
                "artifact_metadata_invalid", "工作区文件元数据不是有效 JSON", status=400
            ) from exc
        return json.loads(json.dumps(metadata, ensure_ascii=False, allow_nan=False))

    @staticmethod
    def _hash_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
        return f"sha256:{digest.hexdigest()}", size

    @staticmethod
    def _check_existing_record(
        existing: dict[str, Any] | None,
        *,
        normalized_uri: str,
        relative_path: str,
        kind: str,
        content_hash: str,
        size_bytes: int,
        media_type: str,
    ) -> None:
        if existing is None:
            return
        immutable = {
            "relative_path": relative_path,
            "kind": kind,
            "content_hash": content_hash,
            "size_bytes": int(size_bytes),
            "media_type": media_type,
        }
        if any(str(existing[field]) != str(value) for field, value in immutable.items()):
            raise ProductionError(
                "workspace_file_conflict",
                "同一工作区 URI 已登记不同内容",
                status=409,
                details={"uri": normalized_uri, "existing_hash": existing["content_hash"]},
            )

    @staticmethod
    def _verify_target(target: Path, record: dict[str, Any]) -> None:
        if target.is_symlink() or not target.is_file():
            raise ProductionError(
                "workspace_file_missing", "工作区文件内容不存在", status=500
            )
        try:
            actual_hash, actual_size = ArtifactStore._hash_file(target)
        except OSError as exc:
            raise ProductionError(
                "workspace_file_missing", "工作区文件内容无法读取", status=500
            ) from exc
        if actual_hash != record["content_hash"] or actual_size != int(record["size_bytes"]):
            raise ProductionError(
                "artifact_hash_mismatch",
                "工作区文件哈希与登记不一致",
                status=500,
                details={"uri": record["uri"], "expected_hash": record["content_hash"]},
            )
