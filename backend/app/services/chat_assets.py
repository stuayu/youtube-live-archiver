"""Validated, content-addressed cache for chat emoji and badge artwork."""
from collections import OrderedDict
from pathlib import Path
import threading
import time
from urllib.parse import urlparse
from urllib.request import urlopen

from sqlalchemy import select

from ..models import ChatMessage
from .image_cache import asset_key, cached_path, fetch_image

MAX_ASSET_BYTES = 1024 * 1024
ASSET_TIMEOUT_SECONDS = 4
ASSET_SUFFIXES = (".png", ".jpg", ".webp", ".gif")
MAX_KNOWN_URLS = 50_000
FAILURE_RETRY_SECONDS = 600

# Keys handed to the browser by the chat API, so the asset route never has to scan chat rows.
_known_urls: OrderedDict[str, str] = OrderedDict()
_failed_until: dict[str, float] = {}
_lock = threading.Lock()


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (
        host == "yt3.ggpht.com"
        or host == "www.gstatic.com"
        or host == "fonts.gstatic.com"
        or host == "googleusercontent.com"
        or host.endswith(".googleusercontent.com")
    )


def register_asset(url: str) -> str:
    """Remember the source of a key that is about to be sent to the browser."""
    key = asset_key(url)
    with _lock:
        _known_urls[key] = url
        _known_urls.move_to_end(key)
        while len(_known_urls) > MAX_KNOWN_URLS:
            _known_urls.popitem(last=False)
    return key


def resolve_asset(key: str, root: str | Path) -> Path | None:
    """Serve from disk; fetch only keys the chat API issued, and do not retry recent failures."""
    existing = cached_path(key, root)
    if existing:
        return existing
    with _lock:
        url = _known_urls.get(key)
        if url is None or _failed_until.get(key, 0) > time.monotonic():
            return None
    try:
        return cache_asset(url, root)
    except ValueError:
        with _lock:
            _failed_until[key] = time.monotonic() + FAILURE_RETRY_SECONDS
        return None


def cache_asset(url: str, root: str | Path) -> Path:
    """Fetch a chat image while preserving chat's host and size policy."""
    return fetch_image(url, root, allowed=_allowed_url, max_bytes=MAX_ASSET_BYTES,
                       context="chat asset", opener=urlopen)


def prefetch_video_assets(db, video_id: int, root: str | Path, limit: int = 500) -> int:
    """Best-effort cache of unique image URLs referenced by a video's saved chat."""
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
    urls: set[str] = set()

    def visit(value):
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
        elif isinstance(value, str) and value.startswith("https://"):
            urls.add(value)

    for row in rows:
        visit(row.message_runs or [])
        visit(row.author_badges or [])
        if row.sticker_url:
            urls.add(row.sticker_url)
    # Image fetches can wait on remote CDN timeouts. Close the read transaction first.
    if db.in_transaction():
        db.commit()
    cached = 0
    for url in sorted(urls)[:limit]:
        try:
            cache_asset(url, root)
            cached += 1
        except ValueError:
            continue
    return cached
