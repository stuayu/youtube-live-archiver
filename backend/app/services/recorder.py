"""In-process recorder dispatch and lifecycle registry."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections.abc import Callable
from collections import deque
from dataclasses import dataclass
from typing import Any

import psutil
from sqlalchemy import select
from sqlalchemy.orm import object_session
from sqlalchemy.orm import Session


from ..config import settings
from ..db import SessionLocal
from ..models import AuditLog, Channel, ChatMessage, Fingerprint, RecordingRule, Storage, Video, now
from .backfill import media_file
from .download import downloader_args
from .ffmpeg import launch_ffmpeg
from .fingerprints import audio_fingerprint, video_phash, POSITIONS
from .locks import default_locks, recorder_lock
from .youtube import cookie_args, inspect, popen_ytdlp, ytdlp_command, classify_ytdlp_error
from .recovery import recovery_delay, remove_intermediates_after_success
from .recording_rules import select_rule

log = logging.getLogger(__name__)


_lock = threading.RLock()
_threads: dict[int, tuple[str, threading.Thread]] = {}
_stopping: set[int] = set()
_user_stopping: set[int] = set()
_stop_reasons: dict[int, str] = {}
_chat_targets: dict[int, tuple[Path, str]] = {}
active_recorders: dict[int, subprocess.Popen] = {}
active_upstreams: dict[int, subprocess.Popen] = {}
active_chats: dict[int, subprocess.Popen] = {}
_stderr_tails: dict[int, tuple[deque[bytes], list[int], threading.Thread]] = {}
STDERR_TAIL_LIMIT = 64 * 1024
RECORDER_HEARTBEAT_SECONDS = 20
TEE_YTDLP_EXIT_TIMEOUT_SECONDS = 30
LIVE_END_INSPECT_DELAYS_SECONDS = (10, 30, 60)


def stopping(video_id: int) -> bool:
    with _lock:
        return video_id in _stopping


def mark_stopping(video_id: int, *, user_requested: bool = True, reason: str | None = None) -> None:
    with _lock:
        _stopping.add(video_id)
        if user_requested:
            _user_stopping.add(video_id)
            _stop_reasons[video_id] = "user"
        else:
            _user_stopping.discard(video_id)
            _stop_reasons[video_id] = reason or "shutdown"


def user_stopping(video_id: int) -> bool:
    with _lock:
        return video_id in _user_stopping


def request_process_interrupt(process) -> bool:
    if process is None or process.poll() is not None:
        return False
    try:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            group_id = os.getpgid(process.pid)
            if group_id == process.pid:
                os.killpg(group_id, signal.SIGINT)
            else:
                process.send_signal(signal.SIGINT)
        return True
    except (OSError, AttributeError, ProcessLookupError):
        return False


def _process_tree(process) -> list[psutil.Process]:
    try:
        root = psutil.Process(process.pid)
        return [*root.children(recursive=True), root]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return []


def _terminate_process_tree(process, *, force: bool = False, grace_seconds: float = 5) -> None:
    descendants = _process_tree(process)
    for child in descendants:
        try:
            child.kill() if force else child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    try:
        process.kill() if force else process.terminate()
    except (OSError, ProcessLookupError):
        pass
    _, alive = psutil.wait_procs(descendants, timeout=grace_seconds)
    for child in alive:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if process.poll() is None:
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    psutil.wait_procs(alive, timeout=grace_seconds)


def stop_process(process, *, grace_seconds: float = 5) -> None:
    if process is None:
        return
    descendants = _process_tree(process)[:-1]
    if process.poll() is None:
        request_process_interrupt(process)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            _terminate_process_tree(process, grace_seconds=grace_seconds)
    _, alive = psutil.wait_procs(descendants, timeout=1)
    for child in alive:
        try:
            child.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    _, alive = psutil.wait_procs(alive, timeout=grace_seconds)
    for child in alive:
        try:
            child.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if process.poll() is None:
        _terminate_process_tree(process, force=True, grace_seconds=grace_seconds)


def capture_stderr(process, *, limit: int = STDERR_TAIL_LIMIT) -> None:
    if process.stderr is None:
        return
    chunks: deque[bytes] = deque()
    size = [0]

    def drain() -> None:
        while True:
            chunk = process.stderr.read(8192)
            if not chunk:
                return
            chunks.append(chunk)
            size[0] += len(chunk)
            while size[0] > limit and chunks:
                excess = size[0] - limit
                first = chunks[0]
                if len(first) <= excess:
                    size[0] -= len(chunks.popleft())
                else:
                    chunks[0] = first[excess:]
                    size[0] -= excess

    reader = threading.Thread(target=drain, name=f"stderr-tail-{process.pid}", daemon=True)
    with _lock:
        _stderr_tails[process.pid] = (chunks, size, reader)
    reader.start()


def stderr_tail(process, *, wait: bool = True) -> bytes:
    with _lock:
        state = _stderr_tails.get(process.pid)
    if not state:
        return b""
    chunks, _, reader = state
    if wait:
        reader.join(timeout=2)
    with _lock:
        _stderr_tails.pop(process.pid, None)
    result = b"".join(chunks)[-STDERR_TAIL_LIMIT:]
    if getattr(process, "_youtube_force_ipv6", False):
        from .youtube_ip import note_ipv6_failure
        note_ipv6_failure(result.decode("utf-8", "replace"))
    return result


def popen_recording(args: list[str], **kwargs):
    kwargs["stderr"] = subprocess.PIPE
    process = popen_ytdlp(args, **kwargs)
    capture_stderr(process)
    return process


def launch_recording_ffmpeg(args: list[str], **kwargs):
    kwargs["stderr"] = subprocess.PIPE
    process = launch_ffmpeg(args, **kwargs)
    capture_stderr(process)
    return process


def _chat_file(directory: Path, stem: str) -> Path:
    return directory / f"{stem}.live_chat.json"


def finalize_chat_file(directory: Path, stem: str) -> Path | None:
    final = _chat_file(directory, stem)
    partial = Path(f"{final}.part")
    if not final.exists() and partial.is_file():
        partial.replace(final)
    if not final.is_file():
        return None
    for fragment in directory.glob(f"{final.name}.part-Frag*"):
        if re.fullmatch(re.escape(final.name) + r"\.part-Frag\d+", fragment.name):
            fragment.unlink(missing_ok=True)
    return final


def _ingest_live_chat_delta(db, video: Video, target: Path, stem: str, offsets: dict[str, int]) -> None:
    if not video.channel.chat_enabled:
        return
    path = _chat_file(target, stem)
    if not path.is_file() and Path(f"{path}.part").is_file():
        path = Path(f"{path}.part")
    if not path.is_file():
        return
    try:
        from .chat import ingest_jsonl_delta
        key = str(path.resolve())
        offsets[key] = ingest_jsonl_delta(db, video.id, path, offsets.get(key, 0))
        db.commit()
    except Exception:
        db.rollback()
        log.debug("live chat delta not ready for %s", video.youtube_video_id, exc_info=True)


def _finish_chat_process(video_id: int) -> None:
    chat = active_chats.pop(video_id, None)
    target = _chat_targets.pop(video_id, None)
    if chat is not None and chat.poll() is None:
        try:
            chat.wait(timeout=60)
        except subprocess.TimeoutExpired:
            try:
                stop_process(chat)
            except Exception:
                log.warning("chat process stop failed for video %s", video_id)
    if target:
        try:
            finalize_chat_file(*target)
        except OSError:
            log.warning("chat file finalization failed for video %s", video_id)


def _ingest_chat_snapshot(db: Session, video: Video, target: Path, stem: str) -> None:
    path = _chat_file(target, stem)
    if not path.is_file():
        return
    try:
        from .chat import ingest_jsonl
        ingest_jsonl(db, video.id, path, complete_snapshot=False, isolated=True)
        db.commit()
    except Exception:
        db.rollback()
        log.warning("live chat import failed for %s", video.youtube_video_id)


def _capture_live_from_start_offset(db: Session, video: Video, target: Path, stem: str) -> bool:
    attempts = list((video.raw_metadata or {}).get("attempts", []))
    attempt_index = next((index for index in range(len(attempts) - 1, -1, -1)
                          if attempts[index].get("stem") == stem
                          and attempts[index].get("outcome") == "running"
                          and attempts[index].get("from_start")), None)
    if attempt_index is None:
        return False
    part_path = target / f"{stem}.f134.mkv.part"
    if not part_path.is_file():
        return False
    # ffprobe can wait while the media container is still being written.
    # Keep the DB transaction closed until it returns.
    db.commit()
    from .recovery import probe_start_time
    start_time = probe_start_time(part_path)
    if start_time is None:
        return False
    start_time_ms = int(start_time * 1000) if start_time > 5 else 0
    attempts[attempt_index] = {
        **attempts[attempt_index],
        "start_time_checked": True,
        "part_start_time_ms": start_time_ms,
    }
    video.raw_metadata = {
        **(video.raw_metadata or {}),
        "attempts": attempts,
        "live_from_start_start_time_ms": start_time_ms,
        "recording_start_offset_ms": start_time_ms,
    }
    from .chat import rebase_live_chat_offsets
    rebase_live_chat_offsets(db, video.id)
    db.commit()
    return True


def _captured_live_from_start_offset(metadata: dict, stem: str) -> int | None:
    for attempt in reversed(metadata.get("attempts", [])):
        if attempt.get("stem") == stem and attempt.get("from_start"):
            value = attempt.get("part_start_time_ms")
            if isinstance(value, (int, float)):
                return int(value)
    value = metadata.get("live_from_start_start_time_ms")
    return int(value) if isinstance(value, (int, float)) else None


def _probe_live_from_start_offset(target: Path, stem: str, fallback: Path) -> int:
    from .recovery import probe_start_time
    part_path = target / f"{stem}.f134.mkv.part"
    source = part_path if part_path.is_file() else fallback
    start_time = probe_start_time(source)
    return int(start_time * 1000) if start_time is not None and start_time > 5 else 0


def _wait_for_attempt(db: Session, video: Video, target: Path, stem: str,
                      process: subprocess.Popen, lease: Any,
                      chat_offsets: dict[str, int]) -> bytes:
    last_heartbeat = time.monotonic()
    last_start_time_probe = 0.0
    while process.poll() is None:
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        if process.poll() is not None:
            break
        if not lease.still_owned():
            mark_stopping(video.id, user_requested=False, reason="lock_lost")
            stop_process(process)
            raise RuntimeError("recorder advisory lock lost during recording")
        if stopping(video.id):
            request_process_interrupt(process)
        elapsed = time.monotonic() - last_heartbeat
        if elapsed >= RECORDER_HEARTBEAT_SECONDS:
            try:
                video.recording_heartbeat_at = now()
                _audit_low_disk_space(db, video)
                db.commit()
            except Exception:
                db.rollback()
                log.warning("recording heartbeat write failed for %s; retrying on next interval",
                            video.youtube_video_id)
            last_heartbeat = time.monotonic()
        _ingest_live_chat_delta(db, video, target, stem, chat_offsets)
        if elapsed >= 1 and time.monotonic() - last_start_time_probe >= 1:
            attempt = next((item for item in reversed((video.raw_metadata or {}).get("attempts", []))
                            if item.get("stem") == stem), {})
            if attempt.get("from_start") and not attempt.get("start_time_checked"):
                try:
                    _capture_live_from_start_offset(db, video, target, stem)
                except Exception:
                    db.rollback()
                    log.warning("live start offset probe failed for %s; retrying while recording",
                                video.youtube_video_id)
                last_start_time_probe = time.monotonic()
    return stderr_tail(process)


def _audit_low_disk_space(db: Session, video: Video) -> None:
    storage = db.get(Storage, video.storage_id) if video.storage_id else None
    if storage is None:
        return
    try:
        free_bytes = shutil.disk_usage(storage.root_path).free
    except OSError:
        return
    metadata = video.raw_metadata or {}
    if free_bytes < storage.minimum_free_bytes:
        if not metadata.get("low_disk_space_alerted"):
            db.add(AuditLog(video_id=video.id, action="LOW_DISK_SPACE", reason="MINIMUM_FREE_BYTES",
                            details={"free_bytes": free_bytes,
                                     "minimum_free_bytes": storage.minimum_free_bytes,
                                     "storage_id": storage.id}))
            video.raw_metadata = {**metadata, "low_disk_space_alerted": True}
    elif metadata.get("low_disk_space_alerted"):
        updated = dict(metadata)
        updated.pop("low_disk_space_alerted", None)
        video.raw_metadata = updated


def format_args(quality: str | None) -> list[str]:
    quality = (quality or "").strip()
    if not quality or quality.lower() == "best":
        return []
    return ["-f", quality]


def format_unavailable(stderr: bytes | str | None) -> bool:
    message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    lowered = message.lower()
    return any(value in lowered for value in (
        "requested format is not available", "requested format not available",
        "no video formats found", "format is not available"))


def _write_recording_error(target: Path, video_id: str, message: bytes | str | None) -> Path | None:
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        log_path = target.parent / f"{video_id}-recording-error.log"
        content = message if isinstance(message, bytes) else (message or "Recording failed.").encode("utf-8", "replace")
        log_path.write_bytes(content[-STDERR_TAIL_LIMIT:])
        return log_path
    except OSError:
        log.warning("failed to persist recording error log for %s", video_id)
        return None


def clear_stopping(video_id: int) -> None:
    with _lock:
        _stopping.discard(video_id)
        _user_stopping.discard(video_id)
        _stop_reasons.pop(video_id, None)


def stop_reason(video_id: int) -> str | None:
    with _lock:
        return _stop_reasons.get(video_id)


def classify_attempt_result(exit_code: int | None, media_available: bool,
                            stop_kind: str | None, is_live: bool) -> str:
    if stop_kind == "user":
        return "USER_STOP_MEDIA" if media_available else "USER_STOP_EMPTY"
    if stop_kind in {"shutdown", "lock_lost"}:
        return "INTERRUPTED_RECOVERY"
    if exit_code != 0 or not media_available:
        return "FAILED"
    if is_live:
        return "VERIFY_LIVE"
    return "COMPLETE"


def _has_live_end_data_blocks_error(stderr: bytes | str | None) -> bool:
    message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    return re.search(r"(?im)^ERROR: Did not get any data blocks\s*$", message) is not None


def _retryable_archive_backfill_media(video: Video, target: Path, profile: str,
                                      stderr: bytes | str | None) -> Path | None:
    """Keep a saved live segment queued when yt-dlp loses an archive fragment."""
    message = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    if profile != "archive":
        return None
    if not re.search(r"(?im)^ERROR: Unable to download video: \[Errno 2\] No such file or directory:.*\.part-Frag\d+",
                     message):
        return None
    metadata = video.raw_metadata or {}
    if not any(item.get("profile") == "live" and item.get("outcome") == "success"
               for item in metadata.get("attempts", [])):
        return None
    primary_name = metadata.get("primary_media")
    if not isinstance(primary_name, str):
        return None
    media = target / primary_name
    if not media.is_file() or media.stat().st_size <= 0:
        return None
    from .recovery import probe_duration
    return media if probe_duration(media) else None


def _verified_live_end_media(video: Video, target: Path, stem: str, inspected: dict,
                             media: Path | None, allow_is_live: bool = False) -> Path | None:
    """Accept the observed yt-dlp live-end error only with a full, probeable archive."""
    terminal_statuses = {"post_live", "was_live", "not_live"}
    if allow_is_live:
        terminal_statuses.add("is_live")
    if inspected.get("live_status") not in terminal_statuses:
        return None
    metadata = inspected
    if not metadata.get("duration"):
        metadata = _read_stem_metadata(target, stem)
    try:
        expected = float(metadata.get("duration"))
    except (TypeError, ValueError):
        return None
    if expected <= 0:
        return None
    from_start = any(item.get("stem") == stem and item.get("from_start")
                     for item in (video.raw_metadata or {}).get("attempts", []))
    if not from_start:
        offset_ms = (video.raw_metadata or {}).get("recording_start_offset_ms")
        if not isinstance(offset_ms, (int, float)):
            release = _timestamp_seconds(inspected.get("release_timestamp"))
            if release is not None and video.recorded_at is not None:
                offset_ms = max(0, int((video.recorded_at.timestamp() - release) * 1000))
        if isinstance(offset_ms, (int, float)):
            expected = max(0.0, expected - offset_ms / 1000)
    if media is None or not media.is_file() or media.stat().st_size <= 0:
        return None
    from .recovery import probe_duration
    actual = probe_duration(media)
    tolerance = max(30.0, expected * 0.01)
    if actual is None or abs(actual - expected) > tolerance:
        return None
    return media


def _inspect_live_after_exit(video_id: str, cookie_profile: str | None) -> dict:
    url = f"https://youtube.com/watch?v={video_id}"
    try:
        inspected = inspect(url, cookie_profile)
    except Exception:
        return {}
    for delay in LIVE_END_INSPECT_DELAYS_SECONDS:
        if inspected.get("live_status") != "is_live":
            break
        time.sleep(delay)
        try:
            inspected = inspect(url, cookie_profile)
        except Exception:
            break
    return inspected


def _stem_media_paths(directory: Path, stem: str) -> list[Path]:
    paths = [directory / f"{stem}.{extension}" for extension in ("mkv", "mp4", "ts", "webm", "m4v", "mov")]
    paths.append(directory / f"{stem}.tee.ts")
    paths.extend(directory / f"{stem}.{extension}.part" for extension in ("mkv", "mp4", "ts", "webm"))
    paths.append(directory / f"{stem}.partial.mkv")
    return [path for path in paths if path.is_file() and path.stat().st_size > 0]


def _update_partial_evidence(video: Video, target: Path, stem: str) -> bool:
    from .recovery import probe_duration
    session = object_session(video)
    if session is not None and session.in_transaction():
        session.commit()
    attempts = (video.raw_metadata or {}).get("attempts", [])
    stems = list(dict.fromkeys([item.get("stem") for item in attempts if item.get("stem")] + [stem]))
    media = list(dict.fromkeys(path for item_stem in stems for path in _stem_media_paths(target, item_stem)))
    playable = [path for path in media if not path.name.endswith(".part")]
    if not media:
        return False
    primary = max(playable, key=lambda path: probe_duration(path) or 0) if playable else media[0]
    video.file_size = sum(path.stat().st_size for path in media)
    attempts_by_stem = {item.get("stem"): item for item in attempts if item.get("stem")}
    primary_attempt = attempts_by_stem.get(primary.stem) or attempts_by_stem.get(primary.name.split(".", 1)[0])
    metadata = _read_stem_metadata(target, primary_attempt.get("stem") if primary_attempt else primary.stem)
    started_at = primary_attempt.get("started_at") if primary_attempt else None
    release = metadata.get("release_timestamp")
    try:
        release = float(release) if release is not None else None
        if release and release > 10_000_000_000:
            release /= 1000
    except (TypeError, ValueError, OverflowError):
        release = None
    try:
        start_epoch = datetime.fromisoformat(str(started_at).replace("Z", "+00:00")).timestamp() if started_at else None
    except (TypeError, ValueError, OverflowError):
        start_epoch = None
    from_start = bool(primary_attempt and primary_attempt.get("from_start"))
    if from_start:
        primary_stem = str(primary_attempt.get("stem") or stem)
        offset_ms = _captured_live_from_start_offset(video.raw_metadata or {}, primary_stem)
        if offset_ms is None:
            offset_ms = _probe_live_from_start_offset(target, primary_stem, primary)
    else:
        offset_ms = max(0, int((start_epoch - release) * 1000)) if start_epoch is not None and release else 0
    existing = video.raw_metadata or {}
    video.raw_metadata = {**existing, **{key: metadata[key] for key in ("description", "thumbnail", "categories", "upload_date", "release_timestamp") if metadata.get(key) is not None},
                          "title": metadata.get("fulltitle") or metadata.get("title") or existing.get("title"),
                          "primary_media": primary.name,
                          "local_duration": probe_duration(primary) if not primary.name.endswith(".part") else None,
                          "recording_start_offset_ms": offset_ms}
    if from_start:
        video.raw_metadata["live_from_start_start_time_ms"] = offset_ms
    if metadata.get("fulltitle") or metadata.get("title"):
        video.title = str(metadata.get("fulltitle") or metadata.get("title"))
    if metadata.get("thumbnail"):
        video.thumbnail_url = str(metadata["thumbnail"])
    index = target / "recording.json"
    try:
        data = json.loads(index.read_text(encoding="utf-8")) if index.is_file() else {}
    except (OSError, ValueError):
        data = {}
    live_stems = list(dict.fromkeys(item.get("stem") for item in attempts
                                    if item.get("profile") == "live" and item.get("stem")))
    data.update(primary_media=primary.name,
                segments=[{"name": path.name, "size": path.stat().st_size} for path in media])
    data["attempts"] = [{**item, "from_start": bool(item.get("from_start"))} for item in attempts]
    data["intervals"] = [{"stem": item_stem,
                          "started_at": attempts_by_stem.get(item_stem, {}).get("started_at"),
                          "from_start": bool(attempts_by_stem.get(item_stem, {}).get("from_start")),
                          "files": [path.name for path in _stem_media_paths(target, item_stem)]}
                         for item_stem in live_stems]
    index.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return bool(playable)


def stop_recording(video_id: int, *, user_requested: bool = True) -> bool:
    processes = [active_recorders.get(video_id), active_upstreams.get(video_id), active_chats.get(video_id)]
    processes = [process for process in processes if process is not None and process.poll() is None]
    if not processes:
        return False
    mark_stopping(video_id, user_requested=user_requested,
                  reason=None if user_requested else "shutdown")
    for process in processes:
        request_process_interrupt(process)
    return True


def force_stop_recording(video_id: int) -> None:
    processes = [active_recorders.get(video_id), active_upstreams.get(video_id), active_chats.get(video_id)]
    for process in processes:
        if process is not None:
            _terminate_process_tree(process, force=True)


def running_counts() -> tuple[int, int]:
    with _lock:
        for video_id, (_, thread) in list(_threads.items()):
            if not thread.is_alive():
                _threads.pop(video_id, None)
        live = sum(profile == "live" for profile, _ in _threads.values())
        archive = sum(profile == "archive" for profile, _ in _threads.values())
        return live, archive


def dispatch(video_id: int, profile: str, target: Callable[[int], None]) -> bool:
    with _lock:
        if video_id in _threads:
            return False
        _stopping.discard(video_id)
        def run() -> None:
            try:
                target(video_id)
            finally:
                with _lock:
                    _stopping.discard(video_id)
                    _user_stopping.discard(video_id)
                    _stop_reasons.pop(video_id, None)
                    current = _threads.get(video_id)
                    if current and current[1] is threading.current_thread():
                        _threads.pop(video_id, None)
        thread = threading.Thread(target=run, name=f"recorder-{video_id}", daemon=True)
        _threads[video_id] = (profile, thread)
        thread.start()
        return True


def shutdown(stop: Callable[[int], None], timeout: float) -> list[int]:
    with _lock:
        active = list(_threads.items())
    for video_id, _ in active:
        mark_stopping(video_id, user_requested=False)
        stop(video_id)
    deadline = time.monotonic() + max(0.0, timeout)
    for _, (_, thread) in active:
        thread.join(timeout=max(0.0, deadline - time.monotonic()))
    unfinished = [video_id for video_id, (_, thread) in active if thread.is_alive()]
    for video_id in unfinished:
        force_stop_recording(video_id)
    for video_id, (_, thread) in active:
        if video_id in unfinished:
            thread.join(timeout=1)
    return [video_id for video_id, (_, thread) in active if thread.is_alive()]


def write_process_manifest(directory: Path, process, role: str) -> None:
    try:
        import psutil
        started = psutil.Process(process.pid).create_time()
        path = directory / ".recorder.json"
        current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"processes": []}
        current["processes"] = [item for item in current.get("processes", []) if item.get("pid") != process.pid]
        current["processes"].append({"pid": process.pid, "started_at": started, "role": role})
        path.write_text(json.dumps(current), encoding="utf-8")
    except (ImportError, OSError, ValueError):
        return


def cleanup_orphan_processes() -> None:
    """Stop only manifest PIDs whose creation time and command identify yt-dlp/ffmpeg."""
    import psutil
    from sqlalchemy import select
    from ..db import SessionLocal
    from ..models import Video
    from .locks import default_locks, recorder_lock
    with SessionLocal() as db:
        recordings = db.execute(select(Video.id, Video.youtube_video_id, Video.file_path).where(
            Video.status == "RECORDING", Video.file_path.is_not(None))).all()
    for _video_id, youtube_video_id, directory_name in recordings:
        namespace, key = recorder_lock(youtube_video_id)
        with default_locks.acquire(namespace, key) as lease:
            if not lease.acquired or not lease.still_owned():
                continue
            manifest = Path(directory_name) / ".recorder.json"
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for item in data.get("processes", []):
                try:
                    process = psutil.Process(int(item["pid"]))
                    if abs(process.create_time() - float(item["started_at"])) > 0.01:
                        continue
                    command = " ".join(process.cmdline()).lower()
                    if "yt-dlp" not in command and "fake_ytdlp.py" not in command and "ffmpeg" not in command:
                        continue
                    descendants = process.children(recursive=True)
                    for child in descendants:
                        child.terminate()
                    process.terminate()
                    _, alive = psutil.wait_procs([process, *descendants], timeout=5)
                    for remaining in alive:
                        remaining.kill()
                except (psutil.NoSuchProcess, psutil.AccessDenied, KeyError, ValueError):
                    continue
            manifest.unlink(missing_ok=True)


def begin_attempt(metadata: dict, stem: str, profile: str, started_at: datetime,
                  *, from_start: bool = False) -> dict:
    attempts = list((metadata or {}).get("attempts", []))
    attempts.append({"stem": stem, "profile": profile, "started_at": started_at.isoformat(),
                     "ended_at": None, "outcome": "running", "from_start": from_start})
    return {**(metadata or {}), "attempts": attempts}


def finish_attempt(metadata: dict, stem: str, profile: str, outcome: str, ended_at: datetime) -> dict:
    attempts = list((metadata or {}).get("attempts", []))
    for attempt in reversed(attempts):
        if attempt.get("stem") == stem and attempt.get("profile") == profile and attempt.get("outcome") == "running":
            attempt.update(ended_at=ended_at.isoformat(), outcome=outcome)
            return {**(metadata or {}), "attempts": attempts}
    attempts.append({"stem": stem, "profile": profile, "started_at": ended_at.isoformat(),
                     "ended_at": ended_at.isoformat(), "outcome": outcome})
    return {**(metadata or {}), "attempts": attempts}


def _wait_for_tee_downloader(process: subprocess.Popen, video_id: str, channel_id: str) -> None:
    try:
        process.wait(timeout=TEE_YTDLP_EXIT_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        log.error("yt-dlp did not exit after tee recording youtube_video_id=%s channel_id=%s",
                  video_id, channel_id)
        stop_process(process)
        raise RuntimeError("yt-dlp did not exit after tee recording") from exc


def _tee_ytdlp_arguments(args: list[str], target: Path, output_stem: str) -> list[str]:
    """Keep media on stdout while sidecar files use their typed templates."""
    tee_args = list(args)
    output_arg = tee_args.index("-o")
    tee_args[output_arg + 1] = "-"
    typed_template = str(target / f"{output_stem}.%(ext)s")
    tee_args[-1:-1] = ["-P", f"thumbnail:{target}",
                       "-o", f"thumbnail:{typed_template}", "-o", f"description:{typed_template}"]
    return tee_args


def _normalize_tee_sidecars(target: Path, output_stem: str) -> None:
    """Move yt-dlp's stdout-template sidecars from '-.<ext>' into named files."""
    for path in target.glob("-.*"):
        if path.is_file():
            destination = target / f"{output_stem}{path.suffix}"
            if not destination.exists():
                path.replace(destination)


