"""PostgreSQL transcript search helpers; no schema extension is required."""
import re

def excerpt(text: str, term: str, radius: int = 90) -> dict:
    text = text or ""
    match = re.search(re.escape(term), text, re.IGNORECASE)
    if not match:
        return {"before": text[:radius], "match": "", "after": ""}
    start, end = match.span()
    left = max(0, start-radius); right = min(len(text), end+radius)
    return {"before": ("…" if left else "") + text[left:start], "match": text[start:end], "after": text[end:right] + ("…" if right < len(text) else "")}

def format_time(seconds: float) -> str:
    value=max(0,int(seconds)); h=value//3600; m=(value%3600)//60; s=value%60
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

def timeline(summary: dict | None, segments: list) -> list[dict]:
    data=summary if isinstance(summary,dict) else {}
    rows=data.get("topics") or data.get("timeline") or []
    result=[]
    for row in rows:
        if not isinstance(row,dict): continue
        start=row.get("start",row.get("time",row.get("start_seconds")))
        try: start=float(start)
        except (TypeError,ValueError): continue
        title=row.get("title") or row.get("topic") or row.get("summary")
        if title: result.append({"start":start,"time":format_time(start),"title":str(title)})
    if result: return sorted(result,key=lambda x:x["start"])
    return [{"start":float(s.start),"time":format_time(s.start),"title":(s.text or "").strip()[:120]} for s in segments if (s.text or "").strip()]
