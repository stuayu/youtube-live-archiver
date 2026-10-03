"""Safe ffmpeg operations used by derived media processing."""
import subprocess
import tempfile
from pathlib import Path

from ..config import settings


def run_ffmpeg(args: list[str], *, timeout=60, text=False):
    """Run a finite ffmpeg command through the shared wrapper."""
    return subprocess.run(args, capture_output=True, timeout=timeout, text=text, check=False)


def launch_ffmpeg(args: list[str], **kwargs):
    """Launch streaming ffmpeg commands through the shared process wrapper."""
    return subprocess.Popen(args, **kwargs)


def extract_whisper_audio(source: Path, *, ffmpeg_path: str | None = None,
                          start_seconds: float = 0, duration_seconds: float | None = None) -> Path:
    """Extract one mono 16 kHz PCM WAV chunk; the source is opened read-only."""
    if not source.is_file():
        raise FileNotFoundError(source)
    fd, name = tempfile.mkstemp(prefix="yla-transcript-", suffix=".wav")
    import os
    os.close(fd)
    output = Path(name)
    try:
        args = [ffmpeg_path or settings.ffmpeg_path, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
        if start_seconds > 0:
            args.extend(["-ss", f"{start_seconds:.3f}"])
        args.extend(["-i", str(source)])
        if duration_seconds is not None:
            args.extend(["-t", f"{duration_seconds:.3f}"])
        args.extend(["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output)])
        result = run_ffmpeg(args,
                                timeout=None)
        if result.returncode or not output.is_file() or output.stat().st_size <= 44:
            raise RuntimeError((result.stderr or b"ffmpeg audio extraction failed").decode(errors="replace")[-1000:])
        return output
    except Exception:
        output.unlink(missing_ok=True)
        raise
