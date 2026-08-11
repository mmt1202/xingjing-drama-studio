from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingJournalRow,
)

from .models import BillingActionReceiptRow, BillingAuditRow, BillingOrderRow, InvoiceRequestRow, TeamPlanRow


class BillingIdempotencyConflict(RuntimeError):
    pass


class BillingFinanceRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: str,
        workspace_id: str,
    ) -> None:
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._workspace_id = workspace_id

    async def plan(self) -> dict[str, object] | None:
        async with self._session_factory() as session:
            row = await session.get(TeamPlanRow, (self._tenant_id, self._workspace_id))
        if row is None:
            return None
        return {
            "plan_id": row.plan_id,
            "status": row.status,
            "seat_limit": row.seat_limit,
            "features": list(row.features),
            "quotas": dict(row.quotas),
            "quota_remaining": dict(row.quota_remaining),
            "version": row.version,
            "updated_at": _utc(row.updated_at).isoformat(),
        }

    async def invoices(self) -> tuple[dict[str, object], ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(InvoiceRequestRow)
                    .where(
                        InvoiceRequestRow.tenant_id == self._tenant_id,
                        InvoiceRequestRow.workspace_id == self._workspace_id,
                    )
                    .order_by(InvoiceRequestRow.created_at.desc(), InvoiceRequestRow.invoice_id.desc())
                )
            ).all()
        return tuple(_invoice(row) for row in rows)

    async def orders(self) -> tuple[dict[str, object], ...]:
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    select(BillingOrderRow)
                    .where(
                        BillingOrderRow.tenant_id == self._tenant_id,
                        BillingOrderRow.workspace_id == self._workspace_id,
                    )
                    .order_by(BillingOrderRow.created_at.desc(), BillingOrderRow.order_id.desc())
                )
            ).all()
        return tuple(_order(row) for row in rows)

    async def audit_events(
        self,
        *,
        request_id: str | None = None,
        actor_id: str | None = None,
        object_id: str | None = None,
    ) -> tuple[dict[str, object], ...]:
        query = select(BillingAuditRow).where(
            BillingAuditRow.tenant_id == self._tenant_id,
            BillingAuditRow.workspace_id == self._workspace_id,
        )
        if request_id:
            query = query.where(BillingAuditRow.request_id == request_id)
        if actor_id:
            query = query.where(BillingAuditRow.actor_id == actor_id)
        if object_id:
            query = query.where(BillingAuditRow.object_id == object_id)
        async with self._session_factory() as session:
            rows = (
                await session.scalars(
                    query.order_by(BillingAuditRow.occurred_at.desc(), BillingAuditRow.event_id.desc())
                )
            ).all()
        return tuple(_audit(row) for row in rows)

    async def create_payment_order(
        self,
        *,
        order_id: str,
        amount_minor: int,
        currency: str,
        order_type: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        expires_at: datetime,
        occurred_at: datetime,
    ) -> dict[str, object]:
        if amount_minor <= 0 or len(currency) != 3 or _utc(expires_at) <= _utc(occurred_at):
            raise ValueError("PAYMENT_ORDER_INVALID")
        command = {
            "order_id": order_id,
            "amount_minor": amount_minor,
            "currency": currency.upper(),
            "order_type": order_type,
            "actor_id": actor_id,
            "expires_at": _utc(expires_at).isoformat(),
        }
        digest = _digest(command)
        now = _utc(occurred_at)
        async with self._session_factory.begin() as session:
            receipt = await session.get(
                BillingActionReceiptRow,
                (self._tenant_id, self._workspace_id, "payment.order.create", idempotency_key),
            )
            if receipt is not None:
                if receipt.command_digest != digest:
                    raise BillingIdempotencyConflict()
                return dict(receipt.result_payload)
            if await session.get(BillingOrderRow, (self._tenant_id, self._workspace_id, order_id)) is not None:
                raise BillingIdempotencyConflict()
            row = BillingOrderRow(
                tenant_id=self._tenant_id,
                workspace_id=self._workspace_id,
                order_id=order_id,
                order_type=order_type,
                amount_minor=amount_minor,
                currency=currency.upper(),
                status="pending",
                external_reference=None,
                expires_at=_utc(expires_at),
                paid_at=None,
                closed_at=None,
                refunded_minor=0,
                version=1,
                created_at=now,
                updated_at=now,
            )
            result = _order(row)
            session.add(row)
            session.add(self._receipt("payment.order.create", idempotency_key, digest, result, now))
            session.add(self._audit_row(actor_id, "payment.order_created", "payment_order", order_id, request_id, {}, result, now))
        return result

    async def apply_payment_callback(
        self,
        *,
        order_id: str,
        event_id: str,
        external_reference: str,
        paid_minor: int,
        occurred_at: datetime,
    ) -> dict[str, object]:
        command = {"order_id": order_id, "external_reference": external_reference, "paid_minor": paid_minor}
        digest = _digest(command)
        now = _utc(occurred_at)
        async with self._session_factory.begin() as session:
            receipt = await session.get(
                BillingActionReceiptRow,
                (self._tenant_id, self._workspace_id, "payment.callback", event_id),
            )
            if receipt is not None:
                if receipt.command_digest != digest:
                    raise BillingIdempotencyConflict()
                return dict(receipt.result_payload)
            order = await session.get(
                BillingOrderRow,
                (self._tenant_id, self._workspace_id, order_id),
                with_for_update=True,
            )
            if order is None:
                raise LookupError("PAYMENT_ORDER_NOT_FOUND")
            if paid_minor != order.amount_minor:
                raise ValueError("PAYMENT_AMOUNT_MISMATCH")
            before = _order(order)
            if order.status == "pending":
                if _utc(order.expires_at) <= now:
                    order.status, order.closed_at = "closed", now
                    order.version += 1
                    order.updated_at = now
                    raise ValueError("PAYMENT_ORDER_EXPIRED")
                account = await session.get(GenerationBillingAccountRow, self._workspace_id, with_for_update=True)
                if account is None:
                    account = GenerationBillingAccountRow(
                        workspace_id=self._workspace_id,
                        currency=order.currency,
                        available_minor=0,
                        held_minor=0,
                        spent_minor=0,
                        version=1,
                        updated_at=now,
                    )
                    session.add(account)
                if account.currency != order.currency:
                    raise ValueError("PAYMENT_CURRENCY_MISMATCH")
                account.available_minor += paid_minor
                account.version += 1
                account.updated_at = now
                order.status = "paid"
                order.external_reference = external_reference
                order.paid_at = now
                order.version += 1
                order.updated_at = now
                session.add(
                    GenerationBillingJournalRow(
                        event_id=f"payment:{event_id}",
                        workspace_id=self._workspace_id,
                        project_id="",
                        task_id=None,
                        action="grant",
                        currency=order.currency,
                        amount_minor=paid_minor,
                        postings=[
                            {"account": "workspace.available", "delta_minor": paid_minor},
                            {"account": "payment.clearing", "delta_minor": -paid_minor},
                        ],
                        reference=external_reference,
                        occurred_at=now,
                    )
                )
            elif order.status != "paid" or order.external_reference != external_reference:
                raise ValueError("PAYMENT_ORDER_NOT_PAYABLE")
            result = _order(order)
            session.add(self._receipt("payment.callback", event_id, digest, result, now))
            session.add(self._audit_row("payment-provider", "payment.order_paid", "payment_order", order_id, event_id, before, result, now))
        return result

    async def refund_payment_order(
        self,
        *,
        order_id: str,
        amount_minor: int,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> dict[str, object]:
        if amount_minor <= 0:
            raise ValueError("PAYMENT_REFUND_AMOUNT_INVALID")
        digest = _digest({"order_id": order_id, "amount_minor": amount_minor, "actor_id": actor_id})
        now = _utc(occurred_at)
        async with self._session_factory.begin() as session:
            receipt = await session.get(
                BillingActionReceiptRow,
                (self._tenant_id, self._workspace_id, "payment.refund", idempotency_key),
            )
            if receipt is not None:
                if receipt.command_digest != digest:
                    raise BillingIdempotencyConflict()
                return dict(receipt.result_payload)
            order = await session.get(BillingOrderRow, (self._tenant_id, self._workspace_id, order_id), with_for_update=True)
            if order is None or order.status != "paid" or order.refunded_minor + amount_minor > order.amount_minor:
                raise ValueError("PAYMENT_ORDER_NOT_REFUNDABLE")
            account = await session.get(GenerationBillingAccountRow, self._workspace_id, with_for_update=True)
            if account is None or account.available_minor < amount_minor:
                raise ValueError("PAYMENT_REFUND_CREDITS_ALREADY_USED")
            before = _order(order)
            account.available_minor -= amount_minor
            account.version += 1
            account.updated_at = now
            order.refunded_minor += amount_minor
            if order.refunded_minor == order.amount_minor:
                order.status = "refunded"
            order.version += 1
            order.updated_at = now
            session.add(
                GenerationBillingJournalRow(
                    event_id=f"refund:{idempotency_key}",
                    workspace_id=self._workspace_id,
                    project_id="",
                    task_id=None,
                    action="refund",
                    currency=order.currency,
                    amount_minor=amount_minor,
                    postings=[
                        {"account": "workspace.available", "delta_minor": -amount_minor},
                        {"account": "payment.clearing", "delta_minor": amount_minor},
                    ],
                    reference=order.external_reference or order.order_id,
                    occurred_at=now,
                )
            )
            result = _order(order)
            session.add(self._receipt("payment.refund", idempotency_key, digest, result, now))
            session.add(self._audit_row(actor_id, "payment.order_refunded", "payment_order", order_id, request_id, before, result, now))
        return result

    async def available_credits(self) -> int:
        async with self._session_factory() as session:
            account = await session.get(GenerationBillingAccountRow, self._workspace_id)
        return 0 if account is None else account.available_minor

    async def close_expired_payment_orders(self, *, now: datetime, limit: int = 100) -> int:
        if limit < 1 or limit > 1_000:
            raise ValueError("PAYMENT_CLOSE_LIMIT_INVALID")
        instant = _utc(now)
        async with self._session_factory.begin() as session:
            rows = list(
                (
                    await session.scalars(
                        select(BillingOrderRow)
                        .where(
                            BillingOrderRow.tenant_id == self._tenant_id,
                            BillingOrderRow.workspace_id == self._workspace_id,
                            BillingOrderRow.status == "pending",
                            BillingOrderRow.expires_at <= instant,
                        )
                        .order_by(BillingOrderRow.expires_at, BillingOrderRow.order_id)
                        .limit(limit)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for order in rows:
                before = _order(order)
                order.status = "closed"
                order.closed_at = instant
                order.version += 1
                order.updated_at = instant
                session.add(
                    self._audit_row(
                        "billing-sweeper",
                        "payment.order_closed",
                        "payment_order",
                        order.order_id,
                        f"payment-close:{order.order_id}:{order.version}",
                        before,
                        _order(order),
                        instant,
                    )
                )
        return len(rows)

    def _receipt(
        self, action: str, key: str, digest: str, result: dict[str, object], occurred_at: datetime
    ) -> BillingActionReceiptRow:
        return BillingActionReceiptRow(
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            action=action,
            idempotency_key=key,
            command_digest=digest,
            result_payload=result,
            created_at=occurred_at,
        )

    def _audit_row(
        self,
        actor_id: str,
        action: str,
        object_type: str,
        object_id: str,
        request_id: str,
        before: dict[str, object],
        after: dict[str, object],
        occurred_at: datetime,
    ) -> BillingAuditRow:
        return BillingAuditRow(
            tenant_id=self._tenant_id,
            workspace_id=self._workspace_id,
            event_id=str(uuid4()),
            actor_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=object_id,
            request_id=request_id,
            before_payload=before,
            after_payload=after,
            occurred_at=occurred_at,
        )

    async def request_invoice(
        self,
        *,
        invoice_title: str,
        amount_minor: int,
        currency: str,
        actor_id: str,
        request_id: str,
        idempotency_key: str,
        occurred_at: datetime,
    ) -> dict[str, object]:
        command = {
            "invoice_title": invoice_title,
            "amount_minor": amount_minor,
            "currency": currency,
            "actor_id": actor_id,
        }
        digest = _digest(command)
        now = _utc(occurred_at)
        async with self._session_factory.begin() as session:
            # Serialize the exact scoped idempotency key before checking it. This
            # keeps invoice + receipt + audit atomic under concurrent retries.
            await session.execute(
                select(
                    func.pg_advisory_xact_lock(
                        cast(
                            _lock_key(self._tenant_id, self._workspace_id, "invoice.request", idempotency_key),
                            BigInteger,
                        )
                    )
                )
            )
            receipt = await session.get(
                BillingActionReceiptRow,
                (self._tenant_id, self._workspace_id, "invoice.request", idempotency_key),
            )
            if receipt is not None:
                if receipt.command_digest != digest:
                    raise BillingIdempotencyConflict()
                return dict(receipt.result_payload)
            invoice_id = str(uuid4())
            row = InvoiceRequestRow(
                tenant_id=self._tenant_id,
                workspace_id=self._workspace_id,
                invoice_id=invoice_id,
                invoice_title=invoice_title,
                amount_minor=amount_minor,
                currency=currency,
                status="pending",
                requested_by=actor_id,
                request_id=request_id,
                created_at=now,
                updated_at=now,
            )
            result = _invoice(row)
            session.add(row)
            session.add(
                BillingActionReceiptRow(
                    tenant_id=self._tenant_id,
                    workspace_id=self._workspace_id,
                    action="invoice.request",
                    idempotency_key=idempotency_key,
                    command_digest=digest,
                    result_payload=result,
                    created_at=now,
                )
            )
            session.add(
                BillingAuditRow(
                    tenant_id=self._tenant_id,
                    workspace_id=self._workspace_id,
                    event_id=str(uuid4()),
                    actor_id=actor_id,
                    action="invoice.requested",
                    object_type="invoice_request",
                    object_id=invoice_id,
                    request_id=request_id,
                    before_payload={},
                    after_payload=result,
                    occurred_at=now,
                )
            )
        return result

    async def record_export(
        self,
        *,
        actor_id: str,
        request_id: str,
        dataset: str,
        row_count: int,
        occurred_at: datetime,
    ) -> None:
        now = _utc(occurred_at)
        async with self._session_factory.begin() as session:
            session.add(
                BillingAuditRow(
                    tenant_id=self._tenant_id,
                    workspace_id=self._workspace_id,
                    event_id=str(uuid4()),
                    actor_id=actor_id,
                    action="billing.exported",
                    object_type="billing_export",
                    object_id=request_id,
                    request_id=request_id,
                    before_payload={},
                    after_payload={"dataset": dataset, "row_count": row_count},
                    occurred_at=now,
                )
            )


def _invoice(row: InvoiceRequestRow) -> dict[str, object]:
    return {
        "id": row.invoice_id,
        "document_type": "invoice",
        "business_number": row.invoice_id,
        "invoice_title": row.invoice_title,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "status": row.status,
        "requested_by": row.requested_by,
        "request_id": row.request_id,
        "occurred_at": _utc(row.created_at).isoformat(),
        "updated_at": _utc(row.updated_at).isoformat(),
    }


def _order(row: BillingOrderRow) -> dict[str, object]:
    return {
        "id": row.order_id,
        "tenant_id": row.tenant_id,
        "workspace_id": row.workspace_id,
        "document_type": "order",
        "business_number": row.order_id,
        "invoice_title": row.order_type,
        "amount_minor": row.amount_minor,
        "currency": row.currency,
        "status": row.status,
        "external_reference": row.external_reference,
        "expires_at": _utc(row.expires_at).isoformat(),
        "paid_at": None if row.paid_at is None else _utc(row.paid_at).isoformat(),
        "closed_at": None if row.closed_at is None else _utc(row.closed_at).isoformat(),
        "refunded_minor": row.refunded_minor,
        "version": row.version,
        "occurred_at": _utc(row.created_at).isoformat(),
        "updated_at": _utc(row.updated_at).isoformat(),
    }


def _digest(value: dict[str, object]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


def _lock_key(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


def _audit(row: BillingAuditRow) -> dict[str, object]:
    return {
        "id": row.event_id,
        "event_id": row.event_id,
        "actor_id": row.actor_id,
        "action": row.action,
        "object_type": row.object_type,
        "object_id": row.object_id,
        "request_id": row.request_id,
        "before": dict(row.before_payload),
        "after": dict(row.after_payload),
        "occurred_at": _utc(row.occurred_at).isoformat(),
    }


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
