"""Media probing and browser rendition metadata."""
import json
import subprocess
from pathlib import Path

from ..config import settings
from .backfill import media_file
from .transcode import output_path, status as conversion_status



def inspect_media(path: Path, ffprobe: str | None = None) -> dict | None:
    if not path.is_file() or path.stat().st_size <= 0:
        return None
    try:
        result = subprocess.run(
            [ffprobe or settings.ffprobe_path, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        if result.returncode:
            return None
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    fmt = payload.get("format") or {}
    streams = payload.get("streams") or []
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), {})
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    return {
        "container": str(fmt.get("format_name") or path.suffix.lstrip(".")).split(",")[0],
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "width": video.get("width"),
        "height": video.get("height"),
    }


def rendition(video_id: int, source: Path, mode: str, info: dict | None) -> dict:
    derived = output_path(source, mode)
    job = conversion_status(video_id, mode)
    available = derived.is_file() and derived.stat().st_size > 0
    if available:
        job["status"] = "completed"
    if mode == "remux":
        url = f"/api/videos/{video_id}/file?playable=true"
        media = {
            "container": "mp4", "video_codec": (info or {}).get("video_codec"),
            "audio_codec": (info or {}).get("audio_codec"),
        }
    else:
        url = f"/api/videos/{video_id}/file?rendition=h264"
        media = {**(info or {}), "container": "mp4", "video_codec": "h264", "audio_codec": "aac"}
    return {
        "name": mode, "url": url, **media, "available": available,
        "size": derived.stat().st_size if available else None,
        "status": job.get("status", "idle"), "progress": job.get("progress", 0),
    }


def video_playback_metadata(video_id: int, source: Path, info: dict | None) -> dict:
    source_available = source.is_file() and source.stat().st_size > 0
    source_info = info or {}
    return {
        "media": source_info or None,
        "renditions": [
            {
                "name": "source", "url": f"/api/videos/{video_id}/file",
                "container": source_info.get("container") or source.suffix.lstrip("."),
                "video_codec": source_info.get("video_codec"),
                "audio_codec": source_info.get("audio_codec"),
                "available": source_available,
                "size": source.stat().st_size if source_available else None,
            },
            rendition(video_id, source, "remux", info),
            rendition(video_id, source, "h264", info),
        ],
    }


def source_media(directory: Path) -> Path | None:
    """Original media the renditions derive from: primary, else largest, else a salvaged partial."""
    try:
        return media_file(directory)
    except OSError:
        return None
