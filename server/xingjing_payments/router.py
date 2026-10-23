"""Interactive finance commands and a separately authenticated gateway event route."""

from __future__ import annotations

from json import JSONDecodeError

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from .contracts import CreateOrder, OrderAction, PaymentFailure, RefundAction


class PaymentRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request):
            try:
                return await original(request)
            except PaymentFailure as error:
                code, status = error.code, error.status
            except (ValidationError, RequestValidationError, JSONDecodeError, UnicodeDecodeError):
                code, status = "PAYMENT_REQUEST_INVALID", 422
            except IntegrityError:
                code, status = "PAYMENT_CONFLICT", 409
            except SQLAlchemyError:
                code, status = "PAYMENT_SOURCE_UNAVAILABLE", 503
            except OverflowError:
                code, status = "PAYMENT_BALANCE_LIMIT", 503
            except HTTPException as error:
                code, status = "PAYMENT_CONTEXT_REJECTED", error.status_code
            return JSONResponse(
                status_code=status,
                content={
                    "error": {"code": code, "message": code},
                    "meta": {"requestId": request.headers.get("X-Request-Id", "")},
                },
            )

        return handle


def _key(request):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not key or len(key) > 128:
        raise PaymentFailure("PAYMENT_IDEMPOTENCY_KEY_REQUIRED", 422)
    return key


def create_payment_router(runtime):
    router = APIRouter(prefix="/api/v1", tags=["payments"], route_class=PaymentRoute)
    prefix = "/workspaces/{workspace_id}/payments"

    def envelope(value, context):
        return {"data": value, "meta": {"requestId": context.request_id}}

    @router.get(prefix + "/catalog")
    async def catalog(request: Request, workspace_id: str):
        context = await runtime.context(request, workspace_id)
        return envelope(await runtime.catalog(context), context)

    async def listing(request, workspace_id, resource, status, q, offset, limit):
        context = await runtime.context(request, workspace_id)
        return envelope(
            await runtime.listing(context, resource, status=status, q=q, offset=offset, limit=limit), context
        )

    @router.get(prefix + "/orders")
    async def orders(
        request: Request,
        workspace_id: str,
        status: str | None = Query(None, max_length=32),
        q: str = Query("", max_length=255),
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        return await listing(request, workspace_id, "orders", status, q, offset, limit)

    @router.get(prefix + "/refunds")
    async def refunds(
        request: Request,
        workspace_id: str,
        status: str | None = Query(None, max_length=32),
        q: str = Query("", max_length=255),
        offset: int = Query(0, ge=0),
        limit: int = Query(25, ge=1, le=100),
    ):
        return await listing(request, workspace_id, "refunds", status, q, offset, limit)

    @router.get(prefix + "/orders/{order_id}")
    async def detail(request: Request, workspace_id: str, order_id: str):
        context = await runtime.context(request, workspace_id)
        return envelope(await runtime.detail(context, order_id), context)

    @router.post(prefix + "/orders")
    async def create(request: Request, workspace_id: str):
        context = await runtime.context(request, workspace_id)
        payload = CreateOrder.model_validate(await request.json())
        return envelope(
            await runtime.execute(context, "create", None, payload, _key(request), request=request), context
        )

    @router.post(prefix + "/orders/{order_id}/actions")
    async def order_action(request: Request, workspace_id: str, order_id: str):
        context = await runtime.context(request, workspace_id)
        payload = OrderAction.model_validate(await request.json())
        return envelope(
            await runtime.execute(context, "order", order_id, payload, _key(request), request=request), context
        )

    @router.post(prefix + "/refunds/{refund_id}/actions")
    async def refund_action(request: Request, workspace_id: str, refund_id: str):
        context = await runtime.context(request, workspace_id)
        payload = RefundAction.model_validate(await request.json())
        return envelope(
            await runtime.execute(context, "refund", refund_id, payload, _key(request), request=request), context
        )

    @router.post("/payment-gateway/events")
    async def event(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536:
                raise PaymentFailure("PAYMENT_GATEWAY_MESSAGE_TOO_LARGE", 413)
        return {
            "data": await runtime.callback(bytes(raw), request.headers),
            "meta": {"requestId": request.headers.get("X-Request-Id", "gateway-event")},
        }

    return router
