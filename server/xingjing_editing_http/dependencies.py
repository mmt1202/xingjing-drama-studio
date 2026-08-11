from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from server.xingjing_editing import AccessContext, RenderService, TimelineService
from server.xingjing_editing.ports import AuditReader, RenderRepository

AccessContextResolver = Callable[[Request], AccessContext | Awaitable[AccessContext]]
ProjectScopeAuthorizer = Callable[[Request, str], bool | Awaitable[bool]]
RenderInputValidator = Callable[[Request, str, str, str], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EditingDependencies:
    timeline_service: TimelineService
    render_service: RenderService | None
    render_repository: RenderRepository | None
    audit_reader: AuditReader | None
    access_context_resolver: AccessContextResolver
    callback_context_resolver: AccessContextResolver
    project_scope_authorizer: ProjectScopeAuthorizer | None = None
    render_input_validator: RenderInputValidator | None = None

    async def access_context(self, request: Request) -> AccessContext:
        context = self.access_context_resolver(request)
        if inspect.isawaitable(context):
            return await context
        return context

    async def callback_context(self, request: Request) -> AccessContext:
        context = self.callback_context_resolver(request)
        if inspect.isawaitable(context):
            return await context
        return context

    def require_render_service(self) -> RenderService:
        if self.render_service is None:
            raise RuntimeError("EDITING_RENDER_SERVICE_NOT_CONFIGURED")
        return self.render_service

    def require_render_repository(self) -> RenderRepository:
        if self.render_repository is None:
            raise RuntimeError("EDITING_RENDER_REPOSITORY_NOT_CONFIGURED")
        return self.render_repository

    def require_audit_reader(self) -> AuditReader:
        if self.audit_reader is None:
            raise RuntimeError("EDITING_AUDIT_READER_NOT_CONFIGURED")
        return self.audit_reader

    async def authorize_project(self, request: Request, project_id: str) -> None:
        if self.project_scope_authorizer is None:
            raise RuntimeError("EDITING_PROJECT_SCOPE_AUTHORIZER_NOT_CONFIGURED")
        allowed = self.project_scope_authorizer(request, project_id)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if not allowed:
            raise LookupError("PROJECT_NOT_FOUND")

    async def validate_render_input(
        self, request: Request, project_id: str, timeline_id: str, timeline_version_id: str
    ) -> None:
        if self.render_input_validator is None:
            raise RuntimeError("EDITING_RENDER_INPUT_VALIDATOR_NOT_CONFIGURED")
        result = self.render_input_validator(request, project_id, timeline_id, timeline_version_id)
        if inspect.isawaitable(result):
            await result


def create_editing_dependencies(
    *,
    timeline_service: TimelineService,
    render_service: RenderService | None = None,
    render_repository: RenderRepository | None = None,
    audit_reader: AuditReader | None = None,
    access_context_resolver: AccessContextResolver,
    callback_context_resolver: AccessContextResolver | None = None,
    project_scope_authorizer: ProjectScopeAuthorizer | None = None,
    render_input_validator: RenderInputValidator | None = None,
) -> EditingDependencies:
    return EditingDependencies(
        timeline_service=timeline_service,
        render_service=render_service,
        render_repository=render_repository,
        audit_reader=audit_reader,
        access_context_resolver=access_context_resolver,
        callback_context_resolver=callback_context_resolver or access_context_resolver,
        project_scope_authorizer=project_scope_authorizer,
        render_input_validator=render_input_validator,
    )


__all__ = [
    "AccessContextResolver",
    "EditingDependencies",
    "ProjectScopeAuthorizer",
    "RenderInputValidator",
    "create_editing_dependencies",
]
