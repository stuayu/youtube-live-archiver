from types import SimpleNamespace
from app.services.search import excerpt, format_time, timeline

def test_excerpt_returns_highlight_parts():
    assert excerpt("前の発言です。新機能を紹介します。後の発言", "新機能") == {"before":"前の発言です。", "match":"新機能", "after":"を紹介します。後の発言"}

def test_timestamp_format_and_jump_seconds():
    assert format_time(5535) == "01:32:15"
    assert format_time(155) == "02:35"

def test_timeline_prefers_summary_then_transcript_fallback():
    segments=[SimpleNamespace(start=2.0,text="opening"),SimpleNamespace(start=20.0,text="feature")]
    assert timeline({"topics":[{"start":12.5,"title":"topic"}]},segments)[0]["title"] == "topic"
    assert timeline(None,segments)[1]["time"] == "00:20"
    assert timeline({},[]) == []
