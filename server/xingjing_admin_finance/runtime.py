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


class ModelDefinitionRow(Base):
    __tablename__ = "xingjing_admin_model_definitions"
    provider_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    model_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(256), nullable=False)
    capability: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    health: Mapped[str] = mapped_column(String(32), nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quota_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    routing_weight: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FinanceOperationRow(Base):
    __tablename__ = "xingjing_admin_finance_operations"
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
        UniqueConstraint("actor_id", "idempotency_key", name="uq_xj_admin_finance_command"),
    )
    command_id: Mapped[str] = mapped_column(String(36), primary_key=True)
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
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    domain: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
            session.execute(text("SELECT 1 FROM xingjing_admin_model_definitions LIMIT 1"))
            session.execute(text("SELECT 1 FROM xingjing_admin_finance_operations LIMIT 1"))
        _synchronize_model_catalog(sessions)
    except SQLAlchemyError as error:
        engine.dispose()
        raise RuntimeError("XINGJING_FINANCE_MIGRATION_REQUIRED") from error
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    return AdminFinanceRuntime(_router(sessions, resolver), engine)


def _synchronize_model_catalog(sessions: sessionmaker[Session]) -> None:
    raw = os.environ.get("XINGJING_M06_MODEL_CATALOG_JSON", "").strip()
    if not raw:
        return
    try:
        catalog = json.loads(raw)
    except json.JSONDecodeError as error:
        raise RuntimeError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID") from error
    if not isinstance(catalog, list):
        raise RuntimeError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
    now = datetime.now(UTC)
    default_price = _environment_integer("XINGJING_M06_ESTIMATED_COST_MINOR")
    pricing_version = os.environ.get("XINGJING_M06_PRICING_VERSION", "unconfigured").strip()
    with sessions.begin() as session:
        for value in catalog:
            if not isinstance(value, dict):
                raise RuntimeError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
            provider_id = _required_catalog_text(value, "providerId")
            model_id = _required_catalog_text(value, "modelId")
            if session.get(ModelDefinitionRow, (provider_id, model_id)):
                continue
            capabilities = value.get("capabilities")
            capability = ",".join(item for item in capabilities if isinstance(item, str)) if isinstance(capabilities, list) else ""
            session.add(ModelDefinitionRow(
                provider_id=provider_id, model_id=model_id,
                display_name=_required_catalog_text(value, "displayName"),
                capability=capability or "generation",
                model_version=_required_catalog_text(value, "version"),
                status="active" if bool(value.get("active", True)) else "disabled",
                health="unknown", unit_price_minor=default_price, currency="CNY",
                quota_per_minute=0, routing_weight=100, version=1,
                updated_by="deployment-catalog", updated_at=now,
            ))
            session.add(AuditRow(
                audit_id=str(uuid4()), actor_id="deployment-catalog",
                workspace_id="platform", request_id=f"catalog:{pricing_version}",
                domain="model", action="catalog.import",
                object_id=f"{provider_id}:{model_id}", before_payload={},
                after_payload={"version": 1, "status": "active"}, occurred_at=now,
            ))


