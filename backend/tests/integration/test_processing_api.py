from datetime import timedelta
from uuid import uuid4

import pytest

from app.db import SessionLocal
from app.models import Channel, ProcessingJob, TranscriptSegment, Video, now


def add_channel(name="Processing Channel"):
    with SessionLocal() as db:
        channel = Channel(youtube_channel_id="UC" + uuid4().hex[:22],
                          url="https://www.youtube.com/@processing", name=name)
        db.add(channel)
        db.flush()
        channel_id = channel.id
        db.commit()
        return channel_id


def add_video(channel_id, title, youtube_video_id=None, duration=180):
    with SessionLocal() as db:
        video = Video(youtube_video_id=youtube_video_id or uuid4().hex[:11], channel_id=channel_id,
                      title=title, status="ACTIVE", original_duration=duration)
        db.add(video)
        db.flush()
        video_id = video.id
        db.commit()
        return video_id


@pytest.mark.integration
def test_processing_jobs_filter_search_counts_and_worker_queue_positions(client):
    channel_id = add_channel("Queue Search Channel")
    first_video = add_video(channel_id, "Queued transcript", "transcript01", duration=1800)
    second_video = add_video(channel_id, "Summary video", "summaryvid01")
    with SessionLocal() as db:
        old = now() - timedelta(minutes=10)
        db.add_all([
            ProcessingJob(video_id=first_video, kind="TRANSCRIBE", status="pending", attempts=0,
                          payload={"total_chunks": 3, "completed_chunks": 2, "chunk_seconds": 600},
                          created_at=old, updated_at=old),
            ProcessingJob(video_id=second_video, kind="TRANSCRIBE", status="pending", attempts=0,
                          payload={}, created_at=old, updated_at=old),
            ProcessingJob(video_id=second_video, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0,
                          payload={"chunks": [], "next_chunk": 0}, created_at=old, updated_at=old),
            ProcessingJob(video_id=second_video, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                          payload={"chunks": []}, created_at=now(), updated_at=now()),
            ProcessingJob(video_id=first_video, kind="CHAT_REPLAY", status="processing", attempts=1,
                          payload={}, created_at=old, updated_at=old),
            ProcessingJob(video_id=second_video, kind="CHAT_REPLAY", status="retry_wait", attempts=3,
                          next_attempt_at=now() + timedelta(hours=1), payload={}, created_at=old,
                          updated_at=old, error="rate limited"),
        ])
        db.commit()

    all_jobs = client.get("/api/processing/jobs")
    assert all_jobs.status_code == 200, all_jobs.text
    body = all_jobs.json()
    assert body["total"] == 6
    assert body["counts"]["by_kind"]["SUMMARIZE_CHUNKS"]["pending"] == 1
    transcriptions = [row for row in body["items"] if row["kind"] == "TRANSCRIBE"]
    assert sorted(row["queue_position"] for row in transcriptions) == [1, 2]
    first_transcription = next(row for row in transcriptions if row["video"]["id"] == str(first_video))
    second_transcription = next(row for row in transcriptions if row["video"]["id"] == str(second_video))
    assert first_transcription["queue_position"] == 1
    assert second_transcription["queue_position"] == 2
    assert first_transcription["progress"] == {"done": 2, "total": 3}
    assert first_transcription["current_chunk"] == {"start": 1200, "end": 1800}
    rows = {row["kind"]: row for row in body["items"] if row["kind"] != "TRANSCRIBE" and
            row["kind"] != "CHAT_REPLAY"}
    assert rows["SUMMARIZE_FINAL"]["queue_position"] == 1
    assert rows["SUMMARIZE_CHUNKS"]["queue_position"] == 2
    assert first_transcription["video"]["channel_name"] == "Queue Search Channel"
    chat_rows = [row for row in body["items"] if row["kind"] == "CHAT_REPLAY"]
    assert next(row for row in chat_rows if row["status"] == "retry_wait")["queue_position"] is None

    filtered = client.get("/api/processing/jobs", params=[("kind", "TRANSCRIBE"), ("status", "pending"),
                                                            ("q", "transcript01")])
    assert filtered.status_code == 200
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["video"]["title"] == "Queued transcript"

    title_search = client.get("/api/processing/jobs", params={"q": "Queued transcript"})
    assert title_search.json()["total"] == 2

    channel_search = client.get("/api/processing/jobs", params=[("q", "Queue Search Channel"),
                                                                 ("status", "pending"),
                                                                 ("status", "processing")])
    assert channel_search.json()["total"] == 5

    paged = client.get("/api/processing/jobs", params={"limit": 2, "offset": 2})
    assert paged.status_code == 200
    assert paged.json()["total"] == 6 and len(paged.json()["items"]) == 2
    assert paged.json()["offset"] == 2 and paged.json()["limit"] == 2


@pytest.mark.integration
def test_summary_job_detail_reports_chunk_ranges_states_and_saved_summary(client):
    channel_id = add_channel()
    video_id = add_video(channel_id, "Chunked summary")
    with SessionLocal() as db:
        db.add_all([
            TranscriptSegment(video_id=video_id, start=0, end=60, text="first section.", raw=False),
            TranscriptSegment(video_id=video_id, start=61, end=100, text="second section", raw=False),
            ProcessingJob(video_id=video_id, kind="SUMMARIZE_CHUNKS", status="processing", attempts=2,
                          payload={"model": "test-model", "chunk_minutes": 1, "total_chunks": 2,
                                   "next_chunk": 1,
                                   "chunks": [{"start": 0, "end": 60, "summary": "First summary"}]}),
            ProcessingJob(video_id=video_id, kind="SUMMARIZE_FINAL", status="pending", attempts=0,
                          payload={"chunks": [{"start": 0, "end": 60, "summary": "Final input summary"}]}),
        ])
        db.commit()
        job_id = db.query(ProcessingJob).filter_by(video_id=video_id, kind="SUMMARIZE_CHUNKS").one().id
        final_id = db.query(ProcessingJob).filter_by(video_id=video_id, kind="SUMMARIZE_FINAL").one().id

    response = client.get(f"/api/processing/jobs/{job_id}")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["progress"] == {"done": 1, "total": 2}
    assert detail["current_chunk"] == {"start": 61, "end": 100}
    assert detail["chunks"] == [
        {"index": 0, "start": 0, "end": 60, "state": "done", "summary": "First summary"},
        {"index": 1, "start": 61, "end": 100, "state": "current", "summary": None},
    ]

    final_detail = client.get(f"/api/processing/jobs/{final_id}").json()
    assert final_detail["chunks"] == [
        {"index": 0, "start": 0, "end": 60, "state": "done", "summary": "Final input summary"}
    ]
