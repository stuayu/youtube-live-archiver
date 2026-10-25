from datetime import datetime, timedelta, timezone

from app.services.activity_profile import _classify_shares, build_profile


def stamp(day: int, hour: int) -> datetime:
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


def test_insufficient_profile_is_normal():
    profile = build_profile([stamp(1, 10)] * 7)
    assert profile.mode == "insufficient"
    assert set(profile.levels) == {"normal"}


def test_hour_mode_repeats_levels_for_all_weekdays():
    profile = build_profile([stamp(1, 12)] * 8)
    assert profile.mode == "hour"
    assert len(profile.levels) == 168
    assert all(profile.levels[12] == profile.levels[day * 24 + 12] for day in range(7))


def test_weekhour_mode_and_next_hot_boundary():
    starts = [datetime(2026, 9, 7 + i, 21, tzinfo=timezone.utc) for i in range(24)]
    profile = build_profile(starts)
    assert profile.mode == "weekhour"
    assert len(profile.levels) == 168
    assert profile.level_at(datetime(2026, 10, 4, 21, tzinfo=timezone.utc)) == "hot"
    boundary = profile.next_hot_start(datetime(2026, 10, 4, 8, tzinfo=timezone.utc))
    assert boundary == datetime(2026, 10, 4, 21, tzinfo=timezone.utc)


def test_smoothing_carries_activity_to_neighboring_hours():
    profile = build_profile([stamp(1, 12)] * 24)
    tuesday = profile.levels[24:48]
    assert tuesday[12] == "hot"
    assert tuesday[11] != "cold" and tuesday[13] != "cold"


def test_next_hot_start_wraps_week():
    starts = [datetime(2026, 9, 13, 23, tzinfo=timezone.utc)] * 24
    profile = build_profile(starts)
    assert profile.next_hot_start(datetime(2026, 10, 4, 23, 30, tzinfo=timezone.utc)) == datetime(2026, 10, 5, 0, tzinfo=timezone.utc)


def test_hot_ties_choose_earlier_bins():
    profile = build_profile([stamp(1, 12)] * 24)
    tuesday = profile.levels[24:48]
    assert tuesday[11] == "hot"
    assert tuesday[13] == "warm"


def test_cumulative_seventy_and_ninety_five_percent_boundaries():
    assert _classify_shares([0.70, 0.25, 0.05]) == ["hot", "warm", "cold"]


def test_equal_share_order_uses_earlier_index():
    levels = _classify_shares([0.5, 0.25, 0.25])
    assert levels == ["hot", "hot", "warm"]


def test_profile_datetime_normalizes_timezone():
    profile = build_profile([datetime(2026, 9, 1, 12, tzinfo=timezone(timedelta(hours=9)))] * 8)
    assert profile.level_at(datetime(2026, 9, 1, 3, tzinfo=timezone.utc)) != "cold"
