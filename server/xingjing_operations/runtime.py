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
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_admin_finance.runtime import FinanceOperationRow
from server.xingjing_admin_governance.runtime import (
    ActionPayload,
    CommandRow,
    GovernanceObjectRow,
    SecurityAuditRow,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import OutboxRow

type ContextResolver = Callable[
    [Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]
]


class PreferencePayload(BaseModel):
    resource: str
    version: int
    value: dict[str, object]


@dataclass(slots=True)
class OperationsRuntime:
    router: APIRouter
    preferences_router: APIRouter
    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def create_unavailable_operations_routers(
    code: str,
) -> tuple[APIRouter, APIRouter]:
    admin = APIRouter(prefix="/api/v1/admin", tags=["admin-operations"])
    account = APIRouter(prefix="/api/v1/account", tags=["account-preferences"])

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": {"code": code}})

    for path in (
        "/operations",
        "/operations/actions",
        "/operations-config",
        "/operations-config/actions",
        "/notifications",
        "/notifications/actions",
        "/support-tickets",
        "/support-tickets/actions",
    ):
        admin.add_api_route(
            path, unavailable, methods=["POST" if path.endswith("/actions") else "GET"]
        )
    account.add_api_route(
        "/preferences", unavailable, methods=["GET"], operation_id="unavailable_account_preferences_get"
    )
    account.add_api_route(
        "/preferences", unavailable, methods=["PUT"], operation_id="unavailable_account_preferences_put"
    )
    account.add_api_route("/version-history", unavailable, methods=["GET"])
    return admin, account


def create_production_operations_runtime(
    *,
    database_url: str | None = None,
    context_resolver: ContextResolver | None = None,
) -> OperationsRuntime:
    url = (
        database_url
        or os.environ.get("XINGJING_OPERATIONS_DATABASE_URL")
        or os.environ.get("XINGJING_GOVERNANCE_DATABASE_URL", "")
    ).strip()
    if not url:
        raise RuntimeError("XINGJING_OPERATIONS_DATABASE_URL_REQUIRED")
    if make_url(url).drivername not in {
        "postgresql",
        "postgresql+psycopg",
        "postgresql+psycopg2",
    }:
        raise RuntimeError("XINGJING_OPERATIONS_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session:
            required_tables = (
                "xingjing_admin_governance_objects",
                "xingjing_admin_finance_operations",
                "xingjing_outbox",
                "xingjing_projects",
                "xingjing_generation_tasks",
                "xingjing_generated_assets",
                "xingjing_compliance_reviews",
                "xingjing_content_scripts",
                "xingjing_content_script_versions",
                "xingjing_asset_records",
                "xingjing_asset_versions",
                "xingjing_storyboards",
                "xingjing_editing_timeline_versions",
                "xingjing_editing_final_video_versions",
                "identity.users",
                "identity.workspace_members",
            )
            missing = session.scalars(
                text(
                    "SELECT required.name FROM unnest(CAST(:tables AS text[])) "
                    "AS required(name) WHERE to_regclass(required.name) IS NULL"
                ),
                {"tables": list(required_tables)},
            ).all()
            if missing:
                raise RuntimeError(
                    "XINGJING_OPERATIONS_MIGRATION_REQUIRED:"
                    + ",".join(str(item) for item in missing)
                )
    except (RuntimeError, SQLAlchemyError) as error:
        engine.dispose()
        raise RuntimeError("XINGJING_OPERATIONS_MIGRATION_REQUIRED") from error
    resolver = context_resolver or TrustedWorkspaceContextResolver(
        PlatformSessionGateway()
    )
    return OperationsRuntime(
        _admin_router(sessions, resolver),
        _preferences_router(sessions, resolver),
        engine,
    )


def _resolve_dependency(
    resolver: ContextResolver,
) -> Callable[[Request], Awaitable[TrustedWorkspaceContext]]:
    async def resolve(request: Request) -> TrustedWorkspaceContext:
        value = resolver(request)
        return await value if inspect.isawaitable(value) else value

    return resolve


def _require(trusted: TrustedWorkspaceContext, permission: str) -> None:
    if permission not in trusted.permissions:
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})


