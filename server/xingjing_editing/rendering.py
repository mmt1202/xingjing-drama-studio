from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .contracts import IdempotencyKey, Identifier, PreviewSummary, Sha256, canonical_sha256, validate_idempotency_key
from .errors import InvalidRenderTransition


class RenderStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


class RenderBillingStatus(StrEnum):
    ACTIVE = "active"
    SETTLED = "settled"
    RELEASED = "released"


class RenderProfile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    container: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,31}$")]
    video_codec: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,31}$")]
    audio_codec: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9._-]{0,31}$")]
    width: Annotated[int, Field(gt=0, le=16_384)]
    height: Annotated[int, Field(gt=0, le=16_384)]
    frame_rate_milli: Annotated[int, Field(gt=0, le=240_000)]


class RequestRenderCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    project_id: Identifier
    timeline_id: Identifier
    timeline_version_id: Identifier
    expected_timeline_revision: Annotated[int, Field(ge=1)]
    profile: RenderProfile
    deadline_at: datetime
    max_attempts: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def validate_deadline(self) -> RequestRenderCommand:
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at 必须包含时区")
        return self


class RenderFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")]
    message: Annotated[str, StringConstraints(min_length=1, max_length=4_000)]
    retryable: bool


class RenderCallbackOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RenderCallbackDisposition(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    IGNORED_LATE = "ignored_late"
    IGNORED_STALE_ATTEMPT = "ignored_stale_attempt"
    REJECTED_JOB_MISMATCH = "rejected_job_mismatch"


class RenderOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: Identifier
    content_sha256: Sha256
    rendered_input_snapshot_sha256: Sha256
    rendered_composition_sha256: Sha256


class RenderCallback(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: Identifier
    renderer_job_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    outcome: RenderCallbackOutcome
    occurred_at: datetime
    output: RenderOutput | None = None
    failure: RenderFailure | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> RenderCallback:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at 必须包含时区")
        if self.outcome is RenderCallbackOutcome.SUCCEEDED:
            if self.output is None or self.failure is not None:
                raise ValueError("成功回调必须包含输出且不能包含失败信息")
        elif self.outcome is RenderCallbackOutcome.FAILED:
            if self.failure is None or self.output is not None:
                raise ValueError("失败回调必须包含失败信息且不能包含输出")
        elif self.output is not None or self.failure is not None:
            raise ValueError("取消回调不能包含输出或失败信息")
        return self


class StoredRenderObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: Identifier
    content_sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(ge=0)]


class OutputSummary(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    object_key: Identifier
    content_sha256: Sha256
    size_bytes: Annotated[int, Field(ge=0)]
    duration_ms: Annotated[int, Field(ge=0)]
    input_snapshot_sha256: Sha256
    composition_sha256: Sha256


class FinalVideoVersion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version_id: Identifier
    final_video_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    timeline_id: Identifier
    timeline_version_id: Identifier
    render_task_id: Identifier
    render_attempt: Annotated[int, Field(ge=1)]
    profile: RenderProfile
    preview: PreviewSummary
    output: OutputSummary
    deduplication_key: Sha256
    created_at: datetime

    @model_validator(mode="after")
    def validate_consistency(self) -> FinalVideoVersion:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at 必须包含时区")
        if self.output.input_snapshot_sha256 != self.preview.input_snapshot_sha256:
            raise ValueError("输出摘要与预览输入摘要不一致")
        if self.output.composition_sha256 != self.preview.composition_sha256:
            raise ValueError("输出摘要与预览构图摘要不一致")
        return self


class FinalVideoSelection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    final_video_id: Identifier
    selected_version_id: Identifier
    revision: Annotated[int, Field(ge=1)]
    selected_by: Identifier
    selected_at: datetime

    @model_validator(mode="after")
    def validate_selected_at(self) -> FinalVideoSelection:
        if self.selected_at.tzinfo is None or self.selected_at.utcoffset() is None:
            raise ValueError("selected_at 必须包含时区")
        return self


class RenderCompletion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: RenderTask
    version: FinalVideoVersion
    version_created: bool


class RenderCallbackResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: RenderTask
    disposition: RenderCallbackDisposition
    version: FinalVideoVersion | None = None


class RenderTask(BaseModel):
    """持久化渲染任务快照；成功状态只能由已验证的渲染回调产生。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: Identifier
    tenant_id: Identifier
    workspace_id: Identifier
    project_id: Identifier
    timeline_id: Identifier
    timeline_version_id: Identifier
    timeline_revision: Annotated[int, Field(ge=1)]
    final_video_id: Identifier
    idempotency_key: IdempotencyKey
    idempotency_scope: Identifier
    request_fingerprint: Sha256
    preview: PreviewSummary
    profile: RenderProfile
    status: RenderStatus = RenderStatus.QUEUED
    attempt: Annotated[int, Field(ge=0)] = 0
    max_attempts: Annotated[int, Field(ge=1, le=100)]
    task_revision: Annotated[int, Field(ge=1)] = 1
    renderer_job_id: Identifier | None = None
    failure: RenderFailure | None = None
    cancellation_reason: Annotated[str, StringConstraints(min_length=1, max_length=1_000)] | None = None
    retry_idempotency_key: IdempotencyKey | None = None
    output_version_id: Identifier | None = None
    billing_currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
    billing_estimated_minor: Annotated[int, Field(gt=0)]
    billing_actual_minor: Annotated[int, Field(ge=0)] | None = None
    billing_pricing_version: Identifier
    billing_status: RenderBillingStatus = RenderBillingStatus.ACTIVE
    processed_callback_event_ids: tuple[Identifier, ...] = ()
    created_at: datetime
    updated_at: datetime
    deadline_at: datetime

    @classmethod
    def queued(
        cls,
        *,
        task_id: str,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        timeline_id: str,
        timeline_version_id: str,
        timeline_revision: int,
        final_video_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        preview: PreviewSummary,
        profile: RenderProfile,
        billing_currency: str,
        billing_estimated_minor: int,
        billing_pricing_version: str,
        max_attempts: int,
        created_at: datetime,
        deadline_at: datetime,
    ) -> RenderTask:
        validate_idempotency_key(idempotency_key)
        return cls(
            task_id=task_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            project_id=project_id,
            timeline_id=timeline_id,
            timeline_version_id=timeline_version_id,
            timeline_revision=timeline_revision,
            final_video_id=final_video_id,
            idempotency_key=idempotency_key,
            idempotency_scope=f"{tenant_id}:{workspace_id}:{idempotency_key}",
            request_fingerprint=request_fingerprint,
            preview=preview,
            profile=profile,
            billing_currency=billing_currency,
            billing_estimated_minor=billing_estimated_minor,
            billing_pricing_version=billing_pricing_version,
            max_attempts=max_attempts,
            created_at=created_at,
            updated_at=created_at,
            deadline_at=deadline_at,
        )

    @model_validator(mode="after")
    def validate_snapshot(self) -> RenderTask:
        for field_name in ("created_at", "updated_at", "deadline_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} 必须包含时区")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at 不能早于 created_at")
        if self.deadline_at <= self.created_at:
            raise ValueError("deadline_at 必须晚于 created_at")
        expected_scope = f"{self.tenant_id}:{self.workspace_id}:{self.idempotency_key}"
        if self.idempotency_scope != expected_scope:
            raise ValueError("幂等范围与租户、工作区和幂等键不一致")
        if (
            self.preview.timeline_id != self.timeline_id
            or self.preview.timeline_version_id != self.timeline_version_id
            or self.preview.timeline_revision != self.timeline_revision
        ):
            raise ValueError("预览摘要没有绑定到任务的时间线版本")
        return self


def render_request_fingerprint(command: RequestRenderCommand, preview: PreviewSummary) -> str:
    return canonical_sha256(
        {
            "command": command.model_dump(mode="json"),
            "preview": preview.model_dump(mode="json"),
        }
    )


def start_render_task(
    task: RenderTask,
    *,
    renderer_job_id: str,
    accepted_at: datetime,
) -> RenderTask:
    if task.status not in {RenderStatus.QUEUED, RenderStatus.RETRYING}:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 开始渲染")
    if task.attempt >= task.max_attempts:
        raise InvalidRenderTransition("渲染任务已耗尽最大尝试次数")
    if accepted_at.tzinfo is None or accepted_at.utcoffset() is None:
        raise ValueError("accepted_at 必须包含时区")
    if accepted_at < task.updated_at:
        raise ValueError("渲染接单时间不能早于任务更新时间")
    if accepted_at >= task.deadline_at:
        raise InvalidRenderTransition("渲染任务已经超过截止时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.RUNNING,
                "attempt": task.attempt + 1,
                "task_revision": task.task_revision + 1,
                "renderer_job_id": renderer_job_id,
                "failure": None,
                "updated_at": accepted_at,
            }
        ).model_dump()
    )


def fail_render_task(
    task: RenderTask,
    *,
    failure: RenderFailure,
    event_id: str,
    occurred_at: datetime,
) -> RenderTask:
    if task.status not in {RenderStatus.RUNNING, RenderStatus.CANCELLING}:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 标记渲染失败")
    if occurred_at < task.updated_at:
        raise ValueError("回调时间不能早于任务更新时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.FAILED,
                "failure": failure,
                "task_revision": task.task_revision + 1,
                "updated_at": occurred_at,
                "processed_callback_event_ids": (*task.processed_callback_event_ids, event_id),
            }
        ).model_dump()
    )


def timeout_render_task(
    task: RenderTask,
    *,
    event_id: str,
    occurred_at: datetime,
) -> RenderTask:
    if task.status not in {
        RenderStatus.QUEUED,
        RenderStatus.RUNNING,
        RenderStatus.RETRYING,
        RenderStatus.CANCELLING,
    }:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 标记渲染超时")
    if occurred_at < task.updated_at:
        raise ValueError("超时时间不能早于任务更新时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.FAILED,
                "failure": RenderFailure(code="RENDER_TIMEOUT", message="渲染任务超过截止时间", retryable=True),
                "task_revision": task.task_revision + 1,
                "updated_at": occurred_at,
                "processed_callback_event_ids": (*task.processed_callback_event_ids, event_id),
            }
        ).model_dump()
    )


def request_cancel_render_task(task: RenderTask, *, reason: str, requested_at: datetime) -> RenderTask:
    if not reason.strip():
        raise ValueError("取消原因不能为空")
    if requested_at < task.updated_at:
        raise ValueError("取消时间不能早于任务更新时间")
    if task.status in {RenderStatus.QUEUED, RenderStatus.RETRYING}:
        target = RenderStatus.CANCELLED
    elif task.status is RenderStatus.RUNNING:
        target = RenderStatus.CANCELLING
    else:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 请求取消")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": target,
                "cancellation_reason": reason,
                "task_revision": task.task_revision + 1,
                "updated_at": requested_at,
            }
        ).model_dump()
    )


def retry_render_task(task: RenderTask, *, idempotency_key: str, retried_at: datetime) -> RenderTask:
    validate_idempotency_key(idempotency_key)
    if task.status is not RenderStatus.FAILED:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 重试渲染")
    if task.failure is None or not task.failure.retryable:
        raise InvalidRenderTransition("当前渲染失败不可重试")
    if task.attempt >= task.max_attempts:
        raise InvalidRenderTransition("渲染任务已耗尽最大尝试次数")
    if retried_at < task.updated_at:
        raise ValueError("重试时间不能早于任务更新时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.RETRYING,
                "renderer_job_id": None,
                "retry_idempotency_key": idempotency_key,
                "task_revision": task.task_revision + 1,
                "updated_at": retried_at,
            }
        ).model_dump()
    )


def cancel_render_task_from_callback(task: RenderTask, *, event_id: str, occurred_at: datetime) -> RenderTask:
    if task.status not in {RenderStatus.RUNNING, RenderStatus.CANCELLING}:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 确认取消")
    if occurred_at < task.updated_at:
        raise ValueError("回调时间不能早于任务更新时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.CANCELLED,
                "task_revision": task.task_revision + 1,
                "updated_at": occurred_at,
                "processed_callback_event_ids": (*task.processed_callback_event_ids, event_id),
            }
        ).model_dump()
    )


def succeed_render_task(
    task: RenderTask,
    *,
    output_version_id: str,
    event_id: str,
    occurred_at: datetime,
) -> RenderTask:
    if task.status is not RenderStatus.RUNNING:
        raise InvalidRenderTransition(f"不能从 {task.status.value} 标记渲染成功")
    if occurred_at < task.updated_at:
        raise ValueError("回调时间不能早于任务更新时间")
    if occurred_at >= task.deadline_at:
        raise InvalidRenderTransition("渲染回调已经超过截止时间")
    return RenderTask.model_validate(
        task.model_copy(
            update={
                "status": RenderStatus.SUCCEEDED,
                "output_version_id": output_version_id,
                "task_revision": task.task_revision + 1,
                "updated_at": occurred_at,
                "processed_callback_event_ids": (*task.processed_callback_event_ids, event_id),
            }
        ).model_dump()
    )
