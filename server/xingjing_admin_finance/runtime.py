from __future__ import annotations

import csv
import inspect
import io
import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from sqlalchemy import (
    JSON,
    BigInteger,
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


class FinanceOperationRow(Base):
    __tablename__ = "xingjing_admin_finance_operations"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommandRow(Base):
    __tablename__ = "xingjing_admin_finance_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "actor_id", "idempotency_key",
                         name="uq_xj_admin_finance_command_scope"),
    )
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditRow(Base):
    __tablename__ = "xingjing_admin_finance_audit"
    __table_args__ = (
        Index("ix_xj_admin_finance_audit_request", "request_id", "occurred_at"),
    )
    audit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FinanceExportRow(Base):
    __tablename__ = "xingjing_admin_finance_exports"
    export_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    export_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_csv: Mapped[str] = mapped_column(Text, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionPayload(BaseModel):
    action: str
    objectId: str
    version: int
    reason: str | None = None
    payload: dict[str, object] | None = None


type ContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


@dataclass(slots=True)
class AdminFinanceRuntime:
    router: APIRouter
    engine: Engine

    def close(self) -> None:
        self.engine.dispose()


def create_unavailable_admin_finance_router(code: str) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-finance-models"])

    async def unavailable() -> JSONResponse:
        return JSONResponse(status_code=503, content={"error": {"code": code}})

    router.add_api_route("/finance", unavailable, methods=["GET"])
    router.add_api_route("/finance/actions", unavailable, methods=["POST"])
    router.add_api_route("/models", unavailable, methods=["GET"])
    router.add_api_route("/models/actions", unavailable, methods=["POST"])
    return router


def create_production_admin_finance_runtime(
    *, database_url: str | None = None, context_resolver: ContextResolver | None = None,
) -> AdminFinanceRuntime:
    url = (database_url or os.environ.get("XINGJING_FINANCE_DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError("XINGJING_FINANCE_DATABASE_URL_REQUIRED")
    if make_url(url).drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise RuntimeError("XINGJING_FINANCE_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    engine = create_engine(url, pool_pre_ping=True)
    sessions = sessionmaker(engine, expire_on_commit=False)
    try:
        with sessions() as session:
            session.execute(text("SELECT 1 FROM xingjing_model_definitions LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_model_health LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_model_pricing LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_admin_finance_operations LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_admin_finance_exports LIMIT 1"))
    except SQLAlchemyError as error:
        engine.dispose()
        raise RuntimeError("XINGJING_FINANCE_MIGRATION_REQUIRED") from error
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return AdminFinanceRuntime(_router(sessions, resolver), engine)


def _router(sessions: sessionmaker[Session], resolver: ContextResolver) -> APIRouter:
    router = APIRouter(prefix="/api/v1/admin", tags=["admin-finance-models"])

    async def resolve(request: Request) -> TrustedWorkspaceContext:
        value = resolver(request)
        return await value if inspect.isawaitable(value) else value

    def require(trusted: TrustedWorkspaceContext, permission: str) -> None:
        if permission not in trusted.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})

    @router.get("/finance")
    def finance(
        resource: str = Query("billing"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        search: str = Query("", max_length=200), status: str | None = Query(None, max_length=128),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.finance.view")
        with sessions() as session:
            records, total = _finance_records(
                session, resource, trusted.tenant_id, trusted.workspace_id,
                page=page, page_size=page_size,
                search=search.strip(), status=status,
            )
        return {"items": records, "page": page, "pageSize": page_size, "total": total}

    @router.get("/models")
    def models(
        resource: str = Query("models"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        search: str = Query("", max_length=200), status: str | None = Query(None, max_length=128),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.model.view")
        with sessions() as session:
            records, total = _model_records(
                session, resource, trusted.tenant_id, trusted.workspace_id,
                page=page, page_size=page_size,
                search=search.strip(), status=status,
            )
        return {"items": records, "page": page, "pageSize": page_size, "total": total}

    def execute_action(
        domain: str, payload: ActionPayload, trusted: TrustedWorkspaceContext, idempotency_key: str,
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
            session.add(AuditRow(
                audit_id=str(uuid4()), tenant_id=trusted.tenant_id,
                workspace_id=trusted.workspace_id,
                actor_id=trusted.actor_id, request_id=trusted.request_id,
                domain=domain, action=payload.action, object_id=payload.objectId,
                before_payload=before, after_payload=after, occurred_at=now,
            ))
            session.add(OutboxRow(
                tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
                event_type=f"admin.{domain}.changed", aggregate_type=domain,
                aggregate_id=payload.objectId,
                payload={"action": payload.action, "object": after, "requestId": trusted.request_id},
                schema_version=1, created_at=now, published_at=None,
            ))
            return result

    @router.post("/finance/actions")
    def finance_action(
        payload: ActionPayload, trusted: TrustedWorkspaceContext = Depends(resolve),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        return execute_action("finance", payload, trusted, idempotency_key)

    @router.post("/models/actions")
    def model_action(
        payload: ActionPayload, trusted: TrustedWorkspaceContext = Depends(resolve),
        idempotency_key: str = Header("", alias="Idempotency-Key"),
    ) -> dict[str, object]:
        return execute_action("model", payload, trusted, idempotency_key)

    @router.get("/finance/exports/{export_id}")
    def download_finance_export(
        export_id: str, trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> Response:
        require(trusted, "admin.finance.view")
        with sessions() as session:
            export = session.get(FinanceExportRow, export_id)
            if export is None or export.tenant_id != trusted.tenant_id or export.workspace_id != trusted.workspace_id:
                raise HTTPException(404, detail={"code": "FINANCE_EXPORT_NOT_FOUND"})
            return Response(
                content=export.content_csv.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{export.export_kind}-{export.export_id}.csv"',
                         "X-Content-SHA256": export.content_sha256},
            )

    return router


def _row(item: object) -> dict[str, object]:
    mapping = cast(dict[str, object], item)
    updated = mapping.get("updated_at") or mapping.get("occurred_at") or mapping.get("created_at")
    return {
        "id": str(mapping["id"]), "name": str(mapping["name"]),
        "status": str(mapping["status"]), "version": _integer(mapping.get("version"), 1),
        "updatedAt": updated.isoformat() if isinstance(updated, datetime) else str(updated),
        "sensitive": mapping.get("sensitive") or {},
        "details": mapping.get("details") or {},
    }


def _integer(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _query(
    session: Session, statement: str, tenant_id: str, workspace_id: str, *, page: int, page_size: int,
    search: str, status: str | None,
) -> tuple[list[dict[str, object]], int]:
    filtered = f"""SELECT * FROM ({statement}) source
      WHERE (:search='' OR lower(id::text) LIKE :needle OR lower(name) LIKE :needle)
        AND (:status IS NULL OR lower(status)=ANY(string_to_array(lower(:status),',')))"""
    parameters = {
        "tenant": tenant_id, "ws": workspace_id, "search": search,
        "needle": f"%{search.lower()}%", "status": status,
        "limit": page_size, "offset": (page - 1) * page_size,
    }
    total = int(session.scalar(text(f"SELECT count(*) FROM ({filtered}) counted"), parameters) or 0)
    rows = session.execute(
        text(f"{filtered} ORDER BY updated_at DESC, id DESC LIMIT :limit OFFSET :offset"), parameters,
    ).mappings()
    return [_row(dict(item)) for item in rows], total


def _finance_records(
    session: Session, resource: str, tenant_id: str, workspace_id: str, *, page: int, page_size: int,
    search: str, status: str | None,
) -> tuple[list[dict[str, object]], int]:
    statements = {
        "billing": """SELECT workspace_id id, '算力账户 '||workspace_id name, 'active' status,
          version, updated_at, jsonb_build_object('availableMinor',available_minor,'heldMinor',held_minor,
          'spentMinor',spent_minor,'currency',currency) details
          FROM xingjing_generation_billing_accounts WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "costs": """SELECT event_id id, reference name, action status, 1 version, occurred_at updated_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'projectId',project_id,
          'taskId',task_id) details FROM xingjing_generation_billing_journals
          WHERE workspace_id=:ws ORDER BY occurred_at DESC,event_id""",
        "invoices": """SELECT invoice_id id, invoice_title name, status, version, updated_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'requestedBy',requested_by,
          'approvedBy',approved_by) details
          FROM xingjing_team_invoice_requests WHERE tenant_id=:tenant AND workspace_id=:ws
          ORDER BY updated_at DESC""",
        "orders": """SELECT order_id id, order_type name, status, version, updated_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'externalReference',external_reference)
          details FROM xingjing_team_billing_orders WHERE tenant_id=:tenant AND workspace_id=:ws
          ORDER BY updated_at DESC""",
        "entitlements": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas,
          'quotaRemaining',quota_remaining) details FROM xingjing_team_billing_plans
          WHERE tenant_id=:tenant AND workspace_id=:ws ORDER BY updated_at DESC""",
        "plans": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas) details
          FROM xingjing_team_billing_plans WHERE tenant_id=:tenant AND workspace_id=:ws
          ORDER BY updated_at DESC""",
        "plan-detail": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas,
          'quotaRemaining',quota_remaining) details FROM xingjing_team_billing_plans
          WHERE tenant_id=:tenant AND workspace_id=:ws ORDER BY updated_at DESC""",
        "reconciliation": """SELECT operation_id id, object_id name, status, version, updated_at,
          jsonb_build_object('type',operation_type,'amountMinor',amount_minor,'currency',currency,
          'reason',reason,'result',result_payload)
          details FROM xingjing_admin_finance_operations
          WHERE tenant_id=:tenant AND workspace_id=:ws AND operation_type='reconciliation'
          ORDER BY updated_at DESC""",
        "refunds": """SELECT operation_id id, object_id name, status, version, updated_at,
          jsonb_build_object('type',operation_type,'amountMinor',amount_minor,'currency',currency,'reason',reason)
          details FROM xingjing_admin_finance_operations
          WHERE tenant_id=:tenant AND workspace_id=:ws
            AND operation_type IN ('refund','support_compensation')
          ORDER BY updated_at DESC""",
        "revenue": """SELECT order_id id,COALESCE(external_reference,order_id) name,status,version,updated_at,
          jsonb_build_object('source','credit_order','grossMinor',amount_minor,'refundedMinor',refunded_minor,
          'netMinor',amount_minor-refunded_minor,'currency',currency) details
          FROM xingjing_team_billing_orders WHERE tenant_id=:tenant AND workspace_id=:ws
            AND status IN ('paid','refunded')
          UNION ALL
          SELECT orders.id||':'||(settlement->>'id') id,orders.aggregate->>'title' name,
          settlement->>'status' status,(settlement->>'version')::integer version,
          (settlement->>'updated_at')::timestamptz updated_at,
          jsonb_build_object('source','commercial_settlement','grossMinor',(settlement->>'amount_minor')::bigint,
          'refundedMinor',0,'netMinor',(settlement->>'amount_minor')::bigint,'currency',settlement->>'currency',
          'orderId',orders.id,'milestoneId',settlement->>'milestone_id') details
          FROM xingjing_commercial_orders orders
          CROSS JOIN LATERAL jsonb_array_elements(COALESCE(orders.aggregate->'settlements','[]'::jsonb)) settlement
          WHERE orders.owner_workspace_id=:ws AND settlement->>'status'='paid'""",
        "audit": """SELECT audit_id id, action name, domain status, 1 version, occurred_at updated_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectId',object_id,
          'before',before_payload,'after',after_payload) details
          FROM xingjing_admin_finance_audit
          WHERE tenant_id=:tenant AND workspace_id=:ws AND domain='finance'
          ORDER BY occurred_at DESC,audit_id""",
    }
    statement = statements.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    return _query(session, statement, tenant_id, workspace_id, page=page, page_size=page_size,
                  search=search, status=status)


def _model_records(
    session: Session, resource: str, tenant_id: str, workspace_id: str, *, page: int, page_size: int,
    search: str, status: str | None,
) -> tuple[list[dict[str, object]], int]:
    statements = {
        "models": """SELECT definition.registry_key id, definition.display_name name,
          CASE WHEN definition.active THEN 'active' ELSE 'disabled' END status,
          definition.version,definition.updated_at,
          jsonb_build_object('providerId',definition.provider_id,'providerModelName',definition.provider_model_name,
          'modelVersion',definition.model_version,'region',definition.region,'mediaTypes',definition.media_types,
          'capabilities',definition.capabilities,'health',COALESCE(health.status,'unknown'),
          'latencyMs',health.latency_ms,'activeCalls',COALESCE(health.active_calls,0),
          'pricingVersion',pricing.pricing_version,'unitPriceMinor',pricing.estimated_minor,
          'currency',pricing.currency,'maxConcurrency',definition.max_concurrency) details
          FROM xingjing_model_definitions definition
          LEFT JOIN xingjing_model_health health ON health.registry_key=definition.registry_key
          LEFT JOIN LATERAL (SELECT model_pricing.pricing_version,model_pricing.estimated_minor,
            model_pricing.currency FROM xingjing_model_pricing model_pricing
            WHERE model_pricing.registry_key=definition.registry_key
              AND model_pricing.effective_at<=now() AND model_pricing.retired_at IS NULL
            ORDER BY model_pricing.effective_at DESC,model_pricing.id DESC LIMIT 1) pricing ON true
          ORDER BY definition.updated_at DESC,definition.registry_key""",
        "callback-logs": """SELECT evidence_id id, COALESCE(payload->>'provider','模型回调') name,
          COALESCE(payload->>'status','received') status, 1 version, occurred_at updated_at,
          jsonb_build_object('projectId',project_id,'taskId',task_id,'evidence',payload) details
          FROM xingjing_generation_provider_evidence WHERE workspace_id=:ws
          ORDER BY occurred_at DESC,evidence_id""",
        "quality": """SELECT task_id id, COALESCE(snapshot->>'modelId',task_id) name, status, version, updated_at,
          jsonb_build_object('projectId',project_id,'modelId',snapshot->>'modelId',
          'providerId',snapshot->>'providerId','failureCode',snapshot->>'failureCode') details
          FROM xingjing_generation_tasks WHERE workspace_id=:ws ORDER BY updated_at DESC,task_id""",
        "audit": """SELECT audit_id id, action name, domain status, 1 version, occurred_at updated_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectId',object_id,
          'before',before_payload,'after',after_payload) details
          FROM xingjing_admin_finance_audit
          WHERE ((tenant_id=:tenant AND workspace_id=:ws)
                 OR (tenant_id='platform' AND workspace_id='platform')) AND domain='model'
          ORDER BY occurred_at DESC,audit_id""",
    }
    statement = statements.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    return _query(session, statement, tenant_id, workspace_id, page=page, page_size=page_size,
                  search=search, status=status)


def _apply_action(
    session: Session, domain: str, payload: ActionPayload,
    trusted: TrustedWorkspaceContext, now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    if domain == "model":
        if payload.action in {"重新校验", "刷新统计"}:
            return {"version": payload.version}, {
                "id": payload.objectId,
                "status": "revalidation_requested" if payload.action == "重新校验" else "statistics_refresh_requested",
                "version": payload.version,
            }
        registry_key = payload.objectId.strip()
        if not registry_key:
            raise HTTPException(422, detail={"code": "MODEL_OBJECT_ID_INVALID"})
        model = session.execute(text("""
            SELECT registry_key,active,version FROM xingjing_model_definitions
             WHERE registry_key=:registry_key FOR UPDATE
        """), {"registry_key": registry_key}).mappings().first()
        if model is None:
            raise HTTPException(404, detail={"code": "MODEL_NOT_FOUND"})
        if int(model["version"]) != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        before = {"status": "active" if model["active"] else "disabled", "version": payload.version}
        if payload.action == "更新模型":
            active = not bool(model["active"])
            session.execute(text("""
                UPDATE xingjing_model_definitions SET active=:active,version=version+1,updated_at=:now
                 WHERE registry_key=:registry_key AND version=:version
            """), {"active": active, "now": now, "registry_key": registry_key, "version": payload.version})
        else:
            raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})
        return before, {"id": registry_key, "status": "active" if active else "disabled",
                        "version": payload.version + 1}

    if payload.action in {"发起调账", "确认对账", "执行退款审批"}:
        operation_type = {
            "发起调账": "adjustment", "确认对账": "reconciliation", "执行退款审批": "refund",
        }[payload.action]
        operation = session.scalar(select(FinanceOperationRow).where(
            FinanceOperationRow.operation_id == payload.objectId,
            FinanceOperationRow.tenant_id == trusted.tenant_id,
            FinanceOperationRow.workspace_id == trusted.workspace_id,
        ))
        if operation:
            if operation.version != payload.version:
                raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
            if payload.action == "执行退款审批":
                if operation.requested_by == trusted.actor_id:
                    raise HTTPException(409, detail={"code": "FOUR_EYES_APPROVAL_REQUIRED"})
                _approve_refund(session, operation, trusted, now)
                operation.status, operation.approved_by = "approved", trusted.actor_id
            elif payload.action == "发起调账":
                if operation.requested_by == trusted.actor_id:
                    raise HTTPException(409, detail={"code": "FOUR_EYES_APPROVAL_REQUIRED"})
                _approve_adjustment(session, operation, trusted, now)
                operation.status, operation.approved_by = "approved", trusted.actor_id
            elif payload.action == "确认对账":
                if operation.status != "matched":
                    raise HTTPException(409, detail={"code": "RECONCILIATION_REVIEW_REQUIRED"})
                operation.status = "confirmed"
            operation.version += 1
            operation.updated_at = now
            return {"status": "pending", "version": payload.version}, {
                "id": operation.operation_id, "status": operation.status, "version": operation.version,
            }
        amount_minor, currency = (0, "CNY")
        if operation_type == "adjustment":
            values = payload.payload or {}
            amount = values.get("amountMinor")
            requested_currency = values.get("currency")
            if isinstance(amount, bool) or not isinstance(amount, int) or amount == 0:
                raise HTTPException(422, detail={"code": "ADJUSTMENT_AMOUNT_INVALID"})
            if not isinstance(requested_currency, str) or len(requested_currency.strip()) != 3:
                raise HTTPException(422, detail={"code": "ADJUSTMENT_CURRENCY_INVALID"})
            if not (payload.reason or "").strip():
                raise HTTPException(422, detail={"code": "ADJUSTMENT_REASON_REQUIRED"})
            amount_minor, currency = amount, requested_currency.strip().upper()
        if operation_type == "refund":
            order = session.execute(text(
                "SELECT amount_minor,currency,status FROM xingjing_team_billing_orders "
                "WHERE tenant_id=:tenant AND workspace_id=:ws AND order_id=:id FOR UPDATE"
            ), {"tenant": trusted.tenant_id, "ws": trusted.workspace_id,
                "id": payload.objectId}).mappings().first()
            if order is None:
                raise HTTPException(404, detail={"code": "ORDER_NOT_FOUND"})
            if str(order["status"]) != "paid":
                raise HTTPException(409, detail={"code": "ORDER_NOT_REFUNDABLE"})
            amount_minor, currency = int(order["amount_minor"]), str(order["currency"])
        result_payload: dict[str, object] = {}
        operation_status = "pending"
        if operation_type == "reconciliation":
            result_payload = _reconcile_supplier_lines(session, trusted, payload.payload)
            operation_status = "review_required" if result_payload["differences"] else "matched"
        operation = FinanceOperationRow(
            operation_id=payload.objectId, tenant_id=trusted.tenant_id,
            workspace_id=trusted.workspace_id,
            operation_type=operation_type, object_id=payload.objectId, amount_minor=amount_minor,
            currency=currency, status=operation_status, reason=payload.reason or payload.action,
            result_payload=result_payload,
            requested_by=trusted.actor_id, approved_by=None, version=1,
            created_at=now, updated_at=now,
        )
        session.add(operation)
        return {}, {
            "id": operation.operation_id,
            "status": operation.status,
            "version": 1,
            "result": result_payload,
        }

    if payload.action in {"更新权益", "保存套餐", "发布套餐"}:
        plan = session.execute(text(
            "SELECT version,status,seat_limit,features,quotas,quota_remaining FROM xingjing_team_billing_plans "
            "WHERE tenant_id=:tenant AND workspace_id=:ws AND (workspace_id=:id OR plan_id=:id) FOR UPDATE"
        ), {"tenant": trusted.tenant_id, "ws": trusted.workspace_id,
            "id": payload.objectId}).mappings().first()
        if plan is None:
            raise HTTPException(404, detail={"code": "PLAN_NOT_FOUND"})
        if int(plan["version"]) != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        values = payload.payload or {}
        status = "active" if payload.action == "发布套餐" else str(plan["status"])
        seat_limit = values.get("seatLimit", plan["seat_limit"])
        features = values.get("features", plan["features"])
        quotas = values.get("quotas", plan["quotas"])
        quota_remaining = values.get("quotaRemaining", plan["quota_remaining"])
        if isinstance(seat_limit, bool) or not isinstance(seat_limit, int) or seat_limit < 0:
            raise HTTPException(422, detail={"code": "PLAN_SEAT_LIMIT_INVALID"})
        if not isinstance(features, list) or not all(isinstance(item, str) and item.strip() for item in features):
            raise HTTPException(422, detail={"code": "PLAN_FEATURES_INVALID"})
        if not _valid_quota_map(quotas) or not _valid_quota_map(quota_remaining):
            raise HTTPException(422, detail={"code": "PLAN_QUOTAS_INVALID"})
        quota_map = cast(dict[str, int], quotas)
        remaining_map = cast(dict[str, int], quota_remaining)
        if any(remaining_map.get(key, 0) > value for key, value in quota_map.items()):
            raise HTTPException(422, detail={"code": "PLAN_QUOTA_REMAINING_EXCEEDS_LIMIT"})
        session.execute(text(
            "UPDATE xingjing_team_billing_plans SET status=:status,seat_limit=:seat_limit,"
            "features=CAST(:features AS jsonb),quotas=CAST(:quotas AS jsonb),"
            "quota_remaining=CAST(:quota_remaining AS jsonb),version=version+1,updated_at=:now "
            "WHERE tenant_id=:tenant AND workspace_id=:ws "
            "AND (workspace_id=:id OR plan_id=:id) AND version=:version"
        ), {"status": status, "seat_limit": seat_limit,
            "features": json.dumps(features), "quotas": json.dumps(quotas),
            "quota_remaining": json.dumps(quota_remaining), "now": now, "ws": trusted.workspace_id,
            "tenant": trusted.tenant_id, "id": payload.objectId, "version": payload.version})
        return {"status": plan["status"], "version": payload.version,
                "seatLimit": plan["seat_limit"], "features": plan["features"], "quotas": plan["quotas"]}, {
            "id": payload.objectId, "status": status, "version": payload.version + 1,
            "seatLimit": seat_limit, "features": features, "quotas": quotas,
        }

    if payload.action in {"执行审批", "更新订单"}:
        table = "xingjing_team_invoice_requests" if payload.action == "执行审批" else "xingjing_team_billing_orders"
        id_column = "invoice_id" if payload.action == "执行审批" else "order_id"
        selected_fields = "status,version,requested_by" if id_column == "invoice_id" else "status,version,external_reference"
        row = session.execute(text(
            f"SELECT {selected_fields} FROM {table} "
            f"WHERE tenant_id=:tenant AND workspace_id=:ws AND {id_column}=:id FOR UPDATE"
        ), {"tenant": trusted.tenant_id, "ws": trusted.workspace_id,
            "id": payload.objectId}).mappings().first()
        if row is None:
            raise HTTPException(404, detail={"code": "FINANCE_OBJECT_NOT_FOUND"})
        current_version = int(row["version"])
        if current_version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        if id_column == "invoice_id":
            if row["requested_by"] == trusted.actor_id:
                raise HTTPException(409, detail={"code": "FOUR_EYES_APPROVAL_REQUIRED"})
            if "admin.finance.approve" not in trusted.permissions:
                raise HTTPException(403, detail={"code": "INVOICE_APPROVAL_PERMISSION_REQUIRED"})
            if str(row["status"]) != "pending":
                raise HTTPException(409, detail={"code": "INVOICE_NOT_APPROVABLE"})
            next_status = "approved"
            session.execute(text(
                "UPDATE xingjing_team_invoice_requests SET status='approved',approved_by=:actor,"
                "version=version+1,updated_at=:now WHERE tenant_id=:tenant AND workspace_id=:ws "
                "AND invoice_id=:id AND version=:version"
            ), {"actor": trusted.actor_id, "now": now, "tenant": trusted.tenant_id,
                "ws": trusted.workspace_id,
                "id": payload.objectId, "version": payload.version})
        else:
            values = payload.payload or {}
            requested_status = values.get("status", row["status"])
            if requested_status not in {"pending", "failed", "closed"}:
                raise HTTPException(422, detail={"code": "ORDER_STATUS_INVALID"})
            if str(row["status"]) in {"paid", "refunded", "closed"} and requested_status != row["status"]:
                raise HTTPException(409, detail={"code": "ORDER_TERMINAL"})
            external_reference = values.get("externalReference", row["external_reference"])
            if external_reference is not None and not isinstance(external_reference, str):
                raise HTTPException(422, detail={"code": "ORDER_EXTERNAL_REFERENCE_INVALID"})
            next_status = str(requested_status)
            session.execute(text(
                "UPDATE xingjing_team_billing_orders SET status=:status,external_reference=:external_reference,"
                "closed_at=CASE WHEN :status='closed' THEN :now ELSE closed_at END,"
                "version=version+1,updated_at=:now WHERE tenant_id=:tenant AND workspace_id=:ws "
                "AND order_id=:id AND version=:version"
            ), {"status": next_status, "external_reference": external_reference, "now": now,
                "tenant": trusted.tenant_id, "ws": trusted.workspace_id,
                "id": payload.objectId, "version": payload.version})
        return {"status": row["status"], "version": current_version}, {
            "id": payload.objectId, "status": next_status, "version": current_version + 1,
        }

    if payload.action in {"导出对账", "导出收入"}:
        return {}, _create_finance_export(session, trusted, payload.action, now)
    raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})


def _valid_quota_map(value: object) -> bool:
    return isinstance(value, dict) and all(
        isinstance(key, str) and key.strip() and isinstance(amount, int)
        and not isinstance(amount, bool) and amount >= 0
        for key, amount in value.items()
    )


def _create_finance_export(
    session: Session, trusted: TrustedWorkspaceContext, action: str, now: datetime,
) -> dict[str, object]:
    export_kind = "reconciliation" if action == "导出对账" else "revenue"
    if export_kind == "reconciliation":
        rows = session.execute(text("""
            SELECT operation_id id,operation_type type,object_id,status,amount_minor,currency,
                   reason,updated_at FROM xingjing_admin_finance_operations
             WHERE tenant_id=:tenant AND workspace_id=:workspace ORDER BY updated_at,operation_id
        """), {"tenant": trusted.tenant_id, "workspace": trusted.workspace_id}).mappings().all()
        fields = ("id", "type", "object_id", "status", "amount_minor", "currency", "reason", "updated_at")
    else:
        rows = session.execute(text("""
            SELECT order_id id,'credit_order' source,status,amount_minor gross_minor,
                   refunded_minor,amount_minor-refunded_minor net_minor,currency,updated_at
              FROM xingjing_team_billing_orders
             WHERE tenant_id=:tenant AND workspace_id=:workspace AND status IN ('paid','refunded')
            UNION ALL
            SELECT orders.id||':'||(settlement->>'id'),'commercial_settlement',settlement->>'status',
                   (settlement->>'amount_minor')::bigint,0,(settlement->>'amount_minor')::bigint,
                   settlement->>'currency',(settlement->>'updated_at')::timestamptz
              FROM xingjing_commercial_orders orders
              CROSS JOIN LATERAL jsonb_array_elements(COALESCE(orders.aggregate->'settlements','[]'::jsonb)) settlement
             WHERE orders.owner_workspace_id=:workspace AND settlement->>'status'='paid'
             ORDER BY updated_at,id
        """), {"tenant": trusted.tenant_id, "workspace": trusted.workspace_id}).mappings().all()
        fields = ("id", "source", "status", "gross_minor", "refunded_minor", "net_minor", "currency", "updated_at")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row[field].isoformat() if isinstance(row[field], datetime) else row[field] for field in fields})
    content = output.getvalue()
    export_id = str(uuid4())
    digest = "sha256:" + sha256(content.encode()).hexdigest()
    session.add(FinanceExportRow(
        export_id=export_id, tenant_id=trusted.tenant_id, workspace_id=trusted.workspace_id,
        export_kind=export_kind, content_csv=content, content_sha256=digest,
        row_count=len(rows), created_by=trusted.actor_id, created_at=now,
    ))
    return {"id": export_id, "status": "ready", "version": 1, "rowCount": len(rows),
            "contentSha256": digest, "downloadUrl": f"/api/v1/admin/finance/exports/{export_id}"}


