"""recording recovery state"""
from alembic import op
import sqlalchemy as sa

revision = "0009_recording_recovery"
down_revision = "0008_channel_created_at"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("videos", sa.Column("is_partial", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("videos", sa.Column("recovery_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("videos", sa.Column("next_recovery_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_videos_next_recovery_at", "videos", ["next_recovery_at"])


def downgrade():
    op.drop_index("ix_videos_next_recovery_at", table_name="videos")
    op.drop_column("videos", "next_recovery_at")
    op.drop_column("videos", "recovery_attempts")
    op.drop_column("videos", "is_partial")
