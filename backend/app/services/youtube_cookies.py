"""Private, app-managed YouTube cookies and isolated yt-dlp snapshots."""

from __future__ import annotations

import base64
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path
import psutil

from ..config import settings

MAX_COOKIE_BYTES = 1024 * 1024
_lock = threading.RLock()
_active_snapshots: set[Path] = set()
log = logging.getLogger(__name__)


def cookie_path() -> Path:
    return Path(settings.data_dir).resolve() / 'youtube-credentials' / 'cookies.txt'


def configured() -> bool:
    return cookie_path().is_file()


def _protect_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        directory.chmod(0o700)
        return
    # A fresh DACL avoids inheriting access from the application's data folder.
    path64 = base64.b64encode(str(directory).encode('utf-8')).decode('ascii')
    script = f"""$ErrorActionPreference = 'Stop'
$path = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{path64}'))
$acl = New-Object System.Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$ids = @('S-1-5-18', 'S-1-5-32-544', [Security.Principal.WindowsIdentity]::GetCurrent().User.Value) | Select-Object -Unique
foreach ($id in $ids) {{
  $sid = New-Object Security.Principal.SecurityIdentifier($id)
  $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
  $acl.AddAccessRule($rule)
}}
Set-Acl -LiteralPath $path -AclObject $acl
"""
    encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                            capture_output=True, timeout=30, check=False)
    if result.returncode:
        raise OSError('Cookie 保存先のアクセス権を設定できませんでした')


def _validated_cookie_data(data: bytes) -> bytes:
    if len(data) > MAX_COOKIE_BYTES:
        raise ValueError('Cookie ファイルは 1 MB 以下にしてください')
    try:
        lines = data.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError:
        raise ValueError('Cookie ファイルは UTF-8 の Netscape 形式で保存してください') from None
    if not lines or not (lines[0].startswith('# Netscape HTTP Cookie File')
                         or lines[0].startswith('# HTTP Cookie File')):
        raise ValueError('Netscape 形式の Cookie ファイルを選択してください')
    selected = []
    for line in lines[1:]:
        if not line or (line.startswith('#') and not line.startswith('#HttpOnly_')):
            continue
        parts = line.removeprefix('#HttpOnly_').split('\t')
        if (len(parts) != 7 or parts[1] not in ('TRUE', 'FALSE')
                or parts[3] not in ('TRUE', 'FALSE') or not parts[2].startswith('/')
                or not parts[4].isdigit() or not parts[5] or '\x00' in line):
            raise ValueError('Cookie ファイルの形式を確認してください')
        domain = parts[0].lower().lstrip('.')
        if domain == 'youtube.com' or domain.endswith('.youtube.com'):
            selected.append(line)
    if not selected:
        raise ValueError('YouTube の Cookie が見つかりません。YouTube の Cookie を書き出してください')
    return ('# Netscape HTTP Cookie File\n' + '\n'.join(selected) + '\n').encode('utf-8')


def save(data: bytes) -> None:
    content = _validated_cookie_data(data)
    path = cookie_path()
    with _lock:
        _protect_directory(path.parent)
        descriptor, name = tempfile.mkstemp(prefix='.cookie-', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        _cleanup_unused_snapshots(path.parent)


def remove() -> None:
    with _lock:
        path = cookie_path()
        path.unlink(missing_ok=True)
        _cleanup_unused_snapshots(path.parent)


def cleanup_orphan_snapshots() -> None:
    """Remove copies left by a stopped process; copies a running process still uses are kept."""
    try:
        with _lock:
            _cleanup_unused_snapshots(cookie_path().parent)
    except Exception:
        log.exception('startup YouTube cookie copy cleanup failed')


def start_startup_cleanup() -> None:
    threading.Thread(target=cleanup_orphan_snapshots, name='youtube-cookie-startup-cleanup', daemon=True).start()


def _cleanup_unused_snapshots(directory: Path) -> None:
    try:
        candidates = {path.resolve() for path in directory.glob('.active-cookie-*')}
    except OSError:
        log.warning('could not inspect inactive YouTube cookie copies')
        return
    candidates.difference_update(_active_snapshots)
    if not candidates:
        return
    used = _snapshots_used_by_processes()
    if used is None:
        return
    for path in candidates - used:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning('could not remove an inactive YouTube cookie copy')


def _snapshots_used_by_processes() -> set[Path] | None:
    """Return cookie copies passed to any running process, or None when that cannot be known."""
    used: set[Path] = set()
    try:
        for process in psutil.process_iter():
            try:
                command = process.cmdline()
            except (psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            except (psutil.AccessDenied, OSError, ValueError):
                # Only a process proven to belong to another user is ignored. Any other process that cannot be
                # inspected might be a yt-dlp using a copy, whatever its name says, so cleanup must stop.
                if _runs_as_other_user(process):
                    continue
                return None
            used.update(_cookie_copies_in_command(command))
    except (psutil.Error, OSError):
        return None
    return used


def _runs_as_other_user(process: psutil.Process) -> bool:
    # Windows ownership is not checked here, so an uninspectable process is never treated as another user's.
    if os.name == 'nt':
        return False
    try:
        return process.uids().real != os.getuid()
    except (psutil.Error, OSError):
        return False


def _cookie_copies_in_command(command: list[str]) -> set[Path]:
    copies: set[Path] = set()
    for index, arg in enumerate(command):
        if arg == '--cookies' and index + 1 < len(command):
            value = command[index + 1]
        elif arg.startswith('--cookies='):
            value = arg.removeprefix('--cookies=')
        else:
            continue
        try:
            copies.add(Path(value).resolve())
        except (OSError, ValueError):
            continue
    return copies


def release_snapshot(path: Path | None) -> None:
    if path is None:
        return
    with _lock:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning('could not remove an inactive YouTube cookie copy')
        finally:
            _active_snapshots.discard(path.resolve())


def snapshot_args(args: list[str]) -> tuple[list[str], Path | None]:
    """Isolate yt-dlp's writable cookie jar from other recordings and updates."""
    if '--cookies' not in args:
        return args, None
    index = args.index('--cookies') + 1
    if index >= len(args) or Path(args[index]).resolve() != cookie_path():
        return args, None
    with _lock:
        try:
            content = cookie_path().read_bytes()
        except FileNotFoundError:
            raise ValueError('YouTube Cookie が削除されました。設定を確認してください') from None
        descriptor, name = tempfile.mkstemp(prefix='.active-cookie-', dir=cookie_path().parent)
        try:
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(content)
        except Exception:
            Path(name).unlink(missing_ok=True)
            raise
        _active_snapshots.add(Path(name).resolve())
    result = list(args)
    result[index] = name
    return result, Path(name)
