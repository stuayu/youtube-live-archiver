import json
import runpy
import shutil
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Channel, ChatMessage, Storage, Video
from app.workers import recorder_worker
from app.workers import recovery_worker


pytestmark = pytest.mark.integration


def test_attempt_result_classifier_covers_all_outcomes():
    from app.services.recorder import classify_attempt_result
    assert classify_attempt_result(0, True, "user", True) == "USER_STOP_MEDIA"
    assert classify_attempt_result(0, False, "user", True) == "USER_STOP_EMPTY"
    assert classify_attempt_result(0, True, "shutdown", True) == "INTERRUPTED_RECOVERY"
    assert classify_attempt_result(1, True, "lock_lost", True) == "INTERRUPTED_RECOVERY"
    assert classify_attempt_result(1, True, None, True) == "FAILED"
    assert classify_attempt_result(0, False, None, False) == "FAILED"
    assert classify_attempt_result(0, True, None, True) == "VERIFY_LIVE"
    assert classify_attempt_result(0, True, None, False) == "COMPLETE"


@pytest.mark.parametrize("interrupt_behavior", ["finalize", "leave_part"])
def test_user_stop_keeps_media_when_yt_dlp_exits_zero_or_nonzero(
        tmp_path, fake_youtube, interrupt_behavior):
    video_id = _create_video(tmp_path, f"stop-{interrupt_behavior}", chat=False)
    key = f"stop-{interrupt_behavior}"
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 5,
        "title": "Temporary live title", "fulltitle": "Stable stream title",
        "recording": {"mode": "live", "ended": False,
        "interrupt_behavior": interrupt_behavior}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file()
                     and (target / "video.f134.mkv.part").stat().st_size > 0)
    from app.api import stop_video_recording
    with SessionLocal() as db:
        stop_video_recording(video_id, db)
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE"), _get_video(video_id).raw_metadata
    row = _get_video(video_id)
    target = Path(row.file_path)
    assert row.is_partial and row.raw_metadata["user_stopped"]
    assert row.file_size > 0 and row.raw_metadata["local_duration"] > 0
    assert row.retention_expires_at is None
    assert row.title == "Stable stream title"
    assert row.raw_metadata["recording_start_offset_ms"] == 0
    assert row.raw_metadata["primary_media"] in {"video.mkv", "video.partial.mkv"}
    index = json.loads((target / "recording.json").read_text())
    assert index["segments"]
    assert index["intervals"][0]["started_at"]
    assert not (target / ".recorder.json").exists()
    recovery_worker()
    assert _get_video(video_id).status == "ACTIVE"
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


def test_user_stop_finalization_preserves_live_from_start_offset(tmp_path, fake_youtube, monkeypatch):
    from app.api import stop_video_recording
    from app.services.recorder import running_counts
    from app.services.recovery import probe_start_time

    monkeypatch.setattr("app.services.recorder.RECORDER_HEARTBEAT_SECONDS", 0.2)
    key = "from-start-user-stop-offset"
    video_id = _create_video(tmp_path, key, chat=False, from_start=True)
    expected_seconds = 37
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live",
        "release_timestamp": int(time.time()) - 600,
        "recording": {
            "mode": "live",
            "ended": False,
            "interrupt_behavior": "finalize",
            "start_offset_seconds": expected_seconds,
        },
    }}})

    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    part = target / "video.f134.mkv.part"
    assert _wait_for(lambda: part.is_file() and part.stat().st_size > 0)
    assert _wait_for(
        lambda: _get_video(video_id).raw_metadata.get("live_from_start_start_time_ms") is not None
    )
    captured_start_ms = _get_video(video_id).raw_metadata["live_from_start_start_time_ms"]
    assert captured_start_ms >= (expected_seconds - 3) * 1000
    assert probe_start_time(part) == pytest.approx(expected_seconds, abs=3)

    with SessionLocal() as db:
        stop_video_recording(video_id, db)

    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    row = _get_video(video_id)
    final_media = Path(row.file_path) / "video.mkv"
    assert probe_start_time(final_media) == pytest.approx(0, abs=0.5)
    assert row.raw_metadata["recording_start_offset_ms"] == captured_start_ms
    assert row.raw_metadata["recording_start_offset_ms"] >= (expected_seconds - 3) * 1000
    assert row.raw_metadata["attempts"][-1]["part_start_time_ms"] == captured_start_ms
    assert _wait_for(lambda: running_counts() == (0, 0))


@pytest.mark.parametrize("interrupt_behavior", ["finalize", "leave_part"])
def test_shutdown_stop_is_failed_but_recovery_starts_a_new_interval(
        tmp_path, fake_youtube, interrupt_behavior):
    key = f"shutdown-{interrupt_behavior}"
    video_id = _create_video(tmp_path, key, chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False,
        "interrupt_behavior": interrupt_behavior}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    assert _wait_for(lambda: (Path(_get_video(video_id).file_path) / "video.f134.mkv.part").is_file())
    from app.services.recorder import shutdown
    from app.workers import stop_recording
    assert shutdown(lambda pk: stop_recording(pk, user_requested=False), timeout=10) == []
    stopped = _get_video(video_id)
    assert stopped.status == "RECORDING_FAILED"
    assert stopped.next_recovery_at is None
    target = Path(stopped.file_path)
    assert any(path.stat().st_size > 0 for path in target.glob("video*"))
    recovery_worker()
    assert _get_video(video_id).status == "DISCOVERED"
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": True}}}})
    recorder_worker()
    assert _wait_for(lambda: (target / "video-part-2.mkv").is_file()
                     or (target / "video-part-2.mkv.part").is_file())
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))
    assert any(path.name.startswith("video-part-2.") for path in target.iterdir())


def test_live_zero_exit_while_still_live_restarts_another_interval(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "live-cut-short"
    video_id = _create_video(tmp_path, key, chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False,
        "early_exit": True, "early_exit_after": 6}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    assert _wait_for(lambda: (Path(_get_video(video_id).file_path) / "video.f134.mkv.part").is_file())
    assert _wait_for(lambda: _get_video(video_id).status in ("DISCOVERED", "RECORDING_FAILED"), timeout=20)
    first = _get_video(video_id)
    target = Path(first.file_path)
    if first.status == "RECORDING_FAILED":
        assert first.next_recovery_at is not None
        return
    assert first.raw_metadata["attempts"][-1]["stem"] == "video"
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_SEGMENT_INTERRUPTED"))
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": True}}}})
    recorder_worker()
    assert _wait_for(lambda: (target / "video-part-2.mkv").is_file())
    assert (target / "video.mkv").is_file()


def test_late_live_start_records_from_stream_start_without_backfill(tmp_path, fake_youtube):
    key = "late-live-start"
    video_id = _create_video(tmp_path, key, chat=False)
    with SessionLocal() as db:
        db.get(Video, video_id).retention_days = 30
        db.commit()
    release_timestamp = int(time.time()) - 180
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file()
                     and (target / "video.f134.mkv.part").stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": True}}}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE"), _get_video(video_id).raw_metadata
    row = _get_video(video_id)
    assert row.raw_metadata.get("archive_backfill") != "queued"
    assert row.raw_metadata["recording_start_offset_ms"] == 0
    assert row.raw_metadata["live_from_start"] == "used"
    assert not row.is_partial, row.raw_metadata
    assert row.retention_expires_at is not None
    assert any(call["argv"].count("--live-from-start") for call in fake_youtube.calls())
    recording_calls = [call["argv"] for call in fake_youtube.calls()
                       if "--skip-download" not in call["argv"]]
    assert any("--live-from-start" in call for call in recording_calls)
    assert all("--concurrent-fragments" not in call for call in recording_calls)
    recording_index = json.loads((Path(row.file_path) / "recording.json").read_text(encoding="utf-8"))
    assert recording_index["attempts"][-1]["from_start"] is True


def test_live_from_start_age_boundary_and_old_stream_skips(tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(settings, "live_from_start_max_age_hours", 12)
    fixed_now = 2_000_000_000.0
    assert recorder._live_start_too_old(fixed_now - 12 * 3600, fixed_now) is False
    assert recorder._live_start_too_old(fixed_now - 12 * 3600 - 1, fixed_now) is True
    monkeypatch.setattr(settings, "live_from_start_max_age_hours", 24)
    assert recorder._live_start_too_old(fixed_now - 20 * 3600, fixed_now) is False
    monkeypatch.setattr(settings, "live_from_start_max_age_hours", 12)
    video_id = _create_video(tmp_path, "too-old-live", chat=False)
    release_timestamp = int(time.time()) - 13 * 3600
    fake_youtube.set_state({"channels": {}, "videos": {"too-old-live": {
        "live_status": "is_live",
        "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": True},
    }}})

    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).raw_metadata.get("archive_backfill") == "queued"), (
        _get_video(video_id).status,
        _get_video(video_id).raw_metadata,
    )
    row = _get_video(video_id)
    assert row.raw_metadata["live_from_start"] == "skipped_too_old"
    calls = [call["argv"] for call in fake_youtube.calls()
             if "--skip-download" not in call["argv"]]
    assert calls and all("--live-from-start" not in call for call in calls)
    assert row.raw_metadata["recording_start_offset_ms"] >= 12 * 3600 * 1000
    assert row.raw_metadata["archive_backfill"] == "queued"
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.channel.live_playback_enabled = True
        assert recorder._select_live_from_start(db, video, video.channel, True) == "skipped_too_old"


