from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class RecoveryBase(DeclarativeBase):
    pass


class RecoveryReceipt(RecoveryBase):
    __tablename__ = "xingjing_script_recovery_receipts"
    __table_args__ = (Index("ix_xj_script_recovery_source", "workspace_id", "project_id", "script_id"),)

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    script_id: Mapped[str] = mapped_column(String(128), nullable=False)
    request_id: Mapped[str] = mapped_column(String(255), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
