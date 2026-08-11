from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from server.xingjing_editing import (
    AddTrackCommand,
    CreateTimelineCommand,
    OutputPolicy,
    RenderCallback,
    RenderCallbackOutcome,
    RenderFailure,
    RenderOutput,
    RenderProfile,
    ReplaceClipCommand,
    RequestRenderCommand,
    TimelineTrackInput,
    UpdateClipCommand,
    UpdateOutputPolicyCommand,
)
from server.xingjing_editing.rendering import FinalVideoVersion


class CreateTimelineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: str = Field(min_length=1, max_length=128)
    tracks: tuple[TimelineTrackInput, ...]

    def to_command(self, *, project_id: str) -> CreateTimelineCommand:
        return CreateTimelineCommand(project_id=project_id, episode_id=self.episode_id, tracks=self.tracks)


class ReplaceClipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(min_length=1, max_length=128)
    clip_id: str = Field(min_length=1, max_length=128)
    source_version_id: str = Field(min_length=1, max_length=128)
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    expected_revision: int = Field(ge=1)

    def to_command(self, *, project_id: str, timeline_id: str) -> ReplaceClipCommand:
        return ReplaceClipCommand(project_id=project_id, timeline_id=timeline_id, **self.model_dump())


class UpdateClipBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    track_id: str = Field(min_length=1, max_length=128)
    clip_id: str = Field(min_length=1, max_length=128)
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    volume_milli: int = Field(ge=0, le=2_000)
    effects: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    expected_revision: int = Field(ge=1)

    def to_command(self, *, project_id: str, timeline_id: str) -> UpdateClipCommand:
        return UpdateClipCommand(project_id=project_id, timeline_id=timeline_id, **self.model_dump())


class UpdateOutputPolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    policy: OutputPolicy

    def to_command(self, *, project_id: str, timeline_id: str) -> UpdateOutputPolicyCommand:
        return UpdateOutputPolicyCommand(project_id=project_id, timeline_id=timeline_id, **self.model_dump())


class AddTrackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    track: TimelineTrackInput

    def to_command(self, *, project_id: str, timeline_id: str) -> AddTrackCommand:
        return AddTrackCommand(project_id=project_id, timeline_id=timeline_id, **self.model_dump())


class TimelineSourceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str
    version_id: str
    duration_ms: int
    content_sha256: str
    media_kind: str


class FinalDeliverySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    final_video_version: FinalVideoVersion
    output_policy: OutputPolicy
    delivery_digest: str


class SelectFinalVideoVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


class SubmitRenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_id: str = Field(min_length=1, max_length=128)
    timeline_version_id: str = Field(min_length=1, max_length=128)
    expected_timeline_revision: int = Field(ge=1)
    profile: RenderProfile
    deadline_at: datetime
    max_attempts: int = Field(ge=1, le=100)

    def to_command(self, *, project_id: str) -> RequestRenderCommand:
        return RequestRenderCommand(
            project_id=project_id,
            timeline_id=self.timeline_id,
            timeline_version_id=self.timeline_version_id,
            expected_timeline_revision=self.expected_timeline_revision,
            profile=self.profile,
            deadline_at=self.deadline_at,
            max_attempts=self.max_attempts,
        )


class CancelRenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_task_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=1_000)


class ProviderRenderCallbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_task_revision: int = Field(ge=1)
    event_id: str = Field(min_length=1, max_length=128)
    renderer_job_id: str = Field(min_length=1, max_length=128)
    attempt: int = Field(ge=1)
    outcome: RenderCallbackOutcome
    occurred_at: datetime
    output: RenderOutput | None = None
    failure: RenderFailure | None = None

    def to_callback(self) -> RenderCallback:
        return RenderCallback(
            event_id=self.event_id,
            renderer_job_id=self.renderer_job_id,
            attempt=self.attempt,
            outcome=self.outcome,
            occurred_at=self.occurred_at,
            output=self.output,
            failure=self.failure,
        )


class RetryRenderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_task_revision: int = Field(ge=1)


__all__ = [
    "CancelRenderBody",
    "AddTrackBody",
    "CreateTimelineBody",
    "FinalDeliverySnapshot",
    "ProviderRenderCallbackBody",
    "RetryRenderBody",
    "SelectFinalVideoVersionBody",
    "ReplaceClipBody",
    "SubmitRenderBody",
    "TimelineSourceSummary",
    "UpdateClipBody",
    "UpdateOutputPolicyBody",
]
