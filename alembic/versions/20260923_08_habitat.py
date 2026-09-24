"""Add the account-scoped Habitat wallet, ledger and owned decorations."""

from alembic import op
import sqlalchemy as sa


revision = "20260923_08"
down_revision = "20260922_07"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "habitat_wallets",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("layout_revision", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("selected_cat_ids", sa.JSON(), nullable=False),
        sa.CheckConstraint("balance >= 0", name="ck_habitat_balance_nonnegative"),
    )
    op.create_table(
        "habitat_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(160), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("photo_hash", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "event_key", name="uq_habitat_transaction_event"),
    )
    op.create_index("ix_habitat_transactions_user_id", "habitat_transactions", ["user_id"])
    op.create_index("ix_habitat_transactions_category", "habitat_transactions", ["category"])
    op.create_index("ix_habitat_transactions_created_at", "habitat_transactions", ["created_at"])
    op.create_table(
        "habitat_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sku", sa.String(48), nullable=False),
        sa.Column("purchase_key", sa.String(64)),
        sa.Column("x", sa.Integer()),
        sa.Column("y", sa.Integer()),
        sa.Column("rotation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "purchase_key", name="uq_habitat_purchase_key"),
    )
    op.create_index("ix_habitat_items_user_id", "habitat_items", ["user_id"])


def downgrade():
    op.drop_index("ix_habitat_items_user_id", table_name="habitat_items")
    op.drop_table("habitat_items")
    op.drop_index("ix_habitat_transactions_created_at", table_name="habitat_transactions")
    op.drop_index("ix_habitat_transactions_category", table_name="habitat_transactions")
    op.drop_index("ix_habitat_transactions_user_id", table_name="habitat_transactions")
    op.drop_table("habitat_transactions")
    op.drop_table("habitat_wallets")
