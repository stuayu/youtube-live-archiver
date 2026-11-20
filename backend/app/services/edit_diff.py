"""録画と YouTube の現行アーカイブを音量の推移で突き合わせ、編集された区間を求める。

映像は再エンコードで画質が変わるが、0.1 秒ごとの音量 (dB) の推移はほぼ保たれる。
YouTube 版を 30 秒ずつに区切り、録画のどこと一致するかを正規化相互相関で探す。
一致した区間 (run) のずれ量が変わる所がカット・追加の境目になる。
"""
import subprocess
from pathlib import Path

import numpy as np

from ..config import settings
from .ffmpeg import launch_ffmpeg

RATE = 10  # 1 秒あたりの音量サンプル数
FLOOR_DB = -60.0
SAMPLE_RATE = 8000
BLOCK_SECONDS = 30
HOP_SECONDS = 5
MIN_SCORE = 0.75
MIN_STD_DB = 1.0
MIN_REGION_SECONDS = 3.0
# 境界補正後にこれより短く残った一致区間は偶然の一致として捨てる。
MIN_RUN_SECONDS = 10.0
# 照合前に除く緩やかな音量変化の幅。話し声の細かい抑揚だけを比べる。
DETREND_SECONDS = 3
# 境界推定で「どちらにも一致しない」と見なす 1 サンプルあたりの費用 (dB)。
UNMATCHED_COST = 4.0
MAX_COST = 15.0


