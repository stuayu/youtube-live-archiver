from datetime import timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import AppSetting, ProcessingJob, TranscriptSegment, Video, now
from app.services import processing, transcription, ffmpeg


def db_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_enqueue_transcription_is_idempotent(tmp_path):
    db = db_session(tmp_path)
    db.add(Video(youtube_video_id="abc", channel_id=1, status="ACTIVE", file_path=str(tmp_path)))
    db.commit()
    one = processing.enqueue_transcription(db, 1)
    two = processing.enqueue_transcription(db, 1)
    assert one.id == two.id
    assert db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == 1)).id == one.id
    assert db.query(ProcessingJob).count() == 1
    db.close()


def test_retry_backoff_is_exponential_and_capped():
    assert [processing.retry_delay_seconds(n) for n in (1, 2, 3, 4)] == [60, 120, 240, 480]
    assert processing.retry_delay_seconds(30) == 86400


def test_failure_updates_transcription_only_and_schedules_retry(monkeypatch, tmp_path):
    db = db_session(tmp_path)
    video = Video(youtube_video_id="abc", channel_id=1, status="ACTIVE", file_path=str(tmp_path), transcription_status="processing")
    db.add(video); db.flush(); video_id = video.id
    job = ProcessingJob(video_id=video.id, kind="TRANSCRIBE", status="processing", attempts=1, payload={}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); job_id = job.id
    db.close()
    monkeypatch.setattr(processing, "SessionLocal", sessionmaker(bind=processing_test_engine(tmp_path)))
    monkeypatch.setattr(processing, "extract_whisper_audio", lambda source, **kw: tmp_path / "audio.wav")
    monkeypatch.setattr(processing, "load_whisper_model", lambda **kw: object())
    monkeypatch.setattr(processing, "transcribe_audio", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("mock failure")))
    (tmp_path / "media.mp4").write_bytes(b"media")
    monkeypatch.setattr("app.services.backfill.media_file", lambda _: tmp_path / "media.mp4")
    monkeypatch.setattr(processing.settings, "processing_max_attempts", 5)
    processing._process_transcription(job_id)
    check = sessionmaker(bind=processing_test_engine(tmp_path))()
    saved = check.get(Video, video_id); queued = check.get(ProcessingJob, job_id)
    assert saved.status == "ACTIVE"
    assert saved.transcription_status == "retry_wait"
    assert queued.status == "retry_wait"
    assert queued.next_attempt_at.replace(tzinfo=timezone.utc) >= now() + timedelta(seconds=59)
    check.close()


def test_success_stores_raw_and_clean_segments_without_changing_recording(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    db = factory()
    media = tmp_path / "recording.mp4"; media.write_bytes(b"original")
    video = Video(youtube_video_id="success", channel_id=1, status="ACTIVE", file_path=str(tmp_path),
                  recording_finished_at=now())
    db.add(video); db.commit(); video_id = video.id
    job = ProcessingJob(video_id=video_id, kind="TRANSCRIBE", status="pending", attempts=0, payload={}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); job_id = job.id; db.close()
    monkeypatch.setattr(processing, "SessionLocal", factory)
    monkeypatch.setattr(processing, "extract_whisper_audio", lambda source, **kw: tmp_path / "audio.wav")
    monkeypatch.setattr(processing, "load_whisper_model", lambda **kw: object())
    monkeypatch.setattr(processing, "transcribe_audio", lambda *a, **k: ([
        {"start": 1, "end": 2, "text": " hello  world "}, {"start": 2, "end": 3, "text": "hello world"}], "en"))
    monkeypatch.setattr("app.services.backfill.media_file", lambda _: media)
    processing._process_transcription(job_id)
    check = factory()
    assert check.get(Video, video_id).status == "ACTIVE"
    assert check.get(Video, video_id).transcription_status == "completed"
    rows = check.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id == video_id).order_by(TranscriptSegment.raw.desc())).all()
    assert len([x for x in rows if x.raw]) == 2
    assert len([x for x in rows if not x.raw]) == 1
    assert (tmp_path / "transcript.json").is_file() and (tmp_path / "transcript.vtt").is_file()
    assert media.read_bytes() == b"original"
    check.close()


