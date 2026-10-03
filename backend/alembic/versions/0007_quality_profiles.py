"""Split live and archive quality profiles."""
from alembic import op
import sqlalchemy as sa

revision = "0007_quality_profiles"
down_revision = "0006_channel_stats"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("channels", sa.Column("live_quality", sa.String(500), nullable=False, server_default=""))
    op.add_column("channels", sa.Column("archive_quality", sa.String(500), nullable=False, server_default="bv*[vcodec^=av01]+ba/bv*[vcodec^=vp9]+ba/bv*+ba/b"))
    op.add_column("videos", sa.Column("format_selector", sa.Text(), nullable=True))
    op.add_column("videos", sa.Column("format_profile", sa.String(20), nullable=True))
    op.add_column("videos", sa.Column("format_fallback", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("videos", sa.Column("actual_format", sa.String(500), nullable=True))
    # Legacy channel quality controlled recording; preserve it for live captures.
    op.execute("UPDATE channels SET live_quality = CASE WHEN quality = 'best' THEN '' ELSE quality END")

def downgrade():
    op.drop_column("videos", "actual_format")
    op.drop_column("videos", "format_fallback")
    op.drop_column("videos", "format_profile")
    op.drop_column("videos", "format_selector")
    op.drop_column("channels", "archive_quality")
    op.drop_column("channels", "live_quality")
