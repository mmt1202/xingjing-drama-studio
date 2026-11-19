"""Preview and commit the same scoped recovery, with a single transactional receipt."""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from lib.episode_editor import EpisodeEditError
from lib.i18n import _ as translate
from server.xingjing_analytics.database import identity
from server.xingjing_assets_persistence.repository import AssetRow
from server.xingjing_content.models import DirectorProfile, RecoveryOrigin, ScriptDocument
from server.xingjing_content_persistence.repository import AuditContext, SqlAlchemyContentRepository
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_projects_runtime.episode_sources import scoped
from server.xingjing_projects_runtime.episode_sources_models import EpisodeWorkingScriptRow
from server.xingjing_source_planning.guards import ensure_plans_complete
from server.xingjing_source_planning.mapping import ScriptMappingService
from server.xingjing_source_planning.service import SourcePlanningService, fingerprint, require_key, text_digest
from server.xingjing_storyboard_persistence.repository import StoryboardRow

from .contracts import Confirmation, Selection
from .merge import merge
from .models import RecoveryReceipt


def visible(session: AsyncSession, context: TrustedWorkspaceContext, permission: str, variable: str) -> bool:
    if permission not in context.permissions:
        return False
    url = os.environ.get(variable)
    return not url or identity(session.get_bind().engine.url) == identity(url)


async def references(session: AsyncSession, context: TrustedWorkspaceContext, document: ScriptDocument) -> dict:
    ref = f"{document.script_id}@{document.locked_version_number or document.current_version.number}"
    directors = [v for v in document.director_versions if v.script_version_id == ref]
    confirmed = {c.candidate_id for c in document.director_confirmations if c.script_version_id == ref}
    assets = boards = None
    if visible(session, context, "asset.view", "XINGJING_ASSET_DATABASE_URL"):
        assets = await session.scalar(
            select(func.count())
            .select_from(AssetRow)
            .where(
                AssetRow.tenant_id == context.tenant_id,
                AssetRow.workspace_id == context.workspace_id,
                AssetRow.owner_project_id == document.project_id,
                AssetRow.frozen_snapshot["script_version_id"].as_string() == ref,
            )
        )
    if visible(session, context, "shot.view", "XINGJING_STORYBOARD_DATABASE_URL"):
        filters = (
            *scoped(StoryboardRow, context, document.project_id),
            StoryboardRow.payload["source_context"]["script_version_id"].as_string() == ref,
        )
        rows = await session.execute(
            select(StoryboardRow.storyboard_id, StoryboardRow.episode_id, StoryboardRow.version)
            .where(*filters)
            .order_by(StoryboardRow.storyboard_id)
        )
        items = [{"id": row[0], "episode": row[1], "version": row[2]} for row in rows]
        boards = {"count": len(items), "items": items}
    return {
        "scriptVersionId": ref,
        "directorCount": len(directors),
        "directors": [{"version": v.number, "confirmed": v.id in confirmed} for v in directors],
        "assets": assets,
        "boards": boards,
    }


