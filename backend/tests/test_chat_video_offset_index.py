import pytest
from sqlalchemy import create_engine, inspect

from app.config import settings
from app.models import ChatMessage


def test_chat_message_model_declares_video_offset_index():
    # 動画ごとの min/max(offset_ms) と offset_ms 順の取得が全件索引走査にならないよう、
    # video_id と offset_ms の複合索引を持つ。
    indexes = {index.name: [column.name for column in index.columns] for index in ChatMessage.__table__.indexes}
    assert indexes.get("ix_chat_messages_video_offset") == ["video_id", "offset_ms"]


@pytest.mark.integration
def test_migrated_database_has_video_offset_index():
    engine = create_engine(settings.database_url)
    try:
        indexes = {index["name"]: index["column_names"] for index in inspect(engine).get_indexes("chat_messages")}
    finally:
        engine.dispose()
    assert indexes.get("ix_chat_messages_video_offset") == ["video_id", "offset_ms"]
