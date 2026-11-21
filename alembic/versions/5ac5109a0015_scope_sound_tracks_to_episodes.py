"""Associate creator audio and subtitle tracks with an optional episode."""

import sqlalchemy as sa

from alembic import op

revision = "5ac5109a0015"
down_revision = "5ac5109a0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    schema = sa.inspect(op.get_bind())
    for table in ("xingjing_audio_tracks", "xingjing_subtitle_tracks"):
        if "episode_number" not in {column["name"] for column in schema.get_columns(table)}:
            op.add_column(table, sa.Column("episode_number", sa.Integer(), nullable=True))


def downgrade() -> None:
    for table in ("xingjing_audio_tracks", "xingjing_subtitle_tracks"):
        op.drop_column(table, "episode_number")
