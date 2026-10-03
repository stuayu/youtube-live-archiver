import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import Channel, Video, ChatMessage
from app.services.chat import ingest_jsonl, ingest_records, playback_offset_ms
from app.api import dplayer_danmaku

@pytest.fixture
def db():
    engine=create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ch=Channel(youtube_channel_id="UCtest",url="https://youtube.com/@test",name="test")
        session.add(ch); session.flush()
        video=Video(youtube_video_id="abcdefghijk",channel_id=ch.id,title="test")
        session.add(video); session.flush()
        yield session,video

def entry(mid="m1", offset=2300):
    return {"liveChatTextMessageRenderer":{"id":mid,"videoOffsetTimeMsec":offset,"authorName":{"simpleText":"A"},"message":{"runs":[{"text":"hello"}]}}}

def test_idempotent_ingest(db):
    session,video=db
    ingest_records(session,video.id,[entry()]); session.commit()
    ingest_records(session,video.id,[entry()]); session.commit()
    assert session.query(ChatMessage).count()==1

def test_duplicate_ids_in_one_batch_are_ignored(db):
    session,video=db
    duplicate=entry()
    duplicate["liveChatTextMessageRenderer"]["message"]["runs"]=[{"text":"duplicate"}]
    ingest_records(session,video.id,[entry(),duplicate]); session.commit()
    rows=session.query(ChatMessage).filter_by(video_id=video.id).all()
    assert len(rows)==1 and rows[0].message_text=="hello"

def test_snapshot_marks_missing_and_event_deletes(db):
    session,video=db
    ingest_records(session,video.id,[entry(),entry("m2")]); session.commit()
    ingest_records(session,video.id,[entry(),{"actions":[{"markChatItemAsDeletedAction":{"deletedStateMessage":{"targetItemId":"m1"}}}]}]); session.commit()
    deleted=session.query(ChatMessage).filter_by(message_id="m2").one()
    assert deleted.is_deleted and deleted.deletion_source=="判定不能"
    assert session.query(ChatMessage).filter_by(message_id="m1").one().is_deleted

def test_failed_fetch_does_not_mark_missing(db, tmp_path):
    session,video=db
    ingest_records(session,video.id,[entry()]); session.commit()
    # Replay download failure is raised before a complete snapshot can mutate rows.
    bad=tmp_path/"broken.live_chat.json"
    bad.write_text('{invalid json}\n',encoding="utf-8")
    with pytest.raises(Exception): ingest_jsonl(session,video.id,bad,complete_snapshot=True)
    session.commit()
    assert session.query(ChatMessage).one().is_deleted is False

def test_offset_ms():
    assert playback_offset_ms({"videoOffsetTimeMsec":"1500"})==1500
    assert playback_offset_ms({"videoOffsetTimeSeconds":2.5})==2500

def test_dplayer_v3_rows_are_arrays_in_seconds_and_hide_deleted_by_default(db):
    session,video=db
    video.raw_metadata={"recording_start_offset_ms":1500}
    session.add_all([
        ChatMessage(video_id=video.id,message_id="before-recording",offset_ms=1000,author_name="old",message_text="before"),
        ChatMessage(video_id=video.id,message_id="normal",offset_ms=4000,author_name="A",message_text="hello"),
        ChatMessage(video_id=video.id,message_id="deleted",offset_ms=5000,author_name="B",message_text="gone",is_deleted=True),
    ])
    session.flush()
    result=dplayer_danmaku(video.id,"all-0",session)
    assert result["code"]==0
    assert result["data"]==[[2.5,0,"#ffffff","A","hello"]]
    assert isinstance(result["data"][0][0],float)
    assert dplayer_danmaku(video.id,"all-1",session)["data"][-1][-1]=="[削除済] gone"

def test_dplayer_v3_returns_all_rows_over_1000(db):
    session,video=db
    session.add_all(ChatMessage(video_id=video.id,message_id=f"many-{i}",offset_ms=i*1000,
        author_name="viewer",message_text=f"message {i}") for i in range(1205))
    session.flush()
    result=dplayer_danmaku(video.id,"all-0",session)
    assert len(result["data"])==1205
    assert result["data"][0][0]==0.0
    assert result["data"][-1][0]==1204.0
