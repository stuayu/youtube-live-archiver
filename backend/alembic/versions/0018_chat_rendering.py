"""Store parsed chat presentation data."""
from alembic import op
import sqlalchemy as sa


revision = "0018_chat_rendering"
down_revision = "0017_live_from_start"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_messages", sa.Column("message_runs", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("chat_messages", sa.Column("author_role", sa.String(length=30), nullable=False, server_default="viewer"))
    op.add_column("chat_messages", sa.Column("amount_display", sa.String(length=100), nullable=True))
    op.add_column("chat_messages", sa.Column("header_text", sa.Text(), nullable=True))
    op.add_column("chat_messages", sa.Column("sticker_url", sa.String(length=2000), nullable=True))


def downgrade() -> None:
    op.drop_column("chat_messages", "sticker_url")
    op.drop_column("chat_messages", "header_text")
    op.drop_column("chat_messages", "amount_display")
    op.drop_column("chat_messages", "author_role")
    op.drop_column("chat_messages", "message_runs")
