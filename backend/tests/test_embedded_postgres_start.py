import subprocess
from pathlib import Path

import pytest


class _Info:
    def __init__(self, running: bool):
        self._running = running

    def is_running(self) -> bool:
        return self._running


def _patch(monkeypatch, *, info, calls, result=None):
    from app import database

    monkeypatch.setattr(database, "_read_postmaster_info", lambda data_dir: info)

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if isinstance(result, BaseException):
            raise result
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(database.subprocess, "run", fake_run)
    monkeypatch.setattr(database, "_find_free_port", lambda: 54321)
    return database


def _data_dir(tmp_path: Path) -> Path:
    """initdb 済みのデータディレクトリを模す。"""
    data_dir = tmp_path / "postgres"
    data_dir.mkdir()
    (data_dir / "PG_VERSION").write_text("16\n", encoding="utf-8")
    return data_dir


def test_prestart_runs_pg_ctl_with_long_timeout_and_log_outside_data_dir(monkeypatch, tmp_path):
    from app.config import settings

    calls = []
    database = _patch(monkeypatch, info=None, calls=calls)
    monkeypatch.setattr(settings, "embedded_postgres_start_timeout_seconds", 300)
    data_dir = _data_dir(tmp_path)

    database.prestart_embedded_postgres(data_dir, is_windows=True, bin_dir=Path("bin"))

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == str(Path("bin") / "pg_ctl.exe")
    assert args[args.index("-D") + 1] == str(data_dir)
    assert args[args.index("-t") + 1] == "300"
    log_path = Path(args[args.index("-l") + 1])
    # データディレクトリ内の log はクラッシュ回復時の fsync で共有違反になるため外に出す。
    assert log_path.parent == tmp_path
    assert data_dir not in log_path.parents
    assert '-p 54321' in args and '-h "127.0.0.1"' in args
    assert args[-1] == "start"
    # アプリのコンソールが消えても postgres の子プロセスが巻き込まれないよう別コンソールで起動する。
    assert kwargs["creationflags"] & database.CREATE_NO_WINDOW
    assert kwargs["timeout"] > 300


def test_prestart_skips_when_server_already_running(monkeypatch, tmp_path):
    calls = []
    database = _patch(monkeypatch, info=_Info(True), calls=calls)

    database.prestart_embedded_postgres(_data_dir(tmp_path), is_windows=True, bin_dir=Path("bin"))

    assert calls == []


def test_prestart_starts_when_pid_file_is_stale(monkeypatch, tmp_path):
    calls = []
    database = _patch(monkeypatch, info=_Info(False), calls=calls)

    database.prestart_embedded_postgres(_data_dir(tmp_path), is_windows=True, bin_dir=Path("bin"))

    assert len(calls) == 1


def test_prestart_is_noop_outside_windows(monkeypatch, tmp_path):
    calls = []
    database = _patch(monkeypatch, info=None, calls=calls)

    database.prestart_embedded_postgres(tmp_path / "postgres", is_windows=False, bin_dir=Path("bin"))

    assert calls == []


def test_prestart_skips_uninitialized_data_dir(monkeypatch, tmp_path):
    calls = []
    database = _patch(monkeypatch, info=None, calls=calls)

    database.prestart_embedded_postgres(
        tmp_path / "postgres", is_windows=True, bin_dir=Path("bin"), initialized=False
    )

    assert calls == []


@pytest.mark.parametrize(
    "error",
    [
        subprocess.TimeoutExpired(["pg_ctl"], 330),
        subprocess.CalledProcessError(1, ["pg_ctl"]),
    ],
)
def test_prestart_failure_raises_runtime_error_with_log_path(monkeypatch, tmp_path, error):
    calls = []
    database = _patch(monkeypatch, info=None, calls=calls, result=error)

    with pytest.raises(RuntimeError) as raised:
        database.prestart_embedded_postgres(_data_dir(tmp_path), is_windows=True, bin_dir=Path("bin"))

    assert str(tmp_path / "postgres.log") in str(raised.value)


def test_prestart_uses_configured_bin_dir(monkeypatch, tmp_path):
    from app.config import settings

    calls = []
    database = _patch(monkeypatch, info=None, calls=calls)
    # pgserver 同梱版より新しい PostgreSQL のマイナー版を使えるようにする。
    monkeypatch.setattr(settings, "embedded_postgres_bin_dir", str(tmp_path / "pgsql" / "bin"))
    monkeypatch.setattr(database, "_pgserver_bin_dir", lambda: Path("bundled"))

    database.prestart_embedded_postgres(_data_dir(tmp_path), is_windows=True)

    assert calls[0][0][0] == str(tmp_path / "pgsql" / "bin" / "pg_ctl.exe")


def test_prestart_falls_back_to_pgserver_bin_dir(monkeypatch, tmp_path):
    from app.config import settings

    calls = []
    database = _patch(monkeypatch, info=None, calls=calls)
    monkeypatch.setattr(settings, "embedded_postgres_bin_dir", "")
    monkeypatch.setattr(database, "_pgserver_bin_dir", lambda: Path("bundled"))

    database.prestart_embedded_postgres(_data_dir(tmp_path), is_windows=True)

    assert calls[0][0][0] == str(Path("bundled") / "pg_ctl.exe")
