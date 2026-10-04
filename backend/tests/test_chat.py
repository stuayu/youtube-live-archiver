import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.db import Base
from app.models import Channel, Video, ChatMessage
from app.api import dplayer_danmaku
from app.api.videos import chat_messages
from app.services.chat import (argb_to_hex, ingest_jsonl, ingest_records, parse_amount,
                              parse_record, parse_renderer, parse_runs, playback_offset_ms,
                              rebase_live_chat_offsets, reparse_video_chat)
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

def test_jsonl_import_keeps_valid_records_around_malformed_lines(db, tmp_path):
    session, video = db
    source = tmp_path / "video.live_chat.json"
    source.write_text(json.dumps(entry("valid")) + "\n{broken}\n" + json.dumps(entry("valid-2")) + "\n",
                      encoding="utf-8")
    assert ingest_jsonl(session, video.id, source, complete_snapshot=False) == 2
    assert session.query(ChatMessage).filter_by(video_id=video.id).count() == 2

def test_chat_finalization_only_removes_fragments_after_part_rename(tmp_path):
    from app.services.recorder import finalize_chat_file
    part = tmp_path / "video.live_chat.json.part"
    fragment = tmp_path / "video.live_chat.json.part-Frag5"
    unrelated = tmp_path / "video-part-2.live_chat.json.part-Frag1"
    part.write_text('{"valid":true}\n', encoding="utf-8")
    fragment.write_text("fragment", encoding="utf-8")
    unrelated.write_text("other", encoding="utf-8")
    finalized = finalize_chat_file(tmp_path, "video")
    assert finalized == tmp_path / "video.live_chat.json"
    assert finalized.read_text(encoding="utf-8") == '{"valid":true}\n'
    assert not fragment.exists()
    assert unrelated.exists()

def test_offset_ms():
    assert playback_offset_ms({"videoOffsetTimeMsec":"1500"})==1500
    assert playback_offset_ms({"videoOffsetTimeSeconds":2.5})==2500


def test_live_offsets_rebase_to_release_and_replay_upsert_deduplicates(db):
    session, video = db
    release = 1_000.0
    video.raw_metadata = {"release_timestamp": release, "recording_start_offset_ms": 32_149_000}
    renderer = {"id": "same-message", "timestampUsec": "1032015000",
                "authorName": {"simpleText": "A"}, "message": {"runs": [{"text": "hello"}]}}
    live = {"isLive": True, "videoOffsetTimeMsec": "-136057",
            "replayChatItemAction": {"actions": [{"addChatItemAction": {
                "item": {"liveChatTextMessageRenderer": renderer}}}]}}
    ingest_records(session, video.id, [live])
    message = session.query(ChatMessage).one()
    assert message.offset_ms == 32_015
    from datetime import timezone
    assert message.published_at.replace(tzinfo=timezone.utc).timestamp() == pytest.approx(1_032.015)

    replay = {"videoOffsetTimeMsec": "32015", "liveChatTextMessageRenderer": renderer}
    ingest_records(session, video.id, [replay], complete_snapshot=False)
    assert session.query(ChatMessage).count() == 1
    assert message.offset_ms == 32_015
    assert reparse_video_chat(session, video.id) == 1
    assert message.offset_ms == 32_015


def test_live_chat_offsets_wait_for_release_timestamp(db):
    session, video = db
    renderer = {"id": "pending-release", "timestampUsec": "1032015000",
                "authorName": {"simpleText": "A"}, "message": {"runs": [{"text": "hello"}]}}
    record = {"isLive": True, "videoOffsetTimeMsec": "-136057",
              "liveChatTextMessageRenderer": renderer}
    ingest_records(session, video.id, [record])
    message = session.query(ChatMessage).one()
    assert message.offset_ms == 0
    video.raw_metadata = {"release_timestamp": 1_000.0}
    assert rebase_live_chat_offsets(session, video.id) == 1
    assert message.offset_ms == 32_015


