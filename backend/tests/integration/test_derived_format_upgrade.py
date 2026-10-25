from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, Channel, Storage, Video
from app.services.transcode import cleanup_old_derived_files


def _video_for_folder(folder, storage=None):
    with SessionLocal() as db:
        channel = Channel(
            youtube_channel_id="UC" + uuid4().hex[:22],
            url="https://www.youtube.com/@derived-test",
            name="Derived cleanup test",
        )
        db.add(channel)
        db.flush()
        if storage is None or storage.id is None:
            storage = Storage(name=f"derived-test-{uuid4().hex}", root_path=str(folder.parent))
            db.add(storage)
            db.flush()
        video = Video(
            youtube_video_id=uuid4().hex[:11],
            channel_id=channel.id,
            title="Derived cleanup test",
            status="COMPLETED",
            file_path=str(folder),
            storage_id=storage.id,
            raw_metadata={},
        )
        db.add(video)
        db.flush()
        video_id = video.id
        db.commit()
        return video_id


def test_upgrade_removes_only_derived_media_once(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    valid = tmp_path / "valid"
    missing_source = tmp_path / "missing-source"
    valid.mkdir()
    missing_source.mkdir()
    original = valid / "original.mkv"
    original.write_bytes(b"original recording")
    playable = valid / "original.playable.mp4"
    h264 = valid / "original.h264.mp4"
    playable.write_bytes(b"old remux")
    h264.write_bytes(b"old h264")
    orphan_derived = missing_source / "orphan.playable.mp4"
    orphan_derived.write_bytes(b"keep without source")
    video_id = _video_for_folder(valid)
    _video_for_folder(missing_source)

    assert cleanup_old_derived_files() is True
    assert original.read_bytes() == b"original recording"
    assert not playable.exists()
    assert not h264.exists()
    assert orphan_derived.read_bytes() == b"keep without source"
    assert (Path(settings.data_dir) / "derived-format-version").read_text() == "2"
    assert not (Path(settings.data_dir) / "derived-format-pending").exists()
    with SessionLocal() as db:
        logs = db.query(AuditLog).filter_by(video_id=video_id, action="DERIVED_CLEANUP").all()
        assert {(item.reason, item.details["path"], item.details["size"]) for item in logs} == {
            ("FORMAT_UPGRADED", playable.name, len(b"old remux")),
            ("FORMAT_UPGRADED", h264.name, len(b"old h264")),
        }
    fresh_derived = valid / "new.playable.mp4"
    fresh_derived.write_bytes(b"new format")
    assert cleanup_old_derived_files() is True
    assert fresh_derived.exists()


def test_missing_recording_folder_does_not_block_upgrade(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    root = tmp_path / "storage"
    root.mkdir()
    _video_for_folder(root / "deleted-recording")
    assert cleanup_old_derived_files() is True


def test_missing_storage_root_keeps_upgrade_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    folder = tmp_path / "missing-storage" / "recording"
    root = tmp_path / "missing-storage-root"
    storage = Storage(name=f"missing-{uuid4().hex}", root_path=str(root))
    _video_for_folder(folder, storage=storage)
    assert cleanup_old_derived_files() is False
    assert (Path(settings.data_dir) / "derived-format-pending").exists()
    assert not (Path(settings.data_dir) / "derived-format-version").exists()


def test_retry_preserves_derived_files_created_after_pending_started(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    data_dir = Path(settings.data_dir)
    data_dir.mkdir()
    pending = data_dir / "derived-format-pending"
    pending.write_text("started", encoding="ascii")
    import os
    os.utime(pending, (1_700_000_000, 1_700_000_000))
    folder = tmp_path / "storage" / "recording"
    folder.mkdir(parents=True)
    (folder / "original.mkv").write_bytes(b"original")
    fresh = folder / "new.playable.mp4"
    fresh.write_bytes(b"new format")
    _video_for_folder(folder)
    assert cleanup_old_derived_files() is True
    assert fresh.exists()
