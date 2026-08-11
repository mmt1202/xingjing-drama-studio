from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_editing.contracts import AuditEvent, AuditOutcome, TimelineVersion, canonical_sha256
from server.xingjing_editing.errors import (
    EditingBillingError,
    IdempotencyConflict,
    InsufficientCredits,
    VersionConflict,
)
from server.xingjing_editing.rendering import (
    FinalVideoSelection,
    FinalVideoVersion,
    RenderBillingStatus,
    RenderCompletion,
    RenderStatus,
    RenderTask,
)
from server.xingjing_generation_persistence.repository import GenerationBillingAccountRow

from .models import (
    EditingAuditRow,
    EditingRenderBillingHoldRow,
    EditingRenderBillingJournalRow,
    FinalVideoSelectionReceiptRow,
    FinalVideoSelectionRow,
    FinalVideoVersionRow,
    RenderCallbackReceiptRow,
    RenderOutboxRow,
    RenderTaskRow,
    TimelineHeadRow,
    TimelineIdempotencyRow,
    TimelineVersionRow,
)


class SqlAlchemyAuditRecorder:
    """Durably appends editing audit events through the application's DB session factory."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: AuditEvent) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                EditingAuditRow(
                    tenant_id=event.tenant_id,
                    workspace_id=event.workspace_id,
                    project_id=event.project_id,
                    event_id=event.event_id,
                    actor_id=event.actor_id,
                    request_id=event.request_id,
                    action=event.action,
                    object_type=event.object_type,
                    object_id=event.object_id,
                    outcome=event.outcome.value,
                    before_sha256=event.before_sha256,
                    after_sha256=event.after_sha256,
                    details=event.details,
                    occurred_at=event.occurred_at,
                )
            )

    async def list_events(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        request_id: str | None,
        actor_id: str | None,
        object_id: str | None,
        action: str | None,
        offset: int,
        limit: int,
    ) -> tuple[AuditEvent, ...]:
        predicates = [
            EditingAuditRow.tenant_id == tenant_id,
            EditingAuditRow.workspace_id == workspace_id,
            EditingAuditRow.project_id == project_id,
        ]
        if request_id is not None:
            predicates.append(EditingAuditRow.request_id == request_id)
        if actor_id is not None:
            predicates.append(EditingAuditRow.actor_id == actor_id)
        if object_id is not None:
            predicates.append(EditingAuditRow.object_id == object_id)
        if action is not None:
            predicates.append(EditingAuditRow.action == action)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(EditingAuditRow)
                    .where(*predicates)
                    .order_by(EditingAuditRow.occurred_at.desc(), EditingAuditRow.event_id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            return tuple(
                AuditEvent(
                    event_id=row.event_id,
                    tenant_id=row.tenant_id,
                    workspace_id=row.workspace_id,
                    project_id=row.project_id,
                    actor_id=row.actor_id,
                    request_id=row.request_id,
                    action=row.action,
                    object_type=row.object_type,
                    object_id=row.object_id,
                    outcome=AuditOutcome(row.outcome),
                    occurred_at=row.occurred_at,
                    before_sha256=row.before_sha256,
                    after_sha256=row.after_sha256,
                    details=row.details,
                )
                for row in rows
            )


class _TimelineReader:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def _get_version(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion | None:
        row = (
            await session.execute(
                select(TimelineVersionRow).where(
                    TimelineVersionRow.tenant_id == tenant_id,
                    TimelineVersionRow.workspace_id == workspace_id,
                    TimelineVersionRow.timeline_id == timeline_id,
                    TimelineVersionRow.version_id == version_id,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else TimelineVersion.model_validate(row.snapshot)

    async def get_timeline_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
        version_id: str,
    ) -> TimelineVersion | None:
        async with self._session_factory() as session:
            return await self._get_version(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                timeline_id=timeline_id,
                version_id=version_id,
            )


class SqlAlchemyTimelineRepository(_TimelineReader):
    """基于 SQLAlchemy AsyncSession 的生产时间线仓储适配。"""

    async def create_timeline(
        self,
        timeline: TimelineVersion,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        try:
            async with self._session_factory.begin() as session:
                replay = await self._replay(
                    session,
                    tenant_id=timeline.tenant_id,
                    workspace_id=timeline.workspace_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return replay, False
                session.add(
                    TimelineHeadRow(
                        tenant_id=timeline.tenant_id,
                        workspace_id=timeline.workspace_id,
                        timeline_id=timeline.timeline_id,
                        project_id=timeline.project_id,
                        episode_id=timeline.episode_id,
                        final_video_id=timeline.final_video_id,
                        current_version_id=timeline.version_id,
                        current_revision=timeline.revision,
                    )
                )
                await session.flush()
                self._add_version(session, timeline)
                await session.flush()
                self._add_idempotency(
                    session,
                    timeline=timeline,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                await session.flush()
                return timeline, True
        except IntegrityError:
            replay = await self._replay_after_competing_insert(
                tenant_id=timeline.tenant_id,
                workspace_id=timeline.workspace_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay, False
            raise

    async def get_current_timeline(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        timeline_id: str,
    ) -> TimelineVersion | None:
        async with self._session_factory() as session:
            head = (
                await session.execute(
                    select(TimelineHeadRow).where(
                        TimelineHeadRow.tenant_id == tenant_id,
                        TimelineHeadRow.workspace_id == workspace_id,
                        TimelineHeadRow.timeline_id == timeline_id,
                    )
                )
            ).scalar_one_or_none()
            if head is None:
                return None
            return await self._get_version(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                timeline_id=timeline_id,
                version_id=head.current_version_id,
            )

    async def list_current_timelines(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        episode_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[TimelineVersion, ...]:
        async with self._session_factory() as session:
            predicates = [
                TimelineHeadRow.tenant_id == tenant_id,
                TimelineHeadRow.workspace_id == workspace_id,
                TimelineHeadRow.project_id == project_id,
            ]
            if episode_id is not None:
                predicates.append(TimelineHeadRow.episode_id == episode_id)
            heads = (
                await session.execute(
                    select(TimelineHeadRow)
                    .where(*predicates)
                    .order_by(TimelineHeadRow.current_revision.desc(), TimelineHeadRow.timeline_id.asc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars().all()
            timelines = [
                await self._get_version(
                    session,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                    timeline_id=head.timeline_id,
                    version_id=head.current_version_id,
                )
                for head in heads
            ]
        return tuple(timeline for timeline in timelines if timeline is not None)

    async def append_timeline(
        self,
        timeline: TimelineVersion,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[TimelineVersion, bool]:
        try:
            async with self._session_factory.begin() as session:
                replay = await self._replay(
                    session,
                    tenant_id=timeline.tenant_id,
                    workspace_id=timeline.workspace_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                if replay is not None:
                    return replay, False
                head = (
                    await session.execute(
                        select(TimelineHeadRow).where(
                            TimelineHeadRow.tenant_id == timeline.tenant_id,
                            TimelineHeadRow.workspace_id == timeline.workspace_id,
                            TimelineHeadRow.timeline_id == timeline.timeline_id,
                        )
                    )
                ).scalar_one_or_none()
                current_revision = 0 if head is None else head.current_revision
                if head is None or current_revision != expected_revision:
                    raise VersionConflict(
                        expected_version=expected_revision,
                        current_version=current_revision,
                    )
                self._validate_successor(timeline, head=head, expected_revision=expected_revision)
                result = await session.execute(
                    update(TimelineHeadRow)
                    .where(
                        TimelineHeadRow.tenant_id == timeline.tenant_id,
                        TimelineHeadRow.workspace_id == timeline.workspace_id,
                        TimelineHeadRow.timeline_id == timeline.timeline_id,
                        TimelineHeadRow.current_revision == expected_revision,
                    )
                    .values(
                        current_version_id=timeline.version_id,
                        current_revision=timeline.revision,
                    )
                )
                if _rowcount(result) != 1:
                    replay = await self._replay(
                        session,
                        tenant_id=timeline.tenant_id,
                        workspace_id=timeline.workspace_id,
                        idempotency_key=idempotency_key,
                        request_fingerprint=request_fingerprint,
                    )
                    if replay is not None:
                        return replay, False
                    actual_revision = await session.scalar(
                        select(TimelineHeadRow.current_revision).where(
                            TimelineHeadRow.tenant_id == timeline.tenant_id,
                            TimelineHeadRow.workspace_id == timeline.workspace_id,
                            TimelineHeadRow.timeline_id == timeline.timeline_id,
                        )
                    )
                    raise VersionConflict(
                        expected_version=expected_revision,
                        current_version=actual_revision or 0,
                    )
                self._add_version(session, timeline)
                await session.flush()
                self._add_idempotency(
                    session,
                    timeline=timeline,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                )
                await session.flush()
                return timeline, True
        except IntegrityError:
            replay = await self._replay_after_competing_insert(
                tenant_id=timeline.tenant_id,
                workspace_id=timeline.workspace_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay, False
            raise

    async def _replay_after_competing_insert(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> TimelineVersion | None:
        async with self._session_factory() as session:
            return await self._replay(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )

    async def _replay(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> TimelineVersion | None:
        record = (
            await session.execute(
                select(TimelineIdempotencyRow).where(
                    TimelineIdempotencyRow.tenant_id == tenant_id,
                    TimelineIdempotencyRow.workspace_id == workspace_id,
                    TimelineIdempotencyRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if record is None:
            return None
        if record.request_fingerprint != request_fingerprint:
            raise IdempotencyConflict()
        stored = await self._get_version(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            timeline_id=record.timeline_id,
            version_id=record.version_id,
        )
        if stored is None:
            raise RuntimeError("时间线幂等记录引用的版本不存在")
        return stored

    @staticmethod
    def _validate_successor(
        timeline: TimelineVersion,
        *,
        head: TimelineHeadRow,
        expected_revision: int,
    ) -> None:
        if timeline.revision != expected_revision + 1:
            raise ValueError("时间线后继版本 revision 必须连续递增")
        if timeline.parent_version_id != head.current_version_id:
            raise ValueError("时间线后继版本必须引用当前版本")
        if (
            timeline.project_id != head.project_id
            or timeline.episode_id != head.episode_id
            or timeline.final_video_id != head.final_video_id
        ):
            raise ValueError("时间线后继版本不能跨项目、剧集或成片对象")

    @staticmethod
    def _add_version(session: AsyncSession, timeline: TimelineVersion) -> None:
        session.add(
            TimelineVersionRow(
                tenant_id=timeline.tenant_id,
                workspace_id=timeline.workspace_id,
                timeline_id=timeline.timeline_id,
                version_id=timeline.version_id,
                project_id=timeline.project_id,
                revision=timeline.revision,
                parent_version_id=timeline.parent_version_id,
                snapshot=timeline.model_dump(mode="json"),
                created_at=timeline.created_at,
            )
        )

    @staticmethod
    def _add_idempotency(
        session: AsyncSession,
        *,
        timeline: TimelineVersion,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> None:
        session.add(
            TimelineIdempotencyRow(
                tenant_id=timeline.tenant_id,
                workspace_id=timeline.workspace_id,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                timeline_id=timeline.timeline_id,
                version_id=timeline.version_id,
            )
        )


class SqlAlchemyRenderRepository(_TimelineReader):
    """基于数据库唯一约束、CAS 和事务 outbox 的生产渲染仓储适配。"""

    async def get_render_task_by_idempotency(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> RenderTask | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(RenderTaskRow).where(
                        RenderTaskRow.tenant_id == tenant_id,
                        RenderTaskRow.workspace_id == workspace_id,
                        RenderTaskRow.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            return self._task_from_row(row)

    async def create_render_task(
        self,
        task: RenderTask,
        *,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[RenderTask, bool]:
        if task.idempotency_key != idempotency_key or task.request_fingerprint != request_fingerprint:
            raise ValueError("渲染任务与幂等参数不一致")
        try:
            async with self._session_factory.begin() as session:
                replay = await self._get_task_by_idempotency(
                    session,
                    tenant_id=task.tenant_id,
                    workspace_id=task.workspace_id,
                    idempotency_key=idempotency_key,
                )
                if replay is not None:
                    if replay.request_fingerprint != request_fingerprint:
                        raise IdempotencyConflict()
                    return replay, False
                timeline = await self._get_version(
                    session,
                    tenant_id=task.tenant_id,
                    workspace_id=task.workspace_id,
                    timeline_id=task.timeline_id,
                    version_id=task.timeline_version_id,
                )
                if (
                    timeline is None
                    or timeline.project_id != task.project_id
                    or timeline.final_video_id != task.final_video_id
                    or timeline.revision != task.timeline_revision
                ):
                    raise ValueError("渲染任务不能跨项目、时间线版本或成片对象")
                session.add(self._task_row(task))
                await session.flush()
                await self._freeze_billing(session, task=task, action="freeze")
                return task, True
        except IntegrityError:
            replay = await self.get_render_task_by_idempotency(
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                idempotency_key=idempotency_key,
            )
            if replay is None:
                raise IdempotencyConflict() from None
            if replay.request_fingerprint != request_fingerprint:
                raise IdempotencyConflict() from None
            return replay, False

    async def get_render_task(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        task_id: str,
    ) -> RenderTask | None:
        async with self._session_factory() as session:
            return await self._get_task(
                session,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                task_id=task_id,
            )

    async def list_render_tasks(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        offset: int,
        limit: int,
    ) -> tuple[RenderTask, ...]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(RenderTaskRow)
                    .where(
                        RenderTaskRow.tenant_id == tenant_id,
                        RenderTaskRow.workspace_id == workspace_id,
                        RenderTaskRow.project_id == project_id,
                    )
                    .order_by(RenderTaskRow.created_at.desc(), RenderTaskRow.task_id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            return tuple(RenderTask.model_validate(row.snapshot) for row in rows)

    async def list_final_video_versions(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        final_video_id: str | None,
        offset: int,
        limit: int,
    ) -> tuple[FinalVideoVersion, ...]:
        predicates = [
            FinalVideoVersionRow.tenant_id == tenant_id,
            FinalVideoVersionRow.workspace_id == workspace_id,
            FinalVideoVersionRow.project_id == project_id,
        ]
        if final_video_id is not None:
            predicates.append(FinalVideoVersionRow.final_video_id == final_video_id)
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(FinalVideoVersionRow)
                    .where(*predicates)
                    .order_by(FinalVideoVersionRow.created_at.desc(), FinalVideoVersionRow.version_id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).scalars()
            return tuple(FinalVideoVersion.model_validate(row.snapshot) for row in rows)

    async def get_final_video_version(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        version_id: str,
    ) -> FinalVideoVersion | None:
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(FinalVideoVersionRow).where(
                        FinalVideoVersionRow.tenant_id == tenant_id,
                        FinalVideoVersionRow.workspace_id == workspace_id,
                        FinalVideoVersionRow.project_id == project_id,
                        FinalVideoVersionRow.version_id == version_id,
                    )
                )
            ).scalar_one_or_none()
            return None if row is None else FinalVideoVersion.model_validate(row.snapshot)

    async def get_final_video_selection(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        project_id: str,
        final_video_id: str,
    ) -> FinalVideoSelection | None:
        async with self._session_factory() as session:
            row = await session.get(
                FinalVideoSelectionRow,
                (tenant_id, workspace_id, project_id, final_video_id),
            )
            return None if row is None else FinalVideoSelection.model_validate(row.snapshot)

    async def select_final_video_version(
        self,
        selection: FinalVideoSelection,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[FinalVideoSelection, bool]:
        try:
            return await self._select_final_video_version_once(
                selection,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
            )
        except IntegrityError:
            async with self._session_factory() as session:
                receipt = await session.get(
                    FinalVideoSelectionReceiptRow,
                    (selection.tenant_id, selection.workspace_id, idempotency_key),
                )
                if receipt is None or receipt.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict() from None
                return FinalVideoSelection.model_validate(receipt.snapshot), False

    async def _select_final_video_version_once(
        self,
        selection: FinalVideoSelection,
        *,
        expected_revision: int,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> tuple[FinalVideoSelection, bool]:
        async with self._session_factory.begin() as session:
            receipt = await session.get(
                FinalVideoSelectionReceiptRow,
                (selection.tenant_id, selection.workspace_id, idempotency_key),
            )
            if receipt is not None:
                if receipt.request_fingerprint != request_fingerprint:
                    raise IdempotencyConflict()
                return FinalVideoSelection.model_validate(receipt.snapshot), False
            version = await self._get_final_version_by_id(
                session,
                tenant_id=selection.tenant_id,
                workspace_id=selection.workspace_id,
                final_video_id=selection.final_video_id,
                version_id=selection.selected_version_id,
            )
            if version is None or version.project_id != selection.project_id:
                raise ValueError("成片版本不存在或不属于当前项目")
            current = await session.get(
                FinalVideoSelectionRow,
                (
                    selection.tenant_id,
                    selection.workspace_id,
                    selection.project_id,
                    selection.final_video_id,
                ),
                with_for_update=True,
            )
            actual_revision = 0 if current is None else current.revision
            if actual_revision != expected_revision:
                raise VersionConflict(expected_version=expected_revision, current_version=actual_revision)
            if selection.revision != expected_revision + 1:
                raise ValueError("成片选择修订号必须连续递增")
            if current is None:
                session.add(
                    FinalVideoSelectionRow(
                        tenant_id=selection.tenant_id,
                        workspace_id=selection.workspace_id,
                        project_id=selection.project_id,
                        final_video_id=selection.final_video_id,
                        selected_version_id=selection.selected_version_id,
                        revision=selection.revision,
                        snapshot=selection.model_dump(mode="json"),
                        selected_at=selection.selected_at,
                    )
                )
            else:
                current.selected_version_id = selection.selected_version_id
                current.revision = selection.revision
                current.snapshot = selection.model_dump(mode="json")
                current.selected_at = selection.selected_at
            session.add(
                FinalVideoSelectionReceiptRow(
                    tenant_id=selection.tenant_id,
                    workspace_id=selection.workspace_id,
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    snapshot=selection.model_dump(mode="json"),
                    created_at=selection.selected_at,
                )
            )
            await session.flush()
            return selection, True

    async def save_render_task(self, task: RenderTask, *, expected_revision: int) -> RenderTask:
        try:
            async with self._session_factory.begin() as session:
                current = await self._require_task(session, task=task)
                if current.task_revision != expected_revision:
                    if current == task:
                        return current
                    raise VersionConflict(
                        expected_version=expected_revision,
                        current_version=current.task_revision,
                    )
                task = await self._apply_billing_transition(session, current=current, task=task)
                self._validate_task_successor(current, task, expected_revision=expected_revision)
                new_event_ids = self._new_callback_event_ids(current, task)
                await self._reject_reused_callback_events(session, task=task, event_ids=new_event_ids)
                result = await session.execute(
                    update(RenderTaskRow)
                    .where(
                        RenderTaskRow.tenant_id == task.tenant_id,
                        RenderTaskRow.workspace_id == task.workspace_id,
                        RenderTaskRow.task_id == task.task_id,
                        RenderTaskRow.task_revision == expected_revision,
                    )
                    .values(self._task_values(task))
                    .execution_options(synchronize_session=False)
                )
                if _rowcount(result) != 1:
                    actual = await self._get_task(
                        session,
                        tenant_id=task.tenant_id,
                        workspace_id=task.workspace_id,
                        task_id=task.task_id,
                    )
                    if actual == task:
                        return task
                    raise VersionConflict(
                        expected_version=expected_revision,
                        current_version=0 if actual is None else actual.task_revision,
                    )
                self._add_callback_receipts(session, task=task, event_ids=new_event_ids)
                await session.flush()
                return task
        except IntegrityError:
            current = await self.get_render_task(
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                task_id=task.task_id,
            )
            if current == task:
                return task
            raise IdempotencyConflict() from None

    async def complete_render(
        self,
        task: RenderTask,
        version: FinalVideoVersion,
        *,
        expected_revision: int,
    ) -> RenderCompletion:
        try:
            return await self._complete_render_once(
                task,
                version,
                expected_revision=expected_revision,
            )
        except IntegrityError:
            existing = await self._get_final_version_by_deduplication(version)
            if existing is not None:
                return await self._complete_render_once(
                    task,
                    version,
                    expected_revision=expected_revision,
                    resolved_version=existing,
                )
            raise IdempotencyConflict() from None

    async def _complete_render_once(
        self,
        task: RenderTask,
        version: FinalVideoVersion,
        *,
        expected_revision: int,
        resolved_version: FinalVideoVersion | None = None,
    ) -> RenderCompletion:
        async with self._session_factory.begin() as session:
            current = await self._require_task(session, task=task)
            if current.task_revision != expected_revision:
                if set(task.processed_callback_event_ids).issubset(current.processed_callback_event_ids):
                    stored_version = await self._get_final_version_by_id(
                        session,
                        tenant_id=current.tenant_id,
                        workspace_id=current.workspace_id,
                        final_video_id=current.final_video_id,
                        version_id=current.output_version_id,
                    )
                    if stored_version is not None:
                        return RenderCompletion(task=current, version=stored_version, version_created=False)
                raise VersionConflict(
                    expected_version=expected_revision,
                    current_version=current.task_revision,
                )
            task = await self._settle_billing(session, current=current, task=task)
            self._validate_completion(current, task=task, version=version, expected_revision=expected_revision)
            existing = resolved_version or await self._get_final_version_by_deduplication_in_session(session, version)
            create_version = existing is None
            stored_version = version if existing is None else existing
            stored_task = RenderTask.model_validate(
                task.model_copy(update={"output_version_id": stored_version.version_id}).model_dump()
            )
            self._validate_task_successor(current, stored_task, expected_revision=expected_revision)
            new_event_ids = self._new_callback_event_ids(current, stored_task)
            await self._reject_reused_callback_events(session, task=stored_task, event_ids=new_event_ids)
            result = await session.execute(
                update(RenderTaskRow)
                .where(
                    RenderTaskRow.tenant_id == stored_task.tenant_id,
                    RenderTaskRow.workspace_id == stored_task.workspace_id,
                    RenderTaskRow.task_id == stored_task.task_id,
                    RenderTaskRow.task_revision == expected_revision,
                )
                .values(self._task_values(stored_task))
                .execution_options(synchronize_session=False)
            )
            if _rowcount(result) != 1:
                actual = await self._get_task(
                    session,
                    tenant_id=stored_task.tenant_id,
                    workspace_id=stored_task.workspace_id,
                    task_id=stored_task.task_id,
                )
                raise VersionConflict(
                    expected_version=expected_revision,
                    current_version=0 if actual is None else actual.task_revision,
                )
            if create_version:
                session.add(self._final_version_row(stored_version))
                await session.flush()
            self._add_callback_receipts(session, task=stored_task, event_ids=new_event_ids)
            session.add(
                RenderOutboxRow(
                    outbox_key=self._completion_outbox_key(stored_task, new_event_ids),
                    tenant_id=stored_task.tenant_id,
                    workspace_id=stored_task.workspace_id,
                    event_type="editing.render.completed.v1",
                    aggregate_type="render_task",
                    aggregate_id=stored_task.task_id,
                    payload={
                        "task_id": stored_task.task_id,
                        "task_revision": stored_task.task_revision,
                        "callback_event_id": new_event_ids[-1] if new_event_ids else None,
                        "final_video_id": stored_task.final_video_id,
                        "final_video_version_id": stored_version.version_id,
                        "deduplication_key": stored_version.deduplication_key,
                    },
                    created_at=stored_task.updated_at,
                    published_at=None,
                )
            )
            await session.flush()
            return RenderCompletion(
                task=stored_task,
                version=stored_version,
                version_created=create_version,
            )

    async def _get_final_version_by_deduplication(
        self,
        version: FinalVideoVersion,
    ) -> FinalVideoVersion | None:
        async with self._session_factory() as session:
            return await self._get_final_version_by_deduplication_in_session(session, version)

    @staticmethod
    async def _get_final_version_by_deduplication_in_session(
        session: AsyncSession,
        version: FinalVideoVersion,
    ) -> FinalVideoVersion | None:
        row = (
            await session.execute(
                select(FinalVideoVersionRow).where(
                    FinalVideoVersionRow.tenant_id == version.tenant_id,
                    FinalVideoVersionRow.workspace_id == version.workspace_id,
                    FinalVideoVersionRow.final_video_id == version.final_video_id,
                    FinalVideoVersionRow.deduplication_key == version.deduplication_key,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else FinalVideoVersion.model_validate(row.snapshot)

    @staticmethod
    async def _get_final_version_by_id(
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        final_video_id: str,
        version_id: str | None,
    ) -> FinalVideoVersion | None:
        if version_id is None:
            return None
        row = (
            await session.execute(
                select(FinalVideoVersionRow).where(
                    FinalVideoVersionRow.tenant_id == tenant_id,
                    FinalVideoVersionRow.workspace_id == workspace_id,
                    FinalVideoVersionRow.final_video_id == final_video_id,
                    FinalVideoVersionRow.version_id == version_id,
                )
            )
        ).scalar_one_or_none()
        return None if row is None else FinalVideoVersion.model_validate(row.snapshot)

    async def _require_task(self, session: AsyncSession, *, task: RenderTask) -> RenderTask:
        current = await self._get_task(
            session,
            tenant_id=task.tenant_id,
            workspace_id=task.workspace_id,
            task_id=task.task_id,
        )
        if current is None:
            raise RuntimeError("渲染任务不存在或不属于当前租户工作区")
        return current

    @staticmethod
    async def _get_task_by_idempotency(
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        idempotency_key: str,
    ) -> RenderTask | None:
        row = (
            await session.execute(
                select(RenderTaskRow).where(
                    RenderTaskRow.tenant_id == tenant_id,
                    RenderTaskRow.workspace_id == workspace_id,
                    RenderTaskRow.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        return SqlAlchemyRenderRepository._task_from_row(row)

    @staticmethod
    async def _get_task(
        session: AsyncSession,
        *,
        tenant_id: str,
        workspace_id: str,
        task_id: str,
    ) -> RenderTask | None:
        row = (
            await session.execute(
                select(RenderTaskRow)
                .where(
                    RenderTaskRow.tenant_id == tenant_id,
                    RenderTaskRow.workspace_id == workspace_id,
                    RenderTaskRow.task_id == task_id,
                )
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        return SqlAlchemyRenderRepository._task_from_row(row)

    @staticmethod
    def _task_from_row(row: RenderTaskRow | None) -> RenderTask | None:
        return None if row is None else RenderTask.model_validate(row.snapshot)

    @staticmethod
    def _task_row(task: RenderTask) -> RenderTaskRow:
        return RenderTaskRow(
            tenant_id=task.tenant_id,
            workspace_id=task.workspace_id,
            task_id=task.task_id,
            project_id=task.project_id,
            timeline_id=task.timeline_id,
            timeline_version_id=task.timeline_version_id,
            final_video_id=task.final_video_id,
            idempotency_key=task.idempotency_key,
            request_fingerprint=task.request_fingerprint,
            task_revision=task.task_revision,
            status=task.status.value,
            attempt=task.attempt,
            output_version_id=task.output_version_id,
            snapshot=task.model_dump(mode="json"),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    @staticmethod
    def _task_values(task: RenderTask) -> dict[str, object]:
        return {
            "task_revision": task.task_revision,
            "status": task.status.value,
            "attempt": task.attempt,
            "output_version_id": task.output_version_id,
            "snapshot": task.model_dump(mode="json"),
            "updated_at": task.updated_at,
        }

    @staticmethod
    async def _billing_account(
        session: AsyncSession,
        *,
        workspace_id: str,
    ) -> GenerationBillingAccountRow:
        account = await session.get(GenerationBillingAccountRow, workspace_id, with_for_update=True)
        if account is None:
            raise EditingBillingError(
                "EDITING_BILLING_ACCOUNT_MISSING",
                "工作区尚未建立共享计费账户",
                status_code=409,
            )
        return account

    async def _freeze_billing(
        self,
        session: AsyncSession,
        *,
        task: RenderTask,
        action: str,
    ) -> None:
        account = await self._billing_account(session, workspace_id=task.workspace_id)
        if account.currency != task.billing_currency:
            raise EditingBillingError("EDITING_BILLING_CURRENCY_MISMATCH", "渲染计费币种与工作区账户不一致")
        if account.available_minor < task.billing_estimated_minor:
            raise InsufficientCredits()
        account.available_minor -= task.billing_estimated_minor
        account.held_minor += task.billing_estimated_minor
        account.version += 1
        account.updated_at = task.updated_at
        if action == "freeze":
            session.add(
                EditingRenderBillingHoldRow(
                    tenant_id=task.tenant_id,
                    workspace_id=task.workspace_id,
                    project_id=task.project_id,
                    task_id=task.task_id,
                    currency=task.billing_currency,
                    estimated_minor=task.billing_estimated_minor,
                    actual_minor=0,
                    released_minor=0,
                    pricing_version=task.billing_pricing_version,
                    status=RenderBillingStatus.ACTIVE.value,
                    terminal_event_id=None,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                )
            )
        else:
            hold = await self._require_billing_hold(session, task=task)
            hold.status = RenderBillingStatus.ACTIVE.value
            hold.actual_minor = 0
            hold.released_minor = 0
            hold.terminal_event_id = None
            hold.updated_at = task.updated_at
        session.add(
            EditingRenderBillingJournalRow(
                event_id=canonical_sha256(
                    {"action": action, "task_id": task.task_id, "task_revision": task.task_revision}
                ),
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                task_id=task.task_id,
                action=action,
                currency=task.billing_currency,
                amount_minor=task.billing_estimated_minor,
                postings=[
                    {"account": "workspace.available", "amount_minor": -task.billing_estimated_minor},
                    {"account": "workspace.held", "amount_minor": task.billing_estimated_minor},
                ],
                reference=f"render:{task.task_id}:{task.task_revision}",
                occurred_at=task.updated_at,
            )
        )

    @staticmethod
    async def _require_billing_hold(
        session: AsyncSession,
        *,
        task: RenderTask,
    ) -> EditingRenderBillingHoldRow:
        hold = await session.get(
            EditingRenderBillingHoldRow,
            (task.tenant_id, task.workspace_id, task.project_id, task.task_id),
            with_for_update=True,
        )
        if hold is None:
            raise EditingBillingError("EDITING_BILLING_HOLD_MISSING", "渲染任务缺少费用冻结记录")
        return hold

    async def _apply_billing_transition(
        self,
        session: AsyncSession,
        *,
        current: RenderTask,
        task: RenderTask,
    ) -> RenderTask:
        if current.billing_status is RenderBillingStatus.RELEASED and task.status is RenderStatus.RETRYING:
            reactivated = task.model_copy(
                update={"billing_status": RenderBillingStatus.ACTIVE, "billing_actual_minor": None}
            )
            await self._freeze_billing(session, task=reactivated, action="refreeze")
            return reactivated
        if (
            current.billing_status is RenderBillingStatus.ACTIVE
            and task.status in {RenderStatus.FAILED, RenderStatus.CANCELLED}
        ):
            return await self._release_billing(session, current=current, task=task)
        return task

    async def _release_billing(
        self,
        session: AsyncSession,
        *,
        current: RenderTask,
        task: RenderTask,
    ) -> RenderTask:
        hold = await self._require_billing_hold(session, task=task)
        if hold.status == RenderBillingStatus.RELEASED.value:
            return task.model_copy(
                update={"billing_status": RenderBillingStatus.RELEASED, "billing_actual_minor": 0}
            )
        if hold.status != RenderBillingStatus.ACTIVE.value:
            raise EditingBillingError("EDITING_BILLING_TERMINAL_CONFLICT", "渲染费用已经进入其他终态")
        account = await self._billing_account(session, workspace_id=task.workspace_id)
        account.held_minor -= hold.estimated_minor
        account.available_minor += hold.estimated_minor
        account.version += 1
        account.updated_at = task.updated_at
        hold.status = RenderBillingStatus.RELEASED.value
        hold.actual_minor = 0
        hold.released_minor = hold.estimated_minor
        hold.terminal_event_id = str(task.processed_callback_event_ids[-1]) if task.processed_callback_event_ids else None
        hold.updated_at = task.updated_at
        session.add(
            EditingRenderBillingJournalRow(
                event_id=canonical_sha256(
                    {"action": "release", "task_id": task.task_id, "task_revision": task.task_revision}
                ),
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                task_id=task.task_id,
                action="release",
                currency=task.billing_currency,
                amount_minor=hold.estimated_minor,
                postings=[
                    {"account": "workspace.held", "amount_minor": -hold.estimated_minor},
                    {"account": "workspace.available", "amount_minor": hold.estimated_minor},
                ],
                reference=f"render:{task.task_id}:{task.task_revision}",
                occurred_at=task.updated_at,
            )
        )
        return task.model_copy(
            update={"billing_status": RenderBillingStatus.RELEASED, "billing_actual_minor": 0}
        )

    async def _settle_billing(
        self,
        session: AsyncSession,
        *,
        current: RenderTask,
        task: RenderTask,
    ) -> RenderTask:
        hold = await self._require_billing_hold(session, task=task)
        if hold.status == RenderBillingStatus.SETTLED.value:
            return task.model_copy(
                update={
                    "billing_status": RenderBillingStatus.SETTLED,
                    "billing_actual_minor": hold.actual_minor,
                }
            )
        if current.billing_status is not RenderBillingStatus.ACTIVE or hold.status != RenderBillingStatus.ACTIVE.value:
            raise EditingBillingError("EDITING_BILLING_TERMINAL_CONFLICT", "渲染成功时费用冻结已不在活动状态")
        actual_minor = task.billing_estimated_minor
        account = await self._billing_account(session, workspace_id=task.workspace_id)
        account.held_minor -= hold.estimated_minor
        account.spent_minor += actual_minor
        account.available_minor += hold.estimated_minor - actual_minor
        account.version += 1
        account.updated_at = task.updated_at
        hold.status = RenderBillingStatus.SETTLED.value
        hold.actual_minor = actual_minor
        hold.released_minor = hold.estimated_minor - actual_minor
        hold.terminal_event_id = str(task.processed_callback_event_ids[-1])
        hold.updated_at = task.updated_at
        session.add(
            EditingRenderBillingJournalRow(
                event_id=canonical_sha256(
                    {"action": "settle", "task_id": task.task_id, "task_revision": task.task_revision}
                ),
                tenant_id=task.tenant_id,
                workspace_id=task.workspace_id,
                project_id=task.project_id,
                task_id=task.task_id,
                action="settle",
                currency=task.billing_currency,
                amount_minor=actual_minor,
                postings=[
                    {"account": "workspace.held", "amount_minor": -hold.estimated_minor},
                    {"account": "platform.revenue", "amount_minor": actual_minor},
                ],
                reference=f"render:{task.task_id}:{task.task_revision}",
                occurred_at=task.updated_at,
            )
        )
        return task.model_copy(
            update={"billing_status": RenderBillingStatus.SETTLED, "billing_actual_minor": actual_minor}
        )

    @staticmethod
    def _final_version_row(version: FinalVideoVersion) -> FinalVideoVersionRow:
        return FinalVideoVersionRow(
            tenant_id=version.tenant_id,
            workspace_id=version.workspace_id,
            final_video_id=version.final_video_id,
            version_id=version.version_id,
            project_id=version.project_id,
            timeline_id=version.timeline_id,
            timeline_version_id=version.timeline_version_id,
            render_task_id=version.render_task_id,
            deduplication_key=version.deduplication_key,
            snapshot=version.model_dump(mode="json"),
            created_at=version.created_at,
        )

    @staticmethod
    def _validate_task_successor(current: RenderTask, task: RenderTask, *, expected_revision: int) -> None:
        immutable_fields = (
            "task_id",
            "tenant_id",
            "workspace_id",
            "project_id",
            "timeline_id",
            "timeline_version_id",
            "timeline_revision",
            "final_video_id",
            "idempotency_key",
            "idempotency_scope",
            "request_fingerprint",
            "preview",
            "profile",
            "billing_currency",
            "billing_estimated_minor",
            "billing_pricing_version",
            "max_attempts",
            "created_at",
            "deadline_at",
        )
        if any(getattr(current, field) != getattr(task, field) for field in immutable_fields):
            raise ValueError("渲染任务更新不能改变身份、范围或不可变输入")
        if task.task_revision != expected_revision + 1:
            raise ValueError("渲染任务 task_revision 必须连续递增")
        if not set(current.processed_callback_event_ids).issubset(task.processed_callback_event_ids):
            raise ValueError("渲染任务不能删除已处理回调事件")

    @staticmethod
    def _validate_completion(
        current: RenderTask,
        *,
        task: RenderTask,
        version: FinalVideoVersion,
        expected_revision: int,
    ) -> None:
        SqlAlchemyRenderRepository._validate_task_successor(current, task, expected_revision=expected_revision)
        if task.status.value != "succeeded":
            raise ValueError("complete_render 只接受成功终态任务")
        if (
            version.tenant_id != task.tenant_id
            or version.workspace_id != task.workspace_id
            or version.project_id != task.project_id
            or version.final_video_id != task.final_video_id
            or version.timeline_id != task.timeline_id
            or version.timeline_version_id != task.timeline_version_id
            or version.render_task_id != task.task_id
            or version.render_attempt != task.attempt
        ):
            raise ValueError("成片版本必须绑定到同一租户、工作区、项目、时间线和渲染任务")

    @staticmethod
    def _new_callback_event_ids(current: RenderTask, task: RenderTask) -> tuple[str, ...]:
        current_ids = set(current.processed_callback_event_ids)
        return tuple(event_id for event_id in task.processed_callback_event_ids if event_id not in current_ids)

    @staticmethod
    async def _reject_reused_callback_events(
        session: AsyncSession,
        *,
        task: RenderTask,
        event_ids: Iterable[str],
    ) -> None:
        event_ids = tuple(event_ids)
        if not event_ids:
            return
        existing = list(
            (
                await session.execute(
                    select(RenderCallbackReceiptRow).where(
                        RenderCallbackReceiptRow.tenant_id == task.tenant_id,
                        RenderCallbackReceiptRow.workspace_id == task.workspace_id,
                        RenderCallbackReceiptRow.event_id.in_(event_ids),
                    )
                )
            ).scalars()
        )
        if existing:
            raise IdempotencyConflict()

    @staticmethod
    def _add_callback_receipts(
        session: AsyncSession,
        *,
        task: RenderTask,
        event_ids: Iterable[str],
    ) -> None:
        for event_id in event_ids:
            session.add(
                RenderCallbackReceiptRow(
                    tenant_id=task.tenant_id,
                    workspace_id=task.workspace_id,
                    event_id=event_id,
                    task_id=task.task_id,
                    task_revision=task.task_revision,
                    task_snapshot=task.model_dump(mode="json"),
                    processed_at=task.updated_at,
                )
            )

    @staticmethod
    def _completion_outbox_key(task: RenderTask, event_ids: tuple[str, ...]) -> str:
        event_component = event_ids[-1] if event_ids else str(task.task_revision)
        return canonical_sha256(
            {
                "event_type": "editing.render.completed.v1",
                "tenant_id": task.tenant_id,
                "workspace_id": task.workspace_id,
                "task_id": task.task_id,
                "callback_event_id": event_component,
            }
        )


def _rowcount(result: Any) -> int:
    return result.rowcount or 0
