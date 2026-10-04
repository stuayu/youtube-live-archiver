"""Channel polling, live discovery, and monitor health accounting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
import random
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import AuditLog, Channel, Video, now
from .channel_stats import record_snapshot
from .locks import channel_lock, default_locks
from .youtube import channel_scan, classify_ytdlp_error, inspect

log = logging.getLogger(__name__)
_cooldown_lock = threading.Lock()
_cooldown_until: datetime | None = None
_rate_limit_streak = 0


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError, OSError):
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _thumbnail(entry: dict) -> str:
    thumbs = entry.get("thumbnails") or []
    if thumbs and isinstance(thumbs[-1], dict) and thumbs[-1].get("url"):
        return thumbs[-1]["url"]
    return f"https://i.ytimg.com/vi/{entry['id']}/hqdefault.jpg"


def _inspect(video_id: str, cookie_profile: str | None) -> tuple[dict | None, str]:
    try:
        data = inspect(f"https://www.youtube.com/watch?v={video_id}", cookie_profile)
        availability = (data.get("availability") or "").lower()
        mapped = {"private": "PRIVATE", "subscriber_only": "MEMBERS_ONLY", "needs_subscription": "MEMBERS_ONLY",
                  "premium_only": "MEMBERS_ONLY", "unavailable": "UNAVAILABLE", "deleted": "DELETED",
                  "removed": "DELETED"}.get(availability)
        return data, mapped or "PUBLIC"
    except Exception as exc:
        return None, classify_ytdlp_error(str(exc))


def _inspect_datetime(data: dict | None) -> datetime | None:
    if not data:
        return None
    return _timestamp(data.get("release_timestamp") or data.get("timestamp"))


def _scheduled_entry(entry: dict) -> datetime | None:
    return _timestamp(entry.get("release_timestamp") or entry.get("scheduled_start_time") or entry.get("timestamp"))


def calculate_next_interval(poll_interval: str | None, scheduled_times: list[datetime | None], current: datetime) -> tuple[float, float]:
    """Return jitter bounds in seconds, using the nearest eligible schedule."""
    raw_poll = (poll_interval or "AUTO").strip().upper()
    if raw_poll == "AUTO":
        normal_low, normal_high = 1800.0, 3600.0
        numeric_poll = False
    else:
        try:
            minutes = int(raw_poll)
            if minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            minutes = 30
        normal_low = normal_high = float(minutes * 60)
        numeric_poll = True
    normal_jitter = (normal_low, normal_high) if normal_low != normal_high else (normal_low * .9, normal_high * 1.1)
    if not scheduled_times:
        return normal_jitter
    candidates = [value for value in scheduled_times if value is not None]
    if not candidates:
        interval = (300.0, 600.0)
        if numeric_poll:
            interval = (min(interval[0], normal_low), min(interval[1], normal_low))
        return interval
    intervals = []
    for scheduled in candidates:
        delta = (scheduled - current).total_seconds()
        if -1800 <= delta <= 300:
            intervals.append((15.0, 30.0))
        elif 300 < delta <= 1800:
            intervals.append((60.0, 180.0))
        elif 1800 < delta <= 86400 or -21600 <= delta < -1800:
            intervals.append((300.0, 600.0))
        else:
            intervals.append((300.0, 600.0) if len(candidates) < len(scheduled_times) else normal_jitter)
    interval = min(intervals, key=lambda bounds: bounds[0])
    if numeric_poll:
        cap = normal_low
        interval = (min(interval[0], cap), min(interval[1], cap))
    return interval


def _schedule_next(ch: Channel, db: Session, at: datetime, entries: list[dict]) -> None:
    rows = db.scalars(select(Video).where(Video.channel_id == ch.id, Video.status == "WAITING_LIVE")).all()
    schedules = []
    for row in rows:
        metadata = row.raw_metadata or {}
        if metadata.get("schedule_enabled") is False:
            continue
        schedules.append(row.scheduled_start_at)
    low, high = calculate_next_interval(ch.poll_interval, schedules, at)
    ch.next_check_at = at + timedelta(seconds=random.uniform(low, high))


def _set_discovered(video: Video, live_status: str) -> None:
    video.status = "DISCOVERED"
    video.raw_metadata = {**(video.raw_metadata or {}), "live_status": live_status}


def _handle_scan(db: Session, ch: Channel, entries: list[dict], channel_data: dict, at: datetime) -> None:
    baseline = ch.baseline_completed_at is None
    present: set[str] = set()
    inspect_budget = max(0, settings.monitor_max_inspects_per_scan)
    inspect_used = 0
    upcoming = []
    for entry in entries:
        if not entry.get("id") or entry.get("live_status") != "is_upcoming":
            continue
        existing = db.scalar(select(Video).where(Video.youtube_video_id == str(entry["id"])))
        if existing is None:
            upcoming.append(entry)
            continue
        metadata = existing.raw_metadata or {}
        inspected_at = _timestamp(metadata.get("schedule_inspected_at"))
        if existing.status == "WAITING_LIVE" and (existing.scheduled_start_at is None or inspected_at is None
                                                    or at - inspected_at >= timedelta(minutes=30)):
            upcoming.append(entry)
    def inspection_priority(entry: dict) -> datetime:
        known = _scheduled_entry(entry)
        if known is not None:
            return known
        existing = db.scalar(select(Video).where(Video.youtube_video_id == str(entry["id"])))
        return existing.scheduled_start_at if existing and existing.scheduled_start_at else datetime.max.replace(tzinfo=timezone.utc)

    upcoming.sort(key=inspection_priority)
    inspect_results: dict[str, tuple[dict | None, str]] = {}

    def inspect_once(video_id: str) -> tuple[dict | None, str]:
        nonlocal inspect_used
        if video_id in inspect_results:
            return inspect_results[video_id]
        if inspect_used >= inspect_budget:
            return None, "INSPECT_LIMIT"
        inspect_used += 1
        db.commit()
        result = _inspect(video_id, ch.cookie_profile)
        inspect_results[video_id] = result
        return result

    # Inspect nearest scheduled entries first; list entries intentionally lack schedule timestamps.
    for entry in upcoming:
        if inspect_used >= inspect_budget:
            break
        inspect_once(str(entry["id"]))

    for entry in entries:
        video_id = str(entry.get("id") or "")
        if not video_id:
            continue
        present.add(video_id)
        state = entry.get("live_status")
        video = db.scalar(select(Video).where(Video.youtube_video_id == video_id))
        if video:
            if video.status == "CANCELLED" and state in ("is_upcoming", "is_live"):
                video.status = "WAITING_LIVE" if state == "is_upcoming" else "DISCOVERED"
                video.raw_metadata = {**(video.raw_metadata or {}), "live_status": state}
                video.raw_metadata.pop("cancel_reason", None)
            if video.status != "WAITING_LIVE":
                continue
            video.title = entry.get("title") or video.title
            thumbs = entry.get("thumbnails") or []
            thumb = thumbs[-1].get("url") if thumbs and isinstance(thumbs[-1], dict) else None
            if thumb:
                video.thumbnail_url = thumb
            if state in ("is_live", "was_live"):
                metadata = video.raw_metadata or {}
                if metadata.get("schedule_enabled", True):
                    _set_discovered(video, state)
                elif state == "was_live":
                    video.status = "SKIPPED"
                    video.raw_metadata = {**metadata, "live_status": state, "skip_reason": "user_disabled"}
                continue
            if state == "is_upcoming":
                scheduled = video.scheduled_start_at
                previous_inspected = (video.raw_metadata or {}).get("schedule_inspected_at")
                last_inspected = _timestamp(previous_inspected)
                if scheduled is None or last_inspected is None or at - last_inspected >= timedelta(minutes=30):
                    data, _ = inspect_once(video_id)
                    if data:
                        video.scheduled_start_at = _inspect_datetime(data) or scheduled
                        video.raw_metadata = {**(video.raw_metadata or {}), "schedule_inspected_at": at.isoformat()}
                continue
            continue

        if state not in ("is_live", "is_upcoming", "was_live"):
            continue
        raw = {"live_status": state}
        scheduled = _scheduled_entry(entry)
        status = "DISCOVERED"
        if state == "is_upcoming":
            status = "WAITING_LIVE"
            data, _ = inspect_once(video_id)
            if data:
                scheduled = _inspect_datetime(data) or scheduled
                raw["schedule_inspected_at"] = at.isoformat()
        elif state == "was_live":
            if baseline:
                count = sum(1 for item in entries[:entries.index(entry) + 1] if item.get("live_status") == "was_live")
                allowed = max(0, ch.backfill_limit)
                status = "DISCOVERED" if count <= allowed else "SKIPPED"
                raw["backfill_excluded"] = status == "SKIPPED"
            else:
                data, error_kind = inspect_once(video_id)
                if not data:
                    continue
                release_time = _inspect_datetime(data)
                if release_time is None or release_time < ch.created_at:
                    status = "SKIPPED"
                else:
                    raw["missed_live"] = True
                    raw["live_status"] = "was_live"
        thumbs = entry.get("thumbnails") or []
        thumb = thumbs[-1].get("url") if thumbs and isinstance(thumbs[-1], dict) else None
        db.add(Video(youtube_video_id=video_id, channel_id=ch.id, title=entry.get("title") or "",
                     thumbnail_url=thumb or _thumbnail({**entry, "id": video_id}), scheduled_start_at=scheduled,
                     status=status, retention_days=ch.retention_days, raw_metadata={**raw, "live_status": state}))

    # Reconcile disappeared schedules after processing entries, sharing the inspect cap.
    db.flush()
    waiting = db.scalars(select(Video).where(Video.channel_id == ch.id, Video.status == "WAITING_LIVE")).all()
    candidates = []
    for video in waiting:
        if video.youtube_video_id in present:
            continue
        metadata = video.raw_metadata or {}
        inspected_at = _timestamp(metadata.get("schedule_inspected_at"))
        old_enough = (video.scheduled_start_at is None and at - video.created_at >= timedelta(hours=1)) or \
                     (video.scheduled_start_at is not None and video.scheduled_start_at <= at)
        if old_enough and (inspected_at is None or at - inspected_at >= timedelta(minutes=10)):
            candidates.append(video)
    for video in candidates:
        data, kind = inspect_once(video.youtube_video_id)
        if kind in ("PRIVATE", "DELETED", "UNAVAILABLE", "MEMBERS_ONLY"):
            video.status = "CANCELLED"
            video.raw_metadata = {**(video.raw_metadata or {}), "cancel_reason": kind}
            db.add(AuditLog(channel_id=ch.id, video_id=video.id, action="UPCOMING_CANCELLED", reason=kind))
            continue
        if data:
            live_status = data.get("live_status")
            video.raw_metadata = {**(video.raw_metadata or {}), "schedule_inspected_at": at.isoformat(), "live_status": live_status}
            if live_status in ("is_live", "was_live", "post_live"):
                _set_discovered(video, live_status)
            elif live_status == "is_upcoming":
                video.scheduled_start_at = _inspect_datetime(data) or video.scheduled_start_at
            else:
                video.status = "CANCELLED"
                video.raw_metadata = {**(video.raw_metadata or {}), "cancel_reason": "NOT_LIVE"}
                db.add(AuditLog(channel_id=ch.id, video_id=video.id, action="UPCOMING_CANCELLED", reason="NOT_LIVE"))
            continue
        video.raw_metadata = {**(video.raw_metadata or {}), "schedule_inspected_at": at.isoformat()}

    if baseline:
        ch.baseline_completed_at = at
    for attr, keys in (("subscriber_count", ("channel_follower_count", "subscriber_count")),
                       ("video_count", ("channel_video_count", "video_count")),
                       ("view_count", ("channel_view_count", "view_count"))):
        value = next((channel_data.get(key) for key in keys if channel_data.get(key) is not None), None)
        if value is not None:
            setattr(ch, attr, int(value))
    record_snapshot(db, ch, channel_data)
    _schedule_next(ch, db, at, entries)


def _cooldown_active(at: datetime) -> bool:
    with _cooldown_lock:
        return bool(_cooldown_until and _cooldown_until > at)


def _apply_rate_limit(at: datetime) -> None:
    global _cooldown_until, _rate_limit_streak
    with _cooldown_lock:
        _rate_limit_streak += 1
        minutes = min(60, 10 * 2 ** (_rate_limit_streak - 1))
        _cooldown_until = at + timedelta(minutes=minutes)


def _clear_rate_limit() -> None:
    global _cooldown_until, _rate_limit_streak
    with _cooldown_lock:
        if _cooldown_until and _cooldown_until > now():
            return
        _cooldown_until = None
        _rate_limit_streak = 0


def _scan_one(channel_pk: int) -> None:
    at = now()
    if _cooldown_active(at):
        return
    db = SessionLocal()
    try:
        ch = db.get(Channel, channel_pk)
        if ch is None:
            return
        namespace, key = channel_lock(ch.youtube_channel_id)
        with default_locks.acquire(namespace, key) as lease:
            if not lease.acquired:
                return
            try:
                scan_url = f"https://www.youtube.com/channel/{ch.youtube_channel_id}/streams"
                db.commit()
                entries, data = channel_scan(scan_url, ch.cookie_profile, max(10, ch.backfill_limit + 10))
                ch.last_checked_at = at
                ch.last_success_at = at
                recovered = bool(ch.last_error)
                ch.last_error = None
                ch.consecutive_failures = 0
                _handle_scan(db, ch, entries, data, at)
                if not lease.still_owned():
                    db.rollback()
                    return
                if recovered:
                    db.add(AuditLog(channel_id=ch.id, action="MONITOR_RECOVERED", reason="SCAN_SUCCEEDED"))
                db.commit()
                _clear_rate_limit()
            except Exception as exc:
                db.rollback()
                ch = db.get(Channel, channel_pk)
                if ch is None:
                    return
                at = now()
                error_text = str(exc)
                kind = classify_ytdlp_error(error_text)
                if kind == "NO_STREAMS_TAB":
                    recovered = bool(ch.last_error)
                    ch.last_checked_at = at
                    ch.last_success_at = at
                    ch.last_error = None
                    ch.consecutive_failures = 0
                    ch.baseline_completed_at = ch.baseline_completed_at or at
                    low, high = calculate_next_interval(ch.poll_interval, [], at)
                    ch.next_check_at = at + timedelta(seconds=random.uniform(low, high))
                    if recovered:
                        db.add(AuditLog(channel_id=ch.id, action="MONITOR_RECOVERED", reason="SCAN_SUCCEEDED"))
                    db.commit()
                    _clear_rate_limit()
                    return
                prior_kind = (ch.last_error or "").split(":", 1)[0]
                ch.last_checked_at = at
                ch.consecutive_failures = (ch.consecutive_failures or 0) + 1
                excerpt = error_text[-500:]
                ch.last_error = f"{kind}: {excerpt}"
                try:
                    normal = 1800 if (ch.poll_interval or "AUTO").upper() == "AUTO" else max(60, int(ch.poll_interval) * 60)
                except (TypeError, ValueError):
                    normal = 30 * 60
                backoff = max(60, min(normal, 60 * 2 ** min(ch.consecutive_failures - 1, 20)))
                if kind == "RATE_LIMITED":
                    backoff = max(backoff, 15 * 60)
                    _apply_rate_limit(at)
                minimum_backoff = 15 * 60 if kind == "RATE_LIMITED" else 60
                delay = max(minimum_backoff, random.uniform(backoff * .9, backoff * 1.1))
                ch.next_check_at = at + timedelta(seconds=delay)
                if not prior_kind:
                    db.add(AuditLog(channel_id=ch.id, action="MONITOR_ERROR", reason=kind, details={"message": excerpt}))
                elif prior_kind != kind:
                    db.add(AuditLog(channel_id=ch.id, action="MONITOR_ERROR", reason=kind, details={"message": excerpt}))
                db.commit()
                log.warning("channel monitor failed for %s (%s): %s", ch.youtube_channel_id, kind, excerpt)
    finally:
        db.close()


def channel_monitor() -> None:
    at = now()
    if _cooldown_active(at):
        return
    with SessionLocal() as db:
        channel_ids = db.scalars(select(Channel.id).where(
            Channel.enabled.is_(True), Channel.archived.is_(False),
            (Channel.next_check_at.is_(None) | (Channel.next_check_at <= at)))
            .order_by(Channel.next_check_at.asc().nullsfirst(), Channel.id.asc())
            .limit(max(1, settings.monitor_batch_size))).all()
    if not channel_ids:
        return
    with ThreadPoolExecutor(max_workers=max(1, settings.monitor_concurrency)) as pool:
        futures = [pool.submit(_scan_one, channel_id) for channel_id in channel_ids]
        for future in futures:
            try:
                future.result()
            except Exception as exc:
                log.warning("channel monitor worker failed: %s", exc)
