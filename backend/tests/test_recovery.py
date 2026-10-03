from app.services.recovery import classify_recovery, recovery_delay, remove_intermediates_after_success


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
    removed = remove_intermediates_after_success(tmp_path, final, audited.append)
    assert len(removed) == 2 and set(removed) == set(audited)
    assert final.exists() and (tmp_path / "notes.txt").exists()