def _reconcile_supplier_lines(
    session: Session,
    trusted: TrustedWorkspaceContext,
    payload: dict[str, object] | None,
) -> dict[str, object]:
    values = None if payload is None else payload.get("externalLines")
    if not isinstance(values, list):
        raise HTTPException(422, detail={"code": "RECONCILIATION_LINES_REQUIRED"})
    external: dict[tuple[str, str], int] = {}
    for value in values:
        if not isinstance(value, dict):
            raise HTTPException(422, detail={"code": "RECONCILIATION_LINE_INVALID"})
        reference, currency, amount = value.get("externalId"), value.get("currency"), value.get("amountMinor")
        if (
            not isinstance(reference, str)
            or not reference.strip()
            or not isinstance(currency, str)
            or len(currency.strip()) != 3
            or isinstance(amount, bool)
            or not isinstance(amount, int)
            or amount < 0
        ):
            raise HTTPException(422, detail={"code": "RECONCILIATION_LINE_INVALID"})
        key = (reference.strip(), currency.strip().upper())
        if key in external:
            raise HTTPException(409, detail={"code": "RECONCILIATION_LINE_DUPLICATE"})
        external[key] = amount
    rows = session.execute(
        text(
            """
            SELECT reference,currency,amount_minor FROM xingjing_generation_billing_journals
             WHERE workspace_id=:workspace_id AND action='settle'
            UNION ALL
            SELECT reference,currency,amount_minor FROM xingjing_audio_billing_journals
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND action='settle'
            UNION ALL
            SELECT reference,currency,amount_minor FROM xingjing_editing_render_billing_journals
             WHERE tenant_id=:tenant_id AND workspace_id=:workspace_id AND action='settle'
            """
        ),
        {"tenant_id": trusted.tenant_id, "workspace_id": trusted.workspace_id},
    ).mappings()
    internal: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["reference"]), str(row["currency"]).upper())
        internal[key] = internal.get(key, 0) + int(row["amount_minor"])
    differences: list[dict[str, object]] = []
    for reference, currency in sorted(internal.keys() | external.keys()):
        internal_minor = internal.get((reference, currency))
        external_minor = external.get((reference, currency))
        if internal_minor == external_minor:
            continue
        kind = "missing_external" if external_minor is None else "unexpected_external" if internal_minor is None else "amount_mismatch"
        differences.append(
            {
                "kind": kind,
                "externalId": reference,
                "currency": currency,
                "internalMinor": internal_minor,
                "externalMinor": external_minor,
            }
        )
    return {
        "internalLineCount": len(internal),
        "externalLineCount": len(external),
        "differences": differences,
        "reviewRequired": bool(differences),
    }