def _execute_attempt(db: Session, v: Video, ch: Channel, root: Path, target: Path,
                     output_stem: str, profile: str, is_live: bool, selector: str | None,
                     lease: Any, chat_offsets: dict[str, int]) -> tuple[subprocess.Popen, bytes]:
    """Run yt-dlp, separated chat capture, heartbeats, and optional tee output."""
    import subprocess

    from_start = is_live and (v.raw_metadata or {}).get("live_from_start") == "used"
    info_template = "%(.{id,title,fulltitle,description,duration,release_timestamp,timestamp,upload_date,categories,tags,channel,channel_id,uploader,thumbnail,live_status,was_live,is_live,availability,format_id,format,ext,width,height,fps,vcodec,acodec,webpage_url,view_count,like_count})j"
    args = [*ytdlp_command(), "--newline", "--print-to-file", info_template,
        str(target / f"{output_stem}.info.json"), "--write-thumbnail", "--write-description",
        *cookie_args(ch.cookie_profile), "--merge-output-format", "mkv", *format_args(selector), "-o", str(target / f"{output_stem}.%(ext)s"),
        *downloader_args(is_live=is_live),
        f"https://youtube.com/watch?v={v.youtube_video_id}"]
    if from_start:
        args.insert(args.index("--merge-output-format"), "--live-from-start")
    if is_live and ch.chat_enabled:
        chat_args = ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "--sub-format", "json",
                     *cookie_args(ch.cookie_profile), "-o", str(target / f"{output_stem}.%(ext)s"),
                     f"https://youtube.com/watch?v={v.youtube_video_id}"]
        active_chats[v.id] = popen_ytdlp(chat_args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _chat_targets[v.id] = (target, output_stem)
        write_process_manifest(target, active_chats[v.id], "chat")
        if stopping(v.id):
            stop_process(active_chats[v.id])
    hls_dir = (root / "live" / v.youtube_video_id).resolve()
    tee_enabled = bool(is_live and ch.live_playback_enabled)
    if tee_enabled:
        from .live_playback import tee_args
        v.live_hls_path = str(hls_dir)
        tee_ytdlp = _tee_ytdlp_arguments(args, target, output_stem)
        # Tee only the media stream. yt-dlp remains responsible for YouTube URL refresh and metadata.
        yt_proc = None
        ff_proc = None
        try:
            yt_proc = popen_recording(tee_ytdlp[len(ytdlp_command()):], stdout=subprocess.PIPE,
                                      cwd=target)
            active_upstreams[v.id] = yt_proc
            write_process_manifest(target, yt_proc, "yt-dlp")
            if stopping(v.id):
                stop_process(yt_proc)
            ff_proc = launch_recording_ffmpeg(tee_args(settings.ffmpeg_path, hls_dir, target / f"{output_stem}.mkv"),
                stdin=yt_proc.stdout, stdout=subprocess.DEVNULL)
            write_process_manifest(target, ff_proc, "ffmpeg")
            if yt_proc.stdout is not None:
                yt_proc.stdout.close()
            active_recorders[v.id] = ff_proc
            if stopping(v.id):
                stop_process(ff_proc)
            while True:
                try:
                    ff_proc.wait(timeout=1)
                    ff_stderr = stderr_tail(ff_proc)
                    break
                except subprocess.TimeoutExpired:
                    if not lease.still_owned():
                        mark_stopping(v.id, user_requested=False, reason="lock_lost")
                        stop_process(ff_proc)
                        stop_process(yt_proc)
                        raise RuntimeError("recorder advisory lock lost during tee recording")
                    if stopping(v.id):
                        stop_process(yt_proc)
                        stop_process(ff_proc)
                    try:
                        from .live_playback import prune_hls
                        prune_hls(hls_dir, ch.live_playback_limit_bytes)
                    except Exception:
                        log.exception("HLS rolling cleanup failed for %s", v.youtube_video_id)
                    v.recording_heartbeat_at = now()
                    _audit_low_disk_space(db, v)
                    db.commit()
                    _ingest_live_chat_delta(db, v, target, output_stem, chat_offsets)
            _wait_for_tee_downloader(yt_proc, v.youtube_video_id, ch.youtube_channel_id)
            yt_stderr = stderr_tail(yt_proc)
            _normalize_tee_sidecars(target, output_stem)
            active_upstreams.pop(v.id, None)
            active_recorders.pop(v.id, None)
            tee_rc = ff_proc.returncode or yt_proc.returncode
            requested_stop = stop_reason(v.id) is not None or stopping(v.id)
            transport_stream = target / f"{output_stem}.tee.ts"
            tee_media = transport_stream.is_file() and transport_stream.stat().st_size > 0
            remuxed = False
            if tee_media:
                from .live_playback import finalize_tee_archive
                remuxed = finalize_tee_archive(
                    settings.ffmpeg_path, transport_stream, target / f"{output_stem}.mkv"
                )
            natural_end_error = (
                yt_proc.returncode not in (None, 0)
                and ff_proc.returncode == 0
                and remuxed
                and _has_live_end_data_blocks_error(yt_stderr)
            )
            if requested_stop:
                proc = ff_proc
                proc.returncode = 0 if tee_media or remuxed else (tee_rc or 1)
            elif natural_end_error:
                proc = ff_proc
                proc.returncode = yt_proc.returncode
                proc._live_terminal_stderr = yt_stderr
            elif tee_rc:
                from .live_playback import fallback_to_direct
                def record_tee_failure(error, return_code):
                    db.add(AuditLog(video_id=v.id, action="LIVE_PLAYBACK_FALLBACK", reason="TEE_FAILED",
                        details={"error": str(error) if error else None, "return_code": return_code,
                          "ffmpeg": (ff_stderr or b"").decode(errors="replace")[-2000:], "yt_dlp": (yt_stderr or b"").decode(errors="replace")[-2000:]}))
                fallback_stem = _next_output_stem(v, target, profile)
                fallback_args = [arg.replace(f"{output_stem}.", f"{fallback_stem}.") for arg in args]
                v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "tee_failed", now())
                v.raw_metadata = begin_attempt(v.raw_metadata or {}, fallback_stem, profile, now(),
                                               from_start=from_start)
                proc = fallback_to_direct(RuntimeError("tee process failed"), tee_rc,
                    lambda: popen_recording(fallback_args[len(ytdlp_command()):], stdout=subprocess.DEVNULL),
                    record_tee_failure)
                setattr(proc, "recording_stem", fallback_stem)
                v.live_hls_path = None
                shutil.rmtree(hls_dir, ignore_errors=True)
                write_process_manifest(target, proc, "yt-dlp")
                if stopping(v.id):
                    stop_process(proc)
                db.commit()
            elif not remuxed:
                from .live_playback import finalize_tee_archive
                transport_stream = target / f"{output_stem}.tee.ts"
                proc = ff_proc
                proc.returncode = 0 if tee_media else 1
            else:
                proc = ff_proc
                proc.returncode = 0
        except Exception as tee_error:
            log.exception("tee launch failed for %s", v.youtube_video_id)
            if yt_proc and yt_proc.poll() is None:
                stop_process(yt_proc)
            active_upstreams.pop(v.id, None)
            transport_stream = target / f"{output_stem}.tee.ts"
            if transport_stream.is_file() and transport_stream.stat().st_size > 0:
                from .live_playback import finalize_tee_archive
                finalize_tee_archive(settings.ffmpeg_path, transport_stream, target / f"{output_stem}.mkv")
            if stop_reason(v.id) is not None or stopping(v.id):
                proc = ff_proc or yt_proc
                if proc is None:
                    raise tee_error
                proc.returncode = 0 if transport_stream.is_file() and transport_stream.stat().st_size > 0 else 1
            else:
                from .live_playback import fallback_to_direct
                fallback_stem = _next_output_stem(v, target, profile)
                fallback_args = [arg.replace(f"{output_stem}.", f"{fallback_stem}.") for arg in args]
                v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "tee_launch_failed", now())
                v.raw_metadata = begin_attempt(v.raw_metadata or {}, fallback_stem, profile, now(),
                                               from_start=from_start)
                proc = fallback_to_direct(tee_error, None,
                    lambda: popen_recording(fallback_args[len(ytdlp_command()):], stdout=subprocess.DEVNULL),
                    lambda error, rc: db.add(AuditLog(video_id=v.id, action="LIVE_PLAYBACK_FALLBACK",
                        reason="TEE_LAUNCH_FAILED", details={"error": str(error)})))
                setattr(proc, "recording_stem", fallback_stem)
            v.live_hls_path = None
            if proc:
                write_process_manifest(target, proc, "yt-dlp")
                if stopping(v.id):
                    stop_process(proc)
            shutil.rmtree(hls_dir, ignore_errors=True)
            db.commit()
    else:
        proc = popen_recording(args[len(ytdlp_command()):], stdout=subprocess.DEVNULL)
        write_process_manifest(target, proc, "yt-dlp")
    active_recorders[v.id] = proc
    if stopping(v.id):
        stop_process(proc)
    stderr = _wait_for_attempt(db, v, target, output_stem, proc, lease, chat_offsets)
    stderr += getattr(proc, "_live_terminal_stderr", b"")
    active_recorders.pop(v.id, None)
    attempt_args = args
    if proc.returncode and selector and format_unavailable(stderr):
        log.warning("format selector unavailable for %s; retrying with yt-dlp automatic selection", v.youtube_video_id)
        v.format_fallback = True
        db.commit()
        retry_args = [a for a in args if a not in ("-f", selector)]
        attempt_args = retry_args
        proc = popen_recording(retry_args[len(ytdlp_command()):], stdout=subprocess.DEVNULL)
        write_process_manifest(target, proc, "yt-dlp")
        active_recorders[v.id] = proc
        if stopping(v.id):
            stop_process(proc)
        retry_stderr = _wait_for_attempt(db, v, target, output_stem, proc, lease, chat_offsets)
        active_recorders.pop(v.id, None)
        stderr = (stderr or b"") + b"\nFORMAT FALLBACK to automatic selection\n" + (retry_stderr or b"")
    if from_start and proc.returncode and not stopping(v.id) and not any(
            path.is_file() and path.stat().st_size > 0 for path in _stem_media_paths(target, output_stem)):
        retry_args = [arg for arg in attempt_args if arg != "--live-from-start"]
        if "--concurrent-fragments" in retry_args:
            index = retry_args.index("--concurrent-fragments")
            del retry_args[index:index + 2]
        proc = popen_recording(retry_args[len(ytdlp_command()):], stdout=subprocess.DEVNULL)
        write_process_manifest(target, proc, "yt-dlp")
        active_recorders[v.id] = proc
        if stopping(v.id):
            stop_process(proc)
        fallback_stderr = _wait_for_attempt(db, v, target, output_stem, proc, lease, chat_offsets)
        active_recorders.pop(v.id, None)
        stderr = (stderr or b"") + b"\nLIVE_FROM_START FALLBACK\n" + (fallback_stderr or b"")
        v.raw_metadata = {**(v.raw_metadata or {}), "live_from_start": "fallback"}
        attempts = list((v.raw_metadata or {}).get("attempts", []))
        for attempt in reversed(attempts):
            if attempt.get("stem") == output_stem and attempt.get("outcome") == "running":
                attempt["from_start"] = False
                break
        v.raw_metadata = {**(v.raw_metadata or {}), "attempts": attempts}
        db.add(AuditLog(video_id=v.id, action="LIVE_FROM_START_FALLBACK", reason="NO_MEDIA",
                        details={"stderr": (stderr or b"").decode("utf-8", "replace")[-2000:]}))
        db.commit()
    elif from_start and proc.returncode == 0:
        v.raw_metadata = {**(v.raw_metadata or {}), "live_from_start": "used"}
    db.commit()
    _finish_chat_process(v.id)
    return proc, stderr


