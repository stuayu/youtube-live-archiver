"""Safe, idempotent yt-dlp live-chat JSONL ingestion."""
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ..models import ChatMessage, now

def playback_offset_ms(message: dict) -> int:
    """yt-dlp chat offsets are relative to the original live stream."""
    for key in ("videoOffsetTimeMsec", "offset_ms", "offsetMs"):
        value = message.get(key)
        if value is not None:
            try: return max(0, int(value))
            except (TypeError, ValueError): pass
    for key in ("videoOffsetTimeSeconds", "offset_seconds"):
        try: return max(0, int(float(message[key]) * 1000))
        except (KeyError, TypeError, ValueError): pass
    return 0

def _text(runs):
    if isinstance(runs, str): return runs
    if not isinstance(runs, list): return ""
    return "".join(str(r.get("text") or r.get("emoji", {}).get("shortcuts", [""])[0]) for r in runs if isinstance(r, dict))

def ingest_records(db, video_id: int, records: list[dict], *, complete_snapshot: bool = True) -> int:
    """Upsert snapshot; mark absent stored messages deleted only after valid complete fetch."""
    seen, removed, timestamp = set(), set(), now()
    for item in records:
        replay = item.get("replayChatItemAction", {}) if isinstance(item, dict) else {}
        actions = replay.get("actions", item.get("actions", []) if isinstance(item, dict) else [])
        for action in actions:
            if "markChatItemAsDeletedAction" in action:
                target = action["markChatItemAsDeletedAction"].get("deletedStateMessage", {}).get("targetItemId")
                if target: removed.add(str(target))
            if "markChatItemsByAuthorAsDeletedAction" in action:
                author = action["markChatItemsByAuthorAsDeletedAction"].get("externalChannelId")
                removed.add("author:" + str(author)) if author else None
        renderers = [item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer") or item.get("liveChatPaidStickerRenderer") or item.get("liveChatMembershipItemRenderer") or item.get("liveChatSponsorshipsGiftPurchaseAnnouncementRenderer")]
        for action in actions:
            added = action.get("addChatItemAction", {}).get("item", {})
            renderers.append(added.get("liveChatTextMessageRenderer") or added.get("liveChatPaidMessageRenderer") or added.get("liveChatPaidStickerRenderer") or added.get("liveChatMembershipItemRenderer") or added.get("liveChatSponsorshipsGiftPurchaseAnnouncementRenderer"))
        for renderer in (r for r in renderers if r):
            mid = str(renderer.get("id") or renderer.get("message_id") or "")
            if not mid: continue
            if mid in seen: continue
            seen.add(mid)
            author = renderer.get("authorName") or renderer.get("author_name") or {}
            author_name = _text(author.get("simpleText") if isinstance(author, dict) else author)
            amount = renderer.get("purchaseAmountText") or {}
            amount_text = _text(amount.get("simpleText") if isinstance(amount, dict) else amount)
            parsed_amount = None
            if amount_text:
                import re
                match = re.search(r"([0-9][0-9,.]*)", amount_text)
                if match:
                    try: parsed_amount = float(match.group(1).replace(",", ""))
                    except ValueError: pass
            badges = [str(b.get("liveChatAuthorBadgeRenderer", {}).get("tooltip", "")) for b in renderer.get("authorBadges", []) if isinstance(b, dict)]
            text = _text(renderer.get("message", {}).get("runs", []))
            typ = "superchat" if "Paid" in str(item) else "membership" if "Membership" in str(item) or "Sponsorship" in str(item) else "normal"
            if "channelId" in renderer and renderer.get("authorChannelId"): author_id = renderer["authorChannelId"]
            else: author_id = renderer.get("authorExternalChannelId")
            source = {**item, "videoOffsetTimeMsec": replay.get("videoOffsetTimeMsec", item.get("videoOffsetTimeMsec"))}
            row = dict(video_id=video_id, message_id=mid, offset_ms=playback_offset_ms(source), published_at=None,
                       author_channel_id=author_id, author_name=author_name, author_badges=badges, message_text=text,
                       superchat_amount=parsed_amount, superchat_currency=None, superchat_color=renderer.get("headerBackgroundColor"),
                       message_type=typ, raw={"record":item,"renderer":renderer}, last_seen_at=timestamp)
            # Portable ORM upsert: preserve deletion state and original first-seen time.
            old = db.scalar(select(ChatMessage).where(ChatMessage.message_id == mid))
            if old:
                old.last_seen_at = timestamp
                old.raw = row["raw"]
                old.offset_ms, old.author_channel_id, old.author_name, old.author_badges, old.message_text = row["offset_ms"], author_id, author_name, badges, text
                old.superchat_amount, old.superchat_color, old.message_type = parsed_amount, row["superchat_color"], typ
            else:
                db.add(ChatMessage(**row, first_seen_at=timestamp))
    db.flush()
    by_id = {m.message_id: m for m in db.scalars(select(ChatMessage).where(ChatMessage.video_id == video_id)).all()}
    for target in removed:
        if target.startswith("author:"):
            author = target[7:]
            matches = [m for m in by_id.values() if m.author_channel_id == author]
        else: matches = [by_id[target]] if target in by_id else []
        for msg in matches:
            msg.is_deleted, msg.deleted_at, msg.deletion_source = True, timestamp, "判定不能"
    if complete_snapshot:
        for mid, msg in by_id.items():
            if mid not in seen and mid not in removed and not msg.is_deleted:
                msg.is_deleted, msg.deleted_at, msg.deletion_source = True, timestamp, "判定不能"
    db.flush()
    return len(seen)

def ingest_jsonl(db, video_id: int, path: str | Path, *, complete_snapshot: bool = True,
                 isolated: bool = False) -> int:
    records=[]
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip(): records.append(json.loads(line))
    if not records: raise ValueError("empty chat file")
    if complete_snapshot and not any(
        (r.get("liveChatTextMessageRenderer") or r.get("liveChatPaidMessageRenderer") or
         any(any(k in a for k in ("addChatItemAction", "markChatItemAsDeletedAction", "markChatItemsByAuthorAsDeletedAction"))
             for a in (r.get("replayChatItemAction", {}).get("actions") or r.get("actions") or [])))
        for r in records if isinstance(r, dict)
    ):
        raise ValueError("chat snapshot contains no recognizable items")
    if isolated:
        with db.begin_nested():
            return ingest_records(db, video_id, records, complete_snapshot=complete_snapshot)
    return ingest_records(db, video_id, records, complete_snapshot=complete_snapshot)

def ingest_jsonl_delta(db, video_id: int, path: str | Path, offset: int = 0) -> int:
    """Ingest only complete JSONL lines appended during recording; keep partial tails for next poll."""
    data = Path(path).read_bytes()
    start = min(max(0, offset), len(data))
    boundary = data.rfind(b"\n") + 1
    if boundary <= start:
        return offset
    records = []
    for line in data[start:boundary].splitlines():
        if line.strip():
            try:
                value = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(value, dict):
                records.append(value)
    if records:
        # A malformed or duplicate delta must not leave the recorder's outer
        # transaction in a failed state; the media recording remains primary.
        if db is None:  # Retain the small pure-parser seam used by unit callers.
            ingest_records(db, video_id, records, complete_snapshot=False)
        else:
            with db.begin_nested():
                ingest_records(db, video_id, records, complete_snapshot=False)
    return boundary

def retrieve_replay(db, video_id: int, youtube_video_id: str, cookie_profile: str | None = None) -> int:
    """Only apply deletions after yt-dlp succeeded and yielded a parseable replay."""
    import subprocess, tempfile
    from ..config import settings
    from .youtube import cookie_args
    with tempfile.TemporaryDirectory(prefix="yla-chat-") as temp:
        output = str(Path(temp) / "%(id)s.%(ext)s")
        args = [settings.yt_dlp_path,"--no-warnings","--skip-download","--write-subs","--sub-langs","live_chat","--sub-format","json","-o",output,*cookie_args(cookie_profile),f"https://www.youtube.com/watch?v={youtube_video_id}"]
        proc = subprocess.run(args,capture_output=True,timeout=3600,check=False)
        files = list(Path(temp).glob("*live_chat*")) + list(Path(temp).glob("*.live_chat.json"))
        if proc.returncode or not files:
            raise RuntimeError("chat replay retrieval failed")
        return ingest_jsonl(db, video_id, files[0], complete_snapshot=True, isolated=True)
