import os
import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path(request) -> Path:
    """テスト用の一時ディレクトリ。

    pytest 標準の tmp_path は、環境によっては Windows の管理された Temp の ACL が
    読めず収集時点で失敗する。そのため %TEMP% を優先しつつ、使えない場合は
    ワークスペース内へ退避する。いずれの場合もテストごとに作り直し、
    前回の残骸が FileExistsError を起こさないようにする。
    """
    run = os.environ.get("YLA_TEST_RUN", "default")
    name = f"{request.node.name}-{run}".replace("/", "_")[:120]

    base = None
    try:
        probe = Path(tempfile.gettempdir()) / "yla-tests"
        probe.mkdir(parents=True, exist_ok=True)
        (probe / ".probe").write_text("ok", encoding="utf-8")
        (probe / ".probe").unlink()
        base = probe
    except OSError:
        base = Path(__file__).resolve().parents[2] / ".pytest-tmp"

    path = base / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)
