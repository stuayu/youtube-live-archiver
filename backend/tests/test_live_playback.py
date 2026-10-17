import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.live_playback import (
    cleanup_hls,
    fallback_to_direct,
    hls_playlist_ready,
    safe_hls_asset,
    tee_args,
)


def test_tee_launch_failure_falls_back_to_direct_recorder():
    events = []
    result = fallback_to_direct(RuntimeError("ffmpeg missing"), None,
        lambda: events.append("direct") or "direct-process",
        lambda error, code: events.append((str(error), code)))
    assert result == "direct-process"
    assert events == [("ffmpeg missing", None), "direct"]


def test_hls_segments_are_published_only_after_complete_write(tmp_path):
    args = tee_args("ffmpeg", tmp_path / "live", tmp_path / "archive.mkv")
    assert "hls_flags=append_list+independent_segments+temp_file" in args[-1]


def test_hls_playlist_is_ready_only_after_a_published_segment(tmp_path):
    playlist = tmp_path / "index.m3u8"
    playlist.write_text("#EXTM3U\n#EXTINF:6.0,\nsegment_000001.ts\n")
    segment = tmp_path / "segment_000001.ts"
    assert not hls_playlist_ready(tmp_path)
    segment.write_bytes(b"complete segment")
    assert hls_playlist_ready(tmp_path)
    segment.unlink()
    assert not hls_playlist_ready(tmp_path)


def test_hls_cleanup_requires_verified_archive(tmp_path, monkeypatch):
    import app.services.live_playback as live
    media = tmp_path / "video.mkv"
    media.write_bytes(b"durable archive")
    sidecar = tmp_path / "live" / "video"; sidecar.mkdir(parents=True)
    (sidecar / "index.m3u8").write_text("#EXTM3U")
    video = SimpleNamespace(live_hls_path=str(sidecar))
    monkeypatch.setattr(live, "archive_is_complete", lambda path, ffprobe: False)
    assert cleanup_hls(video, media) is False
    assert sidecar.exists() and media.exists()
    assert video.live_hls_path == str(sidecar)


def test_live_endpoint_asset_resolution_blocks_path_traversal(tmp_path):
    hls = tmp_path / "storage" / "live" / "abc123"; hls.mkdir(parents=True)
    (hls / "index.m3u8").write_text("#EXTM3U\nsegment_000001.ts\n")
    (hls / "segment_000001.ts").write_bytes(b"segment")
    video = SimpleNamespace(live_hls_path=str(hls), storage=SimpleNamespace(root_path=str(tmp_path / "storage")), youtube_video_id="abc123")
    assert safe_hls_asset(video, "index.m3u8") == (hls / "index.m3u8").resolve()
    for asset in ("../secret", "%2e%2e%2fsecret", "segment_000001.ts/../../secret"):
        with pytest.raises(ValueError):
            safe_hls_asset(video, asset)


def test_hls_delivery_endpoint_rejects_traversal(tmp_path):
    from fastapi import HTTPException
    from app.api import live_asset
    hls = tmp_path / "storage" / "live" / "abc123"; hls.mkdir(parents=True)
    (hls / "index.m3u8").write_text("#EXTM3U")
    video = SimpleNamespace(live_hls_path=str(hls), storage=SimpleNamespace(root_path=str(tmp_path / "storage")), youtube_video_id="abc123")
    db = SimpleNamespace(get=lambda model, video_id: video)
    with pytest.raises(HTTPException) as error:
        live_asset(1, "../secret", db)
    assert error.value.status_code == 404


def test_verified_hls_cleanup_never_removes_mkv(tmp_path, monkeypatch):
    import app.services.live_playback as live
    media = tmp_path / "video.mkv"
    media.write_bytes(b"archive")
    sidecar = tmp_path / "live" / "abc123"; sidecar.mkdir(parents=True)
    (sidecar / "index.m3u8").write_text("#EXTM3U")
    video = SimpleNamespace(live_hls_path=str(sidecar))
    monkeypatch.setattr(live, "archive_is_complete", lambda path, ffprobe: path == media)
    assert cleanup_hls(video, media) is True
    assert media.is_file() and media.read_bytes() == b"archive"
    assert not sidecar.exists()
    assert video.live_hls_path is None


