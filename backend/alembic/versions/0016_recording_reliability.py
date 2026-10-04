"""Recording integrity tracking."""
from alembic import op
import sqlalchemy as sa


revision = "0016_recording_reliability"
down_revision = "0015_channel_monitor_health"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("integrity_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_videos_integrity_checked_at", "videos", ["integrity_checked_at"])


def downgrade() -> None:
    op.drop_index("ix_videos_integrity_checked_at", table_name="videos")
    op.drop_column("videos", "integrity_checked_at")
