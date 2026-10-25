"""Check and safely install official yt-dlp release binaries."""

from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import threading
import time
import uuid
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from ..config import settings
from .youtube import run_ytdlp

log = logging.getLogger(__name__)
_lock = threading.Lock()
_update_in_progress = False
_state: dict = {
    "checked_at": None,
    "version": None,
    "installed_channel": None,
    "latest_version": None,
    "latest_release_date": None,
    "stale": None,
    "update_available": None,
    "latest_check_failed": None,
    "update_overdue": False,
    "update_enabled": settings.yt_dlp_auto_update,
    "update_channel": settings.yt_dlp_update_channel,
    "update_error": None,
    "update_phase": "idle",
    "previous_version": None,
    "managed": False,
    "managed_update_supported": settings.yt_dlp_path.strip().lower() in ("", "yt-dlp"),
}

CHANNEL_REPOSITORIES = {
    "stable": "yt-dlp/yt-dlp",
    "nightly": "yt-dlp/yt-dlp-nightly-builds",
    "master": "yt-dlp/yt-dlp-master-builds",
}
MIN_DENO_VERSION = Version("2.3.0")
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MAX_RELEASE_METADATA_BYTES = 2 * 1024 * 1024


def _tool_directory() -> Path:
    return Path(settings.data_dir).resolve() / "tools" / "yt-dlp"


def _manifest_path() -> Path:
    return _tool_directory() / "current.json"


def managed_release_info() -> dict | None:
    try:
        manifest = json.loads(_manifest_path().read_text(encoding="utf-8"))
        filename = manifest.get("filename")
        if not isinstance(filename, str) or Path(filename).name != filename:
            return None
        executable = (_tool_directory() / filename).resolve()
        if executable.parent != _tool_directory().resolve() or not executable.is_file():
            return None
        if manifest.get("channel") not in CHANNEL_REPOSITORIES or not isinstance(manifest.get("version"), str):
            return None
        return {**manifest, "path": executable}
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def managed_executable() -> Path | None:
    info = managed_release_info()
    return info["path"] if info else None


def managed_update_supported() -> bool:
    return settings.yt_dlp_path.strip().lower() in ("", "yt-dlp")


def release_repository(channel: str) -> str:
    try:
        return CHANNEL_REPOSITORIES[channel]
    except KeyError:
        raise ValueError("yt-dlp 更新チャンネルが不正です")


def asset_name_for_runtime(system: str, machine: str) -> str:
    machine = machine.lower().replace("_", "")
    if system == "Windows":
        if machine in ("arm64", "aarch64"):
            return "yt-dlp_arm64.exe"
        if machine in ("x86", "i386", "i686"):
            return "yt-dlp_x86.exe"
        return "yt-dlp.exe"
    if system == "Darwin":
        return "yt-dlp_macos"
    if system == "Linux":
        if machine in ("arm64", "aarch64"):
            return "yt-dlp_linux_aarch64"
        return "yt-dlp_linux"
    raise ValueError("この OS 用の yt-dlp 公式実行ファイルはありません")


