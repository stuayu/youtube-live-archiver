import base64
import io

import httpx
import pytest
from PIL import Image

from app.services import social


def _sample_jpeg():
    stream = io.BytesIO()
    Image.new("RGB", (1280, 720), "navy").save(stream, format="JPEG")
    return stream.getvalue()


def test_post_text_creates_utf8_byte_ranges_for_hashtag_facets():
    text, facets = social._post_text(
        {"text": "今日の配信", "hashtags": ["#ライブ"], "include_video_link": False},
        "abcdefghijk",
    )
    start, end, tag = facets[0]
    encoded = text.encode("utf-8")
    assert encoded[int(start):int(end)].decode("utf-8") == "#ライブ"
    assert tag == "ライブ"


def test_post_text_does_not_treat_a_longer_hashtag_as_an_exact_match():
    text, facets = social._post_text(
        {"text": "#配信中です", "hashtags": ["配信"], "include_video_link": False},
        "abcdefghijk",
    )

    assert text == "#配信中です\n#配信"
    assert [tag for _, _, tag in facets] == ["配信中です", "配信"]


def test_misskey_search_accepts_array_response_and_paginates(monkeypatch):
    calls = []
    notes = [
        {"id": f"note-{index}", "text": f"#配信 {index}", "createdAt": "2026-10-25T00:00:00Z",
         "user": {"username": "viewer", "host": "example.test"}, "files": []}
        for index in range(25)
    ]
    monkeypatch.setattr(social, "_decrypt", lambda value: "test-token")
    monkeypatch.setattr(social, "_call_json", lambda *args, **kwargs: calls.append(kwargs) or notes)

    items, cursor = social._search_misskey(
        {"instance_url": "https://example.test", "token": "encrypted"}, "配信", "prior-note",
    )

    assert len(items) == 25
    assert cursor == "note-24"
    assert calls[0]["json_body"]["untilId"] == "prior-note"
    assert items[0]["url"] == "https://example.test/notes/note-0"


def test_call_json_accepts_misskey_array_and_does_not_follow_redirects(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=[{"id": "note-1"}])
        return httpx.Response(307, headers={"Location": "https://attacker.example/collect"})

    original_client = httpx.Client
    monkeypatch.setattr(
        social.httpx,
        "Client",
        lambda *args, **kwargs: original_client(*args, transport=httpx.MockTransport(handler), **kwargs),
    )

    assert social._call_json("POST", "https://misskey.example/api/notes/search-by-tag") == [{"id": "note-1"}]
    with pytest.raises(social.SocialServiceError):
        social._call_json("POST", "https://misskey.example/api/notes/create")
    assert len(calls) == 2
    assert calls[1].url.host == "misskey.example"


def test_capture_data_checks_jpeg_signature_and_claimed_dimensions():
    sample = _sample_jpeg()
    assert social._decode_image({
        "data_base64": base64.b64encode(sample).decode("ascii"),
        "mime_type": "image/jpeg",
        "width": 1280,
        "height": 720,
    }) == sample
    with pytest.raises(ValueError, match="画像サイズ"):
        social._decode_image({
            "data_base64": base64.b64encode(sample).decode("ascii"),
            "mime_type": "image/jpeg",
            "width": 640,
            "height": 360,
        })


def test_bluesky_post_uploads_selected_images_and_adds_utf8_hashtag_facets(monkeypatch):
    calls = []
    replies = [
        {"accessJwt": "access-token"},
        {"blob": {"$type": "blob", "ref": {"$link": "blob-cid"}, "mimeType": "image/jpeg", "size": 100}},
        {"blob": {"$type": "blob", "ref": {"$link": "blob-cid-2"}, "mimeType": "image/jpeg", "size": 100}},
        {"blob": {"$type": "blob", "ref": {"$link": "blob-cid-3"}, "mimeType": "image/jpeg", "size": 100}},
        {"blob": {"$type": "blob", "ref": {"$link": "blob-cid-4"}, "mimeType": "image/jpeg", "size": 100}},
        {"uri": "at://did:plc:test/app.bsky.feed.post/post-key"},
    ]

    monkeypatch.setattr(social, "_decrypt", lambda value: "refresh-token")
    monkeypatch.setattr(social, "_call_json", lambda *args, **kwargs: calls.append((args, kwargs)) or replies.pop(0))
    monkeypatch.setattr(social, "_save_setting", lambda *args, **kwargs: None)
    image = {"bytes": _sample_jpeg(), "alt": "配信の場面"}

    result = social._post_bluesky(
        None,
        {"service_url": "https://bsky.social", "refresh_token": "encrypted", "did": "did:plc:test",
         "handle": "viewer.example"},
        "配信\n#ライブ",
        [("7", "17", "ライブ")],
        [image, image, image, image],
    )

    create_record = calls[-1][1]["json_body"]["record"]
    assert result["url"] == "https://bsky.app/profile/viewer.example/post/post-key"
    assert create_record["facets"][0]["index"] == {"byteStart": 7, "byteEnd": 17}
    assert create_record["embed"]["images"][0]["alt"] == "配信の場面"
    assert len(create_record["embed"]["images"]) == 4
    assert len(calls) == 6


def test_misskey_post_uploads_multiple_images_then_creates_note(monkeypatch):
    calls = []
    replies = [
        {"id": "file-one"}, {"id": "file-two"},
        {"createdNote": {"id": "note-one"}},
    ]
    monkeypatch.setattr(social, "_decrypt", lambda value: "misskey-token")
    monkeypatch.setattr(social, "_call_json", lambda *args, **kwargs: calls.append((args, kwargs)) or replies.pop(0))
    image = {"bytes": _sample_jpeg(), "alt": "配信の場面"}

    result = social._post_misskey(
        {"instance_url": "https://misskey.example", "token": "encrypted"},
        "#ライブ", [image, image], "followers",
    )

    note = calls[-1][1]["json_body"]
    assert result == {"success": True, "url": "https://misskey.example/notes/note-one"}
    assert note["fileIds"] == ["file-one", "file-two"]
    assert note["visibility"] == "followers"
    assert calls[0][1]["data"]["comment"] == "配信の場面"