def _approve_adjustment(
    session: Session,
    operation: FinanceOperationRow,
    trusted: TrustedWorkspaceContext,
    now: datetime,
) -> None:
    if "admin.finance.approve" not in trusted.permissions:
        raise HTTPException(403, detail={"code": "ADJUSTMENT_APPROVAL_PERMISSION_REQUIRED"})
    if operation.operation_type != "adjustment" or operation.status != "pending":
        raise HTTPException(409, detail={"code": "ADJUSTMENT_NOT_APPROVABLE"})
    account = session.execute(text(
        "SELECT available_minor,currency FROM xingjing_generation_billing_accounts "
        "WHERE workspace_id=:ws FOR UPDATE"
    ), {"ws": operation.workspace_id}).mappings().first()
    if account is None or str(account["currency"]) != operation.currency:
        raise HTTPException(409, detail={"code": "CREDIT_ACCOUNT_NOT_FOUND_OR_CURRENCY_MISMATCH"})
    next_available = int(account["available_minor"]) + operation.amount_minor
    if next_available < 0:
        raise HTTPException(409, detail={"code": "ADJUSTMENT_BALANCE_INSUFFICIENT"})
    session.execute(text(
        "UPDATE xingjing_generation_billing_accounts SET available_minor=:available,"
        "version=version+1,updated_at=:now WHERE workspace_id=:ws"
    ), {"available": next_available, "now": now, "ws": operation.workspace_id})
    session.execute(text("""
        INSERT INTO xingjing_generation_billing_journals
          (event_id,workspace_id,project_id,task_id,action,currency,amount_minor,
           postings,reference,occurred_at)
        VALUES
          (:event_id,:workspace_id,'platform',NULL,'admin_adjustment',:currency,:amount_minor,
           CAST(:postings AS jsonb),:reference,:occurred_at)
    """), {
        "event_id": f"adjustment:{operation.operation_id}",
        "workspace_id": operation.workspace_id,
        "currency": operation.currency,
        "amount_minor": operation.amount_minor,
        "postings": json.dumps([
            {"account": "platform.adjustment", "amountMinor": -operation.amount_minor},
            {"account": "credit.available", "amountMinor": operation.amount_minor},
        ]),
        "reference": f"adjustment:{operation.object_id}:{trusted.request_id}",
        "occurred_at": now,
    })