def test_live_from_start_nonzero_media_start_is_recorded_and_backfilled(tmp_path, fake_youtube):
    video_id = _create_video(tmp_path, "nonzero-start", chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {"nonzero-start": {
        "live_status": "is_live",
        "release_timestamp": int(time.time()) - 60,
        "recording": {"mode": "live", "ended": False,
                      "start_offset_seconds": 17},
    }}})

    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    part = target / "video.f134.mkv.part"
    assert _wait_for(lambda: part.is_file() and part.stat().st_size > 0)
    assert _wait_for(
        lambda: _get_video(video_id).raw_metadata.get("live_from_start_start_time_ms") is not None
    )
    fake_youtube.set_state({"channels": {}, "videos": {"nonzero-start": {
        "live_status": "is_live",
        "release_timestamp": int(time.time()) - 60,
        "recording": {"mode": "live", "ended": True,
                      "start_offset_seconds": 17},
    }}})
    assert _wait_for(lambda: _get_video(video_id).raw_metadata.get("archive_backfill") == "queued"), (
        _get_video(video_id).status,
        _get_video(video_id).raw_metadata,
    )
    row = _get_video(video_id)
    assert row.raw_metadata["recording_start_offset_ms"] >= 5_000
    assert row.raw_metadata["archive_backfill"] == "queued"
    assert row.is_partial


def test_frag_files_are_not_media(tmp_path):
    from app.services.recorder import _stem_media_paths

    target = tmp_path / "frag-media"
    target.mkdir()
    fragment = target / "video.f134.mkv.part-Frag1"
    fragment.write_bytes(b"fragment data")
    assert _stem_media_paths(target, "video") == []


@pytest.mark.parametrize("disabled_by", ["channel", "global"])
def test_live_from_start_can_be_disabled_without_parallel_fragments(
    tmp_path, fake_youtube, monkeypatch, disabled_by
):
    video_id = _create_video(
        tmp_path,
        f"disabled-{disabled_by}",
        chat=False,
        from_start=disabled_by != "channel",
    )
    if disabled_by == "global":
        monkeypatch.setattr(settings, "live_from_start", False)
    fake_youtube.set_state({"channels": {}, "videos": {f"disabled-{disabled_by}": {
        "live_status": "is_live",
        "release_timestamp": int(time.time()) - 60,
        "recording": {"mode": "live", "ended": True},
    }}})

    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    row = _get_video(video_id)
    assert row.raw_metadata["live_from_start"] == "fallback"
    calls = [call["argv"] for call in fake_youtube.calls()
             if "--skip-download" not in call["argv"]]
    assert calls and all("--live-from-start" not in call for call in calls)
    assert all("--concurrent-fragments" not in call for call in calls)


def test_fake_live_from_start_parallel_mode_leaves_unplayable_fragments(tmp_path, fake_youtube):
    import os
    import subprocess
    import sys

    key = "parallel-fragments"
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live",
        "recording": {"mode": "live", "ended": False},
    }}})
    output = tmp_path / "parallel.%(ext)s"
    command = [
        sys.executable,
        str(Path(__file__).parent / "fakes" / "fake_ytdlp.py"),
        "-o",
        str(output),
        "--live-from-start",
        "--concurrent-fragments",
        "4",
        f"https://youtube.com/watch?v={key}",
    ]
    process = subprocess.Popen(command, env=os.environ.copy(), stdout=subprocess.DEVNULL)
    assert _wait_for(lambda: (tmp_path / "parallel.f134.mkv.part-Frag1").is_file())
    process.terminate()
    assert process.wait(timeout=5) != 0
    assert (tmp_path / "parallel.f134.mkv.part").is_file()
    assert (tmp_path / "parallel.f134.mkv.part").stat().st_size == 0
    assert list(tmp_path.glob("parallel.*.part-Frag*"))
    assert not (tmp_path / "parallel.mkv").exists()


def test_live_from_start_unsupported_falls_back_after_format_retry(tmp_path, fake_youtube):
    from app.services.recorder import running_counts

    key = "from-start-unsupported"
    video_id = _create_video(tmp_path, key, chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 600,
        "recording": {"mode": "live", "ended": True, "live_from_start_unsupported": True,
                      "format_error": True}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "DISCOVERED"), _get_video(video_id).raw_metadata
    assert _wait_for(lambda: running_counts() == (0, 0))
    row = _get_video(video_id)
    assert row.raw_metadata["live_from_start"] == "fallback"
    assert row.raw_metadata["archive_backfill"] == "queued"
    assert row.raw_metadata["recording_start_offset_ms"] >= 120_000
    calls = [call["argv"] for call in fake_youtube.calls()
             if "--skip-download" not in call["argv"]]
    assert any("--live-from-start" in call for call in calls)
    assert any("--live-from-start" not in call for call in calls)
    assert any("--concurrent-fragments" not in call and "--live-from-start" not in call for call in calls)
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_FROM_START_FALLBACK"))


def test_short_archive_backfill_is_retained_and_retried_with_new_stem(tmp_path, fake_youtube):
    from app.models import now
    from app.services.recorder import running_counts

    key = "short-archive-backfill"
    video_id = _create_video(tmp_path, key, chat=False, live_status="was_live")
    target = tmp_path / "archive" / f"UC{key:0<22}"[:24] / "2026" / key
    target.mkdir(parents=True, exist_ok=True)
    fixture = runpy.run_path(str(Path(__file__).parent / "fakes" / "fake_ytdlp.py"))["_media_fixture"]()
    (target / "video.mkv").write_bytes(fixture)
    with SessionLocal() as db:
        row = db.get(Video, video_id)
        row.status = "DISCOVERED"
        row.file_path = str(target)
        row.raw_metadata = {"live_status": "was_live", "archive_backfill": "queued",
            "recording_start_offset_ms": 300_000, "primary_media": "video.mkv",
            "attempts": [{"stem": "video", "profile": "live", "from_start": False,
                          "outcome": "interrupted", "started_at": now().isoformat()}]}
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 600,
        "recording": {"mode": "archive"}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "DISCOVERED"
                     and _get_video(video_id).raw_metadata.get("attempts", [])[-1].get("outcome") == "incomplete_archive")
    partial = _get_video(video_id)
    assert partial.is_partial and partial.raw_metadata["archive_backfill"] == "queued"
    assert partial.raw_metadata["primary_media"] == "video.mkv"
    assert partial.raw_metadata["recording_start_offset_ms"] == 300_000
    assert (target / "video-archive.mkv").is_file()
    with SessionLocal() as db:
        db.get(Video, video_id).next_recovery_at = now()
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 3,
        "recording": {"mode": "archive"}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    assert (target / "video-archive-2.mkv").is_file()
    assert (target / "video-archive.mkv").is_file()
    assert _wait_for(lambda: running_counts() == (0, 0))
    assert not (Path(row.file_path) / ".recorder.json").exists()
    completed = _get_video(video_id)
    assert completed.raw_metadata["archive_backfill"] == "completed"
    assert completed.raw_metadata["recording_start_offset_ms"] == 0
    assert completed.raw_metadata["primary_media"] == "video-archive-2.mkv"


def test_missing_backfill_fragment_keeps_saved_live_media_and_requeues(tmp_path, fake_youtube):
    from app.models import now

    key = "missing-backfill-fragment"
    video_id = _create_video(tmp_path, key, chat=False, live_status="was_live")
    target = tmp_path / "archive" / f"UC{key:0<22}"[:24] / "2026" / key
    target.mkdir(parents=True, exist_ok=True)
    fixture = runpy.run_path(str(Path(__file__).parent / "fakes" / "fake_ytdlp.py"))[
        "_media_fixture"]()
    saved_live_media = target / "video.mkv"
    saved_live_media.write_bytes(fixture)
    with SessionLocal() as db:
        row = db.get(Video, video_id)
        row.status = "DISCOVERED"
        row.file_path = str(target)
        row.recording_finished_at = now()
        row.raw_metadata = {
            "live_status": "was_live",
            "recording_start_offset_ms": 300_000, "primary_media": saved_live_media.name,
            "attempts": [{"stem": "video", "profile": "live", "from_start": False,
                          "outcome": "success", "started_at": now().isoformat()}],
        }
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 600,
        "recording": {"mode": "archive", "fail": (
            "Unable to download video: [Errno 2] No such file or directory: "
            "'/tmp/video-archive.f160.mp4.part-Frag84'")},
    }}})

    recorder_worker()

    assert _wait_for(lambda: _get_video(video_id).raw_metadata.get("attempts", [])[-1].get("outcome")
                     == "archive_backfill_retryable_failure")
    row = _get_video(video_id)
    assert row.is_partial and row.raw_metadata["archive_backfill"] == "queued"
    assert row.raw_metadata["attempts"][-1]["outcome"] == "archive_backfill_retryable_failure"
    assert row.next_recovery_at is not None
    assert saved_live_media.stat().st_size == len(fixture)
    assert (target.parent / f"{key}-recording-error.log").exists() is False
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.video_id == video_id,
            AuditLog.action == "ARCHIVE_BACKFILL_RETRY_QUEUED"))


def test_live_gap_backfill_disabled_does_not_queue_archive(tmp_path, fake_youtube, monkeypatch):
    monkeypatch.setattr(settings, "live_gap_backfill", False)
    key = "late-live-no-backfill"
    video_id = _create_video(tmp_path, key, chat=False)
    release_timestamp = int(time.time()) - 180
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file()
                     and (target / "video.f134.mkv.part").stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": True}}}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    row = _get_video(video_id)
    assert row.raw_metadata.get("archive_backfill") != "queued"


def test_live_short_zero_exit_uses_recovery_backoff(tmp_path, fake_youtube):
    key = "live-short-exit"
    video_id = _create_video(tmp_path, key, chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False,
        "early_exit": True, "early_exit_after": 0}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING_FAILED")
    row = _get_video(video_id)
    assert row.next_recovery_at is not None
    assert not (Path(row.file_path) / ".recorder.json").exists()
    assert ((Path(row.file_path) / "video.mkv").is_file()
            or (Path(row.file_path) / "video.partial.mkv").is_file())


