from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, cast


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: Literal["error", "warning"] = "error"


@dataclass(frozen=True, slots=True)
class SourceDocument:
    filename: str
    media_type: str
    content: str


@dataclass(frozen=True, slots=True)
class SceneHeading:
    location: str
    time_of_day: str | None = None
    setting: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptParagraph:
    paragraph_id: str
    kind: Literal["action", "dialogue", "narration"]
    text: str
    source_text: str
    speaker: str | None = None


@dataclass(frozen=True, slots=True)
class ScriptScene:
    scene_id: str
    heading: SceneHeading
    paragraphs: tuple[ScriptParagraph, ...]


@dataclass(frozen=True, slots=True)
class SourceMapping:
    target_id: str
    source_start: int
    source_end: int


@dataclass(frozen=True, slots=True)
class CharacterInsight:
    name: str
    appearances: int
    description: str = ""


@dataclass(frozen=True, slots=True)
class RelationshipInsight:
    source: str
    target: str
    relation: str


@dataclass(frozen=True, slots=True)
class StoryBeat:
    label: str
    summary: str
    scene_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContentAnalysis:
    word_count: int = 0
    character_count: int = 0
    scene_count: int = 0
    dialogue_count: int = 0
    estimated_duration_ms: int = 0
    estimated_episode_count: int = 1
    sensitive_terms: tuple[str, ...] = ()
    characters: tuple[CharacterInsight, ...] = ()
    locations: tuple[str, ...] = ()
    props: tuple[str, ...] = ()
    relationships: tuple[RelationshipInsight, ...] = ()
    story_beats: tuple[StoryBeat, ...] = ()
    episode_suggestions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptVersion:
    number: int
    source_content: str
    scenes: tuple[ScriptScene, ...]
    source_mappings: tuple[SourceMapping, ...]
    validation_errors: tuple[ValidationIssue, ...]
    change_summary: str
    analysis: ContentAnalysis = field(default_factory=ContentAnalysis)


@dataclass(frozen=True, slots=True)
class DirectorProfile:
    audience: str | None = None
    pacing: str | None = None
    visual_style: str | None = None
    camera_language: str | None = None
    production_constraints: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ScriptDocument:
    script_id: str
    workspace_id: str
    project_id: str
    title: str
    revision: int
    source_document: SourceDocument
    versions: tuple[ScriptVersion, ...]
    locked_version_number: int | None = None
    director_profile: DirectorProfile = field(default_factory=DirectorProfile)

    @property
    def current_version(self) -> ScriptVersion:
        return self.versions[-1]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def downstream_snapshot(self) -> dict[str, Any]:
        if self.locked_version_number is None:
            raise ValueError("SCRIPT_NOT_FROZEN")
        version = next(item for item in self.versions if item.number == self.locked_version_number)
        characters = list(
            dict.fromkeys(
                paragraph.speaker
                for scene in version.scenes
                for paragraph in scene.paragraphs
                if paragraph.speaker is not None
            )
        )
        return {
            "script_id": self.script_id,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "version_number": version.number,
            "characters": characters,
            "locations": [scene.heading.location for scene in version.scenes],
            "scenes": [asdict(scene) for scene in version.scenes],
            "source_mappings": [asdict(mapping) for mapping in version.source_mappings],
            "director_profile": asdict(self.director_profile),
        }


def script_from_dict(data: dict[str, object]) -> ScriptDocument:
    source_data = _dict(data["source_document"])
    versions: list[ScriptVersion] = []
    for raw_version in _list(data["versions"]):
        version_data = _dict(raw_version)
        scenes: list[ScriptScene] = []
        for raw_scene in _list(version_data["scenes"]):
            scene_data = _dict(raw_scene)
            scenes.append(
                ScriptScene(
                    scene_id=str(scene_data["scene_id"]),
                    heading=_heading_from_dict(_dict(scene_data["heading"])),
                    paragraphs=tuple(_paragraph_from_dict(_dict(item)) for item in _list(scene_data["paragraphs"])),
                )
            )
        versions.append(
            ScriptVersion(
                number=_integer(version_data["number"]),
                source_content=_string(version_data["source_content"]),
                scenes=tuple(scenes),
                source_mappings=tuple(
                    _mapping_from_dict(_dict(item)) for item in _list(version_data["source_mappings"])
                ),
                validation_errors=tuple(
                    _issue_from_dict(_dict(item)) for item in _list(version_data["validation_errors"])
                ),
                change_summary=_string(version_data["change_summary"]),
                analysis=_analysis_from_dict(_dict(version_data.get("analysis", {}))),
            )
        )
    profile_data = _dict(data.get("director_profile", {}))
    return ScriptDocument(
        script_id=_string(data["script_id"]),
        workspace_id=_string(data["workspace_id"]),
        project_id=_string(data["project_id"]),
        title=_string(data["title"]),
        revision=_integer(data["revision"]),
        source_document=SourceDocument(
            filename=_string(source_data["filename"]),
            media_type=_string(source_data["media_type"]),
            content=_string(source_data["content"]),
        ),
        versions=tuple(versions),
        locked_version_number=(_integer(value) if (value := data.get("locked_version_number")) is not None else None),
        director_profile=_profile_from_dict(profile_data),
    )


