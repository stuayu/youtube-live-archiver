import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppSetting, ProcessingJob, TranscriptSegment, Video, now
from app.schemas import SummarySettingsIn
from app.services import processing, summarization


def _prompt_text(messages):
    return "\n".join(message["content"] for message in messages)


@pytest.mark.parametrize("build", [summarization.chunk_messages, summarization.final_messages,
                                   summarization.intermediate_messages])
def test_explicit_language_is_required_in_every_prompt(build):
    text = _prompt_text(build("x", language="ja"))
    assert "Japanese" in text and "same language as the" not in text


@pytest.mark.parametrize("build", [summarization.chunk_messages, summarization.final_messages,
                                   summarization.intermediate_messages])
def test_auto_language_keeps_the_source_language(build):
    assert "same language as the" in _prompt_text(build("x"))


def test_cache_key_separates_languages_but_keeps_existing_auto_keys():
    base = summarization.cache_key("same", "model")
    assert summarization.cache_key("same", "model", "auto") == base
    assert summarization.cache_key("same", "model", "ja") != base
    assert summarization.cache_key("same", "model", "ja") != summarization.cache_key("same", "model", "en")


def test_settings_accept_only_known_languages():
    assert SummarySettingsIn(summary_language="ja").summary_language == "ja"
    with pytest.raises(ValidationError):
        SummarySettingsIn(summary_language="xx")


def test_chunk_job_uses_saved_language_and_passes_it_to_the_final_job(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'lang.sqlite'}"); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); video = Video(youtube_video_id="lang", channel_id=1, status="ACTIVE", transcription_status="completed")
    db.add(video); db.flush()
    db.add(TranscriptSegment(video_id=video.id, start=0, end=2, text="hello"))
    db.add_all([AppSetting(key="summary_enabled", value={"value": True}),
                AppSetting(key="openrouter_model", value={"value": "model"}),
                AppSetting(key="openrouter_api_key", value={"value": "secret"}),
                AppSetting(key="summary_language", value={"value": "ja"})])
    job = ProcessingJob(video_id=video.id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0,
                        payload={"model": "model", "chunks": [], "next_chunk": 0}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid = job.id; db.close()
    monkeypatch.setattr(processing, "SessionLocal", factory)
    requests = []
    monkeypatch.setattr(summarization, "call_openrouter", lambda messages, **kwargs: requests.append(messages) or {
        "summary": "要約", "topics": [], "keywords": [], "important_moments": []})

    processing._process_summary(jid)

    check = factory()
    assert "Japanese" in _prompt_text(requests[0])
    assert check.get(ProcessingJob, jid).payload["language"] == "ja"
    final = check.query(ProcessingJob).filter_by(kind="SUMMARIZE_FINAL").one()
    assert final.payload["language"] == "ja"
    check.close()


def test_final_job_writes_in_the_language_from_its_payload(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'final-lang.sqlite'}"); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory(); video = Video(youtube_video_id="final-lang", channel_id=1, status="ACTIVE",
                                  transcription_status="completed", summary_status="pending")
    db.add(video); db.flush()
    db.add_all([AppSetting(key="summary_enabled", value={"value": True}),
                AppSetting(key="openrouter_model", value={"value": "model"}),
                AppSetting(key="openrouter_api_key", value={"value": "secret"}),
                AppSetting(key="summary_language", value={"value": "en"})])
    chunks = [{"start": 0, "end": 10, "summary": "opening", "topics": [], "keywords": [], "important_moments": []}]
    job = ProcessingJob(video_id=video.id, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                        payload={"model": "model", "chunks": chunks, "language": "ja"}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid = job.id; db.close()
    monkeypatch.setattr(processing, "SessionLocal", factory)
    requests = []
    monkeypatch.setattr(summarization, "call_openrouter", lambda messages, **kwargs: requests.append(messages) or {
        "summary": "まとめ", "topics": [], "highlights": [], "keywords": [], "timeline": []})

    processing._process_summary(jid)

    assert "Japanese" in _prompt_text(requests[0]) and "English" not in _prompt_text(requests[0])