def test_recording_stderr_tail_is_bounded(tmp_path, fake_youtube):
    key = "stderr-flood"
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 2,
        "recording": {"mode": "archive", "stderr_bytes": 300_000}}}})
    target = tmp_path / "media"
    target.mkdir()
    from app.services.recorder import popen_recording, stderr_tail, STDERR_TAIL_LIMIT
    process = popen_recording(["-o", str(target / "video.%(ext)s"),
                               f"https://youtube.com/watch?v={key}"])
    assert process.wait(timeout=20) == 0
    tail = stderr_tail(process)
    assert len(tail) <= STDERR_TAIL_LIMIT
    assert len(tail) == STDERR_TAIL_LIMIT


def _wait_for(predicate, timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def _create_video(tmp_path, video_id="record-live", *, chat=True, edit_mode="off", live_status="is_live",
                  playback=False, from_start=True):
    (tmp_path / "archive").mkdir(exist_ok=True)
    with SessionLocal() as db:
        storage = Storage(name=f"storage-{video_id}", root_path=str(tmp_path / "archive"))
        db.add(storage)
        channel = Channel(youtube_channel_id=f"UC{video_id:0<22}"[:24], url="https://youtube.com/@test", name="test",
                          storage=storage, chat_enabled=chat, retention_days=30, edit_detection_mode=edit_mode,
                          live_playback_enabled=playback, live_from_start=from_start)
        db.add(channel)
        video = Video(youtube_video_id=video_id, channel=channel, title="test", status="DISCOVERED",
                      raw_metadata={"live_status": live_status, "rule_result": "included"})
        db.add(video)
        db.commit()
        return video.id


def _get_video(video_id):
    with SessionLocal() as db:
        return db.get(Video, video_id)


def test_dispatch_returns_and_live_video_is_separate_from_chat(tmp_path, fake_youtube, monkeypatch):
    video_id = _create_video(tmp_path)
    fake_youtube.set_state({"channels": {}, "videos": {"record-live": {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": False}}}})
    started = time.monotonic()
    recorder_worker()
    assert time.monotonic() - started < 2
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    row = _get_video(video_id)
    target = Path(row.file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").exists() and (target / "video.f134.mkv.part").stat().st_size > 0)
    calls = fake_youtube.calls()
    video_call = next(call["argv"] for call in calls if "--skip-download" not in call["argv"])
    assert "--write-subs" not in video_call
    assert any("--skip-download" in call["argv"] and "--write-subs" in call["argv"] for call in calls)
    state = {"channels": {}, "videos": {"record-live": {"live_status": "is_live", "release_timestamp": int(time.time()) - 2,
             "recording": {"mode": "live", "ended": True}}}}
    fake_youtube.set_state(state)
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))
    saved = _get_video(video_id)
    assert saved.original_duration and saved.raw_metadata["local_duration"]
    assert saved.raw_metadata["rule_result"] == "included"
    assert saved.raw_metadata["recording_start_offset_ms"] >= 0
    recording_index = json.loads((target / "recording.json").read_text(encoding="utf-8"))
    assert recording_index["primary_media"] == "video.mkv"
    assert recording_index["segments"]
    assert list(target.glob("*.live_chat.json"))
    with SessionLocal() as db:
        assert db.scalar(select(ChatMessage.id).where(ChatMessage.video_id == video_id))


def test_partial_retention_policy_is_always_keep():
    from datetime import datetime, timezone
    from app.services.policy import initial_retention_decision, may_delete
    now = datetime.now(timezone.utc)
    assert initial_retention_decision(protected=False, finished_at=now, expires_at=now, now=now, is_partial=True) == "KEEP"
    assert not may_delete(protected=False, status="DELETE_ELIGIBLE", remote_status="PUBLIC", edit_unchanged=True,
                          check_succeeded=True, retention_expires_at=now, grace_until=now, now=now, is_partial=True)


def test_user_stop_salvages_and_recovery_does_not_restart(tmp_path, fake_youtube):
    video_id = _create_video(tmp_path, "record-stop", chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {"record-stop": {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 3,
        "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    part = Path(_get_video(video_id).file_path) / "video.f134.mkv.part"
    assert _wait_for(lambda: part.is_file() and part.stat().st_size > 0)
    from app.api import stop_video_recording, retry_video_recording
    with SessionLocal() as db:
        stop_video_recording(video_id, db)
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))
    saved = _get_video(video_id)
    partial = Path(saved.file_path) / "video.partial.mkv"
    assert saved.is_partial and partial.is_file() and saved.retention_expires_at is None
    recovery_worker()
    assert _get_video(video_id).status == "ACTIVE"
    with SessionLocal() as db:
        result = retry_video_recording(video_id, db)
    assert result["status"] == "DISCOVERED"
    assert not _get_video(video_id).raw_metadata.get("user_stopped")


def test_recording_retry_rejects_video_with_active_recording_status(tmp_path):
    from fastapi import HTTPException
    from app.api import retry_video_recording

    video_id = _create_video(tmp_path, "retry-while-recording", chat=False)
    target = tmp_path / "archive" / "channel" / "2026" / "retry-while-recording"
    target.mkdir(parents=True)
    (target / "video.mkv.part").write_bytes(b"recording in progress")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING"
        video.file_path = str(target)
        db.commit()

    with SessionLocal() as db:
        with pytest.raises(HTTPException) as error:
            retry_video_recording(video_id, db)

    assert error.value.status_code == 409
    assert _get_video(video_id).status == "RECORDING"


def test_manual_delete_does_not_remove_archive_while_recorder_lock_is_held(
        tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from fastapi import HTTPException
    from app.api import videos as api
    from app.services import locks

    video_id = _create_video(tmp_path, "delete-recorder-race", chat=False)
    target = tmp_path / "archive" / "channel" / "2026" / "delete-recorder-race"
    target.mkdir(parents=True)
    (target / "video.mkv").write_bytes(b"recording evidence")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "DISCOVERED"
        video.file_path = str(target)
        video.storage_id = video.channel.storage_id
        db.commit()

    @contextmanager
    def recorder_lock_is_busy(_namespace, _key):
        yield SimpleNamespace(acquired=False)

    monkeypatch.setattr(locks.default_locks, "acquire", recorder_lock_is_busy)
    with SessionLocal() as db:
        with pytest.raises(HTTPException) as error:
            api.delete_video(video_id, db)

    assert error.value.status_code == 409
    assert target.is_dir()
    assert _get_video(video_id).status == "DISCOVERED"


def test_live_and_archive_recordings_have_separate_concurrency_limits(tmp_path, fake_youtube, monkeypatch):
    live_one = _create_video(tmp_path, "live-one", chat=False)
    live_two = _create_video(tmp_path, "live-two", chat=False)
    archive = _create_video(tmp_path, "archive-one", chat=False, live_status="was_live")
    monkeypatch.setattr(settings, "max_concurrent_live_recordings", 2)
    monkeypatch.setattr(settings, "max_concurrent_archive_downloads", 1)
    fake_youtube.set_state({"channels": {}, "videos": {
        "live-one": {"live_status": "is_live", "recording": {"mode": "live", "ended": False}},
        "live-two": {"live_status": "is_live", "recording": {"mode": "live", "ended": False}},
        "archive-one": {"live_status": "was_live", "duration": 3, "recording": {"mode": "archive", "delay": 1}},
    }})
    recorder_worker()
    assert _wait_for(lambda: all(_get_video(pk).status == "RECORDING" for pk in (live_one, live_two, archive)))
    for video_id in (live_one, live_two):
        target = Path(_get_video(video_id).file_path)
        assert _wait_for(lambda target=target: (target / "video.f134.mkv.part").is_file()
                         and (target / "video.f134.mkv.part").stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {
        key: {"live_status": "is_live", "recording": {"mode": "live", "ended": True}}
        for key in ("live-one", "live-two")
    }})
    assert _wait_for(lambda: all(_get_video(pk).status == "ACTIVE" for pk in (live_one, live_two, archive)))
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


def test_fingerprint_error_does_not_fail_recording(tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder
    from app.services import recovery
    sampled = []
    video_id = _create_video(tmp_path, "fingerprint-live", chat=False, edit_mode="standard")
    monkeypatch.setattr(settings, "fingerprint_enabled", True)
    monkeypatch.setattr(recovery, "probe_duration", lambda *_: 30.0)
    def fail_fingerprint(*args):
        sampled.append(args)
        raise RuntimeError("ffmpeg failed")
    monkeypatch.setattr(recorder, "video_phash", fail_fingerprint)
    fake_youtube.set_state({"channels": {}, "videos": {"fingerprint-live": {
        "live_status": "is_live", "release_timestamp": int(time.time()) + 100, "duration": 3,
        "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file()
                     and (target / "video.f134.mkv.part").stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {"fingerprint-live": {
        "live_status": "is_live", "release_timestamp": int(time.time()) + 100, "duration": 3,
        "recording": {"mode": "live", "ended": True}}}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    saved_before = _get_video(video_id)
    assert sampled, (saved_before.raw_metadata, saved_before.original_duration, saved_before.is_partial,
                     saved_before.edit_detection_mode, [p.name for p in Path(saved_before.file_path).iterdir()])
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.action).where(AuditLog.video_id == video_id, AuditLog.action == "FINGERPRINT_FAILED"))
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


def test_direct_recording_of_upcoming_event_returns_to_waiting_live(tmp_path, fake_youtube):
    video_id = _create_video(tmp_path, "upcoming-record", chat=False, live_status="is_upcoming")
    fake_youtube.set_state({"channels": {}, "videos": {"upcoming-record": {"live_status": "is_upcoming"}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "WAITING_LIVE")
    row = _get_video(video_id)
    assert row.raw_metadata["live_status"] == "is_upcoming"
    assert row.status != "RECORDING_FAILED"


def test_archive_retry_reuses_stem_and_resumes_partial_file(tmp_path, fake_youtube):
    video_id = _create_video(tmp_path, "archive-retry", chat=False, live_status="was_live")
    fake_youtube.set_state({"channels": {}, "videos": {"archive-retry": {
        "live_status": "was_live", "duration": 3,
        "recording": {"mode": "archive", "fail": "network_error"}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING_FAILED")
    row = _get_video(video_id)
    target = Path(row.file_path)
    partial = target / "video.mkv.part"
    assert partial.is_file() and partial.stat().st_size > 0
    error_log = target.parent / "archive-retry-recording-error.log"
    assert error_log.is_file() and "network unreachable" in error_log.read_text(encoding="utf-8")
    from datetime import timedelta
    from app.models import now
    with SessionLocal() as db:
        db.get(Video, video_id).next_recovery_at = now() - timedelta(seconds=1)
        db.commit()
    recovery_worker()
    assert _get_video(video_id).status == "DISCOVERED"
    fake_youtube.set_state({"channels": {}, "videos": {"archive-retry": {
        "live_status": "was_live", "duration": 3, "release_timestamp": int(time.time()) - 20,
        "recording": {"mode": "archive"}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    saved = _get_video(video_id)
    assert (target / "video.mkv").is_file()
    assert [attempt["stem"] for attempt in saved.raw_metadata["attempts"]] == ["video", "video"]
    assert saved.raw_metadata["recording_start_offset_ms"] == 0
    assert _wait_for(lambda: __import__("app.services.recorder", fromlist=["running_counts"]).running_counts() == (0, 0))


def test_archive_backfill_completed_only_for_queued_backfill():
    from app.services.recorder import _archive_backfill_completed

    assert _archive_backfill_completed({"archive_backfill": "queued"}, "archive")
    assert not _archive_backfill_completed({}, "archive")
    assert not _archive_backfill_completed({"archive_backfill": "queued"}, "live")


def test_integrity_skips_unreachable_storage_and_marks_reachable_missing_media(tmp_path):
    from app.workers import integrity_worker
    unavailable_id = _create_video(tmp_path, "integrity-unreachable", chat=False)
    missing_id = _create_video(tmp_path, "integrity-missing", chat=False)
    with SessionLocal() as db:
        unavailable = db.get(Video, unavailable_id)
        unavailable.status = "ACTIVE"
        unavailable.file_path = str(tmp_path / "unmounted" / "video")
        unavailable.channel.storage.root_path = str(tmp_path / "unmounted")
        db.get(Video, missing_id).status = "ACTIVE"
        db.get(Video, missing_id).file_path = str(tmp_path / "archive" / "empty-video")
        (tmp_path / "archive" / "empty-video").mkdir()
        db.commit()
    integrity_worker()
    assert _get_video(unavailable_id).status == "ACTIVE"
    assert _get_video(unavailable_id).integrity_checked_at is None
    assert _get_video(missing_id).status == "RECORDING_FAILED"
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(AuditLog.video_id == missing_id, AuditLog.action == "MEDIA_MISSING"))


def test_retention_worker_isolates_failure_per_video(tmp_path, monkeypatch):
    from datetime import timedelta
    from app import workers
    first = _create_video(tmp_path, "retention-fails", chat=False)
    second = _create_video(tmp_path, "retention-continues", chat=False)
    with SessionLocal() as db:
        for video_id in (first, second):
            video = db.get(Video, video_id)
            video.status = "ACTIVE"
            video.recording_finished_at = video.created_at
            video.retention_expires_at = video.created_at - timedelta(days=1)
        db.commit()
    def verify(db, video, final=False):
        if video.id == first:
            raise RuntimeError("remote timeout")
        video.status = "DELETE_ELIGIBLE"
        return False
    monkeypatch.setattr(workers, "verify_video", verify)
    workers.retention_worker()
    assert _get_video(first).status == "ACTIVE"
    assert _get_video(second).status == "DELETE_ELIGIBLE"


def test_application_shutdown_stops_recording_process(tmp_path, fake_youtube):
    video_id = _create_video(tmp_path, "shutdown-live", chat=False)
    fake_youtube.set_state({"channels": {}, "videos": {"shutdown-live": {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    from app.services.recorder import shutdown
    from app.workers import stop_recording
    unfinished = shutdown(lambda pk: stop_recording(pk, user_requested=False), timeout=10)
    assert unfinished == []
    row = _get_video(video_id)
    assert row.status == "RECORDING_FAILED"
    assert row.next_recovery_at is None
    from app.services.recorder import running_counts
    assert running_counts() == (0, 0)


def test_storage_waiting_recovers_on_fallback_storage(tmp_path, fake_youtube):
    import logging

    records = []
    handler = logging.Handler()
    handler.emit = lambda record: records.append(record)
    logger = logging.getLogger("app")
    logger.addHandler(handler)
    video_id = _create_video(tmp_path, "storage-retry", chat=False, live_status="was_live")
    fallback_path = tmp_path / "fallback"
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.channel.storage.root_path = str(tmp_path / "offline-primary")
        fallback = Storage(name="fallback-storage", root_path=str(fallback_path))
        db.add(fallback)
        db.flush()
        video.channel.fallback_storage = fallback
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"storage-retry": {
        "live_status": "was_live", "duration": 3, "recording": {"mode": "archive"}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "WAITING_FOR_STORAGE")
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))
    first_retry_at = _get_video(video_id).next_recovery_at
    assert first_retry_at is not None
    recorder_worker()
    assert _get_video(video_id).next_recovery_at == first_retry_at
    logger.removeHandler(handler)
    assert sum("storage unavailable for channel" in record.getMessage() for record in records) == 1
    fallback_path.mkdir()
    from app.models import now
    with SessionLocal() as db:
        waiting = db.get(Video, video_id)
        waiting.next_recovery_at = now()
        db.commit()
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    saved = _get_video(video_id)
    assert saved.storage_id is not None
    assert Path(saved.file_path).is_relative_to(fallback_path)
    assert _wait_for(lambda: running_counts() == (0, 0))


def test_stale_live_recovery_salvages_then_records_new_stem(tmp_path, fake_youtube, monkeypatch):
    from datetime import timedelta
    from app.models import now
    video_id = _create_video(tmp_path, "crash-live", chat=False)
    target = tmp_path / "archive" / "crash-channel" / "2026" / "crash-live"
    target.mkdir(parents=True)
    fixture = runpy.run_path(str(Path(__file__).parent / "fakes" / "fake_ytdlp.py"))["_media_fixture"]()
    (target / "video.mkv.part").write_bytes(fixture)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING"
        video.file_path = str(target)
        video.recording_heartbeat_at = now() - timedelta(minutes=3)
        video.recorded_at = now() - timedelta(minutes=4)
        video.raw_metadata = {**video.raw_metadata, "attempts": [{"stem": "video", "profile": "live",
            "started_at": (now() - timedelta(minutes=4)).isoformat(), "outcome": "interrupted"}]}
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"crash-live": {"live_status": "is_live",
        "release_timestamp": int(time.time()) - 60, "recording": {"mode": "live", "ended": False}}}})
    recovery_worker()
    assert _get_video(video_id).status == "DISCOVERED"
    assert (target / "video.partial.mkv").is_file()
    fake_youtube.set_state({"channels": {}, "videos": {"crash-live": {"live_status": "is_live",
        "release_timestamp": int(time.time()) - 60, "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    first_part = target / "video-part-2.f134.mkv.part"
    assert _wait_for(lambda: first_part.is_file() and first_part.stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {"crash-live": {"live_status": "is_live",
        "release_timestamp": int(time.time()) - 60, "recording": {"mode": "live", "ended": True}}}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    row = _get_video(video_id)
    assert not row.is_partial and row.raw_metadata.get("archive_backfill") != "queued"
    assert row.raw_metadata["primary_media"] == "video-part-2.mkv"
    assert (target / "video.partial.mkv").is_file()
    assert (target / "video-part-2.mkv").is_file()
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


def test_stale_recording_scan_does_not_overwrite_recording_completed_before_lock(
        tmp_path, monkeypatch):
    from contextlib import contextmanager
    from datetime import timedelta
    from types import SimpleNamespace
    from app import workers
    from app.models import now

    video_id = _create_video(tmp_path, "stale-completed-race", chat=False)
    target = tmp_path / "archive" / "channel" / "2026" / "stale-completed-race"
    target.mkdir(parents=True)
    (target / "video.mkv").write_bytes(b"completed archive")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING"
        video.file_path = str(target)
        video.recording_heartbeat_at = now() - timedelta(minutes=3)
        db.commit()

    @contextmanager
    def complete_before_lock(_namespace, _key):
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            video.status = "ACTIVE"
            video.recording_heartbeat_at = None
            video.recording_finished_at = now()
            db.commit()
        yield SimpleNamespace(acquired=True, still_owned=lambda: True)

    monkeypatch.setattr(workers.default_locks, "acquire", complete_before_lock)
    workers._recover_stale_recordings()

    row = _get_video(video_id)
    assert row.status == "ACTIVE"
    assert row.recording_finished_at is not None
    assert (target / "video.mkv").is_file()


def test_recovery_attempt_limit_exhausts_silently_and_manual_retry_resets(tmp_path, fake_youtube, monkeypatch):
    video_id = _create_video(tmp_path, "recovery-limit", chat=False)
    monkeypatch.setattr(settings, "recovery_max_attempts", 2)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING_FAILED"
        video.recovery_attempts = 2
        video.next_recovery_at = None
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"recovery-limit": {"live_status": "is_live"}}})
    recovery_worker()
    first = _get_video(video_id)
    assert first.raw_metadata["recovery_exhausted"] is True
    assert first.next_recovery_at is None
    action_count = len(fake_youtube.calls())
    recovery_worker()
    assert len(fake_youtube.calls()) == action_count
    with SessionLocal() as db:
        from app.api import retry_video_recording
        retry_video_recording(video_id, db)
    assert not _get_video(video_id).raw_metadata.get("recovery_exhausted")


def test_format_error_retries_once_with_automatic_selection(tmp_path, fake_youtube, monkeypatch):
    from app.services import quality
    video_id = _create_video(tmp_path, "format-fallback", chat=False, live_status="was_live")
    monkeypatch.setattr(quality, "resolve_quality", lambda *_: ("requested-format", "test"))
    fake_youtube.set_state({"channels": {}, "videos": {"format-fallback": {
        "live_status": "was_live", "duration": 3,
        "recording": {"mode": "archive", "format_error": True}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE")
    row = _get_video(video_id)
    assert row.format_fallback is True
    attempts = fake_youtube.calls()
    record_calls = [call["argv"] for call in attempts if "--print-to-file" in call["argv"]]
    assert sum("-f" in argv for argv in record_calls) == 1
    assert len(record_calls) == 2
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


def _make_expired_video(tmp_path, video_id, *, partial=False):
    from datetime import timedelta
    from app.models import now
    video_id_pk = _create_video(tmp_path, video_id, chat=False)
    directory = tmp_path / "archive" / "channel" / "2026" / video_id
    directory.mkdir(parents=True)
    (directory / "evidence.bin").write_bytes(b"recorded evidence")
    with SessionLocal() as db:
        video = db.get(Video, video_id_pk)
        video.status = "ACTIVE"
        video.file_path = str(directory)
        video.recording_finished_at = now() - timedelta(days=40)
        video.retention_expires_at = now() - timedelta(days=10)
        video.is_partial = partial
        db.commit()
    return video_id_pk, directory


def test_retention_first_check_grace_and_final_delete(tmp_path, fake_youtube):
    from datetime import timedelta
    from app.models import now
    from app.workers import retention_worker
    video_id, directory = _make_expired_video(tmp_path, "retention-cycle")
    fake_youtube.set_state({"channels": {}, "videos": {"retention-cycle": {
        "availability": "public", "duration": 100, "live_status": "was_live"}}})
    retention_worker()
    row = _get_video(video_id)
    assert row.status == "DELETE_ELIGIBLE" and row.deletion_after > now()
    with SessionLocal() as db:
        db.get(Video, video_id).deletion_after = now() - timedelta(seconds=1)
        db.commit()
    retention_worker()
    assert _get_video(video_id).status == "DELETED_BY_RETENTION"
    assert not directory.exists()
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(AuditLog.video_id == video_id, AuditLog.action == "DELETE"))


def test_retention_remote_wait_closes_short_idle_transaction(tmp_path, monkeypatch):
    import os
    from datetime import timedelta
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from app.models import RemoteCheck, now
    from app import workers

    video_id, directory = _make_expired_video(tmp_path, "slow-retention")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.edit_detection_mode = "basic"
        video.original_duration = 100
        db.commit()
    engine = create_engine(os.environ["DATABASE_URL"],
                           connect_args={"options": "-c idle_in_transaction_session_timeout=1000"})
    short_sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(workers, "SessionLocal", short_sessions)

    def slow_inspection(*args, **kwargs):
        time.sleep(1.3)
        return "PUBLIC", 100, None, {"availability": "public", "duration": 100}

    monkeypatch.setattr(workers, "_inspection", slow_inspection)
    with short_sessions() as db:
        assert workers.verify_video(db, db.get(Video, video_id)) is False
        db.commit()
    first = _get_video(video_id)
    assert first.status == "DELETE_ELIGIBLE"
    with SessionLocal() as db:
        db.get(Video, video_id).deletion_after = now() - timedelta(seconds=1)
        db.commit()
    with short_sessions() as db:
        assert workers.verify_video(db, db.get(Video, video_id), final=True) is True
        db.commit()
    final = _get_video(video_id)
    assert final.status == "DELETED_BY_RETENTION"
    assert not directory.exists()
    with SessionLocal() as db:
        assert db.scalar(select(RemoteCheck.id).where(RemoteCheck.video_id == video_id))
    engine.dispose()


def test_retention_rejects_directory_without_matching_video_id(tmp_path, monkeypatch):
    from datetime import timedelta
    from app import workers
    from app.models import RemoteCheck, now

    video_id, _ = _make_expired_video(tmp_path, "unsafe-retention")
    channel_root = tmp_path / "archive" / "channel"
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.file_path = str(channel_root)
        video.edit_detection_mode = "basic"
        video.original_duration = 100
        video.status = "DELETE_ELIGIBLE"
        db.add(RemoteCheck(video_id=video.id, remote_status="PUBLIC", checked_at=now(),
                           error=None, edit_detected=False))
        video.deletion_candidate_at = now() - timedelta(days=5)
        video.deletion_after = now() - timedelta(seconds=1)
        db.commit()

    monkeypatch.setattr(workers, "_inspection", lambda *_args, **_kwargs: (
        "PUBLIC", 100, None, {"availability": "public", "duration": 100}))
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert workers.verify_video(db, video, final=True) is False
        assert video.status == "PROTECTION_PENDING"
        db.commit()
    assert channel_root.is_dir()


@pytest.mark.parametrize("missing", ["folder", "file", "legacy-file"],
                         ids=["missing-folder", "missing-file", "legacy-file"])
def test_retention_completes_missing_folder_or_unlinks_legacy_video_file(
        tmp_path, monkeypatch, missing):
    from datetime import timedelta
    from app import workers
    from app.models import RemoteCheck, now

    video_id, directory = _make_expired_video(tmp_path, f"retention-gone-{missing}")
    root = tmp_path / "archive"
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.edit_detection_mode = "off"
        video.status = "DELETE_ELIGIBLE"
        video.deletion_candidate_at = now() - timedelta(days=5)
        video.deletion_after = now() - timedelta(seconds=1)
        db.add(RemoteCheck(video_id=video.id, remote_status="PUBLIC", checked_at=now(),
                           error=None, edit_detected=False))
        if missing == "folder":
            video.file_path = str(directory)
        elif missing == "file":
            video.file_path = str(root / f"video-{video.youtube_video_id}.mp4")
        else:
            direct_file = root / f"video-{video.youtube_video_id}.mp4"
            direct_file.write_bytes(b"media")
            (root / "unrelated.txt").write_text("keep", encoding="utf-8")
            video.file_path = str(direct_file)
        db.commit()
    if missing == "folder":
        shutil.rmtree(directory)
    monkeypatch.setattr(workers, "_inspection", lambda *_args, **_kwargs: (
        "PUBLIC", 100, None, {"availability": "public", "duration": 100}))

    with SessionLocal() as db:
        assert workers.verify_video(db, db.get(Video, video_id), final=True) is True
        db.commit()

    row = _get_video(video_id)
    assert row.status == "DELETED_BY_RETENTION"
    with SessionLocal() as db:
        audit = db.scalar(select(AuditLog).where(
            AuditLog.video_id == video_id, AuditLog.action == "DELETE"))
        if missing != "legacy-file":
            assert audit.details["file_state"] == "already_missing"
    if missing == "legacy-file":
        assert not Path(row.file_path).exists()
        assert (root / "unrelated.txt").exists()


@pytest.mark.parametrize(("remote", "expected", "reason"), [
    ({"status": "private"}, "PROTECTED", "REMOTE_PRIVATE"),
    ({"error": "network_error"}, "PROTECTION_PENDING", "REMOTE_CHECK_FAILED"),
])
def test_retention_remote_private_and_network_failure_keep_files(tmp_path, fake_youtube, remote, expected, reason):
    from app.workers import retention_worker
    video_id, directory = _make_expired_video(tmp_path, f"retention-{expected.lower()}")
    fake_youtube.set_state({"channels": {}, "videos": {f"retention-{expected.lower()}": {
        "live_status": "was_live", "duration": 100, **remote}}})
    retention_worker()
    row = _get_video(video_id)
    assert row.status == expected
    assert row.protection_reason == reason
    assert directory.exists()


def test_retention_missing_availability_never_deletes(tmp_path, fake_youtube):
    from datetime import timedelta
    from app import workers
    from app.models import RemoteCheck, now

    video_id, directory = _make_expired_video(tmp_path, "retention-no-availability")
    fake_youtube.set_state({"channels": {}, "videos": {"retention-no-availability": {
        "live_status": "was_live", "duration": 100}}})
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.edit_detection_mode = "off"
        video.status = "DELETE_ELIGIBLE"
        video.deletion_candidate_at = now() - timedelta(days=5)
        video.deletion_after = now() - timedelta(seconds=1)
        db.add(RemoteCheck(video_id=video.id, remote_status="PUBLIC", checked_at=now(),
                           error=None, edit_detected=False))
        db.commit()

    with SessionLocal() as db:
        assert workers.verify_video(db, db.get(Video, video_id), final=True) is False
        db.commit()

    row = _get_video(video_id)
    assert row.status == "PROTECTION_PENDING"
    assert row.remote_status == "UNKNOWN"
    assert directory.exists()


def test_protection_pending_rechecks_and_returns_to_grace_period(tmp_path, fake_youtube):
    from app import workers

    video_id, directory = _make_expired_video(tmp_path, "retention-pending-retry")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.edit_detection_mode = "off"
        video.status = "PROTECTION_PENDING"
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"retention-pending-retry": {
        "availability": "public", "live_status": "was_live", "duration": 100}}})

    workers.protection_worker()

    row = _get_video(video_id)
    assert row.status == "DELETE_ELIGIBLE"
    assert row.deletion_after is not None
    assert directory.exists()


def test_protection_pending_final_retry_can_delete_after_successful_recheck(tmp_path, fake_youtube):
    from datetime import timedelta
    from app import workers
    from app.models import RemoteCheck, now

    video_id, directory = _make_expired_video(tmp_path, "retention-pending-final")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.edit_detection_mode = "off"
        video.status = "PROTECTION_PENDING"
        video.deletion_candidate_at = now() - timedelta(days=5)
        video.deletion_after = now() - timedelta(seconds=1)
        db.add(RemoteCheck(video_id=video.id, remote_status="PUBLIC", checked_at=now(),
                           error=None, edit_detected=False))
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {"retention-pending-final": {
        "availability": "public", "live_status": "was_live", "duration": 100}}})

    workers.protection_worker()

    assert _get_video(video_id).status == "DELETED_BY_RETENTION"
    assert not directory.exists()


def test_partial_recording_never_enters_retention_remote_check(tmp_path, fake_youtube):
    from app.workers import retention_worker
    video_id, directory = _make_expired_video(tmp_path, "retention-partial", partial=True)
    fake_youtube.set_state({"channels": {}, "videos": {"retention-partial": {
        "availability": "private", "duration": 100}}})
    retention_worker()
    assert _get_video(video_id).status == "ACTIVE"
    assert directory.exists()
    assert not fake_youtube.calls()


@pytest.mark.parametrize(("started_at", "command"), [
    (40.5, ["python", "yt-dlp"]),
    (40.0, ["python", "unrelated-worker"]),
])
def test_orphan_cleanup_never_kills_reused_or_unrelated_pid(tmp_path, monkeypatch, started_at, command):
    import psutil
    from app.services import recorder
    video_id = _create_video(tmp_path, "orphan-safety", chat=False)
    directory = tmp_path / "archive" / "orphan"
    directory.mkdir()
    (directory / ".recorder.json").write_text(json.dumps({"processes": [
        {"pid": 4567, "started_at": started_at, "role": "yt-dlp"}]}), encoding="utf-8")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING"
        video.file_path = str(directory)
        db.commit()
    class Process:
        killed = False
        def __init__(self, _pid): pass
        def create_time(self): return 40.0
        def cmdline(self): return command
        def children(self, recursive=True): return []
        def terminate(self): self.killed = True
        def kill(self): self.killed = True
    process = Process(4567)
    monkeypatch.setattr(psutil, "Process", lambda _pid: process)
    recorder.cleanup_orphan_processes()
    assert not process.killed


def test_orphan_cleanup_does_not_kill_process_owned_by_another_recorder(tmp_path, monkeypatch):
    import psutil
    from app.services import recorder
    from app.services.locks import default_locks, recorder_lock

    video_id = _create_video(tmp_path, "locked-orphan", chat=False)
    directory = tmp_path / "archive" / "locked-orphan"
    directory.mkdir()
    manifest = directory / ".recorder.json"
    manifest.write_text(json.dumps({"processes": [
        {"pid": 4568, "started_at": 40.0, "role": "yt-dlp"},
    ]}), encoding="utf-8")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "RECORDING"
        video.file_path = str(directory)
        db.commit()
        youtube_id = video.youtube_video_id

    class Process:
        killed = False
        def __init__(self, _pid): pass
        def create_time(self): return 40.0
        def cmdline(self): return ["python", "yt-dlp"]
        def children(self, recursive=True): return []
        def terminate(self): self.killed = True
        def kill(self): self.killed = True

    process = Process(4568)
    monkeypatch.setattr(psutil, "Process", lambda _pid: process)
    namespace, key = recorder_lock(youtube_id)
    with default_locks.acquire(namespace, key) as lease:
        assert lease.acquired
        recorder.cleanup_orphan_processes()
    assert not process.killed
    assert manifest.exists()


def test_recording_completion_commit_retries_after_connection_rollback(tmp_path, monkeypatch):
    from sqlalchemy.exc import PendingRollbackError
    from app.services import recorder

    video_id = _create_video(tmp_path, "commit-retry", chat=False)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.status = "ACTIVE"
        video.raw_metadata = {"attempts": [{"outcome": "success"}]}
        original_commit = db.commit
        calls = 0

        def disconnect_once():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise PendingRollbackError("connection was dropped before commit")
            original_commit()

        monkeypatch.setattr(db, "commit", disconnect_once)
        recorder._commit_completed_recording(db, video)
        assert calls == 2
    assert _get_video(video_id).status == "ACTIVE"
    assert _get_video(video_id).raw_metadata["attempts"][0]["outcome"] == "success"


def test_completed_commit_persists_in_place_json_metadata_updates(tmp_path):
    from app.services.recorder import _commit_completed_recording

    video_id = _create_video(tmp_path, "completed-json-metadata", chat=False)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.raw_metadata.update(archive_backfill="queued", live_status="was_live")
        video.status = "DISCOVERED"
        video.file_size = 123
        _commit_completed_recording(db, video)

    saved = _get_video(video_id)
    assert saved.raw_metadata["archive_backfill"] == "queued"
    assert saved.raw_metadata["live_status"] == "was_live"


def test_heartbeat_write_failure_does_not_stop_recording_process(monkeypatch):
    import subprocess
    from types import SimpleNamespace
    from app.services import recorder

    class Process:
        pid = 54321
        waits = 0
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            self.waits += 1
            if self.waits >= 45:
                self.returncode = 0
                return 0
            raise subprocess.TimeoutExpired("recorder", timeout)

    class Session:
        commits = 0
        rollbacks = 0

        def commit(self):
            self.commits += 1
            if self.commits == 1:
                raise OSError("temporary DB outage")

        def rollback(self):
            self.rollbacks += 1

    clock = [0]

    def monotonic():
        clock[0] += 1
        return float(clock[0])

    process = Process()
    session = Session()
    lease = SimpleNamespace(still_owned=lambda: True)
    video = SimpleNamespace(
        id=1,
        youtube_video_id="heartbeat-retry",
        recording_heartbeat_at=None,
        raw_metadata={},
    )
    monkeypatch.setattr(recorder.time, "monotonic", monotonic)
    monkeypatch.setattr(recorder, "_ingest_live_chat_delta", lambda *_args: None)
    monkeypatch.setattr(recorder, "_audit_low_disk_space", lambda *_args: None)
    assert recorder._wait_for_attempt(session, video, Path("."), "video", process, lease, {}) == b""
    assert process.waits == 45
    assert session.commits >= 2
    assert session.rollbacks == 1


def test_live_completion_keeps_idle_transaction_closed_during_slow_external_steps(
    tmp_path, fake_youtube, monkeypatch
):
    import os
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app import workers
    from app.services import recorder, recovery

    video_id = _create_video(tmp_path, "slow-complete", chat=True)
    engine = create_engine(
        os.environ["DATABASE_URL"],
        connect_args={"options": "-c idle_in_transaction_session_timeout=1000"},
    )
    short_sessions = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(workers, "SessionLocal", short_sessions)
    monkeypatch.setattr(recorder, "SessionLocal", short_sessions)
    fake_youtube.set_state({"channels": {}, "videos": {"slow-complete": {
        "live_status": "is_live",
        "release_timestamp": int(time.time()) - 5,
        "recording": {"mode": "live", "ended": False},
    }}})

    real_inspect = recorder.inspect

    def slow_inspect(*args, **kwargs):
        time.sleep(2.1)
        return real_inspect(*args, **kwargs)

    monkeypatch.setattr(recorder, "inspect", slow_inspect)
    real_finish_chat = recorder._finish_chat_process

    def slow_finish_chat(target_id):
        real_finish_chat(target_id)
        time.sleep(2.1)

    monkeypatch.setattr(recorder, "_finish_chat_process", slow_finish_chat)
    real_probe = recovery.probe_duration
    probe_slowed = False

    def slow_probe(*args, **kwargs):
        nonlocal probe_slowed
        if not probe_slowed:
            probe_slowed = True
            time.sleep(2.1)
        return real_probe(*args, **kwargs)

    monkeypatch.setattr(recovery, "probe_duration", slow_probe)
    workers.recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file()
                     and (target / "video.f134.mkv.part").stat().st_size > 0)
    fake_youtube.set_state({"channels": {}, "videos": {"slow-complete": {
        "live_status": "was_live",
        "release_timestamp": int(time.time()) - 6,
        "recording": {"mode": "live", "ended": True},
    }}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30)
    assert _get_video(video_id).recording_finished_at is not None
    assert _wait_for(lambda: recorder.running_counts() == (0, 0), timeout=30)
    engine.dispose()


@pytest.mark.parametrize("part_only", [False, True])
def test_live_end_data_blocks_error_accepts_only_complete_media(
        tmp_path, fake_youtube, monkeypatch, part_only):
    from app.services import recorder
    from app.services.recovery import probe_duration

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = f"live-end-error-{part_only}"
    video_id = _create_video(tmp_path, key, chat=False, from_start=not part_only)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 1,
        "recording": {"mode": "live", "ended": False,
                      "terminal_error": "Did not get any data blocks",
                      "part_only_on_end": part_only},
    }}})

    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    pending = target / ("video.mkv.part" if part_only else "video.f134.mkv.part")
    assert _wait_for(lambda: pending.is_file())
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 3,
        "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": True,
                      "terminal_error": "Did not get any data blocks",
                      "part_only_on_end": part_only},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status in {"ACTIVE", "DISCOVERED"}, timeout=30), (
        _get_video(video_id).status,
        _get_video(video_id).raw_metadata,
    )
    row = _get_video(video_id)
    final_media = target / ("video.partial.mkv" if part_only else "video.mkv")
    assert final_media.is_file() and final_media.stat().st_size > 0
    assert probe_duration(final_media) == pytest.approx(3, abs=30)
    assert row.raw_metadata["attempts"][-1]["outcome"] == "success"
    assert row.raw_metadata["attempts"][-1]["exit_code"] == 1
    assert row.raw_metadata["live_end_exit_evidence"]["error"] == "Did not get any data blocks"
    assert not (target / f"{key}-recording-error.log").exists()
    assert row.file_size > 0
    with SessionLocal() as db:
        assert db.scalar(select(AuditLog.id).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_END_ERROR_ACCEPTED"))
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


def test_live_end_error_with_short_media_remains_failed_and_preserved(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "live-end-error-short"
    video_id = _create_video(tmp_path, key, chat=False, from_start=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 1,
        "recording": {"mode": "live", "ended": False,
                      "terminal_error": "Did not get any data blocks"},
    }}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.f134.mkv.part").is_file())
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 3600,
        "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": True,
                      "terminal_error": "Did not get any data blocks"},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING_FAILED", timeout=30)
    row = _get_video(video_id)
    assert (target / "video.mkv").is_file()
    error_log = target.parent / f"{key}-recording-error.log"
    assert "Did not get any data blocks" in error_log.read_text(encoding="utf-8")
    assert row.raw_metadata["attempts"][-1]["outcome"] == "failed"
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


def test_live_end_error_completion_still_queues_archive_backfill(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "live-end-error-backfill"
    video_id = _create_video(tmp_path, key, chat=False, from_start=False)
    release_timestamp = int(time.time()) - 600
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.raw_metadata = {**video.raw_metadata, "release_timestamp": release_timestamp}
        db.commit()
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": False,
                      "terminal_error": "Did not get any data blocks"},
    }}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.mkv.part").is_file())
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 603,
        "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": True,
                      "terminal_error": "Did not get any data blocks"},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status != "RECORDING", timeout=30), (
        _get_video(video_id).status, _get_video(video_id).raw_metadata)
    row = _get_video(video_id)
    assert row.raw_metadata["attempts"][-1]["outcome"] == "success"
    assert row.raw_metadata["archive_backfill"] == "queued"
    assert row.raw_metadata["live_status"] == "was_live"
    assert row.raw_metadata["attempts"][-1]["completion_reason"] == "complete_after_live_end_error"
    assert row.is_partial
    assert not (target.parent / f"{key}-recording-error.log").exists()
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


def test_live_end_inspection_retries_stale_status_with_terminal_evidence(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "stale-live-inspect"
    video_id = _create_video(tmp_path, key, chat=False, from_start=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 1,
        "recording": {"mode": "live", "ended": False,
                      "terminal_error": "Did not get any data blocks"},
    }}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.mkv.part").is_file())
    responses = iter(({"live_status": "is_live", "duration": 3},
                      {"live_status": "was_live", "duration": 3}))
    monkeypatch.setattr(recorder, "inspect", lambda *_args: next(responses))
    monkeypatch.setattr("app.services.recovery.probe_duration", lambda _path: 3)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 3,
        "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": True,
                      "terminal_error": "Did not get any data blocks"},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30)
    row = _get_video(video_id)
    assert row.raw_metadata["attempts"][-1]["outcome"] == "success"
    assert row.raw_metadata["live_status"] == "was_live"
    with SessionLocal() as db:
        interrupted = db.scalars(select(AuditLog).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_SEGMENT_INTERRUPTED")).all()
        assert not interrupted
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


def test_live_full_duration_is_accepted_while_inspection_stays_stale_is_live(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "stale-live-complete-duration"
    release_timestamp = int(time.time()) - 1
    video_id = _create_video(tmp_path, key, chat=False, from_start=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": False},
    }}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    assert _wait_for(lambda: (target / "video.mkv.part").is_file())
    responses = iter((
        {"live_status": "is_live", "duration": 3, "release_timestamp": release_timestamp},
        {"live_status": "is_live", "duration": 3, "release_timestamp": release_timestamp},
        {"live_status": "is_live", "duration": 3, "release_timestamp": release_timestamp},
    ))
    monkeypatch.setattr(recorder, "inspect", lambda *_args: next(responses))
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "duration": 3, "release_timestamp": release_timestamp,
        "recording": {"mode": "live", "ended": True},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30)
    row = _get_video(video_id)
    assert row.raw_metadata["attempts"][-1]["outcome"] == "success"
    assert row.raw_metadata["live_status"] == "was_live"
    with SessionLocal() as db:
        assert not db.scalars(select(AuditLog).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_SEGMENT_INTERRUPTED")).all()
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for tee integration")
def test_tee_creates_archive_and_hls_segments(tmp_path, fake_youtube, caplog):
    import logging

    logger = logging.getLogger("app.services.recorder")
    caplog.set_level(logging.INFO, logger="app.services.recorder")
    logger.addHandler(caplog.handler)
    video_id = _create_video(tmp_path, "tee-live", chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {"tee-live": {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    row = _get_video(video_id)
    hls_dir = tmp_path / "archive" / "live" / "tee-live"
    playlist_ready = _wait_for(lambda: (hls_dir / "index.m3u8").is_file() and any(hls_dir.glob("segment_*.ts")), timeout=18)
    if not playlist_ready:
        with SessionLocal() as db:
            fallbacks = db.scalars(select(AuditLog).where(
                AuditLog.video_id == video_id, AuditLog.action == "LIVE_PLAYBACK_FALLBACK")).all()
            failure_details = [{"reason": item.reason, "return_code": (item.details or {}).get("return_code"),
                                "ffmpeg": ((item.details or {}).get("ffmpeg") or "").replace(str(tmp_path), "<tmp>")}
                               for item in fallbacks]
        raise AssertionError(f"tee HLS output did not appear; fallback diagnostics={failure_details!r}")
    index = (hls_dir / "index.m3u8").read_text(encoding="utf-8")
    assert "segment_" in index
    fake_youtube.set_state({"channels": {}, "videos": {"tee-live": {
        "live_status": "is_live", "recording": {"mode": "live", "ended": True}}}})
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=20)
    media = Path(_get_video(video_id).file_path) / "video.mkv"
    assert media.is_file() and media.stat().st_size > 0
    folder = media.parent
    assert list(folder.glob("*.webp"))
    assert list(folder.glob("*.description"))
    assert not Path("-.webp").exists()
    logger.removeHandler(caplog.handler)
    messages = {
        id(record): record.getMessage()
        for record in caplog.records
    }.values()
    assert sum("recording started" in message for message in messages) == 1
    assert sum("recording completed" in message for message in messages) == 1
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for tee integration")
@pytest.mark.parametrize("terminal_error", [
    "Did not get any data blocks",
    "Video is no longer live. Giving up after 3 retries",
])
def test_tee_live_end_error_keeps_verified_remux_without_fallback(
        tmp_path, fake_youtube, monkeypatch, terminal_error):
    from app.services import recorder

    monkeypatch.setattr(recorder, "LIVE_END_INSPECT_DELAYS_SECONDS", (0, 0))
    key = "tee-live-end-error"
    video_id = _create_video(tmp_path, key, chat=False, playback=True, from_start=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 1,
        "recording": {"mode": "live", "ended": False,
                      "terminal_error": terminal_error},
    }}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "was_live", "duration": 3,
        "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": True,
                      "terminal_error": terminal_error},
    }}})

    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30), (
        _get_video(video_id).status,
        _get_video(video_id).raw_metadata,
    )
    assert (target / "video.mkv").is_file()
    assert not (target / f"{key}-recording-error.log").exists()
    with SessionLocal() as db:
        assert not db.scalars(select(AuditLog).where(
            AuditLog.video_id == video_id, AuditLog.action == "LIVE_PLAYBACK_FALLBACK")).all()
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


