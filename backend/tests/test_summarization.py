import json
import logging
from datetime import timedelta, timezone
from urllib.error import HTTPError

import pytest
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
    assert result == {} and delays == [60] and len(calls) == 2

def test_final_429_keeps_retry_after_for_durable_job(monkeypatch):
    def fail(request, timeout): raise HTTPError(request.full_url, 429, "rate", {"Retry-After":"3600"}, None)
    monkeypatch.setattr(summarization, "urlopen", fail)
    try: summarization.call_openrouter([], api_key="key", model="m", base_url="https://unit.test", attempts=1)
    except summarization.RateLimitError as exc: assert exc.retry_after == 3600
    else: assert False, "rate limit exception was not propagated"

def test_429_retry_wait_is_never_shorter_than_one_minute(monkeypatch):
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
    summarization.call_openrouter([], api_key="secret", model="x", base_url="https://unit.test",
                                  attempts=2, sleeper=delays.append)
    assert delays == [60]

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

def test_summary_chunk_job_processes_only_one_chunk_per_invocation(monkeypatch, tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'bounded.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="bounded", channel_id=1, status="ACTIVE", transcription_status="completed")
    db.add(video); db.flush()
    db.add_all([TranscriptSegment(video_id=video.id, start=start, end=start + 1, text=f"words {start}")
                for start in (0, 61, 121)])
    db.add_all([AppSetting(key="summary_enabled", value={"value":True}),
                AppSetting(key="openrouter_model", value={"value":"model"}),
                AppSetting(key="openrouter_api_key", value={"value":"secret"}),
                AppSetting(key="summary_chunk_minutes", value={"value":1})])
    job=ProcessingJob(video_id=video.id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0,
                      payload={"model":"model","chunks":[],"next_chunk":0,"chunk_minutes":1},
                      created_at=now(),updated_at=now())
    db.add(job); db.commit(); jid=job.id; vid=video.id; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory)
    calls=[]
    monkeypatch.setattr(summarization,"call_openrouter",lambda *a,**k: calls.append(a) or {
        "summary":"part","topics":[],"keywords":[],"important_moments":[]})

    processing._process_summary(jid)

    check=factory(); saved=check.get(ProcessingJob,jid); saved_video=check.get(Video,vid)
    assert len(calls) == 1
    assert saved.status == "pending" and saved.payload["next_chunk"] == 1
    assert saved_video.summary_status == "pending"
    assert check.query(ProcessingJob).filter_by(kind="SUMMARIZE_FINAL").count() == 0
    check.close()

def test_summary_worker_prioritizes_final_and_rotates_chunk_jobs(monkeypatch, tmp_path):
    from datetime import timedelta
    from contextlib import contextmanager
    from types import SimpleNamespace
    engine=create_engine(f"sqlite:///{tmp_path / 'fair-queue.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); old=now() - timedelta(hours=1)
    videos=[Video(youtube_video_id=f"queue-{index}", channel_id=1, status="ACTIVE", transcription_status="completed")
            for index in range(3)]
    db.add_all(videos); db.flush()
    jobs=[ProcessingJob(video_id=videos[0].id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0,
                        payload={"model":"m"}, created_at=old, updated_at=old),
          ProcessingJob(video_id=videos[1].id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0,
                        payload={"model":"m"}, created_at=old, updated_at=old),
          ProcessingJob(video_id=videos[2].id, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                        payload={"model":"m","chunks":[]}, created_at=old, updated_at=now())]
    db.add_all(jobs); db.commit(); ids={job.id: job.kind for job in jobs}; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory)
    monkeypatch.setattr(processing,"_recover_interrupted_summaries",lambda: None)
    monkeypatch.setattr(processing,"effective_summary_settings",lambda db: {
        "summary_enabled":True,"openrouter_max_concurrency":1})
    class TestLocks:
        @contextmanager
        def acquire(self, namespace, key):
            yield SimpleNamespace(acquired=True)
    monkeypatch.setattr(processing,"default_locks",TestLocks())
    selected=[]
    def process(job_id):
        selected.append(job_id)
        with factory() as session:
            job=session.get(ProcessingJob, job_id)
            if job.kind == "SUMMARIZE_FINAL":
                job.status="completed"
            else:
                job.status="pending"
                job.updated_at=now() + timedelta(seconds=10)
            session.commit()
    monkeypatch.setattr(processing,"_process_summary",process)

    processing.summary_worker()
    processing.summary_worker()
    processing.summary_worker()

    assert [ids[job_id] for job_id in selected] == ["SUMMARIZE_FINAL", "SUMMARIZE_CHUNKS", "SUMMARIZE_CHUNKS"]
    assert selected[1:] == [jobs[0].id, jobs[1].id]

