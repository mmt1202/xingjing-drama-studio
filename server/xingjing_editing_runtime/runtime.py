"""Production-only composition for M08 editing and final-render HTTP routes."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from inspect import isawaitable
from pathlib import Path
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_audio_persistence.models import (
    audio_track_versions,
    audio_tracks,
    subtitle_track_versions,
    subtitle_tracks,
)
from server.xingjing_editing import (
    AccessContext,
    EditingError,
    Permission,
    RenderService,
    SourceMediaVersion,
    TimelineService,
    TrackKind,
    Uuid7IdGenerator,
)
from server.xingjing_editing_http import create_editing_dependencies, create_editing_router
from server.xingjing_editing_http.callback_auth import HmacRenderCallbackContextResolver
from server.xingjing_editing_http.renderer import HttpRendererGateway
from server.xingjing_editing_persistence import (
    SqlAlchemyAuditRecorder,
    SqlAlchemyRenderRepository,
    SqlAlchemyTimelineRepository,
)
from server.xingjing_editing_storage import LocalRenderObjectStorage
from server.xingjing_generation_persistence.repository import GeneratedAssetRow
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow

TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]
logger = logging.getLogger(__name__)


class EditingRuntimeConfigurationError(RuntimeError):
    """Raised when the production-only M08 composition lacks a required dependency."""


class EditingRenderBillingPolicy:
    def __init__(self, *, currency: str, pricing_version: str, minor_per_megapixel_second: int) -> None:
        self.currency = currency
        self.pricing_version = pricing_version
        self._minor_per_megapixel_second = minor_per_megapixel_second

    def estimate_minor(self, *, duration_ms: int, width: int, height: int) -> int:
        megapixel_milliseconds = width * height * duration_ms
        divisor = 1_000_000 * 1_000
        return max(1, (megapixel_milliseconds * self._minor_per_megapixel_second + divisor - 1) // divisor)


class FFprobeRenderMediaProbe:
    """Reads a real media duration from ffprobe; absence or invalid output fails closed."""

    async def probe_duration_ms(self, path: Path) -> int | None:
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            output, _ = await process.communicate()
        except (OSError, subprocess.SubprocessError):
            return None
        if process.returncode != 0:
            return None
        try:
            duration_ms = round(float(output.decode("utf-8").strip()) * 1_000)
        except (UnicodeDecodeError, ValueError):
            return None
        return duration_ms if duration_ms >= 0 else None


class GeneratedAssetMediaVersionCatalog:
    """Exposes only verified, immutable M06 video outputs as M08 timeline sources."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def get_version(
        self, *, tenant_id: str, workspace_id: str, version_id: str
    ) -> SourceMediaVersion | None:
        async with self._session_factory() as session:
            asset = await session.scalar(
                select(GeneratedAssetRow)
                .join(
                    ProjectRow,
                    (ProjectRow.id == GeneratedAssetRow.project_id)
                    & (ProjectRow.workspace_id == GeneratedAssetRow.workspace_id),
                )
                .where(
                    GeneratedAssetRow.asset_id == version_id,
                    GeneratedAssetRow.workspace_id == workspace_id,
                    GeneratedAssetRow.media_type == "video",
                    ProjectRow.tenant_id == tenant_id,
                    ProjectRow.workspace_id == workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
        if asset is not None:
            raw_duration = asset.artifact_metadata.get("duration_ms")
            if isinstance(raw_duration, int) and not isinstance(raw_duration, bool) and raw_duration > 0:
                return SourceMediaVersion(
                    asset_id=asset.asset_id,
                    version_id=asset.asset_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    owner_project_id=asset.project_id,
                    content_sha256=asset.content_sha256,
                    duration_ms=raw_duration,
                    object_key=asset.object_key,
                    media_kind=TrackKind.VIDEO,
                )
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(
                        audio_tracks.c.id.label("track_id"),
                        audio_tracks.c.project_id,
                        audio_tracks.c.track_kind,
                        audio_track_versions.c.id.label("version_id"),
                        audio_track_versions.c.object_key,
                        audio_track_versions.c.object_sha256,
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
                    .where(
                        audio_track_versions.c.id == version_id,
                        audio_tracks.c.tenant_id == tenant_id,
                        audio_tracks.c.workspace_id == workspace_id,
                        audio_tracks.c.state == "ready",
                    )
                )
            ).mappings().one_or_none()
        if row is not None:
            return self._audio_source(row, tenant_id=tenant_id, workspace_id=workspace_id)
        async with self._session_factory() as session:
            subtitle_row = (
                await session.execute(
                    select(
                        subtitle_tracks.c.id.label("track_id"),
                        subtitle_tracks.c.project_id,
                        subtitle_track_versions.c.id.label("version_id"),
                        subtitle_track_versions.c.object_key,
                        subtitle_track_versions.c.object_sha256,
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
                    .where(
                        subtitle_track_versions.c.id == version_id,
                        subtitle_tracks.c.tenant_id == tenant_id,
                        subtitle_tracks.c.workspace_id == workspace_id,
                        subtitle_tracks.c.state == "ready",
                    )
                )
            ).mappings().one_or_none()
        return (
            None
            if subtitle_row is None
            else self._subtitle_source(subtitle_row, tenant_id=tenant_id, workspace_id=workspace_id)
        )

    async def list_versions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        offset: int,
        limit: int,
    ) -> tuple[SourceMediaVersion, ...]:
        async with self._session_factory() as session:
            assets = (
                await session.execute(
                    select(GeneratedAssetRow)
                    .join(
                        ProjectRow,
                        (ProjectRow.id == GeneratedAssetRow.project_id)
                        & (ProjectRow.workspace_id == GeneratedAssetRow.workspace_id),
                    )
                    .where(
                        GeneratedAssetRow.workspace_id == workspace_id,
                        GeneratedAssetRow.project_id == project_id,
                        GeneratedAssetRow.media_type == "video",
                        ProjectRow.tenant_id == tenant_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                    .order_by(GeneratedAssetRow.created_at.desc(), GeneratedAssetRow.asset_id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
        results: list[SourceMediaVersion] = []
        for asset in assets:
            raw_duration = asset.artifact_metadata.get("duration_ms")
            if not isinstance(raw_duration, int) or isinstance(raw_duration, bool) or raw_duration <= 0:
                continue
            results.append(SourceMediaVersion(
                asset_id=asset.asset_id,
                version_id=asset.asset_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                owner_project_id=asset.project_id,
                content_sha256=asset.content_sha256,
                duration_ms=raw_duration,
                object_key=asset.object_key,
                media_kind=TrackKind.VIDEO,
            ))
        async with self._session_factory() as session:
            audio_rows = (
                await session.execute(
                    select(
                        audio_tracks.c.id.label("track_id"),
                        audio_tracks.c.project_id,
                        audio_tracks.c.track_kind,
                        audio_track_versions.c.id.label("version_id"),
                        audio_track_versions.c.object_key,
                        audio_track_versions.c.object_sha256,
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
                    .where(
                        audio_tracks.c.tenant_id == tenant_id,
                        audio_tracks.c.workspace_id == workspace_id,
                        audio_tracks.c.project_id == project_id,
                        audio_tracks.c.state == "ready",
                    )
                    .order_by(audio_tracks.c.updated_at.desc(), audio_tracks.c.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).mappings()
        for row in audio_rows:
            source = self._audio_source(row, tenant_id=tenant_id, workspace_id=workspace_id)
            if source is not None:
                results.append(source)
        async with self._session_factory() as session:
            subtitle_rows = (
                await session.execute(
                    select(
                        subtitle_tracks.c.id.label("track_id"),
                        subtitle_tracks.c.project_id,
                        subtitle_track_versions.c.id.label("version_id"),
                        subtitle_track_versions.c.object_key,
                        subtitle_track_versions.c.object_sha256,
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
                    .where(
                        subtitle_tracks.c.tenant_id == tenant_id,
                        subtitle_tracks.c.workspace_id == workspace_id,
                        subtitle_tracks.c.project_id == project_id,
                        subtitle_tracks.c.state == "ready",
                    )
                    .order_by(subtitle_tracks.c.updated_at.desc(), subtitle_tracks.c.id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).mappings()
        for row in subtitle_rows:
            source = self._subtitle_source(row, tenant_id=tenant_id, workspace_id=workspace_id)
            if source is not None:
                results.append(source)
        return tuple(results)

    @staticmethod
    def _audio_source(
        row,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> SourceMediaVersion | None:
        object_key = row["object_key"]
        content_sha256 = row["object_sha256"]
        payload = row["cue_payload"]
        duration_ms = payload.get("durationMs") if isinstance(payload, dict) else None
        kind = {
            "voice": TrackKind.VOICE,
            "bgm": TrackKind.BGM,
            "sfx": TrackKind.SFX,
        }.get(str(row["track_kind"]))
        if (
            kind is None
            or not isinstance(object_key, str)
            or not isinstance(content_sha256, str)
            or not isinstance(duration_ms, int)
            or isinstance(duration_ms, bool)
            or duration_ms <= 0
        ):
            return None
        return SourceMediaVersion(
            asset_id=str(row["track_id"]),
            version_id=str(row["version_id"]),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_project_id=str(row["project_id"]),
            content_sha256=content_sha256,
            duration_ms=duration_ms,
            object_key=object_key,
            media_kind=kind,
        )

    @staticmethod
    def _subtitle_source(
        row,
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> SourceMediaVersion | None:
        object_key = row["object_key"]
        content_sha256 = row["object_sha256"]
        cues = row["cues"]
        if (
            not isinstance(object_key, str)
            or not isinstance(content_sha256, str)
            or not isinstance(cues, list)
        ):
            return None
        ends: list[int] = []
        for cue in cues:
            if not isinstance(cue, dict):
                continue
            end_ms = cue.get("endMs")
            if isinstance(end_ms, int) and not isinstance(end_ms, bool):
                ends.append(end_ms)
        duration_ms = max(ends, default=0)
        if duration_ms <= 0:
            return None
        return SourceMediaVersion(
            asset_id=str(row["track_id"]),
            version_id=str(row["version_id"]),
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            owner_project_id=str(row["project_id"]),
            content_sha256=content_sha256,
            duration_ms=duration_ms,
            object_key=object_key,
            media_kind=TrackKind.SUBTITLE,
        )


class EditingRuntime:
    """Owns M08 SQLAlchemy services and exposes a router for the main application to mount."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        context_resolver: TrustedContextResolver,
        engine: AsyncEngine,
        renderer_url: str,
        renderer_token: str,
        object_storage_root: Path,
        callback_secret: str,
        billing_policy: EditingRenderBillingPolicy,
        timeout_sweep_interval_seconds: int = 30,
    ) -> None:
        self._session_factory = session_factory
        self._context_resolver = context_resolver
        self._engine = engine
        ids = Uuid7IdGenerator()
        timeline_repository = SqlAlchemyTimelineRepository(session_factory)
        render_repository = SqlAlchemyRenderRepository(session_factory)
        audit = SqlAlchemyAuditRecorder(session_factory)
        self._media_catalog = GeneratedAssetMediaVersionCatalog(session_factory)
        storage = LocalRenderObjectStorage(root=object_storage_root, media_probe=FFprobeRenderMediaProbe())
        renderer = HttpRendererGateway(base_url=renderer_url, service_token=renderer_token)
        render_service = RenderService(
            repository=render_repository,
            renderer=renderer,
            object_storage=storage,
            audit=audit,
            ids=ids,
            clock=_UtcClock(),
            billing_policy=billing_policy,
        )
        self._render_repository = render_repository
        self._render_service = render_service
        self._timeout_sweep_interval_seconds = timeout_sweep_interval_seconds
        self._timeout_sweeper: asyncio.Task[None] | None = None
        self._dependencies = create_editing_dependencies(
            timeline_service=TimelineService(
                repository=timeline_repository,
                media_catalog=self._media_catalog,
                audit=audit,
                ids=ids,
                clock=_UtcClock(),
            ),
            render_service=render_service,
            render_repository=render_repository,
            audit_reader=audit,
            access_context_resolver=self.access_context,
            callback_context_resolver=HmacRenderCallbackContextResolver(secret=callback_secret),
            project_scope_authorizer=self.authorize_project,
            render_input_validator=self.validate_render_input,
        )

    def router(self) -> APIRouter:
        return create_editing_router(self._dependencies)

    async def start(self) -> None:
        if self._timeout_sweeper is None:
            self._timeout_sweeper = asyncio.create_task(
                self._run_timeout_sweeper(), name="xingjing-m08-timeout-sweeper"
            )

    async def expire_overdue_tasks(self) -> int:
        tasks = await self._render_repository.list_overdue_render_tasks(at=datetime.now(UTC))
        expired = 0
        for task in tasks:
            context = AccessContext(
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                actor_id="system:m08-timeout-sweeper",
                request_id=f"timeout-sweep:{task.task_id}:{task.attempt}",
                permissions=frozenset({Permission.FINAL_MANAGE}),
            )
            try:
                result = await self._render_service.reconcile_timeout(context, task_id=task.task_id)
                expired += int(result.status.value == "failed")
            except (EditingError, SQLAlchemyError):
                logger.exception("M08 render timeout reconciliation failed task_id=%s", task.task_id)
        return expired

    async def _run_timeout_sweeper(self) -> None:
        try:
            while True:
                await self.expire_overdue_tasks()
                await asyncio.sleep(self._timeout_sweep_interval_seconds)
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        if self._timeout_sweeper is not None:
            self._timeout_sweeper.cancel()
            await asyncio.gather(self._timeout_sweeper, return_exceptions=True)
            self._timeout_sweeper = None
        await self._engine.dispose()

    async def _trusted_context(self, request: Request) -> TrustedWorkspaceContext:
        cached = getattr(request.state, "_xingjing_editing_trusted_context", None)
        if cached is not None:
            return cached
        resolved = self._context_resolver(request)
        trusted = (
            await cast(Awaitable[TrustedWorkspaceContext], resolved)
            if isawaitable(resolved)
            else cast(TrustedWorkspaceContext, resolved)
        )
        request.state._xingjing_editing_trusted_context = trusted
        return trusted

    async def access_context(self, request: Request) -> AccessContext:
        trusted = await self._trusted_context(request)
        return AccessContext(
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            actor_id=trusted.actor_id,
            request_id=trusted.request_id or str(uuid4()),
            permissions=frozenset(
                Permission(value) for value in trusted.permissions if value in Permission._value2member_map_
            ),
        )

    async def authorize_project(self, request: Request, project_id: str) -> bool:
        trusted = await self._trusted_context(request)
        try:
            async with self._session_factory() as session:
                return (
                    await session.scalar(
                        select(ProjectRow.id).where(
                            ProjectRow.id == project_id,
                            ProjectRow.tenant_id == trusted.tenant_id,
                            ProjectRow.workspace_id == trusted.workspace_id,
                            ProjectRow.deleted_at.is_(None),
                        )
                    )
                ) is not None
        except SQLAlchemyError as error:
            raise EditingRuntimeConfigurationError("EDITING_PROJECT_SCOPE_UNAVAILABLE") from error

    async def validate_render_input(
        self, request: Request, project_id: str, timeline_id: str, timeline_version_id: str
    ) -> None:
        """Verify every immutable clip still maps to the scoped M06 artifact recorded in the timeline."""
        context = await self.access_context(request)
        timeline = await self._dependencies.timeline_service.get_timeline(
            context, project_id=project_id, timeline_id=timeline_id
        )
        if timeline.version_id != timeline_version_id:
            raise EditingError("TIMELINE_VERSION_CONFLICT", "时间线版本已变化", status_code=409)
        clips = tuple(clip for track in timeline.tracks for clip in track.clips)
        if not clips or any(clip.source_object_key is None for clip in clips):
            raise EditingError("RENDER_INPUT_UNAVAILABLE", "时间线缺少可读取的媒体对象映射", status_code=503)
        for clip in clips:
            source = await self._media_catalog.get_version(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                version_id=clip.source_version_id,
            )
            if source is None or source.owner_project_id != project_id:
                raise EditingError("RENDER_INPUT_UNAVAILABLE", "时间线引用的媒体资产已不可用", status_code=503)
            if source.object_key != clip.source_object_key or source.content_sha256 != clip.source_sha256:
                raise EditingError("RENDER_INPUT_CHANGED", "媒体对象与不可变时间线摘要不一致", status_code=409)


class _UtcClock:
    def now(self):
        from datetime import UTC, datetime

        return datetime.now(UTC)


def create_production_editing_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
    renderer_url: str | None = None,
    renderer_token: str | None = None,
    object_storage_root: str | Path | None = None,
    callback_secret: str | None = None,
    billing_currency: str | None = None,
    billing_pricing_version: str | None = None,
    billing_minor_per_megapixel_second: int | None = None,
    timeout_sweep_interval_seconds: int | None = None,
) -> EditingRuntime:
    """Build M08 exclusively from real database, renderer, storage, and identity dependencies."""

    url = _required(
        database_url or os.environ.get("XINGJING_EDITING_DATABASE_URL"),
        "XINGJING_EDITING_DATABASE_URL_REQUIRED",
    )
    if "+aiosqlite" not in url and "+asyncpg" not in url:
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_DATABASE_URL_MUST_BE_ASYNC")
    configured_renderer_url = _required(
        renderer_url or os.environ.get("XINGJING_EDITING_RENDERER_URL"), "XINGJING_EDITING_RENDERER_URL_REQUIRED"
    )
    if not configured_renderer_url.startswith(("http://", "https://")):
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_RENDERER_URL_INVALID")
    configured_renderer_token = _required(
        renderer_token or os.environ.get("XINGJING_EDITING_RENDERER_TOKEN"), "XINGJING_EDITING_RENDERER_TOKEN_REQUIRED"
    )
    configured_callback_secret = _required(
        callback_secret or os.environ.get("XINGJING_EDITING_CALLBACK_SECRET"),
        "XINGJING_EDITING_CALLBACK_SECRET_REQUIRED",
    )
    if len(configured_callback_secret) < 32:
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_CALLBACK_SECRET_MINIMUM_32_REQUIRED")
    configured_sweep_interval = timeout_sweep_interval_seconds
    if configured_sweep_interval is None:
        try:
            configured_sweep_interval = int(os.environ.get("XINGJING_EDITING_TIMEOUT_SWEEP_SECONDS", "30"))
        except ValueError as error:
            raise EditingRuntimeConfigurationError("XINGJING_EDITING_TIMEOUT_SWEEP_SECONDS_INVALID") from error
    if not 5 <= configured_sweep_interval <= 3600:
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_TIMEOUT_SWEEP_SECONDS_INVALID")
    configured_currency = _required(
        billing_currency or os.environ.get("XINGJING_EDITING_BILLING_CURRENCY"),
        "XINGJING_EDITING_BILLING_CURRENCY_REQUIRED",
    ).upper()
    if len(configured_currency) != 3:
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_BILLING_CURRENCY_INVALID")
    configured_pricing_version = _required(
        billing_pricing_version or os.environ.get("XINGJING_EDITING_BILLING_PRICING_VERSION"),
        "XINGJING_EDITING_BILLING_PRICING_VERSION_REQUIRED",
    )
    raw_rate = billing_minor_per_megapixel_second
    if raw_rate is None:
        try:
            raw_rate = int(os.environ.get("XINGJING_EDITING_BILLING_MINOR_PER_MEGAPIXEL_SECOND", ""))
        except ValueError as error:
            raise EditingRuntimeConfigurationError("XINGJING_EDITING_BILLING_RATE_INVALID") from error
    if raw_rate <= 0:
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_BILLING_RATE_INVALID")
    root = Path(
        _required(
            object_storage_root or os.environ.get("XINGJING_EDITING_OBJECT_STORAGE_ROOT"),
            "XINGJING_EDITING_OBJECT_STORAGE_ROOT_REQUIRED",
        )
    ).expanduser()
    if not root.is_dir():
        raise EditingRuntimeConfigurationError("XINGJING_EDITING_OBJECT_STORAGE_ROOT_INVALID")
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise EditingRuntimeConfigurationError("EDITING_DATABASE_UNAVAILABLE") from error
    return EditingRuntime(
        session_factory=async_sessionmaker(engine, expire_on_commit=False),
        context_resolver=context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        engine=engine,
        renderer_url=configured_renderer_url,
        renderer_token=configured_renderer_token,
        object_storage_root=root,
        callback_secret=configured_callback_secret,
        billing_policy=EditingRenderBillingPolicy(
            currency=configured_currency,
            pricing_version=configured_pricing_version,
            minor_per_megapixel_second=raw_rate,
        ),
        timeout_sweep_interval_seconds=configured_sweep_interval,
    )


def _required(value: str | Path | None, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EditingRuntimeConfigurationError(code)
    return text
