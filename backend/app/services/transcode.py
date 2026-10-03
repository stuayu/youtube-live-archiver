"""Safe on-demand media remux/transcode queue; source media is never modified."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import threading
from .ffmpeg import run_ffmpeg

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-convert")
_guard = threading.Lock()
_jobs: dict[tuple[int, str], dict] = {}


def output_path(source: Path, mode: str) -> Path:
    return source.with_name(source.stem + (".playable.mp4" if mode == "remux" else ".h264.mp4"))


def _run(source: Path, dest: Path, mode: str, ffmpeg: str):
    temp = dest.with_name(dest.stem + ".tmp.mp4")
    try:
        args = [ffmpeg, "-y", "-i", str(source)]
        args += ["-c", "copy", "-movflags", "+faststart"] if mode == "remux" else ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac", "-movflags", "+faststart"]
        args.append(str(temp))
        result = run_ffmpeg(args, text=True)
        if result.returncode or not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError((result.stderr or "ffmpeg failed")[-2000:])
        os.replace(temp, dest)
        return {"status": "completed", "path": str(dest), "error": None}
    except Exception as exc:
        temp.unlink(missing_ok=True)
        return {"status": "failed", "path": None, "error": str(exc)}


def enqueue(video_id: int, source: Path, mode: str, ffmpeg: str):
    if mode not in ("remux", "h264"):
        raise ValueError("unknown conversion mode")
    key = (video_id, mode)
    dest = output_path(source, mode)
    with _guard:
        if dest.is_file() and dest.stat().st_size:
            return {"status": "completed", "path": str(dest), "error": None}
        current = _jobs.get(key)
        if current and current["status"] in ("queued", "running"):
            return dict(current)
        _jobs[key] = {"status": "queued", "path": None, "error": None}
        def work():
            with _guard: _jobs[key] = {"status": "running", "path": None, "error": None}
            result = _run(source, dest, mode, ffmpeg)
            with _guard: _jobs[key] = result
        _pool.submit(work)
        return dict(_jobs[key])


def status(video_id: int, mode: str):
    with _guard:
        return dict(_jobs.get((video_id, mode), {"status": "idle", "path": None, "error": None}))
