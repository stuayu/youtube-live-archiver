from .common import *

@router.get("/status")
def api_status(db: Session = Depends(get_db)):
    import shutil
    import subprocess
    from .. import main as main_module
    from ..services import monitor as monitor_service
    from ..services.recorder import running_counts
    database_ok = True
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db.commit()
    except Exception:
        db.rollback()
        database_ok = False
        from fastapi import HTTPException
        raise HTTPException(status_code=503, detail="データベースに接続できません")
    with main_module._job_lock:
        job_stats = {key: dict(value) for key, value in main_module._job_stats.items()}
    scheduler_jobs = []
    for job in main_module.scheduler.get_jobs():
        stats = job_stats.get(job.id, {})
        scheduler_jobs.append({"id": job.id, "next_run_at": job.next_run_time,
                               **{key: stats.get(key, 0 if key in ("run_count", "error_count") else None)
                                  for key in ("last_started_at", "last_finished_at", "last_duration_ms", "last_error", "run_count", "error_count")}})
    tools = main_module._tool_cache
    if tools is None:
        from ..services.youtube import run_ytdlp
        try:
            result = run_ytdlp(["--version"], capture_output=True, text=True, timeout=10, check=False)
            tools = {"yt_dlp_available": result.returncode == 0,
                     "yt_dlp_version": (result.stdout or "").strip() if result.returncode == 0 else None,
                     "yt_dlp_error": (result.stderr or "version query failed")[-500:] if result.returncode else None}
        except Exception as exc:
            tools = {"yt_dlp_available": False, "yt_dlp_version": None, "yt_dlp_error": str(exc)[:500]}
        tools.update(ffmpeg_available=bool(shutil.which(settings.ffmpeg_path) or Path(settings.ffmpeg_path).is_file()),
                     ffprobe_available=bool(shutil.which(settings.ffprobe_path) or Path(settings.ffprobe_path).is_file()))
        main_module._tool_cache = tools
    active_channels = db.scalar(select(func.count(Channel.id)).where(Channel.enabled.is_(True), Channel.archived.is_(False))) or 0
    error_channels = db.scalar(select(func.count(Channel.id)).where(Channel.enabled.is_(True), Channel.consecutive_failures > 0)) or 0
    oldest_success = db.scalar(select(func.min(Channel.last_success_at)).where(Channel.enabled.is_(True), Channel.archived.is_(False)))
    with monitor_service._cooldown_lock:
        cooldown_until = monitor_service._cooldown_until
        cooldown_seconds = max(0, int((cooldown_until - now()).total_seconds())) if cooldown_until else 0
    recordings = db.execute(select(Video, Channel.name).join(Channel, Channel.id == Video.channel_id).where(Video.status == "RECORDING").order_by(Video.recorded_at)).all()
    live_slots, archive_slots = running_counts()
    waiting_count = db.scalar(select(func.count(Video.id)).where(Video.status.in_(("DISCOVERED", "WAITING_FOR_STORAGE", "WAITING_LIVE")))) or 0
    state_counts = {status: count for status, count in db.execute(select(Video.status, func.count(Video.id)).group_by(Video.status)).all()}
    storage_rows = db.scalars(select(Storage).order_by(Storage.name)).all()
    unavailable = []
    low_space = []
    warning_space = []
    for storage in storage_rows:
        root = Path(storage.root_path)
        if not root.exists() or not root.is_dir():
            unavailable.append(storage.name)
            continue
        try:
            import shutil as _shutil
            free = _shutil.disk_usage(root).free
            if free < storage.minimum_free_bytes: low_space.append(storage.name)
            elif free < storage.warning_free_bytes: warning_space.append(storage.name)
        except OSError:
            unavailable.append(storage.name)
    attention = []
    if not storage_rows:
        attention.append({"level": "warning", "code": "storage_missing", "message": "保存先が登録されていません"})
    for name in unavailable: attention.append({"level": "error", "code": "storage_unreachable", "message": f"保存先 {name} に到達できません"})
    for name in low_space: attention.append({"level": "warning", "code": "storage_low_space", "message": f"保存先 {name} の空き容量が不足しています"})
    for name in warning_space: attention.append({"level": "warning", "code": "storage_warning_space", "message": f"保存先 {name} の空き容量が警告しきい値を下回っています"})
    if error_channels: attention.append({"level": "warning", "code": "monitor_errors", "message": f"{error_channels} チャンネルで監視エラー"})
    if not tools["yt_dlp_available"]: attention.append({"level": "error", "code": "yt_dlp_missing", "message": "yt-dlp が見つかりません"})
    if not tools["ffmpeg_available"]: attention.append({"level": "error", "code": "ffmpeg_missing", "message": "ffmpeg が見つかりません"})
    if not tools["ffprobe_available"]: attention.append({"level": "error", "code": "ffprobe_missing", "message": "ffprobe が見つかりません"})
    failed_count = state_counts.get("RECORDING_FAILED", 0) + state_counts.get("WAITING_FOR_STORAGE", 0)
    if failed_count: attention.append({"level": "warning", "code": "recording_failures", "message": f"録画失敗 {failed_count} 件"})
    db.commit()
    from ..database import embedded_data_dir, is_embedded_database
    embedded = is_embedded_database()
    database = {"available": database_ok, "mode": "embedded" if embedded else "external"}
    if embedded:
        database["data_dir"] = str(embedded_data_dir())
    return utc_json_value({"database": database,
            "scheduler": {"running": main_module.scheduler.running, "jobs": scheduler_jobs},
            "tools": tools,
            "monitor": {"enabled_channel_count": active_channels, "error_channel_count": error_channels,
                        "cooldown_remaining_seconds": cooldown_seconds, "oldest_last_success_at": oldest_success},
            "recordings": {"count": len(recordings), "items": [{"video_id": str(v.id), "youtube_video_id": v.youtube_video_id,
                "title": v.title, "channel_name": name, "started_at": v.recorded_at, "heartbeat_at": v.recording_heartbeat_at} for v, name in recordings],
                "waiting_count": waiting_count, "live_slots_used": live_slots,
                "live_slots_limit": settings.max_concurrent_live_recordings, "archive_slots_used": archive_slots,
                "archive_slots_limit": settings.max_concurrent_archive_downloads},
            "counts": state_counts,
            "storages": {"unreachable": unavailable, "low_space": low_space, "warning_space": warning_space}, "attention": attention})

