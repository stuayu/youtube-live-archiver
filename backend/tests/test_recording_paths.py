from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services.recording_paths import _clusters, sanitize_folder_name, video_directory_name
from app.services.recorder import _recording_directory


def test_sanitize_forbidden_characters_reserved_names_and_whitespace():
    assert sanitize_folder_name('  a<>:"/\\|?*  ', 50) == "a＜＞：＂／＼｜？＊"
    assert sanitize_folder_name("CON.txt", 50) == "CON.txt_"
    assert sanitize_folder_name("Lpt9", 50) == "Lpt9_"
    assert sanitize_folder_name("COM1.mp4", 50) == "COM1.mp4_"
    assert sanitize_folder_name("a   b...", 50) == "a b"
    assert sanitize_folder_name("\x00\x1f", 50) == "__"


def test_sanitize_limits_preserve_emoji_and_do_not_split_combining_sequences():
    assert sanitize_folder_name("日😀本語", 3) == "日😀…"
    assert sanitize_folder_name("e\u0301xyz", 2) == "e\u0301…"
    assert sanitize_folder_name("👩\u200d💻x", 1) == "…"
    assert sanitize_folder_name("\ud83d\ude00xy", 2) == "😀…"


def test_video_directory_name_has_local_date_empty_title_and_unique_id():
    date = datetime(2026, 10, 16, 16, 30, tzinfo=timezone.utc)
    first = video_directory_name("abc123XYZ", "  ", date)
    second = video_directory_name("def456UVW", "  ", date)
    assert first == f"{date.astimezone().date():%Y-%m-%d} [abc123XYZ]"
    assert first != second
    long_name = video_directory_name("abc123XYZ", "絵文字😀" * 100, date)
    assert len(list(_clusters(long_name))) <= 160


def test_recording_directory_uses_channel_name_and_stable_existing_folder(tmp_path):
    root = tmp_path / "archive"
    channel = SimpleNamespace(name="風真いろは ch", youtube_channel_id="UCchannel")
    video = SimpleNamespace(youtube_video_id="abc123XYZ", title="配信 / 雑談",
                            scheduled_start_at=None,
                            created_at=datetime(2026, 10, 17, tzinfo=timezone.utc), file_path=None)
    expected = root / "風真いろは ch" / video_directory_name(
        video.youtube_video_id, video.title, video.created_at)
    assert _recording_directory(video, channel, root) == expected

    legacy = root / channel.youtube_channel_id / "2026" / video.youtube_video_id
    legacy.mkdir(parents=True)
    video.file_path = str(legacy)
    video.title = "renamed title"
    channel.name = "renamed channel"
    assert _recording_directory(video, channel, root) == legacy

    video.file_path = None
    channel.name = ""
    assert _recording_directory(video, channel, root).parent == root / "UCchannel"


def test_two_channels_with_same_display_name_still_get_distinct_video_directories(tmp_path):
    from app.services.recording_paths import CHANNEL_DIRECTORY_MAX_LENGTH

    root = tmp_path / "archive"
    channel_a = SimpleNamespace(name="同じ名前", youtube_channel_id="UCfirst")
    channel_b = SimpleNamespace(name="同じ名前", youtube_channel_id="UCsecond")
    base = dict(title="配信", scheduled_start_at=None,
                created_at=datetime(2026, 10, 17, tzinfo=timezone.utc), file_path=None)
    video_a = SimpleNamespace(youtube_video_id="id-one", **base)
    video_b = SimpleNamespace(youtube_video_id="id-two", **base)
    first = _recording_directory(video_a, channel_a, root)
    second = _recording_directory(video_b, channel_b, root)
    assert first.parent == second.parent == root / "同じ名前"
    assert first != second
    assert CHANNEL_DIRECTORY_MAX_LENGTH == 50


def test_recording_directory_date_fallback_order(tmp_path, monkeypatch):
    from app.services import recorder

    root = tmp_path / "archive"
    channel = SimpleNamespace(name="channel", youtube_channel_id="UCchannel")
    scheduled = datetime(2025, 12, 31, 16, 30, tzinfo=timezone.utc)
    created = datetime(2024, 6, 1, tzinfo=timezone.utc)
    current = datetime(2023, 3, 2, tzinfo=timezone.utc)
    video = SimpleNamespace(youtube_video_id="abc123XYZ", title="test",
                            scheduled_start_at=scheduled, created_at=created, file_path=None)
    assert _recording_directory(video, channel, root).name.startswith(
        f"{scheduled.astimezone().date():%Y-%m-%d}")
    video.scheduled_start_at = None
    assert _recording_directory(video, channel, root).name.startswith(
        f"{created.astimezone().date():%Y-%m-%d}")
    video.created_at = None
    monkeypatch.setattr(recorder, "now", lambda: current)
    assert _recording_directory(video, channel, root).name.startswith(
        f"{current.astimezone().date():%Y-%m-%d}")


def test_recording_directory_limits_full_utf16_path_with_long_names(tmp_path):
    from app.services.recording_paths import recording_directory_parts

    root = Path("C:/長い保存先") / ("archive" * 8)
    channel_name = "長いチャンネル名😀" * 20
    title = "配信😀" * 100
    date = datetime(2026, 10, 17, tzinfo=timezone.utc)
    channel, folder = recording_directory_parts(root, channel_name, "video-archive-2",
                                                title, date)
    full_path = str(root / channel / folder)
    units = len(full_path.encode("utf-16-le")) // 2
    assert units <= 196
    assert folder.startswith(date.astimezone().date().strftime("%Y-%m-%d"))
    assert "[video-archive-2]" in folder
