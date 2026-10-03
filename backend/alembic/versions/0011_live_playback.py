"""per channel live playback and HLS paths"""
from alembic import op
import sqlalchemy as sa

revision = "0011_live_playback"
down_revision = "0010_live_chat"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("channels", sa.Column("live_playback_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("channels", sa.Column("live_playback_limit_bytes", sa.BigInteger(), nullable=False, server_default="5368709120"))
    op.add_column("videos", sa.Column("live_hls_path", sa.String(length=2000), nullable=True))

def downgrade():
    op.drop_column("videos", "live_hls_path")
    op.drop_column("channels", "live_playback_limit_bytes")
    op.drop_column("channels", "live_playback_enabled")
