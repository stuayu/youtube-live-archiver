from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy import select, func, or_
from datetime import datetime, timedelta, timezone
from pathlib import Path
import mimetypes
import os
from urllib.request import urlopen
from .config import settings
from sqlalchemy.orm import Session, object_session
from .db import get_db
from .models import Channel, ChannelStat, Storage, Video, AuditLog, ChatMessage, TranscriptSegment, AppSetting, RecordingRule, now
from .schemas import ChannelIn, ChannelOut, StorageIn, StorageOut, VideoOut, RecordingRuleIn, RecordingRuleOut
from .services.youtube import resolve_channel, channel_metadata
from .services.backfill import parse_byte_range, registered_video_directory, media_file
from .workers import stop_recording
from .services.channel_stats import aggregate_stats, period_deltas, record_snapshot
from .services.quality import PRESETS
from .services.transcode import enqueue as enqueue_conversion, status as conversion_status, output_path
from .services.chat import ingest_jsonl
from .services.recording_rules import matches

router = APIRouter(prefix="/api")


@router.get("/recording-rules", response_model=list[RecordingRuleOut])
def recording_rules(db: Session = Depends(get_db)):
    return db.scalars(select(RecordingRule).order_by(RecordingRule.priority, RecordingRule.id)).all()


@router.post("/recording-rules", response_model=RecordingRuleOut)
def create_recording_rule(body: RecordingRuleIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    if data["channel_id"] is not None and db.get(Channel, data["channel_id"]) is None:
        raise HTTPException(422, "対象チャンネルがありません")
    if data["storage_id"] is not None and db.get(Storage, data["storage_id"]) is None:
        raise HTTPException(422, "保存先がありません")
    rule = RecordingRule(**data); db.add(rule); db.commit(); db.refresh(rule)
    return rule


@router.put("/recording-rules/{rule_id}", response_model=RecordingRuleOut)
def update_recording_rule(rule_id: int, body: RecordingRuleIn, db: Session = Depends(get_db)):
    rule = db.get(RecordingRule, rule_id)
    if rule is None: raise HTTPException(404, "ルールがありません")
    data = body.model_dump()
    if data["channel_id"] is not None and db.get(Channel, data["channel_id"]) is None:
        raise HTTPException(422, "対象チャンネルがありません")
    if data["storage_id"] is not None and db.get(Storage, data["storage_id"]) is None:
        raise HTTPException(422, "保存先がありません")
    for key, value in data.items(): setattr(rule, key, value)
    db.commit(); db.refresh(rule)
    return rule


@router.delete("/recording-rules/{rule_id}")
def delete_recording_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(RecordingRule, rule_id)
    if rule is None: raise HTTPException(404, "ルールがありません")
    db.delete(rule); db.commit()
    return {"ok": True}


@router.post("/recording-rules/preview")
def preview_recording_rule(body: RecordingRuleIn, db: Session = Depends(get_db)):
    data = body.model_dump()
    videos = db.scalars(select(Video).order_by(Video.created_at.desc())).all()
    hits = []
    probe = type("RuleProbe", (), data)()
    for video in videos:
        if data["channel_id"] is not None and video.channel_id != data["channel_id"]: continue
        description = str((video.raw_metadata or {}).get("description") or "")
        if matches(probe, video.title or "", description):
            hits.append({"id": str(video.youtube_video_id), "title": video.title, "status": video.status})
    return {"count": len(hits), "videos": hits[:20]}


@router.get("/settings/transcription")
def get_transcription_settings(db: Session = Depends(get_db)):
    from .services.processing import effective_settings
    return effective_settings(db)


@router.put("/settings/transcription")
def put_transcription_settings(data: dict, db: Session = Depends(get_db)):
    from .services.processing import TRANSCRIPTION_DEFAULTS
    allowed = set(TRANSCRIPTION_DEFAULTS)
    if set(data) - allowed:
        raise HTTPException(422, "不明な設定項目です")
    if data.get("whisper_device", "auto") not in ("auto", "cpu", "cuda"):
        raise HTTPException(422, "whisper_device が不正です")
    for key, value in data.items():
        if key == "transcription_max_concurrency" and not 1 <= int(value) <= 8:
            raise HTTPException(422, "同時実行数は1〜8です")
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value={"value": value}); db.add(row)
        else:
            row.value = {"value": value}
    db.commit()
    return {**TRANSCRIPTION_DEFAULTS, **{row.key: row.value["value"] for row in db.query(AppSetting).all() if row.key in allowed}}

@router.get("/settings/summary")
def get_summary_settings(db: Session = Depends(get_db)):
    from .services.processing import effective_summary_settings
    return effective_summary_settings(db)