def test_interrupted_transcription_resumes_after_committed_chunk(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    media = tmp_path / "long.mp4"; media.write_bytes(b"original")
    db = factory()
    video = Video(youtube_video_id="resume", channel_id=1, status="ACTIVE", file_path=str(tmp_path),
                  original_duration=1200, recording_finished_at=now())
    db.add(video); db.commit(); video_id = video.id
    job = ProcessingJob(video_id=video_id, kind="TRANSCRIBE", status="pending", attempts=0, payload={}, created_at=now(), updated_at=now())
    db.add(job); db.commit(); job_id = job.id; db.close()
    monkeypatch.setattr(processing, "SessionLocal", factory)
    monkeypatch.setattr(processing, "load_whisper_model", lambda **kw: object())
    extracted = []
    def extract(_source, *, start_seconds=0, duration_seconds=None):
        extracted.append(start_seconds)
        return tmp_path / "audio.wav"
    monkeypatch.setattr(processing, "extract_whisper_audio", extract)
    fail_once = {"done": False}
    def transcribe(_model, _audio, *, language=None, timestamp_offset=0):
        if timestamp_offset == 600 and not fail_once["done"]:
            fail_once["done"] = True
            raise RuntimeError("mock interruption")
        return ([{"start": timestamp_offset + .5, "end": timestamp_offset + 1, "text": "chunk one" if timestamp_offset == 0 else "chunk two"}], "en")
    monkeypatch.setattr(processing, "transcribe_audio", transcribe)
    monkeypatch.setattr("app.services.backfill.media_file", lambda _: media)
    monkeypatch.setattr(processing.settings, "processing_max_attempts", 5)
    processing._process_transcription(job_id)
    check = factory(); saved_job = check.get(ProcessingJob, job_id)
    assert saved_job.status == "retry_wait" and saved_job.payload["completed_chunks"] == 1
    assert check.query(TranscriptSegment).filter_by(video_id=video_id, raw=False).count() == 1
    check.close()
    processing._process_transcription(job_id)
    check = factory()
    rows = check.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
    assert extracted == [0, 600, 600]
    assert [(x.start, x.text) for x in rows] == [(.5, "chunk one"), (600.5, "chunk two")]
    assert check.get(ProcessingJob, job_id).payload["completed_chunks"] == 2
    check.close()


def processing_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    return engine


def test_preprocessing_preserves_timestamps_and_removes_repeat_loops():
    input_rows = [{"start": 1.25, "end": 2.5, "text": "  hello   there "},
                  {"start": 2.6, "end": 2.9, "text": "hello there"},
                  {"start": 3.0, "end": 3.3, "text": "hello there"},
                  {"start": 4, "end": 5, "text": "next"}]
    output = transcription.preprocess_segments(input_rows)
    assert output == [{"start": 1.25, "end": 2.5, "text": "hello there"}, {"start": 4.0, "end": 5.0, "text": "next"}]
    loop = [{"start": i, "end": i + .5, "text": word} for i, word in enumerate(["A", "B", "A", "B", "A", "B", "next"])]
    assert [x["text"] for x in transcription.preprocess_segments(loop)] == ["A", "B", "A", "B", "next"]


def test_audio_extraction_does_not_modify_source(monkeypatch, tmp_path):
    source = tmp_path / "original.mkv"; source.write_bytes(b"original archive")
    def fake_run(args, **kwargs):
        from pathlib import Path
        Path(args[-1]).write_bytes(b"0" * 64)
        return SimpleNamespace(returncode=0, stderr=b"")
    monkeypatch.setattr(ffmpeg.subprocess, "run", fake_run)
    audio = ffmpeg.extract_whisper_audio(source)
    assert source.read_bytes() == b"original archive"
    assert audio.is_file()
    audio.unlink()


def test_protected_video_derivatives_are_retention_excluded():
    from app.services.policy import may_delete
    assert not may_delete(protected=True, status="DELETE_ELIGIBLE", remote_status="PUBLIC",
        edit_unchanged=True, check_succeeded=True, retention_expires_at=now()-timedelta(days=1),
        grace_until=now()-timedelta(days=1), now=now())


def test_transcript_cleanup_keeps_protected_video_rows(tmp_path):
    from app.services.derived import clear_transcript_segments
    db = db_session(tmp_path)
    video = Video(youtube_video_id="protected", channel_id=1, protected=True, status="ACTIVE")
    db.add(video); db.flush()
    db.add(TranscriptSegment(video_id=video.id, start=0, end=1, text="retained", raw=False)); db.commit()
    assert clear_transcript_segments(db, video) is False
    assert db.query(TranscriptSegment).filter_by(video_id=video.id).count() == 1
    video.protected = False
    assert clear_transcript_segments(db, video) is True
    assert db.query(TranscriptSegment).filter_by(video_id=video.id).count() == 0
    db.close()


def test_srt_keeps_punctuation_and_transcript_exports_timestamps():
    assert "00:00:01,250 --> 00:00:02,500\nHi." in transcription.to_srt([{"start": 1.25, "end": 2.5, "text": "Hi."}])
    assert "00:00:01.250 --> 00:00:02.500" in transcription.to_webvtt([{"start": 1.25, "end": 2.5, "text": "Hi."}])


def test_processing_worker_keeps_transcription_separate_from_replay(monkeypatch):
    from app.services import processing

    ran = []
    monkeypatch.setattr(processing, "_process_transcription", lambda job_id: ran.append(job_id))
    monkeypatch.setattr(processing, "summary_worker", lambda: None)
    processing.processing_worker()
    assert ran == []
