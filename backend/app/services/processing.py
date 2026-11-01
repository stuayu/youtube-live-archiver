"""Database-backed, restartable processing queue."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import logging
import math
import json
import re
from pathlib import Path

from sqlalchemy import case, select

from ..config import settings
from ..db import SessionLocal
from ..models import AppSetting, ProcessingJob, TranscriptSegment, Video, SummaryCache, now
from .ffmpeg import extract_whisper_audio
from .locks import default_locks
from .transcription import preprocess_segments, load_whisper_model, transcribe_audio, to_webvtt

log = logging.getLogger(__name__)


def retry_delay_seconds(attempt: int) -> int:
    return min(86400, 60 * (2 ** max(0, attempt - 1)))

TRANSCRIPTION_DEFAULTS = {
    "transcription_enabled": settings.transcription_enabled, "whisper_model": settings.whisper_model, "whisper_device": settings.whisper_device,
    "whisper_compute_type": settings.whisper_compute_type, "whisper_language": settings.whisper_language,
    "transcription_max_concurrency": settings.transcription_max_concurrency,
}
SUMMARY_DEFAULTS = {"summary_enabled": settings.summary_enabled, "openrouter_api_key": "", "openrouter_api_key_set": bool(settings.openrouter_api_key),
    "openrouter_model": settings.openrouter_model, "openrouter_base_url": settings.openrouter_base_url,
    "summary_chunk_minutes": settings.summary_chunk_minutes, "openrouter_max_concurrency": settings.openrouter_max_concurrency,
    "openrouter_max_attempts": settings.openrouter_max_attempts}


def effective_settings(db):
    result = dict(TRANSCRIPTION_DEFAULTS)
    for row in db.scalars(select(AppSetting)).all():
        if row.key in result:
            result[row.key] = row.value.get("value")
    return result

def effective_summary_settings(db):
    result = dict(SUMMARY_DEFAULTS)
    for row in db.scalars(select(AppSetting)).all():
        if row.key in result and row.key != "openrouter_api_key": result[row.key] = row.value.get("value")
        elif row.key == "openrouter_api_key": result["openrouter_api_key_set"] = bool(row.value.get("value"))
    result["openrouter_api_key"] = ""  # secrets are never returned
    return result

def enqueue_summary(db, video_id, *, force=False, final_only=False, model=None):
    from .summarization import split_transcript
    video = db.get(Video, video_id)
    opts = effective_summary_settings(db)
    secret_row = db.get(AppSetting, "openrouter_api_key")
    key = settings.openrouter_api_key or (secret_row.value.get("value", "") if secret_row else "")
    if not opts["summary_enabled"] or not key or not (model or opts["openrouter_model"]):
        video.summary_status = "disabled"; db.commit(); return None
    if video.transcription_status != "completed": return None
    if not final_only:
        existing = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == "SUMMARIZE_CHUNKS"))
        if existing and existing.status in ("pending", "processing", "retry_wait") and not force: return existing
        if existing:
            existing.status, existing.attempts, existing.error, existing.next_attempt_at = "pending", 0, None, None
            existing.payload = {"model": model or opts["openrouter_model"], "chunks": [], "next_chunk": 0}
        else:
            existing = ProcessingJob(video_id=video_id, kind="SUMMARIZE_CHUNKS", status="pending", attempts=0, payload={"model": model or opts["openrouter_model"], "chunks": [], "next_chunk": 0})
            db.add(existing)
    else:
        existing = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == "SUMMARIZE_FINAL"))
        if not existing:
            existing = ProcessingJob(video_id=video_id, kind="SUMMARIZE_FINAL", status="pending", attempts=0, payload={"model": model or opts["openrouter_model"]}); db.add(existing)
        else: existing.status, existing.attempts, existing.payload, existing.error = "pending", 0, {"model": model or opts["openrouter_model"]}, None
    video.summary_status = "pending"; video.summary_error = None; db.commit(); return existing


def enqueue_transcription(db, video_id: int, *, force=False):
    video = db.get(Video, video_id)
    if not video or not video.file_path:
        return None
    opts = effective_settings(db)
    if not opts["transcription_enabled"]:
        video.transcription_status = "disabled"
        db.commit()
        return None
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == "TRANSCRIBE"))
    if job:
        if job.status in ("pending", "processing") or (job.status == "retry_wait" and not force):
            return job
        if not force and job.status == "completed":
            return job
        job.status, job.attempts, job.error = "pending", 0, None
        job.next_attempt_at, job.finished_at = None, None
        job.payload = {"model": opts["whisper_model"]}
        video.transcription_status = "pending"
    else:
        job = ProcessingJob(video_id=video_id, kind="TRANSCRIBE", status="pending", attempts=0,
                            payload={"model": opts["whisper_model"]}, created_at=now(), updated_at=now())
        db.add(job); video.transcription_status = "pending"
    video.transcription_error = None
    db.commit()
    return job


def _summary_error_message(exc) -> str:
    from .summarization import (ProviderHTTPError, ProviderResponseError, RateLimitError,
                                TruncatedProviderResponseError)
    if isinstance(exc, RateLimitError):
        message = f"プロバイダーの利用上限に達しました。約{max(60, exc.retry_after)}秒後に自動再試行します。"
    elif isinstance(exc, TruncatedProviderResponseError):
        message = ("プロバイダーの応答が出力上限で途中終了しました (finish_reason=length)。"
                   "別のモデルを選ぶか、チャンク長を短くしてください。")
    elif isinstance(exc, ProviderResponseError):
        if exc.finish_reason == "error":
            message = ("プロバイダーが空の応答を返しました (finish_reason=error)。"
                       "モデルの混雑または入力の長さが原因の可能性があります。別のモデルを選ぶか、"
                       "チャンク長を短くしてください。")
        elif "not valid JSON" in str(exc):
            message = "モデルの返答が JSON 形式ではありませんでした。別のモデルをお試しください。"
        elif "missing finish_reason" in str(exc):
            message = "プロバイダー応答に finish_reason がありませんでした。モデルの応答形式を確認してください。"
        elif exc.finish_reason:
            message = (f"プロバイダーが空の応答を返しました (finish_reason={exc.finish_reason[:40]}). "
                       "別のモデルを選ぶか、チャンク長を短くしてください。")
        elif exc.provider_code or exc.provider_message:
            detail = ", ".join(value for value in (
                f"code={exc.provider_code}" if exc.provider_code else None,
                f"message={exc.provider_message}" if exc.provider_message else None) if value)
            message = f"プロバイダーがエラーを返しました ({detail})。時間をおいて再試行してください。"
        else:
            message = ("プロバイダーから有効な応答がありませんでした。モデルの混雑や入力形式を確認し、"
                       "別のモデルもお試しください。")
    elif isinstance(exc, ProviderHTTPError):
        if exc.status_code >= 500:
            message = f"プロバイダー側で一時的な障害が発生しました (HTTP {exc.status_code})。自動再試行します。"
        else:
            message = f"プロバイダーがリクエストを拒否しました (HTTP {exc.status_code})。モデル設定を確認してください。"
    elif isinstance(exc, ValueError):
        detail = str(exc)
        missing = re.search(r"(?:missing|invalid) key '([a-z_]+)'", detail)
        if not missing:
            missing = re.search(r"invalid (topics|keywords|important_moments|summary|topic|moment)", detail)
        if missing:
            message = f"モデルの返答形式で '{missing.group(1)}' が不足または不正です。別のモデルをお試しください。"
        elif "JSON" in detail:
            message = "モデルの返答が JSON 形式ではありませんでした。別のモデルをお試しください。"
        else:
            message = "モデルの返答形式が要約に必要な項目を満たしていません。別のモデルをお試しください。"
    elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        message = "要約プロバイダーへの接続がタイムアウトまたは切断されました。自動再試行します。"
    else:
        message = "要約プロバイダーで一時的なエラーが発生しました。時間をおいて自動再試行します。"
    return str(message).replace("\n", " ")[:500]


def _process_transcription(job_id):
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job:
            return
        video = db.get(Video, job.video_id)
        if not video or not video.file_path or video.status.startswith("DELETED"):
            job.status, job.finished_at = "disabled", now(); db.commit(); return
        opts = effective_settings(db)
        video.transcription_status = "processing"; video.transcription_started_at = now(); video.transcription_error = None
        job.status = "processing"; job.attempts += 1; job.error = None
        job.next_attempt_at = None; job.finished_at = None
        job.started_at = now(); job.updated_at = now(); db.commit()
        source_dir = Path(video.file_path)
        from .backfill import media_file
        source = media_file(source_dir) if source_dir.is_dir() else source_dir
        if not source or not source.is_file():
            raise FileNotFoundError("recording media is unavailable")
        # Persist each ten-minute chunk before advancing the durable cursor. A
        # restarted worker repeats at most the in-flight chunk.
        chunk_seconds = 600
        total_seconds = float(video.original_duration or 0)
        total_chunks = max(1, math.ceil(total_seconds / chunk_seconds)) if total_seconds > 0 else 1
        payload = dict(job.payload or {})
        completed_chunks = min(total_chunks, max(0, int(payload.get("completed_chunks", 0))))
        if completed_chunks == 0:
            db.query(TranscriptSegment).filter_by(video_id=video.id).delete(synchronize_session=False)
        job.payload = {**payload, "model": opts["whisper_model"], "completed_chunks": completed_chunks,
                       "total_chunks": total_chunks, "chunk_seconds": chunk_seconds}
        db.commit()
        model = load_whisper_model(model_name=opts["whisper_model"], device=opts["whisper_device"],
                                   compute_type=opts["whisper_compute_type"])
        language = None
        for chunk in range(completed_chunks, total_chunks):
            start = chunk * chunk_seconds
            length = min(chunk_seconds, total_seconds - start) if total_seconds > 0 else None
            audio = extract_whisper_audio(source, start_seconds=start, duration_seconds=length)
            try:
                raw, detected_language = transcribe_audio(model, audio, language=opts["whisper_language"], timestamp_offset=start)
            finally:
                audio.unlink(missing_ok=True)
            language = detected_language or language
            clean = preprocess_segments(raw)
            # Replace only this chunk's derived rows, then checkpoint atomically.
            lower, upper = start, (start + length) if length is not None else float("inf")
            db.query(TranscriptSegment).filter(TranscriptSegment.video_id == video.id,
                TranscriptSegment.start >= lower, TranscriptSegment.start < upper).delete(synchronize_session=False)
            for is_raw, rows in ((True, raw), (False, clean)):
                db.add_all([TranscriptSegment(video_id=video.id, start=float(x["start"]), end=float(x["end"]), text=str(x["text"]), raw=is_raw) for x in rows])
            completed_chunks = chunk + 1
            job.payload = {**(job.payload or {}), "completed_chunks": completed_chunks,
                           "total_chunks": total_chunks, "language": language}
            job.updated_at = now()
            db.commit()
        clean = [{"start": row.start, "end": row.end, "text": row.text} for row in db.scalars(
            select(TranscriptSegment).where(TranscriptSegment.video_id == video.id, TranscriptSegment.raw.is_(False))
            .order_by(TranscriptSegment.start)).all()]
        result = {"segments": clean}
        if source_dir.is_dir():
            try:
                import json
                temp = source_dir / "transcript.json.tmp"
                temp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                temp.replace(source_dir / "transcript.json")
                vtt = source_dir / "transcript.vtt.tmp"
                vtt.write_text(to_webvtt(clean), encoding="utf-8"); vtt.replace(source_dir / "transcript.vtt")
            except OSError:
                log.warning("transcript sidecar write failed for video %s; database transcript is retained", video.id)
        video.transcription_status = "completed"; video.transcription_model = opts["whisper_model"]
        video.transcription_finished_at = now(); video.transcription_error = None
        job.status, job.error, job.finished_at, job.updated_at = "completed", None, now(), now()
        job.payload = {**(job.payload or {}), "model": opts["whisper_model"], "language": language,
                       "completed_chunks": total_chunks, "segments": len(clean)}
        db.commit()
        try:
            enqueue_summary(db, video.id)
        except Exception as summary_exc:
            log.warning("summary enqueue failed for video %s (%s)", video.id, type(summary_exc).__name__)
    except Exception as exc:
        db.rollback()
        job = db.get(ProcessingJob, job_id)
        if job:
            video = db.get(Video, job.video_id)
            job.error = str(exc)[:2000]; job.updated_at = now()
            if job.attempts >= max(1, settings.processing_max_attempts):
                job.status, job.finished_at = "failed", now()
                if video: video.transcription_status = "failed"; video.transcription_error = str(exc)[:2000]
            else:
                delay = retry_delay_seconds(job.attempts)
                job.status, job.next_attempt_at = "retry_wait", now() + timedelta(seconds=delay)
                if video: video.transcription_status = "retry_wait"; video.transcription_error = str(exc)[:2000]
            db.commit()
        log.warning("transcription job %s failed: %s", job_id, type(exc).__name__)
    finally:
        db.close()

def _process_summary(job_id):
    from .summarization import (split_transcript, format_transcript, call_openrouter, validate_chunk, validate_final,
                                cache_key, chunk_messages, final_messages, intermediate_messages,
                                compact_final_chunks, serialize_final_chunks, FINAL_INPUT_MAX_CHARS,
                                FINAL_MERGE_GROUP_SIZE, assemble_final_from_chunks)
    db = SessionLocal()
    try:
        job = db.get(ProcessingJob, job_id)
        if not job: return
        video = db.get(Video, job.video_id)
        if not video or video.transcription_status != "completed":
            job.status, job.error, job.finished_at = "disabled", "transcript unavailable", now(); db.commit(); return
        opts = effective_summary_settings(db)
        video_id = video.id
        job_kind = job.kind
        secret_row = db.get(AppSetting, "openrouter_api_key")
        api_key = settings.openrouter_api_key or (secret_row.value.get("value", "") if secret_row else "")
        if not opts["summary_enabled"] or not api_key or not (job.payload or {}).get("model"):
            job.status = "disabled"; video.summary_status = "disabled"; job.finished_at = now(); db.commit(); return
        video.summary_status = "processing"; job.status = "processing"; job.attempts += 1
        job.started_at = now(); job.next_attempt_at = None; job.updated_at = now(); db.commit()
        rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.video_id == video_id, TranscriptSegment.raw.is_(False)).order_by(TranscriptSegment.start)).all()
        segments = [{"start": r.start, "end": r.end, "text": r.text} for r in rows]
        model = (job.payload or {}).get("model", opts["openrouter_model"])
        max_attempts = max(1, int(opts["openrouter_max_attempts"]))
        def request(text, validator, build_messages, *, cache_text=None):
            key = cache_key(cache_text if cache_text is not None else text, model)
            cached = db.get(SummaryCache, key)
            if cached:
                return validator(cached.result)
            # プロバイダー呼び出し (最大数分) の間 DB トランザクションを開いたままにしない。
            db.commit()
            # commit() returns the checked-out connection before HTTP I/O; the
            # cache write below checks out a fresh connection after the response.
            result = validator(call_openrouter(build_messages(text), api_key=api_key, model=model,
                base_url=opts["openrouter_base_url"], attempts=1))
            try:
                # Cache is optional; a failed write must not discard the valid result.
                db.merge(SummaryCache(key=key, result=result)); db.commit()
            except Exception:
                db.rollback()
                log.warning("summary cache write failed for video %s", video_id)
            return result
        payload = dict(job.payload or {})
        if job_kind == "SUMMARIZE_CHUNKS":
            # 再開時に区間がずれないよう、開始時のチャンク長を使い続ける。
            chunk_minutes = int(payload.get("chunk_minutes") or opts["summary_chunk_minutes"])
            chunks = split_transcript(segments, chunk_minutes)
            summaries = list(payload.get("chunks", [])); index = int(payload.get("next_chunk", 0))
            payload.update(total_chunks=len(chunks), chunk_minutes=chunk_minutes); job.payload = {**payload}; db.commit()
            while index < len(chunks):
                text = format_transcript(chunks[index])
                result = request(text, validate_chunk, chunk_messages)
                summaries.append({"start": chunks[index][0]["start"], "end": chunks[index][-1]["end"], **result})
                index += 1
                job.payload = {**payload, "chunks": summaries, "next_chunk": index}
                job.updated_at = now()
                if index < len(chunks):
                    # Yield after one provider request so other videos and ready
                    # final summaries can run before this video takes another turn.
                    job.status, job.attempts, job.error, job.next_attempt_at = "pending", 0, None, None
                    video.summary_status, video.summary_error = "pending", None
                    db.commit()
                    return
                db.commit()
            final = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == "SUMMARIZE_FINAL"))
            if not final:
                final = ProcessingJob(video_id=video_id, kind="SUMMARIZE_FINAL", status="pending", attempts=0, payload={"model": model, "chunks": summaries}); db.add(final)
            else: final.status, final.payload = "pending", {"model": model, "chunks": summaries}
            job.status, job.error, job.attempts, job.next_attempt_at, job.finished_at = "completed", None, 0, None, now()
            video.summary_status, video.summary_error = "pending", None
            db.commit(); return
        summaries = payload.get("chunks")
        if not summaries:
            chunks_job = db.scalar(select(ProcessingJob).where(ProcessingJob.video_id == video_id, ProcessingJob.kind == "SUMMARIZE_CHUNKS"))
            summaries = (chunks_job.payload or {}).get("chunks", []) if chunks_job else []

        def yield_final_progress(updated_payload):
            job.payload = updated_payload
            job.status, job.attempts, job.error, job.next_attempt_at = "pending", 0, None, None
            job.updated_at = now()
            video.summary_status, video.summary_error = "pending", None
            db.commit()

        merge_state = payload.get("final_merge")
        if not merge_state:
            merged_chunks = payload.get("merged_chunks")
            final_summaries = compact_final_chunks(merged_chunks) if merged_chunks else compact_final_chunks(summaries)
            compact_text = json.dumps(final_summaries, ensure_ascii=False, separators=(",", ":"))
            if len(compact_text) > FINAL_INPUT_MAX_CHARS:
                merge_state = {"level": 1, "source": final_summaries, "results": [], "next_group": 0}
                payload["final_merge"] = merge_state

        if merge_state:
            source = merge_state.get("source", [])
            group_index = int(merge_state.get("next_group", 0))
            group_start = group_index * FINAL_MERGE_GROUP_SIZE
            group = source[group_start:group_start + FINAL_MERGE_GROUP_SIZE]
            merge_text = json.dumps(group, ensure_ascii=False, separators=(",", ":"))
            result = request(merge_text, validate_chunk, intermediate_messages,
                             cache_text=f"intermediate:{merge_state.get('level', 1)}:{merge_text}")
            numeric_starts = [item["start"] for item in group if isinstance(item.get("start"), (int, float))]
            numeric_ends = [item["end"] for item in group if isinstance(item.get("end"), (int, float))]
            merged = {"start": min(numeric_starts, default=0), "end": max(numeric_ends, default=0), **result}
            results = [*merge_state.get("results", []), merged]
            if group_start + len(group) < len(source):
                payload["final_merge"] = {**merge_state, "results": results, "next_group": group_index + 1}
            else:
                reduced_text = serialize_final_chunks(results)
                if len(reduced_text) > FINAL_INPUT_MAX_CHARS:
                    payload["final_merge"] = {"level": int(merge_state.get("level", 1)) + 1,
                                              "source": compact_final_chunks(results),
                                              "results": [], "next_group": 0}
                else:
                    payload.pop("final_merge", None)
                    payload["merged_chunks"] = compact_final_chunks(results)
            yield_final_progress(payload)
            return

        merged_chunks = payload.get("merged_chunks")
        final_summaries = compact_final_chunks(merged_chunks) if merged_chunks else compact_final_chunks(summaries)
        text = json.dumps(final_summaries, ensure_ascii=False, separators=(",", ":"))
        result = request(text, validate_final, final_messages)
        video.summary_data, video.summary_model, video.summary_status, video.summary_error = result, model, "completed", None
        job.status, job.error, job.attempts, job.next_attempt_at, job.finished_at = "completed", None, 0, None, now()
        job.updated_at = now(); db.commit()
    except Exception as exc:
        db.rollback(); job = db.get(ProcessingJob, job_id)
        if job:
            video = db.get(Video, job.video_id)
            error_message = _summary_error_message(exc)
            job.error = error_message
            max_attempts = max(1, int(effective_summary_settings(db)["openrouter_max_attempts"]))
            if job.attempts >= max_attempts and job.kind == "SUMMARIZE_FINAL":
                fallback_chunks = (job.payload or {}).get("chunks") or []
                if not fallback_chunks:
                    chunks_job = db.scalar(select(ProcessingJob).where(
                        ProcessingJob.video_id == job.video_id, ProcessingJob.kind == "SUMMARIZE_CHUNKS"))
                    fallback_chunks = (chunks_job.payload or {}).get("chunks", []) if chunks_job else []
                if video and fallback_chunks:
                    fallback_note = ("AI による最終統合に失敗したため、チャンク要約を結合して作成しました。"
                                     "内容は自動統合の結果と異なる場合があります。")
                    video.summary_data = assemble_final_from_chunks(fallback_chunks)
                    video.summary_model = (job.payload or {}).get("model") or effective_summary_settings(db)["openrouter_model"]
                    video.summary_status, video.summary_error = "completed", fallback_note
                    job.status, job.error, job.next_attempt_at, job.finished_at = "completed", fallback_note, None, now()
                else:
                    job.status, job.finished_at = "failed", now()
                    if video: video.summary_status, video.summary_error = "failed", error_message
            elif job.attempts >= max_attempts:
                job.status, job.finished_at = "failed", now()
                if video: video.summary_status, video.summary_error = "failed", error_message
            else:
                delay = max(60, min(86400, 60 * (2 ** max(0, job.attempts - 1))))
                delay = max(delay, int(getattr(exc, "retry_after", 0)))
                job.status, job.next_attempt_at = "retry_wait", now() + timedelta(seconds=delay)
                if video: video.summary_status, video.summary_error = "retry_wait", error_message
            db.commit()
        log.warning("summary job %s failed (%s)", job_id, type(exc).__name__)
    finally: db.close()

def _recover_interrupted_summaries() -> None:
    """Requeue summary jobs left in "processing" by a restart (their worker lock is gone).

    Chunk results are stored in the job payload, so the job resumes where it stopped."""
    with SessionLocal() as db:
        interrupted = db.scalars(select(ProcessingJob.id).where(
            ProcessingJob.kind.in_(("SUMMARIZE_CHUNKS", "SUMMARIZE_FINAL")),
            ProcessingJob.status == "processing")).all()
    for job_id in interrupted:
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if not lease.acquired:
                continue  # a live worker owns it
            with SessionLocal() as db:
                job = db.get(ProcessingJob, job_id)
                if job and job.status == "processing":
                    job.status, job.next_attempt_at, job.error = "retry_wait", now(), "worker interrupted; resuming"
                    video = db.get(Video, job.video_id)
                    if video and video.summary_status == "processing":
                        video.summary_status = "pending"
                    db.commit()
                    log.info("resuming interrupted summary job %s for video %s", job_id, job.video_id)


def summary_worker():
    _recover_interrupted_summaries()
    db = SessionLocal()
    try:
        opts = effective_summary_settings(db)
        if not opts["summary_enabled"]:
            queued = db.scalars(select(ProcessingJob).where(ProcessingJob.kind.in_(("SUMMARIZE_CHUNKS", "SUMMARIZE_FINAL")), ProcessingJob.status.in_(("pending", "retry_wait")))).all()
            for job in queued:
                job.status, job.finished_at = "disabled", now()
                video = db.get(Video, job.video_id)
                if video: video.summary_status = "disabled"
            db.commit()
            return
        concurrency = max(1, min(8, int(opts["openrouter_max_concurrency"])))
        due = db.scalars(select(ProcessingJob.id).where(ProcessingJob.kind.in_(("SUMMARIZE_CHUNKS", "SUMMARIZE_FINAL")),
            ((ProcessingJob.status == "pending") | ((ProcessingJob.status == "retry_wait") & (ProcessingJob.next_attempt_at <= now())))).order_by(
                case((ProcessingJob.kind == "SUMMARIZE_FINAL", 0), else_=1),
                ProcessingJob.updated_at.asc(), ProcessingJob.created_at.asc(), ProcessingJob.id.asc()
            ).limit(concurrency)).all()
    finally: db.close()
    def run(job_id):
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if lease.acquired: _process_summary(job_id)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="summary") as pool: list(pool.map(run, due))


_transcription_reconciled_at = None
TRANSCRIPTION_RECONCILE_DAYS = 7


def _reconcile_missing_transcriptions() -> None:
    """Queue recent finished recordings whose TRANSCRIBE job was never created.

    Recording completion swallows enqueue errors, which used to leave the row at
    "pending" forever. Limited to recent recordings so old archives are not
    suddenly transcribed in bulk."""
    global _transcription_reconciled_at
    from datetime import timedelta
    current = now()
    if _transcription_reconciled_at and current - _transcription_reconciled_at < timedelta(minutes=10):
        return
    _transcription_reconciled_at = current
    with SessionLocal() as db:
        has_job = select(ProcessingJob.id).where(ProcessingJob.video_id == Video.id,
                                                  ProcessingJob.kind == "TRANSCRIBE").exists()
        missing = db.scalars(select(Video.id).where(
            Video.status.in_(("ACTIVE", "PROTECTED")), Video.file_path.is_not(None),
            Video.transcription_status == "pending",
            Video.recording_finished_at >= current - timedelta(days=TRANSCRIPTION_RECONCILE_DAYS),
            ~has_job).limit(20)).all()
        for video_id in missing:
            try:
                if enqueue_transcription(db, video_id):
                    log.info("queued missing transcription for video %s", video_id)
            except Exception:
                db.rollback()
                log.exception("could not queue missing transcription for video %s", video_id)


def processing_worker():
    """Claim due jobs; pg advisory locks make multiple app processes safe."""
    _reconcile_missing_transcriptions()
    db = SessionLocal()
    concurrency = max(1, settings.transcription_max_concurrency)
    try:
        concurrency = max(1, int(effective_settings(db)["transcription_max_concurrency"]))
        interrupted = db.scalars(select(ProcessingJob.id).where(ProcessingJob.kind == "TRANSCRIBE",
            ProcessingJob.status == "processing")).all()
    finally:
        db.close()
    # Lock ownership tells us whether a processing row belongs to a live worker.
    # This recovers promptly after restart without stealing an active instance's job.
    for job_id in interrupted:
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if not lease.acquired:
                continue
            with SessionLocal() as recovery_db:
                job = recovery_db.get(ProcessingJob, job_id)
                if job and job.status == "processing":
                    job.status, job.next_attempt_at, job.error = "retry_wait", now(), "worker interrupted; retrying"
                    video = recovery_db.get(Video, job.video_id)
                    if video and video.transcription_status == "processing":
                        video.transcription_status = "retry_wait"
                    recovery_db.commit()
    # Include newly reset interrupted jobs in this tick.
    db = SessionLocal()
    try:
        due = db.scalars(select(ProcessingJob.id).where(
            ProcessingJob.kind == "TRANSCRIBE",
            ((ProcessingJob.status == "pending") | ((ProcessingJob.status == "retry_wait") & (ProcessingJob.next_attempt_at <= now()))),
        ).order_by(ProcessingJob.created_at.asc(), ProcessingJob.id.asc()).limit(concurrency)).all()
    finally:
        db.close()
    def run(job_id):
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if lease.acquired:
                _process_transcription(job_id)
    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="processing") as pool:
        list(pool.map(run, due))
    # 要約は別ジョブ (summary-queue) で動かし、文字起こしの完了を待たせない。
