"""Channel metadata time series."""
from alembic import op
import sqlalchemy as sa

revision = "0006_channel_stats"
down_revision = "0005_gui_backfill_metadata"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("channels", sa.Column("video_count", sa.BigInteger(), nullable=True))
    op.add_column("channels", sa.Column("view_count", sa.BigInteger(), nullable=True))
    op.create_table(
        "channel_stats",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channel_id", sa.Integer(), sa.ForeignKey("channels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscriber_count", sa.BigInteger(), nullable=True),
        sa.Column("subscriber_count_rounded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("video_count", sa.BigInteger(), nullable=True),
        sa.Column("view_count", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_channel_stats_channel_id", "channel_stats", ["channel_id"])
    op.create_index("ix_channel_stats_captured_at", "channel_stats", ["captured_at"])


def downgrade():
    op.drop_index("ix_channel_stats_captured_at", table_name="channel_stats")
    op.drop_index("ix_channel_stats_channel_id", table_name="channel_stats")
    op.drop_table("channel_stats")
    op.drop_column("channels", "view_count")
    op.drop_column("channels", "video_count")
