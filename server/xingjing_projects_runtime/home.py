"""Workspace landing data without opening a project or exposing its configuration."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal

from fastapi import APIRouter, Query, Request
from sqlalchemy import and_, func, or_, select

from lib.episode_editor import EpisodeEditError
from server.xingjing_generation_persistence.repository import TaskRow
from server.xingjing_platform_persistence.persistence import ProjectRow

if TYPE_CHECKING:
    from .runtime import ProjectsRuntime


def register_home_routes(runtime: ProjectsRuntime, router: APIRouter) -> None:
    async def context_for(request: Request, workspace_id: str, permission: str):
        context = await runtime._context(request, permission)
        if workspace_id != context.workspace_id:
            raise EpisodeEditError("WORKSPACE_NOT_FOUND", 404)
        return context

    @router.get("/workspaces/{workspace_id}/creator-context")
    async def creator_context(request: Request, workspace_id: str):
        context = await context_for(request, workspace_id, "workspace.view")
        return {
            "data": {
                "workspaceId": context.workspace_id,
                "actorId": context.actor_id,
                "permissions": sorted(context.permissions),
            },
            "meta": {"requestId": context.request_id},
        }

    @router.get("/workspaces/{workspace_id}/creator-home")
    async def creator_home(
        request: Request,
        workspace_id: str,
        q: str = Query("", max_length=255),
        days: int = 0,
        state: Literal["", "draft", "active", "review", "completed"] = "",
        projectType: Literal["", "drama", "comic", "brand", "adaptation"] = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(6, ge=1, le=24),
    ):
        from .runtime import _iso

        context = await context_for(request, workspace_id, "project.view")
        if days not in {0, 7, 30}:
            raise EpisodeEditError("INVALID_DATE_RANGE", 422)
        filters = [
            ProjectRow.tenant_id == context.tenant_id,
            ProjectRow.workspace_id == context.workspace_id,
            ProjectRow.deleted_at.is_(None),
        ]
        if q.strip():
            filters.append(
                or_(
                    ProjectRow.name.icontains(q.strip(), autoescape=True),
                    ProjectRow.id.icontains(q.strip(), autoescape=True),
                )
            )
        if days:
            filters.append(ProjectRow.updated_at >= datetime.now(UTC) - timedelta(days=days))
        if projectType:
            filters.append(ProjectRow.project_type == projectType)
        if state == "draft":
            filters.append(ProjectRow.setup_state == "draft")
        elif state == "active":
            filters.extend([ProjectRow.setup_state == "ready", ProjectRow.production_status != "completed"])
        elif state == "review":
            filters.append(ProjectRow.production_status.in_(("review", "compliance_check", "blocked")))
        elif state == "completed":
            filters.append(ProjectRow.production_status == "completed")
        async with runtime._require_sessions()() as session:
            total = await session.scalar(select(func.count()).select_from(ProjectRow).where(*filters))
            rows = (
                await session.scalars(
                    select(ProjectRow)
                    .where(*filters)
                    .order_by(ProjectRow.updated_at.desc(), ProjectRow.id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        return {
            "data": {
                "workspaceId": context.workspace_id,
                "items": [
                    {
                        "id": row.id,
                        "name": row.name,
                        "type": row.project_type,
                        "targetPlatform": row.target_platform,
                        "setupState": row.setup_state,
                        "productionStatus": row.production_status,
                        "version": row.version,
                        "updatedAt": _iso(row.updated_at),
                    }
                    for row in rows
                ],
                "total": total or 0,
                "offset": offset,
                "limit": limit,
            },
            "meta": {"requestId": context.request_id},
        }

    @router.get("/workspaces/{workspace_id}/generation-tasks")
    async def workspace_tasks(
        request: Request,
        workspace_id: str,
        q: str = Query("", max_length=255),
        state: Literal[
            "", "queued", "running", "retrying", "cancelling", "succeeded", "failed", "cancelled", "timeout"
        ] = "",
        days: int = 0,
        offset: int = Query(0, ge=0),
        limit: int = Query(8, ge=1, le=50),
    ):
        from .runtime import _iso

        context = await context_for(request, workspace_id, "generation.view")
        if days not in {0, 7, 30}:
            raise EpisodeEditError("INVALID_DATE_RANGE", 422)
        if "project.view" not in context.permissions:
            raise EpisodeEditError("PERMISSION_DENIED", 403)
        statement = (
            select(TaskRow, ProjectRow.name)
            .join(
                ProjectRow,
                and_(ProjectRow.id == TaskRow.project_id, ProjectRow.workspace_id == TaskRow.workspace_id),
            )
            .where(
                ProjectRow.tenant_id == context.tenant_id,
                ProjectRow.workspace_id == context.workspace_id,
                ProjectRow.deleted_at.is_(None),
                ProjectRow.setup_state == "ready",
            )
        )
        if state:
            statement = statement.where(TaskRow.status == state)
        if days:
            statement = statement.where(TaskRow.updated_at >= datetime.now(UTC) - timedelta(days=days))
        if q.strip():
            statement = statement.where(
                or_(
                    TaskRow.task_id.icontains(q.strip(), autoescape=True),
                    ProjectRow.name.icontains(q.strip(), autoescape=True),
                )
            )
        async with runtime._require_sessions()() as session:
            total = await session.scalar(select(func.count()).select_from(statement.subquery()))
            rows = (
                await session.execute(
                    statement.order_by(TaskRow.updated_at.desc(), TaskRow.project_id.desc(), TaskRow.task_id.desc())
                    .offset(offset)
                    .limit(limit)
                )
            ).all()
        return {
            "data": {
                "workspaceId": context.workspace_id,
                "items": [
                    {
                        "id": row.task_id,
                        "projectId": row.project_id,
                        "projectName": name,
                        "status": row.status,
                        "updatedAt": _iso(row.updated_at),
                        "version": row.version,
                    }
                    for row, name in rows
                ],
                "total": total or 0,
                "offset": offset,
                "limit": limit,
            },
            "meta": {"requestId": context.request_id},
        }
