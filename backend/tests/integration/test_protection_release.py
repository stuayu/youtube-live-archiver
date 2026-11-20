from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from app import workers
from app.db import SessionLocal
from app.models import AuditLog, Channel, ProcessingJob, RemoteCheck, Video, now


def add_channel():
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + uuid4().hex[:22], url="https://www.youtube.com/@release",
                          name="Release", enabled=True)
        db.add(channel)
        db.commit()
        return channel.id


def add_video(tmp_path, *, status="ACTIVE", reason=None, remote_duration=None):
    directory = tmp_path / uuid4().hex
    directory.mkdir()
    (directory / "evidence.bin").write_bytes(b"recorded evidence")
    with SessionLocal() as db:
        video = Video(youtube_video_id=uuid4().hex[:11], channel_id=add_channel(), title="Edited", status=status,
                      file_path=str(directory), protected=status == "PROTECTED", protection_reason=reason,
                      original_duration=7200, edit_detection_mode="basic", raw_metadata={},
                      recording_finished_at=now() - timedelta(days=40),
                      retention_expires_at=now() - timedelta(days=10))
        db.add(video)
        db.flush()
        if remote_duration is not None:
            db.add(RemoteCheck(video_id=video.id, remote_status="PUBLIC", remote_duration=remote_duration,
                               edit_detected=True, checked_at=now()))
        db.commit()
        return video.id


def inspect_as(monkeypatch, duration):
    monkeypatch.setattr(workers, "_inspection", lambda *_args, **_kwargs: (
        "PUBLIC", duration, None, {"availability": "public", "duration": duration}))


def verify(video_id):
    with SessionLocal() as db:
        workers.verify_video(db, db.get(Video, video_id))
        db.commit()
    with SessionLocal() as db:
        return db.get(Video, video_id)


@pytest.mark.integration
@pytest.mark.parametrize("reason", ["REMOTE_DURATION_CHANGED", "REMOTE_CONTENT_CHANGED", "REMOTE_PRIVATE",
                                    "REMOTE_DELETED", "REMOTE_UNAVAILABLE", "MANUAL"])
def test_any_protection_except_legal_hold_can_be_released(client, tmp_path, reason):
    video_id = add_video(tmp_path, status="PROTECTED", reason=reason, remote_duration=6900)
    response = client.delete(f"/api/videos/{video_id}/protect")
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        assert (video.protected, video.status, video.protection_reason) == (False, "ACTIVE", None)
        log = db.scalar(select(AuditLog).where(AuditLog.video_id == video_id, AuditLog.action == "UNPROTECT"))
        assert log.reason == reason


@pytest.mark.integration
def test_legal_hold_cannot_be_released(client, tmp_path):
    video_id = add_video(tmp_path, status="PROTECTED", reason="LEGAL_HOLD")
    assert client.delete(f"/api/videos/{video_id}/protect").status_code == 409


@pytest.mark.integration
def test_released_edit_is_accepted_and_not_protected_again_for_the_same_edit(client, tmp_path, monkeypatch):
    video_id = add_video(tmp_path, status="PROTECTED", reason="REMOTE_DURATION_CHANGED", remote_duration=6900)
    assert client.delete(f"/api/videos/{video_id}/protect").status_code == 200
    with SessionLocal() as db:
        assert db.get(Video, video_id).raw_metadata["edit_accepted"]["remote_duration"] == 6900
    inspect_as(monkeypatch, 6905)
    assert verify(video_id).status == "DELETE_ELIGIBLE"


@pytest.mark.integration
def test_a_further_edit_after_release_is_protected_again(client, tmp_path, monkeypatch):
    video_id = add_video(tmp_path, status="PROTECTED", reason="REMOTE_DURATION_CHANGED", remote_duration=6900)
    assert client.delete(f"/api/videos/{video_id}/protect").status_code == 200
    inspect_as(monkeypatch, 6000)
    video = verify(video_id)
    assert (video.status, video.protection_reason) == ("PROTECTED", "REMOTE_DURATION_CHANGED")


@pytest.mark.integration
def test_detected_edit_queues_location_analysis(client, tmp_path, monkeypatch):
    video_id = add_video(tmp_path)
    inspect_as(monkeypatch, 6000)
    assert verify(video_id).protection_reason == "REMOTE_DURATION_CHANGED"
    with SessionLocal() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id,
                                                    ProcessingJob.kind == "EDIT_ANALYSIS"))
        assert job.status == "pending"
    response = client.get(f"/api/videos/{video_id}/edit-analysis")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"


@pytest.mark.integration
def test_edit_analysis_can_be_requested_from_the_player(client, tmp_path):
    video_id = add_video(tmp_path)
    assert client.get(f"/api/videos/{video_id}/edit-analysis").json()["status"] == "none"
    response = client.post(f"/api/videos/{video_id}/edit-analysis")
    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert client.post("/api/videos/999999/edit-analysis").status_code == 404
