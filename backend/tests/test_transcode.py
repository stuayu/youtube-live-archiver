from pathlib import Path
from threading import Event
import time

from app.services import transcode
from app.services import ffmpeg


def test_remux_preserves_source_and_deduplicates(monkeypatch, tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"original")
    started, release = Event(), Event()
    calls = []
    def run(src, dest, mode, ffmpeg):
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


def test_failed_conversion_leaves_original_untouched(monkeypatch, tmp_path):
    source = tmp_path / "original.mkv"
    source.write_bytes(b"keep me")
    def fail(*_, **__):
        raise RuntimeError("conversion failed")
    monkeypatch.setattr(ffmpeg, "run_ffmpeg", fail)
    result = transcode._run(source, transcode.output_path(source, "remux"), "remux", "ffmpeg")
    assert result["status"] == "failed"
    assert source.read_bytes() == b"keep me"
    assert not transcode.output_path(source, "remux").exists()
