from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from server.xingjing_marketplace import (
    ForkNotAllowed,
    ForkNotFound,
    ForkProjectNotFound,
    IdempotencyConflict,
    InvalidInput,
    InvalidTransition,
    MarketItemNotFound,
    MarketplaceError,
    MarketplaceService,
    PermissionDenied,
    RequestContext,
    RevenueShareInput,
    ReviewNotFound,
    RightsPolicyInput,
    TemplateKind,
    TemplateNotFound,
    VersionConflict,
)

T = TypeVar("T")


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _RightsPayload(_Payload):
    scope: str
    commercial_use: bool
    attribution_required: bool
    inheritable_scopes: list[str]
    allowed_workspace_ids: list[str] = Field(default_factory=list)
    allow_fork: bool = True


class _RevenueSharePayload(_Payload):
    beneficiary_role: str
    basis_points: int


class _CreateTemplatePayload(_Payload):
    title: str
    kind: str
    content: dict[str, object]
    tags: list[str]
    rights: _RightsPayload
    revenue_shares: list[_RevenueSharePayload]
    price_minor: int = 0


class _VersionPayload(_Payload):
    content: dict[str, object]
    rights: _RightsPayload
    revenue_shares: list[_RevenueSharePayload]
    price_minor: int = 0


class _StatementPayload(_Payload):
    statement: str


class _DecisionPayload(_Payload):
    decision: str
    reason: str


class _ReasonPayload(_Payload):
    reason: str


class _ForkPayload(_Payload):
    market_item_id: str
    expected_market_revision: int
    expected_source_version_id: str
    project_name: str
    intended_commercial_use: bool
    requested_inheritable_scopes: list[str]


class _CommunityPayload(_Payload):
    fork_id: str
    title: str
    tags: list[str]
    rights: _RightsPayload
    revenue_shares: list[_RevenueSharePayload]
    price_minor: int


class _AdminActionPayload(_Payload):
    action: str
    review_id: str | None = None
    template_id: str | None = None
    purchase_id: str | None = None
    decision: str | None = None
    reason: str
    version: int


@dataclass(frozen=True, slots=True)
class MarketplaceDependencies:
    service: MarketplaceService
    context_resolver: ContextResolver


ContextResolver = Callable[[Request], RequestContext | Awaitable[RequestContext]]


def create_dependencies(
    service: MarketplaceService,
    *,
    context_resolver: ContextResolver,
) -> MarketplaceDependencies:
    """Create the dependency bundle the host application can keep for its lifespan."""

    return MarketplaceDependencies(service, context_resolver)


