from .common import *

from ..db import SessionLocal
from ..services.media import inspect_media, source_media, video_playback_metadata


def _ensure_media_info(video_id: int, directory: Path | None, metadata: dict) -> dict | None:
    current = metadata.get("media_info")
    if isinstance(current, dict):
        return current
    source = source_media(directory) if directory else None
    if source is None:
        return None
    info = inspect_media(source, settings.ffprobe_path)
    if info:
        with SessionLocal() as session:
            video = session.get(Video, video_id)
            if video:
                video.raw_metadata = {**(video.raw_metadata or {}), "media_info": info}
                session.commit()
    return info

@router.post("/videos/{video_id}/schedule/{enabled}")
def set_video_schedule(video_id: int, enabled: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if enabled not in ("enable", "disable"): raise HTTPException(404, "操作がありません")
    metadata = video.raw_metadata or {}
    allowed = video.status == "WAITING_LIVE" or (video.status == "SKIPPED" and metadata.get("skip_reason") == "user_disabled")
    if not allowed: raise HTTPException(409, "この状態の動画は配信予約を変更できません")
    if enabled == "enable" and video.status == "SKIPPED": video.status = "WAITING_LIVE"
    if enabled == "disable" and video.status == "WAITING_LIVE":
        video.status = "SKIPPED"
        metadata["skip_reason"] = "user_disabled"
    video.raw_metadata = {**metadata, "schedule_enabled": enabled == "enable"}
    db.commit()
    return {"ok": True, "status": video.status}

@router.post("/videos/{video_id}/recording/try")
def try_record_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status not in ("WAITING_LIVE", "SKIPPED", "CANCELLED"):
        raise HTTPException(409, "この状態の動画は録画を再試行できません")
    video.status = "DISCOVERED"
    video.raw_metadata = {**(video.raw_metadata or {}), "schedule_enabled": True}
    db.commit()
    return {"ok": True, "status": "DISCOVERED"}

@router.get("/videos/count")
def videos_count(status: str | None = None, channel_id: int | None = None, q: str | None = None,
                from_: str | None = Query(default=None, alias="from"), to: str | None = None,
                min_size: int | None = None, max_size: int | None = None,
                storage_id: int | None = None, protection_reason: str | None = None,
                permanent: bool | None = None, scope: str = "archive", db: Session = Depends(get_db)):
    if status and status not in _VIDEO_STATUSES | {"RECORDING_FAILED"}: raise HTTPException(422, "status が不正です")
    if scope not in ("all", "archive", "upcoming", "deleted"): raise HTTPException(422, "scope が不正です")
    from_dt, to_dt = _parse_date_filter(from_), _parse_date_filter(to)
    if to and len(to) == 10: to_dt += timedelta(days=1) - timedelta(microseconds=1)
    query = _video_query(status, channel_id, q, from_dt, to_dt, min_size, max_size,
                         storage_id, protection_reason, permanent, scope)
    count = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    db.commit()
    return {"count": count}

@router.get("/videos", response_model=list[VideoOut])
def videos(offset: int = 0, limit: int = 50, status: str | None = None,
           channel_id: int | None = None, q: str | None = None,
           from_: str | None = Query(default=None, alias="from"), to: str | None = None,
           min_size: int | None = None, max_size: int | None = None,
           storage_id: int | None = None, protection_reason: str | None = None,
           permanent: bool | None = None, sort: str = "date", direction: str = "desc",
           scope: str = "archive",
           db: Session = Depends(get_db)):
    """scope=archive（既定）は実メディアファイルに紐づく録画レコードのみ返す。
    配信予定/配信中（WAITING_LIVE / DISCOVERED）は「配信予定」画面の担当なので、
    録画一覧には混ぜない。scope=upcoming でその逆、scope=all で全件。"""
    if status and status not in _VIDEO_STATUSES | {"RECORDING_FAILED"}: raise HTTPException(422, "status が不正です")
    if scope not in ("all", "archive", "upcoming", "deleted"): raise HTTPException(422, "scope が不正です")
    if sort not in ("date", "size", "duration", "title") or direction not in ("asc", "desc"):
        raise HTTPException(422, "sort または direction が不正です")
    from_dt, to_dt = _parse_date_filter(from_), _parse_date_filter(to)
    if to and len(to) == 10: to_dt += timedelta(days=1) - timedelta(microseconds=1)
    query = _video_query(status, channel_id, q, from_dt, to_dt, min_size, max_size,
                         storage_id, protection_reason, permanent, scope)
    sort_col = {"date": Video.recorded_at, "size": Video.file_size, "duration": Video.original_duration,
                "title": Video.title}.get(sort, Video.recorded_at)
    if scope == "upcoming":
        query = query.order_by(Video.scheduled_start_at.asc().nulls_last(), Video.id.desc())
    else:
        query = query.order_by(sort_col.asc() if direction == "asc" else sort_col.desc(), Video.id.desc())
    result_rows = db.scalars(query.offset(max(0, offset)).limit(min(100, max(1, limit)))).all()
    probe_candidates = [
        (video.id, Path(video.file_path) if video.file_path else None, dict(video.raw_metadata or {}))
        for video in result_rows
    ]
    db.commit()
    media_info_by_id: dict[int, dict | None] = {}
    probes = 0
    for video_id, directory, metadata in probe_candidates:
        info = metadata.get("media_info")
        if not isinstance(info, dict) and probes < 5:
            info = _ensure_media_info(video_id, directory, metadata)
            probes += 1
        media_info_by_id[video_id] = info if isinstance(info, dict) else None
    result = []
    for v in result_rows:
        item = VideoOut.model_validate(v).model_dump()
        directory = Path(v.file_path) if v.file_path else None
        directory = directory if directory and directory.is_dir() else None
        item["has_file"] = directory is not None
        item["live_playback_available"] = bool(v.live_hls_path and (Path(v.live_hls_path) / "index.m3u8").is_file())
        item["channel_name"] = v.channel.name if v.channel else ""
        item["channel_avatar"] = bool(v.channel and v.channel.avatar_cache_path and Path(v.channel.avatar_cache_path).is_file())
        item["storage_name"] = v.storage.name if v.storage else None
        metadata = v.raw_metadata or {}
        item.update(user_stopped=bool(metadata.get("user_stopped")),
                    duration_seconds=v.original_duration if v.original_duration is not None else metadata.get("local_duration"),
                    deletion_after=v.deletion_after, archive_backfill=metadata.get("archive_backfill"),
                    segment_count=len(metadata.get("intervals") or [item for item in metadata.get("attempts", []) if item.get("profile") == "live"]), recovery_exhausted=bool(metadata.get("recovery_exhausted")),
                    skip_reason=metadata.get("skip_reason"), cancel_reason=metadata.get("cancel_reason"),
                    missed_live=bool(metadata.get("missed_live")), schedule_enabled=metadata.get("schedule_enabled", True),
                    live_status=metadata.get("live_status"), recording_start_offset_ms=metadata.get("recording_start_offset_ms", 0),
                    live_from_start=metadata.get("live_from_start"))
        if v.status in ("RECORDING_FAILED", "WAITING_FOR_STORAGE") and v.file_path:
            error_path = Path(v.file_path).parent / f"{v.youtube_video_id}-recording-error.log"
            try: item["last_error"] = error_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            except OSError: item["last_error"] = None
        media = media_file(directory) if directory else None
        item["media_extension"] = media.suffix.lower() if media else None
        source = source_media(directory) if directory else None
        item.update(video_playback_metadata(v.id, source, media_info_by_id.get(v.id))
                    if source else {"media": media_info_by_id.get(v.id), "renditions": []})
        item["has_thumbnail"] = bool(directory and any(p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for p in directory.iterdir()))
        result.append(item)
    return result

@router.get("/videos/{video_id}", response_model=VideoOut)
def video_detail(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    directory = Path(video.file_path) if video.file_path and Path(video.file_path).is_dir() else None
    metadata = dict(video.raw_metadata or {})
    source = source_media(directory) if directory else None
    db.commit()
    info = _ensure_media_info(video.id, directory, metadata) if source else None
    db.commit()
    item = VideoOut.model_validate(video).model_dump()
    item.update(channel_name=video.channel.name if video.channel else "", storage_name=video.storage.name if video.storage else None,
                has_file=bool(video.file_path and Path(video.file_path).is_dir()),
                live_playback_available=bool(video.live_hls_path and (Path(video.live_hls_path) / "index.m3u8").is_file()),
                channel_avatar=bool(video.channel and video.channel.avatar_cache_path and Path(video.channel.avatar_cache_path).is_file()))
    metadata = video.raw_metadata or {}
    item.update(user_stopped=bool(metadata.get("user_stopped")),
                duration_seconds=video.original_duration if video.original_duration is not None else metadata.get("local_duration"),
                deletion_after=video.deletion_after, archive_backfill=metadata.get("archive_backfill"),
                segment_count=len(metadata.get("intervals") or [attempt for attempt in metadata.get("attempts", []) if attempt.get("profile") == "live"]), recovery_exhausted=bool(metadata.get("recovery_exhausted")),
                skip_reason=metadata.get("skip_reason"), cancel_reason=metadata.get("cancel_reason"),
                missed_live=bool(metadata.get("missed_live")), schedule_enabled=metadata.get("schedule_enabled", True),
                live_status=metadata.get("live_status"), recording_start_offset_ms=metadata.get("recording_start_offset_ms", 0),
                live_from_start=metadata.get("live_from_start"))
    media = media_file(directory) if directory else None
    item["media_extension"] = media.suffix.lower() if media else None
    item.update(video_playback_metadata(video.id, source, info) if source else {"media": info, "renditions": []})
    item["has_thumbnail"] = bool(directory and any(path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} for path in directory.iterdir()))
    if video.status in ("RECORDING_FAILED", "WAITING_FOR_STORAGE") and video.file_path:
        try: item["last_error"] = (Path(video.file_path).parent / f"{video.youtube_video_id}-recording-error.log").read_text(encoding="utf-8", errors="replace")[-2000:]
        except OSError: item["last_error"] = None
    return item

@router.get("/videos/{video_id}/transcript")
def get_transcript(video_id: int, offset: int = 0, limit: int = 100, q: str | None = None,
                   format: str = "json", db: Session = Depends(get_db)):
    from ..services.transcription import to_srt, to_webvtt
    video = _video_or_404(db, video_id)
    if format in ("vtt", "srt", "text"):
        rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
        segments = [{"start": x.start, "end": x.end, "text": x.text} for x in rows]
        body = to_webvtt(segments) if format == "vtt" else to_srt(segments) if format == "srt" else "\n".join(x["text"] for x in segments)
        db.commit()
        return {"format": format, "content": body}
    query = select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False))
    if q:
        term = q.strip()
        full_text = func.to_tsvector("simple", TranscriptSegment.text).op("@@")(func.plainto_tsquery("simple", term))
        # Keep substring matching for languages (notably Japanese) that do not
        # have whitespace-delimited tokens in PostgreSQL's simple dictionary.
        query = query.where(or_(TranscriptSegment.text.ilike(f"%{term}%"), full_text))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(TranscriptSegment.start).offset(max(0, offset)).limit(min(500, max(1, limit)))).all()
    result = {"status": video.transcription_status, "error": video.transcription_error,
            "model": video.transcription_model, "total": total,
            "segments": [{"start": x.start, "end": x.end, "text": x.text} for x in rows]}
    db.commit()
    return result

