"""Cached YouTube address-family preference and IPv6 reachability probe."""
from __future__ import annotations

from datetime import datetime, timezone
import socket
import threading
import time

from ..config import settings

PROBE_INTERVAL_SECONDS = 600
PROBE_RETRY_SECONDS = 60
PROBE_TIMEOUT_SECONDS = 3
_lock = threading.Lock()
_checked_at: datetime | None = None
_reachable: bool | None = None
_error: str | None = None
_last_probe = 0.0
_running = False
_probe_wakeup = threading.Event()


def current_mode() -> str:
    return settings.youtube_ip_mode


def probe_ipv6() -> tuple[bool, str | None]:
    """Resolve YouTube AAAA and establish one bounded TCP connection."""
    try:
        addresses = socket.getaddrinfo("www.youtube.com", 443, socket.AF_INET6,
                                       socket.SOCK_STREAM)
        if not addresses:
            return False, "no AAAA records"
        last_error = None
        for family, socktype, proto, _, address in addresses:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(PROBE_TIMEOUT_SECONDS)
            try:
                sock.connect(address)
                return True, None
            except OSError as error:
                last_error = str(error)
            finally:
                sock.close()
        return False, last_error
    except OSError as error:
        return False, str(error)


def _probe_loop() -> None:
    while True:
        if current_mode() != "prefer_ipv6":
            _probe_wakeup.wait(PROBE_INTERVAL_SECONDS)
            _probe_wakeup.clear()
            continue
        probe_if_due()
        with _lock:
            delay = PROBE_INTERVAL_SECONDS if _reachable else PROBE_RETRY_SECONDS
            wait = max(0.0, delay - (time.monotonic() - _last_probe))
        _probe_wakeup.wait(wait)
        _probe_wakeup.clear()


def probe_if_due() -> tuple[bool, str | None] | None:
    """Return the cached probe unless its success/failure retry interval expired."""
    if current_mode() != "prefer_ipv6":
        return None
    with _lock:
        delay = PROBE_INTERVAL_SECONDS if _reachable else PROBE_RETRY_SECONDS
        due = _checked_at is None or time.monotonic() - _last_probe >= delay
        cached = (_reachable, _error)
    return refresh_probe() if due else cached


def refresh_probe() -> tuple[bool, str | None]:
    """Run one probe and publish its cached result; callers should use a worker."""
    global _checked_at, _reachable, _error, _last_probe
    reachable, error = probe_ipv6()
    with _lock:
        _reachable = reachable
        _error = error
        _checked_at = datetime.now(timezone.utc)
        _last_probe = time.monotonic()
    return reachable, error


def start_probe() -> None:
    """Start one daemon probe thread; never block startup or request handlers."""
    global _running
    if current_mode() != "prefer_ipv6":
        return
    with _lock:
        if _running:
            return
        _running = True
    threading.Thread(target=_probe_loop, name="youtube-ipv6-probe", daemon=True).start()


def note_ipv6_failure(message: str) -> None:
    """Disable preferred IPv6 after route/socket failures, never on HTTP 429."""
    global _checked_at, _reachable, _error, _last_probe
    text = (message or "").lower()
    if not any(marker in text for marker in (
        "network is unreachable", "network unreachable", "no route to host",
        "errno 65", "errno 101",
        "cannot assign requested address", "address family not supported",
        "failed to create ipv6 connection", "ipv6 connection failed",
    )):
        return
    with _lock:
        if current_mode() == "prefer_ipv6":
            _reachable = False
            _error = message[-500:]
            _checked_at = datetime.now(timezone.utc)
            _last_probe = time.monotonic()
            _probe_wakeup.set()


def ytdlp_ip_args(args: list[str]) -> list[str]:
    """Return one force flag per launch, excluding version-only invocations."""
    if "--version" in args and len(args) == 1:
        return []
    if "--force-ipv6" in args or "--force-ipv4" in args:
        return []
    mode = current_mode()
    if mode == "ipv6":
        return ["--force-ipv6"]
    if mode == "ipv4":
        return ["--force-ipv4"]
    if mode == "prefer_ipv6":
        with _lock:
            return ["--force-ipv6"] if _reachable is True else []
    return []


def status() -> dict:
    mode = current_mode()
    with _lock:
        reachable = _reachable
        checked_at = _checked_at
        error = _error
    family = {"ipv6": "IPv6", "ipv4": "IPv4", "auto": "OS 任せ"}.get(mode)
    if mode == "prefer_ipv6":
        family = "IPv6" if reachable else "OS 任せ"
    return {"mode": mode, "address_family": family,
            "ipv6_reachable": reachable, "checked_at": checked_at,
            "probe_error": error}
