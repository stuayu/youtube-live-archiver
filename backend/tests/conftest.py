import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url


def _start_database():
    admin_url = os.environ.get("YLA_TEST_DATABASE_URL")
    server = None
    temp_root = None
    if not admin_url:
        try:
            from pgserver import PostgresServer
            temp_root = Path(tempfile.mkdtemp(prefix="yla-pg-"))
            server = PostgresServer(temp_root / "data", cleanup_mode="delete")
            admin_url = server.get_uri(database="postgres")
        except (ImportError, OSError, RuntimeError) as exc:
            if temp_root:
                shutil.rmtree(temp_root, ignore_errors=True)
            return None, None, None, None, f"YLA_TEST_DATABASE_URL 未設定、pgserver 起動不可: {exc}"

    # Initialize app settings only after giving them a test-only database URL.
    os.environ["DATABASE_URL"] = admin_url
    from app.database import normalize_database_url
    admin_url = normalize_database_url(admin_url)
    parsed = make_url(admin_url)
    database_name = f"yla_test_{secrets.token_hex(6)}"
    database_url = parsed.set(database=database_name)
    admin_engine = create_engine(parsed, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            candidates = connection.execute(text("""
                SELECT d.datname, shobj_description(d.oid, 'pg_database') AS description,
                       count(a.pid) AS connections
                FROM pg_database AS d
                LEFT JOIN pg_stat_activity AS a ON a.datname = d.datname
                WHERE d.datname LIKE 'yla_test_%'
                GROUP BY d.oid, d.datname
            """)).mappings().all()
            for candidate in candidates:
                description = candidate["description"] or ""
                marker = "yla_test_created_at="
                if candidate["connections"] or not description.startswith(marker):
                    continue
                try:
                    created_at = datetime.fromisoformat(description[len(marker):])
                except ValueError:
                    continue
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                if created_at >= cutoff:
                    continue
                current_connections = connection.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
                    {"name": candidate["datname"]},
                ).scalar_one()
                if current_connections == 0:
                    safe_name = candidate["datname"].replace('"', '""')
                    connection.execute(text(f'DROP DATABASE IF EXISTS "{safe_name}"'))
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created_at = datetime.now(timezone.utc).isoformat()
            connection.execute(text(f"COMMENT ON DATABASE \"{database_name}\" IS 'yla_test_created_at={created_at}'"))
    except Exception:
        admin_engine.dispose()
        if server:
            server.cleanup()
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
        raise
    admin_engine.dispose()
    os.environ["DATABASE_URL"] = database_url.render_as_string(hide_password=False)

    backend_dir = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=backend_dir,
                            capture_output=True, text=True, check=False)
    if result.returncode:
        drop_engine = create_engine(parsed, isolation_level="AUTOCOMMIT")
        with drop_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)'))
        drop_engine.dispose()
        if server:
            server.cleanup()
        if temp_root:
            shutil.rmtree(temp_root, ignore_errors=True)
        raise RuntimeError(f"alembic upgrade head failed:\n{result.stdout}\n{result.stderr}")
    return parsed, database_name, server, temp_root, None


_ADMIN_URL, _DATABASE_NAME, _PGSERVER, _PGSERVER_ROOT, _DATABASE_SKIP_REASON = _start_database()
_TEST_URL = os.environ.get("DATABASE_URL") if _DATABASE_NAME else None

# App settings in tests must not inherit developer-shell toggles such as
# TRANSCRIPTION_ENABLED=false. Preserve only the isolated test database URL.
if _DATABASE_NAME:
    from app.config import Settings, settings

    settings.database_url = os.environ["DATABASE_URL"]

    for field_name, field in Settings.model_fields.items():
        if field_name == "database_url" or field.default is None:
            continue
        default_value = field.default
        env_name = field_name.upper()
        os.environ[env_name] = str(default_value).lower() if isinstance(default_value, bool) else str(default_value)
        setattr(settings, field_name, default_value)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires the temporary PostgreSQL database")


def pytest_collection_modifyitems(items):
    if _DATABASE_SKIP_REASON:
        skip = pytest.mark.skip(reason=_DATABASE_SKIP_REASON)
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip)


@pytest.fixture(autouse=True)
def _truncate_test_database():
    yield
    try:
        from app.services import monitor
        with monitor._cooldown_lock:
            monitor._cooldown_until = None
            monitor._rate_limit_streak = 0
        # An app lifespan that ended inside the test leaves conversions stopped.
        from app.services import transcode
        transcode.resume()
        from app.services import chat_assets
        with chat_assets._lock:
            chat_assets._known_urls.clear()
            chat_assets._failed_until.clear()
    except ImportError:
        pass
    if not _TEST_URL:
        return
    engine = create_engine(_TEST_URL)
    with engine.begin() as connection:
        names = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename <> 'alembic_version'")).scalars().all()
        if names:
            quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in names)
            connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _drop_test_database():
    yield
    if _ADMIN_URL is not None and _DATABASE_NAME is not None:
        admin_engine = create_engine(_ADMIN_URL, isolation_level="AUTOCOMMIT")
        with admin_engine.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{_DATABASE_NAME}" WITH (FORCE)'))
        admin_engine.dispose()
    if _PGSERVER is not None:
        _PGSERVER.cleanup()
    if _PGSERVER_ROOT is not None:
        shutil.rmtree(_PGSERVER_ROOT, ignore_errors=True)


@pytest.fixture
def tmp_path(request) -> Path:
    """Use a per-test directory and fall back to the workspace if system temp is unavailable."""
    run = os.environ.get("YLA_TEST_RUN", "default")
    name = f"{request.node.name}-{run}".replace("/", "_")[:120]
    try:
        base = Path(tempfile.gettempdir()) / "yla-tests"
        base.mkdir(parents=True, exist_ok=True)
        (base / ".probe").write_text("ok", encoding="utf-8")
        (base / ".probe").unlink()
    except OSError:
        base = Path(__file__).resolve().parents[2] / ".pytest-tmp"
    path = base / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def fake_youtube(tmp_path, monkeypatch):
    from app.config import settings
    from app.services.youtube import ytdlp_command
    state_path = tmp_path / "fake-ytdlp-state.json"
    calls_path = tmp_path / "fake-ytdlp-calls.jsonl"
    state_path.write_text(json.dumps({"channels": {}, "videos": {}}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FAKE_YTDLP_STATE", str(state_path))
    monkeypatch.setenv("FAKE_YTDLP_CALLS", str(calls_path))
    monkeypatch.setattr(settings, "yt_dlp_path", str(Path(__file__).parent / "fakes" / "fake_ytdlp.py"))
    class FakeYouTube:
        def set_state(self, state):
            replacement = state_path.with_suffix(".next")
            replacement.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            replacement.replace(state_path)
        def calls(self):
            if not calls_path.exists():
                return []
            return [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
        def command(self):
            return ytdlp_command()
    return FakeYouTube()


@pytest.fixture
def client(monkeypatch, fake_youtube):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
