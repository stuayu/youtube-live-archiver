"""Parse and ingest yt-dlp live-chat JSONL without losing its source records."""

import json
import re
import tempfile
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, exists, and_, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..db import SessionLocal
from ..models import ChatMessage, Channel, ProcessingJob, Video, now
from .locks import default_locks

RENDERER_TYPES = {
    "liveChatTextMessageRenderer": "normal",
    "liveChatPaidMessageRenderer": "superchat",
    "liveChatPaidStickerRenderer": "supersticker",
    "liveChatMembershipItemRenderer": "membership",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": "membership_gift",
    "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer": "membership_gift_received",
}
_asset_prefetch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-assets")
_asset_prefetch_lock = threading.Lock()
_asset_prefetch_pending: set[int] = set()
_oversized_chat_warnings: set[int] = set()
log = logging.getLogger(__name__)
IGNORED_RENDERERS = {
    "liveChatViewerEngagementMessageRenderer",
    "liveChatModeChangeMessageRenderer",
    "liveChatTickerPaidMessageItemRenderer",
    "liveChatTickerPaidStickerItemRenderer",
    "liveChatTickerSponsorItemRenderer",
}
CURRENCY_PREFIXES = (
    ("US$", "USD"), ("CA$", "CAD"), ("A$", "AUD"), ("NT$", "TWD"),
    ("HK$", "HKD"), ("SGD", "SGD"), ("MX$", "MXN"), ("R$", "BRL"),
    ("¥", "JPY"), ("￥", "JPY"), ("$", "USD"), ("₩", "KRW"),
    ("€", "EUR"), ("£", "GBP"), ("₱", "PHP"), ("₹", "INR"),
    ("RM", "MYR"), ("฿", "THB"), ("Rp", "IDR"),
)


def playback_offset_ms(message: dict[str, Any]) -> int:
    """yt-dlp chat offsets are relative to the original live stream."""
    for key in ("videoOffsetTimeMsec", "offset_ms", "offsetMs"):
        value = message.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                pass
    for key in ("videoOffsetTimeSeconds", "offset_seconds"):
        try:
            return max(0, int(float(message[key]) * 1000))
        except (KeyError, TypeError, ValueError):
            pass
    return 0


def _published_at(renderer: dict[str, Any]):
    from datetime import datetime, timezone
    value = renderer.get("timestampUsec")
    try:
        return datetime.fromtimestamp(int(value) / 1_000_000, tz=timezone.utc) if value else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _release_epoch(video: Video | None) -> float | None:
    value = (video.raw_metadata or {}).get("release_timestamp") if video else None
    try:
        epoch = float(value) if value is not None else None
        return epoch / 1000 if epoch and epoch > 10_000_000_000 else epoch
    except (TypeError, ValueError, OverflowError):
        return None


