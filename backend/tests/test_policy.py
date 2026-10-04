from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from app.services.policy import (classify_remote, may_delete, initial_retention_decision,
    compare_fingerprints, has_prior_successful_check)

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)


@pytest.mark.parametrize("status,error", [(None, None), ("UNKNOWN", None), ("PUBLIC", "timeout"), ("PUBLIC", "DNS failure")])
def test_remote_failure_is_pending_and_never_deletable(status, error):
    assert classify_remote(status, error, 100, 100).status == "PROTECTION_PENDING"
    assert not may_delete(protected=False, status="DELETE_ELIGIBLE", remote_status=status, edit_unchanged=True,
                          check_succeeded=False, retention_expires_at=NOW-timedelta(days=1),
                          grace_until=NOW-timedelta(hours=1), now=NOW)


@pytest.mark.parametrize("status,reason", [("PRIVATE", "REMOTE_PRIVATE"), ("DELETED", "REMOTE_DELETED"),
    ("REMOVED", "REMOTE_DELETED"), ("UNAVAILABLE", "REMOTE_UNAVAILABLE"), ("MEMBERS_ONLY", "REMOTE_UNAVAILABLE"),
    ("ACCESS_DENIED", "REMOTE_UNAVAILABLE")])
def test_unavailable_remote_protects(status, reason):
    d = classify_remote(status, None, 100, 100)
    assert (d.status, d.reason) == ("PROTECTED", reason)


@pytest.mark.parametrize("remote_duration,expected", [(103, "DELETE_ELIGIBLE"), (112, "DELETE_ELIGIBLE"), (200, "PROTECTED")])
def test_duration_tolerance(remote_duration, expected):
    assert classify_remote("PUBLIC", None, 100, remote_duration, 30).status == expected


def test_metadata_does_not_participate_in_policy():
    # classifier intentionally accepts only availability and content duration, never title/description/thumbnail/category.
    assert classify_remote("PUBLIC", None, 100, 100).status == "DELETE_ELIGIBLE"


def test_missing_duration_fails_safe():
    assert classify_remote("PUBLIC", None, None, 100).status == "PROTECTION_PENDING"
    assert classify_remote("PUBLIC", None, 100, None).status == "PROTECTION_PENDING"


@pytest.mark.parametrize("kwargs", [
    {"protected": True}, {"status": "PROTECTION_PENDING"}, {"remote_status": "PRIVATE"},
    {"edit_unchanged": False}, {"edit_unchanged": None}, {"check_succeeded": False},
    {"retention_expires_at": None}, {"grace_until": None},
])
def test_each_missing_safety_condition_keeps(kwargs):
    args = dict(protected=False, status="DELETE_ELIGIBLE", remote_status="PUBLIC", edit_unchanged=True,
                check_succeeded=True, retention_expires_at=NOW-timedelta(days=1), grace_until=NOW-timedelta(hours=1), now=NOW)
    args.update(kwargs)
    assert not may_delete(**args)


def test_all_safety_conditions_allow_delete():
    assert may_delete(protected=False, status="DELETE_ELIGIBLE", remote_status="PUBLIC", edit_unchanged=True,
        check_succeeded=True, retention_expires_at=NOW, grace_until=NOW, now=NOW)


def test_protected_excluded_and_expiry_required():
    assert initial_retention_decision(protected=True, finished_at=NOW-timedelta(days=50), expires_at=NOW-timedelta(days=2), now=NOW) == "KEEP"
    assert initial_retention_decision(protected=False, finished_at=None, expires_at=NOW-timedelta(days=1), now=NOW) == "KEEP"


@pytest.mark.parametrize("status,expected", [("PUBLIC", "DELETE_ELIGIBLE"), ("PRIVATE", "PROTECTED"),
    ("DELETED", "PROTECTED"), ("REMOVED", "PROTECTED"), ("UNAVAILABLE", "PROTECTED"),
    ("MEMBERS_ONLY", "PROTECTED"), ("ACCESS_DENIED", "PROTECTED"), ("UNKNOWN", "PROTECTION_PENDING")])
