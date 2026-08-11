from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class BillingPersistenceBase(DeclarativeBase):
    """M11 team-finance metadata installed by the formal Alembic migration."""


JSON_DOCUMENT = JSON().with_variant(JSONB, "postgresql")


class TeamPlanRow(BillingPersistenceBase):
    __tablename__ = "xingjing_team_billing_plans"
    __table_args__ = (
        CheckConstraint("seat_limit >= 0", name="ck_xj_team_plan_seat_limit"),
        CheckConstraint("version >= 1", name="ck_xj_team_plan_version"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    seat_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    features: Mapped[list[str]] = mapped_column(JSON_DOCUMENT, nullable=False)
    quotas: Mapped[dict[str, int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    quota_remaining: Mapped[dict[str, int]] = mapped_column(JSON_DOCUMENT, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class InvoiceRequestRow(BillingPersistenceBase):
    __tablename__ = "xingjing_team_invoice_requests"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_xj_team_invoice_amount"),
        CheckConstraint("status IN ('pending','approved','issued','rejected')", name="ck_xj_team_invoice_status"),
        Index("ix_xj_team_invoice_created", "tenant_id", "workspace_id", "created_at", "invoice_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    invoice_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    invoice_title: Mapped[str] = mapped_column(String(512), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingOrderRow(BillingPersistenceBase):
    __tablename__ = "xingjing_team_billing_orders"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_xj_team_order_amount"),
        CheckConstraint(
            "status IN ('pending','paid','failed','refunded','closed')",
            name="ck_xj_team_order_status",
        ),
        CheckConstraint("version >= 1", name="ck_xj_team_order_version"),
        CheckConstraint(
            "refunded_minor >= 0 AND refunded_minor <= amount_minor",
            name="ck_xj_team_order_refunded",
        ),
        Index("ix_xj_team_order_created", "tenant_id", "workspace_id", "created_at", "order_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    order_type: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refunded_minor: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingActionReceiptRow(BillingPersistenceBase):
    __tablename__ = "xingjing_team_billing_action_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "workspace_id", "action", "idempotency_key", name="uq_xj_team_billing_action_key"
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    action: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    command_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BillingAuditRow(BillingPersistenceBase):
    __tablename__ = "xingjing_team_billing_audit_events"
    __table_args__ = (
        Index("ix_xj_team_billing_audit_request", "tenant_id", "workspace_id", "request_id"),
        Index("ix_xj_team_billing_audit_time", "tenant_id", "workspace_id", "occurred_at", "event_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    before_payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    after_payload: Mapped[dict[str, object]] = mapped_column(JSON_DOCUMENT, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
