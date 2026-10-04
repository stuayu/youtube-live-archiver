from pathlib import Path
from datetime import datetime, timezone
import json
import tempfile
import shutil
import pytest
from app.services.backfill import eligible_backfill_ids, media_file, parse_byte_range, registered_video_directory


def test_backfill_limit_chooses_newest_completed_only():
    entries = [
        {"id": "old", "live_status": "was_live", "timestamp": 100},
        {"id": "new", "live_status": "was_live", "timestamp": 300},
        {"id": "mid", "live_status": "was_live", "timestamp": 200},
        {"id": "upcoming", "live_status": "is_upcoming", "timestamp": 400},
    ]
    assert eligible_backfill_ids(entries, 0) == set()
    assert eligible_backfill_ids(entries, 2) == {"new", "mid"}
    assert eligible_backfill_ids(entries, 0, datetime.fromtimestamp(150, timezone.utc)) == {"mid", "new"}


@pytest.mark.parametrize("header,expected", [("bytes=0-9", (0, 9)), ("bytes=10-", (10, 99)), ("bytes=-10", (90, 99))])
def test_range_parsing(header, expected):
    assert parse_byte_range(header, 100) == expected


def test_invalid_range_rejected():
    with pytest.raises(ValueError): parse_byte_range("bytes=100-", 100)
    with pytest.raises(ValueError): parse_byte_range("items=0-1", 100)


def test_registered_video_path_must_be_inside_storage():
    scratch = Path(__file__).parent / ".path-security-test"
    root, valid, outside = scratch / "storage", scratch / "storage" / "channel" / "video", scratch / "outside"
    try:
        valid.mkdir(parents=True, exist_ok=True); outside.mkdir(parents=True, exist_ok=True)
        assert registered_video_directory(str(valid), str(root)) == valid.resolve()
        with pytest.raises(ValueError): registered_video_directory(str(outside), str(root))
        with pytest.raises(ValueError): registered_video_directory(str(root), str(root))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def test_media_file_selection_prefers_playback_then_primary_then_largest_and_partial(tmp_path):
    primary = tmp_path / "video-part-2.mkv"
    largest = tmp_path / "video.mkv"
    playback = tmp_path / "video.playable.mp4"
    partial = tmp_path / "video.partial.mkv"
    primary.write_bytes(b"p" * 20)
    largest.write_bytes(b"l" * 100)
    playback.write_bytes(b"b" * 5)
    (tmp_path / "recording.json").write_text(json.dumps({"primary_media": primary.name}), encoding="utf-8")
    assert media_file(tmp_path) == playback.resolve()
    playback.unlink()
    assert media_file(tmp_path) == primary.resolve()
    (tmp_path / "recording.json").unlink()
    assert media_file(tmp_path) == largest.resolve()
    largest.unlink()
    primary.unlink()
    partial.write_bytes(b"x" * 10)
    assert media_file(tmp_path) == partial.resolve()
