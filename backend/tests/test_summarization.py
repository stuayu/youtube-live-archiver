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
    assert summarization.validate_chunk({"summary": "only"}) == {
        "summary": "only", "topics": [], "keywords": [], "important_moments": []}
    for bad in ({"topics": []}, ["summary"], {"summary": "s", "topics": "nope"}):
        with pytest.raises(ValueError):
            summarization.validate_chunk(bad)


def test_final_normalizes_loose_model_output_to_the_ui_shape():
    raw = {"result": {
        "summary": "```json\n{\"summary\": \"全体の概要。\"}\n```",
        "topics": [{"start": "00:12:34", "end": "700", "title": "雑談", "summary": "近況", "extra": 1},
                   {"start": 5, "end": 60, "title": "挨拶", "summary": ""},
                   {"start": "unknown", "title": "捨てる"}, "not a topic"],
        "important_moments": [{"timestamp": "1:05", "description": "告知"}],
        "keywords": "朝活、#ラジオ, 朝活",
        "timeline": None,
        "unexpected": {"x": 1},
    }}
    result = summarization.validate_final(raw)
    assert result == {
        "summary": "全体の概要。",
        "topics": [{"start": 5.0, "end": 60.0, "title": "挨拶", "summary": ""},
                   {"start": 754.0, "end": 754.0, "title": "雑談", "summary": "近況"}],
        "highlights": [{"timestamp": 65.0, "description": "告知"}],
        "keywords": ["朝活", "ラジオ"],
        "timeline": [{"start": 5.0, "title": "挨拶"}, {"start": 754.0, "title": "雑談"}],
    }


def test_prompts_spell_out_the_strict_output_rules():
    for messages in (summarization.chunk_messages("x"), summarization.final_messages("[]"),
                     summarization.intermediate_messages("[]")):
        prompt = messages[1]["content"]
        assert "Never write \"00:12:34\"" in prompt and "code fences" in messages[0]["content"]

def test_final_validation_defaults_missing_and_null_lists():
    result = summarization.validate_final({"summary": "s", "topics": None, "highlights": [], "keywords": []})
    assert result == {"summary": "s", "topics": [], "highlights": [], "keywords": [], "timeline": []}
    with pytest.raises(ValueError, match="summary"):
        summarization.validate_final({"topics": []})

def test_openrouter_error_finish_reason_is_a_retryable_provider_error(monkeypatch):
    def opener(request, timeout):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"choices": [{"finish_reason": "error", "message": {
                    "content": None, "reasoning": "private reasoning text"}}]}).encode()
        return Response()

    monkeypatch.setattr(summarization, "urlopen", opener)
    with pytest.raises(RuntimeError, match="finish_reason=error") as raised:
        summarization.call_openrouter([], api_key="never-log-this", model="model",
                                      base_url="https://unit.test", attempts=1)
    assert "private reasoning text" not in str(raised.value)
    assert "never-log-this" not in str(raised.value)

@pytest.mark.parametrize(("payload", "error_type", "finish_reason"), [
    ({"error": {"code": "overloaded", "message": "Provider is temporarily busy"}},
     summarization.ProviderResponseError, None),
    ({"choices": [{"finish_reason": "stop", "error": {
        "code": "capacity", "message": "Provider capacity reached"}}]},
     summarization.ProviderResponseError, None),
    ({"choices": [{"finish_reason": "length", "message": {"content": None}}]},
     summarization.TruncatedProviderResponseError, "length"),
    ({"choices": [{"message": {"content": "{}"}}]}, summarization.ProviderResponseError, None),
    ({"choices": [{"finish_reason": "stop", "message": {"content": ""}}]},
     summarization.ProviderResponseError, "stop"),
])
def test_openrouter_classifies_provider_errors_and_truncation(monkeypatch, payload, error_type, finish_reason):
    def opener(request, timeout):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps(payload).encode()
        return Response()

    monkeypatch.setattr(summarization, "urlopen", opener)
    with pytest.raises(error_type) as raised:
        summarization.call_openrouter([], api_key="test-key", model="model",
                                      base_url="https://unit.test", attempts=1)
    assert raised.value.finish_reason == finish_reason
    if payload.get("error"):
        assert raised.value.provider_code == "overloaded"
        assert raised.value.provider_message == "Provider is temporarily busy"
        assert "provider_code=overloaded" in str(raised.value)
        assert "provider_message=Provider is temporarily busy" in str(raised.value)
    if payload.get("choices", [{}])[0].get("error"):
        assert raised.value.provider_code == "capacity"

