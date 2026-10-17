import logging
import json
import os
import subprocess
import tempfile
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.orm import object_session
from sqlalchemy.orm.exc import UnmappedInstanceError
from .db import SessionLocal
from .models import Channel, Video, RemoteCheck, AuditLog, Storage, Fingerprint, RecordingRule, now
from .config import settings
from .services.policy import classify_remote, compare_fingerprints, has_prior_successful_check, initial_retention_decision, may_delete
from .services.fingerprints import sample, video_phash, audio_fingerprint, POSITIONS
from .services.ffmpeg import launch_ffmpeg
from .services.youtube import inspect, cookie_args, run_ytdlp, popen_ytdlp, ytdlp_command, classify_ytdlp_error

_manual_video_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="manual-video")
_manual_video_lock = threading.Lock()
_manual_video_inflight: set[int] = set()
from .services.download import downloader_args
from .services.locks import default_locks, recorder_lock, retention_lock
from .services.backfill import media_file
from .services.recovery import classify_recovery, recovery_delay, remove_intermediates_after_success
from .services.recording_rules import select_rule

log = logging.getLogger(__name__)
from .services.recorder import (active_recorders, active_upstreams, active_chats,
    _write_recording_error, _finish_chat_process, format_args, format_unavailable)


def _ingest_live_chat_delta(db, video: Video, target: Path, offsets: dict[str, int]) -> None:
    attempts = (video.raw_metadata or {}).get("attempts", [])
    latest = next((item for item in reversed(attempts) if item.get("profile") == "live"), None)
    if latest:
        from .services.recorder import _ingest_live_chat_delta as ingest_delta
        ingest_delta(db, video, target, latest["stem"], offsets)


def stop_recording(video_id: int, *, user_requested: bool = True) -> bool:
    from .services.recorder import stop_recording as request_stop
    return request_stop(video_id, user_requested=user_requested)


def _protect(db, video: Video, reason: str):
    if video.protected:
        return
    video.protected, video.protection_reason, video.protected_at, video.status = True, reason, now(), "PROTECTED"
    db.add(AuditLog(video_id=video.id, action="PROTECT", reason=reason))


def _inspection(video: Video) -> tuple[str, float | None, str | None, dict | None]:
    try:
        video_id = video.youtube_video_id
        channel = video.channel
        cookie_profile = channel.cookie_profile if channel else None
        try:
            session = object_session(video)
        except UnmappedInstanceError:
            session = None
        if session is not None and session.in_transaction():
            session.commit()
        data = inspect(f"https://www.youtube.com/watch?v={video_id}", cookie_profile)
        privacy = (data.get("availability") or "").lower()
        aliases = {"subscriber_only": "MEMBERS_ONLY", "needs_subscription": "MEMBERS_ONLY",
                   "premium_only": "MEMBERS_ONLY", "needs_auth": "ACCESS_DENIED", "login_required": "ACCESS_DENIED"}
        status = "PUBLIC" if not privacy or privacy in ("public", "unlisted") else aliases.get(privacy, privacy.upper())
        return status, data.get("duration"), None, data
    except Exception as exc:
        classified = classify_ytdlp_error(str(exc))
        if classified in {"PRIVATE", "DELETED", "MEMBERS_ONLY", "UNAVAILABLE"}:
            return classified, None, None, None
        return "UNKNOWN", None, str(exc), None


