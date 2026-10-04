import json
import time

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import AuditLog, Channel, Storage, Video
from app.workers import manual_video_worker


@pytest.fixture
def client(monkeypatch, fake_youtube):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def state(fake_youtube, vid, live_status, **extra):
    channel_id = "UC" + "m" * 22
    fake_youtube.set_state({"channels": {"UC" + "m" * 22: {"channel": "Manual Channel", "channel_id": channel_id}},
                            "videos": {vid: {"id": vid, "channel_id": channel_id, "channel": "Manual Channel",
                                             "title": f"Title {vid}", "live_status": live_status,
                                             "release_timestamp": int(time.time()) + 7200, "duration": 125,
                                             "thumbnail": "https://img.test/thumb.jpg", **extra}}})
    return channel_id


@pytest.mark.integration
@pytest.mark.parametrize(("vid", "live_status", "expected"), [
    ("manual12345", "is_live", "DISCOVERED"), ("manual1234a", "is_upcoming", "WAITING_LIVE"),
    ("manual1234b", "was_live", "DISCOVERED"), ("manual1234c", "not_live", "DISCOVERED")])
def test_manual_add_creates_channel_video_and_audit(client, fake_youtube, vid, live_status, expected):
    channel_id = state(fake_youtube, vid, live_status)
    response = client.post("/api/videos/manual", json={"url": f"https://youtu.be/{vid}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["action"] == ("wait_live" if expected == "WAITING_LIVE" else "record_live" if live_status == "is_live" else "download_archive")
    assert body["created"] and body["channel_created"]
    assert body["video"]["status"] == expected
    assert body["video"]["manual"] is True
    if expected == "DISCOVERED":
        archive_rows = client.get("/api/videos?scope=archive").json()
        upcoming_rows = client.get("/api/videos?scope=upcoming").json()
        assert any(row["youtube_video_id"] == vid for row in archive_rows)
        assert not any(row["youtube_video_id"] == vid for row in upcoming_rows)
    assert any(call["argv"][-1] == f"https://www.youtube.com/watch?v={vid}" for call in fake_youtube.calls())
    with SessionLocal() as db:
        channel = db.scalar(select(Channel).where(Channel.youtube_channel_id == channel_id))
        row = db.scalar(select(Video).where(Video.youtube_video_id == vid))
        audit = db.scalar(select(AuditLog).where(AuditLog.video_id == row.id, AuditLog.action == "MANUAL_ADD"))
        assert channel.enabled is False
        assert row.raw_metadata["manual"] is True
        assert row.raw_metadata["schedule_enabled"] is True
        assert audit is not None

@pytest.mark.integration
def test_manual_preview_invalid_duplicate_and_storage_protection(client, fake_youtube):
    vid = "p12345678ab"
    state(fake_youtube, vid, "was_live")
    bad = client.post("/api/videos/manual/preview", json={"url": "https://example.com/nope"})
    assert bad.status_code == 422 and "url" in bad.json()["fields"]
    preview = client.post("/api/videos/manual/preview", json={"url": vid})
    assert preview.status_code == 200 and preview.json()["action"] == "download_archive"
    with SessionLocal() as db:
        storage = Storage(name="manual-store", root_path="/tmp/manual-store")
        db.add(storage)
        db.flush()
        storage_id = storage.id
        db.commit()
    added = client.post("/api/videos/manual", json={"url": vid, "storage_id": storage_id, "protected": True})
    assert added.status_code == 200 and added.json()["video"]["protected"]
    duplicate = client.post("/api/videos/manual", json={"url": vid})
    assert duplicate.status_code == 409 and duplicate.json()["video_id"] == vid
    missing = client.post("/api/videos/manual", json={"url": "abcdefghijk", "storage_id": 999999})
    assert missing.status_code == 422 and "storage_id" in missing.json()["fields"]


@pytest.mark.integration
def test_manual_preview_thumbnail_proxy_and_live_title_cleanup(client, fake_youtube, monkeypatch, tmp_path):
    from app.config import settings
    vid = "liveclean01"
    state(fake_youtube, vid, "is_live", title="Live title 2026-10-04 22:46")
    response = client.post("/api/videos/manual/preview", json={"url": vid})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["title"] == "Live title"
    assert preview["thumbnail"] == f"/api/videos/manual/thumbnail/{vid}"
    assert client.get("/api/videos/manual/thumbnail/invalid").status_code == 404
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "remote_image_fetch_enabled", False)
    assert client.get(f"/api/videos/manual/thumbnail/{vid}").status_code == 404


@pytest.mark.integration
@pytest.mark.parametrize(("kind", "message"), [("private", "非公開"), ("deleted", "削除済み"),
                                                   ("members_only", "メンバー限定"), ("rate_limited", "アクセス制限中")])
def test_manual_inspect_classifies_errors(client, fake_youtube, kind, message):
    vid = "err" + str((len(kind) * 1_111_111) % 100_000_000).zfill(8)
    state(fake_youtube, vid, "was_live", status=kind)
    response = client.post("/api/videos/manual/preview", json={"url": vid})
    assert response.status_code == 422 and message in response.json()["fields"]["url"]


@pytest.mark.integration
def test_manual_worker_advances_live_and_cancels_private(client, fake_youtube):
    channel_id = "UC" + "w" * 22
    ids = ("workerlive1", "workerpriv1")
    fake_youtube.set_state({"channels": {}, "videos": {
        ids[0]: {"id": ids[0], "channel_id": channel_id, "live_status": "is_live"},
        ids[1]: {"id": ids[1], "channel_id": channel_id, "status": "private"},
    }})
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id=channel_id, url="https://www.youtube.com/channel/" + channel_id,
                          name="Worker", enabled=False)
        db.add(channel)
        db.flush()
        for vid in ids:
            db.add(Video(youtube_video_id=vid, channel_id=channel.id, title=vid, status="WAITING_LIVE",
                         raw_metadata={"manual": True, "schedule_enabled": True, "live_status": "is_upcoming"}))
        db.commit()
    manual_video_worker()
    with SessionLocal() as db:
        rows = {row.youtube_video_id: row for row in db.scalars(select(Video)).all()}
        assert rows[ids[0]].status == "DISCOVERED"
        assert rows[ids[0]].raw_metadata["live_status"] == "is_live"
        assert rows[ids[1]].status == "CANCELLED"
        assert rows[ids[1]].raw_metadata["cancel_reason"] == "PRIVATE"
