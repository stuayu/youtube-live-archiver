"""Installed yt-dlp version checks and optional, recording-safe updates."""
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.request import urlopen
from packaging.version import Version

from ..config import settings
from .youtube import run_ytdlp

log = logging.getLogger(__name__)
_lock = threading.Lock()
_update_in_progress = False
_state: dict = {"checked_at": None, "version": None, "release_date": None,
                "latest_version": None, "latest_release_date": None, "stale": None,
                "latest_check_failed": None, "update_overdue": False,
                "update_enabled": settings.yt_dlp_auto_update, "update_error": None,
                "previous_version": None}


def _release(version: str | None) -> tuple[str | None, str | None]:
    try:
        normalized = str(Version(version)) if version else None
        endpoint = (f"https://pypi.org/pypi/yt-dlp/{normalized}/json" if normalized
                    else "https://pypi.org/pypi/yt-dlp/json")
        with urlopen(endpoint, timeout=10) as response:
            data = json.load(response)
        resolved_version = data.get("info", {}).get("version", version)
        releases = data.get("releases", {}).get(resolved_version, []) or data.get("urls", [])
        release = min((item.get("upload_time_iso_8601") for item in releases
                       if item.get("upload_time_iso_8601")), default=None)
        return resolved_version, release
    except Exception as exc:
        log.warning("yt-dlp release metadata unavailable: %s", exc)
        return None, None


def _version() -> str:
    result = run_ytdlp(["--version"], capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "yt-dlp --version failed")[-500:])
    version = (result.stdout or "").strip()
    if not version:
        raise RuntimeError("yt-dlp --version returned an empty version")
    return version


def is_update_in_progress() -> bool:
    with _lock:
        return _update_in_progress


def _standalone_target() -> bool:
    target = settings.yt_dlp_path.strip()
    if not target:
        return False
    path = Path(target)
    if not path.is_file():
        resolved = shutil.which(target)
        path = Path(resolved) if resolved else path
    if path.suffix.lower() == ".exe":
        script = path.with_name(f"{path.stem}-script.py")
        try:
            return not (script.is_file() and "yt_dlp" in script.read_text(encoding="utf-8"))
        except OSError:
            return True
    if not path.is_file():
        return False
    try:
        return not path.read_bytes()[:2] == b"#!"
    except OSError:
        return False


def check_version() -> dict:
    checked_at = datetime.now(timezone.utc)
    error = None
    try:
        version = _version()
        _, release_date = _release(version)
        latest_version, latest_date = _release(None)
        latest_check_failed = latest_version is None
        stale = None if latest_check_failed else Version(latest_version) > Version(version)
        update_overdue = False
        if stale and latest_date:
            released = datetime.fromisoformat(latest_date.replace("Z", "+00:00"))
            update_overdue = (checked_at - released).total_seconds() >= 7 * 86400
        previous_version = None
        if settings.yt_dlp_auto_update and stale and _standalone_target():
            error = "単体版のため自動更新対象外（yt-dlp -U で更新）"
        if settings.yt_dlp_auto_update and stale and not _standalone_target():
            from ..db import SessionLocal
            from ..models import Video
            from sqlalchemy import select
            global _update_in_progress
            with _lock:
                _update_in_progress = True
            try:
                with SessionLocal() as db:
                    recording = db.scalar(select(Video.id).where(Video.status == "RECORDING").limit(1))
                if recording is None:
                    previous_version = version
                    with _lock:
                        _state["previous_version"] = previous_version
                    _update(version)
                    version = _version()
                    _, release_date = _release(version)
                    stale = None if latest_version is None else Version(latest_version) > Version(version)
                    if stale and latest_date:
                        released = datetime.fromisoformat(latest_date.replace("Z", "+00:00"))
                        update_overdue = (checked_at - released).total_seconds() >= 7 * 86400
                    else:
                        update_overdue = False
            finally:
                with _lock:
                    _update_in_progress = False
        state = {"checked_at": checked_at, "version": version, "release_date": release_date,
                 "latest_version": latest_version, "latest_release_date": latest_date,
                 "stale": stale, "latest_check_failed": latest_check_failed,
                 "update_overdue": update_overdue, "update_enabled": settings.yt_dlp_auto_update,
                 "update_error": error, "previous_version": previous_version}
    except Exception as exc:
        error = str(exc)[:500]
        log.exception("yt-dlp version check failed")
        with _lock:
            state = {**_state, "checked_at": checked_at, "update_error": error,
                     "latest_version": None, "latest_release_date": None,
                     "stale": None, "latest_check_failed": True, "update_overdue": False,
                     "update_enabled": settings.yt_dlp_auto_update}
    with _lock:
        _state.update(state)
        return dict(_state)


def _update(previous_version: str) -> None:
    command = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "yt-dlp pip update failed")[-1000:])
    try:
        _version()
    except Exception:
        rollback = subprocess.run([sys.executable, "-m", "pip", "install", f"yt-dlp=={previous_version}"],
                                  capture_output=True, text=True, timeout=600, check=False)
        if rollback.returncode:
            raise RuntimeError("yt-dlp update verification and rollback both failed")
        raise RuntimeError(f"yt-dlp update verification failed; restored {previous_version}")


def status() -> dict:
    with _lock:
        return dict(_state)
