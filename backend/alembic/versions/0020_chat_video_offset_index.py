"""Index chat messages by video and offset."""
from alembic import op

revision = "0020_chat_video_offset_index"
down_revision = "0019_poll_windows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 動画単位の min/max(offset_ms) が offset_ms 単独索引の全件走査になり、
    # チャット API が 1 リクエストあたり数十秒かかっていたため複合索引を追加する。
    # 稼働中の DB へ手動で先に作成していても失敗しないよう if_not_exists にする。
    op.create_index("ix_chat_messages_video_offset", "chat_messages", ["video_id", "offset_ms"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_chat_messages_video_offset", table_name="chat_messages", if_exists=True)
