"""M14 商单聚合的独立 SQLAlchemy 表定义。

该 metadata 由主线 Alembic 迁移显式接入；生产运行时不得隐式建表。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from server.xingjing_commercial.models import JsonObject


class CommercialPersistenceBase(DeclarativeBase):
    """商单持久化独立 metadata。"""


class CommercialOrderRow(CommercialPersistenceBase):
    __tablename__ = "xingjing_commercial_orders"
    __table_args__ = (
        Index(
            "ix_xj_commercial_orders_workspace_updated",
            "owner_workspace_id",
            "updated_at",
            "id",
        ),
    )

    owner_workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    aggregate: Mapped[JsonObject] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommercialCommandRow(CommercialPersistenceBase):
    __tablename__ = "xingjing_commercial_commands"

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(255), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CommercialAuditRow(CommercialPersistenceBase):
    __tablename__ = "xingjing_commercial_audit"
    __table_args__ = (
        Index(
            "ix_xj_commercial_audit_workspace_object",
            "workspace_id",
            "object_id",
            "occurred_at",
            "event_id",
        ),
        Index(
            "ix_xj_commercial_audit_request",
            "workspace_id",
            "request_id",
        ),
        Index(
            "ix_xj_commercial_audit_actor",
            "workspace_id",
            "actor_id",
            "occurred_at",
        ),
    )

    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    before: Mapped[JsonObject | None] = mapped_column(JSON)
    after: Mapped[JsonObject | None] = mapped_column(JSON)
    result: Mapped[str] = mapped_column(String(32), nullable=False)