def _remote_samples(video: Video, duration: float, include_audio: bool) -> list[dict]:
    """Download only six short remote sections, then fingerprint each section."""
    result = []
    with tempfile.TemporaryDirectory(prefix="archiver-fp-") as temp:
        for position in POSITIONS:
            second = duration * position
            start, end = max(0, second - 2), second + 2.5
            output = os.path.join(temp, f"sample-{position}.%(ext)s")
            args = ["--no-warnings", "--download-sections", f"*{start:.3f}-{end:.3f}",
                    "--force-keyframes-at-cuts", "-f", "wv*+wa/w" if include_audio else "wv*/w",
                    "-o", output, *cookie_args(video.channel.cookie_profile),
                    *downloader_args(),
                    f"https://www.youtube.com/watch?v={video.youtube_video_id}"]
            proc = run_ytdlp(args, capture_output=True, timeout=settings.remote_timeout_seconds * 4, check=False)
            media = next((str(p) for p in Path(temp).glob(f"sample-{position}.*") if p.is_file()), None)
            if proc.returncode or not media:
                raise RuntimeError((proc.stderr.decode(errors="replace")[-1000:] if proc.stderr else "remote sample unavailable"))
            center = second - start
            offsets = [center + delta for delta in (-2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2) if center + delta >= 0]
            video_hashes, audio_hashes = [], []
            for offset in offsets:
                try:
                    video_hashes.append(video_phash(media, offset))
                except Exception:
                    continue
                if include_audio:
                    try:
                        audio_hashes.append(audio_fingerprint(media, offset))
                    except Exception:
                        pass
            values = {"position": position, "video_hashes": video_hashes,
                      "audio_hashes": audio_hashes if include_audio else []}
            result.append(values)
    return result


def verify_video(db, video: Video, *, final: bool = False, _lease=None) -> bool:
    """確認を記録し、安全であれば初回候補化/最終削除を処理する。"""
    if video.is_partial:
        return False
    if _lease is None:
        namespace, key = retention_lock(video.youtube_video_id)
        with default_locks.acquire(namespace, key) as lease:
            if not lease.acquired:
                log.info("skip retention for %s: another process owns lock", video.youtube_video_id)
                return False
            return verify_video(db, video, final=final, _lease=lease)
    if not final and video.status == "ACTIVE":
        # Do not mutate the state until the advisory lock is held. A competing
        # worker must leave the row eligible for the process that owns the lock.
        video.status = "VERIFYING_REMOTE"
    # Release the ORM transaction before waiting for yt-dlp.
    video_id = video.id
    db.commit()
    video = db.get(Video, video_id)
    if video is None or video.is_partial or video.protected:
        return False
    status, duration, error, raw = _inspection(video)
    db.refresh(video)
    expected_statuses = ("DELETE_ELIGIBLE",) if final else ("VERIFYING_REMOTE", "ACTIVE")
    if video.protected or video.is_partial or video.status not in expected_statuses:
        return False
    mode = (video.edit_detection_mode or (video.channel.edit_detection_mode if video.channel else "standard")).lower()
    compare_duration = mode != "off"
    decision = classify_remote(status, error, video.original_duration if compare_duration else (video.original_duration or 1),
                               duration if compare_duration else (video.original_duration or 1), settings.duration_tolerance_seconds)
    if decision.status == "DELETE_ELIGIBLE" and mode in ("standard", "strong"):
        try:
            db.commit()
            video = db.get(Video, video_id)
            if video is None or video.is_partial or video.protected:
                return False
            local = [{"position": fp.position, "video_hash": fp.video_hash, "audio_hash": fp.audio_hash}
                     for fp in video.fingerprints]
            db.commit()
            remote = _remote_samples(video, duration, mode == "strong")
            db.refresh(video)
            if video.protected or video.is_partial or video.status not in expected_statuses:
                return False
            decision = compare_fingerprints(mode, local, remote, settings.fingerprint_video_threshold, settings.fingerprint_audio_threshold)
        except Exception as exc:
            error = f"fingerprint check failed: {exc}"
            decision = type(decision)("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")
    changed = decision.reason in ("REMOTE_DURATION_CHANGED", "REMOTE_CONTENT_CHANGED")
    check_error = error or ("remote content check indeterminate" if decision.status == "PROTECTION_PENDING" else None)
    db.add(RemoteCheck(video_id=video.id, remote_status=status, remote_duration=duration,
                       edit_detected=True if changed else (None if decision.status == "PROTECTION_PENDING" else False),
                       error=check_error, raw_metadata=raw))
    video.remote_status = status
    if decision.status == "PROTECTED":
        _protect(db, video, decision.reason or "REMOTE_UNAVAILABLE")
        return False
    if decision.status == "PROTECTION_PENDING":
        video.status = "PROTECTION_PENDING"
        video.protection_reason = "REMOTE_CHECK_FAILED"
        # 失敗は履歴に残すが、試行回数にかかわらず削除経路へ移さない。
        return False
    if not final:
        video.status = "DELETE_ELIGIBLE"
        video.protection_reason = None
        video.deletion_candidate_at = now()
        grace = video.channel.deletion_grace_days if video.channel else 3
        video.deletion_after = now() + timedelta(days=grace)
        return False
    # 2回以上の確認を明示し、以前の成功確認がない動画は削除しない。
    db.flush()
    latest_id = db.scalar(select(RemoteCheck.id).where(RemoteCheck.video_id == video.id)
                          .order_by(RemoteCheck.checked_at.desc(), RemoteCheck.id.desc()).limit(1))
    checks = db.execute(select(RemoteCheck.id, RemoteCheck.remote_status, RemoteCheck.error, RemoteCheck.edit_detected)
                        .where(RemoteCheck.video_id == video.id)).all()
    prior_success = has_prior_successful_check([dict(id=x.id, remote_status=x.remote_status, error=x.error,
        edit_detected=x.edit_detected) for x in checks], latest_id)
    check = may_delete(protected=video.protected, status="DELETE_ELIGIBLE" if video.deletion_candidate_at else video.status, remote_status=status,
        edit_unchanged=decision.status == "DELETE_ELIGIBLE", check_succeeded=error is None,
        retention_expires_at=video.retention_expires_at, grace_until=video.deletion_after, now=now(), is_partial=video.is_partial)
    if not check or not prior_success:
        video.status = "PROTECTION_PENDING"
        return False
    # パスはDBに保存した録画ディレクトリ内に限定する。
    if not video.file_path:
        video.status = "PROTECTION_PENDING"
        return False
    try:
        p = Path(video.file_path).resolve()
        configured_storage = video.storage or (video.channel.storage if video.channel else None)
        root = Path(configured_storage.root_path if configured_storage else settings.storage_root).resolve()
        if root not in p.parents:
            raise ValueError("録画パスが storage root 外です")
        if not _lease.still_owned():
            video.status = "PROTECTION_PENDING"
            log.error("retention lock lost before deleting %s; keeping file", video.youtube_video_id)
            return False
        db.commit()
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    except Exception as exc:
        video.status = "PROTECTION_PENDING"
        log.exception("retention file removal failed: %s", exc)
        return False
    video.status, video.deleted_at = "DELETED_BY_RETENTION", now()
    # The Retention Fail Safe has already authorized removal and the folder has
    # been deleted; drop DB transcript derivatives at the same lifecycle point.
    from .services.derived import clear_transcript_segments
    clear_transcript_segments(db, video)
    db.add(AuditLog(video_id=video.id, action="DELETE", reason="Retention expired",
                    details={"remote": status, "retention_days": video.retention_days}))
    return True


