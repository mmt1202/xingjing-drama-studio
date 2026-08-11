from __future__ import annotations

import hashlib
import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import JSON, BigInteger, DateTime, Index, String, Text, UniqueConstraint, create_engine, select, text
from sqlalchemy.engine import CursorResult, Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.xingjing_admin_governance.runtime import SensitiveApprovalRow
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
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource: Mapped[str] = mapped_column(String(32), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    settings: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommandRow(Base):
    __tablename__ = "xingjing_admin_business_commands"
    __table_args__ = (UniqueConstraint("tenant_id", "workspace_id", "actor_id", "idempotency_key", name="uq_xj_admin_business_command_scope"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
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
        search: str = Query("", max_length=200), status: str | None = Query(None, max_length=64),
        target_tenant_id: str | None = Query(None, alias="targetTenantId", max_length=128),
        target_workspace_id: str | None = Query(None, alias="targetWorkspaceId", max_length=128),
        approval_id: str | None = Query(None, alias="approvalId", max_length=36),
        access_reason: str | None = Query(None, alias="accessReason", max_length=1000),
        trusted: TrustedWorkspaceContext = Depends(context),
    ) -> dict[str, object]:
        with sessions.begin() as session:
            tenant_id, workspace_id = _resolve_data_scope(
                session, trusted, target_tenant_id=target_tenant_id, target_workspace_id=target_workspace_id,
                approval_id=approval_id, access_reason=access_reason,
            )
            records, total = _records(
                session, resource, tenant_id, workspace_id, page=page, page_size=page_size,
                search=search.strip(), status=status,
            )
            return {"items": records, "page": page, "pageSize": page_size, "total": total}

    @router.post("/business-objects/actions")
    def act(
        payload: ActionPayload, trusted: TrustedWorkspaceContext = Depends(context),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        if "admin.business.manage" not in trusted.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
        if not idempotency_key.strip() or payload.version < 0:
            raise HTTPException(422, detail={"code": "INVALID_COMMAND"})
        if payload.action == "申请跨域访问":
            return _request_cross_scope_approval(sessions, trusted, payload, idempotency_key)
        resource, status = _action(payload.action)
        canonical_payload = json.dumps(payload.payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(
            f"{resource}:{payload.objectId}:{payload.version}:{payload.action}:{payload.reason or ''}:{canonical_payload}".encode()
        ).hexdigest()
        now = datetime.now(UTC)
        with sessions.begin() as session:
            command_scope = payload.payload or {}
            target_tenant, target_workspace = _resolve_data_scope(
                session, trusted,
                target_tenant_id=str(command_scope.get("targetTenantId", "")) or None,
                target_workspace_id=str(command_scope.get("targetWorkspaceId", "")) or None,
                approval_id=str(command_scope.get("approvalId", "")) or None,
                access_reason=str(command_scope.get("accessReason", "")) or None,
            )
            previous = session.scalar(select(CommandRow).where(
                CommandRow.tenant_id == trusted.tenant_id, CommandRow.workspace_id == trusted.workspace_id,
                CommandRow.actor_id == trusted.actor_id, CommandRow.idempotency_key == idempotency_key))
            if previous:
                if previous.request_fingerprint != fingerprint:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                return {"requestId": trusted.request_id, "status": "succeeded"}
            current = session.get(GovernanceRow, (target_tenant, target_workspace, resource, payload.objectId))
            current_version = current.version if current else payload.version
            if current and current.version != payload.version:
                raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
            before = current.status if current else "unchanged"
            effective_status = _apply_authoritative_status(
                session, resource, payload.objectId, status, payload.version,
                actor_id=trusted.actor_id, reason=payload.reason or "", tenant_id=target_tenant,
                workspace_id=target_workspace,
            )
            if current:
                current.status, current.version, current.updated_by, current.updated_at = (
                    effective_status, current_version + 1, trusted.actor_id, now)
                current.settings = payload.payload or current.settings
            else:
                session.add(GovernanceRow(tenant_id=target_tenant, workspace_id=target_workspace,
                                          resource=resource, object_id=payload.objectId, status=effective_status,
                                          settings=payload.payload or {},
                                          version=current_version + 1, updated_by=trusted.actor_id, updated_at=now))
            session.add(CommandRow(id=str(uuid4()), tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
                                   actor_id=trusted.actor_id, idempotency_key=idempotency_key,
                                   request_fingerprint=fingerprint, result_json='{"status":"succeeded"}', created_at=now))
            session.add(AuditRow(id=str(uuid4()), actor_id=trusted.actor_id, request_id=trusted.request_id,
                                 tenant_id=target_tenant, workspace_id=target_workspace,
                                 resource=resource, object_id=payload.objectId, action=payload.action,
                                 before_status=before, after_status=effective_status, occurred_at=now))
            session.add(OutboxRow(
                tenant_id=target_tenant, workspace_id=target_workspace,
                event_type="admin.business.governance_changed", aggregate_type=resource,
                aggregate_id=payload.objectId,
                payload={"resource": resource, "objectId": payload.objectId, "status": effective_status,
                         "action": payload.action, "requestId": trusted.request_id},
                schema_version=1, created_at=now, published_at=None,
            ))
        return {"requestId": trusted.request_id, "status": "succeeded"}

    return router


def _request_cross_scope_approval(
    sessions: sessionmaker[Session], trusted: TrustedWorkspaceContext, payload: ActionPayload,
    idempotency_key: str,
) -> dict[str, object]:
    target_workspace = payload.objectId.strip()
    target_tenant = str((payload.payload or {}).get("targetTenantId", "")).strip()
    if not target_workspace or not target_tenant or not (payload.reason or "").strip():
        raise HTTPException(422, detail={"code": "CROSS_SCOPE_REQUEST_INVALID"})
    now = datetime.now(UTC)
    fingerprint = hashlib.sha256(
        f"cross_scope:{target_tenant}:{target_workspace}:{payload.reason}".encode()
    ).hexdigest()
    with sessions.begin() as session:
        previous = session.scalar(select(CommandRow).where(
            CommandRow.tenant_id == trusted.tenant_id, CommandRow.workspace_id == trusted.workspace_id,
            CommandRow.actor_id == trusted.actor_id, CommandRow.idempotency_key == idempotency_key,
        ))
        if previous:
            if previous.request_fingerprint != fingerprint:
                raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
            result = json.loads(previous.result_json)
            return cast(dict[str, object], result)
        approval = SensitiveApprovalRow(
            approval_id=str(uuid4()), tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
            action="admin.business.cross_scope", target_type="workspace", target_id=target_workspace,
            request_payload={"targetTenantId": target_tenant, "reason": payload.reason,
                             "expiresAt": (now + timedelta(hours=24)).isoformat()},
            requested_by=trusted.actor_id, votes=[], status="pending", version=1,
            created_at=now, updated_at=now,
        )
        result: dict[str, object] = {
            "requestId": trusted.request_id, "status": "processing", "objectId": approval.approval_id,
        }
        session.add(approval)
        session.add(CommandRow(
            id=str(uuid4()), tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
            actor_id=trusted.actor_id, idempotency_key=idempotency_key,
            request_fingerprint=fingerprint, result_json=json.dumps(result), created_at=now,
        ))
        session.add(AuditRow(
            id=str(uuid4()), tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
            actor_id=trusted.actor_id, request_id=trusted.request_id, resource="cross_scope_approval",
            object_id=approval.approval_id, action=payload.action, before_status="none",
            after_status="pending", occurred_at=now,
        ))
        session.add(OutboxRow(
            tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
            event_type="admin.business.cross_scope_requested", aggregate_type="approval",
            aggregate_id=approval.approval_id,
            payload={"approvalId": approval.approval_id, "targetTenantId": target_tenant,
                     "targetWorkspaceId": target_workspace, "requestId": trusted.request_id},
            schema_version=1, created_at=now, published_at=None,
        ))
    return result


def _records(
    session: Session, resource: str, tenant_id: str, workspace_id: str, *, page: int = 1, page_size: int = 20,
    search: str = "", status: str | None = None,
) -> tuple[list[dict[str, object]], int]:
    queries = {
        "users": "SELECT u.id::text id, u.display_name name, u.status, u.version, u.updated_at, jsonb_build_object('email',u.email,'role',wm.role_key) details FROM identity.users u JOIN identity.workspace_members wm ON wm.user_id=u.id WHERE wm.workspace_id::text=:ws AND wm.status<>'REMOVED'",
        "teams": "SELECT id::text id, name, status, version, updated_at, jsonb_build_object('slug',slug) details FROM identity.workspaces WHERE id::text=:ws",
        "projects": "SELECT id, name, CASE WHEN deleted_at IS NOT NULL THEN 'deleted' ELSE production_status END status, version, updated_at, jsonb_build_object('type',project_type,'targetPlatform',target_platform,'ownerId',owner_id) details FROM xingjing_projects WHERE tenant_id=:tenant AND workspace_id=:ws",
        "tasks": "SELECT task.task_id id, COALESCE(task.snapshot->>'name', task.task_id) name, task.status, task.version, task.updated_at, jsonb_build_object('projectId',task.project_id,'kind',task.snapshot->>'kind','progress',task.snapshot->>'progress') details FROM xingjing_generation_tasks task JOIN xingjing_projects project ON project.id=task.project_id AND project.workspace_id=task.workspace_id WHERE project.tenant_id=:tenant AND task.workspace_id=:ws",
        "content": """
            SELECT asset_id id, name, 'asset' status, revision version,
                   COALESCE((SELECT av.created_at::timestamptz FROM xingjing_asset_versions av
                     WHERE av.tenant_id=a.tenant_id AND av.workspace_id=a.workspace_id
                       AND av.asset_id=a.asset_id AND av.version_id=a.current_version_id), now()) updated_at,
                   jsonb_build_object('contentType','asset','kind',kind,'projectId',owner_project_id) details
              FROM xingjing_asset_records a WHERE tenant_id=:tenant AND workspace_id=:ws
            UNION ALL
            SELECT generated.asset_id, generated.asset_id, 'generated', 1, generated.created_at,
                   jsonb_build_object('contentType',generated.media_type,'projectId',generated.project_id,'taskId',generated.task_id)
              FROM xingjing_generated_assets generated JOIN xingjing_projects project
                ON project.id=generated.project_id AND project.workspace_id=generated.workspace_id
             WHERE project.tenant_id=:tenant AND generated.workspace_id=:ws
            UNION ALL
            SELECT final_video_id || ':' || version_id, final_video_id, 'final_video', 1, created_at,
                   jsonb_build_object('contentType','final_video','projectId',project_id,'versionId',version_id)
              FROM xingjing_editing_final_video_versions WHERE tenant_id=:tenant AND workspace_id=:ws
        """,
    }
    if resource == "dashboard":
        counts: dict[str, int] = {}
        for name in ("users", "projects", "tasks"):
            _, counts[name] = _records(session, name, tenant_id, workspace_id, page=1, page_size=1)
        counts["models"] = int(session.scalar(text(
            "SELECT count(*) FROM xingjing_model_definitions WHERE active=true"
        )) or 0)
        counts["revenue"] = int(session.scalar(text("""
            SELECT COALESCE(sum((settlement->>'amount_minor')::bigint),0)
              FROM xingjing_commercial_orders orders
              CROSS JOIN LATERAL jsonb_array_elements(COALESCE(orders.aggregate->'settlements','[]'::jsonb)) settlement
             WHERE orders.owner_workspace_id=:ws AND settlement->>'status'='paid'
        """), {"ws": workspace_id}) or 0)
        counts["reviews"] = int(session.scalar(text(
            "SELECT count(*) FROM xingjing_compliance_reviews WHERE tenant_id=:tenant AND workspace_id=:ws "
            "AND (conclusion<>'approved' OR manual_review_status IN ('pending','appealed'))"
        ), {"tenant": tenant_id, "ws": workspace_id}) or 0)
        counts["tickets"] = int(session.scalar(text(
            "SELECT count(*) FROM xingjing_admin_governance_objects WHERE tenant_id=:tenant AND workspace_id=:ws "
            "AND resource='tickets' AND status NOT IN ('closed','resolved')"
        ), {"tenant": tenant_id, "ws": workspace_id}) or 0)
        unhealthy = int(session.scalar(text("""
            SELECT (SELECT count(*) FROM xingjing_generation_tasks WHERE workspace_id=:ws AND status='failed')
                 + (SELECT count(*) FROM xingjing_outbox WHERE tenant_id=:tenant AND workspace_id=:ws AND published_at IS NULL)
        """), {"tenant": tenant_id, "ws": workspace_id}) or 0)
        counts["services"] = unhealthy
        labels = {"users": "平台用户", "projects": "活跃项目", "tasks": "生成任务", "models": "可用模型",
                  "revenue": "已结算收入（分）", "reviews": "待处理审核", "tickets": "未结工单", "services": "服务异常"}
        now = datetime.now(UTC).isoformat()
        dashboard = [
            {"id": name, "name": labels[name], "status": "attention" if name in {"reviews", "tickets", "services"} and value else "current",
             "version": 1, "updatedAt": now, "details": {"count": value}}
            for name, value in counts.items()
        ]
        settings = session.get(GovernanceRow, (tenant_id, workspace_id, "dashboard", "dashboard"))
        if settings is not None:
            dashboard.append({"id": "dashboard", "name": "看板配置", "status": settings.status,
                              "version": settings.version, "updatedAt": settings.updated_at.isoformat(),
                              "details": settings.settings})
        return dashboard, len(dashboard)
    statement = queries.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    filtered = f"SELECT * FROM ({statement}) source WHERE (:search='' OR lower(id::text) LIKE :needle OR lower(name) LIKE :needle) AND (:status IS NULL OR lower(status)=ANY(string_to_array(lower(:status),',')))"
    params = {"tenant": tenant_id, "ws": workspace_id, "search": search, "needle": f"%{search.lower()}%", "status": status,
              "limit": page_size, "offset": (page - 1) * page_size}
    total = int(session.scalar(text(f"SELECT count(*) FROM ({filtered}) counted"), params) or 0)
    rows = session.execute(text(f"{filtered} ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"), params).mappings().all()
    governance = {row.object_id: row for row in session.scalars(select(GovernanceRow).where(
        GovernanceRow.tenant_id == tenant_id, GovernanceRow.workspace_id == workspace_id,
        GovernanceRow.resource == resource)).all()}
    records: list[dict[str, object]] = []
    for row in rows:
        item_id = str(row["id"])
        governed = governance.get(item_id)
        records.append({"id": item_id, "name": str(row["name"]),
                       "status": governed.status if governed else str(row["status"]),
                       "version": governed.version if governed else int(row["version"]),
                       "updatedAt": row["updated_at"].isoformat(), "details": row.get("details") or {}})
    return records, total


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


def _resolve_data_scope(
    session: Session, trusted: TrustedWorkspaceContext, *, target_tenant_id: str | None,
    target_workspace_id: str | None, approval_id: str | None, access_reason: str | None,
) -> tuple[str, str]:
    target_tenant = (target_tenant_id or trusted.tenant_id).strip()
    target_workspace = (target_workspace_id or trusted.workspace_id).strip()
    if target_tenant == trusted.tenant_id and target_workspace == trusted.workspace_id:
        return target_tenant, target_workspace
    if not approval_id or not access_reason or not access_reason.strip():
        raise HTTPException(403, detail={"code": "CROSS_SCOPE_APPROVAL_REQUIRED"})
    approval = session.get(SensitiveApprovalRow, approval_id)
    expires_at = approval.request_payload.get("expiresAt") if approval else None
    approved_tenant = approval.request_payload.get("targetTenantId") if approval else None
    try:
        expires = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        expires = datetime.min.replace(tzinfo=UTC)
    if (
        approval is None or approval.status != "approved" or approval.action != "admin.business.cross_scope"
        or approval.tenant_id != trusted.tenant_id or approval.workspace_id != trusted.workspace_id
        or approval.requested_by != trusted.actor_id
        or approval.target_type != "workspace" or approval.target_id != target_workspace
        or approved_tenant != target_tenant or expires <= datetime.now(UTC)
    ):
        raise HTTPException(403, detail={"code": "CROSS_SCOPE_APPROVAL_INVALID"})
    session.add(AuditRow(
        id=str(uuid4()), tenant_id=target_tenant, workspace_id=target_workspace,
        actor_id=trusted.actor_id, request_id=trusted.request_id, resource="cross_scope_query",
        object_id=target_workspace, action="query", before_status="approved",
        after_status=access_reason.strip(), occurred_at=datetime.now(UTC),
    ))
    return target_tenant, target_workspace


def _apply_authoritative_status(
    session: Session, resource: str, object_id: str, status: str, version: int, *, actor_id: str, reason: str,
    tenant_id: str, workspace_id: str,
) -> str:
    if resource == "users":
        update_result = cast(CursorResult[tuple[object, ...]], session.execute(text(
            "UPDATE identity.users u SET status=:status, version=u.version+1, updated_at=:now "
            "WHERE u.id::text=:id AND u.version=:version AND EXISTS "
            "(SELECT 1 FROM identity.workspace_members wm WHERE wm.user_id=u.id AND wm.workspace_id::text=:ws AND wm.status<>'REMOVED')"
        ), {"status": status, "now": datetime.now(UTC), "id": object_id, "version": version, "ws": workspace_id}))
        if update_result.rowcount != 1:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        return status
    if resource == "teams":
        update_result = cast(CursorResult[tuple[object, ...]], session.execute(text(
            "UPDATE identity.workspaces SET status=:status, version=version+1, updated_at=:now "
            "WHERE id::text=:id AND id::text=:ws AND version=:version"
        ), {"status": status, "now": datetime.now(UTC), "id": object_id, "version": version, "ws": workspace_id}))
        if update_result.rowcount != 1:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        return status
    if resource == "projects":
        result = cast(CursorResult[tuple[object, ...]], session.execute(text(
            "UPDATE xingjing_projects SET production_status='frozen', version=version+1, updated_at=:now "
            "WHERE id=:id AND tenant_id=:tenant AND workspace_id=:ws AND version=:version AND deleted_at IS NULL"
        ), {"now": datetime.now(UTC), "id": object_id, "tenant": tenant_id,
            "ws": workspace_id, "version": version}))
        if result.rowcount != 1:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        return "frozen"
    if resource == "tasks":
        now = datetime.now(UTC)
        evidence = {"requestedBy": actor_id, "reason": reason, "requestedAt": now.isoformat()}
        result = cast(CursorResult[tuple[object, ...]], session.execute(text(
            "UPDATE xingjing_generation_tasks SET snapshot=jsonb_set(snapshot,'{adminCompensation}',CAST(:evidence AS jsonb),true), "
            "version=version+1, updated_at=:now WHERE task_id=:id AND workspace_id=:ws AND version=:version "
            "AND status IN ('failed','cancelled','succeeded') AND EXISTS (SELECT 1 FROM xingjing_projects project "
            "WHERE project.id=xingjing_generation_tasks.project_id AND project.workspace_id=:ws AND project.tenant_id=:tenant)"
        ), {"evidence": json.dumps(evidence, ensure_ascii=False), "now": now, "id": object_id,
            "tenant": tenant_id, "ws": workspace_id, "version": version}))
        if result.rowcount != 1:
            raise HTTPException(409, detail={"code": "TASK_NOT_TERMINAL_OR_VERSION_CONFLICT"})
        return "compensation_requested"
    if resource == "content":
        if not reason.strip():
            raise HTTPException(422, detail={"code": "GOVERNANCE_REASON_REQUIRED"})
        exists = session.scalar(text("""
            SELECT EXISTS(
              SELECT 1 FROM xingjing_asset_records WHERE tenant_id=:tenant AND workspace_id=:ws AND asset_id=:id
              UNION ALL SELECT 1 FROM xingjing_generated_assets generated WHERE workspace_id=:ws AND asset_id=:id
                AND EXISTS (SELECT 1 FROM xingjing_projects project WHERE project.id=generated.project_id
                            AND project.workspace_id=:ws AND project.tenant_id=:tenant)
              UNION ALL SELECT 1 FROM xingjing_editing_final_video_versions
                WHERE tenant_id=:tenant AND workspace_id=:ws AND (final_video_id || ':' || version_id)=:id
            )
        """), {"tenant": tenant_id, "ws": workspace_id, "id": object_id})
        if not exists:
            raise HTTPException(404, detail={"code": "CONTENT_NOT_FOUND"})
        return "blocked"
    return status