def test_parse_mixed_standard_and_custom_emoji_runs():
    text, runs = parse_runs([{"text": "hi "}, {"emoji": {"emojiId": "💚"}},
        {"emoji": {"emojiId": "UC/emote", "isCustomEmoji": True,
                   "shortcuts": [":green:"], "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/a"}]}}}])
    assert text == "hi 💚:green:"
    assert runs == [{"type": "text", "text": "hi "}, {"type": "emoji", "text": "💚"},
                    {"type": "emote", "id": "UC/emote", "shortcut": ":green:",
                     "url": "https://yt3.ggpht.com/a"}]


@pytest.mark.parametrize(("display", "amount", "currency"), [
    ("¥1,000", 1000, "JPY"), ("NT$150.00", 150, "TWD"), ("CA$5.25", 5.25, "CAD"),
    ("€2.50", 2.5, "EUR"), ("unknown", None, None),
])
def test_parse_amount_table(display, amount, currency):
    assert parse_amount(display) == (amount, currency)


def test_argb_is_saved_as_css_color():
    assert argb_to_hex(4294947584) == "#ffb300"


def test_renderer_types_and_membership_header():
    membership = parse_renderer("liveChatMembershipItemRenderer", {
        "id": "member", "authorName": {"simpleText": "viewer"},
        "headerPrimaryText": {"simpleText": "Member for 45 months"},
        "headerSubtext": {"simpleText": "Gold"}, "message": {"runs": []}})
    assert membership and membership["message_type"] == "membership"
    assert membership["header_text"] == "Member for 45 months · Gold"
    assert membership["message_text"] == ""
    sticker = parse_renderer("liveChatPaidStickerRenderer", {
        "id": "sticker", "authorName": {"simpleText": "A"},
        "purchaseAmountText": {"simpleText": "¥500"}, "headerBackgroundColor": 4294901760,
        "sticker": {"thumbnails": [{"url": "https://yt3.ggpht.com/sticker"}]}})
    assert sticker and sticker["message_type"] == "supersticker"
    assert sticker["superchat_color"] == "#ff0000"
    assert sticker["amount_display"] == "¥500" and sticker["sticker_url"].endswith("sticker")
    assert parse_renderer("liveChatViewerEngagementMessageRenderer", {}) is None


def test_ingestion_persists_renderer_runs_and_reparse_preserves_deletion(db):
    from app.services.chat import reparse_video_chat
    session, video = db
    record = {"liveChatTextMessageRenderer": {"id": "custom", "authorName": {"simpleText": "A"},
        "message": {"runs": [{"text": "hello "}, {"emoji": {"emojiId": "UC/e", "isCustomEmoji": True,
        "shortcuts": [":yay:"], "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/e"}]}}}]}}}
    ingest_records(session, video.id, [record], complete_snapshot=False)
    row = session.query(ChatMessage).one()
    row.is_deleted = True
    row.message_text = "stale"
    session.flush()
    assert reparse_video_chat(session, video.id) == 1
    assert row.message_text == "hello :yay:"
    assert row.message_runs[-1]["type"] == "emote"
    assert row.is_deleted is True


