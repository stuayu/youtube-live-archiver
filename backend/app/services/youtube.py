import json
import subprocess
import os
from ..config import settings
from .download import downloader_args


def cookie_args(profile: str | None) -> list[str]:
    profile = (profile or "").strip()
    if not profile:
        return []
    if os.path.isfile(profile):
        return ["--cookies", profile]
    if profile.split(":", 1)[0] in {"chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi", "safari"}:
        return ["--cookies-from-browser", profile]
    raise ValueError("Cookie Profile は cookie ファイルのパス、または chrome[:profile] 形式で指定してください")


def inspect(url: str, cookie_profile: str | None = None) -> dict:
    """yt-dlp metadata。失敗/タイムアウトを成功扱いにしない。"""
    result = subprocess.run([settings.yt_dlp_path, "--dump-single-json", "--skip-download", "--no-warnings", *downloader_args(), *cookie_args(cookie_profile), url],
                            capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "yt-dlp failed")[-2000:])
    return json.loads(result.stdout)


_TAB_SUFFIXES = ("/streams", "/videos", "/live", "/shorts", "/playlists", "/featured")


def streams_url(url: str) -> str:
    """チャンネル直下の URL は tab 一覧 (Videos/Live/Shorts) を返してしまい、
    個々の配信が取れない。配信一覧である /streams を明示する。"""
    trimmed = url.split("?", 1)[0].rstrip("/")
    if trimmed.endswith(_TAB_SUFFIXES):
        return trimmed
    if "/channel/" in trimmed or "/@" in trimmed or "/c/" in trimmed or "/user/" in trimmed:
        return trimmed + "/streams"
    return trimmed


def channel_entries(url: str, cookie_profile: str | None = None, limit: int = 10) -> list[dict]:
    return channel_scan(url, cookie_profile, limit)[0]


def channel_scan(url: str, cookie_profile: str | None = None, limit: int = 10) -> tuple[list[dict], dict]:
    """Fetch the existing channel scan once and retain its outer metadata too."""
    url = streams_url(url)
    result = subprocess.run([settings.yt_dlp_path, "--flat-playlist", "--dump-single-json", "--playlist-end", str(max(10, limit)), *cookie_args(cookie_profile), url],
                            capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "channel scan failed")[-2000:])
    data = json.loads(result.stdout)
    return data.get("entries") or [], data


def resolve_channel(url: str) -> tuple[str, str]:
    """URL / @handle / UC ID を yt-dlp で解決し、正規 channel_id と URL を返す。"""
    if url.startswith("UC") and len(url) == 24:
        canonical = f"https://www.youtube.com/channel/{url}"
    elif url.startswith("@"):
        canonical = f"https://www.youtube.com/{url}"
    else:
        canonical = url
    if "/channel/UC" in canonical:
        channel_id = canonical.split("/channel/", 1)[1].split("?", 1)[0].rstrip("/")
        if len(channel_id) == 24:
            return channel_id, canonical
    result = subprocess.run([settings.yt_dlp_path, "--flat-playlist", "--dump-single-json", "--playlist-end", "1", canonical],
                            capture_output=True, text=True, timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode != 0:
        raise ValueError((result.stderr or "チャンネルを解決できません")[-1000:])
    data = json.loads(result.stdout)
    channel_id = data.get("channel_id") or data.get("id")
    if not channel_id and data.get("entries"):
        channel_id = data["entries"][0].get("channel_id")
    if not channel_id or not str(channel_id).startswith("UC"):
        raise ValueError("YouTube Channel ID (UC...) を取得できません")
    return str(channel_id), canonical


def channel_metadata(url: str, cookie_profile: str | None = None) -> dict:
    """Fetch channel page metadata; callers treat failures as non-fatal."""
    # --flat-playlist を付けないとチャンネル内の全動画を列挙しに行き、数分かかる。
    # 必要なのはチャンネル自体のメタ情報だけなので列挙は 1 件に絞る。
    result = subprocess.run([settings.yt_dlp_path, "--dump-single-json", "--skip-download", "--no-warnings",
                             "--flat-playlist", "--playlist-end", "1",
                             *cookie_args(cookie_profile), url], capture_output=True, text=True,
                            timeout=settings.remote_timeout_seconds, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "channel metadata unavailable")[-1500:])
    return json.loads(result.stdout)
