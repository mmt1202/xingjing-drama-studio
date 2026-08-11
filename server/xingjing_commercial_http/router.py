import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import NoReturn
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from server.xingjing_commercial import (
    AcceptanceDecision,
    AccountingRejected,
    Actor,
    CommercialError,
    CommercialOrder,
    CommercialService,
    IdempotencyConflict,
    InvalidTransition,
    OrderNotFound,
    PermissionDenied,
    SettlementBlocked,
    ValidationError,
    VersionConflict,
)

from .dependencies import (
    CommercialHttpDependencies,
    CommercialOrderIndex,
    CommercialOrderRef,
    default_dependencies,
)
from .schemas import (
    AcceptDeliveryRequest,
    ContractRequest,
    DeliveryRequest,
    OpenDisputeRequest,
    QuoteRequest,
    ResolveDisputeRequest,
    ReturnDeliveryRequest,
    SettlementConfirmationRequest,
)

_ETAG_PATTERN = re.compile(r'^(?:W/)?"([1-9][0-9]*)"$')


@dataclass(frozen=True, slots=True)
class CommandContext:
    expected_version: int
    idempotency_key: str
    request_id: str


def _error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})


def _command_context(
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> CommandContext:
    if idempotency_key is None or not idempotency_key.strip():
        raise _error(status.HTTP_400_BAD_REQUEST, "IDEMPOTENCY_KEY_REQUIRED")
    if if_match is None or not if_match.strip():
        raise _error(status.HTTP_428_PRECONDITION_REQUIRED, "IF_MATCH_REQUIRED")
    match = _ETAG_PATTERN.fullmatch(if_match.strip())
    if match is None:
        raise _error(status.HTTP_400_BAD_REQUEST, "INVALID_IF_MATCH")
    normalized_request_id = request_id.strip() if request_id is not None else ""
    return CommandContext(
        expected_version=int(match.group(1)),
        idempotency_key=idempotency_key.strip(),
        request_id=normalized_request_id or str(uuid4()),
    )


def _raise_http_error(exc: CommercialError) -> NoReturn:
    if isinstance(exc, PermissionDenied):
        raise _error(status.HTTP_403_FORBIDDEN, exc.code) from exc
    if isinstance(exc, OrderNotFound):
        raise _error(status.HTTP_404_NOT_FOUND, exc.code) from exc
    if isinstance(exc, ValidationError):
        raise _error(status.HTTP_400_BAD_REQUEST, exc.code) from exc
    if isinstance(exc, AccountingRejected):
        raise _error(status.HTTP_502_BAD_GATEWAY, exc.code) from exc
    if isinstance(exc, (VersionConflict, IdempotencyConflict, InvalidTransition, SettlementBlocked)):
        raise _error(status.HTTP_409_CONFLICT, exc.code) from exc
    raise _error(status.HTTP_400_BAD_REQUEST, exc.code) from exc


def _domain_call[ResultT](call: Callable[[], ResultT]) -> ResultT:
    try:
        return call()
    except CommercialError as exc:
        _raise_http_error(exc)


def _order_response(order: CommercialOrder) -> JSONResponse:
    return JSONResponse(order.to_dict(), headers={"ETag": f'"{order.version}"'})


def _require_list_permission(actor: Actor) -> None:
    if not ({"commercial.view", "admin.commercial.view"} & actor.permissions):
        raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN")


def _order_ref(index: CommercialOrderIndex, order_id: str) -> CommercialOrderRef:
    ref = index.find_order_ref(order_id)
    if ref is None:
        raise _error(status.HTTP_404_NOT_FOUND, "COMMERCIAL_ORDER_NOT_FOUND")
    return ref


def _get_order(
    *,
    order_id: str,
    actor: Actor,
    service: CommercialService,
    index: CommercialOrderIndex,
) -> CommercialOrder:
    ref = _order_ref(index, order_id)
    return _domain_call(lambda: service.get_order(actor, ref.owner_workspace_id, ref.order_id))


def _confirm_settlement(
    *,
    order_id: str,
    settlement_id: str,
    confirmation: SettlementConfirmationRequest,
    actor: Actor,
    service: CommercialService,
    index: CommercialOrderIndex,
) -> CommercialOrderRef:
    ref = _order_ref(index, order_id)
    order = _domain_call(lambda: service.get_order(actor, ref.owner_workspace_id, ref.order_id))
    settlement = next((item for item in order.settlements if item.id == settlement_id), None)
    if settlement is None:
        raise _error(status.HTTP_404_NOT_FOUND, "SETTLEMENT_NOT_FOUND")
    if (
        settlement.amount_minor != confirmation.amount_minor
        or settlement.currency != confirmation.currency.strip().upper()
    ):
        raise _error(status.HTTP_409_CONFLICT, "AMOUNT_CONFIRMATION_MISMATCH")
    return ref


def _build_surface(prefix: str, dependencies: CommercialHttpDependencies) -> APIRouter:
    surface = APIRouter(prefix=prefix, tags=["commercial-orders"])

    @surface.get("")
    def list_orders(
        owner_workspace_id: str | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=100),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> dict[str, object]:
        _require_list_permission(actor)
        visible: list[CommercialOrder] = []
        for ref in index.list_order_refs():
            if owner_workspace_id is not None and ref.owner_workspace_id != owner_workspace_id:
                continue
            try:
                visible.append(service.get_order(actor, ref.owner_workspace_id, ref.order_id))
            except OrderNotFound:
                continue
            except CommercialError as exc:
                _raise_http_error(exc)
        visible.sort(key=lambda order: (order.created_at, order.id), reverse=True)
        page = visible[offset : offset + limit]
        return {
            "items": [order.to_dict() for order in page],
            "total": len(visible),
            "offset": offset,
            "limit": limit,
        }

    @surface.get("/{order_id}")
    def get_order(
        order_id: str,
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        return _order_response(_get_order(order_id=order_id, actor=actor, service=service, index=index))

    @surface.get("/{order_id}/milestones")
    def list_milestones(
        order_id: str,
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> dict[str, object]:
        order = _get_order(order_id=order_id, actor=actor, service=service, index=index)
        return {"items": [item.to_dict() for item in order.milestones], "total": len(order.milestones)}

    @surface.post("/{order_id}/quotes")
    def submit_quote(
        order_id: str,
        request: QuoteRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.submit_quote(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                amount_minor=request.amount_minor,
                currency=request.currency,
                proposal=request.proposal,
                valid_until=request.valid_until,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/quotes/{quote_id}/accept")
    def accept_quote(
        order_id: str,
        quote_id: str,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.accept_quote(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                quote_id=quote_id,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/contracts")
    def record_contract(
        order_id: str,
        request: ContractRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.record_contract_version(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                content_ref=request.content_ref,
                content_digest=request.content_digest,
                amount_minor=request.amount_minor,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/milestones/{milestone_id}/deliveries")
    def submit_delivery(
        order_id: str,
        milestone_id: str,
        request: DeliveryRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.submit_delivery(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                milestone_id=milestone_id,
                artifact_version_id=request.artifact_version_id,
                artifact_digest=request.artifact_digest,
                note=request.note,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/deliveries/{delivery_id}/return")
    def return_delivery(
        order_id: str,
        delivery_id: str,
        request: ReturnDeliveryRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.decide_delivery(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                delivery_id=delivery_id,
                decision=AcceptanceDecision.CHANGES_REQUESTED,
                reason=request.reason,
                evidence_ref=None,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/deliveries/{delivery_id}/accept")
    def accept_delivery(
        order_id: str,
        delivery_id: str,
        request: AcceptDeliveryRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.decide_delivery(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                delivery_id=delivery_id,
                decision=AcceptanceDecision.ACCEPTED,
                reason=None,
                evidence_ref=request.evidence_ref,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/milestones/{milestone_id}/disputes")
    def open_dispute(
        order_id: str,
        milestone_id: str,
        request: OpenDisputeRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.open_dispute(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                milestone_id=milestone_id,
                kind=request.kind,
                reason=request.reason,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/disputes/{dispute_id}/resolve")
    def resolve_dispute(
        order_id: str,
        dispute_id: str,
        request: ResolveDisputeRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        ref = _order_ref(index, order_id)
        order = _domain_call(
            lambda: service.resolve_dispute(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                dispute_id=dispute_id,
                resolution=request.resolution,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    def settlement_action(
        action: str,
        *,
        order_id: str,
        settlement_id: str,
        request: SettlementConfirmationRequest,
        command: CommandContext,
        service: CommercialService,
        actor: Actor,
        index: CommercialOrderIndex,
    ) -> JSONResponse:
        ref = _confirm_settlement(
            order_id=order_id,
            settlement_id=settlement_id,
            confirmation=request,
            actor=actor,
            service=service,
            index=index,
        )
        method = {
            "freeze": service.freeze_settlement,
            "resume": service.resume_settlement,
            "pay": service.settle,
        }[action]
        order = _domain_call(
            lambda: method(
                actor=actor,
                owner_workspace_id=ref.owner_workspace_id,
                order_id=ref.order_id,
                settlement_id=settlement_id,
                expected_version=command.expected_version,
                request_id=command.request_id,
                idempotency_key=command.idempotency_key,
            )
        )
        return _order_response(order)

    @surface.post("/{order_id}/settlements/{settlement_id}/freeze")
    def freeze_settlement(
        order_id: str,
        settlement_id: str,
        request: SettlementConfirmationRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        return settlement_action(
            "freeze",
            order_id=order_id,
            settlement_id=settlement_id,
            request=request,
            command=command,
            service=service,
            actor=actor,
            index=index,
        )

    @surface.post("/{order_id}/settlements/{settlement_id}/resume")
    def resume_settlement(
        order_id: str,
        settlement_id: str,
        request: SettlementConfirmationRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        return settlement_action(
            "resume",
            order_id=order_id,
            settlement_id=settlement_id,
            request=request,
            command=command,
            service=service,
            actor=actor,
            index=index,
        )

    @surface.post("/{order_id}/settlements/{settlement_id}/pay")
    def pay_settlement(
        order_id: str,
        settlement_id: str,
        request: SettlementConfirmationRequest,
        command: CommandContext = Depends(_command_context),
        service: CommercialService = Depends(dependencies.service),
        actor: Actor = Depends(dependencies.actor),
        index: CommercialOrderIndex = Depends(dependencies.order_index),
    ) -> JSONResponse:
        return settlement_action(
            "pay",
            order_id=order_id,
            settlement_id=settlement_id,
            request=request,
            command=command,
            service=service,
            actor=actor,
            index=index,
        )

    return surface


def create_commercial_router(
    dependencies: CommercialHttpDependencies = default_dependencies,
) -> APIRouter:
    commercial_router = APIRouter()
    commercial_router.include_router(_build_surface("/commercial-orders", dependencies))
    commercial_router.include_router(_build_surface("/admin/commercial-orders", dependencies))
    return commercial_router


router = create_commercial_router()
