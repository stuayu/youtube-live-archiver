import logging
import json
import os
import random
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from sqlalchemy import select
from .db import SessionLocal
from .models import Channel, Video, RemoteCheck, AuditLog, Storage, Fingerprint, RecordingRule, now
from .config import settings
from .services.policy import classify_remote, compare_fingerprints, has_prior_successful_check, initial_retention_decision, may_delete
from .services.fingerprints import sample, video_phash, audio_fingerprint, POSITIONS
from .services.ffmpeg import launch_ffmpeg
from .services.youtube import inspect, channel_scan, cookie_args
from .services.channel_stats import record_snapshot
from .services.download import downloader_args
from .services.locks import default_locks, channel_lock, recorder_lock, retention_lock
from .services.backfill import eligible_backfill_ids
from .services.backfill import media_file
from .services.recovery import classify_recovery, recovery_delay, remove_intermediates_after_success
from .services.recording_rules import select_rule

log = logging.getLogger(__name__)
active_recorders: dict[int, subprocess.Popen] = {}
active_upstreams: dict[int, subprocess.Popen] = {}


def _write_recording_error(target: Path, video_id: str, message: bytes | str | None) -> Path | None:
    """Persist failure diagnostics beside the target's parent, outside cleanup paths."""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        log_path = target.parent / f"{video_id}-recording-error.log"
        if isinstance(message, bytes):
            payload = message[-4000:]
        else:
            payload = (message or "Recording failed without stderr.").encode("utf-8", "replace")[-4000:]
        log_path.write_bytes(payload)
        return log_path
    except OSError:
        log.warning("failed to persist recording error log for %s", video_id)
        return None


def _ingest_live_chat_delta(db, video: Video, target: Path, offsets: dict[str, int]) -> None:
    if not video.channel.chat_enabled:
        return
    try:
        from .services.chat import ingest_jsonl_delta
        for path in target.glob("*live_chat*"):
            key = str(path.resolve())
            offsets[key] = ingest_jsonl_delta(db, video.id, path, offsets.get(key, 0))
    except Exception:
        # A partially written or delayed chat export is independent of media recording.
        log.debug("live chat delta not ready for %s", video.youtube_video_id, exc_info=True)


def format_args(quality: str | None) -> list[str]:
    """`-f best` は pre-merged 形式のみを指すため、HLS のライブ配信では
    'Requested format is not available' で失敗する。既定では -f を渡さず
    yt-dlp の自動選択に任せる。"""
    quality = (quality or "").strip()
    if not quality or quality.lower() == "best":
        return []
    return ["-f", quality]


def format_unavailable(stderr: bytes | str | None) -> bool:
    message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    lowered = message.lower()
    return any(x in lowered for x in ("requested format is not available", "requested format not available", "no video formats found", "format is not available"))


def stop_recording(video_id: int) -> bool:
    proc = active_recorders.get(video_id)
    upstream = active_upstreams.get(video_id)
    if not proc and not upstream:
        return False
    for child in (proc, upstream):
        if child and child.poll() is None:
            child.terminate()
    return True


def _protect(db, video: Video, reason: str):
    if video.protected:
        return
    video.protected, video.protection_reason, video.protected_at, video.status = True, reason, now(), "PROTECTED"
    db.add(AuditLog(video_id=video.id, action="PROTECT", reason=reason))


def _inspection(video: Video) -> tuple[str, float | None, str | None, dict | None]:
    try:
        data = inspect(f"https://www.youtube.com/watch?v={video.youtube_video_id}", video.channel.cookie_profile if video.channel else None)
        privacy = (data.get("availability") or "").lower()
        aliases = {"subscriber_only": "MEMBERS_ONLY", "needs_subscription": "MEMBERS_ONLY",
                   "premium_only": "MEMBERS_ONLY", "needs_auth": "ACCESS_DENIED", "login_required": "ACCESS_DENIED"}
        status = "PUBLIC" if not privacy or privacy in ("public", "unlisted") else aliases.get(privacy, privacy.upper())
        return status, data.get("duration"), None, data
    except Exception as exc:
        return "UNKNOWN", None, str(exc), None


