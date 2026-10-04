from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from app.db import SessionLocal
from app.models import AuditLog, Channel, Storage, Video, now
from test_api_round3 import add_video


def add_channel(*, enabled=True):
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + uuid4().hex[:22], url="https://www.youtube.com/@review",
                          name="Review", enabled=enabled)
        db.add(channel)
        db.flush()
        channel_id = channel.id
        db.commit()
        return channel_id


@pytest.mark.integration
@pytest.mark.parametrize("root", ["D:\\archive", "D:/archive", "\\\\server\\share"])
def test_storage_accepts_absolute_windows_paths(client, root):
    response = client.post("/api/storages", json={"name": f"windows-{uuid4().hex}", "root_path": root})
    assert response.status_code == 200, response.text
    assert response.json()["root_path"] == root


@pytest.mark.integration
def test_storage_threshold_patch_disable_and_db_usage(client, tmp_path):
    invalid = client.post("/api/storages", json={"name": "bad-threshold", "root_path": str(tmp_path),
                                                   "minimum_free_bytes": 20, "warning_free_bytes": 10})
    assert invalid.status_code == 422
    assert invalid.json()["fields"]["warning_free_bytes"] == "minimum_free_bytes 以上にしてください"
    storage = client.post("/api/storages", json={"name": "tracked", "root_path": str(tmp_path)})
    assert storage.status_code == 200
    storage_id = int(storage.json()["id"])
    patched = client.patch(f"/api/storages/{storage_id}", json={"enabled": False})
    assert patched.status_code == 200 and patched.json()["enabled"] is False
    channel_id = add_channel()
    with SessionLocal() as db:
        rows = [Video(youtube_video_id=uuid4().hex[:11], channel_id=channel_id, title="usage", status="ACTIVE",
                      file_path=str(tmp_path / f"{index}"), storage_id=storage_id, file_size=index + 10)
                for index in range(3)]
        db.add_all(rows)
        db.commit()
    usage = client.get("/api/storages/usage")
    item = next(row for row in usage.json() if row["id"] == str(storage_id))
    assert item["app_bytes"] == 33
    assert item["recording_count"] == 3


@pytest.mark.integration
def test_channel_patch_validation_and_schedule_resets(client, fake_youtube):
    channel_id = add_channel()
    invalid = [
        {"poll_interval": "10"}, {"edit_detection_mode": "turbo"}, {"backfill_limit": 201},
        {"storage_id": 987654}, {"cookie_profile": "unknown-profile"},
    ]
    for body in invalid:
        response = client.patch(f"/api/channels/{channel_id}", json=body)
        assert response.status_code == 422, (body, response.text)
    with SessionLocal() as db:
        channel = db.get(Channel, channel_id)
        channel.next_check_at = now() - timedelta(days=1)
        db.commit()
    assert client.patch(f"/api/channels/{channel_id}", json={"enabled": False}).status_code == 200
    with SessionLocal() as db:
        db.get(Channel, channel_id).next_check_at = now() - timedelta(days=1)
        db.commit()
    assert client.patch(f"/api/channels/{channel_id}", json={"enabled": True}).status_code == 200
    assert client.patch(f"/api/channels/{channel_id}", json={"poll_interval": "45"}).status_code == 200
    cleared = client.patch(f"/api/channels/{channel_id}", json={"retention_days": None})
    assert cleared.status_code == 200 and cleared.json()["retention_days"] is None
    with SessionLocal() as db:
        assert db.get(Channel, channel_id).next_check_at is None
    assert client.post(f"/api/channels/{channel_id}/check-now").status_code == 200
    assert fake_youtube.calls() == []