def test_tee_fallback_format_retry_uses_fallback_stem_consistently(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder
    from app.services import quality

    fake_ffmpeg = Path(__file__).parent / "fakes" / "fake_ffmpeg.py"
    monkeypatch.setattr(settings, "ffmpeg_path", str(fake_ffmpeg.resolve()))
    monkeypatch.setattr(quality, "resolve_quality", lambda *_: ("requested-format", "test"))
    monkeypatch.setattr(recorder, "_select_live_from_start", lambda *_args: "skipped_disabled")
    monkeypatch.setenv("FAKE_FFMPEG_TEE_FAIL", "1")
    key = "tee-fallback-format"
    video_id = _create_video(tmp_path, key, chat=False, playback=True, from_start=False)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 10,
        "recording": {"mode": "live", "ended": True, "format_error": True},
    }}})

    recorder_worker()

    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30), (
        _get_video(video_id).status, _get_video(video_id).raw_metadata)
    row = _get_video(video_id)
    target = Path(row.file_path)
    fallback_stem = row.raw_metadata["attempts"][-1]["stem"]
    assert fallback_stem != "video"
    assert (target / f"{fallback_stem}.mkv").is_file()
    assert row.raw_metadata["attempts"][-1]["outcome"] == "success"
    fallback_calls = [call["argv"] for call in fake_youtube.calls()
                      if "--skip-download" not in call["argv"] and fallback_stem in " ".join(call["argv"])]
    assert len(fallback_calls) == 2
    assert "-f" in fallback_calls[0]
    assert "-f" not in fallback_calls[1]
    assert _wait_for(lambda: recorder.running_counts() == (0, 0))


