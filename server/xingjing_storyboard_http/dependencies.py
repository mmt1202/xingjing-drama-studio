# pyright: reportMissingImports=false
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request

from server.xingjing_generation_runtime import GenerationRuntime
from server.xingjing_identity_context import TrustedWorkspaceContext
from server.xingjing_storyboard.service import AccessContext, ShotDraft, StoryboardService
from server.xingjing_storyboard_persistence import SqlAlchemyPromptTemplateRepository, SqlAlchemyStoryboardRepository

TrustedContextResolver = Callable[[Request], TrustedWorkspaceContext | Awaitable[TrustedWorkspaceContext]]
ProjectScopeAuthorizer = Callable[[TrustedWorkspaceContext, str], bool | Awaitable[bool]]
SmartDraftGenerator = Callable[
    [AccessContext, str, str, str],
    tuple[ShotDraft, ...] | Awaitable[tuple[ShotDraft, ...]],
]


@dataclass(frozen=True, slots=True)
class StoryboardHttpDependencies:
    service: StoryboardService
    trusted_context_resolver: TrustedContextResolver | None
    project_scope_authorizer: ProjectScopeAuthorizer | None
    upload_store: object
    smart_draft_generator: SmartDraftGenerator | None = None
    prompt_repository: SqlAlchemyPromptTemplateRepository | None = None
    audit_repository: SqlAlchemyStoryboardRepository | None = None
    generation_runtime: GenerationRuntime | None = None


def create_storyboard_http_dependencies(
    *,
    service: StoryboardService,
    trusted_context_resolver: TrustedContextResolver | None = None,
    project_scope_authorizer: ProjectScopeAuthorizer | None = None,
    upload_store: object,
    smart_draft_generator: SmartDraftGenerator | None = None,
    prompt_repository: SqlAlchemyPromptTemplateRepository | None = None,
    audit_repository: SqlAlchemyStoryboardRepository | None = None,
    generation_runtime: GenerationRuntime | None = None,
) -> StoryboardHttpDependencies:
    return StoryboardHttpDependencies(
        service=service,
        trusted_context_resolver=trusted_context_resolver,
        project_scope_authorizer=project_scope_authorizer,
        upload_store=upload_store,
        smart_draft_generator=smart_draft_generator,
        prompt_repository=prompt_repository,
        audit_repository=audit_repository,
        generation_runtime=generation_runtime,
    )
