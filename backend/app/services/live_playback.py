"""Helpers for isolated, opt-in HLS sidecars."""
from pathlib import Path
import re
import shutil
import subprocess

SAFE_ASSET = re.compile(r"^(?:index\.m3u8|segment_[0-9]{6,}\.ts)$")

def fallback_to_direct(error, return_code, direct_start, record_fallback):
    """Start the trusted direct recorder after any tee launch/runtime failure."""
    if error is None and not return_code:
        return None
    record_fallback(error, return_code)
    return direct_start()

def prune_hls(hls_dir: Path, limit_bytes: int) -> int:
    """Roll old HLS segments and their playlist entries when the opt-in cap is exceeded."""
    if limit_bytes <= 0 or not hls_dir.is_dir():
        return 0
    segments = sorted(hls_dir.glob("segment_[0-9][0-9][0-9][0-9][0-9][0-9].ts"))
    total = sum(p.stat().st_size for p in segments if p.is_file())
    removed = 0
    playlist = hls_dir / "index.m3u8"
    lines = playlist.read_text(encoding="utf-8").splitlines() if playlist.is_file() else []
    while segments and total > limit_bytes:
        old = segments.pop(0)
        size = old.stat().st_size
        old.unlink(missing_ok=True)
        total -= size
        removed += 1
        name = old.name
        for i, line in enumerate(lines):
            if line == name:
                del lines[max(0, i - 1):i + 1]
                break
    if removed and playlist.is_file():
        playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return removed

def tee_args(ffmpeg_path: str, hls_dir: Path, output_file: Path) -> list[str]:
    hls_dir.mkdir(parents=True, exist_ok=True)
    return [ffmpeg_path, "-hide_banner", "-loglevel", "error", "-y", "-i", "pipe:0", "-map", "0", "-c", "copy", "-f", "tee",
            f"[f=matroska]{output_file}|[f=hls:hls_time=6:hls_list_size=0:hls_flags=append_list+independent_segments:hls_segment_filename={hls_dir / 'segment_%06d.ts'}]{hls_dir / 'index.m3u8'}"]

def safe_hls_asset(video, asset: str) -> Path:
    if not video.live_hls_path or not SAFE_ASSET.fullmatch(asset):
        raise ValueError("invalid HLS asset")
    root = Path(video.live_hls_path).resolve()
    if not video.storage or not video.youtube_video_id:
        raise ValueError("recording storage is not registered")
    storage_root = Path(video.storage.root_path).resolve()
    expected_root = (storage_root / "live" / video.youtube_video_id).resolve()
    if root != expected_root or storage_root not in root.parents:
        raise ValueError("HLS directory is outside the registered live area")
    target = (root / asset).resolve()
    if target.parent != root or not target.is_file():
        raise ValueError("HLS asset unavailable")
    return target

def archive_is_complete(media: Path, ffprobe_path: str = "ffprobe") -> bool:
    try:
        if not media.is_file() or media.stat().st_size <= 0:
            return False
        result = subprocess.run(
            [ffprobe_path, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(media)],
            capture_output=True, text=True, timeout=30, check=False)
        return result.returncode == 0 and float(result.stdout.strip()) > 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return False

def cleanup_hls(video, media: Path, ffprobe_path: str = "ffprobe") -> bool:
    """Delete sidecars only after the durable archive independently probes cleanly."""
    if not video.live_hls_path or not archive_is_complete(media, ffprobe_path):
        return False
    root = Path(video.live_hls_path).resolve()
    if not root.is_dir():
        return False
    shutil.rmtree(root)
    video.live_hls_path = None
    return True