@pytest.mark.integration
def test_channel_monitor_states_aggregates_and_archived_filter(client):
    channel_ids = [add_channel(enabled=enabled) for enabled in (False, True, True, True, True, True)]
    with SessionLocal() as db:
        paused, recording_channel, error_channel, pending_channel, ok_channel, archived_channel = [db.get(Channel, x) for x in channel_ids]
        error_channel.consecutive_failures = 2
        ok_channel.last_checked_at = now()
        archived_channel.archived = True
        db.add_all([
            Video(youtube_video_id=uuid4().hex[:11], channel_id=recording_channel.id, title="recording", status="RECORDING"),
            Video(youtube_video_id=uuid4().hex[:11], channel_id=recording_channel.id, title="finished", status="ACTIVE", recording_finished_at=now()),
            Video(youtube_video_id=uuid4().hex[:11], channel_id=pending_channel.id, title="upcoming", status="WAITING_LIVE"),
            Video(youtube_video_id=uuid4().hex[:11], channel_id=ok_channel.id, title="failed", status="RECORDING_FAILED"),
        ])
        db.commit()
    rows = {row["id"]: row for row in client.get("/api/channels").json()}
    assert len(rows) == 5
    assert rows[str(channel_ids[0])]["monitor_state"] == "paused"
    assert rows[str(channel_ids[1])]["monitor_state"] == "recording"
    assert rows[str(channel_ids[1])]["recording_count"] == 1
    assert rows[str(channel_ids[1])]["recording_now_count"] == 1
    assert rows[str(channel_ids[2])]["monitor_state"] == "error"
    assert rows[str(channel_ids[3])]["monitor_state"] == "pending"
    assert rows[str(channel_ids[4])]["monitor_state"] == "ok"
    assert rows[str(channel_ids[4])]["failed_count"] == 1
    archived = client.get("/api/channels", params={"include_archived": "true"}).json()
    assert len(archived) == 6


@pytest.mark.integration
@pytest.mark.parametrize("body", [
    {"poll_interval": "5"}, {"edit_detection_mode": "invalid"}, {"backfill_limit": 201},
    {"storage_id": 999999}, {"cookie_profile": "invalid"},
])
def test_channel_patch_invalid_values_have_japanese_fields(client, body):
    channel_id = add_channel()
    response = client.patch(f"/api/channels/{channel_id}", json=body)
    assert response.status_code == 422
    if "fields" in response.json():
        assert response.json()["fields"]
    else:
        assert response.json()["error"]
    assert "Value error," not in str(response.json())


@pytest.mark.integration
def test_channel_registration_duplicate_name_fallback_and_non_youtube_url(client, fake_youtube):
    channel_youtube_id = "UC" + "c" * 22
    resolved = {"channel_id": channel_youtube_id, "channel": "Metadata Name"}
    fake_youtube.set_state({"channels": {"@resolved": resolved, channel_youtube_id: resolved}, "videos": {}})
    first = client.post("/api/channels", json={"url": "https://youtube.com/@resolved", "name": "Input Name"})
    assert first.status_code == 200 and first.json()["name"] == "Metadata Name"
    duplicate = client.post("/api/channels", json={"url": "https://youtube.com/@resolved"})
    assert duplicate.status_code == 409
    fake_youtube.set_state({"channels": {}, "videos": {}})
    fallback = client.post("/api/channels", json={"url": "UC" + "d" * 22, "name": "Input Name"})
    assert fallback.status_code == 200 and fallback.json()["name"] == "Input Name"
    by_id = client.post("/api/channels", json={"url": "UC" + "e" * 22})
    assert by_id.status_code == 200 and by_id.json()["name"] == "UC" + "e" * 22
    bad = client.post("/api/channels", json={"url": "https://example.com/not-youtube"})
    assert bad.status_code == 422


