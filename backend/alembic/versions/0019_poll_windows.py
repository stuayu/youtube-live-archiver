"""Set six hour default monitoring and add custom time windows."""
from alembic import op
import sqlalchemy as sa

revision = "0019_poll_windows"
down_revision = "0018_chat_rendering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("poll_windows", sa.JSON(), nullable=False, server_default="[]"))
    op.execute("UPDATE channels SET poll_interval = '360' WHERE poll_interval = 'AUTO'")


def downgrade() -> None:
    op.drop_column("channels", "poll_windows")
