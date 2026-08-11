"""merge upstream and xingjing migration heads

Revision ID: cd5026363f1a
Revises: 9c41ad2f7be5, f2t6x0y4z928
Create Date: 2026-08-11 11:49:22.305608

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "cd5026363f1a"
down_revision: str | Sequence[str] | None = ("9c41ad2f7be5", "f2t6x0y4z928")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