def _remote_samples(video: Video, duration: float, include_audio: bool) -> list[dict]:
    """Download only six short remote sections, then fingerprint each section."""
    result = []
    with tempfile.TemporaryDirectory(prefix="archiver-fp-") as temp:
        for position in POSITIONS:
            second = duration * position
            start, end = max(0, second - 2), second + 3
            output = os.path.join(temp, f"sample-{position}.%(ext)s")
            args = [settings.yt_dlp_path, "--no-warnings", "--download-sections", f"*{start:.3f}-{end:.3f}",
                    "--force-keyframes-at-cuts", "-o", output, *cookie_args(video.channel.cookie_profile),
                    *downloader_args(),
                    f"https://www.youtube.com/watch?v={video.youtube_video_id}"]
            proc = subprocess.run(args, capture_output=True, timeout=settings.remote_timeout_seconds * 4, check=False)
            media = next((str(p) for p in Path(temp).glob(f"sample-{position}.*") if p.is_file()), None)
            if proc.returncode or not media:
                raise RuntimeError((proc.stderr.decode(errors="replace")[-1000:] if proc.stderr else "remote sample unavailable"))
            offset = second - start
            values = {"position": position, "video_hash": video_phash(media, offset),
                      "audio_hash": audio_fingerprint(media, offset) if include_audio else None}
            result.append(values)
    return result


def verify_video(db, video: Video, *, final: bool = False, _lease=None) -> bool:
    """確認を記録し、安全であれば初回候補化/最終削除を処理する。"""
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
    status, duration, error, raw = _inspection(video)
    mode = (video.edit_detection_mode or (video.channel.edit_detection_mode if video.channel else "standard")).lower()
    compare_duration = mode != "off"
    decision = classify_remote(status, error, video.original_duration if compare_duration else (video.original_duration or 1),
                               duration if compare_duration else (video.original_duration or 1), settings.duration_tolerance_seconds)
    if decision.status == "DELETE_ELIGIBLE" and mode in ("standard", "strong"):
        try:
            local = [{"position": fp.position, "video_hash": fp.video_hash, "audio_hash": fp.audio_hash}
                     for fp in video.fingerprints]
            remote = _remote_samples(video, duration, mode == "strong")
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
        retention_expires_at=video.retention_expires_at, grace_until=video.deletion_after, now=now())
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


