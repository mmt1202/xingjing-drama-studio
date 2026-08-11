from __future__ import annotations

from .errors import CrossScopeReference, MediaObjectNotFound, TaskNotFound, TrackNotFound
from .ports import (
    AudioTrackRepository,
    MediaProvider,
    MediaTaskDispatcher,
    MediaTaskRepository,
    ObjectStorage,
    SubtitleTrackRepository,
)
from .tasks import MediaTask, MediaTaskStatus
from .timeline import SubtitleTimeline
from .tracks import (
    AudioTrackVersion,
    SubtitleTrackVersion,
    next_audio_version,
    next_subtitle_version,
)


class TaskCoordinator:
    """Coordinates persistence and dispatch without choosing concrete infrastructure."""

    def __init__(
        self,
        repository: MediaTaskRepository,
        dispatcher: MediaTaskDispatcher,
        *,
        provider: MediaProvider | None = None,
    ) -> None:
        self._repository = repository
        self._dispatcher = dispatcher
        self._provider = provider

    async def enqueue(self, task: MediaTask) -> MediaTask:
        registration = await self._repository.add_idempotent(task)
        if registration.created:
            await self._dispatcher.dispatch(registration.task)
        return registration.task

    async def cancel(self, *, workspace_id: str, task_id: str, reason: str) -> MediaTask:
        task = await self._repository.get(workspace_id=workspace_id, task_id=task_id)
        if task is None:
            raise TaskNotFound()
        if task.status is MediaTaskStatus.RUNNING:
            cancelling = task.request_cancel(reason)
            if task.provider_job_id is not None:
                if self._provider is None:
                    raise RuntimeError("provider cancellation port is not configured")
                await self._provider.cancel(task.provider_job_id)
            cancelled = cancelling.confirm_cancelled()
        else:
            cancelled = task.cancel(reason)
        await self._repository.save(cancelled)
        return cancelled


class TrackVersionService:
    """Creates immutable track successors at persistence and storage boundaries."""

    def __init__(
        self,
        audio_repository: AudioTrackRepository,
        subtitle_repository: SubtitleTrackRepository,
        storage: ObjectStorage,
    ) -> None:
        self._audio_repository = audio_repository
        self._subtitle_repository = subtitle_repository
        self._storage = storage

    @staticmethod
    def _check_project(*, requested_project_id: str, stored_project_id: str) -> None:
        if requested_project_id != stored_project_id:
            raise CrossScopeReference("track belongs to another project")

    async def append_audio(
        self,
        *,
        workspace_id: str,
        project_id: str,
        track_id: str,
        expected_version: int,
        object_key: str,
    ) -> AudioTrackVersion:
        current = await self._audio_repository.get_current(workspace_id=workspace_id, track_id=track_id)
        if current is None:
            raise TrackNotFound()
        self._check_project(requested_project_id=project_id, stored_project_id=current.project_id)
        if not await self._storage.exists(workspace_id=workspace_id, object_key=object_key):
            raise MediaObjectNotFound()
        successor = next_audio_version(current, expected_version=expected_version, object_key=object_key)
        await self._audio_repository.append(successor, expected_version=expected_version)
        return successor

    async def append_subtitle(
        self,
        *,
        workspace_id: str,
        project_id: str,
        track_id: str,
        expected_version: int,
        timeline: SubtitleTimeline,
    ) -> SubtitleTrackVersion:
        current = await self._subtitle_repository.get_current(workspace_id=workspace_id, track_id=track_id)
        if current is None:
            raise TrackNotFound()
        self._check_project(requested_project_id=project_id, stored_project_id=current.project_id)
        successor = next_subtitle_version(current, expected_version=expected_version, timeline=timeline)
        await self._subtitle_repository.append(successor, expected_version=expected_version)
        return successor