def _approve_refund(
    session: Session, operation: FinanceOperationRow,
    trusted: TrustedWorkspaceContext, now: datetime,
) -> None:
    if "admin.finance.approve" not in trusted.permissions:
        raise HTTPException(403, detail={"code": "REFUND_APPROVAL_PERMISSION_REQUIRED"})
    if operation.operation_type == "support_compensation":
        if operation.status != "pending_approval":
            raise HTTPException(409, detail={"code": "COMPENSATION_NOT_APPROVABLE"})
        account = session.execute(text(
            "SELECT available_minor,version FROM xingjing_generation_billing_accounts "
            "WHERE workspace_id=:ws FOR UPDATE"
        ), {"ws": operation.workspace_id}).mappings().first()
        if account is None:
            raise HTTPException(409, detail={"code": "CREDIT_ACCOUNT_NOT_FOUND"})
        session.execute(text(
            "UPDATE xingjing_generation_billing_accounts SET available_minor=available_minor+:amount,"
            "version=version+1,updated_at=:now WHERE workspace_id=:ws"
        ), {"amount": operation.amount_minor, "now": now, "ws": operation.workspace_id})
        session.execute(text("""
            INSERT INTO xingjing_generation_billing_journals
              (event_id,workspace_id,project_id,task_id,action,currency,amount_minor,
               postings,reference,occurred_at)
            VALUES
              (:event_id,:workspace_id,'platform',NULL,'support_compensation',
               :currency,:amount_minor,CAST(:postings AS jsonb),:reference,:occurred_at)
        """), {
            "event_id": f"support-compensation:{operation.operation_id}",
            "workspace_id": operation.workspace_id,
            "currency": operation.currency,
            "amount_minor": operation.amount_minor,
            "postings": json.dumps([
                {"account": "support.compensation_expense", "direction": "debit",
                 "amountMinor": operation.amount_minor},
                {"account": "credit.available", "direction": "credit",
                 "amountMinor": operation.amount_minor},
            ]),
            "reference": f"support:{operation.object_id}:{trusted.request_id}",
            "occurred_at": now,
        })
        session.execute(text(
            "UPDATE xingjing_admin_governance_objects "
            "SET status='completed',version=version+1,updated_by=:actor,updated_at=:now "
            "WHERE tenant_id=:tenant AND workspace_id=:ws AND resource='tickets' AND object_id=:ticket"
        ), {
            "actor": trusted.actor_id,
            "now": now,
            "tenant": operation.tenant_id,
            "ws": operation.workspace_id,
            "ticket": operation.object_id,
        })
        return
    if operation.operation_type != "refund" or operation.status != "pending":
        raise HTTPException(409, detail={"code": "REFUND_NOT_APPROVABLE"})
    order = session.execute(text(
        "SELECT amount_minor,currency,status,version,refunded_minor FROM xingjing_team_billing_orders "
        "WHERE tenant_id=:tenant AND workspace_id=:ws AND order_id=:id FOR UPDATE"
    ), {"tenant": operation.tenant_id, "ws": operation.workspace_id,
        "id": operation.object_id}).mappings().first()
    if (
        order is None
        or str(order["status"]) != "paid"
        or int(order["refunded_minor"]) + operation.amount_minor > int(order["amount_minor"])
    ):
        raise HTTPException(409, detail={"code": "ORDER_NOT_REFUNDABLE"})
    account = session.execute(text(
        "SELECT available_minor,version FROM xingjing_generation_billing_accounts "
        "WHERE workspace_id=:ws FOR UPDATE"
    ), {"ws": operation.workspace_id}).mappings().first()
    if account is None:
        raise HTTPException(409, detail={"code": "CREDIT_ACCOUNT_NOT_FOUND"})
    if int(account["available_minor"]) < operation.amount_minor:
        raise HTTPException(409, detail={"code": "REFUND_BALANCE_INSUFFICIENT"})
    session.execute(text(
        "UPDATE xingjing_generation_billing_accounts SET available_minor=available_minor-:amount,"
        "version=version+1,updated_at=:now WHERE workspace_id=:ws"
    ), {"amount": operation.amount_minor, "now": now, "ws": operation.workspace_id})
    session.execute(text(
        "UPDATE xingjing_team_billing_orders SET "
        "refunded_minor=refunded_minor+:amount,"
        "status=CASE WHEN refunded_minor+:amount=amount_minor THEN 'refunded' ELSE 'paid' END,"
        "version=version+1,updated_at=:now "
        "WHERE tenant_id=:tenant AND workspace_id=:ws AND order_id=:id"
    ), {"amount": operation.amount_minor, "now": now, "tenant": operation.tenant_id,
        "ws": operation.workspace_id, "id": operation.object_id})
    session.execute(text("""
        INSERT INTO xingjing_generation_billing_journals
          (event_id,workspace_id,project_id,task_id,action,currency,amount_minor,postings,reference,occurred_at)
        VALUES
          (:event_id,:workspace_id,'platform',NULL,'refund',:currency,:amount_minor,
           CAST(:postings AS jsonb),:reference,:occurred_at)
    """), {
        "event_id": f"refund:{operation.operation_id}",
        "workspace_id": operation.workspace_id,
        "currency": operation.currency,
        "amount_minor": operation.amount_minor,
        "postings": json.dumps([
            {"account": "credit.available", "direction": "debit", "amountMinor": operation.amount_minor},
            {"account": "refund.payable", "direction": "credit", "amountMinor": operation.amount_minor},
        ]),
        "reference": f"refund:{operation.object_id}:{trusted.request_id}",
        "occurred_at": now,
    })
