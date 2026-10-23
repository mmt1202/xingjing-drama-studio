"""Scoped payment projections and atomic postings into the existing shared account."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select, update

from server.xingjing_analytics.database import identity
from server.xingjing_audio_persistence.models import audio_billing_holds
from server.xingjing_billing_persistence.models import BillingOrderRow
from server.xingjing_editing_persistence.models import EditingRenderBillingHoldRow
from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingHoldRow,
    GenerationBillingJournalRow,
)
from server.xingjing_platform_persistence.persistence import OutboxRow
from server.xingjing_team_persistence.repository import WorkspaceRow

from .contracts import MAX_MINOR, PaymentFailure
from .models import PaymentAuditRow, PaymentOrderRow, PaymentRefundRow


def now():
    return datetime.now(UTC)


def iso(value):
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def scoped(model, context):
    return model.tenant_id == context.tenant_id, model.workspace_id == context.workspace_id


def require(context, permission="billing.view"):
    if context.role.casefold() == "api_key":
        raise PaymentFailure("PAYMENT_INTERACTIVE_SESSION_REQUIRED", 403)
    if "billing.view" not in context.permissions or permission not in context.permissions:
        raise PaymentFailure("PAYMENT_PERMISSION_DENIED", 403)


def same_database(bound, variables=None):
    variables = variables or (
        "XINGJING_PAYMENT_DATABASE_URL",
        "XINGJING_BILLING_DATABASE_URL",
        "XINGJING_GENERATION_DATABASE_URL",
        "XINGJING_AUDIO_DATABASE_URL",
        "XINGJING_EDITING_DATABASE_URL",
        "XINGJING_TEAM_DATABASE_URL",
        "XINGJING_FINANCE_DATABASE_URL",
    )
    for name in variables:
        configured = os.environ.get(name, "").strip()
        if configured:
            try:
                if identity(bound) != identity(configured):
                    raise ValueError("source mismatch")
            except (ValueError, TypeError) as error:
                raise PaymentFailure("PAYMENT_SOURCE_DATABASE_MISMATCH", 503) from error


async def verify_workspace(session, context):
    same_database(session.bind.url)
    tenants = list(
        (
            await session.scalars(
                select(WorkspaceRow.tenant_id).where(WorkspaceRow.workspace_id == context.workspace_id)
            )
        ).all()
    )
    if tenants != [context.tenant_id]:
        raise PaymentFailure("PAYMENT_WORKSPACE_SOURCE_UNVERIFIED", 503)


async def lock_workspace(session, context):
    await verify_workspace(session, context)
    await session.execute(
        update(WorkspaceRow)
        .where(*scoped(WorkspaceRow, context))
        .values(permission_version=WorkspaceRow.permission_version)
    )
    await session.scalar(select(WorkspaceRow.workspace_id).where(*scoped(WorkspaceRow, context)).with_for_update())


async def get_order(session, context, order_id, *, lock=False):
    statement = select(BillingOrderRow).where(*scoped(BillingOrderRow, context), BillingOrderRow.order_id == order_id)
    if lock:
        statement = statement.with_for_update()
    order = await session.scalar(statement)
    if order is None:
        raise PaymentFailure("PAYMENT_ORDER_NOT_FOUND", 404)
    linked = await session.get(PaymentOrderRow, order_id, with_for_update=lock)
    if linked is not None and (linked.tenant_id, linked.workspace_id) != (context.tenant_id, context.workspace_id):
        linked = None
    return order, linked


async def get_refund(session, context, refund_id, *, lock=False):
    row = await session.scalar(
        select(PaymentRefundRow)
        .where(*scoped(PaymentRefundRow, context), PaymentRefundRow.id == refund_id)
        .with_for_update()
        if lock
        else select(PaymentRefundRow).where(*scoped(PaymentRefundRow, context), PaymentRefundRow.id == refund_id)
    )
    if row is None:
        raise PaymentFailure("PAYMENT_REFUND_NOT_FOUND", 404)
    return row


async def refund_capacity(session, context, order, linked, *, lock=False):
    if linked is None or linked.paid_spent_minor is None or linked.transaction_id is None:
        return 0, "PAYMENT_REFUND_SOURCE_UNVERIFIED"
    if order.status != "paid":
        return 0, "PAYMENT_ORDER_NOT_REFUNDABLE"
    account = await session.get(GenerationBillingAccountRow, context.workspace_id, with_for_update=lock)
    if account is None or account.currency != order.currency:
        return 0, "PAYMENT_ACCOUNT_CURRENCY_MISMATCH"
    if account.spent_minor != linked.paid_spent_minor:
        return 0, "PAYMENT_REFUND_CREDITS_ALREADY_USED"
    if linked.paid_account_version is None or account.version != linked.paid_account_version:
        return 0, "PAYMENT_REFUND_ACCOUNT_CHANGED"
    for table, conditions in (
        (GenerationBillingHoldRow, [GenerationBillingHoldRow.workspace_id == context.workspace_id]),
        (EditingRenderBillingHoldRow, list(scoped(EditingRenderBillingHoldRow, context))),
    ):
        if await session.scalar(select(func.count()).select_from(table).where(*conditions, table.status == "active")):
            return 0, "PAYMENT_REFUND_ACTIVE_BUSINESS_HOLDS"
    if await session.scalar(
        select(func.count())
        .select_from(audio_billing_holds)
        .where(
            audio_billing_holds.c.tenant_id == context.tenant_id,
            audio_billing_holds.c.workspace_id == context.workspace_id,
            audio_billing_holds.c.status == "active",
        )
    ):
        return 0, "PAYMENT_REFUND_ACTIVE_BUSINESS_HOLDS"
    pending = await session.scalar(
        select(func.coalesce(func.sum(PaymentRefundRow.amount_minor), 0)).where(
            *scoped(PaymentRefundRow, context),
            PaymentRefundRow.order_id == order.order_id,
            PaymentRefundRow.status.in_(["pending_approval", "processing", "unknown"]),
        )
    )
    amount = min(order.amount_minor - order.refunded_minor - pending, account.available_minor)
    return max(0, amount), None if amount > 0 else "PAYMENT_REFUND_BALANCE_INSUFFICIENT"


async def order_dto(session, context, order, linked, *, configured):
    capacity, reason = await refund_capacity(session, context, order, linked)
    manageable = "billing.manage" in context.permissions and configured
    instant = now()
    checkout = (
        linked.checkout_url
        if linked
        and linked.state == "pending"
        and linked.checkout_expires_at
        and (
            linked.checkout_expires_at.replace(tzinfo=UTC)
            if linked.checkout_expires_at.tzinfo is None
            else linked.checkout_expires_at
        )
        > instant
        else None
    )
    result = {
        "id": order.order_id,
        "offerId": linked.offer_id if linked else "",
        "channel": linked.channel if linked else "",
        "provider": linked.provider if linked else "unknown",
        "amountMinor": str(order.amount_minor),
        "creditMinor": str(linked.credit_minor) if linked else None,
        "currency": order.currency,
        "status": linked.state if linked else order.status,
        "version": linked.version if linked else order.version,
        "createdAt": iso(order.created_at),
        "updatedAt": iso(linked.updated_at if linked else order.updated_at),
        "expiresAt": iso(order.expires_at),
        "paidAt": iso(order.paid_at),
        "refundedMinor": str(order.refunded_minor),
        "refundableMinor": str(capacity),
        "checkout": {"url": checkout, "expiresAt": iso(linked.checkout_expires_at) if checkout else None},
        "actions": {
            "canRefresh": manageable and linked is not None,
            "canCancel": manageable and linked is not None and order.status == "pending",
            "canRequestRefund": manageable and capacity > 0,
        },
    }
    if reason:
        result["refundReasonCode"] = reason
    return result


def refund_dto(context, row, settings, verifier):
    approve = "billing.manage" in context.permissions and "admin.finance.approve" in context.permissions
    reason = None
    if settings is None:
        reason = "PAYMENT_GATEWAY_NOT_CONFIGURED"
    elif settings.refund_mfa_threshold_minor is None:
        reason = "PAYMENT_APPROVAL_POLICY_REQUIRED"
    elif row.approval_basis_minor >= settings.refund_mfa_threshold_minor and verifier is None:
        reason = "PAYMENT_REFUND_MFA_REQUIRED"
    elif row.requested_by == context.actor_id:
        reason = "PAYMENT_FOUR_EYES_REQUIRED"
    elif not approve:
        reason = "PAYMENT_PERMISSION_DENIED"
    result = {
        "id": row.id,
        "orderId": row.order_id,
        "amountMinor": str(row.amount_minor),
        "approvalBasisMinor": str(row.approval_basis_minor),
        "currency": row.currency,
        "status": row.status,
        "version": row.version,
        "requestedBy": row.requested_by,
        "approvedBy": row.approved_by,
        "reason": row.reason,
        "ticketId": row.ticket_id,
        "createdAt": iso(row.created_at),
        "updatedAt": iso(row.updated_at),
        "completedAt": iso(row.completed_at),
        "actions": {
            "canApprove": row.status == "pending_approval" and reason is None,
            "canReject": settings is not None
            and row.status == "pending_approval"
            and (approve or row.requested_by == context.actor_id),
            "canRefresh": settings is not None
            and row.status in {"processing", "unknown"}
            and "billing.manage" in context.permissions,
        },
    }
    if reason:
        result["reasonCode"] = reason
    return result


def evidence(session, context, order_id, action, before, after):
    def redact(value):
        if isinstance(value, dict):
            return {
                key: (
                    {"available": bool(item.get("url")), "expiresAt": item.get("expiresAt")}
                    if key == "checkout" and isinstance(item, dict)
                    else redact(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    event_id, instant = str(uuid4()), now()
    session.add(
        PaymentAuditRow(
            id=event_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            order_id=order_id,
            actor_id=context.actor_id,
            request_id=context.request_id,
            action=action,
            before=redact(before),
            after=redact(after),
            created_at=instant,
        )
    )
    session.add(
        OutboxRow(
            id=event_id,
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            event_type="payment." + action,
            aggregate_type="payment_order",
            aggregate_id=order_id,
            payload={"orderId": order_id, "actorId": context.actor_id, "requestId": context.request_id},
            schema_version=1,
            created_at=instant,
        )
    )


def journal(session, context, event_id, action, amount, currency, postings, reference):
    session.add(
        GenerationBillingJournalRow(
            event_id=event_id,
            workspace_id=context.workspace_id,
            project_id="",
            task_id=None,
            action=action,
            currency=currency,
            amount_minor=amount,
            postings=postings,
            reference=reference,
            occurred_at=now(),
        )
    )


async def move_refund_reserve(session, context, order, linked, refund, mode):
    account = await session.get(GenerationBillingAccountRow, context.workspace_id, with_for_update=True)
    if account is None or account.currency != refund.currency:
        raise PaymentFailure("PAYMENT_ACCOUNT_CURRENCY_MISMATCH", 409)
    check_account(account)
    clean = linked.paid_account_version == account.version
    amount = refund.amount_minor
    if mode == "reserve":
        if account.available_minor < amount:
            raise PaymentFailure("PAYMENT_REFUND_BALANCE_INSUFFICIENT", 409)
        if account.held_minor > MAX_MINOR - amount:
            raise PaymentFailure("PAYMENT_BALANCE_LIMIT", 409)
        account.available_minor -= amount
        account.held_minor += amount
        postings = [
            {"account": "workspace.available", "delta_minor": -amount},
            {"account": "refund.held", "delta_minor": amount},
        ]
    else:
        if account.held_minor < amount:
            raise PaymentFailure("PAYMENT_REFUND_RESERVE_MISSING", 409)
        account.held_minor -= amount
        if mode == "release":
            if account.available_minor > MAX_MINOR - amount:
                raise PaymentFailure("PAYMENT_BALANCE_LIMIT", 409)
            account.available_minor += amount
        postings = [
            {"account": "refund.held", "delta_minor": -amount},
            {"account": "workspace.available" if mode == "release" else "payment.clearing", "delta_minor": amount},
        ]
    account.version += 1
    account.updated_at = now()
    linked.paid_account_version = account.version if clean else None
    journal(
        session,
        context,
        "payment-refund:" + refund.id + ":" + mode,
        "refund_" + mode,
        amount,
        refund.currency,
        postings,
        refund.id,
    )


def check_account(account):
    if any(
        type(value) is not int or value < 0 or value > MAX_MINOR
        for value in (account.available_minor, account.held_minor, account.spent_minor)
    ):
        raise PaymentFailure("PAYMENT_ACCOUNT_INVALID", 503)
