import os
import socket
import threading
import time

import uvicorn

from .config import settings


def _watchdog(host: str, port: int, grace: float = 30.0, interval: float = 15.0, failures: int = 3) -> None:
    """Windows では IOCP の accept ループが WinError 64 等で死んでも
    プロセスは生き残り、サービスマネージャが異常に気付けない。
    自分自身へ接続できない状態が続いたらプロセスごと落として再起動させる。
    """
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


if __name__ == "__main__":
    threading.Thread(
        target=_watchdog,
        args=(settings.app_host, settings.app_port),
        daemon=True,
    ).start()
    reload_dirs = [d.strip() for d in settings.app_reload_dirs.split(",") if d.strip()]
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=settings.app_reload,
        reload_dirs=reload_dirs if settings.app_reload else None,
    )
