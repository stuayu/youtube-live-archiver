"""Detached Windows service switcher used after a staged dependency update."""

import ctypes
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


SERVICE_STOPPED = 1
SERVICE_START_PENDING = 2
SERVICE_STOP_PENDING = 3
SERVICE_RUNNING = 4
SC_MANAGER_CONNECT = 0x0001
SERVICE_QUERY_STATUS = 0x0004
SC_STATUS_PROCESS_INFO = 0
log = logging.getLogger("dependency_update_runner")


class SERVICE_STATUS_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", ctypes.c_ulong), ("dwCurrentState", ctypes.c_ulong),
        ("dwControlsAccepted", ctypes.c_ulong), ("dwWin32ExitCode", ctypes.c_ulong),
        ("dwServiceSpecificExitCode", ctypes.c_ulong), ("dwCheckPoint", ctypes.c_ulong),
        ("dwWaitHint", ctypes.c_ulong), ("dwProcessId", ctypes.c_ulong),
        ("dwServiceFlags", ctypes.c_ulong),
    ]


def save(path: Path, state: dict, **updates) -> dict:
    value = {**state, **updates}
    temporary = path.with_name(path.name + ".runner.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)
    return value


def service_state(name: str) -> int | None:
    advapi = ctypes.WinDLL("Advapi32", use_last_error=True)
    advapi.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    advapi.OpenSCManagerW.restype = ctypes.c_void_p
    advapi.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint]
    advapi.OpenServiceW.restype = ctypes.c_void_p
    advapi.QueryServiceStatusEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p,
                                            ctypes.c_uint, ctypes.POINTER(ctypes.c_ulong)]
    advapi.QueryServiceStatusEx.restype = ctypes.c_int
    advapi.CloseServiceHandle.argtypes = [ctypes.c_void_p]
    advapi.CloseServiceHandle.restype = ctypes.c_int
    manager = advapi.OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
    if not manager:
        return None
    try:
        service = advapi.OpenServiceW(manager, name, SERVICE_QUERY_STATUS)
        if not service:
            return None
        try:
            status = SERVICE_STATUS_PROCESS()
            needed = ctypes.c_ulong()
            ok = advapi.QueryServiceStatusEx(service, SC_STATUS_PROCESS_INFO,
                                             ctypes.byref(status), ctypes.sizeof(status),
                                             ctypes.byref(needed))
            return int(status.dwCurrentState) if ok else None
        finally:
            advapi.CloseServiceHandle(service)
    finally:
        advapi.CloseServiceHandle(manager)