@router.get("/search/transcripts")
def search_transcripts(q: str, channel_id: int | None = None, from_: datetime | None = Query(default=None, alias="from"), to: datetime | None = None, offset: int = 0, limit: int = 30, db: Session = Depends(get_db)):
    """Search completed transcript segments with optional pg_trgm acceleration."""
    from ..services.search import excerpt, format_time
    term=q.strip()
    if not term: return {"total":0,"offset":max(0,offset),"limit":min(100,max(1,limit)),"results":[],"engine":"ilike"}
    # Check extension at runtime so installations without CREATE EXTENSION still work.
    trigram=False
    if db.bind and db.bind.dialect.name == "postgresql":
        try:
            trigram=bool(db.execute(__import__("sqlalchemy").text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')")).scalar())
        except Exception:
            db.rollback()
            trigram = False
    query=(select(TranscriptSegment,Video,Channel).join(Video,Video.id==TranscriptSegment.video_id).join(Channel,Channel.id==Video.channel_id)
           .where(TranscriptSegment.raw.is_(False), Video.file_path.is_not(None),
                  Video.status.not_in(("DELETED_BY_RETENTION", "DELETED_MANUALLY"))))
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
    db.commit()
    return {"total":total,"offset":max(0,offset),"limit":min(100,max(1,limit)),"engine":"pg_trgm" if trigram else "ilike","results":results}

@router.get("/videos/{video_id}/timeline")
def video_timeline(video_id: int, db: Session = Depends(get_db)):
    from ..services.search import timeline
    video=_video_or_404(db,video_id)
    segments=db.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id==video_id,TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
    result = {"items":timeline(video.summary_data,segments)}
    db.commit()
    return result

@router.post("/videos/{video_id}/transcript")
def request_transcription(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    if video.status != "ACTIVE" or not video.recording_finished_at:
        raise HTTPException(409, "録画完了済みの動画だけ文字起こしできます")
    from ..services.processing import enqueue_transcription
    job = enqueue_transcription(db, video_id, force=True)
    if not job:
        if video.transcription_status == "disabled":
            raise HTTPException(409, "文字起こしは設定で無効です")
        raise HTTPException(409, "録画ファイルがありません")
    return {"ok": True, "job_id": str(job.id), "status": job.status}

@router.get("/videos/{video_id}/summary")
def get_summary(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    result = {"status": video.summary_status, "error": video.summary_error, "model": video.summary_model, "summary": video.summary_data}
    db.commit()
    return result

@router.post("/videos/{video_id}/summary")
def regenerate_summary(video_id: int, mode: str = "all", model: str | None = None, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    if mode not in ("all", "final"): raise HTTPException(422, "mode は all または final")
    from ..services.processing import enqueue_summary
    job = enqueue_summary(db, video_id, force=True, final_only=(mode == "final"), model=model)
    if not job:
        if video.summary_status == "disabled": raise HTTPException(409, "要約は無効、未設定、または文字起こし未完了です")
        raise HTTPException(409, "ローカル文字起こしが完了していません")
    return {"ok": True, "job_id": str(job.id), "status": job.status}

@router.get("/videos/{video_id}/live/{asset}")
def live_asset(video_id: int, asset: str, db: Session = Depends(get_db)):
    from ..services.live_playback import safe_hls_asset
    video = _video_or_404(db, video_id)
    try:
        path = safe_hls_asset(video, asset)
    except ValueError:
        raise HTTPException(404, "ライブ再生ファイルがありません")
    is_playlist = path.suffix == ".m3u8"
    headers = {"Cache-Control": "no-store" if is_playlist else "public, max-age=31536000, immutable"}
    # FileResponse streams after the endpoint returns. Release the lookup transaction
    # and pooled connection before Starlette spends time reading and sending it.
    db.close()
    return FileResponse(path, media_type="application/vnd.apple.mpegurl" if is_playlist else "video/mp2t", headers=headers)

@router.get("/videos/{video_id}/chat")
def chat_messages(video_id: int, offset: int = 0, limit: int = 200, q: str | None = None,
                  kind: str | None = None, include_deleted: bool = False,
                  after_ms: int | None = None, before_ms: int | None = None,
                  db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    available_query = select(func.count()).select_from(stmt.subquery())
    available_total = db.scalar(available_query) or 0
    if kind == "superchat": stmt = stmt.where(ChatMessage.message_type.in_(("superchat", "supersticker")))
    elif kind == "membership": stmt = stmt.where(ChatMessage.message_type.in_(("membership", "membership_gift", "membership_gift_received")))
    recording_offset = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0)))
    if after_ms is not None: stmt = stmt.where(ChatMessage.offset_ms >= after_ms + recording_offset)
    if before_ms is not None: stmt = stmt.where(ChatMessage.offset_ms <= before_ms + recording_offset)
    if q:
        term = f"%{q}%"
        stmt = stmt.where(or_(ChatMessage.message_text.ilike(term), ChatMessage.author_name.ilike(term)))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms, ChatMessage.id).offset(max(0, offset)).limit(min(500, max(1, limit)))).all()
    sums = db.execute(select(ChatMessage.superchat_currency, func.sum(ChatMessage.superchat_amount)).where(
        ChatMessage.video_id == video_id, ChatMessage.message_type.in_(("superchat", "supersticker")), ChatMessage.superchat_amount.is_not(None)).group_by(ChatMessage.superchat_currency)).all()
    unknown = db.execute(select(ChatMessage.amount_display, func.count()).where(
        ChatMessage.video_id == video_id, ChatMessage.message_type.in_(("superchat", "supersticker")),
        ChatMessage.superchat_amount.is_(None), ChatMessage.amount_display.is_not(None)).group_by(ChatMessage.amount_display)).all()
    from ..services.chat_assets import asset_key
    def cached_asset(url: str | None) -> str | None:
        return f"/api/chat-assets/{asset_key(url)}" if url and url.startswith("https://") else None
    messages = []
    for m in rows:
        from ..services.chat import parse_raw_message
        parsed_raw = parse_raw_message(m.raw, m.message_type) if not m.message_runs else None
        source_runs = m.message_runs or (parsed_raw or {}).get("message_runs") or []
        parsed_badges = (parsed_raw or {}).get("author_badges") or []
        stored_badges = m.author_badges or []
        has_legacy_badges = any(not isinstance(badge, dict) for badge in stored_badges)
        source_badges = parsed_badges if has_legacy_badges and parsed_badges else stored_badges or parsed_badges
        runs = []
        for run in source_runs:
            copied = dict(run)
            if copied.get("type") == "emote":
                copied["url"] = cached_asset(copied.get("url")) or ""
            runs.append(copied)
        badges = []
        for badge in source_badges:
            if isinstance(badge, str):
                copied_badge = {"type": "member", "label": badge, "url": ""}
            elif isinstance(badge, dict):
                copied_badge = dict(badge)
            else:
                continue
            copied_badge["url"] = cached_asset(copied_badge.get("url")) or ""
            badges.append(copied_badge)
        message_text = m.message_text or (parsed_raw or {}).get("message_text", "")
        messages.append({"id":str(m.id),"message_id":m.message_id,"offset_ms":m.offset_ms,
            "time_seconds":max(0, m.offset_ms-recording_offset)/1000,"published_at":m.published_at,
            "author_channel_id":m.author_channel_id,"author_name":m.author_name,"author_badges":badges,"message_text":message_text,
            "superchat_amount":m.superchat_amount,"superchat_currency":m.superchat_currency,"superchat_color":m.superchat_color,
            "message_type":m.message_type,"is_deleted":m.is_deleted,"deleted_at":m.deleted_at,"deletion_source":m.deletion_source,
            "runs":runs,"author":{"name":m.author_name,"role":m.author_role or (parsed_raw or {}).get("author_role", "viewer"),"badges":badges},
            "amount_display":m.amount_display or (parsed_raw or {}).get("amount_display"),
            "currency":m.superchat_currency or (parsed_raw or {}).get("superchat_currency"),
            "amount":m.superchat_amount or (parsed_raw or {}).get("superchat_amount"),
            "color":m.superchat_color or (parsed_raw or {}).get("superchat_color"),
            "header_text":m.header_text or (parsed_raw or {}).get("header_text"),
            "sticker_url":cached_asset(m.sticker_url or (parsed_raw or {}).get("sticker_url"))})
    result = {"total": total, "available_total": available_total, "chat_enabled": bool(video.channel and video.channel.chat_enabled),
        "chat_replay": (video.raw_metadata or {}).get("chat_replay"), "messages": messages,
        "superchat_totals": {currency or "unknown": float(amount or 0) for currency, amount in sums},
        "unknown_amounts": {display: count for display, count in unknown}}
    db.commit()
    return result


@router.get("/chat-assets/{key}")
def chat_asset(key: str, db: Session = Depends(get_db)):
    import re
    from ..config import settings
    from ..services.chat_assets import asset_key, cache_asset
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise HTTPException(404, "画像がありません")
    rows = db.scalars(select(ChatMessage)).all()
    urls = set()
    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.startswith("https://"):
            urls.add(value)
    for message in rows:
        visit(message.message_runs or [])
        visit(message.author_badges or [])
        visit(message.raw or {})
        if message.sticker_url:
            urls.add(message.sticker_url)
    source = next((url for url in urls if asset_key(url) == key), None)
    if not source:
        raise HTTPException(404, "画像がありません")
    # Asset misses perform a network request; release the read session first.
    db.close()
    try:
        path = cache_asset(source, Path(settings.data_dir) / "chat-assets")
    except ValueError:
        raise HTTPException(404, "画像を取得できません")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

@router.get("/videos/{video_id}/chat/danmaku")
def chat_danmaku(video_id: int, include_deleted: bool = False, kind: str | None = None, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if kind == "superchat": stmt = stmt.where(ChatMessage.message_type.in_(("superchat", "supersticker")))
    elif kind == "membership": stmt = stmt.where(ChatMessage.message_type.in_(("membership", "membership_gift", "membership_gift_received")))
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms)).all()
    recording_offset = int((video.raw_metadata or {}).get("recording_start_offset_ms", 0))
    data = [[max(0,m.offset_ms-recording_offset)/1000, "right", m.superchat_color or "#ffffff", m.author_name,
        ("[削除済] " if m.is_deleted else "") + m.message_text] for m in rows]
    db.commit()
    return {"code":0,"recording_offset_ms":recording_offset,"data":data}