def _read_stem_metadata(target: Path, stem: str) -> dict:
    info_path = target / f"{stem}.info.json"
    try:
        if not info_path.is_file() or info_path.stat().st_size > 20 * 1024 * 1024:
            return {}
        return json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _merge_completion_metadata(video: Video, channel: Channel, stem: str, profile: str,
                               is_live: bool, local_duration: float | None,
                               inspected: dict | None, finished_at: datetime) -> int:
    metadata = _read_stem_metadata(Path(video.file_path), stem)
    inspect_attempted = inspected is not None
    inspected = inspected or {}
    if is_live and not inspect_attempted and (not metadata.get("duration") or not metadata.get("release_timestamp")):
        try:
            session = object_session(video)
            if session is not None: session.commit()
            inspected = inspect(f"https://youtube.com/watch?v={video.youtube_video_id}", channel.cookie_profile)
        except Exception:
            inspected = {}
    live_start = metadata.get("release_timestamp") or inspected.get("release_timestamp")
    try:
        live_start = float(live_start) if live_start is not None else None
        if live_start and live_start > 10_000_000_000:
            live_start /= 1000
    except (TypeError, ValueError, OverflowError):
        live_start = None
    from_start = bool((video.raw_metadata or {}).get("live_from_start") == "used")
    if from_start:
        offset_ms = _captured_live_from_start_offset(video.raw_metadata or {}, stem)
        if offset_ms is None:
            offset_ms = _probe_live_from_start_offset(
                Path(video.file_path), stem, Path(video.file_path) / f"{stem}.mkv")
    else:
        offset_ms = (max(0, int((video.recorded_at.timestamp() - live_start) * 1000))
                     if is_live and live_start and video.recorded_at else 0)
    metadata = {**metadata, **{key: value for key, value in inspected.items() if metadata.get(key) is None}}
    original_duration = metadata.get("duration")
    if not is_live:
        original_duration = metadata.get("duration") or local_duration
    elif original_duration is None and local_duration is not None:
        original_duration = offset_ms / 1000 + local_duration
    video.original_duration = float(original_duration) if original_duration is not None else None
    fields = ("title", "description", "thumbnail", "categories", "channel_id", "upload_date", "release_timestamp")
    video.raw_metadata = {**(video.raw_metadata or {}),
                          **{key: metadata[key] for key in fields if metadata.get(key) is not None},
                          "local_duration": local_duration,
                          "recording_start_offset_ms": offset_ms}
    if from_start:
        video.raw_metadata["live_from_start_start_time_ms"] = offset_ms
    title = metadata.get("fulltitle") or metadata.get("title")
    if title:
        video.title = str(title)
    if metadata.get("thumbnail"):
        video.thumbnail_url = str(metadata["thumbnail"])
    video.actual_format = metadata.get("format_id") or metadata.get("format")
    video.raw_metadata = finish_attempt(video.raw_metadata or {}, stem, profile, "success", finished_at)
    return offset_ms


