from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import AuditLog, Channel, RecordingRule, Storage, Video, TranscriptSegment, now


@pytest.fixture
def client(monkeypatch, fake_youtube):
    from app.config import settings
    from app.main import app

    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def add_channel(*, enabled=True):
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + "a" * 22, url="https://www.youtube.com/@test",
                          name="Test", enabled=enabled)
        db.add(channel)
        db.flush()
        channel_id = channel.id
        db.commit()
        return channel_id


def add_video(channel_id, *, status="ACTIVE", file_path=None, protected=False, raw_metadata=None):
    with SessionLocal() as db:
        video = Video(youtube_video_id=uuid4().hex[:11],
                      channel_id=channel_id, title="Sample", status=status, file_path=file_path,
                      protected=protected, raw_metadata=raw_metadata or {})
        db.add(video)
        db.flush()
        video_id = video.id
        db.commit()
        return video_id


@pytest.mark.integration
def test_transcript_search_without_q_and_with_q(client):
    channel_id = add_channel()
    video_id = add_video(channel_id)
    with SessionLocal() as db:
        db.add(TranscriptSegment(video_id=video_id, start=0, end=1, text="sample transcript"))
        db.commit()
    assert client.get(f"/api/videos/{video_id}/transcript").status_code == 200
    response = client.get(f"/api/videos/{video_id}/transcript", params={"q": "sample"})
    assert response.status_code == 200
    assert response.json()["total"] == 1


@pytest.mark.integration
def test_transcript_sync_loads_the_page_containing_the_playback_time(client):
    channel_id = add_channel()
    video_id = add_video(channel_id)
    with SessionLocal() as db:
        db.add_all([
            TranscriptSegment(video_id=video_id, start=index * 4, end=index * 4 + 3,
                              text=f"発言{index}")
            for index in range(250)
        ])
        db.commit()

    response = client.get(
        f"/api/videos/{video_id}/transcript",
        params={"at_seconds": 864, "limit": 100},
    )

    assert response.status_code == 200
    assert response.json()["offset"] == 200
    assert response.json()["segments"][0]["text"] == "発言200"
    assert response.json()["segments"][16]["text"] == "発言216"


@pytest.mark.integration
def test_transcript_sync_returns_to_first_page_before_first_segment(client):
    channel_id = add_channel()
    video_id = add_video(channel_id)
    with SessionLocal() as db:
        db.add_all([
            TranscriptSegment(video_id=video_id, start=index * 4 + 20, end=index * 4 + 23,
                              text=f"発言{index}")
            for index in range(250)
        ])
        db.commit()

    response = client.get(
        f"/api/videos/{video_id}/transcript",
        params={"at_seconds": 0, "offset": 200, "limit": 100},
    )

    assert response.status_code == 200
    assert response.json()["offset"] == 0
    assert response.json()["segments"][0]["text"] == "発言0"


@pytest.mark.integration
def test_storage_validation_usage_and_reference_safe_delete(client, tmp_path):
    assert client.post("/api/storages", json={"name": "relative", "root_path": "archive"}).status_code == 422
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("not a directory", encoding="utf-8")
    storage = client.post("/api/storages", json={"name": "windows", "root_path": str(invalid_root)})
    assert storage.status_code == 200
    assert client.post("/api/storages", json={"name": "windows", "root_path": str(tmp_path)}).status_code == 409
    storage_id = storage.json()["id"]
    channel_id = add_channel()
    with SessionLocal() as db:
        db.get(Channel, channel_id).storage_id = int(storage_id)
        db.commit()
    assert client.delete(f"/api/storages/{storage_id}").status_code == 409
    usage = client.get("/api/storages/usage")
    assert usage.status_code == 200
    assert usage.json()[0]["reachable"] is False


@pytest.mark.integration
def test_unreferenced_storage_delete_does_not_touch_files(client, tmp_path):
    root = tmp_path / "storage"
    response = client.post("/api/storages", json={"name": "unused", "root_path": str(root)})
    assert response.status_code == 200
    sentinel = root / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert client.delete(f"/api/storages/{response.json()['id']}").status_code == 200
    assert sentinel.read_text(encoding="utf-8") == "keep"


@pytest.mark.integration
def test_channel_patch_is_local_and_accepts_null_retention(client, fake_youtube):
    channel_id = add_channel()
    response = client.patch(f"/api/channels/{channel_id}", json={"name": "Renamed", "url": "https://youtube.com/@other",
                                                                    "youtube_channel_id": "UC" + "b" * 22,
                                                                    "retention_days": ""})
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"
    assert response.json()["url"] == "https://www.youtube.com/@test"
    assert response.json()["youtube_channel_id"] == "UC" + "a" * 22
    assert response.json()["retention_days"] is None
    assert fake_youtube.calls() == []
    assert client.patch(f"/api/channels/{channel_id}", json={"poll_interval": "5"}).status_code == 422


