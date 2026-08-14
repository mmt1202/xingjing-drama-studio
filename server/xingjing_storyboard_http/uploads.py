from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import uuid4


@dataclass(slots=True)
class StoredUpload:
    upload_id: str
    filename: str
    content: bytes = b""
    completed: bool = False


class InMemoryStoryboardUploadStore:
    """HTTP 导入和导出的进程内测试端口；生产可注入持久化实现。"""

    def __init__(self) -> None:
        self._uploads: dict[tuple[str, str, str, str], StoredUpload] = {}
        self._exports: dict[tuple[str, str, str, str], tuple[bytes, str, str]] = {}

    @staticmethod
    def _scope(context: object) -> tuple[str, str, str]:
        return (
            str(getattr(context, "tenant_id")),
            str(getattr(context, "workspace_id")),
            str(getattr(context, "project_id")),
        )

    def create(self, context: object, *, filename: str, **_: object) -> StoredUpload:
        upload = StoredUpload(f"upload-{uuid4()}", filename)
        self._uploads[(*self._scope(context), upload.upload_id)] = upload
        return upload

    def put(self, context: object, upload_id: str, content: bytes) -> bool:
        upload = self._uploads.get((*self._scope(context), upload_id))
        if upload is None:
            return False
        upload.content = content
        return True

    def complete(self, context: object, upload_id: str, **_: object) -> bool:
        upload = self._uploads.get((*self._scope(context), upload_id))
        if upload is None:
            return False
        upload.completed = True
        return True

    def get(self, context: object, upload_id: str) -> StoredUpload | None:
        upload = self._uploads.get((*self._scope(context), upload_id))
        return upload if upload and upload.completed else None

    def put_export(self, context: object, content: bytes, media_type: str, filename: str) -> str:
        export_id = sha256(content).hexdigest()
        self._exports[(*self._scope(context), export_id)] = (content, media_type, filename)
        return export_id

    def get_export(self, context: object, export_id: str) -> tuple[bytes, str, str] | None:
        return self._exports.get((*self._scope(context), export_id))
