from datetime import timedelta
import time

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Channel, Video, now
from app.services import monitor
from app.workers import channel_monitor, recovery_worker


pytestmark = pytest.mark.integration


def add_channel(channel_id="UCabcdefghijabcdefghij12", *, backfill_limit=0, poll_interval="AUTO"):
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id=channel_id, url="https://www.youtube.com/@old-url/videos",
                           name=channel_id, backfill_limit=backfill_limit, poll_interval=poll_interval)
        db.add(channel)
        db.commit()
        db.refresh(channel)
        return channel.id


def set_due(channel_id):
    with SessionLocal() as db:
        channel = db.get(Channel, channel_id)
        channel.next_check_at = now() - timedelta(seconds=1)
        db.commit()


def read_channel(channel_id):
    with SessionLocal() as db:
        return db.get(Channel, channel_id)


def read_videos():
    with SessionLocal() as db:
        return db.scalars(select(Video).order_by(Video.youtube_video_id)).all()


def entries(*values):
    return [{"id": video_id, "title": video_id, "live_status": status,
             "thumbnails": [{"url": f"https://img.test/{video_id}.jpg"}]} for video_id, status in values]


def channel_state(channel_id, items, **options):
    return {"channels": {channel_id: {"entries": items, **options}}, "videos": {}}


def test_first_tick_scans_immediately_and_backfill_limit(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij12"
    pk = add_channel(channel_id, backfill_limit=2)
    fake_youtube.set_state(channel_state(channel_id, entries(("old-a", "was_live"), ("old-b", "was_live"),
                                                         ("old-c", "was_live"))))
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert [rows[key].status for key in ("old-a", "old-b", "old-c")] == ["DISCOVERED", "DISCOVERED", "SKIPPED"]
    assert read_channel(pk).baseline_completed_at is not None
    assert read_channel(pk).next_check_at > now()
    assert len(fake_youtube.calls()) == 2
    assert fake_youtube.calls()[0]["argv"][-1] == f"https://www.youtube.com/channel/{channel_id}/streams"
    assert fake_youtube.calls()[1]["argv"][-1] == "https://www.youtube.com/watch?v=old-c"
    assert rows["old-c"].raw_metadata["start_probe_attempted"] is True


def test_twenty_slow_channels_are_dispatched_without_blocking_scheduler(fake_youtube, monkeypatch):
    monkeypatch.setattr(settings, "monitor_batch_size", 20)
    monkeypatch.setattr(settings, "monitor_concurrency", 3)
    monkeypatch.setattr(settings, "remote_timeout_seconds", 0.15)
    channel_ids = [f"UCslow{index:018d}" for index in range(20)]
    for channel_id in channel_ids:
        add_channel(channel_id)
        set_due_by_youtube_id(channel_id)
    fake_youtube.set_state({"channels": {
        channel_id: {"sleep": 0.35 if index % 5 == 0 else 0.05, "entries": []}
        for index, channel_id in enumerate(channel_ids)
    }, "videos": {}})
    started = time.monotonic()
    monitor.channel_monitor(background=True)
    dispatch_seconds = time.monotonic() - started
    assert dispatch_seconds < 0.25
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if len([item for item in fake_youtube.calls() if "--flat-playlist" in item["argv"]]) < 20:
            monitor.channel_monitor(background=True)
        with monitor._scan_lock:
            if not monitor._scan_inflight and len(fake_youtube.calls()) >= 20:
                break
        time.sleep(0.05)
    with monitor._scan_lock:
        assert not monitor._scan_inflight
    calls = fake_youtube.calls()
    scanned = [item for item in calls if "--flat-playlist" in item["argv"]]
    assert len(scanned) == 20
    total_seconds = time.monotonic() - started
    assert total_seconds < 5
    with SessionLocal() as db:
        deadlines = db.scalars(select(Channel.next_check_at).where(
            Channel.youtube_channel_id.in_(channel_ids))).all()
    assert len(deadlines) == 20
    assert all(deadline > now() for deadline in deadlines)
    with SessionLocal() as db:
        failed_deadlines = db.scalars(select(Channel.next_check_at).where(
            Channel.youtube_channel_id.in_(channel_ids), Channel.last_error.is_not(None))).all()
    assert failed_deadlines
    assert all(deadline <= now() + timedelta(seconds=70) for deadline in failed_deadlines)
    print(f"20 channels: dispatch={dispatch_seconds:.3f}s total={total_seconds:.3f}s, concurrency=3")


def set_due_by_youtube_id(channel_id):
    with SessionLocal() as db:
        channel = db.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id))
        channel.next_check_at = now() - timedelta(seconds=1)
        db.commit()



