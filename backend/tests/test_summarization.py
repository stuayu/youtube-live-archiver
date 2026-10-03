import json
import logging
from urllib.error import HTTPError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppSetting, ProcessingJob, TranscriptSegment, Video, now
from app.services import processing, summarization

def test_chunks_preserve_utterances_and_have_timestamps():
    rows = [{"start": 0, "end": 10, "text": "first."}, {"start": 1790, "end": 1805, "text": "boundary speech."}, {"start": 1810, "end": 1820, "text": "next."}]
    chunks = summarization.split_transcript(rows, 30)
    assert [[x["text"] for x in part] for part in chunks] == [["first.", "boundary speech."], ["next."]]
    assert "[00:29:50 - 00:30:05]\nboundary speech." in summarization.format_transcript(chunks[0])

def test_json_validation_and_repair():
    valid = {"summary":"s", "topics":[{"start":1,"end":2,"title":"t","summary":"x"}], "keywords":[], "important_moments":[]}
    assert summarization.validate_chunk(summarization.repair_json("prefix " + json.dumps(valid) + " trailing")) == valid
    try: summarization.validate_chunk({"summary":"bad"})
    except ValueError: pass
    else: assert False, "invalid schema accepted"

def test_429_retry_after_and_backoff(monkeypatch):
    calls, delays = [], []
    def opener(request, timeout):
        calls.append(request)
        if len(calls) == 1: raise HTTPError(request.full_url, 429, "rate", {"Retry-After":"7"}, None)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"choices":[{"message":{"content":"{}"}}]}).encode()
        return Response()
    monkeypatch.setattr(summarization, "urlopen", opener)
    result = summarization.call_openrouter([], api_key="secret", model="x", base_url="https://unit.test", attempts=2, sleeper=delays.append)
    assert result == {} and delays == [7] and len(calls) == 2

def test_final_429_keeps_retry_after_for_durable_job(monkeypatch):
    def fail(request, timeout): raise HTTPError(request.full_url, 429, "rate", {"Retry-After":"3600"}, None)
    monkeypatch.setattr(summarization, "urlopen", fail)
    try: summarization.call_openrouter([], api_key="key", model="m", base_url="https://unit.test", attempts=1)
    except summarization.RateLimitError as exc: assert exc.retry_after == 3600
    else: assert False, "rate limit exception was not propagated"

def test_cache_hit_key_stable():
    assert summarization.cache_key("same", "model") == summarization.cache_key("same", "model")
    assert summarization.cache_key("same", "model") != summarization.cache_key("same", "other")

def test_summary_job_uses_cache_without_http(monkeypatch, tmp_path):
    from app.models import SummaryCache
    engine=create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="cached", channel_id=1, status="ACTIVE", transcription_status="completed")
    db.add(video); db.flush(); rows=[{"start":0,"end":2,"text":"cached words"}]
    db.add(TranscriptSegment(video_id=video.id, start=0, end=2, text="cached words")); db.add(AppSetting(key="summary_enabled", value={"value":True})); db.add(AppSetting(key="openrouter_model", value={"value":"model"})); db.add(AppSetting(key="openrouter_api_key", value={"value":"secret"}))
    result={"summary":"cached","topics":[],"keywords":[],"important_moments":[]}
    db.add(SummaryCache(key=summarization.cache_key(summarization.format_transcript(rows), "model"), result=result))
    job=ProcessingJob(video_id=video.id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0, payload={"model":"model","chunks":[],"next_chunk":0},created_at=now(),updated_at=now()); db.add(job); db.commit(); jid=job.id; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory)
    monkeypatch.setattr(summarization,"call_openrouter",lambda *a,**k: (_ for _ in ()).throw(AssertionError("cache miss")))
    processing._process_summary(jid)
    check=factory(); assert check.get(ProcessingJob,jid).status == "completed"; assert check.query(ProcessingJob).filter_by(kind="SUMMARIZE_FINAL").count() == 1; check.close()

def test_provider_failure_does_not_change_recording_or_transcription(monkeypatch, tmp_path, caplog):
    engine=create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="v", channel_id=1, status="ACTIVE", transcription_status="completed", summary_status="processing")
    db.add(video); db.flush(); db.add(TranscriptSegment(video_id=video.id,start=0,end=1,text="local words")); db.add(AppSetting(key="summary_enabled",value={"value":True})); db.add(AppSetting(key="openrouter_model",value={"value":"model"})); db.add(AppSetting(key="openrouter_api_key",value={"value":"never-log-this"})); db.add(AppSetting(key="openrouter_max_attempts",value={"value":2}))
    job=ProcessingJob(video_id=video.id,kind="SUMMARIZE_CHUNKS",status="processing",attempts=0,payload={"model":"model","chunks":[],"next_chunk":0},created_at=now(),updated_at=now()); db.add(job); db.commit(); vid=video.id; jid=job.id; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory); monkeypatch.setattr(summarization,"call_openrouter",lambda *a,**k: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    with caplog.at_level(logging.WARNING): processing._process_summary(jid)
    check=factory(); saved=check.get(Video,vid)
    assert saved.status == "ACTIVE" and saved.transcription_status == "completed" and saved.summary_status == "retry_wait"
    assert "never-log-this" not in caplog.text and "local words" not in caplog.text
    check.close()
