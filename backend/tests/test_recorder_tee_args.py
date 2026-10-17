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
