"""Safe on-demand media remux/transcode queue; source media is never modified."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import os
import subprocess
import threading

from ..config import settings
from .ffmpeg import launch_ffmpeg
from sqlalchemy import select

DERIVED_FORMAT_VERSION = 2

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="media-convert")
_guard = threading.Lock()
_jobs: dict[tuple[int, str], dict] = {}
_processes: set[subprocess.Popen] = set()
_stopping = threading.Event()


def output_path(source: Path, mode: str) -> Path:
    return source.with_name(source.stem + (".playable.mp4" if mode == "remux" else ".h264.mp4"))


def _run(source: Path, dest: Path, mode: str, ffmpeg: str, progress=None):
    temp = dest.with_name(dest.stem + ".tmp.mp4")
    process = None
    try:
        duration = 0.0
        try:
            probe = subprocess.run(
                [settings.ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(source)],
                capture_output=True, text=True, timeout=15, check=False,
            )
            if probe.returncode == 0:
                duration = float(json.loads(probe.stdout).get("format", {}).get("duration") or 0)
        except (OSError, ValueError, TypeError, subprocess.SubprocessError):
            pass
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
        args += ["-c", "copy", "-movflags", "+faststart"] if mode == "remux" else ["-c:v", "libx264", "-preset", "medium", "-c:a", "aac", "-movflags", "+faststart"]
        args.extend(["-progress", "pipe:1", "-nostats", "-chunk_duration", "1000000", str(temp)])
        # stderr is merged so it cannot fill an unread pipe; -progress lines are key=value.
        process = launch_ffmpeg(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        with _guard:
            _processes.add(process)
        messages: deque[str] = deque(maxlen=20)
        assert process.stdout is not None
        for line in process.stdout:
            key, separator, value = line.strip().partition("=")
            if not separator or " " in key:
                if line.strip():
                    messages.append(line.strip())
            elif key == "out_time_us" and duration > 0 and progress:
                try:
                    fraction = int(value) / 1_000_000 / duration
                except ValueError:
                    continue
                progress(min(0.99, max(0.0, fraction)))
        return_code = process.wait()
        if return_code or not temp.is_file() or temp.stat().st_size == 0:
            raise RuntimeError("\n".join(messages)[-2000:] or "ffmpeg conversion failed")
        os.replace(temp, dest)
        return {"status": "completed", "path": str(dest), "error": None, "progress": 1.0}
    except Exception as exc:
        temp.unlink(missing_ok=True)
        return {"status": "failed", "path": None, "error": str(exc), "progress": 0.0}
    finally:
        if process is not None:
            with _guard:
                _processes.discard(process)


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
        _jobs[key] = {"status": "queued", "path": None, "error": None, "progress": 0.0}
        def work():
            if _stopping.is_set():
                with _guard:
                    _jobs.pop(key, None)
                return
            with _guard:
                _jobs[key] = {"status": "running", "path": None, "error": None, "progress": 0.0}
            result = _run(source, dest, mode, ffmpeg,
                          lambda value: _set_progress(key, value))
            with _guard: _jobs[key] = result
        _pool.submit(work)
        return dict(_jobs[key])


def status(video_id: int, mode: str):
    with _guard:
        current = _jobs.get((video_id, mode))
        if current:
            return dict(current)
        return {"status": "idle", "path": None, "error": None, "progress": 0.0}


def _set_progress(key: tuple[int, str], value: float) -> None:
    with _guard:
        current = _jobs.get(key)
        if current and current["status"] == "running":
            current["progress"] = value


def shutdown() -> None:
    """Stop conversions at app shutdown; a worker blocked on ffmpeg would delay process exit."""
    _stopping.set()
    with _guard:
        processes = list(_processes)
    for process in processes:
        try:
            process.terminate()
        except OSError:
            pass


def resume() -> None:
    _stopping.clear()


def cleanup_temporary_files(directories: list[Path]) -> int:
    removed = 0
    for directory in directories:
        try:
            for path in directory.glob("*.tmp.mp4"):
                if path.is_file():
                    path.unlink()
                    removed += 1
        except OSError:
            continue
    return removed


def cleanup_old_derived_files() -> bool:
    """Remove obsolete playback renditions once, keeping original recordings intact."""
    from ..db import SessionLocal
    from ..models import AuditLog, Storage, Video
    from .media import source_media

    version_file = Path(settings.data_dir) / "derived-format-version"
    pending_file = Path(settings.data_dir) / "derived-format-pending"
    try:
        version = int(version_file.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError, ValueError):
        version = 0
    if version >= DERIVED_FORMAT_VERSION:
        return True

    try:
        started_at = pending_file.stat().st_mtime
    except FileNotFoundError:
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.touch()
        started_at = pending_file.stat().st_mtime
    except OSError:
        return False

    with SessionLocal() as db:
        rows = db.execute(
            select(Video.id, Video.file_path, Storage.root_path)
            .outerjoin(Storage, Video.storage_id == Storage.id)
            .where(Video.file_path.is_not(None))
        ).all()

    removed: list[tuple[int, str, int]] = []
    complete = True
    for video_id, file_path, storage_root in rows:
        directory = Path(file_path)
        if not storage_root:
            complete = False
            continue
        try:
            Path(storage_root).stat()
            try:
                directory.stat()
            except FileNotFoundError:
                # Deleted recordings are expected; a missing individual folder is not a retry condition.
                continue
            if not directory.is_dir():
                complete = False
                continue
            next(directory.iterdir(), None)
            source = source_media(directory)
            if source is None or not source.is_file() or source.stat().st_size <= 0:
                continue
            for suffix in ("*.playable.mp4", "*.h264.mp4"):
                for derived in directory.glob(suffix):
                    if not derived.is_file():
                        continue
                    if derived.stat().st_mtime >= started_at:
                        continue
                    size = derived.stat().st_size
                    derived.unlink()
                    removed.append((video_id, derived.name, size))
        except OSError:
            complete = False

    if removed:
        with SessionLocal() as db:
            db.add_all([
                AuditLog(video_id=video_id, action="DERIVED_CLEANUP", reason="FORMAT_UPGRADED",
                         details={"path": name, "size": size})
                for video_id, name, size in removed
            ])
            db.commit()
    if complete:
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(str(DERIVED_FORMAT_VERSION), encoding="ascii")
        pending_file.unlink(missing_ok=True)
    return complete
