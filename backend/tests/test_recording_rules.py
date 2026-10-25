from types import SimpleNamespace

from app.services.recording_rules import matches, select_rule


def rule(**kwargs):
    return SimpleNamespace(
        id=kwargs.get("id", 1), enabled=kwargs.get("enabled", True),
        channel_id=kwargs.get("channel_id"), is_exclusion=kwargs.get("is_exclusion", False),
        target=kwargs.get("target", "both"), match_mode=kwargs.get("match_mode", "keyword"),
        case_sensitive=kwargs.get("case_sensitive", False), pattern=kwargs.get("pattern", ""),
        priority=kwargs.get("priority", 100),
    )


def test_keyword_fields_and_case_sensitivity():
    assert matches(rule(pattern="Morning"), "morning stream", "")
    assert not matches(rule(pattern="Morning", target="title", case_sensitive=True), "morning stream", "Morning")
    assert matches(rule(pattern="description", target="description"), "", "A DESCRIPTION")


def test_regex_and_exclusions_take_precedence():
    include = rule(id=2, pattern=r"【#\d+】", match_mode="regex")
    exclude = rule(id=3, pattern="切り抜き", is_exclusion=True)
    selected, status = select_rule([include, exclude], 9, "雑談【#2】", "")
    assert selected is include and status == "matched"
    selected, status = select_rule([include, exclude], 9, "雑談【#2】", "切り抜き動画")
    assert selected is None and status == "excluded"


def test_include_rules_constrain_recording_and_channel_scope():
    scoped = rule(pattern="歌", channel_id=4)
    assert select_rule([scoped], 4, "歌枠")[1] == "matched"
    assert select_rule([scoped], 5, "雑談")[1] == "default"
    assert select_rule([scoped], 4, "雑談")[1] == "unmatched"