@router.put("/settings/summary")
def put_summary_settings(data: dict, db: Session = Depends(get_db)):
    from .services.processing import SUMMARY_DEFAULTS, effective_summary_settings
    allowed = set(SUMMARY_DEFAULTS) - {"openrouter_api_key_set"}
    if set(data) - allowed: raise HTTPException(422, "不明な設定項目です")
    for key, value in data.items():
        if key == "openrouter_api_key":
            if value:
                row = db.get(AppSetting, key)
                if row is None: db.add(AppSetting(key=key, value={"value": str(value)}))
                else: row.value = {"value": str(value)}
            continue
        if key == "summary_chunk_minutes" and int(value) not in (15, 20, 30, 60): raise HTTPException(422, "区間は15/20/30/60分です")
        if key == "openrouter_max_concurrency" and not 1 <= int(value) <= 4: raise HTTPException(422, "同時実行数は1〜4です")
        if key == "openrouter_max_attempts" and not 1 <= int(value) <= 10: raise HTTPException(422, "最大試行数は1〜10です")
        if key in ("openrouter_model", "openrouter_base_url") and (not isinstance(value, str) or len(value) > 500): raise HTTPException(422, "設定値が不正です")
        row = db.get(AppSetting, key)
        if row is None: db.add(AppSetting(key=key, value={"value": value}))
        else: row.value = {"value": value}
    db.commit()
    return effective_summary_settings(db)


@router.get("/quality-presets")
def quality_presets():
    return PRESETS


@router.get("/formats/{video_id}")
def list_video_formats(video_id: str, db: Session = Depends(get_db)):
    """Explicit user-triggered yt-dlp --list-formats request."""
    from .services.youtube import cookie_args
    import subprocess
    video = db.scalar(select(Video).where(Video.youtube_video_id == video_id))
    channel = video.channel if video else None
    args = [settings.yt_dlp_path, "--list-formats", "--no-warnings", *cookie_args(channel.cookie_profile if channel else None), f"https://youtube.com/watch?v={video_id}"]
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    except Exception as exc:
        raise HTTPException(502, f"フォーマット取得に失敗しました: {exc}")
    output = result.stdout or result.stderr
    if result.returncode:
        raise HTTPException(502, output[-4000:] or "yt-dlp --list-formats failed")
    return {"video_id": video_id, "formats": output[-20000:]}


@router.get("/channels", response_model=list[ChannelOut])
def channels(db: Session = Depends(get_db)):
    items = db.scalars(select(Channel).where(Channel.archived.is_(False)).order_by(Channel.name)).all()
    return [{**ChannelOut.model_validate(item).model_dump(), "recording_count": db.scalar(
        select(func.count(Video.id)).where(Video.channel_id == item.id, Video.recording_finished_at.is_not(None))) or 0}
        for item in items]


