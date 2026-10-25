from datetime import timedelta
from email.message import Message
from pathlib import Path
from urllib.error import URLError

import pytest

from app.config import settings
from app.db import SessionLocal
from app.models import Channel, Video, now
from app.services.chat_assets import asset_key
from app.services.image_cache import _failed_until
from test_api_round3 import add_channel


@pytest.fixture(autouse=True)
def reset_thumbnail_failures():
    _failed_until.clear()


class Response:
    def __init__(self, body=b"image", content_type="image/jpeg", url=None):
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(body))
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url if self.url is not None else "https://i.ytimg.com/test.jpg"

    def read(self, size=-1):
        return self.body[:size]


def add_thumbnail_video(channel_id, url, *, status="WAITING_LIVE", directory=None, storage_id=None):
    with SessionLocal() as db:
        video = Video(youtube_video_id="thumb" + str(abs(hash(url)))[:6], channel_id=channel_id,
                      title="Thumbnail", status=status, thumbnail_url=url,
                      file_path=str(directory) if directory else None, storage_id=storage_id)
        db.add(video)
        db.commit()
        return video.id


@pytest.mark.integration
def test_remote_thumbnail_cached_and_url_change(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    calls = []
    monkeypatch.setattr("app.services.image_cache.urlopen", lambda request, timeout: (calls.append(request.full_url) or Response(url=request.full_url)))
    channel_id = add_channel()
    url = "https://i.ytimg.com/vi/abc/hq720.jpg"
    video_id = add_thumbnail_video(channel_id, url)
    first = client.get(f"/api/videos/{video_id}/thumbnail")
    second = client.get(f"/api/videos/{video_id}/thumbnail")
    assert first.status_code == second.status_code == 200
    assert first.headers["cache-control"] == "public, max-age=3600"
    assert len(calls) == 1
    with SessionLocal() as db:
        db.get(Video, video_id).thumbnail_url = url + "?new=1"
        db.commit()
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 200
    assert len(calls) == 2


@pytest.mark.integration
@pytest.mark.parametrize("url,response", [
    ("https://evil.test/a.jpg", Response()),
    ("http://i.ytimg.com/a.jpg", Response()),
    ("https://i.ytimg.com/a.jpg", Response(content_type="text/html")),
    ("https://i.ytimg.com/a.jpg", Response(url="https://evil.test/redirect.jpg")),
    ("https://i.ytimg.com/a.jpg", Response(body=b"x" * (2 * 1024 * 1024 + 1))),
    ("https://i.ytimg.com/a.jpg", TimeoutError("timeout")),
])
def test_invalid_thumbnail_fetches_are_suppressed(client, monkeypatch, tmp_path, url, response):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    calls = []

    def fetch(_request, timeout):
        calls.append(timeout)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("app.services.image_cache.urlopen", fetch)
    channel_id = add_channel()
    video_id = add_thumbnail_video(channel_id, url)
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 404
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 404
    assert len(calls) == (0 if url.startswith(("http:", "https://evil")) else 1)


@pytest.mark.integration
def test_fetch_disabled_serves_cache_only(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "remote_image_fetch_enabled", False)
    calls = []
    monkeypatch.setattr("app.services.image_cache.urlopen", lambda *_args, **_kwargs: calls.append(1))
    channel_id = add_channel()
    url = "https://i.ytimg.com/cached.jpg"
    video_id = add_thumbnail_video(channel_id, url)
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 404
    assert not calls
    folder = Path(tmp_path) / "thumbnails"
    folder.mkdir()
    (folder / f"{asset_key(url)}.jpg").write_bytes(b"cached")
    assert client.get(f"/api/videos/{video_id}/thumbnail").status_code == 200
    assert not calls


@pytest.mark.integration
def test_folder_thumbnail_precedes_remote_and_list_flags(client, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "remote_image_fetch_enabled", False)
    monkeypatch.setattr("app.services.image_cache.urlopen", lambda *_args, **_kwargs: pytest.fail("unexpected fetch"))
    channel_id = add_channel()
    folder = tmp_path / "video"
    folder.mkdir()
    (folder / "local.jpg").write_bytes(b"local")
    from app.models import Storage
    with SessionLocal() as db:
        storage = Storage(name="thumbnail-test", root_path=str(tmp_path))
        db.add(storage)
        db.commit()
        storage_id = storage.id
    ids = [add_thumbnail_video(channel_id, "https://i.ytimg.com/x.jpg", directory=folder, storage_id=storage_id),
           add_thumbnail_video(channel_id, "https://i.ytimg.com/y.jpg"),
           add_thumbnail_video(channel_id, None),
           add_thumbnail_video(channel_id, "https://evil.test/z.jpg")]
    local = client.get(f"/api/videos/{ids[0]}/thumbnail")
    assert local.content == b"local"
    rows = client.get("/api/videos", params={"scope": "upcoming", "limit": 100}).json()
    flags = {int(row["id"]): row["has_thumbnail"] for row in rows}
    assert [flags[item] for item in ids] == [True, True, False, False]


@pytest.mark.integration
def test_thumbnail_cache_cleanup_even_when_playable_cleanup_disabled(monkeypatch, tmp_path):
    from app.workers import derived_cleanup_worker

    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    monkeypatch.setattr(settings, "playable_cache_days", 0)
    folder = tmp_path / "thumbnails"
    folder.mkdir()
    stale = folder / "stale.jpg"
    fresh = folder / "fresh.jpg"
    stale.touch()
    fresh.touch()
    old = (now() - timedelta(days=61)).timestamp()
    import os
    os.utime(stale, (old, old))
    derived_cleanup_worker()
    assert not stale.exists()
    assert fresh.exists()
