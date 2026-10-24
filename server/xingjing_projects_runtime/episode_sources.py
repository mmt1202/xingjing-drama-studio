"""Scoped SQL source reads and explicit episode-to-working-script handoffs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Request
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, aliased

from lib.episode_editor import EpisodeEditError, _sources, read_episodes
from lib.episode_ledger import normalize_source_text
from lib.project_manager import get_project_manager
from server.xingjing_analytics.database import identity
from server.xingjing_content.models import ScriptDocument, SourceDocument
from server.xingjing_content.parser import parse_script
from server.xingjing_content_persistence.repository import (
    AuditContext,
    ScriptRow,
    ScriptVersionRow,
    SqlAlchemyContentRepository,
)
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_platform_persistence import PersistenceUnitOfWork, Scope
from server.xingjing_platform_persistence.persistence import ProjectRow
from server.xingjing_storyboard_persistence.repository import StoryboardRow
from server.xingjing_team_persistence.repository import WorkspaceRow

from .episode_sources_models import EpisodeWorkingCommandRow, EpisodeWorkingScriptRow

MAX_SOURCES = 200
MAX_SOURCE_CHARACTERS = 2_000_000
MAX_TOTAL_SOURCE_CHARACTERS = 20_000_000


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def scoped(model: Any, context: TrustedWorkspaceContext, project_id: str):
    project_column = model.id if model is ProjectRow else model.project_id
    return (
        model.tenant_id == context.tenant_id,
        model.workspace_id == context.workspace_id,
        project_column == project_id,
    )


def same_database(session: AsyncSession) -> None:
    bound = session.get_bind().engine.url
    for variable in ("XINGJING_PROJECT_DATABASE_URL", "XINGJING_CONTENT_DATABASE_URL", "XINGJING_TEAM_DATABASE_URL"):
        configured = os.environ.get(variable, "").strip()
        if configured:
            try:
                if identity(bound) != identity(configured):
                    raise ValueError("source database differs")
            except (ValueError, TypeError, SQLAlchemyError) as error:
                raise EpisodeEditError("EPISODE_SOURCE_DATABASE_MISMATCH", 503) from error


async def authorize(
    session: AsyncSession, context: TrustedWorkspaceContext, project_id: str
) -> TrustedWorkspaceContext:
    same_database(session)
    tenants = list(
        await session.scalars(select(WorkspaceRow.tenant_id).where(WorkspaceRow.workspace_id == context.workspace_id))
    )
    if tenants != [context.tenant_id]:
        raise EpisodeEditError("EPISODE_WORKSPACE_UNAVAILABLE", 403)
    if context.role.casefold() not in {"owner", "admin", "platform_admin"}:
        assignment = (
            await session.execute(
                text("""
            SELECT permissions, data_scope FROM xingjing_project_members
            WHERE tenant_id=:tenant AND workspace_id=:workspace AND project_id=:project
              AND member_id=:actor AND active IS TRUE
        """),
                {
                    "tenant": context.tenant_id,
                    "workspace": context.workspace_id,
                    "project": project_id,
                    "actor": context.actor_id,
                },
            )
        ).one_or_none()
        if assignment is None:
            raise EpisodeEditError("FORBIDDEN", 403)
        permissions, scopes = (json.loads(item) if isinstance(item, str) else item for item in assignment)
        if not isinstance(scopes, list) or not scopes or any(item != f"project:{project_id}" for item in scopes):
            raise EpisodeEditError("EPISODE_NARROW_SCOPE_UNSUPPORTED", 403)
        workspace_permissions = (
            await session.execute(
                text("""
            SELECT role.permissions FROM xingjing_team_members AS member
            JOIN xingjing_team_roles AS role ON role.tenant_id=member.tenant_id
              AND role.workspace_id=member.workspace_id AND role.role_id=member.role_id
            WHERE member.tenant_id=:tenant AND member.workspace_id=:workspace
              AND member.member_id=:actor AND member.active IS TRUE
        """),
                {"tenant": context.tenant_id, "workspace": context.workspace_id, "actor": context.actor_id},
            )
        ).scalar_one_or_none()
        if isinstance(workspace_permissions, str):
            workspace_permissions = json.loads(workspace_permissions)
        if not isinstance(permissions, list) or not isinstance(workspace_permissions, list):
            raise EpisodeEditError("FORBIDDEN", 403)
        if any(not all(isinstance(item, str) for item in values) for values in (permissions, workspace_permissions)):
            raise EpisodeEditError("FORBIDDEN", 403)
        context = replace(
            context, permissions=context.permissions & frozenset(permissions) & frozenset(workspace_permissions)
        )
    if "project.view" not in context.permissions:
        raise EpisodeEditError("FORBIDDEN", 403)
    return context


async def source_context(runtime: Any, request: Request, project_id: str, *, manage: bool = False):
    context = await runtime._context(request, "project.manage" if manage else "project.view")
    if context.role.upper() == "API_KEY":
        raise EpisodeEditError("EPISODE_INTERACTIVE_SESSION_REQUIRED", 403)
    if request.headers.get("X-Workspace-Id", context.workspace_id) != context.workspace_id:
        raise EpisodeEditError("EPISODE_WORKSPACE_MISMATCH", 403)
    if request.headers.get("X-Project-Id", project_id) != project_id:
        raise EpisodeEditError("PROJECT_SCOPE_MISMATCH", 403)
    async with runtime._require_sessions()() as session:
        context = await authorize(session, context, project_id)
    if manage and "project.manage" not in context.permissions:
        raise EpisodeEditError("FORBIDDEN", 403)
    return context


@dataclass
class SourceCollection:
    options: list[dict[str, Any]]
    texts: dict[str, str]


async def sources(session: AsyncSession, context: TrustedWorkspaceContext, project_id: str) -> SourceCollection:
    context = await authorize(session, context, project_id)
    if "script.view" not in context.permissions:
        return SourceCollection([], {})
    derived = select(EpisodeWorkingScriptRow.script_id).where(*scoped(EpisodeWorkingScriptRow, context, project_id))
    predicates = (
        ScriptRow.workspace_id == context.workspace_id,
        ScriptRow.project_id == project_id,
        ScriptRow.script_id.not_in(derived),
    )
    sizes = (
        await session.execute(
            select(ScriptRow.script_id, func.length(ScriptRow.source_content)).where(*predicates).limit(MAX_SOURCES + 1)
        )
    ).all()
    if len(sizes) > MAX_SOURCES:
        raise EpisodeEditError("EPISODE_SOURCE_LIMIT", 422)
    if (
        any(size > MAX_SOURCE_CHARACTERS for _, size in sizes)
        or sum(size for _, size in sizes) > MAX_TOTAL_SOURCE_CHARACTERS
    ):
        raise EpisodeEditError("EPISODE_SOURCE_TOO_LARGE", 413)
    rows = list(
        await session.scalars(
            select(ScriptRow).where(*predicates).order_by(ScriptRow.title, ScriptRow.script_id).limit(MAX_SOURCES + 1)
        )
    )
    manager = get_project_manager()
    files = await asyncio.to_thread(_sources, manager.get_project_path(project_id))
    if len(rows) + len(files) > MAX_SOURCES:
        raise EpisodeEditError("EPISODE_SOURCE_LIMIT", 422)
    options = []
    texts = dict(files)
    for path, value in files.items():
        options.append(
            {
                "path": path,
                "label": path,
                "kind": "file",
                "scriptId": None,
                "digest": digest(value),
                "length": len(value),
                "coordinateSystem": "nfc_lf",
            }
        )
    for row in rows:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", row.script_id):
            raise EpisodeEditError("EPISODE_SOURCE_UNSAFE", 422)
        value = normalize_source_text(row.source_content)
        if len(value) > MAX_SOURCE_CHARACTERS:
            raise EpisodeEditError("EPISODE_SOURCE_TOO_LARGE", 413)
        path = f"sql-content/{row.script_id}/original.txt"
        texts[path] = value
        options.append(
            {
                "path": path,
                "label": row.title,
                "kind": "sql_original",
                "scriptId": row.script_id,
                "digest": digest(value),
                "length": len(value),
                "coordinateSystem": "nfc_lf",
            }
        )
    if sum(map(len, texts.values())) > MAX_TOTAL_SOURCE_CHARACTERS:
        raise EpisodeEditError("EPISODE_SOURCE_TOO_LARGE", 413)
    return SourceCollection(options, texts)


async def enrich_working_scripts(
    session: AsyncSession, context: TrustedWorkspaceContext, project_id: str, snapshot: dict
) -> dict:
    context = await authorize(session, context, project_id)
    for episode in snapshot["items"]:
        episode["workingScript"] = None
    snapshot["permissions"] = {
        "canReadSources": "script.view" in context.permissions,
        "canCreateWorkingScript": {"project.manage", "script.view", "script.manage"} <= context.permissions,
    }
    if "script.view" not in context.permissions:
        for episode in snapshot["items"]:
            episode["issues"] = [
                issue for issue in episode.get("issues", []) if issue not in {"source_missing", "source_changed"}
            ]
            if "source_restricted" not in episode["issues"]:
                episode["issues"].append("source_restricted")
        return snapshot
    current_digests = {
        source["path"]: source.get("digest") or digest(source["text"])
        for source in snapshot.get("sources", [])
        if source.get("digest") or isinstance(source.get("text"), str)
    }
    history = aliased(EpisodeWorkingScriptRow)
    latest_sequence = (
        select(func.max(history.sequence))
        .where(
            history.tenant_id == EpisodeWorkingScriptRow.tenant_id,
            history.workspace_id == EpisodeWorkingScriptRow.workspace_id,
            history.project_id == EpisodeWorkingScriptRow.project_id,
            history.episode == EpisodeWorkingScriptRow.episode,
        )
        .correlate(EpisodeWorkingScriptRow)
        .scalar_subquery()
    )
    latest_version = (
        select(func.max(ScriptVersionRow.number))
        .where(
            ScriptVersionRow.workspace_id == ScriptRow.workspace_id,
            ScriptVersionRow.script_id == ScriptRow.script_id,
        )
        .correlate(ScriptRow)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(EpisodeWorkingScriptRow, ScriptRow, ScriptVersionRow)
            .join(
                ScriptRow,
                (ScriptRow.workspace_id == EpisodeWorkingScriptRow.workspace_id)
                & (ScriptRow.script_id == EpisodeWorkingScriptRow.script_id),
            )
            .join(
                ScriptVersionRow,
                (ScriptVersionRow.workspace_id == ScriptRow.workspace_id)
                & (ScriptVersionRow.script_id == ScriptRow.script_id),
            )
            .where(
                *scoped(EpisodeWorkingScriptRow, context, project_id),
                ScriptRow.project_id == project_id,
                EpisodeWorkingScriptRow.sequence == latest_sequence,
                ScriptVersionRow.number == latest_version,
            )
            .order_by(EpisodeWorkingScriptRow.sequence.desc(), ScriptVersionRow.number.desc())
        )
    ).all()
    latest = {}
    for link, script, version in rows:
        if link.episode not in latest:
            latest[link.episode] = working_dto(link, script, version)
    for episode in snapshot["items"]:
        item = latest.get(episode["episode"])
        if item:
            selected = episode.get("sourceRange") or {}
            item["rangeMatches"] = (
                not {"source_missing", "source_changed"}.intersection(episode.get("issues", []))
                and current_digests.get(item["sourceRef"]["sourceId"]) == item["sourceRef"]["digest"]
                and all(
                    selected.get(field) == item["sourceRef"].get(target)
                    for field, target in (("source_file", "sourceId"), ("start", "start"), ("end", "end"))
                )
            )
            episode["workingScript"] = item
    return snapshot


def working_dto(link: EpisodeWorkingScriptRow, script: ScriptRow, version: ScriptVersionRow) -> dict[str, Any]:
    return {
        "scriptId": script.script_id,
        "revision": script.revision,
        "versionNumber": version.number,
        "lockedVersionNumber": script.locked_version_number,
        "validationState": "invalid" if version.validation_errors else "valid",
        "sourceRef": link.source_ref,
        "rangeMatches": True,
        "createdAt": link.created_at.replace(tzinfo=UTC).isoformat()
        if link.created_at.tzinfo is None
        else link.created_at.isoformat(),
    }


async def create_working_script(
    runtime: Any,
    context: TrustedWorkspaceContext,
    project_id: str,
    episode: int,
    *,
    revision: str,
    key: str,
    body: dict,
) -> dict:
    if not {"project.manage", "script.view", "script.manage"} <= context.permissions:
        raise EpisodeEditError("FORBIDDEN", 403)
    if (
        set(body) - {"sourceDigest", "previousWorkingScriptId"}
        or not isinstance(body.get("sourceDigest"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", body["sourceDigest"])
    ):
        raise EpisodeEditError("EPISODE_WORKING_SCRIPT_INVALID", 422)
    previous = body.get("previousWorkingScriptId")
    if previous is not None and (not isinstance(previous, str) or not previous or len(previous) > 128):
        raise EpisodeEditError("EPISODE_WORKING_SCRIPT_INVALID", 422)
    fingerprint = digest(json.dumps({"episode": episode, "revision": revision, "body": body}, sort_keys=True))
    async with PersistenceUnitOfWork(
        runtime._require_sessions(), Scope(context.tenant_id, context.workspace_id)
    ) as uow:
        session = uow.session
        context = await authorize(session, context, project_id)
        if not {"project.manage", "script.view", "script.manage"} <= context.permissions:
            raise EpisodeEditError("FORBIDDEN", 403)
        project = await session.scalar(
            select(ProjectRow)
            .where(
                *scoped(ProjectRow, context, project_id), ProjectRow.id == project_id, ProjectRow.deleted_at.is_(None)
            )
            .with_for_update()
        )
        if project is None:
            raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
        if project.setup_state != "ready":
            raise EpisodeEditError("PROJECT_SETUP_REQUIRED", 409)
        await session.execute(
            update(ProjectRow)
            .where(*scoped(ProjectRow, context, project_id), ProjectRow.id == project_id)
            .values(version=ProjectRow.version)
        )
        command_key = (context.tenant_id, context.workspace_id, project_id, context.actor_id, key)
        receipt = await session.get(EpisodeWorkingCommandRow, command_key)
        if receipt:
            if receipt.fingerprint != fingerprint:
                raise EpisodeEditError("IDEMPOTENCY_CONFLICT", 409)
            return receipt.response
        collection = await sources(session, context, project_id)
        snapshot = await asyncio.to_thread(
            read_episodes, get_project_manager(), project_id, source_texts=collection.texts
        )
        if snapshot["revision"] != revision:
            raise EpisodeEditError("VERSION_CONFLICT", 409)
        entry = next((item for item in snapshot["items"] if item["episode"] == episode), None)
        if entry is None:
            raise EpisodeEditError("EPISODE_NOT_FOUND", 404)
        if "source_changed" in entry.get("issues", []):
            raise EpisodeEditError("EPISODE_SOURCE_CHANGED", 409)
        if entry["locked"] or entry["production"].get("scriptVersionId"):
            raise EpisodeEditError("EPISODE_PRODUCTION_LOCKED", 409)
        if await session.scalar(
            select(StoryboardRow.storyboard_id)
            .where(*scoped(StoryboardRow, context, project_id), StoryboardRow.episode_id == str(episode))
            .limit(1)
        ):
            raise EpisodeEditError("EPISODE_PRODUCTION_INPUTS_LOCKED", 409)
        selected = entry.get("sourceRange") or {}
        option = next((item for item in collection.options if item["path"] == selected.get("source_file")), None)
        if option is None or option["digest"] != body["sourceDigest"]:
            raise EpisodeEditError("EPISODE_SOURCE_CHANGED", 409)
        start, end = selected.get("start"), selected.get("end")
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= option["length"]:
            raise EpisodeEditError("EPISODE_SOURCE_RANGE_INVALID", 422)
        source_ref = {
            "kind": option["kind"],
            "sourceId": option["path"],
            "sourceScriptId": option["scriptId"],
            "digest": option["digest"],
            "coordinateSystem": "nfc_lf",
            "start": start,
            "end": end,
        }
        latest = await session.scalar(
            select(EpisodeWorkingScriptRow)
            .where(*scoped(EpisodeWorkingScriptRow, context, project_id), EpisodeWorkingScriptRow.episode == episode)
            .order_by(EpisodeWorkingScriptRow.sequence.desc())
            .limit(1)
        )
        created = latest is None or latest.source_ref != source_ref
        now = datetime.now(UTC)
        if latest is not None and latest.source_ref != source_ref and previous != latest.script_id:
            raise EpisodeEditError("EPISODE_WORKING_SCRIPT_REPLACEMENT_REQUIRED", 409)
        if latest is not None and previous is not None and previous != latest.script_id:
            raise EpisodeEditError("VERSION_CONFLICT", 409)
        if created:
            value = collection.texts[option["path"]][start:end]
            if not value.strip():
                raise EpisodeEditError("EPISODE_SOURCE_RANGE_INVALID", 422)
            version = await asyncio.to_thread(
                parse_script, value, version_number=1, change_summary="Episode source range"
            )
            document = ScriptDocument(
                str(uuid4()),
                context.workspace_id,
                project_id,
                entry["title"],
                1,
                SourceDocument(f"episode_{episode}.txt", "text/plain", value),
                (version,),
            )

            def persist(sync_session: Session):
                repository = SqlAlchemyContentRepository(
                    lambda: sync_session, audit_context=AuditContext(context.request_id, context.actor_id, now)
                )
                repository.add_initial_document(sync_session, document, idempotency_key=key)

            await session.run_sync(persist)
            link = EpisodeWorkingScriptRow(
                id=str(uuid4()),
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                episode=episode,
                sequence=latest.sequence + 1 if latest else 1,
                script_id=document.script_id,
                source_ref=source_ref,
                created_by=context.actor_id,
                created_at=now,
            )
            session.add(link)
            await session.flush()
        else:
            assert latest is not None
            link = latest
        script = await session.scalar(
            select(ScriptRow).where(
                ScriptRow.workspace_id == context.workspace_id,
                ScriptRow.project_id == project_id,
                ScriptRow.script_id == link.script_id,
            )
        )
        version_row = await session.scalar(
            select(ScriptVersionRow)
            .where(ScriptVersionRow.workspace_id == context.workspace_id, ScriptVersionRow.script_id == link.script_id)
            .order_by(ScriptVersionRow.number.desc())
            .limit(1)
        )
        if script is None or version_row is None:
            raise EpisodeEditError("EPISODE_WORKING_SCRIPT_UNAVAILABLE", 503)
        result = {"workingScript": working_dto(link, script, version_row), "revision": revision, "created": created}
        session.add(
            EpisodeWorkingCommandRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                project_id=project_id,
                actor_id=context.actor_id,
                key=key,
                fingerprint=fingerprint,
                response=result,
                created_at=now,
            )
        )
        if created:
            await uow.audit.append(
                context.actor_id,
                "episode.working_script.created",
                "script",
                link.script_id,
                {
                    "requestId": context.request_id,
                    "episode": episode,
                    "sourceRef": source_ref,
                    "previousWorkingScriptId": latest.script_id if latest else None,
                },
            )
            await uow.outbox.add(
                "episode.working_script.created",
                "script",
                link.script_id,
                {"episode": episode, "scriptId": link.script_id},
            )
        await uow.commit()
        return result
