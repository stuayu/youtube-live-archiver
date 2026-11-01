import math
from typing import Literal

from fastapi import Depends, HTTPException, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from .common import router
from ..db import get_db
from ..models import Channel, ProcessingJob, SummaryCache, TranscriptSegment, Video, now
from ..schemas import (
    ProcessingChunkOut,
    ProcessingJobCountsOut,
    ProcessingJobDetailOut,
    ProcessingJobOut,
    ProcessingJobPageOut,
    ProcessingVideoOut,
)

ProcessingKind = Literal["TRANSCRIBE", "SUMMARIZE_CHUNKS", "SUMMARIZE_FINAL", "CHAT_REPLAY"]


def _queue_positions(db: Session, at):
    eligible = or_(
        ProcessingJob.status == "pending",
        (ProcessingJob.status == "retry_wait") & (ProcessingJob.next_attempt_at <= at),
    )
    positions = {}
    lane_orders = {
        "TRANSCRIBE": (ProcessingJob.created_at.asc(), ProcessingJob.id.asc()),
        "SUMMARIZE": (
            case((ProcessingJob.kind == "SUMMARIZE_FINAL", 0), else_=1),
            ProcessingJob.updated_at.asc(), ProcessingJob.created_at.asc(), ProcessingJob.id.asc(),
        ),
        "CHAT_REPLAY": (
            ProcessingJob.payload["manual_queue"].as_string().desc().nullslast(),
            ProcessingJob.next_attempt_at.asc().nullsfirst(), ProcessingJob.id.asc(),
        ),
    }
    kinds = {
        "TRANSCRIBE": (ProcessingJob.kind == "TRANSCRIBE"),
        "SUMMARIZE": ProcessingJob.kind.in_(("SUMMARIZE_CHUNKS", "SUMMARIZE_FINAL")),
        "CHAT_REPLAY": (ProcessingJob.kind == "CHAT_REPLAY"),
    }
    for lane, order in lane_orders.items():
        ids = db.scalars(select(ProcessingJob.id).where(kinds[lane], eligible).order_by(*order)).all()
        positions.update({job_id: index for index, job_id in enumerate(ids, start=1)})
    return positions


def _counts(db: Session) -> ProcessingJobCountsOut:
    total = db.scalar(select(func.count(ProcessingJob.id))) or 0
    by_kind: dict[str, dict[str, int]] = {}
    by_status: dict[str, int] = {}
    for kind, status, count in db.execute(
        select(ProcessingJob.kind, ProcessingJob.status, func.count(ProcessingJob.id))
        .group_by(ProcessingJob.kind, ProcessingJob.status)
    ):
        by_kind.setdefault(kind, {})[status] = count
        by_status[status] = by_status.get(status, 0) + count
    return ProcessingJobCountsOut(total=total, by_kind=by_kind, by_status=by_status)


def _transcript_chunks(db: Session, video_id: int):
    return [
        {"start": row.start, "end": row.end, "text": row.text}
        for row in db.scalars(
            select(TranscriptSegment).where(
                TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False)
            ).order_by(TranscriptSegment.start)
        ).all()
    ]


def _job_chunks(db: Session, job: ProcessingJob, video: Video, *, include_summaries=False,
                source_segments=None):
    payload = job.payload or {}
    if job.kind == "SUMMARIZE_CHUNKS":
        from ..services.summarization import cache_key, format_transcript, split_transcript

        segments = source_segments if source_segments is not None else _transcript_chunks(db, video.id)
        chunk_minutes = max(1, int(payload.get("chunk_minutes") or 30))
        source_chunks = split_transcript(segments, chunk_minutes)
        saved = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
        declared_total = payload.get("total_chunks")
        total = max(len(source_chunks), len(saved), int(declared_total or 0))
        if not total:
            return [], None
        done = min(total, max(0, int(payload.get("next_chunk") or 0)))
        if job.status == "completed":
            done = total
        output = []
        for index in range(total):
            saved_item = saved[index] if index < len(saved) and isinstance(saved[index], dict) else {}
            if index < len(source_chunks) and source_chunks[index]:
                start = float(source_chunks[index][0]["start"])
                end = float(source_chunks[index][-1]["end"])
            elif saved_item.get("start") is not None and saved_item.get("end") is not None:
                start, end = float(saved_item["start"]), float(saved_item["end"])
            else:
                start = float(index * chunk_minutes * 60)
                end = max(start, min(float(video.original_duration or start + chunk_minutes * 60),
                                     start + chunk_minutes * 60))
            state = "done" if index < done else (
                "current" if job.status == "processing" and index == done else "pending"
            )
            summary = saved_item.get("summary") if isinstance(saved_item.get("summary"), str) else None
            if include_summaries and summary is None and index < len(source_chunks) and source_chunks[index]:
                transcript = format_transcript(source_chunks[index])
                key = cache_key(transcript, str(payload.get("model") or ""))
                cached = db.get(SummaryCache, key)
                result = cached.result if cached else None
                if isinstance(result, dict) and isinstance(result.get("summary"), str):
                    summary = result["summary"]
            output.append(ProcessingChunkOut(index=index, start=start, end=end, state=state, summary=summary))
        focus = output[done] if done < total and job.status in (
            "pending", "processing", "retry_wait", "failed"
        ) else None
        return output, {"done": done, "total": total, "current": focus}

    if job.kind == "TRANSCRIBE":
        chunk_seconds = max(1, int(payload.get("chunk_seconds") or 600))
        duration = float(video.original_duration or 0)
        total = int(payload.get("total_chunks") or (math.ceil(duration / chunk_seconds) if duration > 0 else 0))
        if not total:
            return [], None
        done = min(total, max(0, int(payload.get("completed_chunks") or 0)))
        if job.status == "completed":
            done = total
        output = []
        for index in range(total):
            start = float(index * chunk_seconds)
            end = max(start, min(duration, start + chunk_seconds)) if duration > 0 else start + chunk_seconds
            state = "done" if index < done else (
                "current" if job.status == "processing" and index == done else "pending"
            )
            output.append(ProcessingChunkOut(index=index, start=start, end=end, state=state))
        focus = output[done] if done < total and job.status in (
            "pending", "processing", "retry_wait", "failed"
        ) else None
        return output, {"done": done, "total": total, "current": focus}

    if job.kind == "SUMMARIZE_FINAL":
        saved = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
        output = [
            ProcessingChunkOut(
                index=index,
                start=float(chunk["start"]),
                end=float(chunk["end"]),
                state="done",
                summary=chunk.get("summary") if isinstance(chunk.get("summary"), str) else None,
            )
            for index, chunk in enumerate(saved)
            if isinstance(chunk, dict) and chunk.get("start") is not None and chunk.get("end") is not None
        ]
        return output, None

    return [], None


