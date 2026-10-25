import subprocess

import pytest

from app.config import settings
from app.services import youtube_ip
from app.services.youtube import popen_ytdlp, run_ytdlp


def test_ytdlp_force_flags_for_all_modes_and_version(monkeypatch):
    captured = []

    def run(command, **_kwargs):
        captured.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("app.services.youtube.ytdlp_command", lambda: ["yt-dlp"])
    monkeypatch.setattr(youtube_ip, "_reachable", True)
    for mode, expected in (("prefer_ipv6", "--force-ipv6"),
                           ("ipv6", "--force-ipv6"),
                           ("ipv4", "--force-ipv4"), ("auto", None)):
        monkeypatch.setattr(settings, "youtube_ip_mode", mode)
        run_ytdlp(["--dump-single-json"])
        flags = [item for item in captured[-1] if item.startswith("--force-ip")]
        assert flags == ([expected] if expected else [])
    run_ytdlp(["--version"])
    assert "--force-ipv6" not in captured[-1]


def test_prefer_ipv6_probe_cache_and_failure_retry_state(monkeypatch):
    calls = []
    monkeypatch.setattr(settings, "youtube_ip_mode", "prefer_ipv6")
    monkeypatch.setattr(youtube_ip, "_reachable", None)
    monkeypatch.setattr(youtube_ip, "probe_ipv6", lambda: (calls.append(1) or True, None))
    monkeypatch.setattr(youtube_ip, "_checked_at", None)
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == []
    assert youtube_ip.probe_if_due() == (True, None)
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == ["--force-ipv6"]
    assert youtube_ip.probe_if_due() == (True, None)
    assert len(calls) == 1
    monkeypatch.setattr(youtube_ip, "probe_ipv6", lambda: (calls.append(2) or False, "unreachable"))
    monkeypatch.setattr(youtube_ip, "_last_probe", youtube_ip.time.monotonic()
                        - youtube_ip.PROBE_INTERVAL_SECONDS - 1)
    assert youtube_ip.probe_if_due() == (False, "unreachable")
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == []
    assert len(calls) == 2
    monkeypatch.setattr(youtube_ip, "probe_ipv6", lambda: (calls.append(3) or True, None))
    monkeypatch.setattr(youtube_ip, "_last_probe", youtube_ip.time.monotonic()
                        - youtube_ip.PROBE_RETRY_SECONDS - 1)
    assert youtube_ip.probe_if_due() == (True, None)
    assert len(calls) == 3


def test_only_ipv6_route_errors_disable_preference(monkeypatch):
    import threading

    monkeypatch.setattr(settings, "youtube_ip_mode", "prefer_ipv6")
    monkeypatch.setattr(youtube_ip, "_reachable", True)
    monkeypatch.setattr(youtube_ip, "_probe_wakeup", threading.Event())
    youtube_ip.note_ipv6_failure("HTTP Error 429: Too Many Requests")
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == ["--force-ipv6"]
    youtube_ip.note_ipv6_failure("OSError: [Errno 65] No route to host")
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == []
    assert "No route" in youtube_ip.status()["probe_error"]
    assert youtube_ip._probe_wakeup.is_set()


def test_application_https_tries_ipv6_before_ipv4(monkeypatch):
    import socket
    from app.services import http_ip

    families = []

    class FakeSocket:
        def __init__(self, family, *_args):
            self.family = family

        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            if self.family == socket.AF_INET6:
                raise OSError("network unreachable")

        def close(self):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(sock, server_hostname):
            assert server_hostname == "www.youtube.com"
            return sock

    def addresses(_host, _port, family, _socktype):
        families.append(family)
        return [(family, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0)
                 if family == socket.AF_INET6 else ("127.0.0.1", 443))]

    monkeypatch.setattr(settings, "youtube_ip_mode", "prefer_ipv6")
    monkeypatch.setattr(youtube_ip, "_reachable", True)
    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    connection = http_ip._FamilyHTTPSConnection("www.youtube.com", 443)
    connection._context = FakeContext()
    connection.connect()
    assert families == [socket.AF_INET6, socket.AF_INET]
    assert connection.sock.family == socket.AF_INET


