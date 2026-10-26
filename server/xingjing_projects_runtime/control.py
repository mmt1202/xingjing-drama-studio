"""Read-time project overview assembled from scoped production records."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from lib.episode_editor import EpisodeEditError, read_episodes
from lib.project_manager import get_project_manager
from server.xingjing_content.models import (
    director_confirmation_from_dict,
    director_profile_digest,
    director_version_from_dict,
)
from server.xingjing_content_persistence.repository import (
    DirectorConfirmationRow,
    DirectorVersionRow,
    ScriptRow,
    ScriptVersionRow,
)
from server.xingjing_editing_persistence.models import TimelineHeadRow
from server.xingjing_generation_persistence.repository import GenerationBillingHoldRow, TaskRow
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import AuditRow, ProjectRow
from server.xingjing_review_persistence.models import ReviewDeliveryRow, ReviewLinkRow

from .episode_production import production_catalog, project_production
from .episode_sources import enrich_working_scripts, source_context, sources

if TYPE_CHECKING:
    from .runtime import ProjectsRuntime


STAGES = (
    ("control", "control", "project.view", None, None),
    ("script", "script-import", "script.view", None, None),
    ("checkup", "script-checkup", "script.view", "source", "script-import"),
    ("director", "ai-director", "script.view", "source", "script-import"),
    ("assets", "assets", "asset.view", "frozen", "script-lab"),
    ("shots", "shots", "shot.view", "assets", "episodes"),
    ("storyboard", "storyboard", "shot.view", "shots", "shots"),
    ("generation", "generation", "generation.view", None, None),
    ("audio", "audio", "audio.view", None, None),
    ("editing", "postproduction", "final.view", None, None),
    ("review", "collaboration/CR-085", "review.view", "finals", "postproduction"),
    ("export", "release-package", "export.view", "finals", "postproduction"),
    ("compliance", "compliance", "compliance.view", "finals", "postproduction"),
    ("versions", "version-history", "project.view", None, None),
)


def scoped(model: Any, context: TrustedWorkspaceContext, project_id: str):
    return (
        model.tenant_id == context.tenant_id,
        model.workspace_id == context.workspace_id,
        model.project_id == project_id,
    )


async def _confirmed_director_scripts(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    scripts: list[ScriptRow],
    versions: dict[str, ScriptVersionRow],
) -> set[str]:
    """Only explicit confirmations of the latest candidate count as stage evidence."""
    if not scripts or "script.view" not in context.permissions:
        return set()
    candidates = await session.scalars(
        select(DirectorVersionRow)
        .join(
            ScriptRow,
            and_(
                ScriptRow.workspace_id == DirectorVersionRow.workspace_id,
                ScriptRow.script_id == DirectorVersionRow.script_id,
            ),
        )
        .where(ScriptRow.workspace_id == context.workspace_id, ScriptRow.project_id == project_id)
        .order_by(DirectorVersionRow.number.desc())
    )
    latest: dict[str, DirectorVersionRow] = {}
    for candidate in candidates:
        latest.setdefault(candidate.script_id, candidate)
    confirmations = await session.scalars(
        select(DirectorConfirmationRow)
        .join(
            ScriptRow,
            and_(
                ScriptRow.workspace_id == DirectorConfirmationRow.workspace_id,
                ScriptRow.script_id == DirectorConfirmationRow.script_id,
            ),
        )
        .where(ScriptRow.workspace_id == context.workspace_id, ScriptRow.project_id == project_id)
    )
    confirmed: set[str] = set()
    scoped_scripts = {script.script_id: script for script in scripts}
    for row in confirmations:
        script = scoped_scripts.get(row.script_id)
        version = versions.get(row.script_id)
        candidate_row = latest.get(row.script_id)
        if not script or not version or not candidate_row or script.locked_version_number != version.number:
            continue
        try:
            candidate = director_version_from_dict(candidate_row.payload)
            confirmation = director_confirmation_from_dict(row.payload)
        except (KeyError, TypeError, ValueError):
            continue
        version_id = f"{script.script_id}@{version.number}"
        digest = hashlib.sha256(version.source_content.encode()).hexdigest()
        fields = (
            candidate.profile.audience,
            candidate.profile.pacing,
            candidate.profile.visual_style,
            candidate.profile.camera_language,
        )
        if (
            candidate.id == candidate_row.candidate_id == confirmation.candidate_id
            and candidate.number == candidate_row.number == row.candidate_number == confirmation.candidate_number
            and confirmation.id == row.confirmation_id
            and candidate.script_version_id == confirmation.script_version_id == version_id
            and candidate.script_content_digest == confirmation.script_content_digest == digest
            and confirmation.profile_digest == director_profile_digest(candidate.profile)
            and all(isinstance(value, str) and value.strip() for value in fields)
        ):
            confirmed.add(script.script_id)
    return confirmed


async def production_facts(
    session: AsyncSession,
    context: TrustedWorkspaceContext,
    project_id: str,
    snapshot: dict[str, Any],
    target_platform: str = "",
) -> dict[str, Any]:
    snapshot = await enrich_working_scripts(session, context, project_id, snapshot)
    catalog = await production_catalog(session, context, project_id)
    production = project_production(snapshot, catalog)
    scripts = []
    if "script.view" in context.permissions:
        scripts = list(
            (
                await session.scalars(
                    select(ScriptRow).where(
                        ScriptRow.workspace_id == context.workspace_id,
                        ScriptRow.project_id == project_id,
                    )
                )
            ).all()
        )
    versions = []
    if scripts:
        versions = list(
            (
                await session.scalars(
                    select(ScriptVersionRow)
                    .join(
                        ScriptRow,
                        and_(
                            ScriptRow.workspace_id == ScriptVersionRow.workspace_id,
                            ScriptRow.script_id == ScriptVersionRow.script_id,
                        ),
                    )
                    .where(ScriptRow.workspace_id == context.workspace_id, ScriptRow.project_id == project_id)
                    .order_by(ScriptVersionRow.number.desc())
                )
            ).all()
        )
    latest: dict[str, ScriptVersionRow] = {}
    for version in versions:
        latest.setdefault(version.script_id, version)
    confirmed_directors = await _confirmed_director_scripts(session, context, project_id, scripts, latest)
    timelines = []
    if "final.view" in context.permissions:
        timelines = list(
            (
                await session.scalars(
                    select(TimelineHeadRow)
                    .where(
                        *scoped(TimelineHeadRow, context, project_id),
                    )
                    .order_by(TimelineHeadRow.timeline_id)
                )
            ).all()
        )
    frozen_boards = {b["episodeId"] for b in catalog["boards"] if b.get("frozen")}
    approvals: dict[str, str] = {}
    if "review.view" in context.permissions:
        rows = (
            await session.execute(
                select(ReviewDeliveryRow, ReviewLinkRow.created_at)
                .join(
                    ReviewLinkRow,
                    and_(
                        ReviewLinkRow.tenant_id == ReviewDeliveryRow.tenant_id,
                        ReviewLinkRow.workspace_id == ReviewDeliveryRow.workspace_id,
                        ReviewLinkRow.id == ReviewDeliveryRow.review_link_id,
                        ReviewLinkRow.final_video_version_id == ReviewDeliveryRow.final_video_version_id,
                    ),
                )
                .where(*scoped(ReviewLinkRow, context, project_id), ReviewLinkRow.state == "active")
                .order_by(ReviewLinkRow.created_at.desc(), ReviewLinkRow.id.desc())
            )
        ).all()
        for delivery, _ in rows:
            approvals.setdefault(delivery.final_video_version_id, delivery.status)
    items = []
    can_check_bindings = {"script.view", "asset.view", "final.view"}.issubset(context.permissions)
    for episode, produced in zip(snapshot["items"], production["items"], strict=True):
        binding = episode["production"]
        final = next(
            (
                f
                for f in catalog["finals"]
                if f["id"] == binding.get("finalVideoVersionId") and f["episodeId"] == str(episode["episode"])
            ),
            None,
        )
        timeline = next((t for t in timelines if t.episode_id == str(episode["episode"])), None)
        items.append(
            {
                "episode": episode["episode"],
                "title": episode["title"],
                "scriptId": (episode.get("workingScript") or {}).get("scriptId")
                or (
                    str(binding.get("scriptVersionId", "")).rsplit("@", 1)[0]
                    if "script.view" in context.permissions
                    else None
                ),
                "scriptVersionId": binding.get("scriptVersionId") if "script.view" in context.permissions else None,
                "assetSnapshotId": binding.get("assetSnapshotId") if "asset.view" in context.permissions else None,
                "state": produced["state"] if can_check_bindings else "restricted",
                "issue": produced["issue"] if can_check_bindings else None,
                "shots": sum(b["shots"] for b in produced["boards"]) if "shot.view" in context.permissions else None,
                "finalVersionId": final["id"] if final else None,
                "timelineId": final["timelineId"] if final else timeline.timeline_id if timeline else None,
                "approval": approvals.get(final["id"]) if final else None,
                "assetsReady": bool(binding.get("assetSnapshotId")) and can_check_bindings and not produced["issue"],
            }
        )
    count = len(items)
    all_planned = bool(count) and count >= snapshot["plannedCount"]
    all_finals = all_planned and all(item["finalVersionId"] for item in items)
    asset_ready = (
        all_planned
        and can_check_bindings
        and all(
            e["production"].get("assetSnapshotId") and not p["issue"]
            for e, p in zip(snapshot["items"], production["items"], strict=True)
        )
    )
    # Imported novels and unused candidate drafts are not episode production inputs.
    working_ids = [
        (e.get("workingScript") or {}).get("scriptId")
        or str(e["production"].get("scriptVersionId", "")).rsplit("@", 1)[0]
        for e in snapshot["items"]
    ]
    working_scripts = {s.script_id: s for s in scripts}
    all_working = (
        all_planned
        and all(working_ids)
        and all(identity in working_scripts for identity in working_ids)
        and all((e.get("workingScript") or {}).get("rangeMatches", True) for e in snapshot["items"])
    )
    facts = {
        "source": bool(scripts),
        "analyzed": all_working
        and all(
            identity in latest and bool(latest[identity].analysis) and not latest[identity].validation_errors
            for identity in working_ids
        ),
        "director": all_working and all(identity in confirmed_directors for identity in working_ids),
        "frozen": bool(catalog["scripts"]),
        "assets": any(s["valid"] and s["assets"] for s in catalog["snapshots"]),
        "assetsReady": asset_ready,
        "shots": any(b["shots"] for b in catalog["boards"]),
        "boardsReady": all_planned
        and all(
            str(e["episode"]) in frozen_boards and not p["issue"]
            for e, p in zip(items, production["items"], strict=True)
        ),
        "finals": bool(catalog["finals"]),
        "finalsReady": all_finals,
        "approved": all_finals and all(i["approval"] in {"approved", "confirmed"} for i in items),
        "complianceClear": all_finals
        and all(
            p["review"]
            and p["review"]["conclusion"] == "approved"
            and p["review"]["status"] in {"not_required", "approved"}
            for p in production["items"]
        ),
        "riskBlocked": any(p["state"] == "review_blocked" for p in production["items"]),
        "packagesReady": all_finals
        and all(
            p["state"] == "package_ready"
            and any(
                d["target"] == target_platform and d["status"] == "succeeded" and d["format"] == "publish_package"
                for d in p["deliveries"]
            )
            for p in production["items"]
        ),
    }
    facts["packagesReady"] = facts["packagesReady"] and facts["complianceClear"] and facts["approved"]
    return {"catalog": catalog, "items": items, "facts": facts}


def stage_links(row: ProjectRow, permissions: frozenset[str], facts: dict[str, Any]):
    result = []
    for identity, target, permission, prerequisite, recovery in STAGES:
        reason = None
        if permission not in permissions:
            reason = "permission"
        elif identity != "control" and row.deleted_at:
            reason = "archived"
        elif identity != "control" and row.setup_state != "ready":
            reason = "setup"
        elif prerequisite and not facts.get(prerequisite):
            reason = prerequisite
        result.append(
            {
                "id": identity,
                "target": target,
                "reason": reason,
                "recovery": "guided-create" if reason == "setup" else recovery if reason == prerequisite else None,
            }
        )
    return result


async def overview(runtime: ProjectsRuntime, context: TrustedWorkspaceContext, row: ProjectRow, *, days: int):
    from .control_actions import transition_options
    from .runtime import _iso, _summary

    snapshot = {"items": [], "revision": "", "plannedCount": 0}
    since = datetime.now(UTC) - timedelta(days=days) if days else None
    async with runtime._require_sessions()() as session:
        if row.setup_state == "ready":
            collection = await sources(session, context, row.id)
            snapshot = await asyncio.to_thread(
                read_episodes, get_project_manager(), row.id, source_texts=collection.texts
            )
        evidence = await production_facts(session, context, row.id, snapshot, row.target_platform)
        tasks = None
        task_scope = (TaskRow.workspace_id == context.workspace_id, TaskRow.project_id == row.id)
        if "generation.view" in context.permissions:
            clauses = (*task_scope, *((TaskRow.updated_at >= since,) if since else ()))
            groups = (
                await session.execute(select(TaskRow.status, func.count()).where(*clauses).group_by(TaskRow.status))
            ).all()
            recent = (
                await session.scalars(
                    select(TaskRow).where(*clauses).order_by(TaskRow.updated_at.desc(), TaskRow.task_id.desc()).limit(8)
                )
            ).all()
            tasks = {
                "counts": {state: count for state, count in groups},
                "items": [
                    {
                        "id": t.task_id,
                        "status": t.status,
                        "updatedAt": _iso(t.updated_at),
                        "mediaType": t.snapshot.get("request", {}).get("media_type", ""),
                    }
                    for t in recent
                ],
            }
        costs = None
        if "billing.view" in context.permissions:
            holds = (
                await session.execute(
                    select(
                        GenerationBillingHoldRow.currency,
                        GenerationBillingHoldRow.status,
                        func.sum(GenerationBillingHoldRow.actual_minor),
                        func.sum(
                            GenerationBillingHoldRow.estimated_minor
                            - GenerationBillingHoldRow.actual_minor
                            - GenerationBillingHoldRow.released_minor
                        ),
                    )
                    .where(
                        GenerationBillingHoldRow.workspace_id == context.workspace_id,
                        GenerationBillingHoldRow.project_id == row.id,
                        *((GenerationBillingHoldRow.updated_at >= since,) if since else ()),
                    )
                    .group_by(GenerationBillingHoldRow.currency, GenerationBillingHoldRow.status)
                )
            ).all()
            totals: dict[str, dict[str, Any]] = {}
            for currency, hold_status, actual, estimated in holds:
                total = totals.setdefault(currency, {"currency": currency, "spentMinor": 0, "heldMinor": 0})
                total["spentMinor"] += int(actual or 0)
                if hold_status == "active":
                    total["heldMinor"] += int(estimated or 0)
            costs = list(totals.values())
        logs = (
            await session.scalars(
                select(AuditRow)
                .where(
                    AuditRow.tenant_id == context.tenant_id,
                    AuditRow.workspace_id == context.workspace_id,
                    AuditRow.resource_type == "project",
                    AuditRow.resource_id == row.id,
                    AuditRow.action.in_(("project.advance", "project.rollback")),
                    *((AuditRow.created_at >= since,) if since else ()),
                )
                .order_by(AuditRow.created_at.desc(), AuditRow.id.desc())
                .limit(20)
            )
        ).all()
    project = _summary(row)
    configuration = row.configuration or {}
    project["configuration"] = {
        key: configuration.get(key)
        for key in (
            "generationMode",
            "episodeCount",
            "scheduleDays",
            "teamSeats",
            "reviewSeats",
        )
    }
    if "billing.view" in context.permissions:
        project["configuration"]["budgetMinor"] = configuration.get("budgetMinor")
    history = [
        {
            "id": log.id,
            "actorId": log.actor_id,
            "action": log.action,
            "from": log.details.get("from"),
            "to": log.details.get("to"),
            "reason": log.details.get("reason"),
            "createdAt": _iso(log.created_at),
            "requestId": log.details.get("requestId"),
        }
        for log in logs
    ]
    return {
        "project": project,
        "workspaceId": context.workspace_id,
        "permissions": sorted(context.permissions),
        "episodes": evidence["items"],
        "plannedCount": snapshot["plannedCount"],
        "stages": stage_links(row, context.permissions, evidence["facts"]),
        "tasks": tasks,
        "costs": costs,
        "transitions": transition_options(row, context.permissions, evidence["facts"]),
        "history": history,
        "updatedAt": datetime.now(UTC).isoformat(),
        "days": days,
    }


def register_control_routes(runtime: ProjectsRuntime, router: APIRouter):
    @router.get("/projects/{project_id}/control")
    async def control(
        request: Request,
        project_id: str,
        days: int = Query(default=0, ge=0, le=30),
        q: str = Query(default="", max_length=255),
        episodeState: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=10, ge=1, le=50),
    ):
        context = await source_context(runtime, request, project_id)
        row = await runtime._row(context.tenant_id, context.workspace_id, project_id, include_deleted=True)
        if row is None:
            raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
        if days not in {0, 7, 30}:
            raise EpisodeEditError("PROJECT_RANGE_INVALID", 422)
        data = await overview(runtime, context, row, days=days)
        matches = [
            e
            for e in data["episodes"]
            if (not q or q.casefold() in f"{e['episode']} {e['title']}".casefold())
            and (not episodeState or e["state"] == episodeState)
        ]
        data["episodePage"] = {
            "items": matches[offset : offset + limit],
            "total": len(matches),
            "offset": offset,
            "limit": limit,
        }
        return {"data": data, "meta": {"requestId": context.request_id}}
