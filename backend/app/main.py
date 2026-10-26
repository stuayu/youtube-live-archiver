from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.encoders import jsonable_encoder
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
import logging
from .config import configure_logging

configure_logging()
from .api import router
from .workers import (channel_monitor, recorder_worker, retention_worker, protection_worker,
                      recovery_worker, integrity_worker, derived_cleanup_worker, manual_video_worker)
from .services.processing import processing_worker
from .services.chat import chat_replay_worker

scheduler = BackgroundScheduler(timezone="UTC")
import shutil
import subprocess
import threading
import re
from datetime import datetime, timezone

_job_lock = threading.Lock()
_job_stats: dict[str, dict] = {}
_mutation_gate = threading.Lock()
_active_mutations = 0
_tool_cache: dict | None = None
_job_intervals = {"channel-monitor": 5, "recorder": 5, "manual-video": 60,
                  "retention": 900, "derived-cleanup": 86400, "protection": 21600,
                  "recording-recovery": 60, "recording-integrity": 1800,
                  "processing-queue": 10, "chat-replay": 60, "yt-dlp-version": 86400}


def _tracked_job(job_id: str, callback):
    interval_seconds = _job_intervals.get(job_id, 0)
    def run():
        from .services.dependency_update import maintenance_requested
        if maintenance_requested():
            return None
        started = datetime.now(timezone.utc)
        with _job_lock:
            item = _job_stats.setdefault(job_id, {"run_count": 0, "error_count": 0})
            previous_started = item.get("last_started_at")
            expected = (previous_started.timestamp() + interval_seconds
                        if previous_started and interval_seconds else started.timestamp())
            item["last_delay_ms"] = max(0, int((started.timestamp() - expected) * 1000))
            item["last_started_at"] = started
            item["run_count"] += 1
        try:
            result = callback()
            with _job_lock:
                item["last_error"] = None
                item["last_success_at"] = datetime.now(timezone.utc)
            return result
        except Exception as exc:
            with _job_lock:
                item["error_count"] += 1
                item["last_error"] = str(exc)
            logging.getLogger(__name__).exception("scheduler job %s failed", job_id)
            raise
        finally:
            finished = datetime.now(timezone.utc)
            with _job_lock:
                item["last_finished_at"] = finished
                item["last_duration_ms"] = int((finished - started).total_seconds() * 1000)
    run.__name__ = f"tracked_{job_id.replace('-', '_')}"
    return run