def _live_record(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    record = raw.get("record", raw)
    return record if isinstance(record, dict) else {}


def _recording_start_epoch(video: Video | None) -> float | None:
    if video is None:
        return None
    attempts = (video.raw_metadata or {}).get("attempts", [])
    started = next((item.get("started_at") for item in attempts
                    if isinstance(item, dict) and item.get("started_at")), None)
    value = started or video.recorded_at
    if value is None:
        return None
    try:
        from datetime import datetime
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return _epoch_seconds(parsed)
    except (TypeError, ValueError, OverflowError):
        return None


def _correct_live_offset(parsed: dict[str, Any], raw: Any, release_epoch: float | None,
                         recording_start_epoch: float | None, recording_offset_ms: int) -> None:
    published_at = parsed.get("published_at")
    if _live_record(raw).get("isLive") is not True or not published_at:
        return
    published_epoch = _epoch_seconds(published_at)
    if release_epoch is not None:
        parsed["offset_ms"] = max(0, int((published_epoch - release_epoch) * 1000))
    elif recording_start_epoch is not None:
        parsed["offset_ms"] = max(
            0, recording_offset_ms + int((published_epoch - recording_start_epoch) * 1000)
        )


def _resolve_release_epoch(db, video: Video, *, inspect_missing: bool) -> float | None:
    """Read the persisted stream start, then the recording info file, then optionally inspect."""
    release = _release_epoch(video)
    if release is None and video.file_path:
        directory = Path(video.file_path)
        if directory.is_dir():
            for info_path in sorted(directory.glob("*.info.json")):
                try:
                    info = json.loads(info_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                release = _release_epoch(Video(raw_metadata={"release_timestamp": info.get("release_timestamp")}))
                if release is not None:
                    break
    if release is None and inspect_missing:
        try:
            from .youtube import inspect

            channel = video.channel
            metadata = inspect(
                f"https://www.youtube.com/watch?v={video.youtube_video_id}",
                channel.cookie_profile if channel else None,
            )
            release = _release_epoch(Video(raw_metadata=metadata))
        except Exception:
            release = None
    if release is not None and release != _release_epoch(video):
        video.raw_metadata = {**(video.raw_metadata or {}), "release_timestamp": release}
        db.flush()
    return release


def _epoch_seconds(value) -> float:
    from datetime import timezone
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _simple_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("simpleText"), str):
            return value["simpleText"]
        return parse_runs(value.get("runs"))[0]
    return ""


def parse_runs(value: Any) -> tuple[str, list[dict[str, str]]]:
    """Return searchable plain text plus typed display runs."""
    if isinstance(value, str):
        return value, [{"type": "text", "text": value}] if value else []
    if isinstance(value, dict):
        value = value.get("runs", [])
    if not isinstance(value, list):
        return "", []
    plain: list[str] = []
    runs: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        if isinstance(text, str) and text:
            plain.append(text)
            runs.append({"type": "text", "text": text})
            continue
        emoji = item.get("emoji")
        if not isinstance(emoji, dict):
            continue
        emoji_id = str(emoji.get("emojiId") or "")
        if not emoji.get("isCustomEmoji"):
            char = emoji_id or _simple_text(emoji.get("shortcuts", [""])[0])
            if char:
                plain.append(char)
                runs.append({"type": "emoji", "text": char})
            continue
        shortcuts = emoji.get("shortcuts")
        shortcut = str(shortcuts[0]) if isinstance(shortcuts, list) and shortcuts else emoji_id
        thumbnails = (emoji.get("image") or {}).get("thumbnails", [])
        url = str(thumbnails[-1].get("url", "")) if thumbnails and isinstance(thumbnails[-1], dict) else ""
        if shortcut:
            plain.append(shortcut)
        if emoji_id:
            runs.append({"type": "emote", "id": emoji_id, "shortcut": shortcut, "url": url})
    return "".join(plain), runs


def argb_to_hex(value: Any) -> str | None:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            return value.lower()
        return None
    return f"#{(number >> 16) & 255:02x}{(number >> 8) & 255:02x}{number & 255:02x}"


def parse_amount(display: str | None) -> tuple[float | None, str | None]:
    if not display:
        return None, None
    compact = display.replace(",", "").replace(" ", "")
    currency = next((code for prefix, code in CURRENCY_PREFIXES if compact.startswith(prefix)), None)
    match = re.search(r"\d+(?:\.\d+)?", compact)
    try:
        return (float(match.group()) if match else None), currency
    except ValueError:
        return None, currency


def _badges(renderer: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    output: list[dict[str, str]] = []
    role = "viewer"
    for raw in renderer.get("authorBadges", []):
        badge = raw.get("liveChatAuthorBadgeRenderer", {}) if isinstance(raw, dict) else {}
        if not isinstance(badge, dict):
            continue
        label = str(badge.get("tooltip") or "")
        image = badge.get("icon", {}).get("thumbnails") or badge.get("customThumbnail", {}).get("thumbnails") or []
        url = str(image[-1].get("url", "")) if image and isinstance(image[-1], dict) else ""
        lowered = label.lower()
        kind = "member" if "member" in lowered else "moderator" if "moderator" in lowered else "owner" if "owner" in lowered else "verified" if "verified" in lowered else "badge"
        if kind == "owner":
            role = "owner"
        elif role != "owner" and kind == "moderator":
            role = "moderator"
        elif role == "viewer" and kind == "member":
            role = "member"
        output.append({"type": kind, "label": label, "url": url})
    return output, role


def _renderer_entries(record: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    candidates = [record]
    replay = record.get("replayChatItemAction", {})
    actions = replay.get("actions", record.get("actions", [])) if isinstance(replay, dict) else []
    for action in actions:
        added = action.get("addChatItemAction", {}).get("item", {}) if isinstance(action, dict) else {}
        if isinstance(added, dict):
            candidates.append(added)
    result: list[tuple[str, dict[str, Any]]] = []
    for candidate in candidates:
        for key, value in candidate.items():
            if key in RENDERER_TYPES and isinstance(value, dict):
                result.append((key, value))
            elif key == "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer" and isinstance(value, dict):
                result.append((key, value))
    return result


def parse_renderer(renderer_key: str, renderer: dict[str, Any]) -> dict[str, Any] | None:
    """Parse one supported YouTube renderer; unsupported/system rows return None."""
    message_type = RENDERER_TYPES.get(renderer_key)
    if not message_type:
        return None
    author = renderer.get("authorName", renderer.get("author_name", ""))
    author_name = _simple_text(author)
    header_text = None
    author_container = renderer
    if message_type == "membership_gift":
        header = renderer.get("header", {}).get("liveChatSponsorshipsHeaderRenderer", {})
        header_text = _simple_text(header.get("primaryText"))
        author_container = header
        author_name = _simple_text(header.get("authorName")) or author_name
    elif message_type == "membership":
        header_primary = _simple_text(renderer.get("headerPrimaryText"))
        header_sub = _simple_text(renderer.get("headerSubtext"))
        header_text = " · ".join(part for part in (header_primary, header_sub) if part) or None
    if message_type == "membership_gift_received":
        header_text = None
    text, runs = parse_runs(renderer.get("message", {}).get("runs", renderer.get("message", "")))
    if message_type == "membership_gift_received":
        text, runs = parse_runs(renderer.get("headerSubtext", renderer.get("message", "")))
    if message_type == "supersticker":
        sticker = renderer.get("sticker", {})
        image = sticker.get("thumbnails", []) if isinstance(sticker, dict) else []
        sticker_url = str(image[-1].get("url", "")) if image and isinstance(image[-1], dict) else None
        text = _simple_text(renderer.get("stickerText"))
        if text:
            runs = [{"type": "text", "text": text}]
    else:
        sticker_url = None
    display = _simple_text(renderer.get("purchaseAmountText"))
    amount, currency = parse_amount(display)
    badges, role = _badges(author_container)
    if message_type == "membership":
        badges.append({"type": "member", "label": header_text or "メンバー", "url": ""})
        role = "member" if role == "viewer" else role
    return {
        "message_type": message_type,
        "message_text": text,
        "message_runs": runs,
        "author_name": author_name,
        "author_channel_id": renderer.get("authorExternalChannelId") or renderer.get("authorChannelId"),
        "author_badges": badges,
        "author_role": role,
        "amount_display": display or None,
        "superchat_amount": amount,
        "superchat_currency": currency,
        "superchat_color": argb_to_hex(renderer.get("bodyBackgroundColor") or renderer.get("headerBackgroundColor")),
        "header_text": header_text,
        "sticker_url": sticker_url,
    }


def parse_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    parsed = []
    replay = record.get("replayChatItemAction", {})
    for key, renderer in _renderer_entries(record):
        result = parse_renderer(key, renderer)
        if not result:
            continue
        result["message_id"] = str(renderer.get("id") or renderer.get("message_id") or "")
        result["published_at"] = _published_at(renderer)
        source = {**record, "videoOffsetTimeMsec": replay.get("videoOffsetTimeMsec", record.get("videoOffsetTimeMsec"))}
        result["offset_ms"] = playback_offset_ms(source)
        result["raw"] = {"record": record, "renderer": renderer, "renderer_type": key}
        parsed.append(result)
    return [item for item in parsed if item["message_id"]]


def parse_raw_message(raw: Any, fallback_type: str | None = None) -> dict[str, Any] | None:
    """Parse both current raw records and the legacy {record, renderer} envelope."""
    if not isinstance(raw, dict):
        return None
    record = raw.get("record", raw)
    if not isinstance(record, dict):
        return None
    renderer = raw.get("renderer")
    key = raw.get("renderer_type")
    if isinstance(renderer, dict) and isinstance(key, str):
        parsed = parse_renderer(key, renderer)
        if parsed:
            parsed["message_id"] = str(renderer.get("id") or renderer.get("message_id") or "")
            parsed["published_at"] = _published_at(renderer)
            parsed["raw"] = raw
            parsed["offset_ms"] = playback_offset_ms(record)
            return parsed
    entries = _renderer_entries(record)
    if entries:
        selected = next(
            (item for item in entries if item[1] == renderer),
            None,
        )
        if selected is None:
            selected = next(
                (item for item in entries if RENDERER_TYPES.get(item[0]) == fallback_type),
                entries[0],
            )
        parsed = parse_record(record)
        message_id = str((renderer or {}).get("id") or "") if isinstance(renderer, dict) else ""
        return next(
            (item for item in parsed if item["message_id"] == message_id),
            parsed[0] if parsed else None,
        )
    if isinstance(renderer, dict):
        keyed = next(
            ((name, renderer[name]) for name in RENDERER_TYPES if isinstance(renderer.get(name), dict)),
            None,
        )
        if keyed:
            parsed = parse_renderer(*keyed)
            if parsed:
                parsed["message_id"] = str(keyed[1].get("id") or "")
                parsed["published_at"] = _published_at(keyed[1])
                parsed["raw"] = raw
                parsed["offset_ms"] = playback_offset_ms(record)
                return parsed
        key = next((name for name, kind in RENDERER_TYPES.items() if kind == fallback_type), None)
        if key:
            parsed = parse_renderer(key, renderer)
            if parsed:
                parsed["message_id"] = str(renderer.get("id") or "")
                parsed["published_at"] = _published_at(renderer)
                parsed["raw"] = raw
                parsed["offset_ms"] = playback_offset_ms(record)
                return parsed
    return None


def _deleted_ids(records: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    messages: set[str] = set()
    authors: set[str] = set()
    for record in records:
        replay = record.get("replayChatItemAction", {})
        actions = replay.get("actions", record.get("actions", [])) if isinstance(replay, dict) else []
        for action in actions:
            if not isinstance(action, dict):
                continue
            deleted = action.get("markChatItemAsDeletedAction", {}).get("deletedStateMessage", {})
            if deleted.get("targetItemId"):
                messages.add(str(deleted["targetItemId"]))
            author = action.get("markChatItemsByAuthorAsDeletedAction", {}).get("externalChannelId")
            if author:
                authors.add(str(author))
    return messages, authors


def ingest_records(db, video_id: int, records: list[dict[str, Any]], *, complete_snapshot: bool = True) -> int:
    """Upsert parsed messages; existing deletion state is never overwritten."""
    seen: set[str] = set()
    removed, removed_authors = _deleted_ids(records)
    timestamp = now()
    video = db.get(Video, video_id) if db is not None else None
    release_epoch = _resolve_release_epoch(db, video, inspect_missing=False) if video else None
    recording_start_epoch = _recording_start_epoch(video)
    recording_offset_ms = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0))) if video else 0
    parsed_messages: dict[str, dict[str, Any]] = {}
    for record in records:
        for parsed in parse_record(record):
            _correct_live_offset(parsed, record, release_epoch, recording_start_epoch, recording_offset_ms)
            mid = parsed["message_id"]
            seen.add(mid)
            parsed_messages.setdefault(mid, parsed)
    old_rows = []
    if db is not None:
        seen_ids = sorted(seen)
        for start in range(0, len(seen_ids), 500):
            old_rows.extend(db.scalars(select(ChatMessage).where(
                ChatMessage.message_id.in_(seen_ids[start:start + 500]))).all())
    old_by_id = {message.message_id: message for message in old_rows}
    update_keys = ("author_channel_id", "author_name", "author_badges", "author_role", "message_text",
                   "message_runs", "amount_display", "superchat_amount", "superchat_currency",
                   "superchat_color", "message_type", "header_text", "sticker_url")
    for mid, parsed in parsed_messages.items():
        old = old_by_id.get(mid)
        if old:
            for key in update_keys:
                setattr(old, key, parsed[key])
            # Keep offsets stable across delta ingest and final snapshot parses.
            # Both are derived from the same published timestamp and recording metadata.
            old.offset_ms = parsed["offset_ms"]
            old.published_at = parsed.get("published_at")
            old.raw = parsed["raw"]
            old.last_seen_at = timestamp
    new_rows = [
        {
            "video_id": video_id, "message_id": mid, "published_at": parsed.get("published_at"),
            "first_seen_at": timestamp, "last_seen_at": timestamp, "is_deleted": False,
            "deleted_at": None, "deletion_source": None, "offset_ms": parsed["offset_ms"],
            "raw": parsed["raw"],
            **{key: value for key, value in parsed.items()
               if key not in {"message_id", "offset_ms", "published_at", "raw"}},
        }
        for mid, parsed in parsed_messages.items() if mid not in old_by_id
    ]
    if new_rows and db is not None:
        dialect = db.get_bind().dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert if dialect == "sqlite" else None
        if insert is None:
            db.add_all(ChatMessage(**row) for row in new_rows)
        else:
            for start in range(0, len(new_rows), 500):
                db.execute(insert(ChatMessage).values(new_rows[start:start + 500])
                           .on_conflict_do_nothing(index_elements=["message_id"]))
    db.flush()
    if complete_snapshot:
        rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
        for message in rows:
            if message.message_id in removed or message.author_channel_id in removed_authors:
                message.is_deleted = True
                message.deleted_at = timestamp
                message.deletion_source = "判定不能"
            elif complete_snapshot and message.message_id not in seen and not message.is_deleted:
                message.is_deleted = True
                message.deleted_at = timestamp
                message.deletion_source = "判定不能"
    elif removed or removed_authors:
        delete_stmt = select(ChatMessage).where(ChatMessage.video_id == video_id)
        predicates = []
        if removed:
            predicates.append(ChatMessage.message_id.in_(removed))
        if removed_authors:
            predicates.append(ChatMessage.author_channel_id.in_(removed_authors))
        for message in db.scalars(delete_stmt.where(or_(*predicates))).all():
            message.is_deleted = True
            message.deleted_at = timestamp
            message.deletion_source = "判定不能"
    db.flush()
    return len(seen)


def reparse_video_chat(db, video_id: int) -> int:
    """Recompute parsed columns in place from raw records, preserving deletion state."""
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
    video = db.get(Video, video_id)
    needs_release = any(_live_record(row.raw).get("isLive") is True for row in rows)
    release_epoch = _resolve_release_epoch(db, video, inspect_missing=True) if video and needs_release else None
    recording_start_epoch = _recording_start_epoch(video)
    recording_offset_ms = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0))) if video else 0
    for row in rows:
        parsed = parse_raw_message(row.raw, row.message_type)
        if not parsed:
            continue
        if _live_record(row.raw).get("isLive") is True:
            if release_epoch is not None:
                _correct_live_offset(parsed, row.raw, release_epoch, recording_start_epoch, recording_offset_ms)
            else:
                parsed["offset_ms"] = row.offset_ms
        for name in ("author_channel_id", "author_name", "author_badges", "author_role", "message_text",
                     "message_runs", "amount_display", "superchat_amount", "superchat_currency",
                     "superchat_color", "message_type", "header_text", "sticker_url", "published_at", "offset_ms"):
            value = parsed.get(name)
            setattr(row, name, value)
    db.flush()
    return len(rows)