def test_reparse_legacy_raw_envelope_matches_new_ingestion(db):
    from app.services.chat import reparse_video_chat

    session, video = db
    renderer = {
        "id": "legacy-envelope",
        "authorName": {"simpleText": "Member viewer"},
        "authorBadges": [{"liveChatAuthorBadgeRenderer": {
            "tooltip": "Member (1 year)",
            "customThumbnail": {"thumbnails": [{"url": "https://yt3.ggpht.com/member.png"}]},
        }}],
        "message": {"runs": [{"text": "助かる "}, {"emoji": {
            "emojiId": "UClegacy/emote", "isCustomEmoji": True,
            "shortcuts": [":_tasukaru:", ":tasukaru:"],
            "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/emote.png"}]},
        }}]},
    }
    record = {"liveChatTextMessageRenderer": renderer, "videoOffsetTimeMsec": 1234}
    old = ChatMessage(video_id=video.id, message_id="legacy-envelope", offset_ms=1234,
                      author_name="Member viewer", author_badges=["Member (1 year)"],
                      message_text="助かる :_tasukaru:", message_runs=[], message_type="normal",
                      raw={"record": record, "renderer": renderer})
    session.add(old)
    session.flush()
    ingest_records(session, video.id, [{**record, "liveChatTextMessageRenderer": {
        **renderer, "id": "new-envelope"}}], complete_snapshot=False)
    fresh = session.query(ChatMessage).filter_by(message_id="new-envelope").one()
    fresh_result = (fresh.message_runs, fresh.author_badges, fresh.message_type, fresh.header_text,
                    fresh.amount_display, fresh.superchat_color)

    assert reparse_video_chat(session, video.id) == 2
    assert old.message_runs == fresh.message_runs
    assert old.author_badges == fresh.author_badges
    assert (old.message_runs, old.author_badges, old.message_type, old.header_text,
            old.amount_display, old.superchat_color) == fresh_result
    assert old.message_runs[-1] == {
        "type": "emote", "id": "UClegacy/emote", "shortcut": ":_tasukaru:",
        "url": "https://yt3.ggpht.com/emote.png",
    }
    assert old.author_badges == [{"type": "member", "label": "Member (1 year)",
                                  "url": "https://yt3.ggpht.com/member.png"}]


def test_reparse_live_legacy_row_uses_info_release_and_archive_primary_time(db, tmp_path):
    session, video = db
    video.file_path = str(tmp_path)
    video.raw_metadata = {"archive_backfill": "completed", "primary_media": "archive.mp4",
                          "recording_start_offset_ms": 0}
    (tmp_path / "abcdefghijk.info.json").write_text(
        json.dumps({"release_timestamp": 1_000}), encoding="utf-8"
    )
    renderer = {
        "id": "live-legacy",
        "timestampUsec": "33015000000",
        "authorName": {"simpleText": "member"},
        "authorBadges": [{"liveChatAuthorBadgeRenderer": {
            "tooltip": "Member (2 months)",
            "customThumbnail": {"thumbnails": [{"url": "https://yt3.ggpht.com/member.png"}]},
        }}],
        "message": {"runs": [{"text": "hello"}]},
    }
    record = {"isLive": True, "videoOffsetTimeMsec": 0,
              "liveChatTextMessageRenderer": renderer}
    row = ChatMessage(video_id=video.id, message_id="live-legacy", offset_ms=3_648_000,
                      author_name="member", author_badges=["Member (2 months)"],
                      message_text="hello", message_runs=[], message_type="normal",
                      raw={"record": record, "renderer": renderer})
    session.add(row)
    session.flush()

    assert reparse_video_chat(session, video.id) == 1
    assert video.raw_metadata["release_timestamp"] == 1_000
    assert row.offset_ms == 32_015_000
    assert row.message_runs == [{"type": "text", "text": "hello"}]
    assert row.author_badges[0]["type"] == "member"

    listing = chat_messages(video.id, db=session)
    danmaku = dplayer_danmaku(video.id, "all-0", db=session)
    assert listing["messages"][0]["time_seconds"] == pytest.approx(32_015)
    assert danmaku["data"][0][0] == pytest.approx(32_015)


def test_live_chat_reparse_uses_inspect_when_metadata_and_info_are_missing(db, monkeypatch):
    from app.services import youtube

    session, video = db
    renderer = {"id": "inspect-release", "timestampUsec": "33015000000",
                "authorName": {"simpleText": "A"}, "message": {"runs": [{"text": "hi"}]}}
    record = {"isLive": True, "videoOffsetTimeMsec": 2_000,
              "liveChatTextMessageRenderer": renderer}
    ingest_records(session, video.id, [record], complete_snapshot=False)
    message = session.query(ChatMessage).one()
    message.raw = {"record": record, "renderer": renderer}
    message.offset_ms = 2_000
    monkeypatch.setattr(youtube, "inspect", lambda *_args, **_kwargs: {"release_timestamp": 1_000})

    assert reparse_video_chat(session, video.id) == 1
    assert message.offset_ms == 32_015_000
    assert video.raw_metadata["release_timestamp"] == 1_000