@pytest.mark.parametrize("stop_kind", ["user", "shutdown"])
@pytest.mark.parametrize("remux_succeeds", [True, False])
def test_tee_stop_finalizes_or_preserves_transport_stream_without_fallback(
        tmp_path_factory, fake_youtube, monkeypatch, stop_kind, remux_succeeds):
    from app.services.recorder import running_counts, shutdown, stop_recording

    fake_ffmpeg = Path(__file__).parent / "fakes" / "fake_ffmpeg.py"
    monkeypatch.setattr(settings, "ffmpeg_path", str(fake_ffmpeg.resolve()))
    monkeypatch.setenv("FAKE_FFMPEG_REMUX_FAIL", "0" if remux_succeeds else "1")

    key = "tee-user-stop"
    recording_root = tmp_path_factory.mktemp("tee-stop")
    video_id = _create_video(recording_root, key, chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    hls_dir = recording_root / "archive" / "live" / key
    assert _wait_for(lambda: (hls_dir / "index.m3u8").is_file()
                     and any(hls_dir.glob("segment_*.ts")), timeout=18)
    if stop_kind == "user":
        assert stop_recording(video_id)
        expected_status = "ACTIVE"
    else:
        shutdown(lambda pk: stop_recording(pk, user_requested=False), timeout=10)
        expected_status = "RECORDING_FAILED"
    assert _wait_for(lambda: _get_video(video_id).status == expected_status, timeout=30)
    row = _get_video(video_id)
    media = target / ("video.mkv" if remux_succeeds else "video.tee.ts")
    assert media.is_file() and media.stat().st_size > 0
    assert (target / "video.tee.ts").exists() is (not remux_succeeds)
    if stop_kind == "user":
        assert row.is_partial
        assert row.retention_expires_at is None
    else:
        assert row.status == "RECORDING_FAILED"
    if not remux_succeeds:
        from app.services.backfill import media_file
        from app.services.media import source_media
        assert source_media(target) == media.resolve()
        assert media_file(target) == media.resolve()
        assert row.file_size == media.stat().st_size
    assert _wait_for(lambda: running_counts() == (0, 0))
    recording_calls = [call["argv"] for call in fake_youtube.calls()
                       if "--print-to-file" in call["argv"]]
    assert len(recording_calls) == 1
    assert recording_calls[0][recording_calls[0].index("-o") + 1] == "-"


def test_tee_ffmpeg_launch_failure_during_stop_does_not_reference_uninitialized_process(
        tmp_path, fake_youtube, monkeypatch):
    from app.services import recorder

    key = "tee-ffmpeg-launch-failure-stop"
    video_id = _create_video(tmp_path, key, chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})

    def fail_ffmpeg_while_stopping(*args, **kwargs):
        recorder.mark_stopping(video_id)
        raise RuntimeError("fake ffmpeg launch failure")

    monkeypatch.setattr(recorder, "launch_recording_ffmpeg", fail_ffmpeg_while_stopping)
    monkeypatch.setattr(recorder, "write_process_manifest", lambda *_args, **_kwargs: None)
    recorder_worker()
    from app.services.recorder import running_counts
    assert _wait_for(lambda: running_counts() == (0, 0))
    row = _get_video(video_id)
    assert row.raw_metadata["user_stopped"]
    assert row.raw_metadata["attempts"][-1]["outcome"] == "user_stop_empty"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required for tee integration")
