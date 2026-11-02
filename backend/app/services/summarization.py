"""Transcript-only, optional OpenRouter summary pipeline."""
import hashlib
import json
import logging
import re
import time
from collections import Counter
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from urllib.error import HTTPError

log = logging.getLogger(__name__)
PROMPT_VERSION = "transcript-summary-v3"
SYSTEM = ("Summarize only the supplied timestamped transcript. Do not infer or invent facts, "
          "do not invent timestamps, and say when something is unknown. "
          "Reply with exactly one JSON object and nothing else: no Markdown, no code fences, no commentary.")

# The output is shown directly in the UI, so the shape must never vary.
OUTPUT_RULES = (
    "Output rules (follow all of them):\n"
    "- Reply with one JSON object only. Do not wrap it in ``` fences or add any text before or after it.\n"
    "- Use exactly the keys shown above. Do not add, rename, or nest keys.\n"
    "- Every timestamp (start, end, timestamp) is a number of seconds from the start of the stream, "
    "such as 754. Never write \"00:12:34\" or other strings for a timestamp.\n"
    "- \"summary\" values are plain prose sentences. Never put JSON, lists, bullet marks, Markdown, "
    "or timestamps inside them.\n"
    "- \"title\" is a short heading (under 40 characters). \"keywords\" are short words or names, "
    "without # or other symbols.\n"
    "- Use an empty list [] when there is nothing to report. Never use null.\n")
CHUNK_INSTRUCTIONS = (
    "Summarize this part of a live stream transcript. Write in the same language as the transcript. "
    "Timestamps are seconds from the start of the stream, taken from the [HH:MM:SS - HH:MM:SS] markers "
    "(for example [00:12:34 - 00:12:40] starts at 754). "
    "Return a JSON object with exactly these keys:\n"
    '{"summary": string, '
    '"topics": [{"start": number, "end": number, "title": string, "summary": string}], '
    '"keywords": [string], '
    '"important_moments": [{"timestamp": number, "description": string}]}\n'
    + OUTPUT_RULES + "\nTranscript:\n")
FINAL_INSTRUCTIONS = (
    "Combine these per-section summaries of one live stream into a final summary. Write in the same "
    "language as the summaries. Timestamps are seconds from the start of the stream; reuse the supplied "
    "timestamps instead of inventing new ones. "
    "Return a JSON object with exactly these keys:\n"
    '{"summary": string, '
    '"topics": [{"start": number, "end": number, "title": string, "summary": string}], '
    '"highlights": [{"timestamp": number, "description": string}], '
    '"keywords": [string], '
    '"timeline": [{"start": number, "title": string}]}\n'
    "\"summary\" is an overview of the whole stream in 3 to 8 sentences. List topics in time order "
    "(at most 15), highlights for the most notable moments (at most 10), and at most 15 keywords.\n"
    + OUTPUT_RULES + "\nSection summaries (JSON):\n")
MERGE_INSTRUCTIONS = (
    "Combine these adjacent section summaries into one intermediate summary. Preserve the supplied timestamps, "
    "retain important facts and topic titles, and write in the same language as the summaries. "
    "Do not invent facts or timestamps. Return a JSON object with exactly these keys:\n"
    '{"summary": string, "topics": [{"start": number, "end": number, "title": string, "summary": string}], '
    '"keywords": [string], "important_moments": [{"timestamp": number, "description": string}]}\n'
    + OUTPUT_RULES + "\nSection summaries (JSON):\n")

FINAL_INPUT_MAX_CHARS = 12_000
FINAL_MERGE_GROUP_SIZE = 3
FINAL_SUMMARY_CHAR_LIMIT = 1_000
FINAL_TOPIC_LIMIT = 8
FINAL_MOMENT_LIMIT = 8


