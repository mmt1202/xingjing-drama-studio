"""S04 production task, event and notification runtime.

PostgreSQL is the only source of truth. Browser callers are scoped by the Java
identity service; workers authenticate with a deployment service token.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver
from server.xingjing_platform_persistence.persistence import (
    NotificationDeliveryRow,
    NotificationPreferenceRow,
    NotificationRow,
    Scope,
    TaskDeadLetterRow,
    TaskEventRow,
    TaskRow,
    build_claim_tasks_statement,
)

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
RETRYABLE_CODES = frozenset({"PROVIDER_TIMEOUT", "RATE_LIMITED", "TEMPORARY_UNAVAILABLE", "NETWORK_ERROR"})


def _now() -> datetime:
    return datetime.now(UTC)


class PlatformTasksRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession] | None,
        resolver: TrustedWorkspaceContextResolver,
        *,
        worker_token: str,
        engine: AsyncEngine | None = None,
        unavailable_code: str | None = None,
    ) -> None:
        self._sessions = sessions
        self._resolver = resolver
        self._worker_token = worker_token
        self._engine = engine
        self._unavailable_code = unavailable_code
        self.router = self._router()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    def _router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/platform/tasks", operation_id="list_platform_tasks")
        async def list_tasks(
            request: Request, projectId: str | None = None, taskStatus: str | None = None, limit: int = 50
        ):
            context = await self._context(request, "generation.view")
            limit = min(max(limit, 1), 200)
            async with self._session() as session:
                clauses = self._scope(TaskRow, context)
                if projectId:
                    clauses.append(TaskRow.project_id == projectId)
                if taskStatus:
                    clauses.append(TaskRow.status == taskStatus)
                rows = list(
                    (
                        await session.scalars(
                            select(TaskRow).where(*clauses).order_by(TaskRow.created_at.desc()).limit(limit)
                        )
                    ).all()
                )
            return _ok([_task(row) for row in rows], context.request_id)

        @router.post("/platform/tasks", operation_id="create_platform_task", status_code=201)
        async def create_task(request: Request):
            context = await self._context(request, "generation.manage")
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key:
                return _error(context.request_id, "IDEMPOTENCY_KEY_REQUIRED", 400)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            task_type = _text(body.get("taskType"))
            payload = body.get("payload")
            if not task_type or not isinstance(payload, Mapping):
                return _error(context.request_id, "INVALID_TASK_REQUEST", 400)
            fingerprint = _fingerprint({"taskType": task_type, "payload": payload, "projectId": body.get("projectId")})
            now = _now()
            row = TaskRow(
                id=str(uuid4()),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                idempotency_key=key,
                request_fingerprint=fingerprint,
                task_type=task_type,
                payload=dict(payload),
                project_id=_optional_text(body.get("projectId")),
                batch_id=_optional_text(body.get("batchId")),
                created_by=context.actor_id,
                max_attempts=_bounded_int(body.get("maxAttempts"), 1, 5, 3),
                deadline_at=now + timedelta(seconds=_bounded_int(body.get("timeoutSeconds"), 30, 86400, 3600)),
                created_at=now,
                updated_at=now,
            )
            sessions = self._session()
            async with sessions as session, session.begin():
                existing = await session.scalar(
                    select(TaskRow).where(*self._scope(TaskRow, context), TaskRow.idempotency_key == key)
                )
                if existing is not None:
                    if not hmac.compare_digest(existing.request_fingerprint, fingerprint):
                        return _error(context.request_id, "IDEMPOTENCY_CONFLICT", 409)
                    return _ok(_task(existing), context.request_id)
                session.add(row)
                await self._event(session, context, row, "task.queued", f"submit:{key}", {"status": "queued"})
                try:
                    await session.flush()
                except IntegrityError:
                    return _error(context.request_id, "IDEMPOTENCY_CONFLICT", 409)
            return _ok(_task(row), context.request_id)

        @router.get("/platform/tasks/{task_id}", operation_id="get_platform_task")
        async def get_task(request: Request, task_id: str):
            context = await self._context(request, "generation.view")
            async with self._session() as session:
                row = await session.scalar(select(TaskRow).where(*self._scope(TaskRow, context), TaskRow.id == task_id))
            return (
                _error(context.request_id, "TASK_NOT_FOUND", 404)
                if row is None
                else _ok(_task(row), context.request_id)
            )

        @router.post("/platform/tasks/{task_id}/actions", operation_id="act_on_platform_task")
        async def task_action(request: Request, task_id: str):
            context = await self._context(request, "generation.manage")
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            action = _text(body.get("action"))
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(TaskRow).where(*self._scope(TaskRow, context), TaskRow.id == task_id).with_for_update()
                )
                if row is None:
                    return _error(context.request_id, "TASK_NOT_FOUND", 404)
                if action == "cancel" and row.status not in TERMINAL:
                    row.cancel_requested_at = _now()
                    row.status = "cancelled" if row.status in {"queued", "retrying"} else "cancelling"
                elif (
                    action == "retry" and row.status in {"failed", "timed_out"} and row.attempt_count < row.max_attempts
                ):
                    row.status, row.next_attempt_at = "retrying", _now()
                    row.last_error_code = row.last_error_message = None
                else:
                    return _error(context.request_id, "INVALID_TASK_TRANSITION", 409)
                row.version += 1
                row.updated_at = _now()
                await self._event(
                    session,
                    context,
                    row,
                    f"task.{action}_requested",
                    f"action:{action}:{request.headers.get('Idempotency-Key', context.request_id)}",
                    {"status": row.status},
                )
            return _ok(_task(row), context.request_id)

        @router.get("/platform/events", operation_id="stream_platform_events")
        async def stream_events(request: Request):
            context = await self._context(request, "workspace.view")
            raw_cursor = request.headers.get("Last-Event-ID") or request.query_params.get("after") or "0"
            cursor = int(raw_cursor) if raw_cursor.isdigit() else 0

            async def generate():
                nonlocal cursor
                yield "retry: 3000\n\n"
                for _ in range(120):
                    if await request.is_disconnected():
                        return
                    async with self._session() as session:
                        rows = list(
                            (
                                await session.scalars(
                                    select(TaskEventRow)
                                    .where(*self._scope(TaskEventRow, context), TaskEventRow.sequence > cursor)
                                    .order_by(TaskEventRow.sequence)
                                    .limit(100)
                                )
                            ).all()
                        )
                    if rows:
                        for row in rows:
                            cursor = row.sequence
                            yield f"id: {row.sequence}\nevent: {row.event_type}\ndata: {json.dumps(_event(row), ensure_ascii=False)}\n\n"
                    else:
                        yield ": keepalive\n\n"
                    await asyncio.sleep(1)

            return StreamingResponse(
                generate(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        @router.get("/notifications", operation_id="list_user_notifications")
        async def list_notifications(request: Request, unreadOnly: bool = False, limit: int = 100):
            context = await self._context(request, "workspace.view")
            async with self._session() as session:
                clauses = [*self._scope(NotificationRow, context), NotificationRow.recipient_id == context.actor_id]
                if unreadOnly:
                    clauses.append(NotificationRow.read_at.is_(None))
                rows = list(
                    (
                        await session.scalars(
                            select(NotificationRow)
                            .where(*clauses)
                            .order_by(NotificationRow.created_at.desc())
                            .limit(min(max(limit, 1), 200))
                        )
                    ).all()
                )
                unread = await session.scalar(
                    select(func.count())
                    .select_from(NotificationRow)
                    .where(
                        *self._scope(NotificationRow, context),
                        NotificationRow.recipient_id == context.actor_id,
                        NotificationRow.read_at.is_(None),
                    )
                )
            return _ok({"items": [_notification(row) for row in rows], "unreadCount": unread or 0}, context.request_id)

        @router.post("/notifications/actions", operation_id="act_on_user_notifications")
        async def notification_action(request: Request):
            context = await self._context(request, "workspace.view")
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            action, notification_id = _text(body.get("action")), _optional_text(body.get("notificationId"))
            async with self._session() as session, session.begin():
                clauses = [
                    *self._scope(NotificationRow, context),
                    NotificationRow.recipient_id == context.actor_id,
                    NotificationRow.read_at.is_(None),
                ]
                if action == "read" and notification_id:
                    clauses.append(NotificationRow.id == notification_id)
                elif action != "read_all":
                    return _error(context.request_id, "INVALID_NOTIFICATION_ACTION", 400)
                rows = list((await session.scalars(select(NotificationRow).where(*clauses).with_for_update())).all())
                now = _now()
                for row in rows:
                    row.read_at = now
            return _ok({"updated": len(rows)}, context.request_id)

        @router.get("/notification-preferences", operation_id="get_notification_preferences")
        async def get_preferences(request: Request):
            context = await self._context(request, "workspace.view")
            async with self._session() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(NotificationPreferenceRow)
                            .where(
                                *self._scope(NotificationPreferenceRow, context),
                                NotificationPreferenceRow.recipient_id == context.actor_id,
                            )
                            .order_by(NotificationPreferenceRow.channel)
                        )
                    ).all()
                )
            return _ok(
                [{"channel": row.channel, "enabled": row.enabled, "destination": row.destination} for row in rows],
                context.request_id,
            )

        @router.put("/notification-preferences/{channel}", operation_id="set_notification_preference")
        async def set_preference(request: Request, channel: str):
            context = await self._context(request, "workspace.view")
            if channel not in {"email", "sms", "push"}:
                return _error(context.request_id, "UNSUPPORTED_NOTIFICATION_CHANNEL", 400)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            enabled, destination = body.get("enabled"), _optional_text(body.get("destination"))
            if not isinstance(enabled, bool) or enabled and not destination:
                return _error(context.request_id, "INVALID_NOTIFICATION_PREFERENCE", 400)
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(NotificationPreferenceRow)
                    .where(
                        *self._scope(NotificationPreferenceRow, context),
                        NotificationPreferenceRow.recipient_id == context.actor_id,
                        NotificationPreferenceRow.channel == channel,
                    )
                    .with_for_update()
                )
                if row is None:
                    row = NotificationPreferenceRow(
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        recipient_id=context.actor_id,
                        channel=channel,
                        enabled=enabled,
                        destination=destination,
                    )
                    session.add(row)
                else:
                    row.enabled, row.destination, row.updated_at = enabled, destination, _now()
            return _ok({"channel": channel, "enabled": enabled, "destination": destination}, context.request_id)

        @router.post("/internal/platform/tasks/claim", operation_id="claim_platform_tasks")
        async def claim_tasks(request: Request):
            self._worker(request)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            tenant, workspace, worker = (
                _text(body.get("tenantId")),
                _text(body.get("workspaceId")),
                _text(body.get("workerId")),
            )
            if not tenant or not workspace or not worker:
                return _error(request.headers.get("X-Request-Id", ""), "INVALID_WORKER_REQUEST", 400)
            now = _now()
            async with self._session() as session, session.begin():
                rows = list(
                    (
                        await session.scalars(
                            build_claim_tasks_statement(
                                Scope(tenant, workspace), now, min(_bounded_int(body.get("limit"), 1, 50, 1), 50)
                            )
                        )
                    ).all()
                )
                for row in rows:
                    row.status, row.lease_owner = "running", worker
                    row.lease_expires_at = now + timedelta(seconds=_bounded_int(body.get("leaseSeconds"), 15, 900, 60))
                    row.attempt_count += 1
                    row.version += 1
                    row.updated_at = now
            return {"data": [_task(row, include_payload=True) for row in rows]}

        @router.post("/internal/platform/tasks/{task_id}/result", operation_id="record_platform_task_result")
        async def record_result(request: Request, task_id: str):
            self._worker(request)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            tenant, workspace = _text(body.get("tenantId")), _text(body.get("workspaceId"))
            callback_id, outcome = _text(body.get("callbackId")), _text(body.get("outcome"))
            if (
                not tenant
                or not workspace
                or not callback_id
                or outcome not in {"progress", "succeeded", "failed", "cancelled"}
            ):
                return _error(request.headers.get("X-Request-Id", ""), "INVALID_WORKER_RESULT", 400)
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(TaskRow)
                    .where(TaskRow.tenant_id == tenant, TaskRow.workspace_id == workspace, TaskRow.id == task_id)
                    .with_for_update()
                )
                if row is None:
                    return _error(request.headers.get("X-Request-Id", ""), "TASK_NOT_FOUND", 404)
                duplicate = await session.scalar(
                    select(TaskEventRow.id).where(
                        TaskEventRow.tenant_id == tenant,
                        TaskEventRow.workspace_id == workspace,
                        TaskEventRow.event_key == f"callback:{callback_id}",
                    )
                )
                if duplicate:
                    return {"data": _task(row), "meta": {"duplicate": True}}
                if row.status in TERMINAL:
                    session.add(
                        TaskDeadLetterRow(
                            tenant_id=tenant,
                            workspace_id=workspace,
                            task_id=row.id,
                            reason="LATE_CALLBACK",
                            payload=dict(body),
                        )
                    )
                    return {"data": _task(row), "meta": {"late": True}}
                if outcome == "progress":
                    row.progress = max(row.progress, _bounded_int(body.get("progress"), 0, 99, row.progress))
                elif outcome == "succeeded":
                    raw_result = body.get("result")
                    result = dict(cast(Mapping[str, object], raw_result)) if isinstance(raw_result, Mapping) else {}
                    row.status, row.progress, row.result, row.completed_at = "succeeded", 100, result, _now()
                    await self._notify(session, row, "任务已完成", f"{row.task_type} 已完成")
                elif outcome == "cancelled":
                    row.status, row.completed_at = "cancelled", _now()
                else:
                    code = _text(body.get("errorCode")) or "PROVIDER_ERROR"
                    row.last_error_code, row.last_error_message = code, _text(body.get("errorMessage"))
                    if code in RETRYABLE_CODES and row.attempt_count < row.max_attempts:
                        row.status, row.next_attempt_at = (
                            "retrying",
                            _now() + timedelta(seconds=min(300, 2**row.attempt_count * 5)),
                        )
                    else:
                        row.status, row.completed_at = "failed", _now()
                        await self._notify(session, row, "任务失败", f"{row.task_type} 执行失败，可在任务中心查看详情")
                row.lease_owner = None
                row.lease_expires_at = None
                row.version += 1
                row.updated_at = _now()
                context = _WorkerContext(tenant, workspace, "worker", request.headers.get("X-Request-Id", callback_id))
                await self._event(
                    session,
                    context,
                    row,
                    f"task.{row.status}",
                    f"callback:{callback_id}",
                    {"status": row.status, "progress": row.progress},
                )
            return {"data": _task(row)}

        @router.post("/internal/platform/tasks/{task_id}/lease", operation_id="renew_platform_task_lease")
        async def renew_lease(request: Request, task_id: str):
            self._worker(request)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            tenant, workspace, worker = (
                _text(body.get("tenantId")),
                _text(body.get("workspaceId")),
                _text(body.get("workerId")),
            )
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(TaskRow)
                    .where(TaskRow.tenant_id == tenant, TaskRow.workspace_id == workspace, TaskRow.id == task_id)
                    .with_for_update()
                )
                if row is None:
                    return _error(request.headers.get("X-Request-Id", ""), "TASK_NOT_FOUND", 404)
                if (
                    row.status != "running"
                    or row.lease_owner != worker
                    or row.lease_expires_at is None
                    or row.lease_expires_at <= _now()
                ):
                    return _error(request.headers.get("X-Request-Id", ""), "LEASE_LOST", 409)
                row.lease_expires_at = _now() + timedelta(seconds=_bounded_int(body.get("leaseSeconds"), 15, 900, 60))
                row.updated_at = _now()
            return {"data": _task(row)}

        @router.post("/internal/platform/tasks/sweep", operation_id="sweep_platform_tasks")
        async def sweep_tasks(request: Request):
            self._worker(request)
            now = _now()
            async with self._session() as session, session.begin():
                rows = list(
                    (
                        await session.scalars(
                            select(TaskRow)
                            .where(
                                TaskRow.deadline_at.is_not(None),
                                TaskRow.deadline_at <= now,
                                TaskRow.status.not_in(TERMINAL),
                            )
                            .with_for_update(skip_locked=True)
                            .limit(500)
                        )
                    ).all()
                )
                for row in rows:
                    row.status, row.completed_at, row.lease_owner, row.lease_expires_at = "timed_out", now, None, None
                    row.last_error_code, row.last_error_message = "TASK_DEADLINE_EXCEEDED", "task deadline exceeded"
                    row.version += 1
                    row.updated_at = now
                    context = _WorkerContext(
                        row.tenant_id, row.workspace_id, "sweeper", request.headers.get("X-Request-Id", str(uuid4()))
                    )
                    await self._event(
                        session,
                        context,
                        row,
                        "task.timed_out",
                        f"timeout:{row.id}:{row.version}",
                        {"status": row.status},
                    )
                    await self._notify(session, row, "任务超时", f"{row.task_type} 已超过截止时间")
            return {"data": {"timedOut": len(rows)}}

        @router.get("/admin/task-dead-letters", operation_id="list_task_dead_letters")
        async def list_dead_letters(request: Request):
            context = await self._context(request, "admin.ops.view")
            async with self._session() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(TaskDeadLetterRow)
                            .where(*self._scope(TaskDeadLetterRow, context))
                            .order_by(TaskDeadLetterRow.created_at.desc())
                            .limit(200)
                        )
                    ).all()
                )
            return _ok(
                [
                    {
                        "id": row.id,
                        "taskId": row.task_id,
                        "reason": row.reason,
                        "status": row.status,
                        "payload": row.payload,
                        "resolution": row.resolution,
                    }
                    for row in rows
                ],
                context.request_id,
            )

        @router.post("/admin/task-dead-letters/{letter_id}/actions", operation_id="resolve_task_dead_letter")
        async def resolve_dead_letter(request: Request, letter_id: str):
            context = await self._context(request, "admin.ops.manage")
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            action, resolution = _text(body.get("action")), _text(body.get("resolution"))
            if action not in {"ignore", "reconcile", "compensate"} or not resolution:
                return _error(context.request_id, "INVALID_DEAD_LETTER_ACTION", 400)
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(TaskDeadLetterRow)
                    .where(*self._scope(TaskDeadLetterRow, context), TaskDeadLetterRow.id == letter_id)
                    .with_for_update()
                )
                if row is None:
                    return _error(context.request_id, "DEAD_LETTER_NOT_FOUND", 404)
                if row.status != "pending":
                    return _error(context.request_id, "DEAD_LETTER_ALREADY_RESOLVED", 409)
                row.status, row.resolution, row.resolved_at = action, resolution, _now()
            return _ok({"id": row.id, "status": row.status}, context.request_id)

        @router.post("/internal/notification-deliveries/claim", operation_id="claim_notification_deliveries")
        async def claim_deliveries(request: Request):
            self._worker(request)
            now = _now()
            async with self._session() as session, session.begin():
                rows = list(
                    (
                        await session.scalars(
                            select(NotificationDeliveryRow)
                            .where(
                                NotificationDeliveryRow.status.in_(("queued", "retrying")),
                                or_(
                                    NotificationDeliveryRow.next_attempt_at.is_(None),
                                    NotificationDeliveryRow.next_attempt_at <= now,
                                ),
                            )
                            .order_by(NotificationDeliveryRow.created_at)
                            .with_for_update(skip_locked=True)
                            .limit(50)
                        )
                    ).all()
                )
                for row in rows:
                    row.status, row.attempt_count, row.updated_at = "sending", row.attempt_count + 1, now
            return {"data": [_delivery(row) for row in rows]}

        @router.post(
            "/internal/notification-deliveries/{delivery_id}/receipt",
            operation_id="record_notification_delivery_receipt",
        )
        async def delivery_receipt(request: Request, delivery_id: str):
            self._worker(request)
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            outcome, receipt = _text(body.get("outcome")), body.get("receipt")
            async with self._session() as session, session.begin():
                row = await session.scalar(
                    select(NotificationDeliveryRow).where(NotificationDeliveryRow.id == delivery_id).with_for_update()
                )
                if row is None:
                    return _error(request.headers.get("X-Request-Id", ""), "DELIVERY_NOT_FOUND", 404)
                if outcome == "delivered":
                    row.status = "delivered"
                    row.provider_receipt = (
                        dict(cast(Mapping[str, object], receipt)) if isinstance(receipt, Mapping) else {}
                    )
                elif outcome == "failed":
                    row.last_error = _text(body.get("error")) or "provider delivery failed"
                    row.status = "retrying" if row.attempt_count < 5 else "failed"
                    row.next_attempt_at = (
                        _now() + timedelta(seconds=min(1800, 30 * 2**row.attempt_count))
                        if row.status == "retrying"
                        else None
                    )
                else:
                    return _error(request.headers.get("X-Request-Id", ""), "INVALID_DELIVERY_RECEIPT", 400)
                row.updated_at = _now()
            return {"data": _delivery(row)}

        return router

    async def _context(self, request: Request, permission: str):
        if self._unavailable_code:
            raise HTTPException(503, detail={"code": self._unavailable_code})
        context = await self._resolver(request)
        if permission not in context.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED", "permission": permission})
        return context

    def _session(self):
        if self._sessions is None:
            raise HTTPException(503, detail={"code": self._unavailable_code or "TASK_RUNTIME_UNAVAILABLE"})
        return self._sessions()

    def _worker(self, request: Request) -> None:
        supplied = request.headers.get("X-Xingjing-Worker-Token", "")
        if not self._worker_token or not hmac.compare_digest(supplied, self._worker_token):
            raise HTTPException(401, detail={"code": "WORKER_UNAUTHENTICATED"})

    @staticmethod
    def _scope(row_type: Any, context: Any) -> list[Any]:
        return [row_type.tenant_id == context.tenant_id, row_type.workspace_id == context.workspace_id]

    async def _event(
        self,
        session: AsyncSession,
        context: Any,
        task: TaskRow,
        event_type: str,
        event_key: str,
        payload: dict[str, object],
    ) -> None:
        session.add(
            TaskEventRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                event_key=event_key,
                event_type=event_type,
                aggregate_id=task.id,
                actor_id=context.actor_id,
                request_id=context.request_id,
                payload=payload,
            )
        )

    @staticmethod
    async def _notify(session: AsyncSession, task: TaskRow, title: str, body: str) -> None:
        notification = NotificationRow(
            tenant_id=task.tenant_id,
            workspace_id=task.workspace_id,
            recipient_id=task.created_by,
            category="task",
            title=title,
            body=body,
            resource_type="task",
            resource_id=task.id,
            dedupe_key=f"task:{task.id}:{task.status}",
            metadata_json={"taskType": task.task_type, "status": task.status},
        )
        session.add(notification)
        await session.flush()
        preferences = list(
            (
                await session.scalars(
                    select(NotificationPreferenceRow).where(
                        NotificationPreferenceRow.tenant_id == task.tenant_id,
                        NotificationPreferenceRow.workspace_id == task.workspace_id,
                        NotificationPreferenceRow.recipient_id == task.created_by,
                        NotificationPreferenceRow.enabled.is_(True),
                        NotificationPreferenceRow.destination.is_not(None),
                    )
                )
            ).all()
        )
        for preference in preferences:
            session.add(
                NotificationDeliveryRow(
                    tenant_id=task.tenant_id,
                    workspace_id=task.workspace_id,
                    notification_id=notification.id,
                    recipient_id=task.created_by,
                    channel=preference.channel,
                    destination=preference.destination or "",
                    template_key="task.status",
                    payload={"title": title, "body": body, "taskId": task.id, "status": task.status},
                )
            )


class _WorkerScope:
    def __init__(self, tenant_id: str, workspace_id: str) -> None:
        self.tenant_id, self.workspace_id = tenant_id, workspace_id


class _WorkerContext(_WorkerScope):
    def __init__(self, tenant_id: str, workspace_id: str, actor_id: str, request_id: str) -> None:
        super().__init__(tenant_id, workspace_id)
        self.actor_id, self.request_id = actor_id, request_id


def create_production_tasks_runtime(*, database_url: str | None = None) -> PlatformTasksRuntime:
    resolver = TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = (
        database_url
        or os.environ.get("XINGJING_TASK_DATABASE_URL")
        or os.environ.get("XINGJING_PROJECT_DATABASE_URL", "")
    ).strip()
    worker_token = os.environ.get("XINGJING_TASK_WORKER_TOKEN", "").strip()
    if not url:
        return PlatformTasksRuntime(
            None, resolver, worker_token=worker_token, unavailable_code="XINGJING_TASK_DATABASE_URL_REQUIRED"
        )
    if "+asyncpg" not in url and "+aiosqlite" not in url:
        return PlatformTasksRuntime(
            None, resolver, worker_token=worker_token, unavailable_code="XINGJING_TASK_DATABASE_URL_MUST_BE_ASYNC"
        )
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return PlatformTasksRuntime(
            None, resolver, worker_token=worker_token, unavailable_code="XINGJING_TASK_DATABASE_UNAVAILABLE"
        )
    return PlatformTasksRuntime(
        async_sessionmaker(engine, expire_on_commit=False), resolver, worker_token=worker_token, engine=engine
    )


async def _json(request: Request) -> Mapping[str, object] | JSONResponse:
    try:
        value = await request.json()
    except ValueError:
        return _error(request.headers.get("X-Request-Id", ""), "INVALID_JSON", 400)
    return (
        cast(Mapping[str, object], value)
        if isinstance(value, Mapping)
        else _error(request.headers.get("X-Request-Id", ""), "INVALID_REQUEST_BODY", 400)
    )


def _task(row: TaskRow, *, include_payload: bool = False) -> dict[str, object]:
    value: dict[str, object] = {
        "id": row.id,
        "taskType": row.task_type,
        "projectId": row.project_id or "",
        "batchId": row.batch_id or "",
        "status": row.status,
        "progress": row.progress,
        "attemptCount": row.attempt_count,
        "maxAttempts": row.max_attempts,
        "version": row.version,
        "error": {"code": row.last_error_code, "message": row.last_error_message} if row.last_error_code else None,
        "result": row.result,
        "createdAt": _iso(row.created_at),
        "updatedAt": _iso(row.updated_at),
    }
    if include_payload:
        value["payload"] = row.payload
    return value


def _event(row: TaskEventRow) -> dict[str, object]:
    return {
        "id": row.id,
        "sequence": row.sequence,
        "type": row.event_type,
        "schemaVersion": row.schema_version,
        "aggregateId": row.aggregate_id,
        "payload": row.payload,
        "createdAt": _iso(row.created_at),
    }


def _notification(row: NotificationRow) -> dict[str, object]:
    return {
        "id": row.id,
        "category": row.category,
        "title": row.title,
        "body": row.body,
        "resourceType": row.resource_type,
        "resourceId": row.resource_id,
        "metadata": row.metadata_json,
        "readAt": _iso(row.read_at) if row.read_at else None,
        "createdAt": _iso(row.created_at),
    }


def _delivery(row: NotificationDeliveryRow) -> dict[str, object]:
    return {
        "id": row.id,
        "notificationId": row.notification_id,
        "recipientId": row.recipient_id,
        "channel": row.channel,
        "destination": row.destination,
        "templateKey": row.template_key,
        "payload": row.payload,
        "status": row.status,
        "attemptCount": row.attempt_count,
        "providerReceipt": row.provider_receipt,
        "lastError": row.last_error,
    }


def _fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    candidate = value if isinstance(value, int) and not isinstance(value, bool) else default
    return min(max(candidate, minimum), maximum)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _optional_text(value: object) -> str | None:
    result = _text(value)
    return result or None


def _iso(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat()


def _ok(data: object, request_id: str):
    return {"data": data, "meta": {"requestId": request_id}}


def _error(request_id: str, code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": code, "retryable": status_code >= 500, "details": []},
            "meta": {"requestId": request_id},
        },
    )
