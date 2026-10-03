"""Add channels.created_at used as the backfill cutoff."""
from alembic import op
import sqlalchemy as sa

revision = "0008_channel_created_at"
down_revision = "0007_quality_profiles"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "channels",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
    )


def downgrade():
    op.drop_column("channels", "created_at")
