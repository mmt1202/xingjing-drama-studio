"""Trusted project scope and SQL audit/outbox adapter for the episode ledger."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Query, Request
from pydantic import ValidationError
from sqlalchemy import select, update

from lib.config.resolver import ConfigResolver
from lib.db import async_session_factory
from lib.episode_editor import EpisodeEdit, EpisodeEditError, read_episodes, save_episodes, split_remaining
from lib.project_manager import get_project_manager
from server.services.cost_estimation import CostEstimationService
from server.xingjing_platform_persistence import PersistenceUnitOfWork, Scope
from server.xingjing_platform_persistence.persistence import ProjectRow

from .episode_production import (
    prepare_production_batch,
    production_catalog,
    project_production,
    validate_production_edits,
)
from .episode_sources import create_working_script, enrich_working_scripts, source_context, sources

if TYPE_CHECKING:
    from .runtime import ProjectsRuntime


def register_episode_routes(runtime: ProjectsRuntime, router: APIRouter) -> None:
    async def ready(context, project_id: str):
        row = await runtime._row(context.tenant_id, context.workspace_id, project_id, include_deleted=False)
        if row is None:
            raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
        if row.setup_state != "ready":
            raise EpisodeEditError("PROJECT_SETUP_REQUIRED", 409)
        return row

    async def snapshot(context, project_id: str):
        async with runtime._require_sessions()() as session:
            collection = await sources(session, context, project_id)
            data = await asyncio.to_thread(
                read_episodes, get_project_manager(), project_id, source_texts=collection.texts
            )
            data["sources"] = collection.options
            return await enrich_working_scripts(session, context, project_id, data)

    @router.get("/projects/{project_id}/episodes/source")
    async def source(request: Request, project_id: str, sourceId: str):
        context = await source_context(runtime, request, project_id)
        await ready(context, project_id)
        if "script.view" not in context.permissions:
            raise EpisodeEditError("FORBIDDEN", 403)
        async with runtime._require_sessions()() as session:
            collection = await sources(session, context, project_id)
        option = next((item for item in collection.options if item["path"] == sourceId), None)
        if option is None:
            raise EpisodeEditError("EPISODE_SOURCE_NOT_FOUND", 404)
        return {
            "data": {"source": {**option, "text": collection.texts[sourceId]}},
            "meta": {"requestId": context.request_id},
        }

    @router.post("/projects/{project_id}/episodes/{episode}/working-script")
    async def working_script(request: Request, project_id: str, episode: int):
        context = await source_context(runtime, request, project_id, manage=True)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 128:
            raise EpisodeEditError("IDEMPOTENCY_KEY_REQUIRED", 400)
        if not 1 <= episode <= 1_000_000:
            raise EpisodeEditError("EPISODE_NOT_FOUND", 404)
        data = await create_working_script(
            runtime,
            context,
            project_id,
            episode,
            revision=_revision_header(request),
            key=key,
            body=await _body(request),
        )
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/episodes")
    async def episodes(
        request: Request,
        project_id: str,
        q: str = "",
        productionStatus: str = "",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        context = await source_context(runtime, request, project_id)
        row = await ready(context, project_id)
        data = await snapshot(context, project_id)
        items = data["items"]
        data["summary"] = {
            "total": len(items),
            "scripted": sum(e["scriptStatus"] == "generated" for e in items),
            "inProduction": sum(e["status"] == "in_production" for e in items),
            "completed": sum(e["status"] == "completed" for e in items),
        }
        filtered = [
            e
            for e in items
            if (not productionStatus or e["status"] == productionStatus)
            and (not q or q.casefold() in f"{e['episode']} {e['title']} {e['hook']}".casefold())
        ]
        data["items"] = filtered[offset : offset + limit]
        data["projectName"] = row.name
        data["total"] = len(filtered)
        return {"data": data, "meta": {"requestId": context.request_id, "total": len(filtered)}}

    @router.post("/projects/{project_id}/episodes/split-preview")
    async def preview(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id, manage=True)
        await ready(context, project_id)
        body = await _body(request)
        revision = _revision_header(request)
        if not isinstance(body.get("source"), str):
            raise EpisodeEditError("EPISODE_SPLIT_INVALID")
        if "script.view" not in context.permissions:
            raise EpisodeEditError("FORBIDDEN", 403)
        async with runtime._require_sessions()() as session:
            collection = await sources(session, context, project_id)
        option = next((item for item in collection.options if item["path"] == body["source"]), None)
        if option is None:
            raise EpisodeEditError("EPISODE_SOURCE_NOT_FOUND", 404)
        if (option["kind"] == "sql_original" or body.get("sourceDigest") is not None) and body.get(
            "sourceDigest"
        ) != option["digest"]:
            raise EpisodeEditError("EPISODE_SOURCE_CHANGED", 409)
        data = await asyncio.to_thread(
            split_remaining,
            get_project_manager(),
            project_id,
            body.get("source", ""),
            body.get("count", 0),
            revision,
            source_texts=collection.texts,
        )
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/episodes/production")
    async def production(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id)
        await ready(context, project_id)
        current_snapshot = await snapshot(context, project_id)
        async with runtime._require_sessions()() as session:
            catalog = await production_catalog(session, context, project_id)
        data = project_production(current_snapshot, catalog)
        data["permissions"] = sorted(context.permissions)
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.post("/projects/{project_id}/episodes/production/prepare")
    async def prepare(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id, manage=True)
        await ready(context, project_id)
        revision = _revision_header(request)
        body = await _body(request)
        action, numbers, target = body.get("action"), body.get("episodes"), body.get("target", "")
        if action not in {"storyboards", "packages"} or not isinstance(numbers, list) or not 1 <= len(numbers) <= 50:
            raise EpisodeEditError("EPISODE_BATCH_INVALID")
        if any(type(n) is not int or n < 1 for n in numbers) or len(set(numbers)) != len(numbers):
            raise EpisodeEditError("EPISODE_BATCH_INVALID")
        required = (
            {"shot.manage", "shot.view", "script.view", "asset.view"}
            if action == "storyboards"
            else {
                "export.manage",
                "export.view",
                "final.view",
            }
        )
        if not required.issubset(context.permissions):
            raise EpisodeEditError("FORBIDDEN", 403)
        if not isinstance(target, str) or len(target) > 128 or (action == "packages" and not target.strip()):
            raise EpisodeEditError("EPISODE_EXPORT_TARGET_REQUIRED")
        current_snapshot = await snapshot(context, project_id)
        if current_snapshot["revision"] != revision:
            raise EpisodeEditError("VERSION_CONFLICT", 409)
        async with runtime._require_sessions()() as session:
            catalog = await production_catalog(session, context, project_id)
        data = prepare_production_batch(context, project_id, current_snapshot, catalog, numbers, action, target.strip())
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.put("/projects/{project_id}/episodes")
    async def save(request: Request, project_id: str):
        context = await source_context(runtime, request, project_id, manage=True)
        revision = _revision_header(request)
        key = request.headers.get("Idempotency-Key", "").strip()
        if not key or len(key) > 128:
            raise EpisodeEditError("IDEMPOTENCY_KEY_REQUIRED", 400)
        body = await _body(request)
        raw = body.get("items")
        if not isinstance(raw, list) or len(raw) > 500:
            raise EpisodeEditError("EPISODE_SET_INVALID")
        try:
            edits = [EpisodeEdit.model_validate(item) for item in raw]
        except ValidationError as error:
            raise EpisodeEditError("EPISODE_FIELDS_INVALID") from error
        async with PersistenceUnitOfWork(
            runtime._require_sessions(), Scope(context.tenant_id, context.workspace_id)
        ) as uow:
            # Serialize with archive/configuration writes; scope always comes from the trusted session.
            row = await uow.session.scalar(
                select(ProjectRow)
                .where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                raise EpisodeEditError("PROJECT_NOT_FOUND", 404)
            if row.setup_state != "ready":
                raise EpisodeEditError("PROJECT_SETUP_REQUIRED", 409)
            # Start a real write transaction on SQLite before InboxRepository's
            # savepoint. Otherwise a released savepoint can survive an audit rollback.
            claimed_row = await uow.session.scalar(
                update(ProjectRow)
                .where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                    ProjectRow.version == row.version,
                )
                .values(version=ProjectRow.version)
                .returning(ProjectRow.id)
            )
            if claimed_row is None:
                raise EpisodeEditError("VERSION_CONFLICT", 409)
            claimed = await uow.inbox.claim(f"episodes.save:{project_id}", key)
            collection = await sources(uow.session, context, project_id)
            manager = get_project_manager()
            project = await asyncio.to_thread(manager.load_project, project_id)
            if not any(receipt["key"] == key for receipt in project.get("episode_edit_receipts", [])):
                current_snapshot = await asyncio.to_thread(
                    read_episodes, manager, project_id, source_texts=collection.texts
                )
                await validate_production_edits(uow.session, context, project_id, edits, current_snapshot)
            receipt = await asyncio.to_thread(
                save_episodes,
                manager,
                project_id,
                edits,
                revision=revision,
                key=key,
                actor=context.actor_id,
                replay_only=not claimed,
                source_texts=collection.texts,
            )
            if claimed:
                await uow.audit.append(
                    context.actor_id,
                    "episodes.updated",
                    "project",
                    project_id,
                    {
                        "requestId": context.request_id,
                        "before": receipt["before"],
                        "after": receipt["after"],
                        "episodes": receipt["episodes"],
                        "savedAt": receipt["at"],
                    },
                )
                await uow.outbox.add(
                    "episodes.updated",
                    "project",
                    project_id,
                    {
                        "revision": receipt["after"],
                        "episodes": receipt["episodes"],
                    },
                )
            await uow.commit()
        data = await snapshot(context, project_id)
        return {"data": data, "meta": {"requestId": context.request_id}}

    @router.get("/projects/{project_id}/episodes/costs")
    async def costs(request: Request, project_id: str):
        context = await runtime._context(request, "project.view")
        await ready(context, project_id)
        manager = get_project_manager()

        def load():
            project = manager.load_project(project_id)
            scripts = {}
            for entry in project.get("episodes", []):
                filename = entry.get("script_file")
                if filename:
                    try:
                        scripts[filename] = manager.load_script(project_id, filename)
                    except FileNotFoundError:
                        continue
            return project, scripts

        project, scripts = await asyncio.to_thread(load)
        service = CostEstimationService(ConfigResolver(async_session_factory), async_session_factory)
        data = await service.compute(project, scripts, project_name=project_id)
        return {"data": data, "meta": {"requestId": context.request_id}}


async def _body(request: Request) -> dict[str, Any]:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 2_000_000:
            raise EpisodeEditError("EPISODE_PAYLOAD_TOO_LARGE", 413)
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise EpisodeEditError("INVALID_JSON", 400) from error
    if not isinstance(body, dict):
        raise EpisodeEditError("INVALID_REQUEST_BODY", 400)
    return body


def _revision_header(request: Request) -> str:
    revision = request.headers.get("If-Match", "").strip('"')
    if len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
        raise EpisodeEditError("VERSION_REQUIRED", 428)
    return revision