def test_final_summary_job_uses_saved_chunks_and_completes(monkeypatch, tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'final.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="final", channel_id=1, status="ACTIVE", transcription_status="completed",
                              summary_status="pending")
    db.add(video); db.flush(); vid=video.id
    db.add_all([AppSetting(key="summary_enabled", value={"value":True}),
                AppSetting(key="openrouter_model", value={"value":"model"}),
                AppSetting(key="openrouter_api_key", value={"value":"secret"})])
    chunks=[{"start":0,"end":10,"summary":"opening","topics":[],"keywords":[],"important_moments":[]}]
    job=ProcessingJob(video_id=vid, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                      payload={"model":"model","chunks":chunks}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid=job.id; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory)
    requests=[]
    result={"summary":"combined","topics":[],"highlights":[],"keywords":[],"timeline":[]}
    monkeypatch.setattr(summarization,"call_openrouter",lambda messages,**kwargs: requests.append(messages) or result)

    processing._process_summary(jid)

    check=factory(); saved=check.get(ProcessingJob,jid); saved_video=check.get(Video,vid)
    assert saved.status == "completed" and saved.error is None
    assert saved_video.summary_status == "completed" and saved_video.summary_data == result
    assert len(requests) == 1 and "opening" in requests[0][1]["content"]
    check.close()

@pytest.mark.parametrize("provider_error", [TimeoutError("request timed out"), summarization.RateLimitError(7)])
def test_provider_failure_does_not_change_recording_or_transcription(monkeypatch, tmp_path, caplog, provider_error):
    engine=create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="v", channel_id=1, status="ACTIVE", transcription_status="completed", summary_status="processing")
    db.add(video); db.flush(); db.add(TranscriptSegment(video_id=video.id,start=0,end=1,text="local words")); db.add(AppSetting(key="summary_enabled",value={"value":True})); db.add(AppSetting(key="openrouter_model",value={"value":"model"})); db.add(AppSetting(key="openrouter_api_key",value={"value":"never-log-this"})); db.add(AppSetting(key="openrouter_max_attempts",value={"value":2}))
    job=ProcessingJob(video_id=video.id,kind="SUMMARIZE_CHUNKS",status="processing",attempts=0,payload={"model":"model","chunks":[],"next_chunk":0},created_at=now(),updated_at=now()); db.add(job); db.commit(); vid=video.id; jid=job.id; db.close()
    calls=[]
    def fail(*args, **kwargs):
        calls.append(None)
        raise provider_error
    monkeypatch.setattr(processing,"SessionLocal",factory); monkeypatch.setattr(summarization,"call_openrouter",fail)
    retry_started=now()
    with caplog.at_level(logging.WARNING): processing._process_summary(jid)
    check=factory(); saved=check.get(Video,vid); saved_job=check.get(ProcessingJob,jid)
    assert saved.status == "ACTIVE" and saved.transcription_status == "completed" and saved.summary_status == "retry_wait"
    assert saved_job.status == "retry_wait" and saved_job.next_attempt_at is not None
    retry_at=saved_job.next_attempt_at
    if retry_at.tzinfo is None:
        retry_at=retry_at.replace(tzinfo=timezone.utc)
    assert retry_at >= retry_started + timedelta(seconds=59)
    assert type(provider_error).__name__ in saved_job.error and saved.summary_error == saved_job.error
    assert len(calls) == 1
    assert "never-log-this" not in caplog.text and "local words" not in caplog.text
    check.close()


def test_summary_survives_cache_write_failure(monkeypatch, tmp_path):
    """A failed SummaryCache commit used to poison the session (PendingRollbackError)."""
    from sqlalchemy.exc import OperationalError
    engine=create_engine(f"sqlite:///{tmp_path / 'cachefail.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="cachefail", channel_id=1, status="ACTIVE", transcription_status="completed")
    db.add(video); db.flush()
    db.add(TranscriptSegment(video_id=video.id, start=0, end=2, text="words")); db.add(AppSetting(key="summary_enabled", value={"value":True})); db.add(AppSetting(key="openrouter_model", value={"value":"model"})); db.add(AppSetting(key="openrouter_api_key", value={"value":"secret"}))
    job=ProcessingJob(video_id=video.id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0, payload={"model":"model","chunks":[],"next_chunk":0},created_at=now(),updated_at=now()); db.add(job); db.commit(); jid=job.id; db.close()
    monkeypatch.setattr(processing,"SessionLocal",factory)
    result={"summary":"ok","topics":[],"keywords":[],"important_moments":[]}
    monkeypatch.setattr(summarization,"call_openrouter",lambda *a,**k: result)
    from sqlalchemy.orm import Session as OrmSession
    original_merge = OrmSession.merge
    def failing_merge(self, instance, *args, **kwargs):
        if type(instance).__name__ == "SummaryCache":
            raise OperationalError("INSERT", {}, Exception("connection dropped"))
        return original_merge(self, instance, *args, **kwargs)
    monkeypatch.setattr(OrmSession, "merge", failing_merge)
    processing._process_summary(jid)
    check=factory(); job=check.get(ProcessingJob,jid)
    assert job.status == "completed" and job.payload["next_chunk"] == 1 and job.payload["total_chunks"] == 1
    check.close()
