"""Preserve actual construction intervals; leave legacy starts unknown."""
from alembic import op
import sqlalchemy as sa

revision = "20261009_13"
down_revision = "20261006_12"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quintal_facilities", sa.Column(
        "started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("quintal_facilities", "started_at")
