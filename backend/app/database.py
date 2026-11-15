"""Database URL and embedded PostgreSQL lifecycle shared by app and Alembic."""
from __future__ import annotations

import atexit
import socket
import subprocess
import sys
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings

_lock = threading.Lock()
_embedded_server = None

# Windows の CreateProcess フラグ。他 OS の subprocess には定義がないため値を直接持つ。
CREATE_NO_WINDOW = 0x08000000


def normalize_database_url(url: str) -> str:
    """Use psycopg v3 explicitly for PostgreSQL URLs."""
    parsed = make_url(url)
    if parsed.drivername == "postgresql":
        parsed = parsed.set(drivername="postgresql+psycopg")
    return parsed.render_as_string(hide_password=False)


def is_embedded_database(url: str | None = None) -> bool:
    configured = (settings.database_url if url is None else url).strip()
    return not configured or configured.lower() == "embedded"


def embedded_data_dir() -> Path:
    return Path(settings.data_dir).expanduser().resolve() / "postgres"


def embedded_log_path(data_dir: Path) -> Path:
    return data_dir.parent / "postgres.log"


def _read_postmaster_info(data_dir: Path):
    from pgserver.utils import PostmasterInfo

    return PostmasterInfo.read_from_pgdata(data_dir)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _pgserver_bin_dir() -> Path:
    from pgserver._commands import POSTGRES_BIN_PATH

    return POSTGRES_BIN_PATH


def prestart_embedded_postgres(
    data_dir: Path,
    *,
    is_windows: bool | None = None,
    bin_dir: Path | None = None,
    initialized: bool | None = None,
) -> None:
    """Windows では pgserver に任せる前に pg_ctl で自前起動する。

    pgserver は pg_ctl start を 10 秒で打ち切り、ログもデータディレクトリ内に書く。
    異常終了後のクラッシュ回復ではデータディレクトリの fsync がそのログの共有違反で
    30 秒以上待たされるため、タイムアウトでアプリが終了し、アプリのコンソールを
    引き継いだ postgres の子プロセスが 0xC0000142 で落ちて回復が完了しない。
    ここでは十分なタイムアウト、データディレクトリ外のログ、独立したコンソールで
    起動しておき、pgserver には起動済みサーバーへ接続させる。
    """
    if is_windows is None:
        is_windows = sys.platform == "win32"
    if not is_windows:
        return
    if initialized is None:
        initialized = (data_dir / "PG_VERSION").exists()
    if not initialized:
        # 初回は pgserver に initdb から任せる（回復処理が走らないため問題にならない）。
        return
    info = _read_postmaster_info(data_dir)
    if info is not None and info.is_running():
        return

    timeout = max(1, int(settings.embedded_postgres_start_timeout_seconds))
    log_path = embedded_log_path(data_dir)
    if bin_dir is None:
        configured = settings.embedded_postgres_bin_dir.strip()
        bin_dir = Path(configured).expanduser() if configured else _pgserver_bin_dir()
    pg_ctl = bin_dir / "pg_ctl.exe"
    args = [
        str(pg_ctl),
        "-D", str(data_dir),
        "-w",
        "-t", str(timeout),
        "-o", '-h "127.0.0.1"',
        "-o", f"-p {_find_free_port()}",
        "-l", str(log_path),
        "start",
    ]
    try:
        subprocess.run(
            args,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout + 30,
            creationflags=CREATE_NO_WINDOW,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"組み込み PostgreSQL を起動できませんでした（{exc}）。ログを確認してください: {log_path}"
        ) from exc


def start_embedded_postgres():
    """Start or attach to the pgserver instance for this data directory."""
    global _embedded_server
    with _lock:
        if _embedded_server is None:
            try:
                from pgserver import get_server
            except ImportError as exc:
                raise RuntimeError(
                    "DATABASE_URL が未設定です。PostgreSQL の接続 URL を backend/.env に設定するか、"
                    "組み込み PostgreSQL を使う場合は `pip install pgserver` を実行してください"
                    "（pgserver の配布物は Python 3.12 以前の Windows x64 / macOS / Linux x86_64 向けです）。"
                ) from exc
            data_dir = embedded_data_dir()
            data_dir.parent.mkdir(parents=True, exist_ok=True)
            prestart_embedded_postgres(data_dir)
            _embedded_server = get_server(data_dir, cleanup_mode="stop")
            atexit.register(stop_embedded_postgres)
        return _embedded_server


def stop_embedded_postgres() -> None:
    global _embedded_server
    with _lock:
        server = _embedded_server
        _embedded_server = None
    if server is not None:
        server.cleanup()


def resolve_database_url() -> str:
    configured = settings.database_url.strip()
    if not configured or configured.lower() == "embedded":
        server = start_embedded_postgres()
        return normalize_database_url(server.get_uri(database="postgres"))
    return normalize_database_url(configured)


def database_log_identity(url: str | None = None) -> str:
    if is_embedded_database():
        return f"組み込み PostgreSQL: {embedded_data_dir()}"
    parsed = make_url(url or resolve_database_url())
    host = parsed.host or "localhost"
    port = f":{parsed.port}" if parsed.port else ""
    database = parsed.database or ""
    return f"PostgreSQL: {host}{port}/{database}"


def database_location(url: str) -> str:
    parsed = make_url(url)
    return f"{parsed.host or 'localhost'}{f':{parsed.port}' if parsed.port else ''}/{parsed.database or ''}"