class ScriptRecovery:
    def __init__(self, planning: SourcePlanningService):
        self.planning = planning
        self.scripts = ScriptMappingService(planning)

    async def _open(self, session, context, project_id, script_id, *, lock=False):
        context = await self.planning.project(session, context, project_id, lock=lock)
        document = await self.scripts.document(session, context, project_id, script_id, write=lock)
        link = await session.scalar(
            select(EpisodeWorkingScriptRow).where(
                *scoped(EpisodeWorkingScriptRow, context, project_id), EpisodeWorkingScriptRow.script_id == script_id
            )
        )
        fork = document.locked_version_number is not None
        overview = {
            "scriptId": script_id,
            "revision": document.revision,
            "baseVersion": document.current_version.number,
            "mode": "fork" if fork else "append",
            "episode": link.episode if link else None,
            "canConfirm": "script.manage" in context.permissions
            and (not fork or link is None or "project.manage" in context.permissions),
        }
        return context, document, link, overview

    async def read(self, context, project_id, script_id):
        async with self.planning.sessions() as session:
            _, _, _, overview = await self._open(session, context, project_id, script_id)
            return overview

    async def _prepare(self, session, context, document, link, overview, selection: Selection, locale):
        if selection.revision != document.revision or selection.base_version != document.current_version.number:
            raise EpisodeEditError("SCRIPT_RECOVERY_VERSION_CHANGED", 409)
        source = next((v for v in document.versions if v.number == selection.source_version), None)
        if source is None or source.number >= selection.base_version:
            raise EpisodeEditError("SCRIPT_RECOVERY_SELECTION_INVALID", 422)
        fork = overview["mode"] == "fork"
        number = 1 if fork else selection.base_version + 1
        summary = translate("script_recovery_summary", locale=locale, version=source.number, reason=selection.reason)
        version, diffs, counts = merge(document.current_version, source, selection.changes, number, summary)
        impact = await references(session, context, document)
        analysis = document.current_version.analysis
        impact["refreshAnalysis"] = version.source_content != document.current_version.source_content and bool(
            analysis.props
            or analysis.relationships
            or analysis.story_beats
            or analysis.episode_suggestions
            or any(person.description for person in analysis.characters)
        )
        sequence = None
        previous_working = None
        if link and fork:
            project = await asyncio.to_thread(self.planning.manager.load_project, document.project_id)
            await ensure_plans_complete(session, context, document.project_id, project)
            if not any(e.get("episode") == link.episode for e in project.get("episodes", [])):
                raise EpisodeEditError("SCRIPT_RECOVERY_EPISODE_CHANGED", 409)
            latest = (
                await session.execute(
                    select(EpisodeWorkingScriptRow.sequence, EpisodeWorkingScriptRow.script_id)
                    .where(
                        *scoped(EpisodeWorkingScriptRow, context, document.project_id),
                        EpisodeWorkingScriptRow.episode == link.episode,
                    )
                    .order_by(EpisodeWorkingScriptRow.sequence.desc())
                    .limit(1)
                )
            ).one()
            sequence, previous_working = latest.sequence + 1, latest.script_id
        signature = fingerprint(
            [
                document.workspace_id,
                document.project_id,
                document.script_id,
                selection.model_dump(by_alias=True),
                overview,
                number,
                text_digest(version.source_content),
                [asdict(a) for a in version.source_attributions],
                counts,
                impact,
                sequence,
                previous_working,
            ]
        )
        preview = {
            **overview,
            "version": number,
            "sourceVersion": source.number,
            "previewDigest": signature,
            "changes": diffs,
            "candidate": version.source_content,
            "mappings": counts,
            "impact": impact,
            "workingSequence": sequence,
            "previousWorkingScriptId": previous_working,
            "validation": [{"code": issue.code, "path": issue.path} for issue in version.validation_errors],
        }
        return version, preview

    async def preview(self, context, project_id, script_id, selection, locale):
        async with self.planning.sessions() as session:
            context, document, link, overview = await self._open(session, context, project_id, script_id)
            _, preview = await self._prepare(session, context, document, link, overview, selection, locale)
            return preview

    async def confirm(self, context, project_id, script_id, command: Confirmation, *, key: str, locale: str):
        require_key(key)
        body = command.model_dump(by_alias=True)
        operation = fingerprint([script_id, body])
        async with self.planning.sessions() as session, session.begin():
            context, document, link, overview = await self._open(session, context, project_id, script_id, lock=True)
            if "script.manage" not in context.permissions:
                raise EpisodeEditError("SCRIPT_RECOVERY_FORBIDDEN", 403)
            previous = await session.get(
                RecoveryReceipt, (context.tenant_id, context.workspace_id, project_id, context.actor_id, key)
            )
            if previous:
                if previous.fingerprint != operation:
                    raise EpisodeEditError("SCRIPT_RECOVERY_KEY_REUSED", 409)
                if (
                    previous.result["mode"] == "fork"
                    and previous.result["episode"]
                    and "project.manage" not in context.permissions
                ):
                    raise EpisodeEditError("SCRIPT_RECOVERY_FORBIDDEN", 403)
                return previous.result
            if not overview["canConfirm"]:
                raise EpisodeEditError("SCRIPT_RECOVERY_FORBIDDEN", 403)
            version, preview = await self._prepare(
                session, context, document, link, overview, command.selection, locale
            )
            if preview["previewDigest"] != command.preview_digest:
                raise EpisodeEditError("SCRIPT_RECOVERY_PREVIEW_CHANGED", 409)
            now = datetime.now(UTC)
            fork = preview["mode"] == "fork"
            if fork:
                origin = RecoveryOrigin(
                    script_id,
                    command.selection.base_version,
                    command.selection.source_version,
                    document.recovery_origin.original_script_id if document.recovery_origin else script_id,
                )
                suffix = translate("script_recovery_copy", locale=locale)
                updated = ScriptDocument(
                    str(uuid4()),
                    context.workspace_id,
                    project_id,
                    f"{document.title[: 511 - len(suffix)]} {suffix}",
                    1,
                    document.source_document,
                    (version,),
                    recovery_origin=origin,
                )
            else:
                updated = replace(
                    document,
                    revision=document.revision + 1,
                    versions=(*document.versions, version),
                    director_profile=DirectorProfile(),
                )

            def save(sync: Session) -> None:
                repository = SqlAlchemyContentRepository(
                    lambda: sync,
                    audit_context=AuditContext(
                        context.request_id, context.actor_id, now, "content.script.partially_restored"
                    ),
                )
                receipt_key = f"recovery:{fingerprint([context.actor_id, key])}"
                if fork:
                    repository.add_initial_document(sync, updated, idempotency_key=receipt_key)
                else:
                    repository.append_document_version(sync, updated, document, idempotency_key=receipt_key)

            await session.run_sync(save)
            if fork and link:
                session.add(
                    EpisodeWorkingScriptRow(
                        id=str(uuid4()),
                        tenant_id=context.tenant_id,
                        workspace_id=context.workspace_id,
                        project_id=project_id,
                        episode=link.episode,
                        sequence=preview["workingSequence"],
                        script_id=updated.script_id,
                        source_ref=link.source_ref,
                        created_by=context.actor_id,
                        created_at=now,
                    )
                )
            result = {
                "mode": preview["mode"],
                "scriptId": updated.script_id,
                "parentScriptId": script_id,
                "version": version.number,
                "revision": updated.revision,
                "episode": overview["episode"],
                "confirmedAt": now.isoformat(),
            }
            session.add(
                RecoveryReceipt(
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    project_id=project_id,
                    script_id=script_id,
                    actor_id=context.actor_id,
                    request_key=key,
                    request_id=context.request_id,
                    fingerprint=operation,
                    command=body,
                    result=result,
                    created_at=now,
                )
            )
            return result
