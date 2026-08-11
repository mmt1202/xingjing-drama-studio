from __future__ import annotations

import inspect
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import BigInteger, DateTime, Index, String, Text, UniqueConstraint, create_engine, select, text
from sqlalchemy.engine import CursorResult, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import OutboxRow


class Base(DeclarativeBase):
    pass


class GovernanceRow(Base):
    __tablename__ = "xingjing_admin_business_governance"
    resource: Mapped[str] = mapped_column(String(32), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommandRow(Base):
    __tablename__ = "xingjing_admin_business_commands"
    __table_args__ = (UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_admin_business_command"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(512), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_admin_business_audit"
    __table_args__ = (Index("ix_xj_admin_business_audit_request", "request_id", "occurred_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str | None] = mapped_column(String(128))
    workspace_id: Mapped[str | None] = mapped_column(String(128))
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    resource: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before_status: Mapped[str] = mapped_column(String(32), nullable=False)
    after_status: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionPayload(BaseModel):
    action: str
    objectId: str
    version: int
    reason: str | None = None
    payload: dict[str, object] | None = None


type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


@dataclass(slots=True)
class AdminBusinessRuntime:
    router: APIRouter
    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def create_unavailable_admin_business_router(code: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-business"])

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"code": code})

    router.add_api_route("/session/context", unavailable, methods=["GET"])
    router.add_api_route("/business-objects", unavailable, methods=["GET"])
    router.add_api_route("/business-objects/actions", unavailable, methods=["POST"])
    return router


def create_production_admin_business_runtime(
    *, database_url: str | None = None, context_resolver: ContextResolver | None = None,
) -> AdminBusinessRuntime:
    url = (database_url or os.environ.get("XINGJING_ADMIN_DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError("XINGJING_ADMIN_DATABASE_URL_REQUIRED")
    parsed = make_url(url)
    if parsed.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise RuntimeError("XINGJING_ADMIN_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return AdminBusinessRuntime(_router(sessions, resolver), engine)


def _router(sessions: sessionmaker[Session], resolver: ContextResolver) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-business"])

    async def context(request: Request) -> TrustedWorkspaceContext:
        value = resolver(request)
        trusted = await value if inspect.isawaitable(value) else value
        if "admin.business.view" not in trusted.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
        return trusted

    @router.get("/session/context")
    def session_context(trusted: TrustedWorkspaceContext = Depends(context)) -> dict[str, object]:
        return {"actor": {"id": trusted.actor_id, "displayName": trusted.actor_id, "role": trusted.role},
                "workspace": {"id": trusted.workspace_id, "name": trusted.workspace_id},
                "permissions": sorted(trusted.permissions), "dataScope": f"workspace:{trusted.workspace_id}"}

    @router.get("/business-objects")
    def list_objects(
        resource: str = Query("dashboard"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(context),
    ) -> dict[str, object]:
        with sessions() as session:
            records = _records(session, resource, trusted.workspace_id)
            total = len(records)
            start = (page - 1) * page_size
            return {"items": records[start:start + page_size], "page": page,
                    "pageSize": page_size, "total": total}

    @router.post("/business-objects/actions")
    def act(
        payload: ActionPayload, trusted: TrustedWorkspaceContext = Depends(context),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        if "admin.business.manage" not in trusted.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
        if not idempotency_key.strip() or payload.version < 0:
            raise HTTPException(422, detail={"code": "INVALID_COMMAND"})
        resource, status = _action(payload.action)
        fingerprint = f"{resource}:{payload.objectId}:{payload.version}:{payload.action}:{payload.reason or ''}"
        now = datetime.now(UTC)
        with sessions.begin() as session:
            previous = session.scalar(select(CommandRow).where(
                CommandRow.actor_id == trusted.actor_id, CommandRow.idempotency_key == idempotency_key))
            if previous:
                if previous.request_fingerprint != fingerprint:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                return {"requestId": trusted.request_id, "status": "succeeded"}
            current = session.get(GovernanceRow, (resource, payload.objectId))
            current_version = current.version if current else payload.version
            if current and current.version != payload.version:
                raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
            before = current.status if current else "unchanged"
            if current:
                current.status, current.version, current.updated_by, current.updated_at = (
                    status, current_version + 1, trusted.actor_id, now)
            else:
                session.add(GovernanceRow(resource=resource, object_id=payload.objectId, status=status,
                                          version=current_version + 1, updated_by=trusted.actor_id, updated_at=now))
            _apply_authoritative_status(session, resource, payload.objectId, status, payload.version)
            session.add(CommandRow(id=str(uuid4()), actor_id=trusted.actor_id, idempotency_key=idempotency_key,
                                   request_fingerprint=fingerprint, result_json='{"status":"succeeded"}', created_at=now))
            session.add(AuditRow(id=str(uuid4()), actor_id=trusted.actor_id, request_id=trusted.request_id,
                                 tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
                                 resource=resource, object_id=payload.objectId, action=payload.action,
                                 before_status=before, after_status=status, occurred_at=now))
            session.add(OutboxRow(
                tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
                event_type="admin.business.governance_changed", aggregate_type=resource,
                aggregate_id=payload.objectId,
                payload={"resource": resource, "objectId": payload.objectId, "status": status,
                         "action": payload.action, "requestId": trusted.request_id},
                schema_version=1, created_at=now, published_at=None,
            ))
        return {"requestId": trusted.request_id, "status": "succeeded"}

    return router


def _records(session: Session, resource: str, workspace_id: str) -> list[dict[str, object]]:
    queries = {
        "users": "SELECT u.id::text id, u.display_name name, u.status, u.version, u.updated_at FROM identity.users u JOIN identity.workspace_members wm ON wm.user_id=u.id WHERE wm.workspace_id::text=:ws AND wm.status<>'REMOVED' ORDER BY u.updated_at DESC",
        "teams": "SELECT id::text id, name, status, version, updated_at FROM identity.workspaces WHERE id::text=:ws ORDER BY updated_at DESC",
        "projects": "SELECT id, name, CASE WHEN deleted_at IS NULL THEN 'active' ELSE 'deleted' END status, version, updated_at FROM xingjing_projects WHERE workspace_id=:ws ORDER BY updated_at DESC",
        "tasks": "SELECT task_id id, task_id name, status, version, updated_at FROM xingjing_generation_tasks WHERE workspace_id=:ws ORDER BY updated_at DESC",
        "content": "SELECT id, name, CASE WHEN deleted_at IS NULL THEN 'active' ELSE 'deleted' END status, version, updated_at FROM xingjing_projects WHERE workspace_id=:ws ORDER BY updated_at DESC",
    }
    if resource == "dashboard":
        dashboard: list[dict[str, object]] = []
        for name in ("users", "teams", "projects", "tasks", "content"):
            count = len(_records(session, name, workspace_id))
            dashboard.append({"id": name, "name": name, "status": "healthy", "version": 1,
                           "updatedAt": datetime.now(UTC).isoformat(), "count": count})
        return dashboard
    statement = queries.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    rows = session.execute(text(statement), {"ws": workspace_id}).mappings().all()
    governance = {row.object_id: row for row in session.scalars(select(GovernanceRow).where(
        GovernanceRow.resource == resource)).all()}
    records: list[dict[str, object]] = []
    for row in rows:
        item_id = str(row["id"])
        governed = governance.get(item_id)
        records.append({"id": item_id, "name": str(row["name"]),
                       "status": governed.status if governed else str(row["status"]),
                       "version": governed.version if governed else int(row["version"]),
                       "updatedAt": row["updated_at"].isoformat()})
    return records


def _action(action: str) -> tuple[str, str]:
    mapping = {
        "恢复用户": ("users", "ACTIVE"), "更新团队": ("teams", "ACTIVE"),
        "冻结项目": ("projects", "FROZEN"), "执行治理": ("content", "BLOCKED"),
        "执行补偿": ("tasks", "COMPENSATED"), "保存配置": ("dashboard", "UPDATED"),
    }
    try:
        return mapping[action]
    except KeyError as error:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"}) from error


def _apply_authoritative_status(session: Session, resource: str, object_id: str, status: str, version: int) -> None:
    tables = {"users": "identity.users", "teams": "identity.workspaces"}
    table = tables.get(resource)
    if table:
        update_result = cast(CursorResult[tuple[object, ...]], session.execute(text(
            f"UPDATE {table} SET status=:status, version=version+1, updated_at=:now WHERE id::text=:id AND version=:version"
        ), {"status": status, "now": datetime.now(UTC), "id": object_id, "version": version}))
        if update_result.rowcount != 1:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