def test_openrouter_provider_error_metadata_redacts_key_and_request_content(monkeypatch):
    messages = [{"role": "user", "content": "private transcript excerpt"}]
    def opener(request, timeout):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"error": {"code": "rejected", "message":
                    "private transcript excerpt uses api_key=never-log-this"}}).encode()
        return Response()

    monkeypatch.setattr(summarization, "urlopen", opener)
    with pytest.raises(summarization.ProviderResponseError) as raised:
        summarization.call_openrouter(messages, api_key="never-log-this", model="model",
                                      base_url="https://unit.test", attempts=1)
    assert "private transcript excerpt" not in str(raised.value)
    assert "never-log-this" not in str(raised.value)

def test_final_provider_error_is_retry_wait_with_readable_message(monkeypatch, tmp_path):
    from sqlalchemy import event
    engine=create_engine(f"sqlite:///{tmp_path / 'provider-error.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="provider-error", channel_id=1, status="ACTIVE",
                              transcription_status="completed", summary_status="processing")
    db.add(video); db.flush(); vid=video.id
    db.add_all([AppSetting(key="summary_enabled", value={"value":True}),
                AppSetting(key="openrouter_model", value={"value":"model"}),
                AppSetting(key="openrouter_api_key", value={"value":"never-log-this"}),
                AppSetting(key="openrouter_max_attempts", value={"value":2})])
    job=ProcessingJob(video_id=vid, kind="SUMMARIZE_FINAL", status="processing", attempts=0,
                      payload={"model":"model", "chunks":[{"start":0,"end":10,"summary":"opening"}]},
                      created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid=job.id; db.close()

    checked_out = 0
    def on_checkout(*args):
        nonlocal checked_out
        checked_out += 1
    def on_checkin(*args):
        nonlocal checked_out
        checked_out -= 1
    event.listen(engine, "checkout", on_checkout)
    event.listen(engine, "checkin", on_checkin)
    def opener(request, timeout):
        assert checked_out == 0, "provider request held an idle database connection"
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self):
                return json.dumps({"choices": [{"finish_reason": "error", "message": {
                    "content": None, "reasoning": "private reasoning text"}}]}).encode()
        return Response()

    monkeypatch.setattr(processing, "SessionLocal", factory)
    monkeypatch.setattr(summarization, "urlopen", opener)
    processing._process_summary(jid)

    check=factory(); saved=check.get(ProcessingJob, jid); saved_video=check.get(Video, vid)
    assert saved.status == "retry_wait"
    assert "finish_reason=error" in saved.error
    assert "別のモデル" in saved.error or "短いチャンク長" in saved.error
    assert saved_video.summary_status == "retry_wait" and saved_video.summary_error == saved.error
    assert "ValueError" not in saved.error
    assert "private reasoning text" not in saved.error and "never-log-this" not in saved.error
    assert len(saved.error) <= 500
    check.close()

