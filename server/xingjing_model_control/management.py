"""Permission-scoped model operations using the live routing catalog."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import OutboxRow

from .admin_models import ModelAuditRow, ModelCommandRow
from .management_schema import (
    FAILURE_CEILING,
    MIN_SUCCESS_SAMPLES,
    SUCCESS_SAMPLE_SIZE,
    ActiveInput,
    ModelCommand,
    ModelInput,
    PolicyInput,
    PriceInput,
)
from .runtime import (
    ModelCallEvidenceRow,
    ModelDefinitionRow,
    ModelHealthRow,
    ModelPricingRow,
    ModelRoutingPolicyRow,
    _model,
    _utc,
)

if TYPE_CHECKING:
    from .runtime import ModelControlRuntime


@asynccontextmanager
async def read_session(runtime: ModelControlRuntime) -> AsyncIterator[AsyncSession]:
    try:
        async with runtime._session() as session:
            yield session
    except SQLAlchemyError as error:
        raise HTTPException(503, detail={"code": "MODEL_CONTROL_SCHEMA_UNAVAILABLE"}) from error


def require_workspace(request: Request, context: TrustedWorkspaceContext) -> None:
    expected = request.headers.get("X-Workspace-Id")
    if expected and expected != context.workspace_id:
        raise HTTPException(404, detail={"code": "WORKSPACE_NOT_FOUND"})


def policy_payload(row: ModelRoutingPolicyRow) -> dict[str, object]:
    return {
        "id": row.id,
        "capability": row.capability,
        "mediaType": row.media_type,
        "allowedRegistryKeys": row.allowed_registry_keys,
        "preferredRegistryKeys": row.preferred_registry_keys,
        "maxEstimatedMinor": row.max_estimated_minor,
        "allowFallback": row.allow_fallback,
        "routingOptions": row.routing_options or {},
        "version": row.version,
        "updatedAt": _utc(row.updated_at).isoformat(),
    }


def price_payload(row: ModelPricingRow) -> dict[str, object]:
    return {
        "id": row.id,
        "pricingVersion": row.pricing_version,
        "currency": row.currency,
        "estimatedMinor": row.estimated_minor,
        "effectiveAt": _utc(row.effective_at).isoformat(),
        "retiredAt": _utc(row.retired_at).isoformat() if row.retired_at else None,
    }


def health_payload(row: ModelHealthRow | None) -> dict[str, object]:
    return {
        "status": row.status if row else "unknown",
        "activeCalls": row.active_calls if row else 0,
        "consecutiveFailures": row.consecutive_failures if row else 0,
        "latencyMs": row.latency_ms if row else None,
        "circuitOpenUntil": _utc(row.circuit_open_until).isoformat() if row and row.circuit_open_until else None,
        "checkedAt": _utc(row.checked_at).isoformat() if row else None,
    }


async def current_price(session: AsyncSession, key: str) -> ModelPricingRow | None:
    now = datetime.now(UTC)
    return await session.scalar(
        select(ModelPricingRow)
        .where(
            ModelPricingRow.registry_key == key,
            ModelPricingRow.effective_at <= now,
            or_(ModelPricingRow.retired_at.is_(None), ModelPricingRow.retired_at > now),
        )
        .order_by(ModelPricingRow.effective_at.desc(), ModelPricingRow.id.desc())
        .limit(1)
    )


async def model_payload(session: AsyncSession, row: ModelDefinitionRow) -> dict[str, object]:
    price = await current_price(session, row.registry_key)
    return {
        **_model(row),
        "updatedAt": _utc(row.updated_at).isoformat(),
        "health": health_payload(await session.get(ModelHealthRow, row.registry_key)),
        "pricing": price_payload(price) if price else None,
    }


def audit_payload(row: ModelAuditRow) -> dict[str, object]:
    return {
        "id": row.id,
        "action": row.action,
        "objectId": row.object_id,
        "actorId": row.actor_id,
        "requestId": row.request_id,
        "reason": row.reason,
        "before": row.before_payload,
        "after": row.after_payload,
        "createdAt": _utc(row.created_at).isoformat(),
    }


def register_management_routes(runtime: ModelControlRuntime, router: APIRouter) -> None:
    @router.get("/admin/model-control/audit", operation_id="search_model_operations_audit")
    async def audit(
        request: Request,
        q: str = Query("", max_length=255),
        days: int = 0,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
    ):
        context = await runtime._context(request, "admin.model.view")
        require_workspace(request, context)
        if days not in {0, 7, 30}:
            raise HTTPException(422, detail={"code": "INVALID_DATE_RANGE"})
        filters = [ModelAuditRow.tenant_id == context.tenant_id, ModelAuditRow.workspace_id == context.workspace_id]
        if q.strip():
            filters.append(
                or_(
                    *(
                        column.icontains(q.strip(), autoescape=True)
                        for column in (
                            ModelAuditRow.actor_id,
                            ModelAuditRow.request_id,
                            ModelAuditRow.object_id,
                            ModelAuditRow.reason,
                        )
                    )
                )
            )
        if days:
            filters.append(ModelAuditRow.created_at >= datetime.now(UTC) - timedelta(days=days))
        async with read_session(runtime) as session:
            total = await session.scalar(select(func.count()).select_from(ModelAuditRow).where(*filters))
            rows = (
                await session.scalars(
                    select(ModelAuditRow)
                    .where(*filters)
                    .order_by(ModelAuditRow.created_at.desc(), ModelAuditRow.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
            return {
                "data": {
                    "items": [audit_payload(row) for row in rows],
                    "total": total or 0,
                    "offset": offset,
                    "limit": limit,
                },
                "meta": {"requestId": context.request_id},
            }

    @router.get("/admin/model-control", operation_id="read_model_operations")
    async def overview(
        request: Request,
        q: str = Query("", max_length=255),
        provider: str = "",
        media: Literal["", "image", "video", "audio", "text"] = "",
        state: Literal["", "active", "disabled"] = "",
        health: Literal["", "healthy", "degraded", "unhealthy", "unknown"] = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(12, ge=1, le=50),
    ):
        context = await runtime._context(request, "admin.model.view")
        require_workspace(request, context)
        try:
            async with read_session(runtime) as session:
                definitions = list(
                    (
                        await session.scalars(
                            select(ModelDefinitionRow).order_by(
                                ModelDefinitionRow.updated_at.desc(), ModelDefinitionRow.registry_key
                            )
                        )
                    ).all()
                )
                health_rows = {row.registry_key: row for row in (await session.scalars(select(ModelHealthRow))).all()}
                filtered = [
                    row
                    for row in definitions
                    if (
                        (
                            not q.strip()
                            or q.strip().casefold()
                            in f"{row.registry_key} {row.display_name} {row.provider_id}".casefold()
                        )
                        and (not provider or row.provider_id == provider)
                        and (not media or media in row.media_types)
                        and (not state or row.active == (state == "active"))
                        and (not health or str(health_payload(health_rows.get(row.registry_key))["status"]) == health)
                    )
                ]
                items = [await model_payload(session, row) for row in filtered[offset : offset + limit]]
                policies = (
                    await session.scalars(
                        select(ModelRoutingPolicyRow)
                        .where(
                            ModelRoutingPolicyRow.tenant_id == context.tenant_id,
                            ModelRoutingPolicyRow.workspace_id == context.workspace_id,
                        )
                        .order_by(ModelRoutingPolicyRow.capability, ModelRoutingPolicyRow.media_type)
                    )
                ).all()
                audits = (
                    await session.scalars(
                        select(ModelAuditRow)
                        .where(
                            ModelAuditRow.tenant_id == context.tenant_id,
                            ModelAuditRow.workspace_id == context.workspace_id,
                        )
                        .order_by(ModelAuditRow.created_at.desc(), ModelAuditRow.id.desc())
                        .limit(20)
                    )
                ).all()
                month = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                calls = await session.scalar(
                    select(func.count())
                    .select_from(ModelCallEvidenceRow)
                    .where(
                        ModelCallEvidenceRow.tenant_id == context.tenant_id,
                        ModelCallEvidenceRow.workspace_id == context.workspace_id,
                        ModelCallEvidenceRow.created_at >= month,
                    )
                )
                return {
                    "data": {
                        "context": {
                            "actorId": context.actor_id,
                            "workspaceId": context.workspace_id,
                            "permissions": sorted(context.permissions),
                            "role": context.role,
                        },
                        "items": items,
                        "total": len(filtered),
                        "offset": offset,
                        "limit": limit,
                        "summary": {
                            "total": len(definitions),
                            "healthy": sum(
                                row.status == "healthy"
                                for row in health_rows.values()
                                if row.registry_key in {d.registry_key for d in definitions}
                            ),
                            "abnormal": sum(
                                row.status in {"unhealthy", "degraded"}
                                for row in health_rows.values()
                                if row.registry_key in {d.registry_key for d in definitions}
                            ),
                            "monthlyCalls": calls or 0,
                        },
                        "policies": [policy_payload(row) for row in policies],
                        "audit": [audit_payload(row) for row in audits],
                        "catalog": [_model(row) for row in definitions],
                        "routingLimits": {
                            "failureCeiling": FAILURE_CEILING,
                            "successSampleSize": SUCCESS_SAMPLE_SIZE,
                            "minimumSuccessSamples": MIN_SUCCESS_SAMPLES,
                        },
                    },
                    "meta": {"requestId": context.request_id},
                }
        except SQLAlchemyError as error:
            raise HTTPException(503, detail={"code": "MODEL_CONTROL_SCHEMA_UNAVAILABLE"}) from error

    @router.get("/admin/model-control/detail", operation_id="read_model_operations_detail")
    async def detail(request: Request, registryKey: str = Query(..., min_length=1, max_length=128)):
        context = await runtime._context(request, "admin.model.view")
        require_workspace(request, context)
        async with read_session(runtime) as session:
            row = await session.get(ModelDefinitionRow, registryKey)
            if row is None:
                raise HTTPException(404, detail={"code": "MODEL_NOT_FOUND"})
            history = (
                await session.scalars(
                    select(ModelPricingRow)
                    .where(ModelPricingRow.registry_key == registryKey)
                    .order_by(ModelPricingRow.effective_at.desc(), ModelPricingRow.id.desc())
                    .limit(20)
                )
            ).all()
            return {
                "data": {
                    **await model_payload(session, row),
                    "priceHistory": [price_payload(item) for item in history],
                },
                "meta": {"requestId": context.request_id},
            }


async def execute_command(
    runtime: ModelControlRuntime, context: TrustedWorkspaceContext, request: Request, body: object
) -> dict[str, object]:
    require_workspace(request, context)
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise HTTPException(422, detail={"code": "IDEMPOTENCY_KEY_REQUIRED"})
    try:
        command = ModelCommand.model_validate(body)
        if not command.reason.strip():
            raise ValueError("REASON_REQUIRED")
        schema = {
            "upsert_model": ModelInput,
            "set_model_active": ActiveInput,
            "publish_pricing": PriceInput,
            "upsert_policy": PolicyInput,
        }[command.action]
        payload = schema.model_validate(command.payload)
    except (ValidationError, ValueError) as error:
        raise HTTPException(422, detail={"code": "MODEL_CONFIGURATION_INVALID"}) from error
    fingerprint = hashlib.sha256(
        json.dumps(command.model_dump(), ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    scope = (
        ModelCommandRow.tenant_id == context.tenant_id,
        ModelCommandRow.workspace_id == context.workspace_id,
        ModelCommandRow.actor_id == context.actor_id,
        ModelCommandRow.idempotency_key == key,
    )

    def replay(row: ModelCommandRow):
        if row.fingerprint != fingerprint:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return cast(dict[str, object], json.loads(row.result_json))

    try:
        async with runtime._session() as session, session.begin():
            if isinstance(payload, PolicyInput):
                policy_scope = (
                    ModelRoutingPolicyRow.tenant_id == context.tenant_id,
                    ModelRoutingPolicyRow.workspace_id == context.workspace_id,
                    ModelRoutingPolicyRow.capability == payload.capability,
                    ModelRoutingPolicyRow.media_type == payload.mediaType,
                )
                await session.execute(
                    update(ModelRoutingPolicyRow).where(*policy_scope).values(version=ModelRoutingPolicyRow.version)
                )
                policy = await session.scalar(select(ModelRoutingPolicyRow).where(*policy_scope).with_for_update())
                model = None
            else:
                if not command.registryKey:
                    raise HTTPException(422, detail={"code": "MODEL_KEY_REQUIRED"})
                await session.execute(
                    update(ModelDefinitionRow)
                    .where(ModelDefinitionRow.registry_key == command.registryKey)
                    .values(version=ModelDefinitionRow.version)
                )
                model = await session.get(ModelDefinitionRow, command.registryKey, with_for_update=True)
                policy = None
            receipt = await session.scalar(select(ModelCommandRow).where(*scope))
            if receipt:
                return replay(receipt)
            before = policy_payload(policy) if policy else _model(model) if model else {}
            if (policy.version if policy else model.version if model else 0) != command.version:
                raise HTTPException(409, detail={"code": "VERSION_CONFLICT"})
            now = datetime.now(UTC)
            if isinstance(payload, PolicyInput):
                rows = (
                    await session.scalars(
                        select(ModelDefinitionRow).where(
                            ModelDefinitionRow.registry_key.in_(payload.allowedRegistryKeys)
                        )
                    )
                ).all()
                if len(rows) != len(payload.allowedRegistryKeys) or any(
                    payload.capability not in row.capabilities or payload.mediaType not in row.media_types
                    for row in rows
                ):
                    raise HTTPException(422, detail={"code": "MODEL_POLICY_REFERENCE_INVALID"})
                if not any(row.active for row in rows):
                    raise HTTPException(422, detail={"code": "MODEL_POLICY_NO_ACTIVE_MODEL"})
                if policy is None:
                    policy = ModelRoutingPolicyRow(
                        id=str(uuid4()),
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        capability=payload.capability,
                        media_type=payload.mediaType,
                        version=0,
                    )
                    session.add(policy)
                policy.allowed_registry_keys, policy.preferred_registry_keys = (
                    payload.allowedRegistryKeys,
                    payload.preferredRegistryKeys,
                )
                policy.max_estimated_minor, policy.allow_fallback = payload.maxEstimatedMinor, payload.allowFallback
                policy.routing_options = payload.routingOptions.model_dump()
                policy.updated_at, policy.version = now, policy.version + 1
                after, object_id = policy_payload(policy), policy.id
            else:
                if isinstance(payload, ModelInput):
                    if model is None:
                        model = ModelDefinitionRow(registry_key=cast(str, command.registryKey), version=0)
                        session.add(model)
                    if model.version > 0 and (
                        model.provider_id != payload.providerId
                        or model.provider_model_name != payload.providerModelName
                    ):
                        raise HTTPException(422, detail={"code": "MODEL_IDENTITY_IMMUTABLE"})
                    values = {
                        "provider_id": payload.providerId,
                        "provider_model_name": payload.providerModelName,
                        "display_name": payload.displayName.strip(),
                        "model_version": payload.modelVersion.strip(),
                        "region": payload.region.strip(),
                        "media_types": payload.mediaTypes,
                        "capabilities": payload.capabilities,
                        "parameter_schema": payload.parameterSchema,
                        "max_concurrency": payload.maxConcurrency,
                        "active": payload.active,
                    }
                    if "credentialRef" in payload.model_fields_set:
                        values["credential_ref"] = payload.credentialRef
                    for name, value in values.items():
                        setattr(model, name, value)
                elif model is None:
                    raise HTTPException(404, detail={"code": "MODEL_NOT_FOUND"})
                if isinstance(payload, ActiveInput):
                    model.active = payload.active
                if isinstance(payload, PriceInput):
                    prior = await session.scalar(
                        select(ModelPricingRow).where(
                            ModelPricingRow.registry_key == model.registry_key,
                            ModelPricingRow.pricing_version == payload.pricingVersion,
                        )
                    )
                    if prior:
                        raise HTTPException(409, detail={"code": "PRICING_VERSION_EXISTS"})
                    before["pricing"] = (
                        price_payload(previous)
                        if (previous := await current_price(session, model.registry_key))
                        else None
                    )
                    await session.execute(
                        update(ModelPricingRow)
                        .where(ModelPricingRow.registry_key == model.registry_key, ModelPricingRow.retired_at.is_(None))
                        .values(retired_at=now)
                    )
                    session.add(
                        ModelPricingRow(
                            id=str(uuid4()),
                            registry_key=model.registry_key,
                            pricing_version=payload.pricingVersion,
                            currency=payload.currency,
                            estimated_minor=payload.estimatedMinor,
                            effective_at=now,
                        )
                    )
                await session.flush()
                if model.active and (
                    not model.credential_ref or await current_price(session, model.registry_key) is None
                ):
                    raise HTTPException(422, detail={"code": "MODEL_ACTIVATION_REQUIRES_CREDENTIAL_AND_PRICE"})
                model.version, model.updated_at = model.version + 1, now
                after, object_id = await model_payload(session, model), model.registry_key
            result = {"object": after, "objectId": object_id, "requestId": context.request_id, "status": "succeeded"}
            session.add(
                ModelCommandRow(
                    id=str(uuid4()),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    idempotency_key=key,
                    fingerprint=fingerprint,
                    result_json=json.dumps(result, ensure_ascii=False),
                    created_at=now,
                )
            )
            session.add(
                ModelAuditRow(
                    id=str(uuid4()),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    actor_id=context.actor_id,
                    request_id=context.request_id,
                    action=command.action,
                    object_id=object_id,
                    reason=command.reason.strip(),
                    before_payload=before,
                    after_payload=after,
                    created_at=now,
                )
            )
            session.add(
                OutboxRow(
                    id=str(uuid4()),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    event_type="model.configuration.changed",
                    aggregate_type="model_control",
                    aggregate_id=hashlib.sha256(object_id.encode()).hexdigest(),
                    payload={
                        "action": command.action,
                        "objectId": object_id,
                        "actorId": context.actor_id,
                        "requestId": context.request_id,
                        "version": after["version"],
                    },
                    created_at=now,
                )
            )
            return result
    except IntegrityError as error:
        async with runtime._session() as session:
            receipt = await session.scalar(select(ModelCommandRow).where(*scope))
            if receipt:
                return replay(receipt)
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"}) from error
    except SQLAlchemyError as error:
        raise HTTPException(503, detail={"code": "MODEL_CONTROL_SCHEMA_UNAVAILABLE"}) from error
