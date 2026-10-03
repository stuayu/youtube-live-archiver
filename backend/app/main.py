from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.background import BackgroundScheduler
from .api import router
from .workers import channel_monitor, recorder_worker, retention_worker, protection_worker, live_detector, remote_archive_inspector, recovery_worker
from .services.processing import processing_worker

scheduler = BackgroundScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        recovery_worker()
    except Exception:
        import logging
        logging.getLogger(__name__).exception("startup recording recovery scan failed; scheduler will retry")
    scheduler.add_job(channel_monitor, "interval", seconds=5, id="channel-monitor", max_instances=1, coalesce=True)
    scheduler.add_job(live_detector, "interval", seconds=15, id="live-detector", max_instances=1)
    scheduler.add_job(recorder_worker, "interval", seconds=20, id="recorder", max_instances=1)
    scheduler.add_job(remote_archive_inspector, "interval", minutes=10, id="remote-inspector", max_instances=1)
    scheduler.add_job(retention_worker, "interval", minutes=15, id="retention", max_instances=1)
    scheduler.add_job(protection_worker, "interval", hours=6, id="protection", max_instances=1)
    scheduler.add_job(recovery_worker, "interval", minutes=1, id="recording-recovery", max_instances=1, coalesce=True)
    scheduler.add_job(processing_worker, "interval", seconds=10, id="processing-queue", max_instances=1, coalesce=True)
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="YouTube Live Archiver", lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
async def api_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": str(exc.detail)}, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    message = "; ".join(str(item.get("msg", "invalid input")) for item in exc.errors())
    return JSONResponse(status_code=422, content={"error": message or "invalid input"})
# Tailnet 越しのアクセスを許可するため、オリジン制限は行わない（プライベートネットワーク前提）。
app.add_middleware(CORSMiddleware, allow_origin_regex=".*", allow_methods=["*"], allow_headers=["*"])
app.include_router(router)


@app.get("/health")
def health(): return {"status": "ok"}


# frontend をビルド済みの場合は同一プロセスから配信する（単一サービスで常駐させるため）。
_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _dist.is_dir():
    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