def _recording_media_inventory(video: Video, target: Path, profile: str,
                               fallback_media: Path) -> tuple[list[Path], list[Path], Path, Path | None,
                                                              list[dict], set[str]]:
    from .recovery import probe_duration
    attempts = (video.raw_metadata or {}).get("attempts", [])
    live_files = [(stem, path) for stem in dict.fromkeys(
        item.get("stem") for item in attempts if item.get("profile") == "live")
        for path in _stem_media_paths(target, stem)]
    live_media = [path for _, path in live_files]
    archive_stems = {item.get("stem") for item in attempts if item.get("profile") == "archive"}
    attempts_by_stem = {item.get("stem"): item for item in attempts if item.get("stem")}
    archive_media = next((target / f"{stem}.{extension}" for stem in archive_stems
        if attempts_by_stem.get(stem, {}).get("outcome") == "success"
        for extension in ("mkv", "mp4", "ts", "webm", "m4v", "mov")
        if (target / f"{stem}.{extension}").is_file()
        and (target / f"{stem}.{extension}").stat().st_size > 0), None)
    completed_from_start = [path for path in live_media
                            if attempts_by_stem.get(path.name.split(".", 1)[0], {}).get("from_start")
                            and attempts_by_stem.get(path.name.split(".", 1)[0], {}).get("outcome") == "success"]
    primary = (max(completed_from_start, key=lambda path: probe_duration(path) or 0)
               if completed_from_start else archive_media or
               (max(live_media, key=lambda path: probe_duration(path) or 0) if live_media else fallback_media))
    attempted_stems = {item.get("stem") for item in attempts if item.get("stem")}
    original_media = list(dict.fromkeys(path for stem in attempted_stems
        for path in _stem_media_paths(target, stem) if not path.name.endswith(".part")))
    intervals = {stem for stem, _ in live_files}
    segments = [{"name": path.name, "size": path.stat().st_size} for path in sorted(original_media)]
    return live_media, original_media, primary, archive_media, segments, intervals