def _read_url(url: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    request = Request(url, headers={"User-Agent": "YouTube-Live-Archiver", "Accept": "application/octet-stream"})
    try:
        with urlopen(request, timeout=30) as response:
            payload = response.read(max_bytes + 1)
    except Exception as exc:
        raise RuntimeError("yt-dlp の配布ファイルを取得できませんでした") from exc
    if len(payload) > max_bytes:
        raise RuntimeError("yt-dlp の配布ファイルがサイズ上限を超えています")
    return payload


def _read_release_json(url: str) -> dict:
    request = Request(url, headers={
        "User-Agent": "YouTube-Live-Archiver",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urlopen(request, timeout=15) as response:
            payload = response.read(MAX_RELEASE_METADATA_BYTES + 1)
        if len(payload) > MAX_RELEASE_METADATA_BYTES:
            raise RuntimeError("yt-dlp のリリース情報がサイズ上限を超えています")
        result = json.loads(payload)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("yt-dlp の最新版を確認できませんでした") from exc
    if not isinstance(result, dict):
        raise RuntimeError("yt-dlp のリリース情報が不正です")
    return result


def _release_download_url(value: str, repository: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.path.startswith(f"/{repository}/releases/download/")
    )


def _latest_release(channel: str) -> dict:
    repository = release_repository(channel)
    release = _read_release_json(f"https://api.github.com/repos/{repository}/releases/latest")
    tag = release.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise RuntimeError("yt-dlp のリリース番号がありません")
    try:
        Version(tag)
    except InvalidVersion as exc:
        raise RuntimeError("yt-dlp のリリース番号を解釈できません") from exc
    if not isinstance(release.get("assets"), list):
        raise RuntimeError("yt-dlp の配布ファイル一覧がありません")
    return release


def _sha256_for_asset(release: dict, asset: dict, repository: str) -> str:
    digest = asset.get("digest")
    if isinstance(digest, str) and re.fullmatch(r"sha256:[a-fA-F0-9]{64}", digest):
        return digest.split(":", 1)[1].lower()
    sums = next((item for item in release["assets"] if item.get("name") == "SHA2-256SUMS"), None)
    if not sums or not _release_download_url(str(sums.get("browser_download_url") or ""), repository):
        raise RuntimeError("yt-dlp の SHA-256 情報がありません")
    lines = _read_url(sums["browser_download_url"], max_bytes=1024 * 1024).decode("utf-8", "replace").splitlines()
    for line in lines:
        match = re.fullmatch(r"\s*([a-fA-F0-9]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2) == asset.get("name"):
            return match.group(1).lower()
    raise RuntimeError("yt-dlp の SHA-256 情報に対象ファイルがありません")


def _write_manifest(value: dict) -> None:
    directory = _tool_directory()
    directory.mkdir(parents=True, exist_ok=True)
    target = _manifest_path()
    temporary = directory / f".current-{uuid.uuid4().hex}.tmp"
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, separators=(",", ":"))
        file.flush()
        os.fsync(file.fileno())
    os.replace(temporary, target)


def _current_manifest() -> dict | None:
    info = managed_release_info()
    if not info:
        return None
    return {key: value for key, value in info.items() if key != "path"}


def install_release(channel: str, release: dict, *, force: bool = False) -> dict:
    repository = release_repository(channel)
    tag = release.get("tag_name")
    try:
        expected_version = str(Version(tag))
    except (InvalidVersion, TypeError):
        raise RuntimeError("yt-dlp のリリース番号が不正です")
    if not managed_update_supported():
        raise RuntimeError("YT_DLP_PATH に独自の実行ファイルが指定されているため、アプリ管理更新は利用できません")
    asset_name = asset_name_for_runtime(platform.system(), platform.machine())
    asset = next((item for item in release.get("assets", []) if item.get("name") == asset_name), None)
    if not asset:
        raise RuntimeError(f"この環境用の yt-dlp 配布ファイルがありません: {asset_name}")
    download_url = asset.get("browser_download_url")
    if not isinstance(download_url, str) or not _release_download_url(download_url, repository):
        raise RuntimeError("yt-dlp 配布元が公式リリースではありません")
    current = _current_manifest()
    if current and current.get("channel") == channel and current.get("version") == expected_version and not force:
        try:
            active_version = str(Version(_version()))
            if active_version == expected_version:
                return {"updated": False, "version": expected_version, "channel": channel}
        except Exception:
            log.warning("managed yt-dlp binary is not usable; reinstalling %s", expected_version)

    expected_digest = _sha256_for_asset(release, asset, repository)
    payload = _read_url(download_url)
    if asset.get("size") is not None and len(payload) != asset["size"]:
        raise RuntimeError("yt-dlp 配布ファイルのサイズが一致しません")
    if hashlib.sha256(payload).hexdigest() != expected_digest:
        raise RuntimeError("yt-dlp 配布ファイルの SHA-256 検証に失敗しました")

    directory = _tool_directory()
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if platform.system() == "Windows" else ""
    safe_tag = re.sub(r"[^A-Za-z0-9._-]", "_", tag)[:80]
    filename = f"yt-dlp-{channel}-{safe_tag}-{uuid.uuid4().hex[:10]}{suffix}"
    executable = directory / filename
    staged = directory / f".{uuid.uuid4().hex}.download"
    try:
        with staged.open("xb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        if platform.system() != "Windows":
            staged.chmod(0o755)
        version_result = subprocess.run(
            [str(staged), "--version"], capture_output=True, text=True, timeout=30, check=False,
        )
        actual_version = (version_result.stdout or "").strip()
        if version_result.returncode or not actual_version:
            raise RuntimeError("ダウンロードした yt-dlp を起動できませんでした")
        try:
            verified_version = str(Version(actual_version))
        except InvalidVersion as exc:
            raise RuntimeError("ダウンロードした yt-dlp の版を解釈できません") from exc
        if verified_version != expected_version:
            raise RuntimeError("ダウンロードした yt-dlp の版を確認できません。旧版を維持しました")
        os.replace(staged, executable)
        previous_manifest = _current_manifest()
        manifest = {
            "channel": channel,
            "version": verified_version,
            "tag": tag,
            "filename": filename,
            "asset_name": asset_name,
            "sha256": expected_digest,
            "published_at": release.get("published_at"),
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            _write_manifest(manifest)
            active_version = _version()
            if str(Version(active_version)) != verified_version:
                raise RuntimeError("切り替え後の yt-dlp 版を確認できません")
        except Exception:
            if previous_manifest:
                _write_manifest(previous_manifest)
            else:
                _manifest_path().unlink(missing_ok=True)
            executable.unlink(missing_ok=True)
            raise
        keep = {filename}
        if previous_manifest and isinstance(previous_manifest.get("filename"), str):
            keep.add(previous_manifest["filename"])
        for old_binary in directory.glob("yt-dlp-*"):
            if old_binary.name not in keep:
                try:
                    old_binary.unlink(missing_ok=True)
                except OSError:
                    # Windows can keep an older executable open until its process exits.
                    pass
        return {"updated": True, "version": verified_version, "channel": channel}
    finally:
        staged.unlink(missing_ok=True)


def _read_setting(db, key: str, default):
    from ..models import AppSetting

    row = db.get(AppSetting, key)
    value = row.value.get("value") if row and isinstance(row.value, dict) else None
    return default if value is None else value


def preferences(db=None) -> dict:
    channel = settings.yt_dlp_update_channel
    auto_update = settings.yt_dlp_auto_update
    close_db = False
    if db is None:
        try:
            from ..db import SessionLocal
            db = SessionLocal()
            close_db = True
        except Exception:
            db = None
    if db is not None:
        try:
            channel = _read_setting(db, "yt_dlp_update_channel", channel)
            auto_update = _read_setting(db, "yt_dlp_auto_update", auto_update)
        except Exception:
            if not close_db:
                raise
            log.exception("could not read yt-dlp preferences; using environment defaults")
        finally:
            if close_db:
                db.close()
    if channel not in CHANNEL_REPOSITORIES:
        channel = settings.yt_dlp_update_channel
    return {"channel": channel, "auto_update": bool(auto_update)}


def save_preferences(db, *, channel: str, auto_update: bool) -> dict:
    release_repository(channel)
    from ..models import AppSetting

    values = {"yt_dlp_update_channel": channel, "yt_dlp_auto_update": bool(auto_update)}
    for key, value in values.items():
        row = db.get(AppSetting, key)
        if row is None:
            db.add(AppSetting(key=key, value={"value": value}))
        else:
            row.value = {"value": value}
    db.commit()
    return {"channel": channel, "auto_update": bool(auto_update), **status()}


def _version() -> str:
    result = run_ytdlp(["--version"], capture_output=True, text=True, timeout=15, check=False)
    if result.returncode:
        raise RuntimeError((result.stderr or "yt-dlp --version failed")[-500:])
    version = (result.stdout or "").strip()
    if not version:
        raise RuntimeError("yt-dlp --version returned an empty version")
    return version


def _is_newer(latest: str, installed: str) -> bool:
    try:
        return Version(latest) > Version(installed)
    except InvalidVersion:
        return latest != installed


def _release_is_overdue(published_at: str | None, checked_at: datetime) -> bool:
    if not published_at:
        return False
    try:
        published = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return (checked_at - published.astimezone(timezone.utc)).total_seconds() >= 7 * 86400
    except ValueError:
        return False


def check_version() -> dict:
    checked_at = datetime.now(timezone.utc)
    prefs = preferences()
    try:
        version_error = None
        try:
            version = _version()
        except Exception as exc:
            version = None
            version_error = str(exc)[:500]
        release = _latest_release(prefs["channel"])
        latest_version = str(Version(release["tag_name"]))
        installed = managed_release_info()
        channel_changed = bool(installed and installed.get("channel") != prefs["channel"])
        stale = channel_changed or version is None or _is_newer(latest_version, version)
        can_manage = managed_update_supported()
        managed_needed = installed is None and can_manage
        update_required = stale or managed_needed
        state = {
            "checked_at": checked_at,
            "version": version,
            "installed_channel": installed.get("channel") if installed else None,
            "latest_version": latest_version,
            "latest_release_date": release.get("published_at"),
            "release_date": installed.get("published_at") if installed else None,
            "stale": stale,
            "update_available": update_required,
            "latest_check_failed": False,
            "update_overdue": _release_is_overdue(release.get("published_at"), checked_at) if stale else False,
            "update_enabled": prefs["auto_update"],
            "update_channel": prefs["channel"],
            "update_error": version_error,
            "managed": installed is not None,
            "managed_update_supported": can_manage,
        }
        with _lock:
            state["update_phase"] = _state.get("update_phase", "idle")
            state["previous_version"] = _state.get("previous_version")
            _state.update(state)
        if prefs["auto_update"] and update_required and can_manage:
            trigger_update(prefs["channel"], force=version is None)
    except Exception as exc:
        log.warning("yt-dlp version check failed: %s", exc)
        with _lock:
            _state.update({
                "checked_at": checked_at,
                "latest_version": None,
                "latest_release_date": None,
                "stale": None,
                "update_available": None,
                "latest_check_failed": True,
                "update_overdue": False,
                "update_enabled": prefs["auto_update"],
                "update_channel": prefs["channel"],
                "update_error": str(exc)[:500],
            })
    return status()


def _active_recordings() -> bool:
    from ..db import SessionLocal
    from ..models import Video
    from sqlalchemy import select
    from .recorder import running_counts

    with SessionLocal() as db:
        recording = db.scalar(select(Video.id).where(Video.status == "RECORDING").limit(1))
    live_count, archive_count = running_counts()
    return recording is not None or live_count > 0 or archive_count > 0


def _wait_until_idle() -> None:
    while _active_recordings():
        time.sleep(5)


def _perform_update(channel: str, force: bool) -> None:
    global _update_in_progress
    try:
        with _lock:
            _state.update({"update_phase": "waiting_for_recordings", "update_error": None})
        _wait_until_idle()
        with _lock:
            _state["update_phase"] = "downloading_and_verifying"
        release = _latest_release(channel)
        try:
            previous = _version()
        except Exception:
            previous = None
        result = install_release(channel, release, force=force)
        current_version = _version()
        with _lock:
            _state.update({
                "version": current_version,
                "latest_version": release.get("tag_name"),
                "latest_release_date": release.get("published_at"),
                "checked_at": datetime.now(timezone.utc),
                "installed_channel": channel,
                "release_date": release.get("published_at"),
                "managed": True,
                "managed_update_supported": True,
                "previous_version": previous,
                "update_error": None,
                "update_phase": "completed" if result["updated"] else "up_to_date",
                "stale": False,
                "update_available": False,
                "update_overdue": False,
            })
    except Exception as exc:
        log.exception("yt-dlp update failed")
        with _lock:
            _state.update({"update_error": str(exc)[:500], "update_phase": "failed"})
    finally:
        with _lock:
            _update_in_progress = False
            _state["update_in_progress"] = False


def trigger_update(channel: str | None = None, force: bool = False) -> bool:
    global _update_in_progress
    selected_channel = channel or preferences()["channel"]
    release_repository(selected_channel)
    if not managed_update_supported():
        raise ValueError("独自の YT_DLP_PATH が設定されているため、アプリ管理更新は利用できません")
    with _lock:
        if _update_in_progress:
            return False
        _update_in_progress = True
        _state.update({
            "update_in_progress": True,
            "update_phase": "waiting_for_recordings",
            "update_error": None,
            "update_channel": selected_channel,
        })
    worker = threading.Thread(target=_perform_update, args=(selected_channel, force), daemon=True,
                              name="yt-dlp-update")
    worker.start()
    return True


def dependency_status() -> dict:
    deno_path = shutil.which("deno")
    if not deno_path:
        executable = managed_executable()
        if executable is None:
            configured = settings.yt_dlp_path.strip()
            configured_path = Path(shutil.which(configured) or configured) if configured else None
            if configured_path and configured_path.is_file():
                executable = configured_path
        if executable:
            candidate = executable.with_name("deno.exe" if os.name == "nt" else "deno")
            if candidate.is_file():
                deno_path = str(candidate)
    deno_version = None
    deno_supported = False
    if deno_path:
        try:
            result = subprocess.run([deno_path, "--version"], capture_output=True, text=True, timeout=5, check=False)
            match = re.search(r"\bdeno\s+(\d+\.\d+\.\d+)\b", result.stdout or "", re.IGNORECASE)
            if result.returncode == 0 and match:
                deno_version = match.group(1)
                deno_supported = Version(deno_version) >= MIN_DENO_VERSION
        except (OSError, subprocess.SubprocessError, InvalidVersion):
            pass
    ejs_available = managed_executable() is not None
    if not ejs_available:
        try:
            ejs_available = importlib.util.find_spec("yt_dlp_ejs") is not None
        except (ImportError, ValueError):
            ejs_available = False
    return {
        "deno_available": deno_path is not None,
        "deno_version": deno_version,
        "deno_supported": deno_supported,
        "ejs_available": ejs_available,
    }


def is_update_in_progress() -> bool:
    with _lock:
        return _update_in_progress


def status() -> dict:
    with _lock:
        result = dict(_state)
        result["update_in_progress"] = _update_in_progress
    result.update(dependency_status())
    return result
