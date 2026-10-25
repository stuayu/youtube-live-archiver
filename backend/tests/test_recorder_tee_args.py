import subprocess
import io
import logging
from pathlib import Path

import pytest

from app.services import recorder


def test_tee_sidecar_templates_keep_thumbnail_and_description_with_recording(tmp_path):
    args = ["yt-dlp", "--write-thumbnail", "--write-description", "-o",
            str(tmp_path / "video.%(ext)s"), "https://youtube.com/watch?v=abcdefghijk"]
    tee_args = recorder._tee_ytdlp_arguments(args, tmp_path, "video")
    output_index = tee_args.index("-o")
    assert tee_args[output_index + 1] == "-"
    assert f"thumbnail:{tmp_path / 'video.%(ext)s'}" in tee_args
    assert f"description:{tmp_path / 'video.%(ext)s'}" in tee_args
    assert tee_args[tee_args.index("-P") + 1] == f"thumbnail:{tmp_path}"
    assert "--paths" not in tee_args


def test_tee_downloader_timeout_stops_process(monkeypatch):
    stopped = []
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    recorder.log.addHandler(handler)

    class HangingProcess:
        def wait(self, timeout):
            assert timeout == recorder.TEE_YTDLP_EXIT_TIMEOUT_SECONDS
            raise subprocess.TimeoutExpired("yt-dlp", timeout)

    monkeypatch.setattr(recorder, "stop_process", lambda process: stopped.append(process))
    try:
        with pytest.raises(RuntimeError, match="did not exit"):
            recorder._wait_for_tee_downloader(HangingProcess(), "abcdefghijk", "UCchannel")
    finally:
        recorder.log.removeHandler(handler)
    assert len(stopped) == 1
    assert "youtube_video_id=abcdefghijk" in output.getvalue()


def test_live_inspection_does_not_wait_for_nonterminal_interruption(monkeypatch):
    monkeypatch.setattr(recorder, "inspect", lambda *_args: {"live_status": "is_live"})
    monkeypatch.setattr(recorder.time, "sleep", lambda *_args: pytest.fail(
        "nonterminal live interruption must resume immediately"))

    result = recorder._inspect_live_after_exit("abcdefghijk", None, b"connection reset")

    assert result["live_status"] == "is_live"


def test_terminal_live_inspection_refreshes_heartbeat_between_short_transactions(
        monkeypatch, tmp_path):
    from types import SimpleNamespace

    calls = []
    live_video = SimpleNamespace(status="RECORDING", recording_heartbeat_at=None)

    class Session:
        active = False

        def __enter__(self):
            self.active = True
            return self

        def __exit__(self, *_args):
            self.active = False

        def get(self, *_args):
            return live_video

        def commit(self):
            calls.append(live_video.recording_heartbeat_at)

    session = Session()
    responses = iter(({"live_status": "is_live", "duration": 3},
                      {"live_status": "was_live", "duration": 3}))
    monkeypatch.setattr(recorder, "inspect", lambda *_args: next(responses))
    monkeypatch.setattr(recorder, "SessionLocal", lambda: session)
    monkeypatch.setattr("app.services.recovery.probe_duration", lambda _path: 3)
    monkeypatch.setattr(recorder.time, "sleep", lambda _delay: (
        pytest.fail("database transaction left open during delay") if session.active else None))
    media = tmp_path / "video.mkv"
    media.write_bytes(b"media")

    result = recorder._inspect_live_after_exit(
        "abcdefghijk", None, b"Did not get any data blocks", media, 42)

    assert result["live_status"] == "was_live"
    assert len(calls) == 1
    assert live_video.recording_heartbeat_at is not None
