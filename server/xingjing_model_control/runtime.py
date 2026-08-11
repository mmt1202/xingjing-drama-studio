"""S05 persisted model catalog, routing, health, pricing and prompt control plane."""

from __future__ import annotations

import hashlib
import hmac
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver


def _now() -> datetime:
    return datetime.now(UTC)


class ModelControlBase(DeclarativeBase):
    pass


class ModelDefinitionRow(ModelControlBase):
    __tablename__ = "xingjing_model_definitions"
    registry_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(64), nullable=False)
    media_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    parameter_schema: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(128))
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ModelHealthRow(ModelControlBase):
    __tablename__ = "xingjing_model_health"
    registry_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    active_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    circuit_open_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ModelPricingRow(ModelControlBase):
    __tablename__ = "xingjing_model_pricing"
    __table_args__ = (UniqueConstraint("registry_key", "pricing_version", name="uq_xj_model_pricing_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    registry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ModelRoutingPolicyRow(ModelControlBase):
    __tablename__ = "xingjing_model_routing_policies"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "capability", "media_type", name="uq_xj_model_route_scope"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    capability: Mapped[str] = mapped_column(String(128), nullable=False)
    media_type: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_registry_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    preferred_registry_keys: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    max_estimated_minor: Mapped[int | None] = mapped_column(Integer)
    allow_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PromptTemplateRow(ModelControlBase):
    __tablename__ = "xingjing_prompt_templates"
    __table_args__ = (UniqueConstraint("template_key", "template_version", name="uq_xj_prompt_template_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    output_schema: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class ModelCallEvidenceRow(ModelControlBase):
    __tablename__ = "xingjing_model_call_evidence"
    __table_args__ = (Index("ix_xj_model_evidence_scope_time", "tenant_id", "workspace_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[str] = mapped_column(String(64), nullable=False)
    registry_key: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_job_id: Mapped[str | None] = mapped_column(String(255))
    request_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response_digest: Mapped[str | None] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    cost_minor: Mapped[int | None] = mapped_column(Integer)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_evidence_ref: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


@dataclass(frozen=True, slots=True)
class ModelRouteDecision:
    registry_key: str
    provider_id: str
    model_id: str
    pricing_version: str
    currency: str
    estimated_minor: int
    fallback_used: bool


class ModelRouteResolver:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def resolve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        capability: str,
        media_type: str,
        requested_provider_id: str | None,
        requested_model_id: str | None,
        parameters: Mapping[str, object],
        acquire: bool = False,
    ) -> ModelRouteDecision:
        now = _now()
        async with self._sessions() as session:
            policy = await session.scalar(
                select(ModelRoutingPolicyRow).where(
                    ModelRoutingPolicyRow.tenant_id == tenant_id,
                    ModelRoutingPolicyRow.workspace_id == workspace_id,
                    ModelRoutingPolicyRow.capability == capability,
                    ModelRoutingPolicyRow.media_type == media_type,
                )
            )
            statement = (
                select(ModelDefinitionRow, ModelHealthRow, ModelPricingRow)
                .join(ModelHealthRow, ModelHealthRow.registry_key == ModelDefinitionRow.registry_key, isouter=True)
                .join(
                    ModelPricingRow,
                    and_(
                        ModelPricingRow.registry_key == ModelDefinitionRow.registry_key,
                        ModelPricingRow.effective_at <= now,
                        or_(ModelPricingRow.retired_at.is_(None), ModelPricingRow.retired_at > now),
                    ),
                )
                .where(
                    ModelDefinitionRow.active.is_(True),
                    ModelDefinitionRow.media_types.contains([media_type]),
                    ModelDefinitionRow.capabilities.contains([capability]),
                )
                .order_by(ModelPricingRow.estimated_minor, ModelDefinitionRow.registry_key)
            )
            rows = list((await session.execute(statement)).all())
        candidates: list[tuple[ModelDefinitionRow, ModelHealthRow | None, ModelPricingRow]] = []
        for model, health, pricing in rows:
            if (
                requested_provider_id
                and model.provider_id != requested_provider_id
                or requested_model_id
                and model.provider_model_name != requested_model_id
            ):
                continue
            if policy and model.registry_key not in policy.allowed_registry_keys:
                continue
            if health and (
                health.status == "unhealthy"
                or health.circuit_open_until
                and _utc(health.circuit_open_until) > now
                or health.active_calls >= model.max_concurrency
            ):
                continue
            if (
                policy
                and policy.max_estimated_minor is not None
                and pricing.estimated_minor > policy.max_estimated_minor
            ):
                continue
            _validate_parameters(parameters, model.parameter_schema)
            candidates.append((model, health, pricing))
        if not candidates:
            raise LookupError("MODEL_ROUTE_UNAVAILABLE")
        preferred = policy.preferred_registry_keys if policy else []
        candidates.sort(
            key=lambda item: (
                preferred.index(item[0].registry_key) if item[0].registry_key in preferred else len(preferred),
                item[2].estimated_minor,
            )
        )
        selected = candidates[0]
        fallback = bool(preferred and selected[0].registry_key != preferred[0])
        if fallback and policy and not policy.allow_fallback:
            raise LookupError("MODEL_FALLBACK_NOT_ALLOWED")
        if acquire:
            async with self._sessions() as session, session.begin():
                health = await session.get(ModelHealthRow, selected[0].registry_key, with_for_update=True)
                if health is None:
                    health = ModelHealthRow(registry_key=selected[0].registry_key, status="unknown")
                    session.add(health)
                    await session.flush()
                if health.active_calls >= selected[0].max_concurrency:
                    raise LookupError("MODEL_CONCURRENCY_EXHAUSTED")
                if health.circuit_open_until and _utc(health.circuit_open_until) > now:
                    raise LookupError("MODEL_CIRCUIT_OPEN")
                health.active_calls += 1
        return ModelRouteDecision(
            selected[0].registry_key,
            selected[0].provider_id,
            selected[0].provider_model_name,
            selected[2].pricing_version,
            selected[2].currency,
            selected[2].estimated_minor,
            fallback,
        )

    async def release(self, *, provider_id: str, model_id: str, succeeded: bool) -> None:
        async with self._sessions() as session, session.begin():
            model = await session.scalar(
                select(ModelDefinitionRow)
                .where(
                    ModelDefinitionRow.provider_id == provider_id,
                    ModelDefinitionRow.provider_model_name == model_id,
                    ModelDefinitionRow.active.is_(True),
                )
                .limit(1)
            )
            if model is None:
                return
            health = await session.get(ModelHealthRow, model.registry_key, with_for_update=True)
            if health is None:
                return
            health.active_calls = max(0, health.active_calls - 1)
            if succeeded:
                health.status, health.consecutive_failures, health.circuit_open_until = "healthy", 0, None
            else:
                health.consecutive_failures += 1
                if health.consecutive_failures >= 3:
                    health.status = "unhealthy"
                    health.circuit_open_until = _now() + timedelta(
                        seconds=min(900, 30 * 2 ** (health.consecutive_failures - 3))
                    )
                else:
                    health.status = "degraded"
            health.checked_at = _now()


class ModelControlRuntime:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession] | None,
        resolver: TrustedWorkspaceContextResolver,
        worker_token: str,
        *,
        engine: AsyncEngine | None = None,
        unavailable_code: str | None = None,
    ) -> None:
        self.sessions, self.resolver, self.worker_token, self.engine, self.unavailable_code = (
            sessions,
            resolver,
            worker_token,
            engine,
            unavailable_code,
        )
        self.router = self._router()

    async def close(self) -> None:
        if self.engine:
            await self.engine.dispose()

    def route_resolver(self) -> ModelRouteResolver | None:
        return ModelRouteResolver(self.sessions) if self.sessions else None

    def _router(self) -> APIRouter:
        router = APIRouter()

        @router.get("/model-catalog", operation_id="list_model_catalog")
        async def catalog(request: Request):
            context = await self._context(request, "generation.view")
            async with self._session() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(ModelDefinitionRow)
                            .where(ModelDefinitionRow.active.is_(True))
                            .order_by(ModelDefinitionRow.registry_key)
                        )
                    ).all()
                )
            return _ok([_model(row) for row in rows], context.request_id)

        @router.post("/model-routing/resolve", operation_id="resolve_model_route")
        async def resolve_route(request: Request):
            context = await self._context(request, "generation.manage")
            body = await _body(request)
            try:
                decision = await cast(ModelRouteResolver, self.route_resolver()).resolve(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    capability=_required(body, "capability"),
                    media_type=_required(body, "mediaType"),
                    requested_provider_id=_optional(body.get("providerId")),
                    requested_model_id=_optional(body.get("modelId")),
                    parameters=cast(Mapping[str, object], body.get("parameters", {})),
                )
            except (LookupError, ValueError) as error:
                return _error(context.request_id, str(error), 409)
            return _ok(asdict(decision), context.request_id)

        @router.post("/admin/model-control/actions", operation_id="admin_model_control_action")
        async def admin_action(request: Request):
            context = await self._context(request, "admin.model.manage")
            body = await _body(request)
            action = _required(body, "action")
            async with self._session() as session, session.begin():
                result = await _apply_admin_action(session, action, body)
            return _ok(result, context.request_id)

        @router.post("/internal/model-control/health", operation_id="record_model_health")
        async def health(request: Request):
            self._worker(request)
            body = await _body(request)
            key, outcome = _required(body, "registryKey"), _required(body, "outcome")
            if outcome not in {"healthy", "failed"}:
                return _error(request.headers.get("X-Request-Id", ""), "MODEL_HEALTH_OUTCOME_INVALID", 400)
            async with self._session() as session, session.begin():
                row = await session.get(ModelHealthRow, key, with_for_update=True)
                if row is None:
                    row = ModelHealthRow(registry_key=key)
                    session.add(row)
                if outcome == "healthy":
                    row.status, row.consecutive_failures, row.circuit_open_until = "healthy", 0, None
                else:
                    row.consecutive_failures += 1
                    row.status = "unhealthy" if row.consecutive_failures >= 3 else "degraded"
                    row.circuit_open_until = (
                        _now() + timedelta(seconds=min(900, 30 * 2 ** max(0, row.consecutive_failures - 3)))
                        if row.consecutive_failures >= 3
                        else None
                    )
                row.latency_ms = _positive_int(body.get("latencyMs"), allow_zero=True)
                row.active_calls = _positive_int(body.get("activeCalls"), allow_zero=True) or 0
                row.checked_at = _now()
            return {
                "data": {
                    "registryKey": key,
                    "status": row.status,
                    "circuitOpenUntil": row.circuit_open_until.isoformat() if row.circuit_open_until else None,
                }
            }

        @router.post("/internal/model-control/evidence", operation_id="record_model_call_evidence")
        async def evidence(request: Request):
            self._worker(request)
            body = await _body(request)
            row = ModelCallEvidenceRow(
                id=str(uuid4()),
                tenant_id=_required(body, "tenantId"),
                workspace_id=_required(body, "workspaceId"),
                task_id=_required(body, "taskId"),
                registry_key=_required(body, "registryKey"),
                provider_job_id=_optional(body.get("providerJobId")),
                request_digest=_required_digest(body, "requestDigest"),
                response_digest=_optional_digest(body.get("responseDigest")),
                duration_ms=_positive_int(body.get("durationMs"), allow_zero=True),
                cost_minor=_positive_int(body.get("costMinor"), allow_zero=True),
                pricing_version=_required(body, "pricingVersion"),
                outcome=_required(body, "outcome"),
                raw_evidence_ref=_optional(body.get("rawEvidenceRef")),
            )
            async with self._session() as session, session.begin():
                session.add(row)
            return {"data": {"id": row.id}}

        return router

    async def _context(self, request: Request, permission: str):
        if self.unavailable_code:
            raise HTTPException(503, detail={"code": self.unavailable_code})
        context = await self.resolver(request)
        if permission not in context.permissions:
            raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
        return context

    def _session(self):
        if not self.sessions:
            raise HTTPException(503, detail={"code": self.unavailable_code or "MODEL_CONTROL_UNAVAILABLE"})
        return self.sessions()

    def _worker(self, request: Request) -> None:
        supplied = request.headers.get("X-Model-Control-Token", "")
        if not self.worker_token or not hmac.compare_digest(supplied, self.worker_token):
            raise HTTPException(401, detail={"code": "MODEL_CONTROL_UNAUTHENTICATED"})


