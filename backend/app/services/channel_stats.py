from calendar import monthrange
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from ..config import settings
from ..models import ChannelStat, now


def _bucket(dt: datetime, granularity: str) -> datetime:
    dt = dt.astimezone(timezone.utc)
    if granularity == "day":
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    if granularity == "week":
        day = dt - timedelta(days=dt.weekday())
        return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return datetime(dt.year, dt.month, 1, tzinfo=timezone.utc)


def aggregate_stats(rows: list[dict], granularity: str) -> list[dict]:
    """Bucket observed snapshots; missing buckets remain missing (never interpolate)."""
    buckets: dict[datetime, dict] = {}
    for row in sorted(rows, key=lambda x: x["captured_at"]):
        key = _bucket(row["captured_at"], granularity)
        item = buckets.setdefault(key, {"captured_at": key, "subscriber_count": None,
                                        "subscriber_count_rounded": False, "video_count": None, "view_count": None})
        for field in ("subscriber_count", "video_count", "view_count"):
            if row.get(field) is not None:
                item[field] = row[field]
        item["subscriber_count_rounded"] = row.get("subscriber_count_rounded", False)
    keys = sorted(buckets)
    if not keys:
        return []
    # Emit explicit empty buckets between observations so the graph can break
    # the line where a capture was missed.
    result = []
    cursor = keys[0]
    last = keys[-1]
    while cursor <= last:
        result.append(buckets.get(cursor, {"captured_at": cursor, "subscriber_count": None,
                                           "subscriber_count_rounded": False, "video_count": None, "view_count": None}))
        if granularity == "day":
            cursor += timedelta(days=1)
        elif granularity == "week":
            cursor += timedelta(days=7)
        else:
            year = cursor.year + (1 if cursor.month == 12 else 0)
            month = 1 if cursor.month == 12 else cursor.month + 1
            cursor = datetime(year, month, 1, tzinfo=timezone.utc)
    return result


def period_deltas(rows: list[dict], now: datetime) -> dict:
    """Compare latest observed value with the latest sample at/before 1d/7d/1mo ago."""
    metrics = ("subscriber_count", "video_count", "view_count")
    ordered = sorted(rows, key=lambda x: x["captured_at"])
    current = next((r for r in reversed(ordered) if r["captured_at"] <= now), None)
    if not current:
        return {period: {m: None for m in metrics} for period in ("day", "week", "month")}
    month = now.month - 1 or 12
    year = now.year - (1 if now.month == 1 else 0)
    targets = {"day": now - timedelta(days=1), "week": now - timedelta(days=7),
               "month": now.replace(year=year, month=month, day=min(now.day, monthrange(year, month)[1]))}
    result = {}
    for period, target in targets.items():
        prior = next((r for r in reversed(ordered) if r["captured_at"] <= target), None)
        result[period] = {m: (current.get(m) - prior.get(m) if prior and current.get(m) is not None and prior.get(m) is not None else None)
                          for m in metrics}
    return result


def record_snapshot(db, channel, metadata: dict) -> bool:
    """Record successful metadata at the configured cadence; failed fetches never call this."""
    subscriber = metadata.get("channel_follower_count", metadata.get("subscriber_count"))
    videos = metadata.get("channel_video_count", metadata.get("video_count"))
    views = metadata.get("channel_view_count", metadata.get("view_count"))
    if subscriber is None and videos is None and views is None:
        return False
    latest = db.scalar(select(ChannelStat).where(ChannelStat.channel_id == channel.id)
                       .order_by(ChannelStat.captured_at.desc()).limit(1))
    captured_at = now()
    if latest is not None and captured_at - latest.captured_at < timedelta(hours=max(1, settings.channel_stats_interval_hours)):
        return False
    subscriber = int(subscriber) if subscriber is not None else None
    videos = int(videos) if videos is not None else None
    views = int(views) if views is not None else None
    db.add(ChannelStat(channel_id=channel.id, captured_at=captured_at,
                       subscriber_count=subscriber,
                       subscriber_count_rounded=subscriber is not None,
                       video_count=videos, view_count=views))
    return True
