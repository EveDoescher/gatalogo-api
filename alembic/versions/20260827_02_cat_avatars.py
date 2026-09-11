"""Add private reference photos and generated avatar assets."""

from alembic import op
import sqlalchemy as sa


revision = "20260827_02"
down_revision = "20260827_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid_type = sa.Uuid()
    op.create_table(
        "cat_photos",
        sa.Column("id", uuid_type, primary_key=True),
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="ready"),
        sa.Column("analysis", sa.JSON(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("cat_id", "kind", name="uq_cat_photo_kind"),
    )
    op.create_index("ix_cat_photos_cat_id", "cat_photos", ["cat_id"])
    op.create_table(
        "cat_avatars",
        sa.Column("cat_id", uuid_type, sa.ForeignKey("cats.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending"),
        sa.Column("texture_keys", sa.JSON(), nullable=False),
        sa.Column("preview_key", sa.String(length=512), nullable=True),
        sa.Column("coat_map", sa.JSON(), nullable=True),
        sa.Column("asset_hash", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade() -> None:
    op.drop_table("cat_avatars")
    op.drop_table("cat_photos")
