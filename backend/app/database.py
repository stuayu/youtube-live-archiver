"""Database URL and embedded PostgreSQL lifecycle shared by app and Alembic."""
from __future__ import annotations

import atexit
import threading
from pathlib import Path

from sqlalchemy.engine import make_url

from .config import settings

_lock = threading.Lock()
_embedded_server = None


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
