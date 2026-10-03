"""Transcript-only, optional OpenRouter summary pipeline."""
import hashlib
import json
import logging
import re
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

log = logging.getLogger(__name__)
PROMPT_VERSION = "transcript-summary-v1"
SYSTEM = ("Summarize only the supplied timestamped transcript. Do not infer or invent facts, "
          "do not invent timestamps, and say when something is unknown. Return only valid JSON.")

class RateLimitError(RuntimeError):
    def __init__(self, retry_after):
        super().__init__("OpenRouter rate limited the request")
        self.retry_after = max(0, int(retry_after))

def split_transcript(rows, chunk_minutes=30):
    """Group utterance-atomic rows in bounded windows, preferring a nearby sentence end."""
    if not rows: return []
    width = max(1, int(chunk_minutes)) * 60
    chunks, start = [], 0
    while start < len(rows):
        boundary = float(rows[start]["start"]) + width
        end = start
        while end < len(rows) and float(rows[end]["end"]) <= boundary: end += 1
        if end < len(rows) and end > start:
            # Extend to the nearest sentence-ending utterance, up to five minutes.
            candidate = min(len(rows), end + 20)
            for index in range(end, candidate):
                if float(rows[index]["start"]) > boundary + 300: break
                if re.search(r"[。.!?！？][\"'」』）)]*$", str(rows[index]["text"]).strip()):
                    end = index + 1; break
        if end == start: end += 1  # one utterance can itself straddle the window
        chunks.append(rows[start:end]); start = end
    return chunks

def format_transcript(rows):
    def stamp(n):
        n = max(0, int(float(n))); h, rem = divmod(n, 3600); m, s = divmod(rem, 60)
        return f"{h:02}:{m:02}:{s:02}"
    return "\n\n".join(f"[{stamp(x['start'])} - {stamp(x['end'])}]\n{x['text']}" for x in rows)

def validate_chunk(data):
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str): raise ValueError("invalid summary")
    for key in ("topics", "keywords", "important_moments"):
        if not isinstance(data.get(key), list): raise ValueError(f"invalid {key}")
    for topic in data["topics"]:
        if not isinstance(topic, dict) or not isinstance(topic.get("start"), (int,float)) or not isinstance(topic.get("end"), (int,float)) or topic["end"] < topic["start"] or not isinstance(topic.get("title"), str) or not isinstance(topic.get("summary"), str): raise ValueError("invalid topic")
    for moment in data["important_moments"]:
        if not isinstance(moment, dict) or not isinstance(moment.get("timestamp"), (int,float)) or not isinstance(moment.get("description"), str): raise ValueError("invalid moment")
    if not all(isinstance(k, str) for k in data["keywords"]): raise ValueError("invalid keyword")
    return data

def validate_final(data):
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str): raise ValueError("invalid summary")
    for key in ("topics", "highlights", "keywords", "timeline"):
        if not isinstance(data.get(key), list): raise ValueError(f"invalid {key}")
    return data

def repair_json(text):
    try: return json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r"\{.*\}", str(text), re.S)
        if not match: raise ValueError("response is not JSON")
        return json.loads(match.group(0))

def call_openrouter(messages, *, api_key, model, base_url, attempts=5, opener=None, sleeper=time.sleep):
    """HTTP transport is injectable for tests; never logs request/response contents or secrets."""
    if not api_key or not model: raise RuntimeError("summary provider is not configured")
    request = Request(base_url.rstrip("/") + "/chat/completions", data=json.dumps({"model":model,"messages":messages,"response_format":{"type":"json_object"}}).encode(), headers={"Authorization":f"Bearer {api_key}","Content-Type":"application/json"}, method="POST")
    open_fn = opener or urlopen
    for attempt in range(max(1, attempts)):
        try:
            with open_fn(request, timeout=90) as response: raw = response.read()
            payload=json.loads(raw); return repair_json(payload["choices"][0]["message"]["content"])
        except HTTPError as exc:
            delay = None
            if exc.code == 429:
                value=exc.headers.get("Retry-After", "")
                try: delay=max(0,int(value))
                except ValueError:
                    try: delay=max(0,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
                    except Exception: pass
            if attempt + 1 >= max(1, attempts):
                if exc.code == 429: raise RateLimitError(delay if delay is not None else min(86400, 60 * (2 ** attempt))) from None
                raise RuntimeError(f"OpenRouter HTTP {exc.code}") from None
            sleeper(delay if delay is not None else min(86400, 60 * (2 ** attempt)))
        except Exception as exc:
            if attempt + 1 >= max(1, attempts): raise
            sleeper(min(86400, 60 * (2 ** attempt)))

def cache_key(transcript, model):
    return hashlib.sha256((transcript + "\0" + PROMPT_VERSION + "\0" + model).encode()).hexdigest()