def active_mutation_count() -> int:
    with _mutation_gate:
        return _active_mutations


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .config import settings
    from .services.youtube_ip import start_probe
    start_probe()
    try:
        from .db import SessionLocal
        from .models import Video
        from .services.transcode import (
            cleanup_old_derived_files,
            cleanup_temporary_files,
            resume as resume_conversions,
        )
        from sqlalchemy import select
        resume_conversions()
        with SessionLocal() as db:
            paths = db.scalars(select(Video.file_path).where(Video.file_path.is_not(None))).all()
        cleanup_temporary_files([Path(value) for value in paths if value])
        try:
            if not cleanup_old_derived_files():
                logging.getLogger(__name__).warning(
                    "derived format cleanup incomplete; it will retry on next startup"
                )
        except Exception:
            logging.getLogger(__name__).exception("derived format upgrade cleanup failed")
    except Exception:
        logging.getLogger(__name__).exception("derived temp cleanup failed")
    if settings.scheduler_enabled:
        try:
            from .services.recorder import cleanup_orphan_processes
            cleanup_orphan_processes()
        except Exception:
            logging.getLogger(__name__).exception("orphan recorder cleanup failed")
        try:
            from .workers import _recover_stale_recordings
            _recover_stale_recordings()
        except Exception:
            logging.getLogger(__name__).exception("startup recording recovery scan failed; scheduler will retry")
        scheduler.add_job(_tracked_job("channel-monitor", lambda: channel_monitor(background=True)), "interval", seconds=5,
                          id="channel-monitor", max_instances=1, coalesce=True, misfire_grace_time=3)
        scheduler.add_job(_tracked_job("manual-video", lambda: manual_video_worker(background=True)), "interval", seconds=60,
                          id="manual-video", max_instances=1, coalesce=True, misfire_grace_time=10)
        scheduler.add_job(_tracked_job("recorder", recorder_worker), "interval", seconds=5, id="recorder", max_instances=1, coalesce=True, misfire_grace_time=3)
        scheduler.add_job(_tracked_job("retention", retention_worker), "interval", minutes=15,
                          id="retention", max_instances=1, coalesce=True, misfire_grace_time=60)
        scheduler.add_job(_tracked_job("derived-cleanup", derived_cleanup_worker), "interval", hours=24,
                          id="derived-cleanup", max_instances=1, coalesce=True, misfire_grace_time=300)
        scheduler.add_job(_tracked_job("protection", protection_worker), "interval", hours=6,
                          id="protection", max_instances=1, coalesce=True, misfire_grace_time=300)
        scheduler.add_job(_tracked_job("recording-recovery", recovery_worker), "interval", minutes=1, id="recording-recovery", max_instances=1, coalesce=True,
                          next_run_time=datetime.now(timezone.utc), misfire_grace_time=10)
        scheduler.add_job(_tracked_job("recording-integrity", integrity_worker), "interval", minutes=30,
                          id="recording-integrity", max_instances=1, coalesce=True, misfire_grace_time=60)
        scheduler.add_job(_tracked_job("processing-queue", processing_worker), "interval", seconds=10,
                          id="processing-queue", max_instances=1, coalesce=True, misfire_grace_time=5)
        scheduler.add_job(_tracked_job("chat-replay", chat_replay_worker), "interval", seconds=60,
                          id="chat-replay", max_instances=1, coalesce=True, misfire_grace_time=10)
        from .services.ytdlp_update import check_version
        scheduler.add_job(_tracked_job("yt-dlp-version", check_version), "interval", hours=24,
                          id="yt-dlp-version", max_instances=1, coalesce=True,
                          misfire_grace_time=300, next_run_time=datetime.now(timezone.utc))
        scheduler.start()
    yield
    try:
        from .services.transcode import shutdown as shutdown_conversions
        shutdown_conversions()
        if not settings.scheduler_enabled:
            return
        if scheduler.running:
            scheduler.shutdown(wait=False)
        from .services.recorder import shutdown as shutdown_recorders
        from .db import SessionLocal
        from .models import Video
        from sqlalchemy import select
        from .workers import stop_recording
        unfinished = shutdown_recorders(lambda video_id: stop_recording(video_id, user_requested=False),
                                        settings.recorder_shutdown_timeout_seconds)
        if unfinished:
            with SessionLocal() as db:
                rows = db.scalars(select(Video).where(Video.id.in_(unfinished))).all()
                for video in rows:
                    video.status = "RECORDING_FAILED"
                    video.recording_heartbeat_at = None
                    video.next_recovery_at = None
                db.commit()
    finally:
        from .database import stop_embedded_postgres
        from .db import engine
        engine.dispose()
        stop_embedded_postgres()


app = FastAPI(title="YouTube Live Archiver", lifespan=lifespan)


@app.middleware("http")
async def pause_mutations_during_dependency_switch(request: Request, call_next):
    from .services.dependency_update import maintenance_requested
    global _active_mutations
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return await call_next(request)
    path = request.url.path
    is_cancel = path.endswith("/settings/dependencies/cancel")
    is_recording_stop = path.endswith("/recording/stop")
    tracked = False
    with _mutation_gate:
        phase = None
        if maintenance_requested():
            from .services.dependency_update import _read_state
            phase = _read_state().get("phase")
        allowed_during_wait = phase == "waiting_for_idle" and (is_cancel or is_recording_stop)
        if phase and not allowed_during_wait:
            return JSONResponse(status_code=503,
                                content={"error": "依存関係の更新準備中です。しばらくしてから再試行してください"},
                                headers={"Retry-After": "10"})
        _active_mutations += 1
        tracked = True
    try:
        return await call_next(request)
    finally:
        if tracked:
            with _mutation_gate:
                _active_mutations -= 1


class UTCJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        def to_utc(value):
            normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
            return normalized.isoformat()

        encoded = jsonable_encoder(content, custom_encoder={datetime: to_utc})

        def normalize(value):
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})", value):
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    return value
                return to_utc(parsed)
            return value

        return super().render(normalize(encoded))


app.router.default_response_class = UTCJSONResponse


@app.exception_handler(StarletteHTTPException)
async def api_error(request: Request, exc: StarletteHTTPException):
    if isinstance(exc.detail, dict) and "fields" in exc.detail:
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail, headers=exc.headers)
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    def message_for(item):
        error_type = item.get("type", "")
        context = item.get("ctx") or {}
        if error_type in ("int_parsing", "int_type"):
            return "整数で入力してください"
        if error_type in ("float_parsing", "float_type"):
            return "数値で入力してください"
        if error_type in ("bool_parsing", "bool_type"):
            return "真偽値で入力してください"
        if error_type in ("string_type", "string_sub_type"):
            return "文字列で入力してください"
        if error_type == "extra_forbidden":
            return "不明な項目です"
        if error_type == "missing":
            return "必須です"
        if error_type in ("greater_than_equal", "greater_than"):
            return f"{context.get('ge', context.get('gt'))} 以上で入力してください"
        if error_type in ("less_than_equal", "less_than"):
            return f"{context.get('le', context.get('lt'))} 以下で入力してください"
        if error_type in ("literal_error", "enum"):
            return "選択肢にありません"
        if error_type == "string_too_short":
            return f"{context.get('min_length', 1)} 文字以上で入力してください"
        if error_type == "string_too_long":
            return f"{context.get('max_length', '')} 文字以下で入力してください"
        raw = str(item.get("msg", "入力内容が不正です"))
        if raw.startswith("Value error, "):
            raw = raw.removeprefix("Value error, ")
        return raw

    fields = {}
    for item in exc.errors():
        location = [str(part) for part in item.get("loc", ()) if part not in ("body", "query", "path", "header")]
        field = ".".join(location) or "入力"
        fields[field] = message_for(item)
    first = next(iter(fields.values()), "入力内容を確認してください")
    summary = first if len(fields) == 1 else "入力内容を確認してください"
    return JSONResponse(status_code=422, content={"error": summary, "fields": fields})


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    logging.getLogger(__name__).exception("database integrity conflict", exc_info=exc)
    return JSONResponse(status_code=409, content={"error": "既に登録済みのデータです"})


@app.exception_handler(SQLAlchemyError)
async def database_unavailable(request: Request, exc: SQLAlchemyError):
    reason = (str(getattr(exc, "orig", None) or exc).splitlines() or [""])[0][:300]
    logging.getLogger(__name__).warning("database request failed: %s: %s", exc.__class__.__name__, reason)
    return JSONResponse(status_code=503, content={"error": "データベースに接続できません"})


@app.exception_handler(Exception)
async def internal_error(request: Request, exc: Exception):
    logging.getLogger(__name__).exception("unhandled API exception", exc_info=exc)
    return JSONResponse(status_code=500, content={"error": "内部エラーが発生しました"})
# Tailnet 越しのアクセスを許可するため、オリジン制限は行わない（プライベートネットワーク前提）。
app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health():
    from .services.dependency_update import runtime_id
    return {"status": "ok", "runtime_id": runtime_id()}


# frontend をビルド済みの場合は同一プロセスから配信する（単一サービスで常駐させるため）。
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@app.get("/", include_in_schema=False)
def frontend_index():
    if not _dist.is_dir() or not (_dist / "index.html").is_file():
        message = "画面のビルドがありません。`cd frontend && npm ci && npm run build` を実行してください"
        return HTMLResponse(f"<!doctype html><html lang='ja'><meta charset='utf-8'><title>画面のビルドがありません</title><body><h1>画面のビルドがありません</h1><p>{message}</p></body></html>", status_code=503)
    return FileResponse(_dist / "index.html")


if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
