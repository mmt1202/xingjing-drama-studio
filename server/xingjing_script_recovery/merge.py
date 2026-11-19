"""Splice explicitly chosen historical lines, retaining their version and object ancestry."""

from __future__ import annotations

from dataclasses import dataclass, replace

from lib.episode_editor import EpisodeEditError
from server.xingjing_content.models import ScriptScene, ScriptVersion, SourceAttribution
from server.xingjing_content.parser import parse_script
from server.xingjing_content.recognition import scene_heading
from server.xingjing_source_planning.mapping import targets

from .contracts import Change

_LINE_ENDS = "\r\n\v\f\x1c\x1d\x1e\x85\u2028\u2029"


def invalid() -> EpisodeEditError:
    return EpisodeEditError("SCRIPT_RECOVERY_SELECTION_INVALID", 422)


@dataclass(frozen=True, slots=True)
class Line:
    raw: str
    version: int
    object_id: str | None

    @property
    def ending(self) -> str:
        if self.raw.endswith("\r\n"):
            return "\r\n"
        return self.raw[-1:] if self.raw and self.raw[-1] in _LINE_ENDS else ""


class Layout:
    def __init__(self, version: ScriptVersion):
        self.version = version
        self.lines: list[Line] = []
        self.spans: dict[str, tuple[int, int]] = {}
        self.parents: dict[str, ScriptScene] = {}
        scene_index, paragraph_index = -1, 0
        mapped_lines = {mapping.source_start for mapping in version.source_mappings}
        offset = 0
        for index, raw in enumerate(version.source_content.splitlines(keepends=True)):
            stripped = raw.strip()
            identity = None
            start = offset + len(raw) - len(raw.lstrip())
            offset += len(raw)
            # Use the stored objects to distinguish historical action lines and preambles from headings.
            next_index = scene_index + 1
            heading = scene_heading(stripped) if start not in mapped_lines else None
            if (
                heading
                and next_index < len(version.scenes)
                and (
                    heading[0] == version.scenes[next_index].heading.location
                    or stripped.lstrip("#").lstrip().startswith(("场景：", "场景:"))
                )
            ):
                scene_index += 1
                paragraph_index = 0
                scene = version.scenes[scene_index]
                identity = scene.scene_id
                self.spans[identity] = (index, index + 1)
                self.parents[identity] = scene
            elif stripped and scene_index >= 0:
                scene = version.scenes[scene_index]
                if paragraph_index >= len(scene.paragraphs):
                    raise EpisodeEditError("SCRIPT_RECOVERY_STRUCTURE_CHANGED", 409)
                paragraph = scene.paragraphs[paragraph_index]
                if paragraph.source_text != stripped:
                    raise EpisodeEditError("SCRIPT_RECOVERY_STRUCTURE_CHANGED", 409)
                paragraph_index += 1
                identity = paragraph.paragraph_id
                self.parents[identity] = scene
                self.spans[identity] = (index, index + 1)
                self.spans[scene.scene_id] = (self.spans[scene.scene_id][0], index + 1)
            self.lines.append(Line(raw, version.number, identity))
        if len(self.spans) != sum(1 + len(scene.paragraphs) for scene in version.scenes):
            raise EpisodeEditError("SCRIPT_RECOVERY_STRUCTURE_CHANGED", 409)

    def span(self, identity: str | None, part: str) -> tuple[int, int]:
        if identity is None or identity not in self.spans:
            raise invalid()
        if (part == "paragraph") != (identity != self.parents[identity].scene_id):
            raise invalid()
        start, end = self.spans[identity]
        return start, start + 1 if part == "heading" else end

    def insertion(self, target: str, part: str) -> tuple[int, int | None]:
        if part == "scene" and target == "@start":
            scenes = self.version.scenes
            return (self.spans[scenes[0].scene_id][0] if scenes else len(self.lines)), None
        if target not in self.parents:
            raise invalid()
        is_scene = self.parents[target].scene_id == target
        if part == "heading" or (part == "scene" and not is_scene):
            raise invalid()
        start, end = self.spans[target]
        return (start + 1 if part == "paragraph" and is_scene else end), start


@dataclass
class Edit:
    change: Change
    start: int
    end: int
    anchor: int | None
    replacement: list[Line]


