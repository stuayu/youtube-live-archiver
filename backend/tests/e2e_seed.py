"""Seed the scratch dev DB with representative rows for UI review (no YouTube access)."""
import os
import shutil
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import SessionLocal  # noqa: E402
from app.models import (AuditLog, Channel, ChannelStat, ChatMessage, RecordingRule, Storage, TranscriptSegment,  # noqa: E402
                        Video, now)
from app.services.chat import ingest_records  # noqa: E402

root = Path(os.environ["STORAGE_ROOT"])
db = SessionLocal()
from sqlalchemy import text
db.execute(text("TRUNCATE chat_messages, transcript_segments, audit_logs, recording_rules, channel_stats, "
                "fingerprints, remote_checks, processing_jobs, videos, channels RESTART IDENTITY CASCADE"))
db.commit()

storage = db.query(Storage).filter_by(name="LOCAL").first()
if storage is None:
    storage = Storage(name="LOCAL", root_path=str(root), minimum_free_bytes=0, warning_free_bytes=0)
    db.add(storage)
    db.commit()
nas = db.query(Storage).filter_by(name="NAS-01").first()
if not nas:
    nas = Storage(name="NAS-01", root_path="/Volumes/does-not-exist/youtube", minimum_free_bytes=50 * 1024**3,
                  warning_free_bytes=200 * 1024**3)
    db.add(nas)
db.commit()

names = [("UCaaaaaaaaaaaaaaaaaaaaaa", "さくらみこ切り抜きではない公式チャンネル / Official Very Long Channel Name Ch."),
         ("UCbbbbbbbbbbbbbbbbbbbbbb", "NASA"), ("UCcccccccccccccccccccccc", "Lofi Girl"),
         ("UCdddddddddddddddddddddd", "停止中チャンネル")]
channels = []
for i, (cid, name) in enumerate(names):
    ch = Channel(youtube_channel_id=cid, url=f"https://www.youtube.com/channel/{cid}", name=name, enabled=i != 3,
                 poll_interval=["30", "AUTO", "60", "45"][i], storage_id=[storage.id, nas.id, None, None][i],
                 retention_days=[30, None, 7, 90][i], subscriber_count=[2_340_000, 12_500_000, 15_800_000, None][i],
                 description=["説明文がとても長いチャンネルです。" * 12, "NASA's official channel", None, None][i],
                 next_check_at=now() + timedelta(days=3650), backfill_limit=[0, 5, 0, 0][i],
                 live_playback_enabled=i == 0)
    db.add(ch)
    channels.append(ch)