def test_tee_heartbeat_database_failure_does_not_restart_or_lose_recording(
        tmp_path, fake_youtube, monkeypatch):
    from sqlalchemy.exc import PendingRollbackError
    from sqlalchemy.orm import Session as OrmSession
    from app.services import recorder

    key = "tee-heartbeat-db-failure"
    video_id = _create_video(tmp_path, key, chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})
    processes = []
    original_popen = recorder.popen_recording
    original_commit = OrmSession.commit
    original_rollback = OrmSession.rollback
    broken_sessions = set()
    injected = [False]

    def capture_recording_process(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    def fail_tee_heartbeat_commit(session):
        if (not injected[0] and video_id in recorder.active_upstreams
                and video_id in recorder.active_recorders):
            injected[0] = True
            broken_sessions.add(id(session))
            raise PendingRollbackError("simulated lost database connection")
        if id(session) in broken_sessions:
            raise PendingRollbackError("transaction requires rollback")
        return original_commit(session)

    def recover_session(session):
        result = original_rollback(session)
        broken_sessions.discard(id(session))
        return result

    monkeypatch.setattr(recorder, "popen_recording", capture_recording_process)
    monkeypatch.setattr(OrmSession, "commit", fail_tee_heartbeat_commit)
    monkeypatch.setattr(OrmSession, "rollback", recover_session)
    recorder_worker()

    try:
        assert _wait_for(lambda: _get_video(video_id).status == "RECORDING", timeout=15)
        assert _wait_for(lambda: injected[0], timeout=15)
        fake_youtube.set_state({"channels": {}, "videos": {key: {
            "live_status": "was_live", "duration": 3,
            "recording": {"mode": "live", "ended": True}}}})
        assert _wait_for(lambda: _get_video(video_id).status in ("ACTIVE", "RECORDING_FAILED"), timeout=30)
        row = _get_video(video_id)
        assert row.status == "ACTIVE"
        media = Path(row.file_path) / "video.mkv"
        assert media.is_file() and media.stat().st_size > 0
        assert _wait_for(lambda: recorder.running_counts() == (0, 0))
        assert injected[0]
        assert len(processes) == 1
        assert all(process.poll() is not None for process in processes)
    finally:
        for process in processes:
            recorder.stop_process(process)


def test_tee_fallback_database_failure_stops_fallback_process(
        tmp_path, fake_youtube, monkeypatch):
    import os
    import subprocess
    import sys
    from sqlalchemy.exc import PendingRollbackError
    from sqlalchemy.orm import Session as OrmSession
    from app.services import recorder

    key = "tee-fallback-db-failure"
    video_id = _create_video(tmp_path, key, chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "release_timestamp": int(time.time()) - 2,
        "recording": {"mode": "live", "ended": False}}}})
    original_popen = recorder.popen_recording
    original_commit = OrmSession.commit
    original_rollback = OrmSession.rollback
    tee_processes = []
    fallback_process = []
    broken_sessions = set()
    injected = [False]

    def fail_ffmpeg_launch(*_args, **_kwargs):
        raise RuntimeError("simulated FFmpeg launch failure")

    def capture_process(args, **kwargs):
        if not tee_processes:
            process = original_popen(args, **kwargs)
            tee_processes.append(process)
            return process
        process_options = ({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
                           if os.name == "nt" else {"start_new_session": True})
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, **process_options)
        recorder.capture_stderr(process)
        fallback_process.append(process)
        return process

    def fail_fallback_commit(session):
        if fallback_process and not injected[0]:
            injected[0] = True
            broken_sessions.add(id(session))
            raise PendingRollbackError("simulated lost database connection")
        if id(session) in broken_sessions:
            raise PendingRollbackError("transaction requires rollback")
        return original_commit(session)

    def recover_session(session):
        result = original_rollback(session)
        broken_sessions.discard(id(session))
        return result

    monkeypatch.setattr(recorder, "launch_recording_ffmpeg", fail_ffmpeg_launch)
    monkeypatch.setattr(recorder, "popen_recording", capture_process)
    monkeypatch.setattr(OrmSession, "commit", fail_fallback_commit)
    monkeypatch.setattr(OrmSession, "rollback", recover_session)
    recorder_worker()

    try:
        assert _wait_for(lambda: _get_video(video_id).status == "RECORDING_FAILED", timeout=20)
        assert fallback_process
        assert fallback_process[0].poll() is not None
        assert _wait_for(lambda: recorder.running_counts() == (0, 0))
    finally:
        for process in (*tee_processes, *fallback_process):
            recorder.stop_process(process)


