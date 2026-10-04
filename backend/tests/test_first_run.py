import sys
import runpy

import pytest
from sqlalchemy.exc import OperationalError


def test_database_url_uses_psycopg_driver():
    from app.database import normalize_database_url

    assert normalize_database_url("postgresql://user:pass@localhost/db").startswith("postgresql+psycopg://")
    assert normalize_database_url("postgresql+psycopg://user:pass@localhost/db").startswith("postgresql+psycopg://")


def test_missing_pgserver_shows_instruction_and_exits_one(monkeypatch, tmp_path, capsys):
    from app import database
    from app.config import settings

    monkeypatch.setattr(settings, "database_url", "")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(database, "_embedded_server", None)
    monkeypatch.setitem(sys.modules, "pgserver", None)
    with pytest.raises(SystemExit) as error:
        runpy.run_module("app.__main__", run_name="__main__")
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert output.count("DATABASE_URL が未設定です") == 1
    assert "pip install pgserver" in output


def test_ytdlp_command_resolves_configured_path_and_python_module(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import youtube

    monkeypatch.setattr(settings, "yt_dlp_path", "yt-dlp")
    monkeypatch.setattr(youtube.shutil, "which", lambda _: "/tools/yt-dlp")
    assert youtube.ytdlp_command() == ["yt-dlp"]

    script = tmp_path / "yt-dlp.py"
    script.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "yt_dlp_path", str(script))
    monkeypatch.setattr(youtube.shutil, "which", lambda _: None)
    assert youtube.ytdlp_command() == [sys.executable, str(script)]

    monkeypatch.setattr(settings, "yt_dlp_path", "missing-yt-dlp")
    monkeypatch.setattr(youtube.importlib.util, "find_spec", lambda _: object())
    assert youtube.ytdlp_command() == [sys.executable, "-m", "yt_dlp"]

    monkeypatch.setattr(youtube.importlib.util, "find_spec", lambda _: None)
    assert youtube.ytdlp_command() == ["missing-yt-dlp"]


def test_startup_external_database_failure_exits_before_uvicorn(monkeypatch, capsys):
    from app import __main__ as app_main
    from app.config import settings
    from app.db import engine

    attempts = []

    def fail_connect():
        attempts.append(1)
        raise OperationalError("connect", {}, OSError("connection refused"))

    monkeypatch.setattr(engine, "connect", fail_connect)
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://user:secret@example.invalid:5432/sample")
    monkeypatch.setattr(settings, "database_connect_retry_seconds", 0.02)
    with pytest.raises(SystemExit) as error:
        app_main._check_database_and_migrate()
    assert error.value.code == 1
    output = capsys.readouterr().out
    assert "データベースに接続できません" in output
    assert "example.invalid:5432/sample" in output
    assert "secret" not in output
    assert len(attempts) >= 2


def test_startup_auto_migrate_and_disabled_schema_message(monkeypatch, capsys):
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from alembic import command
    from app import __main__ as app_main
    from app.config import settings

    class OldRevision:
        def get_current_revision(self):
            return "old"

    monkeypatch.setattr(MigrationContext, "configure", lambda *_args, **_kwargs: OldRevision())
    monkeypatch.setattr(ScriptDirectory, "get_current_head", lambda self: "head")
    upgrades = []
    monkeypatch.setattr(command, "upgrade", lambda _config, target: upgrades.append(target))
    monkeypatch.setattr(settings, "auto_migrate", True)
    app_main._check_database_and_migrate()
    assert upgrades == ["head"]

    monkeypatch.setattr(settings, "auto_migrate", False)
    with pytest.raises(SystemExit) as error:
        app_main._check_database_and_migrate()
    assert error.value.code == 1
    assert "python -m alembic upgrade head" in capsys.readouterr().out


def test_missing_frontend_returns_503_html(client, monkeypatch, tmp_path):
    from app import main

    monkeypatch.setattr(main, "_dist", tmp_path)
    response = client.get("/")
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("text/html")
    assert "npm run build" in response.text


def test_status_reports_database_mode_and_missing_storage(client):
    response = client.get("/api/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["database"]["available"] is True
    assert payload["database"]["mode"] in ("external", "embedded")
    assert any(item["code"] == "storage_missing" for item in payload["attention"])


def test_status_database_disconnect_returns_503(client, monkeypatch):
    from app.api.common import get_db

    class BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise OperationalError("select", {}, OSError("connection refused"))

        def rollback(self):
            pass

    app = client.app
    app.dependency_overrides[get_db] = lambda: BrokenSession()
    try:
        response = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 503
    assert response.json() == {"error": "データベースに接続できません"}
