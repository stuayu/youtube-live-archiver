from app.services.recovery import classify_recovery, recovery_delay, remove_intermediates_after_success, choose_recording_stem
from pathlib import Path


def test_live_and_archive_recovery_branches():
    assert classify_recovery("PUBLIC", None, {"live_status": "is_live"}).action == "RESUME_LIVE"
    assert classify_recovery("PUBLIC", None, {"live_status": "was_live", "duration": 12}).action == "RETRIEVE_ARCHIVE"


def test_unavailable_protects_partial_and_unknown_defers():
    partial_exists = True
    decision = classify_recovery("PRIVATE", None, {})
    assert decision.action == "PROTECT" and decision.reason == "REMOTE_PRIVATE"
    assert partial_exists  # branch has no file operation; evidence remains untouched
    assert classify_recovery("UNKNOWN", "network timeout", None).action == "DEFER"
    assert partial_exists


def test_recovery_backoff_is_bounded():
    assert [recovery_delay(i).total_seconds() for i in (1, 2, 3, 4, 5, 10)] == [60, 300, 900, 3600, 21600, 21600]


def test_cleanup_requires_verified_final_and_only_removes_intermediates(tmp_path):
    (tmp_path / "video.f299.mp4.part").write_bytes(b"partial")
    (tmp_path / "video.f299.mp4.ytdl").write_text("resume")
    (tmp_path / "notes.txt").write_text("keep")
    audited = []
    assert remove_intermediates_after_success(tmp_path, tmp_path / "video.mkv", audited.append) == []
    assert (tmp_path / "video.f299.mp4.part").exists()
    final = tmp_path / "video.mkv"
    final.write_bytes(b"complete")
    removed = remove_intermediates_after_success(tmp_path, final, audited.append, validated=True)
    assert len(removed) == 2 and set(removed) == set(audited)
    assert final.exists() and (tmp_path / "notes.txt").exists()


def test_cleanup_is_stem_scoped(tmp_path):
    final = tmp_path / "video-part-2.mkv"
    final.write_bytes(b"verified")
    previous = tmp_path / "video.mkv.part"
    previous.write_bytes(b"preserve")
    current = tmp_path / "video-part-2.mkv.part"
    current.write_bytes(b"same stem")
    removed = remove_intermediates_after_success(tmp_path, final, lambda _: None,
                                                 stem="video-part-2", validated=True)
    assert removed == [str(current)]
    assert previous.exists()


def test_stem_selection_handles_live_and_archive_retries(tmp_path):
    assert choose_recording_stem(tmp_path, "live") == "video"
    assert choose_recording_stem(tmp_path, "live", {"video"}) == "video-part-2"
    assert choose_recording_stem(tmp_path, "live", {"video", "video-part-2"}) == "video-part-3"
    assert choose_recording_stem(tmp_path, "archive") == "video"
    assert choose_recording_stem(tmp_path, "archive", {"video"}) == "video-archive"


def test_salvage_timeout_scales_with_size_and_keeps_part_on_timeout(tmp_path, monkeypatch):
    import subprocess
    import app.services.recovery as recovery

    part = tmp_path / "video.mkv.part"
    with part.open("wb") as stream:
        stream.truncate(6_000_000_000)
    monkeypatch.setattr(recovery, "probe_duration", lambda *_args: 120.0)
    seen = {}

    def timeout_run(args, **kwargs):
        seen["timeout"] = kwargs["timeout"]
        raise subprocess.TimeoutExpired(args, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout_run)
    result = recovery.salvage_live_part(tmp_path, "video", lambda _path: None)
    assert result is None
    assert seen["timeout"] == 1200
    assert part.is_file() and not (tmp_path / "video.partial.mkv").exists()


def test_salvage_combines_dash_parts_and_removes_sources_only_after_validation(tmp_path, monkeypatch):
    import subprocess
    from types import SimpleNamespace
    import app.services.recovery as recovery

    video = tmp_path / "video.f134.mkv.part"
    audio = tmp_path / "video.f140.mkv.part"
    video.write_bytes(b"video fragments")
    audio.write_bytes(b"audio fragments")
    monkeypatch.setattr(recovery, "probe_duration", lambda path: 120.0 if path in (video, audio) else 119.5)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        Path(args[-1]).write_bytes(b"verified remux")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(subprocess, "run", run)
    audited = []
    result = recovery.salvage_live_part(tmp_path, "video", audited.append)
    assert result == tmp_path / "video.partial.mkv"
    assert "0:v?" in calls[0] and "1:a?" in calls[0]
    assert not video.exists() and not audio.exists()
    assert set(audited) == {str(video), str(audio)}


def test_salvage_unverifiable_dash_parts_preserves_both(tmp_path, monkeypatch):
    import app.services.recovery as recovery

    video = tmp_path / "video.f134.mkv.part"
    audio = tmp_path / "video.f140.mkv.part"
    video.write_bytes(b"video fragments")
    audio.write_bytes(b"audio fragments")
    monkeypatch.setattr(recovery, "probe_duration", lambda _path: None)
    assert recovery.salvage_live_part(tmp_path, "video", lambda _path: None) is None
    assert video.is_file() and audio.is_file()
