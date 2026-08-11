from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from .errors import InvalidTaskTransition


class MediaTaskKind(StrEnum):
    TTS = "tts"
    MIX = "mix"
    SUBTITLE_RENDER = "subtitle_render"
    LIP_SYNC = "lip_sync"


class MediaTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FALLBACK = "fallback"


class FallbackPolicy(StrEnum):
    NONE = "none"
    KEEP_SOURCE = "keep_source"


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    message: str
    retryable: bool


@dataclass(frozen=True, slots=True)
class MediaTask:
    task_id: str
    workspace_id: str
    project_id: str
    kind: MediaTaskKind
    idempotency_key: str
    input_version_ids: tuple[str, ...]
    selection_start_ms: int
    selection_end_ms: int
    fallback_policy: FallbackPolicy
    status: MediaTaskStatus = MediaTaskStatus.QUEUED
    provider: str | None = None
    provider_job_id: str | None = None
    output_object_key: str | None = None
    output_version_id: str | None = None
    failure: ProviderFailure | None = None
    cancellation_reason: str | None = None

    @classmethod
    def queued(
        cls,
        task_id: str,
        workspace_id: str,
        project_id: str,
        kind: MediaTaskKind,
        idempotency_key: str,
        input_version_ids: tuple[str, ...],
        selection_start_ms: int,
        selection_end_ms: int,
        fallback_policy: FallbackPolicy,
    ) -> MediaTask:
        if not all((task_id, workspace_id, project_id, idempotency_key)):
            raise ValueError("task identity and idempotency key are required")
        if not input_version_ids:
            raise ValueError("at least one immutable input version is required")
        if selection_start_ms < 0 or selection_end_ms <= selection_start_ms:
            raise ValueError("invalid task selection")
        return cls(
            task_id,
            workspace_id,
            project_id,
            kind,
            idempotency_key,
            input_version_ids,
            selection_start_ms,
            selection_end_ms,
            fallback_policy,
        )

    def _require(self, *allowed: MediaTaskStatus) -> None:
        if self.status not in allowed:
            raise InvalidTaskTransition(f"cannot transition from {self.status}")

    def start(self, provider: str, provider_job_id: str | None = None) -> MediaTask:
        self._require(MediaTaskStatus.QUEUED)
        if not provider:
            raise ValueError("provider identity is required")
        return replace(self, status=MediaTaskStatus.RUNNING, provider=provider, provider_job_id=provider_job_id)

    def succeed(self, output_object_key: str, output_version_id: str) -> MediaTask:
        self._require(MediaTaskStatus.RUNNING)
        if not output_object_key or not output_version_id:
            raise ValueError("successful task requires persisted output references")
        return replace(
            self,
            status=MediaTaskStatus.SUCCEEDED,
            output_object_key=output_object_key,
            output_version_id=output_version_id,
        )

    def fail(self, failure: ProviderFailure) -> MediaTask:
        self._require(MediaTaskStatus.RUNNING)
        if not failure.code:
            raise ValueError("failure code is required")
        return replace(self, status=MediaTaskStatus.FAILED, failure=failure)

    def cancel(self, reason: str) -> MediaTask:
        self._require(MediaTaskStatus.QUEUED)
        if not reason:
            raise ValueError("cancellation reason is required")
        return replace(self, status=MediaTaskStatus.CANCELLED, cancellation_reason=reason)

    def request_cancel(self, reason: str) -> MediaTask:
        self._require(MediaTaskStatus.RUNNING)
        if not reason:
            raise ValueError("cancellation reason is required")
        return replace(self, status=MediaTaskStatus.CANCELLING, cancellation_reason=reason)

    def confirm_cancelled(self) -> MediaTask:
        self._require(MediaTaskStatus.CANCELLING)
        return replace(self, status=MediaTaskStatus.CANCELLED)

    def apply_fallback(self, source_version_id: str) -> MediaTask:
        self._require(MediaTaskStatus.FAILED)
        if self.fallback_policy is not FallbackPolicy.KEEP_SOURCE:
            raise InvalidTaskTransition("task has no fallback policy")
        if source_version_id not in self.input_version_ids:
            raise ValueError("fallback must reference an immutable task input")
        return replace(self, status=MediaTaskStatus.FALLBACK, output_version_id=source_version_id)
