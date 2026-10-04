"""Chat rescue features: media, location, cat card, read receipts, reactions, and user presence."""
from alembic import op
import sqlalchemy as sa

revision = "20260916_06"
down_revision = "20260908_05"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversation_messages", sa.Column("type", sa.String(24), server_default="text", nullable=False))
    op.add_column("conversation_messages", sa.Column("media_url", sa.String(512), nullable=True))
    op.add_column("conversation_messages", sa.Column("location_lat", sa.Float(), nullable=True))
    op.add_column("conversation_messages", sa.Column("location_lng", sa.Float(), nullable=True))
    op.add_column("conversation_messages", sa.Column("location_title", sa.String(256), nullable=True))
    op.add_column("conversation_messages", sa.Column("cat_data", sa.JSON(), nullable=True))
    op.add_column("conversation_messages", sa.Column("read_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversation_messages", sa.Column("reply_to_id", sa.Uuid(), sa.ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True))
    op.add_column("conversation_messages", sa.Column("reactions", sa.JSON(), nullable=True))
    op.add_column("conversation_messages", sa.Column("is_edited", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("conversation_messages", sa.Column("is_deleted", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("conversation_messages", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column("conversation_messages", "updated_at")
    op.drop_column("conversation_messages", "is_deleted")
    op.drop_column("conversation_messages", "is_edited")
    op.drop_column("conversation_messages", "reactions")
    op.drop_column("conversation_messages", "reply_to_id")
    op.drop_column("conversation_messages", "read_at")
    op.drop_column("conversation_messages", "cat_data")
    op.drop_column("conversation_messages", "location_title")
    op.drop_column("conversation_messages", "location_lng")
    op.drop_column("conversation_messages", "location_lat")
    op.drop_column("conversation_messages", "media_url")
    op.drop_column("conversation_messages", "type")
    op.drop_column("users", "last_seen_at")
