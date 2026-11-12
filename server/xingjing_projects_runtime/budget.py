"""Budget policy writes share project commands' transaction and audit boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.episode_editor import EpisodeEditError
from server.xingjing_budget import BudgetAdmissionService, BudgetError, BudgetPolicyRow, LockedBudgetScope
from server.xingjing_platform_persistence.persistence import ProjectRow

from .episode_sources import authorize

if TYPE_CHECKING:
    from .runtime import ProjectsRuntime


async def apply_project_budget(
    session: AsyncSession, context: Any, project_id: str, *, limit_minor: int | None, previous_limit: object
) -> None:
    if type(previous_limit) in (int, type(None)) and limit_minor == previous_limit:
        return
    if not {"billing.view", "billing.budget.manage"} <= context.permissions:
        raise BudgetError("BUDGET_MANAGEMENT_FORBIDDEN")
    service = BudgetAdmissionService()
    scope = await service.lock_scope(
        session, tenant_id=context.tenant_id, workspace_id=context.workspace_id, project_id=project_id
    )
    await service.set_project_limit(session, scope, limit_minor=limit_minor, actor_id=context.actor_id)


async def enrich_episode_budgets(session: AsyncSession, context: Any, row: ProjectRow, data: dict) -> dict:
    scope = LockedBudgetScope(context.tenant_id, context.workspace_id, row.id, row)
    snapshot = await BudgetAdmissionService().snapshot(session, scope)
    policies = {item["episodeId"]: item for item in snapshot.episodes}
    can_read = "billing.view" in context.permissions
    for episode in data["items"]:
        policy = policies.get(episode["episode"], {})
        episode["budgetConfirmed"] = bool(policy.get("confirmed"))
        if policy.get("confirmed"):
            episode["budgetMinor"] = policy.get("limitMinor")
        if not can_read:
            episode["budgetMinor"] = None
            episode["budgetConfirmed"] = False
    data.setdefault("permissions", {})["canManageBudget"] = can_read and "billing.budget.manage" in context.permissions
    if can_read:
        data["budgetPolicy"] = {
            "currency": snapshot.currency,
            "episodePolicyConfirmed": snapshot.episode_policy_confirmed,
            "hasUnattributedCosts": any(item.committed_minor for item in snapshot.unattributed_usage),
        }
    return data


async def apply_episode_budgets(
    session: AsyncSession,
    context: Any,
    row: ProjectRow,
    edits: list,
    *,
    activate: bool,
    recovering: bool = False,
    required_revision: str | None = None,
) -> str | None:
    service = BudgetAdmissionService()
    scope = LockedBudgetScope(context.tenant_id, context.workspace_id, row.id, row)
    current = await service.snapshot(session, scope)
    previous = {item["episodeId"]: item for item in current.episodes}
    limits: dict[int, int | None] = {}
    changed = False
    for edit in edits:
        old = previous.get(edit.episode, {})
        old_limit = old.get("limitMinor") if old.get("confirmed") else old.get("plannedLimitMinor")
        requested = edit.budgetMinor if "budgetMinor" in edit.model_fields_set else old_limit
        if requested is not None and (type(requested) is not int or requested < 0):
            raise BudgetError("BUDGET_POLICY_INVALID")
        limits[edit.episode] = requested
        changed |= requested != old_limit
    if not changed and not activate:
        return None
    if not {"billing.view", "billing.budget.manage"} <= context.permissions:
        raise BudgetError("BUDGET_MANAGEMENT_FORBIDDEN")
    policies = (
        await session.execute(
            select(
                BudgetPolicyRow.scope_key,
                BudgetPolicyRow.currency,
                BudgetPolicyRow.limit_minor,
                BudgetPolicyRow.active,
                BudgetPolicyRow.version,
            )
            .where(
                BudgetPolicyRow.tenant_id == context.tenant_id,
                BudgetPolicyRow.workspace_id == context.workspace_id,
                BudgetPolicyRow.project_id == row.id,
                BudgetPolicyRow.confirmed.is_(True),
            )
            .order_by(BudgetPolicyRow.scope_key)
        )
    ).all()
    revision = hashlib.sha256(
        json.dumps([list(value) for value in policies], separators=(",", ":")).encode()
    ).hexdigest()
    if recovering and required_revision != revision:
        raise BudgetError("BUDGET_POLICY_VERSION_CONFLICT")
    await service.set_episode_limits(
        session,
        scope,
        limits=limits,
        titles={edit.episode: edit.title for edit in edits},
        actor_id=context.actor_id,
    )
    return revision


def register_budget_routes(runtime: ProjectsRuntime, router: APIRouter) -> None:
    async def read(request: Request, project_id: str, *, costs: bool) -> dict[str, Any]:
        context = await runtime._context(request, "project.view")
        async with runtime._require_sessions()() as session:
            context = await authorize(session, context, project_id)
            if costs and "billing.view" not in context.permissions:
                raise EpisodeEditError("PERMISSION_DENIED", 403)
            row = await session.scalar(
                select(ProjectRow).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
            if row is None:
                raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
            if row.setup_state != "ready":
                raise EpisodeEditError("PROJECT_SETUP_REQUIRED", 409)
            scope = LockedBudgetScope(context.tenant_id, context.workspace_id, project_id, row)
            snapshot = await BudgetAdmissionService().snapshot(session, scope)
            if not costs:
                data = {
                    "episodes": [
                        {"episodeId": episode["episodeId"], "title": episode.get("title") or str(episode["episodeId"])}
                        for episode in snapshot.episodes
                    ],
                    "requiresAttribution": snapshot.requires_attribution,
                    "canUseSharedScope": bool({"project.manage", "billing.budget.manage"} & context.permissions),
                }
            else:
                data = {
                    "currency": snapshot.currency,
                    "projectLimitMinor": snapshot.project_limit_minor,
                    "usage": [asdict(item) for item in snapshot.usage],
                    "unattributedUsage": [asdict(item) for item in snapshot.unattributed_usage],
                    "episodes": snapshot.episodes,
                    "requiresAttribution": snapshot.requires_attribution,
                    "episodePolicyConfirmed": snapshot.episode_policy_confirmed,
                    "canManageBudget": "billing.budget.manage" in context.permissions,
                }
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/budget/options")
    async def options(request: Request, project_id: str):
        return await read(request, project_id, costs=False)

    @router.get("/projects/{project_id}/budget")
    async def usage(request: Request, project_id: str):
        return await read(request, project_id, costs=True)
