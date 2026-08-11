# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from server.xingjing_content.ai import ContentAiService
from server.xingjing_content.service import ContentService
from server.xingjing_identity_context import TrustedWorkspaceContext

TrustedContextResolver = Callable[[Request], Awaitable[TrustedWorkspaceContext]]
ProjectScopeAuthorizer = Callable[[TrustedWorkspaceContext, str], Awaitable[bool]]
ContentServiceFactory = Callable[[TrustedWorkspaceContext], ContentService]
ContentAiServiceFactory = Callable[[TrustedWorkspaceContext, str], ContentAiService]


class ContentRuntimeUnavailable(RuntimeError):
    """生产组合未就绪时由 HTTP 契约转换为稳定的 503。"""

    def __init__(self, code: str = "CONTENT_RUNTIME_UNAVAILABLE") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ContentHttpDependencies:
    """M03 HTTP 的显式组合根；生产必须注入真实内容仓储服务和可信上下文。"""

    service: ContentService | None
    trusted_context_resolver: TrustedContextResolver
    project_scope_authorizer: ProjectScopeAuthorizer
    service_factory: ContentServiceFactory | None = None
    ai_service_factory: ContentAiServiceFactory | None = None
    unavailable_code: str | None = None

    def service_for(self, context: TrustedWorkspaceContext) -> ContentService:
        if self.unavailable_code is not None:
            raise ContentRuntimeUnavailable(self.unavailable_code)
        if self.service_factory is not None:
            return self.service_factory(context)
        if self.service is not None:
            return self.service
        raise ContentRuntimeUnavailable()

    def ai_service_for(self, context: TrustedWorkspaceContext, project_id: str) -> ContentAiService:
        if self.ai_service_factory is None:
            raise ContentRuntimeUnavailable("CONTENT_AI_RUNTIME_UNAVAILABLE")
        return self.ai_service_factory(context, project_id)
