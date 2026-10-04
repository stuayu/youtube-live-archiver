import re
from urllib.parse import urlsplit, parse_qs


_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_video_url(text: str) -> str:
    value = (text or "").strip()
    if _VIDEO_ID.fullmatch(value):
        return value
    if not value:
        raise ValueError("YouTube 動画 URL または 11 文字の動画 ID を入力してください")
    if ":" not in value and "/" not in value and "?" not in value:
        raise ValueError("動画 ID は英数字・_・- の 11 文字で指定してください")
    candidate = value if "://" in value else f"https://{value}"
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() not in ("http", "https"):
            raise ValueError
        if not (host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")):
            raise ValueError
        parts = [part for part in parsed.path.split("/") if part]
        if host == "youtu.be":
            video_id = parts[0] if parts else ""
        elif parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) == 1 and parts[0] == "watch" and parsed.query:
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        elif len(parts) >= 2 and parts[0] in ("live", "shorts"):
            video_id = parts[1]
        else:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError("YouTube の動画 URL を入力してください") from None
    if not _VIDEO_ID.fullmatch(video_id):
        raise ValueError("動画 ID は英数字・_・- の 11 文字で指定してください")
    return video_id


def canonical_video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def action_for_live_status(live_status: str | None) -> str:
    if live_status == "is_live":
        return "record_live"
    if live_status == "is_upcoming":
        return "wait_live"
    return "download_archive"
