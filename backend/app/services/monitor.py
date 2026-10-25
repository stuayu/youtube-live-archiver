"""Channel polling, live discovery, and monitor health accounting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import logging
import random
import threading

from sqlalchemy import select, func, cast, Float
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import AuditLog, Channel, Video, now
from .channel_stats import record_snapshot
from .activity_profile import Profile, build_profile
from .locks import channel_lock, default_locks
from .youtube import channel_scan, classify_ytdlp_error, inspect

log = logging.getLogger(__name__)
_cooldown_lock = threading.Lock()
_cooldown_until: datetime | None = None
_rate_limit_streak = 0
_rate_limit_context = ("unknown", "unknown")
_scan_pool = ThreadPoolExecutor(max_workers=max(1, settings.monitor_concurrency), thread_name_prefix="channel-scan")
_scan_lock = threading.Lock()
_scan_inflight: set[int] = set()


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


def calculate_next_interval(poll_interval: str | None, scheduled_times: list[datetime | None], current: datetime,
                            profile: Profile | None = None) -> tuple[float, float]:
    """Return lower and upper polling delay bounds in seconds."""
    raw_poll = (poll_interval or "AUTO").strip().upper()
    if raw_poll == "AUTO":
        profile = profile or build_profile([])
        normal_low, normal_high = {"hot": (900.0, 1800.0), "warm": (1800.0, 3600.0),
                                   "cold": (7200.0, 14400.0), "normal": (1800.0, 3600.0)}[profile.level_at(current)]
    else:
        try:
            minutes = int(raw_poll)
            if minutes <= 0:
                raise ValueError
        except (TypeError, ValueError):
            minutes = 30
        normal_low = normal_high = float(minutes * 60)
    normal_jitter = (normal_low, normal_high) if normal_low != normal_high else (normal_low * .9, normal_high * 1.1)
    interval = normal_jitter
    if raw_poll == "AUTO" and profile is not None and profile.level_at(current) != "hot":
        next_hot = profile.next_hot_start(current)
        if next_hot:
            cap = max(60.0, (next_hot - current).total_seconds() + 600.0)
            interval = (min(interval[0], cap), min(interval[1], cap))
    eligible = scheduled_times
    if eligible:
        candidates = [value for value in eligible if value is not None]
        if not candidates:
            scheduled_interval = (600.0, 1200.0)
            interval = (min(interval[0], scheduled_interval[0]), min(interval[1], scheduled_interval[1]))
        else:
            for scheduled in candidates:
                delta = (scheduled - current).total_seconds()
                if delta > 1800:
                    cap = max(60.0, delta - 1800.0)
                    scheduled_interval = (min(interval[0], cap), min(interval[1], cap))
                elif delta > 300:
                    scheduled_interval = (120.0, 240.0)
                elif delta > -600:
                    scheduled_interval = (20.0, 40.0)
                elif delta > -3600:
                    scheduled_interval = (60.0, 120.0)
                elif delta > -21600:
                    scheduled_interval = (300.0, 600.0)
                else:
                    scheduled_interval = interval
                interval = (min(interval[0], scheduled_interval[0]), min(interval[1], scheduled_interval[1]))
            if any(value is None for value in eligible):
                interval = (min(interval[0], 600.0), min(interval[1], 1200.0))
    return interval


def _activity_profile(db: Session, channel_id: int, at: datetime) -> Profile:
    cutoff = at - timedelta(days=90)
    release_value = cast(Video.raw_metadata, JSONB)["release_timestamp"].as_string()
    release_time = func.to_timestamp(cast(release_value, Float))
    rows = db.execute(select(release_time).where(Video.channel_id == channel_id,
        Video.status != "CANCELLED", release_value.is_not(None), release_time >= cutoff,
        release_time <= at)).all()
    starts = [row[0].replace(tzinfo=timezone.utc) if row[0].tzinfo is None
              else row[0].astimezone(timezone.utc) for row in rows if row[0] is not None]
    profile = build_profile(starts)
    if len(starts) < 12:
        return Profile(len(starts), "insufficient", ("normal",) * 168)
    return profile


def _schedule_next(ch: Channel, db: Session, at: datetime, entries: list[dict]) -> None:
    rows = db.scalars(select(Video).where(Video.channel_id == ch.id, Video.status == "WAITING_LIVE")).all()
    schedules = []
    for row in rows:
        metadata = row.raw_metadata or {}
        if metadata.get("schedule_enabled") is False:
            continue
        schedules.append(row.scheduled_start_at)
    profile = _activity_profile(db, ch.id, at) if (ch.poll_interval or "AUTO").upper() == "AUTO" else None
    low, high = calculate_next_interval(ch.poll_interval, schedules, at, profile)
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

    auto_poll = (ch.poll_interval or "AUTO").upper() == "AUTO"

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

    # Flat channel lists omit start times, so gradually inspect older recordings.
    if auto_poll and inspect_used < inspect_budget and not _cooldown_active(at):
        profile = _activity_profile(db, ch.id, at)
        if profile.samples < 12:
            probe = db.scalar(select(Video).where(
                Video.channel_id == ch.id, Video.status != "CANCELLED",
                Video.scheduled_start_at.is_(None), Video.recorded_at.is_(None),
                Video.raw_metadata["release_timestamp"].as_string().is_(None),
                Video.raw_metadata["start_probe_attempted"].as_string().is_(None),
            ).order_by(Video.id.desc()).limit(1))
            if probe:
                data, kind = inspect_once(probe.youtube_video_id)
                if kind == "RATE_LIMITED":
                    _apply_rate_limit(at, ch.youtube_channel_id, probe.youtube_video_id)
                elif data is not None:
                    release = _timestamp(data.get("release_timestamp"))
                    metadata = {**(probe.raw_metadata or {}), "start_probe_attempted": True}
                    if release is not None:
                        metadata["release_timestamp"] = data.get("release_timestamp")
                        if data.get("duration") is not None:
                            probe.original_duration = data.get("duration")
                    probe.raw_metadata = metadata
                else:
                    probe.raw_metadata = {**(probe.raw_metadata or {}), "start_probe_attempted": True}

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


def _apply_rate_limit(at: datetime, channel_id: str = "unknown", video_id: str = "unknown") -> None:
    global _cooldown_until, _rate_limit_streak, _rate_limit_context
    with _cooldown_lock:
        was_active = bool(_cooldown_until and _cooldown_until > at)
        _rate_limit_streak += 1
        minutes = min(60, 10 * 2 ** (_rate_limit_streak - 1))
        _cooldown_until = at + timedelta(minutes=minutes)
        if not was_active:
            _rate_limit_context = (channel_id, video_id)
    if not was_active:
        log.warning("YouTube rate limit entered for %s minutes channel_id=%s youtube_video_id=%s",
                    minutes, channel_id, video_id)


def _clear_rate_limit() -> None:
    global _cooldown_until, _rate_limit_streak
    with _cooldown_lock:
        if _cooldown_until and _cooldown_until > now():
            return
        was_active = _cooldown_until is not None
        channel_id, video_id = _rate_limit_context
        _cooldown_until = None
        _rate_limit_streak = 0
    if was_active:
        log.info("YouTube rate limit cooldown cleared channel_id=%s youtube_video_id=%s",
                 channel_id, video_id)


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
                    profile = _activity_profile(db, ch.id, at) if (ch.poll_interval or "AUTO").upper() == "AUTO" else None
                    low, high = calculate_next_interval(ch.poll_interval, [], at, profile)
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
                    _apply_rate_limit(at, ch.youtube_channel_id, "unknown")
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


def channel_monitor(background: bool = False) -> None:
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
    if not background:
        concurrency = max(1, settings.monitor_concurrency)
        for offset in range(0, len(channel_ids), concurrency):
            group = channel_ids[offset:offset + concurrency]
            futures = []
            for channel_id in group:
                with _scan_lock:
                    if channel_id in _scan_inflight:
                        continue
                    _scan_inflight.add(channel_id)
                futures.append((channel_id, _scan_pool.submit(_scan_one, channel_id)))
            for channel_id, future in futures:
                try:
                    future.result()
                except Exception as exc:
                    log.warning("channel monitor worker failed: %s", exc)
                finally:
                    with _scan_lock:
                        _scan_inflight.discard(channel_id)
            if _cooldown_active(now()):
                break
        return
    with _scan_lock:
        available = max(0, max(1, settings.monitor_concurrency) - len(_scan_inflight))
    futures = []
    for channel_id in channel_ids:
        if not available:
            break
        with _scan_lock:
            if channel_id in _scan_inflight:
                continue
            _scan_inflight.add(channel_id)
            available -= 1
        future = _scan_pool.submit(_scan_one, channel_id)
        future.add_done_callback(lambda completed, pk=channel_id: _finish_scan(pk, completed))
        futures.append(future)


def _finish_scan(channel_id: int, future) -> None:
    try:
        future.result()
    except Exception as exc:
        log.warning("channel monitor worker failed: %s", exc)
    finally:
        with _scan_lock:
            _scan_inflight.discard(channel_id)
