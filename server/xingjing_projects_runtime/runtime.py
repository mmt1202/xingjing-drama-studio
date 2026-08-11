"""M02 project-management production composition.

PostgreSQL owns identity, scope, versions and archive state. The existing project
directory remains a media projection for legacy generation services.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable, Coroutine, Mapping
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from lib.project_manager import get_project_manager
from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver
from server.xingjing_platform_persistence import OptimisticConflict, PersistenceUnitOfWork, Scope
from server.xingjing_platform_persistence.persistence import Project, ProjectRow


class ProjectsRuntime:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] | None,
        resolver: TrustedWorkspaceContextResolver,
        *,
        engine: AsyncEngine | None = None,
        unavailable_code: str | None = None,
    ) -> None:
        self._sessions = session_factory
        self._resolver = resolver
        self._engine = engine
        self._unavailable_code = unavailable_code
        self.router = self._build_router()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    def _build_router(self) -> APIRouter:
        router = APIRouter(route_class=_ProjectsRoute)

        @router.get("/projects")
        async def list_projects(request: Request, archived: bool = False, pageToken: str | None = None):
            context = await self._context(request, "project.view")
            sessions = self._require_sessions()
            async with sessions() as session:
                statement = (
                    select(ProjectRow)
                    .where(
                        ProjectRow.tenant_id == context.tenant_id,
                        ProjectRow.workspace_id == context.workspace_id,
                        ProjectRow.deleted_at.is_not(None) if archived else ProjectRow.deleted_at.is_(None),
                    )
                    .order_by(ProjectRow.updated_at.desc(), ProjectRow.id.desc())
                    .limit(201)
                )
                rows = list((await session.scalars(statement)).all())
            if pageToken:
                try:
                    offset = max(0, int(pageToken))
                except ValueError:
                    return _error(context.request_id, "INVALID_PAGE_TOKEN", status.HTTP_400_BAD_REQUEST)
            else:
                offset = 0
            visible = rows[offset : offset + 200]
            data = [_summary(row) for row in visible]
            next_token = str(offset + len(visible)) if offset + len(visible) < len(rows) else None
            # ``projects`` keeps the original ArcReel client operational while
            # ``data/meta`` is the formal M02 contract used by Xingjing pages.
            return {
                "data": data,
                "projects": [_legacy_summary(row) for row in visible],
                "meta": {"requestId": context.request_id, "total": len(rows), "page": {"nextToken": next_token}},
            }

        @router.get("/projects/{project_id}")
        async def get_project(request: Request, project_id: str):
            context = await self._context(request, "project.view")
            row = await self._row(context.tenant_id, context.workspace_id, project_id, include_deleted=True)
            if row is None:
                return _error(context.request_id, "PROJECT_NOT_FOUND", status.HTTP_404_NOT_FOUND)
            data = _summary(row)
            legacy_project: dict[str, object] = {"title": row.name, "episodes": []}
            manager = get_project_manager()
            if await asyncio.to_thread(manager.project_exists, row.id):
                legacy_project = await asyncio.to_thread(manager.load_project, row.id)
            return {
                "data": {**data, "episodes": _episodes(legacy_project)},
                "project": legacy_project,
                "scripts": {},
                "asset_fingerprints": {},
                "meta": {"requestId": context.request_id},
            }

        @router.post("/projects/{project_id}/actions")
        async def project_action(request: Request, project_id: str):
            context = await self._context(request, "project.manage")
            body = await _json(request)
            if isinstance(body, JSONResponse):
                return body
            action = _text(body.get("action"))
            payload = body.get("payload")
            if not isinstance(payload, Mapping):
                return _error(context.request_id, "INVALID_ACTION_PAYLOAD", status.HTTP_400_BAD_REQUEST)
            key = request.headers.get("Idempotency-Key", "").strip()
            if not key:
                return _error(context.request_id, "IDEMPOTENCY_KEY_REQUIRED", status.HTTP_400_BAD_REQUEST)
            try:
                if action == "create":
                    project = await self._create(context, key, payload)
                else:
                    expected = _if_match(request)
                    if expected is None:
                        return _error(context.request_id, "VERSION_REQUIRED", status.HTTP_428_PRECONDITION_REQUIRED)
                    project = await self._mutate(context, project_id, action, payload, expected, key)
            except OptimisticConflict:
                return _error(context.request_id, "VERSION_CONFLICT", status.HTTP_409_CONFLICT)
            except FileExistsError:
                return _error(context.request_id, "PROJECT_PROJECTION_CONFLICT", status.HTTP_409_CONFLICT)
            except (SQLAlchemyError, OSError):
                return _error(context.request_id, "PROJECT_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except ValueError as error:
                return _error(context.request_id, str(error) or "INVALID_PROJECT_ACTION", status.HTTP_400_BAD_REQUEST)
            return {"data": _project(project), "meta": {"requestId": context.request_id}}

        return router

    async def _context(self, request: Request, permission: str):
        if self._unavailable_code:
            raise _RuntimeUnavailable(self._unavailable_code)
        context = await self._resolver(request)
        if permission not in context.permissions:
            raise _PermissionDenied(permission)
        return context

    def _require_sessions(self) -> async_sessionmaker[AsyncSession]:
        if self._sessions is None:
            raise _RuntimeUnavailable(self._unavailable_code or "PROJECT_RUNTIME_UNAVAILABLE")
        return self._sessions

    async def _row(self, tenant_id: str, workspace_id: str, project_id: str, *, include_deleted: bool):
        sessions = self._require_sessions()
        clauses = [
            ProjectRow.tenant_id == tenant_id,
            ProjectRow.workspace_id == workspace_id,
            ProjectRow.id == project_id,
        ]
        if not include_deleted:
            clauses.append(ProjectRow.deleted_at.is_(None))
        async with sessions() as session:
            return await session.scalar(select(ProjectRow).where(*clauses))

    async def _create(self, context, key: str, payload: Mapping[str, object]) -> Project:
        name = _text(payload.get("name"))
        if not name:
            raise ValueError("PROJECT_NAME_REQUIRED")
        scope = Scope(context.tenant_id, context.workspace_id)
        sessions = self._require_sessions()
        created_projection = False
        async with PersistenceUnitOfWork(sessions, scope) as uow:
            project = await uow.projects.create(
                key,
                name,
                project_type=_text(payload.get("type")) or "drama",
                target_platform=_text(payload.get("targetPlatform")) or "web",
                owner_id=context.actor_id,
            )
            if project.name != name:
                raise ValueError("IDEMPOTENCY_CONFLICT")
            manager = get_project_manager()
            if not await asyncio.to_thread(manager.project_exists, project.id):
                await asyncio.to_thread(manager.create_project, project.id, content_mode="narration")
                created_projection = True
                await asyncio.to_thread(manager.create_project_metadata, project.id, name, "", "narration")
            await uow.audit.append(context.actor_id, "project.created", "project", project.id, {"requestId": context.request_id})
            await uow.outbox.add("project.created", "project", project.id, {"name": name, "ownerId": context.actor_id})
            try:
                await uow.commit()
            except Exception:
                if created_projection:
                    await asyncio.to_thread(manager.delete_project_directory, project.id)
                raise
        return project

    async def _mutate(
        self,
        context,
        project_id: str,
        action: str,
        payload: Mapping[str, object],
        expected: int,
        key: str,
    ) -> Project:
        del key  # version CAS is the idempotency boundary for mutations
        scope = Scope(context.tenant_id, context.workspace_id)
        async with PersistenceUnitOfWork(self._require_sessions(), scope) as uow:
            current = await uow.projects.get(project_id, include_deleted=True)
            if current is None:
                raise ValueError("PROJECT_NOT_FOUND")
            if action == "update":
                name = _text(payload.get("name"))
                if not name:
                    raise ValueError("PROJECT_NAME_REQUIRED")
                project = await uow.projects.rename(project_id, name, expected_version=expected)
                manager = get_project_manager()
                if await asyncio.to_thread(manager.project_exists, project_id):
                    await asyncio.to_thread(
                        manager.update_project,
                        project_id,
                        lambda value: value.__setitem__("title", name),
                    )
            elif action == "archive":
                project = await uow.projects.soft_delete(project_id, expected_version=expected)
            elif action == "restore":
                project = await uow.projects.restore(project_id, expected_version=expected)
            elif action in {"advance", "rollback"}:
                target = _text(payload.get("productionStatus"))
                if not target:
                    raise ValueError("PRODUCTION_STATUS_REQUIRED")
                project = await uow.projects.set_production_status(project_id, target, expected_version=expected)
            else:
                raise ValueError("UNSUPPORTED_PROJECT_ACTION")
            await uow.audit.append(
                context.actor_id,
                f"project.{action}",
                "project",
                project_id,
                {"requestId": context.request_id, "version": project.version},
            )
            await uow.outbox.add(
                f"project.{action}",
                "project",
                project_id,
                {"version": project.version, "productionStatus": project.production_status},
            )
            await uow.commit()
        return project


class _RuntimeUnavailable(RuntimeError):
    pass


class _PermissionDenied(RuntimeError):
    pass


class _ProjectsRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except _RuntimeUnavailable as error:
                return _error(request.headers.get("X-Request-Id", ""), str(error), status.HTTP_503_SERVICE_UNAVAILABLE)
            except _PermissionDenied as error:
                return _error(
                    request.headers.get("X-Request-Id", ""),
                    "PERMISSION_DENIED",
                    status.HTTP_403_FORBIDDEN,
                    {"permission": str(error)},
                )
            except HTTPException as error:
                detail = error.detail if isinstance(error.detail, Mapping) else {}
                raw_code = detail.get("code")
                code = raw_code if isinstance(raw_code, str) else "REQUEST_REJECTED"
                return _error(request.headers.get("X-Request-Id", ""), code, error.status_code)
            except SQLAlchemyError:
                return _error(
                    request.headers.get("X-Request-Id", ""),
                    "PROJECT_RUNTIME_UNAVAILABLE",
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                )

        return handler


def create_production_projects_runtime(*, database_url: str | None = None) -> ProjectsRuntime:
    resolver = TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = (database_url or os.environ.get("XINGJING_PROJECT_DATABASE_URL", "")).strip()
    if not url:
        return ProjectsRuntime(None, resolver, unavailable_code="XINGJING_PROJECT_DATABASE_URL_REQUIRED")
    if "+asyncpg" not in url and "+aiosqlite" not in url:
        return ProjectsRuntime(None, resolver, unavailable_code="XINGJING_PROJECT_DATABASE_URL_MUST_BE_ASYNC")
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return ProjectsRuntime(None, resolver, unavailable_code="XINGJING_PROJECT_DATABASE_UNAVAILABLE")
    return ProjectsRuntime(async_sessionmaker(engine, expire_on_commit=False), resolver, engine=engine)


async def _json(request: Request) -> Mapping[str, object] | JSONResponse:
    try:
        body = await request.json()
    except ValueError:
        return _error(request.headers.get("X-Request-Id", ""), "INVALID_JSON", status.HTTP_400_BAD_REQUEST)
    if not isinstance(body, Mapping):
        return _error(request.headers.get("X-Request-Id", ""), "INVALID_REQUEST_BODY", status.HTTP_400_BAD_REQUEST)
    return cast(Mapping[str, object], body)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _if_match(request: Request) -> int | None:
    raw = request.headers.get("If-Match", "").strip().strip('"')
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def _summary(row: ProjectRow) -> dict[str, object]:
    return {
        "id": row.id,
        "name": row.name,
        "type": row.project_type,
        "targetPlatform": row.target_platform,
        "ownerName": row.owner_id,
        "productionStatus": row.production_status,
        "archived": row.deleted_at is not None,
        "version": row.version,
        "updatedAt": _iso(row.updated_at),
    }


def _project(project: Project) -> dict[str, object]:
    return {
        "id": project.id,
        "name": project.name,
        "type": project.project_type,
        "targetPlatform": project.target_platform,
        "ownerName": project.owner_id,
        "productionStatus": project.production_status,
        "archived": project.deleted_at is not None,
        "version": project.version,
        "updatedAt": _iso(project.created_at),
        "episodes": [],
    }


def _legacy_summary(row: ProjectRow) -> dict[str, object]:
    return {"name": row.id, "title": row.name, "style": "", "thumbnail": None, "status": {}}


def _episodes(project: Mapping[str, object]) -> list[dict[str, object]]:
    values = project.get("episodes")
    if not isinstance(values, list):
        return []
    result: list[dict[str, object]] = []
    for index, value in enumerate(values):
        if isinstance(value, Mapping):
            result.append(
                {
                    "id": str(value.get("episode", index + 1)),
                    "title": str(value.get("title", "")),
                    "status": str(value.get("status", "draft")),
                }
            )
    return result


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _error(
    request_id: str, code: str, status_code: int, details: Mapping[str, object] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": code,
                "retryable": status_code >= 500,
                "details": dict(details or {}),
            },
            "meta": {"requestId": request_id},
        },
    )