def _job_row(job: ProcessingJob, video: Video, channel: Channel, queue_positions, db: Session,
             *, include_chunks=False, source_segments=None):
    chunks, details = _job_chunks(
        db, job, video, include_summaries=include_chunks, source_segments=source_segments
    )
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "attempts": job.attempts,
        "error": job.error,
        "next_attempt_at": job.next_attempt_at,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "updated_at": job.updated_at,
        "finished_at": job.finished_at,
        "video": ProcessingVideoOut(
            id=video.id, youtube_video_id=video.youtube_video_id, title=video.title,
            channel_name=channel.name,
        ),
        "progress": {"done": details["done"], "total": details["total"]} if details else None,
        "current_chunk": {"start": details["current"].start, "end": details["current"].end}
        if details and details["current"] else None,
        "queue_position": queue_positions.get(job.id),
        "chunks": chunks if include_chunks else None,
    }


@router.get("/processing/jobs", response_model=ProcessingJobPageOut)
def processing_jobs(
    kind: ProcessingKind | None = None,
    status: list[str] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    base = select(ProcessingJob, Video, Channel).join(Video, ProcessingJob.video_id == Video.id).join(
        Channel, Video.channel_id == Channel.id
    )
    filtered = base
    if kind:
        filtered = filtered.where(ProcessingJob.kind == kind)
    if status:
        filtered = filtered.where(ProcessingJob.status.in_(status))
    if q and q.strip():
        term = f"%{q.strip()}%"
        filtered = filtered.where(or_(
            Video.title.ilike(term), Video.youtube_video_id.ilike(term), Channel.name.ilike(term)
        ))
    total = db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    rows = db.execute(
        filtered.order_by(
            case((ProcessingJob.status == "processing", 0), (ProcessingJob.status == "pending", 1),
                 (ProcessingJob.status == "retry_wait", 2), else_=3),
            ProcessingJob.created_at.desc(), ProcessingJob.id.desc(),
        ).offset(offset).limit(limit)
    ).all()
    queue_positions = _queue_positions(db, now())
    summary_video_ids = [
        video.id for job, video, _ in rows if job.kind == "SUMMARIZE_CHUNKS"
    ]
    segments_by_video: dict[int, list[dict]] = {}
    if summary_video_ids:
        segment_rows = db.scalars(select(TranscriptSegment).where(
            TranscriptSegment.video_id.in_(summary_video_ids), TranscriptSegment.raw.is_(False)
        ).order_by(TranscriptSegment.video_id, TranscriptSegment.start)).all()
        for segment in segment_rows:
            segments_by_video.setdefault(segment.video_id, []).append(
                {"start": segment.start, "end": segment.end, "text": segment.text}
            )
    items = [
        _job_row(job, video, channel, queue_positions, db,
                 source_segments=segments_by_video.get(video.id, []))
        for job, video, channel in rows
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit, "counts": _counts(db)}


@router.get("/processing/jobs/{job_id}", response_model=ProcessingJobDetailOut)
def processing_job_detail(job_id: int, db: Session = Depends(get_db)):
    row = db.execute(
        select(ProcessingJob, Video, Channel)
        .join(Video, ProcessingJob.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .where(ProcessingJob.id == job_id)
    ).first()
    if row is None:
        raise HTTPException(404, "処理ジョブがありません")
    job, video, channel = row
    return _job_row(job, video, channel, _queue_positions(db, now()), db, include_chunks=True)