def test_tee_downloader_failure_remuxes_and_falls_back_to_new_stem(
        tmp_path_factory, fake_youtube, monkeypatch):
    from app.services.recorder import active_upstreams, stop_recording

    fake_ffmpeg = Path(__file__).parent / "fakes" / "fake_ffmpeg.py"
    monkeypatch.setattr(settings, "ffmpeg_path", str(fake_ffmpeg.resolve()))
    recording_root = tmp_path_factory.mktemp("tee-failure")
    key = "tee-abnormal"
    video_id = _create_video(recording_root, key, chat=False, playback=True)
    fake_youtube.set_state({"channels": {}, "videos": {key: {
        "live_status": "is_live", "recording": {"mode": "live", "ended": False}}}})
    recorder_worker()
    assert _wait_for(lambda: _get_video(video_id).status == "RECORDING")
    target = Path(_get_video(video_id).file_path)
    hls_dir = recording_root / "archive" / "live" / key
    assert _wait_for(lambda: any(hls_dir.glob("segment_*.ts")), timeout=18)
    upstream = active_upstreams[video_id]
    upstream.terminate()
    assert _wait_for(lambda: len([call for call in fake_youtube.calls()
                                  if "--print-to-file" in call["argv"]]) >= 2, timeout=20)
    assert (target / "video.mkv").is_file()
    first_size = (target / "video.mkv").stat().st_size
    fallback = [call["argv"] for call in fake_youtube.calls()
                if "--print-to-file" in call["argv"]][-1]
    assert fallback[fallback.index("-o") + 1].endswith("video-part-2.%(ext)s")
    assert stop_recording(video_id)
    assert _wait_for(lambda: _get_video(video_id).status == "ACTIVE", timeout=30)
    assert (target / "video.mkv").stat().st_size == first_size
    assert any(path.name.startswith("video-part-2.") and path.stat().st_size > 0
               for path in target.iterdir())
    assert _wait_for(lambda: __import__("app.services.recorder", fromlist=["running_counts"])
                     .running_counts() == (0, 0))
