from contextlib import contextmanager
from types import SimpleNamespace

import numpy as np
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import ProcessingJob, Video
from app.services import edit_analysis
from app.services.edit_diff import RATE


def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def add_video(factory, tmp_path, **values):
    with factory() as db:
        video = Video(youtube_video_id="abc", channel_id=1, status="PROTECTED", file_path=str(tmp_path),
                      protected=True, protection_reason="REMOTE_DURATION_CHANGED", **values)
        db.add(video)
        db.commit()
        return video.id


def envelope(seconds, seed):
    rng = np.random.default_rng(seed)
    return np.clip(-30 + np.cumsum(rng.normal(0, 2, int(seconds * RATE))) % 20 - 10, -60, 0)


def test_enqueue_is_idempotent_until_forced(tmp_path):
    factory = session_factory(tmp_path)
    video_id = add_video(factory, tmp_path)
    with factory() as db:
        first = edit_analysis.enqueue(db, video_id)
        db.commit()
        first.status = "completed"
        db.commit()
        assert edit_analysis.enqueue(db, video_id).status == "completed"
        again = edit_analysis.enqueue(db, video_id, force=True)
        db.commit()
        assert again.id == first.id and again.status == "pending"
        assert db.query(ProcessingJob).count() == 1


def test_job_stores_removed_regions_for_the_player(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    video_id = add_video(factory, tmp_path)
    local = envelope(1200, 1)
    remote = np.concatenate([local[:300 * RATE], local[420 * RATE:]]) + 2
    monkeypatch.setattr(edit_analysis, "SessionLocal", factory)
    monkeypatch.setattr(edit_analysis, "local_envelope", lambda video: local)
    monkeypatch.setattr(edit_analysis, "remote_envelope", lambda video: remote)
    with factory() as db:
        job = edit_analysis.enqueue(db, video_id)
        db.commit()
        job_id = job.id
    edit_analysis.process(job_id)
    with factory() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "completed" and job.error is None
        removed = job.payload["result"]["removed"]
        assert len(removed) == 1
        assert abs(removed[0]["start"] - 300) < 2 and abs(removed[0]["end"] - 420) < 2
        view = edit_analysis.view(db, video_id)
        assert view["status"] == "completed" and view["result"]["removed"] == removed


def test_failed_download_waits_and_reports_the_error(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    video_id = add_video(factory, tmp_path)
    monkeypatch.setattr(edit_analysis, "SessionLocal", factory)
    monkeypatch.setattr(edit_analysis, "local_envelope", lambda video: envelope(60, 2))

    def unavailable(video):
        raise RuntimeError("HTTP Error 403")

    monkeypatch.setattr(edit_analysis, "remote_envelope", unavailable)
    with factory() as db:
        job_id = edit_analysis.enqueue(db, video_id).id
        db.commit()
    edit_analysis.process(job_id)
    with factory() as db:
        view = edit_analysis.view(db, video_id)
        assert view["status"] == "retry_wait"
        assert "403" in view["error"]
        assert view["result"] is None


def test_view_without_job_is_none(tmp_path):
    factory = session_factory(tmp_path)
    video_id = add_video(factory, tmp_path)
    with factory() as db:
        assert edit_analysis.view(db, video_id) == {"status": "none", "error": None, "result": None,
                                                    "updated_at": None}


def test_worker_runs_due_jobs_only(tmp_path, monkeypatch):
    factory = session_factory(tmp_path)
    video_id = add_video(factory, tmp_path)
    monkeypatch.setattr(edit_analysis, "SessionLocal", factory)
    ran = []
    monkeypatch.setattr(edit_analysis, "process", ran.append)

    @contextmanager
    def acquire(namespace, key):
        yield SimpleNamespace(acquired=True)

    monkeypatch.setattr(edit_analysis.default_locks, "acquire", acquire)
    with factory() as db:
        job_id = edit_analysis.enqueue(db, video_id).id
        db.commit()
    edit_analysis.worker()
    assert ran == [job_id]
    with factory() as db:
        db.get(ProcessingJob, job_id).status = "completed"
        db.commit()
    edit_analysis.worker()
    assert ran == [job_id]
    with factory() as db:
        assert db.scalar(select(ProcessingJob.kind)) == "EDIT_ANALYSIS"
