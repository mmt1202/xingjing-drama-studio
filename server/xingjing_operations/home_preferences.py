"""Validated, serialized home preferences using the existing account records."""

from __future__ import annotations

import json
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


class HomePreferences(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    showRecent: bool = True
    showTasks: bool = True
    showInspiration: bool = True
    defaultMode: Literal["pilot", "series", "comic", "realistic"] = "pilot"
    recentDays: Literal[0, 7, 30] = 0
    recentLimit: Literal[3, 6, 9] = 6


def require_home_workspace(request: Request, trusted: TrustedWorkspaceContext) -> None:
    expected = request.headers.get("X-Workspace-Id")
    if expected and expected != trusted.workspace_id:
        raise HTTPException(404, detail={"code": "WORKSPACE_NOT_FOUND"})


def save_home_preferences(
    sessions: sessionmaker[Session],
    trusted: TrustedWorkspaceContext,
    payload: PreferencePayload,
    request: Request,
    key: str,
) -> dict[str, object]:
    from .runtime import _append_evidence, _write_object

    require_home_workspace(request, trusted)
    try:
        value = HomePreferences.model_validate(payload.value).model_dump(exclude_unset=True)
    except ValidationError as error:
        raise HTTPException(422, detail={"code": "INVALID_PREFERENCE"}) from error
    if payload.version < 0:
        raise HTTPException(422, detail={"code": "INVALID_PREFERENCE"})
    command = ActionPayload(
        action="保存设置",
        objectId=trusted.actor_id,
        version=payload.version,
        resource="preference:creator-home",
        payload=value,
    )
    fingerprint = (
        "sha256:" + sha256(json.dumps(command.model_dump(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
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
                GovernanceObjectRow.resource == "preference:creator-home",
                GovernanceObjectRow.object_id == trusted.actor_id,
            )
            # This also starts a SQLite writer transaction before the receipt read.
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
            _append_evidence(session, "preferences", command, before, after, trusted, request, now)
            return result
    except IntegrityError as error:
        # Concurrent first writes have no row to lock. Their unique constraints
        # still select one winner; a duplicate request receives its saved result.
        with sessions() as session:
            receipt = session.scalar(select(CommandRow).where(*receipt_scope))
            if receipt:
                return replay(receipt)
        raise HTTPException(409, detail={"code": "VERSION_CONFLICT"}) from error
