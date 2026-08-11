"""create Xingjing M09/S06 compliance authority tables

Revision ID: a9e6f4c2b817
Revises: f51c9d7a832e
Create Date: 2026-07-18
"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_compliance_persistence.repository import CompliancePersistenceBase

revision: str = "a9e6f4c2b817"
down_revision: str | Sequence[str] | None = "f51c9d7a832e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Install only the formal compliance authority metadata."""
    CompliancePersistenceBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Remove formal compliance authority tables in dependency order."""
    CompliancePersistenceBase.metadata.drop_all(op.get_bind(), checkfirst=True)
