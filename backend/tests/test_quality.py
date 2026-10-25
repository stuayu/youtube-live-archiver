from types import SimpleNamespace

from app.services.quality import DEFAULT_ARCHIVE, PRESET_MAP, preset_selector, resolve_quality
from app.workers import format_args, format_unavailable


def test_presets_and_defaults():
    assert len(PRESET_MAP) >= 7
    assert preset_selector("av1") == DEFAULT_ARCHIVE
    assert format_args("best") == []
    assert format_args("") == []


def test_profile_defaults_differ():
    channel = SimpleNamespace(live_quality=None, archive_quality=None, quality=None)
    assert resolve_quality(None, channel, "live")[0] == ""
    assert resolve_quality(None, channel, "archive")[0] == DEFAULT_ARCHIVE


def test_resolution_priority_video_then_channel_then_global():
    channel = SimpleNamespace(live_quality="720p", archive_quality="vp9", quality="best")
    video = SimpleNamespace(raw_metadata={"archive_quality": "h264"})
    assert resolve_quality(video, channel, "archive")[0] == PRESET_MAP["h264"]["selector"]
    assert resolve_quality(None, channel, "archive")[0] == PRESET_MAP["vp9"]["selector"]
    assert resolve_quality(None, None, "archive", "720p")[0] == PRESET_MAP["720p"]["selector"]


def test_format_fallback_detection():
    assert format_unavailable(b"ERROR: Requested format is not available")
    assert not format_unavailable(b"network timeout")
