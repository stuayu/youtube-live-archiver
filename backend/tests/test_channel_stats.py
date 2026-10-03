from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.services import channel_stats as stats_service
from app.services.channel_stats import aggregate_stats, period_deltas


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_aggregation_uses_last_observed_per_bucket_and_preserves_gaps():
    rows = [
        {"captured_at": dt("2026-01-01T01:00:00"), "subscriber_count": 100, "subscriber_count_rounded": True, "video_count": 2, "view_count": None},
        {"captured_at": dt("2026-01-01T20:00:00"), "subscriber_count": 110, "subscriber_count_rounded": True, "video_count": 3, "view_count": 40},
        {"captured_at": dt("2026-01-03T02:00:00"), "subscriber_count": 130, "subscriber_count_rounded": False, "video_count": 4, "view_count": 50},
    ]
    days = aggregate_stats(rows, "day")
    assert [x["subscriber_count"] for x in days] == [110, None, 130]
    assert [x["captured_at"].day for x in days] == [1, 2, 3]
    weeks = aggregate_stats(rows, "week")
    assert len(weeks) == 1 and weeks[0]["subscriber_count"] == 130
    months = aggregate_stats(rows, "month")
    assert len(months) == 1 and months[0]["view_count"] == 50


def test_period_deltas_require_observed_prior_snapshot():
    rows = [
        {"captured_at": dt("2025-12-31T00:00:00"), "subscriber_count": 90, "video_count": 4, "view_count": 500},
        {"captured_at": dt("2026-01-01T00:00:00"), "subscriber_count": 100, "video_count": 5, "view_count": 1000},
        {"captured_at": dt("2026-01-24T00:00:00"), "subscriber_count": 130, "video_count": 7, "view_count": 1800},
        {"captured_at": dt("2026-01-31T00:00:00"), "subscriber_count": 140, "video_count": 8, "view_count": 2200},
    ]
    deltas = period_deltas(rows, dt("2026-01-31T00:00:00"))
    assert deltas["day"]["subscriber_count"] == 10
    assert deltas["week"]["subscriber_count"] == 10
    assert deltas["week"]["view_count"] == 400
    assert deltas["month"]["subscriber_count"] == 50
    assert period_deltas([], dt("2026-01-31T00:00:00"))["day"]["video_count"] is None


def test_snapshot_skips_missing_metrics_and_obeys_interval(monkeypatch):
    class DB:
        latest = None
        added = []
        def scalar(self, _query): return self.latest
        def add(self, value): self.added.append(value)

    fixed = dt("2026-02-01T12:00:00")
    monkeypatch.setattr(stats_service, "now", lambda: fixed)
    monkeypatch.setattr(stats_service.settings, "channel_stats_interval_hours", 24)
    db, channel = DB(), SimpleNamespace(id=7)
    assert not stats_service.record_snapshot(db, channel, {"description": "metadata without stats"})
    assert db.added == []
    assert stats_service.record_snapshot(db, channel, {"channel_follower_count": 1234})
    snapshot = db.added[-1]
    assert snapshot.subscriber_count == 1234 and snapshot.subscriber_count_rounded is True
    db.latest = SimpleNamespace(captured_at=fixed - timedelta(hours=23))
    assert not stats_service.record_snapshot(db, channel, {"channel_follower_count": 1250})
    db.latest = SimpleNamespace(captured_at=fixed - timedelta(hours=24))
    assert stats_service.record_snapshot(db, channel, {"channel_follower_count": 1250})
