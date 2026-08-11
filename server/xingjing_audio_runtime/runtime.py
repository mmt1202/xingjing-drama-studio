"""Production composition for the already-migrated M07 PostgreSQL adapter.

This module accepts identity only through the Java platform session resolver and
never creates a SQLite, in-memory, local-media, or fake-Provider fallback.
Provider and object-storage implementations are deployment dependencies passed
in explicitly by the HTTP/application composition layer.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_audio.timeline import SubtitleCue, SubtitleTimeline
from server.xingjing_audio_persistence.models import generation_tasks
from server.xingjing_audio_persistence.repository import AudioPersistenceConflict, AudioPostgresRepository, AudioScope
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow

from .artifact_storage import AudioArtifactStorageError, LocalAudioObjectStorage, StoredObject
from .provider_gateway import HttpAudioMediaProvider, MediaSubmission

TrustedContextResolver = Callable[[Request], Awaitable[TrustedWorkspaceContext]]
SessionFactory = async_sessionmaker[AsyncSession]
logger = logging.getLogger(__name__)


class AudioRuntimeUnavailable(RuntimeError):
    """A required production dependency is unavailable or intentionally absent."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AudioBillingPolicy:
    currency: str
    pricing_version: str
    estimated_minor_by_kind: Mapping[str, int]

    def estimate(self, task_kind: str) -> int:
        value = self.estimated_minor_by_kind.get(task_kind)
        if value is None or value <= 0:
            raise AudioRuntimeUnavailable("AUDIO_TASK_PRICE_NOT_CONFIGURED")
        return value


class AudioObjectStorage(Protocol):
    """Production object storage must verify media before its reference is persisted."""

    async def inspect(self, *, scope: AudioScope, object_key: str) -> StoredObject: ...