def test_multiple_schedules_use_shortest_interval():
    now_at = now()
    long = now_at + timedelta(hours=8)
    soon = now_at + timedelta(minutes=4)
    assert monitor.calculate_next_interval("AUTO", [long, soon], now_at) == (20, 40)


def test_activity_profile_selects_only_release_timestamp_column():
    from sqlalchemy import event
    from app.services.monitor import _activity_profile

    channel_id = add_channel("UCabcdefghijabcdefghij34")
    statements = []
    with SessionLocal() as db:
        event.listen(db.bind, "before_cursor_execute", lambda _conn, _cursor, stmt, _params, _ctx, _many: statements.append(stmt))
        _activity_profile(db, channel_id, now())
    query = next(stmt for stmt in statements if "FROM videos" in stmt and "to_timestamp" in stmt)
    selected = query.split("FROM videos")[0].lower()
    assert "->>" in selected
    assert "select videos.raw_metadata" not in selected


def test_learned_activity_sets_short_hot_and_long_cold_checks():
    from app.services.monitor import _activity_profile, _schedule_next

    channel_id = add_channel("UCabcdefghijabcdefghij33")
    with SessionLocal() as db:
        channel = db.get(Channel, channel_id)
        current = now()
        for index in range(8):
            started = (current - timedelta(days=index + 1)).replace(hour=21, minute=10, second=0, microsecond=0)
            db.add(Video(youtube_video_id=f"profile{index:05d}", channel_id=channel_id, title="history",
                         status="ACTIVE", raw_metadata={"release_timestamp": started.timestamp()}))
        db.add(Video(youtube_video_id="future000001", channel_id=channel_id, title="future", status="WAITING_LIVE",
                     scheduled_start_at=current + timedelta(days=2),
                     raw_metadata={"release_timestamp": (current + timedelta(days=2)).timestamp()}))
        db.add(Video(youtube_video_id="cancelled01", channel_id=channel_id, title="cancelled", status="CANCELLED",
                     raw_metadata={"release_timestamp": current.timestamp()}))
        db.add(Video(youtube_video_id="oldstream01", channel_id=channel_id, title="old", status="ACTIVE",
                     raw_metadata={"release_timestamp": (current - timedelta(days=120)).timestamp()}))
        db.commit()
        assert _activity_profile(db, channel_id, current).samples == 8
        assert _activity_profile(db, channel_id, current).mode == "insufficient"
        for index in range(8, 12):
            started = (current - timedelta(days=index + 1)).replace(hour=21, minute=10, second=0, microsecond=0)
            db.add(Video(youtube_video_id=f"profile{index:05d}", channel_id=channel_id, title="history",
                         status="ACTIVE", raw_metadata={"release_timestamp": started.timestamp()}))
        db.flush()
        hot = current.replace(hour=21, minute=0, second=0, microsecond=0)
        _schedule_next(channel, db, hot, [])
        hot_delay = (channel.next_check_at - hot).total_seconds()
        assert 900 <= hot_delay <= 1800
        cold = current.replace(hour=9, minute=0, second=0, microsecond=0)
        _schedule_next(channel, db, cold, [])
        cold_delay = (channel.next_check_at - cold).total_seconds()
        assert 7200 <= cold_delay <= 14400


