from __future__ import annotations

from dataclasses import dataclass, replace

from .errors import ConcurrencyConflict
from .timeline import SubtitleTimeline


def _validate_identity(track_id: str, workspace_id: str, project_id: str, version: int) -> None:
    if not track_id or not workspace_id or not project_id:
        raise ValueError("track, workspace and project identities are required")
    if type(version) is not int or version < 1:
        raise ValueError("version must be a positive integer")


@dataclass(frozen=True, slots=True)
class AudioTrackVersion:
    track_id: str
    workspace_id: str
    project_id: str
    version: int
    object_key: str

    @classmethod
    def create(
        cls, track_id: str, workspace_id: str, project_id: str, version: int, object_key: str
    ) -> AudioTrackVersion:
        _validate_identity(track_id, workspace_id, project_id, version)
        if not object_key:
            raise ValueError("object storage key is required")
        return cls(track_id, workspace_id, project_id, version, object_key)


@dataclass(frozen=True, slots=True)
class SubtitleTrackVersion:
    track_id: str
    workspace_id: str
    project_id: str
    version: int
    timeline: SubtitleTimeline

    @classmethod
    def create(
        cls, track_id: str, workspace_id: str, project_id: str, version: int, timeline: SubtitleTimeline
    ) -> SubtitleTrackVersion:
        _validate_identity(track_id, workspace_id, project_id, version)
        return cls(track_id, workspace_id, project_id, version, timeline)


def _check_version(current: int, expected: int) -> None:
    if current != expected:
        raise ConcurrencyConflict(expected_version=expected, current_version=current)


def next_audio_version(current: AudioTrackVersion, *, expected_version: int, object_key: str) -> AudioTrackVersion:
    _check_version(current.version, expected_version)
    if not object_key:
        raise ValueError("object storage key is required")
    return replace(current, version=current.version + 1, object_key=object_key)


def next_subtitle_version(
    current: SubtitleTrackVersion, *, expected_version: int, timeline: SubtitleTimeline
) -> SubtitleTrackVersion:
    _check_version(current.version, expected_version)
    return replace(current, version=current.version + 1, timeline=timeline)
