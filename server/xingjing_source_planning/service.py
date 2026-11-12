"""Confirm source-based episode drafts using the existing ledger and SQL transaction."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Session

from lib.episode_editor import EpisodeEdit, EpisodeEditError, read_episodes, save_episodes
from lib.episode_ledger import SourceRange
from lib.i18n import _ as translate
from lib.project_manager import ProjectManager
from lib.source_structure import SourceStructureError, chapters, split_ranges, uncovered_ranges
from server.xingjing_content.models import ScriptDocument, SourceDocument
from server.xingjing_content.parser import parse_script
from server.xingjing_content_persistence.repository import AuditContext, SqlAlchemyContentRepository
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence import PersistenceUnitOfWork, Scope
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_projects_runtime.episode_production import validate_production_edits
from server.xingjing_projects_runtime.episode_sources import authorize, scoped, sources
from server.xingjing_projects_runtime.episode_sources_models import EpisodeWorkingScriptRow

from .contracts import ConfirmPlan, CreatePlan
from .guards import ensure_plans_complete
from .models import EpisodePlanRow, PlanConfirmationRow, SourceSnapshotRow

MANAGE = frozenset({"project.view", "project.manage", "script.view", "script.manage"})


def fingerprint(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc(value: datetime) -> str:
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def require_key(key: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", key):
        raise EpisodeEditError("SOURCE_PLAN_KEY_REQUIRED", 422)


def snapshot_id(context: TrustedWorkspaceContext, project_id: str, source_id: str, digest: str) -> str:
    return str(
        uuid5(NAMESPACE_URL, fingerprint([context.tenant_id, context.workspace_id, project_id, source_id, digest]))
    )


def editable(item: dict) -> EpisodeEdit:
    return EpisodeEdit.model_validate({key: item[key] for key in EpisodeEdit.model_fields if key in item})


class SourcePlanningService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], manager: ProjectManager) -> None:
        self.sessions, self.manager = sessions, manager

    async def project(self, session: AsyncSession, context: TrustedWorkspaceContext, project_id: str, *, write=False):
        context = await authorize(session, context, project_id)
        required = MANAGE if write else {"project.view", "script.view"}
        if not required.issubset(context.permissions):
            raise EpisodeEditError("SOURCE_PLAN_PERMISSION_DENIED", 403)
        statement = select(ProjectRow).where(*scoped(ProjectRow, context, project_id), ProjectRow.deleted_at.is_(None))
        row = await session.scalar(statement.with_for_update() if write else statement)
        if row is None:
            raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
        if row.setup_state != "ready":
            raise EpisodeEditError("PROJECT_SETUP_REQUIRED", 409)
        if write:
            await session.execute(
                update(ProjectRow).where(*scoped(ProjectRow, context, project_id)).values(version=ProjectRow.version)
            )
        return context

    async def catalog(self, context: TrustedWorkspaceContext, project_id: str, source_id: str) -> dict:
        async with self.sessions() as session:
            await self.project(session, context, project_id)
            collection = await sources(session, context, project_id)
        option = next((option for option in collection.options if option["path"] == source_id), None)
        if option is None:
            raise EpisodeEditError("EPISODE_SOURCE_NOT_FOUND", 404)
        content = collection.texts[source_id]
        identity = snapshot_id(context, project_id, source_id, option["digest"])
        snapshot = await asyncio.to_thread(read_episodes, self.manager, project_id, source_texts=collection.texts)
        affected = [
            item
            for item in snapshot["items"]
            if item["sourceRange"] and item["sourceRange"]["source_file"] == source_id
        ]
        changed = any("source_changed" in item["issues"] for item in affected)
        covered = [(item["sourceRange"]["start"], item["sourceRange"]["end"]) for item in affected]
        try:
            structure = chapters(content, identity)
            available = [] if changed else uncovered_ranges(content, covered)
        except SourceStructureError as error:
            raise EpisodeEditError(str(error)) from error
        return {
            "source": option,
            "snapshotId": identity,
            "chapters": [asdict(item) for item in structure],
            "episodeRevision": snapshot["revision"],
            "sourceChanged": changed,
            "uncoveredRanges": available,
            "uncoveredCharacters": sum(end - start for start, end in available),
            "coordinateSystem": "nfc_lf_codepoints",
        }

    async def create(
        self, context: TrustedWorkspaceContext, project_id: str, command: CreatePlan, *, key: str, locale: str
    ) -> dict:
        require_key(key)
        digest = fingerprint(command.model_dump(mode="json", by_alias=True))
        async with PersistenceUnitOfWork(self.sessions, Scope(context.tenant_id, context.workspace_id)) as uow:
            session = uow.session
            context = await self.project(session, context, project_id, write=True)
            previous = await session.scalar(
                select(EpisodePlanRow).where(
                    *scoped(EpisodePlanRow, context, project_id),
                    EpisodePlanRow.actor_id == context.actor_id,
                    EpisodePlanRow.request_key == key,
                )
            )
            if previous:
                if previous.fingerprint != digest:
                    raise EpisodeEditError("IDEMPOTENCY_CONFLICT", 409)
                return await self._detail(session, context, previous)
            await ensure_plans_complete(
                session, context, project_id, await asyncio.to_thread(self.manager.load_project, project_id)
            )
            collection = await sources(session, context, project_id)
            option = next((item for item in collection.options if item["path"] == command.source_id), None)
            if option is None or option["digest"] != command.source_digest:
                raise EpisodeEditError("SOURCE_PLAN_SOURCE_CHANGED", 409)
            current = await asyncio.to_thread(read_episodes, self.manager, project_id, source_texts=collection.texts)
            if current["revision"] != command.episode_revision:
                raise EpisodeEditError("SOURCE_PLAN_VERSION_CHANGED", 409)
            if len(current["items"]) + command.count > 500 or (
                current["contentMode"] == "ad" and (current["items"] or command.count != 1)
            ):
                raise EpisodeEditError("SOURCE_PLAN_EPISODE_LIMIT", 422)
            affected = [
                item
                for item in current["items"]
                if item["sourceRange"] and item["sourceRange"]["source_file"] == command.source_id
            ]
            if any("source_changed" in item["issues"] for item in affected):
                raise EpisodeEditError("SOURCE_PLAN_SOURCE_CHANGED", 409)
            content = collection.texts[command.source_id]
            identity = snapshot_id(context, project_id, command.source_id, command.source_digest)
            try:
                structure = chapters(content, identity)
                chosen = None
                if command.chapter_ids is not None:
                    if len(set(command.chapter_ids)) != len(command.chapter_ids) or not set(
                        command.chapter_ids
                    ).issubset({item.id for item in structure}):
                        raise EpisodeEditError("SOURCE_PLAN_CHAPTER_INVALID", 422)
                    chosen = [(item.start, item.end) for item in structure if item.id in command.chapter_ids]
                covered = [(item["sourceRange"]["start"], item["sourceRange"]["end"]) for item in affected]
                available = uncovered_ranges(content, covered, chosen)
                ranges = split_ranges(content, available, command.count, structure)
            except SourceStructureError as error:
                raise EpisodeEditError(str(error)) from error
            at, plan_id = datetime.now(UTC), str(uuid4())
            source = await session.get(SourceSnapshotRow, identity)
            if source is None:
                source = SourceSnapshotRow(
                    id=identity,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    source_id=command.source_id,
                    source_digest=command.source_digest,
                    option=option,
                    content=content,
                    chapters=[asdict(item) for item in structure],
                    created_at=at,
                )
                session.add(source)
            candidates = []
            for index, (start, end) in enumerate(ranges):
                chapter_matches = [item for item in structure if item.start < end and item.end > start]
                title = next((item.title for item in chapter_matches if item.title and item.start == start), None)
                number = current["nextEpisode"] + index
                candidates.append(
                    {
                        "id": str(uuid4()),
                        "episode": number,
                        "title": title or translate("source_plan_episode_title", locale=locale, number=number),
                        "start": start,
                        "end": end,
                        "targetDuration": current["defaultDuration"],
                        "chapterIds": [item.id for item in chapter_matches],
                        "excerpt": content[start : min(end, start + 360)],
                    }
                )
            row = EpisodePlanRow(
                id=plan_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                actor_id=context.actor_id,
                request_key=key,
                fingerprint=digest,
                snapshot_id=identity,
                episode_revision=current["revision"],
                base_edits=[editable(item).model_dump() for item in current["items"]],
                proposal={
                    "sourceLabel": option["label"],
                    "method": "chapter_paragraph_rules",
                    "candidates": candidates,
                    "availableRanges": available,
                    "uncoveredCharacters": sum(end - start for start, end in available),
                },
                result=None,
                created_at=at,
            )
            session.add(row)
            await uow.audit.append(
                context.actor_id,
                "source_plan.proposed",
                "source_plan",
                plan_id,
                {
                    "requestId": context.request_id,
                    "snapshotId": identity,
                    "episodeRevision": current["revision"],
                    "count": len(candidates),
                },
            )
            await uow.commit()
            return await self._detail(session, context, row)

    async def list(self, context: TrustedWorkspaceContext, project_id: str) -> dict:
        async with self.sessions() as session:
            context = await self.project(session, context, project_id)
            project = await asyncio.to_thread(self.manager.load_project, project_id)
            receipt_ids = set(project.get("episode_plan_receipts", {}))
            completed = (
                set(
                    await session.scalars(
                        select(PlanConfirmationRow.plan_id).where(
                            *scoped(PlanConfirmationRow, context, project_id),
                            PlanConfirmationRow.plan_id.in_(receipt_ids),
                        )
                    )
                )
                if receipt_ids
                else set()
            )
            pending = receipt_ids - completed
            rows = list(
                await session.scalars(
                    select(EpisodePlanRow)
                    .where(*scoped(EpisodePlanRow, context, project_id))
                    .order_by(
                        case((EpisodePlanRow.id.in_(pending), 0), else_=1),
                        EpisodePlanRow.created_at.desc(),
                        EpisodePlanRow.id,
                    )
                    .limit(50)
                )
            )
        return {
            "items": [
                {
                    "id": row.id,
                    "sourceLabel": row.proposal["sourceLabel"],
                    "count": len(row.proposal["candidates"]),
                    "createdAt": utc(row.created_at),
                    "applied": row.result is not None,
                    "confirmationPending": row.id in pending,
                }
                for row in rows
            ],
            "canManage": MANAGE.issubset(context.permissions),
        }

    async def get(self, context: TrustedWorkspaceContext, project_id: str, plan_id: str) -> dict:
        async with self.sessions() as session:
            context = await self.project(session, context, project_id)
            row = await session.scalar(
                select(EpisodePlanRow).where(*scoped(EpisodePlanRow, context, project_id), EpisodePlanRow.id == plan_id)
            )
            if row is None:
                raise EpisodeEditError("SOURCE_PLAN_NOT_FOUND", 404)
            return await self._detail(session, context, row)

    async def _detail(self, session: AsyncSession, context: TrustedWorkspaceContext, row: EpisodePlanRow) -> dict:
        source = await session.get(SourceSnapshotRow, row.snapshot_id)
        if source is None or (source.tenant_id, source.workspace_id, source.project_id) != (
            context.tenant_id,
            context.workspace_id,
            row.project_id,
        ):
            raise EpisodeEditError("SOURCE_PLAN_SNAPSHOT_MISSING", 409)
        project = await asyncio.to_thread(self.manager.load_project, row.project_id)
        receipt = project.get("episode_plan_receipts", {}).get(row.id) if row.result is None else None
        pending = None
        if receipt and receipt["actor"] == context.actor_id and MANAGE.issubset(context.permissions):
            original = {item["episode"]: item["id"] for item in row.proposal["candidates"]}
            pending = {
                "key": receipt["key"],
                "payload": {
                    "candidates": [
                        {
                            "id": original[item["episode"]],
                            "title": item["title"],
                            "start": item["sourceRange"]["start"],
                            "end": item["sourceRange"]["end"],
                            "targetDuration": item["targetDuration"],
                        }
                        for item in receipt["planItems"]
                    ]
                },
            }
        return {
            "id": row.id,
            "projectId": row.project_id,
            "snapshotId": row.snapshot_id,
            "source": source.option,
            "chapters": source.chapters,
            "episodeRevision": row.episode_revision,
            "proposal": row.proposal,
            "result": row.result,
            "createdAt": utc(row.created_at),
            "canManage": MANAGE.issubset(context.permissions),
            "confirmationPending": bool(receipt),
            "pendingConfirmation": pending,
        }

    async def read_snapshot(
        self, context: TrustedWorkspaceContext, project_id: str, identity: str, start: int, end: int
    ) -> dict:
        async with self.sessions() as session:
            await self.project(session, context, project_id)
            row = await session.scalar(
                select(SourceSnapshotRow).where(
                    *scoped(SourceSnapshotRow, context, project_id), SourceSnapshotRow.id == identity
                )
            )
        if row is None:
            raise EpisodeEditError("SOURCE_PLAN_SNAPSHOT_MISSING", 404)
        if not 0 <= start < end <= len(row.content) or end - start > 8000:
            raise EpisodeEditError("SOURCE_PLAN_RANGE_INVALID", 422)
        if text_digest(row.content) != row.source_digest:
            raise EpisodeEditError("SOURCE_PLAN_SOURCE_CHANGED", 409)
        return {
            "snapshotId": identity,
            "source": row.option,
            "start": start,
            "end": end,
            "text": row.content[start:end],
            "chapters": [item for item in row.chapters if item["start"] < end and item["end"] > start],
            "coordinateSystem": "nfc_lf_codepoints",
        }

    async def confirm(
        self,
        context: TrustedWorkspaceContext,
        project_id: str,
        plan_id: str,
        command: ConfirmPlan,
        *,
        key: str,
        locale: str,
    ) -> dict:
        require_key(key)
        digest = fingerprint({"planId": plan_id, "payload": command.model_dump(mode="json", by_alias=True)})
        async with PersistenceUnitOfWork(self.sessions, Scope(context.tenant_id, context.workspace_id)) as uow:
            session = uow.session
            context = await self.project(session, context, project_id, write=True)
            previous = await session.scalar(
                select(PlanConfirmationRow).where(
                    *scoped(PlanConfirmationRow, context, project_id),
                    PlanConfirmationRow.actor_id == context.actor_id,
                    PlanConfirmationRow.request_key == key,
                )
            )
            if previous:
                if previous.fingerprint != digest:
                    raise EpisodeEditError("IDEMPOTENCY_CONFLICT", 409)
                return previous.result
            plan = await session.scalar(
                select(EpisodePlanRow).where(*scoped(EpisodePlanRow, context, project_id), EpisodePlanRow.id == plan_id)
            )
            if plan is None:
                raise EpisodeEditError("SOURCE_PLAN_NOT_FOUND", 404)
            if plan.result is not None:
                raise EpisodeEditError("SOURCE_PLAN_ALREADY_APPLIED", 409)
            source = await session.get(SourceSnapshotRow, plan.snapshot_id)
            if (
                source is None
                or (source.tenant_id, source.workspace_id, source.project_id)
                != (context.tenant_id, context.workspace_id, project_id)
                or text_digest(source.content) != source.source_digest
            ):
                raise EpisodeEditError("SOURCE_PLAN_SNAPSHOT_MISSING", 409)
            edits = self._confirmed_edits(plan, source, command)
            project = await asyncio.to_thread(self.manager.load_project, project_id)
            await ensure_plans_complete(session, context, project_id, project, allow_plan_id=plan.id)
            receipt = project.get("episode_plan_receipts", {}).get(plan.id)
            source_texts = {source.source_id: source.content}
            if receipt is None:
                collection = await sources(session, context, project_id)
                option = next((item for item in collection.options if item["path"] == source.source_id), None)
                if option is None or option["digest"] != source.source_digest:
                    raise EpisodeEditError("SOURCE_PLAN_SOURCE_CHANGED", 409)
                current = await asyncio.to_thread(
                    read_episodes, self.manager, project_id, source_texts=collection.texts
                )
                if current["revision"] != plan.episode_revision:
                    raise EpisodeEditError("SOURCE_PLAN_VERSION_CHANGED", 409)
                await validate_production_edits(session, context, project_id, edits, current)
                source_texts = collection.texts
            at = datetime.now(UTC)
            new_edits = edits[len(plan.base_edits) :]
            result_items = []
            for edit in new_edits:
                assert edit.sourceRange is not None
                start, end = edit.sourceRange.start, edit.sourceRange.end
                value = source.content[start:end]
                script_id = str(uuid5(NAMESPACE_URL, f"source-plan:{plan.id}:episode:{edit.episode}"))
                version = await asyncio.to_thread(
                    parse_script,
                    value,
                    version_number=1,
                    change_summary=translate("source_plan_working_summary", locale=locale),
                )
                document = ScriptDocument(
                    script_id,
                    context.workspace_id,
                    project_id,
                    edit.title,
                    1,
                    SourceDocument(f"episode_{edit.episode}.txt", "text/plain", value),
                    (version,),
                )

                def persist(sync_session: Session, document=document):
                    repository = SqlAlchemyContentRepository(
                        lambda: sync_session, audit_context=AuditContext(context.request_id, context.actor_id, at)
                    )
                    repository.add_initial_document(
                        sync_session, document, idempotency_key=f"source-plan:{plan.id}:{document.script_id}"
                    )

                await session.run_sync(persist)
                reference = {
                    "kind": source.option["kind"],
                    "sourceId": source.source_id,
                    "sourceScriptId": source.option["scriptId"],
                    "digest": source.source_digest,
                    "coordinateSystem": "nfc_lf",
                    "start": start,
                    "end": end,
                    "snapshotId": source.id,
                    "chapterIds": [
                        item["id"] for item in source.chapters if item["start"] < end and item["end"] > start
                    ],
                    "mappingKind": "exact_quote",
                    "planId": plan.id,
                }
                session.add(
                    EpisodeWorkingScriptRow(
                        id=str(uuid4()),
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        project_id=project_id,
                        episode=edit.episode,
                        sequence=1,
                        script_id=script_id,
                        source_ref=reference,
                        created_by=context.actor_id,
                        created_at=at,
                    )
                )
                result_items.append(
                    {"episode": edit.episode, "scriptId": script_id, "title": edit.title, "sourceRef": reference}
                )
            await session.flush()
            saved = await asyncio.to_thread(
                save_episodes,
                self.manager,
                project_id,
                edits,
                revision=plan.episode_revision,
                key=key,
                actor=context.actor_id,
                source_texts=source_texts,
                persist_budget=False,
                plan_id=plan.id,
            )
            result = {
                "planId": plan.id,
                "snapshotId": source.id,
                "episodeRevision": saved["after"],
                "items": result_items,
                "newProviderCalls": 0,
                "confirmedAt": at.isoformat(),
            }
            plan.result = result
            session.add(
                PlanConfirmationRow(
                    id=str(uuid4()),
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    actor_id=context.actor_id,
                    request_key=key,
                    plan_id=plan.id,
                    fingerprint=digest,
                    result=result,
                    created_at=at,
                )
            )
            await uow.audit.append(
                context.actor_id,
                "source_plan.confirmed",
                "source_plan",
                plan.id,
                {
                    "requestId": context.request_id,
                    "snapshotId": source.id,
                    "before": saved["before"],
                    "after": saved["after"],
                    "items": result_items,
                    "candidates": command.model_dump(mode="json", by_alias=True)["candidates"],
                },
            )
            await uow.outbox.add(
                "source_plan.confirmed", "project", project_id, {"planId": plan.id, "items": result_items}
            )
            await uow.commit()
            return result

    @staticmethod
    def _confirmed_edits(plan: EpisodePlanRow, source: SourceSnapshotRow, command: ConfirmPlan) -> list[EpisodeEdit]:
        proposals = {item["id"]: item for item in plan.proposal["candidates"]}
        ids = [item.id for item in command.candidates]
        if len(set(ids)) != len(ids) or any(identity not in proposals for identity in ids):
            raise EpisodeEditError("SOURCE_PLAN_CANDIDATE_INVALID", 422)
        ranges = []
        edits = [EpisodeEdit.model_validate(item) for item in plan.base_edits]
        for candidate in command.candidates:
            start, end = candidate.start, candidate.end
            if (
                not 0 <= start < end <= len(source.content)
                or not source.content[start:end].strip()
                or not any(begin <= start < end <= finish for begin, finish in plan.proposal["availableRanges"])
            ):
                raise EpisodeEditError("SOURCE_PLAN_RANGE_INVALID", 422)
            ranges.append((start, end))
            edits.append(
                EpisodeEdit(
                    episode=proposals[candidate.id]["episode"],
                    title=candidate.title,
                    targetDuration=candidate.target_duration,
                    sourceRange=SourceRange(source_file=source.source_id, start=start, end=end),
                )
            )
        ordered = sorted(ranges)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise EpisodeEditError("SOURCE_PLAN_RANGE_OVERLAP", 422)
        return edits