def create_model_control_runtime(*, database_url: str | None = None) -> ModelControlRuntime:
    resolver = TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = (
        database_url
        or os.environ.get("XINGJING_MODEL_DATABASE_URL")
        or os.environ.get("XINGJING_GENERATION_DATABASE_URL", "")
    ).strip()
    token = os.environ.get("XINGJING_MODEL_CONTROL_TOKEN", "").strip()
    if not url:
        return ModelControlRuntime(None, resolver, token, unavailable_code="XINGJING_MODEL_DATABASE_URL_REQUIRED")
    if "+asyncpg" not in url and "+aiosqlite" not in url:
        return ModelControlRuntime(None, resolver, token, unavailable_code="XINGJING_MODEL_DATABASE_URL_MUST_BE_ASYNC")
    engine = create_async_engine(url, pool_pre_ping=True)
    return ModelControlRuntime(async_sessionmaker(engine, expire_on_commit=False), resolver, token, engine=engine)


async def _apply_admin_action(session: AsyncSession, action: str, body: Mapping[str, object]) -> dict[str, object]:
    if action == "upsert_model":
        key = _required(body, "registryKey")
        row = await session.get(ModelDefinitionRow, key, with_for_update=True)
        values = {
            "provider_id": _required(body, "providerId"),
            "provider_model_name": _required(body, "providerModelName"),
            "display_name": _required(body, "displayName"),
            "model_version": _required(body, "modelVersion"),
            "region": _required(body, "region"),
            "media_types": _string_list(body.get("mediaTypes")),
            "capabilities": _string_list(body.get("capabilities")),
            "parameter_schema": _mapping(body.get("parameterSchema")),
            "credential_ref": _optional(body.get("credentialRef")),
            "max_concurrency": _positive_int(body.get("maxConcurrency")) or 10,
            "active": body.get("active", True) is True,
            "updated_at": _now(),
        }
        if row is None:
            row = ModelDefinitionRow(registry_key=key, **values)
            session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
            row.version += 1
        return _model(row)
    if action == "publish_pricing":
        row = ModelPricingRow(
            registry_key=_required(body, "registryKey"),
            pricing_version=_required(body, "pricingVersion"),
            currency=_required(body, "currency").upper(),
            estimated_minor=_positive_int(body.get("estimatedMinor")) or 0,
            effective_at=_now(),
        )
        if len(row.currency) != 3 or row.estimated_minor <= 0:
            raise HTTPException(400, detail={"code": "MODEL_PRICING_INVALID"})
        session.add(row)
        return {"id": row.id, "pricingVersion": row.pricing_version}
    if action == "upsert_policy":
        tenant, workspace, capability, media = (
            _required(body, name) for name in ("tenantId", "workspaceId", "capability", "mediaType")
        )
        row = await session.scalar(
            select(ModelRoutingPolicyRow)
            .where(
                ModelRoutingPolicyRow.tenant_id == tenant,
                ModelRoutingPolicyRow.workspace_id == workspace,
                ModelRoutingPolicyRow.capability == capability,
                ModelRoutingPolicyRow.media_type == media,
            )
            .with_for_update()
        )
        values = {
            "allowed_registry_keys": _string_list(body.get("allowedRegistryKeys")),
            "preferred_registry_keys": _string_list(body.get("preferredRegistryKeys")),
            "max_estimated_minor": _positive_int(body.get("maxEstimatedMinor")),
            "allow_fallback": body.get("allowFallback", False) is True,
            "updated_at": _now(),
        }
        if row is None:
            row = ModelRoutingPolicyRow(
                tenant_id=tenant, workspace_id=workspace, capability=capability, media_type=media, **values
            )
            session.add(row)
        else:
            for name, value in values.items():
                setattr(row, name, value)
            row.version += 1
        return {"id": row.id, "version": row.version}
    if action == "publish_prompt":
        key, source, content = _required(body, "templateKey"), _required(body, "source"), _required(body, "content")
        current = await session.scalar(
            select(PromptTemplateRow)
            .where(PromptTemplateRow.template_key == key)
            .order_by(PromptTemplateRow.template_version.desc())
            .limit(1)
        )
        version = (current.template_version if current else 0) + 1
        if current:
            current.active = False
        row = PromptTemplateRow(
            template_key=key,
            template_version=version,
            source=source,
            content=content,
            output_schema=_mapping(body.get("outputSchema")),
            active=True,
        )
        session.add(row)
        return {"id": row.id, "templateKey": key, "version": version, "digest": _digest(content)}
    if action == "rollback_prompt":
        key = _required(body, "templateKey")
        target_version = _positive_int(body.get("targetVersion"))
        if target_version is None:
            raise HTTPException(400, detail={"code": "PROMPT_TARGET_VERSION_REQUIRED"})
        target = await session.scalar(select(PromptTemplateRow).where(PromptTemplateRow.template_key == key, PromptTemplateRow.template_version == target_version))
        if target is None:
            raise HTTPException(404, detail={"code": "PROMPT_TEMPLATE_VERSION_NOT_FOUND"})
        current = await session.scalar(select(PromptTemplateRow).where(PromptTemplateRow.template_key == key).order_by(PromptTemplateRow.template_version.desc()).limit(1))
        version = (current.template_version if current else 0) + 1
        if current:
            current.active = False
        row = PromptTemplateRow(template_key=key, template_version=version, source=f"rollback:{target_version}", content=target.content, output_schema=target.output_schema, active=True)
        session.add(row)
        return {"id": row.id, "templateKey": key, "version": version, "rolledBackFrom": target_version, "digest": _digest(row.content)}
    raise HTTPException(400, detail={"code": "MODEL_CONTROL_ACTION_UNSUPPORTED"})