def envelope_from_pcm(samples: np.ndarray, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    window = sample_rate // RATE
    count = samples.size // window
    frames = samples[:count * window].astype(np.float64).reshape(count, window) / 32768.0
    energy = (frames * frames).mean(axis=1)
    return np.maximum(10 * np.log10(energy + 1e-12), FLOOR_DB)


def audio_envelope(path: str | Path, *, timeout: float | None = None) -> np.ndarray:
    """数時間の音声でもメモリに全 PCM を載せず、1 分ずつ音量へ変換する。"""
    process = launch_ffmpeg([settings.ffmpeg_path, "-v", "error", "-i", str(path), "-vn", "-ac", "1",
                             "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    chunk_bytes = SAMPLE_RATE * 2 * 60
    parts = []
    try:
        assert process.stdout is not None
        while True:
            data = process.stdout.read(chunk_bytes)
            if not data:
                break
            usable = len(data) - len(data) % 2
            parts.append(envelope_from_pcm(np.frombuffer(data[:usable], dtype="<i2"), SAMPLE_RATE))
        stderr = process.stderr.read() if process.stderr else b""
        if process.wait(timeout=timeout) != 0:
            raise RuntimeError(stderr.decode(errors="replace")[-1000:] or "ffmpeg failed")
    finally:
        if process.poll() is None:
            process.kill()
    if not parts:
        raise RuntimeError("audio is empty")
    return np.concatenate(parts)


def _block_matches(local: np.ndarray, remote: np.ndarray, block: int, hop: int) -> list[tuple[int, int]]:
    """YouTube 版の各ブロックについて (開始位置, 録画とのずれ) を返す。"""
    n_local = local.size
    if n_local < block or remote.size < block:
        return []
    size = 1 << int(np.ceil(np.log2(n_local + block)))
    spectrum = np.fft.rfft(local, size)
    sums = np.concatenate([[0.0], np.cumsum(local)])
    squares = np.concatenate([[0.0], np.cumsum(local * local)])
    window_mean = (sums[block:] - sums[:-block]) / block
    window_std = np.sqrt(np.maximum((squares[block:] - squares[:-block]) / block - window_mean ** 2, 0))
    starts = list(range(0, remote.size - block + 1, hop))
    if starts[-1] != remote.size - block:
        starts.append(remote.size - block)
    matches = []
    for start in starts:
        piece = remote[start:start + block]
        std = piece.std()
        if std < MIN_STD_DB:
            continue  # 無音や一定音は録画のどこにでも一致してしまう
        corr = np.fft.irfft(spectrum * np.fft.rfft((piece - piece.mean())[::-1], size), size)
        corr = corr[block - 1:block - 1 + window_std.size]
        score = np.where(window_std >= MIN_STD_DB, corr / np.maximum(block * std * window_std, 1e-9), -1.0)
        best = int(np.argmax(score))
        if score[best] >= MIN_SCORE:
            matches.append((start, best - start))
    return matches


def _detrend(envelope: np.ndarray) -> np.ndarray:
    width = DETREND_SECONDS * RATE + 1
    if envelope.size < width:
        return envelope - envelope.mean()
    padded = np.pad(envelope, width // 2, mode="edge")
    return envelope - np.convolve(padded, np.ones(width) / width, mode="valid")


def _runs(matches: list[tuple[int, int]], block: int, hop: int, n_local: int) -> list[dict]:
    """同じずれで連続して一致したブロックを 1 区間にまとめる。1 ブロックだけの一致は偶然と見なす。"""
    groups: list[list[tuple[int, int]]] = []
    for start, offset in matches:
        last = groups[-1][-1] if groups else None
        if last and start - last[0] <= hop and abs(offset - last[1]) <= 2:
            groups[-1].append((start, offset))
        else:
            groups.append([(start, offset)])
    runs = []
    for group in groups:
        if len(group) < 2:
            continue
        offset = int(np.median([item[1] for item in group]))
        start = max(group[0][0], -offset)
        end = min(group[-1][0] + block, n_local - offset)
        if end > start:
            runs.append({"start": start, "end": end, "offset": offset})
    return runs


def _gain(local: np.ndarray, remote: np.ndarray, run: dict) -> float:
    r = remote[run["start"]:run["end"]]
    l = local[run["start"] + run["offset"]:run["end"] + run["offset"]]
    audible = (r > FLOOR_DB + 5) & (l > FLOOR_DB + 5)
    return float(np.median((r - l)[audible])) if audible.any() else 0.0


def _costs(local: np.ndarray, remote: np.ndarray, lo: int, hi: int, run: dict | None) -> np.ndarray:
    if run is None:
        return np.full(hi - lo, UNMATCHED_COST)
    index = np.arange(lo, hi) + run["offset"]
    valid = (index >= 0) & (index < local.size)
    costs = np.full(hi - lo, MAX_COST)
    costs[valid] = np.minimum(np.abs(remote[lo:hi][valid] - local[index[valid]] - run["gain"]), MAX_COST)
    return costs


def _split(local: np.ndarray, remote: np.ndarray, lo: int, hi: int,
           before: dict | None, after: dict | None) -> tuple[int, int]:
    """[lo, hi) を「before に一致 → どちらにも不一致 → after に一致」の 3 つに分ける最小費用の境目。"""
    first = np.concatenate([[0.0], np.cumsum(_costs(local, remote, lo, hi, before))])
    second = np.concatenate([[0.0], np.cumsum(_costs(local, remote, lo, hi, after))])
    length = hi - lo
    t = np.arange(length + 1)
    # 費用 = first[t1] + UNMATCHED*(t2 - t1) + second[end] - second[t2]
    left = first - UNMATCHED_COST * t
    total = np.minimum.accumulate(left) + UNMATCHED_COST * t + second[-1] - second
    t2 = int(np.argmin(total))
    t1 = int(np.argmin(left[:t2 + 1]))
    return lo + t1, lo + t2


def _refine(local: np.ndarray, remote: np.ndarray, runs: list[dict], block: int) -> list[dict]:
    if not runs:
        return []
    for run in runs:
        run["gain"] = _gain(local, remote, run)
    for index in range(len(runs) + 1):
        before = runs[index - 1] if index > 0 else None
        after = runs[index] if index < len(runs) else None
        lo = max(before["start"] if before else 0, (before["end"] if before else after["start"]) - block)
        hi = min(after["end"] if after else remote.size, (after["start"] if after else before["end"]) + block)
        lo = max(0, min(lo, hi))
        if hi <= lo:
            continue
        t1, t2 = _split(local, remote, lo, hi, before, after)
        if before:
            before["end"] = max(before["start"], t1)
        if after:
            after["start"] = min(after["end"], t2)
    return runs


def _consistent_runs(local: np.ndarray, remote: np.ndarray, candidates: list[dict], block: int) -> list[dict]:
    """境界補正で短く残った候補を偶然の一致として除き、残った候補だけで補正し直す。"""
    minimum = int(MIN_RUN_SECONDS * RATE)
    while True:
        refined = _refine(local, remote, [dict(run) for run in candidates], block)
        keep = [index for index, run in enumerate(refined) if run["end"] - run["start"] >= minimum]
        if len(keep) == len(candidates):
            return refined
        candidates = [candidates[index] for index in keep]


def _gaps(covered: list[tuple[int, int]], length: int, minimum: int) -> list[tuple[int, int]]:
    gaps, cursor = [], 0
    for start, end in sorted(covered):
        if start - cursor >= minimum:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if length - cursor >= minimum:
        gaps.append((cursor, length))
    return gaps


def _nearest(value: int, candidates: list[tuple[int, int]], tolerance: int) -> int | None:
    """value に接する区間端があれば、その区間の対応先を返す。"""
    best = min(candidates, key=lambda item: abs(item[0] - value), default=None)
    return best[1] if best and abs(best[0] - value) <= tolerance else None


def analyze(local: np.ndarray, remote: np.ndarray) -> dict:
    """秒単位の結果を返す。removed は録画の時刻、added は YouTube 版の時刻。"""
    block, hop = BLOCK_SECONDS * RATE, HOP_SECONDS * RATE
    local = np.asarray(local, dtype=np.float64)
    remote = np.asarray(remote, dtype=np.float64)
    candidates = _runs(_block_matches(_detrend(local), _detrend(remote), block, hop), block, hop, local.size)
    runs = _consistent_runs(local, remote, candidates, block)
    minimum = int(MIN_REGION_SECONDS * RATE)
    tolerance = RATE
    local_ranges = [(run["start"] + run["offset"], run["end"] + run["offset"]) for run in runs]
    removed = []
    for start, end in _gaps(local_ranges, local.size, minimum):
        # カット箇所が YouTube 版のどこにつながったか。前の区間の終わり、なければ次の区間の始まり。
        remote_at = _nearest(start, [(run["end"] + run["offset"], run["end"]) for run in runs], tolerance)
        if remote_at is None:
            remote_at = _nearest(end, [(run["start"] + run["offset"], run["start"]) for run in runs], tolerance)
        removed.append({"start": start / RATE, "end": end / RATE,
                        "remote_at": remote_at / RATE if remote_at is not None else None})
    added = []
    for start, end in _gaps([(run["start"], run["end"]) for run in runs], remote.size, minimum):
        local_at = _nearest(start, [(run["end"], run["end"] + run["offset"]) for run in runs], tolerance)
        if local_at is None:
            local_at = _nearest(end, [(run["start"], run["start"] + run["offset"]) for run in runs], tolerance)
        added.append({"start": start / RATE, "end": end / RATE,
                      "local_at": local_at / RATE if local_at is not None else None})
    matched = sum(run["end"] - run["start"] for run in runs)
    return {
        "local_duration": local.size / RATE,
        "remote_duration": remote.size / RATE,
        "matched_ratio": round(matched / remote.size, 4) if remote.size else 0.0,
        "removed": removed,
        "added": added,
        "segments": [{"remote_start": run["start"] / RATE, "remote_end": run["end"] / RATE,
                      "local_start": (run["start"] + run["offset"]) / RATE,
                      "local_end": (run["end"] + run["offset"]) / RATE} for run in runs],
    }
