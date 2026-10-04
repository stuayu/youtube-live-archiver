from .common import *

@router.get("/channels")
def channels(include_archived: bool = False, db: Session = Depends(get_db)):
    counts = (select(Video.channel_id.label("channel_id"),
                     func.count(Video.id).filter(Video.recording_finished_at.is_not(None)).label("recording_count"),
                     func.count(Video.id).filter(Video.status == "RECORDING").label("recording_now_count"),
                     func.count(Video.id).filter(Video.status == "WAITING_LIVE").label("upcoming_count"),
                     func.count(Video.id).filter(Video.status.in_(("RECORDING_FAILED", "WAITING_FOR_STORAGE"))).label("failed_count"))
              .group_by(Video.channel_id).subquery())
    query = select(Channel, counts.c.recording_count, counts.c.recording_now_count,
                   counts.c.upcoming_count, counts.c.failed_count).outerjoin(counts, counts.c.channel_id == Channel.id)
    if not include_archived:
        query = query.where(Channel.archived.is_(False))
    result = []
    for ch, recorded, recording, upcoming, failed in db.execute(query.order_by(Channel.name)).all():
        state = "paused" if not ch.enabled else "recording" if (recording or 0) else "error" if ch.consecutive_failures else "pending" if ch.last_checked_at is None else "ok"
        result.append({**ChannelOut.model_validate(ch).model_dump(), "recording_count": recorded or 0,
                       "recording_now_count": recording or 0, "upcoming_count": upcoming or 0,
                       "failed_count": failed or 0, "monitor_state": state})
    db.commit()
    return result

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
    db.commit()
    return utc_json_value({"channel_id": str(channel_id), "from": start, "to": end, "granularity": granularity,
            "points": points, "current": latest, "deltas": period_deltas(rows, end),
            "recordings": {"count": len(recordings), "total_bytes": sum(v.file_size or 0 for v in archived),
                           "protected_count": sum(reasons.values()), "protected_reasons": reasons,
                           "average_duration_seconds": sum(durations) / len(durations) if durations else None}})

@router.post("/channels/preview")
def preview_channel(data: ChannelPreviewIn):
    """Resolve a channel URL and return display name and icon before registration."""
    raw = data.url.strip()
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

@router.post("/channels")
def add_channel(body: ChannelCreate, db: Session = Depends(get_db)):
    from ..services.youtube import cookie_args
    try: cookie_args(body.cookie_profile)
    except ValueError as exc: raise HTTPException(422, str(exc))
    try:
        channel_id, canonical_url = resolve_channel(body.url)
    except Exception as exc:
        raise HTTPException(422, f"チャンネル解決に失敗しました: {exc}")
    ch = db.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id))
    values = body.model_dump(exclude={"url", "name"})
    values.update(youtube_channel_id=channel_id, url=canonical_url)
    for key in ("storage_id", "fallback_storage_id"):
        if values.get(key) is not None and db.get(Storage, values[key]) is None:
            raise HTTPException(422, "保存先がありません")
    if ch and not ch.archived:
        raise HTTPException(409, "チャンネルは登録済みです")
    if ch:
        for key, value in values.items(): setattr(ch, key, value)
        ch.name = body.name or ch.youtube_channel_id
        ch.archived = False
        ch.enabled = True
        ch.next_check_at = None
    else:
        ch = Channel(**values, name=body.name or channel_id)
        db.add(ch)
    db.commit()
    db.refresh(ch)
    refresh_channel_metadata(ch)
    db.commit()
    db.refresh(ch)
    aggregates = db.execute(select(
        func.count(Video.id).filter(Video.recording_finished_at.is_not(None)),
        func.count(Video.id).filter(Video.status == "RECORDING"),
        func.count(Video.id).filter(Video.status == "WAITING_LIVE"),
        func.count(Video.id).filter(Video.status.in_(("RECORDING_FAILED", "WAITING_FOR_STORAGE"))),
    ).where(Video.channel_id == ch.id)).one()
    recording, recording_now, upcoming, failed = aggregates
    state = "paused" if not ch.enabled else "recording" if recording_now else "error" if ch.consecutive_failures else "pending" if ch.last_checked_at is None else "ok"
    return {**ChannelOut.model_validate(ch).model_dump(), "recording_count": recording or 0,
            "recording_now_count": recording_now or 0, "upcoming_count": upcoming or 0,
            "failed_count": failed or 0, "monitor_state": state}

@router.post("/channels/{channel_id}/refresh-metadata", response_model=ChannelOut)
def refresh_metadata(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    db.commit()
    refresh_channel_metadata(ch)
    db.commit()
    db.refresh(ch)
    return ch

@router.get("/channels/{channel_id}/avatar")
def channel_avatar(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch or not ch.avatar_cache_path: raise HTTPException(404, "アイコンがありません")
    path = Path(ch.avatar_cache_path).resolve()
    root = (Path(settings.data_dir).resolve() / "channel-icons").resolve()
    if root not in path.parents or not path.is_file(): raise HTTPException(404, "アイコンがありません")
    db.close()
    return FileResponse(path)

@router.put("/channels/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: int, body: ChannelPatch, apply_retention_to_existing: bool = False, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    return _patch_channel(ch, body, apply_retention_to_existing, db)

@router.patch("/channels/{channel_id}", response_model=ChannelOut)
def patch_channel(channel_id: int, body: ChannelPatch, apply_retention_to_existing: bool = False, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    return _patch_channel(ch, body, apply_retention_to_existing, db)

@router.post("/channels/{channel_id}/check-now")
def check_channel_now(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    ch.next_check_at = now()
    db.commit()
    return {"ok": True, "next_check_at": utc_json_value(ch.next_check_at)}

@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    ch = db.get(Channel, channel_id)
    if not ch: raise HTTPException(404, "チャンネルがありません")
    ch.enabled = False
    ch.archived = True
    db.commit()
    return {"ok": True}