def edits_for(current: Layout, history: Layout, changes: list[Change], limit: int) -> list[Edit]:
    result: list[Edit] = []
    seen = set()
    inserted = 0
    for change in changes:
        signature = (change.action, change.part, change.source_id, change.target_id)
        if signature in seen or (change.part == "heading" and change.action != "replace"):
            raise invalid()
        seen.add(signature)
        replacement: list[Line] = []
        if change.action != "remove":
            left, right = history.span(change.source_id, change.part)
            replacement = history.lines[left:right]
        elif change.source_id is not None:
            raise invalid()
        anchor = None
        if change.action == "insert":
            start, anchor = current.insertion(change.target_id, change.part)
            end = start
        else:
            start, end = current.span(change.target_id, change.part)
            if replacement:
                last = replacement[-1]
                replacement[-1] = replace(last, raw=last.raw.rstrip(_LINE_ENDS) + current.lines[end - 1].ending)
        for other in result:
            if (
                max(other.start, start) < min(other.end, end)
                or (anchor is not None and other.start <= anchor < other.end)
                or (other.anchor is not None and start <= other.anchor < end)
            ):
                raise EpisodeEditError("SCRIPT_RECOVERY_OVERLAP", 422)
        inserted += sum(len(line.raw) for line in replacement)
        if inserted > limit:
            raise EpisodeEditError("SCRIPT_RECOVERY_TOO_LARGE", 413)
        result.append(Edit(change, start, end, anchor, replacement))
    return result


def render(current: Layout, edits: list[Edit]) -> tuple[str, list[Line]]:
    # A paragraph after the final line belongs to the old scene, before any scene inserted at that same boundary.
    ordered = sorted(edits, key=lambda e: (e.start, e.end != e.start, e.change.part == "scene"))
    cursor = 0
    lines: list[Line] = []
    for edit in ordered:
        lines.extend(current.lines[cursor : edit.start])
        lines.extend(edit.replacement)
        cursor = edit.end
    lines.extend(current.lines[cursor:])
    newline = next((line.ending for line in current.lines if line.ending), "\n")
    pieces = [line.raw if line.ending or i + 1 == len(lines) else line.raw + newline for i, line in enumerate(lines)]
    return "".join(pieces), lines


def transfer_mappings(candidate: Layout, lines: list[Line], current: Layout, history: Layout):
    layouts = {item.version.number: item for item in (current, history)}
    evidence = {
        (item.version.number, attribution.target_id): attribution
        for item in layouts.values()
        for attribution in item.version.source_attributions
    }
    if not evidence:
        return (), {"kept": 0, "restored": 0, "superseded": 0}
    digests = {number: targets(layout.version) for number, layout in layouts.items()}
    new_targets = targets(candidate.version)
    ancestry = {
        row.object_id: (line.version, line.object_id)
        for row, line in zip(candidate.lines, lines, strict=True)
        if row.object_id and line.object_id
    }
    result: list[SourceAttribution] = []
    kept = restored = 0
    for target_id, origin in ancestry.items():
        version_number, old_id = origin
        attribution = evidence.get(origin)
        if not attribution or attribution.target_digest != digests[version_number][old_id]["digest"]:
            continue
        parent = candidate.parents[target_id]
        old_parent = layouts[version_number].parents[old_id]
        if parent.heading != old_parent.heading:
            continue
        if target_id == parent.scene_id:
            if version_number == current.version.number:
                same_scene = [replace(p, paragraph_id="") for p in old_parent.paragraphs] == [
                    replace(p, paragraph_id="") for p in parent.paragraphs
                ]
            else:
                old_children = [(version_number, p.paragraph_id) for p in old_parent.paragraphs]
                same_scene = old_children == [ancestry.get(p.paragraph_id) for p in parent.paragraphs]
            if not same_scene:
                continue
        result.append(replace(attribution, target_id=target_id, target_digest=new_targets[target_id]["digest"]))
        if version_number == current.version.number:
            kept += 1
        else:
            restored += 1
    return tuple(result), {
        "kept": kept,
        "restored": restored,
        "superseded": len(current.version.source_attributions) - kept,
    }


def merge(current: ScriptVersion, source: ScriptVersion, changes: list[Change], number: int, summary: str):
    current_layout, source_layout = Layout(current), Layout(source)
    limit = max(2_000_000, len(current.source_content), len(source.source_content))
    edits = edits_for(current_layout, source_layout, changes, limit)
    content, lines = render(current_layout, edits)
    if len(content) > limit:
        raise EpisodeEditError("SCRIPT_RECOVERY_TOO_LARGE", 413)
    if not content.strip():
        raise invalid()
    version = (
        replace(current, number=number, change_summary=summary)
        if content == current.source_content
        else parse_script(content, version_number=number, change_summary=summary)
    )
    mappings, counts = transfer_mappings(Layout(version), lines, current_layout, source_layout)
    if content == current.source_content and mappings == current.source_attributions:
        raise EpisodeEditError("SCRIPT_RECOVERY_NO_DIFFERENCE", 422)
    diffs = [
        {
            **edit.change.model_dump(by_alias=True),
            "before": "".join(line.raw for line in current_layout.lines[edit.start : edit.end]),
            "after": "".join(line.raw for line in edit.replacement),
        }
        for edit in edits
    ]
    return replace(version, source_attributions=mappings), diffs, counts
