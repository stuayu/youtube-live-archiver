from .common import *
from ..schemas import YtDlpSettingsIn, YtDlpUpdateIn
from fastapi.responses import JSONResponse
import subprocess
from urllib.parse import urlsplit
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse


def _require_cookie_origin(request: Request) -> None:
    origin = request.headers.get('origin')
    if not origin:
        return
    try:
        source = urlsplit(origin)
        target = request.url
        source_port = source.port or (443 if source.scheme == 'https' else 80)
        target_port = target.port or (443 if target.scheme == 'https' else 80)
        same = (source.scheme in ('http', 'https') and source.scheme == target.scheme
                and source.hostname == target.hostname and source_port == target_port
                and not source.username and not source.password and not source.path
                and not source.query and not source.fragment)
    except ValueError:
        same = False
    if not same:
        raise HTTPException(403, 'Cookie の登録はアプリと同じ接続先から行ってください')


@router.get('/settings/youtube-cookies')
def youtube_cookie_status():
    from ..services import youtube_cookies
    return JSONResponse({'configured': youtube_cookies.configured()}, headers={'Cache-Control': 'no-store'})


@router.put('/settings/youtube-cookies')
async def save_youtube_cookies(request: Request):
    from ..services import youtube_cookies
    _require_cookie_origin(request)
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/octet-stream':
        raise HTTPException(415, 'Cookie ファイルを選択してください')
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > youtube_cookies.MAX_COOKIE_BYTES:
            raise HTTPException(413, 'Cookie ファイルは 1 MB 以下にしてください')
        data.extend(chunk)
    try:
        await run_in_threadpool(youtube_cookies.save, bytes(data))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except (OSError, subprocess.SubprocessError):
        raise HTTPException(500, 'Cookie を安全に保存できませんでした。サーバーの保存先を確認してください') from None
    return JSONResponse({'configured': True}, headers={'Cache-Control': 'no-store'})


@router.delete('/settings/youtube-cookies')
async def delete_youtube_cookies(request: Request):
    from ..services import youtube_cookies
    _require_cookie_origin(request)
    try:
        await run_in_threadpool(youtube_cookies.remove)
    except OSError:
        raise HTTPException(500, 'Cookie を削除できませんでした') from None
    return JSONResponse({'configured': False}, headers={'Cache-Control': 'no-store'})


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
    from ..services import youtube_cookies, ytdlp_update
    return {**ytdlp_update.preferences(db), **ytdlp_update.status(),
            'youtube_cookies_configured': youtube_cookies.configured()}


@router.put("/settings/ytdlp")
def put_ytdlp_settings(data: YtDlpSettingsIn, db: Session = Depends(get_db)):
    from ..services import youtube_cookies, ytdlp_update
    return {**ytdlp_update.save_preferences(db, channel=data.channel, auto_update=data.auto_update),
            'youtube_cookies_configured': youtube_cookies.configured()}


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

def _summary_provider_target(db: Session, values: dict) -> dict:
    """Settings the summary jobs will actually use after applying ``values``.

    Uses the same key precedence as the summary jobs (environment first), and
    never sends a stored key to a base URL that only this request supplied."""
    from ..services.processing import effective_summary_settings
    from ..services.summarization import ProviderCheckError
    current = effective_summary_settings(db)
    entered = str(values.get("openrouter_api_key") or "")
    base_url = str(values.get("openrouter_base_url", current["openrouter_base_url"]) or "")
    if entered and settings.openrouter_api_key:
        raise ProviderCheckError("サーバーの .env に OPENROUTER_API_KEY が設定されているため、画面で登録したキーは"
                                 "使われません。.env から OPENROUTER_API_KEY を削除してサービスを再起動するか、"
                                 ".env のキーを更新してください", field="openrouter_api_key", code="env_key_overrides")
    if not entered and base_url.rstrip("/") != str(current["openrouter_base_url"] or "").rstrip("/"):
        raise ProviderCheckError("API ベース URL を変える場合は、API キーも入力し直してください"
                                 "（登録済みのキーを新しい送信先へ送らないため）",
                                 field="openrouter_api_key", code="key_required_for_new_url")
    saved = db.get(AppSetting, "openrouter_api_key")
    key = settings.openrouter_api_key or entered or (saved.value.get("value", "") if saved else "")
    return {"api_key": str(key or ""),
            "model": str(values.get("openrouter_model", current["openrouter_model"]) or ""),
            "base_url": base_url}


def _provider_check_response(exc) -> JSONResponse:
    fields = {exc.field: str(exc)} if exc.field else {}
    return JSONResponse(status_code=422, content={"error": str(exc), "fields": fields, "code": exc.code})


@router.post("/settings/summary/test")
def test_summary_provider(data: SummarySettingsIn, db: Session = Depends(get_db)):
    """Send a small sample transcript to the provider with the given (or saved) settings."""
    from ..services.summarization import ProviderCheckError, check_provider
    try:
        target = _summary_provider_target(db, data.model_dump(exclude_unset=True))
        db.commit()
        return check_provider(**target)
    except ProviderCheckError as exc:
        db.rollback()
        return _provider_check_response(exc)


@router.put("/settings/summary")
def put_summary_settings(data: SummarySettingsIn, db: Session = Depends(get_db)):
    from ..services.processing import SUMMARY_DEFAULTS, effective_summary_settings
    from ..services.summarization import ProviderCheckError, check_provider
    allowed = set(SUMMARY_DEFAULTS) - {"openrouter_api_key_set"}
    values = data.model_dump(exclude_unset=True)
    if set(values) - allowed: raise HTTPException(422, "不明な設定項目です")
    if not values.get("openrouter_api_key"): values.pop("openrouter_api_key", None)
    # 接続に関わる値を変えたときは、保存前に実際にプロバイダーへテスト送信する。
    current = effective_summary_settings(db)
    connection_changed = bool(values.get("openrouter_api_key")) or any(
        key in values and values[key] != current.get(key) for key in ("openrouter_model", "openrouter_base_url"))
    enabling = values.get("summary_enabled", current["summary_enabled"])
    if connection_changed:
        try:
            target = _summary_provider_target(db, values)
            if target["api_key"] or enabling:
                db.commit()
                check_provider(**target)
        except ProviderCheckError as exc:
            db.rollback()
            return _provider_check_response(exc)
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
