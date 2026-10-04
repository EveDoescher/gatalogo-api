"""Persist companion affinity, food allowances and plant growth."""

from alembic import op
import sqlalchemy as sa

revision = "20260924_10"
down_revision = "20260923_09"
branch_labels = None
depends_on = None


def upgrade():
    for table, column in (("habitat_wallets", "care_state"),
                          ("habitat_items", "care_state"),
                          ("habitat_transactions", "details")):
        op.add_column(table, sa.Column(column, sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    for table, column in (("habitat_transactions", "details"),
                          ("habitat_items", "care_state"),
                          ("habitat_wallets", "care_state")):
        op.drop_column(table, column)