@router.get("/channels/{channel_id}/stats")
def channel_stats(channel_id: int, from_: str | None = Query(default=None, alias="from"), to: str | None = None,
                  granularity: str = "day", db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    if granularity not in ("day", "week", "month"):
        raise HTTPException(422, "granularity は day/week/month のいずれかです")
    def parse(value, fallback):
        if not value: return fallback
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result
        except ValueError: raise HTTPException(422, "期間は ISO 8601 形式で指定してください")
    end = parse(to, now())
    start = parse(from_, end - timedelta(days=90))
    raw = db.scalars(select(ChannelStat).where(ChannelStat.channel_id == channel_id,
        ChannelStat.captured_at >= start - timedelta(days=35), ChannelStat.captured_at <= end).order_by(ChannelStat.captured_at)).all()
    rows = [dict(captured_at=x.captured_at, subscriber_count=x.subscriber_count,
                 subscriber_count_rounded=x.subscriber_count_rounded, video_count=x.video_count, view_count=x.view_count) for x in raw]
    points = aggregate_stats([x for x in rows if start <= x["captured_at"] <= end], granularity)
    # Recording metrics use completed local archive records in the requested interval.
    recordings = db.scalars(select(Video).where(Video.channel_id == channel_id,
        Video.recorded_at >= start, Video.recorded_at <= end, Video.recording_finished_at.is_not(None))).all()
    reasons = {}
    for video in recordings:
        if video.protected:
            reason = video.protection_reason or "UNKNOWN"
            reasons[reason] = reasons.get(reason, 0) + 1
    archived = [v for v in recordings if v.file_path]
    durations = [v.original_duration for v in recordings if v.original_duration is not None]
    latest_row = db.scalar(select(ChannelStat).where(ChannelStat.channel_id == channel_id,
        ChannelStat.captured_at <= end).order_by(ChannelStat.captured_at.desc()).limit(1))
    latest = ({"captured_at": latest_row.captured_at, "subscriber_count": latest_row.subscriber_count,
               "subscriber_count_rounded": latest_row.subscriber_count_rounded, "video_count": latest_row.video_count,
               "view_count": latest_row.view_count} if latest_row else None)
    return {"channel_id": str(channel_id), "from": start.isoformat(), "to": end.isoformat(), "granularity": granularity,
            "points": points, "current": latest, "deltas": period_deltas(rows, end),
            "recordings": {"count": len(recordings), "total_bytes": sum(v.file_size or 0 for v in archived),
                           "protected_count": sum(reasons.values()), "protected_reasons": reasons,
                           "average_duration_seconds": sum(durations) / len(durations) if durations else None}}


@router.post("/channels/preview")
def preview_channel(data: dict):
    """Resolve a channel URL and return display name and icon before registration."""
    raw = str(data.get("url") or "").strip()
    if not raw:
        raise HTTPException(422, "チャンネル URL を入力してください")
    try:
        channel_id, canonical = resolve_channel(raw)
        metadata = channel_metadata(canonical)
    except Exception as exc:
        raise HTTPException(422, f"プレビュー取得に失敗しました: {exc}")
    return {"channel_id": channel_id, "url": canonical,
            "name": metadata.get("channel") or metadata.get("uploader") or metadata.get("title") or channel_id,
            "avatar": metadata.get("avatar") or metadata.get("channel_thumbnail") or metadata.get("thumbnail")}


@router.post("/channels", response_model=ChannelOut)
def add_channel(body: ChannelIn, db: Session = Depends(get_db)):
    try:
        channel_id, canonical_url = resolve_channel(body.youtube_channel_id or body.url or "")
    except Exception as exc:
        raise HTTPException(422, f"チャンネル解決に失敗しました: {exc}")
    if db.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id)):
        raise HTTPException(409, "チャンネルは登録済みです")
    values = body.model_dump(); values.update(youtube_channel_id=channel_id, url=canonical_url)
    ch = Channel(**values); db.add(ch); db.commit(); db.refresh(ch)
    refresh_channel_metadata(ch)
    db.commit(); db.refresh(ch); return ch


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
            try:
                folder = Path(settings.data_dir).resolve() / "channel-icons"
                folder.mkdir(parents=True, exist_ok=True)
                with urlopen(ch.avatar_url, timeout=10) as response:
                    payload = response.read(5 * 1024 * 1024 + 1)
                    if len(payload) <= 5 * 1024 * 1024:
                        content_type = response.headers.get_content_type()
                        suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}.get(content_type, ".img")
                        target = folder / f"{ch.youtube_channel_id}{suffix}"
                        target.write_bytes(payload); ch.avatar_cache_path = str(target)
            except Exception:
                pass
    except Exception:
        pass