@router.get("/videos/{video_id}/chat/danmaku/{filter_key}/v3/")
def dplayer_danmaku(video_id: int, filter_key: str, db: Session = Depends(get_db)):
    """DPlayer v3 danmaku protocol adapter for saved live chat."""
    from re import sub

    video = _video_or_404(db, video_id)
    parts = filter_key.split("-")
    kind = parts[0] if parts and parts[0] in ("all", "superchat", "membership") else "all"
    include_deleted = len(parts) > 1 and parts[1] == "1"
    recording_offset = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0)))
    stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
    if not include_deleted: stmt = stmt.where(ChatMessage.is_deleted.is_(False))
    if kind == "superchat":
        stmt = stmt.where(ChatMessage.message_type.in_(("superchat", "supersticker")))
    elif kind == "membership":
        stmt = stmt.where(ChatMessage.message_type.in_(
            ("membership", "membership_gift", "membership_gift_received")
        ))
    # Comments from before a mid-stream recording have negative local time.
    # Exclude them instead of clamping every one to zero (which creates a
    # large burst at playback start).
    if recording_offset: stmt = stmt.where(ChatMessage.offset_ms >= recording_offset)
    rows = db.scalars(stmt.order_by(ChatMessage.offset_ms)).all()
    # DPlayer 1.27's built-in v3 reader indexes each row as
    # [time, type, color, author, text]. Object rows pass the code check but
    # become undefined fields in that reader, so no danmaku is drawn.
    data = []
    for message in rows:
        text = "".join(run.get("text", "") for run in (message.message_runs or []) if run.get("type") in ("text", "emoji"))
        if not message.message_runs:
            text = message.message_text
        text = sub(r":[^:\s]+:", "", text)
        if message.message_type in ("superchat", "supersticker") and message.amount_display:
            text = f"{message.amount_display} {text}".strip()
        elif message.message_type.startswith("membership") and message.header_text:
            header = sub(
                r"Member for (\d+) months?",
                r"メンバー \1 か月",
                message.header_text,
                flags=2,
            )
            text = f"{header} {text}".strip()
        if not text.strip():
            continue
        if len(text) > 50:
            text = text[:49] + "…"
        if message.is_deleted:
            text = "[削除済] " + text
        data.append([max(0, message.offset_ms-recording_offset)/1000, 0,
                     message.superchat_color or "#ffffff", message.author_name, text])
    db.commit()
    return {"code": 0, "data": data}


