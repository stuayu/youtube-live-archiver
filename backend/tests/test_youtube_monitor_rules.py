from datetime import datetime, timedelta, timezone
import subprocess

import pytest

from app.services.monitor import calculate_next_interval
from app.services.activity_profile import Profile, build_profile
from app.services.recovery import classify_recovery
from app.services.youtube import classify_ytdlp_error, resolve_channel, streams_url, ytdlp_command
from app.services.policy import classify_remote
from app.workers import _inspection


@pytest.mark.parametrize(("message", "expected"), [
    ("HTTP Error 429", "RATE_LIMITED"),
    ("Sign in to confirm you're not a bot", "RATE_LIMITED"),
    ("Sign in to confirm you’re not a bot", "RATE_LIMITED"),
    ("This content isn't available, try again later", "RATE_LIMITED"),
    ("This channel does not have a streams tab", "NO_STREAMS_TAB"),
    ("Private video. Sign in if granted access", "PRIVATE"),
    ("This video is private", "PRIVATE"),
    ("This video has been removed", "DELETED"),
    ("This video is available to this channel's members", "MEMBERS_ONLY"),
    ("This video is unavailable", "UNAVAILABLE"),
    ("This live event will begin in 2 hours", "UPCOMING"),
    ("Premieres in 3 hours", "UPCOMING"),
    ("network timeout", "UNKNOWN"),
])
def test_ytdlp_error_classification(message, expected):
    assert classify_ytdlp_error(message) == expected


@pytest.mark.parametrize("message", [
    "ERROR: Sign in to confirm you�fre not a bot",
    "ERROR: Sign in to confirm you’re not a bot",
])
def test_bot_challenge_is_rate_limited_when_apostrophe_is_mojibake(message):
    assert classify_ytdlp_error(message) == "RATE_LIMITED"


def test_age_gate_sign_in_prompt_is_not_a_bot_challenge():
    message = "ERROR: Sign in to confirm your age. This video may be inappropriate for some users."
    assert classify_ytdlp_error(message) == "UNKNOWN"


def test_streams_url_normalizes_channel_tabs_and_query():
    assert streams_url("https://www.youtube.com/@foo/videos?view=0") == "https://www.youtube.com/@foo/streams"
    assert streams_url("https://www.youtube.com/channel/UCabcdefghijabcdefghij12/live") == "https://www.youtube.com/channel/UCabcdefghijabcdefghij12/streams"
    assert streams_url("https://youtube.com/c/foo/featured") == "https://youtube.com/c/foo/streams"