def test_backfill_zero_skips_all_past_lives(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij13"
    add_channel(channel_id, backfill_limit=0)
    fake_youtube.set_state(channel_state(channel_id, entries(("past-a", "was_live"), ("past-b", "was_live"))))
    channel_monitor()
    assert [video.status for video in read_videos()] == ["SKIPPED", "SKIPPED"]
    assert all(video.raw_metadata["backfill_excluded"] for video in read_videos())


def test_upcoming_inspection_sets_schedule_and_thumbnail(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij14"
    add_channel(channel_id)
    start = int((now() + timedelta(hours=2)).timestamp())
    fake_youtube.set_state({"channels": {channel_id: {"entries": entries(("soon", "is_upcoming"))}},
                            "videos": {"soon": {"live_status": "is_upcoming", "release_timestamp": start}}})
    channel_monitor()
    video = read_videos()[0]
    assert video.status == "WAITING_LIVE"
    assert int(video.scheduled_start_at.timestamp()) == start
    assert video.thumbnail_url == "https://img.test/soon.jpg"
    assert len(fake_youtube.calls()) == 2
    assert "--ignore-no-formats-error" in fake_youtube.calls()[-1]["argv"]


@pytest.mark.parametrize(("limit", "statuses"), [(0, ["SKIPPED", "SKIPPED"]), (2, ["DISCOVERED", "DISCOVERED"])])
def test_baseline_backfill_limit(limit, statuses, fake_youtube):
    channel_id = f"UCabcdefghijabcdefghij{20 + limit}"
    add_channel(channel_id, backfill_limit=limit)
    fake_youtube.set_state(channel_state(channel_id, entries(("newer", "was_live"), ("older", "was_live"))))
    channel_monitor()
    assert [video.status for video in read_videos()] == statuses


def test_post_baseline_missed_live_timestamp_and_inspection_failure(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij15"
    pk = add_channel(channel_id)
    fake_youtube.set_state(channel_state(channel_id, []))
    channel_monitor()
    set_due(pk)
    post_time = int((now() + timedelta(minutes=1)).timestamp())
    fake_youtube.set_state({"channels": {channel_id: {"entries": entries(("missed", "was_live"), ("unknown", "was_live"))}},
                            "videos": {"missed": {"live_status": "was_live", "release_timestamp": post_time},
                                       "unknown": {"error": "network_error"}}})
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert rows["missed"].status == "DISCOVERED"
    assert rows["missed"].raw_metadata["missed_live"] is True
    assert "unknown" not in rows


def test_post_baseline_old_live_is_skipped_and_new_live_is_discovered(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij25"
    pk = add_channel(channel_id)
    with SessionLocal() as db:
        channel = db.get(Channel, pk)
        channel.created_at = now() - timedelta(days=1)
        db.commit()
    fake_youtube.set_state(channel_state(channel_id, []))
    channel_monitor()
    set_due(pk)
    before = int((now() - timedelta(days=2)).timestamp())
    after = int((now() - timedelta(hours=2)).timestamp())
    fake_youtube.set_state({"channels": {channel_id: {"entries": entries(("before-register", "was_live"), ("after-register", "was_live"))}},
                            "videos": {"before-register": {"live_status": "was_live", "release_timestamp": before},
                                       "after-register": {"live_status": "was_live", "release_timestamp": after}}})
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert rows["before-register"].status == "SKIPPED"
    assert rows["after-register"].status == "DISCOVERED"
    assert rows["after-register"].raw_metadata["missed_live"] is True


def test_new_live_is_discovered(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij26"
    add_channel(channel_id)
    fake_youtube.set_state(channel_state(channel_id, entries(("live-now", "is_live"))))
    channel_monitor()
    assert read_videos()[0].status == "DISCOVERED"


def test_waiting_live_promotes_on_live_and_disabled_schedule_skips(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij16"
    pk = add_channel(channel_id)
    with SessionLocal() as db:
        db.add_all([
            Video(youtube_video_id="live-now", channel_id=pk, title="", status="WAITING_LIVE", raw_metadata={"schedule_enabled": True}),
            Video(youtube_video_id="disabled", channel_id=pk, title="", status="WAITING_LIVE", raw_metadata={"schedule_enabled": False}),
        ])
        db.commit()
    fake_youtube.set_state(channel_state(channel_id, entries(("live-now", "is_live"), ("disabled", "was_live"))))
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert rows["live-now"].status == "DISCOVERED"
    assert rows["disabled"].status == "SKIPPED"
    assert rows["disabled"].raw_metadata["skip_reason"] == "user_disabled"


def test_failed_scan_backoff_recovery_and_no_repeat_tick(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij17"
    pk = add_channel(channel_id)
    fake_youtube.set_state(channel_state(channel_id, [], error="network_error"))
    channel_monitor()
    first_count = len(fake_youtube.calls())
    failed = read_channel(pk)
    assert failed.consecutive_failures == 1
    assert failed.last_error.startswith("UNKNOWN:")
    assert failed.next_check_at > now()
    channel_monitor()
    assert len(fake_youtube.calls()) == first_count
    set_due(pk)
    fake_youtube.set_state(channel_state(channel_id, []))
    channel_monitor()
    recovered = read_channel(pk)
    assert recovered.consecutive_failures == 0
    assert recovered.last_error is None
    with SessionLocal() as db:
        actions = db.scalars(select(AuditLog.action).where(AuditLog.channel_id == pk)).all()
    assert actions == ["MONITOR_ERROR", "MONITOR_RECOVERED"]


def test_no_streams_tab_is_success(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij18"
    pk = add_channel(channel_id)
    fake_youtube.set_state(channel_state(channel_id, [], error="no_streams_tab"))
    channel_monitor()
    channel = read_channel(pk)
    assert channel.last_success_at is not None
    assert channel.last_error is None
    assert channel.consecutive_failures == 0
    assert channel.next_check_at > now()


def test_rate_limit_cooldown_skips_queued_channels(fake_youtube, monkeypatch):
    first = "UCabcdefghijabcdefghij31"
    second = "UCabcdefghijabcdefghij32"
    add_channel(first)
    add_channel(second)
    monkeypatch.setattr(settings, "monitor_concurrency", 1)
    fake_youtube.set_state({"channels": {first: {"error": "rate_limited"}, second: {"entries": []}}, "videos": {}})
    channel_monitor()
    assert len(fake_youtube.calls()) == 1
    before = len(fake_youtube.calls())
    channel_monitor()
    assert len(fake_youtube.calls()) == before
    monitor._clear_rate_limit()


def test_disappeared_upcoming_cancelled_and_uncertain_left_waiting(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij19"
    pk = add_channel(channel_id)
    inspected_at = (now() - timedelta(minutes=11)).isoformat()
    with SessionLocal() as db:
        db.add_all([
            Video(youtube_video_id="cancel-me", channel_id=pk, title="", status="WAITING_LIVE",
                  scheduled_start_at=now() - timedelta(minutes=2), raw_metadata={"schedule_inspected_at": inspected_at}),
            Video(youtube_video_id="uncertain", channel_id=pk, title="", status="WAITING_LIVE",
                  scheduled_start_at=now() - timedelta(minutes=2), raw_metadata={"schedule_inspected_at": inspected_at}),
        ])
        db.commit()
    fake_youtube.set_state({"channels": {channel_id: {"entries": []}},
                            "videos": {"cancel-me": {"error": "deleted"}, "uncertain": {"error": "network_error"}}})
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert rows["cancel-me"].status == "CANCELLED"
    assert rows["cancel-me"].raw_metadata["cancel_reason"] == "DELETED"
    assert rows["uncertain"].status == "WAITING_LIVE"


def test_cancelled_schedule_can_recover_and_waiting_metadata_refreshes(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij27"
    pk = add_channel(channel_id)
    with SessionLocal() as db:
        db.add_all([
            Video(youtube_video_id="revive-live", channel_id=pk, title="old", status="CANCELLED",
                  raw_metadata={"cancel_reason": "UNAVAILABLE"}),
            Video(youtube_video_id="rename-live", channel_id=pk, title="old title", thumbnail_url="old.jpg",
                  status="WAITING_LIVE", raw_metadata={"schedule_enabled": True}),
        ])
        db.commit()
    fake_youtube.set_state({"channels": {channel_id: {"entries": [
        {"id": "revive-live", "title": "revived", "live_status": "is_live"},
        {"id": "rename-live", "title": "new title", "live_status": "is_upcoming",
         "thumbnails": [{"url": "new.jpg"}]},
    ]}}, "videos": {"rename-live": {"live_status": "is_upcoming",
                                      "release_timestamp": int((now() + timedelta(hours=1)).timestamp())}}})
    channel_monitor()
    rows = {video.youtube_video_id: video for video in read_videos()}
    assert rows["revive-live"].status == "DISCOVERED"
    assert rows["rename-live"].title == "new title"
    assert rows["rename-live"].thumbnail_url == "new.jpg"


def test_disappeared_waiting_video_with_not_live_status_is_cancelled(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij28"
    pk = add_channel(channel_id)
    with SessionLocal() as db:
        db.add(Video(youtube_video_id="not-live", channel_id=pk, title="", status="WAITING_LIVE",
                     scheduled_start_at=now() - timedelta(minutes=1),
                     raw_metadata={"schedule_inspected_at": (now() - timedelta(minutes=11)).isoformat()}))
        db.commit()
    fake_youtube.set_state({"channels": {channel_id: {"entries": []}},
                            "videos": {"not-live": {"live_status": "not_live"}}})
    channel_monitor()
    row = next(video for video in read_videos() if video.youtube_video_id == "not-live")
    assert row.status == "CANCELLED"
    assert row.raw_metadata["cancel_reason"] == "NOT_LIVE"


def test_parallel_scans_start_without_serial_wait(fake_youtube, monkeypatch):
    slow = "UCabcdefghijabcdefghij41"
    fast = "UCabcdefghijabcdefghij42"
    add_channel(slow)
    add_channel(fast)
    monkeypatch.setattr(settings, "monitor_concurrency", 2)
    fake_youtube.set_state({"channels": {slow: {"entries": [], "sleep": 0.8}, fast: {"entries": []}}, "videos": {}})
    channel_monitor()
    started = sorted(call["started_at"] for call in fake_youtube.calls())
    assert len(started) == 2
    assert started[1] - started[0] < 0.5


def test_recovery_upcoming_returns_video_to_waiting_live(fake_youtube):
    channel_id = "UCabcdefghijabcdefghij43"
    pk = add_channel(channel_id)
    with SessionLocal() as db:
        video = Video(youtube_video_id="recover-upcoming", channel_id=pk, title="", status="RECORDING_FAILED",
                      raw_metadata={"live_status": "was_live"})
        db.add(video)
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"recover-upcoming": {"live_status": "is_upcoming"}}})
    recovery_worker()
    row = read_videos()[0]
    assert row.status == "WAITING_LIVE"
    assert row.raw_metadata["live_status"] == "is_upcoming"
    assert row.next_recovery_at is None


def test_monitor_scan_wait_uses_no_idle_transaction(monkeypatch):
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    channel_id = "UCabcdefghijabcdefghij51"
    channel_pk = add_channel(channel_id)
    engine = create_engine(os.environ["DATABASE_URL"],
                           connect_args={"options": "-c idle_in_transaction_session_timeout=1000"})
    short_sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(monitor, "SessionLocal", short_sessions)

    def slow_scan(*args, **kwargs):
        time.sleep(1.3)
        return [], {"channel": "slow"}

    monkeypatch.setattr(monitor, "channel_scan", slow_scan)
    monitor._scan_one(channel_pk)
    with SessionLocal() as db:
        assert db.get(Channel, channel_pk).last_success_at is not None
    engine.dispose()


def test_recovery_inspection_wait_uses_no_idle_transaction(monkeypatch, tmp_path):
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import workers

    channel_id = "UCabcdefghijabcdefghij52"
    channel_pk = add_channel(channel_id)
    with SessionLocal() as db:
        video = Video(youtube_video_id="recover-slow", channel_id=channel_pk, title="slow",
                      status="RECORDING_FAILED", raw_metadata={"live_status": "is_live"})
        db.add(video); db.commit(); video_pk = video.id
    engine = create_engine(os.environ["DATABASE_URL"],
                           connect_args={"options": "-c idle_in_transaction_session_timeout=1000"})
    short_sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(workers, "SessionLocal", short_sessions)

    def slow_inspection(video):
        time.sleep(1.3)
        return "PUBLIC", None, None, {"live_status": "is_live"}

    monkeypatch.setattr(workers, "_inspection", slow_inspection)
    recovery_worker()
    with SessionLocal() as db:
        recovered = db.get(Video, video_pk)
        assert recovered.status == "DISCOVERED"
        assert recovered.recovery_attempts == 1
    engine.dispose()
