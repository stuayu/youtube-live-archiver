"""Persist live chat messages and per-channel capture preference."""
from alembic import op
import sqlalchemy as sa

revision = "0010_live_chat"
down_revision = "0009_recording_recovery"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("channels", sa.Column("chat_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table("chat_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", sa.String(200), nullable=False),
        sa.Column("offset_ms", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("author_channel_id", sa.String(100)), sa.Column("author_name", sa.String(500), nullable=False, server_default=""),
        sa.Column("author_badges", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("message_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("superchat_amount", sa.Float()), sa.Column("superchat_currency", sa.String(10)),
        sa.Column("superchat_color", sa.String(20)), sa.Column("message_type", sa.String(40), nullable=False, server_default="normal"),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deleted_at", sa.DateTime(timezone=True)), sa.Column("deletion_source", sa.String(40)))
    op.create_index("ix_chat_messages_video_id", "chat_messages", ["video_id"])
    op.create_index("ix_chat_messages_message_id", "chat_messages", ["message_id"], unique=True)
    op.create_index("ix_chat_messages_offset_ms", "chat_messages", ["offset_ms"])
    op.create_index("ix_chat_messages_is_deleted", "chat_messages", ["is_deleted"])

def downgrade():
    op.drop_table("chat_messages")
    op.drop_column("channels", "chat_enabled")
