"""Stable chapter coordinates and complete coverage for normalized source text."""

import re
from bisect import bisect_left
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5


class SourceStructureError(ValueError):
    pass


@dataclass(frozen=True)
class SourceChapter:
    id: str
    ordinal: int
    title: str | None
    kind: str
    start: int
    end: int


_CHAPTER = re.compile(
    r"^(?:第[零〇一二三四五六七八九十百千万两OＯ\d]{1,16}[章节回集话幕卷]"
    r"|(?:chapter|episode|part|act|book)\s+(?:\d+|[ivxlcdm]+)\b"
    r"|(?:chương|tập|phần|hồi)\s+\d+\b)",
    re.IGNORECASE,
)


def heading_title(line: str, *, book_titles: tuple[str, ...] = ()) -> str | None:
    stripped = line.strip()
    title = re.sub(r"^#{1,6}\s+", "", stripped).strip()
    if not title or len(title) > 100 or re.search(r"[。！？!?]", title):
        return None
    chapter_title = re.sub(r"^(?:正文|文)\s*[:：]?\s*(?=第)", "", title)
    for name in book_titles:
        if name and chapter_title.startswith(name):
            candidate = chapter_title[len(name) :].lstrip(" ·:：-")
            if _CHAPTER.match(candidate):
                return candidate
    return chapter_title if title != stripped or _CHAPTER.match(chapter_title) else None


def chapters(text: str, snapshot_id: str, *, book_titles: tuple[str, ...] = ()) -> tuple[SourceChapter, ...]:
    headings = []
    offset = 0
    for line in text.splitlines(keepends=True):
        title = heading_title(line, book_titles=book_titles)
        if title:
            headings.append((offset, title))
        offset += len(line)
    if len(headings) > 4096:
        raise SourceStructureError("SOURCE_PLAN_CHAPTER_LIMIT")
    entries = [(start, title, "heading") for start, title in headings]
    if not entries or entries[0][0] > 0:
        entries.insert(0, (0, None, "preamble" if headings else "body"))
    return tuple(
        SourceChapter(
            id=str(uuid5(NAMESPACE_URL, f"source-chapter:{snapshot_id}:{start}:{end}")),
            ordinal=index + 1,
            title=title,
            kind=kind,
            start=start,
            end=end,
        )
        for index, (start, title, kind) in enumerate(entries)
        if (end := entries[index + 1][0] if index + 1 < len(entries) else len(text)) > start
    )


def merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def uncovered_ranges(
    text: str, covered: list[tuple[int, int]], selected: list[tuple[int, int]] | None = None
) -> list[tuple[int, int]]:
    intervals = merge_ranges(covered)
    if any(
        type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text) for start, end in intervals
    ):
        raise SourceStructureError("SOURCE_PLAN_SOURCE_CHANGED")
    available = []
    for begin, finish in merge_ranges(selected if selected is not None else [(0, len(text))]):
        if not 0 <= begin < finish <= len(text):
            raise SourceStructureError("SOURCE_PLAN_RANGE_INVALID")
        cursor = begin
        for start, end in intervals:
            if end <= cursor:
                continue
            if start >= finish:
                break
            if start > cursor:
                available.append((cursor, min(start, finish)))
            cursor = max(cursor, end)
            if cursor >= finish:
                break
        if cursor < finish:
            available.append((cursor, finish))
    return [(start, end) for start, end in available if text[start:end].strip()]


def _within_limit(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    parts = (end - start + limit - 1) // limit
    if parts <= 1:
        return [(start, end)]
    # Prefer sentence or paragraph boundaries while leaving enough room for every remaining part.
    stops = [start + match.end() for match in re.finditer(r"\n+|[。！？.!?][”’」』\"']*\s*", text[start:end])]
    spans = []
    for remaining in range(parts - 1, 0, -1):
        low, high = max(start + 1, end - remaining * limit), min(start + limit, end - remaining)
        target = start + (end - start) // (remaining + 1)
        index = bisect_left(stops, target)
        nearby = [point for point in stops[max(0, index - 1) : index + 1] if low <= point <= high]
        cut = min(nearby, key=lambda point: abs(point - target)) if nearby else target
        spans.append((start, cut))
        start = cut
    return [*spans, (start, end)]


def split_ranges(
    text: str,
    available: list[tuple[int, int]],
    count: int,
    structure: tuple[SourceChapter, ...],
    *,
    max_characters: int | None = None,
) -> list[tuple[int, int]]:
    """Partition every selected uncovered span without crossing already allocated content."""
    if type(count) is not int or not 1 <= count <= 200 or not available:
        raise SourceStructureError("SOURCE_PLAN_COUNT_INVALID")
    if count < len(available):
        raise SourceStructureError("SOURCE_PLAN_COUNT_TOO_SMALL")
    chapter_ranges = []
    for start, end in available:
        selected_chapters = [
            chapter
            for chapter in structure
            if chapter.kind == "heading" and chapter.start < end and chapter.end > start
        ]
        # Keep an opening title or preface with its first chapter, including partially allocated sources.
        boundaries = [start, *(chapter.start for chapter in selected_chapters[1:]), end]
        chapter_ranges.extend(zip(boundaries, boundaries[1:]))
    if (
        max_characters
        and sum((end - start + max_characters - 1) // max_characters for start, end in chapter_ranges) == count
    ):
        bounded = [span for start, end in chapter_ranges for span in _within_limit(text, start, end, max_characters)]
        if all(text[start:end].strip() for start, end in bounded):
            return bounded
    if len(chapter_ranges) == count:
        return chapter_ranges
    headings = {chapter.start for chapter in structure if chapter.kind == "heading"}
    choices = []
    for start, end in available:
        lines = [
            (start + match.start(), match.group())
            for match in re.finditer(r"[^\n]+(?:\n+|$)", text[start:end])
            if match.group().strip()
        ]
        # A chapter heading stays with the following paragraph.
        boundaries = (
            [start]
            + [position for index, (position, _) in enumerate(lines[1:], 1) if not heading_title(lines[index - 1][1])]
            + [end]
        )
        choices.append(boundaries)
    allocation = [1] * len(available)
    for _ in range(count - len(available)):
        eligible = [index for index, points in enumerate(choices) if allocation[index] < len(points) - 1]
        if not eligible:
            raise SourceStructureError("SOURCE_PLAN_NOT_ENOUGH_PARAGRAPHS")
        best = max(eligible, key=lambda index: (available[index][1] - available[index][0]) / allocation[index])
        allocation[best] += 1
    result = []
    for points, parts in zip(choices, allocation, strict=True):
        selected = [points[0]]
        previous_index = 0
        for part in range(1, parts):
            target = points[0] + (points[-1] - points[0]) * part / parts
            eligible = range(previous_index + 1, len(points) - (parts - part))
            index = min(
                eligible, key=lambda item: abs(points[item] - target) * (0.7 if points[item] in headings else 1)
            )
            selected.append(points[index])
            previous_index = index
        selected.append(points[-1])
        result.extend(zip(selected, selected[1:]))
    return result