def chunk_messages(transcript_text: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": CHUNK_INSTRUCTIONS + transcript_text}]


def final_messages(summaries_json: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": FINAL_INSTRUCTIONS + summaries_json}]


def intermediate_messages(summaries_json: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": MERGE_INSTRUCTIONS + summaries_json}]


def compact_final_chunks(chunks: list[dict]) -> list[dict]:
    """Drop redundant chunk fields and cap verbose detail before final integration."""
    compacted = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        summary = str(chunk.get("summary") or "").strip()
        if len(summary) > FINAL_SUMMARY_CHAR_LIMIT:
            summary = summary[:FINAL_SUMMARY_CHAR_LIMIT].rstrip() + "…"
        item = {"start": chunk.get("start"), "end": chunk.get("end"), "summary": summary}
        topics = []
        for topic in chunk.get("topics") or []:
            if not isinstance(topic, dict):
                continue
            topics.append({"start": topic.get("start"), "end": topic.get("end"),
                           "title": str(topic.get("title") or "")[:160]})
            if len(topics) >= FINAL_TOPIC_LIMIT:
                break
        moments = []
        for moment in chunk.get("important_moments") or []:
            if not isinstance(moment, dict):
                continue
            description = str(moment.get("description") or "")
            if len(description) > 240:
                description = description[:240].rstrip() + "…"
            moments.append({"timestamp": moment.get("timestamp"), "description": description})
            if len(moments) >= FINAL_MOMENT_LIMIT:
                break
        item["topics"] = topics
        item["important_moments"] = moments
        compacted.append(item)
    return compacted


def serialize_final_chunks(chunks: list[dict]) -> str:
    return json.dumps(compact_final_chunks(chunks), ensure_ascii=False, separators=(",", ":"))


def _clock(seconds: float) -> str:
    total = max(0, int(seconds)); hours, rem = divmod(total, 3600); minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def assemble_final_from_chunks(chunks: list[dict]) -> dict:
    """Create a stable, usable final result without making another provider request."""
    summaries, topics, highlights = [], [], []
    keyword_counts: Counter[str] = Counter()
    keyword_order: dict[str, int] = {}
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        summary = str(chunk.get("summary") or "").strip()
        if summary:
            start, end = chunk.get("start"), chunk.get("end")
            span = (f"[{_clock(start)}–{_clock(end)}] "
                    if isinstance(start, (int, float)) and isinstance(end, (int, float)) else "")
            summaries.append(span + summary)
        for topic in chunk.get("topics") or []:
            if isinstance(topic, dict) and isinstance(topic.get("title"), str):
                topics.append({"start": topic.get("start", chunk.get("start", 0)),
                               "end": topic.get("end", chunk.get("end", 0)),
                               "title": topic["title"], "summary": str(topic.get("summary") or "")})
        for moment in chunk.get("important_moments") or []:
            if isinstance(moment, dict) and isinstance(moment.get("timestamp"), (int, float)):
                highlights.append({"timestamp": moment["timestamp"],
                                   "description": str(moment.get("description") or "")})
        for keyword in chunk.get("keywords") or []:
            if isinstance(keyword, str) and keyword.strip():
                normalized = keyword.strip()
                if normalized not in keyword_order:
                    keyword_order[normalized] = len(keyword_order)
                keyword_counts[normalized] += 1
    topics.sort(key=lambda item: (item["start"] if isinstance(item["start"], (int, float)) else 0,
                                  item["title"]))
    highlights.sort(key=lambda item: item["timestamp"])
    keywords = sorted(keyword_counts, key=lambda word: (-keyword_counts[word], keyword_order[word]))[:30]
    return {
        "summary": "\n\n".join(summaries) or "チャンク要約の内容を全体要約としてまとめました。",
        "topics": topics,
        "highlights": highlights,
        "keywords": keywords,
        "timeline": [{"start": topic["start"], "title": topic["title"]} for topic in topics],
        "assembled": True,
    }


REQUEST_TIMEOUT_SECONDS = 300


class RateLimitError(RuntimeError):
    def __init__(self, retry_after):
        super().__init__("OpenRouter rate limited the request")
        self.retry_after = max(60, int(retry_after))


class ProviderResponseError(RuntimeError):
    """Retryable provider failure with only safe response metadata attached."""
    def __init__(self, message: str, *, finish_reason: str | None = None,
                 provider_code: str | None = None, provider_message: str | None = None):
        finish_reason = re.sub(r"[^A-Za-z0-9_.-]", "", finish_reason)[:40] if finish_reason else None
        details = []
        if finish_reason and f"finish_reason={finish_reason}" not in message:
            details.append(f"finish_reason={finish_reason}")
        if provider_code:
            details.append(f"provider_code={provider_code}")
        if provider_message:
            details.append(f"provider_message={provider_message}")
        if details:
            message = f"{message} ({', '.join(details)})"
        super().__init__(message)
        self.finish_reason = finish_reason
        self.provider_code = provider_code
        self.provider_message = provider_message


class TruncatedProviderResponseError(ProviderResponseError):
    """The provider stopped generation at its output limit."""


class ProviderHTTPError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__(f"OpenRouter HTTP {status_code}")
        self.status_code = status_code


def _safe_provider_error(error, *, messages, api_key):
    if not isinstance(error, dict):
        return None, None
    code = error.get("code") or error.get("type")
    code = str(code)[:40] if code is not None else None
    if code and (not re.fullmatch(r"[A-Za-z0-9_.-]+", code) or re.match(r"(?i)sk(?:-or)?-", code)):
        code = None
    message = error.get("message")
    message = message.strip()[:4_000] if isinstance(message, str) else ""
    if len(message) >= 4_000:
        return code, None
    for request_message in messages:
        content = request_message.get("content")
        if isinstance(content, str) and len(content) >= 12:
            message = message.replace(content, "[request content]")
            if len(content) >= 32 and any(content[index:index + 32] in message
                                          for index in range(len(content) - 31)):
                return code, None
    if api_key:
        message = message.replace(api_key, "[redacted]")
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", message)
    message = re.sub(r"(?i)\b(?:sk-or-v1-|sk-or-|sk-)\w+", "[redacted]", message)
    message = re.sub(r"(?i)\b(?:api[_ -]?key|token|authorization)\s*[:=]\s*\S+", "[redacted]", message)
    message = re.sub(r"[\x00-\x1f\x7f]", " ", message)
    return code, message[:160].strip() or None


def _provider_response_error(payload, *, messages, api_key):
    if not isinstance(payload, dict):
        raise ProviderResponseError("provider returned an invalid response")
    provider_error = payload.get("error")
    if "error" in payload and provider_error is not None:
        code, message = _safe_provider_error(provider_error, messages=messages, api_key=api_key)
        raise ProviderResponseError("provider returned an error", provider_code=code, provider_message=message)
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise ProviderResponseError("provider response is missing a choice")
    choice = choices[0]
    if "error" in choice and choice["error"] is not None:
        code, message = _safe_provider_error(choice["error"], messages=messages, api_key=api_key)
        raise ProviderResponseError("provider returned a choice error", provider_code=code,
                                    provider_message=message)
    finish_reason = choice.get("finish_reason")
    if finish_reason == "length":
        raise TruncatedProviderResponseError("provider response was truncated", finish_reason="length")
    if not isinstance(finish_reason, str) or not finish_reason.strip():
        raise ProviderResponseError("provider response is missing finish_reason")
    if finish_reason == "error":
        raise ProviderResponseError("provider returned an empty response (finish_reason=error)", finish_reason="error")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseError("provider returned an empty response", finish_reason=finish_reason)
    try:
        return repair_json(content)
    except (TypeError, ValueError) as exc:
        raise ProviderResponseError("provider response was not valid JSON", finish_reason=finish_reason) from exc

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

TOPIC_LIMIT = 30
MOMENT_LIMIT = 20
KEYWORD_LIMIT = 20


def _seconds(value):
    """Accept 754, 754.0, "754" or "00:12:34" and return seconds, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if value >= 0 and value == value and value != float("inf") else None
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d+(?:\.\d+)?", text):
            return float(text)
        match = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.\d+)?", text)
        if match:
            hours, minutes, secs = (int(part or 0) for part in match.groups())
            return float(hours * 3600 + minutes * 60 + secs)
    return None


def _plain_text(value, limit=None):
    """Return clean prose; unwrap JSON or code fences a model put inside a text field."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", value.strip()).strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            inner = json.loads(text)
        except ValueError:
            inner = None
        if isinstance(inner, dict):
            text = _plain_text(inner.get("summary") or inner.get("description") or inner.get("title") or "")
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _items(value):
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _unwrap(data):
    """Some models wrap the object, e.g. {"result": {...}}; take the inner object."""
    if isinstance(data, dict) and "summary" not in data and len(data) == 1:
        inner = next(iter(data.values()))
        if isinstance(inner, dict):
            return inner
    return data


def _normalize_topics(value):
    topics = []
    for item in _items(value):
        start = _seconds(item.get("start", item.get("timestamp")))
        title = _plain_text(item.get("title") or item.get("topic"), 160)
        summary = _plain_text(item.get("summary") or item.get("description"), 1_000)
        if start is None or not (title or summary):
            continue
        end = _seconds(item.get("end"))
        end = start if end is None or end < start else end
        topics.append({"start": start, "end": end, "title": title or summary[:40], "summary": summary})
    topics.sort(key=lambda topic: topic["start"])
    return topics[:TOPIC_LIMIT]


def _normalize_moments(value):
    moments = []
    for item in _items(value):
        timestamp = _seconds(item.get("timestamp", item.get("start")))
        description = _plain_text(item.get("description") or item.get("title") or item.get("summary"), 400)
        if timestamp is not None and description:
            moments.append({"timestamp": timestamp, "description": description})
    moments.sort(key=lambda moment: moment["timestamp"])
    return moments[:MOMENT_LIMIT]


def _normalize_keywords(value):
    if isinstance(value, str):
        value = re.split(r"[,、，]", value)
    keywords = []
    for item in value if isinstance(value, list) else []:
        word = _plain_text(item, 60).lstrip("#＃").strip() if isinstance(item, str) else ""
        if word and word not in keywords:
            keywords.append(word)
    return keywords[:KEYWORD_LIMIT]


def validate_chunk(data):
    """Normalize a section summary to the exact shape the pipeline and UI expect."""
    data = _unwrap(data)
    if not isinstance(data, dict) or not isinstance(data.get("summary"), str):
        raise ValueError("schema missing key 'summary'")
    for key in ("topics", "keywords", "important_moments"):
        if data.get(key) is not None and not isinstance(data[key], (list, str) if key == "keywords" else list):
            raise ValueError(f"schema invalid key '{key}'")
    return {
        "summary": _plain_text(data["summary"]),
        "topics": _normalize_topics(data.get("topics")),
        "keywords": _normalize_keywords(data.get("keywords")),
        "important_moments": _normalize_moments(data.get("important_moments")),
    }


def validate_final(data):
    """Normalize the final summary; anything the UI cannot show is dropped, not passed through."""
    data = _unwrap(data)
    summary = _plain_text(data.get("summary")) if isinstance(data, dict) else ""
    if not summary:
        raise ValueError("schema missing key 'summary' or summary text is empty")
    for key in ("topics", "highlights", "keywords", "timeline"):
        if data.get(key) is not None and not isinstance(data[key], (list, str) if key == "keywords" else list):
            raise ValueError(f"schema invalid key '{key}'")
    topics = _normalize_topics(data.get("topics"))
    highlights = _normalize_moments(data.get("highlights") if data.get("highlights") is not None
                                    else data.get("important_moments"))
    timeline = []
    for item in _items(data.get("timeline")):
        start = _seconds(item.get("start", item.get("timestamp")))
        title = _plain_text(item.get("title") or item.get("description"), 160)
        if start is not None and title:
            timeline.append({"start": start, "title": title})
    timeline.sort(key=lambda row: row["start"])
    if not timeline:
        timeline = [{"start": topic["start"], "title": topic["title"]} for topic in topics]
    return {"summary": summary, "topics": topics, "highlights": highlights,
            "keywords": _normalize_keywords(data.get("keywords")), "timeline": timeline[:TOPIC_LIMIT]}

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
            # 大きなモデルは 30 分ぶんの文字起こしの要約に数分かかることがある。
            with open_fn(request, timeout=REQUEST_TIMEOUT_SECONDS) as response: raw = response.read()
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError) as exc:
                raise ProviderResponseError("provider returned an invalid response") from exc
            return _provider_response_error(payload, messages=messages, api_key=api_key)
        except HTTPError as exc:
            delay = None
            if exc.code == 429:
                value=exc.headers.get("Retry-After", "")
                try: delay=max(0,int(value))
                except ValueError:
                    try: delay=max(0,int((parsedate_to_datetime(value)-datetime.now(timezone.utc)).total_seconds()))
                    except Exception: pass
            retry_delay = max(60, delay if delay is not None else min(86400, 60 * (2 ** attempt)))
            if attempt + 1 >= max(1, attempts):
                if exc.code == 429: raise RateLimitError(retry_delay) from None
                raise ProviderHTTPError(exc.code) from None
            sleeper(retry_delay)
        except Exception as exc:
            if attempt + 1 >= max(1, attempts): raise
            sleeper(min(86400, 60 * (2 ** attempt)))

def cache_key(transcript, model):
    return hashlib.sha256((transcript + "\0" + PROMPT_VERSION + "\0" + model).encode()).hexdigest()


class ProviderCheckError(RuntimeError):
    """A provider test failure with a message the user can act on.

    ``field`` names the setting most likely at fault (for the form), and the
    message never contains the API key or the provider response body verbatim
    beyond its short error text."""

    def __init__(self, message: str, *, field: str | None = None, code: str = "provider_error"):
        super().__init__(message)
        self.field = field
        self.code = code


PROVIDER_TEST_TRANSCRIPT = ("[00:00:00 - 00:00:05]\nこれは接続テスト用の文字起こしです。\n\n"
                            "[00:00:05 - 00:00:10]\n要約機能が正しく動くか確認しています。")


def _provider_error_text(exc: HTTPError) -> str:
    try:
        body = json.loads(exc.read() or b"{}")
    except Exception:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or "")[:300]