class AudioMediaProvider(Protocol):
    """Production Provider port; implementations perform actual remote work."""

    async def submit(self, *, scope: AudioScope, task: Mapping[str, Any]) -> MediaSubmission: ...

    async def check_lip_sync(self, *, scope: AudioScope, inputs: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def cancel(self, *, scope: AudioScope, provider_task_id: str) -> None: ...


class AudioRuntime:
    """Owns M07 production dependencies and binds every repository call to a trusted scope."""

    def __init__(
        self,
        *,
        repository: AudioPostgresRepository | None,
        session_factory: SessionFactory | None,
        context_resolver: TrustedContextResolver | None,
        provider: AudioMediaProvider | None = None,
        object_storage: AudioObjectStorage | None = None,
        provider_callback_secret: str | None = None,
        billing_policy: AudioBillingPolicy | None = None,
        unavailable_code: str | None = None,
        engine: AsyncEngine | None = None,
        task_timeout_seconds: int = 21_600,
    ) -> None:
        self.repository = repository
        self._session_factory = session_factory
        self._context_resolver = context_resolver
        self._provider = provider
        self._object_storage = object_storage
        self._provider_callback_secret = provider_callback_secret
        self._billing_policy = billing_policy
        self.unavailable_code = unavailable_code
        self._engine = engine
        self._task_timeout_seconds = task_timeout_seconds
        self._timeout_sweeper: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self.unavailable_code is None

    async def scope_for(
        self, request: Request, project_id: str, permission: str
    ) -> tuple[AudioScope, TrustedWorkspaceContext]:
        """Resolve a Java-backed session, permission, and scoped active project."""
        if self.unavailable_code or self._context_resolver is None:
            raise AudioRuntimeUnavailable(self.unavailable_code or "AUDIO_RUNTIME_UNAVAILABLE")
        context = await self._context_resolver(request)
        if permission not in context.permissions:
            raise PermissionError(permission)
        scope = AudioScope(context.tenant_id, context.workspace_id, project_id)
        if not await self._project_exists(scope):
            raise LookupError("PROJECT_NOT_FOUND")
        return scope, context

    async def get_audio_track(self, scope: AudioScope, track_id: str) -> Mapping[str, Any]:
        return await self._repository_or_raise().get_audio_track(scope, track_id)

    async def get_subtitle_track(self, scope: AudioScope, track_id: str) -> Mapping[str, Any]:
        return await self._repository_or_raise().get_subtitle_track(scope, track_id)

    async def list_tracks(
        self,
        scope: AudioScope,
        *,
        track_kind: str | None = None,
        state: str | None = None,
        language: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], int]:
        repository = self._repository_or_raise()
        audio_tracks, audio_total = await repository.list_audio_tracks(
            scope, track_kind=track_kind, state=state, query=query, offset=offset, limit=limit
        )
        subtitle_tracks, subtitle_total = await repository.list_subtitle_tracks(
            scope, state=state, language=language, query=query, offset=offset, limit=limit
        )
        return audio_tracks, subtitle_tracks, audio_total + subtitle_total

    async def list_media_tasks(
        self,
        scope: AudioScope,
        *,
        status: str | None = None,
        task_kind: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._repository_or_raise().list_generation_tasks(
            scope, status=status, task_kind=task_kind, query=query, offset=offset, limit=limit
        )

    async def list_audit_events(
        self,
        scope: AudioScope,
        *,
        request_id: str | None = None,
        actor_id: str | None = None,
        object_type: str | None = None,
        object_id: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._repository_or_raise().list_audit_events(
            scope,
            request_id=request_id,
            actor_id=actor_id,
            object_type=object_type,
            object_id=object_id,
            offset=offset,
            limit=limit,
        )

    async def list_lip_sync_versions(
        self, scope: AudioScope, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._repository_or_raise().list_lip_sync_versions(scope, offset=offset, limit=limit)

    async def list_track_versions(
        self, scope: AudioScope, *, track_kind: str, track_id: str, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        repository = self._repository_or_raise()
        if track_kind == "audio":
            return await repository.list_audio_track_versions(scope, track_id, offset=offset, limit=limit)
        if track_kind == "subtitle":
            return await repository.list_subtitle_track_versions(scope, track_id, offset=offset, limit=limit)
        raise ValueError("AUDIO_TRACK_KIND_INVALID")

    async def delivery_snapshot(self, scope: AudioScope) -> Mapping[str, Any]:
        return await self._repository_or_raise().delivery_snapshot(scope)

    async def media_object_file(self, scope: AudioScope, object_key: str) -> tuple[str, str]:
        """Resolve an M07 artifact only after the HTTP layer has authorized the project scope."""
        storage = self._object_storage
        if not isinstance(storage, LocalAudioObjectStorage):
            raise AudioRuntimeUnavailable("AUDIO_OBJECT_STREAM_UNAVAILABLE")
        try:
            stored = await storage.inspect(scope=scope, object_key=object_key)
            return str(storage.file_for(scope=scope, object_key=object_key)), stored.mime_type
        except AudioArtifactStorageError as error:
            raise AudioRuntimeUnavailable(str(error)) from error

    async def create_lip_sync_version(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        version_id: str,
        audio_version_id: str,
        subtitle_version_id: str | None,
        input_video_key: str,
        calibration: Mapping[str, Any],
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        preflight = await self.check_lip_sync_inputs(
            scope,
            audio_version_id=audio_version_id,
            subtitle_version_id=subtitle_version_id,
            input_video_key=input_video_key,
            calibration=calibration,
        )
        if not preflight["allowed"]:
            raise ValueError("LIP_SYNC_PREFLIGHT_REJECTED")
        return await self._repository_or_raise().create_lip_sync_version(
            scope=scope,
            version_id=version_id,
            audio_version_id=audio_version_id,
            subtitle_version_id=subtitle_version_id,
            input_video_key=input_video_key,
            calibration=calibration,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "create_lip_sync_version", version_id, audio_version_id, subtitle_version_id, input_video_key, calibration
            ),
        )

    async def check_lip_sync_inputs(
        self,
        scope: AudioScope,
        *,
        audio_version_id: str,
        subtitle_version_id: str | None,
        input_video_key: str,
        calibration: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        await self._repository_or_raise().validate_lip_sync_inputs(
            scope,
            audio_version_id=audio_version_id,
            subtitle_version_id=subtitle_version_id,
            input_video_key=input_video_key,
        )
        result = await self._provider_or_raise().check_lip_sync(
            scope=scope,
            inputs={
                "audioVersionId": audio_version_id,
                "subtitleVersionId": subtitle_version_id,
                "inputVideoKey": input_video_key,
                "calibration": dict(calibration),
            },
        )
        return {
            "allowed": bool(result["allowed"]),
            "checks": result.get("checks", []),
            "modelId": result.get("modelId"),
            "audioDurationMs": result.get("audioDurationMs"),
            "videoDurationMs": result.get("videoDurationMs"),
            "speechRate": result.get("speechRate"),
            "faceVisibility": result.get("faceVisibility"),
        }

    async def complete_lip_sync_version(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        version_id: str,
        expected_version: int,
        source_task_id: str,
        output_key: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        stored = await self._inspect_object(scope, output_key)
        if not stored.mime_type.startswith("video/"):
            raise ValueError("LIP_SYNC_OUTPUT_MUST_BE_VIDEO")
        return await self._repository_or_raise().complete_lip_sync_version(
            scope=scope,
            version_id=version_id,
            expected_version=expected_version,
            source_task_id=source_task_id,
            output_key=stored.key,
            output_sha256=stored.sha256,
            output_size_bytes=stored.size_bytes,
            mime_type=stored.mime_type,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint(
                "complete_lip_sync_version", version_id, expected_version, source_task_id, stored
            ),
        )

    async def create_lip_sync_fallback(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        version_id: str,
        fallback_of_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        return await self._repository_or_raise().create_lip_sync_fallback(
            scope=scope,
            version_id=version_id,
            fallback_of_id=fallback_of_id,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("create_lip_sync_fallback", version_id, fallback_of_id),
        )

    async def select_lip_sync_version(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        version_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        return await self._repository_or_raise().select_lip_sync_version(
            scope=scope,
            version_id=version_id,
            expected_version=expected_version,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("select_lip_sync_version", version_id, expected_version),
        )

    async def create_audio_track(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        track_id: str,
        track_kind: str,
        title: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        return await self._repository_or_raise().create_audio_track(
            scope=scope,
            track_id=track_id,
            track_kind=track_kind,
            title=title,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("create_audio_track", track_id, track_kind, title),
        )

    async def create_subtitle_track(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        track_id: str,
        language: str,
        format: str,
        title: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        return await self._repository_or_raise().create_subtitle_track(
            scope=scope,
            track_id=track_id,
            language=language,
            format=format,
            title=title,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("create_subtitle_track", track_id, language, format, title),
        )

    async def append_audio_version(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        track_id: str,
        version_id: str,
        expected_version: int,
        cue_payload: Mapping[str, Any],
        object_key: str,
        idempotency_key: str,
        source_task_id: str | None = None,
    ) -> Mapping[str, Any]:
        track = await self._repository_or_raise().get_audio_track(scope, track_id)
        validated_payload = _validate_audio_payload(str(track["track_kind"]), cue_payload)
        stored = await self._inspect_object(scope, object_key)
        return await self._repository_or_raise().append_audio_version(
            scope=scope,
            track_id=track_id,
            version_id=version_id,
            expected_version=expected_version,
            cue_payload=validated_payload,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("append_audio_version", track_id, expected_version, validated_payload, stored),
            object_key=stored.key,
            object_sha256=stored.sha256,
            object_size_bytes=stored.size_bytes,
            mime_type=stored.mime_type,
            source_task_id=source_task_id,
        )

    async def append_subtitle_version(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        track_id: str,
        version_id: str,
        expected_version: int,
        cues: Mapping[str, Any],
        idempotency_key: str,
        object_key: str | None = None,
        source_task_id: str | None = None,
    ) -> Mapping[str, Any]:
        validated_cues = _validate_subtitle_payload(cues)
        stored = await self._inspect_object(scope, object_key) if object_key else None
        return await self._repository_or_raise().append_subtitle_version(
            scope=scope,
            track_id=track_id,
            version_id=version_id,
            expected_version=expected_version,
            cues=validated_cues,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("append_subtitle_version", track_id, expected_version, validated_cues, stored),
            object_key=stored.key if stored else None,
            object_sha256=stored.sha256 if stored else None,
            object_size_bytes=stored.size_bytes if stored else None,
            mime_type=stored.mime_type if stored else None,
            source_task_id=source_task_id,
        )

    async def create_media_task(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        task_id: str,
        task_kind: str,
        resource_type: str,
        resource_id: str,
        idempotency_key: str,
        max_attempts: int = 2,
    ) -> Mapping[str, Any]:
        provider = self._provider_or_raise()
        repository = self._repository_or_raise()
        billing = self._billing_policy
        if billing is None:
            raise AudioRuntimeUnavailable("AUDIO_BILLING_NOT_CONFIGURED")
        estimated_minor = billing.estimate(task_kind)
        fingerprint = _fingerprint("create_media_task", task_id, task_kind, resource_type, resource_id, max_attempts)
        task = await repository.create_generation_task(
            scope=scope,
            task_id=task_id,
            task_kind=task_kind,
            resource_type=resource_type,
            resource_id=resource_id,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            max_attempts=max_attempts,
            estimated_minor=estimated_minor,
            currency=billing.currency,
            pricing_version=billing.pricing_version,
            timeout_at=datetime.now(UTC) + timedelta(seconds=self._task_timeout_seconds),
        )
        if task["status"] != "pending":
            return task
        try:
            submission = await provider.submit(scope=scope, task=task)
            if not submission.provider_name or not submission.provider_task_id:
                raise AudioRuntimeUnavailable("AUDIO_PROVIDER_INVALID_SUBMISSION")
        except Exception as error:
            await repository.transition_task(
                scope=scope,
                task_id=str(task["id"]),
                expected_version=int(task["version"]),
                status="failed",
                actor_id=context.actor_id,
                request_id=context.request_id,
                failure_metadata={"code": "AUDIO_PROVIDER_SUBMISSION_FAILED"},
                attempt_count=1,
                finished_at=datetime.now(UTC),
                billing_event_id=context.request_id,
                release_billing=True,
            )
            if isinstance(error, AudioRuntimeUnavailable):
                raise
            raise AudioRuntimeUnavailable("AUDIO_PROVIDER_SUBMISSION_FAILED") from error
        return await repository.transition_task(
            scope=scope,
            task_id=str(task["id"]),
            expected_version=int(task["version"]),
            status="queued",
            actor_id=context.actor_id,
            request_id=context.request_id,
            provider_name=submission.provider_name,
            provider_task_id=submission.provider_task_id,
            attempt_count=1,
        )

    async def retry_media_task(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        task_id: str,
        expected_version: int,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        repository = self._repository_or_raise()
        current = await self._get_task(scope, task_id)
        failure = current.get("failure_metadata")
        if isinstance(failure, Mapping) and failure.get("retryable") is False:
            raise ValueError("AUDIO_TASK_FAILURE_NOT_RETRYABLE")
        fingerprint = _fingerprint("retry_media_task", task_id, expected_version)
        retrying, replayed = await repository.begin_task_retry(
            scope=scope,
            task_id=task_id,
            expected_version=expected_version,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
        )
        if replayed:
            return retrying
        next_attempt = int(retrying["attempt_count"]) + 1
        try:
            submission = await self._provider_or_raise().submit(scope=scope, task=retrying)
            if not submission.provider_name or not submission.provider_task_id:
                raise AudioRuntimeUnavailable("AUDIO_PROVIDER_INVALID_SUBMISSION")
        except Exception as error:
            await repository.transition_task(
                scope=scope,
                task_id=task_id,
                expected_version=int(retrying["version"]),
                status="failed",
                actor_id=context.actor_id,
                request_id=context.request_id,
                failure_metadata={"code": "AUDIO_PROVIDER_RETRY_SUBMISSION_FAILED"},
                attempt_count=next_attempt,
                finished_at=datetime.now(UTC),
                billing_event_id=context.request_id,
                release_billing=True,
            )
            if isinstance(error, AudioRuntimeUnavailable):
                raise
            raise AudioRuntimeUnavailable("AUDIO_PROVIDER_RETRY_SUBMISSION_FAILED") from error
        return await repository.transition_task(
            scope=scope,
            task_id=task_id,
            expected_version=int(retrying["version"]),
            status="queued",
            actor_id=context.actor_id,
            request_id=context.request_id,
            provider_name=submission.provider_name,
            provider_task_id=submission.provider_task_id,
            attempt_count=next_attempt,
        )

    async def record_media_fallback(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        task_id: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        if not reason.strip():
            raise ValueError("AUDIO_FALLBACK_REASON_REQUIRED")
        recorded, _ = await self._repository_or_raise().record_task_fallback(
            scope=scope,
            task_id=task_id,
            expected_version=expected_version,
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=_fingerprint("record_media_fallback", task_id, expected_version, reason),
            fallback_metadata={"strategy": "keep_original", "reason": reason.strip()},
        )
        return recorded

    async def cancel_media_task(
        self,
        scope: AudioScope,
        context: TrustedWorkspaceContext,
        *,
        task_id: str,
        expected_version: int,
        reason: str,
        idempotency_key: str,
    ) -> Mapping[str, Any]:
        fingerprint = _fingerprint("cancel_media_task", task_id, expected_version, reason)
        replay = await self._repository_or_raise().replay_task_cancellation(
            scope=scope, idempotency_key=idempotency_key, request_fingerprint=fingerprint
        )
        if replay is not None:
            return replay
        task = await self._get_task(scope, task_id)
        if int(task["version"]) != expected_version:
            raise ValueError("AUDIO_TASK_VERSION_CONFLICT")
        status = str(task["status"])
        if status not in {"pending", "queued", "retrying", "running"}:
            raise ValueError("AUDIO_TASK_NOT_CANCELLABLE")
        cancellation_metadata = {"reason": reason}
        provider_task_id = task.get("provider_task_id")
        if status == "pending" and not provider_task_id:
            cancelled, replayed = await self._repository_or_raise().begin_task_cancellation(
                scope=scope,
                task_id=task_id,
                expected_version=expected_version,
                target_status="cancelled",
                actor_id=context.actor_id,
                request_id=context.request_id,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                cancellation_metadata=cancellation_metadata,
                finished_at=datetime.now(UTC),
            )
            return cancelled
        cancelling, replayed = await self._repository_or_raise().begin_task_cancellation(
            scope=scope,
            task_id=task_id,
            expected_version=expected_version,
            target_status="cancelling",
            actor_id=context.actor_id,
            request_id=context.request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            cancellation_metadata=cancellation_metadata,
        )
        if replayed:
            return cancelling
        if status == "running" or provider_task_id:
            if not isinstance(provider_task_id, str) or not provider_task_id:
                raise AudioRuntimeUnavailable("AUDIO_PROVIDER_CANCELLATION_UNAVAILABLE")
            await self._provider_or_raise().cancel(scope=scope, provider_task_id=provider_task_id)
        return await self._repository_or_raise().transition_task(
            scope=scope,
            task_id=task_id,
            expected_version=int(cancelling["version"]),
            status="cancelled",
            actor_id=context.actor_id,
            request_id=context.request_id,
            cancellation_metadata=cancellation_metadata,
            finished_at=datetime.now(UTC),
            billing_event_id=context.request_id,
            release_billing=True,
        )

    async def apply_provider_callback(
        self, *, raw_body: bytes, signature: str, payload: Mapping[str, object]
    ) -> Mapping[str, Any]:
        """Accept one signed terminal Provider event and persist verified media output."""
        secret = self._provider_callback_secret
        if not secret:
            raise AudioRuntimeUnavailable("AUDIO_PROVIDER_CALLBACK_UNAVAILABLE")
        normalized_signature = signature.removeprefix("sha256=").strip()
        expected_signature = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not normalized_signature or not hmac.compare_digest(normalized_signature, expected_signature):
            raise PermissionError("AUDIO_PROVIDER_CALLBACK_SIGNATURE_INVALID")
        scope = AudioScope(
            self._callback_text(payload, "tenantId"),
            self._callback_text(payload, "workspaceId"),
            self._callback_text(payload, "projectId"),
        )
        task_id = self._callback_text(payload, "taskId")
        provider_task_id = self._callback_text(payload, "providerTaskId")
        event_id = self._callback_text(payload, "eventId")
        outcome = self._callback_text(payload, "status")
        current = await self._get_task(scope, task_id)
        if current.get("provider_task_id") != provider_task_id:
            raise PermissionError("AUDIO_PROVIDER_CALLBACK_TASK_MISMATCH")
        if str(current.get("status")) in {"succeeded", "failed", "cancelled", "timed_out", "cancelling"}:
            return current
        if outcome == "succeeded":
            result_metadata = await self._store_callback_outputs(scope, task_id, event_id, payload)
            status = "succeeded"
            failure_metadata = None
            billing_currency, actual_minor = self._callback_billing(payload)
        elif outcome == "failed":
            status = "failed"
            result_metadata = None
            failure_metadata = {
                "eventId": event_id,
                "code": self._callback_text(payload, "failureCode"),
                "message": self._callback_text(payload, "failureMessage"),
                "retryable": self._callback_bool(payload, "retryable"),
            }
            billing_currency, actual_minor = None, None
        else:
            raise ValueError("AUDIO_PROVIDER_CALLBACK_STATUS_INVALID")
        try:
            return await self._repository_or_raise().transition_task(
                scope=scope,
                task_id=task_id,
                expected_version=int(current["version"]),
                status=status,
                actor_id=f"provider:{str(current.get('provider_name') or 'external')}",
                request_id=event_id,
                result_metadata=result_metadata,
                failure_metadata=failure_metadata,
                finished_at=datetime.now(UTC),
                billing_event_id=event_id,
                actual_minor=actual_minor,
                billing_currency=billing_currency,
                release_billing=(
                    status == "failed"
                    and (
                        not bool(failure_metadata and failure_metadata.get("retryable"))
                        or int(current.get("attempt_count") or 0) >= int(current.get("max_attempts") or 1)
                    )
                ),
            )
        except AudioPersistenceConflict:
            refreshed = await self._get_task(scope, task_id)
            if str(refreshed.get("status")) in {"succeeded", "failed", "cancelled", "timed_out"}:
                return refreshed
            raise

    async def start(self) -> None:
        if self.available and self._timeout_sweeper is None:
            self._timeout_sweeper = asyncio.create_task(self._timeout_sweeper_loop())

    async def _timeout_sweeper_loop(self) -> None:
        while True:
            try:
                await self.expire_overdue_tasks()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transient dependency failures retry next sweep
                logger.exception("M07 timeout sweep failed; the next sweep will retry")
            await asyncio.sleep(60)

    async def expire_overdue_tasks(self, *, at: datetime | None = None, limit: int = 100) -> int:
        expired_at = at or datetime.now(UTC)
        rows = await self._repository_or_raise().list_overdue_tasks(at=expired_at, limit=limit)
        provider_jobs: list[tuple[AudioScope, str]] = []
        completed = 0
        for task in rows:
            scope = AudioScope(str(task["tenant_id"]), str(task["workspace_id"]), str(task["project_id"]))
            try:
                await self._repository_or_raise().transition_task(
                    scope=scope,
                    task_id=str(task["id"]),
                    expected_version=int(task["version"]),
                    status="timed_out",
                    actor_id="audio-timeout-sweeper",
                    request_id=f"timeout:{task['id']}:{task['version']}",
                    failure_metadata={"code": "AUDIO_TASK_TIMEOUT", "message": "任务超过截止时间", "retryable": False},
                    finished_at=expired_at,
                    billing_event_id=f"timeout:{task['id']}:{task['version']}",
                    release_billing=True,
                )
            except AudioPersistenceConflict:
                continue
            provider_task_id = task.get("provider_task_id")
            if isinstance(provider_task_id, str) and provider_task_id:
                provider_jobs.append((scope, provider_task_id))
            completed += 1
        if provider_jobs and self._provider is not None:
            results = await asyncio.gather(
                *(self._provider.cancel(scope=scope, provider_task_id=job_id) for scope, job_id in provider_jobs),
                return_exceptions=True,
            )
            for (_, job_id), result in zip(provider_jobs, results, strict=True):
                if isinstance(result, Exception):
                    logger.warning("M07 provider cancellation failed after timeout: job_id=%s error=%s", job_id, result)
        return completed

    async def close(self) -> None:
        if self._timeout_sweeper is not None:
            self._timeout_sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await self._timeout_sweeper
            self._timeout_sweeper = None
        if self._engine is not None:
            engine = self._engine
            self._engine = None
            await engine.dispose()

    def _repository_or_raise(self) -> AudioPostgresRepository:
        if self.unavailable_code or self.repository is None:
            raise AudioRuntimeUnavailable(self.unavailable_code or "AUDIO_RUNTIME_UNAVAILABLE")
        return self.repository

    def _provider_or_raise(self) -> AudioMediaProvider:
        if self._provider is None:
            raise AudioRuntimeUnavailable("AUDIO_PROVIDER_NOT_CONFIGURED")
        return self._provider

    async def _inspect_object(self, scope: AudioScope, object_key: str | None) -> StoredObject:
        if self._object_storage is None:
            raise AudioRuntimeUnavailable("AUDIO_OBJECT_STORAGE_NOT_CONFIGURED")
        if not object_key:
            raise ValueError("AUDIO_OBJECT_KEY_REQUIRED")
        stored = await self._object_storage.inspect(scope=scope, object_key=object_key)
        if stored.key != object_key or not stored.sha256 or stored.size_bytes <= 0 or not stored.mime_type:
            raise AudioRuntimeUnavailable("AUDIO_OBJECT_STORAGE_INVALID_OBJECT")
        return stored

    async def _store_callback_outputs(
        self, scope: AudioScope, task_id: str, event_id: str, payload: Mapping[str, object]
    ) -> Mapping[str, Any]:
        storage = self._object_storage
        importer = cast(Callable[..., Awaitable[StoredObject]] | None, getattr(storage, "import_provider_output", None))
        if not callable(importer):
            raise AudioRuntimeUnavailable("AUDIO_PROVIDER_ARTIFACT_STORAGE_UNAVAILABLE")
        raw_outputs = payload.get("outputs")
        if not isinstance(raw_outputs, list) or not raw_outputs:
            raise ValueError("AUDIO_PROVIDER_CALLBACK_OUTPUTS_REQUIRED")
        outputs: list[dict[str, object]] = []
        seen_output_ids: set[str] = set()
        for raw_output in raw_outputs:
            if not isinstance(raw_output, Mapping):
                raise ValueError("AUDIO_PROVIDER_CALLBACK_OUTPUT_INVALID")
            output_id = self._callback_text(raw_output, "outputId")
            if output_id in seen_output_ids:
                raise ValueError("AUDIO_PROVIDER_CALLBACK_OUTPUT_DUPLICATE")
            seen_output_ids.add(output_id)
            try:
                stored = await importer(
                    scope=scope,
                    task_id=task_id,
                    output_id=output_id,
                    source_path=self._callback_text(raw_output, "sourcePath"),
                )
            except AudioArtifactStorageError as error:
                raise ValueError(str(error)) from error
            outputs.append({
                "outputId": output_id,
                "objectKey": stored.key,
                "sha256": stored.sha256,
                "sizeBytes": stored.size_bytes,
                "mimeType": stored.mime_type,
            })
        return {"providerEventId": event_id, "outputs": outputs}

    @staticmethod
    def _callback_text(payload: Mapping[str, object], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"AUDIO_PROVIDER_CALLBACK_{name.upper()}_REQUIRED")
        return value.strip()

    @staticmethod
    def _callback_bool(payload: Mapping[str, object], name: str) -> bool:
        value = payload.get(name)
        if not isinstance(value, bool):
            raise ValueError(f"AUDIO_PROVIDER_CALLBACK_{name.upper()}_REQUIRED")
        return value

    @staticmethod
    def _callback_billing(payload: Mapping[str, object]) -> tuple[str, int]:
        raw = payload.get("billing")
        if not isinstance(raw, Mapping):
            raise ValueError("AUDIO_PROVIDER_CALLBACK_BILLING_REQUIRED")
        currency = raw.get("currency")
        actual_minor = raw.get("actualMinor")
        if (
            not isinstance(currency, str)
            or len(currency.strip()) != 3
            or isinstance(actual_minor, bool)
            or not isinstance(actual_minor, int)
            or actual_minor < 0
        ):
            raise ValueError("AUDIO_PROVIDER_CALLBACK_BILLING_INVALID")
        return currency.strip().upper(), actual_minor

    async def _project_exists(self, scope: AudioScope) -> bool:
        if self._session_factory is None:
            raise AudioRuntimeUnavailable(self.unavailable_code or "AUDIO_RUNTIME_UNAVAILABLE")
        try:
            async with self._session_factory() as session:
                return (
                    await session.scalar(
                        select(ProjectRow.id).where(
                            ProjectRow.id == scope.project_id,
                            ProjectRow.tenant_id == scope.tenant_id,
                            ProjectRow.workspace_id == scope.workspace_id,
                            ProjectRow.deleted_at.is_(None),
                        )
                    )
                ) is not None
        except SQLAlchemyError as error:
            raise AudioRuntimeUnavailable("AUDIO_RUNTIME_UNAVAILABLE") from error

    async def _get_task(self, scope: AudioScope, task_id: str) -> Mapping[str, Any]:
        if self._session_factory is None:
            raise AudioRuntimeUnavailable(self.unavailable_code or "AUDIO_RUNTIME_UNAVAILABLE")
        try:
            async with self._session_factory() as session:
                row = (
                    (
                        await session.execute(
                            select(generation_tasks).where(
                                *scope.criteria(generation_tasks), generation_tasks.c.id == task_id
                            )
                        )
                    )
                    .mappings()
                    .one_or_none()
                )
        except SQLAlchemyError as error:
            raise AudioRuntimeUnavailable("AUDIO_RUNTIME_UNAVAILABLE") from error
        if row is None:
            raise LookupError("AUDIO_TASK_NOT_FOUND")
        return dict(row)


def create_production_audio_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
    provider: AudioMediaProvider | None = None,
    object_storage: AudioObjectStorage | None = None,
) -> AudioRuntime:
    """Create M07's PostgreSQL-only production composition over migrated tables."""
    url = (database_url or os.environ.get("XINGJING_AUDIO_DATABASE_URL", "")).strip()
    if not url:
        return _unavailable_runtime("XINGJING_AUDIO_DATABASE_URL_REQUIRED")
    if not url.startswith("postgresql+asyncpg://"):
        return _unavailable_runtime("XINGJING_AUDIO_DATABASE_URL_MUST_BE_POSTGRESQL_ASYNCPG")
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return _unavailable_runtime("AUDIO_RUNTIME_DEPENDENCY_UNAVAILABLE")
    factory: SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
    configured_provider = provider or _provider_from_environment()
    artifact_root = os.environ.get("XINGJING_M07_ARTIFACT_ROOT", "").strip()
    provider_project_root = os.environ.get("XINGJING_M07_PROVIDER_PROJECT_ROOT", "").strip()
    callback_secret = os.environ.get("XINGJING_M07_PROVIDER_CALLBACK_SECRET", "").strip()
    if callback_secret and len(callback_secret) < 32:
        return _unavailable_runtime("XINGJING_M07_PROVIDER_CALLBACK_SECRET_MINIMUM_32_REQUIRED")
    raw_timeout = os.environ.get("XINGJING_M07_TASK_TIMEOUT_SECONDS", "21600").strip()
    if not raw_timeout.isdigit() or not 60 <= int(raw_timeout) <= 604_800:
        return _unavailable_runtime("XINGJING_M07_TASK_TIMEOUT_SECONDS_INVALID")
    return AudioRuntime(
        repository=AudioPostgresRepository(factory),
        session_factory=factory,
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        provider=configured_provider,
        object_storage=object_storage or (LocalAudioObjectStorage(artifact_root, provider_project_root=provider_project_root) if artifact_root else None),
        provider_callback_secret=callback_secret or None,
        billing_policy=_billing_policy_from_environment(),
        engine=engine,
        task_timeout_seconds=int(raw_timeout),
    )


def _unavailable_runtime(code: str) -> AudioRuntime:
    return AudioRuntime(repository=None, session_factory=None, context_resolver=None, unavailable_code=code)


def _provider_from_environment() -> HttpAudioMediaProvider | None:
    values = {
        "provider_id": os.environ.get("XINGJING_M07_PROVIDER_ID", "").strip(),
        "submit_url": os.environ.get("XINGJING_M07_PROVIDER_SUBMIT_URL", "").strip(),
        "check_url": os.environ.get("XINGJING_M07_PROVIDER_CHECK_URL", "").strip(),
        "cancel_url_template": os.environ.get("XINGJING_M07_PROVIDER_CANCEL_URL", "").strip(),
        "bearer_token": os.environ.get("XINGJING_M07_PROVIDER_TOKEN", "").strip(),
    }
    if not any(values.values()):
        return None
    try:
        return HttpAudioMediaProvider(**values)
    except ValueError:
        return None


def _billing_policy_from_environment() -> AudioBillingPolicy | None:
    currency = os.environ.get("XINGJING_M07_BILLING_CURRENCY", "").strip().upper()
    pricing_version = os.environ.get("XINGJING_M07_PRICING_VERSION", "").strip()
    raw_prices = os.environ.get("XINGJING_M07_ESTIMATED_COSTS_JSON", "").strip()
    if not any((currency, pricing_version, raw_prices)):
        return None
    try:
        parsed = json.loads(raw_prices)
    except ValueError:
        return None
    if (
        len(currency) != 3
        or not pricing_version
        or not isinstance(parsed, dict)
        or set(parsed) != {"audio", "subtitle", "lip_sync", "mix"}
        or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in parsed.values())
    ):
        return None
    return AudioBillingPolicy(
        currency=currency,
        pricing_version=pricing_version,
        estimated_minor_by_kind=cast(dict[str, int], parsed),
    )


def _validate_subtitle_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    duration_ms = payload.get("durationMs")
    raw_cues = payload.get("cues")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or not isinstance(raw_cues, list):
        raise ValueError("INVALID_SUBTITLE_TIMELINE")
    parsed: list[SubtitleCue] = []
    for raw in raw_cues:
        if not isinstance(raw, Mapping):
            raise ValueError("INVALID_SUBTITLE_CUE")
        cue_id = raw.get("id")
        start_ms = raw.get("startMs")
        end_ms = raw.get("endMs")
        text_value = raw.get("text")
        source_line_id = raw.get("sourceLineId")
        if (
            not isinstance(cue_id, str)
            or isinstance(start_ms, bool)
            or not isinstance(start_ms, int)
            or isinstance(end_ms, bool)
            or not isinstance(end_ms, int)
            or not isinstance(text_value, str)
            or (source_line_id is not None and not isinstance(source_line_id, str))
        ):
            raise ValueError("INVALID_SUBTITLE_CUE")
        parsed.append(SubtitleCue(cue_id, start_ms, end_ms, text_value, source_line_id))
    timeline = SubtitleTimeline.create(duration_ms, tuple(parsed))
    return {
        "durationMs": timeline.duration_ms,
        "cues": [
            {
                "id": cue.cue_id,
                "startMs": cue.start_ms,
                "endMs": cue.end_ms,
                "text": cue.text,
                **({"sourceLineId": cue.source_line_id} if cue.source_line_id else {}),
            }
            for cue in timeline.cues
        ],
    }


def _validate_audio_payload(track_kind: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
    field = "clips" if track_kind in {"dialogue", "voiceover"} else "segments"
    rows = payload.get(field)
    duration_ms = payload.get("durationMs")
    if isinstance(duration_ms, bool) or not isinstance(duration_ms, int) or duration_ms <= 0 or not isinstance(rows, list):
        raise ValueError("INVALID_AUDIO_TIMELINE")
    normalized: list[dict[str, object]] = []
    previous_end = 0
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise ValueError("INVALID_AUDIO_SEGMENT")
        segment_id = raw.get("id")
        start_ms = raw.get("startMs")
        end_ms = raw.get("endMs")
        if (
            not isinstance(segment_id, str)
            or not segment_id.strip()
            or isinstance(start_ms, bool)
            or not isinstance(start_ms, int)
            or isinstance(end_ms, bool)
            or not isinstance(end_ms, int)
            or start_ms < previous_end
            or end_ms <= start_ms
            or end_ms > duration_ms
        ):
            raise ValueError("INVALID_AUDIO_SEGMENT_TIMECODE")
        if track_kind in {"dialogue", "voiceover"}:
            required = ("roleId", "voiceId", "text", "language")
        else:
            required = ("objectKey",)
        if any(not isinstance(raw.get(name), str) or not str(raw[name]).strip() for name in required):
            raise ValueError("INVALID_AUDIO_SEGMENT_FIELDS")
        gain_db = raw.get("gainDb", 0)
        fade_in_ms = raw.get("fadeInMs", 0)
        fade_out_ms = raw.get("fadeOutMs", 0)
        if (
            isinstance(gain_db, bool)
            or not isinstance(gain_db, (int, float))
            or not -60 <= float(gain_db) <= 12
            or isinstance(fade_in_ms, bool)
            or not isinstance(fade_in_ms, int)
            or isinstance(fade_out_ms, bool)
            or not isinstance(fade_out_ms, int)
            or min(fade_in_ms, fade_out_ms) < 0
            or fade_in_ms + fade_out_ms > end_ms - start_ms
        ):
            raise ValueError("INVALID_AUDIO_MIX_PARAMETERS")
        normalized.append(dict(raw))
        previous_end = end_ms
    loudness_lufs = payload.get("loudnessLufs", -16)
    if (
        isinstance(loudness_lufs, bool)
        or not isinstance(loudness_lufs, (int, float))
        or not -36 <= float(loudness_lufs) <= -5
    ):
        raise ValueError("INVALID_AUDIO_LOUDNESS")
    return {"durationMs": duration_ms, field: normalized, "loudnessLufs": float(loudness_lufs)}


def _fingerprint(operation: str, *values: object) -> str:
    payload = json.dumps(
        [operation, *values], default=_json_default, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value: object) -> object:
    if isinstance(value, StoredObject):
        return {"key": value.key, "sha256": value.sha256, "size_bytes": value.size_bytes, "mime_type": value.mime_type}
    raise TypeError(f"unsupported fingerprint value: {type(value)!r}")