def test_classify_remote_all_availability_branches(status, expected):
    assert classify_remote(status, None, 100, 100).status == expected


def test_fingerprint_differences_and_failures_fail_safe():
    local = [{"position": p, "video_hash": "0000000000000000", "audio_hash": "00000000"} for p in (0.05, 0.2)]
    same = [dict(x) for x in local]
    assert compare_fingerprints("standard", local, same).status == "DELETE_ELIGIBLE"
    changed = [dict(x) for x in same]; changed[1]["video_hash"] = "ffffffffffffffff"
    assert compare_fingerprints("standard", local, changed).reason == "REMOTE_CONTENT_CHANGED"
    assert compare_fingerprints("strong", local, [{**x, "audio_hash": "ffffffff"} for x in same]).reason == "REMOTE_CONTENT_CHANGED"
    assert compare_fingerprints("standard", [], []).status == "PROTECTION_PENDING"
    assert compare_fingerprints("strong", local, [{"position": .05, "video_hash": "0", "audio_hash": None}]).status == "PROTECTION_PENDING"


def test_shifted_fingerprint_candidates_use_minimum_distance():
    local = [{"position": .05, "video_hash": "00000000", "audio_hash": "0000"}]
    aligned = [{"position": .05, "video_hashes": ["ffffffff", "00000000"], "audio_hashes": ["ffff", "0000"]}]
    assert compare_fingerprints("strong", local, aligned).status == "DELETE_ELIGIBLE"
    changed = [{"position": .05, "video_hashes": ["ffffffff", "eeeeeeee"], "audio_hashes": ["ffff"]}]
    assert compare_fingerprints("standard", local, changed).reason == "REMOTE_CONTENT_CHANGED"
    assert compare_fingerprints("standard", local, [{"position": .05, "video_hashes": []}]).status == "PROTECTION_PENDING"


def test_grace_and_final_confirmation_require_two_successful_remote_checks():
    checks = [dict(id=1, remote_status="PUBLIC", error=None, edit_detected=False),
              dict(id=2, remote_status="PUBLIC", error=None, edit_detected=False)]
    assert has_prior_successful_check(checks, 2)
    assert not has_prior_successful_check(checks[:1], 1)
    assert not has_prior_successful_check([dict(id=1, remote_status="PUBLIC", error="timeout", edit_detected=False)], 2)
    args = dict(protected=False, status="DELETE_ELIGIBLE", remote_status="PUBLIC", edit_unchanged=True,
                check_succeeded=True, retention_expires_at=NOW-timedelta(days=4), grace_until=NOW-timedelta(days=1), now=NOW)
    assert may_delete(**args) and has_prior_successful_check(checks, 2)
    assert not may_delete(**{**args, "grace_until": NOW+timedelta(seconds=1)})


@pytest.mark.parametrize(("include_audio", "expected_format"), [(False, "wv*/w"), (True, "wv*+wa/w")])
def test_remote_fingerprint_samples_use_lowest_format(monkeypatch, tmp_path, include_audio, expected_format):
    from types import SimpleNamespace
    from app import workers

    calls = []
    def fake_run(args, **kwargs):
        calls.append(args)
        output = Path(args[args.index("-o") + 1].replace("%(ext)s", "mkv"))
        output.write_bytes(b"remote sample")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(workers, "run_ytdlp", fake_run)
    monkeypatch.setattr(workers, "video_phash", lambda *args: "abcd")
    monkeypatch.setattr(workers, "audio_fingerprint", lambda *args: "1234")
    video = SimpleNamespace(youtube_video_id="abcdefghijk", channel=SimpleNamespace(cookie_profile=None))
    samples = workers._remote_samples(video, 100, include_audio)
    assert len(samples) == 6
    assert len(calls) == 6
    assert all(args[args.index("-f") + 1] == expected_format for args in calls)
    assert all("--force-keyframes-at-cuts" in args for args in calls)
