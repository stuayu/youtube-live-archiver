from pathlib import Path
from threading import Event, Thread
import os
import sys
import time

import pytest

from app.services import transcode


def fake_ffmpeg(tmp_path: Path, body: str) -> str:
    """Executable stand-in for ffmpeg; the last argument is the output path."""
    script = tmp_path / "fake-ffmpeg"
    script.write_text(f"#!{sys.executable}\nimport sys, time\noutput = sys.argv[-1]\n{body}\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def test_remux_preserves_source_and_deduplicates(monkeypatch, tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"original")
    started, release = Event(), Event()
    calls = []
    def run(src, dest, mode, ffmpeg, progress=None):
        calls.append((src, dest, mode)); started.set(); release.wait(2)
        dest.write_bytes(b"mp4")
        return {"status": "completed", "path": str(dest), "error": None}
    monkeypatch.setattr(transcode, "_run", run)
    video_id = int(time.time_ns() % 100000000)
    first = transcode.enqueue(video_id, source, "remux", "ffmpeg")
    assert first["status"] == "queued"
    assert started.wait(2)
    second = transcode.enqueue(video_id, source, "remux", "ffmpeg")
    assert second["status"] == "running"
    assert len(calls) == 1
    release.set()
    for _ in range(100):
        if transcode.status(video_id, "remux")["status"] == "completed": break
        time.sleep(.01)
    assert source.read_bytes() == b"original"
    assert Path(transcode.status(video_id, "remux")["path"]).read_bytes() == b"mp4"


@pytest.mark.skipif(os.name == "nt", reason="fake ffmpeg is a POSIX script")
def test_failed_conversion_leaves_original_untouched_and_keeps_ffmpeg_error(tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"keep me")
    ffmpeg = fake_ffmpeg(tmp_path, """
open(output, "wb").write(b"partial")
print("out_time_us=1000000", flush=True)
print("progress=continue", flush=True)
print("Error opening output: Invalid data found", file=sys.stderr, flush=True)
sys.exit(1)
""")
    dest = transcode.output_path(source, "remux")
    result = transcode._run(source, dest, "remux", ffmpeg)
    assert result["status"] == "failed"
    assert "Invalid data found" in result["error"]
    assert "out_time_us" not in result["error"]
    assert source.read_bytes() == b"keep me"
    assert not dest.exists()
    assert list(tmp_path.glob("*.tmp.mp4")) == []


@pytest.mark.skipif(os.name == "nt", reason="fake ffmpeg is a POSIX script")
def test_successful_conversion_replaces_temporary_file(tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"source")
    ffmpeg = fake_ffmpeg(tmp_path, """
open(output, "wb").write(b"converted")
print("progress=end", flush=True)
""")
    dest = transcode.output_path(source, "h264")
    result = transcode._run(source, dest, "h264", ffmpeg)
    assert result == {"status": "completed", "path": str(dest), "error": None, "progress": 1.0}
    assert dest.read_bytes() == b"converted"
    assert source.read_bytes() == b"source"
    assert list(tmp_path.glob("*.tmp.mp4")) == []


@pytest.mark.skipif(os.name == "nt", reason="fake ffmpeg is a POSIX script")
def test_shutdown_stops_running_conversion_and_skips_queued_jobs(tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"source")
    ffmpeg = fake_ffmpeg(tmp_path, """
open(output, "wb").write(b"partial")
print("progress=continue", flush=True)
time.sleep(60)
""")
    dest = transcode.output_path(source, "h264")
    results = []
    worker = Thread(target=lambda: results.append(transcode._run(source, dest, "h264", ffmpeg)))
    try:
        worker.start()
        deadline = time.monotonic() + 10
        while not transcode._processes and time.monotonic() < deadline:
            time.sleep(0.02)
        assert transcode._processes
        started = time.monotonic()
        transcode.shutdown()
        worker.join(10)
        assert not worker.is_alive()
        assert time.monotonic() - started < 10
        assert results[0]["status"] == "failed"
        assert not dest.exists()
        assert list(tmp_path.glob("*.tmp.mp4")) == []
        assert not transcode._processes
        video_id = int(time.time_ns() % 100000000)
        assert transcode.enqueue(video_id, source, "remux", ffmpeg)["status"] == "queued"
        for _ in range(200):
            if transcode.status(video_id, "remux")["status"] == "idle":
                break
            time.sleep(0.01)
        assert transcode.status(video_id, "remux")["status"] == "idle"
        assert not transcode.output_path(source, "remux").exists()
    finally:
        transcode.resume()
        for process in list(transcode._processes):
            process.kill()


def test_progress_is_reported_and_stale_temporary_files_are_removed(tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"source")
    temporary = tmp_path / "old.playable.tmp.mp4"
    temporary.write_bytes(b"partial")
    assert transcode.cleanup_temporary_files([tmp_path]) == 1
    assert not temporary.exists()
    assert source.read_bytes() == b"source"
    key = (123456, "remux")
    transcode._jobs[key] = {"status": "running", "progress": 0.0}
    transcode._set_progress(key, 0.45)
    assert transcode.status(*key)["progress"] == 0.45
