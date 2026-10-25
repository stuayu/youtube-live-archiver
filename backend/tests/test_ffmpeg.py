import subprocess

from app.services.ffmpeg import launch_ffmpeg, run_ffmpeg


def test_run_ffmpeg_executes_python_shim_with_active_interpreter(tmp_path):
    shim = tmp_path / "ffmpeg-shim.py"
    shim.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")

    result = run_ffmpeg([str(shim), "run-shim"], timeout=10, text=True)

    assert result.returncode == 0
    assert result.stdout.strip() == "run-shim"


def test_launch_ffmpeg_executes_python_shim_with_active_interpreter(tmp_path):
    shim = tmp_path / "ffmpeg-shim.py"
    shim.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")

    process = launch_ffmpeg([str(shim), "launch-shim"], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    stdout, _stderr = process.communicate(timeout=10)

    assert process.returncode == 0
    assert stdout.strip() == "launch-shim"