def rebase_live_chat_offsets(db, video_id: int) -> int:
    video = db.get(Video, video_id)
    release_epoch = _release_epoch(video)
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
    recording_start_epoch = _recording_start_epoch(video)
    recording_offset_ms = max(0, int((video.raw_metadata or {}).get("recording_start_offset_ms", 0)))
    changed = 0
    for row in rows:
        record = (row.raw or {}).get("record", {})
        if not record.get("isLive") or not row.published_at:
            continue
        published_epoch = _epoch_seconds(row.published_at)
        if release_epoch is not None:
            row.offset_ms = max(0, int((published_epoch - release_epoch) * 1000))
        elif recording_start_epoch is not None:
            row.offset_ms = max(0, recording_offset_ms + int((published_epoch - recording_start_epoch) * 1000))
        else:
            continue
        changed += 1
    db.flush()
    return changed


def enqueue_replay_retry(db, video_id: int, *, immediate: bool = False,
                         restart: bool = False) -> ProcessingJob | None:
    from datetime import timedelta
    video = db.get(Video, video_id)
    if not video or (video.raw_metadata or {}).get("chat_replay") == "imported":
        return None
    job = db.scalar(select(ProcessingJob).where(
        ProcessingJob.video_id == video_id, ProcessingJob.kind == "CHAT_REPLAY"))
    if job:
        if restart and job.status != "processing":
            job.status, job.attempts, job.error = "retry_wait", 0, None
            job.next_attempt_at, job.finished_at = now(), None
            job.payload = {**(job.payload or {}), "manual_queue": True}
            db.commit()
        return job
    job = ProcessingJob(video_id=video_id, kind="CHAT_REPLAY", status="retry_wait",
                        attempts=0, next_attempt_at=now() if immediate else now() + timedelta(hours=1),
                        payload={"manual_queue": immediate})
    db.add(job)
    db.flush()
    return job