def test_cached_ipv6_failure_skips_ipv6_for_application_https(monkeypatch):
    import socket
    from app.services import http_ip

    families = []

    class FakeSocket:
        def __init__(self, family, *_args):
            self.family = family

        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            pass

        def close(self):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(sock, server_hostname):
            return sock

    def addresses(_host, _port, family, _socktype):
        families.append(family)
        return [(family, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(settings, "youtube_ip_mode", "prefer_ipv6")
    monkeypatch.setattr(youtube_ip, "_reachable", False)
    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    connection = http_ip._FamilyHTTPSConnection("www.youtube.com", 443)
    connection._context = FakeContext()
    connection.connect()
    assert families == [socket.AF_INET]


def test_ipv6_connection_timeout_disables_preference_but_429_does_not(monkeypatch):
    monkeypatch.setattr(settings, "youtube_ip_mode", "prefer_ipv6")
    monkeypatch.setattr(youtube_ip, "_reachable", True)
    youtube_ip.note_ipv6_failure("TimeoutError: timed out")
    assert youtube_ip.ytdlp_ip_args(["--dump-single-json"]) == []


@pytest.mark.parametrize("mode,expected", [
    ("ipv6", "v6"), ("ipv4", "v4"), ("auto", "auto"),
])
def test_application_https_forced_modes_select_the_configured_family(monkeypatch, mode, expected):
    import socket
    from app.services import http_ip

    families = []

    class FakeSocket:
        def __init__(self, family, *_args):
            self.family = family

        def settimeout(self, _timeout):
            pass

        def connect(self, _address):
            pass

        def close(self):
            pass

    class FakeContext:
        @staticmethod
        def wrap_socket(sock, server_hostname):
            assert server_hostname == "www.youtube.com"
            return sock

    def addresses(_host, _port, family, _socktype):
        families.append(family)
        address = ("::1", 443, 0, 0) if family == socket.AF_INET6 else ("127.0.0.1", 443)
        return [(family, socket.SOCK_STREAM, 6, "", address)]

    monkeypatch.setattr(settings, "youtube_ip_mode", mode)
    monkeypatch.setattr(socket, "getaddrinfo", addresses)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    connection = http_ip._FamilyHTTPSConnection("www.youtube.com", 443)
    connection._context = FakeContext()
    connection.connect()
    required = {"v6": socket.AF_INET6, "v4": socket.AF_INET,
                "auto": socket.AF_UNSPEC}[expected]
    assert families == [required]
    assert connection.sock.family == required


def test_recording_prefix_slice_gets_one_ip_flag(monkeypatch, tmp_path):
    from app.services import recorder, youtube

    captured = []

    class FakeProcess:
        pid = 12345

    monkeypatch.setattr(settings, "youtube_ip_mode", "ipv4")
    monkeypatch.setattr(youtube, "ytdlp_command", lambda: ["python", "fake.py"])
    monkeypatch.setattr(youtube.subprocess, "Popen",
                        lambda command, **_kwargs: captured.append(command) or FakeProcess())
    monkeypatch.setattr(recorder, "capture_stderr", lambda _process: None)
    args = [*youtube.ytdlp_command(), "-o", str(tmp_path / "video.%(ext)s"),
            "https://youtube.com/watch?v=abcdefghijk"]
    recorder.popen_recording(args[len(youtube.ytdlp_command()):])
    assert captured[0].count("--force-ipv4") == 1
    assert captured[0][-3:] == ["-o", str(tmp_path / "video.%(ext)s"),
                                "https://youtube.com/watch?v=abcdefghijk"]


def test_tee_dash_sidecars_are_normalized_in_recording_folder(fake_youtube, tmp_path):
    from app.services.recorder import _normalize_tee_sidecars

    backend = tmp_path / "backend"
    target = tmp_path / "recordings" / "video"
    backend.mkdir()
    target.mkdir(parents=True)
    fake_youtube.set_state({"channels": {}, "videos": {"abcdefghijk": {
        "recording": {"ended": True}, "description": "fake description"}}})
    result = subprocess.run([*fake_youtube.command(), "--write-thumbnail", "--write-description",
                             "-o", "-", "https://youtube.com/watch?v=abcdefghijk"],
                            cwd=target, capture_output=True, check=False)
    assert result.returncode == 0
    assert (target / "-.webp").is_file()
    _normalize_tee_sidecars(target, "video")
    assert (target / "video.webp").read_bytes() == b"fake-thumbnail"
    assert (target / "video.description").is_file()
    assert not list(backend.glob("-.*"))