def test_resolve_channel_always_returns_canonical(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij12"
    fake_youtube.set_state({"channels": {"@foo": {"channel_id": channel_id}, "foo": {"channel_id": channel_id}},
                            "videos": {"video1": {"channel_id": channel_id}}})
    assert resolve_channel("youtube.com/@foo/videos?view=0") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel("www.youtube.com/c/foo/featured") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel("https://youtube.com/user/foo/live") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel("@foo") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel(f"https://youtube.com/channel/{channel_id}/featured") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel(channel_id) == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel("https://www.youtube.com/watch?v=video1") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    assert resolve_channel("https://youtu.be/video1") == (channel_id, f"https://www.youtube.com/channel/{channel_id}")
    with pytest.raises(ValueError, match="YouTube のチャンネル URL ではありません"):
        resolve_channel("https://example.org/@foo")


def test_ytdlp_py_path_uses_current_python(fake_youtube):
    assert ytdlp_command()[0]
    assert ytdlp_command()[1].endswith("fake_ytdlp.py")


def test_fake_upcoming_requires_ignore_no_formats_error(fake_youtube):
    fake_youtube.set_state({"channels": {}, "videos": {"soon": {"live_status": "is_upcoming", "release_timestamp": 1}}})
    base = [*fake_youtube.command(), "--dump-single-json", "--skip-download", "https://youtube.com/watch?v=soon"]
    failed = subprocess.run(base, capture_output=True, text=True)
    succeeded = subprocess.run([*base[:-1], "--ignore-no-formats-error", base[-1]], capture_output=True, text=True)
    assert failed.returncode != 0 and "This live event will begin" in failed.stderr
    assert succeeded.returncode == 0 and '"live_status": "is_upcoming"' in succeeded.stdout


NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


@pytest.mark.parametrize("poll", ["30", "45", "60", "90", "120", "180", "240", "360"])
def test_numeric_interval_uses_ten_percent_jitter(poll):
    minutes = int(poll)
    assert calculate_next_interval(poll, [], NOW) == pytest.approx((minutes * 54, minutes * 66))


@pytest.mark.parametrize(("offset", "expected"), [
    (timedelta(minutes=31), (60, 60)),
    (timedelta(minutes=30), (120, 240)),
    (timedelta(minutes=5), (20, 40)),
    (-timedelta(minutes=5), (20, 40)),
    (-timedelta(minutes=10), (60, 120)),
    (-timedelta(minutes=60), (300, 600)),
    (-timedelta(hours=6), (1800, 3600)),
])
def test_scheduled_interval_boundaries(offset, expected):
    assert calculate_next_interval("AUTO", [NOW + offset], NOW) == pytest.approx(expected)


def test_unknown_schedule_uses_six_to_twenty_minutes_or_shorter():
    assert calculate_next_interval("AUTO", [None], NOW) == (600, 1200)
    assert calculate_next_interval("30", [None], NOW) == (600, 1200)
    assert calculate_next_interval("AUTO", [NOW + timedelta(hours=6), None], NOW) == (600, 1200)


def test_scheduled_shortening_also_applies_to_fixed_interval():
    assert calculate_next_interval("360", [NOW + timedelta(minutes=4)], NOW) == (20, 40)


def test_auto_activity_levels_and_hot_start_cap():
    history = [datetime(2026, 9, 1, 21, tzinfo=timezone.utc)] * 8
    profile = build_profile(history)
    hot_now = datetime(2026, 10, 4, 21, tzinfo=timezone.utc)
    cold_now = datetime(2026, 10, 4, 9, tzinfo=timezone.utc)
    assert calculate_next_interval("AUTO", [], NOW) == (1800, 3600)
    assert calculate_next_interval("AUTO", [], hot_now, profile) == (900, 1800)
    assert calculate_next_interval("AUTO", [], cold_now, profile) == (7200, 14400)
    warm_profile = Profile(24, "weekhour", ("warm",) * 168)
    assert calculate_next_interval("AUTO", [], cold_now, warm_profile) == (1800, 3600)
    cold_levels = ["cold"] * 168
    cold_levels[cold_now.weekday() * 24 + 21] = "hot"
    upcoming_hot = Profile(24, "weekhour", tuple(cold_levels))
    assert calculate_next_interval("AUTO", [], cold_now.replace(hour=20), upcoming_hot) == (4200, 4200)


@pytest.mark.parametrize(("attempt", "status", "action"), [
    (1, "UNAVAILABLE", "DEFER"),
    (2, "UNAVAILABLE", "DEFER"),
    (3, "UNAVAILABLE", "PROTECT"),
    (1, "PRIVATE", "PROTECT"),
    (1, "DELETED", "PROTECT"),
    (1, "MEMBERS_ONLY", "PROTECT"),
])
def test_recovery_classification_attempts(attempt, status, action):
    assert classify_recovery(status, None, None, attempt).action == action


def test_recovery_upcoming_waits_for_live():
    assert classify_recovery("PUBLIC", None, {"live_status": "is_upcoming"}).action == "WAIT_LIVE"


@pytest.mark.parametrize(("message", "status"), [("Private video", "PRIVATE"), ("This video has been removed", "DELETED")])
def test_inspection_classifications_protect_local_copy(monkeypatch, message, status):
    from app import workers
    class DummyVideo:
        youtube_video_id = "test-video"
        channel = None
    monkeypatch.setattr(workers, "inspect", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(message)))
    actual, duration, error, raw = _inspection(DummyVideo())
    decision = classify_remote(actual, error, 100, duration)
    assert actual == status
    assert (duration, error, raw) == (None, None, None)
    assert decision.status == "PROTECTED"