def _validate_parameters(parameters: Mapping[str, object], schema: Mapping[str, object]) -> None:
    unknown = set(parameters) - set(schema)
    if unknown:
        raise ValueError("MODEL_PARAMETERS_UNKNOWN")
    for key, value in parameters.items():
        rule = schema.get(key)
        if not isinstance(rule, Mapping):
            raise ValueError("MODEL_PARAMETER_SCHEMA_INVALID")
        kind = rule.get("type")
        if (
            kind == "string"
            and not isinstance(value, str)
            or kind == "integer"
            and (not isinstance(value, int) or isinstance(value, bool))
            or kind == "boolean"
            and not isinstance(value, bool)
        ):
            raise ValueError("MODEL_PARAMETER_TYPE_INVALID")
        allowed = rule.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            raise ValueError("MODEL_PARAMETER_VALUE_INVALID")


async def _body(request: Request) -> Mapping[str, object]:
    try:
        value = await request.json()
    except ValueError as error:
        raise HTTPException(400, detail={"code": "INVALID_JSON"}) from error
    if not isinstance(value, Mapping):
        raise HTTPException(400, detail={"code": "INVALID_REQUEST_BODY"})
    return cast(Mapping[str, object], value)


def _model(row: ModelDefinitionRow) -> dict[str, object]:
    return {
        "registryKey": row.registry_key,
        "providerId": row.provider_id,
        "providerModelName": row.provider_model_name,
        "displayName": row.display_name,
        "modelVersion": row.model_version,
        "region": row.region,
        "mediaTypes": row.media_types,
        "capabilities": row.capabilities,
        "parameterSchema": row.parameter_schema,
        "credentialConfigured": bool(row.credential_ref),
        "maxConcurrency": row.max_concurrency,
        "active": row.active,
        "version": row.version,
    }


def _required(body: Mapping[str, object], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(400, detail={"code": f"{key.upper()}_REQUIRED"})
    return value.strip()


def _optional(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _mapping(value: object) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item.strip() for item in value):
        raise HTTPException(400, detail={"code": "STRING_LIST_REQUIRED"})
    return [cast(str, item).strip() for item in value]


def _positive_int(value: object, *, allow_zero: bool = False) -> int | None:
    return (
        value if isinstance(value, int) and not isinstance(value, bool) and value >= (0 if allow_zero else 1) else None
    )


def _required_digest(body: Mapping[str, object], key: str) -> str:
    value = _required(body, key)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value.lower()):
        raise HTTPException(400, detail={"code": "DIGEST_INVALID"})
    return value.lower()


def _optional_digest(value: object) -> str | None:
    if value is None:
        return None
    return _required_digest({"value": value}, "value")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _ok(data: object, request_id: str):
    return {"data": data, "meta": {"requestId": request_id}}


def _error(request_id: str, code: str, status_code: int):
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {"code": code, "message": code, "retryable": status_code >= 500, "details": []},
            "meta": {"requestId": request_id},
        },
    )