def channel_monitor(background: bool = False):
    from .services.monitor import channel_monitor as run_channel_monitor
    run_channel_monitor(background=background)


def manual_video_worker(background: bool = False):
    """Advance manually-added scheduled videos without requiring channel monitoring."""
    from .services.youtube import inspect, classify_ytdlp_error
    from .services.monitor import _cooldown_active, _apply_rate_limit
    from .services.manual_video import canonical_video_url
    at = now()
    if _cooldown_active(at):
        return
    with SessionLocal() as db:
        candidates = list(db.scalars(select(Video).where(Video.status == "WAITING_LIVE")).all())
        due = []
        for video in candidates:
            metadata = video.raw_metadata or {}
            if metadata.get("manual") is not True or metadata.get("schedule_enabled") is False:
                continue
            next_check = metadata.get("manual_next_check_at")
            if next_check:
                try:
                    parsed = datetime.fromisoformat(next_check.replace("Z", "+00:00"))
                    if parsed.tzinfo is None:
                        parsed = parsed.replace(tzinfo=timezone.utc)
                    if parsed > at:
                        continue
                except (ValueError, AttributeError):
                    pass
            due.append(video.id)
        due = due[:5]
        db.commit()
    for video_pk in due:
        with _manual_video_lock:
            if video_pk in _manual_video_inflight:
                continue
            _manual_video_inflight.add(video_pk)
        future = _manual_video_pool.submit(_inspect_manual_video, video_pk, at)
        if background:
            future.add_done_callback(lambda completed, pk=video_pk: _finish_manual_inspect(pk, completed))
        else:
            try:
                future.result()
            except Exception:
                log.exception("manual video inspection worker failed video_id=%s", video_pk)
            finally:
                with _manual_video_lock:
                    _manual_video_inflight.discard(video_pk)


