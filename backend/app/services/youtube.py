import json
import re
from datetime import datetime, timedelta
import subprocess
import os
import sys
import shutil
import importlib.util
import threading
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from ..config import settings
from .download import downloader_args


def ytdlp_command() -> list[str]:
    path = settings.yt_dlp_path.strip() or "yt-dlp"
    if path.lower() == "yt-dlp":
        from .ytdlp_update import managed_executable
        managed = managed_executable()
        if managed is not None:
            return [str(managed)]
    if path.lower().endswith(".py"):
        if Path(path).is_file():
            return [sys.executable, path]
    elif shutil.which(path) or Path(path).is_file():
        return [path]
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    return [path]


def _anonymous_args(args: list[str]) -> list[str]:
    if any(arg in ('--cookies', '--cookies-from-browser') or
           arg.startswith(('--cookies=', '--cookies-from-browser=')) for arg in args):
        raise ValueError('Cookie は動画のダウンロードにのみ使用できます')
    return ['--ignore-config', '--no-cookies', '--no-cookies-from-browser', *args]


def utf8_subprocess_kwargs(kwargs: dict) -> dict:
    """Make child Python tools write UTF-8 and decode text output as UTF-8.

    Windows otherwise uses the ANSI code page (cp932) for pipes, and Japanese
    titles in yt-dlp JSON make the reader thread crash, leaving stdout None."""
    env = dict(kwargs.get("env") or os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    kwargs["env"] = env
    if (kwargs.get("text") or kwargs.get("universal_newlines")) and not kwargs.get("encoding"):
        kwargs["encoding"] = "utf-8"
        kwargs.setdefault("errors", "replace")
    return kwargs


def run_ytdlp(args: list[str], *, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    from .youtube_ip import note_ipv6_failure, ytdlp_ip_args
    command = [*ytdlp_command(), *ytdlp_ip_args(args), *_anonymous_args(args)]
    result = subprocess.run(command, timeout=timeout, **utf8_subprocess_kwargs(kwargs))
    if result.returncode:
        note_ipv6_failure(str(getattr(result, "stderr", "") or ""))
    return result


def popen_ytdlp(args: list[str], *, allow_cookies: bool = False, **kwargs) -> subprocess.Popen:
    from .youtube_ip import ytdlp_ip_args
    from .youtube_cookies import release_snapshot, snapshot_args
    copied_args, snapshot = snapshot_args(args) if allow_cookies else (_anonymous_args(args), None)
    try:
        command = [*ytdlp_command(), *ytdlp_ip_args(args), *copied_args]
        if os.name == "nt":
            kwargs.setdefault("creationflags", subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            kwargs.setdefault("start_new_session", True)
        process = subprocess.Popen(command, **utf8_subprocess_kwargs(kwargs))
    except Exception:
        release_snapshot(snapshot)
        raise
    process._youtube_force_ipv6 = "--force-ipv6" in command
    if snapshot:
        def cleanup_cookie():
            try:
                process.wait()
            finally:
                release_snapshot(snapshot)
        threading.Thread(target=cleanup_cookie, name='ytdlp-cookie-cleanup', daemon=True).start()
    return process


def run_ytdlp_with_cookies(args: list[str], cookie_profile: str | None, *, timeout: float | None = None,
                           text: bool = True) -> subprocess.CompletedProcess | None:
    """Run a finite yt-dlp command with the configured YouTube cookies.

    Returns None when no cookie is configured. The cookie file is copied per
    process (see youtube_cookies.snapshot_args) and removed when it exits."""
    try:
        cookies = cookie_args(cookie_profile)
    except ValueError:
        return None
    if not cookies:
        return None
    process = popen_ytdlp([*cookies, *args], allow_cookies=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, text=text)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        raise
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def classify_ytdlp_error(stderr: str) -> str:
    message = (stderr or "").lower().replace("’", "'")
    # The apostrophe can arrive mojibaked (e.g. U+FFFD) when Windows stderr is decoded, so match the two phrases separately.
    if "sign in to confirm" in message and "not a bot" in message:
        return "RATE_LIMITED"
    checks = (
        ("RATE_LIMITED", ("http error 429", "this content isn't available, try again later")),
        ("NO_STREAMS_TAB", ("does not have a streams tab",)),
        ("PRIVATE", ("private video", "this is a private video", "this video is private")),
        ("DELETED", ("has been removed", "account associated with this video has been terminated", "no longer available", "video has been deleted")),
        ("MEMBERS_ONLY", ("members-only", "join this channel", "available to this channel's members")),
        ("UPCOMING", ("this live event will begin in", "premieres in")),
        ("UNAVAILABLE", ("video unavailable", "this video is unavailable")),
    )
    for status, phrases in checks:
        if any(phrase in message for phrase in phrases):
            return status
    return "UNKNOWN"


_UPCOMING_IN = re.compile(
    r"(?:this live event will begin in|premieres? in|premiere will begin in)\s+"
    r"(?:(a few moments|shortly)|(\d+)\s*(second|minute|hour|day)s?)",
    re.IGNORECASE)


def upcoming_start_from_error(message: str, at: datetime) -> datetime | None:
    """Estimate the start time from yt-dlp's "This live event will begin in N minutes."."""
    match = _UPCOMING_IN.search(message or "")
    if not match:
        return None
    if match.group(1):
        return at
    unit = {"second": "seconds", "minute": "minutes", "hour": "hours", "day": "days"}[match.group(3).lower()]
    return at + timedelta(**{unit: int(match.group(2))})


def cookie_args(profile: str | None) -> list[str]:
    profile = (profile or "").strip()
    if not profile:
        from .youtube_cookies import cookie_path, configured
        return ["--cookies", str(cookie_path())] if configured() else []
    if os.path.isfile(profile):
        return ["--cookies", profile]
    if profile.split(":", 1)[0] in {"chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi", "safari"}:
        return ["--cookies-from-browser", profile]
    raise ValueError("Cookie Profile は cookie ファイルのパス、または chrome[:profile] 形式で指定してください")


def inspect(url: str, cookie_profile: str | None = None) -> dict:
    """yt-dlp metadata。失敗/タイムアウトを成功扱いにしない。"""
    result = run_ytdlp(["--dump-single-json", "--skip-download", "--no-warnings", "--ignore-no-formats-error", *downloader_args(), url],
                       capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "yt-dlp failed")[-2000:])
    return json.loads(result.stdout)


_TAB_SUFFIXES = ("/streams", "/videos", "/live", "/shorts", "/playlists", "/featured")


def streams_url(url: str) -> str:
    """チャンネル直下の URL は tab 一覧 (Videos/Live/Shorts) を返してしまい、
    個々の配信が取れない。配信一覧である /streams を明示する。"""
    trimmed = url.split("?", 1)[0].rstrip("/")
    for suffix in _TAB_SUFFIXES:
        if trimmed.endswith(suffix):
            trimmed = trimmed[:-len(suffix)]
            break
    if "/channel/" in trimmed or "/@" in trimmed or "/c/" in trimmed or "/user/" in trimmed:
        return trimmed + "/streams"
    return trimmed


def channel_entries(url: str, cookie_profile: str | None = None, limit: int = 10) -> list[dict]:
    return channel_scan(url, cookie_profile, limit)[0]


def channel_scan(url: str, cookie_profile: str | None = None, limit: int = 10) -> tuple[list[dict], dict]:
    """Fetch the existing channel scan once and retain its outer metadata too."""
    url = streams_url(url)
    result = run_ytdlp(["--flat-playlist", "--dump-single-json", "--playlist-end", str(max(10, limit)), url],
                       capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "channel scan failed")[-2000:])
    data = json.loads(result.stdout)
    return data.get("entries") or [], data


def resolve_channel(url: str) -> tuple[str, str]:
    """URL / @handle / UC ID を yt-dlp で解決し、正規 channel_id と URL を返す。"""
    value = url.strip()
    if value.startswith("UC") and len(value) == 24:
        return value, f"https://www.youtube.com/channel/{value}"
    if value.startswith("@"):
        value = f"https://www.youtube.com/{value}"
    elif not value.startswith(("http://", "https://")):
        value = f"https://{value}"
    parts = urlsplit(value)
    host = (parts.hostname or "").lower().removeprefix("www.")
    if host not in ("youtube.com", "m.youtube.com", "youtu.be"):
        raise ValueError("YouTube のチャンネル URL ではありません")
    clean_path = parts.path.rstrip("/")
    if host == "youtu.be":
        video_id = clean_path.lstrip("/").split("/", 1)[0]
        if not video_id:
            raise ValueError("YouTube 動画 URL が不正です")
        value = f"https://www.youtube.com/watch?v={video_id}"
        parts = urlsplit(value)
        host = "youtube.com"
        clean_path = parts.path
    for suffix in _TAB_SUFFIXES:
        if clean_path.endswith(suffix):
            clean_path = clean_path[:-len(suffix)]
            break
    if clean_path.startswith("/channel/"):
        channel_id = clean_path.split("/", 2)[2]
        if channel_id.startswith("UC") and len(channel_id) == 24:
            return channel_id, f"https://www.youtube.com/channel/{channel_id}"
    canonical_input = urlunsplit(("https", "www.youtube.com", clean_path, parts.query, ""))
    result = run_ytdlp(["--flat-playlist", "--dump-single-json", "--playlist-end", "1", canonical_input],
                       capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise ValueError((result.stderr or "チャンネルを解決できません")[-1000:])
    data = json.loads(result.stdout)
    channel_id = data.get("channel_id") or data.get("id")
    if not channel_id and data.get("entries"):
        channel_id = data["entries"][0].get("channel_id")
    if not channel_id or not str(channel_id).startswith("UC"):
        raise ValueError("YouTube Channel ID (UC...) を取得できません")
    return str(channel_id), f"https://www.youtube.com/channel/{channel_id}"


def channel_metadata(url: str, cookie_profile: str | None = None) -> dict:
    """Fetch channel page metadata; callers treat failures as non-fatal."""
    # --flat-playlist を付けないとチャンネル内の全動画を列挙しに行き、数分かかる。
    # 必要なのはチャンネル自体のメタ情報だけなので列挙は 1 件に絞る。
    result = run_ytdlp(["--dump-single-json", "--skip-download", "--no-warnings",
                        "--flat-playlist", "--playlist-end", "1",
                        url], capture_output=True, text=True,
                       timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "channel metadata unavailable")[-1500:])
    return json.loads(result.stdout)