def check_provider(*, api_key: str, model: str, base_url: str, opener=None, timeout: float = 45) -> dict:
    """Send a tiny transcript exactly like a real summary request and validate the reply.

    Raises ProviderCheckError with Japanese guidance on any failure."""
    from urllib.error import URLError
    if not api_key:
        raise ProviderCheckError("API キーが未登録です。OpenRouter の Keys 画面で作成したキーを入力してください",
                                 field="openrouter_api_key", code="missing_key")
    if not model:
        raise ProviderCheckError("モデルが未入力です（例: openai/gpt-4o-mini）", field="openrouter_model",
                                 code="missing_model")
    if not re.match(r"^https?://", base_url or ""):
        raise ProviderCheckError("API ベース URL は http:// か https:// で始めてください"
                                 "（OpenRouter は https://openrouter.ai/api/v1）",
                                 field="openrouter_base_url", code="bad_base_url")
    # 実際の区間要約と同じプロンプト・同じ検証で確かめる。
    messages = chunk_messages(PROVIDER_TEST_TRANSCRIPT)
    request = Request(base_url.rstrip("/") + "/chat/completions",
                      data=json.dumps({"model": model, "messages": messages, "max_tokens": 800,
                                       "response_format": {"type": "json_object"}}).encode(),
                      headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                      method="POST")
    started = time.monotonic()
    try:
        with (opener or urlopen)(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = _provider_error_text(exc)
        suffix = f"（プロバイダーの説明: {detail}）" if detail else ""
        lowered = detail.lower()
        if exc.code == 401:
            raise ProviderCheckError("API キーが無効です。OpenRouter の Keys 画面でキーをコピーし直して入力してください"
                                     + suffix, field="openrouter_api_key", code="invalid_key") from None
        if exc.code == 402:
            raise ProviderCheckError("クレジット残高が不足しています。OpenRouter の Credits 画面でチャージするか、"
                                     "無料モデル（名前が :free で終わるもの）を選んでください" + suffix,
                                     field="openrouter_model", code="insufficient_credits") from None
        if exc.code == 403:
            raise ProviderCheckError("このキーではリクエストが許可されていません。キーの利用制限・上限額や"
                                     "モデルの利用条件を OpenRouter で確認してください" + suffix,
                                     field="openrouter_api_key", code="forbidden") from None
        if exc.code == 404 and not any(word in lowered for word in ("model", "endpoint")):
            raise ProviderCheckError("API の送信先が見つかりません（HTTP 404）。API ベース URL を確認してください"
                                     "（OpenRouter は末尾まで含めて https://openrouter.ai/api/v1）" + suffix,
                                     field="openrouter_base_url", code="endpoint_not_found") from None
        if exc.code == 404 or (exc.code == 400 and "model" in lowered
                               and any(word in lowered for word in ("not", "invalid", "unknown"))):
            raise ProviderCheckError("モデルが見つかりません。モデル名を OpenRouter の Models 画面の ID"
                                     "（例: openai/gpt-4o-mini）と同じにしてください。API ベース URL が"
                                     "間違っている場合も起こります" + suffix,
                                     field="openrouter_model", code="model_not_found") from None
        if exc.code == 400 and ("response_format" in lowered or "json" in lowered):
            raise ProviderCheckError("このモデルは JSON 形式の出力に対応していません。別のモデルを選んでください"
                                     + suffix, field="openrouter_model", code="json_unsupported") from None
        if exc.code == 429:
            raise ProviderCheckError("プロバイダーのアクセス上限に達しています。無料モデルは回数制限が厳しいため、"
                                     "しばらく待つか有料モデルを選んでください" + suffix,
                                     field="openrouter_model", code="rate_limited") from None
        if exc.code >= 500:
            raise ProviderCheckError(f"プロバイダー側で一時的な障害が起きています（HTTP {exc.code}）。"
                                     "時間をおいて再度試すか、別のモデルを選んでください" + suffix,
                                     code="provider_unavailable") from None
        raise ProviderCheckError(f"プロバイダーがリクエストを拒否しました（HTTP {exc.code}）" + suffix,
                                 code="rejected") from None
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ProviderCheckError(f"プロバイダーに接続できません（{type(reason).__name__}）。API ベース URL と、"
                                 "このサーバーからインターネットに出られるかを確認してください",
                                 field="openrouter_base_url", code="unreachable") from None
    try:
        payload = json.loads(raw)
        content = payload["choices"][0]["message"]["content"]
    except Exception:
        raise ProviderCheckError("応答の形式が想定と違います。API ベース URL が OpenAI 互換の"
                                 "エンドポイント（OpenRouter は https://openrouter.ai/api/v1）か確認してください",
                                 field="openrouter_base_url", code="bad_response") from None
    try:
        result = repair_json(content or "")
    except Exception:
        result = None
    if not isinstance(result, dict) or not result:
        raise ProviderCheckError("モデルが JSON 形式で返答しませんでした。要約に失敗しやすいため、"
                                 "別のモデルを選んでください", field="openrouter_model", code="not_json")
    try:
        validate_chunk(result)
    except ValueError:
        raise ProviderCheckError("モデルの返答が要約に必要な形式（summary・topics・keywords・important_moments）"
                                 "になっていません。指示に従いやすい別のモデルを選んでください",
                                 field="openrouter_model", code="bad_schema") from None
    return {"ok": True, "model": payload.get("model") or model,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "sample": result["summary"][:200]}