def _finish_manual_inspect(video_pk: int, future) -> None:
    try:
        future.result()
    except Exception:
        log.exception("manual video inspection worker failed video_id=%s", video_pk)
    finally:
        with _manual_video_lock:
            _manual_video_inflight.discard(video_pk)


def _inspect_manual_video(video_pk: int, at: datetime) -> None:
    from .services.youtube import inspect, classify_ytdlp_error
    from .services.monitor import _apply_rate_limit, _cooldown_active
    from .services.manual_video import canonical_video_url
    if _cooldown_active(now()):
        return
    with SessionLocal() as db:
        video = db.get(Video, video_pk)
        if not video or video.status != "WAITING_LIVE":
            return
        scheduled = video.scheduled_start_at
        metadata = dict(video.raw_metadata or {})
        if scheduled is None:
            interval = timedelta(minutes=10)
        else:
            delta = (scheduled - at).total_seconds()
            interval = (timedelta(minutes=30) if delta > 600 else
                        timedelta(minutes=1) if delta >= -7200 else timedelta(minutes=10))
        metadata["manual_next_check_at"] = (at + interval).isoformat()
        video.raw_metadata = metadata
        video_id = video.youtube_video_id
        channel_id = video.channel.youtube_channel_id if video.channel else "unknown"
        cookie = video.channel.cookie_profile if video.channel else None
        db.commit()
    try:
        result = inspect(canonical_video_url(video_id), cookie)
        state = result.get("live_status")
        failure = ""
    except Exception as exc:
        result = {}
        failure = classify_ytdlp_error(str(exc))
        if failure == "RATE_LIMITED":
            _apply_rate_limit(at, channel_id, video_id)
            return
        state = None
    with SessionLocal() as db:
        video = db.get(Video, video_pk)
        if not video or video.status != "WAITING_LIVE":
            return
        metadata = dict(video.raw_metadata or {})
        if state == "is_live":
            video.status = "DISCOVERED"
            metadata["live_status"] = "is_live"
            log.info("manual video is live; queued recording youtube_video_id=%s channel_id=%s",
                     video.youtube_video_id, channel_id)
        elif state in ("was_live", "post_live"):
            video.status = "DISCOVERED"
            metadata["live_status"] = "was_live"
        elif state == "is_upcoming":
            timestamp = result.get("release_timestamp") or result.get("timestamp")
            if timestamp:
                video.scheduled_start_at = datetime.fromtimestamp(timestamp, timezone.utc)
            log.debug("manual video remains scheduled youtube_video_id=%s channel_id=%s",
                      video.youtube_video_id, channel_id)
        elif failure in ("PRIVATE", "DELETED"):
            video.status = "CANCELLED"
            metadata["cancel_reason"] = failure
        if result.get("title"):
            video.title = result["title"]
        video.raw_metadata = metadata
        db.commit()


def recorder_worker():
    """Scheduler entry point: dispatch candidates and return immediately."""
    from .services.recorder import dispatch_recordings
    dispatch_recordings()


