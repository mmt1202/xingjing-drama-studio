"""add M06 generation billing ledger

Revision ID: d4b9f6e2a018
Revises: c3a8e5d1f907
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_generation_persistence.repository import (
    GenerationBillingAccountRow,
    GenerationBillingHoldRow,
    GenerationBillingJournalRow,
    GenerationProgressRow,
    ProviderSubmissionOutboxRow,
)

revision: str = "d4b9f6e2a018"
down_revision: str | Sequence[str] | None = "c3a8e5d1f907"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("M06 billing persistence requires PostgreSQL")
    GenerationBillingAccountRow.__table__.create(bind, checkfirst=True)
    GenerationBillingHoldRow.__table__.create(bind, checkfirst=True)
    GenerationBillingJournalRow.__table__.create(bind, checkfirst=True)
    ProviderSubmissionOutboxRow.__table__.create(bind, checkfirst=True)
    GenerationProgressRow.__table__.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    GenerationProgressRow.__table__.drop(bind, checkfirst=True)
    ProviderSubmissionOutboxRow.__table__.drop(bind, checkfirst=True)
    GenerationBillingJournalRow.__table__.drop(bind, checkfirst=True)
    GenerationBillingHoldRow.__table__.drop(bind, checkfirst=True)
    GenerationBillingAccountRow.__table__.drop(bind, checkfirst=True)