def wait_state(name: str, desired: int, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if service_state(name) == desired:
            return True
        time.sleep(1)
    return service_state(name) == desired


def stop_service(name: str) -> None:
    current = service_state(name)
    if current == SERVICE_STOPPED:
        return
    if current == SERVICE_STOP_PENDING:
        if wait_state(name, SERVICE_STOPPED):
            return
        raise RuntimeError("サービス停止の完了を確認できませんでした")
    if current == SERVICE_START_PENDING and not wait_state(name, SERVICE_RUNNING):
        raise RuntimeError("サービス起動の完了を確認できませんでした")
    result = subprocess.run(["sc.exe", "stop", name], capture_output=True, timeout=20, check=False)
    after_stop = service_state(name)
    if result.returncode and after_stop not in (SERVICE_STOPPED, SERVICE_STOP_PENDING):
        raise RuntimeError("サービスを停止できませんでした")
    if not wait_state(name, SERVICE_STOPPED):
        raise RuntimeError("サービス停止の完了を確認できませんでした")


def start_service(name: str) -> None:
    current = service_state(name)
    if current == SERVICE_RUNNING:
        return
    if current == SERVICE_START_PENDING:
        if wait_state(name, SERVICE_RUNNING):
            return
        raise RuntimeError("サービス起動の完了を確認できませんでした")
    if current == SERVICE_STOP_PENDING and not wait_state(name, SERVICE_STOPPED):
        raise RuntimeError("サービス停止の完了を確認できませんでした")
    result = subprocess.run(["sc.exe", "start", name], capture_output=True, timeout=20, check=False)
    if result.returncode and service_state(name) != SERVICE_RUNNING:
        raise RuntimeError("サービスを起動できませんでした")
    if not wait_state(name, SERVICE_RUNNING):
        raise RuntimeError("サービス起動の完了を確認できませんでした")


def set_application(value: str) -> None:
    import winreg

    key_path = r"SYSTEM\CurrentControlSet\Services\YouTubeLiveArchiver\Parameters"
    access = winreg.KEY_READ | winreg.KEY_WRITE | getattr(winreg, "KEY_WOW64_64KEY", 0)
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
        winreg.SetValueEx(key, "Application", 0, winreg.REG_SZ, value)


def get_application() -> str | None:
    import winreg

    key_path = r"SYSTEM\CurrentControlSet\Services\YouTubeLiveArchiver\Parameters"
    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
            value, _ = winreg.QueryValueEx(key, "Application")
            return str(value).strip().strip('"')
    except OSError:
        return None


def runtime_id(python_executable: str) -> str:
    normalized = os.path.normcase(str(Path(python_executable).resolve())).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()[:16]


def healthy_runtime(url: str, expected_runtime: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            payload = json.loads(response.read(4096))
            return response.status == 200 and payload.get("status") == "ok" and payload.get("runtime_id") == expected_runtime
    except Exception:
        return False


def wait_healthy(url: str, expected_runtime: str, timeout: int = 90) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy_runtime(url, expected_runtime):
            return True
        time.sleep(2)
    return healthy_runtime(url, expected_runtime)


def install_frontend(source: Path, live: Path) -> None:
    live.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name == "index.html":
            continue
        target = live / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    staged_index = live / ".index-update.tmp"
    shutil.copy2(source / "index.html", staged_index)
    os.replace(staged_index, live / "index.html")


def prune_environments(*keep_python_paths: str | None, root_hint: str | None = None) -> None:
    keep = set()
    roots = set()
    for value in (*keep_python_paths, root_hint):
        if not value:
            continue
        executable = Path(value)
        if len(executable.parents) >= 3 and executable.parents[2].name == ".venv-updates":
            env_dir = executable.parents[1]
            roots.add(executable.parents[2].resolve())
            if value != root_hint:
                keep.add(env_dir.resolve())
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for entry in root.iterdir():
                if entry.is_dir() and entry.resolve() not in keep:
                    shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            log.exception("could not prune old Python update environments")


def delete_task(task_name: str | None) -> None:
    if not task_name:
        return
    try:
        subprocess.run(["schtasks.exe", "/Delete", "/TN", task_name, "/F"],
                       capture_output=True, timeout=20, check=False)
    except Exception:
        pass


def run(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    state_path = Path(config["state_path"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    name = config["service_name"]
    old_python, new_python = config["old_python"], config["new_python"]
    switched = False
    stop_attempted = False
    live_index = Path(config["live_dist"]) / "index.html"
    index_backup = live_index.with_name(".index-update-backup")
    had_live_index = live_index.is_file()
    try:
        state = save(state_path, state, phase="switching", message="サービスを安全に切り替えています")
        stop_attempted = True
        stop_service(name)
        set_application(new_python)
        switched = True
        start_service(name)
        if not wait_healthy(config["health_url"], runtime_id(new_python)):
            raise RuntimeError("新しい環境のヘルスチェックに失敗しました")
        if had_live_index:
            shutil.copy2(live_index, index_backup)
        install_frontend(Path(config["staged_dist"]), Path(config["live_dist"]))
        state = save(state_path, state, phase="completed", finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     error=None, message="依存関係と画面を更新しました")
        index_backup.unlink(missing_ok=True)
        prune_environments(new_python, old_python)
        shutil.rmtree(Path(config["frontend_stage"]), ignore_errors=True)
    except Exception as exc:
        if index_backup.exists():
            os.replace(index_backup, live_index)
        elif not had_live_index:
            live_index.unlink(missing_ok=True)
        current_application = get_application()
        if stop_attempted or switched or service_state(name) == SERVICE_STOPPED or current_application == new_python:
            state = save(state_path, state, phase="rolling_back", message="起動確認に失敗したため旧環境へ戻しています")
            try:
                stop_service(name)
                set_application(old_python)
                start_service(name)
                restored = wait_healthy(config["health_url"], runtime_id(old_python))
            except Exception:
                restored = False
            state = save(state_path, state,
                         phase="rolled_back" if restored else "rollback_failed",
                         finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         error="新環境の起動に失敗し、" + ("旧環境へ戻しました" if restored else "旧環境への復旧も確認できませんでした"),
                         message="更新に失敗しました")
            if restored:
                prune_environments(old_python, root_hint=new_python)
        else:
            state = save(state_path, state, phase="failed",
                         finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         error=str(exc)[:300], message="更新に失敗しました")
            prune_environments(old_python, root_hint=new_python)
        shutil.rmtree(Path(config["frontend_stage"]), ignore_errors=True)
    finally:
        config_path.unlink(missing_ok=True)
        delete_task(config.get("task_name"))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(2)
    run(Path(sys.argv[1]))
