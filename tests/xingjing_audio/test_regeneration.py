from __future__ import annotations

import pytest

from server.xingjing_audio import BoundaryViolation, RegenerationSelection, SubtitleCue, SubtitleTimeline


def test_regeneration_selection_is_exact_contiguous_cue_range() -> None:
    timeline = SubtitleTimeline.create(
        300,
        (
            SubtitleCue("a", 0, 100, "甲"),
            SubtitleCue("b", 100, 200, "乙"),
            SubtitleCue("c", 200, 300, "丙"),
        ),
    )

    selection = RegenerationSelection.from_cues(timeline, ("b", "c"))

    assert (selection.start_ms, selection.end_ms, selection.cue_ids) == (100, 300, ("b", "c"))


@pytest.mark.parametrize("cue_ids", [("a", "c"), ("missing",), (), ("b", "b")])
def test_regeneration_rejects_non_contiguous_or_unknown_selection(cue_ids: tuple[str, ...]) -> None:
    timeline = SubtitleTimeline.create(
        300,
        (
            SubtitleCue("a", 0, 100, "甲"),
            SubtitleCue("b", 100, 200, "乙"),
            SubtitleCue("c", 200, 300, "丙"),
        ),
    )

    with pytest.raises(BoundaryViolation):
        RegenerationSelection.from_cues(timeline, cue_ids)
