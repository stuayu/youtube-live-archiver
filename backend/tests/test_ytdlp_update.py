from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.config import settings
from app.services import ytdlp_update


def test_version_check_warns_when_installed_release_is_over_thirty_days_old(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="2026.1.1", stderr=""))
    release = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    monkeypatch.setattr(ytdlp_update, "_release", lambda version: (
        (version or "2026.2.1"), release))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", False)

    status = ytdlp_update.check_version()

    assert status["stale"] is True
    assert status["version"] == "2026.1.1"
    assert status["latest_version"] == "2026.2.1"
    assert status["update_overdue"] is True


def test_current_version_does_not_warn_just_because_release_is_old(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="2026.1.1", stderr=""))
    release = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    monkeypatch.setattr(ytdlp_update, "_release", lambda version: ("2026.1.1", release))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", False)

    status = ytdlp_update.check_version()

    assert status["stale"] is False
    assert status["latest_check_failed"] is False
    assert status["update_overdue"] is False


def test_latest_version_lookup_failure_is_unknown_without_warning(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="2026.1.1", stderr=""))
    monkeypatch.setattr(ytdlp_update, "_release", lambda version: (
        ("2026.1.1", "2026-01-01T00:00:00+00:00") if version else (None, None)))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", False)

    status = ytdlp_update.check_version()

    assert status["stale"] is None
    assert status["latest_check_failed"] is True
    assert status["update_overdue"] is False


def test_version_query_failure_clears_a_previously_stale_warning(monkeypatch):
    monkeypatch.setattr(ytdlp_update, "_state", {
        **ytdlp_update._state, "stale": True, "latest_version": "2026.2.1",
        "latest_check_failed": False, "update_overdue": True,
    })
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="offline"))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", False)

    status = ytdlp_update.check_version()

    assert status["stale"] is None
    assert status["latest_version"] is None
    assert status["latest_check_failed"] is True
    assert status["update_overdue"] is False


def test_auto_update_records_previous_version_and_verifies_updated_version(monkeypatch):
    versions = iter(("2026.1.1", "2026.2.1", "2026.2.1"))
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout=next(versions), stderr=""))
    dates = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(ytdlp_update, "_release", lambda version: (
        (version or "2026.2.1"), dates))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", True)

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, *_args):
            return None

    monkeypatch.setattr("app.db.SessionLocal", Database)
    monkeypatch.setattr(ytdlp_update.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=0, stdout="", stderr=""))

    status = ytdlp_update.check_version()

    assert status["previous_version"] == "2026.1.1"
    assert status["version"] == "2026.2.1"
    assert status["update_error"] is None


def test_failed_updated_version_check_runs_rollback(monkeypatch):
    results = iter((SimpleNamespace(returncode=0, stdout="2026.1.1", stderr=""),
                    SimpleNamespace(returncode=1, stdout="", stderr="version failed")))
    monkeypatch.setattr(ytdlp_update, "run_ytdlp", lambda *_args, **_kwargs: next(results))
    dates = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(ytdlp_update, "_release", lambda version: (
        (version or "2026.2.1"), dates))
    monkeypatch.setattr(settings, "yt_dlp_auto_update", True)

    class Database:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, *_args):
            return None

    monkeypatch.setattr("app.db.SessionLocal", Database)
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ytdlp_update.subprocess, "run", run)

    status = ytdlp_update.check_version()

    assert len(commands) == 2
    assert commands[0][-1] == "yt-dlp"
    assert commands[1][-1] == "yt-dlp==2026.1.1"
    assert status["previous_version"] == "2026.1.1"
    assert "restored 2026.1.1" in status["update_error"]
