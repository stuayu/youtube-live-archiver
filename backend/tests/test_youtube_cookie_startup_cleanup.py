import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from app.config import settings
from app.services import youtube_cookies


YOUTUBE_COOKIE = (
    b'# Netscape HTTP Cookie File\n'
    b'.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tfake-youtube-secret\n'
)


def test_startup_cleanup_removes_orphan_copy_and_keeps_registered_cookie(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    # No yt-dlp runs here; keep the check independent of other users' or system processes on this machine.
    monkeypatch.setattr(youtube_cookies.psutil, 'process_iter', lambda *args: [])
    youtube_cookies.save(YOUTUBE_COOKIE)
    orphan = youtube_cookies.cookie_path().parent / '.active-cookie-orphan'
    orphan.write_bytes(YOUTUBE_COOKIE)

    youtube_cookies.cleanup_orphan_snapshots()

    assert not orphan.exists()
    assert youtube_cookies.cookie_path().read_bytes() == YOUTUBE_COOKIE


def test_startup_cleanup_preserves_copy_used_by_running_process(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    youtube_cookies.save(YOUTUBE_COOKIE)
    in_use = youtube_cookies.cookie_path().parent / '.active-cookie-in-use'
    in_use.write_bytes(YOUTUBE_COOKIE)
    process = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', '--cookies', str(in_use)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Scan only the child so unrelated processes on this machine, which may be uninspectable, do not decide the result.
    child = youtube_cookies.psutil.Process(process.pid)
    monkeypatch.setattr(youtube_cookies.psutil, 'process_iter', lambda *args: [child])
    try:
        youtube_cookies.cleanup_orphan_snapshots()
        assert in_use.is_file()
    finally:
        process.kill()
        process.wait()

    youtube_cookies.cleanup_orphan_snapshots()
    assert not in_use.exists()


CURRENT_UID = getattr(os, 'getuid', lambda: 0)()


class FakeProcess:
    def __init__(self, name, command=None, error=None, owner=CURRENT_UID, owner_error=None):
        self.info = {'name': name}
        self._command = command
        self._error = error
        self._owner = owner
        self._owner_error = owner_error

    def cmdline(self):
        if self._error is not None:
            raise self._error
        return self._command

    def uids(self):
        if self._owner_error is not None:
            raise self._owner_error
        return SimpleNamespace(real=self._owner)


def _use_fake_processes(monkeypatch, processes):
    monkeypatch.setattr(youtube_cookies.psutil, 'process_iter', lambda *args: processes)


def _orphan_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    youtube_cookies.save(YOUTUBE_COOKIE)
    orphan = youtube_cookies.cookie_path().parent / '.active-cookie-alias'
    orphan.write_bytes(YOUTUBE_COOKIE)
    return orphan


def test_startup_cleanup_keeps_copy_used_by_renamed_ytdlp_process(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('ytdlp-latest', ['/opt/tools/ytdlp-latest', '--cookies', str(orphan), 'https://example.com']),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()


def test_startup_cleanup_keeps_copy_passed_with_equals_form(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('yt_dlp', ['yt_dlp', f'--cookies={orphan}', 'https://example.com']),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()


def test_startup_cleanup_keeps_copy_when_ytdlp_process_cannot_be_inspected(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('ytdlp-latest', error=youtube_cookies.psutil.AccessDenied(pid=1, name='ytdlp-latest')),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()


def test_startup_cleanup_ignores_unrelated_process_that_cannot_be_inspected(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('launchd', error=youtube_cookies.psutil.AccessDenied(pid=1, name='launchd'), owner=CURRENT_UID + 1),
        FakeProcess('analyticsd', error=youtube_cookies.psutil.AccessDenied(pid=2, name='analyticsd'),
                    owner=CURRENT_UID + 1),
        FakeProcess('Finder', ['/System/Library/CoreServices/Finder']),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert not orphan.exists()


@pytest.mark.parametrize('name', ['login', 'launchd', 'Finder', 'python3', ''])
def test_startup_cleanup_stops_when_same_user_process_cannot_be_inspected(tmp_path, monkeypatch, name):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess(name, error=youtube_cookies.psutil.AccessDenied(pid=3, name=name)),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()


def test_startup_cleanup_stops_when_process_owner_cannot_be_determined(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('login', error=youtube_cookies.psutil.AccessDenied(pid=3, name='login'),
                    owner_error=youtube_cookies.psutil.AccessDenied(pid=3, name='login')),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()


def test_startup_cleanup_ignores_other_user_ytdlp_process_that_cannot_be_inspected(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    _use_fake_processes(monkeypatch, [
        FakeProcess('yt-dlp', error=youtube_cookies.psutil.AccessDenied(pid=4, name='yt-dlp'),
                    owner=CURRENT_UID + 1),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert not orphan.exists()


def test_startup_cleanup_stops_on_windows_when_process_cannot_be_inspected(tmp_path, monkeypatch):
    orphan = _orphan_copy(tmp_path, monkeypatch)
    monkeypatch.setattr(youtube_cookies.os, 'name', 'nt')
    _use_fake_processes(monkeypatch, [
        FakeProcess('launchd', error=youtube_cookies.psutil.AccessDenied(pid=5, name='launchd'),
                    owner=CURRENT_UID + 1),
    ])

    youtube_cookies.cleanup_orphan_snapshots()

    assert orphan.is_file()
