"""Normalize recorded production facts without inferring missing ownership."""

from __future__ import annotations

import json
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from server.xingjing_audio_persistence.models import audio_billing_holds, audio_billing_journals
from server.xingjing_audio_persistence.models import generation_tasks as audio_tasks
from server.xingjing_compliance_persistence.repository import DeliveryRow
from server.xingjing_editing_persistence.models import (
    EditingAuditRow,
    EditingRenderBillingHoldRow,
    EditingRenderBillingJournalRow,
    FinalVideoVersionRow,
    RenderTaskRow,
    TimelineVersionRow,
)
from server.xingjing_generation_persistence.repository import AuditRow as GenerationAuditRow
from server.xingjing_generation_persistence.repository import (
    GenerationBillingHoldRow,
    GenerationBillingJournalRow,
    TaskRow,
)
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_team_persistence.repository import MemberRow

from .contracts import SCAN_LIMIT, UNKNOWN, AnalyticsFailure, Dataset, Fact, timestamp, utc


def scope(model: Any, context: TrustedWorkspaceContext, projects: list[str]):
    return (
        model.tenant_id == context.tenant_id,
        model.workspace_id == context.workspace_id,
        model.project_id.in_(projects),
    )


async def limited(session: AsyncSession, statement: Any, *, mappings: bool = False) -> list[Any]:
    result = await session.execute(statement.limit(SCAN_LIMIT + 1))
    rows = result.mappings().all() if mappings else result.scalars().all()
    if len(rows) > SCAN_LIMIT:
        raise AnalyticsFailure("ANALYTICS_SCAN_LIMIT", 422)
    return list(rows)


async def visible_projects(
    session: AsyncSession, context: TrustedWorkspaceContext
) -> tuple[list[ProjectRow], dict[str, list[str]], dict[str, list[str]]]:
    query = select(ProjectRow).where(
        ProjectRow.tenant_id == context.tenant_id,
        ProjectRow.workspace_id == context.workspace_id,
        ProjectRow.deleted_at.is_(None),
    )
    project_permissions: dict[str, list[str]] = {}
    assignment_scopes: dict[str, list[str]] = {}
    if context.role.casefold() not in {"owner", "admin", "platform_admin"}:
        assignments = await session.execute(
            text(
                "SELECT project_id, permissions, data_scope FROM xingjing_project_members WHERE tenant_id=:tenant AND workspace_id=:workspace AND member_id=:actor AND active IS TRUE LIMIT :assignment_limit"
            ),
            {
                "tenant": context.tenant_id,
                "workspace": context.workspace_id,
                "actor": context.actor_id,
                "assignment_limit": SCAN_LIMIT + 1,
            },
        )
        assignment_rows = assignments.all()
        if len(assignment_rows) > SCAN_LIMIT:
            raise AnalyticsFailure("ANALYTICS_SCAN_LIMIT", 422)
        allowed: list[str] = []
        for project_id, raw, raw_scope in assignment_rows:
            try:
                permissions = json.loads(raw) if isinstance(raw, str) else raw
            except ValueError as error:
                raise AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503) from error
            if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
                raise AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503)
            project_permissions[str(project_id)] = sorted(set(permissions) & context.permissions)
            try:
                scopes = json.loads(raw_scope) if isinstance(raw_scope, str) else raw_scope
            except ValueError:
                scopes = None
            normalized = (
                sorted(set(scopes))
                if isinstance(scopes, list) and all(isinstance(value, str) for value in scopes)
                else ["<invalid>"]
            )
            assignment_scopes[str(project_id)] = normalized
            full_scope = bool(normalized) and all(value == f"project:{project_id}" for value in normalized)
            if "project.view" in permissions and full_scope:
                allowed.append(str(project_id))
        query = query.where(ProjectRow.id.in_(allowed))
    rows = list(await limited(session, query.order_by(ProjectRow.id)))
    if context.role.casefold() in {"owner", "admin", "platform_admin"}:
        project_permissions = {p.id: sorted(context.permissions) for p in rows}
        assignment_scopes = {p.id: ["*"] for p in rows}
    return rows, project_permissions, assignment_scopes


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else UNKNOWN


