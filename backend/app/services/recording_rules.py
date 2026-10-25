"""Rule matching shared by preview and the recording workers."""
import re


def matches(rule, title: str, description: str = "") -> bool:
    fields = (title, description) if rule.target == "both" else (title,) if rule.target == "title" else (description,)
    needle = rule.pattern if rule.case_sensitive else rule.pattern.casefold()
    try:
        expression = re.compile(rule.pattern, 0 if rule.case_sensitive else re.IGNORECASE) if rule.match_mode == "regex" else None
    except re.error:
        return False
    for value in fields:
        value = value or ""
        if rule.match_mode == "regex":
            if expression.search(value):
                return True
        elif (needle in (value if rule.case_sensitive else value.casefold())):
            return True
    return False


def select_rule(rules, channel_id: int, title: str, description: str = ""):
    applicable = [r for r in rules if r.enabled and (r.channel_id is None or r.channel_id == channel_id)]
    exclusions = [r for r in applicable if r.is_exclusion and matches(r, title, description)]
    if exclusions:
        return None, "excluded"
    inclusions = [r for r in applicable if not r.is_exclusion]
    hits = [r for r in inclusions if matches(r, title, description)]
    if inclusions and not hits:
        return None, "unmatched"
    if not hits:
        return None, "default"
    return sorted(hits, key=lambda r: (r.priority, r.id))[0], "matched"
