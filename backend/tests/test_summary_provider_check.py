import io
import json
from urllib.error import HTTPError, URLError

import pytest

from app.services.summarization import ProviderCheckError, check_provider

TARGET = {"api_key": "sk-or-secret", "model": "openai/gpt-4o-mini", "base_url": "https://openrouter.ai/api/v1"}


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _ok(content):
    body = {"model": "openai/gpt-4o-mini", "choices": [{"message": {"content": content}}]}
    return lambda request, timeout: _Response(json.dumps(body).encode())


def _http(code, message="boom"):
    def opener(request, timeout):
        raise HTTPError(request.full_url, code, "error", {},
                        io.BytesIO(json.dumps({"error": {"message": message}}).encode()))
    return opener


VALID = json.dumps({"summary": "接続テストの文字起こしです。",
                    "topics": [{"start": 0, "end": 10, "title": "テスト", "summary": "接続確認"}],
                    "keywords": ["テスト"], "important_moments": [{"timestamp": 5, "description": "確認"}]},
                   ensure_ascii=False)


def test_provider_check_sends_sample_and_accepts_json_reply():
    sent = {}

    def opener(request, timeout):
        sent["url"] = request.full_url
        sent["body"] = json.loads(request.data)
        sent["auth"] = request.get_header("Authorization")
        return _ok(VALID)(request, timeout)

    result = check_provider(**TARGET, opener=opener)
    assert result["ok"] is True and result["sample"] == "接続テストの文字起こしです。"
    assert sent["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    prompt = sent["body"]["messages"][1]["content"]
    assert "接続テスト" in prompt and "important_moments" in prompt
    assert sent["auth"] == "Bearer sk-or-secret"


@pytest.mark.parametrize(("opener", "code", "field", "words"), [
    (_http(401, "No auth credentials found"), "invalid_key", "openrouter_api_key", "API キーが無効"),
    (_http(402, "Insufficient credits"), "insufficient_credits", "openrouter_model", "クレジット残高"),
    (_http(403), "forbidden", "openrouter_api_key", "許可されていません"),
    (_http(404, "No endpoints found for openai/gpt-x"), "model_not_found", "openrouter_model", "モデルが見つかりません"),
    (_http(404, "Not Found"), "endpoint_not_found", "openrouter_base_url", "API ベース URL"),
    (_http(400, "openai/gpt-x is not a valid model ID"), "model_not_found", "openrouter_model", "モデルが見つかりません"),
    (_http(400, "response_format json_object is not supported"), "json_unsupported", "openrouter_model", "JSON"),
    (_http(429, "Rate limit exceeded"), "rate_limited", "openrouter_model", "アクセス上限"),
    (_http(503, "Provider returned error"), "provider_unavailable", None, "一時的な障害"),
    (_ok("I cannot answer in JSON"), "not_json", "openrouter_model", "JSON 形式で返答しませんでした"),
    (_ok('{"error": "model unavailable"}'), "bad_schema", "openrouter_model", "必要な形式"),
])
def test_provider_check_explains_failures(opener, code, field, words):
    with pytest.raises(ProviderCheckError) as caught:
        check_provider(**TARGET, opener=opener)
    assert caught.value.code == code and caught.value.field == field
    assert words in str(caught.value)
    assert "sk-or-secret" not in str(caught.value)


def test_provider_check_reports_unreachable_and_bad_inputs():
    def unreachable(request, timeout):
        raise URLError(OSError("getaddrinfo failed"))

    with pytest.raises(ProviderCheckError) as caught:
        check_provider(**TARGET, opener=unreachable)
    assert caught.value.code == "unreachable" and caught.value.field == "openrouter_base_url"
    with pytest.raises(ProviderCheckError) as caught:
        check_provider(**{**TARGET, "api_key": ""})
    assert caught.value.code == "missing_key"
    with pytest.raises(ProviderCheckError) as caught:
        check_provider(**{**TARGET, "base_url": "openrouter.ai/api/v1"})
    assert caught.value.code == "bad_base_url"