def create_router(dependencies: MarketplaceDependencies) -> APIRouter:
    """Build the M13 HTTP adapter; the application owns mounting it under /api/v1."""

    router = APIRouter(prefix="/api/v1", tags=["xingjing-marketplace"])

    async def context_from_identity(request: Request) -> RequestContext:
        context = dependencies.context_resolver(request)
        if inspect.isawaitable(context):
            return await context
        return context

    def idempotency_key(value: Annotated[str, Header(alias="Idempotency-Key")] = "") -> str:
        return value

    def invoke(operation: Callable[[], T]) -> JSONResponse:
        try:
            result = operation()
        except ValueError as error:
            return _error_response(InvalidInput(str(error)))
        except MarketplaceError as error:
            return _error_response(error)
        return JSONResponse({"data": _to_dict(result)})

    @router.post("/templates", status_code=201)
    def create_template(
        payload: _CreateTemplatePayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
    ) -> JSONResponse:
        from server.xingjing_marketplace import CreateTemplate, RevenueShareInput, RightsPolicyInput, RightsScope

        def operation() -> object:
            return dependencies.service.create_template(
                context,
                CreateTemplate(
                    title=payload.title,
                    kind=TemplateKind(payload.kind),
                    content=payload.content,
                    tags=tuple(payload.tags),
                    rights=RightsPolicyInput(
                        scope=RightsScope(payload.rights.scope),
                        commercial_use=payload.rights.commercial_use,
                        attribution_required=payload.rights.attribution_required,
                        inheritable_scopes=tuple(payload.rights.inheritable_scopes),
                        allowed_workspace_ids=tuple(payload.rights.allowed_workspace_ids),
                        allow_fork=payload.rights.allow_fork,
                    ),
                    revenue_shares=tuple(
                        RevenueShareInput(item.beneficiary_role, item.basis_points) for item in payload.revenue_shares
                    ),
                    idempotency_key=key,
                    price_minor=payload.price_minor,
                ),
            )

        response = invoke(operation)
        response.status_code = 201 if response.status_code == 200 else response.status_code
        return response

    @router.get("/templates")
    def list_templates(
        context: RequestContext = Depends(context_from_identity),
    ) -> JSONResponse:
        return invoke(lambda: {"items": dependencies.service.list_templates(context)})

    @router.get("/templates/{template_id}")
    def get_template(
        template_id: str,
        context: RequestContext = Depends(context_from_identity),
    ) -> JSONResponse:
        return invoke(lambda: dependencies.service.get_template(context, template_id))

    @router.put("/templates/{template_id}/versions", status_code=201)
    def create_template_version(
        template_id: str,
        payload: _VersionPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> JSONResponse:
        from server.xingjing_marketplace import CreateTemplateVersion

        response = invoke(
            lambda: dependencies.service.create_template_version(
                context,
                CreateTemplateVersion(
                    template_id=template_id,
                    expected_revision=_revision(if_match),
                    content=payload.content,
                    rights=_rights(payload.rights),
                    revenue_shares=_shares(payload.revenue_shares),
                    idempotency_key=key,
                    price_minor=payload.price_minor,
                ),
            )
        )
        response.status_code = 201 if response.status_code == 200 else response.status_code
        return response

    @router.post("/templates/{template_id}/reviews", status_code=201)
    def submit_template_review(
        template_id: str,
        payload: _StatementPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> JSONResponse:
        from server.xingjing_marketplace import SubmitTemplateReview

        response = invoke(
            lambda: dependencies.service.submit_template_review(
                context, SubmitTemplateReview(template_id, _revision(if_match), payload.statement, key)
            )
        )
        response.status_code = 201 if response.status_code == 200 else response.status_code
        return response

    @router.post("/template-reviews/{review_id}/decision")
    def decide_template_review(
        review_id: str,
        payload: _DecisionPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> JSONResponse:
        from server.xingjing_marketplace import DecideTemplateReview, ReviewDecision

        return invoke(
            lambda: dependencies.service.decide_template_review(
                context,
                DecideTemplateReview(
                    review_id, _revision(if_match), ReviewDecision(payload.decision), payload.reason, key
                ),
            )
        )

    @router.post("/templates/{template_id}/withdraw")
    def withdraw_template(
        template_id: str,
        payload: _ReasonPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> JSONResponse:
        from server.xingjing_marketplace import WithdrawTemplate

        return invoke(
            lambda: dependencies.service.withdraw_template(
                context, WithdrawTemplate(template_id, _revision(if_match), payload.reason, key)
            )
        )

    @router.get("/market/items")
    def list_market(
        context: RequestContext = Depends(context_from_identity),
        search: str | None = None,
        kind: Annotated[list[str], Query()] = [],
        tag: Annotated[list[str], Query()] = [],
        commercial_use: bool | None = None,
        minimum_price_minor: int | None = None,
        maximum_price_minor: int | None = None,
        page_size: int = 50,
        cursor: str | None = None,
    ) -> JSONResponse:
        from server.xingjing_marketplace import MarketQuery

        return invoke(
            lambda: dependencies.service.list_market(
                context,
                MarketQuery(
                    search=search,
                    kinds=frozenset(TemplateKind(item) for item in kind),
                    tags=frozenset(tag),
                    commercial_use=commercial_use,
                    minimum_price_minor=minimum_price_minor,
                    maximum_price_minor=maximum_price_minor,
                    page_size=page_size,
                    cursor=cursor,
                ),
            )
        )

    @router.get("/market/items/{item_id}")
    def get_market_item(
        item_id: str,
        context: RequestContext = Depends(context_from_identity),
    ) -> JSONResponse:
        return invoke(lambda: dependencies.service.get_market_item(context, item_id))

    @router.get("/market/items/{item_id}/usage")
    def get_market_item_usage(
        item_id: str,
        context: RequestContext = Depends(context_from_identity),
    ) -> JSONResponse:
        return invoke(lambda: {"reference_count": dependencies.service.count_market_item_usage(context, item_id)})

    @router.post("/forks", status_code=201)
    def create_fork(
        payload: _ForkPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
    ) -> JSONResponse:
        from server.xingjing_marketplace import CreateFork

        response = invoke(
            lambda: dependencies.service.create_fork(
                context,
                CreateFork(
                    payload.market_item_id,
                    payload.expected_market_revision,
                    payload.expected_source_version_id,
                    payload.project_name,
                    payload.intended_commercial_use,
                    tuple(payload.requested_inheritable_scopes),
                    key,
                ),
            )
        )
        response.status_code = 201 if response.status_code == 200 else response.status_code
        return response

    @router.get("/forks/{fork_id}")
    def get_fork(fork_id: str, context: RequestContext = Depends(context_from_identity)) -> JSONResponse:
        return invoke(lambda: dependencies.service.get_fork(context, fork_id))

    @router.get("/forks")
    def list_forks(context: RequestContext = Depends(context_from_identity)) -> JSONResponse:
        return invoke(lambda: {"items": dependencies.service.list_forks(context)})

    @router.get("/forks/{fork_id}/lineage")
    def get_lineage(fork_id: str, context: RequestContext = Depends(context_from_identity)) -> JSONResponse:
        return invoke(lambda: dependencies.service.get_lineage(context, fork_id))

    @router.get("/fork-projects/{project_id}")
    def get_fork_project(project_id: str, context: RequestContext = Depends(context_from_identity)) -> JSONResponse:
        return invoke(lambda: dependencies.service.get_fork_project(context, project_id))

    @router.post("/community-projects", status_code=201)
    def publish_community_project(
        payload: _CommunityPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
        if_match: Annotated[str, Header(alias="If-Match")] = "",
    ) -> JSONResponse:
        from server.xingjing_marketplace import PublishCommunityProject

        response = invoke(
            lambda: dependencies.service.publish_community_project(
                context,
                PublishCommunityProject(
                    fork_id=payload.fork_id,
                    expected_project_revision=_revision(if_match),
                    title=payload.title,
                    tags=tuple(payload.tags),
                    rights=_rights(payload.rights),
                    revenue_shares=_shares(payload.revenue_shares),
                    price_minor=payload.price_minor,
                    idempotency_key=key,
                ),
            )
        )
        response.status_code = 201 if response.status_code == 200 else response.status_code
        return response

    @router.get("/admin/templates")
    def list_admin_business_objects(
        subject_id: str | None = None,
        context: RequestContext = Depends(context_from_identity),
    ) -> JSONResponse:
        def operation() -> object:
            templates = dependencies.service.list_templates_for_admin(context)
            audits = dependencies.service.list_audit_events(context, subject_id=subject_id)
            return {"items": templates, "audit_events": audits}
        return invoke(operation)

    @router.post("/admin/templates/actions")
    def admin_business_action(
        payload: _AdminActionPayload,
        context: RequestContext = Depends(context_from_identity),
        key: str = Depends(idempotency_key),
    ) -> JSONResponse:
        from server.xingjing_marketplace import (
            DecideTemplateReview,
            RefundMarketPurchase,
            ReviewDecision,
            WithdrawTemplate,
        )

        if payload.action == "decide_review" and payload.review_id and payload.decision:
            review_id = payload.review_id
            return invoke(lambda: dependencies.service.decide_template_review(context, DecideTemplateReview(
                review_id, payload.version, ReviewDecision(payload.decision), payload.reason, key)))
        if payload.action == "withdraw_template" and payload.template_id:
            template_id = payload.template_id
            return invoke(lambda: dependencies.service.withdraw_template(context, WithdrawTemplate(
                template_id, payload.version, payload.reason, key)))
        if payload.action == "refund_purchase" and payload.purchase_id:
            purchase_id = payload.purchase_id
            return invoke(lambda: dependencies.service.refund_market_purchase(
                context, RefundMarketPurchase(purchase_id, payload.reason, key)))
        return _error_response(InvalidInput("unsupported admin business action"))

    return router


def _to_dict(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _to_dict(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_to_dict(item) for item in value]
    if isinstance(value, list):
        return [_to_dict(item) for item in value]
    method = getattr(value, "to_dict", None)
    return method() if callable(method) else value


def _revision(value: str) -> int:
    try:
        return int(value.strip().strip('"'))
    except ValueError as error:
        raise InvalidInput("If-Match must contain a positive integer revision") from error


def _rights(payload: _RightsPayload) -> RightsPolicyInput:
    from server.xingjing_marketplace import RightsPolicyInput, RightsScope

    return RightsPolicyInput(
        scope=RightsScope(payload.scope),
        commercial_use=payload.commercial_use,
        attribution_required=payload.attribution_required,
        inheritable_scopes=tuple(payload.inheritable_scopes),
        allowed_workspace_ids=tuple(payload.allowed_workspace_ids),
        allow_fork=payload.allow_fork,
    )


def _shares(payload: list[_RevenueSharePayload]) -> tuple[RevenueShareInput, ...]:
    return tuple(RevenueShareInput(item.beneficiary_role, item.basis_points) for item in payload)


def _error_response(error: MarketplaceError) -> JSONResponse:
    status_code = 400
    if isinstance(error, PermissionDenied):
        status_code = 403
    elif isinstance(error, (TemplateNotFound, MarketItemNotFound, ReviewNotFound, ForkNotFound, ForkProjectNotFound)):
        status_code = 404
    elif isinstance(error, (IdempotencyConflict, VersionConflict, InvalidTransition)):
        status_code = 409
    elif isinstance(error, ForkNotAllowed):
        status_code = 403
    elif isinstance(error, InvalidInput):
        status_code = 400
    return JSONResponse(status_code=status_code, content={"error": {"code": error.code, "message": str(error)}})