@router.post("/videos/{video_id}/chat/reparse")
def reparse_chat(video_id: int, db: Session = Depends(get_db)):
    _video_or_404(db, video_id)
    from ..services.chat import reparse_video_chat
    count = reparse_video_chat(db, video_id)
    db.commit()
    return {"ok": True, "messages_updated": count}

@router.get("/videos/{video_id}/chat/export")
def export_chat(video_id: int, db: Session = Depends(get_db)):
    video = _video_or_404(db, video_id)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id).order_by(ChatMessage.offset_ms)).all()
    result = {"video_id":video.youtube_video_id,"messages":[{"message_id":m.message_id,"offset_ms":m.offset_ms,"published_at":m.published_at,
        "author_channel_id":m.author_channel_id,"author_name":m.author_name,"author_badges":m.author_badges,"message_text":m.message_text,
        "superchat_amount":m.superchat_amount,"superchat_currency":m.superchat_currency,"superchat_color":m.superchat_color,
        "message_type":m.message_type,"raw":m.raw,"first_seen_at":m.first_seen_at,"last_seen_at":m.last_seen_at,
        "is_deleted":m.is_deleted,"deleted_at":m.deleted_at,"deletion_source":m.deletion_source} for m in rows]}
    db.commit()
    return result

@router.post("/videos/{video_id}/chat/retrieve")
def retrieve_chat(video_id: int, db: Session = Depends(get_db)):
    """Fetch and import the optional live-chat replay."""
    video = _video_or_404(db, video_id)
    channel = video.channel
    if channel and not channel.chat_enabled: raise HTTPException(409, "このチャンネルではチャット取得が無効です")
    from ..services.chat import retrieve_replay
    count = retrieve_replay(db, video.id, video.youtube_video_id, channel.cookie_profile if channel else None)
    current = db.get(Video, video.id)
    return {"ok": True, "messages_seen": count, "chat_replay": (current.raw_metadata or {}).get("chat_replay", "unavailable")}

