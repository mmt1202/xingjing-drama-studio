from __future__ import annotations

import pytest

from server.xingjing_audio import (
    AudioTrackVersion,
    CrossScopeReference,
    MediaObjectNotFound,
    SubtitleCue,
    SubtitleTimeline,
    SubtitleTrackVersion,
    TrackVersionService,
)


class AudioRepositoryStub:
    def __init__(self, current: AudioTrackVersion) -> None:
        self.current = current
        self.appended: list[tuple[AudioTrackVersion, int]] = []

    async def get_current(self, *, workspace_id: str, track_id: str) -> AudioTrackVersion | None:
        if self.current.workspace_id == workspace_id and self.current.track_id == track_id:
            return self.current
        return None

    async def append(self, version: AudioTrackVersion, *, expected_version: int) -> None:
        self.appended.append((version, expected_version))


class SubtitleRepositoryStub:
    def __init__(self, current: SubtitleTrackVersion) -> None:
        self.current = current
        self.appended: list[tuple[SubtitleTrackVersion, int]] = []

    async def get_current(self, *, workspace_id: str, track_id: str) -> SubtitleTrackVersion | None:
        if self.current.workspace_id == workspace_id and self.current.track_id == track_id:
            return self.current
        return None

    async def append(self, version: SubtitleTrackVersion, *, expected_version: int) -> None:
        self.appended.append((version, expected_version))


class StorageStub:
    def __init__(self, existing: set[tuple[str, str]]) -> None:
        self.existing = existing

    async def exists(self, *, workspace_id: str, object_key: str) -> bool:
        return (workspace_id, object_key) in self.existing


def audio_version() -> AudioTrackVersion:
    return AudioTrackVersion.create("audio-1", "ws-1", "p-1", 2, "audio/v2.wav")


def subtitle_version() -> SubtitleTrackVersion:
    timeline = SubtitleTimeline.create(100, (SubtitleCue("cue-1", 0, 100, "台词"),))
    return SubtitleTrackVersion.create("sub-1", "ws-1", "p-1", 3, timeline)


@pytest.mark.asyncio
async def test_audio_version_is_appended_only_for_existing_workspace_object() -> None:
    audio_repository = AudioRepositoryStub(audio_version())
    service = TrackVersionService(
        audio_repository,
        SubtitleRepositoryStub(subtitle_version()),
        StorageStub({("ws-1", "audio/v3.wav")}),
    )

    created = await service.append_audio(
        workspace_id="ws-1",
        project_id="p-1",
        track_id="audio-1",
        expected_version=2,
        object_key="audio/v3.wav",
    )

    assert created.version == 3
    assert audio_repository.appended == [(created, 2)]


@pytest.mark.asyncio
async def test_audio_version_rejects_missing_storage_output() -> None:
    service = TrackVersionService(
        AudioRepositoryStub(audio_version()),
        SubtitleRepositoryStub(subtitle_version()),
        StorageStub(set()),
    )

    with pytest.raises(MediaObjectNotFound):
        await service.append_audio(
            workspace_id="ws-1",
            project_id="p-1",
            track_id="audio-1",
            expected_version=2,
            object_key="audio/missing.wav",
        )


@pytest.mark.asyncio
async def test_track_update_rejects_cross_project_reference() -> None:
    service = TrackVersionService(
        AudioRepositoryStub(audio_version()),
        SubtitleRepositoryStub(subtitle_version()),
        StorageStub({("ws-1", "audio/v3.wav")}),
    )

    with pytest.raises(CrossScopeReference):
        await service.append_audio(
            workspace_id="ws-1",
            project_id="p-other",
            track_id="audio-1",
            expected_version=2,
            object_key="audio/v3.wav",
        )


@pytest.mark.asyncio
async def test_subtitle_version_preserves_repository_compare_and_swap_boundary() -> None:
    repository = SubtitleRepositoryStub(subtitle_version())
    service = TrackVersionService(AudioRepositoryStub(audio_version()), repository, StorageStub(set()))
    updated_timeline = SubtitleTimeline.create(100, (SubtitleCue("cue-1", 0, 100, "新台词"),))

    created = await service.append_subtitle(
        workspace_id="ws-1",
        project_id="p-1",
        track_id="sub-1",
        expected_version=3,
        timeline=updated_timeline,
    )

    assert created.version == 4
    assert repository.appended == [(created, 3)]
