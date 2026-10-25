import pytest

from app.services.manual_video import parse_video_url


@pytest.mark.parametrize("value", [
    "abcdefghijk",
    "https://www.youtube.com/watch?v=abcdefghijk",
    "https://www.youtube.com/watch?list=PL123&v=abcdefghijk&t=5",
    "https://youtu.be/abcdefghijk?t=10",
    "https://www.youtube.com/live/abcdefghijk",
    "https://www.youtube.com/shorts/abcdefghijk",
    "https://m.youtube.com/watch?v=abcdefghijk",
    "https://music.youtube.com/watch?v=abcdefghijk",
    "youtube.com/watch?v=abcdefghijk",
])
def test_parse_video_url_forms(value):
    assert parse_video_url(value) == "abcdefghijk"


@pytest.mark.parametrize("value", [
    "", "javascript:alert(1)", "https://example.com/watch?v=abcdefghijk",
    "https://youtube.com/watch?v=bad", "https://youtu.be/abcdefghij",
    "https://youtube.com/channel/abcdefghijk", "https://youtube.com.evil.test/watch?v=abcdefghijk",
])
def test_parse_video_url_rejects_invalid_input(value):
    with pytest.raises(ValueError):
        parse_video_url(value)
