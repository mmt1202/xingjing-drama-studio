from __future__ import annotations

import pytest

from server.xingjing_audio import (
    AudioTrackVersion,
    ConcurrencyConflict,
    SubtitleCue,
    SubtitleTimeline,
    SubtitleTrackVersion,
    next_audio_version,
    next_subtitle_version,
)


def test_track_versions_are_immutable_successors() -> None:
    audio = AudioTrackVersion.create(
        track_id="audio-1", workspace_id="ws-1", project_id="p-1", version=3, object_key="audio/v3.wav"
    )
    timeline = SubtitleTimeline.create(100, (SubtitleCue("c1", 0, 100, "台词"),))
    subtitle = SubtitleTrackVersion.create(
        track_id="sub-1", workspace_id="ws-1", project_id="p-1", version=5, timeline=timeline
    )

    assert next_audio_version(audio, expected_version=3, object_key="audio/v4.wav").version == 4
    assert next_subtitle_version(subtitle, expected_version=5, timeline=timeline).version == 6
    assert audio.version == 3
    assert subtitle.version == 5


def test_stale_track_update_reports_version_conflict() -> None:
    audio = AudioTrackVersion.create("audio-1", "ws-1", "p-1", 3, "audio/v3.wav")

    with pytest.raises(ConcurrencyConflict) as caught:
        next_audio_version(audio, expected_version=2, object_key="audio/v4.wav")

    assert caught.value.code == "VERSION_CONFLICT"
    assert caught.value.current_version == 3
