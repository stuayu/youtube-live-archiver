from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import select, func, or_
from datetime import datetime, timedelta, timezone
from pathlib import Path
import mimetypes
import os
from ..config import settings
from sqlalchemy.orm import Session, object_session
from ..db import get_db
from ..models import Channel, ChannelStat, Storage, Video, AuditLog, ChatMessage, TranscriptSegment, AppSetting, RecordingRule, ProcessingJob, now
from ..schemas import (ChannelIn, ChannelOut, StorageIn, StorageOut, VideoOut, RecordingRuleIn, RecordingRuleOut,
                      ChannelCreate, ChannelPatch, ChannelPreviewIn, TranscriptionSettingsIn, SummarySettingsIn, StoragePatch)
from ..services.youtube import resolve_channel, channel_metadata
from ..services.backfill import parse_byte_range, registered_video_directory, media_file
from ..workers import stop_recording
from ..services.channel_stats import aggregate_stats, period_deltas, record_snapshot
from ..services.quality import PRESETS
from ..services.transcode import enqueue as enqueue_conversion, status as conversion_status, output_path
from ..services.chat import ingest_jsonl
from ..services.recording_rules import matches
router = APIRouter(prefix="/api")


def utc_json_value(value):
    if isinstance(value, datetime):
        normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return normalized.isoformat()
    if isinstance(value, dict):
        return {key: utc_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [utc_json_value(item) for item in value]
    return value


def refresh_channel_metadata(ch: Channel):
    try:
        data = channel_metadata(ch.url, ch.cookie_profile)
        ch.name = data.get("channel") or data.get("uploader") or data.get("title") or ch.name
        # チャンネルの --flat-playlist 出力では thumbnail 単体は返らず、
        # thumbnails 配列にアバターとバナーが混在して入る。
        thumbs = [t for t in (data.get("thumbnails") or []) if isinstance(t, dict) and t.get("url")]
        avatars = [t for t in thumbs if (t.get("id") or "").startswith("avatar")] or \
                  [t for t in thumbs if t.get("width") and t.get("height") and t["width"] == t["height"]]
        banners = [t for t in thumbs if (t.get("id") or "").startswith("banner")]
        best_avatar = max(avatars, key=lambda t: t.get("width") or 0)["url"] if avatars else None
        best_banner = max(banners, key=lambda t: t.get("width") or 0)["url"] if banners else None
        ch.avatar_url = data.get("avatar") or data.get("channel_thumbnail") or data.get("thumbnail") or best_avatar or ch.avatar_url
        ch.banner_url = data.get("channel_banner") or data.get("banner") or best_banner or ch.banner_url
        subscriber = data.get("channel_follower_count")
        if subscriber is None:
            subscriber = data.get("subscriber_count")
        video_count = data.get("channel_video_count") or data.get("video_count")
        view_count = data.get("channel_view_count") or data.get("view_count")
        if subscriber is not None: ch.subscriber_count = int(subscriber)
        if video_count is not None: ch.video_count = int(video_count)
        if view_count is not None: ch.view_count = int(view_count)
        ch.description = data.get("description") or ch.description
        # yt-dlp exposes YouTube's displayed count; retain the exact returned
        # integer and mark subscriber values as potentially rounded.
        db = object_session(ch)
        if db is not None:
            record_snapshot(db, ch, data)
        if ch.avatar_url:
            if db is not None and db.in_transaction():
                db.commit()
            try:
                folder = Path(settings.data_dir).resolve() / "channel-icons"
                folder.mkdir(parents=True, exist_ok=True)
                from ..services.image_cache import urlopen
                with urlopen(ch.avatar_url, timeout=10) as response:
                    payload = response.read(5 * 1024 * 1024 + 1)
                    if len(payload) <= 5 * 1024 * 1024:
                        content_type = response.headers.get_content_type()
                        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(content_type, ".img")
                        target = folder / f"{ch.youtube_channel_id}{suffix}"
                        target.write_bytes(payload)
                        ch.avatar_cache_path = str(target)
            except Exception:
                pass
    except Exception:
        pass
def _patch_channel(ch: Channel, body: ChannelPatch, apply_existing: bool, db: Session):
    values = body.model_dump(exclude_unset=True)
    values.pop("url", None)
    values.pop("youtube_channel_id", None)
    for key in ("storage_id", "fallback_storage_id"):
        if key in values and values[key] is not None and db.get(Storage, values[key]) is None:
            raise HTTPException(422, "保存先がありません")
    if "cookie_profile" in values:
        from ..services.youtube import cookie_args
        try: cookie_args(values["cookie_profile"])
        except ValueError as exc: raise HTTPException(422, str(exc))
    prior_enabled, prior_interval = ch.enabled, ch.poll_interval
    for key, value in values.items(): setattr(ch, key, value)
    if "enabled" in values and values["enabled"] and not prior_enabled:
        ch.next_check_at = None
    if "poll_interval" in values and values["poll_interval"] != prior_interval:
        ch.next_check_at = None
    if apply_existing and "retention_days" in values:
        rows = db.scalars(select(Video).where(Video.channel_id == ch.id)).all()
        rules = db.scalars(select(RecordingRule).where(RecordingRule.enabled.is_(True),
                            or_(RecordingRule.channel_id.is_(None), RecordingRule.channel_id == ch.id))).all()
        overridden = {rule.id for rule in rules}
        for video in rows:
            metadata = video.raw_metadata or {}
            if video.protected or video.is_partial or video.status in ("DELETED_BY_RETENTION", "DELETED_MANUALLY"):
                continue
            if metadata.get("matched_rule_id") in overridden or metadata.get("rule_overrides", {}).get("retention_days") is not None:
                continue
            video.retention_days = ch.retention_days
            video.retention_expires_at = (video.recording_finished_at + timedelta(days=ch.retention_days)
                                          if ch.retention_days is not None and video.recording_finished_at else None)
            if video.status == "DELETE_ELIGIBLE":
                video.status = "ACTIVE"
                video.deletion_candidate_at = None
                video.deletion_after = None
    db.commit()
    db.refresh(ch)
    return ch
def _video_query(status: str | None, channel_id: int | None, q: str | None,
                 from_: datetime | None, to: datetime | None, min_size: int | None,
                 max_size: int | None, storage_id: int | None,
                 protection_reason: str | None, permanent: bool | None,
                 scope: str):
    query = select(Video)
    if scope == "upcoming":
        query = query.where(or_(Video.status.in_(("WAITING_LIVE", "RECORDING")),
                                (Video.status == "DISCOVERED")
                                & (Video.raw_metadata["live_status"].as_string().in_(("is_live", "is_upcoming")))
                                & Video.raw_metadata["manual"].as_boolean().is_not(True)))
    elif scope == "deleted": query = query.where(Video.status.in_(("DELETED_BY_RETENTION", "DELETED_MANUALLY")))
    elif scope == "archive":
        query = query.where(
            or_(Video.file_path.is_not(None),
                (Video.status == "DISCOVERED") & (Video.raw_metadata["manual"].as_string() == "true"))
        )
    if channel_id is not None: query = query.where(Video.channel_id == channel_id)
    if status:
        if status == "PROTECTED": query = query.where(Video.protected.is_(True))
        elif status == "RECORDING_FAILED": query = query.where(Video.status.in_(("RECORDING_FAILED", "WAITING_FOR_STORAGE")))
        else: query = query.where(Video.status == status)
    if q:
        term = f"%{q.strip()}%"
        query = query.where(or_(Video.title.ilike(term), Video.raw_metadata["description"].as_string().ilike(term)))
    if from_ is not None: query = query.where(Video.recorded_at >= from_)
    if to is not None: query = query.where(Video.recorded_at <= to)
    if min_size is not None: query = query.where(Video.file_size >= min_size)
    if max_size is not None: query = query.where(Video.file_size <= max_size)
    if storage_id is not None: query = query.where(Video.storage_id == storage_id)
    if protection_reason: query = query.where(Video.protection_reason == protection_reason)
    if permanent is True: query = query.where(Video.retention_expires_at.is_(None))
    elif permanent is False: query = query.where(Video.retention_expires_at.is_not(None))
    return query
def _has_archive_media(video: Video) -> bool:
    directory = Path(video.file_path) if video.file_path else None
    return bool(directory and directory.is_dir() and media_file(directory))
_VIDEO_STATUSES = {"DISCOVERED", "WAITING_LIVE", "LIVE", "RECORDING", "WAITING_FOR_STORAGE", "RECORDING_FAILED",
                   "ACTIVE", "PROTECTED", "PROTECTION_PENDING", "VERIFYING_REMOTE", "DELETE_ELIGIBLE", "SKIPPED",
                   "CANCELLED", "DELETED_BY_RETENTION", "DELETED_MANUALLY"}
def _parse_date_filter(value):
    if value is None: return None
    if len(value) == 10:
        try: parsed = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        except ValueError: raise HTTPException(422, "期間は ISO 8601 形式で指定してください")
        return parsed
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError: raise HTTPException(422, "期間は ISO 8601 形式で指定してください")
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
def _video_or_404(db, video_id):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    return video

__all__ = [name for name in globals() if not name.startswith("__")]
