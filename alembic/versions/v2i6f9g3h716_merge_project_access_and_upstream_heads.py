"""merge project access and upstream migration heads

Revision ID: v2i6f9g3h716
Revises: d10f1df40f96, u1h5e8f2g605
"""

from collections.abc import Sequence


revision: str = "v2i6f9g3h716"
down_revision: str | Sequence[str] | None = ("d10f1df40f96", "u1h5e8f2g605")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Join both schema histories without changing either branch."""


def downgrade() -> None:
    """Return to the two independent branch heads."""
