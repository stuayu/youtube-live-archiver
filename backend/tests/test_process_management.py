import os
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from app.services.recorder import stop_process


def test_docker_stop_grace_period_covers_recorder_shutdown_timeout():
    from app.config import settings

    compose = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    backend = compose.split("  backend:", 1)[1].split("  frontend:", 1)[0]
    match = re.search(r"^    stop_grace_period:\s*(\d+)s\s*$", backend, re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) > settings.recorder_shutdown_timeout_seconds


def test_stop_process_reaps_descendant_processes():
    child_code = "import time; time.sleep(120)"
    parent_code = (
        "import subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c',sys.argv[1]]); "
        "print(child.pid,flush=True); time.sleep(120)"
    )
    options = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    parent = subprocess.Popen([sys.executable, "-c", parent_code, child_code],
                              stdout=subprocess.PIPE, text=True, **options)
    child_pid = int(parent.stdout.readline().strip())
    stop_process(parent, grace_seconds=2)
    assert parent.poll() is not None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if not psutil.pid_exists(child_pid):
            break
        try:
            if psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.05)
    try:
        child = psutil.Process(child_pid)
        assert child.status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        pass


def test_read_only_lifespan_skips_recording_cleanup(monkeypatch):
    import asyncio
    from app import main
    from app.config import settings
    from app.services import recorder
    from app import workers

    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(recorder, "cleanup_orphan_processes", lambda: pytest.fail("orphan cleanup ran"))
    monkeypatch.setattr(workers, "_recover_stale_recordings", lambda: pytest.fail("stale recovery ran"))
    monkeypatch.setattr(recorder, "shutdown", lambda *_args: pytest.fail("recorders were stopped"))

    async def exercise_lifespan():
        async with main.lifespan(main.app):
            pass

    asyncio.run(exercise_lifespan())
