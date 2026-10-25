"""Fail-closed PostgreSQL advisory lock provider.

Locks use a stable 64-bit hash of (namespace, key). The dedicated connection
must remain open for the whole critical section; unsupported databases fail
closed and return an unacquired lease.
"""
from contextlib import contextmanager
import hashlib
import logging

from sqlalchemy import text
from ..db import engine

log = logging.getLogger(__name__)


def lock_id(namespace: str, key: str) -> int:
    """Return the one canonical namespaced advisory-lock key."""
    value = int.from_bytes(hashlib.blake2b(f"{namespace}:{key}".encode(), digest_size=8).digest(), "big", signed=True)
    return value


def channel_lock(channel_id: str) -> tuple[str, str]:
    return "channel-monitor", str(channel_id)


def recorder_lock(video_id: str) -> tuple[str, str]:
    return "video-recorder", str(video_id)


def retention_lock(video_id: str) -> tuple[str, str]:
    return "video-retention", str(video_id)


class AdvisoryLease:
    def __init__(self, connection, key: int, acquired: bool):
        self.connection, self.key, self.acquired = connection, key, acquired

    def still_owned(self) -> bool:
        if not self.acquired:
            return False
        try:
            row = self.connection.execute(text("SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND granted AND classid=:hi AND objid=:lo AND objsubid=1)"),
                {"hi": (self.key >> 32) & 0xFFFFFFFF, "lo": self.key & 0xFFFFFFFF}).scalar()
            result = bool(row)
            self.connection.commit()
            return result
        except Exception:
            return False


class AdvisoryLockProvider:
    def __init__(self, engine):
        self.engine = engine

    @contextmanager
    def acquire(self, namespace: str, key: str):
        conn = None
        lease = AdvisoryLease(None, lock_id(namespace, key), False)
        try:
            if self.engine.dialect.name == "postgresql":
                conn = self.engine.connect()
                acquired = bool(conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lease.key}).scalar())
                # Session advisory locks survive transaction boundaries. Do not
                # leave an idle transaction open for the duration of recording.
                conn.commit()
                lease = AdvisoryLease(conn, lease.key, acquired)
        except Exception:
            log.exception("advisory lock unavailable; skipping %s", namespace)
            if conn is not None:
                conn.close()
                conn = None
            lease = AdvisoryLease(None, lease.key, False)
        try:
            yield lease
        finally:
            if conn is not None:
                try:
                    if lease.acquired and lease.still_owned():
                        conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lease.key})
                        conn.commit()
                except Exception:
                    log.warning("advisory lock release failed")
                conn.close()


lock_provider = AdvisoryLockProvider
default_locks = AdvisoryLockProvider(engine)
