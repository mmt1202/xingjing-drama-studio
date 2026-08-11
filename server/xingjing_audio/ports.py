from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .tasks import MediaTask
from .tracks import AudioTrackVersion, SubtitleTrackVersion


@dataclass(frozen=True, slots=True)
class TaskRegistration:
    """Result of an atomic insert-or-return-existing operation."""

    task: MediaTask
    created: bool


class MediaTaskRepository(Protocol):
    async def add_idempotent(self, task: MediaTask) -> TaskRegistration: ...

    async def get(self, *, workspace_id: str, task_id: str) -> MediaTask | None: ...

    async def save(self, task: MediaTask) -> None: ...


class MediaTaskDispatcher(Protocol):
    async def dispatch(self, task: MediaTask) -> None: ...


class AudioTrackRepository(Protocol):
    async def get_current(self, *, workspace_id: str, track_id: str) -> AudioTrackVersion | None: ...

    async def append(self, version: AudioTrackVersion, *, expected_version: int) -> None: ...


class SubtitleTrackRepository(Protocol):
    async def get_current(self, *, workspace_id: str, track_id: str) -> SubtitleTrackVersion | None: ...

    async def append(self, version: SubtitleTrackVersion, *, expected_version: int) -> None: ...


class ObjectStorage(Protocol):
    async def exists(self, *, workspace_id: str, object_key: str) -> bool: ...


class MediaProvider(Protocol):
    async def submit(self, task: MediaTask) -> str: ...

    async def cancel(self, provider_job_id: str) -> None: ...
