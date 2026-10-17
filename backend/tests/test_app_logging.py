import asyncio
import io
import logging

from app import config
from app.main import app, lifespan


def test_direct_app_startup_failure_keeps_original_traceback(monkeypatch):
    from app.services import transcode
    from app.db import engine

    output = io.StringIO()
    old_stderr = config.sys.stderr
    monkeypatch.setattr(config.sys, "stderr", output)
    monkeypatch.setattr(config.settings, "scheduler_enabled", False)
    monkeypatch.setattr(transcode, "resume", lambda: (_ for _ in ()).throw(RuntimeError("startup probe")))
    monkeypatch.setattr(transcode, "shutdown", lambda: None)
    monkeypatch.setattr(engine, "dispose", lambda: None)
    monkeypatch.setattr("app.database.stop_embedded_postgres", lambda: None)
    config.configure_logging()
    try:
        asyncio.run(_run_lifespan())
    finally:
        monkeypatch.setattr(config.sys, "stderr", old_stderr)
        config.configure_logging()
    text = output.getvalue()
    assert "derived temp cleanup failed" in text
    assert "Traceback (most recent call last)" in text
    assert "RuntimeError: startup probe" in text


async def _run_lifespan():
    async with lifespan(app):
        pass


def test_app_logging_has_timestamp_level_logger_and_no_propagation():
    output = io.StringIO()
    old_stderr = config.sys.stderr
    config.sys.stderr = output
    config.configure_logging()
    try:
        logging.getLogger("app.example").info("one event")
    finally:
        config.sys.stderr = old_stderr
        config.configure_logging()
    lines = output.getvalue().splitlines()
    assert len(lines) == 1
    assert " INFO app.example one event" in lines[0]
    assert logging.getLogger("app").propagate is False