def _write_media_checksum(media: Path, target: Path) -> None:
    digest = hashlib.sha256()
    with media.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    (target / "checksum.sha256").write_text(f"{digest.hexdigest()}  {media.name}\n", encoding="utf-8")


def _write_recording_index(video: Video, channel: Channel, target: Path, primary: Path,
                          segments: list[dict], live_stems: set[str]) -> None:
    attempts = {item.get("stem"): item for item in (video.raw_metadata or {}).get("attempts", []) if item.get("stem")}
    intervals = [{"stem": stem, "started_at": attempts.get(stem, {}).get("started_at"),
                  "from_start": bool(attempts.get(stem, {}).get("from_start")),
                  "files": [path.name for path in _stem_media_paths(target, stem)]}
                 for stem in sorted(live_stems)]
    index = {
        "video_id": video.youtube_video_id,
        "channel_id": channel.youtube_channel_id,
        "recorded_at": video.recorded_at.isoformat(),
        "recording_finished_at": video.recording_finished_at.isoformat(),
        "original_duration": video.original_duration,
        "primary_media": primary.name,
        "segments": segments,
        "intervals": intervals,
        "attempts": [{"stem": item.get("stem"), "profile": item.get("profile"),
                      "started_at": item.get("started_at"), "ended_at": item.get("ended_at"),
                      "outcome": item.get("outcome"), "from_start": bool(item.get("from_start"))}
                     for item in (video.raw_metadata or {}).get("attempts", [])],
        "format_profile": video.format_profile,
        "format_selector": video.format_selector,
        "format_fallback": video.format_fallback,
        "actual_format": video.actual_format,
    }
    (target / "recording.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")


def _sample_fingerprints(db: Session, video: Video, channel: Channel, media: Path,
                         offset_ms: int, local_duration: float | None, is_live: bool) -> None:
    mode = (video.edit_detection_mode or channel.edit_detection_mode or "standard").lower()
    if not settings.fingerprint_enabled or mode not in ("standard", "strong"):
        return
    if video.is_partial or not video.original_duration:
        return
    try:
        db.commit()
        offset_seconds = offset_ms / 1000 if is_live else 0
        values = []
        for position in POSITIONS:
            remote_second = video.original_duration * position
            local_second = remote_second - offset_seconds
            if local_second < 0 or local_duration is None or local_second >= local_duration:
                continue
            values.append((position, video_phash(str(media), local_second),
                           audio_fingerprint(str(media), local_second) if mode == "strong" else None))
        for position, video_hash, audio_hash in values:
            db.add(Fingerprint(video_id=video.id, position=position, video_hash=video_hash, audio_hash=audio_hash))
    except Exception as exc:
        db.add(AuditLog(video_id=video.id, action="FINGERPRINT_FAILED", reason="BEST_EFFORT",
                        details={"error": str(exc)[:1000]}))
        log.warning("fingerprint sampling failed for %s: %s", video.youtube_video_id, exc)


def _archive_backfill_completed(metadata: dict, profile: str) -> bool:
    """Only label a successful download as complete when it was queued as backfill."""
    return profile == "archive" and metadata.get("archive_backfill") == "queued"


class RecordingCommitUnavailable(RuntimeError):
    """The completed media is safe on disk but its final DB commit did not recover."""


def _commit_completed_recording(db: Session, video: Video, *, retries: int = 3) -> Video:
    from copy import deepcopy
    from sqlalchemy.exc import DBAPIError, OperationalError, PendingRollbackError

    video_id = video.id
    mapper = type(video).__mapper__
    snapshot = {column.key: deepcopy(getattr(video, column.key)) for column in mapper.column_attrs}
    pending = list(db.new)
    for attempt in range(retries):
        try:
            db.commit()
            return video
        except (PendingRollbackError, OperationalError, DBAPIError) as exc:
            retryable = isinstance(exc, (PendingRollbackError, OperationalError)) or exc.connection_invalidated
            db.rollback()
            if not retryable or attempt + 1 >= retries:
                raise RecordingCommitUnavailable(str(exc)) from exc
            time.sleep(0.25 * (attempt + 1))
            video = db.get(Video, video_id)
            if video is None:
                raise RecordingCommitUnavailable("recording row disappeared during completion") from exc
            for name, value in snapshot.items():
                setattr(video, name, deepcopy(value))
            for row in pending:
                db.add(row)
    raise RecordingCommitUnavailable("recording completion could not be committed")


