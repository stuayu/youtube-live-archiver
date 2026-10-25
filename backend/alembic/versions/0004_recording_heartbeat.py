"""add recorder heartbeat"""
from alembic import op
import sqlalchemy as sa

revision = "0004_recording_heartbeat"
down_revision = "0003_archive_channels"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("recording_heartbeat_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_videos_recording_heartbeat_at", "videos", ["recording_heartbeat_at"])


def downgrade():
    op.drop_index("ix_videos_recording_heartbeat_at", table_name="videos")
    op.drop_column("videos", "recording_heartbeat_at")
