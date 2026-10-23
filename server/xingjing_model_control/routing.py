"""Select from published catalog prices using effective operator policies."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .management_schema import MIN_SUCCESS_SAMPLES, SUCCESS_SAMPLE_SIZE, RoutingOptions
from .runtime import (
    ModelCallEvidenceRow,
    ModelDefinitionRow,
    ModelHealthRow,
    ModelPricingRow,
    ModelRoutingPolicyRow,
    _utc,
    _validate_parameters,
)

type Candidate = tuple[ModelDefinitionRow, ModelHealthRow | None, ModelPricingRow]


async def choose_candidate(
    session: AsyncSession,
    rows: Sequence[Candidate],
    policy: ModelRoutingPolicyRow | None,
    *,
    tenant_id: str,
    workspace_id: str,
    capability: str,
    media_type: str,
    requested_provider_id: str | None,
    requested_model_id: str | None,
    parameters: Mapping[str, object],
    now: datetime,
) -> tuple[Candidate, bool]:
    options = RoutingOptions.model_validate(policy.routing_options or {}) if policy else RoutingOptions()
    candidates: list[Candidate] = []
    seen: set[str] = set()
    parameter_error: ValueError | None = None
    for model, health, price in rows:
        if model.registry_key in seen:
            continue
        seen.add(model.registry_key)
        if media_type not in model.media_types or capability not in model.capabilities or not model.credential_ref:
            continue
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
            or options.maxLatencyMs is not None
            and health.latency_ms is not None
            and health.latency_ms > options.maxLatencyMs
            or options.maxConsecutiveFailures is not None
            and health.consecutive_failures >= options.maxConsecutiveFailures
        ):
            continue
        if options.currency and price.currency != options.currency:
            continue
        if policy and policy.max_estimated_minor is not None and price.estimated_minor > policy.max_estimated_minor:
            continue
        if options.minSuccessRate is not None:
            evidence = (
                await session.scalars(
                    select(ModelCallEvidenceRow.outcome)
                    .where(
                        ModelCallEvidenceRow.tenant_id == tenant_id,
                        ModelCallEvidenceRow.workspace_id == workspace_id,
                        ModelCallEvidenceRow.registry_key == model.registry_key,
                        ModelCallEvidenceRow.outcome.in_(
                            ("succeeded", "success", "healthy", "completed", "failed", "error", "timeout")
                        ),
                    )
                    .order_by(ModelCallEvidenceRow.created_at.desc(), ModelCallEvidenceRow.id.desc())
                    .limit(SUCCESS_SAMPLE_SIZE)
                )
            ).all()
            if len(evidence) >= MIN_SUCCESS_SAMPLES:
                success = sum(value in {"succeeded", "success", "healthy", "completed"} for value in evidence)
                if success * 100 / len(evidence) < options.minSuccessRate:
                    continue
        try:
            _validate_parameters(parameters, model.parameter_schema)
        except ValueError as error:
            parameter_error = error
            continue
        candidates.append((model, health, price))
    if not candidates:
        if parameter_error:
            raise parameter_error
        raise LookupError("MODEL_ROUTE_UNAVAILABLE")
    preferred = policy.preferred_registry_keys if policy else []
    candidates.sort(
        key=lambda item: (
            preferred.index(item[0].registry_key) if item[0].registry_key in preferred else len(preferred),
            item[2].estimated_minor,
            item[0].registry_key,
        )
    )
    if options.mode == "weighted":
        primary = [item for item in candidates if item[0].registry_key in options.weights]
        if primary:
            total = sum(options.weights[item[0].registry_key] for item in primary)
            pick = secrets.randbelow(total)
            for item in primary:
                pick -= options.weights[item[0].registry_key]
                if pick < 0:
                    return item, False
        if policy and not policy.allow_fallback:
            raise LookupError("MODEL_FALLBACK_NOT_ALLOWED")
        return candidates[0], True
    selected = candidates[0]
    fallback = bool(preferred and selected[0].registry_key != preferred[0])
    if fallback and policy and not policy.allow_fallback:
        raise LookupError("MODEL_FALLBACK_NOT_ALLOWED")
    return selected, fallback