def test_chat_asset_cache_is_host_limited_and_reuses_download(tmp_path, monkeypatch):
    from app.services import chat_assets

    class Response:
        headers = {"Content-Length": "4", "Content-Type": "image/png"}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def geturl(self):
            return "https://yt3.ggpht.com/emoji.png"
        def read(self, _limit):
            return b"data"

    calls = []
    monkeypatch.setattr(chat_assets, "urlopen", lambda _request, timeout: (calls.append(timeout), Response())[1])
    url = "https://yt3.ggpht.com/emoji.png"
    first = chat_assets.cache_asset(url, tmp_path)
    second = chat_assets.cache_asset(url, tmp_path)
    assert first == second and first.read_bytes() == b"data"
    assert calls == [chat_assets.ASSET_TIMEOUT_SECONDS]
    with pytest.raises(ValueError):
        chat_assets.cache_asset("http://yt3.ggpht.com/emoji.png", tmp_path)
    with pytest.raises(ValueError):
        chat_assets.cache_asset("https://example.com/evil.png", tmp_path)


def test_chat_asset_cache_rejects_oversized_response(tmp_path, monkeypatch):
    from app.services import chat_assets

    class Response:
        headers = {"Content-Type": "image/png"}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return None
        def geturl(self):
            return "https://yt3.ggpht.com/large.png"
        def read(self, limit):
            return b"x" * limit

    monkeypatch.setattr(chat_assets, "urlopen", lambda *_args, **_kwargs: Response())
    with pytest.raises(ValueError):
        chat_assets.cache_asset("https://yt3.ggpht.com/large.png", tmp_path)


def test_chat_asset_cache_handles_timeout(tmp_path, monkeypatch):
    from app.services import chat_assets

    def timeout(*_args, **_kwargs):
        raise TimeoutError("test timeout")

    monkeypatch.setattr(chat_assets, "urlopen", timeout)
    with pytest.raises(ValueError):
        chat_assets.cache_asset("https://yt3.ggpht.com/slow.png", tmp_path)


def test_chat_api_returns_runs_local_time_and_range(client):
    from app.db import SessionLocal

    with SessionLocal() as session:
        channel = Channel(youtube_channel_id="UCchatapi", url="https://youtube.com/@chatapi", name="chat")
        session.add(channel)
        session.flush()
        video = Video(youtube_video_id="abcdefghijk2", channel_id=channel.id,
                      raw_metadata={"recording_start_offset_ms": 2000})
        session.add(video)
        session.flush()
        session.add(ChatMessage(
            video_id=video.id, message_id="api-message", offset_ms=5000,
            author_name="owner", author_role="owner", author_badges=[],
            message_text="💚:heart:", message_runs=[{"type": "emoji", "text": "💚"},
                {"type": "emote", "id": "custom", "shortcut": ":heart:",
                 "url": "https://yt3.ggpht.com/heart.png"}],
            message_type="superchat", amount_display="¥1,000", superchat_amount=1000,
            superchat_currency="JPY", superchat_color="#ffb300", raw={}))
        session.commit()
        video_id = video.id
    response = client.get(f"/api/videos/{video_id}/chat?after_ms=2000&before_ms=4000")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    message = payload["messages"][0]
    assert message["time_seconds"] == 3
    assert message["runs"][0] == {"type": "emoji", "text": "💚"}
    assert message["runs"][1]["url"].startswith("/api/chat-assets/")
    assert message["author"]["role"] == "owner"
    assert message["amount_display"] == "¥1,000"
    assert payload["superchat_totals"]["JPY"] == 1000


