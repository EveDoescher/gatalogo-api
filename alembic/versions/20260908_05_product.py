"""Private product preferences and durable achievements."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_05"
down_revision = "20260908_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("product_states",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("achievements", sa.JSON(), nullable=False))


def downgrade():
    op.drop_table("product_states")
