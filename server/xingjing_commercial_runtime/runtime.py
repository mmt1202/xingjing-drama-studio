"""将 M14 领域、持久化与 HTTP 适配显式装配为可挂载运行时。"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from server.xingjing_commercial import AccountingPort, Actor, CommercialService, DeliveryArtifactPort
from server.xingjing_commercial_http import (
    CommercialOrderRef,
    create_commercial_dependencies,
    create_commercial_router,
)
from server.xingjing_commercial_persistence import (
    CommercialOrderRow,
    SqlAlchemyCommercialAccountingPort,
    SqlAlchemyCommercialRepository,
    SqlAlchemyDeliveryArtifactPort,
)
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)

type SessionFactory = Callable[[], Session]
type ActorProvider = Callable[..., Actor | Awaitable[Actor]]
type TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


class CommercialRuntimeConfigurationError(RuntimeError):
    """生产装配缺失权威依赖时的失败关闭错误。"""


class TrustedCommercialActorProvider:
    """Builds an M14 actor only from the selected Java workspace session."""

    def __init__(self, resolver: TrustedContextResolver) -> None:
        self._resolver = resolver

    async def __call__(self, request: Request) -> Actor:
        resolved = self._resolver(request)
        context = await resolved if inspect.isawaitable(resolved) else resolved
        permissions = set(context.permissions)
        if "admin.commercial.view" in permissions or "admin.commercial.manage" in permissions:
            return Actor.admin(context.actor_id, permissions, {context.workspace_id})
        return Actor.member(context.actor_id, context.workspace_id, permissions)


def create_unavailable_commercial_router(code: str = "COMMERCIAL_RUNTIME_NOT_CONFIGURED") -> APIRouter:
    """Expose only stable 503s until PostgreSQL deployment prerequisites exist."""

    router = APIRouter(tags=["commercial-orders"])

    async def unavailable(request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"error": {"code": code}, "meta": {"requestId": request.headers.get("X-Request-Id", "unknown")}},
        )

    for path in ("/commercial-orders{suffix:path}", "/admin/commercial-orders{suffix:path}"):
        router.add_api_route(path, unavailable, methods=["GET", "POST", "PUT", "PATCH", "DELETE"], include_in_schema=False)
    return router


class SqlAlchemyCommercialOrderIndex:
    """通过商单持久化表发现聚合，且不绕过领域授权。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list_order_refs(self) -> Sequence[CommercialOrderRef]:
        session = self._session_factory()
        try:
            rows = session.execute(
                select(CommercialOrderRow.owner_workspace_id, CommercialOrderRow.id).order_by(
                    CommercialOrderRow.updated_at.desc(), CommercialOrderRow.id.desc()
                )
            ).all()
            return tuple(CommercialOrderRef(owner_workspace_id=row[0], order_id=row[1]) for row in rows)
        finally:
            session.close()

    def find_order_ref(self, order_id: str) -> CommercialOrderRef | None:
        session = self._session_factory()
        try:
            row = session.execute(
                select(CommercialOrderRow.owner_workspace_id, CommercialOrderRow.id)
                .where(CommercialOrderRow.id == order_id)
                .order_by(CommercialOrderRow.updated_at.desc())
                .limit(1)
            ).one_or_none()
            return None if row is None else CommercialOrderRef(owner_workspace_id=row[0], order_id=row[1])
        finally:
            session.close()


@dataclass(frozen=True, slots=True)
class CommercialRuntime:
    """M14 的可挂载组件；调用方负责将 ``router`` 接入主应用。"""

    router: APIRouter
    accounting_port: AccountingPort
    delivery_artifact_port: DeliveryArtifactPort
    actor: ActorProvider
    _service: CommercialService
    _order_index: SqlAlchemyCommercialOrderIndex
    engine: Engine | None = None

    def service(self) -> CommercialService:
        return self._service

    def order_index(self) -> SqlAlchemyCommercialOrderIndex:
        return self._order_index

    async def close(self) -> None:
        if self.engine is not None:
            await asyncio.to_thread(self.engine.dispose)


def create_commercial_runtime(
    *,
    session_factory: SessionFactory | None,
    accounting_port: AccountingPort | None,
    delivery_artifact_port: DeliveryArtifactPort | None,
    actor_provider: ActorProvider | None,
    now: Callable[[], datetime] | None = None,
) -> CommercialRuntime:
    """装配商单运行时，且绝不为身份、账务或持久化提供内存回退。"""

    if session_factory is None:
        raise CommercialRuntimeConfigurationError("session_factory must be configured")
    if accounting_port is None:
        raise CommercialRuntimeConfigurationError("accounting_port must be configured")
    if actor_provider is None:
        raise CommercialRuntimeConfigurationError("actor_provider must be configured")
    if delivery_artifact_port is None:
        raise CommercialRuntimeConfigurationError("delivery_artifact_port must be configured")

    repository = SqlAlchemyCommercialRepository(session_factory)
    service = CommercialService(
        repository,
        accounting_port,
        delivery_artifacts=delivery_artifact_port,
        now=now,
    )
    order_index = SqlAlchemyCommercialOrderIndex(session_factory)
    dependencies = create_commercial_dependencies(
        service=lambda: service,
        actor=actor_provider,
        order_index=lambda: order_index,
    )
    return CommercialRuntime(
        router=create_commercial_router(dependencies),
        accounting_port=accounting_port,
        delivery_artifact_port=delivery_artifact_port,
        actor=actor_provider,
        _service=service,
        _order_index=order_index,
    )


def create_production_commercial_runtime(
    *,
    database_url: str | None = None,
    context_resolver: TrustedContextResolver | None = None,
) -> CommercialRuntime:
    """Compose M14 only with PostgreSQL and a trusted request-scoped identity.

    Financial actions and deliverables are both checked against authoritative
    PostgreSQL records inside the same commercial transaction.
    """

    url = (database_url or os.environ.get("XINGJING_COMMERCIAL_DATABASE_URL", "")).strip()
    if not url:
        raise CommercialRuntimeConfigurationError("XINGJING_COMMERCIAL_DATABASE_URL_REQUIRED")
    try:
        parsed = make_url(url)
    except (ArgumentError, ValueError) as error:
        raise CommercialRuntimeConfigurationError("XINGJING_COMMERCIAL_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC") from error
    if parsed.drivername not in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
        raise CommercialRuntimeConfigurationError("XINGJING_COMMERCIAL_DATABASE_URL_MUST_BE_POSTGRESQL_SYNC")
    try:
        engine = create_engine(url, pool_pre_ping=True)
    except (ModuleNotFoundError, SQLAlchemyError, ValueError) as error:
        raise CommercialRuntimeConfigurationError("COMMERCIAL_RUNTIME_DATABASE_UNAVAILABLE") from error
    runtime = create_commercial_runtime(
        session_factory=sessionmaker(engine, expire_on_commit=False),
        accounting_port=SqlAlchemyCommercialAccountingPort(),
        delivery_artifact_port=SqlAlchemyDeliveryArtifactPort(),
        actor_provider=TrustedCommercialActorProvider(
            context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
        ),
    )
    return CommercialRuntime(
        router=runtime.router,
        accounting_port=runtime.accounting_port,
        delivery_artifact_port=runtime.delivery_artifact_port,
        actor=runtime.actor,
        _service=runtime.service(),
        _order_index=runtime.order_index(),
        engine=engine,
    )
