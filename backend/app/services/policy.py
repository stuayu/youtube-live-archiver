"""Retention の純粋判定。判定不能は必ず KEEP / PROTECTION_PENDING。"""
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Decision:
    status: str
    reason: str | None = None


def classify_remote(status: str | None, error: str | None, original_duration: float | None,
                    remote_duration: float | None, tolerance_seconds: float = 30) -> Decision:
    if error or not status:
        return Decision("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")
    status = status.upper()
    protected = {
        "PRIVATE": "REMOTE_PRIVATE", "DELETED": "REMOTE_DELETED", "REMOVED": "REMOTE_DELETED",
        "UNAVAILABLE": "REMOTE_UNAVAILABLE", "MEMBERS_ONLY": "REMOTE_UNAVAILABLE",
        "ACCESS_DENIED": "REMOTE_UNAVAILABLE",
    }
    if status in protected:
        return Decision("PROTECTED", protected[status])
    if status != "PUBLIC":
        return Decision("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")
    if original_duration is None or remote_duration is None:
        return Decision("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")
    if abs(original_duration - remote_duration) > tolerance_seconds:
        return Decision("PROTECTED", "REMOTE_DURATION_CHANGED")
    return Decision("DELETE_ELIGIBLE")


def may_delete(*, protected: bool, status: str, remote_status: str | None, edit_unchanged: bool | None,
               check_succeeded: bool, retention_expires_at: datetime | None, grace_until: datetime | None,
               now: datetime) -> bool:
    """全条件必須。再確認成功を呼出側で保証する。"""
    if protected or status != "DELETE_ELIGIBLE":
        return False
    if not check_succeeded or remote_status != "PUBLIC" or edit_unchanged is not True:
        return False
    if retention_expires_at is None or grace_until is None:
        return False
    return now >= retention_expires_at and now >= grace_until


def initial_retention_decision(*, protected: bool, finished_at: datetime | None,
                               expires_at: datetime | None, now: datetime) -> str:
    if protected:
        return "KEEP"
    if finished_at is None or expires_at is None:
        return "KEEP"
    return "VERIFY_REMOTE" if now >= expires_at else "KEEP"


def compare_fingerprints(mode: str, local: list[dict], remote: list[dict],
                         video_threshold: float = 0.25, audio_threshold: float = 0.35) -> Decision:
    """Compare aligned samples. Missing/failed samples are indeterminate (fail safe)."""
    mode = (mode or "off").lower()
    if mode == "off":
        return Decision("DELETE_ELIGIBLE")
    if mode == "basic":
        return Decision("DELETE_ELIGIBLE")  # duration is checked by classify_remote
    need_audio = mode == "strong"
    if mode not in ("standard", "strong") or not local or len(local) != len(remote):
        return Decision("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")
    try:
        by_position = {round(float(x["position"]), 4): x for x in remote}
        video_differences = []
        audio_differences = []
        for sample in local:
            other = by_position[round(float(sample["position"]), 4)]
            a, b = sample["video_hash"], other["video_hash"]
            if not a or not b or len(a) != len(b):
                raise ValueError("missing video fingerprint")
            bits = len(a) * 4
            video_differences.append((int(a, 16) ^ int(b, 16)).bit_count() / bits)
            if need_audio:
                if not sample.get("audio_hash") or not other.get("audio_hash"):
                    raise ValueError("missing audio fingerprint")
                aa, ab = sample["audio_hash"], other["audio_hash"]
                if len(aa) != len(ab):
                    raise ValueError("audio fingerprint size mismatch")
                audio_differences.append((int(aa, 16) ^ int(ab, 16)).bit_count() / (len(aa) * 4))
        if max(video_differences) > video_threshold:
            return Decision("PROTECTED", "REMOTE_CONTENT_CHANGED")
        if need_audio and max(audio_differences) > audio_threshold:
            return Decision("PROTECTED", "REMOTE_CONTENT_CHANGED")
        return Decision("DELETE_ELIGIBLE")
    except Exception:
        return Decision("PROTECTION_PENDING", "REMOTE_CHECK_FAILED")


def has_prior_successful_check(checks: list[dict], current_id: int) -> bool:
    """Require an earlier successful public/unchanged check before final deletion."""
    return any(c.get("id") != current_id and c.get("remote_status") == "PUBLIC"
               and not c.get("error") and c.get("edit_detected") is False for c in checks)
