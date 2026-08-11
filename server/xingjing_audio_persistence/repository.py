"""Async SQLAlchemy repository for the M07 PostgreSQL schema."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import randbits
from time import time_ns
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_audio_persistence.models import (
    audio_billing_holds,
    audio_billing_journals,
    audio_track_versions,
    audio_tracks,
    audit_events,
    generation_tasks,
    idempotency_records,
    lip_sync_versions,
    subtitle_track_versions,
    subtitle_tracks,
)
from server.xingjing_generation_persistence.repository import GeneratedAssetRow, GenerationBillingAccountRow


class AudioPersistenceError(RuntimeError):
    """Base error that callers map to their domain/API error contract."""


class AudioPersistenceNotFound(AudioPersistenceError):
    """A scoped resource is absent; never indicates whether another scope owns it."""


class AudioPersistenceConflict(AudioPersistenceError):
    """A write cannot be completed due to an existing authoritative state."""


class VersionConflict(AudioPersistenceConflict):
    """The expected aggregate version is stale."""


class IdempotencyConflict(AudioPersistenceConflict):
    """One idempotency key was reused with a different request fingerprint."""


class AudioBillingError(AudioPersistenceError):
    """Audio task billing cannot preserve balance or idempotency invariants."""


@dataclass(frozen=True, slots=True)
class AudioScope:
    tenant_id: str
    workspace_id: str
    project_id: str

    def criteria(self, table: Any) -> tuple[Any, ...]:
        return (
            table.c.tenant_id == self.tenant_id,
            table.c.workspace_id == self.workspace_id,
            table.c.project_id == self.project_id,
        )

    def values(self) -> dict[str, str]:
        return {"tenant_id": self.tenant_id, "workspace_id": self.workspace_id, "project_id": self.project_id}


def _uuid7() -> str:
    """Generate an RFC 9562 UUIDv7 for internally-created audit events."""
    milliseconds = time_ns() // 1_000_000
    value = (milliseconds << 80) | (0x7 << 76) | (randbits(12) << 64) | (0b10 << 62) | randbits(62)
    return str(UUID(int=value))


def _billing_event_id(action: str, reference: str) -> str:
    digest = hashlib.sha256(f"{action}:{reference}".encode()).hexdigest()
    return f"audio:{action}:{digest}"


class AudioPostgresRepository:
    """Real async PostgreSQL adapter; callers supply trusted scope and actor context.

    The repository intentionally accepts no tenant/workspace/project fields from a
    payload.  Every lookup and mutation receives ``AudioScope`` separately, so a
    missing or foreign object has the same ``AudioPersistenceNotFound`` outcome.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_audio_track(self, scope: AudioScope, track_id: str) -> Mapping[str, Any]:
        return await self._get_scoped(audio_tracks, scope, track_id)

    async def get_subtitle_track(self, scope: AudioScope, track_id: str) -> Mapping[str, Any]:
        return await self._get_scoped(subtitle_tracks, scope, track_id)

    async def list_audio_tracks(
        self,
        scope: AudioScope,
        *,
        track_kind: str | None = None,
        state: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        criteria = list(scope.criteria(audio_tracks))
        if track_kind:
            criteria.append(audio_tracks.c.track_kind == track_kind)
        if state:
            criteria.append(audio_tracks.c.state == state)
        if query:
            pattern = f"%{query}%"
            criteria.append(or_(audio_tracks.c.id.ilike(pattern), audio_tracks.c.title.ilike(pattern)))
        return await self._list_with_criteria(audio_tracks, criteria, offset=offset, limit=limit)

    async def list_subtitle_tracks(
        self,
        scope: AudioScope,
        *,
        state: str | None = None,
        language: str | None = None,
        query: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        criteria = list(scope.criteria(subtitle_tracks))
        if state:
            criteria.append(subtitle_tracks.c.state == state)
        if language:
            criteria.append(subtitle_tracks.c.language == language)
        if query:
            pattern = f"%{query}%"
            criteria.append(or_(subtitle_tracks.c.id.ilike(pattern), subtitle_tracks.c.title.ilike(pattern)))
        return await self._list_with_criteria(subtitle_tracks, criteria, offset=offset, limit=limit)

    async def list_lip_sync_versions(
        self, scope: AudioScope, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(lip_sync_versions, GeneratedAssetRow.asset_id.label("input_asset_id"))
                .outerjoin(
                    GeneratedAssetRow,
                    (GeneratedAssetRow.workspace_id == lip_sync_versions.c.workspace_id)
                    & (GeneratedAssetRow.project_id == lip_sync_versions.c.project_id)
                    & (GeneratedAssetRow.object_key == lip_sync_versions.c.input_video_key)
                    & (GeneratedAssetRow.media_type == "video"),
                )
                .where(*scope.criteria(lip_sync_versions))
                .order_by(lip_sync_versions.c.updated_at.desc(), lip_sync_versions.c.id.asc())
                .offset(offset)
                .limit(limit)
            )
            total = await session.scalar(
                select(func.count()).select_from(lip_sync_versions).where(*scope.criteria(lip_sync_versions))
            )
        return tuple(dict(row) for row in rows.mappings().all()), int(total or 0)

    async def list_audio_track_versions(
        self, scope: AudioScope, track_id: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._list_track_versions(audio_track_versions, scope, track_id, offset, limit)

    async def list_subtitle_track_versions(
        self, scope: AudioScope, track_id: str, *, offset: int = 0, limit: int = 50
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._list_track_versions(subtitle_track_versions, scope, track_id, offset, limit)

    async def delivery_snapshot(self, scope: AudioScope) -> Mapping[str, Any]:
        async with self._session_factory() as session:
            audio_rows = await session.execute(
                select(
                    audio_tracks.c.id.label("track_id"),
                    audio_tracks.c.track_kind,
                    audio_tracks.c.title,
                    audio_tracks.c.version.label("track_version"),
                    audio_track_versions.c.id.label("version_id"),
                    audio_track_versions.c.revision,
                    audio_track_versions.c.object_key,
                    audio_track_versions.c.object_sha256,
                    audio_track_versions.c.object_size_bytes,
                    audio_track_versions.c.mime_type,
                    audio_track_versions.c.cue_payload,
                )
                .join(
                    audio_track_versions,
                    (audio_track_versions.c.track_id == audio_tracks.c.id)
                    & (audio_track_versions.c.tenant_id == audio_tracks.c.tenant_id)
                    & (audio_track_versions.c.workspace_id == audio_tracks.c.workspace_id)
                    & (audio_track_versions.c.project_id == audio_tracks.c.project_id)
                    & (audio_track_versions.c.revision == audio_tracks.c.current_revision),
                )
                .where(*scope.criteria(audio_tracks), audio_tracks.c.state == "ready")
                .order_by(audio_tracks.c.track_kind.asc(), audio_tracks.c.id.asc())
            )
            subtitle_rows = await session.execute(
                select(
                    subtitle_tracks.c.id.label("track_id"),
                    subtitle_tracks.c.language,
                    subtitle_tracks.c.format,
                    subtitle_tracks.c.title,
                    subtitle_tracks.c.version.label("track_version"),
                    subtitle_track_versions.c.id.label("version_id"),
                    subtitle_track_versions.c.revision,
                    subtitle_track_versions.c.object_key,
                    subtitle_track_versions.c.object_sha256,
                    subtitle_track_versions.c.object_size_bytes,
                    subtitle_track_versions.c.mime_type,
                    subtitle_track_versions.c.cues,
                )
                .join(
                    subtitle_track_versions,
                    (subtitle_track_versions.c.track_id == subtitle_tracks.c.id)
                    & (subtitle_track_versions.c.tenant_id == subtitle_tracks.c.tenant_id)
                    & (subtitle_track_versions.c.workspace_id == subtitle_tracks.c.workspace_id)
                    & (subtitle_track_versions.c.project_id == subtitle_tracks.c.project_id)
                    & (subtitle_track_versions.c.revision == subtitle_tracks.c.current_revision),
                )
                .where(*scope.criteria(subtitle_tracks), subtitle_tracks.c.state == "ready")
                .order_by(subtitle_tracks.c.language.asc(), subtitle_tracks.c.id.asc())
            )
            selected_lip_sync = (
                await session.execute(
                    select(lip_sync_versions)
                    .where(*scope.criteria(lip_sync_versions), lip_sync_versions.c.is_selected.is_(True))
                    .order_by(lip_sync_versions.c.updated_at.desc(), lip_sync_versions.c.id.asc())
                    .limit(1)
                )
            ).mappings().one_or_none()
        return {
            "audioVersions": tuple(dict(row) for row in audio_rows.mappings().all()),
            "subtitleVersions": tuple(dict(row) for row in subtitle_rows.mappings().all()),
            "selectedLipSyncVersion": None if selected_lip_sync is None else dict(selected_lip_sync),
        }

    async def create_lip_sync_version(
        self,
        *,
        scope: AudioScope,
        version_id: str,
        audio_version_id: str,
        subtitle_version_id: str | None,
        input_video_key: str,
        calibration: Mapping[str, Any],
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        await self._assert_lip_sync_inputs(scope, audio_version_id, subtitle_version_id, input_video_key)
        return await self._create_scoped(
            table=lip_sync_versions,
            scope=scope,
            values={
                "id": version_id,
                **scope.values(),
                "audio_version_id": audio_version_id,
                "subtitle_version_id": subtitle_version_id,
                "input_video_key": input_video_key,
                "state": "pending",
                "calibration": dict(calibration),
                "created_by": actor_id,
            },
            operation="create_lip_sync_version",
            resource_type="lip_sync_version",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def validate_lip_sync_inputs(
        self,
        scope: AudioScope,
        *,
        audio_version_id: str,
        subtitle_version_id: str | None,
        input_video_key: str,
    ) -> None:
        await self._assert_lip_sync_inputs(
            scope, audio_version_id, subtitle_version_id, input_video_key
        )

    async def complete_lip_sync_version(
        self,
        *,
        scope: AudioScope,
        version_id: str,
        expected_version: int,
        source_task_id: str,
        output_key: str,
        output_sha256: str,
        output_size_bytes: int,
        mime_type: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        async with self._session_factory.begin() as session:
            replay = await self._claim_idempotency(
                session, scope, "complete_lip_sync_version", idempotency_key, request_fingerprint,
                "lip_sync_version", lip_sync_versions,
            )
            if replay is not None:
                return replay
            source = (
                await session.execute(
                    select(generation_tasks.c.id).where(
                        *scope.criteria(generation_tasks),
                        generation_tasks.c.id == source_task_id,
                        generation_tasks.c.task_kind == "lip_sync",
                        generation_tasks.c.resource_type == "lip_sync_version",
                        generation_tasks.c.resource_id == version_id,
                        generation_tasks.c.status == "succeeded",
                    )
                )
            ).scalar_one_or_none()
            if source is None:
                raise AudioPersistenceConflict("completed lip sync task is not available in this scope")
            result = await session.execute(
                update(lip_sync_versions)
                .where(
                    *scope.criteria(lip_sync_versions), lip_sync_versions.c.id == version_id,
                    lip_sync_versions.c.version == expected_version,
                    lip_sync_versions.c.state.in_(("pending", "failed")),
                )
                .values(
                    state="ready", source_task_id=source_task_id, output_video_key=output_key,
                    output_sha256=output_sha256, output_size_bytes=output_size_bytes, mime_type=mime_type,
                    failure_metadata=None, version=lip_sync_versions.c.version + 1,
                )
                .returning(*lip_sync_versions.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                await self._raise_missing_or_version_conflict(session, lip_sync_versions, scope, version_id)
            await session.execute(
                update(idempotency_records)
                .where(
                    *scope.criteria(idempotency_records),
                    idempotency_records.c.operation == "complete_lip_sync_version",
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
                .values(resource_id=version_id, response_metadata={"resource_id": version_id})
            )
            await self._audit(session, scope, request_id, "complete_lip_sync_version", actor_id, "lip_sync_version", version_id, "succeeded")
            return dict(cast(Mapping[str, Any], row))

    async def create_lip_sync_fallback(
        self,
        *,
        scope: AudioScope,
        version_id: str,
        fallback_of_id: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        async with self._session_factory() as session:
            base = (
                await session.execute(
                    select(lip_sync_versions).where(*scope.criteria(lip_sync_versions), lip_sync_versions.c.id == fallback_of_id)
                )
            ).mappings().one_or_none()
        if base is None:
            raise AudioPersistenceNotFound("scoped resource was not found")
        return await self._create_scoped(
            table=lip_sync_versions,
            scope=scope,
            values={
                "id": version_id,
                **scope.values(),
                "audio_version_id": base["audio_version_id"],
                "subtitle_version_id": base["subtitle_version_id"],
                "input_video_key": base["input_video_key"],
                "state": "fallback",
                "calibration": base["calibration"],
                "fallback_of_id": fallback_of_id,
                "created_by": actor_id,
            },
            operation="create_lip_sync_fallback",
            resource_type="lip_sync_version",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def select_lip_sync_version(
        self,
        *,
        scope: AudioScope,
        version_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        async with self._session_factory.begin() as session:
            replay = await self._claim_idempotency(
                session, scope, "select_lip_sync_version", idempotency_key, request_fingerprint,
                "lip_sync_version", lip_sync_versions,
            )
            if replay is not None:
                return replay
            target = (
                await session.execute(
                    select(lip_sync_versions)
                    .where(*scope.criteria(lip_sync_versions), lip_sync_versions.c.id == version_id)
                    .with_for_update()
                )
            ).mappings().one_or_none()
            if target is None:
                raise AudioPersistenceNotFound("scoped resource was not found")
            if target["version"] != expected_version:
                raise VersionConflict("expected version does not match authoritative version")
            if target["state"] not in {"ready", "fallback"}:
                raise AudioPersistenceConflict("lip sync version is not selectable")
            await session.execute(
                update(lip_sync_versions)
                .where(
                    *scope.criteria(lip_sync_versions),
                    lip_sync_versions.c.input_video_key == target["input_video_key"],
                    lip_sync_versions.c.id != version_id, lip_sync_versions.c.is_selected.is_(True),
                )
                .values(is_selected=False, selected_by=None, selected_at=None, version=lip_sync_versions.c.version + 1)
            )
            result = await session.execute(
                update(lip_sync_versions)
                .where(*scope.criteria(lip_sync_versions), lip_sync_versions.c.id == version_id, lip_sync_versions.c.version == expected_version)
                .values(is_selected=True, selected_by=actor_id, selected_at=func.now(), version=lip_sync_versions.c.version + 1)
                .returning(*lip_sync_versions.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise VersionConflict("expected version does not match authoritative version")
            await session.execute(
                update(idempotency_records)
                .where(
                    *scope.criteria(idempotency_records),
                    idempotency_records.c.operation == "select_lip_sync_version",
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
                .values(resource_id=version_id, response_metadata={"resource_id": version_id})
            )
            await self._audit(session, scope, request_id, "select_lip_sync_version", actor_id, "lip_sync_version", version_id, "succeeded")
            return dict(cast(Mapping[str, Any], row))

    async def create_audio_track(
        self,
        *,
        scope: AudioScope,
        track_id: str,
        track_kind: str,
        title: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        values = {
            "id": track_id,
            **scope.values(),
            "track_kind": track_kind,
            "title": title,
            "created_by": actor_id,
        }
        return await self._create_scoped(
            table=audio_tracks,
            scope=scope,
            values=values,
            operation="create_audio_track",
            resource_type="audio_track",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def list_generation_tasks(
        self,
        scope: AudioScope,
        *,
        status: str | None = None,
        task_kind: str | None = None,
        query: str | None = None,
        offset: int,
        limit: int,
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        criteria = list(scope.criteria(generation_tasks))
        if status:
            criteria.append(generation_tasks.c.status == status)
        if task_kind:
            criteria.append(generation_tasks.c.task_kind == task_kind)
        if query:
            pattern = f"%{query}%"
            criteria.append(
                or_(
                    generation_tasks.c.id.ilike(pattern),
                    generation_tasks.c.resource_id.ilike(pattern),
                    generation_tasks.c.provider_task_id.ilike(pattern),
                )
            )
        async with self._session_factory() as session:
            rows = await session.execute(
                select(
                    generation_tasks,
                    audio_billing_holds.c.currency.label("billing_currency"),
                    audio_billing_holds.c.estimated_minor,
                    audio_billing_holds.c.actual_minor,
                    audio_billing_holds.c.released_minor,
                    audio_billing_holds.c.pricing_version,
                    audio_billing_holds.c.status.label("billing_status"),
                )
                .join(
                    audio_billing_holds,
                    (audio_billing_holds.c.task_id == generation_tasks.c.id)
                    & (audio_billing_holds.c.tenant_id == generation_tasks.c.tenant_id)
                    & (audio_billing_holds.c.workspace_id == generation_tasks.c.workspace_id)
                    & (audio_billing_holds.c.project_id == generation_tasks.c.project_id),
                )
                .where(*criteria)
                .order_by(generation_tasks.c.updated_at.desc(), generation_tasks.c.id.asc())
                .offset(offset)
                .limit(limit)
            )
            total = await session.scalar(
                select(func.count()).select_from(generation_tasks).where(*criteria)
            )
        return tuple(dict(row) for row in rows.mappings().all()), int(total or 0)

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
        criteria = list(scope.criteria(audit_events))
        for column, value in (
            (audit_events.c.request_id, request_id),
            (audit_events.c.actor_id, actor_id),
            (audit_events.c.object_type, object_type),
            (audit_events.c.object_id, object_id),
        ):
            if value:
                criteria.append(column == value)
        async with self._session_factory() as session:
            rows = await session.execute(
                select(audit_events)
                .where(*criteria)
                .order_by(audit_events.c.created_at.desc(), audit_events.c.id.asc())
                .offset(offset)
                .limit(limit)
            )
            total = await session.scalar(select(func.count()).select_from(audit_events).where(*criteria))
        return tuple(dict(row) for row in rows.mappings().all()), int(total or 0)

    async def create_subtitle_track(
        self,
        *,
        scope: AudioScope,
        track_id: str,
        language: str,
        format: str,
        title: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> Mapping[str, Any]:
        values = {
            "id": track_id,
            **scope.values(),
            "language": language,
            "format": format,
            "title": title,
            "created_by": actor_id,
        }
        return await self._create_scoped(
            table=subtitle_tracks,
            scope=scope,
            values=values,
            operation="create_subtitle_track",
            resource_type="subtitle_track",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
        )

    async def append_audio_version(
        self,
        *,
        scope: AudioScope,
        track_id: str,
        version_id: str,
        expected_version: int,
        cue_payload: Mapping[str, Any],
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        object_key: str | None = None,
        object_sha256: str | None = None,
        object_size_bytes: int | None = None,
        mime_type: str | None = None,
        source_task_id: str | None = None,
    ) -> Mapping[str, Any]:
        return await self._append_version(
            aggregate=audio_tracks,
            versions=audio_track_versions,
            scope=scope,
            track_id=track_id,
            version_id=version_id,
            expected_version=expected_version,
            payload_column="cue_payload",
            payload=cue_payload,
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            operation="append_audio_version",
            resource_type="audio_track_version",
            object_key=object_key,
            object_sha256=object_sha256,
            object_size_bytes=object_size_bytes,
            mime_type=mime_type,
            source_task_id=source_task_id,
        )

    async def append_subtitle_version(
        self,
        *,
        scope: AudioScope,
        track_id: str,
        version_id: str,
        expected_version: int,
        cues: Mapping[str, Any],
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        object_key: str | None = None,
        object_sha256: str | None = None,
        object_size_bytes: int | None = None,
        mime_type: str | None = None,
        source_task_id: str | None = None,
    ) -> Mapping[str, Any]:
        return await self._append_version(
            aggregate=subtitle_tracks,
            versions=subtitle_track_versions,
            scope=scope,
            track_id=track_id,
            version_id=version_id,
            expected_version=expected_version,
            payload_column="cues",
            payload=cues,
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            operation="append_subtitle_version",
            resource_type="subtitle_track_version",
            object_key=object_key,
            object_sha256=object_sha256,
            object_size_bytes=object_size_bytes,
            mime_type=mime_type,
            source_task_id=source_task_id,
        )

    async def create_generation_task(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        task_kind: str,
        resource_type: str,
        resource_id: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        max_attempts: int = 1,
        estimated_minor: int,
        currency: str,
        pricing_version: str,
        timeout_at: datetime | None = None,
    ) -> Mapping[str, Any]:
        deadline = timeout_at or datetime.now(UTC) + timedelta(hours=6)
        if (
            estimated_minor <= 0
            or len(currency.strip()) != 3
            or not pricing_version.strip()
            or deadline.tzinfo is None
            or deadline.utcoffset() is None
        ):
            raise AudioBillingError("AUDIO_BILLING_POLICY_INVALID")
        await self._assert_task_resource(scope, resource_type, resource_id)
        values = {
            "id": task_id,
            **scope.values(),
            "task_kind": task_kind,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "status": "pending",
            "created_by": actor_id,
            "idempotency_key": idempotency_key,
            "request_fingerprint": request_fingerprint,
            "max_attempts": max_attempts,
            "timeout_at": deadline,
        }
        normalized_currency = currency.strip().upper()
        async with self._session_factory.begin() as session:
            replay = await self._claim_idempotency(
                session,
                scope,
                "create_generation_task",
                idempotency_key,
                request_fingerprint,
                "generation_task",
                generation_tasks,
            )
            if replay is not None:
                return replay
            account = await session.get(GenerationBillingAccountRow, scope.workspace_id, with_for_update=True)
            if account is None:
                raise AudioBillingError("AUDIO_BILLING_ACCOUNT_MISSING")
            if account.currency != normalized_currency:
                raise AudioBillingError("AUDIO_BILLING_CURRENCY_MISMATCH")
            if account.available_minor < estimated_minor:
                raise AudioBillingError("AUDIO_INSUFFICIENT_CREDITS")
            result = await session.execute(insert(generation_tasks).values(**values).returning(*generation_tasks.c))
            row = dict(result.mappings().one())
            now = cast(datetime, row["created_at"])
            account.available_minor -= estimated_minor
            account.held_minor += estimated_minor
            account.version += 1
            account.updated_at = now
            await session.execute(
                insert(audio_billing_holds).values(
                    **scope.values(),
                    task_id=task_id,
                    currency=normalized_currency,
                    estimated_minor=estimated_minor,
                    pricing_version=pricing_version.strip(),
                )
            )
            await session.execute(
                insert(audio_billing_journals).values(
                    event_id=_billing_event_id("freeze", task_id),
                    **scope.values(),
                    task_id=task_id,
                    action="freeze",
                    currency=normalized_currency,
                    amount_minor=estimated_minor,
                    postings=[
                        {"account": "workspace.available", "amount_minor": -estimated_minor},
                        {"account": "workspace.held", "amount_minor": estimated_minor},
                    ],
                    reference=pricing_version.strip(),
                )
            )
            await session.execute(
                update(idempotency_records)
                .where(
                    *scope.criteria(idempotency_records),
                    idempotency_records.c.operation == "create_generation_task",
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
                .values(resource_id=task_id, response_metadata={"resource_id": task_id})
            )
            await self._audit(
                session, scope, request_id, "create_generation_task", actor_id, "generation_task", task_id, "succeeded"
            )
            return row

    async def list_overdue_tasks(self, *, at: datetime, limit: int = 100) -> tuple[Mapping[str, Any], ...]:
        if at.tzinfo is None or at.utcoffset() is None or not 1 <= limit <= 1_000:
            raise ValueError("AUDIO_OVERDUE_QUERY_INVALID")
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(generation_tasks)
                    .join(
                        audio_billing_holds,
                        (audio_billing_holds.c.task_id == generation_tasks.c.id)
                        & (audio_billing_holds.c.tenant_id == generation_tasks.c.tenant_id)
                        & (audio_billing_holds.c.workspace_id == generation_tasks.c.workspace_id)
                        & (audio_billing_holds.c.project_id == generation_tasks.c.project_id),
                    )
                    .where(
                        generation_tasks.c.status.in_(
                            ("pending", "queued", "running", "retrying", "cancelling", "failed")
                        ),
                        audio_billing_holds.c.status == "active",
                        generation_tasks.c.timeout_at <= at,
                    )
                    .order_by(generation_tasks.c.timeout_at, generation_tasks.c.id)
                    .limit(limit)
                )
            ).mappings()
            return tuple(dict(row) for row in rows)

    async def transition_task(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        status: str,
        actor_id: str,
        request_id: str,
        failure_metadata: Mapping[str, Any] | None = None,
        cancellation_metadata: Mapping[str, Any] | None = None,
        fallback_metadata: Mapping[str, Any] | None = None,
        result_metadata: Mapping[str, Any] | None = None,
        provider_name: str | None = None,
        provider_task_id: str | None = None,
        attempt_count: int | None = None,
        finished_at: datetime | None = None,
        billing_event_id: str | None = None,
        actual_minor: int | None = None,
        billing_currency: str | None = None,
        release_billing: bool = False,
    ) -> Mapping[str, Any]:
        values: dict[str, Any] = {
            "status": status,
            "version": generation_tasks.c.version + 1,
        }
        optional_values = {
            "failure_metadata": failure_metadata,
            "cancellation_metadata": cancellation_metadata,
            "fallback_metadata": fallback_metadata,
            "result_metadata": result_metadata,
            "provider_name": provider_name,
            "provider_task_id": provider_task_id,
            "attempt_count": attempt_count,
            "finished_at": finished_at,
        }
        values.update({name: value for name, value in optional_values.items() if value is not None})
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(generation_tasks)
                .where(*scope.criteria(generation_tasks), generation_tasks.c.id == task_id, generation_tasks.c.version == expected_version)
                .values(**values)
                .returning(*generation_tasks.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                await self._raise_missing_or_version_conflict(session, generation_tasks, scope, task_id)
            if status == "succeeded":
                if billing_event_id is None or actual_minor is None or billing_currency is None:
                    raise AudioBillingError("AUDIO_BILLING_SETTLEMENT_REQUIRED")
                await self._close_task_billing(
                    session,
                    scope,
                    task_id=task_id,
                    event_id=billing_event_id,
                    actual_minor=actual_minor,
                    currency=billing_currency,
                    release=False,
                )
            elif release_billing:
                if billing_event_id is None:
                    raise AudioBillingError("AUDIO_BILLING_RELEASE_EVENT_REQUIRED")
                await self._close_task_billing(
                    session,
                    scope,
                    task_id=task_id,
                    event_id=billing_event_id,
                    actual_minor=0,
                    currency=None,
                    release=True,
                )
            await self._audit(session, scope, request_id, "transition_generation_task", actor_id, "generation_task", task_id, "succeeded")
            return dict(cast(Mapping[str, Any], row))

    async def begin_task_cancellation(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        target_status: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        cancellation_metadata: Mapping[str, Any],
        finished_at: datetime | None = None,
    ) -> tuple[Mapping[str, Any], bool]:
        """Persist a cancellation intent once before any Provider call.

        The returned boolean is true only for an idempotent replay.  A replay
        never sends a second cancellation request to a remote provider.
        """
        operation = "cancel_generation_task"
        async with self._session_factory.begin() as session:
            inserted = await session.execute(
                pg_insert(idempotency_records)
                .values(
                    **scope.values(),
                    operation=operation,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    resource_type="generation_task",
                )
                .on_conflict_do_nothing()
                .returning(idempotency_records.c.idempotency_key)
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    await session.execute(
                        select(idempotency_records).where(
                            *scope.criteria(idempotency_records),
                            idempotency_records.c.operation == operation,
                            idempotency_records.c.idempotency_key == idempotency_key,
                        )
                    )
                ).mappings().one()
                if existing["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with different request content")
                if existing["resource_id"] is None:
                    raise AudioPersistenceConflict("cancellation request is still being recorded")
                replay = await session.execute(
                    select(generation_tasks).where(
                        *scope.criteria(generation_tasks), generation_tasks.c.id == existing["resource_id"]
                    )
                )
                row = replay.mappings().one_or_none()
                if row is None:
                    raise AudioPersistenceConflict("cancelled task is no longer available")
                return dict(row), True

            values: dict[str, Any] = {
                "status": target_status,
                "version": generation_tasks.c.version + 1,
                "cancellation_metadata": dict(cancellation_metadata),
            }
            if finished_at is not None:
                values["finished_at"] = finished_at
            result = await session.execute(
                update(generation_tasks)
                .where(
                    *scope.criteria(generation_tasks),
                    generation_tasks.c.id == task_id,
                    generation_tasks.c.version == expected_version,
                )
                .values(**values)
                .returning(*generation_tasks.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                await self._raise_missing_or_version_conflict(session, generation_tasks, scope, task_id)
            await session.execute(
                update(idempotency_records)
                .where(
                    *scope.criteria(idempotency_records),
                    idempotency_records.c.operation == operation,
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
                .values(resource_id=task_id, response_metadata={"resource_id": task_id})
            )
            if target_status == "cancelled":
                await self._close_task_billing(
                    session,
                    scope,
                    task_id=task_id,
                    event_id=request_id,
                    actual_minor=0,
                    currency=None,
                    release=True,
                )
            await self._audit(session, scope, request_id, operation, actor_id, "generation_task", task_id, "succeeded")
            return dict(cast(Mapping[str, Any], row)), False

    async def replay_task_cancellation(
        self, *, scope: AudioScope, idempotency_key: str, request_fingerprint: str
    ) -> Mapping[str, Any] | None:
        """Return a prior cancellation result without changing task state."""
        async with self._session_factory() as session:
            existing = (
                await session.execute(
                    select(idempotency_records).where(
                        *scope.criteria(idempotency_records),
                        idempotency_records.c.operation == "cancel_generation_task",
                        idempotency_records.c.idempotency_key == idempotency_key,
                    )
                )
            ).mappings().one_or_none()
            if existing is None:
                return None
            if existing["request_fingerprint"] != request_fingerprint:
                raise IdempotencyConflict("idempotency key was reused with different request content")
            if existing["resource_id"] is None:
                raise AudioPersistenceConflict("cancellation request is still being recorded")
            result = await session.execute(
                select(generation_tasks).where(
                    *scope.criteria(generation_tasks), generation_tasks.c.id == existing["resource_id"]
                )
            )
            row = result.mappings().one_or_none()
            if row is None:
                raise AudioPersistenceConflict("cancelled task is no longer available")
            return dict(cast(Mapping[str, Any], row))

    async def begin_task_retry(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[Mapping[str, Any], bool]:
        """Claim one failed task retry atomically before submitting it again."""
        return await self._begin_task_decision(
            scope=scope,
            task_id=task_id,
            expected_version=expected_version,
            target_status="retrying",
            expected_statuses=("failed", "timed_out"),
            operation="retry_generation_task",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            metadata_column=None,
            metadata=None,
            clear_finished_at=True,
            require_remaining_attempt=True,
        )

    async def record_task_fallback(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        fallback_metadata: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], bool]:
        """Audit the user's choice to keep the original media after a failed task."""
        return await self._begin_task_decision(
            scope=scope,
            task_id=task_id,
            expected_version=expected_version,
            target_status="failed",
            expected_statuses=("failed", "timed_out"),
            operation="record_generation_task_fallback",
            actor_id=actor_id,
            request_id=request_id,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            metadata_column="fallback_metadata",
            metadata=fallback_metadata,
            clear_finished_at=False,
            require_remaining_attempt=False,
        )

    async def _begin_task_decision(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        target_status: str,
        expected_statuses: tuple[str, ...],
        operation: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        request_fingerprint: str,
        metadata_column: str | None,
        metadata: Mapping[str, Any] | None,
        clear_finished_at: bool,
        require_remaining_attempt: bool,
    ) -> tuple[Mapping[str, Any], bool]:
        async with self._session_factory.begin() as session:
            inserted = await session.execute(
                pg_insert(idempotency_records)
                .values(
                    **scope.values(), operation=operation, idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint, resource_type="generation_task",
                )
                .on_conflict_do_nothing()
                .returning(idempotency_records.c.idempotency_key)
            )
            if inserted.scalar_one_or_none() is None:
                existing = (
                    await session.execute(
                        select(idempotency_records).where(
                            *scope.criteria(idempotency_records),
                            idempotency_records.c.operation == operation,
                            idempotency_records.c.idempotency_key == idempotency_key,
                        )
                    )
                ).mappings().one()
                if existing["request_fingerprint"] != request_fingerprint:
                    raise IdempotencyConflict("idempotency key was reused with different request content")
                if existing["resource_id"] is None:
                    raise AudioPersistenceConflict("task operation is still being recorded")
                replay = await session.execute(
                    select(generation_tasks).where(
                        *scope.criteria(generation_tasks), generation_tasks.c.id == existing["resource_id"]
                    )
                )
                row = replay.mappings().one_or_none()
                if row is None:
                    raise AudioPersistenceConflict("task operation result is no longer available")
                return dict(row), True

            values: dict[str, Any] = {"status": target_status, "version": generation_tasks.c.version + 1}
            if metadata_column and metadata is not None:
                values[metadata_column] = dict(metadata)
            if clear_finished_at:
                values["finished_at"] = None
            where = [
                *scope.criteria(generation_tasks), generation_tasks.c.id == task_id,
                generation_tasks.c.version == expected_version, generation_tasks.c.status.in_(expected_statuses),
            ]
            if require_remaining_attempt:
                where.append(generation_tasks.c.attempt_count < generation_tasks.c.max_attempts)
            result = await session.execute(update(generation_tasks).where(*where).values(**values).returning(*generation_tasks.c))
            row = result.mappings().one_or_none()
            if row is None:
                current = (
                    await session.execute(
                        select(generation_tasks).where(*scope.criteria(generation_tasks), generation_tasks.c.id == task_id)
                    )
                ).mappings().one_or_none()
                if current is None:
                    raise AudioPersistenceNotFound("scoped resource was not found")
                if current["version"] != expected_version:
                    raise VersionConflict("expected version does not match authoritative version")
                raise AudioPersistenceConflict("task state cannot accept this operation")
            await session.execute(
                update(idempotency_records)
                .where(
                    *scope.criteria(idempotency_records),
                    idempotency_records.c.operation == operation,
                    idempotency_records.c.idempotency_key == idempotency_key,
                )
                .values(resource_id=task_id, response_metadata={"resource_id": task_id})
            )
            if operation == "retry_generation_task":
                await self._reactivate_task_billing(
                    session, scope, task_id=task_id, event_id=request_id
                )
            elif operation == "record_generation_task_fallback":
                await self._close_task_billing(
                    session,
                    scope,
                    task_id=task_id,
                    event_id=request_id,
                    actual_minor=0,
                    currency=None,
                    release=True,
                )
            await self._audit(session, scope, request_id, operation, actor_id, "generation_task", task_id, "succeeded")
            return dict(row), False

    async def claim_task_lease(
        self,
        *,
        scope: AudioScope,
        task_id: str,
        expected_version: int,
        worker_id: str,
        lease_expires_at: datetime,
        request_id: str,
        actor_id: str,
    ) -> Mapping[str, Any]:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                update(generation_tasks)
                .where(
                    *scope.criteria(generation_tasks),
                    generation_tasks.c.id == task_id,
                    generation_tasks.c.version == expected_version,
                    generation_tasks.c.status.in_(("pending", "queued", "retrying")),
                )
                .values(
                    status="running",
                    lease_owner=worker_id,
                    lease_expires_at=lease_expires_at,
                    attempt_count=generation_tasks.c.attempt_count + 1,
                    version=generation_tasks.c.version + 1,
                )
                .returning(*generation_tasks.c)
            )
            row = result.mappings().one_or_none()
            if row is None:
                await self._raise_missing_or_version_conflict(session, generation_tasks, scope, task_id)
            await self._audit(session, scope, request_id, "claim_generation_task_lease", actor_id, "generation_task", task_id, "succeeded")
            return dict(cast(Mapping[str, Any], row))

    async def _get_scoped(self, table: Any, scope: AudioScope, object_id: str) -> Mapping[str, Any]:
        async with self._session_factory() as session:
            result = await session.execute(select(table).where(*scope.criteria(table), table.c.id == object_id))
            row = result.mappings().one_or_none()
            if row is None:
                raise AudioPersistenceNotFound("scoped resource was not found")
            return dict(row)

    async def _assert_lip_sync_inputs(
        self, scope: AudioScope, audio_version_id: str, subtitle_version_id: str | None, input_video_key: str
    ) -> None:
        if not input_video_key.strip():
            raise AudioPersistenceNotFound("scoped video asset was not found")
        async with self._session_factory() as session:
            audio = await session.scalar(
                select(audio_track_versions.c.id).where(
                    *scope.criteria(audio_track_versions), audio_track_versions.c.id == audio_version_id
                )
            )
            if audio is None:
                raise AudioPersistenceNotFound("scoped audio version was not found")
            if subtitle_version_id:
                subtitle = await session.scalar(
                    select(subtitle_track_versions.c.id).where(
                        *scope.criteria(subtitle_track_versions), subtitle_track_versions.c.id == subtitle_version_id
                    )
                )
                if subtitle is None:
                    raise AudioPersistenceNotFound("scoped subtitle version was not found")
            video = await session.scalar(
                select(GeneratedAssetRow.asset_id).where(
                    GeneratedAssetRow.workspace_id == scope.workspace_id,
                    GeneratedAssetRow.project_id == scope.project_id,
                    GeneratedAssetRow.object_key == input_video_key,
                    GeneratedAssetRow.media_type == "video",
                )
            )
            if video is None:
                raise AudioPersistenceNotFound("scoped video asset was not found")

    async def _assert_task_resource(
        self, scope: AudioScope, resource_type: str, resource_id: str
    ) -> None:
        table = {
            "audio_track": audio_tracks,
            "subtitle_track": subtitle_tracks,
            "lip_sync_version": lip_sync_versions,
        }.get(resource_type)
        if table is None:
            raise AudioPersistenceNotFound("unsupported scoped task resource")
        async with self._session_factory() as session:
            exists = await session.scalar(
                select(table.c.id).where(
                    *scope.criteria(table), table.c.id == resource_id
                )
            )
        if exists is None:
            raise AudioPersistenceNotFound("scoped task resource was not found")

    async def _list_scoped(
        self, table: Any, scope: AudioScope, *, offset: int, limit: int
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        return await self._list_with_criteria(
            table, list(scope.criteria(table)), offset=offset, limit=limit
        )

    async def _list_with_criteria(
        self, table: Any, criteria: list[Any], *, offset: int, limit: int
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(table)
                .where(*criteria)
                .order_by(table.c.updated_at.desc(), table.c.id.asc())
                .offset(offset)
                .limit(limit)
            )
            total = await session.scalar(select(func.count()).select_from(table).where(*criteria))
        return tuple(dict(row) for row in rows.mappings().all()), int(total or 0)

    async def _list_track_versions(
        self, table: Any, scope: AudioScope, track_id: str, offset: int, limit: int
    ) -> tuple[tuple[Mapping[str, Any], ...], int]:
        async with self._session_factory() as session:
            rows = await session.execute(
                select(table)
                .where(*scope.criteria(table), table.c.track_id == track_id)
                .order_by(table.c.revision.desc(), table.c.id.asc())
                .offset(offset)
                .limit(limit)
            )
            total = await session.scalar(
                select(func.count()).select_from(table).where(*scope.criteria(table), table.c.track_id == track_id)
            )
        return tuple(dict(row) for row in rows.mappings().all()), int(total or 0)

    async def _create_scoped(self, *, table: Any, scope: AudioScope, values: Mapping[str, Any], operation: str, resource_type: str, actor_id: str, request_id: str, idempotency_key: str, request_fingerprint: str) -> Mapping[str, Any]:
        async with self._session_factory.begin() as session:
            replay = await self._claim_idempotency(
                session, scope, operation, idempotency_key, request_fingerprint, resource_type, table
            )
            if replay is not None:
                return replay
            result = await session.execute(insert(table).values(**values).returning(*table.c))
            row = dict(result.mappings().one())
            await session.execute(
                update(idempotency_records)
                .where(*scope.criteria(idempotency_records), idempotency_records.c.operation == operation, idempotency_records.c.idempotency_key == idempotency_key)
                .values(resource_id=row["id"], response_metadata={"resource_id": str(row["id"])})
            )
            await self._audit(session, scope, request_id, operation, actor_id, resource_type, row["id"], "succeeded")
            return row

    async def _append_version(self, *, aggregate: Any, versions: Any, scope: AudioScope, track_id: str, version_id: str, expected_version: int, payload_column: str, payload: Mapping[str, Any], actor_id: str, request_id: str, idempotency_key: str, request_fingerprint: str, operation: str, resource_type: str, object_key: str | None, object_sha256: str | None, object_size_bytes: int | None, mime_type: str | None, source_task_id: str | None) -> Mapping[str, Any]:
        async with self._session_factory.begin() as session:
            replay = await self._claim_idempotency(
                session, scope, operation, idempotency_key, request_fingerprint, resource_type, versions
            )
            if replay is not None:
                return replay
            aggregate_result = await session.execute(
                update(aggregate)
                .where(*scope.criteria(aggregate), aggregate.c.id == track_id, aggregate.c.version == expected_version)
                .values(
                    current_revision=aggregate.c.current_revision + 1,
                    version=aggregate.c.version + 1,
                    state="ready",
                )
                .returning(aggregate.c.current_revision)
            )
            revision = aggregate_result.scalar_one_or_none()
            if revision is None:
                await self._raise_missing_or_version_conflict(session, aggregate, scope, track_id)
            values = {
                "id": version_id,
                "track_id": track_id,
                **scope.values(),
                "revision": revision,
                payload_column: dict(payload),
                "object_key": object_key,
                "object_sha256": object_sha256,
                "object_size_bytes": object_size_bytes,
                "mime_type": mime_type,
                "source_task_id": source_task_id,
                "created_by": actor_id,
            }
            result = await session.execute(insert(versions).values(**values).returning(*versions.c))
            row = dict(result.mappings().one())
            await session.execute(
                update(idempotency_records)
                .where(*scope.criteria(idempotency_records), idempotency_records.c.operation == operation, idempotency_records.c.idempotency_key == idempotency_key)
                .values(resource_id=version_id, response_metadata={"resource_id": version_id, "revision": revision})
            )
            await self._audit(session, scope, request_id, operation, actor_id, resource_type, version_id, "succeeded")
            return row

    async def _claim_idempotency(
        self,
        session: AsyncSession,
        scope: AudioScope,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
        resource_type: str,
        result_table: Any,
    ) -> Mapping[str, Any] | None:
        statement = pg_insert(idempotency_records).values(
            **scope.values(), operation=operation, idempotency_key=idempotency_key, request_fingerprint=request_fingerprint, resource_type=resource_type
        ).on_conflict_do_nothing().returning(idempotency_records.c.idempotency_key)
        inserted = (await session.execute(statement)).scalar_one_or_none()
        if inserted is not None:
            return None
        existing = (await session.execute(select(idempotency_records).where(*scope.criteria(idempotency_records), idempotency_records.c.operation == operation, idempotency_records.c.idempotency_key == idempotency_key))).mappings().one()
        if existing["request_fingerprint"] != request_fingerprint:
            raise IdempotencyConflict("idempotency key was reused with different request content")
        resource_id = existing["resource_id"]
        if resource_id is None:
            raise AudioPersistenceConflict("idempotent operation is still in progress")
        result = await session.execute(
            select(result_table).where(*scope.criteria(result_table), result_table.c.id == resource_id)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise AudioPersistenceConflict("idempotent result is no longer available")
        return dict(row)

    async def _raise_missing_or_version_conflict(self, session: AsyncSession, table: Any, scope: AudioScope, object_id: str) -> None:
        exists = await session.scalar(select(table.c.id).where(*scope.criteria(table), table.c.id == object_id))
        if exists is None:
            raise AudioPersistenceNotFound("scoped resource was not found")
        raise VersionConflict("expected version does not match authoritative version")

    async def _close_task_billing(
        self,
        session: AsyncSession,
        scope: AudioScope,
        *,
        task_id: str,
        event_id: str,
        actual_minor: int,
        currency: str | None,
        release: bool,
    ) -> None:
        hold = (
            await session.execute(
                select(audio_billing_holds)
                .where(*scope.criteria(audio_billing_holds), audio_billing_holds.c.task_id == task_id)
                .with_for_update()
            )
        ).mappings().one_or_none()
        account = await session.get(GenerationBillingAccountRow, scope.workspace_id, with_for_update=True)
        if hold is None or account is None:
            raise AudioBillingError("AUDIO_BILLING_HOLD_MISSING")
        target_status = "released" if release else "settled"
        if hold["status"] != "active":
            if hold["status"] == "released" and release:
                return
            if hold["status"] == target_status and hold["terminal_event_id"] == event_id:
                return
            raise AudioBillingError("AUDIO_BILLING_TERMINAL_CONFLICT")
        normalized_currency = str(hold["currency"])
        if not release and (
            currency is None
            or currency.strip().upper() != normalized_currency
            or actual_minor < 0
            or actual_minor > int(hold["estimated_minor"])
        ):
            raise AudioBillingError("AUDIO_BILLING_SETTLEMENT_INVALID")
        estimated = int(hold["estimated_minor"])
        charged = 0 if release else actual_minor
        released = estimated - charged
        account.held_minor -= estimated
        account.available_minor += released
        account.spent_minor += charged
        account.version += 1
        account.updated_at = datetime.now(UTC)
        await session.execute(
            update(audio_billing_holds)
            .where(*scope.criteria(audio_billing_holds), audio_billing_holds.c.task_id == task_id)
            .values(
                actual_minor=charged,
                released_minor=released,
                status=target_status,
                terminal_event_id=event_id,
                updated_at=func.now(),
            )
        )
        action = "release" if release else "settle"
        await session.execute(
            insert(audio_billing_journals).values(
                event_id=_billing_event_id(action, event_id),
                **scope.values(),
                task_id=task_id,
                action=action,
                currency=normalized_currency,
                amount_minor=estimated if release else charged,
                postings=[
                    {"account": "workspace.held", "amount_minor": -estimated},
                    {"account": "workspace.available", "amount_minor": released},
                    *([] if release else [{"account": "platform.revenue", "amount_minor": charged}]),
                ],
                reference=event_id,
            )
        )

    async def _reactivate_task_billing(
        self,
        session: AsyncSession,
        scope: AudioScope,
        *,
        task_id: str,
        event_id: str,
    ) -> None:
        hold = (
            await session.execute(
                select(audio_billing_holds)
                .where(*scope.criteria(audio_billing_holds), audio_billing_holds.c.task_id == task_id)
                .with_for_update()
            )
        ).mappings().one_or_none()
        if hold is None:
            raise AudioBillingError("AUDIO_BILLING_HOLD_MISSING")
        if hold["status"] == "active":
            return
        if hold["status"] != "released":
            raise AudioBillingError("AUDIO_BILLING_TERMINAL_CONFLICT")
        estimated = int(hold["estimated_minor"])
        account = await session.get(GenerationBillingAccountRow, scope.workspace_id, with_for_update=True)
        if account is None:
            raise AudioBillingError("AUDIO_BILLING_ACCOUNT_MISSING")
        if account.currency != hold["currency"] or account.available_minor < estimated:
            raise AudioBillingError("AUDIO_INSUFFICIENT_CREDITS")
        account.available_minor -= estimated
        account.held_minor += estimated
        account.version += 1
        account.updated_at = datetime.now(UTC)
        await session.execute(
            update(audio_billing_holds)
            .where(*scope.criteria(audio_billing_holds), audio_billing_holds.c.task_id == task_id)
            .values(
                actual_minor=0,
                released_minor=0,
                status="active",
                terminal_event_id=None,
                updated_at=func.now(),
            )
        )
        await session.execute(
            insert(audio_billing_journals).values(
                event_id=_billing_event_id("refreeze", event_id),
                **scope.values(),
                task_id=task_id,
                action="freeze",
                currency=str(hold["currency"]),
                amount_minor=estimated,
                postings=[
                    {"account": "workspace.available", "amount_minor": -estimated},
                    {"account": "workspace.held", "amount_minor": estimated},
                ],
                reference=event_id,
            )
        )

    async def _audit(self, session: AsyncSession, scope: AudioScope, request_id: str, operation: str, actor_id: str, object_type: str, object_id: str, result: str) -> None:
        await session.execute(
            insert(audit_events).values(
                id=_uuid7(), **scope.values(), request_id=request_id, operation=operation, actor_id=actor_id,
                object_type=object_type, object_id=object_id, result=result,
            )
        )
