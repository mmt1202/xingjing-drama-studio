"""Evidence-gated project stage changes with durable, scoped replay receipts."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select, update

from lib.episode_editor import EpisodeEditError, read_episodes
from lib.project_manager import get_project_manager
from server.xingjing_platform_persistence import PersistenceUnitOfWork, Scope
from server.xingjing_platform_persistence.persistence import AuditRow, ProjectRow
from server.xingjing_team_persistence.repository import MemberRow

from .control import production_facts

if TYPE_CHECKING:
    from .runtime import ProjectsRuntime


# Stage changes record verified progress. Generation, approval and export commands
# continue to execute in the module that owns the corresponding artifacts.
FORWARD = {
    "draft": ("source_imported", "source", {"script.view"}),
    "source_imported": ("analyzing", "source", {"script.view"}),
    "analyzing": ("analyzed", "analyzed", {"script.view"}),
    "analyzed": ("asset_preparing", "director", {"script.view"}),
    "asset_preparing": ("asset_ready", "assetsReady", {"script.view", "asset.view", "final.view"}),
    "asset_ready": ("storyboard_preparing", "assetsReady", {"script.view", "asset.view", "final.view"}),
    "storyboard_preparing": ("production", "boardsReady", {"shot.view"}),
    "production": ("review", "finalsReady", {"final.view"}),
    "review": ("finishing", "approved", {"final.view", "review.view"}),
    "finishing": ("compliance_check", "approved", {"final.view", "review.view"}),
    "blocked": ("compliance_check", "complianceClear", {"final.view", "compliance.view"}),
    "compliance_check": (
        "completed",
        "packagesReady",
        {"script.view", "asset.view", "final.view", "compliance.view", "export.view", "review.view"},
    ),
}
BACKWARD = {"analyzing": "source_imported", "review": "production"}


class OverviewEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=255)
    ownerId: str = Field(min_length=1, max_length=64)
    targetPlatform: str = Field(pattern="^(douyin|kuaishou|wechat|bilibili|web)$")
    budgetMinor: int | None = Field(default=None, ge=1, le=100_000_000_000)
    scheduleDays: int | None = Field(default=None, ge=1, le=3650)
    teamSeats: int = Field(ge=1, le=1000)
    reviewSeats: int = Field(ge=1, le=1000)


async def sync_overview_projection(uow: PersistenceUnitOfWork, context, project_id: str):
    # SQL owns these plan fields. Replaying a committed command repairs a failed
    # filesystem projection using the current SQL value, never the old receipt.
    scope = (
        ProjectRow.id == project_id,
        ProjectRow.tenant_id == context.tenant_id,
        ProjectRow.workspace_id == context.workspace_id,
    )
    await uow.session.execute(update(ProjectRow).where(*scope).values(version=ProjectRow.version))
    row = await uow.session.scalar(
        select(ProjectRow).where(*scope).with_for_update().execution_options(populate_existing=True)
    )
    if row is None:
        raise EpisodeEditError("PROJECT_NOT_FOUND", 404)

    def apply(project):
        project["title"] = row.name
        project["xingjing_setup"] = {k: v for k, v in row.configuration.items() if k != "sourceText"}

    await asyncio.to_thread(get_project_manager().update_project, row.id, apply)


async def update_overview(
    uow: PersistenceUnitOfWork, context, row: ProjectRow, edit: OverviewEdit, expected: int, key: str, digest: str
):
    from .runtime import _project

    if "budgetMinor" in edit.model_fields_set and "billing.view" not in context.permissions:
        raise EpisodeEditError("PERMISSION_DENIED", 403)
    if row.production_status == "completed" and edit.targetPlatform != row.target_platform:
        raise EpisodeEditError("INVALID_STATE_TRANSITION", 409)
    if edit.ownerId != row.owner_id:
        member = await uow.session.scalar(
            select(MemberRow.member_id).where(
                MemberRow.tenant_id == context.tenant_id,
                MemberRow.workspace_id == context.workspace_id,
                MemberRow.member_id == edit.ownerId,
                MemberRow.active.is_(True),
            )
        )
        if member is None:
            raise EpisodeEditError("PROJECT_OWNER_UNAVAILABLE", 422)
    changes = edit.model_dump(exclude_unset=True)
    before = {
        "name": row.name,
        "ownerId": row.owner_id,
        "targetPlatform": row.target_platform,
        "configuration": {k: row.configuration.get(k) for k in changes if k != "name"},
    }
    configuration = {
        **row.configuration,
        **{k: v for k, v in changes.items() if k not in {"name", "targetPlatform"}},
        "primaryPlatform": edit.targetPlatform,
    }
    configuration["secondaryPlatforms"] = [
        p for p in configuration.get("secondaryPlatforms", []) if p != edit.targetPlatform
    ]
    result = _project(
        await uow.projects.configure(
            row.id,
            expected_version=expected,
            name=edit.name,
            project_type=row.project_type,
            target_platform=edit.targetPlatform,
            owner_id=edit.ownerId,
            configuration=configuration,
            setup_state=row.setup_state,
            last_setup_key=key,
            last_setup_digest=digest,
        )
    )
    result["configuration"] = {k: v for k, v in configuration.items() if k != "sourceText"}
    details = {
        "requestId": context.request_id,
        "before": before,
        "after": changes,
        "version": result["version"],
        "idempotencyKey": key,
        "commandDigest": digest,
        "result": result,
    }
    await uow.audit.append(context.actor_id, "project.updateOverview", "project", row.id, details)
    await uow.outbox.add(
        "project.updated", "project", row.id, {"requestId": context.request_id, "version": result["version"]}
    )
    await uow.commit()
    await sync_overview_projection(uow, context, row.id)
    return visible_result(result, context.permissions)


def visible_result(result: dict[str, Any], permissions: frozenset[str]):
    configuration = {k: v for k, v in result.get("configuration", {}).items() if k != "sourceText"}
    if "billing.view" not in permissions:
        configuration.pop("budgetMinor", None)
    return {**result, "configuration": configuration}


def transition_options(row: ProjectRow, permissions: frozenset[str], facts: dict[str, Any]):
    result = []
    next_step = FORWARD.get(row.production_status)
    if row.production_status == "compliance_check" and facts.get("riskBlocked"):
        next_step = ("blocked", "riskBlocked", {"compliance.view"})
    if row.production_status == "asset_ready" and row.configuration.get("generationMode") == "reference_video":
        next_step = ("production", "assetsReady", {"script.view", "asset.view", "final.view"})
    if next_step:
        target, prerequisite, required = next_step
        reason = prerequisite if not facts.get(prerequisite) else None
        if not ({"project.manage"} | required).issubset(permissions):
            reason = "permission"
        if row.setup_state != "ready":
            reason = "setup"
        if row.deleted_at:
            reason = "archived"
        result.append({"action": "advance", "target": target, "reason": reason})
    if row.production_status in BACKWARD:
        reason = "permission" if "project.manage" not in permissions else None
        if row.deleted_at or row.setup_state != "ready":
            reason = "archived" if row.deleted_at else "setup"
        result.append({"action": "rollback", "target": BACKWARD[row.production_status], "reason": reason})
    return result


async def transition(
    runtime: ProjectsRuntime, context, project_id: str, action: str, payload: dict[str, Any], expected: int, key: str
) -> dict[str, Any]:
    from .runtime import _project

    edit = None
    reason = payload.get("reason", "")
    if action == "updateOverview":
        try:
            edit = OverviewEdit.model_validate(payload)
        except ValidationError as error:
            raise EpisodeEditError("PROJECT_OVERVIEW_INVALID", 422) from error
    elif (
        not isinstance(reason, str)
        or not 3 <= len(reason.strip()) <= 500
        or set(payload) - {"reason", "productionStatus"}
    ):
        raise EpisodeEditError("PROJECT_TRANSITION_REASON_REQUIRED", 422)
    reason = str(reason).strip()
    digest = hashlib.sha256(
        json.dumps(
            {"actor": context.actor_id, "action": action, "payload": payload, "version": expected}, sort_keys=True
        ).encode()
    ).hexdigest()
    async with PersistenceUnitOfWork(
        runtime._require_sessions(), Scope(context.tenant_id, context.workspace_id)
    ) as uow:
        row = await uow.session.scalar(
            select(ProjectRow)
            .where(
                ProjectRow.id == project_id,
                ProjectRow.tenant_id == context.tenant_id,
                ProjectRow.workspace_id == context.workspace_id,
            )
            .with_for_update()
        )
        if row is None:
            raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
        # Start a real SQLite write transaction before reading the replay receipt.
        await uow.session.execute(
            update(ProjectRow)
            .where(
                ProjectRow.id == row.id,
                ProjectRow.tenant_id == context.tenant_id,
                ProjectRow.workspace_id == context.workspace_id,
            )
            .values(version=ProjectRow.version)
        )
        receipt = await uow.session.scalar(
            select(AuditRow).where(
                AuditRow.tenant_id == context.tenant_id,
                AuditRow.workspace_id == context.workspace_id,
                AuditRow.resource_type == "project",
                AuditRow.resource_id == project_id,
                AuditRow.action.in_(("project.advance", "project.rollback", "project.updateOverview")),
                AuditRow.details["idempotencyKey"].as_string() == key,
            )
        )
        if receipt:
            if receipt.details.get("commandDigest") != digest:
                raise EpisodeEditError("IDEMPOTENCY_CONFLICT", 409)
            result = receipt.details.get("result")
            if not isinstance(result, dict):
                raise EpisodeEditError("PROJECT_RUNTIME_UNAVAILABLE", 503)
            if edit:
                await sync_overview_projection(uow, context, project_id)
            return visible_result(result, context.permissions)
        if row.version != expected:
            raise EpisodeEditError("VERSION_CONFLICT", 409)
        if row.deleted_at or row.setup_state != "ready":
            raise EpisodeEditError("INVALID_STATE_TRANSITION", 409)
        if edit:
            return await update_overview(uow, context, row, edit, expected, key, digest)
        snapshot = await asyncio.to_thread(read_episodes, get_project_manager(), project_id)
        evidence = await production_facts(uow.session, context, project_id, snapshot, row.target_platform)
        option = next(
            (o for o in transition_options(row, context.permissions, evidence["facts"]) if o["action"] == action), None
        )
        if not option or payload.get("productionStatus", option["target"]) != option["target"]:
            raise EpisodeEditError("INVALID_STATE_TRANSITION", 409)
        if option["reason"]:
            raise EpisodeEditError(
                "PERMISSION_DENIED" if option["reason"] == "permission" else "PROJECT_STAGE_PREREQUISITE",
                403 if option["reason"] == "permission" else 409,
            )
        before = row.production_status
        result = _project(
            await uow.projects.set_production_status(project_id, option["target"], expected_version=expected)
        )
        result["configuration"] = {k: v for k, v in row.configuration.items() if k != "sourceText"}
        details = {
            "requestId": context.request_id,
            "from": before,
            "to": option["target"],
            "reason": reason,
            "beforeVersion": expected,
            "version": result["version"],
            "episodeRevision": snapshot["revision"],
            "idempotencyKey": key,
            "commandDigest": digest,
            "result": result,
        }
        await uow.audit.append(context.actor_id, f"project.{action}", "project", project_id, details)
        await uow.outbox.add(
            f"project.{action}",
            "project",
            project_id,
            {k: details[k] for k in ("requestId", "from", "to", "reason", "version", "episodeRevision")},
        )
        await uow.commit()
        return visible_result(result, context.permissions)
