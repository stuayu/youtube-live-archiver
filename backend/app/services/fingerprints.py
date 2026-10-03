"""ffmpeg を使う軽量な動画サンプル。音声署名は周波数スペクトルの要約。"""
import math
import struct
from ..config import settings
from .ffmpeg import run_ffmpeg

POSITIONS = (0.05, 0.20, 0.40, 0.60, 0.80, 0.95)


def _run(args: list[str]) -> bytes:
    p = run_ffmpeg(args, timeout=60)
    if p.returncode:
        raise RuntimeError(p.stderr.decode(errors="replace")[-1000:])
    return p.stdout


def video_phash(path: str, second: float) -> str:
    # 32x32画像から低周波8x8 DCT係数を取り、medianで二値化するpHash。
    raw = _run([settings.ffmpeg_path, "-v", "error", "-ss", str(second), "-i", path, "-frames:v", "1", "-vf", "scale=32:32,format=gray", "-f", "rawvideo", "-"])
    if len(raw) != 1024:
        raise RuntimeError("video sample is empty")
    pixels = list(raw)
    coeff = []
    for v in range(8):
        for u in range(8):
            total = sum(pixels[y * 32 + x] * math.cos((2*x+1)*u*math.pi/64) * math.cos((2*y+1)*v*math.pi/64)
                        for y in range(32) for x in range(32))
            coeff.append(total)
    ordered = sorted(coeff[1:])
    median = ordered[len(ordered)//2]
    bits = ''.join('1' if value >= median else '0' for value in coeff)
    return f"{int(bits, 2):016x}"


def audio_fingerprint(path: str, second: float) -> str:
    raw = _run([settings.ffmpeg_path, "-v", "error", "-ss", str(second), "-i", path, "-t", "3", "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"])
    count = len(raw) // 2
    if count < 8000:
        raise RuntimeError("audio sample is empty")
    values = struct.unpack("<" + "h" * count, raw[:count * 2])
    buckets = []
    for bucket in range(32):
        start, end = bucket * count // 32, (bucket + 1) * count // 32
        rms = math.sqrt(sum(x * x for x in values[start:end]) / max(1, end - start))
        buckets.append(rms)
    peak = max(buckets) or 1
    # Relative, coarse energy bands remain comparable across lossy re-encodes.
    return "".join(format(min(15, round((value / peak) ** 0.5 * 15)), "x") for value in buckets)


def sample(path: str, duration: float, include_audio: bool = False) -> list[dict]:
    return [{"position": p, "video_hash": video_phash(path, duration * p),
             "audio_hash": audio_fingerprint(path, duration * p) if include_audio else None} for p in POSITIONS]