def _model(provider: object, model: object) -> str:
    provider_id, model_id = _str(provider), _str(model)
    return f"{provider_id}/{model_id}" if provider_id != UNKNOWN and model_id != UNKNOWN else UNKNOWN


def _duration(created, completed) -> int | None:
    start, end = timestamp(created), timestamp(completed)
    return round((end - start).total_seconds() * 1000) if start and end and end >= start else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def load(session: AsyncSession, context: TrustedWorkspaceContext, project_id: str = "") -> Dataset:
    projects, project_permissions, assignment_scopes = await visible_projects(session, context)
    visible_ids = [row.id for row in projects]
    if project_id and project_id not in visible_ids:
        raise AnalyticsFailure("ANALYTICS_PROJECT_NOT_FOUND", 404)
    ids = [project_id] if project_id else visible_ids
    data = Dataset(
        [],
        {p.id: {"label": p.name, "state": p.production_status} for p in projects},
        {},
        {},
        {},
        visible_ids,
        set(),
        [],
        project_permissions,
        assignment_scopes,
    )
    if set(assignment_scopes) - set(visible_ids):
        data.notes.add("narrow_scope_excluded")

    def source_ids(permission: str) -> list[str]:
        return [pid for pid in ids if permission in project_permissions.get(pid, [])]

    if any(
        len(source_ids(permission)) < len(ids)
        for permission in ("generation.view", "audio.view", "final.view", "export.view", "billing.view")
    ):
        data.notes.add("partial_sources_restricted")

    data.sources.append("projects")
    for p in projects:
        if p.id in ids:
            data.facts.append(Fact("project", p.id, utc(p.created_at)))
    if not ids:
        return data
    tasks: dict[tuple[str, str, str], Fact] = {}
    generation_ids = source_ids("generation.view")
    if generation_ids:
        data.sources.append("generation")
        rows = await limited(
            session,
            select(TaskRow).where(TaskRow.workspace_id == context.workspace_id, TaskRow.project_id.in_(generation_ids)),
        )
        audits = await limited(
            session,
            select(GenerationAuditRow)
            .where(
                GenerationAuditRow.workspace_id == context.workspace_id,
                GenerationAuditRow.project_id.in_(generation_ids),
            )
            .order_by(GenerationAuditRow.occurred_at, GenerationAuditRow.audit_id),
        )
        actors: dict[tuple[str, str], str] = {}
        for audit in audits:
            actor = audit.payload.get("actor_id")
            if (
                audit.payload.get("action") == "generation.provider_submitted"
                and isinstance(actor, str)
                and not actor.startswith("provider:")
                and not actor.endswith("-sweeper")
            ):
                actors.setdefault((audit.project_id, audit.task_id), actor)
        for row in rows:
            snapshot = row.snapshot
            request = _mapping(snapshot.get("request"))
            params = _mapping(request.get("parameters"))
            model = _model(
                snapshot.get("resolved_provider_id") or request.get("requested_provider_id"),
                snapshot.get("resolved_model_id") or request.get("requested_model_id"),
            )
            fact = Fact(
                "task",
                row.project_id,
                utc(row.created_at),
                _str(params.get("episode_id") or params.get("episodeId")),
                model,
                actors.get((row.project_id, row.task_id), UNKNOWN),
                row.status,
                bool(params.get("retry_of_task_id"))
                or (type(snapshot.get("attempt")) is int and snapshot["attempt"] > 1),
                _duration(row.created_at, snapshot.get("completed_at")) if row.status == "succeeded" else None,
            )
            tasks[("generation", row.project_id, row.task_id)] = fact
            data.models.setdefault(model, model)
    else:
        data.notes.add("generation_restricted")
    audio_ids = source_ids("audio.view")
    if audio_ids:
        audio_rows = await limited(
            session,
            select(audio_tasks).where(
                audio_tasks.c.tenant_id == context.tenant_id,
                audio_tasks.c.workspace_id == context.workspace_id,
                audio_tasks.c.project_id.in_(audio_ids),
            ),
            mappings=True,
        )
        for row in audio_rows:
            model = f"audio/{row['provider_name']}" if row["provider_name"] else UNKNOWN
            episode = _str(row["resource_id"]) if row["resource_type"] == "episode" else UNKNOWN
            fact = Fact(
                "task",
                str(row["project_id"]),
                utc(row["created_at"]),
                episode,
                model,
                _str(row["created_by"]),
                "timeout" if row["status"] == "timed_out" else str(row["status"]),
                int(row["attempt_count"]) > 1,
                _duration(row["created_at"], row["finished_at"]) if row["status"] == "succeeded" else None,
            )
            tasks[("audio", str(row["project_id"]), str(row["id"]))] = fact
            data.models.setdefault(model, model)
        data.sources.append("audio")
        data.notes.add("audio_provider_not_model")
    else:
        data.notes.add("audio_restricted")
    finals = []
    timelines = []
    final_ids = source_ids("final.view")
    export_ids = source_ids("export.view")
    reference_ids = sorted(set(final_ids + export_ids))
    if reference_ids:
        finals = await limited(
            session, select(FinalVideoVersionRow).where(*scope(FinalVideoVersionRow, context, reference_ids))
        )
        timelines = await limited(
            session, select(TimelineVersionRow).where(*scope(TimelineVersionRow, context, reference_ids))
        )
    timeline_map = {(t.project_id, t.timeline_id, t.version_id): t for t in timelines}
    final_map = {}
    for row in finals:
        timeline = timeline_map.get((row.project_id, row.timeline_id, row.timeline_version_id))
        episode = _str(timeline.snapshot.get("episode_id")) if timeline else UNKNOWN
        if timeline is None:
            data.notes.add("historical_source_gap")
        final_map[(row.project_id, row.version_id)] = (row, episode)
        if episode != UNKNOWN:
            data.episodes[(row.project_id, episode)] = episode
        if row.project_id not in final_ids:
            continue
        shots = set()
        if timeline:
            for track in timeline.snapshot.get("tracks", []):
                if isinstance(track, dict) and track.get("kind") == "video":
                    for clip in track.get("clips", []):
                        if isinstance(clip, dict) and isinstance(clip.get("shot_id"), str) and clip["shot_id"]:
                            shots.add(clip["shot_id"])
        data.facts.append(Fact("final", row.project_id, utc(row.created_at), episode, shots=frozenset(shots)))
    if final_ids:
        data.sources.append("finals")
        render_rows = await limited(session, select(RenderTaskRow).where(*scope(RenderTaskRow, context, final_ids)))
        render_audits = await limited(
            session,
            select(EditingAuditRow)
            .where(
                *scope(EditingAuditRow, context, final_ids),
                EditingAuditRow.object_type == "render_task",
                EditingAuditRow.action == "render.request",
                EditingAuditRow.outcome == "succeeded",
            )
            .order_by(EditingAuditRow.occurred_at, EditingAuditRow.event_id),
        )
        render_actors = {}
        for audit in render_audits:
            if (
                audit.actor_id
                and not audit.actor_id.startswith("provider:")
                and not audit.actor_id.endswith("-sweeper")
            ):
                render_actors.setdefault((audit.project_id, audit.object_id), audit.actor_id)
        for row in render_rows:
            timeline = timeline_map.get((row.project_id, row.timeline_id, row.timeline_version_id))
            episode = _str(timeline.snapshot.get("episode_id")) if timeline else UNKNOWN
            completed = final_map.get((row.project_id, row.output_version_id or ""))
            fact = Fact(
                "task",
                row.project_id,
                utc(row.created_at),
                episode,
                "editing/renderer",
                render_actors.get((row.project_id, row.task_id), UNKNOWN),
                row.status,
                row.attempt > 1,
                _duration(row.created_at, completed[0].created_at) if row.status == "succeeded" and completed else None,
            )
            tasks[("editing", row.project_id, row.task_id)] = fact
            data.models.setdefault(fact.model, fact.model)
        data.sources.append("editing")
    else:
        data.notes.add("finals_restricted")
    data.facts.extend(tasks.values())
    if export_ids:
        rows = await limited(
            session,
            select(DeliveryRow).where(
                *scope(DeliveryRow, context, export_ids),
                DeliveryRow.status == "succeeded",
                DeliveryRow.format == "publish_package",
            ),
        )
        for row in rows:
            final = final_map.get((row.project_id, row.project_version))
            if final is None:
                data.notes.add("historical_source_gap")
            data.facts.append(
                Fact("delivery", row.project_id, utc(row.created_at), UNKNOWN if final is None else final[1])
            )
        data.sources.append("deliveries")
    else:
        data.notes.add("deliveries_restricted")
    cost_ids = source_ids("billing.view")
    if cost_ids:
        data.sources.append("billing")
        generation_holds = await limited(
            session,
            select(GenerationBillingHoldRow).where(
                GenerationBillingHoldRow.workspace_id == context.workspace_id,
                GenerationBillingHoldRow.project_id.in_(cost_ids),
            ),
        )
        generation_journals = await limited(
            session,
            select(GenerationBillingJournalRow).where(
                GenerationBillingJournalRow.workspace_id == context.workspace_id,
                GenerationBillingJournalRow.project_id.in_(cost_ids),
                GenerationBillingJournalRow.action == "settle",
            ),
        )
        audio_holds = await limited(
            session,
            select(audio_billing_holds).where(
                audio_billing_holds.c.tenant_id == context.tenant_id,
                audio_billing_holds.c.workspace_id == context.workspace_id,
                audio_billing_holds.c.project_id.in_(cost_ids),
            ),
            mappings=True,
        )
        audio_journals = await limited(
            session,
            select(audio_billing_journals).where(
                audio_billing_journals.c.tenant_id == context.tenant_id,
                audio_billing_journals.c.workspace_id == context.workspace_id,
                audio_billing_journals.c.project_id.in_(cost_ids),
                audio_billing_journals.c.action == "settle",
            ),
            mappings=True,
        )
        editing_holds = await limited(
            session, select(EditingRenderBillingHoldRow).where(*scope(EditingRenderBillingHoldRow, context, cost_ids))
        )
        editing_journals = await limited(
            session,
            select(EditingRenderBillingJournalRow).where(
                *scope(EditingRenderBillingJournalRow, context, cost_ids),
                EditingRenderBillingJournalRow.action == "settle",
            ),
        )
        for source, holds, journals, mapping in [
            ("generation", generation_holds, generation_journals, False),
            ("audio", audio_holds, audio_journals, True),
            ("editing", editing_holds, editing_journals, False),
        ]:
            for kind, records in [("hold", holds), ("cost", journals)]:
                for record in records:
                    get = record.get if mapping else lambda key, default=None: getattr(record, key, default)
                    if kind == "hold" and get("status") != "active":
                        continue
                    project_id = str(get("project_id"))
                    task_id = str(get("task_id"))
                    task = tasks.get((source, project_id, task_id))
                    currency = get("currency")
                    if kind == "cost":
                        amount = get("amount_minor")
                    else:
                        amounts = [get("estimated_minor"), get("actual_minor"), get("released_minor")]
                        if not all(type(value) is int for value in amounts):
                            raise AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503)
                        integers = cast(list[int], amounts)
                        amount = integers[0] - integers[1] - integers[2]
                    at = timestamp(get("occurred_at") or get("created_at"))
                    if (
                        not isinstance(currency, str)
                        or len(currency) != 3
                        or type(amount) is not int
                        or amount < 0
                        or at is None
                    ):
                        raise AnalyticsFailure("ANALYTICS_DEPENDENCY_UNAVAILABLE", 503)
                    data.facts.append(
                        Fact(
                            kind,
                            project_id,
                            at,
                            task.episode if task else UNKNOWN,
                            task.model if task else UNKNOWN,
                            task.member if task else UNKNOWN,
                            task.status if task else "",
                            task.retry if task else False,
                            currency=currency,
                            amount=amount,
                        )
                    )
    else:
        data.notes.add("costs_restricted")
    for fact in data.facts:
        if fact.episode != UNKNOWN:
            data.episodes.setdefault((fact.project, fact.episode), fact.episode)
        if fact.member != UNKNOWN:
            data.members.setdefault(fact.member, fact.member)
    if "workspace.member.view" in context.permissions:
        members = await limited(
            session,
            select(MemberRow).where(
                MemberRow.tenant_id == context.tenant_id,
                MemberRow.workspace_id == context.workspace_id,
                MemberRow.member_id.in_(list(data.members)),
            ),
        )
        for member in members:
            data.members[member.member_id] = member.email
    if len(data.facts) > 100_000:
        raise AnalyticsFailure("ANALYTICS_SCAN_LIMIT", 422)
    return data