def test_chat_api_supports_legacy_string_badges_and_raw_renderer(client):
    from app.db import SessionLocal

    renderer = {
        "id": "legacy-api",
        "authorName": {"simpleText": "旧形式"},
        "authorBadges": [{"liveChatAuthorBadgeRenderer": {
            "tooltip": "Member (1 year)",
            "customThumbnail": {"thumbnails": [{"url": "https://yt3.ggpht.com/legacy-badge.png"}]},
        }}],
        "message": {"runs": [{"text": "本文 "}, {"emoji": {
            "emojiId": "UClegacy/e", "isCustomEmoji": True, "shortcuts": [":_old:"],
            "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/legacy-emote.png"}]},
        }}]},
    }
    record = {"liveChatTextMessageRenderer": renderer, "videoOffsetTimeMsec": 1000}
    with SessionLocal() as session:
        channel = Channel(youtube_channel_id="UColdchat", url="https://youtube.com/@oldchat", name="old")
        session.add(channel)
        session.flush()
        video = Video(youtube_video_id="abcdefghijk4", channel_id=channel.id)
        session.add(video)
        session.flush()
        session.add(ChatMessage(
            video_id=video.id,
            message_id="legacy-api",
            offset_ms=1000,
            author_name="旧形式",
            author_badges=["Member (1 year)"],
            message_text="本文 :_old:",
            message_runs=[],
            message_type="normal",
            raw={"record": record, "renderer": renderer},
        ))
        session.commit()
        video_id = video.id

    response = client.get(f"/api/videos/{video_id}/chat")
    assert response.status_code == 200
    message = response.json()["messages"][0]
    assert message["runs"][-1]["type"] == "emote"
    assert message["author"]["badges"][0]["label"] == "Member (1 year)"
    assert message["author"]["badges"][0]["url"].startswith("/api/chat-assets/")


def test_chat_asset_key_must_resolve_to_a_stored_chat_url(client, monkeypatch):
    from app.db import SessionLocal
    from app.services import chat_assets

    with SessionLocal() as session:
        channel = Channel(youtube_channel_id="UCasset", url="https://youtube.com/@asset", name="asset")
        session.add(channel)
        session.flush()
        video = Video(youtube_video_id="abcdefghijk3", channel_id=channel.id)
        session.add(video)
        session.flush()
        session.add(ChatMessage(video_id=video.id, message_id="asset-message", offset_ms=0,
            author_name="viewer", author_badges=[], message_runs=[], message_text="", raw={}))
        session.commit()
    monkeypatch.setattr(chat_assets, "urlopen", lambda *_args, **_kwargs: pytest.fail("unexpected network"))
    key = chat_assets.asset_key("https://yt3.ggpht.com/not-in-database.png")
    response = client.get(f"/api/chat-assets/{key}")
    assert response.status_code == 404

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

def test_dplayer_membership_heading_is_localized(db):
    session, video = db
    session.add(ChatMessage(
        video_id=video.id,
        message_id="membership-heading",
        offset_ms=0,
        author_name="member",
        message_text="ありがとう",
        message_type="membership",
        header_text="Member for 45 months · Gold",
    ))
    session.add_all([
        ChatMessage(video_id=video.id, message_id="gift-purchase", offset_ms=1000,
            author_name="gifter", message_text="", message_type="membership_gift",
            header_text="メンバーギフト"),
        ChatMessage(video_id=video.id, message_id="paid", offset_ms=2000,
            author_name="supporter", message_text="thanks", message_type="superchat",
            amount_display="$5.00", superchat_color="#00aa00"),
        ChatMessage(video_id=video.id, message_id="sticker", offset_ms=3000,
            author_name="supporter", message_text="", message_type="supersticker",
            amount_display="¥500", superchat_color="#00bb00"),
    ])
    session.flush()
    result = dplayer_danmaku(video.id, "all-0", session)
    assert result["data"][0][-1] == "メンバー 45 か月 · Gold ありがとう"
    assert len(dplayer_danmaku(video.id, "membership-0", session)["data"]) == 2
    assert len(dplayer_danmaku(video.id, "superchat-0", session)["data"]) == 2