def _complete_recording(db: Session, v: Video, ch: Channel, target: Path, output_stem: str,
                        profile: str, is_live: bool, media: Path, lease: Any,
                        inspected: dict | None = None) -> None:
    """Persist media metadata, fingerprints, retention state, and optional jobs."""
    v.file_path = str(target)
    v.recording_finished_at = now()
    db.commit()
    v.recording_heartbeat_at = None
    from .recovery import probe_duration
    local_duration = probe_duration(media)
    prior_offset_ms = (v.raw_metadata or {}).get("recording_start_offset_ms", 0)
    offset_ms = _merge_completion_metadata(v, ch, output_stem, profile, is_live,
                                           local_duration, inspected, v.recording_finished_at)
    incomplete_archive = bool(profile == "archive"
        and (v.raw_metadata or {}).get("archive_backfill") == "queued"
        and v.original_duration is not None and local_duration is not None
        and v.original_duration - local_duration > settings.duration_tolerance_seconds)
    if incomplete_archive:
        v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile,
                                        "incomplete_archive", v.recording_finished_at)
        v.raw_metadata["recording_start_offset_ms"] = prior_offset_ms
        offset_ms = prior_offset_ms
    _live_media, original_media, primary, archive_media, segments, live_interval_stems = (
        _recording_media_inventory(v, target, profile, media))
    v.file_size = sum(path.stat().st_size for path in original_media)
    v.raw_metadata = {**(v.raw_metadata or {}), "primary_media": primary.name}
    db.commit()
    try:
        from .media import inspect_media
        media_info = inspect_media(primary, settings.ffprobe_path)
        if media_info:
            v.raw_metadata = {**(v.raw_metadata or {}), "media_info": media_info}
    except Exception:
        log.exception("media probe failed for %s", v.youtube_video_id)
    archive_primary_completed = (
        _archive_backfill_completed(v.raw_metadata or {}, profile)
        and not incomplete_archive
        and archive_media is not None
        and primary == archive_media
    )
    if archive_primary_completed:
        offset_ms = 0
        v.raw_metadata["recording_start_offset_ms"] = 0
    _write_media_checksum(primary, target)
    user_stopped = bool((v.raw_metadata or {}).get("user_stopped") or user_stopping(v.id))
    has_complete_from_start = ((v.raw_metadata or {}).get("live_from_start") == "used"
                               and offset_ms <= 5_000)
    missing_start = (v.raw_metadata or {}).get("live_from_start") == "used" and offset_ms > 5_000
    v.is_partial = incomplete_archive or missing_start or user_stopped or (
        len(live_interval_stems) > 1 and archive_media is None)
    if _archive_backfill_completed(v.raw_metadata or {}, profile) and not incomplete_archive:
        v.raw_metadata["archive_backfill"] = "completed"
    if has_complete_from_start:
        metadata = v.raw_metadata or {}
        live_still_running = metadata.get("live_status") == "is_live"
        release = _timestamp_seconds(metadata.get("release_timestamp"))
        attempts = metadata.get("attempts", [])
        late_live_start = (live_still_running and release is not None
                           and release <= time.time() and len(attempts) == 1)
        v.is_partial = user_stopped or late_live_start
        v.raw_metadata["live_from_start"] = "used"
    if is_live and inspected and inspected.get("live_status") in {"post_live", "was_live", "not_live"}:
        v.raw_metadata["live_status"] = "was_live"
    v.retention_expires_at = (v.recording_finished_at + timedelta(days=v.retention_days)) if v.retention_days is not None and not v.is_partial else None
    _write_recording_index(v, ch, target, primary, segments, live_interval_stems)
    for removed in remove_intermediates_after_success(target, media,
            lambda name: db.add(AuditLog(video_id=v.id, action="CLEANUP_INTERMEDIATE", reason="VERIFIED_MERGED_MEDIA", details={"path": name})),
            stem=output_stem, validated=local_duration is not None):
        log.info("removed verified yt-dlp intermediate for %s: %s", v.youtube_video_id, removed)
    if not incomplete_archive:
        _sample_fingerprints(db, v, ch, media, offset_ms, local_duration, is_live)
    if not lease.still_owned():
        raise RuntimeError("recorder advisory lock lost before completion")
    # Never mark a missing/empty output as archived.
    if not media.is_file() or media.stat().st_size <= 0:
        raise RuntimeError("recording output disappeared or is empty")
    if v.live_hls_path:
        try:
            from .live_playback import cleanup_hls
            if cleanup_hls(v, media):
                db.add(AuditLog(video_id=v.id, action="LIVE_HLS_CLEANUP", reason="VERIFIED_ARCHIVE"))
        except Exception:
            # A sidecar cleanup problem must never alter the verified archive result.
            log.exception("live HLS cleanup deferred for %s; leaving sidecars in place", v.youtube_video_id)
    backfill = incomplete_archive or (settings.live_gap_backfill and profile == "live" and not user_stopped
                and (missing_start or (not has_complete_from_start
                     and (len(live_interval_stems) > 1
                          or offset_ms >= settings.live_gap_backfill_threshold_seconds * 1000))))
    if backfill:
        v.raw_metadata.update(archive_backfill="queued", live_status="was_live")
        v.status = "DISCOVERED"
        v.is_partial = True
        v.retention_expires_at = None
        if incomplete_archive:
            v.recovery_attempts = (v.recovery_attempts or 0) + 1
            v.next_recovery_at = now() + recovery_delay(v.recovery_attempts)
    else:
        v.status = "PROTECTED" if v.protected else "ACTIVE"
    db.add(AuditLog(video_id=v.id, action="RECORD", reason="Recording completed"))
    v = _commit_completed_recording(db, v)
    log.info("recording completed youtube_video_id=%s channel_id=%s path=%s",
             v.youtube_video_id, ch.youtube_channel_id, primary)
    # Queue derived transcription only after the recording is committed.
    # Any queue problem is isolated and cannot change recording success.
    try:
        from .processing import enqueue_transcription
        enqueue_transcription(db, v.id)
    except Exception:
        db.rollback()
        log.exception("transcription enqueue failed for %s; recording remains ACTIVE", v.youtube_video_id)
    if ch.chat_enabled:
        # Chat replay is optional post-processing: commit ACTIVE first so
        # a chat DB/network failure cannot mark a complete media file failed.
        try:
            from .chat import ingest_jsonl, retrieve_replay
            chat_file = _chat_file(target, output_stem)
            if chat_file.is_file():
                ingest_jsonl(db, v.id, chat_file, complete_snapshot=False, isolated=True)
                db.commit()
            retrieve_replay(db, v.id, v.youtube_video_id, ch.cookie_profile)
            from .chat import rebase_live_chat_offsets
            rebase_live_chat_offsets(db, v.id)
            if (v.raw_metadata or {}).get("chat_replay") == "unavailable":
                from .chat import enqueue_replay_retry
                enqueue_replay_retry(db, v.id)
            db.commit()
        except Exception:
            db.rollback()
            try:
                from .chat import enqueue_replay_retry
                enqueue_replay_retry(db, v.id)
                db.commit()
            except Exception:
                db.rollback()
            log.exception("post-record chat processing failed for %s; recording remains ACTIVE", v.youtube_video_id)


def recorder_worker() -> None:
    dispatch_recordings()


def dispatch_recordings() -> None:
    """Dispatch due candidates in live-first order without waiting for recording."""
    live_count, archive_count = running_counts()
    with SessionLocal() as dispatch_db:
        candidates = dispatch_db.scalars(select(Video).where(
            Video.status.in_(["DISCOVERED", "WAITING_FOR_STORAGE"])).order_by(Video.created_at, Video.id)).all()
        candidates.sort(key=lambda item: (0 if (item.raw_metadata or {}).get("live_status") == "is_live" else 1,
                                          item.created_at, item.id))
        for candidate in candidates:
            if candidate.next_recovery_at and candidate.next_recovery_at > now():
                continue
            profile = "live" if (candidate.raw_metadata or {}).get("live_status") == "is_live" else "archive"
            if profile == "live" and live_count >= settings.max_concurrent_live_recordings:
                continue
            if profile == "archive" and archive_count >= settings.max_concurrent_archive_downloads:
                continue
            if dispatch(candidate.id, profile, record_video):
                if profile == "live":
                    live_count += 1
                else:
                    archive_count += 1


def _apply_recording_rule(db: Session, video: Video, channel: Channel,
                          rules: list[RecordingRule]) -> bool:
    selected, result = select_rule(
        rules,
        channel.id,
        video.title or "",
        str((video.raw_metadata or {}).get("description") or ""),
    )
    if result in ("excluded", "unmatched"):
        video.status = "SKIPPED"
        video.raw_metadata = {**(video.raw_metadata or {}), "rule_result": result}
        db.commit()
        return False
    if selected is None:
        return True
    overrides: dict[str, Any] = {}
    if selected.quality:
        overrides.update(live_quality=selected.quality, archive_quality=selected.quality)
    if selected.storage_id is not None:
        video.storage_id = selected.storage_id
    if selected.retention_days is not None:
        video.retention_days = selected.retention_days
    if selected.edit_detection_mode:
        video.edit_detection_mode = selected.edit_detection_mode
    video.raw_metadata = {
        **(video.raw_metadata or {}),
        **overrides,
        "matched_rule_id": selected.id,
        "rule_overrides": {
            "storage_id": selected.storage_id,
            "retention_days": selected.retention_days,
            "quality": selected.quality,
            "edit_detection_mode": selected.edit_detection_mode,
        },
    }
    return True


def _choose_storage(db: Session, video: Video, channel: Channel) -> Storage | None:
    override_id = (video.raw_metadata or {}).get("rule_overrides", {}).get("storage_id")
    preferred = db.get(Storage, override_id) if override_id is not None else (
        video.storage if video.storage and video.storage.enabled else channel.storage
    )
    if preferred and not preferred.enabled:
        preferred = None
    fallback = channel.fallback_storage if channel.fallback_storage and channel.fallback_storage.enabled else None
    default = db.scalar(select(Storage).where(Storage.enabled.is_(True)).order_by(Storage.id))
    candidates = list({item.id: item for item in (preferred, fallback, default) if item}.values())
    for candidate in candidates:
        root = Path(candidate.root_path)
        probe = root / f".write-test-{os.getpid()}-{candidate.id}"
        try:
            if not root.is_dir():
                continue
            probe.write_text("ok", encoding="utf-8")
            if shutil.disk_usage(root).free < candidate.minimum_free_bytes:
                continue
            return candidate
        except OSError:
            continue
        finally:
            probe.unlink(missing_ok=True)
    return None


def _recording_directory(video: Video, channel: Channel, storage_root: Path) -> Path:
    from .recording_paths import (CHANNEL_DIRECTORY_MAX_LENGTH, is_video_directory,
                                  sanitize_folder_name, video_directory_name)
    date_anchor = video.scheduled_start_at or video.created_at or now()
    existing = Path(video.file_path) if video.file_path else None
    if existing and is_video_directory(existing, storage_root, video.youtube_video_id):
        return existing
    channel_name = sanitize_folder_name(channel.name or "", CHANNEL_DIRECTORY_MAX_LENGTH)
    channel_name = channel_name or sanitize_folder_name(channel.youtube_channel_id, CHANNEL_DIRECTORY_MAX_LENGTH)
    return storage_root / channel_name / video_directory_name(
        video.youtube_video_id, video.title, date_anchor)