_replay_backfill_checked_at = None


def chat_replay_worker() -> None:
    """Recover and run one due replay job per scheduler tick."""
    global _replay_backfill_checked_at
    at = now()
    from .monitor import _cooldown_active
    if _cooldown_active(at):
        return

    with SessionLocal() as db:
        interrupted = db.scalars(select(ProcessingJob.id).where(
            ProcessingJob.kind == "CHAT_REPLAY", ProcessingJob.status == "processing")).all()
    for job_id in interrupted:
        with default_locks.acquire("processing-job", str(job_id)) as lease:
            if not lease.acquired:
                continue
            with SessionLocal() as db:
                job = db.get(ProcessingJob, job_id)
                if job and job.status == "processing":
                    job.status, job.next_attempt_at, job.updated_at = "retry_wait", at, at
                    db.commit()

    if _replay_backfill_checked_at is None or at - _replay_backfill_checked_at >= timedelta(minutes=10):
        _replay_backfill_checked_at = at
        sql = text("""SELECT v.id, v.file_path FROM videos AS v
            WHERE v.file_path IS NOT NULL AND v.recording_finished_at IS NOT NULL
              AND v.status NOT IN ('DELETED_BY_RETENTION', 'DELETED_MANUALLY')
              AND (v.raw_metadata ->> 'chat_replay' IS NULL OR v.raw_metadata ->> 'chat_replay' <> 'imported')
              AND EXISTS (SELECT 1 FROM channels c WHERE c.id = v.channel_id AND c.chat_enabled = TRUE)
              AND NOT EXISTS (SELECT 1 FROM processing_jobs j WHERE j.video_id = v.id AND j.kind = 'CHAT_REPLAY')
            ORDER BY v.id DESC LIMIT 20""")
        with SessionLocal() as db:
            missing = db.execute(sql).all()
            for index, (video_id, file_path) in enumerate(missing):
                path = Path(file_path)
                if not path.exists():
                    continue
                db.add(ProcessingJob(video_id=video_id, kind="CHAT_REPLAY", status="retry_wait",
                    attempts=0, next_attempt_at=at + timedelta(minutes=5 * index), payload={}))
            db.commit()

    if _cooldown_active(now()):
        return
    with SessionLocal() as db:
        job_id = db.scalar(select(ProcessingJob.id).where(
            ProcessingJob.kind == "CHAT_REPLAY", ProcessingJob.status.in_(("pending", "retry_wait")),
            or_(ProcessingJob.status == "pending", ProcessingJob.next_attempt_at <= now())
        ).order_by(ProcessingJob.payload["manual_queue"].as_string().desc().nullslast(),
                   ProcessingJob.next_attempt_at.asc().nullsfirst(), ProcessingJob.id.asc()).limit(1))
    if job_id is None:
        return
    with default_locks.acquire("processing-job", str(job_id)) as lease:
        if not lease.acquired:
            return
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job and job.status in ("pending", "retry_wait") and (
                    job.status == "pending" or job.next_attempt_at is None or job.next_attempt_at <= now()):
                process_replay_retry(db, job)
                db.commit()
    if _cooldown_active(now()):
        return


