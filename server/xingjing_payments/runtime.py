"""Durable gateway commands with conservative, provenance-bound refund reserves."""

from __future__ import annotations

import inspect
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.xingjing_billing_persistence.models import BillingOrderRow
from server.xingjing_generation_persistence.repository import GenerationBillingAccountRow
from server.xingjing_identity_context import PlatformSessionGateway, TrustedWorkspaceContextResolver
from server.xingjing_identity_context.trusted_context import TrustedWorkspaceContext

from .contracts import MAX_MINOR, PaymentFailure, minor
from .gateway import SignedHttpPaymentGateway, checkout_url, fingerprint, load_settings
from .models import PaymentAuditRow, PaymentCommandRow, PaymentEventRow, PaymentOrderRow, PaymentRefundRow
from .store import (
    check_account,
    evidence,
    get_order,
    get_refund,
    iso,
    journal,
    lock_workspace,
    move_refund_reserve,
    now,
    order_dto,
    refund_capacity,
    refund_dto,
    require,
    scoped,
    verify_workspace,
)


def fact(payload, name):
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > 128:
        raise PaymentFailure("PAYMENT_GATEWAY_FACT_INVALID", 422)
    return value


class PaymentRuntime:
    def __init__(
        self,
        sessions,
        resolver,
        *,
        settings=None,
        gateway=None,
        approval_verifier=None,
        engine=None,
        unavailable_code=None,
    ):
        self.sessions, self.resolver, self.settings = sessions, resolver, settings
        self.gateway = gateway or (SignedHttpPaymentGateway(settings) if settings else None)
        self.approval_verifier, self.engine, self.unavailable_code = approval_verifier, engine, unavailable_code

    @property
    def configured(self):
        return self.sessions is not None and self.settings is not None and self.gateway is not None

    def status(self):
        return {
            "configured": self.configured,
            "reasonCode": None if self.configured else self.unavailable_code or "PAYMENT_GATEWAY_NOT_CONFIGURED",
        }

    def session(self):
        if self.sessions is None:
            raise PaymentFailure(self.unavailable_code or "PAYMENT_DATABASE_UNAVAILABLE", 503)
        return self.sessions()

    def require_gateway(self):
        if not self.configured:
            raise PaymentFailure(self.unavailable_code or "PAYMENT_GATEWAY_NOT_CONFIGURED", 503)

    async def start(self):
        return None

    async def close(self):
        if self.engine is not None:
            await self.engine.dispose()

    async def context(self, request, workspace_id):
        if request.headers.get("X-Project-Id"):
            raise PaymentFailure("PAYMENT_WORKSPACE_CONTEXT_REQUIRED", 403)
        value = self.resolver(request)
        context = await value if inspect.isawaitable(value) else value
        require(context)
        if context.workspace_id != workspace_id or request.headers.get("X-Workspace-Id", workspace_id) != workspace_id:
            raise PaymentFailure("PAYMENT_CONTEXT_MISMATCH", 403)
        return context

    @staticmethod
    def permissions(context):
        return {
            "canView": "billing.view" in context.permissions,
            "canManage": "billing.manage" in context.permissions,
            "canApprove": {"billing.manage", "admin.finance.approve"}.issubset(context.permissions),
        }

    async def catalog(self, context):
        settings = self.settings if self.configured else None
        reason = None
        if settings:
            async with self.session() as session:
                await verify_workspace(session, context)
                account = await session.get(GenerationBillingAccountRow, context.workspace_id)
                if account and account.currency != settings.offers[0].currency:
                    reason = "PAYMENT_ACCOUNT_CURRENCY_MISMATCH"
        return {
            "scope": {"workspaceId": context.workspace_id, "actorId": context.actor_id},
            "permissions": self.permissions(context),
            **self.status(),
            "offers": [
                {
                    "id": item.id,
                    "label": item.label,
                    "amountMinor": str(item.amount_minor),
                    "creditMinor": str(item.credit_minor),
                    "currency": item.currency,
                }
                for item in settings.offers
            ]
            if settings and reason is None
            else [],
            "channels": [{"id": item.id, "label": item.label} for item in settings.channels] if settings else [],
            "policy": {
                "accountUnit": "currency_minor",
                "fourEyes": True,
                "refundMfaThresholdMinor": str(settings.refund_mfa_threshold_minor)
                if settings and settings.refund_mfa_threshold_minor is not None
                else None,
                "mfaVerifierAvailable": self.approval_verifier is not None,
            },
            **({"reasonCode": reason} if reason else {}),
        }

    async def listing(self, context, resource, *, status=None, q="", offset=0, limit=25):
        require(context)
        async with self.session() as session:
            await verify_workspace(session, context)
            if resource == "orders":
                statement = (
                    select(BillingOrderRow, PaymentOrderRow)
                    .outerjoin(
                        PaymentOrderRow,
                        (PaymentOrderRow.order_id == BillingOrderRow.order_id)
                        & (PaymentOrderRow.tenant_id == BillingOrderRow.tenant_id)
                        & (PaymentOrderRow.workspace_id == BillingOrderRow.workspace_id),
                    )
                    .where(*scoped(BillingOrderRow, context))
                )
                if status:
                    statement = statement.where(func.coalesce(PaymentOrderRow.state, BillingOrderRow.status) == status)
                if q.strip():
                    statement = statement.where(BillingOrderRow.order_id.icontains(q.strip(), autoescape=True))
                total = await session.scalar(select(func.count()).select_from(statement.subquery()))
                rows = (
                    await session.execute(
                        statement.order_by(BillingOrderRow.created_at.desc(), BillingOrderRow.order_id.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
                items = [
                    await order_dto(session, context, order, linked, configured=self.configured)
                    for order, linked in rows
                ]
            else:
                statement = select(PaymentRefundRow).where(*scoped(PaymentRefundRow, context))
                if status:
                    statement = statement.where(PaymentRefundRow.status == status)
                if q.strip():
                    statement = statement.where(
                        PaymentRefundRow.order_id.icontains(q.strip(), autoescape=True)
                        | PaymentRefundRow.id.icontains(q.strip(), autoescape=True)
                    )
                total = await session.scalar(select(func.count()).select_from(statement.subquery()))
                rows = (
                    await session.scalars(
                        statement.order_by(PaymentRefundRow.created_at.desc(), PaymentRefundRow.id.desc())
                        .offset(offset)
                        .limit(limit)
                    )
                ).all()
                items = [refund_dto(context, row, self.settings, self.approval_verifier) for row in rows]
            return {"items": items, "total": total or 0, "offset": offset, "limit": limit}

    async def detail(self, context, order_id):
        require(context)
        async with self.session() as session:
            await verify_workspace(session, context)
            order, linked = await get_order(session, context, order_id)
            refunds = (
                await session.scalars(
                    select(PaymentRefundRow)
                    .where(*scoped(PaymentRefundRow, context), PaymentRefundRow.order_id == order_id)
                    .order_by(PaymentRefundRow.created_at.desc())
                    .limit(100)
                )
            ).all()
            history = (
                await session.scalars(
                    select(PaymentAuditRow)
                    .where(*scoped(PaymentAuditRow, context), PaymentAuditRow.order_id == order_id)
                    .order_by(PaymentAuditRow.created_at.desc(), PaymentAuditRow.id.desc())
                    .limit(50)
                )
            ).all()
            return {
                "scope": {"workspaceId": context.workspace_id, "actorId": context.actor_id},
                "permissions": self.permissions(context),
                "order": await order_dto(session, context, order, linked, configured=self.configured),
                "refunds": [refund_dto(context, row, self.settings, self.approval_verifier) for row in refunds],
                "history": [
                    {
                        "action": row.action,
                        "actorId": row.actor_id,
                        "createdAt": iso(row.created_at),
                        **({"note": row.after["note"]} if row.after.get("note") else {}),
                    }
                    for row in history
                ],
            }

    async def execute(self, context, operation, object_id, payload, key, *, request=None):
        require(context, "billing.manage")
        self.require_gateway()
        assert self.gateway is not None
        raw = payload.model_dump(mode="json")
        digest = fingerprint([operation, object_id, raw])
        async with self.session() as session, session.begin():
            await lock_workspace(session, context)
            receipt = await session.get(
                PaymentCommandRow, (context.tenant_id, context.workspace_id, context.actor_id, key)
            )
            if receipt is not None:
                if receipt.fingerprint != digest:
                    raise PaymentFailure("PAYMENT_IDEMPOTENCY_CONFLICT", 409)
                if operation == "refund":
                    refund = await get_refund(session, context, receipt.object_id)
                    if payload.action == "approve" or (
                        payload.action == "reject" and refund.requested_by != context.actor_id
                    ):
                        require(context, "admin.finance.approve")
                    if payload.action == "approve" and refund.requested_by == context.actor_id:
                        raise PaymentFailure("PAYMENT_FOUR_EYES_REQUIRED", 409)
                if receipt.response is not None:
                    return receipt.response
            else:
                receipt = await self._claim(
                    session, context, operation, object_id, payload, key, digest, request=request
                )
            receipt_id = (context.tenant_id, context.workspace_id, context.actor_id, key)
            work, gateway_key, target_id = dict(receipt.request), receipt.gateway_key, receipt.object_id
            if receipt.response is not None:
                return receipt.response
        try:
            response = await self.gateway.command(work.pop("operation"), work, gateway_key)
            if any(
                response.get(field) != work.get(field)
                for field in ("orderId", "refundId", "merchantId", "provider", "amountMinor", "currency")
            ):
                raise PaymentFailure("PAYMENT_GATEWAY_RESPONSE_MISMATCH", 502)
            await self.apply_event(response, context=context)
        except PaymentFailure:
            await self._uncertain(context, target_id, operation)
            raise
        except (SQLAlchemyError, OverflowError, ValueError, TypeError, TimeoutError, OSError) as error:
            await self._uncertain(context, target_id, operation)
            raise PaymentFailure("PAYMENT_GATEWAY_UNCERTAIN", 503) from error
        async with self.session() as session, session.begin():
            await lock_workspace(session, context)
            receipt = await session.get(PaymentCommandRow, receipt_id, with_for_update=True)
            if receipt.response is None:
                if operation == "refund":
                    refund = await get_refund(session, context, target_id)
                    order, linked = await get_order(session, context, refund.order_id)
                    result = {
                        "refund": refund_dto(context, refund, self.settings, self.approval_verifier),
                        "order": await order_dto(session, context, order, linked, configured=self.configured),
                    }
                else:
                    order, linked = await get_order(session, context, target_id)
                    result = {"order": await order_dto(session, context, order, linked, configured=self.configured)}
                receipt.response = result
            return receipt.response

    async def _claim(self, session, context, operation, object_id, payload, key, digest, *, request=None):
        assert self.settings is not None
        instant = now()
        work = {"merchantId": self.settings.merchant_id, "provider": self.settings.provider}
        gateway_key = "payment:" + fingerprint([context.tenant_id, context.workspace_id, context.actor_id, key])
        response = None
        if operation == "create":
            offer = next((item for item in self.settings.offers if item.id == payload.offerId), None)
            if len({item.currency for item in self.settings.offers}) != 1 or any(
                item.amount_minor != item.credit_minor or item.amount_minor < 1 or item.amount_minor > MAX_MINOR
                for item in self.settings.offers
            ):
                raise PaymentFailure("PAYMENT_CONFIGURATION_INVALID", 503)
            if offer is None or payload.channel not in {item.id for item in self.settings.channels}:
                raise PaymentFailure("PAYMENT_OFFER_OR_CHANNEL_UNAVAILABLE", 422)
            object_id = str(uuid4())
            account = await session.get(GenerationBillingAccountRow, context.workspace_id)
            if account is None:
                account = GenerationBillingAccountRow(
                    workspace_id=context.workspace_id,
                    currency=offer.currency,
                    available_minor=0,
                    held_minor=0,
                    spent_minor=0,
                    version=1,
                    updated_at=instant,
                )
                session.add(account)
            if account and account.currency != offer.currency:
                raise PaymentFailure("PAYMENT_ACCOUNT_CURRENCY_MISMATCH", 409)
            if account:
                check_account(account)
            if account and account.available_minor > MAX_MINOR - offer.credit_minor:
                raise PaymentFailure("PAYMENT_BALANCE_LIMIT", 409)
            order = BillingOrderRow(
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                order_id=object_id,
                order_type="credit_top_up",
                amount_minor=offer.amount_minor,
                currency=offer.currency,
                status="pending",
                external_reference=None,
                expires_at=instant + timedelta(seconds=self.settings.expiry_seconds),
                paid_at=None,
                closed_at=None,
                refunded_minor=0,
                version=1,
                created_at=instant,
                updated_at=instant,
            )
            linked = PaymentOrderRow(
                order_id=object_id,
                tenant_id=context.tenant_id,
                workspace_id=context.workspace_id,
                offer_id=offer.id,
                provider=self.settings.provider,
                merchant_id=self.settings.merchant_id,
                channel=payload.channel,
                credit_minor=offer.credit_minor,
                state="creating",
                transaction_id=None,
                checkout_url=None,
                checkout_expires_at=None,
                paid_spent_minor=None,
                paid_account_version=None,
                requested_by=context.actor_id,
                version=1,
                created_at=instant,
                updated_at=instant,
            )
            session.add_all([order, linked])
            work.update(
                operation="create_order",
                orderId=object_id,
                channel=payload.channel,
                amountMinor=str(offer.amount_minor),
                currency=offer.currency,
                expiresAt=iso(order.expires_at),
            )
            evidence(session, context, object_id, "order_created", {}, {"note": payload.reason, "offerId": offer.id})
        elif operation == "order":
            order, linked = await get_order(session, context, object_id, lock=True)
            if linked is None:
                raise PaymentFailure("PAYMENT_REFUND_SOURCE_UNVERIFIED", 409)
            if linked.version != payload.expectedVersion:
                raise PaymentFailure("PAYMENT_VERSION_CONFLICT", 409)
            if payload.action == "requestRefund":
                if not payload.reason or payload.amountMinor is None:
                    raise PaymentFailure("PAYMENT_REFUND_REASON_AMOUNT_REQUIRED", 422)
                amount = minor(payload.amountMinor)
                capacity, reason = await refund_capacity(session, context, order, linked, lock=True)
                if amount > capacity:
                    raise PaymentFailure(reason or "PAYMENT_REFUND_AMOUNT_EXCEEDS_AVAILABLE", 409)
                refund_id = str(uuid4())
                refund = PaymentRefundRow(
                    id=refund_id,
                    tenant_id=context.tenant_id,
                    workspace_id=context.workspace_id,
                    order_id=order.order_id,
                    amount_minor=amount,
                    approval_basis_minor=order.amount_minor,
                    currency=order.currency,
                    status="pending_approval",
                    reason=payload.reason,
                    ticket_id=payload.ticketId or refund_id,
                    requested_by=context.actor_id,
                    approved_by=None,
                    provider_refund_id=None,
                    external_identity=None,
                    version=1,
                    created_at=instant,
                    updated_at=instant,
                    completed_at=None,
                )
                session.add(refund)
                await move_refund_reserve(session, context, order, linked, refund, "reserve")
                linked.version += 1
                linked.updated_at = instant
                await session.flush()
                response = {
                    "refund": refund_dto(context, refund, self.settings, self.approval_verifier),
                    "order": await order_dto(session, context, order, linked, configured=self.configured),
                }
                evidence(session, context, object_id, "refund_requested", {}, {**response, "note": payload.reason})
                work = {"operation": "local"}
            else:
                if payload.action == "cancel" and order.status != "pending":
                    raise PaymentFailure("PAYMENT_ORDER_NOT_CANCELLABLE", 409)
                work.update(
                    operation="get_order" if payload.action == "refresh" else "cancel_order",
                    orderId=order.order_id,
                    amountMinor=str(order.amount_minor),
                    currency=order.currency,
                )
                evidence(
                    session,
                    context,
                    order.order_id,
                    "order_" + payload.action + "_requested",
                    {"status": linked.state, "version": linked.version},
                    {"operation": work["operation"], "note": payload.reason or ""},
                )
        else:
            refund = await get_refund(session, context, object_id, lock=True)
            order, linked = await get_order(session, context, refund.order_id, lock=True)
            if linked is None:
                raise PaymentFailure("PAYMENT_REFUND_SOURCE_UNVERIFIED", 409)
            if refund.version != payload.expectedVersion:
                raise PaymentFailure("PAYMENT_VERSION_CONFLICT", 409)
            before = {
                "refundId": refund.id,
                "status": refund.status,
                "version": refund.version,
                "amountMinor": str(refund.amount_minor),
                "approvedBy": refund.approved_by,
            }
            if payload.action == "approve":
                require(context, "admin.finance.approve")
                if refund.requested_by == context.actor_id:
                    raise PaymentFailure("PAYMENT_FOUR_EYES_REQUIRED", 409)
                if refund.status != "pending_approval":
                    raise PaymentFailure("PAYMENT_REFUND_NOT_APPROVABLE", 409)
                await self._approval(context, refund, request)
                refund.approved_by = context.actor_id
                work.update(operation="create_refund")
            elif payload.action == "reject":
                if "admin.finance.approve" not in context.permissions and refund.requested_by != context.actor_id:
                    raise PaymentFailure("PAYMENT_PERMISSION_DENIED", 403)
                if refund.status != "pending_approval":
                    raise PaymentFailure("PAYMENT_REFUND_NOT_REJECTABLE", 409)
                await move_refund_reserve(session, context, order, linked, refund, "release")
                linked.version += 1
                linked.updated_at = instant
                refund.status, refund.completed_at = "rejected", instant
                response = {"refund": refund_dto(context, refund, self.settings, self.approval_verifier)}
                work = {"operation": "local"}
            else:
                if refund.status not in {"processing", "unknown"}:
                    raise PaymentFailure("PAYMENT_REFUND_NOT_PROCESSING", 409)
                work.update(operation="get_refund")
            refund.version += 1
            refund.updated_at = instant
            if payload.action == "approve":
                refund.status = "processing"
            if payload.action == "reject":
                assert response is not None
                response["refund"] = refund_dto(context, refund, self.settings, self.approval_verifier)
            work.update(
                orderId=order.order_id,
                refundId=refund.id,
                amountMinor=str(refund.amount_minor),
                currency=refund.currency,
                transactionId=linked.transaction_id,
            )
            evidence(
                session,
                context,
                order.order_id,
                "refund_" + payload.action,
                before,
                {"refundId": refund.id, "status": refund.status, "note": payload.reason},
            )
        receipt = PaymentCommandRow(
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            actor_id=context.actor_id,
            key=key,
            fingerprint=digest,
            operation=operation,
            object_id=object_id,
            gateway_key=gateway_key,
            request=work,
            response=response,
            created_at=instant,
        )
        session.add(receipt)
        return receipt

    def _approval_policy(self, refund):
        assert self.settings is not None
        threshold = self.settings.refund_mfa_threshold_minor
        if threshold is None:
            raise PaymentFailure("PAYMENT_APPROVAL_POLICY_REQUIRED", 503)
        if refund.approval_basis_minor >= threshold and self.approval_verifier is None:
            raise PaymentFailure("PAYMENT_REFUND_MFA_REQUIRED", 503)

    async def _approval(self, context, refund, request):
        assert self.settings is not None
        require(context, "admin.finance.approve")
        if refund.requested_by == context.actor_id:
            raise PaymentFailure("PAYMENT_FOUR_EYES_REQUIRED", 409)
        self._approval_policy(refund)
        threshold = self.settings.refund_mfa_threshold_minor
        assert threshold is not None
        if refund.approval_basis_minor >= threshold:
            assert self.approval_verifier is not None
            if not await self.approval_verifier.verify(
                context=context,
                refund_id=refund.id,
                amount_minor=refund.amount_minor,
                approval_basis_minor=refund.approval_basis_minor,
                request=request,
            ):
                raise PaymentFailure("PAYMENT_REFUND_MFA_REQUIRED", 503)

    async def _uncertain(self, context, object_id, operation):
        async with self.session() as session, session.begin():
            await lock_workspace(session, context)
            if operation == "refund":
                refund = await get_refund(session, context, object_id, lock=True)
                if refund.status in {"processing", "unknown"}:
                    refund.status = "unknown"
                    refund.version += 1
                    refund.updated_at = now()
            else:
                order, linked = await get_order(session, context, object_id, lock=True)
                if order.status == "pending" and linked is not None:
                    linked.state = "unknown"
                    linked.version += 1
                    linked.updated_at = now()

    async def apply_event(self, payload, *, context=None):
        self.require_gateway()
        assert self.settings is not None
        provider, merchant, event_id, order_id = (
            fact(payload, field) for field in ("provider", "merchantId", "eventId", "orderId")
        )
        if (provider, merchant) != (self.settings.provider, self.settings.merchant_id):
            raise PaymentFailure("PAYMENT_GATEWAY_MERCHANT_MISMATCH", 403)
        event_digest = fingerprint(payload)
        async with self.session() as session:
            linked = await session.get(PaymentOrderRow, order_id)
            if linked is None:
                raise PaymentFailure("PAYMENT_ORDER_NOT_FOUND", 404)
            scope = TrustedWorkspaceContext(
                linked.tenant_id,
                linked.workspace_id,
                "payment-gateway",
                event_id,
                frozenset({"billing.view", "billing.manage"}),
                "SYSTEM",
            )
            if context is not None and (context.tenant_id, context.workspace_id) != (
                linked.tenant_id,
                linked.workspace_id,
            ):
                raise PaymentFailure("PAYMENT_CONTEXT_MISMATCH", 403)
        async with self.session() as session, session.begin():
            await lock_workspace(session, scope)
            old = await session.get(PaymentEventRow, (provider, merchant, event_id))
            if old is not None:
                if old.fingerprint != event_digest:
                    raise PaymentFailure("PAYMENT_EVENT_CONFLICT", 409)
                return old.result
            order, linked = await get_order(session, scope, order_id, lock=True)
            if linked is None:
                raise PaymentFailure("PAYMENT_REFUND_SOURCE_UNVERIFIED", 409)
            if (linked.provider, linked.merchant_id) != (provider, merchant) or payload.get(
                "currency"
            ) != order.currency:
                raise PaymentFailure("PAYMENT_GATEWAY_FACT_MISMATCH", 409)
            before = {"orderStatus": order.status, "gatewayState": linked.state}
            if payload.get("refundId"):
                refund = await get_refund(session, scope, fact(payload, "refundId"), lock=True)
                before["refund"] = {
                    "id": refund.id,
                    "status": refund.status,
                    "version": refund.version,
                    "amountMinor": str(refund.amount_minor),
                    "approvedBy": refund.approved_by,
                }
                if refund.order_id != order_id or minor(payload.get("amountMinor", "0")) != refund.amount_minor:
                    raise PaymentFailure("PAYMENT_GATEWAY_FACT_MISMATCH", 409)
                if payload.get("transactionId") != linked.transaction_id:
                    raise PaymentFailure("PAYMENT_GATEWAY_TRANSACTION_MISMATCH", 409)
                state = payload.get("state")
                if state not in {"processing", "unknown", "succeeded", "failed"}:
                    raise PaymentFailure("PAYMENT_GATEWAY_FACT_INVALID", 422)
                if refund.status == "pending_approval" or refund.approved_by is None:
                    raise PaymentFailure("PAYMENT_REFUND_NOT_APPROVED", 409)
                if refund.status in {"succeeded", "failed", "rejected"}:
                    if refund.status != state:
                        raise PaymentFailure("PAYMENT_GATEWAY_STATE_CONFLICT", 409)
                    if state == "succeeded" and payload.get("refundTransactionId") != refund.provider_refund_id:
                        raise PaymentFailure("PAYMENT_EXTERNAL_TRANSACTION_REUSED", 409)
                else:
                    if state == "succeeded":
                        external = fact(payload, "refundTransactionId")
                        external_identity = fingerprint([provider, merchant, external])
                        duplicate = await session.scalar(
                            select(PaymentRefundRow.id).where(
                                PaymentRefundRow.external_identity == external_identity,
                                PaymentRefundRow.id != refund.id,
                            )
                        )
                        if duplicate:
                            raise PaymentFailure("PAYMENT_EXTERNAL_TRANSACTION_REUSED", 409)
                        await move_refund_reserve(session, scope, order, linked, refund, "settle")
                        refund.provider_refund_id, refund.external_identity = external, external_identity
                        order.refunded_minor += refund.amount_minor
                        if order.refunded_minor > order.amount_minor:
                            raise PaymentFailure("PAYMENT_REFUND_AMOUNT_EXCEEDS_AVAILABLE", 409)
                        if order.refunded_minor == order.amount_minor:
                            order.status, linked.state = "refunded", "refunded"
                        order.version += 1
                        order.updated_at = now()
                    elif state == "failed":
                        await move_refund_reserve(session, scope, order, linked, refund, "release")
                    refund.status, refund.updated_at = state, now()
                    refund.version += 1
                    if state in {"succeeded", "failed"}:
                        refund.completed_at = now()
                linked.version += 1
                linked.updated_at = now()
                result = {"refundId": refund.id, "status": refund.status}
            else:
                if minor(payload.get("amountMinor", "0")) != order.amount_minor:
                    raise PaymentFailure("PAYMENT_GATEWAY_FACT_MISMATCH", 409)
                state = payload.get("state")
                if state not in {"pending", "processing", "unknown", "paid", "closed", "failed"}:
                    raise PaymentFailure("PAYMENT_GATEWAY_FACT_INVALID", 422)
                if state == "paid":
                    transaction = fact(payload, "transactionId")
                    duplicate = await session.scalar(
                        select(PaymentOrderRow.order_id).where(
                            PaymentOrderRow.provider == provider,
                            PaymentOrderRow.merchant_id == merchant,
                            PaymentOrderRow.transaction_id == transaction,
                            PaymentOrderRow.order_id != order_id,
                        )
                    )
                    if duplicate or (linked.transaction_id and linked.transaction_id != transaction):
                        raise PaymentFailure("PAYMENT_EXTERNAL_TRANSACTION_REUSED", 409)
                    if linked.transaction_id is None:
                        account = await session.get(
                            GenerationBillingAccountRow, scope.workspace_id, with_for_update=True
                        )
                        if account is None:
                            account = GenerationBillingAccountRow(
                                workspace_id=scope.workspace_id,
                                currency=order.currency,
                                available_minor=0,
                                held_minor=0,
                                spent_minor=0,
                                version=1,
                                updated_at=now(),
                            )
                            session.add(account)
                        if account.currency != order.currency:
                            raise PaymentFailure("PAYMENT_ACCOUNT_CURRENCY_MISMATCH", 409)
                        check_account(account)
                        if account.available_minor > MAX_MINOR - linked.credit_minor:
                            raise PaymentFailure("PAYMENT_BALANCE_LIMIT", 409)
                        account.available_minor += linked.credit_minor
                        account.version += 1
                        account.updated_at = now()
                        linked.paid_spent_minor, linked.paid_account_version = account.spent_minor, account.version
                        linked.transaction_id = transaction
                        order.status, order.external_reference, order.paid_at = "paid", transaction, now()
                        order.version += 1
                        journal(
                            session,
                            scope,
                            "payment-grant:" + order_id,
                            "payment_grant",
                            linked.credit_minor,
                            order.currency,
                            [
                                {"account": "workspace.available", "delta_minor": linked.credit_minor},
                                {"account": "payment.clearing", "delta_minor": -linked.credit_minor},
                            ],
                            order_id,
                        )
                    linked.state = "refunded" if order.status == "refunded" else "paid"
                elif order.status in {"paid", "refunded"}:
                    raise PaymentFailure("PAYMENT_GATEWAY_STATE_CONFLICT", 409)
                elif order.status in {"closed", "failed"} and state != order.status:
                    raise PaymentFailure("PAYMENT_GATEWAY_STATE_CONFLICT", 409)
                else:
                    linked.state = state
                    if state in {"failed", "closed"}:
                        order.status = state
                        order.closed_at = now()
                        order.version += 1
                    url = checkout_url(payload.get("checkoutUrl"), self.settings)
                    if url:
                        try:
                            expiry = datetime.fromisoformat(str(payload.get("expiresAt", "")))
                        except ValueError as error:
                            raise PaymentFailure("PAYMENT_GATEWAY_FACT_INVALID", 422) from error
                        if expiry.tzinfo is None:
                            raise PaymentFailure("PAYMENT_GATEWAY_FACT_INVALID", 422)
                        linked.checkout_url = url
                        linked.checkout_expires_at = min(
                            expiry,
                            order.expires_at.replace(tzinfo=UTC)
                            if order.expires_at.tzinfo is None
                            else order.expires_at,
                        )
                linked.version += 1
                linked.updated_at, order.updated_at = now(), now()
                result = {"orderId": order_id, "status": linked.state}
            session.add(
                PaymentEventRow(
                    provider=provider,
                    merchant_id=merchant,
                    event_id=event_id,
                    fingerprint=event_digest,
                    order_id=order_id,
                    result=result,
                    created_at=now(),
                )
            )
            evidence(session, scope, order_id, "gateway_event", before, {**result, "eventId": event_id})
            return result

    async def callback(self, raw, headers):
        self.require_gateway()
        assert self.gateway is not None
        return await self.apply_event(self.gateway.verify_event(raw, headers))


def create_production_payment_runtime(
    *, database_url=None, context_resolver=None, gateway=None, settings=None, approval_verifier=None
):
    resolver = context_resolver or TrustedWorkspaceContextResolver(PlatformSessionGateway())
    url = database_url if database_url is not None else os.environ.get("XINGJING_PAYMENT_DATABASE_URL", "").strip()
    unavailable = None
    try:
        settings = settings or load_settings()
    except PaymentFailure as error:
        unavailable = error.code
        settings = None
    if not url:
        return PaymentRuntime(None, resolver, unavailable_code=unavailable or "PAYMENT_DATABASE_NOT_CONFIGURED")
    try:
        parsed = make_url(url)
        driver = {"sqlite": "sqlite+aiosqlite", "postgresql": "postgresql+asyncpg"}.get(parsed.get_backend_name())
        if driver is None:
            raise ValueError("unsupported payment database")
        engine = create_async_engine(parsed.set(drivername=driver), pool_pre_ping=True)
    except (ValueError, ImportError, SQLAlchemyError):
        return PaymentRuntime(None, resolver, unavailable_code="PAYMENT_DATABASE_UNAVAILABLE")
    return PaymentRuntime(
        async_sessionmaker(engine, expire_on_commit=False),
        resolver,
        engine=engine,
        gateway=gateway,
        settings=settings,
        approval_verifier=approval_verifier,
        unavailable_code=unavailable,
    )