@router.get("/videos/{video_id}/file")
def video_file(video_id: int, request: Request, download: bool = False, playable: bool = False,
               rendition: str = "source", db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path: raise HTTPException(404, "録画ファイルがありません")
    try: candidate = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
    except (ValueError, OSError): raise HTTPException(404, "録画ファイルがありません")
    source = source_media(candidate)
    selected = "remux" if playable and rendition == "source" else rendition
    if selected not in ("source", "remux", "h264"):
        raise HTTPException(422, "rendition が不正です")
    media = source if selected == "source" else output_path(source, selected) if source else None
    if selected != "source" and (media is None or not media.is_file() or media.stat().st_size <= 0):
        raise HTTPException(404, "再生用ファイルがありません")
    if media is None or candidate not in media.parents: raise HTTPException(404, "録画ファイルがありません")
    size = media.stat().st_size
    mime = mimetypes.guess_type(media.name)[0] or "application/octet-stream"
    if selected != "source":
        try:
            os.utime(media, (datetime.now().timestamp(), media.stat().st_mtime))
        except OSError:
            pass
    # The file body can remain open for the duration of a browser playback or
    # download. Do not retain the session or its pooled connection during that stream.
    db.close()
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
            f.seek(start)
            remaining = end - start + 1
            while remaining:
                chunk = f.read(min(1024 * 1024, remaining))
                if not chunk: break
                remaining -= len(chunk)
                yield chunk
    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end-start+1)}
    if code == 206: headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(stream(), status_code=code, media_type=mime, headers=headers)

