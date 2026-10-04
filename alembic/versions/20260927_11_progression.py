"""Confirmed progression and privacy migration; never replay old rewards."""
from datetime import UTC, datetime
from uuid import UUID, uuid4
from alembic import op
import sqlalchemy as sa

revision = "20260927_11"
down_revision = "20260924_10"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table("progress_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("record_id", sa.String(64)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("coins", sa.Integer(), nullable=False),
        sa.Column("photo_hash", sa.String(64)),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "event_key", name="uq_progress_event"))
    for column in ("user_id", "kind", "record_id", "photo_hash", "created_at"):
        op.create_index(f"ix_progress_events_{column}", "progress_events", [column])
    bind = op.get_bind()
    meta = sa.MetaData()
    states = sa.Table("product_states", meta, autoload_with=bind)
    cats = sa.Table("cats", meta, autoload_with=bind)
    photos = sa.Table("cat_photos", meta, autoload_with=bind)
    # Capture all legacy records, including tombstones, without granting anything.
    for cat in bind.execute(sa.select(cats)).mappings():
        bind.execute(table.insert().values(id=uuid4(), user_id=UUID(str(cat["user_id"])),
            event_key=f"legacy:{cat['client_id']}", kind="legacy", record_id=str(cat["client_id"]),
            state="ineligible", rule_version=1, xp=0, coins=0, photo_hash=cat["photo_hash"],
            evidence={"individual_id": str(cat["client_id"]), "legacy": True},
            result={"reason": "Registro anterior à nova progressão"}))
    for row in bind.execute(sa.select(photos.c.id, photos.c.kind, photos.c.content_hash,
            cats.c.user_id, cats.c.client_id).join(cats, photos.c.cat_id == cats.c.id)).mappings():
        bind.execute(table.insert().values(id=uuid4(), user_id=UUID(str(row["user_id"])),
            event_key=f"reference:{row['client_id']}:{row['kind']}" if row["kind"] != "primary" else f"legacy-photo:{row['id']}",
            kind="legacy", record_id=str(row["client_id"]), state="ineligible", rule_version=1,
            xp=0, coins=0, photo_hash=row["content_hash"],
            evidence={"individual_id": str(row["client_id"]), "legacy": True}, result={"reason": "Foto anterior à nova progressão"}))
    for row in bind.execute(sa.select(states)).mappings():
        prefs = dict(row["preferences"] or {})
        prefs.update(gallery_audience="friends", medals_audience="friends", habitat_audience="friends")
        prefs.pop("public_gallery", None)
        bind.execute(states.update().where(states.c.user_id == row["user_id"]).values(preferences=prefs))


def downgrade():
    # Do not restore public visibility or undo earned balances during rollback.
    raise RuntimeError("O histórico de créditos deve ser preservado. Desative as concessões por feature flag para reverter a liberação.")