def recovery_worker():
    """Reconcile failed/stale rows against YouTube without removing local evidence."""
    _recover_stale_recordings()
    with SessionLocal() as db:
        rows = list(db.scalars(select(Video).where(Video.status == "RECORDING_FAILED")).all())
        db.commit()
        for video in rows:
            if (video.raw_metadata or {}).get("user_stopped") or (video.raw_metadata or {}).get("recovery_exhausted"):
                continue
            if video.next_recovery_at and video.next_recovery_at > now():
                continue
            namespace, key = recorder_lock(video.youtube_video_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    continue
                if (video.recovery_attempts or 0) >= settings.recovery_max_attempts:
                    video.next_recovery_at = None
                    video.raw_metadata = {**(video.raw_metadata or {}), "recovery_exhausted": True}
                    db.commit()
                    continue
                video_id = video.id
                db.commit()
                video = db.get(Video, video_id)
                if video is None:
                    continue
                status, _, error, metadata = _inspection(video)
                decision = classify_recovery(status, error, metadata, video.recovery_attempts or 0)
                video.recovery_attempts = (video.recovery_attempts or 0) + 1
                if decision.action == "PROTECT":
                    video.is_partial = True
                    video.protected = True
                    video.protection_reason = decision.reason
                    video.protected_at = now()
                    video.status = "PROTECTED"
                    video.next_recovery_at = None
                    db.add(AuditLog(video_id=video.id, action="PROTECT_PARTIAL", reason=decision.reason,
                                    details={"recovery_attempts": video.recovery_attempts}))
                elif decision.action in ("RESUME_LIVE", "RETRIEVE_ARCHIVE"):
                    video.is_partial = bool(video.file_path)
                    video.raw_metadata = {**(video.raw_metadata or {}), "live_status": "is_live" if decision.action == "RESUME_LIVE" else "was_live"}
                    video.status = "DISCOVERED"
                    video.next_recovery_at = None
                    db.add(AuditLog(video_id=video.id, action="RECOVERY_QUEUED", reason=decision.action,
                                    details={"recovery_attempts": video.recovery_attempts}))
                    log.warning("recording recovery queued youtube_video_id=%s channel_id=%s action=%s",
                                video.youtube_video_id,
                                video.channel.youtube_channel_id if video.channel else "unknown",
                                decision.action)
                elif decision.action == "WAIT_LIVE":
                    video.status = "WAITING_LIVE"
                    video.raw_metadata = {**(video.raw_metadata or {}), "live_status": "is_upcoming"}
                    video.next_recovery_at = None
                else:
                    video.next_recovery_at = now() + recovery_delay(video.recovery_attempts)
                    db.add(AuditLog(video_id=video.id, action="RECOVERY_DEFERRED", reason=decision.reason,
                                    details={"recovery_attempts": video.recovery_attempts}))
                db.commit()


def _recover_stale_recordings() -> None:
    stale_before = now() - timedelta(seconds=90)
    with SessionLocal() as db:
        stale_rows = db.scalars(select(Video).where(Video.status == "RECORDING",
            (Video.recording_heartbeat_at.is_(None)) | (Video.recording_heartbeat_at < stale_before))).all()
        db.commit()
        for stale in stale_rows:
            namespace, key = recorder_lock(stale.youtube_video_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    continue
                metadata = stale.raw_metadata or {}
                attempts = metadata.get("attempts") or []
                latest_live = next((item for item in reversed(attempts) if item.get("profile") == "live"), None)
                if stale.file_path and latest_live:
                    from .services.recovery import salvage_live_part
                    try:
                        salvage_live_part(Path(stale.file_path), latest_live.get("stem") or "video",
                            lambda path: db.add(AuditLog(video_id=stale.id, action="SALVAGE_PARTIAL",
                                reason="VERIFIED_DURATION", details={"path": path})), ffmpeg_path=settings.ffmpeg_path)
                    except Exception as exc:
                        log.warning("stale recording salvage failed for %s: %s", stale.youtube_video_id, exc)
                if latest_live and latest_live.get("outcome") == "running":
                    from .services.recorder import finish_attempt
                    stale.raw_metadata = finish_attempt(stale.raw_metadata or {}, latest_live.get("stem") or "video",
                                                        "live", "interrupted", now())
                stale.status = "RECORDING_FAILED"
                stale.recording_finished_at = None
                stale.recording_heartbeat_at = None
                stale.next_recovery_at = now()
                db.commit()
                log.warning("stale recording detected youtube_video_id=%s channel_id=%s",
                            stale.youtube_video_id,
                            stale.channel.youtube_channel_id if stale.channel else "unknown")

def retention_worker():
    with SessionLocal() as db:
        ids = db.scalars(select(Video.id).where(Video.protected.is_(False), Video.status.in_(["ACTIVE", "DELETE_ELIGIBLE", "VERIFYING_REMOTE"]))).all()
    for video_id in ids:
        with SessionLocal() as db:
            try:
                v = db.get(Video, video_id)
                if not v or v.is_partial:
                    continue
                if v.channel and v.channel.always_protect:
                    _protect(db, v, "MANUAL")
                elif initial_retention_decision(protected=v.protected, finished_at=v.recording_finished_at,
                        expires_at=v.retention_expires_at, now=now(), is_partial=v.is_partial) == "VERIFY_REMOTE":
                    final = v.status == "DELETE_ELIGIBLE" and v.deletion_after is not None and now() >= v.deletion_after
                    if final:
                        verify_video(db, v, final=True)
                    elif v.status in ("ACTIVE", "VERIFYING_REMOTE"):
                        verify_video(db, v)
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("retention failed for video %s: %s", video_id, exc)


def derived_cleanup_worker():
    try:
        from .services.image_cache import cleanup_thumbnails
        cleanup_thumbnails(Path(settings.data_dir) / "thumbnails", 60 * 24 * 60 * 60)
    except Exception as exc:
        log.warning("thumbnail cache cleanup failed: %s", exc)
    if settings.playable_cache_days <= 0:
        return
    from .services.media import source_media
    cutoff = (now() - timedelta(days=settings.playable_cache_days)).timestamp()
    with SessionLocal() as db:
        rows = db.execute(select(Video.id, Video.file_path).where(Video.file_path.is_not(None))).all()
    # Walk the storage without an open transaction; a slow share must not hit the idle timeout.
    removed: list[tuple[int, str, int]] = []
    for video_id, file_path in rows:
        try:
            directory = Path(file_path)
            source = source_media(directory)
            if not source or not source.is_file() or source.stat().st_size <= 0:
                continue
            for suffix in (".playable.mp4", ".h264.mp4"):
                for derived in directory.glob(f"*{suffix}"):
                    if not derived.is_file() or derived.stat().st_atime >= cutoff:
                        continue
                    size = derived.stat().st_size
                    derived.unlink()
                    removed.append((video_id, derived.name, size))
        except OSError:
            continue
    if not removed:
        return
    with SessionLocal() as db:
        db.add_all([
            AuditLog(video_id=video_id, action="DERIVED_CLEANUP", reason="PLAYABLE_CACHE_EXPIRED",
                     details={"path": name, "size": size})
            for video_id, name, size in removed
        ])
        db.commit()


def protection_worker():
    with SessionLocal() as db:
        ids = db.scalars(select(Video.id).where(Video.protected.is_(False), Video.status == "PROTECTION_PENDING")).all()
    for video_id in ids:
        with SessionLocal() as db:
            try:
                v = db.get(Video, video_id)
                if v:
                    verify_video(db, v, final=bool(v.deletion_candidate_at and v.deletion_after and now() >= v.deletion_after))
                db.commit()
            except Exception as exc:
                db.rollback()
                log.warning("protection retry failed for video %s: %s", video_id, exc)


def integrity_worker():
    """Check a bounded batch only when its configured storage root is reachable."""
    from sqlalchemy import nulls_first
    with SessionLocal() as db:
        videos = db.scalars(select(Video).where(Video.status == "ACTIVE").order_by(
            nulls_first(Video.integrity_checked_at), Video.id).limit(settings.integrity_batch_size)).all()
        for video in videos:
            try:
                storage = video.storage or (video.channel.storage if video.channel else None)
                root = Path(storage.root_path if storage else settings.storage_root)
                if not root.is_dir():
                    continue
                list(root.iterdir())
                directory = Path(video.file_path) if video.file_path else None
                media = media_file(directory) if directory and directory.is_dir() else None
                video.integrity_checked_at = now()
                if media and media.is_file() and media.stat().st_size > 0:
                    db.commit()
                    continue
                video.status = "RECORDING_FAILED"
                video.next_recovery_at = None
                db.add(AuditLog(video_id=video.id, action="MEDIA_MISSING", reason="INTEGRITY_CHECK"))
                db.commit()
            except (OSError, PermissionError):
                db.rollback()
            except Exception as exc:
                db.rollback()
                log.warning("integrity check failed for video %s: %s", video.id, exc)
