"""Version visual evidence and bind jobs to immutable photo contents."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_04"
down_revision = "20260828_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("vision_jobs", sa.Column("photo_hash", sa.String(64), nullable=True))
    op.add_column("cat_visual_embeddings", sa.Column("model_version", sa.String(128), nullable=False, server_default="legacy"))
    op.add_column("cat_visual_embeddings", sa.Column("photo_hash", sa.String(64), nullable=True))
    op.add_column("cat_visual_embeddings", sa.Column("features", sa.JSON(), nullable=True))
    op.add_column("match_suggestions", sa.Column("evidence", sa.JSON(), nullable=True))
    # Legacy evidence has no quality/content provenance; keep human decisions.
    op.execute("UPDATE match_suggestions SET restricted = true WHERE status = 'pending'")
    op.execute("UPDATE vision_jobs SET status = 'pending', started_at = NULL WHERE status = 'running'")
    op.execute("UPDATE vision_jobs j SET photo_hash = p.content_hash FROM cat_photos p WHERE j.photo_id = p.id AND j.status = 'pending'")


def downgrade():
    op.drop_column("match_suggestions", "evidence")
    for column in ("features", "photo_hash", "model_version"):
        op.drop_column("cat_visual_embeddings", column)
    op.drop_column("vision_jobs", "photo_hash")
