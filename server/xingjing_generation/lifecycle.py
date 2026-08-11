from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Failure, GenerationTask, Identifier, TaskStatus


class InvalidTransition(ValueError):
    pass


class RetryPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_attempts: Annotated[int, Field(ge=1, le=100)]
    initial_delay_seconds: Annotated[float, Field(gt=0)]
    multiplier: Annotated[float, Field(ge=1)] = 2
    max_delay_seconds: Annotated[float, Field(gt=0)] = 300

    def delay_after(self, attempt: int) -> timedelta:
        seconds = min(
            self.initial_delay_seconds * self.multiplier ** max(attempt - 1, 0),
            self.max_delay_seconds,
        )
        return timedelta(seconds=seconds)


class ProviderCallback(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    provider_job_id: Identifier
    attempt: Annotated[int, Field(ge=1)]
    event_id: Identifier
    succeeded: bool
    output_asset_ids: tuple[Identifier, ...] = ()
    failure: Failure | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> ProviderCallback:
        if self.succeeded and (not self.output_asset_ids or self.failure is not None):
            raise ValueError("成功回调必须包含产物且不能包含失败信息")
        if not self.succeeded and (self.output_asset_ids or self.failure is None):
            raise ValueError("失败回调必须包含失败信息且不能包含产物")
        return self


class CallbackDisposition(StrEnum):
    APPLIED = "applied"
    DUPLICATE = "duplicate"
    IGNORED_LATE = "ignored_late"
    IGNORED_STALE_ATTEMPT = "ignored_stale_attempt"
    REJECTED_JOB_MISMATCH = "rejected_job_mismatch"


class CallbackResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    task: GenerationTask
    disposition: CallbackDisposition


_TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.TIMEOUT, TaskStatus.CANCELLED}


def _at_or_after(task: GenerationTask, at: datetime) -> None:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("状态变更时间必须包含时区")
    if at < task.updated_at:
        raise ValueError("状态变更时间不能倒退")


def _require(task: GenerationTask, allowed: set[TaskStatus], target: TaskStatus) -> None:
    if task.status not in allowed:
        raise InvalidTransition(f"不能从 {task.status.value} 转为 {target.value}")


def _require_before_deadline(task: GenerationTask, at: datetime) -> None:
    if at >= task.timeout_at:
        raise InvalidTransition("任务已经超时")


def _update(task: GenerationTask, at: datetime, **changes: object) -> GenerationTask:
    _at_or_after(task, at)
    return GenerationTask.model_validate(
        task.model_copy(update={"updated_at": at, "version": task.version + 1, **changes}).model_dump()
    )


def start_task(
    task: GenerationTask,
    *,
    provider_id: str,
    model_id: str,
    provider_job_id: str,
    at: datetime,
) -> GenerationTask:
    _require(task, {TaskStatus.QUEUED}, TaskStatus.RUNNING)
    _require_before_deadline(task, at)
    return _update(
        task,
        at,
        status=TaskStatus.RUNNING,
        attempt=task.attempt + 1,
        resolved_provider_id=provider_id,
        resolved_model_id=model_id,
        provider_job_id=provider_job_id,
        next_attempt_at=None,
        failure=None,
    )


def prepare_retry(
    task: GenerationTask,
    *,
    failure: Failure,
    policy: RetryPolicy,
    at: datetime,
) -> GenerationTask:
    _require(task, {TaskStatus.RUNNING}, TaskStatus.RETRYING)
    if failure.retryable and task.attempt < policy.max_attempts:
        return _update(
            task,
            at,
            status=TaskStatus.RETRYING,
            failure=failure,
            next_attempt_at=at + policy.delay_after(task.attempt),
        )
    return _update(
        task,
        at,
        status=TaskStatus.FAILED,
        failure=failure,
        completed_at=at,
        next_attempt_at=None,
    )


def queue_due_retry(task: GenerationTask, *, at: datetime) -> GenerationTask:
    _require(task, {TaskStatus.RETRYING}, TaskStatus.QUEUED)
    _require_before_deadline(task, at)
    if task.next_attempt_at is None or at < task.next_attempt_at:
        raise InvalidTransition("尚未到达重试时间")
    return _update(task, at, status=TaskStatus.QUEUED, provider_job_id=None, next_attempt_at=None)


def cancel_task(task: GenerationTask, *, at: datetime) -> GenerationTask:
    if task.status in {TaskStatus.QUEUED, TaskStatus.RETRYING}:
        return _update(task, at, status=TaskStatus.CANCELLED, completed_at=at, next_attempt_at=None)
    _require(task, {TaskStatus.RUNNING}, TaskStatus.CANCELLING)
    return _update(task, at, status=TaskStatus.CANCELLING)


def mark_cancelled(task: GenerationTask, *, at: datetime) -> GenerationTask:
    _require(task, {TaskStatus.CANCELLING}, TaskStatus.CANCELLED)
    return _update(task, at, status=TaskStatus.CANCELLED, completed_at=at)


def fail_task(task: GenerationTask, *, failure: Failure, at: datetime) -> GenerationTask:
    """Record a terminal infrastructure failure before provider start.

    A legacy queue can fail a dispatch before it has exposed a provider job ID.
    The M06 task must then become truthfully failed, rather than remain queued
    forever or invent a provider job merely to reuse another transition.
    """
    _require(task, {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.CANCELLING}, TaskStatus.FAILED)
    return _update(task, at, status=TaskStatus.FAILED, failure=failure, completed_at=at, next_attempt_at=None)


def expire_task(task: GenerationTask, *, at: datetime) -> GenerationTask:
    if task.status not in {TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.RETRYING, TaskStatus.CANCELLING}:
        raise InvalidTransition(f"不能从 {task.status.value} 转为 timeout")
    if at < task.timeout_at:
        raise InvalidTransition("任务尚未超时")
    return _update(
        task,
        at,
        status=TaskStatus.TIMEOUT,
        failure=Failure(code="TASK_TIMEOUT", message="任务超过截止时间", retryable=False),
        completed_at=at,
        next_attempt_at=None,
    )


def apply_provider_callback(
    task: GenerationTask,
    callback: ProviderCallback,
    *,
    at: datetime,
) -> CallbackResult:
    _at_or_after(task, at)
    if callback.event_id in task.processed_callback_event_ids:
        return CallbackResult(task=task, disposition=CallbackDisposition.DUPLICATE)
    if task.status in _TERMINAL or task.status is TaskStatus.CANCELLING:
        return CallbackResult(task=task, disposition=CallbackDisposition.IGNORED_LATE)
    if at >= task.timeout_at:
        return CallbackResult(task=expire_task(task, at=at), disposition=CallbackDisposition.IGNORED_LATE)
    if callback.attempt != task.attempt:
        return CallbackResult(task=task, disposition=CallbackDisposition.IGNORED_STALE_ATTEMPT)
    if callback.provider_job_id != task.provider_job_id:
        return CallbackResult(task=task, disposition=CallbackDisposition.REJECTED_JOB_MISMATCH)
    _require(task, {TaskStatus.RUNNING}, TaskStatus.SUCCEEDED if callback.succeeded else TaskStatus.FAILED)
    changes: dict[str, object] = {
        "status": TaskStatus.SUCCEEDED if callback.succeeded else TaskStatus.FAILED,
        "completed_at": at,
        "processed_callback_event_ids": (*task.processed_callback_event_ids, callback.event_id),
    }
    if callback.succeeded:
        changes["output_asset_ids"] = callback.output_asset_ids
    else:
        changes["failure"] = callback.failure
    return CallbackResult(task=_update(task, at, **changes), disposition=CallbackDisposition.APPLIED)
