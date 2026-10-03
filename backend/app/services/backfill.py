from pathlib import Path
from datetime import datetime

MEDIA_EXTENSIONS = {".mkv", ".mp4", ".webm", ".ts", ".m4v", ".mov", ".flv", ".avi"}


def media_file(directory: Path) -> Path | None:
    return next((p.resolve() for p in directory.iterdir() if p.is_file() and p.name.endswith(".playable.mp4")), None) or next((p.resolve() for p in directory.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS and not p.name.endswith((".h264.mp4", ".playable.mp4"))), None)


def eligible_backfill_ids(entries: list[dict], limit: int, registered_at: datetime | None = None) -> set[str]:
    """Return the newest N completed live IDs; 0 deliberately returns none."""
    completed = [entry for entry in entries if entry.get("id") and entry.get("live_status") == "was_live"]
    cutoff = registered_at.timestamp() if registered_at else float("inf")
    before, after = [], []
    for entry in completed:
        raw_timestamp = entry.get("release_timestamp") or entry.get("timestamp") or 0
        try: timestamp = float(raw_timestamp)
        except (TypeError, ValueError):
            try:
                parsed = datetime.fromisoformat(str(raw_timestamp).replace("Z", "+00:00"))
                timestamp = parsed.timestamp()
            except (TypeError, ValueError, OSError): timestamp = 0
        (before if timestamp <= cutoff else after).append(entry)
    before.sort(key=lambda entry: entry.get("release_timestamp") or entry.get("timestamp") or 0, reverse=True)
    return {str(entry["id"]) for entry in before[:max(0, limit)] + after}


def parse_byte_range(value: str, size: int) -> tuple[int, int]:
    try:
        unit, span = value.split("=", 1)
        first, last = span.split("-", 1)
        if unit != "bytes": raise ValueError
        start = int(first) if first else max(0, size - int(last))
        end = min(int(last), size - 1) if first and last else size - 1
        if start > end or start >= size: raise ValueError
        return start, end
    except (ValueError, TypeError):
        raise ValueError("invalid byte range")


def registered_video_directory(file_path: str | None, storage_root: str | None) -> Path:
    if not file_path or not storage_root:
        raise ValueError("video path is not registered")
    path, root = Path(file_path).resolve(), Path(storage_root).resolve()
    if root not in path.parents or not path.is_dir():
        raise ValueError("video path is outside registered storage")
    return path
