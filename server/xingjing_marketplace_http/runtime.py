from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from fastapi import Request

from lib.db import get_database_url
from server.xingjing_identity_context import (
    PlatformSessionGateway,
    TrustedWorkspaceContext,
    TrustedWorkspaceContextResolver,
)
from server.xingjing_marketplace import MarketplaceService, RequestContext
from server.xingjing_marketplace_persistence import SqlAlchemyMarketplaceRepository

from .router import ContextResolver, create_dependencies, create_router

TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]


class MarketplaceRuntime:
    """Owns M13 service composition, keeping router mounting free of untrusted headers."""

    def __init__(
        self,
        service: MarketplaceService,
        *,
        context_resolver: TrustedContextResolver,
        close: Callable[[], None] | None = None,
    ) -> None:
        self._service = service
        self._trusted_context_resolver = context_resolver
        self._close = close

    async def context(self, request: Request) -> RequestContext:
        trusted = self._trusted_context_resolver(request)
        if inspect.isawaitable(trusted):
            trusted = await trusted
        return RequestContext(
            actor_id=trusted.actor_id,
            workspace_id=trusted.workspace_id,
            permissions=trusted.permissions,
            request_id=trusted.request_id,
            tenant_id=trusted.tenant_id,
        )

    def router(self):
        resolver: ContextResolver = self.context
        return create_router(create_dependencies(self._service, context_resolver=resolver))

    def close(self) -> None:
        if self._close is not None:
            self._close()
            self._close = None


def create_production_marketplace_runtime() -> MarketplaceRuntime:
    repository = SqlAlchemyMarketplaceRepository.from_url(get_database_url())
    return MarketplaceRuntime(
        MarketplaceService(repository),
        context_resolver=TrustedWorkspaceContextResolver(PlatformSessionGateway()),
        close=repository.close,
    )
