"""Scoped local object storage for verified M06 generated artifacts."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXTENSIONS = {
    "image": {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"},
    "video": {".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime"},
}


class GeneratedArtifactStorageError(ValueError):
    pass


@dataclass(frozen=True)
class StoredGeneratedArtifact:
    object_key: str
    content_sha256: str
    size_bytes: int
    media_type: str
    mime_type: str


class LocalGenerationArtifactStore:
    """Copies a verified project-local result into a stable, scoped object root.

    This is a real disk-backed object-store adapter for development deployments.
    The source file is never exposed as an object key, and a source outside the
    authoritative project root is rejected before any metadata is returned.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    def resolve_object_key(self, object_key: str) -> Path:
        """Resolve only a managed object key; never accept a caller file path."""
        candidate = Path(object_key)
        if not object_key or candidate.is_absolute() or ".." in candidate.parts:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_OBJECT_KEY_INVALID")
        try:
            resolved = (self._root / candidate).resolve(strict=True)
            resolved.relative_to(self._root)
        except (FileNotFoundError, ValueError) as error:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_NOT_FOUND") from error
        if not resolved.is_file():
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_NOT_FILE")
        return resolved

    async def import_project_file(
        self,
        *,
        project_root: str | Path,
        workspace_id: str,
        project_id: str,
        task_id: str,
        asset_id: str,
        media_type: str,
        source_path: str | Path,
    ) -> StoredGeneratedArtifact:
        return await asyncio.to_thread(
            self._import_project_file,
            project_root=Path(project_root),
            workspace_id=workspace_id,
            project_id=project_id,
            task_id=task_id,
            asset_id=asset_id,
            media_type=media_type,
            source_path=Path(source_path),
        )

    def _import_project_file(
        self,
        *,
        project_root: Path,
        workspace_id: str,
        project_id: str,
        task_id: str,
        asset_id: str,
        media_type: str,
        source_path: Path,
    ) -> StoredGeneratedArtifact:
        for value in (workspace_id, project_id, task_id, asset_id):
            if not _IDENTIFIER.fullmatch(value):
                raise GeneratedArtifactStorageError("INVALID_GENERATED_ARTIFACT_IDENTIFIER")
        allowed_extensions = _EXTENSIONS.get(media_type)
        if allowed_extensions is None:
            raise GeneratedArtifactStorageError("INVALID_GENERATED_ARTIFACT_MEDIA_TYPE")

        root = self._root
        project_root = project_root.resolve()
        if not source_path.is_absolute():
            source_path = project_root / source_path
        try:
            source = source_path.resolve(strict=True)
        except FileNotFoundError as error:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_SOURCE_NOT_FOUND") from error
        try:
            source.relative_to(project_root)
        except ValueError as error:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_SOURCE_OUTSIDE_PROJECT") from error
        if not source.is_file():
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_SOURCE_NOT_FILE")
        suffix = source.suffix.lower()
        mime_type = allowed_extensions.get(suffix)
        if mime_type is None:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_EXTENSION_MISMATCH")

        target_dir = root / workspace_id / project_id / task_id
        target = target_dir / f"{asset_id}{suffix}"
        try:
            target.resolve().relative_to(root)
        except ValueError as error:
            raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_OBJECT_KEY_INVALID") from error

        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target_dir / f".{asset_id}.{uuid4().hex}.uploading"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as source_file, temporary.open("xb") as destination:
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    destination.write(chunk)
            if size <= 0:
                raise GeneratedArtifactStorageError("GENERATED_ARTIFACT_EMPTY")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

        return StoredGeneratedArtifact(
            object_key=target.relative_to(root).as_posix(),
            content_sha256=digest.hexdigest(),
            size_bytes=size,
            media_type=media_type,
            mime_type=mime_type,
        )
