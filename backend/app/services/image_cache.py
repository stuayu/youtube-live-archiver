"""Shared safe image fetching and remote thumbnail cache helpers."""
from pathlib import Path
from hashlib import sha256
import threading
import time
from urllib.parse import urlparse
from urllib.error import URLError
from urllib.request import Request

THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024
IMAGE_TIMEOUT_SECONDS = 4
IMAGE_SUFFIXES = (".png", ".jpg", ".webp", ".gif")
FAILURE_RETRY_SECONDS = 600
_failed_until: dict[str, float] = {}
_failure_lock = threading.Lock()


def asset_key(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def urlopen(request, *, timeout: float):
    """Compatibility seam backed by the configured address-family transport."""
    from .http_ip import open_url
    return open_url(request, timeout=timeout)


def cached_path(key: str, root: str | Path) -> Path | None:
    cache_root = Path(root)
    return next((path for suffix in IMAGE_SUFFIXES
                 if (path := cache_root / f"{key}{suffix}").is_file()), None)


def fetch_image(url: str, root: str | Path, *, allowed, max_bytes: int,
                context: str, opener=None, timeout_seconds: float = IMAGE_TIMEOUT_SECONDS) -> Path:
    """Fetch an image with caller-specific host policy and shared safety checks."""
    if not allowed(url):
        raise ValueError(f"unsupported {context} URL")
    cache_root = Path(root).resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    key = asset_key(url)
    existing = cached_path(key, cache_root)
    if existing:
        return existing
    request = Request(url, headers={"User-Agent": "YouTubeLiveArchiver/1.0"})
    try:
        if opener is None:
            opener = urlopen
        with opener(request, timeout=timeout_seconds) as response:
            if not allowed(response.geturl()):
                raise ValueError(f"{context} redirect rejected")
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError(f"{context} is too large")
            body = response.read(max_bytes + 1)
            if not body or len(body) > max_bytes:
                raise ValueError(f"{context} is too large")
            mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            suffix = {"image/png": ".png", "image/jpeg": ".jpg",
                      "image/webp": ".webp", "image/gif": ".gif"}.get(mime)
            if suffix is None:
                raise ValueError(f"unsupported {context} content type")
            destination = (cache_root / f"{key}{suffix}").resolve()
            if cache_root not in destination.parents:
                raise ValueError(f"invalid {context} cache key")
            destination.write_bytes(body)
            return destination
    except (URLError, TimeoutError, OSError, ValueError) as error:
        raise ValueError(f"{context} unavailable") from error


def allowed_thumbnail_url(url: str | None) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "").lower() in {
        "i.ytimg.com", "i9.ytimg.com", "img.youtube.com"
    }


def thumbnail_path(url: str, root: str | Path) -> Path | None:
    return cached_path(asset_key(url), root)


def resolve_thumbnail(url: str | None, root: str | Path,
                      fetch_enabled: bool = True) -> Path | None:
    if not allowed_thumbnail_url(url):
        return None
    assert url is not None
    existing = thumbnail_path(url, root)
    if existing or not fetch_enabled:
        return existing
    with _failure_lock:
        if _failed_until.get(url, 0) > time.monotonic():
            return None
    try:
        return fetch_image(url, root, allowed=allowed_thumbnail_url,
                           max_bytes=THUMBNAIL_MAX_BYTES,
                           context="thumbnail")
    except ValueError:
        with _failure_lock:
            _failed_until[url] = time.monotonic() + FAILURE_RETRY_SECONDS
        return None


def cleanup_thumbnails(root: str | Path, older_than_seconds: int) -> None:
    cutoff = time.time() - older_than_seconds
    folder = Path(root)
    if not folder.is_dir():
        return
    for path in folder.iterdir():
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