def _required_catalog_text(value: dict[object, object], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise RuntimeError("XINGJING_M06_MODEL_CATALOG_JSON_INVALID")
    return candidate.strip()


def _environment_integer(name: str) -> int:
    raw = os.environ.get(name, "0").strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name}_INVALID") from error
    if value < 0:
        raise RuntimeError(f"{name}_INVALID")
    return value


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
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.finance.view")
        with sessions() as session:
            records = _finance_records(session, resource, trusted.workspace_id)
        start = (page - 1) * page_size
        return {"items": records[start:start + page_size], "page": page,
                "pageSize": page_size, "total": len(records)}

    @router.get("/models")
    def models(
        resource: str = Query("models"), page: int = Query(1, ge=1),
        page_size: int = Query(20, alias="pageSize", ge=1, le=100),
        trusted: TrustedWorkspaceContext = Depends(resolve),
    ) -> dict[str, object]:
        require(trusted, "admin.model.view")
        with sessions() as session:
            records = _model_records(session, resource, trusted.workspace_id)
        start = (page - 1) * page_size
        return {"items": records[start:start + page_size], "page": page,
                "pageSize": page_size, "total": len(records)}

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
                command_id=str(uuid4()), actor_id=trusted.actor_id,
                idempotency_key=idempotency_key, fingerprint=fingerprint,
                result_json=json.dumps(result, ensure_ascii=False), created_at=now,
            ))
            session.add(AuditRow(
                audit_id=str(uuid4()), workspace_id=trusted.workspace_id,
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


def _query(session: Session, statement: str, workspace_id: str) -> list[dict[str, object]]:
    return [_row(dict(item)) for item in session.execute(text(statement), {"ws": workspace_id}).mappings()]


def _finance_records(session: Session, resource: str, workspace_id: str) -> list[dict[str, object]]:
    statements = {
        "billing": """SELECT workspace_id id, '算力账户 '||workspace_id name, 'active' status,
          version, updated_at, jsonb_build_object('availableMinor',available_minor,'heldMinor',held_minor,
          'spentMinor',spent_minor,'currency',currency) details
          FROM xingjing_generation_billing_accounts WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "costs": """SELECT event_id id, reference name, action status, 1 version, occurred_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'projectId',project_id,
          'taskId',task_id) details FROM xingjing_generation_billing_journals
          WHERE workspace_id=:ws ORDER BY occurred_at DESC,event_id""",
        "invoices": """SELECT invoice_id id, invoice_title name, status, 1 version, updated_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency) details
          FROM xingjing_team_invoice_requests WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "orders": """SELECT order_id id, order_type name, status, version, updated_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'externalReference',external_reference)
          details FROM xingjing_team_billing_orders WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "entitlements": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas,
          'quotaRemaining',quota_remaining) details FROM xingjing_team_billing_plans
          WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "plans": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas) details
          FROM xingjing_team_billing_plans WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "plan-detail": """SELECT workspace_id id, plan_id name, status, version, updated_at,
          jsonb_build_object('seatLimit',seat_limit,'features',features,'quotas',quotas,
          'quotaRemaining',quota_remaining) details FROM xingjing_team_billing_plans
          WHERE workspace_id=:ws ORDER BY updated_at DESC""",
        "reconciliation": """SELECT operation_id id, object_id name, status, version, updated_at,
          jsonb_build_object('type',operation_type,'amountMinor',amount_minor,'currency',currency,
          'reason',reason,'result',result_payload)
          details FROM xingjing_admin_finance_operations
          WHERE workspace_id=:ws AND operation_type='reconciliation' ORDER BY updated_at DESC""",
        "refunds": """SELECT operation_id id, object_id name, status, version, updated_at,
          jsonb_build_object('type',operation_type,'amountMinor',amount_minor,'currency',currency,'reason',reason)
          details FROM xingjing_admin_finance_operations
          WHERE workspace_id=:ws AND operation_type IN ('refund','support_compensation')
          ORDER BY updated_at DESC""",
        "revenue": """SELECT event_id id, reference name, action status, 1 version, occurred_at,
          jsonb_build_object('amountMinor',amount_minor,'currency',currency,'projectId',project_id) details
          FROM xingjing_generation_billing_journals WHERE workspace_id=:ws
          AND action IN ('settle','credit','purchase','revenue_share') ORDER BY occurred_at DESC,event_id""",
        "audit": """SELECT audit_id id, action name, domain status, 1 version, occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectId',object_id,
          'before',before_payload,'after',after_payload) details
          FROM xingjing_admin_finance_audit WHERE workspace_id=:ws AND domain='finance'
          ORDER BY occurred_at DESC,audit_id""",
    }
    statement = statements.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    return _query(session, statement, workspace_id)


def _model_records(session: Session, resource: str, workspace_id: str) -> list[dict[str, object]]:
    statements = {
        "models": """SELECT provider_id||':'||model_id id, display_name name, status, version, updated_at,
          jsonb_build_object('providerId',provider_id,'modelId',model_id,'capability',capability,
          'modelVersion',model_version,'health',health,'unitPriceMinor',unit_price_minor,'currency',currency,
          'quotaPerMinute',quota_per_minute,'routingWeight',routing_weight) details
          FROM xingjing_admin_model_definitions ORDER BY updated_at DESC,provider_id,model_id""",
        "callback-logs": """SELECT evidence_id id, COALESCE(payload->>'provider','模型回调') name,
          COALESCE(payload->>'status','received') status, 1 version, occurred_at,
          jsonb_build_object('projectId',project_id,'taskId',task_id,'evidence',payload) details
          FROM xingjing_generation_provider_evidence WHERE workspace_id=:ws
          ORDER BY occurred_at DESC,evidence_id""",
        "quality": """SELECT task_id id, COALESCE(snapshot->>'modelId',task_id) name, status, version, updated_at,
          jsonb_build_object('projectId',project_id,'modelId',snapshot->>'modelId',
          'providerId',snapshot->>'providerId','failureCode',snapshot->>'failureCode') details
          FROM xingjing_generation_tasks WHERE workspace_id=:ws ORDER BY updated_at DESC,task_id""",
        "audit": """SELECT audit_id id, action name, domain status, 1 version, occurred_at,
          jsonb_build_object('requestId',request_id,'actorId',actor_id,'objectId',object_id,
          'before',before_payload,'after',after_payload) details
          FROM xingjing_admin_finance_audit
          WHERE (workspace_id=:ws OR workspace_id='platform') AND domain='model'
          ORDER BY occurred_at DESC,audit_id""",
    }
    statement = statements.get(resource)
    if statement is None:
        raise HTTPException(422, detail={"code": "UNSUPPORTED_RESOURCE"})
    return _query(session, statement, workspace_id)


def _apply_action(
    session: Session, domain: str, payload: ActionPayload,
    trusted: TrustedWorkspaceContext, now: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    if domain == "model":
        if payload.action in {"重新校验", "刷新统计"}:
            return {"version": payload.version}, {
                "id": payload.objectId,
                "status": "revalidation_requested" if payload.action == "重新校验" else "statistics_refreshed",
                "version": payload.version + 1,
            }
        provider_id, separator, model_id = payload.objectId.partition(":")
        if not separator:
            raise HTTPException(422, detail={"code": "MODEL_OBJECT_ID_INVALID"})
        model = session.get(ModelDefinitionRow, (provider_id, model_id))
        if model is None:
            raise HTTPException(404, detail={"code": "MODEL_NOT_FOUND"})
        if model.version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        before = {"status": model.status, "health": model.health, "version": model.version}
        if payload.action == "更新模型":
            model.status = "active" if model.status != "active" else "disabled"
        elif payload.action == "重新校验":
            model.health = "checking"
        elif payload.action == "刷新统计":
            model.health = model.health
        else:
            raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})
        model.version += 1
        model.updated_by, model.updated_at = trusted.actor_id, now
        return before, {"id": payload.objectId, "status": model.status,
                        "health": model.health, "version": model.version}

    if payload.action in {"发起调账", "确认对账", "执行退款审批"}:
        operation_type = {
            "发起调账": "adjustment", "确认对账": "reconciliation", "执行退款审批": "refund",
        }[payload.action]
        operation = session.get(FinanceOperationRow, payload.objectId)
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
                "WHERE workspace_id=:ws AND order_id=:id FOR UPDATE"
            ), {"ws": trusted.workspace_id, "id": payload.objectId}).mappings().first()
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
            operation_id=payload.objectId, workspace_id=trusted.workspace_id,
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
            "SELECT version,status FROM xingjing_team_billing_plans "
            "WHERE workspace_id=:ws AND (workspace_id=:id OR plan_id=:id) FOR UPDATE"
        ), {"ws": trusted.workspace_id, "id": payload.objectId}).mappings().first()
        if plan is None:
            raise HTTPException(404, detail={"code": "PLAN_NOT_FOUND"})
        if int(plan["version"]) != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        status = "active" if payload.action == "发布套餐" else str(plan["status"])
        session.execute(text(
            "UPDATE xingjing_team_billing_plans SET status=:status,version=version+1,updated_at=:now "
            "WHERE workspace_id=:ws AND (workspace_id=:id OR plan_id=:id) AND version=:version"
        ), {"status": status, "now": now, "ws": trusted.workspace_id,
            "id": payload.objectId, "version": payload.version})
        return {"status": plan["status"], "version": payload.version}, {
            "id": payload.objectId, "status": status, "version": payload.version + 1,
        }

    if payload.action in {"执行审批", "更新订单"}:
        table = "xingjing_team_invoice_requests" if payload.action == "执行审批" else "xingjing_team_billing_orders"
        id_column = "invoice_id" if payload.action == "执行审批" else "order_id"
        row = session.execute(text(
            f"SELECT status{',version' if id_column == 'order_id' else ''} FROM {table} "
            f"WHERE workspace_id=:ws AND {id_column}=:id FOR UPDATE"
        ), {"ws": trusted.workspace_id, "id": payload.objectId}).mappings().first()
        if row is None:
            raise HTTPException(404, detail={"code": "FINANCE_OBJECT_NOT_FOUND"})
        current_version = int(row.get("version") or 1)
        if current_version != payload.version:
            raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
        next_status = "approved" if id_column == "invoice_id" else str(row["status"])
        version_sql = ",version=version+1" if id_column == "order_id" else ""
        session.execute(text(
            f"UPDATE {table} SET status=:status,updated_at=:now{version_sql} "
            f"WHERE workspace_id=:ws AND {id_column}=:id"
        ), {"status": next_status, "now": now, "ws": trusted.workspace_id, "id": payload.objectId})
        return {"status": row["status"], "version": current_version}, {
            "id": payload.objectId, "status": next_status, "version": current_version + 1,
        }

    if payload.action in {"导出对账", "导出收入"}:
        return {}, {"id": payload.objectId, "status": "exported", "version": payload.version + 1}
    raise HTTPException(422, detail={"code": "UNSUPPORTED_ACTION"})


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
            "WHERE workspace_id=:ws AND resource='tickets' AND object_id=:ticket"
        ), {
            "actor": trusted.actor_id,
            "now": now,
            "ws": operation.workspace_id,
            "ticket": operation.object_id,
        })
        return
    if operation.operation_type != "refund" or operation.status != "pending":
        raise HTTPException(409, detail={"code": "REFUND_NOT_APPROVABLE"})
    order = session.execute(text(
        "SELECT amount_minor,currency,status,version,refunded_minor FROM xingjing_team_billing_orders "
        "WHERE workspace_id=:ws AND order_id=:id FOR UPDATE"
    ), {"ws": operation.workspace_id, "id": operation.object_id}).mappings().first()
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
        "WHERE workspace_id=:ws AND order_id=:id"
    ), {"amount": operation.amount_minor, "now": now, "ws": operation.workspace_id, "id": operation.object_id})
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
