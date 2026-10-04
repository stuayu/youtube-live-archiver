"""Validated, content-addressed cache for chat emoji and badge artwork."""
from hashlib import sha256
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from sqlalchemy import select

from ..models import ChatMessage

MAX_ASSET_BYTES = 1024 * 1024
ASSET_TIMEOUT_SECONDS = 4


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


def asset_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def cache_asset(url: str, root: str | Path) -> Path:
    """Fetch an allowed image once, enforcing redirect, timeout and size limits."""
    if not _allowed_url(url):
        raise ValueError("unsupported chat asset URL")
    cache_root = Path(root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    key = asset_key(url)
    existing = next((path for path in cache_root.glob(f"{key}.*") if path.is_file()), None)
    if existing:
        return existing
    request = Request(url, headers={"User-Agent": "YouTubeLiveArchiver/1.0"})
    try:
        with urlopen(request, timeout=ASSET_TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            if not _allowed_url(final_url):
                raise ValueError("chat asset redirect rejected")
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_ASSET_BYTES:
                raise ValueError("chat asset is too large")
            body = response.read(MAX_ASSET_BYTES + 1)
            if not body or len(body) > MAX_ASSET_BYTES:
                raise ValueError("chat asset is too large")
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            suffix = {
                "image/png": ".png",
                "image/jpeg": ".jpg",
                "image/webp": ".webp",
                "image/gif": ".gif",
            }.get(mime)
            if suffix is None:
                raise ValueError("unsupported chat asset content type")
            destination = (cache_root / f"{key}{suffix}").resolve()
            if cache_root not in destination.parents:
                raise ValueError("invalid chat asset key")
            destination.write_bytes(body)
            return destination
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise ValueError("chat asset unavailable") from error


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
