"""Parse and ingest yt-dlp live-chat JSONL without losing its source records."""

import json
import re
import tempfile
from pathlib import Path
from typing import Any

from sqlalchemy import select

from ..models import ChatMessage, ProcessingJob, Video, now

RENDERER_TYPES = {
    "liveChatTextMessageRenderer": "normal",
    "liveChatPaidMessageRenderer": "superchat",
    "liveChatPaidStickerRenderer": "supersticker",
    "liveChatMembershipItemRenderer": "membership",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": "membership_gift",
    "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer": "membership_gift_received",
}
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


def _correct_live_offset(parsed: dict[str, Any], raw: Any, release_epoch: float | None) -> None:
    published_at = parsed.get("published_at")
    if _live_record(raw).get("isLive") is True and release_epoch is not None and published_at:
        parsed["offset_ms"] = max(0, int((_epoch_seconds(published_at) - release_epoch) * 1000))


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
    for record in records:
        for parsed in parse_record(record):
            _correct_live_offset(parsed, record, release_epoch)
            mid = parsed["message_id"]
            if mid in seen:
                continue
            seen.add(mid)
            old = db.scalar(select(ChatMessage).where(ChatMessage.message_id == mid))
            if old:
                for key in ("author_channel_id", "author_name", "author_badges", "author_role", "message_text", "message_runs", "amount_display", "superchat_amount", "superchat_currency", "superchat_color", "message_type", "header_text", "sticker_url"):
                    setattr(old, key, parsed[key])
                old.offset_ms = parsed["offset_ms"]
                old.published_at = parsed.get("published_at")
                old.raw = parsed["raw"]
                old.last_seen_at = timestamp
            else:
                db.add(ChatMessage(
                    video_id=video_id,
                    message_id=mid,
                    published_at=parsed.get("published_at"),
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                    is_deleted=False,
                    deleted_at=None,
                    deletion_source=None,
                    offset_ms=parsed["offset_ms"],
                    raw=parsed["raw"],
                    **{
                        key: value
                        for key, value in parsed.items()
                        if key not in {"message_id", "offset_ms", "published_at", "raw"}
                    },
                ))
    db.flush()
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
    db.flush()
    return len(seen)


def reparse_video_chat(db, video_id: int) -> int:
    """Recompute parsed columns in place from raw records, preserving deletion state."""
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
    video = db.get(Video, video_id)
    needs_release = any(_live_record(row.raw).get("isLive") is True for row in rows)
    release_epoch = _resolve_release_epoch(db, video, inspect_missing=True) if video and needs_release else None
    for row in rows:
        parsed = parse_raw_message(row.raw, row.message_type)
        if not parsed:
            continue
        if _live_record(row.raw).get("isLive") is True:
            if release_epoch is not None:
                _correct_live_offset(parsed, row.raw, release_epoch)
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
    if not release_epoch:
        return 0
    rows = db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()
    changed = 0
    for row in rows:
        record = (row.raw or {}).get("record", {})
        if not record.get("isLive") or not row.published_at:
            continue
        row.offset_ms = max(0, int(_epoch_seconds(row.published_at) * 1000 - release_epoch * 1000))
        changed += 1
    db.flush()
    return changed


def enqueue_replay_retry(db, video_id: int) -> ProcessingJob | None:
    from datetime import timedelta
    video = db.get(Video, video_id)
    if not video or (video.raw_metadata or {}).get("chat_replay") == "imported":
        return None
    job = db.scalar(select(ProcessingJob).where(
        ProcessingJob.video_id == video_id, ProcessingJob.kind == "CHAT_REPLAY"))
    if job:
        return job
    job = ProcessingJob(video_id=video_id, kind="CHAT_REPLAY", status="retry_wait",
                        attempts=0, next_attempt_at=now() + timedelta(hours=1),
                        payload={})
    db.add(job)
    db.flush()
    return job


def process_replay_retry(db, job: ProcessingJob) -> None:
    from datetime import timedelta
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
    retrieve_replay(db, video.id, video.youtube_video_id, channel.cookie_profile)
    rebase_live_chat_offsets(db, video.id)
    job.attempts += 1
    job.updated_at = now()
    if (video.raw_metadata or {}).get("chat_replay") == "imported":
        job.status = "completed"
        job.finished_at = now()
        job.next_attempt_at = None
    elif job.attempts >= 7:
        job.status = "failed"
        job.finished_at = now()
        job.next_attempt_at = None
    else:
        delay_hours = 6 if job.attempts == 1 else 24
        job.status = "retry_wait"
        job.next_attempt_at = now() + timedelta(hours=delay_hours)


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


def ingest_jsonl_delta(db, video_id: int, path: str | Path, offset: int = 0) -> int:
    """Ingest only complete JSONL lines appended during recording."""
    data = Path(path).read_bytes()
    start = min(max(0, offset), len(data))
    boundary = data.rfind(b"\n") + 1
    if boundary <= start:
        return offset
    records = []
    for line in data[start:boundary].splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(value, dict):
            records.append(value)
    if records:
        if db is None:
            ingest_records(db, video_id, records, complete_snapshot=False)
        else:
            with db.begin_nested():
                ingest_records(db, video_id, records, complete_snapshot=False)
            _prefetch_assets(db, video_id)
    return boundary


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
    from .youtube import cookie_args, run_ytdlp
    import logging

    log = logging.getLogger(__name__)
    if db.in_transaction():
        db.commit()
    with tempfile.TemporaryDirectory(prefix="yla-chat-") as temp:
        output = str(Path(temp) / "%(id)s.%(ext)s")
        args = ["--no-warnings", "--skip-download", "--write-subs", "--sub-langs", "live_chat", "--sub-format", "json", "-o", output, *cookie_args(cookie_profile), f"https://www.youtube.com/watch?v={youtube_video_id}"]
        proc = run_ytdlp(args, capture_output=True, timeout=3600, check=False)
        files = list(Path(temp).glob("*live_chat*")) + list(Path(temp).glob("*.live_chat.json"))
        video = db.get(Video, video_id)
        if proc.returncode or not files:
            if video is not None:
                video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "unavailable"}
                db.commit()
            log.info("chat replay unavailable for %s", youtube_video_id)
            return 0
        try:
            count = ingest_jsonl(db, video_id, files[0], complete_snapshot=True, isolated=True)
        except Exception:
            if video is not None:
                video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "unavailable"}
                db.commit()
            log.info("chat replay unavailable for %s", youtube_video_id)
            return 0
        if video is not None:
            video.raw_metadata = {**(video.raw_metadata or {}), "chat_replay": "imported"}
        db.commit()
        return count
