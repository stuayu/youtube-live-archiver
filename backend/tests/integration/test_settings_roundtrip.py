import pytest


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    monkeypatch.setattr(settings, "scheduler_enabled", False)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.mark.integration
def test_summary_settings_save_what_the_gui_loaded_with_api_key(client, monkeypatch):
    """The settings screen PUTs the GET payload back, including read-only fields."""
    from app.services import summarization
    checked = []
    monkeypatch.setattr(summarization, "check_provider", lambda **target: checked.append(target) or {"ok": True})
    loaded = client.get("/api/settings/summary").json()
    assert "openrouter_api_key_set" in loaded
    response = client.put("/api/settings/summary", json={**loaded, "openrouter_api_key": "sk-or-test"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["openrouter_api_key_set"] is True
    assert body["openrouter_api_key"] == ""
    assert checked and checked[0]["api_key"] == "sk-or-test"
    # Saving again without re-typing the key keeps it.
    again = client.put("/api/settings/summary", json=body)
    assert again.status_code == 200, again.text
    assert again.json()["openrouter_api_key_set"] is True


@pytest.mark.integration
def test_transcription_settings_save_what_the_gui_loaded(client):
    loaded = client.get("/api/settings/transcription").json()
    response = client.put("/api/settings/transcription", json=loaded)
    assert response.status_code == 200, response.text


@pytest.mark.integration
def test_summary_key_is_not_saved_when_provider_rejects_it(client, monkeypatch):
    from app.services import summarization

    def reject(**_target):
        raise summarization.ProviderCheckError("API キーが無効です", field="openrouter_api_key", code="invalid_key")

    monkeypatch.setattr(summarization, "check_provider", reject)
    loaded = client.get("/api/settings/summary").json()
    response = client.put("/api/settings/summary", json={**loaded, "openrouter_api_key": "sk-or-bad"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_key"
    assert body["fields"] == {"openrouter_api_key": "API キーが無効です"}
    assert client.get("/api/settings/summary").json()["openrouter_api_key_set"] is False

    tested = client.post("/api/settings/summary/test", json={"openrouter_api_key": "sk-or-bad"})
    assert tested.status_code == 422 and tested.json()["code"] == "invalid_key"


@pytest.mark.integration
def test_saving_unrelated_summary_settings_does_not_call_provider(client, monkeypatch):
    from app.services import summarization
    monkeypatch.setattr(summarization, "check_provider",
                        lambda **_target: pytest.fail("provider must not be called"))
    response = client.put("/api/settings/summary", json={"summary_chunk_minutes": 20})
    assert response.status_code == 200, response.text


@pytest.mark.integration
def test_saved_key_is_never_sent_to_a_request_supplied_url(client, monkeypatch):
    from app.services import summarization
    monkeypatch.setattr(summarization, "check_provider",
                        lambda **_target: pytest.fail("saved key must not be sent to a new URL"))
    from app.models import AppSetting
    from app.db import SessionLocal
    with SessionLocal() as db:
        db.add(AppSetting(key="openrouter_api_key", value={"value": "sk-or-saved"}))
        db.commit()
    for method, path in (("post", "/api/settings/summary/test"), ("put", "/api/settings/summary")):
        response = getattr(client, method)(path, json={"openrouter_base_url": "https://attacker.example/v1"})
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "key_required_for_new_url"
        assert "sk-or-saved" not in response.text


@pytest.mark.integration
def test_env_key_takes_precedence_like_summary_jobs(client, monkeypatch):
    from app.config import settings
    from app.services import summarization
    checked = []
    monkeypatch.setattr(settings, "openrouter_api_key", "sk-or-env")
    monkeypatch.setattr(summarization, "check_provider", lambda **target: checked.append(target) or {"ok": True})
    rejected = client.put("/api/settings/summary", json={"openrouter_api_key": "sk-or-gui"})
    assert rejected.status_code == 422 and rejected.json()["code"] == "env_key_overrides"
    tested = client.post("/api/settings/summary/test", json={})
    assert tested.status_code == 200 and checked[-1]["api_key"] == "sk-or-env"


@pytest.mark.integration
def test_video_list_reports_transcription_and_summary_status(client):
    from app.db import SessionLocal
    from app.models import Channel, Video, now
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + "p" * 22, url="https://youtube.com/@pipeline", name="Pipeline")
        db.add(channel)
        db.flush()
        db.add(Video(youtube_video_id="pipeline001", channel_id=channel.id, title="t", status="ACTIVE",
                     recording_finished_at=now(), transcription_status="completed", summary_status="processing"))
        db.commit()
    rows = client.get("/api/videos", params={"scope": "all"}).json()
    row = next(item for item in rows if item["youtube_video_id"] == "pipeline001")
    assert row["transcription_status"] == "completed"
    assert row["summary_status"] == "processing"


@pytest.mark.integration
def test_interrupted_summary_resumes_and_list_shows_progress(client):
    from app.db import SessionLocal
    from app.models import Channel, ProcessingJob, Video, now
    from app.services.processing import _recover_interrupted_summaries
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + "q" * 22, url="https://youtube.com/@resume", name="Resume")
        db.add(channel)
        db.flush()
        video = Video(youtube_video_id="resume00001", channel_id=channel.id, title="t", status="ACTIVE",
                      recording_finished_at=now(), transcription_status="completed", summary_status="processing")
        db.add(video)
        db.flush()
        # A restart left this job in "processing" with no worker holding its lock.
        db.add(ProcessingJob(video_id=video.id, kind="SUMMARIZE_CHUNKS", status="processing", attempts=1,
                             payload={"model": "m", "chunks": [{}, {}, {}], "next_chunk": 3, "total_chunks": 19}))
        db.commit()
        video_pk = video.id
    rows = client.get("/api/videos", params={"scope": "all"}).json()
    row = next(item for item in rows if item["youtube_video_id"] == "resume00001")
    assert row["summary_progress"] == {"done": 3, "total": 19}

    _recover_interrupted_summaries()
    with SessionLocal() as db:
        job = db.scalar(select_job(video_pk))
        assert job.status == "retry_wait" and job.next_attempt_at is not None
        assert job.payload["next_chunk"] == 3  # resumes from the saved chunk
        assert db.get(Video, video_pk).summary_status == "pending"


def select_job(video_pk):
    from sqlalchemy import select
    from app.models import ProcessingJob
    return select(ProcessingJob).where(ProcessingJob.video_id == video_pk)