db.commit()
for d in range(40):
    db.add(ChannelStat(channel_id=channels[0].id, captured_at=now() - timedelta(days=40 - d),
                       subscriber_count=2_300_000 + d * 1000, subscriber_count_rounded=True,
                       video_count=900 + d // 3, view_count=500_000_000 + d * 400_000))
    if d not in {8, 9, 23}:
        db.add(ChannelStat(channel_id=channels[1].id, captured_at=now() - timedelta(days=40 - d),
                           subscriber_count=12_400_000 + d * 1700, subscriber_count_rounded=True,
                           video_count=4100 + d // 2, view_count=2_000_000_000 + d * 500_000))
    if d not in {8, 9, 23}:
        db.add(ChannelStat(channel_id=channels[2].id, captured_at=now() - timedelta(days=40 - d),
                           subscriber_count=5_800_000 + d * 1250, subscriber_count_rounded=True,
                           video_count=320 + d // 3, view_count=8_000_000_000 + d * 600_000))


def media(vid: str, seconds: int = 12, ext: str = "mp4") -> Path:
    target = root / channels[0].youtube_channel_id / "2026" / vid
    target.mkdir(parents=True, exist_ok=True)
    out = target / f"video.{ext}"
    if not out.exists():
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=15:duration={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-c:a", "aac", "-shortest", str(out)], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(out), "-frames:v", "1", str(target / "video.jpg")], check=True)
    return target


rows = [
    ("vid00000001", "【雑談】朝までまったりお話しする枠【初見さん歓迎】#雑談 #朝活 とても長いタイトルが折り返されるかの確認用テキスト", "ACTIVE", "mp4", {}),
    ("vid00000002", "歌枠 / Singing Stream", "ACTIVE", "mkv", {}),
    ("vid00000003", "非公開になった配信", "PROTECTED", "mp4", dict(protected=True, protection_reason="REMOTE_PRIVATE", protected_at=now())),
    ("vid00000004", "手動で保護した配信", "PROTECTED", "mp4", dict(protected=True, protection_reason="MANUAL", protected_at=now())),
    ("vid00000005", "録画に失敗した配信", "RECORDING_FAILED", "mp4", dict(is_partial=True, recovery_attempts=3)),
    ("vid00000006", "削除候補の配信", "DELETE_ELIGIBLE", "mp4", dict(deletion_after=now() + timedelta(days=2))),
    ("vid00000007", "確認保留の配信", "PROTECTION_PENDING", "mp4", dict(protection_reason="REMOTE_CHECK_FAILED")),
    ("vid00000008", "録画中の配信 LIVE NOW", "RECORDING", "mp4", dict(recording_heartbeat_at=now())),
]
for i, (vid, title, status, ext, extra) in enumerate(rows):
    target = media(vid, ext=ext)
    if status == "RECORDING":
        hls_root = root / "live-hls" / vid
        hls_root.mkdir(parents=True, exist_ok=True)
        if not (hls_root / "index.m3u8").exists():
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(target / f"video.{ext}"), "-c", "copy",
                            "-hls_time", "4", "-hls_playlist_type", "event", str(hls_root / "index.m3u8")], check=True)
        extra["live_hls_path"] = str(hls_root)
    finished = None if status in ("RECORDING", "RECORDING_FAILED") else now() - timedelta(days=i + 1)
    v = Video(youtube_video_id=vid, channel_id=channels[0].id, storage_id=storage.id, title=title, status=status,
              file_path=str(target), file_size=(target / f"video.{ext}").stat().st_size, original_duration=7432.0 + i * 600,
              recorded_at=now() - timedelta(days=i + 1, hours=2), recording_finished_at=finished, retention_days=30,
              retention_expires_at=finished + timedelta(days=30) if finished else None,
              remote_status="PUBLIC", transcription_status="completed" if i == 0 else "pending", **extra)
    db.add(v)
    db.flush()
    if i == 0:
        for n in range(60):
            db.add(TranscriptSegment(video_id=v.id, start=n * 5.0, end=n * 5.0 + 4.5,
                                     text=f"これは文字起こしのサンプル発言 {n} 番目です。今日は雑談をしていきます。", raw=False))
            kind = "superchat" if n == 0 or n % 17 == 0 else "membership" if n == 3 else "normal"
            parts = [{"text": "こんにちは！" * (1 + n % 4)}]
            if n == 2:
                parts = [{"text": "絵文字 "}, {"emoji": {"emojiId": "UCseed/emote", "isCustomEmoji": True,
                         "shortcuts": [":_seed:"], "image": {"thumbnails": [
                             {"url": "https://yt3.ggpht.com/seed-emote.png"}]}}}]
            if n == 5:
                parts = [{"text": "こんにちは 💚"}]
            renderer = {"id": f"m{n}", "authorName": {"simpleText": f"視聴者{n}"},
                        "authorExternalChannelId": f"UCseed{n:020d}",
                        "timestampUsec": str(int((now() - timedelta(seconds=90 - n)).timestamp() * 1_000_000)),
                        "message": {"runs": parts}}
            if n == 0:
                renderer["authorBadges"] = [{"liveChatAuthorBadgeRenderer": {
                    "tooltip": "Owner", "icon": {"iconType": "OWNER"}}}]
                renderer["purchaseAmountText"] = {"simpleText": "¥1,000"}
                renderer["headerBackgroundColor"] = 4294947584
                record = {"liveChatPaidMessageRenderer": renderer, "videoOffsetTimeMsec": n * 1500}
            elif n == 3:
                renderer["headerPrimaryText"] = {"simpleText": "Member for 45 months"}
                renderer["headerSubtext"] = {"simpleText": "Gold"}
                record = {"liveChatMembershipItemRenderer": renderer, "videoOffsetTimeMsec": n * 1500}
            else:
                record = {"liveChatTextMessageRenderer": renderer, "videoOffsetTimeMsec": n * 1500}
            if n == 10:
                renderer["authorBadges"] = [{"liveChatAuthorBadgeRenderer": {
                    "tooltip": "Member (45 months)", "customThumbnail": {"thumbnails": [
                        {"url": "https://yt3.ggpht.com/seed-member.png"}]}}}]
            if kind == "superchat" and n != 0:
                renderer["purchaseAmountText"] = {"simpleText": "$5.00"}
                renderer["bodyBackgroundColor"] = 4294947584
                record = {"liveChatPaidMessageRenderer": renderer, "videoOffsetTimeMsec": n * 1500}
            ingest_records(db, v.id, [record], complete_snapshot=False)
            if n == 4:
                db.query(ChatMessage).filter_by(video_id=v.id, message_id=f"m{n}").one().is_deleted = True
legacy_target = media("legacy00001")
legacy_video = Video(
    youtube_video_id="legacy00001",
    channel_id=channels[0].id,
    storage_id=storage.id,
    title="旧形式チャットの配信",
    status="ACTIVE",
    file_path=str(legacy_target),
    file_size=(legacy_target / "video.mp4").stat().st_size,
    original_duration=12,
    recorded_at=now() - timedelta(days=2),
    recording_finished_at=now() - timedelta(days=2),
    retention_days=30,
)
db.add(legacy_video)
db.flush()
old_record = {"liveChatTextMessageRenderer": {
    "id": "legacy-emote", "authorName": {"simpleText": "旧形式の投稿者"},
    "authorBadges": [{"liveChatAuthorBadgeRenderer": {
        "tooltip": "Member (1 year)", "customThumbnail": {"thumbnails": [
            {"url": "https://yt3.ggpht.com/legacy-member.png"}]}}}],
    "message": {"runs": [{"text": "旧形式の絵文字 "}, {"emoji": {
        "emojiId": "UClegacy/emote", "isCustomEmoji": True, "shortcuts": [":_legacy:"],
        "image": {"thumbnails": [{"url": "https://yt3.ggpht.com/legacy-emote.png"}]}}}]}}}
db.add(ChatMessage(video_id=legacy_video.id, message_id="legacy-emote", offset_ms=3000,
                   author_name="旧形式の投稿者", author_badges=["Member (1 year)"],
                   message_text="旧形式の絵文字 :_legacy:", message_runs=[], message_type="normal",
                   raw={"record": old_record, "renderer": old_record["liveChatTextMessageRenderer"]}))
upcoming = [("up000000001", "【重大告知】明日の記念配信", now() + timedelta(hours=3), {}),
            ("up000000002", "時刻未定の待機枠 / フリーチャット", None, {}),
            ("up000000003", "録画しない設定の配信", now() + timedelta(days=1), {"schedule_enabled": False}),
            ("up000000004", "開始時刻を過ぎた待機枠", now() - timedelta(hours=5), {})]
for vid, title, when, meta in upcoming:
    db.add(Video(youtube_video_id=vid, channel_id=channels[1].id, title=title, status="WAITING_LIVE", scheduled_start_at=when,
                 raw_metadata={"live_status": "is_upcoming", **meta}, retention_days=30))
# Add enough playable archive rows to exercise pagination without repeated media encoding.
source_target = root / channels[0].youtube_channel_id / "2026" / "vid00000001"
source_media = source_target / "video.mp4"
for n in range(35):
    target = root / channels[0].youtube_channel_id / "2026" / f"page{n:07d}"
    target.mkdir(parents=True, exist_ok=True)
    media_path = target / "video.mp4"
    shutil.copy2(source_media, media_path)
    shutil.copy2(source_target / "video.jpg", target / "video.jpg")
    db.add(Video(youtube_video_id=f"page{n:07d}", channel_id=channels[0].id, storage_id=storage.id,
                 title=f"ページング確認用アーカイブ {n + 1}", status="ACTIVE", file_path=str(target),
                 file_size=media_path.stat().st_size, original_duration=600 + n, recorded_at=now() - timedelta(days=100 + n),
                 recording_finished_at=now() - timedelta(days=99 + n), retention_days=30,
                 retention_expires_at=now() + timedelta(days=10)))

for n in range(34):
    db.add(Video(youtube_video_id=f"old{n:08d}", channel_id=channels[1].id, title=f"過去の配信 #{n}", status="SKIPPED",
                 raw_metadata={"live_status": "was_live", "backfill_excluded": True}))
channels[1].consecutive_failures = 3
channels[1].last_error = "UNKNOWN: ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>"
channels[1].last_checked_at = now() - timedelta(minutes=4)
channels[1].last_success_at = now() - timedelta(hours=3)
channels[0].last_checked_at = channels[0].last_success_at = now() - timedelta(minutes=2)
channels[0].next_check_at = now() + timedelta(minutes=12)
channels[0].baseline_completed_at = now() - timedelta(days=30)
partial = db.query(Video).filter_by(youtube_video_id="vid00000002").one()
partial.is_partial = True
partial.raw_metadata = {**(partial.raw_metadata or {}), "user_stopped": True}
queued = db.query(Video).filter_by(youtube_video_id="vid00000005").one()
queued.raw_metadata = {**(queued.raw_metadata or {}), "archive_backfill": "queued", "live_status": "was_live",
                       "recording_start_offset_ms": 120_000, "live_from_start": "fallback"}
for n, (action, reason) in enumerate([("RECORD", "Recording completed"), ("PROTECT", "REMOTE_PRIVATE"), ("MONITOR_ERROR", "UNKNOWN"),
                                      ("SALVAGE_PARTIAL", "VERIFIED_DURATION"), ("DELETE", "Retention expired"), ("MANUAL_DELETE", "USER_CONFIRMED"),
                                      ("LIVE_SEGMENT_INTERRUPTED", None), ("RECOVERY_QUEUED", "RESUME_LIVE")] * 4):
    db.add(AuditLog(video_id=partial.id if action != "MONITOR_ERROR" else None,
                    channel_id=channels[1].id if action == "MONITOR_ERROR" else None, action=action, reason=reason,
                    created_at=now() - timedelta(minutes=7 * n)))
for n in range(3):
    db.add(Video(youtube_video_id=f"del{n:08d}", channel_id=channels[0].id, storage_id=storage.id, title=f"削除済みの配信 {n}",
                 status="DELETED_BY_RETENTION" if n else "DELETED_MANUALLY", deleted_at=now() - timedelta(days=n),
                 recorded_at=now() - timedelta(days=40 + n), recording_finished_at=now() - timedelta(days=40 + n),
                 original_duration=3600, retention_days=30, file_size=0))
db.add(RecordingRule(name="歌枠だけ録画", pattern="歌枠|singing", match_mode="regex", channel_id=channels[0].id, priority=10))
db.add(RecordingRule(name="メンバー限定を除外", pattern="メンバー限定", is_exclusion=True, priority=5))
db.commit()
print("seeded", db.query(Video).count(), "videos", db.query(Channel).count(), "channels")
db.close()