@router.get("/events")
def api_events(limit: int = Query(default=50, ge=1, le=200), before_id: int | None = None,
               channel_id: int | None = None, video_id: int | None = None,
               action: list[str] = Query(default=[]), db: Session = Depends(get_db)):
    query = (select(AuditLog, Video.title, Channel.name).outerjoin(Video, Video.id == AuditLog.video_id)
             .outerjoin(Channel, Channel.id == func.coalesce(AuditLog.channel_id, Video.channel_id)))
    if before_id is not None: query = query.where(AuditLog.id < before_id)
    if channel_id is not None: query = query.where(or_(AuditLog.channel_id == channel_id, Video.channel_id == channel_id))
    if video_id is not None: query = query.where(AuditLog.video_id == video_id)
    if action: query = query.where(AuditLog.action.in_(action))
    rows = db.execute(query.order_by(AuditLog.id.desc()).limit(limit)).all()
    result = [{"id": str(log.id), "video_id": str(log.video_id) if log.video_id is not None else None,
             "channel_id": str(log.channel_id) if log.channel_id is not None else None,
             "action": log.action, "reason": log.reason, "details": log.details,
             "created_at": utc_json_value(log.created_at), "video_title": title, "channel_name": name}
            for log, title, name in rows]
    db.commit()
    return result

@router.get("/formats/{video_id}")
def list_video_formats(video_id: str, db: Session = Depends(get_db)):
    """Explicit user-triggered yt-dlp --list-formats request."""
    from ..services.youtube import cookie_args, run_ytdlp
    import subprocess
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        raise HTTPException(422, "video_id が不正です")
    video = db.scalar(select(Video).where(Video.youtube_video_id == video_id))
    channel = video.channel if video else None
    db.commit()
    try:
        result = run_ytdlp(["--list-formats", "--no-warnings", *cookie_args(channel.cookie_profile if channel else None), f"https://youtube.com/watch?v={video_id}"], capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    except Exception as exc:
        raise HTTPException(502, f"フォーマット取得に失敗しました: {exc}")
    output = result.stdout or result.stderr
    if result.returncode:
        raise HTTPException(502, output[-4000:] or "yt-dlp --list-formats failed")
    return {"video_id": video_id, "formats": output[-20000:]}
