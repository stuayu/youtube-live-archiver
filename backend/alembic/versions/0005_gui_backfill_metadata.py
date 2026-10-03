"""GUI metadata, scheduled streams and bounded backfill."""
from alembic import op
import sqlalchemy as sa

revision = "0005_gui_backfill_metadata"
down_revision = "0004_recording_heartbeat"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("channels", sa.Column("backfill_limit", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("channels", sa.Column("avatar_url", sa.String(2000), nullable=True))
    op.add_column("channels", sa.Column("avatar_cache_path", sa.String(2000), nullable=True))
    op.add_column("channels", sa.Column("banner_url", sa.String(2000), nullable=True))
    op.add_column("channels", sa.Column("subscriber_count", sa.BigInteger(), nullable=True))
    op.add_column("channels", sa.Column("description", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column("thumbnail_url", sa.String(2000), nullable=True))
    op.add_column("videos", sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_videos_scheduled_start_at", "videos", ["scheduled_start_at"])

def downgrade():
    op.drop_index("ix_videos_scheduled_start_at", table_name="videos")
    op.drop_column("videos", "scheduled_start_at")
    op.drop_column("videos", "thumbnail_url")
    for name in ("description", "subscriber_count", "banner_url", "avatar_cache_path", "avatar_url", "backfill_limit"):
        op.drop_column("channels", name)
