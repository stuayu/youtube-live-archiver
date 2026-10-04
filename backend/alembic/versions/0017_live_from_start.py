"""Add channel live-from-start preference."""
from alembic import op
import sqlalchemy as sa


revision = "0017_live_from_start"
down_revision = "0016_recording_reliability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("live_from_start", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("channels", "live_from_start", server_default=None)


def downgrade() -> None:
    op.drop_column("channels", "live_from_start")
