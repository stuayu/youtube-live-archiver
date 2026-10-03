from dataclasses import dataclass
from datetime import timedelta


BACKOFF_MINUTES = (1, 5, 15, 60, 360)


@dataclass(frozen=True)
class RecoveryDecision:
    action: str
    reason: str | None = None


def recovery_delay(attempts: int) -> timedelta:
    """Bounded retry delay; after the last slot, keep retrying every six hours."""
    index = min(max(attempts - 1, 0), len(BACKOFF_MINUTES) - 1)
    return timedelta(minutes=BACKOFF_MINUTES[index])


def classify_recovery(status: str | None, error: str | None, metadata: dict | None) -> RecoveryDecision:
    if error or not status or status.upper() in ("UNKNOWN", "PROTECTION_PENDING"):
        return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")
    status = status.upper()
    if status in ("PRIVATE", "DELETED", "REMOVED", "UNAVAILABLE", "MEMBERS_ONLY", "ACCESS_DENIED"):
        reason = "REMOTE_PRIVATE" if status == "PRIVATE" else "REMOTE_DELETED" if status in ("DELETED", "REMOVED") else "REMOTE_UNAVAILABLE"
        return RecoveryDecision("PROTECT", reason)
    if status != "PUBLIC":
        return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")
    live_state = (metadata or {}).get("live_status")
    if live_state == "is_live":
        return RecoveryDecision("RESUME_LIVE")
    if live_state in ("was_live", "not_live", "post_live") or (metadata or {}).get("duration"):
        return RecoveryDecision("RETRIEVE_ARCHIVE")
    return RecoveryDecision("DEFER", "REMOTE_CHECK_FAILED")


def remove_intermediates_after_success(directory, final_media, audit_callback):
    """Remove yt-dlp artifacts only after a non-empty merged media file is verified."""
    from pathlib import Path
    directory, final_media = Path(directory), Path(final_media)
    if not final_media.is_file() or final_media.stat().st_size <= 0:
        return []
    removed = []
    final_resolved = final_media.resolve()
    for item in directory.iterdir():
        if not item.is_file() or item.resolve() == final_resolved:
            continue
        name = item.name.lower()
        if name.endswith((".part", ".ytdl")) or ".f" in name and item.suffix.lower() in (".mp4", ".webm", ".m4a", ".mkv"):
            item.unlink()
            removed.append(str(item))
            audit_callback(str(item))
    return removed
