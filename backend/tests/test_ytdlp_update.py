import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from packaging.version import InvalidVersion

from app.config import settings
from app.services import ytdlp_update


def _release(tag="2026.10.23.233055", asset_name="yt-dlp.exe", content=b"validated-binary"):
    digest = hashlib.sha256(content).hexdigest()
    return {
        "tag_name": tag,
        "published_at": "2026-10-23T23:37:57Z",
        "assets": [{
            "name": asset_name,
            "browser_download_url": f"https://github.com/yt-dlp/yt-dlp-nightly-builds/releases/download/{tag}/{asset_name}",
            "digest": f"sha256:{digest}",
            "size": len(content),
        }],
    }, content


def test_release_channel_maps_only_to_official_repositories():
    assert ytdlp_update.release_repository("stable") == "yt-dlp/yt-dlp"
    assert ytdlp_update.release_repository("nightly") == "yt-dlp/yt-dlp-nightly-builds"
    assert ytdlp_update.release_repository("master") == "yt-dlp/yt-dlp-master-builds"
    with pytest.raises(ValueError):
        ytdlp_update.release_repository("other")


def test_platform_selects_the_matching_official_executable():
    assert ytdlp_update.asset_name_for_runtime("Windows", "AMD64") == "yt-dlp.exe"
    assert ytdlp_update.asset_name_for_runtime("Windows", "ARM64") == "yt-dlp_arm64.exe"
    assert ytdlp_update.asset_name_for_runtime("Darwin", "arm64") == "yt-dlp_macos"
    assert ytdlp_update.asset_name_for_runtime("Linux", "aarch64") == "yt-dlp_linux_aarch64"


def test_ytdlp_command_prefers_the_validated_managed_binary(tmp_path, monkeypatch):
    from app.services import youtube

    tool_dir = tmp_path / "tools" / "yt-dlp"
    tool_dir.mkdir(parents=True)
    executable = tool_dir / "yt-dlp-nightly.exe"
    executable.write_bytes(b"binary")
    (tool_dir / "current.json").write_text(json.dumps({
        "filename": executable.name,
        "version": "2026.10.23.233055",
        "channel": "nightly",
    }))
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "yt_dlp_path", "yt-dlp")

    assert youtube.ytdlp_command() == [str(executable.resolve())]


def test_install_validates_download_before_atomically_selecting_managed_binary(tmp_path, monkeypatch):
    release, payload = _release()
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(ytdlp_update.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ytdlp_update.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda _url, **_kwargs: payload)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        returncode=0, stdout=release["tag_name"] + "\n", stderr=""))

    result = ytdlp_update.install_release("nightly", release)

    managed = ytdlp_update.managed_executable()
    manifest = json.loads((tmp_path / "tools" / "yt-dlp" / "current.json").read_text())
    assert result["version"] == release["tag_name"]
    assert result["channel"] == "nightly"
    assert managed is not None and managed.is_file()
    assert managed.read_bytes() == payload
    assert manifest["channel"] == "nightly"
    assert manifest["version"] == release["tag_name"]


def test_same_release_is_skipped_unless_force_reinstall_is_requested(tmp_path, monkeypatch):
    release, payload = _release()
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(ytdlp_update.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ytdlp_update.platform, "machine", lambda: "AMD64")
    downloads = []
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda url, **_kwargs: downloads.append(url) or payload)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        returncode=0, stdout=release["tag_name"] + "\n", stderr=""))

    first = ytdlp_update.install_release("nightly", release)
    first_binary = ytdlp_update.managed_executable()
    skipped = ytdlp_update.install_release("nightly", release)
    forced = ytdlp_update.install_release("nightly", release, force=True)

    assert first["updated"] is True
    assert skipped["updated"] is False
    assert forced["updated"] is True
    assert ytdlp_update.managed_executable() != first_binary
    assert len(downloads) == 2


def test_unverified_binary_keeps_previous_manifest_active(tmp_path, monkeypatch):
    previous, previous_payload = _release(tag="2026.10.22.120000")
    newer, payload = _release(tag="2026.10.24.120000", content=b"new-binary")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(ytdlp_update.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ytdlp_update.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda _url, **_kwargs: previous_payload)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        returncode=0, stdout=previous["tag_name"] + "\n", stderr=""))
    ytdlp_update.install_release("nightly", previous)
    previous_binary = ytdlp_update.managed_executable()
    previous_manifest = json.loads((tmp_path / "tools" / "yt-dlp" / "current.json").read_text())
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda _url, **_kwargs: payload)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda command, **_kwargs: SimpleNamespace(
        returncode=0, stdout="2026.10.24.120001\n", stderr=""))

    with pytest.raises(RuntimeError, match="確認できません"):
        ytdlp_update.install_release("nightly", newer, force=True)

    assert ytdlp_update.managed_executable() == previous_binary
    assert json.loads((tmp_path / "tools" / "yt-dlp" / "current.json").read_text()) == previous_manifest