@pytest.mark.integration
def test_archived_channel_reactivation_and_retention_recalculation(client, fake_youtube):
    channel_id = add_channel(enabled=False)
    with SessionLocal() as db:
        channel = db.get(Channel, channel_id)
        channel.archived = True
        channel.retention_days = 20
        rule = RecordingRule(name="retention override", pattern="x", channel_id=channel_id, retention_days=7)
        db.add(rule); db.flush()
        rule_id = rule.id
        common = dict(channel_id=channel_id, recording_finished_at=now(), retention_days=20)
        eligible = Video(youtube_video_id=uuid4().hex[:11], title="eligible", status="DELETE_ELIGIBLE",
                         deletion_candidate_at=now(), deletion_after=now(), **common)
        overridden = Video(youtube_video_id=uuid4().hex[:11], title="overridden", status="ACTIVE",
                           raw_metadata={"matched_rule_id": rule_id}, **common)
        partial = Video(youtube_video_id=uuid4().hex[:11], title="partial", status="ACTIVE", is_partial=True, **common)
        protected = Video(youtube_video_id=uuid4().hex[:11], title="protected", status="ACTIVE", protected=True, **common)
        db.add_all((eligible, overridden, partial, protected)); db.commit()
        eligible_id, overridden_id, partial_id, protected_id = eligible.id, overridden.id, partial.id, protected.id
    updated = client.patch(f"/api/channels/{channel_id}?apply_retention_to_existing=true", json={"retention_days": 10})
    assert updated.status_code == 200
    with SessionLocal() as db:
        eligible = db.get(Video, eligible_id)
        assert eligible.status == "ACTIVE" and eligible.deletion_after is None
        assert eligible.retention_days == 10
        assert db.get(Video, overridden_id).retention_days == 20
        assert db.get(Video, partial_id).retention_days == 20
        assert db.get(Video, protected_id).retention_days == 20
    fake_youtube.set_state({"channels": {}, "videos": {}})
    revived = client.post("/api/channels", json={"url": "UC" + "a" * 22, "name": "Fallback", "enabled": False})
    assert revived.status_code == 200
    assert revived.json()["id"] == str(channel_id)
    assert revived.json()["archived"] is False and revived.json()["enabled"] is True
    assert fake_youtube.calls()


@pytest.mark.integration
def test_video_page_and_count_keep_missing_files(client, tmp_path):
    channel_id = add_channel()
    first = add_video(channel_id, file_path=str(tmp_path / "missing"))
    second = add_video(channel_id, status="WAITING_LIVE")
    rows = client.get("/api/videos", params={"scope": "archive", "limit": 1}).json()
    assert len(rows) == 1
    assert rows[0]["id"] == str(first)
    assert rows[0]["has_file"] is False
    assert client.get("/api/videos/count", params={"scope": "archive"}).json()["count"] == 1
    assert client.get("/api/videos", params={"scope": "upcoming"}).json()[0]["id"] == str(second)
    assert client.get("/api/videos", params={"status": "NOT_A_STATUS"}).status_code == 422


@pytest.mark.integration
def test_protection_and_delete_guards(client, tmp_path):
    channel_id = add_channel()
    protected_id = add_video(channel_id, file_path=str(tmp_path / "protected"), protected=True)
    assert client.delete(f"/api/videos/{protected_id}").status_code == 409
    recording_id = add_video(channel_id, status="RECORDING", file_path=str(tmp_path / "recording"))
    assert client.delete(f"/api/videos/{recording_id}").status_code == 409
    active_id = add_video(channel_id, file_path=str(tmp_path / "already-missing"))
    assert client.delete(f"/api/videos/{active_id}").status_code == 200
    with SessionLocal() as db:
        assert db.get(Video, active_id).status == "DELETED_MANUALLY"


@pytest.mark.integration
def test_recording_try_and_manual_protection_state_limits(client):
    channel_id = add_channel()
    active_id = add_video(channel_id)
    assert client.post(f"/api/videos/{active_id}/recording/try").status_code == 409
    scheduled_id = add_video(channel_id, status="WAITING_LIVE")
    assert client.post(f"/api/videos/{scheduled_id}/recording/try").json()["status"] == "DISCOVERED"
    assert client.post(f"/api/videos/{scheduled_id}/protect").status_code == 409
    with SessionLocal() as db:
        video = db.get(Video, active_id)
        video.status = "DELETE_ELIGIBLE"
        db.commit()
    assert client.post(f"/api/videos/{active_id}/protect").json()["protected"] is True
    assert client.delete(f"/api/videos/{active_id}/protect").json()["protected"] is False


@pytest.mark.integration
def test_status_and_events_shapes(client):
    channel_id = add_channel(enabled=False)
    status = client.get("/api/status")
    assert status.status_code == 200
    payload = status.json()
    assert payload["database"]["available"] is True
    assert "running" in payload["scheduler"] and isinstance(payload["scheduler"]["jobs"], list)
    assert "yt_dlp_available" in payload["tools"]
    assert "enabled_channel_count" in payload["monitor"]
    assert "items" in payload["recordings"] and "live_slots_limit" in payload["recordings"]
    assert isinstance(payload["counts"], dict)
    assert "unreachable" in payload["storages"]
    assert "attention" in payload
    assert client.get("/api/events", params={"limit": 50}).json() == []
    assert client.get("/api/settings/recording").status_code == 200
    assert client.get("/api/channels").json()[0]["monitor_state"] == "paused"


@pytest.mark.integration
def test_settings_validation_returns_error_object(client):
    response = client.put("/api/settings/transcription", json={"transcription_max_concurrency": "many"})
    assert response.status_code == 422
    assert set(response.json()) == {"error", "fields"}
    assert response.json()["fields"]["transcription_max_concurrency"] == "整数で入力してください"


@pytest.mark.integration
def test_startup_does_not_wait_for_remote_inspection(monkeypatch):
    import time
    from app.config import settings
    from app.main import app, scheduler
    from app.services import youtube

    monkeypatch.setattr(settings, "scheduler_enabled", False)
    monkeypatch.setattr(youtube, "inspect", lambda *args, **kwargs: time.sleep(2))
    started = time.monotonic()
    with TestClient(app, raise_server_exceptions=False):
        assert time.monotonic() - started < 1.5
        assert scheduler.running is False


@pytest.mark.integration
def test_stop_api_without_running_process_and_format_id_validation(client):
    channel_id = add_channel()
    video_id = add_video(channel_id)
    assert client.post(f"/api/videos/{video_id}/recording/stop").status_code == 409
    assert client.get("/api/formats/invalid-id").status_code == 422
