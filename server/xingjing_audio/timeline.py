from __future__ import annotations

from dataclasses import dataclass

from .errors import BoundaryViolation, CueValidationError


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    cue_id: str
    start_ms: int
    end_ms: int
    text: str
    source_line_id: str | None = None


@dataclass(frozen=True, slots=True)
class SubtitleTimeline:
    duration_ms: int
    cues: tuple[SubtitleCue, ...]

    @classmethod
    def create(cls, duration_ms: int, cues: tuple[SubtitleCue, ...]) -> SubtitleTimeline:
        if type(duration_ms) is not int or duration_ms <= 0:
            raise CueValidationError("INVALID_TIMELINE_DURATION")

        seen: set[str] = set()
        previous: SubtitleCue | None = None
        for cue in cues:
            if not cue.cue_id or cue.cue_id in seen:
                raise CueValidationError("DUPLICATE_CUE_ID")
            seen.add(cue.cue_id)
            if type(cue.start_ms) is not int or type(cue.end_ms) is not int:
                raise CueValidationError("INVALID_TIMECODE_PRECISION")
            if cue.start_ms < 0 or cue.end_ms < 0:
                raise CueValidationError("NEGATIVE_TIMECODE")
            if cue.end_ms <= cue.start_ms:
                raise CueValidationError("INVALID_CUE_RANGE")
            if cue.end_ms > duration_ms:
                raise CueValidationError("CUE_OUT_OF_BOUNDS")
            if not cue.text.strip():
                raise CueValidationError("EMPTY_CUE_TEXT")
            if previous is not None:
                if cue.start_ms < previous.start_ms:
                    raise CueValidationError("CUE_NOT_ORDERED")
                if cue.start_ms < previous.end_ms:
                    raise CueValidationError("CUE_OVERLAP")
            previous = cue
        return cls(duration_ms=duration_ms, cues=cues)


@dataclass(frozen=True, slots=True)
class RegenerationSelection:
    start_ms: int
    end_ms: int
    cue_ids: tuple[str, ...]

    @classmethod
    def from_cues(cls, timeline: SubtitleTimeline, cue_ids: tuple[str, ...]) -> RegenerationSelection:
        if not cue_ids or len(set(cue_ids)) != len(cue_ids):
            raise BoundaryViolation("selection must contain unique cues")
        positions = {cue.cue_id: index for index, cue in enumerate(timeline.cues)}
        try:
            indices = tuple(positions[cue_id] for cue_id in cue_ids)
        except KeyError as exc:
            raise BoundaryViolation("selection contains an unknown cue") from exc
        expected = tuple(range(indices[0], indices[0] + len(indices)))
        if indices != expected:
            raise BoundaryViolation("selection must follow timeline order without gaps")
        selected = timeline.cues[indices[0] : indices[-1] + 1]
        return cls(start_ms=selected[0].start_ms, end_ms=selected[-1].end_ms, cue_ids=cue_ids)
