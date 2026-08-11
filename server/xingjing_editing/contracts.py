from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
IdempotencyKey = Annotated[str, StringConstraints(min_length=1, max_length=128)]
_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


def canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def validate_idempotency_key(value: str) -> str:
    if not _IDEMPOTENCY_PATTERN.fullmatch(value):
        raise ValueError("幂等键只能包含 ASCII 字母、数字及 ._:/-，长度为 1 到 128")
    return value


class Permission(StrEnum):
    FINAL_VIEW = "final.view"
    FINAL_MANAGE = "final.manage"


class TrackKind(StrEnum):
    VIDEO = "video"
    VOICE = "voice"
    SUBTITLE = "subtitle"
    BGM = "bgm"
    SFX = "sfx"
    WATERMARK = "watermark"
    OPENING = "opening"
    ENDING = "ending"
    REVIEW_MARKER = "review_marker"


class AuditOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    FAILED = "failed"


class AccessContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Identifier
    workspace_id: Identifier
    actor_id: Identifier
    request_id: Identifier
    permissions: frozenset[Permission]


class SourceMediaVersion(BaseModel):
    """由上游媒体目录返回的不可变版本描述，不接受可变资产 ID 作为渲染输入。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    asset_id: Identifier
    version_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    owner_project_id: Identifier
    allowed_project_ids: tuple[Identifier, ...] = ()
    content_sha256: Sha256
    duration_ms: Annotated[int, Field(gt=0)]
    object_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_024)]
    media_kind: TrackKind = TrackKind.VIDEO


class ClipInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clip_id: Identifier
    shot_id: Identifier | None = None
    source_version_id: Identifier
    source_in_ms: Annotated[int, Field(ge=0)]
    source_out_ms: Annotated[int, Field(gt=0)]
    timeline_start_ms: Annotated[int, Field(ge=0)]
    timeline_end_ms: Annotated[int, Field(gt=0)]
    volume_milli: Annotated[int, Field(ge=0, le=2_000)] = 1_000
    effects: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_ranges(self) -> ClipInput:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("source_out_ms 必须晚于 source_in_ms")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("timeline_end_ms 必须晚于 timeline_start_ms")
        return self


class TimelineTrackInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    track_id: Identifier
    kind: TrackKind
    clips: tuple[ClipInput, ...]

    @model_validator(mode="after")
    def validate_clips(self) -> TimelineTrackInput:
        clip_ids = [clip.clip_id for clip in self.clips]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("同一轨道内 clip_id 不得重复")
        positions = [(clip.timeline_start_ms, clip.timeline_end_ms, clip.clip_id) for clip in self.clips]
        if positions != sorted(positions):
            raise ValueError("轨道片段必须按时间线起止位置稳定排序")
        return self


class CreateTimelineCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    episode_id: Identifier
    tracks: tuple[TimelineTrackInput, ...]

    @model_validator(mode="after")
    def validate_tracks(self) -> CreateTimelineCommand:
        if not self.tracks:
            raise ValueError("时间线至少需要一条轨道")
        track_ids = [track.track_id for track in self.tracks]
        if len(track_ids) != len(set(track_ids)):
            raise ValueError("track_id 不得重复")
        clip_ids = [clip.clip_id for track in self.tracks for clip in track.clips]
        if len(clip_ids) != len(set(clip_ids)):
            raise ValueError("clip_id 在时间线内必须唯一")
        return self


class ReplaceClipCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    timeline_id: Identifier
    track_id: Identifier
    clip_id: Identifier
    source_version_id: Identifier
    source_in_ms: Annotated[int, Field(ge=0)]
    source_out_ms: Annotated[int, Field(gt=0)]
    expected_revision: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_source_range(self) -> ReplaceClipCommand:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("source_out_ms 必须晚于 source_in_ms")
        return self


class UpdateClipCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    timeline_id: Identifier
    track_id: Identifier
    clip_id: Identifier
    source_in_ms: Annotated[int, Field(ge=0)]
    source_out_ms: Annotated[int, Field(gt=0)]
    timeline_start_ms: Annotated[int, Field(ge=0)]
    timeline_end_ms: Annotated[int, Field(gt=0)]
    volume_milli: Annotated[int, Field(ge=0, le=2_000)] = 1_000
    effects: dict[str, JsonValue] = Field(default_factory=dict)
    expected_revision: Annotated[int, Field(ge=1)]

    @model_validator(mode="after")
    def validate_ranges(self) -> UpdateClipCommand:
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError("source_out_ms 必须晚于 source_in_ms")
        if self.timeline_end_ms <= self.timeline_start_ms:
            raise ValueError("timeline_end_ms 必须晚于 timeline_start_ms")
        return self


class ClipReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    clip_id: Identifier
    shot_id: Identifier | None = None
    source_asset_id: Identifier
    source_version_id: Identifier
    source_sha256: Sha256
    source_object_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1_024)] | None = None
    source_in_ms: Annotated[int, Field(ge=0)]
    source_out_ms: Annotated[int, Field(gt=0)]
    timeline_start_ms: Annotated[int, Field(ge=0)]
    timeline_end_ms: Annotated[int, Field(gt=0)]
    volume_milli: Annotated[int, Field(ge=0, le=2_000)]
    effects: dict[str, JsonValue] = Field(default_factory=dict)


class TimelineTrack(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    track_id: Identifier
    kind: TrackKind
    clips: tuple[ClipReference, ...]


class OutputPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    platform_watermark_enabled: bool = True
    customer_watermark_text: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = ""
    watermark_position: Literal["top_left", "top_right", "bottom_left", "bottom_right", "center"] = "bottom_right"
    watermark_opacity_milli: Annotated[int, Field(ge=0, le=1_000)] = 700
    aigc_label_enabled: bool = True
    aigc_label_style: Literal["visible", "metadata", "visible_and_metadata"] = "visible_and_metadata"
    export_template: Literal["preview", "review", "delivery", "platform"] = "preview"


class UpdateOutputPolicyCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    timeline_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    policy: OutputPolicy


class AddTrackCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    timeline_id: Identifier
    expected_revision: Annotated[int, Field(ge=1)]
    track: TimelineTrackInput


def timeline_content_sha256(
    *,
    project_id: str,
    episode_id: str,
    tracks: tuple[TimelineTrack, ...],
    output_policy: OutputPolicy | None = None,
) -> str:
    return canonical_sha256(
        {
            "project_id": project_id,
            "episode_id": episode_id,
            "tracks": [track.model_dump(mode="json") for track in tracks],
            "output_policy": (output_policy or OutputPolicy()).model_dump(mode="json"),
        }
    )


class TimelineVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    timeline_id: Identifier
    version_id: Identifier
    final_video_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    episode_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    parent_version_id: Identifier | None = None
    tracks: tuple[TimelineTrack, ...]
    output_policy: OutputPolicy = Field(default_factory=OutputPolicy)
    content_sha256: Sha256
    created_by: Identifier
    created_at: datetime

    @model_validator(mode="after")
    def validate_snapshot(self) -> TimelineVersion:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at 必须包含时区")
        expected = timeline_content_sha256(
            project_id=self.project_id,
            episode_id=self.episode_id,
            tracks=self.tracks,
            output_policy=self.output_policy,
        )
        legacy_expected = canonical_sha256(
            {
                "project_id": self.project_id,
                "episode_id": self.episode_id,
                "tracks": [track.model_dump(mode="json") for track in self.tracks],
            }
        )
        if self.content_sha256 not in {expected, legacy_expected}:
            raise ValueError("时间线内容摘要与轨道快照不一致")
        return self


class PreviewSummary(BaseModel):
    """预览与真实渲染共同消费的不可变输入摘要。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    timeline_id: Identifier
    project_id: Identifier
    episode_id: Identifier
    final_video_id: Identifier
    timeline_version_id: Identifier
    timeline_revision: Annotated[int, Field(ge=1)]
    composition_sha256: Sha256
    input_snapshot_sha256: Sha256
    duration_ms: Annotated[int, Field(ge=0)]
    track_count: Annotated[int, Field(ge=0)]
    clip_count: Annotated[int, Field(ge=0)]
    source_version_ids: tuple[Identifier, ...]
    updated_at: datetime


