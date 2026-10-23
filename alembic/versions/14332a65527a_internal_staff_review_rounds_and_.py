"""internal staff review rounds and comments

Revision ID: 14332a65527a
Revises: a3c955c19e21
Create Date: 2026-10-23 14:17:47.123667

"""

from collections.abc import Sequence

from alembic import op
from server.xingjing_review_persistence.internal_models import InternalReviewBase

# revision identifiers, used by Alembic.
revision: str = "14332a65527a"
down_revision: str | Sequence[str] | None = "a3c955c19e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    InternalReviewBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Downgrade schema."""
    InternalReviewBase.metadata.drop_all(op.get_bind(), checkfirst=True)
