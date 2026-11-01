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
PROMPT_VERSION = "transcript-summary-v2"
SYSTEM = ("Summarize only the supplied timestamped transcript. Do not infer or invent facts, "
          "do not invent timestamps, and say when something is unknown. Return only valid JSON.")

# The validators below are strict, so the model must be told the exact shape.
CHUNK_INSTRUCTIONS = (
    "Summarize this part of a live stream transcript. Write in the same language as the transcript. "
    "Timestamps are seconds from the start of the stream, taken from the [HH:MM:SS - HH:MM:SS] markers. "
    "Return a JSON object with exactly these keys:\n"
    '{"summary": string, '
    '"topics": [{"start": number, "end": number, "title": string, "summary": string}], '
    '"keywords": [string], '
    '"important_moments": [{"timestamp": number, "description": string}]}\n'
    "Use empty lists when there is nothing to report.\n\nTranscript:\n")
FINAL_INSTRUCTIONS = (
    "Combine these per-section summaries of one live stream into a final summary. Write in the same "
    "language as the summaries. Timestamps are seconds from the start of the stream. "
    "Return a JSON object with exactly these keys:\n"
    '{"summary": string, '
    '"topics": [{"start": number, "end": number, "title": string, "summary": string}], '
    '"highlights": [{"timestamp": number, "description": string}], '
    '"keywords": [string], '
    '"timeline": [{"start": number, "title": string}]}\n'
    "Use empty lists when there is nothing to report.\n\nSection summaries (JSON):\n")


def chunk_messages(transcript_text: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": CHUNK_INSTRUCTIONS + transcript_text}]


def final_messages(summaries_json: str) -> list[dict]:
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": FINAL_INSTRUCTIONS + summaries_json}]


REQUEST_TIMEOUT_SECONDS = 300


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
            # 大きなモデルは 30 分ぶんの文字起こしの要約に数分かかることがある。
            with open_fn(request, timeout=REQUEST_TIMEOUT_SECONDS) as response: raw = response.read()
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