def test_large_final_merge_resumes_after_each_provider_call(monkeypatch, tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'merge-resume.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="merge-resume", channel_id=1, status="ACTIVE",
                              transcription_status="completed", summary_status="pending")
    db.add(video); db.flush(); vid=video.id
    db.add_all([AppSetting(key="summary_enabled", value={"value":True}),
                AppSetting(key="openrouter_model", value={"value":"model"}),
                AppSetting(key="openrouter_api_key", value={"value":"test-key"})])
    chunks=[{"start":i*60, "end":(i+1)*60, "summary":f"chunk-{i} " + ("context " * 200),
             "topics":[{"start":i*60,"end":i*60+20,"title":f"topic-{i}","summary":"detail"}],
             "keywords":[f"keyword-{i}"], "important_moments":[]} for i in range(30)]
    job=ProcessingJob(video_id=vid, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                      payload={"model":"model", "chunks":chunks}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid=job.id; db.close()

    requests=[]
    def respond(messages, **kwargs):
        content=messages[1]["content"]
        requests.append(content)
        if "Combine these per-section summaries" in content:
            return {"summary":"Final summary", "topics":[], "highlights":[], "keywords":[], "timeline":[]}
        return {"summary":"Merged group", "topics":[], "keywords":[], "important_moments":[]}

    monkeypatch.setattr(processing, "SessionLocal", factory)
    monkeypatch.setattr(summarization, "call_openrouter", respond)

    processing._process_summary(jid)
    check=factory(); first=check.get(ProcessingJob, jid)
    assert first.status == "pending"
    assert first.payload["final_merge"]["next_group"] == 1
    first_request=requests[0]
    check.close()

    # A new worker invocation starts from the database checkpoint, not group zero.
    processing._process_summary(jid)
    assert requests[1] != first_request
    for _ in range(20):
        check=factory(); saved=check.get(ProcessingJob, jid); status=saved.status; check.close()
        if status == "completed": break
        processing._process_summary(jid)

    check=factory(); saved=check.get(ProcessingJob, jid); saved_video=check.get(Video, vid)
    assert saved.status == "completed" and saved_video.summary_data["summary"] == "Final summary"
    assert len(requests) > 2
    assert len(requests) == len(set(requests))
    check.close()

def test_exhausted_final_job_falls_back_to_deterministic_chunk_assembly(monkeypatch, tmp_path):
    engine=create_engine(f"sqlite:///{tmp_path / 'final-fallback.sqlite'}"); Base.metadata.create_all(engine); factory=sessionmaker(bind=engine, expire_on_commit=False)
    db=factory(); video=Video(youtube_video_id="final-fallback", channel_id=1, status="ACTIVE",
                              transcription_status="completed", summary_status="pending")
    db.add(video); db.flush(); vid=video.id
    db.add_all([AppSetting(key="summary_enabled", value={"value":True}),
                AppSetting(key="openrouter_model", value={"value":"model"}),
                AppSetting(key="openrouter_api_key", value={"value":"test-key"}),
                AppSetting(key="openrouter_max_attempts", value={"value":1})])
    chunks=[{"start":0,"end":10,"summary":"opening section", "topics":[
                 {"start":2,"end":8,"title":"Opening","summary":"topic detail"}],
             "keywords":["live", "news"], "important_moments":[
                 {"timestamp":5,"description":"A key moment"}]},
            {"start":10,"end":20,"summary":"closing section", "topics":[
                 {"start":12,"end":19,"title":"Closing","summary":"more detail"}],
             "keywords":["news", "music"], "important_moments":[]}]
    job=ProcessingJob(video_id=vid, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                      payload={"model":"model", "chunks":chunks}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); jid=job.id; db.close()

    monkeypatch.setattr(processing, "SessionLocal", factory)
    monkeypatch.setattr(summarization, "call_openrouter",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("provider unavailable")))
    processing._process_summary(jid)

    check=factory(); saved=check.get(ProcessingJob, jid); saved_video=check.get(Video, vid)
    assert saved.status == "completed" and saved.error == saved_video.summary_error
    assert saved_video.summary_status == "completed"
    assert saved_video.summary_data["assembled"] is True
    assert "opening section" in saved_video.summary_data["summary"]
    assert [topic["title"] for topic in saved_video.summary_data["topics"]] == ["Opening", "Closing"]
    assert saved_video.summary_data["highlights"] == [{"timestamp":5,"description":"A key moment"}]
    assert saved_video.summary_data["keywords"] == ["news", "live", "music"]
    assert "AI" in saved_video.summary_error
    check.close()

def test_429_retry_after_and_backoff(monkeypatch):
    calls, delays = [], []
    def opener(request, timeout):
        calls.append(request)
        if len(calls) == 1: raise HTTPError(request.full_url, 429, "rate", {"Retry-After":"7"}, None)
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self): return json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}).encode()
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
            def read(self): return json.dumps({"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}).encode()
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
    assert saved.summary_error == saved_job.error
    if isinstance(provider_error, summarization.RateLimitError):
        assert "利用上限" in saved_job.error and "60秒後" in saved_job.error
    else:
        assert "タイムアウト" in saved_job.error
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


def test_display_summary_normalizes_stored_rows_and_keeps_assembled_flag():
    from app.api.videos import _display_summary
    assert _display_summary(None) is None
    assert _display_summary({"topics": []}) is None
    shown = _display_summary({"summary": "s", "topics": [{"start": "0:30", "title": "t"}], "assembled": True})
    assert shown["assembled"] is True and shown["topics"][0]["start"] == 30.0 and shown["highlights"] == []
