from __future__ import annotations

import pytest

from server.xingjing_audio import CueValidationError, SubtitleCue, SubtitleTimeline


def test_timeline_accepts_strict_ordered_millisecond_cues() -> None:
    timeline = SubtitleTimeline.create(
        duration_ms=4_000,
        cues=(
            SubtitleCue(cue_id="cue-1", start_ms=0, end_ms=1_250, text="你好"),
            SubtitleCue(cue_id="cue-2", start_ms=1_250, end_ms=4_000, text="世界"),
        ),
    )

    assert timeline.duration_ms == 4_000
    assert timeline.cues[1].start_ms == 1_250


@pytest.mark.parametrize(
    ("cues", "code"),
    [
        ((SubtitleCue("a", -1, 10, "x"),), "NEGATIVE_TIMECODE"),
        ((SubtitleCue("a", 10, 10, "x"),), "INVALID_CUE_RANGE"),
        ((SubtitleCue("a", 0, 101, "x"),), "CUE_OUT_OF_BOUNDS"),
        ((SubtitleCue("a", 0, 60, "x"), SubtitleCue("b", 50, 90, "y")), "CUE_OVERLAP"),
        ((SubtitleCue("a", 50, 60, "x"), SubtitleCue("b", 0, 40, "y")), "CUE_NOT_ORDERED"),
        ((SubtitleCue("a", 0, 10, "x"), SubtitleCue("a", 10, 20, "y")), "DUPLICATE_CUE_ID"),
        ((SubtitleCue("a", 0, 10, "  "),), "EMPTY_CUE_TEXT"),
    ],
)
def test_timeline_rejects_invalid_cues(cues: tuple[SubtitleCue, ...], code: str) -> None:
    with pytest.raises(CueValidationError) as caught:
        SubtitleTimeline.create(duration_ms=100, cues=cues)

    assert caught.value.code == code
