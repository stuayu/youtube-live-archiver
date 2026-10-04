"""Reproduce the README local first-run steps without contacting YouTube."""
from __future__ import annotations

import os
import signal
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("$ " + " ".join(command), flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def free_port() -> int:
    candidates = list(range(40000, 60000))
    secrets.SystemRandom().shuffle(candidates)
    for port in candidates:
        if port == 8011:
            continue
        with socket.socket() as listener:
            try:
                listener.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("空きポートが見つかりません")


def get_json(url: str) -> tuple[int, dict]:
    with urlopen(url, timeout=5) as response:
        import json
        return response.status, json.loads(response.read())


def main() -> int:
    temp_parent = Path(tempfile.mkdtemp(prefix="yla-first-run-"))
    copied = temp_parent / "youtube-live-archiver"
    ignore = shutil.ignore_patterns(".git", ".env", "node_modules", ".venv", "dist", "data", "__pycache__", ".pytest_cache")
    process: subprocess.Popen | None = None
    try:
        shutil.copytree(ROOT, copied, ignore=ignore)
        backend = copied / "backend"
        frontend = copied / "frontend"
        venv = backend / ".venv"
        if os.name == "nt":
            run([sys.executable, "-m", "venv", str(venv)], backend, os.environ.copy())
            python = venv / "Scripts" / "python.exe"
            npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
        else:
            run([sys.executable, "-m", "venv", str(venv)], backend, os.environ.copy())
            python = venv / "bin" / "python"
            npm = shutil.which("npm") or "npm"
        env = os.environ.copy()
        for key in (
            "DATABASE_URL", "YLA_TEST_DATABASE_URL", "DATA_DIR", "STORAGE_ROOT", "YT_DLP_PATH",
            "FFMPEG_PATH", "APP_HOST", "APP_PORT", "APP_RELOAD", "AUTO_MIGRATE",
            "DATABASE_CONNECT_RETRY_SECONDS", "SCHEDULER_ENABLED",
        ):
            env.pop(key, None)
        run([str(python), "-m", "pip", "install", "-r", "requirements.txt", "-r", "requirements-dev.txt"], backend, env)
        run([npm, "ci"], frontend, env)
        run([npm, "run", "build"], frontend, env)
        if (backend / ".env").exists():
            raise RuntimeError("smoke copy unexpectedly contains backend/.env")

        port = free_port()
        env["APP_HOST"] = "127.0.0.1"
        env["APP_PORT"] = str(port)
        if os.name == "nt":
            process = subprocess.Popen([str(python), "-m", "app"], cwd=backend, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, text=True)
        else:
            process = subprocess.Popen([str(python), "-m", "app"], cwd=backend, env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       start_new_session=True, text=True)
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.stdout.read() if process.stdout else ""
                raise RuntimeError(f"app exited with {process.returncode}:\n{output}")
            try:
                health_code, health = get_json(base + "/health")
                if health_code == 200 and health.get("status") == "ok":
                    break
            except (URLError, TimeoutError, OSError):
                time.sleep(0.5)
        else:
            raise TimeoutError("app did not become healthy within 120 seconds")

        status_code, status = get_json(base + "/api/status")
        assert status_code == 200 and status["database"]["available"] is True
        assert status["database"]["mode"] == "embedded", status["database"]
        assert status["tools"]["yt_dlp_available"] is True, status["tools"]
        with urlopen(base + "/", timeout=5) as response:
            page = response.read().decode("utf-8", errors="replace")
            assert response.status == 200 and "id=\"app\"" in page
        run([str(python), "-m", "alembic", "current"], backend, env)
        assert get_json(base + "/health")[0] == 200
        storage_root = temp_parent / "recordings"
        request = Request(base + "/api/storages", data=(
            '{"name":"smoke","root_path":' + __import__("json").dumps(str(storage_root)) +
            ',"enabled":true,"minimum_free_bytes":0,"warning_free_bytes":0}'
        ).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            assert response.status == 200
        print("初回起動 smoke test: 成功（DB、API、画面、yt-dlp、保存先登録）", flush=True)
        return 0
    finally:
        output = ""
        if process is not None and process.poll() is None:
            if os.name == "nt":
                try:
                    process.send_signal(getattr(subprocess, "CTRL_BREAK_EVENT", signal.SIGTERM))
                except Exception:
                    process.terminate()
            else:
                process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if process is not None and process.stdout:
            output = process.stdout.read()
            process.stdout.close()
        for line in output.splitlines():
            if line.startswith(("組み込み PostgreSQL:", "アプリデータ:", "保存先ルート:", "http://127.0.0.1:")):
                print(line, flush=True)
        shutil.rmtree(temp_parent, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"初回起動 smoke test: 失敗: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