def _next_output_stem(video: Video, target: Path, profile: str) -> str:
    from .recovery import choose_recording_stem
    attempts = (video.raw_metadata or {}).get("attempts", [])
    live_stems = {item.get("stem") for item in attempts if item.get("profile") == "live"}
    return choose_recording_stem(target, profile, live_stems)


def _timestamp_seconds(value: object) -> float | None:
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            timestamp = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None
    return timestamp / 1000 if timestamp > 10_000_000_000 else timestamp


def _live_start_too_old(release_timestamp: float, now_timestamp: float) -> bool:
    max_age = max(0, settings.live_from_start_max_age_hours) * 3600
    return now_timestamp - release_timestamp > max_age


def _select_live_from_start(db: Session, video: Video, channel: Channel, is_live: bool) -> str | None:
    if not is_live:
        return None
    metadata = video.raw_metadata or {}
    release = _timestamp_seconds(metadata.get("release_timestamp"))
    if (channel.live_from_start and settings.live_from_start and release is not None
            and _live_start_too_old(release, time.time())):
        video.raw_metadata = {**metadata, "live_from_start": "skipped_too_old"}
        return "skipped_too_old"
    if not channel.live_from_start or not settings.live_from_start or channel.live_playback_enabled:
        video.raw_metadata = {**(video.raw_metadata or {}), "live_from_start": "fallback"}
        return "fallback"

    inspected: dict = {}
    if release is None:
        db.commit()
        try:
            inspected = inspect(
                f"https://youtube.com/watch?v={video.youtube_video_id}",
                channel.cookie_profile,
            )
            release = _timestamp_seconds(inspected.get("release_timestamp"))
        except Exception:
            log.info("could not inspect release time for %s; trying live-from-start", video.youtube_video_id)
        if release is not None:
            metadata = {**metadata, "release_timestamp": release}
            if inspected.get("title"):
                metadata.setdefault("title", inspected["title"])
            video.raw_metadata = metadata

    if release is not None and _live_start_too_old(release, time.time()):
        video.raw_metadata = {**metadata, "live_from_start": "skipped_too_old"}
        return "skipped_too_old"
    video.raw_metadata = {**metadata, "live_from_start": "used"}
    return "used"