def test_live_chat_delta_waits_for_complete_jsonl_line(tmp_path, monkeypatch):
    import app.services.chat as chat
    source = tmp_path / "live_chat.json"
    source.write_bytes(b'{"id":"one"}\n{"id":"incomplete"')
    received = []
    prefetched = []
    monkeypatch.setattr(chat, "ingest_records", lambda db, video_id, records, complete_snapshot: received.extend(records))
    monkeypatch.setattr(chat, "_schedule_asset_prefetch", prefetched.append)
    next_offset = chat.ingest_jsonl_delta(None, 1, source, 0)
    assert received == [{"id": "one"}]
    assert prefetched == [1]
    assert next_offset == len(b'{"id":"one"}\n')
    with source.open("ab") as stream:
        stream.write(b"}\n")
    chat.ingest_jsonl_delta(None, 1, source, next_offset)
    assert received == [{"id": "one"}, {"id": "incomplete"}]


def test_live_chat_delta_reads_only_bounded_appended_bytes(tmp_path, monkeypatch):
    import app.services.chat as chat
    source = tmp_path / "large-live-chat.json"
    lines = [json.dumps({"id": str(index)}).encode() + b"\n" for index in range(20)]
    source.write_bytes(b"old-data\n" + b"".join(lines))
    received = []
    monkeypatch.setattr(chat, "ingest_records",
                        lambda db, video_id, records, complete_snapshot: received.extend(records))
    monkeypatch.setattr(chat, "_schedule_asset_prefetch", lambda _video_id: None)
    offset = len(b"old-data\n")
    next_offset = chat.ingest_jsonl_delta(None, 1, source, offset, max_bytes=100, max_records=4)
    assert len(received) == 4
    assert next_offset < source.stat().st_size


def test_live_chat_delta_skips_oversized_line_and_warns_once(tmp_path, monkeypatch, caplog):
    import logging
    import app.services.chat as chat

    video_id = 99001
    chat._oversized_chat_warnings.discard(video_id)
    source = tmp_path / "oversized-live-chat.json"
    source.write_bytes(b"x" * 150)
    received = []
    monkeypatch.setattr(chat, "ingest_records",
                        lambda db, _video_id, records, complete_snapshot: received.extend(records))
    monkeypatch.setattr(chat, "_schedule_asset_prefetch", lambda _video_id: None)

    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)
    logger = logging.getLogger("app.services.chat")
    logger.addHandler(handler)
    try:
        offset = chat.ingest_jsonl_delta(None, video_id, source, max_bytes=100)
        assert offset == 0
        with source.open("ab") as stream:
            stream.write(b"\n{\"id\":\"next\"}\n")
        offset = chat.ingest_jsonl_delta(None, video_id, source, offset, max_bytes=100)
        next_offset = chat.ingest_jsonl_delta(None, video_id, source, offset, max_bytes=100)
    finally:
        logger.removeHandler(handler)

    assert offset == 151
    assert next_offset == source.stat().st_size
    assert received == [{"id": "next"}]
    assert sum("skipping oversized or malformed" in record.getMessage() for record in records) == 1


def test_live_chat_database_failure_rolls_back_without_raising(tmp_path, monkeypatch):
    import app.services.chat as chat
    import app.services.recorder as recorder

    class Database:
        rollbacks = 0

        def rollback(self):
            self.rollbacks += 1

    video = SimpleNamespace(id=1, youtube_video_id='stream-id', channel=SimpleNamespace(chat_enabled=True))
    target = tmp_path
    recorder._chat_file(target, 'capture').write_text('{"id":"one"}\n', encoding='utf-8')
    monkeypatch.setattr(chat, 'ingest_jsonl_delta', lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('db down')))
    database = Database()
    offsets = {}

    recorder._ingest_live_chat_delta(database, video, target, 'capture', offsets)

    assert database.rollbacks == 1
    assert offsets == {}