@pytest.mark.integration
def test_video_scopes_sorting_pagination_and_page_file_checks(client, monkeypatch, tmp_path):
    channel_id = add_channel()
    archive_ids = [add_video(channel_id, file_path=str(tmp_path / f"archive-{n}")) for n in range(5)]
    upcoming_ids = [add_video(channel_id, status="WAITING_LIVE") for _ in range(2)]
    deleted_ids = [add_video(channel_id, status="DELETED_MANUALLY") for _ in range(2)]
    with SessionLocal() as db:
        for index, video_id in enumerate(archive_ids):
            db.get(Video, video_id).recorded_at = datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(days=index)
        for index, video_id in enumerate(upcoming_ids):
            db.get(Video, video_id).scheduled_start_at = datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(days=index)
        db.commit()
    assert {row["id"] for row in client.get("/api/videos", params={"scope": "archive"}).json()} == {str(x) for x in archive_ids}
    assert {row["id"] for row in client.get("/api/videos", params={"scope": "upcoming"}).json()} == {str(x) for x in upcoming_ids}
    assert {row["id"] for row in client.get("/api/videos", params={"scope": "deleted"}).json()} == {str(x) for x in deleted_ids}
    assert [row["id"] for row in client.get("/api/videos", params={"scope": "upcoming"}).json()] == [str(x) for x in upcoming_ids]
    assert [row["id"] for row in client.get("/api/videos", params={"scope": "archive"}).json()] == [str(x) for x in reversed(archive_ids)]
    assert len(client.get("/api/videos", params={"scope": "all"}).json()) == 9
    page1 = client.get("/api/videos", params={"scope": "archive", "offset": 0, "limit": 2}).json()
    page2 = client.get("/api/videos", params={"scope": "archive", "offset": 2, "limit": 2}).json()
    page3 = client.get("/api/videos", params={"scope": "archive", "offset": 4, "limit": 2}).json()
    page_ids = [row["id"] for page in (page1, page2, page3) for row in page]
    assert len(page_ids) == len(set(page_ids)) == 5
    assert client.get("/api/videos", params={"scope": "archive", "limit": 999}).status_code == 200
    assert len(client.get("/api/videos", params={"scope": "archive", "limit": 999}).json()) <= 100
    assert client.get("/api/videos", params={"direction": "sideways"}).status_code == 422
    original = Path.is_dir
    checked = []
    def count_checks(path):
        if str(path).startswith(str(tmp_path / "archive")):
            checked.append(path)
        return original(path)
    monkeypatch.setattr(Path, "is_dir", count_checks)
    response = client.get("/api/videos", params={"scope": "archive", "limit": 2})
    assert response.status_code == 200 and len(checked) <= 2


@pytest.mark.integration
def test_video_filters_count_parity(client, tmp_path):
    first_channel = add_channel()
    second_channel = add_channel()
    root = tmp_path / "storage"
    storage = client.post("/api/storages", json={"name": "filter-storage", "root_path": str(root)}).json()
    older = add_video(first_channel, file_path=str(root / "one"), protected=True)
    newer = add_video(second_channel, file_path=str(root / "two"))
    with SessionLocal() as db:
        first = db.get(Video, older)
        first.title = "Needle title"
        first.file_size = 42
        first.protection_reason = "PRIVATE"
        first.retention_days = None
        first.storage_id = int(storage["id"])
        first.recorded_at = datetime(2026, 10, 3, 12, tzinfo=timezone.utc)
        second = db.get(Video, newer)
        second.title = "Other"
        second.file_size = 8
        second.recorded_at = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
        db.commit()
    cases = [
        {"q": "Needle"}, {"status": "ACTIVE"}, {"protection_reason": "PRIVATE"},
        {"from": "2026-10-03", "to": "2026-10-03"}, {"min_size": 40}, {"max_size": 10},
        {"storage_id": storage["id"]}, {"permanent": "true"}, {"channel_id": first_channel},
    ]
    for filters in cases:
        listed = client.get("/api/videos", params={"scope": "all", **filters})
        counted = client.get("/api/videos/count", params={"scope": "all", **filters})
        assert listed.status_code == 200 and counted.status_code == 200, (filters, listed.text, counted.text)
        assert len(listed.json()) == counted.json()["count"], filters
    included = client.get("/api/videos", params={"scope": "all", "from": "2026-10-03", "to": "2026-10-03"}).json()
    assert str(older) in [row["id"] for row in included]
    assert client.get("/api/videos", params={"sort": "nonsense"}).status_code == 422


