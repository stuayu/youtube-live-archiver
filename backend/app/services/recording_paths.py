"""Safe, stable directory names and validation for recorded video folders."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

CHANNEL_NAME_MAX_LENGTH = 50
TITLE_MAX_LENGTH = 60
CHANNEL_DIRECTORY_MAX_LENGTH = CHANNEL_NAME_MAX_LENGTH
VIDEO_DIRECTORY_MAX_LENGTH = 160
_FORBIDDEN = str.maketrans({
    "<": "＜", ">": "＞", ":": "：", '"': "＂", "/": "／", "\\": "＼",
    "|": "｜", "?": "？", "*": "＊",
})
_RESERVED = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE)


def _clusters(value: str):
    """Yield approximate grapheme clusters without splitting emoji join sequences."""
    cluster = ""
    join_next = False
    regional_count = 0
    for char in value:
        code = ord(char)
        regional = code in range(0x1F1E6, 0x1F200)
        extender = (unicodedata.category(char).startswith("M") or code in range(0xDC00, 0xE000)
                    or code in range(0xFE00, 0xFE10)
                    or code in range(0x1F3FB, 0x1F400) or code == 0x20E3
                    or (regional and regional_count == 1))
        if not cluster or extender or join_next or char == "\u200d":
            cluster += char
        else:
            yield cluster
            cluster = char
        regional_count = regional_count + 1 if regional else 0
        join_next = char == "\u200d"
    if cluster:
        yield cluster


def sanitize_folder_name(value: str, max_length: int) -> str:
    """Make a Windows-safe folder component and cap it by visible grapheme clusters."""
    value = str(value).encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
    value = "".join("_" if ord(char) < 32 or 127 <= ord(char) <= 159 else char
                   for char in value).translate(_FORBIDDEN)
    value = re.sub(r"\s+", " ", value).strip(" .\t\r\n")
    if not value:
        return ""
    clusters = list(_clusters(value))
    if len(clusters) > max_length:
        value = "".join(clusters[:max(0, max_length - 1)]) + "…"
    if _RESERVED.fullmatch(value):
        value += "_"
    return value


def video_directory_name(video_id: str, title: str, date_anchor: datetime) -> str:
    local_date = date_anchor.astimezone().date()
    safe_id = sanitize_folder_name(video_id, 32) or "unknown"
    safe_title = sanitize_folder_name(title or "", TITLE_MAX_LENGTH)
    stem = f"{local_date:%Y-%m-%d} {safe_title}" if safe_title else f"{local_date:%Y-%m-%d}"
    suffix = f" [{safe_id}]"
    max_stem = max(1, VIDEO_DIRECTORY_MAX_LENGTH - len(suffix))
    if len(list(_clusters(stem))) > max_stem:
        stem = sanitize_folder_name(stem, max_stem)
    return f"{stem}{suffix}"


def recording_directory_parts(root: Path, channel_name: str, video_id: str,
                              title: str, date_anchor: datetime) -> tuple[str, str]:
    """Fit the full directory path within the Windows recorder path budget."""
    channel = sanitize_folder_name(channel_name, CHANNEL_DIRECTORY_MAX_LENGTH)
    channel = channel or sanitize_folder_name("unknown", CHANNEL_DIRECTORY_MAX_LENGTH)
    safe_id = sanitize_folder_name(video_id, 32) or "unknown"
    date = date_anchor.astimezone().date().strftime("%Y-%m-%d")
    suffix = f" [{safe_id}]"
    fallback = f"{date}{suffix}"
    clusters = list(_clusters(sanitize_folder_name(title or "", TITLE_MAX_LENGTH)))
    for count in range(len(clusters), -1, -1):
        short_title = "".join(clusters[:count])
        name = f"{date} {short_title}{suffix}" if short_title else fallback
        units = len(str(root / channel / name).encode("utf-16-le", "surrogatepass")) // 2
        if units + 64 <= 260:
            return channel, name
    return "_", fallback


def is_video_directory(path: Path, storage_root: Path, video_id: str) -> bool:
    """Check a registered directory belongs to storage and is named for its video."""
    try:
        resolved, root = path.resolve(), storage_root.resolve()
        return (resolved.is_dir() and root in resolved.parents
                and (resolved.name == video_id or resolved.name.endswith(f" [{video_id}]")))
    except (OSError, RuntimeError):
        return False