def process_replay_retry(db, job: ProcessingJob) -> None:
    from datetime import timedelta
    from .youtube import classify_ytdlp_error
    video = db.get(Video, job.video_id)
    if not video:
        job.status = "failed"
        job.finished_at = now()
        return
    if (video.raw_metadata or {}).get("chat_replay") == "imported":
        job.status = "completed"
        job.finished_at = now()
        return
    channel = video.channel
    if channel and not channel.chat_enabled:
        job.status, job.finished_at = "disabled", now()
        db.commit()
        return
    if job.status == "processing":
        job.status, job.next_attempt_at = "retry_wait", now()
    job.status, job.started_at, job.updated_at = "processing", now(), now()
    job.payload = {key: value for key, value in (job.payload or {}).items() if key != "manual_queue"}
    db.commit()
    try:
        retrieve_replay(db, video.id, video.youtube_video_id, channel.cookie_profile if channel else None)
    except Exception as exc:
        error = str(exc)
        kind = classify_ytdlp_error(error)
        video = db.get(Video, job.video_id)
        metadata = dict(video.raw_metadata or {})
        limited = kind == "RATE_LIMITED"
        if limited:
            from .monitor import _apply_rate_limit
            _apply_rate_limit(now())
        metadata.update(chat_replay="rate_limited" if limited else "failed",
                        chat_replay_checked_at=now().isoformat(),
                        chat_replay_error="アクセス制限中です" if limited else "リプレイ取得に失敗しました")
        video.raw_metadata = metadata
        job.error = metadata["chat_replay_error"]
        job.updated_at = now()
        if limited:
            job.status, job.next_attempt_at = "retry_wait", now() + timedelta(hours=1)
        else:
            job.attempts += 1
            job.status = "failed" if job.attempts >= 10 else "retry_wait"
            delay = (1, 3, 6, 12, 24)[min(job.attempts - 1, 4)]
            job.next_attempt_at = None if job.status == "failed" else now() + timedelta(hours=delay)
        db.commit()
        return
    if (video.raw_metadata or {}).get("chat_replay") == "rate_limited":
        from .monitor import _apply_rate_limit
        _apply_rate_limit(now())
    rebase_live_chat_offsets(db, video.id)
    job.updated_at = now()
    metadata = dict(video.raw_metadata or {})
    if metadata.get("chat_replay") == "imported":
        job.status = "completed"
        job.finished_at = now()
        job.next_attempt_at = None
        job.error = None
    elif metadata.get("chat_replay") == "rate_limited":
        job.status = "retry_wait"
        job.error = metadata.get("chat_replay_error")
        job.next_attempt_at = now() + timedelta(hours=1)
    else:
        job.attempts += 1
        delay = (1, 3, 6, 12, 24)[min(job.attempts - 1, 4)]
        job.error = metadata.get("chat_replay_error")
        job.status = "failed" if job.attempts >= 10 else "retry_wait"
        job.finished_at = now() if job.status == "failed" else None
        job.next_attempt_at = None if job.status == "failed" else now() + timedelta(hours=delay)


