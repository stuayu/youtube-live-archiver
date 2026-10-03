from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.live_playback import cleanup_hls, fallback_to_direct, safe_hls_asset


def test_tee_launch_failure_falls_back_to_direct_recorder():
    events = []
    result = fallback_to_direct(RuntimeError("ffmpeg missing"), None,
        lambda: events.append("direct") or "direct-process",
        lambda error, code: events.append((str(error), code)))
    assert result == "direct-process"
    assert events == [("ffmpeg missing", None), "direct"]


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
    (hls / "index.m3u8").write_text("#EXTM3U")
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
    monkeypatch.setattr(chat, "ingest_records", lambda db, video_id, records, complete_snapshot: received.extend(records))
    next_offset = chat.ingest_jsonl_delta(None, 1, source, 0)
    assert received == [{"id": "one"}]
    assert next_offset == len(b'{"id":"one"}\n')
    with source.open("ab") as stream:
        stream.write(b"}\n")
    chat.ingest_jsonl_delta(None, 1, source, next_offset)
    assert received == [{"id": "one"}, {"id": "incomplete"}]
