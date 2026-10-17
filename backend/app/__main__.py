import os
import socket
import threading
import time
from pathlib import Path

import uvicorn

from .config import configure_logging, settings

configure_logging()


def _watchdog(host: str, port: int, grace: float = 30.0, interval: float = 15.0, failures: int = 3) -> None:
    """Restart the service if its local HTTP listener stops responding."""
    probe_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    time.sleep(grace)
    consecutive = 0
    while True:
        try:
            with socket.create_connection((probe_host, port), timeout=5):
                consecutive = 0
        except OSError:
            consecutive += 1
            if consecutive >= failures:
                print(f"watchdog: {probe_host}:{port} へ {consecutive} 回連続で接続できないため終了します", flush=True)
                os._exit(1)
        time.sleep(interval)


def _check_database_and_migrate() -> str:
    from sqlalchemy import text
    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.script import ScriptDirectory
    from .database import resolve_database_url, database_log_identity, database_location
    from .db import engine

    database_url = resolve_database_url()
    deadline = time.monotonic() + max(0, settings.database_connect_retry_seconds)
    while True:
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            break
        except Exception as exc:
            if time.monotonic() >= deadline:
                reason = str(getattr(exc, "orig", exc)).splitlines()[0]
                reason = reason.replace("\n", " ")[:240]
                print(
                    f"データベースに接続できません: {reason}。backend/.env の DATABASE_URL "
                    f"（現在: {database_location(database_url)}）を確認してください。",
                    flush=True,
                )
                raise SystemExit(1)
            time.sleep(min(1, max(0, deadline - time.monotonic())))

    config_path = Path(__file__).resolve().parents[1] / "alembic.ini"
    alembic_config = Config(str(config_path))
    alembic_config.set_main_option("script_location", str(config_path.parent / "alembic"))
    script = ScriptDirectory.from_config(alembic_config)
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    head = script.get_current_head()
    if settings.auto_migrate:
        if current != head:
            command.upgrade(alembic_config, "head")
    elif current != head:
        print(
            f"データベースのスキーマが古いです（現在: {current or '未適用'}、必要: {head}）。"
            "`python -m alembic upgrade head` を実行するか、AUTO_MIGRATE=true にしてください。",
            flush=True,
        )
        raise SystemExit(1)
    return database_log_identity(database_url)


def _check_optional_tools() -> None:
    import shutil
    import subprocess
    from .services.youtube import ytdlp_command

    try:
        result = subprocess.run([*ytdlp_command(), "--version"], capture_output=True, timeout=10, check=False)
        if result.returncode:
            print("警告: yt-dlp が見つからないか、起動できません", flush=True)
    except (OSError, subprocess.SubprocessError):
        print("警告: yt-dlp が見つからないか、起動できません", flush=True)
    for name in ("ffmpeg", "ffprobe"):
        path = settings.ffmpeg_path if name == "ffmpeg" else settings.ffprobe_path
        if not (shutil.which(path) or Path(path).is_file()):
            print(f"警告: {name} が見つかりません", flush=True)


def _run_uvicorn() -> None:
    reload_dirs = [d.strip() for d in settings.app_reload_dirs.split(",") if d.strip()]
    config = uvicorn.Config(
        "app.main:app", host=settings.app_host, port=settings.app_port,
        reload=settings.app_reload, reload_dirs=reload_dirs if settings.app_reload else None,
    )

    if settings.app_reload:
        print(f"http://{settings.app_host}:{settings.app_port}/ で待受中", flush=True)
        uvicorn.run(
            "app.main:app", host=settings.app_host, port=settings.app_port,
            reload=True, reload_dirs=reload_dirs,
        )
        return

    class StartupMessageServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            if self.started:
                print(f"http://{settings.app_host}:{settings.app_port}/ で待受中", flush=True)

    StartupMessageServer(config).run()


if __name__ == "__main__":
    from .database import stop_embedded_postgres

    try:
        identity = _check_database_and_migrate()
        print(identity, flush=True)
        print(f"アプリデータ: {settings.data_dir}", flush=True)
        print(f"保存先ルート: {settings.storage_root}", flush=True)
        dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
        if not dist.is_dir():
            print("画面のビルドがありません。`cd frontend && npm ci && npm run build` を実行してください", flush=True)
        _check_optional_tools()
        threading.Thread(target=_watchdog, args=(settings.app_host, settings.app_port), daemon=True).start()
        _run_uvicorn()
    except RuntimeError as exc:
        print(str(exc), flush=True)
        raise SystemExit(1)
    finally:
        stop_embedded_postgres()
