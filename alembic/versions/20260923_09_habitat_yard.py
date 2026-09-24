"""Add yard expansion and saved cat starting positions."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_09"
down_revision = "20260923_08"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("habitat_wallets", sa.Column("cat_anchors", sa.JSON(), nullable=False, server_default="{}"))
    op.add_column("habitat_wallets", sa.Column("expansion_level", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("habitat_wallets", sa.Column("layout_rules_version", sa.Integer(), nullable=False, server_default="2"))
    op.execute("UPDATE habitat_wallets SET layout_rules_version = 1")


def downgrade():
    op.drop_column("habitat_wallets", "layout_rules_version")
    op.drop_column("habitat_wallets", "expansion_level")
    op.drop_column("habitat_wallets", "cat_anchors")
