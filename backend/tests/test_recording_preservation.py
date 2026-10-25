from pathlib import Path

from app import workers


def test_nonzero_recording_exit_preserves_partial_artifacts_and_error_log(tmp_path: Path):
    target = tmp_path / "UC_channel" / "2026" / "video123"
    target.mkdir(parents=True)
    partial = target / "video.part"
    info = target / "video.info.json"
    thumbnail = target / "video.webp"
    for path, value in ((partial, b"partial media"), (info, b'{"id":"video123"}'), (thumbnail, b"thumbnail")):
        path.write_bytes(value)

    log_path = workers._write_recording_error(target, "video123", b"yt-dlp exited 1: network error")

    assert target.is_dir()
    assert partial.read_bytes() == b"partial media"
    assert info.is_file() and thumbnail.is_file()
    assert log_path == target.parent / "video123-recording-error.log"
    assert log_path.read_text(encoding="utf-8") == "yt-dlp exited 1: network error"


def test_missing_media_failure_keeps_existing_files_and_writes_log(tmp_path: Path):
    target = tmp_path / "channel" / "video456"
    target.mkdir(parents=True)
    info = target / "video.info.json"
    thumbnail = target / "video.jpg"
    info.write_text("{}", encoding="utf-8")
    thumbnail.write_bytes(b"thumb")

    log_path = workers._write_recording_error(
        target, "video456", "yt-dlp exited successfully but no media file was found."
    )

    assert info.is_file() and thumbnail.is_file()
    assert log_path.is_file()
    assert "no media file" in log_path.read_text(encoding="utf-8")
