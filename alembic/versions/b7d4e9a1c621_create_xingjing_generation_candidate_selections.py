"""create_xingjing_generation_candidate_selections

Revision ID: b7d4e9a1c621
Revises: a0d3e5f7b912
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op

from server.xingjing_generation_persistence.repository import GeneratedCandidateSelectionRow


revision: str = "b7d4e9a1c621"
down_revision: str | Sequence[str] | None = "a0d3e5f7b912"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    GeneratedCandidateSelectionRow.__table__.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    GeneratedCandidateSelectionRow.__table__.drop(op.get_bind(), checkfirst=True)
