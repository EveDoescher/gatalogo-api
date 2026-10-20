"""Allow safe refresh retries without changing session lifetime."""
from alembic import op
import sqlalchemy as sa
revision = "20261018_14"
down_revision = "20261009_13"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("user_sessions", sa.Column("previous_refresh_token_hash", sa.String(64), nullable=True))
    op.add_column("user_sessions", sa.Column("previous_refresh_valid_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_user_sessions_previous_refresh", "user_sessions", ["previous_refresh_token_hash"], unique=True)

def downgrade():
    op.drop_index("ix_user_sessions_previous_refresh", table_name="user_sessions")
    op.drop_column("user_sessions", "previous_refresh_valid_until")
    op.drop_column("user_sessions", "previous_refresh_token_hash")
