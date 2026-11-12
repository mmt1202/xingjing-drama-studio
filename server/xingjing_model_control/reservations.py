"""Task-bound model capacity admitted in the same transaction as task and funds."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_generation import GenerationTask, TaskStatus
from server.xingjing_generation_persistence.repository import TaskRow

from .routing import choose_candidate
from .runtime import (
    ModelDefinitionRow,
    ModelHealthRow,
    ModelPricingRow,
    ModelRouteDecision,
    ModelRouteReservationRow,
    ModelRouteResolver,
    ModelRoutingPolicyRow,
    _utc,
)


class ModelRouteAdmission:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], routes: ModelRouteResolver) -> None:
        self.sessions, self.routes = sessions, routes

    async def reserve(
        self,
        session: AsyncSession,
        task: GenerationTask,
        route: ModelRouteDecision,
        *,
        tenant_id: str,
        release_key: str,
        kind: str,
        parameters: Mapping[str, object],
    ) -> None:
        if kind not in {"native_image", "gateway"}:
            raise ValueError("MODEL_ROUTE_RESERVATION_INVALID")
        bound = self.sessions.kw.get("bind")
        if bound is None or session.get_bind().engine.url != bound.url:
            raise ValueError("GENERATION_MODEL_SHARED_DATABASE_REQUIRED")
        request = task.request
        parent = await session.get(TaskRow, (request.workspace_id, request.project_id, task.task_id))
        if parent is None or parent.snapshot.get("request_fingerprint") != task.request_fingerprint:
            raise ValueError("MODEL_ROUTE_RESERVATION_TASK_MISMATCH")
        model = await session.get(ModelDefinitionRow, route.registry_key, with_for_update=True)
        if (
            model is None
            or not model.active
            or model.version != route.definition_version
            or model.provider_id != route.provider_id
            or model.provider_model_name != route.model_id
            or model.credential_ref != route.credential_ref
        ):
            raise LookupError("MODEL_CONFIGURATION_CHANGED")
        health = await session.get(ModelHealthRow, route.registry_key, with_for_update=True)
        if health is None:
            health = ModelHealthRow(registry_key=route.registry_key, status="unknown", active_calls=0)
            session.add(health)
            await session.flush()
        existing = await session.get(ModelRouteReservationRow, release_key)
        if existing is not None:
            if (
                existing.tenant_id != tenant_id
                or existing.workspace_id != request.workspace_id
                or existing.project_id != request.project_id
                or existing.task_id != task.task_id
                or existing.registry_key != route.registry_key
                or existing.request_fingerprint != task.request_fingerprint
                or existing.pricing_version != route.pricing_version
                or existing.kind != kind
                or existing.released_at is not None
            ):
                raise ValueError("MODEL_ROUTE_RESERVATION_CONFLICT")
            return
        now = datetime.now(UTC)
        price = await session.scalar(
            select(ModelPricingRow).where(
                ModelPricingRow.registry_key == route.registry_key,
                ModelPricingRow.pricing_version == route.pricing_version,
            )
        )
        if (
            price is None
            or _utc(price.effective_at) > now
            or (price.retired_at is not None and _utc(price.retired_at) <= now)
            or price.currency != route.currency
            or price.estimated_minor != route.estimated_minor
            or price.successful_output_minor != route.successful_output_minor
        ):
            raise LookupError("MODEL_CONFIGURATION_CHANGED")
        policy = await session.scalar(
            select(ModelRoutingPolicyRow).where(
                ModelRoutingPolicyRow.tenant_id == tenant_id,
                ModelRoutingPolicyRow.workspace_id == request.workspace_id,
                ModelRoutingPolicyRow.capability == request.capability,
                ModelRoutingPolicyRow.media_type == request.media_type.value,
            )
        )
        await choose_candidate(
            session,
            [(model, health, price)],
            policy,
            tenant_id=tenant_id,
            workspace_id=request.workspace_id,
            capability=request.capability,
            media_type=request.media_type.value,
            requested_provider_id=route.provider_id,
            requested_model_id=route.model_id,
            parameters=parameters,
            now=now,
        )
        health.active_calls += 1
        session.add(
            ModelRouteReservationRow(
                release_key=release_key,
                tenant_id=tenant_id,
                workspace_id=request.workspace_id,
                project_id=request.project_id,
                task_id=task.task_id,
                registry_key=route.registry_key,
                request_fingerprint=task.request_fingerprint,
                pricing_version=route.pricing_version,
                kind=kind,
            )
        )

    async def task_reservation(self, task: GenerationTask) -> ModelRouteReservationRow | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(ModelRouteReservationRow).where(
                    ModelRouteReservationRow.workspace_id == task.request.workspace_id,
                    ModelRouteReservationRow.project_id == task.request.project_id,
                    ModelRouteReservationRow.task_id == task.task_id,
                )
            )

    async def release(self, task: GenerationTask, *, succeeded: bool) -> bool:
        row = await self.task_reservation(task)
        if row is None:
            return False
        if row.request_fingerprint != task.request_fingerprint:
            raise ValueError("MODEL_ROUTE_RESERVATION_TASK_MISMATCH")
        await self.routes.release_once(registry_key=row.registry_key, release_key=row.release_key, succeeded=succeeded)
        return True

    async def mark_dispatched(self, task: GenerationTask) -> None:
        async with self.sessions.begin() as session:
            parent = await session.get(
                TaskRow, (task.request.workspace_id, task.request.project_id, task.task_id), with_for_update=True
            )
            if parent is None or parent.status != TaskStatus.QUEUED.value or parent.version != task.version:
                raise ValueError("GENERATION_TASK_VERSION_CONFLICT")
            row = await session.scalar(
                select(ModelRouteReservationRow)
                .where(
                    ModelRouteReservationRow.workspace_id == task.request.workspace_id,
                    ModelRouteReservationRow.project_id == task.request.project_id,
                    ModelRouteReservationRow.task_id == task.task_id,
                )
                .with_for_update()
            )
            if row is None or row.released_at is not None or row.request_fingerprint != task.request_fingerprint:
                raise ValueError("MODEL_ROUTE_RESERVATION_TASK_MISMATCH")
            if row.dispatch_started_at is None:
                row.dispatch_started_at = datetime.now(UTC)

    async def guard_billing_release(self, session: AsyncSession, task: GenerationTask) -> None:
        from server.xingjing_generation_persistence import GenerationBillingError

        if task.status not in {TaskStatus.TIMEOUT, TaskStatus.CANCELLED}:
            return
        row = await session.scalar(
            select(ModelRouteReservationRow).where(
                ModelRouteReservationRow.workspace_id == task.request.workspace_id,
                ModelRouteReservationRow.project_id == task.request.project_id,
                ModelRouteReservationRow.task_id == task.task_id,
            )
        )
        if row is not None and row.dispatch_started_at is not None:
            if task.status is TaskStatus.TIMEOUT or not task.provider_job_id:
                raise GenerationBillingError("GENERATION_PROVIDER_OUTCOME_UNKNOWN")

    async def reconcile(self, *, limit: int = 100) -> int:
        if not 1 <= limit <= 1000:
            raise ValueError("MODEL_ROUTE_RECONCILE_LIMIT_INVALID")
        async with self.sessions() as session:
            rows = (
                await session.execute(
                    select(ModelRouteReservationRow, TaskRow.status)
                    .join(
                        TaskRow,
                        (TaskRow.workspace_id == ModelRouteReservationRow.workspace_id)
                        & (TaskRow.project_id == ModelRouteReservationRow.project_id)
                        & (TaskRow.task_id == ModelRouteReservationRow.task_id),
                    )
                    .where(
                        ModelRouteReservationRow.kind == "gateway",
                        ModelRouteReservationRow.released_at.is_(None),
                        TaskRow.status.in_(["succeeded", "failed", "cancelled", "timeout"]),
                    )
                    .order_by(ModelRouteReservationRow.created_at, ModelRouteReservationRow.release_key)
                    .limit(limit)
                )
            ).all()
        for row, status in rows:
            await self.routes.release_once(
                registry_key=row.registry_key,
                release_key=row.release_key,
                succeeded=status in {TaskStatus.SUCCEEDED.value, TaskStatus.CANCELLED.value},
            )
        return len(rows)