def record_video(video_id: int) -> None:
    """Record one candidate with its own session and advisory lock."""
    worker_video_id = video_id
    with SessionLocal() as db:
        v = db.scalar(select(Video).where(Video.id == video_id, Video.status.in_([
            "DISCOVERED", "WAITING_FOR_STORAGE"])))
        if v is None:
            return
        rules = db.scalars(select(RecordingRule).where(RecordingRule.enabled.is_(True)).order_by(RecordingRule.priority, RecordingRule.id)).all()
        worker_youtube_id = v.youtube_video_id
        output_stem: str | None = None
        profile: str | None = None
        namespace, key = recorder_lock(v.youtube_video_id)
        with default_locks.acquire(namespace, key) as lease:
            if not lease.acquired:
                log.info("skip recording %s: another process owns lock", v.youtube_video_id)
                return
            if not lease.still_owned():
                return
            ch = v.channel
            if not _apply_recording_rule(db, v, ch, rules):
                return
            storage = _choose_storage(db, v, ch)
            if storage is None:
                if v.status != "WAITING_FOR_STORAGE":
                    log.warning("storage unavailable for channel channel_id=%s youtube_video_id=%s",
                                ch.youtube_channel_id, v.youtube_video_id)
                v.status = "WAITING_FOR_STORAGE"
                v.next_recovery_at = now() + timedelta(seconds=settings.storage_retry_seconds)
                db.commit()
                return
            root = Path(storage.root_path)
            chat_offsets: dict[str, int] = {}
            try:
                target = _recording_directory(v, ch, root)
                target.mkdir(parents=True, exist_ok=True)
                v.fingerprints.clear()
                is_live = (v.raw_metadata or {}).get("live_status") == "is_live"
                live_from_start = _select_live_from_start(db, v, ch, is_live)
                profile = "live" if is_live else "archive"
                from .quality import resolve_quality
                selector, source = resolve_quality(v, ch, profile,
                    settings.live_quality if is_live else settings.archive_quality)
                v.format_selector = selector or ""
                v.format_profile = profile
                v.format_fallback = False
                log.info("recording started youtube_video_id=%s channel_id=%s profile=%s selector=%r source=%s",
                         v.youtube_video_id, ch.youtube_channel_id, profile, selector, source)
                v.file_path = str(target)
                v.storage_id = storage.id
                v.recorded_at = now()
                v.status = "RECORDING"
                v.recording_heartbeat_at = now()
                v.next_recovery_at = None
                release_epoch = _timestamp_seconds((v.raw_metadata or {}).get("release_timestamp"))
                if is_live:
                    recording_offset_ms = (
                        0 if live_from_start == "used" or release_epoch is None
                        else max(0, int(((v.recorded_at or now()).timestamp() - release_epoch) * 1000))
                    )
                    v.raw_metadata = {
                        **(v.raw_metadata or {}),
                        "recording_start_offset_ms": recording_offset_ms,
                    }
                db.commit()
                output_stem = _next_output_stem(v, target, profile)
                v.raw_metadata = begin_attempt(v.raw_metadata or {}, output_stem, profile, v.recorded_at or now(),
                                               from_start=live_from_start == "used")
                db.commit()
                proc, stderr = _execute_attempt(
                    db, v, ch, root, target, output_stem, profile, is_live, selector,
                    lease, chat_offsets)
                output_stem = getattr(proc, "recording_stem", output_stem)
                stop_kind = stop_reason(v.id)
                media_candidates = _stem_media_paths(target, output_stem)
                if stop_kind in {"user", "shutdown", "lock_lost"} and is_live and not any(
                        not path.name.endswith(".part") for path in media_candidates):
                    from .recovery import salvage_live_part
                    salvage_live_part(target, output_stem,
                        lambda path: db.add(AuditLog(video_id=v.id, action="SALVAGE_PARTIAL",
                            reason="VERIFIED_DURATION", details={"path": path})),
                        ffmpeg_path=settings.ffmpeg_path)
                    media_candidates = _stem_media_paths(target, output_stem)
                has_media = any(not path.name.endswith(".part") for path in media_candidates)
                outcome = classify_attempt_result(proc.returncode, has_media, stop_kind, is_live)
                if outcome in ("USER_STOP_MEDIA", "USER_STOP_EMPTY", "INTERRUPTED_RECOVERY"):
                    evidence = _update_partial_evidence(v, target, output_stem)
                    v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile,
                                                    outcome.lower(), now())
                    v.recording_heartbeat_at = None
                    v.recording_finished_at = now() if outcome == "USER_STOP_MEDIA" and evidence else None
                    if outcome == "USER_STOP_MEDIA" and evidence:
                        v.status = "ACTIVE"
                        v.is_partial = True
                        v.raw_metadata = {**(v.raw_metadata or {}), "user_stopped": True}
                        v.retention_expires_at = None
                    else:
                        v.status = "RECORDING_FAILED"
                        v.next_recovery_at = None if outcome == "INTERRUPTED_RECOVERY" else now() + recovery_delay(max(1, v.recovery_attempts or 0))
                        if outcome == "USER_STOP_EMPTY":
                            v.raw_metadata = {**(v.raw_metadata or {}), "user_stopped": True}
                    if is_live and ch.chat_enabled:
                        _ingest_chat_snapshot(db, v, target, output_stem)
                    manifest = target / ".recorder.json"
                    manifest.unlink(missing_ok=True)
                    db.commit()
                    return
                if proc.returncode:
                    error_kind = classify_ytdlp_error(stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr or ""))
                    backfill_media = _retryable_archive_backfill_media(
                        v, target, profile, stderr)
                    if (backfill_media is not None
                            and (v.recovery_attempts or 0) + 1 < settings.recovery_max_attempts):
                        v.raw_metadata = finish_attempt(
                            v.raw_metadata or {}, output_stem, profile,
                            "archive_backfill_retryable_failure", now())
                        v.recovery_attempts = (v.recovery_attempts or 0) + 1
                        v.raw_metadata = {
                            **(v.raw_metadata or {}),
                            "archive_backfill": "queued",
                            "archive_backfill_last_error": (
                                stderr.decode("utf-8", "replace") if isinstance(stderr, bytes)
                                else str(stderr or ""))[-2000:],
                        }
                        v.status = "DISCOVERED"
                        v.is_partial = True
                        v.retention_expires_at = None
                        v.recording_heartbeat_at = None
                        v.next_recovery_at = now() + recovery_delay(v.recovery_attempts)
                        db.add(AuditLog(video_id=v.id, action="ARCHIVE_BACKFILL_RETRY_QUEUED",
                            reason="YT_DLP_FRAGMENT_TEMP_FILE_MISSING", details={
                                "media_path": str(backfill_media),
                                "media_size": backfill_media.stat().st_size,
                                "recovery_attempts": v.recovery_attempts,
                            }))
                        (target / ".recorder.json").unlink(missing_ok=True)
                        db.commit()
                        log.warning("archive backfill retry queued with saved live media "
                                    "youtube_video_id=%s path=%s attempt=%s",
                                    v.youtube_video_id, backfill_media, v.recovery_attempts)
                        return
                    if is_live and _has_live_end_data_blocks_error(stderr):
                        media = next((target / f"{output_stem}.{ext}"
                                      for ext in ("mkv", "mp4", "ts", "webm", "m4v", "mov")
                                      if (target / f"{output_stem}.{ext}").is_file()), None)
                        if media is None:
                            from .recovery import salvage_live_part
                            media = salvage_live_part(target, output_stem,
                                lambda path: db.add(AuditLog(video_id=v.id, action="SALVAGE_PARTIAL",
                                    reason="VERIFIED_DURATION", details={"path": path})),
                                ffmpeg_path=settings.ffmpeg_path)
                        db.commit()
                        inspected = _inspect_live_after_exit(v.youtube_video_id, ch.cookie_profile)
                        db.refresh(v)
                        verified_media = _verified_live_end_media(
                            v, target, output_stem, inspected, media, allow_is_live=True)
                        if verified_media is not None:
                            if inspected.get("live_status") == "is_live":
                                inspected = {**inspected, "live_status": "was_live"}
                            attempts = list((v.raw_metadata or {}).get("attempts", []))
                            for attempt in reversed(attempts):
                                if attempt.get("stem") == output_stem and attempt.get("outcome") == "running":
                                    attempt["exit_code"] = proc.returncode
                                    attempt["completion_reason"] = "complete_after_live_end_error"
                                    break
                            v.raw_metadata = {**(v.raw_metadata or {}), "attempts": attempts}
                            from .recovery import probe_duration
                            duration = probe_duration(verified_media) or 0
                            expected = inspected.get("duration")
                            v.raw_metadata = {
                                **(v.raw_metadata or {}),
                                "live_end_exit_evidence": {
                                    "error": "Did not get any data blocks",
                                    "exit_code": proc.returncode,
                                    "duration_seconds": duration,
                                    "youtube_duration_seconds": expected,
                                },
                            }
                            db.add(AuditLog(video_id=v.id, action="LIVE_END_ERROR_ACCEPTED",
                                reason="VERIFIED_FULL_MEDIA", details={
                                    "exit_code": proc.returncode,
                                    "stderr_tail": (stderr or b"").decode("utf-8", "replace")[-1000:],
                                    "media_path": str(verified_media),
                                    "duration_seconds": duration,
                                    "youtube_duration_seconds": expected,
                                }))
                            log.info("accepted complete live media after yt-dlp live-end error "
                                     "youtube_video_id=%s path=%s duration=%.3f",
                                     v.youtube_video_id, verified_media, duration)
                            _complete_recording(db, v, ch, target, output_stem, profile,
                                                is_live, verified_media, lease, inspected)
                            (target / ".recorder.json").unlink(missing_ok=True)
                            return
                    log.error("recording failed youtube_video_id=%s channel_id=%s reason=%s",
                              v.youtube_video_id, ch.youtube_channel_id,
                              stderr.decode("utf-8", "replace")[-1000:] if isinstance(stderr, bytes)
                              else str(stderr or error_kind)[-1000:])
                    if error_kind == "UPCOMING":
                        v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "upcoming", now())
                        v.status = "WAITING_LIVE"
                        v.raw_metadata = {**(v.raw_metadata or {}), "live_status": "is_upcoming"}
                        v.recording_heartbeat_at = None
                        (target / ".recorder.json").unlink(missing_ok=True)
                        db.commit()
                        return
                    v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "failed", now())
                    user_stopped = bool((v.raw_metadata or {}).get("user_stopped") or user_stopping(v.id))
                    shutdown_requested = stopping(v.id) and not user_stopped
                    rescued = None
                    if is_live:
                        from .recovery import salvage_live_part
                        rescued = salvage_live_part(target, output_stem,
                            lambda path: db.add(AuditLog(video_id=v.id, action="SALVAGE_PARTIAL", reason="VERIFIED_DURATION", details={"path": path})),
                            ffmpeg_path=settings.ffmpeg_path)
                        _update_partial_evidence(v, target, output_stem)
                    if user_stopped and rescued:
                        v.status = "ACTIVE"
                        v.is_partial = True
                        v.recording_finished_at = now()
                        v.retention_expires_at = None
                    else:
                        v.status = "RECORDING_FAILED"
                        v.recording_finished_at = None
                        v.next_recovery_at = None if shutdown_requested else now() + recovery_delay(max(1, v.recovery_attempts or 0))
                    v.recording_heartbeat_at = None
                    # Never remove files on a failed recording attempt: a target can
                    # contain a previously completed archive.
                    # 失敗ログは target 削除後も残るよう親ディレクトリへ書く。
                    _write_recording_error(target, v.youtube_video_id, stderr)
                    (target / ".recorder.json").unlink(missing_ok=True)
                    if user_stopped:
                        chat_file = _chat_file(target, output_stem)
                        if chat_file.is_file():
                            try:
                                from .chat import ingest_jsonl
                                ingest_jsonl(db, v.id, chat_file, complete_snapshot=False, isolated=True)
                            except Exception:
                                log.warning("stopped live chat import failed for %s", v.youtube_video_id)
                    if user_stopped:
                        v.raw_metadata = {**(v.raw_metadata or {}), "user_stopped": True}
                    db.commit()
                    return
                media = next((target / f"{output_stem}.{ext}" for ext in ("mkv", "mp4", "ts", "webm", "m4v", "mov")
                              if (target / f"{output_stem}.{ext}").is_file()), None)
                if not media or media.stat().st_size <= 0:
                    log.error("recording failed youtube_video_id=%s channel_id=%s reason=%s",
                              v.youtube_video_id, ch.youtube_channel_id,
                              "yt-dlp exited successfully but no non-empty media file was found")
                    v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "missing_media", now())
                    if is_live:
                        from .recovery import salvage_live_part
                        salvage_live_part(target, output_stem,
                            lambda path: db.add(AuditLog(video_id=v.id, action="SALVAGE_PARTIAL", reason="VERIFIED_DURATION", details={"path": path})),
                            ffmpeg_path=settings.ffmpeg_path)
                        _update_partial_evidence(v, target, output_stem)
                    v.status = "RECORDING_FAILED"
                    v.recording_finished_at = None
                    v.recording_heartbeat_at = None
                    v.next_recovery_at = None if stopping(v.id) else now() + recovery_delay(max(1, v.recovery_attempts or 0))
                    _write_recording_error(target, v.youtube_video_id,
                        "yt-dlp exited successfully but no non-empty media file was found.\n")
                    (target / ".recorder.json").unlink(missing_ok=True)
                    db.commit()
                    return
                inspected = {}
                if is_live:
                    db.commit()
                    inspected = _inspect_live_after_exit(v.youtube_video_id, ch.cookie_profile)
                    db.refresh(v)
                    if inspected.get("live_status") == "is_live":
                        from .recovery import probe_duration
                        duration = probe_duration(media)
                        verified_media = _verified_live_end_media(
                            v, target, output_stem, inspected, media, allow_is_live=True)
                        if verified_media is not None:
                            inspected = {**inspected, "live_status": "was_live"}
                        else:
                            v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile,
                                                            "interrupted_live", now())
                            v.recording_heartbeat_at = None
                            v.is_partial = True
                            if duration is not None and duration >= 30:
                                v.status = "DISCOVERED"
                                v.recording_finished_at = None
                                v.next_recovery_at = None
                                v.raw_metadata = {**(v.raw_metadata or {}), "live_status": "is_live"}
                                db.add(AuditLog(video_id=v.id, action="LIVE_SEGMENT_INTERRUPTED", reason="STREAM_STILL_LIVE"))
                            else:
                                v.status = "RECORDING_FAILED"
                                v.recording_finished_at = None
                                v.next_recovery_at = now() + recovery_delay(max(1, v.recovery_attempts or 0))
                            _update_partial_evidence(v, target, output_stem)
                            if ch.chat_enabled:
                                _ingest_chat_snapshot(db, v, target, output_stem)
                            manifest = target / ".recorder.json"
                            manifest.unlink(missing_ok=True)
                            db.commit()
                            return
                _complete_recording(db, v, ch, target, output_stem, profile,
                                    is_live, media, lease, inspected)
                (target / ".recorder.json").unlink(missing_ok=True)
            except RecordingCommitUnavailable as exc:
                db.rollback()
                for process in (active_recorders.pop(video_id, None), active_upstreams.pop(video_id, None)):
                    stop_process(process)
                _finish_chat_process(video_id)
                log.error("completed recording preserved for database recovery youtube_video_id=%s channel_id=%s: %s",
                          worker_youtube_id, ch.youtube_channel_id, exc)
                return
            except Exception as exc:
                # A failed query/commit leaves SQLAlchemy's transaction unusable.
                # Clear it before updating recovery state; the recording lock remains
                # the only DB session intentionally held for the recording lifetime.
                db.rollback()
                v = db.get(Video, worker_video_id)
                if v is None:
                    log.exception("recording row disappeared during failure handling")
                    return
                if output_stem is not None and profile is not None:
                    v.raw_metadata = finish_attempt(v.raw_metadata or {}, output_stem, profile, "failed", now())
                    if profile == "live" and v.file_path:
                        from .recovery import salvage_live_part
                        try:
                            salvage_live_part(Path(v.file_path), output_stem,
                                lambda path: db.add(AuditLog(video_id=v.id, action="SALVAGE_PARTIAL",
                                    reason="VERIFIED_DURATION", details={"path": path})),
                                ffmpeg_path=settings.ffmpeg_path)
                            _update_partial_evidence(v, Path(v.file_path), output_stem)
                        except Exception as salvage_error:
                            log.warning("recording salvage failed for %s: %s", v.youtube_video_id, salvage_error)
                v.status = "WAITING_FOR_STORAGE" if not v.file_path else "RECORDING_FAILED"
                try:
                    db.commit()
                except Exception:
                    db.rollback()
                proc = active_recorders.pop(v.id, None)
                stop_process(proc)
                upstream = active_upstreams.pop(v.id, None)
                stop_process(upstream)
                _finish_chat_process(v.id)
                # Intentionally leave file_path and all recording evidence untouched.
                # A failed or partial recording is never evidence that it is safe to delete.
                v.recording_finished_at = None
                v.recording_heartbeat_at = None
                v.is_partial = bool(v.file_path and Path(v.file_path).is_dir() and any(p.is_file() and p.stat().st_size > 0 for p in Path(v.file_path).glob("*")))
                v.next_recovery_at = None if stopping(v.id) else now() + recovery_delay(max(1, v.recovery_attempts))
                if v.file_path:
                    (Path(v.file_path) / ".recorder.json").unlink(missing_ok=True)
                    _write_recording_error(Path(v.file_path), v.youtube_video_id, str(exc))
                db.commit()
                log.exception("recording failed youtube_video_id=%s channel_id=%s: %s",
                              worker_youtube_id, ch.youtube_channel_id, exc)