@router.post("/channels/{channel_id}/refresh-metadata", response_model=ChannelOut)
def refresh_metadata(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    refresh_channel_metadata(ch); db.commit(); db.refresh(ch); return ch


@router.get("/channels/{channel_id}/avatar")
def channel_avatar(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch or not ch.avatar_cache_path: raise HTTPException(404, "アイコンがありません")
    path = Path(ch.avatar_cache_path).resolve()
    root = (Path(settings.data_dir).resolve() / "channel-icons").resolve()
    if root not in path.parents or not path.is_file(): raise HTTPException(404, "アイコンがありません")
    return FileResponse(path)


@router.put("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: int, body: ChannelIn, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    values = body.model_dump()
    try: values["youtube_channel_id"], values["url"] = resolve_channel(body.youtube_channel_id or body.url or "")
    except Exception as exc: raise HTTPException(422, f"チャンネル解決に失敗しました: {exc}")
    for key, value in values.items(): setattr(ch, key, value)
    db.commit(); db.refresh(ch); return ch


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    ch.enabled = False; ch.archived = True; db.commit(); return {"ok": True}


@router.get("/storages", response_model=list[StorageOut])
def storages(db: Session = Depends(get_db)):
    return db.scalars(select(Storage).order_by(Storage.name)).all()


@router.post("/storages", response_model=StorageOut)
def add_storage(body: StorageIn, db: Session = Depends(get_db)):
    item = Storage(**body.model_dump()); db.add(item); db.commit(); db.refresh(item); return item


@router.get("/storages/usage")
def storage_usage(db: Session = Depends(get_db)):
    """Return live filesystem capacity and sizes tracked by this application."""
    import shutil
    result = []
    for storage in db.scalars(select(Storage).order_by(Storage.name)).all():
        root = Path(storage.root_path)
        try:
            usage = shutil.disk_usage(root if root.exists() else root.parent)
            total, free = usage.total, usage.free
        except OSError:
            total = free = None
        tracked = count = 0
        for video in db.scalars(select(Video).where(Video.storage_id == storage.id, Video.file_path.is_not(None))).all():
            try:
                directory = registered_video_directory(video.file_path, storage.root_path)
                tracked += sum(p.stat().st_size for p in directory.rglob("*") if p.is_file())
                count += 1
            except (OSError, ValueError):
                continue
        result.append({"id": str(storage.id), "name": storage.name, "root_path": storage.root_path,
            "total_bytes": total, "free_bytes": free,
            "used_bytes": total - free if total is not None and free is not None else None,
            "app_bytes": tracked, "recording_count": count,
            "minimum_free_bytes": storage.minimum_free_bytes, "warning_free_bytes": storage.warning_free_bytes,
            "warning": free is not None and free < storage.warning_free_bytes,
            "critical": free is not None and free < storage.minimum_free_bytes})
    return result


@router.put("/storages/{storage_id}", response_model=StorageOut)
def update_storage(storage_id: int, body: StorageIn, db: Session = Depends(get_db)):
    item = db.get(Storage, storage_id)
    if not item: raise HTTPException(404, "ストレージがありません")
    for key, value in body.model_dump().items(): setattr(item, key, value)
    db.commit(); db.refresh(item); return item


@router.post("/videos/{video_id}/schedule/{enabled}")
def set_video_schedule(video_id: int, enabled: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status in ("RECORDING", "LIVE"): raise HTTPException(409, "配信開始後は予約を変更できません")
    if enabled not in ("enable", "disable"): raise HTTPException(404, "操作がありません")
    if enabled == "enable" and video.status == "SKIPPED": video.status = "WAITING_LIVE"
    video.raw_metadata = {**(video.raw_metadata or {}), "schedule_enabled": enabled == "enable"}
    db.commit()
    return {"ok": True, "status": video.status}


@router.post("/videos/{video_id}/recording/try")
def try_record_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status in ("RECORDING", "LIVE"): raise HTTPException(409, "すでに録画中です")
    video.status = "DISCOVERED"
    db.commit()
    return {"ok": True, "status": "DISCOVERED"}


def _video_query(status: str | None, channel_id: int | None, q: str | None,
                 from_: datetime | None, to: datetime | None, min_size: int | None,
                 max_size: int | None, storage_id: int | None,
                 protection_reason: str | None, permanent: bool | None,
                 scope: str):
    query = select(Video)
    if scope == "upcoming": query = query.where(Video.status.in_(("WAITING_LIVE", "LIVE", "RECORDING")))
    elif scope != "all" and status not in ("SKIPPED", "DISCOVERED", "WAITING_LIVE"):
        query = query.where(Video.status.not_in(("WAITING_LIVE", "DISCOVERED", "SKIPPED")))
        query = query.where(Video.file_path.is_not(None))
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


@router.get("/videos/count")
def videos_count(status: str | None = None, channel_id: int | None = None, q: str | None = None,
                from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
                min_size: int | None = None, max_size: int | None = None,
                storage_id: int | None = None, protection_reason: str | None = None,
                permanent: bool | None = None, scope: str = "archive", db: Session = Depends(get_db)):
    query = _video_query(status, channel_id, q, from_, to, min_size, max_size,
                         storage_id, protection_reason, permanent, scope)
    rows = db.scalars(query).all()
    if scope not in ("all", "upcoming") and status not in ("SKIPPED", "DISCOVERED", "WAITING_LIVE"):
        count = sum(_has_archive_media(v) for v in rows)
    else:
        count = len(rows)
    return {"count": count}


@router.get("/videos", response_model=list[VideoOut])
def videos(offset: int = 0, limit: int = 50, status: str | None = None,
           channel_id: int | None = None, q: str | None = None,
           from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None,
           min_size: int | None = None, max_size: int | None = None,
           storage_id: int | None = None, protection_reason: str | None = None,
           permanent: bool | None = None, sort: str = "date", direction: str = "desc",
           scope: str = "archive",
           db: Session = Depends(get_db)):
    """scope=archive（既定）は実メディアファイルに紐づく録画レコードのみ返す。
    配信予定/配信中（WAITING_LIVE / DISCOVERED）は「配信予定」画面の担当なので、
    録画一覧には混ぜない。scope=upcoming でその逆、scope=all で全件。"""
    query = _video_query(status, channel_id, q, from_, to, min_size, max_size,
                         storage_id, protection_reason, permanent, scope)
    sort_col = {"date": Video.recorded_at, "size": Video.file_size, "duration": Video.original_duration,
                "title": Video.title}.get(sort, Video.recorded_at)
    query = query.order_by(sort_col.asc() if direction == "asc" else sort_col.desc(), Video.id.desc())
    if scope not in ("all", "upcoming") and status not in ("SKIPPED", "DISCOVERED", "WAITING_LIVE"):
        result_rows = [v for v in db.scalars(query).all() if _has_archive_media(v)]
        result_rows = result_rows[max(0, offset):max(0, offset) + min(100, max(1, limit))]
    else:
        result_rows = db.scalars(query.offset(max(0, offset)).limit(min(100, max(1, limit)))).all()
    result = []
    for v in result_rows:
        item = VideoOut.model_validate(v).model_dump()
        item["has_file"] = bool(v.file_path and Path(v.file_path).is_dir())
        item["live_playback_available"] = bool(v.live_hls_path and (Path(v.live_hls_path) / "index.m3u8").is_file())
        item["channel_name"] = v.channel.name if v.channel else ""
        item["storage_name"] = v.storage.name if v.storage else None
        directory = Path(v.file_path) if v.file_path and Path(v.file_path).is_dir() else None
        media = media_file(directory) if directory else None
        item["media_extension"] = media.suffix.lower() if media else None
        item["has_thumbnail"] = bool(directory and any(p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for p in directory.iterdir()))
        result.append(item)
    return result


def _video_or_404(db, video_id):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    return video


@router.get("/videos/{video_id}/transcript")
def get_transcript(video_id: int, offset: int = 0, limit: int = 100, q: str | None = None,
                   format: str = "json", db: Session = Depends(get_db)):
    from .services.transcription import to_srt, to_webvtt
    video = _video_or_404(db, video_id)
    if format in ("vtt", "srt", "text"):
        rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
        segments = [{"start": x.start, "end": x.end, "text": x.text} for x in rows]
        body = to_webvtt(segments) if format == "vtt" else to_srt(segments) if format == "srt" else "\n".join(x["text"] for x in segments)
        return {"format": format, "content": body}
    query = select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False))
    if q:
        from sqlalchemy import func
        term = q.strip()
        full_text = func.to_tsvector("simple", TranscriptSegment.text).op("@@")(func.plainto_tsquery("simple", term))
        # Keep substring matching for languages (notably Japanese) that do not
        # have whitespace-delimited tokens in PostgreSQL's simple dictionary.
        query = query.where(or_(TranscriptSegment.text.ilike(f"%{term}%"), full_text))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(TranscriptSegment.start).offset(max(0, offset)).limit(min(500, max(1, limit)))).all()
    return {"status": video.transcription_status, "error": video.transcription_error,
            "model": video.transcription_model, "total": total,
            "segments": [{"start": x.start, "end": x.end, "text": x.text} for x in rows]}


@router.get("/search/transcripts")
def search_transcripts(q: str, channel_id: int | None = None, from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, offset: int = 0, limit: int = 30, db: Session = Depends(get_db)):
    """Search completed transcript segments with optional pg_trgm acceleration."""
    from .services.search import excerpt, format_time
    term=q.strip()
    if not term: return {"total":0,"offset":max(0,offset),"limit":min(100,max(1,limit)),"results":[],"engine":"ilike"}
    # Check extension at runtime so installations without CREATE EXTENSION still work.
    trigram=False
    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            trigram=bool(db.execute(__import__("sqlalchemy").text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')")).scalar())
        except Exception:
            db.rollback(); trigram=False
    query=(select(TranscriptSegment,Video,Channel).join(Video,Video.id==TranscriptSegment.video_id).join(Channel,Channel.id==Video.channel_id)
           .where(TranscriptSegment.raw.is_(False),Video.status=="ACTIVE",Video.protected.is_(False)))
    if channel_id is not None: query=query.where(Channel.id==channel_id)
    if from_ is not None: query=query.where(Video.recorded_at>=from_)
    if to is not None: query=query.where(Video.recorded_at<=to)
    # Keep substring matching exact while allowing trigram-index-compatible candidates when installed.
    predicate=TranscriptSegment.text.ilike(f"%{term}%")
    if trigram: predicate=or_(predicate, TranscriptSegment.text.op("%") (term))
    query=query.where(predicate)
    total=db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows=db.execute(query.order_by(Video.recorded_at.desc(),TranscriptSegment.start).offset(max(0,offset)).limit(min(100,max(1,limit)))).all()
    results=[]
    for segment,video,channel in rows:
        results.append({"video_id":str(video.id),"youtube_video_id":video.youtube_video_id,"title":video.title,"channel_id":str(channel.id),"channel_name":channel.name,"start":segment.start,"time":format_time(segment.start),"excerpt":excerpt(segment.text,term),"text":segment.text})
    return {"total":total,"offset":max(0,offset),"limit":min(100,max(1,limit)),"engine":"pg_trgm" if trigram else "ilike","results":results}


@router.get("/videos/{video_id}/timeline")
def video_timeline(video_id: int, db: Session = Depends(get_db)):
    from .services.search import timeline
    video=_video_or_404(db,video_id)
    segments=db.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id==video_id,TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
    return {"items":timeline(video.summary_data,segments)}


@router.post("/videos/{video_id}/transcript")
def request_transcription(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    if video.status != "ACTIVE" or not video.recording_finished_at:
        raise HTTPException(409, "録画完了済みの動画だけ文字起こしできます")
    from .services.processing import enqueue_transcription
    job = enqueue_transcription(db, video_id, force=True)
    if not job:
        if video.transcription_status == "disabled":
            raise HTTPException(409, "文字起こしは設定で無効です")
        raise HTTPException(409, "録画ファイルがありません")
    return {"ok": True, "job_id": str(job.id), "status": job.status}

@router.get("/videos/{video_id}/summary")
def get_summary(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    return {"status": video.summary_status, "error": video.summary_error, "model": video.summary_model, "summary": video.summary_data}

@router.post("/videos/{video_id}/summary")
def regenerate_summary(video_id: int, mode: str = "all", model: str | None = None, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    if mode not in ("all", "final"): raise HTTPException(422, "mode は all または final")
    from .services.processing import enqueue_summary
    job = enqueue_summary(db, video_id, force=True, final_only=(mode == "final"), model=model)
    if not job:
        if video.summary_status == "disabled": raise HTTPException(409, "要約は無効、未設定、または文字起こし未完了です")
        raise HTTPException(409, "ローカル文字起こしが完了していません")
    return {"ok": True, "job_id": str(job.id), "status": job.status}

@router.get("/videos/{video_id}/live/{asset}")
def live_asset(video_id: int, asset: str, db: Session = Depends(get_db)):
    from .services.live_playback import safe_hls_asset
    video = _video_or_404(db, video_id)
    try:
        path = safe_hls_asset(video, asset)
    except ValueError:
        raise HTTPException(404, "ライブ再生ファイルがありません")
    is_playlist = path.suffix == ".m3u8"
    headers = {"Cache-Control": "no-store" if is_playlist else "public, max-age=31536000, immutable"}
    return FileResponse(path, media_type="application/vnd.apple.mpegurl" if is_playlist else "video/mp2t", headers=headers)

@router.get("/videos/{video_id}/chat")
def chat_messages(video_id: int, offset: int = 0, limit: int = 200, q: str | None = None,
                  kind: str | None = None, include_deleted: bool = False, db: Session = Depends(get_db)):
    _video_or_404(db, video_id)
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if kind == "superchat": stmt = stmt.where(ChatMessage.message_type == "superchat")
    elif kind == "membership": stmt = stmt.where(ChatMessage.message_type == "membership")
    if q:
        term = f"%{q}%"; stmt = stmt.where(or_(ChatMessage.message_text.ilike(term), ChatMessage.author_name.ilike(term)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms, ChatMessage.id).offset(max(0, offset)).limit(min(500, max(1, limit)))).all()
    sums = db.execute(select(ChatMessage.superchat_currency, func.sum(ChatMessage.superchat_amount)).where(
        ChatMessage.video_id == video_id, ChatMessage.message_type == "superchat", ChatMessage.superchat_amount.is_not(None)).group_by(ChatMessage.superchat_currency)).all()
    return {"total": total, "messages": [{"id":str(m.id),"message_id":m.message_id,"offset_ms":m.offset_ms,"published_at":m.published_at,
        "author_channel_id":m.author_channel_id,"author_name":m.author_name,"author_badges":m.author_badges,"message_text":m.message_text,
        "superchat_amount":m.superchat_amount,"superchat_currency":m.superchat_currency,"superchat_color":m.superchat_color,
        "message_type":m.message_type,"is_deleted":m.is_deleted,"deleted_at":m.deleted_at,"deletion_source":m.deletion_source} for m in rows],
        "superchat_totals": {currency or "unknown": float(amount or 0) for currency, amount in sums}}

@router.get("/videos/{video_id}/chat/danmaku")
def chat_danmaku(video_id: int, include_deleted: bool = False, kind: str | None = None, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if kind in ("superchat", "membership"): stmt = stmt.where(ChatMessage.message_type == kind)
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms)).all()
    recording_offset = int((video.raw_metadata or {}).get("recording_start_offset_ms", 0))
    return {"code":0,"recording_offset_ms":recording_offset,"data":[[max(0,m.offset_ms-recording_offset)/1000, "right", m.superchat_color or "#ffffff", m.author_name,
        ("[削除済] " if m.is_deleted else "") + m.message_text] for m in rows]}


@router.get("/videos/{video_id}/chat/danmaku/{filter_key}/v3/")
def dplayer_danmaku(video_id: int, filter_key: str, db: Session = Depends(get_db)):
    """DPlayer v3 danmaku protocol adapter for saved live chat."""
    video = _video_or_404(db, video_id)
    parts = filter_key.split("-")
    kind = parts[0] if parts and parts[0] in ("all", "superchat", "membership") else "all"
    include_deleted = len(parts) > 1 and parts[1] == "1"
    recording_offset = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0)))
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if kind != "all": stmt = stmt.where(ChatMessage.message_type == kind)
    # Comments from before a mid-stream recording have negative local time.
    # Exclude them instead of clamping every one to zero (which creates a
    # large burst at playback start).
    if recording_offset: stmt = stmt.where(ChatMessage.offset_ms >= recording_offset)
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms)).all()
    # DPlayer 1.27's built-in v3 reader indexes each row as
    # [time, type, color, author, text]. Object rows pass the code check but
    # become undefined fields in that reader, so no danmaku is drawn.
    return {"code": 0, "data": [[max(0, m.offset_ms-recording_offset)/1000,
        0, m.superchat_color or "#ffffff", m.author_name,
        ("[削除済] " if m.is_deleted else "") + m.message_text] for m in rows]}

