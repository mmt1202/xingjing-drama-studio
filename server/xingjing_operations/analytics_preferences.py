"""Actor-scoped dashboard defaults with durable command receipts."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_admin_governance.runtime import ActionPayload, CommandRow, GovernanceObjectRow
from server.xingjing_identity_context import TrustedWorkspaceContext

if TYPE_CHECKING:
    from .runtime import PreferencePayload


class AnalyticsPreferences(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    range: Literal["week", "month", "quarter", "all"] = "month"
    groupBy: Literal["project", "episode", "model", "member"] = "project"


def require_analytics_context(request: Request, trusted: TrustedWorkspaceContext) -> None:
    if trusted.role == "API_KEY":
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
    expected = request.headers.get("X-Workspace-Id")
    if expected and expected != trusted.workspace_id:
        raise HTTPException(404, detail={"code": "WORKSPACE_NOT_FOUND"})


def save_analytics_preferences(
    sessions: sessionmaker[Session],
    trusted: TrustedWorkspaceContext,
    payload: PreferencePayload,
    request: Request,
    key: str,
) -> dict[str, object]:
    from .runtime import _append_evidence, _write_object

    require_analytics_context(request, trusted)
    if "analytics.view" not in trusted.permissions or "analytics.manage" not in trusted.permissions:
        raise HTTPException(403, detail={"code": "PERMISSION_DENIED"})
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", key) or payload.version < 0:
        raise HTTPException(422, detail={"code": "INVALID_PREFERENCE"})
    try:
        value = AnalyticsPreferences.model_validate(payload.value).model_dump()
    except ValidationError as error:
        raise HTTPException(422, detail={"code": "INVALID_PREFERENCE"}) from error
    command = ActionPayload(
        action="save_dashboard_preferences",
        objectId=trusted.actor_id,
        version=payload.version,
        resource="preference:data-dashboard",
        payload=value,
    )
    fingerprint = (
        "sha256:"
        + sha256(json.dumps(command.model_dump(), ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    )
    receipt_scope = (
        CommandRow.tenant_id == trusted.tenant_id,
        CommandRow.workspace_id == trusted.workspace_id,
        CommandRow.actor_id == trusted.actor_id,
        CommandRow.idempotency_key == key,
    )

    def replay(row: CommandRow) -> dict[str, object]:
        if row.fingerprint != fingerprint:
            raise HTTPException(409, detail={"code": "IDEMPOTENCY_CONFLICT"})
        return cast(dict[str, object], json.loads(row.result_json))

    try:
        with sessions.begin() as session:
            scope = (
                GovernanceObjectRow.tenant_id == trusted.tenant_id,
                GovernanceObjectRow.workspace_id == trusted.workspace_id,
                GovernanceObjectRow.resource == "preference:data-dashboard",
                GovernanceObjectRow.object_id == trusted.actor_id,
            )
            # Serialize updates before checking receipts, including SQLite previews.
            session.execute(update(GovernanceObjectRow).where(*scope).values(version=GovernanceObjectRow.version))
            session.scalar(select(GovernanceObjectRow).where(*scope).with_for_update())
            receipt = session.scalar(select(CommandRow).where(*receipt_scope))
            if receipt:
                return replay(receipt)
            now = datetime.now(UTC)
            before, after = _write_object(session, "preferences", command, trusted, now)
            result = {
                "resource": payload.resource,
                "value": after["payload"],
                "version": after["version"],
                "updatedAt": now.isoformat(),
            }
            session.add(
                CommandRow(
                    command_id=str(uuid4()),
                    tenant_id=trusted.tenant_id,
                    workspace_id=trusted.workspace_id,
                    actor_id=trusted.actor_id,
                    idempotency_key=key,
                    fingerprint=fingerprint,
                    result_json=json.dumps(result, ensure_ascii=False),
                    created_at=now,
                )
            )
            _append_evidence(session, "analytics-preferences", command, before, after, trusted, request, now)
            return result
    except IntegrityError as error:
        # Concurrent first inserts are resolved by the unique object/receipt keys.
        with sessions() as session:
            receipt = session.scalar(select(CommandRow).where(*receipt_scope))
            if receipt:
                return replay(receipt)
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"}) from error