def ingest_jsonl(db, video_id: int, path: str | Path, *, complete_snapshot: bool = True, isolated: bool = False) -> int:
    records = []
    with Path(path).open("r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    if not records:
        raise ValueError("empty chat file")
    if complete_snapshot and not any(parse_record(record) for record in records):
        raise ValueError("chat snapshot contains no recognizable items")
    if isolated:
        with db.begin_nested():
            count = ingest_records(db, video_id, records, complete_snapshot=complete_snapshot)
    else:
        count = ingest_records(db, video_id, records, complete_snapshot=complete_snapshot)
    _prefetch_assets(db, video_id)
    return count


def ingest_jsonl_delta(db, video_id: int, path: str | Path, offset: int = 0, *,
                       max_bytes: int = 1024 * 1024, max_records: int = 250,
                       max_seconds: float = 0.2) -> int:
    """Ingest only complete JSONL lines appended during recording."""
    source = Path(path)
    size = source.stat().st_size
    start = min(max(0, offset), size)
    if start >= size:
        return offset
    with source.open("rb") as file:
        file.seek(start)
        data = file.read(max(1, max_bytes))
    boundary = data.rfind(b"\n") + 1
    if boundary <= 0:
        if len(data) >= max(1, max_bytes) and size > start + len(data):
            if video_id not in _oversized_chat_warnings:
                _oversized_chat_warnings.add(video_id)
                log.warning("skipping oversized or malformed live chat line video_id=%s bytes=%s",
                            video_id, size - start)
            with source.open("rb") as file:
                file.seek(start + len(data))
                skipped = len(data)
                while True:
                    chunk = file.read(64 * 1024)
                    if not chunk:
                        return start
                    newline = chunk.find(b"\n")
                    if newline >= 0:
                        skipped += newline + 1
                        break
                    skipped += len(chunk)
            return start + skipped
        return start
    records = []
    consumed = 0
    deadline = time.monotonic() + max(0.01, max_seconds)
    for line in data[:boundary].splitlines(keepends=True):
        consumed += len(line)
        if time.monotonic() > deadline:
            consumed -= len(line)
            break
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
            if len(records) >= max(1, max_records):
                break
    if records:
        if db is None:
            ingest_records(db, video_id, records, complete_snapshot=False)
        else:
            with db.begin_nested():
                ingest_records(db, video_id, records, complete_snapshot=False)
        _schedule_asset_prefetch(video_id)
    return start + consumed


def _schedule_asset_prefetch(video_id: int) -> None:
    """Prefetch a small chat asset batch off the recorder's polling thread."""
    with _asset_prefetch_lock:
        if video_id in _asset_prefetch_pending:
            return
        _asset_prefetch_pending.add(video_id)

    def run() -> None:
        try:
            time.sleep(0.1)  # Let the recorder commit the delta before opening a fresh session.
            from ..config import settings
            from ..db import SessionLocal
            from .chat_assets import prefetch_video_assets

            with SessionLocal() as session:
                prefetch_video_assets(session, video_id, Path(settings.data_dir) / "chat-assets",
                                      limit=20, max_seconds=2.0)
        except Exception:
            log.debug("live chat asset prefetch failed for video_id=%s", video_id, exc_info=True)
        finally:
            with _asset_prefetch_lock:
                _asset_prefetch_pending.discard(video_id)

    _asset_prefetch_pool.submit(run)


def _prefetch_assets(db, video_id: int) -> None:
    try:
        from ..config import settings
        from .chat_assets import prefetch_video_assets

        prefetch_video_assets(db, video_id, Path(settings.data_dir) / "chat-assets", limit=500)
    except Exception:
        # Chat must remain durable even if an optional CDN image is unavailable.
        return


def retrieve_replay(db, video_id: int, youtube_video_id: str, cookie_profile: str | None = None) -> int:
    """Fetch optional replay through yt-dlp; automated tests replace the downloader."""
    from .youtube import run_ytdlp
    import logging

    log = logging.getLogger(__name__)
    if db.in_transaction():
        db.commit()
    with tempfile.TemporaryDirectory(prefix="yla-chat-") as temp:
        output = str(Path(temp) / "%(id)s.%(ext)s")
        args = ["--no-warnings", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--sub-format", "json", "-o", output, f"https://www.youtube.com/watch?v={youtube_video_id}"]
        proc = run_ytdlp(args, capture_output=True, text=True, timeout=3600, check=False)
        files = list(Path(temp).glob("*live_chat*")) + list(Path(temp).glob("*.live_chat.json"))
        video = db.get(Video, video_id)
        from .youtube import classify_ytdlp_error
        checked_at = now().isoformat()
        stderr = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else str(proc.stderr or "")
        if proc.returncode:
            limited = classify_ytdlp_error(stderr) == "RATE_LIMITED"
            if video is not None:
                video.raw_metadata = {**(video.raw_metadata or {}),
                    "chat_replay": "rate_limited" if limited else "failed",
                    "chat_replay_checked_at": checked_at,
                    "chat_replay_error": "アクセス制限中です" if limited else "リプレイ取得に失敗しました"}
                db.commit()
            log.info("chat replay unavailable for %s", youtube_video_id)
            return "rate_limited" if limited else 0
        if not files:
            if video is not None:
                video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "unavailable",
                    "chat_replay_checked_at": checked_at,
                    "chat_replay_error": "YouTube 側でリプレイがまだ用意されていません"}
                db.commit()
            return 0
        try:
            count = ingest_jsonl(db, video_id, files[0], complete_snapshot=True, isolated=True)
        except Exception:
            if video is not None:
                video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "failed",
                    "chat_replay_checked_at": checked_at,
                    "chat_replay_error": "リプレイの形式を読み取れませんでした"}
                db.commit()
            log.info("chat replay unavailable for %s", youtube_video_id)
            return 0
        if video is not None:
            video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "imported",
                "chat_replay_checked_at": checked_at, "chat_replay_error": None}
        db.commit()
        return count
