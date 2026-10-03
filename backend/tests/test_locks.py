from app.services.locks import (AdvisoryLockProvider, lock_id, channel_lock,
                                recorder_lock, retention_lock)


class Dialect:
    name = "sqlite"


class Engine:
    dialect = Dialect()


def test_lock_id_is_stable_and_namespaced():
    assert lock_id("video", "abc") == lock_id("video", "abc")
    assert lock_id("video", "abc") != lock_id("channel", "abc")


def test_worker_lock_scopes_are_stable_and_do_not_collide():
    channel = channel_lock("UC123")
    recorder = recorder_lock("UC123")
    retention = retention_lock("UC123")
    assert channel == ("channel-monitor", "UC123")
    assert recorder == ("video-recorder", "UC123")
    assert retention == ("video-retention", "UC123")
    assert len({lock_id(*channel), lock_id(*recorder), lock_id(*retention)}) == 3


def test_channel_and_recorder_locks_are_keyed_by_entity_id():
    assert channel_lock("UCa") == channel_lock("UCa")
    assert channel_lock("UCa") != channel_lock("UCb")
    assert recorder_lock("video-a") == recorder_lock("video-a")
    assert recorder_lock("video-a") != recorder_lock("video-b")


def test_unsupported_database_fails_closed():
    provider = AdvisoryLockProvider(Engine())
    with provider.acquire("video", "abc") as lease:
        assert not lease.acquired
        assert not lease.still_owned()
