from dataclasses import dataclass
from datetime import timedelta
import re

from ..config import settings


BACKOFF_MINUTES = (1, 5, 15, 60, 360)


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str | None = None


def recovery_delay(attempts: int) -> timedelta:
    """Bounded retry delay; after the last slot, keep retrying every six hours."""
    index = min(max(attempts - 1, 0), len(BACKOFF_MINUTES) - 1)
    return timedelta(minutes=BACKOFF_MINUTES[index])


def classify_recovery(status: str | None, error: str | None, metadata: dict | None, attempts: int = 0) -> RecoveryDecision:
    if error or not status or status.upper() in ("UNKNOWN", "PROTECTION_PENDING"):
        return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")
    status = status.upper()
    if status == "UNAVAILABLE":
        return RecoveryDecision("PROTECT" if attempts >= 3 else "DEFER", "REMOTE_UNAVAILABLE" if attempts >= 3 else "REMOTE_CHECK_FAILED")
    if status in ("PRIVATE", "DELETED", "REMOVED", "MEMBERS_ONLY", "ACCESS_DENIED"):
        reason = "REMOTE_PRIVATE" if status == "PRIVATE" else "REMOTE_DELETED" if status in ("DELETED", "REMOVED") else "REMOTE_UNAVAILABLE"
        return RecoveryDecision("PROTECT", reason)
    if status != "PUBLIC":
        return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")
    live_state = (metadata or {}).get("live_status")
    if live_state == "is_upcoming":
        return RecoveryDecision("WAIT_LIVE")
    if live_state == "is_live":
        return RecoveryDecision("RESUME_LIVE")
    if live_state in ("was_live", "not_live", "post_live") or (metadata or {}).get("duration"):
        return RecoveryDecision("RETRIEVE_ARCHIVE")
    return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")


def remove_intermediates_after_success(directory, final_media, audit_callback, *, stem: str | None = None, validated: bool = False):
    """Remove only artifacts belonging to one verified output stem."""
    from pathlib import Path
    directory, final_media = Path(directory), Path(final_media)
    if not validated or not final_media.is_file() or final_media.stat().st_size <= 0:
        return []
    stem = stem or final_media.stem
    removed = []
    final_resolved = final_media.resolve()
    for item in directory.iterdir():
        if not item.is_file() or item.resolve() == final_resolved:
            continue
        name = item.name
        if not name.startswith(stem + "."):
            continue
        if "live_chat" in name.lower():
            continue
        lowered = name.lower()
        fragment = re.fullmatch(re.escape(stem) + r"\.f\d+\.[^.]+", name, re.I)
        if lowered.endswith((".part", ".ytdl")) or fragment:
            item.unlink()
            removed.append(str(item))
            audit_callback(str(item))
    return removed


def choose_recording_stem(directory, profile: str, used_live_stems: set[str] | None = None) -> str:
    """Return deterministic output stem without overwriting another attempt."""
    from pathlib import Path
    directory = Path(directory)
    used_live_stems = used_live_stems or set()
    if profile == "archive":
        if "video" not in used_live_stems:
            return "video"
        base = "video-archive"
        if not any(path.name.startswith(f"{base}.") for path in directory.iterdir()):
            return base
        number = 2
        while any(path.name.startswith(f"{base}-{number}.") for path in directory.iterdir()):
            number += 1
        return f"{base}-{number}"
    existing = {path.name.split(".", 1)[0] for path in directory.iterdir()} if directory.exists() else set()
    if "video" not in used_live_stems and "video" not in existing:
        return "video"
    number = 2
    while f"video-part-{number}" in existing or f"video-part-{number}" in used_live_stems:
        number += 1
    return f"video-part-{number}"


def probe_duration(path, ffprobe_path: str | None = None) -> float | None:
    import json
    import subprocess
    try:
        result = subprocess.run([ffprobe_path or settings.ffprobe_path, "-v", "error", "-show_entries", "format=duration",
                                 "-of", "json", str(path)], capture_output=True, timeout=30, check=False)
        if result.returncode:
            return None
        duration = float(json.loads(result.stdout).get("format", {}).get("duration"))
        return duration if duration > 0 else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def probe_start_time(path, ffprobe_path: str | None = None) -> float | None:
    """Return media timeline start_time when ffprobe can read it."""
    import json
    import subprocess
    try:
        result = subprocess.run(
            [ffprobe_path or settings.ffprobe_path, "-v", "error", "-show_entries", "format=start_time", "-of", "json", str(path)],
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            return None
        value = json.loads(result.stdout).get("format", {}).get("start_time")
        start_time = float(value)
        return start_time if start_time >= 0 else None
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired):
        return None


def salvage_live_part(directory, stem: str, audit_callback, *, ffmpeg_path: str = "ffmpeg"):
    """Copy a playable live .part to a partial MKV and preserve it on any uncertainty."""
    import os
    import re
    import subprocess
    import tempfile
    from pathlib import Path
    from .ffmpeg import ffmpeg_command
    directory = Path(directory)
    final = [directory / f"{stem}.{ext}" for ext in ("mkv", "mp4", "ts", "webm")]
    if any(path.is_file() and path.stat().st_size > 0 for path in final):
        return None
    dash_parts = sorted(path for path in directory.glob(f"{stem}.f*.part")
                        if re.fullmatch(re.escape(stem) + r"\.f\d+\.[^.]+\.part", path.name, re.I))
    part = next((directory / f"{stem}.{ext}.part" for ext in ("mkv", "mp4", "ts", "webm")
                 if (directory / f"{stem}.{ext}.part").is_file()), None)
    sources = dash_parts or ([part] if part else [])
    if not sources or any(source.stat().st_size <= 0 or re.search(r"-Frag\d+", source.name)
                          for source in sources):
        return None
    source_durations = [probe_duration(source) for source in sources]
    if any(duration is None for duration in source_durations):
        return None
    source_duration = max(source_durations)
    fd, temp_name = tempfile.mkstemp(prefix=f".{stem}-salvage-", suffix=".mkv", dir=directory)
    os.close(fd)
    temp = Path(temp_name)
    output = directory / f"{stem}.partial.mkv"
    try:
        timeout = max(600, sum(source.stat().st_size for source in sources) / 5_000_000)
        command = [ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
        for source in sources:
            command.extend(["-i", str(source)])
        if len(sources) > 1:
            command.extend(["-map", "0:v?", "-map", "1:a?"])
        else:
            command.extend(["-map", "0"])
        command.extend(["-c", "copy", str(temp)])
        result = subprocess.run(ffmpeg_command(command),
                                capture_output=True, timeout=timeout, check=False)
        duration = probe_duration(temp) if result.returncode == 0 else None
        if duration is None:
            return None
        os.replace(temp, output)
        if abs(duration - source_duration) <= 2:
            for source in sources:
                source.unlink(missing_ok=True)
                audit_callback(str(source))
        return output
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        temp.unlink(missing_ok=True)
