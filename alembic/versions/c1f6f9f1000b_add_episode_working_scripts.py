"""Persist episode working-script origins and idempotent handoffs.

Revision ID: c1f6f9f1000b
Revises: b0e5e8effffa
"""

from alembic import op
from server.xingjing_projects_runtime.episode_sources_models import EpisodeSourceBase

revision = "c1f6f9f1000b"
down_revision = "b0e5e8effffa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    EpisodeSourceBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Episode script provenance and command evidence are retained")
