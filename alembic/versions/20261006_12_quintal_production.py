"""Durable Quintal production, without changing existing balances or progress."""
from alembic import op
import sqlalchemy as sa

revision = "20261006_12"
down_revision = "20260927_11"
branch_labels = None
depends_on = None

def user():
    return sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

def upgrade():
    op.create_table("quintal_facilities", user(), sa.Column("kind", sa.String(24), nullable=False), sa.Column("level", sa.Integer(), nullable=False), sa.Column("target_level", sa.Integer(), nullable=False), sa.Column("ready_at", sa.DateTime(timezone=True)), sa.PrimaryKeyConstraint("user_id", "kind"))
    op.create_table("quintal_resources", user(), sa.Column("resource", sa.String(48), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("user_id", "resource"), sa.CheckConstraint("quantity >= 0", name="ck_quintal_resource_nonnegative"))
    op.create_table("quintal_counters", user(), sa.Column("metric", sa.String(64), nullable=False), sa.Column("quantity", sa.Integer(), nullable=False), sa.Column("projected_quantity", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("user_id", "metric"))
    op.create_table("quintal_plots", user(), sa.Column("slot", sa.Integer(), nullable=False), sa.Column("crop", sa.String(24)), sa.Column("started_at", sa.DateTime(timezone=True)), sa.Column("ready_at", sa.DateTime(timezone=True)), sa.Column("watered", sa.Integer(), nullable=False), sa.PrimaryKeyConstraint("user_id", "slot"))
    for name, key in (("quintal_residents", "kind"), ("quintal_cat_care", "cat_id")):
        op.create_table(name, user(), sa.Column(key, sa.Uuid() if key == "cat_id" else sa.String(24), nullable=False), sa.Column("state", sa.JSON(), nullable=False), sa.PrimaryKeyConstraint("user_id", key))
    op.create_table("quintal_preferences", user(), sa.Column("cat_id", sa.Uuid(), nullable=False), sa.Column("recipe", sa.String(24), nullable=False), sa.Column("state", sa.JSON(), nullable=False), sa.PrimaryKeyConstraint("user_id", "cat_id", "recipe"))
    op.create_table("quintal_tasks", sa.Column("id", sa.Uuid(), primary_key=True), user(), sa.Column("kind", sa.String(24), nullable=False), sa.Column("target", sa.String(48), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False), sa.Column("inputs", sa.JSON(), nullable=False), sa.Column("outputs", sa.JSON(), nullable=False))
    op.create_table("quintal_sessions", sa.Column("id", sa.Uuid(), primary_key=True), user(), sa.Column("kind", sa.String(24), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("state", sa.JSON(), nullable=False))
    for name in ("quintal_tasks", "quintal_sessions"):
        op.create_index(f"ix_{name}_user_id", name, ["user_id"])

def downgrade():
    for name in ("quintal_sessions", "quintal_tasks", "quintal_preferences", "quintal_cat_care", "quintal_residents", "quintal_plots", "quintal_counters", "quintal_resources", "quintal_facilities"):
        op.drop_table(name)
