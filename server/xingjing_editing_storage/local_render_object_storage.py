from __future__ import annotations

import asyncio
import hashlib
import os
import re
from pathlib import Path
from typing import Protocol

from server.xingjing_editing import StoredRenderObject

_SCOPE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_HASH_CHUNK_SIZE = 1024 * 1024


class ObjectStorageError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ObjectStorageConfigurationError(ObjectStorageError):
    def __init__(self, root: Path) -> None:
        super().__init__("OBJECT_STORAGE_ROOT_INVALID", f"对象存储根目录不可用: {root}")


class ObjectStorageKeyError(ObjectStorageError):
    def __init__(self, message: str) -> None:
        super().__init__("OBJECT_STORAGE_KEY_INVALID", message)


class MediaDurationUnavailable(ObjectStorageError):
    def __init__(self, object_key: str) -> None:
        super().__init__("MEDIA_DURATION_UNAVAILABLE", f"无法可靠获得渲染产物时长: {object_key}")


class MediaDurationInvalid(ObjectStorageError):
    def __init__(self, object_key: str) -> None:
        super().__init__("MEDIA_DURATION_INVALID", f"媒体验证器返回了无效时长: {object_key}")


class MediaProbeFailed(ObjectStorageError):
    def __init__(self, object_key: str) -> None:
        super().__init__("MEDIA_PROBE_FAILED", f"无法探测渲染产物媒体元数据: {object_key}")


class RenderMediaProbe(Protocol):
    """可替换的媒体时长探测端口；None 表示时长未知。"""

    async def probe_duration_ms(self, path: Path) -> int | None: ...


class LocalRenderObjectStorage:
    """以本地文件系统实现的 M08 渲染对象存储校验端口。

    对象键必须为 ``{tenant_id}/{workspace_id}/...``，避免调用方借由键名
    越过当前租户和工作区。文件内容、大小和摘要都从磁盘重新读取，媒体时长
    只接受注入探测器的真实结果；未知时长会显式拒绝该产物。
    """

    def __init__(self, *, root: Path | str, media_probe: RenderMediaProbe) -> None:
        configured_root = Path(root).expanduser()
        if not configured_root.is_dir():
            raise ObjectStorageConfigurationError(configured_root)
        self._root = configured_root.resolve()
        self._media_probe = media_probe

    async def stat(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        object_key: str,
    ) -> StoredRenderObject | None:
        path = self._resolve_object_path(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            object_key=object_key,
        )
        metadata = await asyncio.to_thread(self._read_file_metadata, path)
        if metadata is None:
            return None
        size_bytes, content_sha256 = metadata
        try:
            duration_ms = await self._media_probe.probe_duration_ms(path)
        except Exception as error:
            raise MediaProbeFailed(object_key) from error
        if duration_ms is None:
            raise MediaDurationUnavailable(object_key)
        if isinstance(duration_ms, bool) or duration_ms < 0:
            raise MediaDurationInvalid(object_key)
        return StoredRenderObject(
            object_key=object_key,
            content_sha256=content_sha256,
            size_bytes=size_bytes,
            duration_ms=duration_ms,
        )

    def _resolve_object_path(self, *, tenant_id: str, workspace_id: str, object_key: str) -> Path:
        self._validate_scope_component(tenant_id, field="tenant_id")
        self._validate_scope_component(workspace_id, field="workspace_id")
        if not object_key or "\\" in object_key or Path(object_key).is_absolute():
            raise ObjectStorageKeyError("对象键必须是相对 POSIX 路径")
        parts = object_key.split("/")
        if (
            len(parts) < 3
            or parts[0] != tenant_id
            or parts[1] != workspace_id
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ObjectStorageKeyError("对象键不属于当前租户和工作区，或包含非法路径段")
        scope_root = (self._root / tenant_id / workspace_id).resolve()
        if not scope_root.is_relative_to(self._root):
            raise ObjectStorageKeyError("租户工作区目录解析后越出对象存储根目录")
        path = (self._root.joinpath(*parts)).resolve()
        if not path.is_relative_to(scope_root):
            raise ObjectStorageKeyError("对象键解析后越出当前工作区目录")
        return path

    @staticmethod
    def _validate_scope_component(value: str, *, field: str) -> None:
        if not _SCOPE_COMPONENT.fullmatch(value):
            raise ObjectStorageKeyError(f"{field} 不是安全的对象存储路径段")

    @staticmethod
    def _read_file_metadata(path: Path) -> tuple[int, str] | None:
        try:
            with path.open("rb") as source:
                initial_size = os.fstat(source.fileno()).st_size
                digest = hashlib.sha256()
                read_size = 0
                while chunk := source.read(_HASH_CHUNK_SIZE):
                    digest.update(chunk)
                    read_size += len(chunk)
        except FileNotFoundError:
            return None
        except IsADirectoryError:
            return None
        if read_size != initial_size:
            raise ObjectStorageError("OBJECT_STORAGE_FILE_CHANGED", "读取期间渲染产物发生变化")
        return read_size, digest.hexdigest()
