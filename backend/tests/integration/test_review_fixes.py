from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import Channel, ProcessingJob, Storage, Video, now


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _channel(db, suffix, **extra):
    channel = Channel(youtube_channel_id="UC" + suffix * 22, url=f"https://youtube.com/@{suffix}", name=suffix, **extra)
    db.add(channel)
    db.flush()
    return channel


@pytest.mark.integration
def test_storage_root_cannot_move_away_from_existing_recordings(client, tmp_path):
    with SessionLocal() as db:
        storage = Storage(name="main", root_path=str(tmp_path / "old"))
        db.add(storage)
        db.flush()
        channel = _channel(db, "r")
        db.add(Video(youtube_video_id="rootmove001", channel_id=channel.id, title="t", status="ACTIVE",
                     storage_id=storage.id, file_path=str(tmp_path / "old" / "x")))
        db.commit()
        storage_id = storage.id
    response = client.patch(f"/api/storages/{storage_id}", json={"root_path": str(tmp_path / "new")})
    assert response.status_code == 409 and "1 件" in response.json()["error"]
    assert client.patch(f"/api/storages/{storage_id}", json={"name": "renamed"}).status_code == 200


@pytest.mark.integration
def test_manual_delete_refuses_while_storage_drive_is_offline(client, tmp_path):
    with SessionLocal() as db:
        storage = Storage(name="usb", root_path=str(tmp_path / "unplugged"))
        db.add(storage)
        db.flush()
        channel = _channel(db, "d")
        video = Video(youtube_video_id="offline0001", channel_id=channel.id, title="t", status="ACTIVE",
                      storage_id=storage.id, file_path=str(tmp_path / "unplugged" / "ch" / "v"))
        db.add(video)
        db.commit()
        video_id = video.id
    response = client.delete(f"/api/videos/{video_id}")
    assert response.status_code == 409 and "接続できません" in response.json()["error"]
    with SessionLocal() as db:
        row = db.get(Video, video_id)
        assert row.status == "ACTIVE" and row.file_path is not None


@pytest.mark.integration
def test_upcoming_watch_skips_archived_and_paused_channels(client, fake_youtube):
    from app.workers import upcoming_watch_worker
    fake_youtube.set_state({"channels": {}, "videos": {
        vid: {"id": vid, "live_status": "is_live"} for vid in ("archived001", "paused00001")}})
    with SessionLocal() as db:
        archived = _channel(db, "a", archived=True)
        everything_paused = [{"start": "00:00", "end": "00:00", "monitor": False}]
        paused = _channel(db, "p", poll_windows=everything_paused)
        for vid, channel in (("archived001", archived), ("paused00001", paused)):
            db.add(Video(youtube_video_id=vid, channel_id=channel.id, title=vid, status="WAITING_LIVE",
                         scheduled_start_at=now() - timedelta(minutes=5), raw_metadata={"live_status": "is_upcoming"}))
        db.commit()
    upcoming_watch_worker()
    with SessionLocal() as db:
        assert {v.status for v in db.scalars(select(Video)).all()} == {"WAITING_LIVE"}


@pytest.mark.integration
def test_missing_transcription_job_is_queued_for_recent_recordings(client, monkeypatch):
    from app.services import processing
    monkeypatch.setattr(processing, "effective_settings",
                        lambda _db: {**processing.TRANSCRIPTION_DEFAULTS, "transcription_enabled": True})
    with SessionLocal() as db:
        channel = _channel(db, "t")
        recent = Video(youtube_video_id="recentnojob", channel_id=channel.id, title="t", status="ACTIVE",
                       file_path="/tmp/x", recording_finished_at=now(), transcription_status="pending")
        old = Video(youtube_video_id="oldnojob001", channel_id=channel.id, title="t", status="ACTIVE",
                    file_path="/tmp/y", recording_finished_at=now() - timedelta(days=30), transcription_status="pending")
        db.add_all([recent, old])
        db.commit()
        recent_id, old_id = recent.id, old.id
    processing._reconcile_missing_transcriptions()
    with SessionLocal() as db:
        kinds = {job.video_id for job in db.scalars(select(ProcessingJob).where(ProcessingJob.kind == "TRANSCRIBE"))}
        assert recent_id in kinds and old_id not in kinds


@pytest.mark.integration
def test_manual_chat_retry_resets_rate_limit_counter(client):
    from app.services.chat import enqueue_replay_retry
    with SessionLocal() as db:
        channel = _channel(db, "c")
        video = Video(youtube_video_id="chatreset01", channel_id=channel.id, title="t", status="ACTIVE")
        db.add(video)
        db.flush()
        db.add(ProcessingJob(video_id=video.id, kind="CHAT_REPLAY", status="failed", attempts=3,
                             payload={"rate_limited_attempts": 6}))
        db.commit()
        job = enqueue_replay_retry(db, video.id, immediate=True, restart=True)
        assert "rate_limited_attempts" not in job.payload and job.status == "retry_wait"
