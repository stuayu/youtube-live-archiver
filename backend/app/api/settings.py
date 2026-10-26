from .common import *
from ..schemas import YtDlpSettingsIn, YtDlpUpdateIn


@router.get("/settings/dependencies")
def get_dependency_update_status():
    from ..services import dependency_update
    return dependency_update.status()


@router.post("/settings/dependencies/update")
def update_dependencies():
    from ..services import dependency_update
    try:
        started = dependency_update.start_update()
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not started:
        raise HTTPException(409, "依存関係の更新はすでに実行中です")
    return {"started": True}


@router.post("/settings/dependencies/cancel")
def cancel_dependency_update():
    from ..services import dependency_update
    if not dependency_update.cancel_update():
        raise HTTPException(409, "切り替え中の更新はキャンセルできません")
    return {"cancelled": True}


@router.get("/settings/ytdlp")
def get_ytdlp_settings(db: Session = Depends(get_db)):
    from ..services import ytdlp_update
    return {**ytdlp_update.preferences(db), **ytdlp_update.status()}


@router.put("/settings/ytdlp")
def put_ytdlp_settings(data: YtDlpSettingsIn, db: Session = Depends(get_db)):
    from ..services import ytdlp_update
    return ytdlp_update.save_preferences(db, channel=data.channel, auto_update=data.auto_update)


@router.post("/settings/ytdlp/update")
def update_ytdlp(data: YtDlpUpdateIn):
    from ..services import ytdlp_update
    channel = ytdlp_update.preferences()["channel"]
    try:
        started = ytdlp_update.trigger_update(channel, force=data.force)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not started:
        raise HTTPException(409, "yt-dlp の更新処理は既に実行中です")
    return {"started": True, "force": data.force}

@router.get("/settings/transcription")
def get_transcription_settings(db: Session = Depends(get_db)):
    from ..services.processing import effective_settings
    result = effective_settings(db)
    db.commit()
    return result

@router.put("/settings/transcription")
def put_transcription_settings(data: TranscriptionSettingsIn, db: Session = Depends(get_db)):
    from ..services.processing import TRANSCRIPTION_DEFAULTS
    allowed = set(TRANSCRIPTION_DEFAULTS)
    values = data.model_dump(exclude_unset=True)
    if set(values) - allowed:
        raise HTTPException(422, "不明な設定項目です")
    for key, value in values.items():
        row = db.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value={"value": value})
            db.add(row)
        else:
            row.value = {"value": value}
    db.commit()
    return {**TRANSCRIPTION_DEFAULTS, **{row.key: row.value["value"] for row in db.query(AppSetting).all() if row.key in allowed}}

@router.get("/settings/summary")
def get_summary_settings(db: Session = Depends(get_db)):
    from ..services.processing import effective_summary_settings
    result = effective_summary_settings(db)
    db.commit()
    return result

@router.put("/settings/summary")
def put_summary_settings(data: SummarySettingsIn, db: Session = Depends(get_db)):
    from ..services.processing import SUMMARY_DEFAULTS, effective_summary_settings
    allowed = set(SUMMARY_DEFAULTS) - {"openrouter_api_key_set"}
    values = data.model_dump(exclude_unset=True)
    if set(values) - allowed: raise HTTPException(422, "不明な設定項目です")
    for key, value in values.items():
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

@router.get("/settings/recording")
def get_recording_settings():
    return {"max_concurrent_live_recordings": settings.max_concurrent_live_recordings,
            "max_concurrent_archive_downloads": settings.max_concurrent_archive_downloads,
            "monitor_concurrency": settings.monitor_concurrency, "downloader": settings.downloader,
            "default_quality": settings.archive_quality, "live_quality": settings.live_quality,
            "archive_quality": settings.archive_quality, "yt_dlp_path": settings.yt_dlp_path,
            "ffmpeg_path": settings.ffmpeg_path}