@router.get("/videos/{video_id}/chat/export")
def export_chat(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id).order_by(ChatMessage.offset_ms)).all()
    return {"video_id":video.youtube_video_id,"messages":[{"message_id":m.message_id,"offset_ms":m.offset_ms,"published_at":m.published_at,
        "author_channel_id":m.author_channel_id,"author_name":m.author_name,"author_badges":m.author_badges,"message_text":m.message_text,
        "superchat_amount":m.superchat_amount,"superchat_currency":m.superchat_currency,"superchat_color":m.superchat_color,
        "message_type":m.message_type,"raw":m.raw,"first_seen_at":m.first_seen_at,"last_seen_at":m.last_seen_at,
        "is_deleted":m.is_deleted,"deleted_at":m.deleted_at,"deletion_source":m.deletion_source} for m in rows]}

@router.post("/videos/{video_id}/chat/retrieve")
def retrieve_chat(video_id: int, db: Session = Depends(get_db)):
    """Fetch replay into a temporary file; DB changes only after successful yt-dlp completion and parse."""
    import subprocess, tempfile
    from .services.youtube import cookie_args
    video = _video_or_404(db, video_id)
    channel = video.channel
    if channel and not channel.chat_enabled: raise HTTPException(409, "このチャンネルではチャット取得が無効です")
    with tempfile.TemporaryDirectory(prefix="yla-chat-") as temp:
        output = str(Path(temp) / "%(id)s.%(ext)s")
        args = [settings.yt_dlp_path,"--no-warnings","--skip-download","--write-subs","--sub-langs","live_chat","--sub-format","json","-o",output,*cookie_args(channel.cookie_profile if channel else None),f"https://www.youtube.com/watch?v={video.youtube_video_id}"]
        proc = subprocess.run(args,capture_output=True,timeout=3600,check=False)
        files = list(Path(temp).glob("*live_chat*")) + list(Path(temp).glob("*.live_chat.json"))
        if proc.returncode or not files: raise HTTPException(502,"チャットリプレイを取得できませんでした。既存コメントは変更していません")
        try: count = ingest_jsonl(db,video.id,files[0],complete_snapshot=True,isolated=True)
        except Exception as exc: db.rollback(); raise HTTPException(422,f"チャット形式を解析できませんでした: {exc}")
    db.commit()
    return {"ok":True,"messages_seen":count}

