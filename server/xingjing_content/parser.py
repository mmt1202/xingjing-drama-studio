from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .models import (
    CharacterInsight,
    ContentAnalysis,
    SceneHeading,
    ScriptParagraph,
    ScriptScene,
    ScriptVersion,
    SourceMapping,
    ValidationIssue,
)

_HEADING = re.compile(r"^(?:#{1,6}\s*)?场景[：:]\s*(.+?)\s*$")
_SPEECH = re.compile(r"^([^：:\n]{1,40})[：:]\s*(.*)$")
_SENSITIVE_TERMS = ("暴力", "自杀", "毒品", "色情", "赌博")


@dataclass(slots=True)
class _SceneBuilder:
    heading: SceneHeading
    paragraphs: list[ScriptParagraph]


def parse_script(content: str, *, version_number: int, change_summary: str) -> ScriptVersion:
    scenes: list[_SceneBuilder] = []
    mappings: list[SourceMapping] = []
    offset = 0
    for raw_line in content.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        start = offset + (len(line) - len(line.lstrip()))
        offset += len(raw_line)
        if not stripped:
            continue
        heading_match = _HEADING.match(stripped)
        if heading_match:
            parts = [part.strip() for part in heading_match.group(1).split("｜")]
            scenes.append(
                _SceneBuilder(
                    heading=SceneHeading(
                        location=parts[0],
                        time_of_day=parts[1] if len(parts) > 1 else None,
                        setting=parts[2] if len(parts) > 2 else None,
                    ),
                    paragraphs=[],
                )
            )
            continue
        if not scenes:
            continue
        speech_match = _SPEECH.match(stripped)
        speaker: str | None = None
        text = stripped
        kind: Literal["action", "dialogue", "narration"] = "action"
        if speech_match:
            label, text = speech_match.groups()
            if label == "旁白":
                kind = "narration"
            else:
                kind = "dialogue"
                speaker = label
        scene_number = len(scenes)
        paragraph_number = len(scenes[-1].paragraphs) + 1
        paragraph_id = f"S{scene_number:03d}P{paragraph_number:03d}"
        paragraph = ScriptParagraph(
            paragraph_id=paragraph_id,
            kind=kind,
            text=text,
            source_text=stripped,
            speaker=speaker,
        )
        scenes[-1].paragraphs.append(paragraph)
        mappings.append(SourceMapping(target_id=paragraph_id, source_start=start, source_end=start + len(stripped)))

    issues: list[ValidationIssue] = []
    if not scenes:
        issues.append(ValidationIssue("SCENE_HEADING_REQUIRED", "scenes", "至少需要一个场景标题"))
    for index, scene in enumerate(scenes):
        if not scene.paragraphs:
            issues.append(ValidationIssue("SCENE_CONTENT_REQUIRED", f"scenes.{index}.paragraphs", "场景内容不能为空"))
    character_counts: dict[str, int] = {}
    for scene in scenes:
        for paragraph in scene.paragraphs:
            if paragraph.speaker:
                character_counts[paragraph.speaker] = character_counts.get(paragraph.speaker, 0) + 1
    dialogue_count = sum(
        paragraph.kind == "dialogue"
        for scene in scenes
        for paragraph in scene.paragraphs
    )
    estimated_duration_ms = sum(
        max(1_000, min(15_000, len(paragraph.text) * (180 if paragraph.kind == "dialogue" else 120)))
        for scene in scenes
        for paragraph in scene.paragraphs
    )
    compact_length = len(re.sub(r"\s+", "", content))
    analysis = ContentAnalysis(
        word_count=len(content.split()),
        character_count=compact_length,
        scene_count=len(scenes),
        dialogue_count=dialogue_count,
        estimated_duration_ms=estimated_duration_ms,
        estimated_episode_count=max(1, (estimated_duration_ms + 89_999) // 90_000),
        sensitive_terms=tuple(term for term in _SENSITIVE_TERMS if term in content),
        characters=tuple(
            CharacterInsight(name=name, appearances=count)
            for name, count in sorted(character_counts.items())
        ),
        locations=tuple(dict.fromkeys(scene.heading.location for scene in scenes)),
    )
    return ScriptVersion(
        number=version_number,
        source_content=content,
        scenes=tuple(
            ScriptScene(scene_id=f"S{index:03d}", heading=item.heading, paragraphs=tuple(item.paragraphs))
            for index, item in enumerate(scenes, start=1)
        ),
        source_mappings=tuple(mappings),
        validation_errors=tuple(issues),
        change_summary=change_summary,
        analysis=analysis,
    )
