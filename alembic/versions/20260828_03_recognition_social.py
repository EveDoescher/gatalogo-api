"""Add queued visual recognition, missing-cat reports and social graph."""

from alembic import op
import sqlalchemy as sa


revision = "20260828_03"
down_revision = "20260827_02"
branch_labels = None
depends_on = None


def _timestamps(*, updated: bool = False) -> list[sa.Column]:
    columns = [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    ]
    if updated:
        columns.append(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")))
    return columns


def upgrade() -> None:
    uuid_type = sa.Uuid()
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.add_column("users", sa.Column("username", sa.String(length=32), nullable=True))
    op.create_unique_constraint("uq_users_username", "users", ["username"])
    op.create_index("ix_users_username", "users", ["username"])

    op.create_table(
        "vision_jobs",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", uuid_type, sa.ForeignKey("cat_photos.id", ondelete="SET NULL"), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False, server_default="primary"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_timestamps(),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_vision_jobs_status", "vision_jobs", ["status"])
    op.create_index("ix_vision_jobs_user_id", "vision_jobs", ["user_id"])
    op.create_index("ix_vision_jobs_cat_id", "vision_jobs", ["cat_id"])

    op.create_table(
        "cat_visual_embeddings",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", uuid_type, sa.ForeignKey("cat_photos.id", ondelete="CASCADE"), nullable=True),
        sa.Column("region", sa.String(length=24), nullable=False, server_default="body"),
        sa.Column("view", sa.String(length=24), nullable=False, server_default="unknown"),
        sa.Column("embedding", sa.TEXT(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("is_solid_coat", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    # Alterar de TEXT para vector evita dependência da extensão no autogenerate.
    op.execute("ALTER TABLE cat_visual_embeddings ALTER COLUMN embedding TYPE vector(384) USING embedding::vector")
    op.execute("CREATE INDEX ix_cat_visual_embeddings_hnsw ON cat_visual_embeddings USING hnsw (embedding vector_cosine_ops)")
    op.create_index("ix_cat_visual_embeddings_cat_id", "cat_visual_embeddings", ["cat_id"])
    op.create_index("ix_cat_visual_embeddings_user_id", "cat_visual_embeddings", ["user_id"])
    op.create_index("ix_cat_visual_embeddings_photo_id", "cat_visual_embeddings", ["photo_id"])

    op.create_table(
        "missing_reports",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("radius_meters", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="active"),
        *_timestamps(updated=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_missing_reports_user_id", "missing_reports", ["user_id"])
    op.create_index("ix_missing_reports_cat_id", "missing_reports", ["cat_id"])
    op.create_index("ix_missing_reports_status", "missing_reports", ["status"])

    op.create_table(
        "sightings",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("observer_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_sightings_observer_id", "sightings", ["observer_id"])
    op.create_index("ix_sightings_cat_id", "sightings", ["cat_id"])

    op.create_table(
        "match_suggestions",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("missing_report_id", uuid_type, sa.ForeignKey("missing_reports.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sighting_id", uuid_type, sa.ForeignKey("sightings.id", ondelete="CASCADE"), nullable=False),
        sa.Column("similarity", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("restricted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("location_revealed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("missing_report_id", "sighting_id", name="uq_match_report_sighting"),
    )
    op.create_index("ix_match_suggestions_missing_report_id", "match_suggestions", ["missing_report_id"])
    op.create_index("ix_match_suggestions_sighting_id", "match_suggestions", ["sighting_id"])
    op.create_index("ix_match_suggestions_status", "match_suggestions", ["status"])

    op.create_table("friend_invites", sa.Column("id", uuid_type, primary_key=True), sa.Column("sender_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("recipient_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"), *_timestamps(), sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True), sa.UniqueConstraint("sender_id", "recipient_id", name="uq_friend_invite_pair"))
    op.create_index("ix_friend_invites_sender_id", "friend_invites", ["sender_id"])
    op.create_index("ix_friend_invites_recipient_id", "friend_invites", ["recipient_id"])
    op.create_index("ix_friend_invites_status", "friend_invites", ["status"])
    op.create_table("friendships", sa.Column("id", uuid_type, primary_key=True), sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("friend_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), *_timestamps(), sa.UniqueConstraint("user_id", "friend_id", name="uq_friendship_pair"))
    op.create_index("ix_friendships_user_id", "friendships", ["user_id"])
    op.create_index("ix_friendships_friend_id", "friendships", ["friend_id"])
    op.create_table("feed_items", sa.Column("id", uuid_type, primary_key=True), sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("type", sa.String(length=32), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), *_timestamps())
    op.create_index("ix_feed_items_user_id", "feed_items", ["user_id"])
    op.create_table("conversations", sa.Column("id", uuid_type, primary_key=True), sa.Column("match_suggestion_id", uuid_type, sa.ForeignKey("match_suggestions.id", ondelete="SET NULL"), nullable=True, unique=True), *_timestamps())
    op.create_table("conversation_members", sa.Column("id", uuid_type, primary_key=True), sa.Column("conversation_id", uuid_type, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_member"))
    op.create_index("ix_conversation_members_conversation_id", "conversation_members", ["conversation_id"])
    op.create_index("ix_conversation_members_user_id", "conversation_members", ["user_id"])
    op.create_table("conversation_messages", sa.Column("id", uuid_type, primary_key=True), sa.Column("conversation_id", uuid_type, sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("sender_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("body", sa.Text(), nullable=False), *_timestamps())
    op.create_index("ix_conversation_messages_conversation_id", "conversation_messages", ["conversation_id"])
    op.create_table("device_tokens", sa.Column("id", uuid_type, primary_key=True), sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("token", sa.String(length=512), nullable=False), sa.Column("platform", sa.String(length=24), nullable=False), sa.Column("preferences", sa.JSON(), nullable=False), *_timestamps(updated=True), sa.UniqueConstraint("token"))
    op.create_index("ix_device_tokens_user_id", "device_tokens", ["user_id"])
    op.create_table("app_notifications", sa.Column("id", uuid_type, primary_key=True), sa.Column("user_id", uuid_type, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("type", sa.String(length=32), nullable=False), sa.Column("payload", sa.JSON(), nullable=False), sa.Column("read_at", sa.DateTime(timezone=True), nullable=True), *_timestamps())
    op.create_index("ix_app_notifications_user_id", "app_notifications", ["user_id"])


def downgrade() -> None:
    for table in ("app_notifications", "device_tokens", "conversation_messages", "conversation_members", "conversations", "feed_items", "friendships", "friend_invites", "match_suggestions", "sightings", "missing_reports", "cat_visual_embeddings", "vision_jobs"):
        op.drop_table(table)
    op.drop_index("ix_users_username", table_name="users")
    op.drop_constraint("uq_users_username", "users", type_="unique")
    op.drop_column("users", "username")
