"""M03 内容理解的生产运行时组合。

此模块故意不挂载 FastAPI，也不创建 metadata。主应用负责在生命周期已完成
迁移后调用 ``runtime.router()`` 并按其自身的路由前缀注册。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

from fastapi import APIRouter, Request
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_content import ContentService, FileContentRepository
from server.xingjing_content.ai import ContentAiService
from server.xingjing_content_http import (
    ContentHttpDependencies,
    ContentRuntimeUnavailable,
    create_content_router,
)
from server.xingjing_content_persistence import (
    AuditContext,
    SqlAlchemyContentAiRequestRepository,
    SqlAlchemyContentRepository,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_platform_persistence.persistence import ProjectRow

TrustedContextResolver = Callable[[Request], Awaitable[TrustedWorkspaceContext]]
SessionFactory = Callable[[], Session]


class ContentRuntime:
    """持有 M03 HTTP、SQLAlchemy 仓储及可信项目范围校验的组合根。

    M03 的 ``ScriptDocument`` 尚无 ``tenant_id``。因此本运行时不伪造该字段，
    而是将可信会话的 ``tenant_id/workspace_id`` 同时与 S03 项目记录匹配，再将
    已验证的 ``workspace_id`` 传给 M03 仓储。若 S03 映射或迁移不存在，请求会
    以稳定的 503 失败，不能降级为文件或内存数据。
    """

    def __init__(self, dependencies: ContentHttpDependencies, *, close: Callable[[], None] | None = None) -> None:
        self._dependencies = dependencies
        self._router = create_content_router(dependencies)
        self._close = close

    @property
    def available(self) -> bool:
        return self._dependencies.unavailable_code is None

    def router(self) -> APIRouter:
        """返回可由主应用直接 include_router 的 M03 路由。"""
        return self._router

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None


class SqlAlchemyProjectScopeAuthorizer:
    """以 S03 数据库项目记录验证可信工作区可访问的项目范围。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    async def __call__(self, context: TrustedWorkspaceContext, project_id: str) -> bool:
        try:
            return await asyncio.to_thread(self._exists, context, project_id)
        except SQLAlchemyError as error:
            # 未迁移、失连或驱动故障都不能表现为一个不存在的项目，更不能放行。
            raise ContentRuntimeUnavailable("CONTENT_RUNTIME_UNAVAILABLE") from error

    def _exists(self, context: TrustedWorkspaceContext, project_id: str) -> bool:
        with self._session_factory() as session:
            return (
                session.scalar(
                    select(ProjectRow.id).where(
                        ProjectRow.id == project_id,
                        ProjectRow.tenant_id == context.tenant_id,
                        ProjectRow.workspace_id == context.workspace_id,
                        ProjectRow.deleted_at.is_(None),
                    )
                )
                is not None
            )


def create_production_content_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
) -> ContentRuntime:
    """创建失败关闭的 M03 生产组合。

    ``XINGJING_CONTENT_DATABASE_URL`` 必须是同步 SQLAlchemy URL。运行时仅使用
    已迁移的 M03/S03 表；不会调用 ``create_all``，也不会回退到
    ``FileContentRepository`` 或内存实现。
    """
    url = (database_url or os.environ.get("XINGJING_CONTENT_DATABASE_URL", "")).strip()
    if not url:
        return _unavailable_runtime("XINGJING_CONTENT_DATABASE_URL_REQUIRED")
    if "+aiosqlite" in url or "+asyncpg" in url:
        return _unavailable_runtime("XINGJING_CONTENT_DATABASE_URL_MUST_BE_SYNC")

    try:
        engine = create_engine(url, pool_pre_ping=True)
    except (SQLAlchemyError, ValueError, ModuleNotFoundError):
        return _unavailable_runtime("XINGJING_CONTENT_DATABASE_UNAVAILABLE")

    factory: SessionFactory = sessionmaker(engine, expire_on_commit=False)
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    authorizer = SqlAlchemyProjectScopeAuthorizer(factory)

    def service_for(context: TrustedWorkspaceContext) -> ContentService:
        repository = SqlAlchemyContentRepository(
            factory,
            audit_context=AuditContext(
                request_id=context.request_id,
                actor_id=context.actor_id,
                occurred_at=datetime.now(UTC),
            ),
        )
        # ContentService 的领域端口尚沿用 FileContentRepository 标注；真实实现
        # 遵守同一 transact/read_all 协议，此处仅收窄静态类型，绝不实例化文件仓储。
        return ContentService(cast(FileContentRepository, repository))

    def ai_service_for(context: TrustedWorkspaceContext, project_id: str) -> ContentAiService:
        with factory() as session:
            project = session.scalar(
                select(ProjectRow).where(
                    ProjectRow.id == project_id,
                    ProjectRow.tenant_id == context.tenant_id,
                    ProjectRow.workspace_id == context.workspace_id,
                    ProjectRow.deleted_at.is_(None),
                )
            )
            if project is None:
                raise ContentRuntimeUnavailable("PROJECT_NOT_FOUND")
            project_name = project.name
        return ContentAiService(
            SqlAlchemyContentAiRequestRepository(factory),
            tenant_id=context.tenant_id,
            project_name=project_name,
        )

    return ContentRuntime(
        ContentHttpDependencies(
            service=None,
            service_factory=service_for,
            ai_service_factory=ai_service_for,
            trusted_context_resolver=resolver,
            project_scope_authorizer=authorizer,
        ),
        close=engine.dispose,
    )


def _unavailable_runtime(code: str) -> ContentRuntime:
    async def unavailable_context(_: Request) -> TrustedWorkspaceContext:
        raise ContentRuntimeUnavailable(code)

    async def unavailable_project_scope(_: TrustedWorkspaceContext, __: str) -> bool:
        raise ContentRuntimeUnavailable(code)

    return ContentRuntime(
        ContentHttpDependencies(
            service=None,
            trusted_context_resolver=unavailable_context,
            project_scope_authorizer=unavailable_project_scope,
            unavailable_code=code,
        )
    )