@router.post("/videos/{video_id}/convert/{mode}")
def convert_video(video_id: int, mode: str, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path or not video.storage: raise HTTPException(404, "録画ファイルがありません")
    try:
        folder = registered_video_directory(video.file_path, video.storage.root_path)
        # Same source as the renditions and /file: the primary media, not whichever file is listed first.
        source = source_media(folder)
        if source is None: raise HTTPException(404, "変換元がありません")
        db.commit()
        return enqueue_conversion(video_id, source, mode, settings.ffmpeg_path)
    except ValueError as exc: raise HTTPException(400, str(exc))

@router.get("/videos/{video_id}/convert/{mode}")
def get_conversion_status(video_id: int, mode: str, db: Session = Depends(get_db)):
    if mode not in ("remux", "h264"): raise HTTPException(404, "変換形式がありません")
    video = db.get(Video, video_id)
    if video and video.file_path:
        try:
            directory = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
            source = source_media(directory)
            if source:
                dest = output_path(source, mode)
                if dest.is_file() and dest.stat().st_size > 0:
                    return {"status": "completed", "path": str(dest), "error": None, "progress": 1.0}
        except (ValueError, OSError):
            pass
    return conversion_status(video_id, mode)

@router.get("/videos/{video_id}/thumbnail")
def video_thumbnail(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video or not video.file_path: raise HTTPException(404, "サムネイルがありません")
    try: folder = registered_video_directory(video.file_path, video.storage.root_path if video.storage else None)
    except (ValueError, OSError): raise HTTPException(404, "サムネイルがありません")
    thumb = next((p.resolve() for p in folder.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}), None)
    if not thumb or folder not in thumb.parents: raise HTTPException(404, "サムネイルがありません")
    db.close()
    return FileResponse(thumb)

@router.post("/videos/{video_id}/protect")
def protect_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    db.commit()
    if video.status not in ("ACTIVE", "DELETE_ELIGIBLE", "PROTECTION_PENDING", "VERIFYING_REMOTE"):
        raise HTTPException(409, "この状態の動画は保護できません")
    video.protected, video.protection_reason, video.protected_at, video.status = True, "MANUAL", now(), "PROTECTED"
    db.add(AuditLog(video_id=video.id, action="PROTECT", reason="MANUAL"))
    db.commit()
    return {"ok": True, "protected": True}

@router.delete("/videos/{video_id}/protect")
def unprotect_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.protection_reason != "MANUAL": raise HTTPException(409, "手動保護だけ解除できます")
    if video.status != "PROTECTED": raise HTTPException(409, "保護状態の動画だけ解除できます")
    video.protected = False
    video.protection_reason = None
    video.protected_at = None
    video.status = "ACTIVE"
    db.add(AuditLog(video_id=video.id, action="UNPROTECT", reason="MANUAL"))
    db.commit()
    return {"ok": True, "protected": False}

@router.delete("/videos/{video_id}")
def delete_video(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status == "RECORDING": raise HTTPException(409, "録画中は削除できません。先に停止してください")
    if video.protected: raise HTTPException(409, "保護を解除してから削除してください")
    if conversion_status(video.id, "remux").get("status") in ("queued", "running") or conversion_status(video.id, "h264").get("status") in ("queued", "running"):
        raise HTTPException(409, "変換処理中は削除できません")
    path = None
    if video.file_path:
        if not Path(video.file_path).exists():
            path = Path(video.file_path)
        elif not video.storage:
            raise HTTPException(409, "登録保存先が無いため削除できません")
        else:
            try: path = registered_video_directory(video.file_path, video.storage.root_path)
            except (ValueError, OSError): raise HTTPException(409, "録画パスが登録保存先の外です")
    import shutil
    if path is not None and path.exists():
        shutil.rmtree(path)
    if video.live_hls_path:
        try:
            hls_root = Path(video.live_hls_path).resolve()
            storage_root = Path(video.storage.root_path).resolve() if video.storage else None
            expected = (storage_root / "live" / video.youtube_video_id).resolve() if storage_root else None
            if hls_root == expected and hls_root.is_dir(): shutil.rmtree(hls_root)
        except OSError: pass
    from ..services.derived import clear_transcript_segments
    clear_transcript_segments(db, video)
    video.status = "DELETED_MANUALLY"
    video.file_path = None
    video.live_hls_path = None
    video.file_size = 0
    db.add(AuditLog(video_id=video.id, action="MANUAL_DELETE", reason="USER_CONFIRMED"))
    db.commit()
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
    db.commit()
    elapsed = max(0, (now() - video.recorded_at).total_seconds()) if video.recorded_at and video.status == "RECORDING" else 0
    return {"file_size": size, "elapsed_seconds": elapsed,
            "heartbeat_at": utc_json_value(video.recording_heartbeat_at),
            "free_bytes": free, "last_error": last_error}

@router.post("/videos/{video_id}/recording/stop")
def stop_video_recording(video_id: int, db: Session = Depends(get_db)):
    video = db.get(Video, video_id)
    if not video: raise HTTPException(404, "動画がありません")
    if video.status != "RECORDING":
        raise HTTPException(409, "実行中の録画プロセスがありません")
    if not stop_recording(video_id):
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
    if video.status not in ("RECORDING_FAILED", "WAITING_FOR_STORAGE") and not missing_or_empty and not video.is_partial:
        raise HTTPException(409, "この録画は再開できません")
    video.status = "DISCOVERED"
    video.recording_finished_at = None
    video.raw_metadata = {key: value for key, value in (video.raw_metadata or {}).items()
                          if key not in ("user_stopped", "recovery_exhausted")}
    video.is_partial = bool(video.file_path and Path(video.file_path).is_dir())
    video.next_recovery_at = None
    # Preserve file_path and any partial evidence. New download output uses the same
    # target; yt-dlp may resume or replace individual outputs as appropriate.
    db.commit()
    return {"ok": True, "status": "DISCOVERED"}
