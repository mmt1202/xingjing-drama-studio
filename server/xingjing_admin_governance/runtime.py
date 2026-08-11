from __future__ import annotations

import inspect
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import OutboxRow


class Base(DeclarativeBase):
    pass


class GovernanceObjectRow(Base):
    __tablename__ = "xingjing_admin_governance_objects"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    resource: Mapped[str] = mapped_column(String(64), primary_key=True)
    object_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SensitiveApprovalRow(Base):
    __tablename__ = "xingjing_admin_sensitive_approvals"
    approval_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    votes: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommandRow(Base):
    __tablename__ = "xingjing_admin_governance_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "actor_id", "idempotency_key",
                         name="uq_xj_admin_governance_command_scope"),
    )
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecurityAuditRow(Base):
    __tablename__ = "xingjing_admin_security_audit"
    __table_args__ = (
        Index("ix_xj_admin_security_audit_query", "workspace_id", "request_id", "occurred_at"),
    )
    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    device: Mapped[str | None] = mapped_column(String(256))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionPayload(BaseModel):
    action: str
    objectId: str
    version: int
    resource: str | None = None
    reason: str | None = None
    payload: dict[str, object] | None = None


type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


@dataclass(slots=True)
class AdminGovernanceRuntime:
    router: APIRouter
    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def create_unavailable_admin_governance_router(code: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-governance"])

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": {"code": code}})

    router.add_api_route("/compliance", unavailable, methods=["GET"])
    router.add_api_route("/compliance/actions", unavailable, methods=["POST"])
    router.add_api_route("/security", unavailable, methods=["GET"])
    router.add_api_route("/security/actions", unavailable, methods=["POST"])
    return router


def create_production_admin_governance_runtime(
    *, database_url: str | None = None, context_resolver: ContextResolver | None = None,
) -> AdminGovernanceRuntime:
    url = (database_url or os.environ.get("XINGJING_GOVERNANCE_DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError("XINGJING_GOVERNANCE_DATABASE_URL_REQUIRED")
    if make_url(url).drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise RuntimeError("XINGJING_GOVERNANCE_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session:
            session.execute(text("SELECT 1 FROM xingjing_admin_governance_objects LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_admin_sensitive_approvals LIMIT 1"))
            session.execute(text(
                "SELECT tenant_id,workspace_id FROM xingjing_admin_governance_commands LIMIT 1"
            ))
    except SQLAlchemyError as error:
        engine.dispose()
        raise RuntimeError("XINGJING_GOVERNANCE_MIGRATION_REQUIRED") from error
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return AdminGovernanceRuntime(_router(sessions, resolver), engine)


def _router(sessions: sessionmaker[Session], resolver: ContextResolver) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-governance"])

    async def resolve(request: Request) -> TrustedWorkspaceContext:
        value = resolver(request)
        return await value if inspect.isawaitable(value) else value

    def require(trusted: TrustedWorkspaceContext, permission: str) -> None:
        if permission not in trusted.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})

    @router.get("/compliance")
    def compliance(
        resource: str = Query("reviews"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.compliance.view")
        with sessions() as session:
            records = _compliance_records(session, resource, trusted)
        return _page(records, page, page_size)

    @router.get("/security")
    def security(
        resource: str = Query("audit-logs"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        query: str | None = None, request_id: str | None = Query(None, alias="requestId"),
        actor_id: str | None = Query(None, alias="actorId"),
        object_id: str | None = Query(None, alias="objectId"),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.security.view")
        with sessions() as session:
            records = _security_records(
                session, resource, trusted, query=query, request_id=request_id,
                actor_id=actor_id, object_id=object_id,
            )
        return _page(records, page, page_size)

    def act(
        domain: str, payload: ActionPayload, trusted: TrustedWorkspaceContext,
        idempotency_key: str, request: Request,
    ) -> dict[str, object]:
        require(trusted, f"admin.{domain}.manage")
        if not idempotency_key.strip() or payload.version < 0:
            raise HTTPException(422, detail={"code": "INVALID_COMMAND"})
        fingerprint = "sha256:" + sha256(
            json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        with sessions.begin() as session:
            previous = session.scalar(select(CommandRow).where(
                CommandRow.tenant_id == trusted.tenant_id,
                CommandRow.workspace_id == trusted.workspace_id,
                CommandRow.actor_id == trusted.actor_id,
                CommandRow.idempotency_key == idempotency_key,
            ))
            if previous:
                if previous.fingerprint != fingerprint:
                    raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
                return cast(dict[str, object], json.loads(previous.result_json))
            before, after = _apply_action(session, domain, payload, trusted, now)
            result = {"requestId": trusted.request_id, "status": "succeeded", "object": after}
            session.add(CommandRow(
                command_id=str(uuid4()), tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id, actor_id=trusted.actor_id,
                idempotency_key=idempotency_key, fingerprint=fingerprint,
                result_json=json.dumps(result, ensure_ascii=False), created_at=now,
            ))
            session.add(SecurityAuditRow(
                audit_id=str(uuid4()), tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id, actor_id=trusted.actor_id,
                request_id=trusted.request_id, action=payload.action,
                object_type=domain, object_id=payload.objectId,
                before_payload=before, after_payload=after, result="success",
                ip_address=request.client.host if request.client else None,
                device=request.headers.get("User-Agent"), occurred_at=now,
            ))
            session.add(OutboxRow(
                tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
                event_type=f"admin.{domain}.governance_changed",
                aggregate_type=domain, aggregate_id=payload.objectId,
                payload={"action": payload.action, "object": after, "requestId": trusted.request_id},
                schema_version=1, created_at=now, published_at=None,
            ))
            return result

    @router.post("/compliance/actions")
    def compliance_action(
        payload: ActionPayload, request: Request,
        trusted: TrustedWorkspaceContext = Depends(resolve),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        return act("compliance", payload, trusted, idempotency_key, request)

    @router.post("/security/actions")
    def security_action(
        payload: ActionPayload, request: Request,
        trusted: TrustedWorkspaceContext = Depends(resolve),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        return act("security", payload, trusted, idempotency_key, request)

    return router


def _page(records: list[dict[str, object]], page: int, page_size: int) -> dict[str, object]:
    start = (page - 1) * page_size
    return {"items": records[start:start + page_size], "page": page,
            "pageSize": page_size, "total": len(records)}


def _record(mapping: dict[str, object]) -> dict[str, object]:
    updated = mapping.get("updated_at") or mapping.get("occurred_at") or mapping.get("created_at")
    version = mapping.get("version")
    return {
        "id": str(mapping["id"]), "name": str(mapping["name"]),
        "status": str(mapping["status"]),
        "version": version if isinstance(version, int) and not isinstance(version, bool) else 1,
        "updatedAt": updated.isoformat() if isinstance(updated, datetime) else str(updated or ""),
        "details": mapping.get("details") or {},
        "sensitive": mapping.get("sensitive") or {},
    }


def _rows(session: Session, statement: str, parameters: dict[str, object]) -> list[dict[str, object]]:
    return [_record(dict(row)) for row in session.execute(text(statement), parameters).mappings()]


def _compliance_records(
    session: Session, resource: str, trusted: TrustedWorkspaceContext,
) -> list[dict[str, object]]:
    if resource in {"reviews", "risks", "compliance"}:
        condition = ""
        if resource == "risks":
            condition = " AND (r.conclusion='blocked' OR r.manual_review_status IN ('pending','rejected','appealed'))"
        return _rows(session, f"""SELECT r.assessment_id id,
          '项目 '||r.project_id||' / '||r.project_version name,
          r.manual_review_status status,r.version,r.reviewed_at updated_at,
          jsonb_build_object('projectId',r.project_id,'projectVersion',r.project_version,
          'policyId',r.policy_id,'policyVersion',r.policy_version,'conclusion',r.conclusion,
          'inputDigest',r.input_digest,'evidenceDigest',r.evidence_digest,'evidence',r.review_payload) details
          FROM xingjing_compliance_reviews r
          WHERE r.tenant_id=:tenant AND r.workspace_id=:ws {condition}
          ORDER BY r.reviewed_at DESC,r.assessment_id""",
          {"tenant": trusted.tenant_id, "ws": trusted.workspace_id})
    if resource == "word-rules":
        return _governance_objects(session, trusted, "word-rules")
    if resource == "audit":
        return _rows(session, """SELECT event_id id,action name,result status,version,occurred_at,
          jsonb_build_object('actorId',actor_id,'requestId',request_id,'projectId',project_id,
          'projectVersion',project_version,'before',before_payload,'after',after_payload) details
          FROM xingjing_compliance_audit_events WHERE tenant_id=:tenant AND workspace_id=:ws
          ORDER BY occurred_at DESC,event_id""", {"tenant": trusted.tenant_id, "ws": trusted.workspace_id})
    raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})


def _governance_objects(
    session: Session, trusted: TrustedWorkspaceContext, resource: str,
) -> list[dict[str, object]]:
    return _rows(session, """SELECT object_id id,name,status,version,updated_at,payload details
      FROM xingjing_admin_governance_objects
      WHERE tenant_id=:tenant AND workspace_id=:ws AND resource=:resource
      ORDER BY updated_at DESC,object_id""",
      {"tenant": trusted.tenant_id, "ws": trusted.workspace_id, "resource": resource})


def _security_records(
    session: Session, resource: str, trusted: TrustedWorkspaceContext, *,
    query: str | None, request_id: str | None, actor_id: str | None, object_id: str | None,
) -> list[dict[str, object]]:
    if resource == "audit-logs":
        parameters: dict[str, object] = {
            "ws": trusted.workspace_id, "request_id": request_id,
            "actor_id": actor_id, "object_id": object_id, "query": f"%{query or ''}%",
        }
        records = _rows(session, """SELECT id::text id,action name,result status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'targetType',target_type,
          'targetId',target_id,'beforeDigest',before_digest,'afterDigest',after_digest) details
          FROM identity.audit_logs WHERE workspace_id::text=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id::text=:actor_id)
          AND (:object_id IS NULL OR target_id::text=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,id""", parameters)
        records.extend(_rows(session, """SELECT audit_id id,action name,result status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectType',object_type,
          'objectId',object_id,'before',before_payload,'after',after_payload,
          'ip',ip_address,'device',device) details
          FROM xingjing_admin_security_audit WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id=:actor_id)
          AND (:object_id IS NULL OR object_id=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,audit_id""", parameters))
        records.extend(_rows(session, """SELECT id,action name,after_status status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'resource',resource,
          'objectId',object_id,'beforeStatus',before_status,'afterStatus',after_status) details
          FROM xingjing_admin_business_audit WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id=:actor_id)
          AND (:object_id IS NULL OR object_id=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,id""", parameters))
        records.extend(_rows(session, """SELECT audit_id id,action name,result status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'domain',domain,
          'objectId',object_id,'before',before_payload,'after',after_payload) details
          FROM xingjing_admin_finance_audit WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id=:actor_id)
          AND (:object_id IS NULL OR object_id=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,audit_id""", parameters))
        records.extend(_rows(session, """SELECT event_id id,action name,result status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectType',object_type,
          'objectId',object_id,'before',before_payload,'after',after_payload) details
          FROM xingjing_team_audit_events WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id=:actor_id)
          AND (:object_id IS NULL OR object_id=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,event_id""", parameters))
        records.extend(_rows(session, """SELECT event_id id,action name,'success' status,version,occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'projectId',project_id,
          'projectVersion',project_version,'before',before_payload,'after',after_payload) details
          FROM xingjing_compliance_audit_events WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL OR actor_id=:actor_id)
          AND (:object_id IS NULL OR project_id=:object_id)
          AND (:query='' OR action ILIKE :query OR request_id ILIKE :query)
          ORDER BY occurred_at DESC,event_id""", parameters))
        records.extend(_rows(session, """SELECT audit_id id,
          COALESCE(payload->>'action','generation.audit') name,
          COALESCE(payload->>'result','success') status,1 version,occurred_at,
          jsonb_build_object('requestId',request_id,'projectId',project_id,
          'taskId',task_id,'evidence',payload) details
          FROM xingjing_generation_audit WHERE workspace_id=:ws
          AND (:request_id IS NULL OR request_id=:request_id)
          AND (:actor_id IS NULL)
          AND (:object_id IS NULL OR task_id=:object_id OR project_id=:object_id)
          AND (:query='' OR request_id ILIKE :query OR task_id ILIKE :query)
          ORDER BY occurred_at DESC,audit_id""", parameters))
        return sorted(records, key=lambda item: str(item["updatedAt"]), reverse=True)
    if resource == "login-logs":
        return _rows(session, """SELECT e.id::text id,
          COALESCE(u.display_name,'未识别登录尝试') name,lower(e.result) status,
          1 version,e.occurred_at updated_at,
          jsonb_build_object('userId',e.user_id,'reason',e.reason,'ip',e.ip_address,
          'region',e.region,'device',e.device_name,'requestId',e.request_id) details
          FROM identity.login_security_events e LEFT JOIN identity.users u ON u.id=e.user_id
          WHERE e.user_id IS NULL OR EXISTS (
            SELECT 1 FROM identity.workspace_members wm WHERE wm.user_id=e.user_id
            AND wm.workspace_id::text=:ws AND wm.status<>'REMOVED')
          ORDER BY e.occurred_at DESC,e.id""", {"ws": trusted.workspace_id})
    if resource == "approvals":
        return _rows(session, """SELECT approval_id id,action name,status,version,updated_at,
          jsonb_build_object('targetType',target_type,'targetId',target_id,'requestedBy',requested_by,
          'votes',votes,'request',request_payload) details
          FROM xingjing_admin_sensitive_approvals
          WHERE tenant_id=:tenant AND workspace_id=:ws ORDER BY updated_at DESC,approval_id""",
          {"tenant": trusted.tenant_id, "ws": trusted.workspace_id})
    if resource == "staff":
        return _rows(session, """SELECT u.id::text id,u.display_name name,u.status,
          u.version,u.updated_at,jsonb_build_object('roleKey',wm.role_key,'memberStatus',wm.status) details,
          jsonb_build_object('email',u.email) sensitive
          FROM identity.users u JOIN identity.workspace_members wm ON wm.user_id=u.id
          WHERE wm.workspace_id::text=:ws AND wm.status<>'REMOVED'
          ORDER BY u.updated_at DESC,u.id""", {"ws": trusted.workspace_id})
    if resource in {"roles", "word-rules", "announcements", "system-config"}:
        return _governance_objects(session, trusted, resource)
    raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})


def _apply_action(
    session: Session, domain: str, payload: ActionPayload,
    trusted: TrustedWorkspaceContext, now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    if domain == "compliance":
        if payload.action == "发布规则":
            return _update_governance_object(session, payload, trusted, "word-rules", "published", now)
        review = session.execute(text("""SELECT conclusion,manual_review_status,version,project_id,project_version
          FROM xingjing_compliance_reviews WHERE tenant_id=:tenant AND workspace_id=:ws
          AND assessment_id=:id FOR UPDATE"""),
          {"tenant": trusted.tenant_id, "ws": trusted.workspace_id, "id": payload.objectId}).mappings().first()
        if review is None:
            raise HTTPException(404, detail={"code": "COMPLIANCE_REVIEW_NOT_FOUND"})
        if int(review["version"]) != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        before = dict(review)
        if payload.action == "发起复核":
            next_status = "pending"
        elif payload.action == "执行审核":
            if str(review["conclusion"]) == "blocked":
                raise HTTPException(409, detail={"code": "HIGH_RISK_RELEASE_APPROVAL_REQUIRED"})
            next_status = "approved"
        elif payload.action == "执行放行":
            approval = SensitiveApprovalRow(
                approval_id=str(uuid4()), tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id, action="compliance.release",
                target_type="compliance_review", target_id=payload.objectId,
                request_payload={"reason": payload.reason or "manual release",
                                 "projectId": review["project_id"],
                                 "projectVersion": review["project_version"]},
                requested_by=trusted.actor_id, votes=[], status="pending",
                version=1, created_at=now, updated_at=now,
            )
            session.add(approval)
            return before, {"id": approval.approval_id, "status": "pending", "version": 1}
        else:
            raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})
        session.execute(text("""UPDATE xingjing_compliance_reviews
          SET manual_review_status=:status,version=version+1,reviewed_at=:now
          WHERE tenant_id=:tenant AND workspace_id=:ws AND assessment_id=:id AND version=:version"""),
          {"status": next_status, "now": now, "tenant": trusted.tenant_id,
           "ws": trusted.workspace_id, "id": payload.objectId, "version": payload.version})
        return before, {"id": payload.objectId, "status": next_status, "version": payload.version + 1}

    if payload.action == "执行审批":
        approval = session.get(SensitiveApprovalRow, payload.objectId)
        if approval is None or approval.workspace_id != trusted.workspace_id:
            raise HTTPException(404, detail={"code": "APPROVAL_NOT_FOUND"})
        if approval.version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        if approval.requested_by == trusted.actor_id or any(
            vote.get("actorId") == trusted.actor_id for vote in approval.votes
        ):
            raise HTTPException(409, detail={"code": "FOUR_EYES_APPROVAL_REQUIRED"})
        before = {"status": approval.status, "votes": approval.votes, "version": approval.version}
        approval.votes = [*approval.votes, {
            "actorId": trusted.actor_id, "decision": "approve", "requestId": trusted.request_id,
        }]
        approval.status = "approved" if len(approval.votes) >= 2 else "pending"
        approval.version += 1
        approval.updated_at = now
        if approval.status == "approved" and approval.action == "compliance.release":
            session.execute(text("""UPDATE xingjing_compliance_reviews
              SET conclusion='approved',manual_review_status='approved',version=version+1,reviewed_at=:now
              WHERE tenant_id=:tenant AND workspace_id=:ws AND assessment_id=:id"""),
              {"now": now, "tenant": trusted.tenant_id,
               "ws": trusted.workspace_id, "id": approval.target_id})
        return before, {"id": approval.approval_id, "status": approval.status,
                        "votes": approval.votes, "version": approval.version}
    if payload.action == "标记风险":
        return {"version": payload.version}, {
            "id": payload.objectId, "status": "risk_marked", "version": payload.version + 1,
        }
    resources = {
        "保存权限": ("roles", "active"), "发布规则": ("word-rules", "published"),
        "更新员工": ("staff", "active"), "发布公告": ("announcements", "published"),
        "发布配置": ("system-config", "published"),
    }
    if payload.action in resources:
        resource, status = resources[payload.action]
        if resource == "staff":
            user = session.execute(text(
                "SELECT status,version FROM identity.users WHERE id::text=:id FOR UPDATE"
            ), {"id": payload.objectId}).mappings().first()
            if user is None:
                raise HTTPException(404, detail={"code": "STAFF_NOT_FOUND"})
            if int(user["version"]) != payload.version:
                raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
            session.execute(text(
                "UPDATE identity.users SET status='ACTIVE',version=version+1,updated_at=:now "
                "WHERE id::text=:id AND version=:version"
            ), {"now": now, "id": payload.objectId, "version": payload.version})
            return dict(user), {"id": payload.objectId, "status": "ACTIVE",
                                "version": payload.version + 1}
        return _update_governance_object(session, payload, trusted, resource, status, now)
    if payload.action == "导出审计":
        return {}, {"id": payload.objectId, "status": "exported", "version": payload.version + 1}
    raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})


def _update_governance_object(
    session: Session, payload: ActionPayload, trusted: TrustedWorkspaceContext,
    resource: str, status: str, now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    key = (trusted.tenant_id, trusted.workspace_id, resource, payload.objectId)
    current = session.get(GovernanceObjectRow, key)
    if current and current.version != payload.version:
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
    if current is None and payload.version != 0:
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
    before = {} if current is None else {
        "name": current.name, "status": current.status,
        "payload": current.payload, "version": current.version,
    }
    next_payload = payload.payload or (current.payload if current else {})
    if current:
        current.status, current.payload, current.version = status, next_payload, current.version + 1
        current.updated_by, current.updated_at = trusted.actor_id, now
        version = current.version
    else:
        version = 1
        session.add(GovernanceObjectRow(
            tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
            resource=resource, object_id=payload.objectId,
            name=str(next_payload.get("name") or payload.objectId),
            status=status, payload=next_payload, version=version,
            updated_by=trusted.actor_id, updated_at=now,
        ))
    return before, {"id": payload.objectId, "status": status,
                    "payload": next_payload, "version": version}
