"""Keep immutable chapter sources and confirmed episode plans."""

from alembic import op
from server.xingjing_source_planning.models import SourcePlanningBase

revision = "27f2ff670011"
down_revision = "16e1ee560010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    SourcePlanningBase.metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    raise RuntimeError("Source snapshots and confirmed production provenance are retained")
