"""YouTube 側で編集されたアーカイブの「どこが変わったか」を調べるジョブ。

保存判定 (workers.verify_video) は長さと数点のサンプルで編集の有無だけを見る。
ここでは現行アーカイブの音声を丸ごと取得し、録画と突き合わせて区間を特定する。
結果は ProcessingJob.payload["result"] に保存し、再生画面で表示する。
"""
from datetime import timedelta
import logging
import tempfile
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from ..db import SessionLocal
from ..models import ProcessingJob, Video, now
from .download import downloader_args
from .edit_diff import analyze, audio_envelope
from .locks import default_locks
from .media import source_media
from .youtube import run_ytdlp, run_ytdlp_with_cookies

log = logging.getLogger(__name__)

KIND = "EDIT_ANALYSIS"
ACTIVE_STATUSES = ("pending", "processing", "retry_wait")
DOWNLOAD_TIMEOUT_SECONDS = 3600


def enqueue(db, video_id: int, *, force: bool = False) -> ProcessingJob:
    """呼び出し側で commit する。保存判定のトランザクション内からも呼ぶため。"""
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == KIND))
    if job is None:
        job = ProcessingJob(video_id=video_id, kind=KIND, status="pending", attempts=0, payload={},
                            created_at=now(), updated_at=now())
        db.add(job)
        db.flush()
        return job
    if force and job.status != "processing":
        job.status, job.attempts, job.error = "pending", 0, None
        job.next_attempt_at, job.finished_at, job.updated_at = None, None, now()
    return job


def view(db, video_id: int) -> dict:
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == KIND))
    if job is None:
        return {"status": "none", "error": None, "result": None, "updated_at": None}
    return {"status": job.status, "error": job.error, "result": (job.payload or {}).get("result"),
            "updated_at": job.updated_at.isoformat() if job.updated_at else None}


def local_envelope(video: Video):
    path = Path(video.file_path or "")
    media = source_media(path) if path.is_dir() else path
    if not media or not media.is_file():
        raise FileNotFoundError("録画ファイルがありません")
    return audio_envelope(media)


def remote_envelope(video: Video):
    """現行アーカイブの音声だけを取得する。映像は不要なので最小の音声形式を選ぶ。"""
    url = f"https://www.youtube.com/watch?v={video.youtube_video_id}"
    with tempfile.TemporaryDirectory(prefix="archiver-edit-") as temp:
        args = ["--no-warnings", "--no-playlist", "-f", "wa/ba", "-o", str(Path(temp) / "remote.%(ext)s"),
                *downloader_args(), url]
        cookie_profile = video.channel.cookie_profile if video.channel else None
        proc = run_ytdlp_with_cookies(args, cookie_profile, timeout=DOWNLOAD_TIMEOUT_SECONDS, text=False)
        if proc is None:
            proc = run_ytdlp(args, capture_output=True, timeout=DOWNLOAD_TIMEOUT_SECONDS, check=False)
        media = next((p for p in Path(temp).glob("remote.*") if p.is_file() and not p.name.endswith(".part")), None)
        if proc.returncode or media is None:
            stderr = proc.stderr.decode(errors="replace") if isinstance(proc.stderr, bytes) else (proc.stderr or "")
            raise RuntimeError(stderr.strip()[-1000:] or "YouTube の音声を取得できませんでした")
        return audio_envelope(media)


def process(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            return
        video = db.get(Video, job.video_id)
        if not video or not video.file_path or video.status.startswith("DELETED"):
            job.status, job.error, job.finished_at = "disabled", "録画がありません", now()
            db.commit()
            return
        job.status, job.attempts, job.error = "processing", job.attempts + 1, None
        job.started_at, job.next_attempt_at, job.updated_at = now(), None, now()
        db.commit()
        # 数十分かかる取得の間、トランザクションを開いたままにしない。
        local = local_envelope(video)
        remote = remote_envelope(video)
        db.commit()
        result = analyze(local, remote)
        job = db.get(ProcessingJob, job_id)
        job.payload = {**(job.payload or {}), "result": result, "analyzed_at": now().isoformat()}
        job.status, job.error, job.finished_at, job.updated_at = "completed", None, now(), now()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job:
            job.error, job.updated_at = str(exc)[:2000], now()
            if job.attempts >= max(1, settings.processing_max_attempts):
                job.status, job.finished_at = "failed", now()
            else:
                delay = min(86400, 300 * (2 ** max(0, job.attempts - 1)))
                job.status, job.next_attempt_at = "retry_wait", now() + timedelta(seconds=delay)
            db.commit()
        log.warning("edit analysis job %s failed: %s", job_id, type(exc).__name__)
    finally:
        db.close()


def worker() -> None:
    """1 件ずつ処理する。音声全体の取得は帯域を使うため並列にしない。"""
    with SessionLocal() as db:
        stale = db.scalars(select(ProcessingJob.id).where(ProcessingJob.kind == KIND,
                                                          ProcessingJob.status == "processing")).all()
    for job_id in stale:
        # ロックを取れた = 担当プロセスが再起動などで消えている。
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if not lease.acquired:
                continue
            with SessionLocal() as db:
                job = db.get(ProcessingJob, job_id)
                if job and job.status == "processing":
                    job.status, job.next_attempt_at, job.error = "retry_wait", now(), "worker interrupted; retrying"
                    db.commit()
    with SessionLocal() as db:
        due = db.scalar(select(ProcessingJob.id).where(
            ProcessingJob.kind == KIND,
            (ProcessingJob.status == "pending")
            | ((ProcessingJob.status == "retry_wait") & (ProcessingJob.next_attempt_at <= now())),
        ).order_by(ProcessingJob.created_at.asc(), ProcessingJob.id.asc()).limit(1))
    if due is None:
        return
    with default_locks.acquire("processing-job", str(due)) as lease:
        if lease.acquired:
            process(due)