def test_failed_post_switch_check_restores_previous_managed_release(tmp_path, monkeypatch):
    previous, previous_payload = _release(tag="2026.10.22.120000")
    newer, new_payload = _release(tag="2026.10.24.120000", content=b"new-binary")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(ytdlp_update.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ytdlp_update.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda _url, **_kwargs: previous_payload)
    def report_staged_version(command, **_kwargs):
        staged_payload = Path(command[0]).read_bytes()
        version = previous["tag_name"] if staged_payload == previous_payload else newer["tag_name"]
        return SimpleNamespace(returncode=0, stdout=version + "\n", stderr="")

    monkeypatch.setattr(ytdlp_update.subprocess, "run", report_staged_version)
    ytdlp_update.install_release("nightly", previous)
    previous_binary = ytdlp_update.managed_executable()
    monkeypatch.setattr(ytdlp_update, "_read_url", lambda _url, **_kwargs: new_payload)
    monkeypatch.setattr(ytdlp_update, "_version", lambda: "bad-version")

    with pytest.raises(InvalidVersion):
        ytdlp_update.install_release("nightly", newer, force=True)

    assert ytdlp_update.managed_executable() == previous_binary


def test_version_check_uses_selected_channel_and_queues_automatic_update(monkeypatch):
    release, _ = _release()
    calls = []
    monkeypatch.setattr(ytdlp_update, "_version", lambda: "2026.10.21")
    monkeypatch.setattr(ytdlp_update, "_latest_release", lambda channel: release)
    monkeypatch.setattr(ytdlp_update, "managed_release_info", lambda: None)
    monkeypatch.setattr(ytdlp_update, "preferences", lambda _db=None: {
        "auto_update": True, "channel": "nightly",
    })
    monkeypatch.setattr(ytdlp_update, "trigger_update", lambda channel, force=False: calls.append((channel, force)) or True)

    result = ytdlp_update.check_version()

    assert result["version"] == "2026.10.21"
    assert result["latest_version"] == release["tag_name"]
    assert result["update_channel"] == "nightly"
    assert result["update_available"] is True
    assert calls == [("nightly", False)]


def test_version_check_bootstraps_managed_binary_when_current_ytdlp_is_unavailable(monkeypatch):
    release, _ = _release()
    calls = []

    def unavailable_version():
        raise RuntimeError("yt-dlp is unavailable")

    monkeypatch.setattr(ytdlp_update, "_version", unavailable_version)
    monkeypatch.setattr(ytdlp_update, "_latest_release", lambda channel: release)
    monkeypatch.setattr(ytdlp_update, "managed_release_info", lambda: None)
    monkeypatch.setattr(ytdlp_update, "managed_update_supported", lambda: True)
    monkeypatch.setattr(ytdlp_update, "preferences", lambda _db=None: {
        "auto_update": True, "channel": "nightly",
    })
    monkeypatch.setattr(ytdlp_update, "trigger_update", lambda channel, force=False: calls.append((channel, force)) or True)

    result = ytdlp_update.check_version()

    assert result["latest_version"] == release["tag_name"]
    assert result["update_available"] is True
    assert calls == [("nightly", True)]


def test_manual_update_can_recover_when_old_ytdlp_version_cannot_be_read(monkeypatch):
    release, _ = _release()
    calls = []
    versions = iter([RuntimeError("yt-dlp is unavailable"), release["tag_name"]])

    def version():
        result = next(versions)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ytdlp_update, "_active_recordings", lambda: False)
    monkeypatch.setattr(ytdlp_update, "_latest_release", lambda _channel: release)
    monkeypatch.setattr(ytdlp_update, "_version", version)
    monkeypatch.setattr(ytdlp_update, "install_release", lambda channel, item, force=False: calls.append((channel, item, force)) or {
        "updated": True, "version": release["tag_name"], "channel": channel,
    })
    monkeypatch.setattr(ytdlp_update, "_state", {})
    monkeypatch.setattr(ytdlp_update, "_update_in_progress", True)

    ytdlp_update._perform_update("nightly", False)

    assert calls == [("nightly", release, False)]
    assert ytdlp_update._state["previous_version"] is None
    assert ytdlp_update._state["update_phase"] == "completed"


def test_dependency_status_reports_deno_and_ejs_separately(monkeypatch):
    monkeypatch.setattr(ytdlp_update.shutil, "which", lambda command: "/usr/local/bin/deno" if command == "deno" else None)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="deno 2.5.0\n", stderr=""))
    monkeypatch.setattr(ytdlp_update.importlib.util, "find_spec", lambda name: object() if name == "yt_dlp_ejs" else None)

    status = ytdlp_update.dependency_status()

    assert status["deno_available"] is True
    assert status["deno_version"] == "2.5.0"
    assert status["deno_supported"] is True
    assert status["ejs_available"] is True


def test_recorder_dispatch_stops_during_ytdlp_update(monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(ytdlp_update, "_update_in_progress", True)
    monkeypatch.setattr(recorder, "running_counts", lambda: pytest.fail("dispatch must return early"))

    recorder.dispatch_recordings()

    assert ytdlp_update.is_update_in_progress()