@router.get("/videos/{video_id}/file")
def video_file(video_id: int, request: Request, download: bool = False, playable: bool = False, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path: raise HTTPException(404, "録画ファイルがありません")
    try: candidate = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
    except (ValueError, OSError): raise HTTPException(404, "録画ファイルがありません")
    media = media_file(candidate)
    if not playable and media and media.name.endswith(".playable.mp4"):
        media = next((p.resolve() for p in candidate.iterdir() if p.is_file() and p.suffix.lower() in {".mkv", ".mp4", ".webm", ".ts", ".m4v", ".mov", ".flv", ".avi"} and not p.name.endswith((".playable.mp4", ".h264.mp4"))), None)
    if playable:
        media = next((p.resolve() for p in candidate.iterdir() if p.is_file() and p.name.endswith(".playable.mp4")), None) or media
    if media is None or candidate not in media.parents: raise HTTPException(404, "録画ファイルがありません")
    size = media.stat().st_size
    mime = mimetypes.guess_type(media.name)[0] or "application/octet-stream"
    if download: return FileResponse(media, media_type=mime, filename=media.name)
    range_header = request.headers.get("range")
    start, end, code = 0, size - 1, 200
    if range_header:
        try:
            start, end = parse_byte_range(range_header, size)
            code = 206
        except ValueError: raise HTTPException(416, "無効な Range", headers={"Content-Range": f"bytes */{size}"})
    def stream():
        with media.open("rb") as f:
            f.seek(start); remaining = end - start + 1
            while remaining:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk: break
                remaining -= len(chunk); yield chunk
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end-start+1)}
    if code == 206: headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=code, media_type=mime, headers=headers)


