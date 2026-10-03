from pathlib import Path
from datetime import datetime, timezone
import tempfile
import shutil
import pytest
from app.services.backfill import eligible_backfill_ids, parse_byte_range, registered_video_directory


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