@pytest.mark.integration
def test_video_detail_fields_and_utc_datetime(client, tmp_path):
    channel_id = add_channel()
    video_id = add_video(channel_id, status="RECORDING_FAILED", file_path=str(tmp_path / "absent"),
                         raw_metadata={"archive_backfill": "queued", "private-note": "hidden"})
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        youtube_video_id = video.youtube_video_id
        video.is_partial = True
        video.original_duration = None
        video.raw_metadata = {"archive_backfill": "queued", "private-note": "hidden", "local_duration": 123.0,
                              "user_stopped": True, "schedule_enabled": False, "recovery_exhausted": True,
                              "intervals": [{"started_at": "2026-10-03T06:00:00Z"}, {"started_at": "2026-10-03T07:00:00Z"}]}
        video.recorded_at = datetime(2026, 10, 3, 15, tzinfo=timezone(timedelta(hours=9)))
        db.commit()
    error_path = Path(tmp_path) / f"{youtube_video_id}-recording-error.log"
    error_path.write_text("recorder failed at the end", encoding="utf-8")
    response = client.get(f"/api/videos/{video_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["is_partial"] and data["user_stopped"]
    assert data["duration_seconds"] == 123.0 and data["archive_backfill"] == "queued"
    assert data["segment_count"] == 2 and data["recovery_exhausted"] and not data["schedule_enabled"]
    assert data["last_error"] == "recorder failed at the end"
    assert "raw_metadata" not in data and data["recorded_at"].endswith("+00:00")
    assert data["recorded_at"].startswith("2026-10-03T06:00:00")


@pytest.mark.integration
def test_schedule_state_guards_and_recording_retry_cleanup(client):
    channel_id = add_channel()
    active = add_video(channel_id)
    assert client.post(f"/api/videos/{active}/schedule/disable").status_code == 409
    waiting = add_video(channel_id, status="WAITING_LIVE")
    assert client.post(f"/api/videos/{waiting}/schedule/disable").status_code == 200
    assert client.post(f"/api/videos/{waiting}/schedule/enable").json()["status"] == "WAITING_LIVE"
    with SessionLocal() as db:
        video = db.get(Video, waiting)
        video.status = "RECORDING_FAILED"
        video.raw_metadata = {"user_stopped": True, "recovery_exhausted": True}
        db.commit()
    retry = client.post(f"/api/videos/{waiting}/recording/retry")
    assert retry.status_code == 200
    with SessionLocal() as db:
        video = db.get(Video, waiting)
        assert "user_stopped" not in video.raw_metadata and "recovery_exhausted" not in video.raw_metadata


@pytest.mark.integration
def test_error_shape_404_and_unhandled_error(client, monkeypatch):
    missing = client.get("/api/videos/999999")
    assert missing.status_code == 404 and set(missing.json()) == {"error"}
    from app.api import videos as video_api
    monkeypatch.setattr(video_api, "_video_query", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("test failure")))
    failed = client.get("/api/videos", params={"scope": "all"})
    assert failed.status_code == 500 and failed.json() == {"error": "内部エラーが発生しました"}


@pytest.mark.integration
def test_settings_reject_unknown_keys_and_never_return_api_key(client):
    transcription = client.put("/api/settings/transcription", json={"unexpected": True})
    assert transcription.status_code == 422 and transcription.json()["fields"]
    summary = client.put("/api/settings/summary", json={"summary_chunk_minutes": 17})
    assert summary.status_code == 422
    saved = client.put("/api/settings/summary", json={"openrouter_api_key": "secret-for-test"})
    assert saved.status_code == 200
    assert "secret-for-test" not in saved.text
    assert client.get("/api/settings/recording").status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize("endpoint,body", [
    ("transcription", {"transcription_enabled": "not-a-bool"}),
    ("transcription", {"whisper_model": ""}),
    ("transcription", {"whisper_model": "x" * 201}),
    ("transcription", {"whisper_device": "tpu"}),
    ("transcription", {"whisper_compute_type": "x" * 101}),
    ("transcription", {"whisper_language": "x" * 21}),
    ("transcription", {"transcription_max_concurrency": "many"}),
    ("transcription", {"transcription_max_concurrency": 0}),
    ("transcription", {"transcription_max_concurrency": 9}),
    ("summary", {"summary_enabled": "not-a-bool"}),
    ("summary", {"summary_chunk_minutes": "many"}),
    ("summary", {"summary_chunk_minutes": 14}),
    ("summary", {"openrouter_max_concurrency": 0}),
    ("summary", {"openrouter_max_concurrency": 5}),
    ("summary", {"openrouter_max_attempts": 0}),
    ("summary", {"openrouter_max_attempts": 11}),
    ("summary", {"openrouter_model": "x" * 501}),
    ("summary", {"openrouter_base_url": "x" * 501}),
    ("summary", {"openrouter_api_key": "x" * 1001}),
])
def test_settings_validate_each_field_type_range_and_choice(client, endpoint, body):
    response = client.put(f"/api/settings/{endpoint}", json=body)
    assert response.status_code == 422
    assert response.json()["fields"]
    assert all(isinstance(value, str) and value for value in response.json()["fields"].values())


