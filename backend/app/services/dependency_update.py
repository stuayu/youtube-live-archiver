"""Stage Python dependency updates and switch a Windows NSSM service safely."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import importlib.metadata
import importlib.util
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import uuid

from ..config import settings

log = logging.getLogger(__name__)
_lock = threading.RLock()
_cancel = threading.Event()
_worker: threading.Thread | None = None

_DEFAULT_STATE = {
    "phase": "idle", "started_at": None, "finished_at": None,
    "old_python": None, "new_python": None, "error": None,
}
_ACTIVE_PHASES = {"preparing", "waiting_for_idle", "switching", "rolling_back"}


def _state_path() -> Path:
    return Path(settings.data_dir) / "updates" / "dependency-update.json"


def _read_state() -> dict:
    try:
        value = json.loads(_state_path().read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return {**_DEFAULT_STATE, **value}
    except (OSError, ValueError, TypeError):
        pass
    return dict(_DEFAULT_STATE)


def _write_state(**changes) -> dict:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        value = {**_read_state(), **changes}
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, path)
        return value


def _service_info() -> tuple[dict | None, str | None]:
    if os.name != "nt":
        return None, "この更新機能は Windows サービス環境でのみ使えます"
    try:
        import winreg

        root = r"SYSTEM\CurrentControlSet\Services\YouTubeLiveArchiver"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
            try:
                account, _ = winreg.QueryValueEx(key, "ObjectName")
            except FileNotFoundError:
                account = "LocalSystem"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root + r"\Parameters") as key:
            application, _ = winreg.QueryValueEx(key, "Application")
            directory, _ = winreg.QueryValueEx(key, "AppDirectory")
            try:
                parameters, _ = winreg.QueryValueEx(key, "AppParameters")
            except FileNotFoundError:
                parameters = ""
        if str(account).casefold() not in ("localsystem", r"nt authority\system"):
            return None, "YouTubeLiveArchiver サービスは LocalSystem で実行してください"
        application = str(application).strip().strip('"')
        if Path(application).resolve() != Path(sys.executable).resolve():
            return None, "実行中の Python と Windows サービスの設定が一致しません"
        if Path(directory).resolve() != Path(__file__).resolve().parents[2]:
            return None, "Windows サービスの作業フォルダーを確認できません"
        if "-m app" not in str(parameters):
            return None, "Windows サービスの起動引数を確認できません"
        return {"application": str(application), "directory": str(directory)}, None
    except (OSError, ImportError, ValueError) as exc:
        log.warning("dependency updater service discovery failed (%s)", type(exc).__name__)
        return None, "NSSM の Windows サービス設定を確認できません"


def is_supported() -> tuple[bool, str | None]:
    _, reason = _service_info()
    if reason:
        return False, reason
    base_python = getattr(sys, "_base_executable", sys.executable)
    if not Path(base_python).is_file():
        return False, "Python のベース実行ファイルが見つかりません"
    if shutil.which("npm.cmd") is None:
        return False, "サービスの PATH から npm.cmd を見つけられません"
    return True, None


def maintenance_requested() -> bool:
    return _read_state().get("phase") in {"waiting_for_idle", "switching", "rolling_back"}


def status() -> dict:
    state = _read_state()
    supported, reason = is_supported()
    try:
        cuda_installed = importlib.metadata.version("nvidia-cublas-cu12") is not None
    except importlib.metadata.PackageNotFoundError:
        cuda_installed = False
    try:
        current_pip = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        current_pip = None
    from .ytdlp_update import status as ytdlp_status

    return {
        **state,
        "supported": supported,
        "unsupported_reason": reason,
        "python_version": platform_python_version(),
        "pip_version": current_pip,
        "cuda_dependencies": cuda_installed,
        "updating": state.get("phase") in _ACTIVE_PHASES,
        "maintenance": maintenance_requested(),
        "yt_dlp": ytdlp_status(),
    }


def platform_python_version() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def runtime_id(python_executable: str | Path | None = None) -> str:
    value = str(Path(python_executable or sys.executable).resolve())
    normalized = os.path.normcase(value).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:16]


def _stop_process_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
    if process.poll() is None:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        process.wait()


def _run(command: list[str], *, cwd: Path, timeout: int, label: str) -> None:
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=str(cwd), stdout=subprocess.DEVNULL,
                               stderr=subprocess.STDOUT)
    while process.poll() is None:
        if _cancel.wait(1):
            _stop_process_tree(process)
            raise RuntimeError("更新をキャンセルしました")
        if time.monotonic() - started > timeout:
            _stop_process_tree(process)
            raise RuntimeError(f"{label} が時間内に完了しませんでした")
    if process.returncode:
        log.warning("dependency update command failed label=%s code=%s", label, process.returncode)
        raise RuntimeError(f"{label} に失敗しました（終了コード {process.returncode}）")


def _active_work() -> list[str]:
    from sqlalchemy import func, select
    from ..db import SessionLocal
    from ..models import ProcessingJob, Video
    from .recorder import running_counts
    from .transcode import active_count

    reasons = []
    db = SessionLocal()
    try:
        if db.scalar(select(func.count()).select_from(Video).where(Video.status == "RECORDING")):
            reasons.append("録画")
        if db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status == "processing")):
            reasons.append("文字起こし・後処理")
    finally:
        db.close()
    live, archive = running_counts()
    if live or archive:
        reasons.append("録画・ダウンロード")
    if active_count():
        reasons.append("変換")
    from .ytdlp_update import is_update_in_progress
    if is_update_in_progress():
        reasons.append("yt-dlp 更新")
    try:
        from .. import main
        if main.active_mutation_count():
            reasons.append("API更新処理")
        with main._job_lock:
            for job_id, item in main._job_stats.items():
                if job_id == "dependency-update":
                    continue
                started, finished = item.get("last_started_at"), item.get("last_finished_at")
                if started and (finished is None or started > finished):
                    reasons.append("バックグラウンド処理")
                    break
    except (ImportError, AttributeError):
        pass
    return list(dict.fromkeys(reasons))


def _write_runner_config(config: dict) -> Path:
    folder = Path(settings.data_dir) / "updates"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"switch-{uuid.uuid4().hex}.json"
    path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    return path


def _launch_switcher(config_path: Path) -> None:
    runner = Path(__file__).resolve().parents[1] / "dependency_update_runner.py"
    task_name = "\\YouTubeLiveArchiverDependencyUpdate-" + uuid.uuid4().hex
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["task_name"] = task_name
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    command = f'"{sys.executable}" "{runner}" "{config_path}"'
    run_at = datetime.now() + timedelta(days=1)
    try:
        create = subprocess.run(
            ["schtasks.exe", "/Create", "/TN", task_name, "/SC", "ONCE",
             "/SD", run_at.strftime("%m/%d/%Y"), "/ST", "23:59", "/RU", "SYSTEM",
             "/RL", "HIGHEST", "/TR", command, "/F"],
            capture_output=True, timeout=30, check=False,
        )
        if create.returncode:
            raise RuntimeError("更新切替用の Windows タスクを作成できませんでした")
        started = subprocess.run(["schtasks.exe", "/Run", "/TN", task_name],
                                 capture_output=True, timeout=30, check=False)
        if started.returncode:
            raise RuntimeError("更新切替用の Windows タスクを起動できませんでした")
    except Exception:
        subprocess.run(["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, timeout=30, check=False)
        raise


def _perform_update() -> None:
    backend = Path(__file__).resolve().parents[2]
    root = backend.parent
    update_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    env_root = backend / ".venv-updates"
    new_env = env_root / update_id
    frontend_stage = Path(settings.data_dir) / "updates" / f"frontend-{update_id}"
    frontend_build = frontend_stage / "source" / "dist-update"
    constraints: Path | None = None
    config_path: Path | None = None
    env_root.mkdir(parents=True, exist_ok=True)
    try:
        old_python = str(Path(sys.executable).resolve())
        _write_state(phase="preparing", started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=None, old_python=old_python, new_python=None, error=None,
                    update_id=update_id, message="Python依存関係を新しい環境へ準備しています")
        minimum_free = 4 * 1024**3 if importlib.util.find_spec("nvidia") else 2 * 1024**3
        available = min(shutil.disk_usage(path).free for path in (backend, Path(settings.data_dir)))
        if available < minimum_free:
            raise RuntimeError("更新用の空き容量が不足しています")

        base_python = str(Path(getattr(sys, "_base_executable", sys.executable)).resolve())
        _run([base_python, "-m", "venv", str(new_env)], cwd=backend, timeout=180, label="仮想環境の作成")
        new_python = new_env / "Scripts" / "python.exe"
        if not new_python.is_file():
            raise RuntimeError("新しい Python 環境を作成できませんでした")
        requirements = backend / "requirements.txt"
        constraints = frontend_stage.parent / f"constraints-{update_id}.txt"
        try:
            pgserver = importlib.metadata.version("pgserver")
            constraints.write_text(f"pgserver=={pgserver}\n", encoding="ascii")
        except importlib.metadata.PackageNotFoundError:
            constraints = None
        _write_state(message="Python依存関係を更新しています")
        _run([str(new_python), "-m", "pip", "install", "--no-cache-dir", "--upgrade", "pip"],
             cwd=backend, timeout=600, label="pip 更新")
        install = [str(new_python), "-m", "pip", "install", "--no-cache-dir", "--upgrade",
                   "--upgrade-strategy", "eager"]
        if constraints:
            install.extend(["-c", str(constraints)])
        install.extend(["-r", str(requirements)])
        cuda_was_installed = importlib.util.find_spec("nvidia") is not None
        if cuda_was_installed and (backend / "requirements-cuda-windows.txt").is_file():
            install.extend(["-r", str(backend / "requirements-cuda-windows.txt")])
        _run(install, cwd=backend, timeout=1800, label="Python 依存関係のインストール")
        _run([str(new_python), "-m", "pip", "check"], cwd=backend, timeout=120, label="依存関係の整合性確認")
        _run([str(new_python), "-c", "import fastapi, uvicorn, sqlalchemy, psycopg, faster_whisper, ctranslate2"],
             cwd=backend, timeout=120, label="主要モジュールの読み込み確認")

        _write_state(message="フロントエンド依存関係を更新して画面をビルドしています")
        frontend = root / "frontend"
        ignore = shutil.ignore_patterns("node_modules", "dist", "dist-*", ".git", "coverage", "playwright-report", "test-results")
        shutil.copytree(frontend, frontend_stage / "source", ignore=ignore)
        frontend_source = frontend_stage / "source"
        _run(["cmd.exe", "/d", "/c", "npm.cmd update"], cwd=frontend_source,
             timeout=1200, label="npm 依存関係の更新")
        _run(["cmd.exe", "/d", "/c", "npm.cmd run build -- --outDir dist-update"],
             cwd=frontend_source, timeout=900, label="フロントエンドのビルド")
        if not (frontend_build / "index.html").is_file():
            raise RuntimeError("新しい画面ビルドを確認できませんでした")

        from .. import main
        with main._mutation_gate:
            _write_state(phase="waiting_for_idle", new_python=str(new_python),
                        message="録画・文字起こし・変換の完了を待っています")
        while True:
            if _cancel.wait(5):
                raise RuntimeError("更新をキャンセルしました")
            active = _active_work()
            if active:
                _write_state(message="処理の完了を待っています: " + "、".join(active))
                continue

            service, reason = _service_info()
            if not service:
                raise RuntimeError(reason or "Windows サービスを確認できません")
            config_path = _write_runner_config({
                "service_name": "YouTubeLiveArchiver",
                "old_python": old_python,
                "new_python": str(new_python),
                "state_path": str(_state_path()),
                "live_dist": str(root / "frontend" / "dist"),
                "staged_dist": str(frontend_build),
                "frontend_stage": str(frontend_stage),
                "health_url": f"http://127.0.0.1:{settings.app_port}/health",
            })
            from .. import main
            with main._mutation_gate:
                if _cancel.is_set():
                    raise RuntimeError("更新をキャンセルしました")
                if main._active_mutations:
                    wait_for_mutations = True
                else:
                    wait_for_mutations = False
                    _write_state(phase="switching", message="新しい環境へ切り替えて起動確認しています")
            if wait_for_mutations:
                config_path.unlink(missing_ok=True)
                config_path = None
                _write_state(message="設定変更などの実行中処理が終わるのを待っています")
                continue
            _launch_switcher(config_path)
            break
    except Exception as exc:
        log.exception("dependency update preparation failed")
        phase = "cancelled" if "キャンセル" in str(exc) else "failed"
        _write_state(phase=phase, finished_at=datetime.now(timezone.utc).isoformat(),
                    error=str(exc)[:300], message="更新を中止しました")
        shutil.rmtree(new_env, ignore_errors=True)
        shutil.rmtree(frontend_stage, ignore_errors=True)
        if config_path is not None:
            config_path.unlink(missing_ok=True)
    finally:
        if constraints is not None:
            constraints.unlink(missing_ok=True)
        _cancel.clear()


def start_update() -> bool:
    global _worker
    supported, reason = is_supported()
    if not supported:
        raise ValueError(reason or "この環境では依存関係を更新できません")
    with _lock:
        current = _read_state()
        if current.get("phase") in _ACTIVE_PHASES or (_worker and _worker.is_alive()):
            return False
        _cancel.clear()
        _write_state(phase="preparing", started_at=datetime.now(timezone.utc).isoformat(),
                    finished_at=None, error=None, message="更新を準備しています")
        from . import ytdlp_update
        if ytdlp_update.managed_update_supported() and not ytdlp_update.is_update_in_progress():
            try:
                ytdlp_update.trigger_update(force=False)
            except Exception:
                log.exception("could not start yt-dlp update from dependency updater")
        _worker = threading.Thread(target=_perform_update, name="dependency-update", daemon=True)
        _worker.start()
        return True


def cancel_update() -> bool:
    state = _read_state()
    if state.get("phase") not in {"preparing", "waiting_for_idle"}:
        return False
    _cancel.set()
    return True