@router.post("/videos/{video_id}/convert/{mode}")
def convert_video(video_id: int, mode: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path or not video.storage: raise HTTPException(404, "録画ファイルがありません")
    try:
        folder = registered_video_directory(video.file_path, video.storage.root_path)
        source = next((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".mkv", ".mp4", ".webm", ".ts", ".m4v", ".mov", ".flv", ".avi"} and not p.name.endswith((".playable.mp4", ".h264.mp4"))), None)
        if source is None: raise HTTPException(404, "変換元がありません")
        return enqueue_conversion(video_id, source, mode, settings.ffmpeg_path)
    except ValueError as exc: raise HTTPException(400, str(exc))


@router.get("/videos/{video_id}/convert/{mode}")
def get_conversion_status(video_id: int, mode: str):
    if mode not in ("remux", "h264"): raise HTTPException(404, "変換形式がありません")
    return conversion_status(video_id, mode)


@router.get("/videos/{video_id}/thumbnail")
def video_thumbnail(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path: raise HTTPException(404, "サムネイルがありません")
    try: folder = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
    except (ValueError, OSError): raise HTTPException(404, "サムネイルがありません")
    thumb = next((p.resolve() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if not thumb or folder not in thumb.parents: raise HTTPException(404, "サムネイルがありません")
    return FileResponse(thumb)


@router.post("/videos/{video_id}/protect")
def protect_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    video.protected, video.protection_reason, video.protected_at, video.status = True, "MANUAL", now(), "PROTECTED"
    db.add(AuditLog(video_id=video.id, action="PROTECT", reason="MANUAL")); db.commit()
    return {"ok": True, "protected": True}


@router.delete("/videos/{video_id}/protect")
def unprotect_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.protection_reason != "MANUAL": raise HTTPException(409, "手動保護だけ解除できます")
    video.protected = False; video.protection_reason = None; video.protected_at = None
    video.status = "ACTIVE"; db.add(AuditLog(video_id=video.id, action="UNPROTECT", reason="MANUAL")); db.commit()
    return {"ok": True, "protected": False}


@router.delete("/videos/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.protected: raise HTTPException(409, "保護を解除してから削除してください")
    if not video.file_path or not video.storage: raise HTTPException(409, "削除できる録画ファイルがありません")
    try: path = registered_video_directory(video.file_path, video.storage.root_path)
    except (ValueError, OSError): raise HTTPException(404, "録画ファイルがありません")
    import shutil
    shutil.rmtree(path)
    from .services.derived import clear_transcript_segments
    clear_transcript_segments(db, video)
    video.status = "DELETED_MANUALLY"; video.file_path = None; video.file_size = 0
    db.add(AuditLog(video_id=video.id, action="MANUAL_DELETE", reason="USER_CONFIRMED")); db.commit()
    return {"ok": True}


@router.get("/videos/{video_id}/recording/monitor")
def recording_monitor(video_id: int, db: Session = Depends(get_db)):
    import shutil
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    size = 0
    try:
        folder = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
        media = media_file(folder)
        if media and media.is_file(): size = media.stat().st_size
    except (OSError, ValueError):
        folder = None
    free = None
    if video.storage:
        try: free = shutil.disk_usage(video.storage.root_path).free
        except OSError: pass
    errors = list(folder.parent.glob(f"{video.youtube_video_id}-recording-error.log")) if folder else []
    last_error = ""
    if errors:
        try: last_error = errors[0].read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError: pass
    elapsed = max(0, (now() - video.recorded_at).total_seconds()) if video.recorded_at and video.status == "RECORDING" else 0
    return {"file_size": size, "elapsed_seconds": elapsed,
            "heartbeat_at": video.recording_heartbeat_at.isoformat() if video.recording_heartbeat_at else None,
            "free_bytes": free, "last_error": last_error}


@router.post("/videos/{video_id}/recording/stop")
def stop_video_recording(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status != "RECORDING" or not stop_recording(video_id):
        raise HTTPException(409, "実行中の録画プロセスがありません")
    return {"ok": True, "status": "stopping"}


@router.post("/videos/{video_id}/recording/retry")
def retry_video_recording(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    local_media = None
    if video.file_path and Path(video.file_path).is_dir():
        try:
            local_media = media_file(Path(video.file_path))
        except OSError:
            local_media = None
    missing_or_empty = not local_media or local_media.stat().st_size <= 0
    if video.status not in ("RECORDING_FAILED", "WAITING_FOR_STORAGE") and not missing_or_empty:
        raise HTTPException(409, "この録画は再開できません")
    video.status = "DISCOVERED"; video.recording_finished_at = None
    video.is_partial = bool(video.file_path and Path(video.file_path).is_dir())
    video.next_recovery_at = None
    # Preserve file_path and any partial evidence. New download output uses the same
    # target; yt-dlp may resume or replace individual outputs as appropriate.
    db.commit()
    return {"ok": True, "status": "DISCOVERED"}