@pytest.mark.integration
def test_events_filter_page_and_attach_names(client):
    channel_id = add_channel()
    video_id = add_video(channel_id)
    with SessionLocal() as db:
        db.add_all([AuditLog(action=f"action-{index}", channel_id=channel_id, video_id=video_id,
                             reason="review", details={}, created_at=now()) for index in range(4)])
        db.commit()
    rows = client.get("/api/events", params={"limit": 2}).json()
    assert len(rows) == 2 and rows[0]["id"] > rows[1]["id"]
    assert rows[0]["video_title"] == "Sample" and rows[0]["channel_name"] == "Review"
    older = client.get("/api/events", params={"before_id": rows[-1]["id"]}).json()
    assert all(int(row["id"]) < int(rows[-1]["id"]) for row in older)
    filtered = client.get("/api/events", params=[("channel_id", channel_id), ("video_id", video_id),
                                                     ("action", "action-1"), ("action", "action-2")]).json()
    assert {row["action"] for row in filtered} == {"action-1", "action-2"}
    assert client.get("/api/events", params={"limit": 201}).status_code == 422


@pytest.mark.integration
def test_validation_message_and_all_schema_datetimes_are_utc(client):
    bad = client.patch(f"/api/channels/{add_channel()}", json={"backfill_limit": "many"})
    assert bad.status_code == 422
    body = bad.json()
    assert body["fields"]["backfill_limit"] == "整数で入力してください"
    channel_id = add_channel()
    with SessionLocal() as db:
        db.get(Channel, channel_id).last_checked_at = datetime(2026, 10, 3, 15, tzinfo=timezone(timedelta(hours=9)))
        db.commit()
    row = next(row for row in client.get("/api/channels").json() if row["id"] == str(channel_id))
    assert row["last_checked_at"].endswith("+00:00")
    assert row["last_checked_at"].startswith("2026-10-03T06:00:00")


@pytest.mark.integration
def test_status_attention_and_job_run_statistics(client, monkeypatch, tmp_path):
    import types
    from app import main

    channel_id = add_channel()
    with SessionLocal() as db:
        channel = db.get(Channel, channel_id)
        channel.consecutive_failures = 1
        failed = Video(youtube_video_id=uuid4().hex[:11], channel_id=channel_id, title="failed", status="RECORDING_FAILED")
        db.add(failed)
        db.commit()
    client.post("/api/storages", json={"name": "unreachable", "root_path": "D:\\missing"})
    monkeypatch.setattr(main, "_tool_cache", {"yt_dlp_available": False, "yt_dlp_version": None, "yt_dlp_error": "missing",
                                               "ffmpeg_available": False, "ffprobe_available": False})
    monkeypatch.setattr(main, "_job_stats", {})
    monkeypatch.setattr(main.scheduler, "get_jobs", lambda: [types.SimpleNamespace(id="tracked", next_run_time=None)])
    main._tracked_job("tracked", lambda: "ok")()
    with pytest.raises(RuntimeError):
        main._tracked_job("tracked", lambda: (_ for _ in ()).throw(RuntimeError("job failed")))()
    status = client.get("/api/status").json()
    job = status["scheduler"]["jobs"][0]
    assert job["run_count"] == 2 and job["error_count"] == 1
    assert job["last_finished_at"].endswith("+00:00") and job["last_error"] == "job failed"
    codes = {entry["code"] for entry in status["attention"]}
    assert {"storage_unreachable", "monitor_errors", "yt_dlp_missing", "recording_failures"} <= codes


