"""Persist optional search poster address, contacts and identification details."""
from alembic import op
import sqlalchemy as sa

revision = "20260922_07"
down_revision = "20260916_06"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("missing_reports", sa.Column("poster_details", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("missing_reports", "poster_details")
