"""Validated project setup and the filesystem projection used by production tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.alias_generators import to_camel

from lib.project_manager import ProjectManager


class ProjectSetup(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", strict=True)

    short_name: str = Field(default="", max_length=64)
    owner_id: str = Field(default="", max_length=128)
    project_type: Literal["drama", "comic", "brand", "adaptation"] = "drama"
    source_kind: Literal["blank", "novel", "script", "template"] = "blank"
    source_text: str = Field(default="", max_length=1_000_000)
    preset: Literal["", "suspense", "comic", "brand"] = ""
    episode_count: int = Field(default=1, ge=1, le=200)
    episode_duration_seconds: int = Field(default=60, ge=5, le=7200)
    primary_platform: Literal["douyin", "kuaishou", "wechat", "bilibili", "web"] = "douyin"
    secondary_platforms: list[Literal["douyin", "kuaishou", "wechat", "bilibili", "web"]] = Field(default_factory=list)
    aspect_ratio: Literal["9:16", "16:9", "1:1"] = "9:16"
    delivery_type: Literal["final", "proposal"] = "final"
    generation_mode: Literal["storyboard", "reference_video"] = "storyboard"
    director_style: str = Field(default="", max_length=200)
    visual_style: str = Field(default="", max_length=1000)
    model_strategy: Literal["balanced", "quality", "economy"] = "balanced"
    image_model_key: str = Field(default="", max_length=200)
    video_model_key: str = Field(default="", max_length=200)
    candidate_count: int = Field(default=1, ge=1, le=12)
    subtitle_languages: list[Literal["zh", "en", "vi"]] = Field(default_factory=lambda: ["zh"])
    review_language: Literal["zh", "en", "vi"] = "zh"
    budget_minor: int | None = Field(default=None, ge=1, le=100_000_000_000)
    schedule_days: int | None = Field(default=None, ge=1, le=3650)
    team_seats: int = Field(default=1, ge=1, le=1000)
    review_seats: int = Field(default=1, ge=1, le=1000)
    internal_review_rounds: int = Field(default=2, ge=1, le=20)
    client_review_rounds: int = Field(default=1, ge=0, le=20)
    client_can_comment: bool = True
    client_can_download: bool = False
    client_can_approve: bool = True
    rights_basis: Literal["original", "licensed", "pending"] = "original"
    rights_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def consistent(self) -> ProjectSetup:
        if self.project_type == "brand" and self.episode_count != 1:
            raise ValueError("BRAND_SINGLE_EPISODE_REQUIRED")
        if not self.subtitle_languages or len(set(self.subtitle_languages)) != len(self.subtitle_languages):
            raise ValueError("SUBTITLE_LANGUAGES_INVALID")
        if len(set(self.secondary_platforms)) != len(self.secondary_platforms):
            raise ValueError("SECONDARY_PLATFORMS_INVALID")
        if self.primary_platform in self.secondary_platforms:
            raise ValueError("PRIMARY_PLATFORM_DUPLICATED")
        return self

    def payload(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)

    @property
    def content_mode(self) -> Literal["drama", "narration", "ad"]:
        return "ad" if self.project_type == "brand" else "drama"


class SetupInvalid(ValueError):
    def __init__(self, fields: list[str]) -> None:
        super().__init__("PROJECT_SETUP_INVALID")
        self.fields = fields


def parse_setup(value: object, *, actor_id: str, draft: bool) -> ProjectSetup:
    if not isinstance(value, Mapping):
        raise SetupInvalid(["configuration"])
    try:
        setup = ProjectSetup.model_validate(dict(value))
    except ValidationError as error:
        raise SetupInvalid([".".join(map(str, item["loc"])) or "configuration" for item in error.errors()]) from error
    setup.owner_id = setup.owner_id.strip() or actor_id
    if not draft:
        if setup.source_kind not in {"novel", "script"}:
            setup.source_text = ""
        missing = []
        if setup.source_kind in {"novel", "script"} and not setup.source_text.strip():
            missing.append("sourceText")
        if setup.source_kind == "template" and not setup.preset:
            missing.append("preset")
        if missing:
            raise SetupInvalid(missing)
    return setup


def creation_digest(name: str, setup: ProjectSetup | None, draft: bool) -> str:
    payload = {"name": name, "configuration": setup.payload() if setup else None, "draft": draft}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def create_projection(manager: ProjectManager, project_id: str, name: str, setup: ProjectSetup | None) -> Path:
    mode = setup.content_mode if setup else "narration"
    path = manager.create_project(project_id, content_mode=mode)
    try:
        extras: dict[str, object] = {}
        if setup:
            extras = {
                "generation_mode": setup.generation_mode,
                "xingjing_setup": {key: value for key, value in setup.payload().items() if key != "sourceText"},
                "planned_episode_count": setup.episode_count,
                "planned_episode_duration_seconds": setup.episode_duration_seconds,
            }
        manager.create_project_metadata(
            project_id,
            name,
            setup.visual_style if setup else "",
            mode,
            aspect_ratio=setup.aspect_ratio if setup else "9:16",
            extras=extras,
            source_kind="screenplay" if setup and setup.source_kind == "script" else "novel",
            target_duration=setup.episode_duration_seconds if setup and mode == "ad" else None,
            brief=setup.source_text if setup and mode == "ad" else None,
        )
        if setup and setup.source_text:
            (path / "source" / "source.txt").write_text(setup.source_text, encoding="utf-8")
        for directory in (
            "output/masters",
            "output/subtitles",
            "output/covers",
            "output/platforms",
            "output/model-records",
            "output/compliance",
        ):
            (path / directory).mkdir(parents=True, exist_ok=True)
        return path
    except Exception:
        manager.delete_project_directory(project_id)
        raise