@pytest.mark.integration
def test_status_attention_is_empty_without_problems(client, monkeypatch):
    from app import main
    monkeypatch.setattr(main, "_tool_cache", {"yt_dlp_available": True, "yt_dlp_version": "fake",
                                               "yt_dlp_error": None, "ffmpeg_available": True, "ffprobe_available": True})
    status = client.get("/api/status")
    assert status.status_code == 200 and status.json()["attention"] == [
        {"level": "warning", "code": "storage_missing", "message": "保存先が登録されていません"}
    ]


@pytest.mark.integration
def test_video_delete_conversion_storage_boundary_and_hls_sidecar(client, monkeypatch, tmp_path):
    from app.api import videos as video_api
    channel_id = add_channel()
    root = tmp_path / "registered"
    storage = client.post("/api/storages", json={"name": "delete-root", "root_path": str(root)}).json()
    video_dir = root / "recording"
    video_dir.mkdir()
    (video_dir / "video.ts").write_bytes(b"media")
    video_id = add_video(channel_id, file_path=str(video_dir))
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        video.storage_id = int(storage["id"])
        video.live_hls_path = str(root / "live" / video.youtube_video_id)
        hls = Path(video.live_hls_path)
        hls.mkdir(parents=True)
        (hls / "index.m3u8").write_text("playlist")
        db.commit()
    monkeypatch.setattr(video_api, "conversion_status", lambda *_: {"status": "running"})
    assert client.delete(f"/api/videos/{video_id}").status_code == 409
    monkeypatch.setattr(video_api, "conversion_status", lambda *_: {"status": "idle"})
    assert client.delete(f"/api/videos/{video_id}").status_code == 200
    assert not video_dir.exists() and not hls.exists()

    external = tmp_path / "outside" / "keep"
    external.mkdir(parents=True)
    (external / "important.txt").write_text("keep")
    outside_id = add_video(channel_id, file_path=str(external))
    assert client.delete(f"/api/videos/{outside_id}").status_code == 409
    assert (external / "important.txt").read_text() == "keep"


@pytest.mark.integration
def test_protection_state_rules_and_automatic_protection_cannot_be_removed(client):
    channel_id = add_channel()
    for status in ("RECORDING", "RECORDING_FAILED"):
        video_id = add_video(channel_id, status=status)
        response = client.post(f"/api/videos/{video_id}/protect")
        assert response.status_code == 409
        with SessionLocal() as db:
            assert db.get(Video, video_id).status == status
    automatic = add_video(channel_id, status="PROTECTED", protected=True)
    with SessionLocal() as db:
        video = db.get(Video, automatic)
        video.protection_reason = "PRIVATE"
        db.commit()
    assert client.delete(f"/api/videos/{automatic}/protect").status_code == 409
    manual = add_video(channel_id, status="DELETE_ELIGIBLE")
    protected = client.post(f"/api/videos/{manual}/protect")
    assert protected.status_code == 200
    with SessionLocal() as db:
        assert db.get(Video, manual).status == "PROTECTED"
    released = client.delete(f"/api/videos/{manual}/protect")
    assert released.status_code == 200
    with SessionLocal() as db:
        assert db.get(Video, manual).status == "ACTIVE"


@pytest.mark.integration
def test_missing_video_directory_delete_succeeds_and_marks_history(client, tmp_path):
    channel_id = add_channel()
    video_id = add_video(channel_id, file_path=str(tmp_path / "gone"))
    response = client.delete(f"/api/videos/{video_id}")
    assert response.status_code == 200
    detail = client.get(f"/api/videos/{video_id}")
    assert detail.status_code == 200 and detail.json()["status"] == "DELETED_MANUALLY"