def _dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return value


def _string(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return value


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    return _string(value)


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return value


def _heading_from_dict(data: dict[str, object]) -> SceneHeading:
    return SceneHeading(
        location=_string(data["location"]),
        time_of_day=_optional_string(data.get("time_of_day")),
        setting=_optional_string(data.get("setting")),
    )


def _paragraph_from_dict(data: dict[str, object]) -> ScriptParagraph:
    kind = _string(data["kind"])
    if kind not in {"action", "dialogue", "narration"}:
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return ScriptParagraph(
        paragraph_id=_string(data["paragraph_id"]),
        kind=cast(Literal["action", "dialogue", "narration"], kind),
        text=_string(data["text"]),
        source_text=_string(data["source_text"]),
        speaker=_optional_string(data.get("speaker")),
    )


def _mapping_from_dict(data: dict[str, object]) -> SourceMapping:
    return SourceMapping(
        target_id=_string(data["target_id"]),
        source_start=_integer(data["source_start"]),
        source_end=_integer(data["source_end"]),
    )


def _issue_from_dict(data: dict[str, object]) -> ValidationIssue:
    severity = _string(data.get("severity", "error"))
    if severity not in {"error", "warning"}:
        raise ValueError("INVALID_SERIALIZED_CONTRACT")
    return ValidationIssue(
        code=_string(data["code"]),
        path=_string(data["path"]),
        message=_string(data["message"]),
        severity=cast(Literal["error", "warning"], severity),
    )


def _profile_from_dict(data: dict[str, object]) -> DirectorProfile:
    constraints = data.get("production_constraints", [])
    return DirectorProfile(
        audience=_optional_string(data.get("audience")),
        pacing=_optional_string(data.get("pacing")),
        visual_style=_optional_string(data.get("visual_style")),
        camera_language=_optional_string(data.get("camera_language")),
        production_constraints=tuple(_string(item) for item in _list(constraints)),
    )


def _analysis_from_dict(data: dict[str, object]) -> ContentAnalysis:
    return ContentAnalysis(
        word_count=_integer(data.get("word_count", 0)),
        character_count=_integer(data.get("character_count", 0)),
        scene_count=_integer(data.get("scene_count", 0)),
        dialogue_count=_integer(data.get("dialogue_count", 0)),
        estimated_duration_ms=_integer(data.get("estimated_duration_ms", 0)),
        estimated_episode_count=_integer(data.get("estimated_episode_count", 1)),
        sensitive_terms=tuple(_string(item) for item in _list(data.get("sensitive_terms", []))),
        characters=tuple(
            CharacterInsight(
                name=_string(item_data["name"]),
                appearances=_integer(item_data["appearances"]),
                description=_string(item_data.get("description", "")),
            )
            for item in _list(data.get("characters", []))
            for item_data in [_dict(item)]
        ),
        locations=tuple(_string(item) for item in _list(data.get("locations", []))),
        props=tuple(_string(item) for item in _list(data.get("props", []))),
        relationships=tuple(
            RelationshipInsight(
                source=_string(item_data["source"]),
                target=_string(item_data["target"]),
                relation=_string(item_data["relation"]),
            )
            for item in _list(data.get("relationships", []))
            for item_data in [_dict(item)]
        ),
        story_beats=tuple(
            StoryBeat(
                label=_string(item_data["label"]),
                summary=_string(item_data["summary"]),
                scene_ids=tuple(_string(value) for value in _list(item_data.get("scene_ids", []))),
            )
            for item in _list(data.get("story_beats", []))
            for item_data in [_dict(item)]
        ),
        episode_suggestions=tuple(
            _string(item) for item in _list(data.get("episode_suggestions", []))
        ),
    )
