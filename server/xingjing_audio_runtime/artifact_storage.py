"""Disk-backed, scope-isolated M07 artifact storage for a mounted deployment volume."""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from server.xingjing_audio_persistence.repository import AudioScope

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MIME_TYPES = {
    ".aac": "audio/aac", ".ass": "text/x-ssa", ".flac": "audio/flac", ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg", ".mp4": "video/mp4", ".ogg": "audio/ogg", ".srt": "application/x-subrip",
    ".vtt": "text/vtt", ".wav": "audio/wav", ".webm": "video/webm", ".webvtt": "text/vtt",
}


class AudioArtifactStorageError(ValueError):
    """An object or Provider output violates the managed storage boundary."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    size_bytes: int
    mime_type: str


class LocalAudioObjectStorage:
    """A real mounted-volume object store; it never accepts arbitrary file paths."""

    def __init__(self, root: str | Path, *, provider_project_root: str | Path | None = None) -> None:
        self._root = Path(root).expanduser().resolve()
        self._provider_root = Path(provider_project_root).expanduser().resolve() if provider_project_root else None

    async def inspect(self, *, scope: AudioScope, object_key: str) -> StoredObject:
        return await asyncio.to_thread(self._inspect, scope, object_key)

    def file_for(self, *, scope: AudioScope, object_key: str) -> Path:
        """Return a verified local path for a route that has already authorized scope access."""
        return self._resolve_scoped_object(scope, object_key)

    async def import_provider_output(
        self, *, scope: AudioScope, task_id: str, output_id: str, source_path: str
    ) -> StoredObject:
        return await asyncio.to_thread(self._import_provider_output, scope, task_id, output_id, source_path)

    def _inspect(self, scope: AudioScope, object_key: str) -> StoredObject:
        path = self._resolve_scoped_object(scope, object_key)
        return self._stored(path, object_key)

    def _import_provider_output(self, scope: AudioScope, task_id: str, output_id: str, source_path: str) -> StoredObject:
        if self._provider_root is None:
            raise AudioArtifactStorageError("AUDIO_PROVIDER_PROJECT_ROOT_NOT_CONFIGURED")
        for value in (task_id, output_id):
            if not _IDENTIFIER.fullmatch(value):
                raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_IDENTIFIER_INVALID")
        source_candidate = Path(source_path)
        if not source_path or source_candidate.is_absolute() or ".." in source_candidate.parts:
            raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_PATH_INVALID")
        try:
            source = (self._provider_root / source_candidate).resolve(strict=True)
            source.relative_to(self._provider_root)
        except (FileNotFoundError, ValueError) as error:
            raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_NOT_FOUND") from error
        if not source.is_file():
            raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_NOT_FILE")
        suffix = source.suffix.lower()
        if suffix not in _MIME_TYPES:
            raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_MIME_UNSUPPORTED")
        target_dir = self._root / scope.tenant_id / scope.workspace_id / scope.project_id / task_id
        target = target_dir / f"{output_id}{suffix}"
        try:
            target.resolve().relative_to(self._root)
        except ValueError as error:
            raise AudioArtifactStorageError("AUDIO_OBJECT_KEY_INVALID") from error
        target_dir.mkdir(parents=True, exist_ok=True)
        temporary = target_dir / f".{output_id}.{uuid4().hex}.uploading"
        digest = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as source_file, temporary.open("xb") as destination:
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    destination.write(chunk)
            if size <= 0:
                raise AudioArtifactStorageError("AUDIO_PROVIDER_OUTPUT_EMPTY")
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return StoredObject(key=target.relative_to(self._root).as_posix(), sha256=digest.hexdigest(), size_bytes=size, mime_type=_MIME_TYPES[suffix])

    def _resolve_scoped_object(self, scope: AudioScope, object_key: str) -> Path:
        candidate = Path(object_key)
        if not object_key or candidate.is_absolute() or ".." in candidate.parts:
            raise AudioArtifactStorageError("AUDIO_OBJECT_KEY_INVALID")
        expected = (scope.tenant_id, scope.workspace_id, scope.project_id)
        if tuple(candidate.parts[:3]) != expected or len(candidate.parts) < 4:
            raise AudioArtifactStorageError("AUDIO_OBJECT_SCOPE_MISMATCH")
        try:
            resolved = (self._root / candidate).resolve(strict=True)
            resolved.relative_to(self._root)
        except (FileNotFoundError, ValueError) as error:
            raise AudioArtifactStorageError("AUDIO_OBJECT_NOT_FOUND") from error
        if not resolved.is_file():
            raise AudioArtifactStorageError("AUDIO_OBJECT_NOT_FILE")
        return resolved

    @staticmethod
    def _stored(path: Path, object_key: str) -> StoredObject:
        mime_type = _MIME_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0]
        if not mime_type:
            raise AudioArtifactStorageError("AUDIO_OBJECT_MIME_UNSUPPORTED")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        if size <= 0:
            raise AudioArtifactStorageError("AUDIO_OBJECT_EMPTY")
        return StoredObject(key=object_key, sha256=digest.hexdigest(), size_bytes=size, mime_type=mime_type)
