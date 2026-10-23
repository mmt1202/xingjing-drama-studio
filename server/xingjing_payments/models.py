"""Gateway evidence and refund reserves supplement the canonical billing ledger."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class PaymentBase(DeclarativeBase):
    pass


class PaymentOrderRow(PaymentBase):
    __tablename__ = "xingjing_payment_gateway_orders"
    __table_args__ = (
        UniqueConstraint("provider", "merchant_id", "transaction_id", name="uq_xj_payment_external_transaction"),
        Index("ix_xj_payment_orders_scope", "tenant_id", "workspace_id", "created_at", "order_id"),
        CheckConstraint("credit_minor > 0 AND version >= 1", name="ck_xj_payment_order_amount_version"),
        CheckConstraint(
            "state IN ('creating','pending','processing','unknown','paid','closed','failed','refunded')",
            name="ck_xj_payment_order_state",
        ),
    )
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(128), nullable=False)
    merchant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(String(128), nullable=False)
    credit_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    transaction_id: Mapped[str | None] = mapped_column(String(128))
    checkout_url: Mapped[str | None] = mapped_column(Text)
    checkout_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paid_spent_minor: Mapped[int | None] = mapped_column(BigInteger)
    paid_account_version: Mapped[int | None] = mapped_column(Integer)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentRefundRow(PaymentBase):
    __tablename__ = "xingjing_payment_refunds"
    __table_args__ = (
        Index("ix_xj_payment_refunds_scope", "tenant_id", "workspace_id", "created_at", "id"),
        CheckConstraint("amount_minor > 0 AND version >= 1", name="ck_xj_payment_refund_amount_version"),
        CheckConstraint(
            "status IN ('pending_approval','processing','unknown','succeeded','failed','rejected')",
            name="ck_xj_payment_refund_status",
        ),
    )
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    approval_basis_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_id: Mapped[str] = mapped_column(String(128), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(128))
    provider_refund_id: Mapped[str | None] = mapped_column(String(128))
    external_identity: Mapped[str | None] = mapped_column(String(64), unique=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentCommandRow(PaymentBase):
    __tablename__ = "xingjing_payment_commands"
    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    gateway_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentEventRow(PaymentBase):
    __tablename__ = "xingjing_payment_gateway_events"
    provider: Mapped[str] = mapped_column(String(128), primary_key=True)
    merchant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PaymentAuditRow(PaymentBase):
    __tablename__ = "xingjing_payment_audit"
    __table_args__ = (Index("ix_xj_payment_audit_scope", "tenant_id", "workspace_id", "order_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