def channel_monitor():
    with SessionLocal() as db:
        channels = db.scalars(select(Channel).where(Channel.enabled.is_(True))).all()
        for ch in channels:
            namespace, key = channel_lock(ch.youtube_channel_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    log.info("skip channel %s: another process owns lock", ch.youtube_channel_id)
                    continue
                _monitor_one_channel(db, ch, lease)


def _monitor_one_channel(db, ch, lease):
            if ch.next_check_at is None:
                initial_interval = 1800 if ch.poll_interval == "AUTO" else int(ch.poll_interval) * 60
                ch.next_check_at = now() + timedelta(seconds=random.uniform(0, initial_interval))
                if not lease.still_owned():
                    db.rollback()
                    return
                db.commit()
                return
            if ch.next_check_at and ch.next_check_at > now():
                return
            try:
                entries, channel_data = channel_scan(ch.url, ch.cookie_profile, max(10, ch.backfill_limit + 10))
                record_snapshot(db, ch, channel_data)
                for attr, keys in (("subscriber_count", ("channel_follower_count", "subscriber_count")),
                                   ("video_count", ("channel_video_count", "video_count")),
                                   ("view_count", ("channel_view_count", "view_count"))):
                    value = next((channel_data.get(key) for key in keys if channel_data.get(key) is not None), None)
                    if value is not None:
                        setattr(ch, attr, int(value))
                allowed_past = eligible_backfill_ids(entries, ch.backfill_limit, ch.created_at)
                for entry in entries:
                    vid = entry.get("id")
                    if not vid:
                        continue
                    state = entry.get("live_status")
                    existing = db.scalar(select(Video).where(Video.youtube_video_id == vid))
                    if existing:
                        if existing.status == "WAITING_LIVE" and state in ("is_live", "was_live"):
                            if (existing.raw_metadata or {}).get("schedule_enabled", True):
                                existing.status = "DISCOVERED"
                            existing.raw_metadata = {**(existing.raw_metadata or {}), "live_status": state}
                        elif existing.status == "WAITING_LIVE" and state == "is_upcoming":
                            scheduled = entry.get("release_timestamp") or entry.get("scheduled_start_time") or entry.get("timestamp")
                            try: existing.scheduled_start_at = datetime.fromtimestamp(float(scheduled), timezone.utc) if scheduled is not None else existing.scheduled_start_at
                            except (TypeError, ValueError, OSError):
                                try: existing.scheduled_start_at = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")) if scheduled else existing.scheduled_start_at
                                except ValueError: pass
                        continue
                    if state not in ("is_live", "is_upcoming", "was_live"):
                        continue
                    scheduled = entry.get("release_timestamp") or entry.get("scheduled_start_time") or entry.get("timestamp") or entry.get("live_streaming_details", {}).get("scheduled_start_time")
                    try:
                        scheduled_at = datetime.fromtimestamp(float(scheduled), timezone.utc) if scheduled is not None else None
                    except (TypeError, ValueError, OSError):
                        try:
                            scheduled_at = datetime.fromisoformat(str(scheduled).replace("Z", "+00:00")) if scheduled else None
                            if scheduled_at and scheduled_at.tzinfo is None: scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
                        except ValueError: scheduled_at = None
                    eligible = state != "was_live" or str(vid) in allowed_past
                    db.add(Video(youtube_video_id=vid, channel_id=ch.id, title=entry.get("title") or "",
                                 thumbnail_url=entry.get("thumbnail"), scheduled_start_at=scheduled_at,
                                 status="WAITING_LIVE" if state == "is_upcoming" else "DISCOVERED" if eligible else "SKIPPED",
                                 retention_days=ch.retention_days,
                                 raw_metadata={**{k: entry.get(k) for k in ("release_timestamp", "timestamp", "scheduled_start_time", "url", "duration") if entry.get(k) is not None},
                                               "live_status": state, "backfill_excluded": not eligible}))
                upcoming = next((e for e in entries if e.get("live_status") == "is_upcoming"), None)
                if upcoming:
                    when = upcoming.get("release_timestamp") or upcoming.get("timestamp")
                    try:
                        planned = float(when) if when else now().timestamp() + 3600
                    except (TypeError, ValueError):
                        try: planned = datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp()
                        except ValueError: planned = now().timestamp() + 3600
                    seconds_left = planned - now().timestamp()
                    interval = random.uniform(15, 30) if seconds_left <= 300 else random.uniform(60, 180) if seconds_left <= 1800 else random.uniform(300, 600)
                else:
                    interval = random.uniform(1800, 3600) if ch.poll_interval == "AUTO" else int(ch.poll_interval) * 60
                    if ch.poll_interval != "AUTO":
                        interval *= random.uniform(.9, 1.1)
                if ch.poll_interval != "AUTO":
                    interval = min(interval, int(ch.poll_interval) * 60)
                if not lease.still_owned():
                    db.rollback()
                    return
                ch.next_check_at = now() + timedelta(seconds=interval)
                db.commit()
            except Exception:
                db.rollback()
                log.exception("channel monitor failed for %s", ch.youtube_channel_id)


def recorder_worker():
    """録画候補を保存。ストレージ確認に失敗した候補は開始しない。"""
    import subprocess
    with SessionLocal() as db:
        stale_before = now() - timedelta(seconds=90)
        stale_rows = db.scalars(select(Video).where(Video.status == "RECORDING",
            (Video.recording_heartbeat_at.is_(None)) | (Video.recording_heartbeat_at < stale_before))).all()
        for stale in stale_rows:
            namespace, key = recorder_lock(stale.youtube_video_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    continue
                stale.status = "RECORDING_FAILED"
                stale.recording_finished_at = None
                stale.recording_heartbeat_at = None
                # A stale/failed recording is not proof that its files are disposable.
                # Preserve partial media and metadata for recovery and manual review.
                db.commit()
        videos = db.scalars(select(Video).where(Video.status.in_(["DISCOVERED", "WAITING_FOR_STORAGE"]))).all()
        rules = db.scalars(select(RecordingRule).where(RecordingRule.enabled.is_(True)).order_by(RecordingRule.priority, RecordingRule.id)).all()
        for v in videos:
            namespace, key = recorder_lock(v.youtube_video_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    log.info("skip recording %s: another process owns lock", v.youtube_video_id)
                    continue
                if not lease.still_owned():
                    continue
                ch = v.channel
                selected_rule, rule_result = select_rule(rules, ch.id, v.title or "", str((v.raw_metadata or {}).get("description") or ""))
                if rule_result in ("excluded", "unmatched"):
                    v.status = "SKIPPED"
                    v.raw_metadata = {**(v.raw_metadata or {}), "rule_result": rule_result}
                    db.commit()
                    continue
                if selected_rule:
                    overrides = {}
                    if selected_rule.quality:
                        overrides.update(live_quality=selected_rule.quality, archive_quality=selected_rule.quality)
                    if selected_rule.storage_id is not None:
                        v.storage_id = selected_rule.storage_id
                    if selected_rule.retention_days is not None:
                        v.retention_days = selected_rule.retention_days
                    if selected_rule.edit_detection_mode:
                        v.edit_detection_mode = selected_rule.edit_detection_mode
                    v.raw_metadata = {**(v.raw_metadata or {}), **overrides,
                                      "matched_rule_id": selected_rule.id,
                                      "rule_overrides": {"storage_id": selected_rule.storage_id,
                                                         "retention_days": selected_rule.retention_days,
                                                         "quality": selected_rule.quality,
                                                         "edit_detection_mode": selected_rule.edit_detection_mode}}
                rule_storage_id = (v.raw_metadata or {}).get("rule_overrides", {}).get("storage_id")
                preferred = db.get(Storage, rule_storage_id) if rule_storage_id is not None else (v.storage if v.storage and v.storage.enabled else ch.storage)
                if preferred and not preferred.enabled: preferred = None
                fallback = ch.fallback_storage if ch.fallback_storage and ch.fallback_storage.enabled else None
                default = db.scalar(select(Storage).where(Storage.enabled.is_(True)).order_by(Storage.id))
                candidates = [x for x in (preferred, fallback, default) if x]
                candidates = list({x.id: x for x in candidates}.values())
                storage = None
                for candidate in candidates:
                  try:
                    root = Path(candidate.root_path)
                    root.mkdir(parents=True, exist_ok=True)
                    probe = root / f".write-test-{os.getpid()}"
                    probe.write_text("ok"); probe.unlink()
                    if shutil.disk_usage(root).free < candidate.minimum_free_bytes:
                        raise OSError("minimum free space not met")
                    storage = candidate
                    break
                  except Exception:
                    log.warning("storage unavailable for channel %s: %s", ch.youtube_channel_id, candidate.name)
                if storage is None:
                    v.status = "WAITING_FOR_STORAGE"; db.commit(); continue
                root = Path(storage.root_path)
                lock_lost = False
                chat_offsets: dict[str, int] = {}
                try:
                    target = root / ch.youtube_channel_id / datetime.now().strftime("%Y") / v.youtube_video_id
                    target.mkdir(parents=True, exist_ok=True)
                    v.fingerprints.clear()
                    is_live = (v.raw_metadata or {}).get("live_status") == "is_live"
                    profile = "live" if is_live else "archive"
                    from .services.quality import resolve_quality
                    selector, source = resolve_quality(v, ch, profile,
                        settings.live_quality if is_live else settings.archive_quality)
                    v.format_selector = selector or ""
                    v.format_profile = profile
                    v.format_fallback = False
                    log.info("recording %s profile=%s selector=%r source=%s", v.youtube_video_id, profile, selector, source)
                    v.file_path = str(target); v.storage_id = storage.id; v.recorded_at = now(); v.status = "RECORDING"; v.recording_heartbeat_at = now(); db.commit()
                    output_stem = "video" if not v.is_partial and not v.recovery_attempts else f"video-part-{v.recovery_attempts}"
                    args = [settings.yt_dlp_path, "--newline", "--write-info-json", "--write-thumbnail", "--write-description",
                        *( ["--write-subs", "--sub-langs", "live_chat", "--sub-format", "json"] if ch.chat_enabled else [] ),
                        *cookie_args(ch.cookie_profile), "--merge-output-format", "mkv", *format_args(selector), "-o", str(target / f"{output_stem}.%(ext)s"),
                        *downloader_args(is_live=is_live),
                        f"https://youtube.com/watch?v={v.youtube_video_id}"]
                    hls_dir = (root / "live" / v.youtube_video_id).resolve()
                    tee_enabled = bool(is_live and ch.live_playback_enabled)
                    if tee_enabled:
                        from .services.live_playback import tee_args
                        v.live_hls_path = str(hls_dir)
                        tee_ytdlp = list(args)
                        output_arg = tee_ytdlp.index("-o")
                        tee_ytdlp[output_arg + 1] = "-"
                        tee_ytdlp[output_arg:output_arg] = ["--paths", str(target)]
                        # Tee only the media stream. yt-dlp remains responsible for YouTube URL refresh and metadata.
                        yt_proc = None
                        try:
                            yt_proc = subprocess.Popen(tee_ytdlp, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                            active_upstreams[v.id] = yt_proc
                            ff_proc = launch_ffmpeg(tee_args(settings.ffmpeg_path, hls_dir, target / f"{output_stem}.mkv"),
                                stdin=yt_proc.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                            if yt_proc.stdout: yt_proc.stdout.close()
                            active_recorders[v.id] = ff_proc
                            while True:
                                try:
                                    _, ff_stderr = ff_proc.communicate(timeout=20)
                                    break
                                except subprocess.TimeoutExpired:
                                    if not lease.still_owned():
                                        lock_lost = True
                                        ff_proc.terminate(); yt_proc.terminate()
                                        raise RuntimeError("recorder advisory lock lost during tee recording")
                                    try:
                                        from .services.live_playback import prune_hls
                                        prune_hls(hls_dir, ch.live_playback_limit_bytes)
                                    except Exception:
                                        log.exception("HLS rolling cleanup failed for %s", v.youtube_video_id)
                                    _ingest_live_chat_delta(db, v, target, chat_offsets)
                                    v.recording_heartbeat_at = now(); db.commit()
                            yt_stderr = yt_proc.communicate()[1]
                            active_upstreams.pop(v.id, None)
                            active_recorders.pop(v.id, None)
                            tee_rc = ff_proc.returncode or yt_proc.returncode
                            if tee_rc:
                                from .services.live_playback import fallback_to_direct
                                def record_tee_failure(error, return_code):
                                    db.add(AuditLog(video_id=v.id, action="LIVE_PLAYBACK_FALLBACK", reason="TEE_FAILED",
                                        details={"error": str(error) if error else None, "return_code": return_code,
                                          "ffmpeg": (ff_stderr or b"").decode(errors="replace")[-2000:], "yt_dlp": (yt_stderr or b"").decode(errors="replace")[-2000:]}))
                                v.live_hls_path = None
                                shutil.rmtree(hls_dir, ignore_errors=True)
                                proc = fallback_to_direct(RuntimeError("tee process failed"), tee_rc,
                                    lambda: subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE), record_tee_failure)
                                db.commit()
                            else:
                                proc = ff_proc
                                proc.returncode = 0
                        except Exception as tee_error:
                            log.exception("tee launch failed for %s; falling back to direct yt-dlp", v.youtube_video_id)
                            if yt_proc and yt_proc.poll() is None:
                                yt_proc.terminate()
                                try: yt_proc.wait(timeout=10)
                                except subprocess.TimeoutExpired: yt_proc.kill()
                            active_upstreams.pop(v.id, None)
                            from .services.live_playback import fallback_to_direct
                            proc = fallback_to_direct(tee_error, None,
                                lambda: subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE),
                                lambda error, rc: db.add(AuditLog(video_id=v.id, action="LIVE_PLAYBACK_FALLBACK", reason="TEE_LAUNCH_FAILED", details={"error": str(error)})))
                            v.live_hls_path = None
                            shutil.rmtree(hls_dir, ignore_errors=True)
                            db.commit()
                    else:
                        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                    active_recorders[v.id] = proc
                    stderr = b""
                    while True:
                        if not lease.still_owned():
                            lock_lost = True
                            proc.terminate()
                            raise RuntimeError("recorder advisory lock lost; preserving files")
                        try:
                            _, stderr = proc.communicate(timeout=20)
                            break
                        except subprocess.TimeoutExpired:
                            v.recording_heartbeat_at = now()
                            _ingest_live_chat_delta(db, v, target, chat_offsets)
                            db.commit()
                    active_recorders.pop(v.id, None)
                    if proc.returncode and selector and format_unavailable(stderr):
                        log.warning("format selector unavailable for %s; retrying with yt-dlp automatic selection", v.youtube_video_id)
                        v.format_fallback = True
                        db.commit()
                        retry_args = [a for a in args if a not in ("-f", selector)]
                        proc = subprocess.Popen(retry_args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                        active_recorders[v.id] = proc
                        while True:
                            if not lease.still_owned():
                                lock_lost = True; proc.terminate()
                                raise RuntimeError("recorder advisory lock lost during format fallback")
                            try:
                                _, retry_stderr = proc.communicate(timeout=20)
                                break
                            except subprocess.TimeoutExpired:
                                v.recording_heartbeat_at = now(); db.commit()
                        active_recorders.pop(v.id, None)
                        stderr = (stderr or b"") + b"\nFORMAT FALLBACK to automatic selection\n" + (retry_stderr or b"")
                    if proc.returncode:
                        v.status = "RECORDING_FAILED"; v.recording_finished_at = None
                        v.recording_heartbeat_at = None
                        # Never remove files on a failed recording attempt: a target can
                        # contain a previously completed archive.
                        # 失敗ログは target 削除後も残るよう親ディレクトリへ書く。
                        _write_recording_error(target, v.youtube_video_id, stderr)
                        db.commit(); continue
                    media = media_file(target)
                    if not media or media.stat().st_size <= 0:
                        v.status = "RECORDING_FAILED"; v.recording_finished_at = None
                        v.recording_heartbeat_at = None
                        _write_recording_error(target, v.youtube_video_id,
                            "yt-dlp exited successfully but no non-empty media file was found.\n")
                        db.commit(); continue
                    v.file_path = str(target); v.file_size = media.stat().st_size; v.recording_finished_at = now(); v.recording_heartbeat_at = None
                    try:
                        info_path = target / "video.info.json"
                        if not info_path.is_file():
                            info_candidates = list(target.glob("*.info.json"))
                            info_path = info_candidates[0] if info_candidates else info_path
                        meta = json.loads(info_path.read_text(encoding="utf-8")) if info_path.exists() else inspect(f"https://youtube.com/watch?v={v.youtube_video_id}", ch.cookie_profile)
                    except Exception:
                            # 録画ファイルは残し、duration不明ならFail Safeにより後日の自動削除を許可しない。
                        meta = {}
                    v.original_duration = meta.get("duration")
                    try:
                        live_start = float(meta.get("release_timestamp") or meta.get("release_date") or 0)
                        if live_start > 10_000_000_000: live_start /= 1000
                        if live_start and v.recorded_at:
                            v.raw_metadata = {**(v.raw_metadata or {}), "recording_start_offset_ms": max(0, int((v.recorded_at.timestamp()-live_start)*1000))}
                    except (TypeError, ValueError, OSError):
                        pass
                    v.actual_format = meta.get("format_id") or meta.get("format")
                    v.raw_metadata = {k: meta.get(k) for k in ("title", "description", "thumbnail", "categories", "channel_id", "upload_date") if meta.get(k) is not None}
                    if live_start and v.recorded_at:
                        v.raw_metadata["recording_start_offset_ms"] = max(0, int((v.recorded_at.timestamp()-live_start)*1000))
                    import hashlib
                    digest = hashlib.sha256()
                    with media.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(chunk)
                    (target / "checksum.sha256").write_text(f"{digest.hexdigest()}  {media.name}\n", encoding="utf-8")
                    (target / "recording.json").write_text(__import__("json").dumps({"video_id": v.youtube_video_id,
                            "channel_id": ch.youtube_channel_id, "recorded_at": v.recorded_at.isoformat(),
                            "recording_finished_at": v.recording_finished_at.isoformat(), "original_duration": v.original_duration,
                            "format_profile": v.format_profile, "format_selector": v.format_selector,
                            "format_fallback": v.format_fallback, "actual_format": v.actual_format}, ensure_ascii=False, indent=2), encoding="utf-8")
                    for removed in remove_intermediates_after_success(target, media,
                            lambda name: db.add(AuditLog(video_id=v.id, action="CLEANUP_INTERMEDIATE", reason="VERIFIED_MERGED_MEDIA", details={"path": name}))):
                        log.info("removed verified yt-dlp intermediate for %s: %s", v.youtube_video_id, removed)
                    mode = (ch.edit_detection_mode or "standard").lower()
                    if settings.fingerprint_enabled and mode in ("standard", "strong"):
                        for item in sample(str(media), v.original_duration, include_audio=mode == "strong"):
                            db.add(Fingerprint(video_id=v.id, **item))
                    v.retention_expires_at = (v.recording_finished_at + timedelta(days=v.retention_days)) if v.retention_days is not None else None
                    if not lease.still_owned():
                        lock_lost = True
                        raise RuntimeError("recorder advisory lock lost before completion")
                    # Never mark a missing/empty output as archived.
                    if not media.is_file() or media.stat().st_size <= 0:
                        raise RuntimeError("recording output disappeared or is empty")
                    if v.live_hls_path:
                        try:
                            from .services.live_playback import cleanup_hls
                            if cleanup_hls(v, media):
                                db.add(AuditLog(video_id=v.id, action="LIVE_HLS_CLEANUP", reason="VERIFIED_ARCHIVE"))
                        except Exception:
                            # A sidecar cleanup problem must never alter the verified archive result.
                            log.exception("live HLS cleanup deferred for %s; leaving sidecars in place", v.youtube_video_id)
                    v.status = "ACTIVE"; db.add(AuditLog(video_id=v.id, action="RECORD", reason="Recording completed")); db.commit()
                    # Queue derived transcription only after the recording is committed.
                    # Any queue problem is isolated and cannot change recording success.
                    try:
                        from .services.processing import enqueue_transcription
                        enqueue_transcription(db, v.id)
                    except Exception:
                        db.rollback()
                        log.exception("transcription enqueue failed for %s; recording remains ACTIVE", v.youtube_video_id)
                    if ch.chat_enabled:
                        # Chat replay is optional post-processing: commit ACTIVE first so
                        # a chat DB/network failure cannot mark a complete media file failed.
                        try:
                            from .services.chat import ingest_jsonl, retrieve_replay
                            chat_files = list(target.glob("*live_chat*"))
                            if chat_files:
                                ingest_jsonl(db, v.id, chat_files[0], complete_snapshot=False, isolated=True)
                            retrieve_replay(db, v.id, v.youtube_video_id, ch.cookie_profile)
                            db.commit()
                        except Exception:
                            db.rollback()
                            log.exception("post-record chat processing failed for %s; recording remains ACTIVE", v.youtube_video_id)
                except Exception:
                    # A failed query/commit leaves SQLAlchemy's transaction unusable.
                    # Clear it before updating recovery state; the recording lock remains
                    # the only DB session intentionally held for the recording lifetime.
                    db.rollback()
                    v = db.get(Video, v.id)
                    if v is None:
                        log.exception("recording row disappeared during failure handling")
                        continue
                    v.status = "WAITING_FOR_STORAGE" if not v.file_path else "RECORDING_FAILED"
                    proc = active_recorders.pop(v.id, None)
                    if proc and proc.poll() is None:
                        proc.terminate()
                        try: proc.wait(timeout=10)
                        except subprocess.TimeoutExpired: proc.kill()
                    upstream = active_upstreams.pop(v.id, None)
                    if upstream and upstream.poll() is None:
                        upstream.terminate()
                        try: upstream.wait(timeout=10)
                        except subprocess.TimeoutExpired: upstream.kill()
                    # Intentionally leave file_path and all recording evidence untouched.
                    # A failed or partial recording is never evidence that it is safe to delete.
                    v.recording_finished_at = None
                    v.recording_heartbeat_at = None
                    v.is_partial = bool(v.file_path and Path(v.file_path).is_dir() and any(p.is_file() and p.stat().st_size > 0 for p in Path(v.file_path).glob("*")))
                    v.next_recovery_at = now() + recovery_delay(max(1, v.recovery_attempts))
                    db.commit()
                    log.exception("recording failed for %s", v.youtube_video_id)


def recovery_worker():
    """Reconcile failed/stale rows against YouTube without removing local evidence."""
    with SessionLocal() as db:
        stale_before = now() - timedelta(seconds=90)
        rows = db.scalars(select(Video).where(
            (Video.status == "RECORDING_FAILED") |
            (Video.status == "ACTIVE") |
            ((Video.status == "RECORDING") & ((Video.recording_heartbeat_at.is_(None)) | (Video.recording_heartbeat_at < stale_before)))))
        for video in rows:
            if video.status == "ACTIVE":
                try:
                    good = video.file_path and media_file(Path(video.file_path)) and media_file(Path(video.file_path)).stat().st_size > 0
                except OSError:
                    good = False
                if good:
                    continue
                # Missing or zero-byte output is recoverable. Keep the path and
                # directory as evidence; recorder_worker will retry through DISCOVERED.
                video.status = "RECORDING_FAILED"
                video.is_partial = bool(video.file_path and Path(video.file_path).is_dir())
            if video.next_recovery_at and video.next_recovery_at > now():
                continue
            namespace, key = recorder_lock(video.youtube_video_id)
            with default_locks.acquire(namespace, key) as lease:
                if not lease.acquired:
                    continue
                if video.status == "RECORDING":
                    video.status = "RECORDING_FAILED"
                    video.recording_heartbeat_at = None
                status, _, error, metadata = _inspection(video)
                decision = classify_recovery(status, error, metadata)
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
                else:
                    video.next_recovery_at = now() + recovery_delay(video.recovery_attempts)
                    db.add(AuditLog(video_id=video.id, action="RECOVERY_DEFERRED", reason=decision.reason,
                                    details={"recovery_attempts": video.recovery_attempts}))
                db.commit()

def retention_worker():
    with SessionLocal() as db:
        for v in db.scalars(select(Video).where(Video.protected.is_(False), Video.status.in_(["ACTIVE", "DELETE_ELIGIBLE"]))).all():
            if v.channel and v.channel.always_protect:
                _protect(db, v, "MANUAL")
                continue
            if initial_retention_decision(protected=v.protected, finished_at=v.recording_finished_at,
                    expires_at=v.retention_expires_at, now=now()) != "VERIFY_REMOTE":
                continue
            final = v.status == "DELETE_ELIGIBLE" and v.deletion_after is not None and now() >= v.deletion_after
            if final:
                verify_video(db, v, final=True)
            elif v.status == "ACTIVE":
                verify_video(db, v)
            # pending は別ジョブから再試行する。
        db.commit()


def protection_worker():
    with SessionLocal() as db:
        videos = db.scalars(select(Video).where(Video.protected.is_(False), Video.status == "PROTECTION_PENDING")).all()
        for v in videos:
            verify_video(db, v, final=bool(v.deletion_candidate_at and v.deletion_after and now() >= v.deletion_after))
        db.commit()


def live_detector():
    # Channel monitor owns scheduled UPCOMING checks and promotes the video as
    # soon as channel_entries reports is_live/was_live. Avoid a tight second poller.
    return None


def remote_archive_inspector():
    # Remote 検査は Retention/Protection worker の対象ごとに実施する。
    return None
