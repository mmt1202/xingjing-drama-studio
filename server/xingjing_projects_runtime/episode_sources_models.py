"""Persist episode working-script origins without duplicating the episode ledger."""

from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, DateTime, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class EpisodeSourceBase(DeclarativeBase):
    pass


class EpisodeWorkingScriptRow(EpisodeSourceBase):
    __tablename__ = "xingjing_episode_working_scripts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "workspace_id", "project_id", "episode", "sequence", name="uq_xj_ep_working_seq"),
        UniqueConstraint("workspace_id", "script_id", name="uq_xj_ep_working_script"),
        Index("ix_xj_ep_working_scope", "tenant_id", "workspace_id", "project_id", "episode"),
        CheckConstraint("episode >= 1", name="ck_xj_ep_working_episode"),
        CheckConstraint("sequence >= 1", name="ck_xj_ep_working_sequence"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    script_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_ref: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EpisodeWorkingCommandRow(EpisodeSourceBase):
    __tablename__ = "xingjing_episode_working_commands"

    tenant_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    actor_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
