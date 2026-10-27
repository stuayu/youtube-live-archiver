from pathlib import Path
import subprocess
import sys
import time
import pytest

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.services.youtube import cookie_args


YOUTUBE_COOKIE = (
    b'# Netscape HTTP Cookie File\n'
    b'.youtube.com\tTRUE\t/\tTRUE\t2147483647\tSID\tfake-youtube-secret\n'
    b'.example.org\tTRUE\t/\tTRUE\t2147483647\tSID\tunrelated-secret\n'
)


def test_cookie_registration_is_private_filtered_and_used_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    client = TestClient(app)
    endpoint = '/api/settings/youtube-cookies'
    assert client.get(endpoint).json()['configured'] is False
    response = client.put(endpoint, content=YOUTUBE_COOKIE, headers={
        'Content-Type': 'application/octet-stream', 'Origin': 'http://testserver',
    })
    assert response.status_code == 200, response.text
    assert response.json()['configured'] is True
    assert 'fake-youtube-secret' not in response.text
    cookie_path = Path(cookie_args(None)[1])
    assert b'fake-youtube-secret' in cookie_path.read_bytes()
    assert b'unrelated-secret' not in cookie_path.read_bytes()
    override = tmp_path / 'override.txt'
    override.write_text('override')
    assert cookie_args(str(override)) == ['--cookies', str(override)]

    invalid = client.put(endpoint, content=b'not a cookie file', headers={
        'Content-Type': 'application/octet-stream',
    })
    assert invalid.status_code == 422
    assert cookie_path.is_file()
    assert b'fake-youtube-secret' in cookie_path.read_bytes()

    assert client.delete(endpoint, headers={'Origin': 'http://testserver'}).status_code == 200
    assert client.get(endpoint).json()['configured'] is False
    assert cookie_args(None) == []
    assert cookie_args(str(override)) == ['--cookies', str(override)]


def test_cookie_upload_rejects_cross_origin_and_large_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    client = TestClient(app)
    endpoint = '/api/settings/youtube-cookies'
    cross_origin = client.put(endpoint, content=YOUTUBE_COOKIE, headers={
        'Content-Type': 'application/octet-stream', 'Origin': 'https://other.example',
    })
    assert cross_origin.status_code == 403
    too_large = client.put(endpoint, content=b'x' * (1024 * 1024 + 1), headers={
        'Content-Type': 'application/octet-stream',
    })
    assert too_large.status_code == 413
    assert client.get(endpoint).json()['configured'] is False


def test_active_recording_uses_its_own_cookie_copy_when_registration_changes(tmp_path, monkeypatch):
    from app.services import youtube

    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    client = TestClient(app)
    endpoint = '/api/settings/youtube-cookies'
    headers = {'Content-Type': 'application/octet-stream'}
    assert client.put(endpoint, content=YOUTUBE_COOKIE, headers=headers).status_code == 200
    script = tmp_path / 'fake_ytdlp.py'
    script.write_text('''import pathlib, sys, time
cookie = pathlib.Path(sys.argv[sys.argv.index('--cookies') + 1])
started, release, output = map(pathlib.Path, sys.argv[-3:])
started.write_text(str(cookie))
while not release.exists():
    time.sleep(0.02)
output.write_bytes(cookie.read_bytes())
cookie.write_text('yt-dlp rewrote this private copy')
''')
    started, release, output = (tmp_path / x for x in ('started', 'release', 'result'))
    monkeypatch.setattr(youtube, 'ytdlp_command', lambda: [sys.executable, str(script)])
    process = youtube.popen_ytdlp([*cookie_args(None), str(started), str(release), str(output)],
                                  allow_cookies=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert started.exists()
        snapshot = Path(started.read_text())
        assert snapshot != Path(cookie_args(None)[1])
        new_cookie = YOUTUBE_COOKIE.replace(b'fake-youtube-secret', b'replacement-secret')
        assert client.put(endpoint, content=new_cookie, headers=headers).status_code == 200
        assert client.delete(endpoint).status_code == 200
        assert snapshot.is_file()
        release.touch()
        _, stderr = process.communicate(timeout=5)
        assert process.returncode == 0, stderr
        assert b'fake-youtube-secret' in output.read_bytes()
        assert b'replacement-secret' not in output.read_bytes()
        deadline = time.monotonic() + 5
        while snapshot.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not snapshot.exists()
        assert cookie_args(None) == []
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_monitoring_and_channel_resolution_do_not_use_registered_cookies(tmp_path, monkeypatch):
    from app.services import youtube, youtube_cookies

    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    youtube_cookies.save(YOUTUBE_COOKIE)
    seen = []
    def run(args, **kwargs):
        seen.append(args)
        return subprocess.CompletedProcess(args, 0, stdout='{"channel_id":"UC1234567890123456789012","entries":[]}')
    monkeypatch.setattr(youtube, 'run_ytdlp', run)
    assert youtube.resolve_channel('@example')[0] == 'UC1234567890123456789012'
    youtube.inspect('https://youtube.com/watch?v=abcdefghijk', str(youtube_cookies.cookie_path()))
    youtube.channel_scan('https://youtube.com/@example', str(youtube_cookies.cookie_path()))
    youtube.channel_metadata('https://youtube.com/@example', str(youtube_cookies.cookie_path()))
    assert all('--cookies' not in args and '--cookies-from-browser' not in args for args in seen)


def test_non_recording_ytdlp_invocations_ignore_config_and_reject_cookies(monkeypatch):
    from app.services import youtube

    commands = []
    monkeypatch.setattr(youtube, 'ytdlp_command', lambda: ['yt-dlp'])
    monkeypatch.setattr(subprocess, 'run', lambda command, **kwargs: commands.append(command) or
                        subprocess.CompletedProcess(command, 0, stdout='{}', stderr=''))
    youtube.run_ytdlp(['--dump-single-json', 'https://youtube.com/@example'])
    assert '--ignore-config' in commands[-1]
    assert '--no-cookies' in commands[-1]
    assert '--no-cookies-from-browser' in commands[-1]
    with pytest.raises(ValueError):
        youtube.run_ytdlp(['--cookies', '/private/cookie.txt', '--dump-single-json'])


def test_removal_cleans_snapshot_left_by_a_stopped_process(tmp_path, monkeypatch):
    from app.services import youtube_cookies

    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    # No yt-dlp runs here; keep the check independent of other users' or system processes on this machine.
    monkeypatch.setattr(youtube_cookies.psutil, 'process_iter', lambda *args: [])
    youtube_cookies.save(YOUTUBE_COOKIE)
    orphan = youtube_cookies.cookie_path().parent / '.active-cookie-orphan'
    orphan.write_bytes(YOUTUBE_COOKIE)
    youtube_cookies.remove()
    assert not orphan.exists()
