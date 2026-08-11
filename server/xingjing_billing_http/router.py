from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from server.xingjing_billing_persistence import BillingIdempotencyConflict
from server.xingjing_billing_runtime import (
    BillingPageQuery,
    BillingRuntime,
    BillingRuntimeConfigurationError,
    BillingRuntimePermissionDenied,
)


class BillingContractRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request: Request):
            try:
                return await original(request)
            except BillingRuntimePermissionDenied:
                return _error(request, "PERMISSION_DENIED", status.HTTP_403_FORBIDDEN)
            except BillingIdempotencyConflict:
                return _error(request, "IDEMPOTENCY_CONFLICT", status.HTTP_409_CONFLICT)
            except BillingRuntimeConfigurationError:
                return _error(request, "BILLING_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)
            except HTTPException as error:
                return _error(request, "REQUEST_REJECTED", error.status_code)
            except ValueError as error:
                return _error(request, str(error) or "INVALID_BILLING_REQUEST", status.HTTP_400_BAD_REQUEST)

        return handler


def create_billing_router(runtime: BillingRuntime) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces", tags=["xingjing-billing"], route_class=BillingContractRoute)

    @router.get("/{workspace_id}/billing")
    async def billing(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _paged_success(
            context.request_id,
            await runtime.billing(context, BillingPageQuery(page_size, page_token, query)),
        )

    @router.get("/{workspace_id}/billing/holds")
    async def holds(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _paged_success(
            context.request_id,
            await runtime.holds(context, BillingPageQuery(page_size, page_token, query)),
        )

    @router.get("/{workspace_id}/billing/journals")
    async def journals(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _paged_success(
            context.request_id,
            await runtime.journals(context, BillingPageQuery(page_size, page_token, query)),
        )

    @router.get("/{workspace_id}/credit-account")
    async def credit_account(request: Request, workspace_id: str) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        data = await runtime.billing(context, BillingPageQuery(page_size=1))
        account = data.get("account")
        if not isinstance(account, Mapping):
            raise BillingRuntimeConfigurationError("CREDIT_ACCOUNT_UNAVAILABLE")
        return _success(context.request_id, {"object": cast(Mapping[str, object], account)})

    @router.get("/{workspace_id}/credit-transactions")
    async def credit_transactions(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _paged_success(
            context.request_id,
            await runtime.journals(context, BillingPageQuery(page_size, page_token, query)),
        )

    @router.get("/{workspace_id}/billing/plan")
    async def plan(request: Request, workspace_id: str) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _success(context.request_id, await runtime.plan(context))

    @router.get("/{workspace_id}/costs")
    async def costs(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
        project_id: str | None = Query(None, alias="projectId"),
        source: str | None = None,
        model_id: str | None = Query(None, alias="modelId"),
        actor_id: str | None = Query(None, alias="actorId"),
        episode_id: str | None = Query(None, alias="episodeId"),
        shot_id: str | None = Query(None, alias="shotId"),
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.view")
        return _paged_success(
            context.request_id,
            await runtime.costs(
                context,
                BillingPageQuery(
                    page_size,
                    page_token,
                    query,
                    project_id,
                    source,
                    model_id,
                    actor_id,
                    episode_id,
                    shot_id,
                ),
            ),
        )

    @router.get("/{workspace_id}/billing/audit-events")
    async def audit_events(
        request: Request,
        workspace_id: str,
        page_size: int = Query(25, alias="pageSize", ge=1, le=100),
        page_token: str | None = Query(None, alias="pageToken"),
        query: str | None = None,
        request_id: str | None = Query(None, alias="requestId"),
        actor_id: str | None = Query(None, alias="actorId"),
        object_id: str | None = Query(None, alias="objectId"),
    ) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.manage")
        return _paged_success(
            context.request_id,
            await runtime.audit_events(
                context,
                BillingPageQuery(page_size, page_token, query),
                request_id=request_id,
                actor_id=actor_id,
                object_id=object_id,
            ),
        )

    @router.get("/{workspace_id}/billing/export.csv")
    async def export_csv(
        request: Request,
        workspace_id: str,
        dataset: str = Query("documents"),
        query: str | None = None,
        project_id: str | None = Query(None, alias="projectId"),
    ) -> Response:
        context = await runtime.resolve(request, workspace_id, "billing.manage")
        filename, content = await runtime.export_csv(
            context, dataset=dataset, query=query, project_id=project_id
        )
        return Response(
            content=content,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Request-Id": context.request_id,
            },
        )

    @router.post("/{workspace_id}/billing/actions")
    async def action(request: Request, workspace_id: str) -> JSONResponse:
        context = await runtime.resolve(request, workspace_id, "billing.manage")
        body = await _body(request)
        action_name = _text(body, "action")
        nested_payload = body.get("payload")
        payload: Mapping[str, object] = (
            cast(Mapping[str, object], nested_payload) if isinstance(nested_payload, Mapping) else body
        )
        if action_name == "requestInvoice":
            result = await runtime.request_invoice(
                context,
                invoice_title=_text(payload, "invoiceTitle"),
                amount_minor=_positive_integer(payload, "amountMinor"),
                currency=_optional_text(payload, "currency") or "CNY",
                idempotency_key=_idempotency_key(request),
            )
        elif action_name == "createPaymentOrder":
            expires_in = _optional_positive_integer(payload, "expiresInSeconds") or 1_800
            if expires_in > 86_400:
                raise ValueError("PAYMENT_ORDER_EXPIRY_INVALID")
            result = await runtime.create_payment_order(
                context,
                order_id=str(uuid4()),
                amount_minor=_positive_integer(payload, "amountMinor"),
                currency=_optional_text(payload, "currency") or "CNY",
                order_type=_optional_text(payload, "orderType") or "credit_top_up",
                idempotency_key=_idempotency_key(request),
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            )
        elif action_name == "refundPaymentOrder":
            result = await runtime.refund_payment_order(
                context,
                order_id=_text(payload, "orderId"),
                amount_minor=_positive_integer(payload, "amountMinor"),
                idempotency_key=_idempotency_key(request),
            )
        else:
            raise ValueError("BILLING_ACTION_UNSUPPORTED")
        return _success(context.request_id, {"object": result, "status": "succeeded"})

    @router.post("/{workspace_id}/billing/payment-callbacks")
    async def payment_callback(request: Request, workspace_id: str) -> JSONResponse:
        raw_body = await request.body()
        result = await runtime.apply_payment_callback(
            raw_body=raw_body,
            signature=request.headers.get("X-Xingjing-Payment-Signature", ""),
            expected_workspace_id=workspace_id,
        )
        return _success(request.headers.get("X-Request-Id", "payment-callback"), {"object": result})

    return router


def create_unavailable_billing_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces", tags=["xingjing-billing"])

    async def unavailable(request: Request, workspace_id: str, path: str) -> JSONResponse:
        del workspace_id, path
        return _error(request, "BILLING_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
        router.add_api_route(
            "/{workspace_id}/billing{path:path}",
            unavailable,
            methods=[method],
            operation_id=f"unavailable_billing_{method.casefold()}",
        )

    @router.get("/{workspace_id}/credit-account")
    async def credit_account_unavailable(request: Request, workspace_id: str) -> JSONResponse:
        del workspace_id
        return _error(request, "BILLING_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    @router.get("/{workspace_id}/credit-transactions")
    async def credit_transactions_unavailable(request: Request, workspace_id: str) -> JSONResponse:
        del workspace_id
        return _error(request, "BILLING_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    @router.get("/{workspace_id}/costs")
    async def costs_unavailable(request: Request, workspace_id: str) -> JSONResponse:
        del workspace_id
        return _error(request, "BILLING_RUNTIME_UNAVAILABLE", status.HTTP_503_SERVICE_UNAVAILABLE)

    return router


async def _body(request: Request) -> Mapping[str, object]:
    value = await request.json()
    if not isinstance(value, Mapping):
        raise ValueError("JSON_OBJECT_REQUIRED")
    return cast(Mapping[str, object], value)


def _text(value: Mapping[str, object], key: str) -> str:
    candidate = value.get(key)
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{key.upper()}_REQUIRED")
    return candidate.strip()


def _optional_positive_integer(value: Mapping[str, object], key: str) -> int | None:
    candidate = value.get(key)
    if candidate is None:
        return None
    if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate <= 0:
        raise ValueError(f"{key.upper()}_INVALID")
    return candidate


def _optional_text(value: Mapping[str, object], key: str) -> str | None:
    candidate = value.get(key)
    return candidate.strip() if isinstance(candidate, str) and candidate.strip() else None


def _positive_integer(value: Mapping[str, object], key: str) -> int:
    candidate = value.get(key)
    if isinstance(candidate, bool) or not isinstance(candidate, int) or candidate <= 0:
        raise ValueError(f"{key.upper()}_INVALID")
    return candidate


def _idempotency_key(request: Request) -> str:
    value = request.headers.get("Idempotency-Key", "").strip()
    if not value:
        raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
    return value


def _success(request_id: str, data: Mapping[str, object]) -> JSONResponse:
    return JSONResponse({"data": dict(data), "meta": {"requestId": request_id}})


def _paged_success(request_id: str, data: Mapping[str, object]) -> JSONResponse:
    payload = dict(data)
    next_token = payload.pop("next_page_token", None)
    page = {"nextToken": next_token} if isinstance(next_token, str) else {"nextToken": None}
    return JSONResponse({"data": payload, "meta": {"requestId": request_id, "page": page}})


def _error(request: Request, code: str, status_code: int) -> JSONResponse:
    request_id = request.headers.get("X-Request-Id", "unknown")
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "details": {}}, "meta": {"requestId": request_id}},
    )