def _admin_router(
    sessions: sessionmaker[Session], resolver: ContextResolver
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-operations"])
    resolve = _resolve_dependency(resolver)

    def listing(
        domain: str,
        resource: str,
        page: int,
        page_size: int,
        trusted: TrustedWorkspaceContext,
    ) -> dict[str, object]:
        permissions = {
            "operations": "admin.ops.view",
            "operations-config": "admin.business.view",
            "notifications": "admin.notification.view",
            "support": "admin.support.view",
        }
        _require(trusted, permissions[domain])
        with sessions() as session:
            records = _records(session, domain, resource, trusted)
        return _page(records, page, page_size)

    @router.get("/operations")
    def operations(
        resource: str = Query("services"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        return listing("operations", resource, page, page_size, trusted)

    @router.get("/operations-config")
    def operations_config(
        resource: str = Query("ops-config"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        return listing("operations-config", resource, page, page_size, trusted)

    @router.get("/notifications")
    def notifications(
        resource: str = Query("notifications"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        return listing("notifications", resource, page, page_size, trusted)

    @router.get("/support-tickets")
    def support(
        resource: str = Query("tickets"),
        page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        return listing("support", resource, page, page_size, trusted)

    def action(
        domain: str,
        payload: ActionPayload,
        request: Request,
        trusted: TrustedWorkspaceContext,
        idempotency_key: str,
    ) -> dict[str, object]:
        permissions = {
            "operations": "admin.ops.manage",
            "operations-config": "admin.business.manage",
            "notifications": "admin.notification.manage",
            "support": "admin.support.manage",
        }
        _require(trusted, permissions[domain])
        if not payload.resource or not idempotency_key.strip() or payload.version < 0:
            raise HTTPException(422, detail={"code": "INVALID_COMMAND"})
        allowed = {
            "operations": {"services", "alert-rules", "error-logs", "queues", "storage"},
            "operations-config": {
                "growth",
                "ops-config",
                "platform-profiles",
                "publish-rules",
            },
            "notifications": {"notifications", "task-notices"},
            "support": {"tickets"},
        }
        if payload.resource not in allowed[domain]:
            raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
        fingerprint = "sha256:" + sha256(
            json.dumps(payload.model_dump(), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        now = datetime.now(UTC)
        with sessions.begin() as session:
            replay = session.scalar(
                select(CommandRow).where(
                    CommandRow.actor_id == trusted.actor_id,
                    CommandRow.idempotency_key == idempotency_key,
                )
            )
            if replay:
                if replay.fingerprint != fingerprint:
                    raise HTTPException(
                        409, detail={"code": "IDEMPOTENCY_CONFLICT"}
                    )
                return cast(dict[str, object], json.loads(replay.result_json))
            before, after = _write_object(
                session, domain, payload, trusted, now
            )
            result = {
                "requestId": trusted.request_id,
                "status": "succeeded",
                "object": after,
            }
            session.add(
                CommandRow(
                    command_id=str(uuid4()),
                    actor_id=trusted.actor_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    result_json=json.dumps(result, ensure_ascii=False),
                    created_at=now,
                )
            )
            _append_evidence(
                session, domain, payload, before, after, trusted, request, now
            )
            return result

    def bind_action(path: str, domain: str) -> None:
        def endpoint(
            payload: ActionPayload,
            request: Request,
            trusted: TrustedWorkspaceContext = Depends(resolve),
            idempotency_key: str = Header("", alias="Idempotency-Key"),
        ) -> dict[str, object]:
            return action(
                domain, payload, request, trusted, idempotency_key
            )

        router.add_api_route(path, endpoint, methods=["POST"])

    bind_action("/operations/actions", "operations")
    bind_action("/operations-config/actions", "operations-config")
    bind_action("/notifications/actions", "notifications")
    bind_action("/support-tickets/actions", "support")
    return router


def _preferences_router(
    sessions: sessionmaker[Session], resolver: ContextResolver
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/account", tags=["account-preferences"])
    resolve = _resolve_dependency(resolver)

    @router.get("/preferences")
    def get_preferences(
        resource: str | None = None,
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        _require(trusted, "settings.view")
        with sessions() as session:
            statement = select(GovernanceObjectRow).where(
                GovernanceObjectRow.tenant_id == trusted.tenant_id,
                GovernanceObjectRow.workspace_id == trusted.workspace_id,
                GovernanceObjectRow.resource.like("preference:%"),
                GovernanceObjectRow.object_id == trusted.actor_id,
            )
            if resource:
                statement = statement.where(
                    GovernanceObjectRow.resource == f"preference:{resource}"
                )
            rows = session.scalars(statement).all()
        return {
            "items": [
                {
                    "resource": row.resource.removeprefix("preference:"),
                    "value": row.payload,
                    "version": row.version,
                    "updatedAt": row.updated_at.isoformat(),
                }
                for row in rows
            ]
        }

    @router.get("/version-history")
    def version_history(
        project_id: str = Query(..., alias="projectId", min_length=1, max_length=128),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        _require(trusted, "settings.view")
        parameters = {
            "tenant": trusted.tenant_id,
            "workspace": trusted.workspace_id,
            "project": project_id,
        }
        with sessions() as session:
            project_exists = session.scalar(
                text(
                    """SELECT 1 FROM xingjing_projects
                    WHERE tenant_id=:tenant AND workspace_id=:workspace
                    AND id=:project"""
                ),
                parameters,
            )
            if project_exists is None:
                raise HTTPException(404, detail={"code": "PROJECT_NOT_FOUND"})
            rows = session.execute(
                text(
                    """SELECT 'script' kind,v.script_id object_id,
                    v.number::text version_id,v.number revision,
                    v.change_summary summary,NULL::timestamptz created_at
                    FROM xingjing_content_script_versions v
                    JOIN xingjing_content_scripts s
                    ON s.workspace_id=v.workspace_id AND s.script_id=v.script_id
                    WHERE s.workspace_id=:workspace AND s.project_id=:project
                    UNION ALL
                    SELECT 'asset',v.asset_id,v.version_id,v.sequence,v.change_note,
                    NULLIF(v.created_at,'')::timestamptz
                    FROM xingjing_asset_versions v
                    JOIN xingjing_asset_records a
                    ON a.tenant_id=v.tenant_id AND a.workspace_id=v.workspace_id
                    AND a.asset_id=v.asset_id
                    WHERE a.tenant_id=:tenant AND a.workspace_id=:workspace
                    AND a.owner_project_id=:project
                    UNION ALL
                    SELECT 'storyboard',storyboard_id,version::text,version,
                    COALESCE(payload->>'status','storyboard snapshot'),
                    NULLIF(payload->>'updated_at','')::timestamptz
                    FROM xingjing_storyboards WHERE tenant_id=:tenant
                    AND workspace_id=:workspace AND project_id=:project
                    UNION ALL
                    SELECT 'timeline',timeline_id,version_id,revision,
                    COALESCE(snapshot->>'label','timeline snapshot'),created_at
                    FROM xingjing_editing_timeline_versions
                    WHERE tenant_id=:tenant AND workspace_id=:workspace
                    AND project_id=:project
                    UNION ALL
                    SELECT 'final-video',final_video_id,version_id,1,
                    COALESCE(snapshot->>'label','final video'),created_at
                    FROM xingjing_editing_final_video_versions
                    WHERE tenant_id=:tenant AND workspace_id=:workspace
                    AND project_id=:project"""
                ),
                parameters,
            ).mappings()
            items = [
                {
                    "kind": str(row["kind"]),
                    "objectId": str(row["object_id"]),
                    "versionId": str(row["version_id"]),
                    "revision": int(row["revision"]),
                    "summary": str(row["summary"]),
                    "createdAt": (
                        row["created_at"].isoformat()
                        if isinstance(row["created_at"], datetime)
                        else None
                    ),
                }
                for row in rows
            ]
        items.sort(
            key=lambda item: (
                str(item["createdAt"] or ""),
                str(item["kind"]),
                str(item["objectId"]),
                int(cast(int, item["revision"])),
            ),
            reverse=True,
        )
        return {"items": items}

    @router.put("/preferences")
    def put_preferences(
        payload: PreferencePayload,
        request: Request,
        trusted: TrustedWorkspaceContext = Depends(resolve),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        _require(trusted, "settings.manage")
        if payload.resource not in {
            "cover-generation",
            "cover-title-ab",
            "creator-home",
            "risk-appeal",
            "settings",
            "version-history",
        } or not idempotency_key.strip():
            raise HTTPException(422, detail={"code": "INVALID_PREFERENCE"})
        command = ActionPayload(
            action="保存设置",
            objectId=trusted.actor_id,
            version=payload.version,
            resource=f"preference:{payload.resource}",
            payload=payload.value,
        )
        now = datetime.now(UTC)
        fingerprint = "sha256:" + sha256(
            json.dumps(command.model_dump(), ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        with sessions.begin() as session:
            replay = session.scalar(
                select(CommandRow).where(
                    CommandRow.actor_id == trusted.actor_id,
                    CommandRow.idempotency_key == idempotency_key,
                )
            )
            if replay:
                if replay.fingerprint != fingerprint:
                    raise HTTPException(
                        409, detail={"code": "IDEMPOTENCY_CONFLICT"}
                    )
                return cast(dict[str, object], json.loads(replay.result_json))
            before, after = _write_object(
                session, "preferences", command, trusted, now
            )
            result = {
                "resource": payload.resource,
                "value": after["payload"],
                "version": after["version"],
                "updatedAt": now.isoformat(),
            }
            session.add(
                CommandRow(
                    command_id=str(uuid4()),
                    actor_id=trusted.actor_id,
                    idempotency_key=idempotency_key,
                    fingerprint=fingerprint,
                    result_json=json.dumps(result, ensure_ascii=False),
                    created_at=now,
                )
            )
            _append_evidence(
                session,
                "preferences",
                command,
                before,
                after,
                trusted,
                request,
                now,
            )
            return result

    return router


def _page(
    records: list[dict[str, object]], page: int, page_size: int
) -> dict[str, object]:
    start = (page - 1) * page_size
    return {
        "items": records[start : start + page_size],
        "page": page,
        "pageSize": page_size,
        "total": len(records),
    }


def _object_records(
    session: Session, trusted: TrustedWorkspaceContext, resource: str
) -> list[dict[str, object]]:
    rows = session.scalars(
        select(GovernanceObjectRow)
        .where(
            GovernanceObjectRow.tenant_id == trusted.tenant_id,
            GovernanceObjectRow.workspace_id == trusted.workspace_id,
            GovernanceObjectRow.resource == resource,
        )
        .order_by(
            GovernanceObjectRow.updated_at.desc(),
            GovernanceObjectRow.object_id,
        )
    ).all()
    return [
        {
            "id": row.object_id,
            "name": row.name,
            "status": row.status,
            "version": row.version,
            "updatedAt": row.updated_at.isoformat(),
            "details": row.payload,
        }
        for row in rows
    ]


def _records(
    session: Session,
    domain: str,
    resource: str,
    trusted: TrustedWorkspaceContext,
) -> list[dict[str, object]]:
    if domain == "operations" and resource == "services":
        now = datetime.now(UTC)
        session.execute(text("SELECT 1"))
        records: list[dict[str, object]] = [
            {
                "id": "postgresql",
                "name": "PostgreSQL 主数据服务",
                "status": "healthy",
                "version": 1,
                "updatedAt": now.isoformat(),
                "details": {
                    "environment": os.environ.get("ENVIRONMENT", "production"),
                    "source": "live-probe",
                },
            }
        ]
        dependencies = {
            "identity": ("Java 身份服务", "PLATFORM_IDENTITY_URL"),
            "redis": ("Redis", "REDIS_URL"),
            "rabbitmq": ("RabbitMQ", "RABBITMQ_URL"),
            "object-storage": (
                "对象存储",
                "XINGJING_GENERATION_STORAGE_ROOT",
            ),
        }
        records.extend(
            {
                "id": component,
                "name": label,
                "status": "unknown",
                "version": 1,
                "updatedAt": now.isoformat(),
                "details": {
                    "configured": bool(os.environ.get(variable, "").strip()),
                    "reason": "未部署主动探针" if os.environ.get(variable, "").strip()
                    else "部署配置缺失",
                },
            }
            for component, (label, variable) in dependencies.items()
        )
        return records
    if domain == "operations" and resource == "queues":
        rows = session.execute(
            text(
                """SELECT event_type,COUNT(*) total,
                COUNT(*) FILTER (WHERE published_at IS NULL) pending,
                MAX(created_at) updated_at
                FROM xingjing_outbox
                WHERE tenant_id=:tenant AND workspace_id=:workspace
                GROUP BY event_type ORDER BY event_type"""
            ),
            {"tenant": trusted.tenant_id, "workspace": trusted.workspace_id},
        ).mappings()
        records = [
            {
                "id": str(row["event_type"]),
                "name": str(row["event_type"]),
                "status": "backlog" if int(row["pending"]) else "healthy",
                "version": 1,
                "updatedAt": row["updated_at"].isoformat(),
                "details": {
                    "total": int(row["total"]),
                    "pending": int(row["pending"]),
                },
            }
            for row in rows
        ]
        tasks = session.execute(
            text(
                """SELECT status,COUNT(*) total,MAX(updated_at) updated_at
                FROM xingjing_generation_tasks WHERE workspace_id=:workspace
                GROUP BY status ORDER BY status"""
            ),
            {"workspace": trusted.workspace_id},
        ).mappings()
        records.extend(
            {
                "id": f"generation:{row['status']}",
                "name": f"生成任务 / {row['status']}",
                "status": (
                    "backlog"
                    if str(row["status"]) in {"queued", "retrying"}
                    else str(row["status"])
                ),
                "version": 1,
                "updatedAt": row["updated_at"].isoformat(),
                "details": {"total": int(row["total"])},
            }
            for row in tasks
        )
        return records
    if domain == "operations" and resource == "storage":
        row = session.execute(
            text(
                """SELECT COUNT(*) total,COALESCE(SUM(size_bytes),0) bytes,
                MAX(created_at) updated_at FROM xingjing_generated_assets
                WHERE workspace_id=:workspace"""
            ),
            {"workspace": trusted.workspace_id},
        ).mappings().one()
        return [
            {
                "id": "generated-assets",
                "name": "生成产物存储",
                "status": "healthy",
                "version": 1,
                "updatedAt": (
                    row["updated_at"].isoformat()
                    if row["updated_at"]
                    else datetime.now(UTC).isoformat()
                ),
                "details": {
                    "objects": int(row["total"]),
                    "bytes": int(row["bytes"]),
                },
            }
        ]
    if domain == "operations" and resource == "error-logs":
        rows = session.execute(
            text(
                """SELECT task_id id,COALESCE(snapshot->>'kind','generation') name,
                status,version,updated_at,
                COALESCE(snapshot->'failure_metadata','{}'::jsonb) details
                FROM xingjing_generation_tasks
                WHERE workspace_id=:workspace
                AND status='failed' ORDER BY updated_at DESC,task_id LIMIT 500"""
            ),
            {"workspace": trusted.workspace_id},
        ).mappings()
        return [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "status": str(row["status"]),
                "version": int(row["version"]),
                "updatedAt": row["updated_at"].isoformat(),
                "details": row["details"] or {},
            }
            for row in rows
        ]
    if domain == "operations-config" and resource == "growth":
        row = session.execute(
            text(
                """SELECT
                (SELECT COUNT(DISTINCT user_id) FROM identity.workspace_members
                 WHERE workspace_id::text=:workspace AND status<>'REMOVED') users,
                (SELECT COUNT(*) FROM xingjing_projects
                 WHERE tenant_id=:tenant AND workspace_id=:workspace) projects,
                (SELECT COUNT(*) FROM xingjing_generation_tasks
                 WHERE workspace_id=:workspace) tasks,
                (SELECT COUNT(*) FROM xingjing_compliance_reviews
                 WHERE tenant_id=:tenant AND workspace_id=:workspace) reviews"""
            ),
            {"tenant": trusted.tenant_id, "workspace": trusted.workspace_id},
        ).mappings().one()
        return [
            {
                "id": "growth-current",
                "name": "当前运营数据",
                "status": "observed",
                "version": 1,
                "updatedAt": datetime.now(UTC).isoformat(),
                "details": {key: int(value) for key, value in row.items()},
            }
        ]
    allowed = {
        "operations": {"alert-rules"},
        "operations-config": {
            "ops-config",
            "platform-profiles",
            "publish-rules",
        },
        "notifications": {"notifications", "task-notices"},
        "support": {"tickets"},
    }
    if resource not in allowed.get(domain, set()):
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    return _object_records(session, trusted, resource)


def _write_object(
    session: Session,
    domain: str,
    payload: ActionPayload,
    trusted: TrustedWorkspaceContext,
    now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    assert payload.resource is not None
    stored_resource = payload.resource
    live_resources = {"services", "error-logs", "queues", "storage"}
    if domain == "operations" and payload.resource in live_resources:
        stored_resource = f"operation-command:{payload.resource}"
    key = (
        trusted.tenant_id,
        trusted.workspace_id,
        stored_resource,
        payload.objectId,
    )
    current = session.get(GovernanceObjectRow, key)
    if current and current.version != payload.version:
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
    allowed_initial_versions = {0}
    if domain == "operations" and payload.resource in live_resources:
        allowed_initial_versions.add(1)
    if current is None and payload.version not in allowed_initial_versions:
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
    before: dict[str, object] = (
        {}
        if current is None
        else {
            "name": current.name,
            "status": current.status,
            "payload": current.payload,
            "version": current.version,
        }
    )
    next_payload = payload.payload or (current.payload if current else {})
    status = _status_for(domain, payload.action)
    if domain == "notifications" and payload.action == "发送通知":
        recipients = next_payload.get("recipientIds")
        body = next_payload.get("body")
        template_id = next_payload.get("templateId")
        if (
            not isinstance(recipients, list)
            or not recipients
            or not all(isinstance(item, str) and item.strip() for item in recipients)
            or not (
                isinstance(body, str)
                and body.strip()
                or isinstance(template_id, str)
                and template_id.strip()
            )
        ):
            raise HTTPException(422, detail={"code": "INVALID_NOTIFICATION"})
    if domain == "support" and "compensation" in next_payload:
        compensation = next_payload["compensation"]
        if not isinstance(compensation, dict):
            raise HTTPException(422, detail={"code": "INVALID_COMPENSATION"})
        amount = compensation.get("amountMinor")
        currency = compensation.get("currency")
        if (
            not isinstance(amount, int)
            or isinstance(amount, bool)
            or amount <= 0
            or not isinstance(currency, str)
            or len(currency) != 3
            or not payload.reason
        ):
            raise HTTPException(422, detail={"code": "INVALID_COMPENSATION"})
        operation_id = str(uuid4())
        session.add(
            FinanceOperationRow(
                operation_id=operation_id,
                workspace_id=trusted.workspace_id,
                operation_type="support_compensation",
                object_id=payload.objectId,
                amount_minor=amount,
                currency=currency.upper(),
                status="pending_approval",
                reason=payload.reason,
                requested_by=trusted.actor_id,
                approved_by=None,
                version=1,
                created_at=now,
                updated_at=now,
            )
        )
        next_payload = {
            **next_payload,
            "compensationOperationId": operation_id,
        }
        status = "pending_approval"
    if current:
        current.name = str(next_payload.get("name") or current.name)
        current.status = status
        current.payload = next_payload
        current.version += 1
        current.updated_by = trusted.actor_id
        current.updated_at = now
        version = current.version
    else:
        version = 1
        session.add(
            GovernanceObjectRow(
                tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id,
                resource=stored_resource,
                object_id=payload.objectId,
                name=str(next_payload.get("name") or payload.objectId),
                status=status,
                payload=next_payload,
                version=version,
                updated_by=trusted.actor_id,
                updated_at=now,
            )
        )
    return before, {
        "id": payload.objectId,
        "status": status,
        "payload": next_payload,
        "version": version,
    }


def _status_for(domain: str, action: str) -> str:
    if domain == "operations":
        return "requested"
    if domain == "support":
        return "processing"
    if action in {"发布规则", "发布配置", "发布档案"}:
        return "published"
    if action == "发送通知":
        return "queued"
    if action in {"执行恢复", "执行清理"}:
        return "resolved"
    if action == "创建工单":
        return "open"
    return "active"


def _append_evidence(
    session: Session,
    domain: str,
    payload: ActionPayload,
    before: dict[str, object],
    after: dict[str, object],
    trusted: TrustedWorkspaceContext,
    request: Request,
    now: datetime,
) -> None:
    session.add(
        SecurityAuditRow(
            audit_id=str(uuid4()),
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            actor_id=trusted.actor_id,
            request_id=trusted.request_id,
            action=payload.action,
            object_type=domain,
            object_id=payload.objectId,
            before_payload=before,
            after_payload=after,
            result="success",
            ip_address=request.client.host if request.client else None,
            device=request.headers.get("User-Agent"),
            occurred_at=now,
        )
    )
    session.add(
        OutboxRow(
            tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            event_type=f"{domain}.changed",
            aggregate_type=domain,
            aggregate_id=payload.objectId,
            payload={
                "action": payload.action,
                "resource": payload.resource,
                "object": after,
                "requestId": trusted.request_id,
            },
            schema_version=1,
            created_at=now,
            published_at=None,
        )
    )