def build_preview_summary(timeline: TimelineVersion) -> PreviewSummary:
    clips = tuple(clip for track in timeline.tracks for clip in track.clips)
    source_version_ids = tuple(dict.fromkeys(clip.source_version_id for clip in clips))
    input_snapshot_sha256 = canonical_sha256(
        {
            "timeline_id": timeline.timeline_id,
            "timeline_version_id": timeline.version_id,
            "timeline_revision": timeline.revision,
            "composition_sha256": timeline.content_sha256,
            "tracks": [
                {
                    "track_id": track.track_id,
                    "kind": track.kind.value,
                    "clips": [
                        {
                            "clip_id": clip.clip_id,
                            "shot_id": clip.shot_id,
                            "source_asset_id": clip.source_asset_id,
                            "source_version_id": clip.source_version_id,
                            "source_sha256": clip.source_sha256,
                        }
                        for clip in track.clips
                    ],
                }
                for track in timeline.tracks
            ],
        }
    )
    return PreviewSummary(
        timeline_id=timeline.timeline_id,
        project_id=timeline.project_id,
        episode_id=timeline.episode_id,
        final_video_id=timeline.final_video_id,
        timeline_version_id=timeline.version_id,
        timeline_revision=timeline.revision,
        composition_sha256=timeline.content_sha256,
        input_snapshot_sha256=input_snapshot_sha256,
        duration_ms=max((clip.timeline_end_ms for clip in clips), default=0),
        track_count=len(timeline.tracks),
        clip_count=len(clips),
        source_version_ids=source_version_ids,
        updated_at=timeline.created_at,
    )


class AuditEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    project_id: Identifier | None = None
    actor_id: Identifier
    request_id: Identifier
    action: Identifier
    object_type: Identifier
    object_id: Identifier
    outcome: AuditOutcome
    occurred_at: datetime
    before_sha256: Sha256 | None = None
    after_sha256: Sha256 | None = None
    details: dict[str, JsonValue] = Field(default_factory=dict)
