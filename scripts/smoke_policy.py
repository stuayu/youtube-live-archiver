"""Standard-library smoke checks for the retention fail-safe policy."""
from datetime import datetime, timedelta, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.policy import classify_remote, may_delete, compare_fingerprints, has_prior_successful_check

now = datetime.now(timezone.utc)
assert classify_remote("PRIVATE", None, 10, 10).status == "PROTECTED"
assert classify_remote("PUBLIC", "timeout", 10, 10).status == "PROTECTION_PENDING"
assert classify_remote("PUBLIC", None, None, 10).status == "PROTECTION_PENDING"
assert compare_fingerprints("standard", [], []).status == "PROTECTION_PENDING"
ok = dict(protected=False, status="DELETE_ELIGIBLE", remote_status="PUBLIC", edit_unchanged=True,
          check_succeeded=True, retention_expires_at=now-timedelta(days=4), grace_until=now-timedelta(days=1), now=now)
assert may_delete(**ok)
assert not may_delete(**{**ok, "grace_until": now+timedelta(seconds=1)})
assert has_prior_successful_check([dict(id=1, remote_status="PUBLIC", error=None, edit_detected=False)], 2)
print("retention policy smoke checks passed")
